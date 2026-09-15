"""
Wrapper over the Fabric Lakehouse REST API — the item lifecycle an agent drives when it
provisions lakehouse infrastructure from code instead of the portal.


API reference: https://learn.microsoft.com/en-us/rest/api/fabric/lakehouse/items

Operations (SDK groups behind the seam):
  - List / Create / Get / Delete:  ``fabric_client().lakehouse.items``
  - List tables:                   ``fabric_client().lakehouse.tables``       (paged)
  - SQL endpoint metadata refresh: ``fabric_client().sqlendpoint.items``

The traps this client encodes are documented in the wiki — read those, not this file:
  - ``3_wiki/lakehouse/gotchas.md``           — the trap register (naming, paths, tables, endpoint)
  - ``3_wiki/lakehouse/build-framework.md``   — the ordered create → wait → author → run sequence
  - ``3_wiki/lakehouse/coding-guidance.md``   — the call shapes (incl. the pyodbc T-SQL connection)
  - ``3_wiki/lakehouse/operate-framework.md`` — the two endpoint waits this file carries:
    one-time provisioning (`wait_for_sql_endpoint`) vs per-write metadata sync
    (`refresh_sql_endpoint_metadata`)

Errors surface as ``azure.core.exceptions.HttpResponseError`` (carries ``.status_code`` and
the Fabric error body) rather than ``requests.HTTPError``.
"""
import re
import time
from types import SimpleNamespace

from config import FABRIC_WORKSPACE_ID
from sdk import fabric_client, lro_result, sdk_models, to_wire

# SQL endpoint provisioning states (SqlEndpointProvisioningStatus).
SQL_ENDPOINT_TERMINAL = ("Success", "Failed")

# Lakehouse display names: alphanumerics and underscores only (no hyphens, no spaces).
_VALID_NAME = re.compile(r"^\w+$")


def valid_display_name(name: str) -> bool:
    """True if Fabric will accept this as a *lakehouse* display name."""
    return bool(_VALID_NAME.match(name or ""))


def to_abfss(onelake_url: str) -> str:
    """Convert a OneLake **https DFS** URL to the **abfss** form Spark can address.

    ``https://onelake.dfs.fabric.microsoft.com/{ws}/{item}/Tables``
      → ``abfss://{ws}@onelake.dfs.fabric.microsoft.com/{item}/Tables``

    Idempotent: an abfss path passes through untouched.
    """
    if not onelake_url or onelake_url.startswith("abfss://"):
        return onelake_url
    match = re.match(r"https://([^/]+)/([^/]+)/(.*)$", onelake_url)
    if not match:
        return onelake_url
    host, workspace, rest = match.groups()
    return f"abfss://{workspace}@{host}/{rest}"


class _StaticTokenCredential:
    """A caller-supplied bearer token as an azure-core credential (duck-typed).

    Lets `refresh_sql_endpoint_metadata(token=...)` run inside a Fabric notebook on
    ``notebookutils.credentials.getToken("pbi")`` instead of the toolkit's SPN secret.
    The pipeline reads only ``.token`` and ``.expires_on`` off the returned object.
    """

    def __init__(self, token: str):
        self._token = token

    def get_token(self, *scopes, **kwargs):
        return SimpleNamespace(token=self._token, expires_on=int(time.time()) + 3600)


class LakehouseClient:

    def __init__(self, workspace_id: str = FABRIC_WORKSPACE_ID, *, sdk=None):
        self.workspace_id = workspace_id
        self._sdk = sdk

    @property
    def _api(self):
        if self._sdk is None:
            self._sdk = fabric_client()
        return self._sdk

    # ── item CRUD ───────────────────────────────────────────────────

    def list_lakehouses(self) -> list[dict]:
        return to_wire(self._api.lakehouse.items.list_lakehouses(self.workspace_id))

    def resolve_by_name(self, display_name: str) -> dict | None:
        """The lakehouse with this display name, or None. Names are not unique per se —
        first match wins."""
        return next((lh for lh in self.list_lakehouses()
                     if lh.get("displayName") == display_name), None)

    def get(self, lakehouse_id: str) -> dict:
        """GET the item — the only call that returns ``properties`` (paths + SQL endpoint)."""
        return to_wire(self._api.lakehouse.items.get_lakehouse(self.workspace_id, lakehouse_id))

    def create(self, display_name: str, folder_id: str = None,
               enable_schemas: bool = True, description: str = None) -> dict:
        """Create a lakehouse and return the GET-shaped item (with ``properties``).

        ``enable_schemas=True`` is the default on purpose — the API's own default is the
        irreversible legacy schemaless variant: ``3_wiki/lakehouse/design-framework.md``.
        """
        if not valid_display_name(display_name):
            raise ValueError(
                f"invalid lakehouse display name {display_name!r}: lakehouses accept only "
                f"alphanumerics and underscores (no hyphens, no spaces) — try "
                f"{display_name.replace('-', '_').replace(' ', '_')!r}")
        body: dict = {"displayName": display_name}
        if description:
            body["description"] = description
        if folder_id:
            body["folderId"] = folder_id
        if enable_schemas:
            body["creationPayload"] = {"enableSchemas": True}
        request = sdk_models("lakehouse").CreateLakehouseRequest.from_dict(body)
        # begin_* + lro_result, not the SDK's sync wrapper: that one sleeps unboundedly
        # and hands back the raw operation state on a Failed LRO. This bounds the wait
        # and raises on Failed/Cancelled (the old handle_lro contract).
        created = to_wire(lro_result(
            self._api.lakehouse.items.begin_create_lakehouse(self.workspace_id, request),
            timeout=600, label="lakehouse create")) or {}
        lakehouse_id = created.get("id")
        # The create response carries no `properties`; re-GET so callers get the paths and
        # the SQL endpoint block in one call.
        return self.get(lakehouse_id) if lakehouse_id else created

    def delete(self, lakehouse_id: str) -> int:
        return self._api.lakehouse.items.delete_lakehouse(
            self.workspace_id, lakehouse_id,
            cls=lambda pipeline_response, _, __: pipeline_response.http_response.status_code)

    # ── properties / readiness ──────────────────────────────────────

    def is_schema_enabled(self, lakehouse_id: str = None, item: dict = None) -> bool:
        """True iff schema-enabled: the presence of ``properties.defaultSchema`` — not the
        flag we sent at create time — is the ground truth (design-framework.md)."""
        item = item or self.get(lakehouse_id)
        return bool(item.get("properties", {}).get("defaultSchema"))

    def sql_endpoint(self, lakehouse_id: str = None, item: dict = None) -> dict:
        item = item or self.get(lakehouse_id)
        return item.get("properties", {}).get("sqlEndpointProperties", {}) or {}

    def wait_for_sql_endpoint(self, lakehouse_id: str, timeout: int = 600,
                              poll_interval: int = 10) -> dict:
        """Block until the SQL analytics endpoint reaches a terminal provisioning state.

        Returns the sqlEndpointProperties block; raises on ``Failed`` or on timeout.
        Semantics: ``3_wiki/lakehouse/build-framework.md`` (P3).
        """
        deadline = time.time() + timeout
        status = None
        while time.time() < deadline:
            props = self.sql_endpoint(lakehouse_id)
            status = props.get("provisioningStatus")
            if status in SQL_ENDPOINT_TERMINAL:
                if status == "Failed":
                    raise RuntimeError(f"SQL analytics endpoint provisioning FAILED for {lakehouse_id}")
                return props
            time.sleep(poll_interval)
        raise TimeoutError(f"SQL endpoint still '{status}' after {timeout}s for {lakehouse_id}")

    def onelake_paths(self, lakehouse_id: str = None, item: dict = None) -> dict:
        """The raw ``oneLakeTablesPath`` / ``oneLakeFilesPath`` **as the API returns them** —
        the https DFS REST form, not Spark-writable. Use `abfss_paths()`."""
        props = (item or self.get(lakehouse_id)).get("properties", {})
        return {"tables": props.get("oneLakeTablesPath"), "files": props.get("oneLakeFilesPath")}

    def abfss_paths(self, lakehouse_id: str = None, item: dict = None) -> dict:
        """``{"tables": abfss://…/Tables, "files": abfss://…/Files}`` — the Spark-addressable form.

        Always convert at this boundary; never let the raw https property reach Spark.
        See ``3_wiki/lakehouse/gotchas.md`` ("Writing tables") for the failure it causes.
        """
        item = item or self.get(lakehouse_id)
        return {kind: to_abfss(path) for kind, path in self.onelake_paths(item=item).items()}

    def refresh_sql_endpoint_metadata(self, sql_endpoint_id: str = None,
                                      lakehouse_id: str = None, timeout_s: int = 120,
                                      token: str = None) -> list[dict]:
        """Force the SQL analytics endpoint's metadata to catch up with a Spark write;
        returns the per-table result array. Needed after every write a T-SQL consumer
        reads — semantics, response shape, and the ``NotRun`` rule:
        ``3_wiki/lakehouse/operate-framework.md`` ("SQL endpoint metadata sync").

        ``sql_endpoint_id`` is ``properties.sqlEndpointProperties.id`` — the child
        SQLEndpoint item, *not* the lakehouse id; pass ``lakehouse_id`` to look it up.
        ``token`` lets a caller inside a Fabric notebook pass
        ``notebookutils.credentials.getToken("pbi")`` instead of the toolkit's SPN secret.
        """
        if not sql_endpoint_id:
            sql_endpoint_id = self.sql_endpoint(lakehouse_id).get("id")
        if not sql_endpoint_id:
            raise ValueError("no SQL endpoint id — the lakehouse has no sqlEndpointProperties")
        api = fabric_client(_StaticTokenCredential(token)) if token else self._api
        request = sdk_models("sqlendpoint").SqlEndpointRefreshMetadataRequest.from_dict(
            {"timeout": {"timeUnit": "Seconds", "value": timeout_s}})
        # begin_* + lro_result (bounded, raises on Failed) instead of the SDK's unbounded
        # sync wrapper; 3600 is the old handle_lro deadline for this path — the server-side
        # `timeout_s` bounds the refresh itself, this bounds the poll around it.
        body = to_wire(lro_result(
            api.sqlendpoint.items.begin_refresh_sql_endpoint_metadata(
                self.workspace_id, sql_endpoint_id, request),
            timeout=3600, label="SQL endpoint refreshMetadata")) or []
        # The release endpoint wraps the array as {"value": [...]}. Normalise so callers
        # only ever see the list.
        return body.get("value", []) if isinstance(body, dict) else body

    # ── tables ──────────────────────────────────────────────────────

    def list_tables(self, lakehouse_id: str) -> list[dict]:
        """All tables in the lakehouse (the pager is drained fully; this endpoint answers
        under ``data``, which the SDK pager follows).

        **Only works on a legacy schemaless lakehouse** — kept because it is the documented
        API and its absence is itself the finding. See
        ``3_wiki/lakehouse/prerequisites-and-fit.md`` ("What the API plane cannot do").
        """
        return to_wire(self._api.lakehouse.tables.list_tables(self.workspace_id, lakehouse_id))


def sql_endpoint_connection(server: str, endpoint_id: str, token: str = None):
    """A pyodbc connection to a lakehouse's SQL analytics endpoint (T-SQL reads).

    `server` is `sqlEndpointProperties.connectionString`; `endpoint_id` is
    `sqlEndpointProperties.id`. Token audience, driver, and the `1256` access-token
    packing: ``3_wiki/lakehouse/coding-guidance.md`` ("T-SQL over pyodbc").
    """
    import struct
    import pyodbc
    from auth import get_token
    tok = token or get_token("https://database.windows.net/.default")
    raw = tok.encode("utf-16-le")
    packed = struct.pack("<i", len(raw)) + raw
    return pyodbc.connect(
        "Driver={ODBC Driver 18 for SQL Server};"
        f"Server={server},1433;Database={endpoint_id};"
        "Encrypt=yes;TrustServerCertificate=no",
        attrs_before={1256: packed}, timeout=60)
