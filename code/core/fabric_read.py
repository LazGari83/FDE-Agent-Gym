"""fabric_read.py — the Fabric reads that are the same for every item type.

WHAT IS IN HERE

Item reads that do not care what kind of item they are pointed at: list, get, resolve a
display name, get a decoded definition or one part of it, list an item's runs. Plus the
per-workspace reads a run needs to bind itself (folders, role assignments, the workspace
record), and `to_abfss`, the one path conversion more than one topic uses.

Reads specific to ONE item type live with that item type's check family — a mirrored
database's status in `test_openmirror`, a graph's schema and queries in `test_ontology`.
Transport is the official SDK, reached only through the `sdk.py` seam — and the seam's
client never leaves this module (`_client()` is private), because the SDK client carries
the full write surface and checks must never hold one.

WHAT THIS IS FOR

Checks assert on the state of a Fabric workspace, which needs reads and only reads. Keeping
that surface separate buys two things:

  1. The read surface is separate from the modules that build Fabric items, because
     building is not on the path a check walks.

  2. Validation cannot change anything. Every function here is a GET, or a POST to an
     endpoint that only reports — this module exposes no write, whatever the SDK
     underneath it could do.

Errors surface as azure-core's `HttpResponseError` (`.status_code`, body preserved) — the
engine treats any thrown check as a failed check, and the two family branches that read a
status off a fabric_read error handle both exception families.

WHAT DOES NOT BELONG HERE

Anything that changes tenant state or assembles a definition payload — those live in the
item clients. Anything that only one item type needs — that lives in its check family. If a
check appears to need a write, it is asserting on the wrong thing: assert on the state a
build left behind, never by performing the build.
"""


import re

from azure.core.rest import HttpRequest

from config import FABRIC_WORKSPACE_ID, ONELAKE_DFS_HOST
from fabric_http import decode_parts
from sdk import fabric_client, lro_result, to_wire

TIMEOUT = 60   # families still borrow this for their own type-specific requests reads

_api = None


def _client():
    """The seam's SDK client, lazily built once — PRIVATE by design (see module docstring)."""
    global _api
    if _api is None:
        _api = fabric_client()
    return _api


# ── generic item reads ────────────────────────────────────────────────────────
# Everything below is one of these four calls with a type name filled in. Kept generic
# because a check family asserting on a new item type needs no new code here — the Fabric
# items API is uniform for reads even where the typed clients diverge on create.
#
# Every LISTING here rides a typed SDK pager, drained fully by `to_wire` — Fabric
# paginates two different ways (`continuationUri` and `continuationToken`) and a reader
# that follows only one silently truncates on endpoints that use the other. A truncated
# listing is the quietest possible false green — a check asserting absence passes because
# the page it read did not happen to contain the thing.

def get_json(path, *, params=None):
    """GET an absolute Fabric REST path (e.g. `/workspaces/{id}/lakehouses`) as JSON.

    The escape hatch every family eventually needs: a read whose endpoint has no typed
    helper here yet. Prefer a named function below when one fits — this one states nothing
    about what it is reading, so a check that uses it says less to the next reader.

    Returns ONE page, deliberately: the callers that need the whole listing either use a
    named helper below or drive their own cursor loop over this. Raw pipeline send, so the
    body arrives exactly as the wire carried it — no model filtering.
    """
    resp = _client().core.send_request(HttpRequest("GET", path, params=params))
    resp.raise_for_status()
    return resp.json()


def list_items(item_type=None, workspace_id=FABRIC_WORKSPACE_ID):
    """Items in the workspace, optionally of one type (`Lakehouse`, `Notebook`, ...)."""
    return to_wire(_client().core.items.list_items(workspace_id, type=item_type))


def get_item(item_id, workspace_id=FABRIC_WORKSPACE_ID):
    """One item — id, displayName, type, folderId. NO type-specific `properties`: the
    SDK's generic `Item` model carries no such field, so anything the wire returned there
    is dropped in deserialization (proven AG-LAK-001, 2026-08-27 — a schema-enabled
    lakehouse read back with no `defaultSchema`). A read that needs an item type's
    `properties` goes through that type's endpoint, in its check family, via `get_json`."""
    return to_wire(_client().core.items.get_item(workspace_id, item_id))


def resolve_by_name(display_name, item_type=None, workspace_id=FABRIC_WORKSPACE_ID):
    """The item with this exact display name, or None.

    First match wins. Display names are not unique in Fabric.
    """
    return next((i for i in list_items(item_type, workspace_id)
                 if i.get("displayName") == display_name), None)


def get_definition(item_id, workspace_id=FABRIC_WORKSPACE_ID):
    """The item's definition with every part base64-decoded.

    getDefinition is a POST because it takes a format argument, not because it changes
    anything — it is a read, and the only one of its kind in this module. Fabric answers
    it asynchronously for larger definitions; following the operation to its result is
    still a read — nothing is being waited into existence.
    """
    body = to_wire(lro_result(
        _client().core.items.begin_get_item_definition(workspace_id, item_id),
        timeout=3600, label=f"getDefinition({item_id})")) or {}
    if "definition" not in body:
        # Unlike most LROs, this one always has a result document, so a body without one
        # means the result fetch answered something other than the definition. REFUSE it:
        # `decode_parts` would reduce it to `{}`, `get_part` would hand back its default,
        # and a `sourceLacks`-style assertion would then pass against an empty string.
        raise RuntimeError(
            f"getDefinition for item {item_id} reported success but returned no definition "
            f"(body keys: {sorted(body) or 'none'}) — refusing rather than reporting an "
            "item with no content")
    return decode_parts(body)


def get_part(item_id, suffix, workspace_id=FABRIC_WORKSPACE_ID, default=None):
    """One decoded definition part, chosen by the end of its path.

    `get_part(nb, "notebook-content.py")` is a notebook's source; `get_part(mir,
    "mirroring.json")` is a mirrored database's configuration. Matching on the suffix rather
    than the exact path survives the leading folder some item types put their parts under.
    """
    parts = get_definition(item_id, workspace_id)
    return next((v for k, v in parts.items() if str(k).endswith(suffix)), default)


# ── workspace ─────────────────────────────────────────────────────────────────

def get_workspace(workspace_id):
    """One workspace, by id. Listing every workspace is NOT here: `test_common`'s
    ctx-cached `_dp_workspace_names` is the harness's `{id -> displayName}` map and
    `WorkspaceClient.list_workspaces()` is the client-layer one, so a third copy in this
    module would be a third thing to keep honest."""
    return to_wire(_client().core.workspaces.get_workspace(workspace_id))


def list_folders(workspace_id=FABRIC_WORKSPACE_ID):
    """Every folder in the workspace, flat. Nesting is expressed by `parentFolderId`."""
    return to_wire(_client().core.folders.list_folders(workspace_id))


def role_assignments(workspace_id=FABRIC_WORKSPACE_ID):
    """Who holds what role on the workspace, as the API reports it (`{id, principal, role}`
    — the principal is NESTED, and it is the object id, never the application id).

    Admin-gated: on a workspace where the caller is Contributor or Member this raises
    `403 InsufficientWorkspaceRole` rather than returning a shorter list, so it can confirm
    a role and never report one's absence.
    """
    return to_wire(_client().core.workspaces.list_workspace_role_assignments(workspace_id))


def list_job_instances(item_id, workspace_id=FABRIC_WORKSPACE_ID):
    """Every run of a schedulable item, newest first.

    The API returns runs in no guaranteed order, so they are sorted by `startTimeUtc`
    descending: "the latest run" is the only meaningful subject for an assertion about
    whether a build's final state was green. That sort is only sound over the WHOLE
    listing — sorting one arbitrary page would nominate a "latest run" that isn't one.
    """
    instances = to_wire(
        _client().core.job_scheduler.list_item_job_instances(workspace_id, item_id))
    return sorted(instances, key=lambda i: i.get("startTimeUtc") or "", reverse=True)


def to_abfss(onelake_url):
    """`https://onelake.dfs.../{ws}/{item}/Tables` -> `abfss://{ws}@onelake.dfs.../{item}/Tables`.

    Idempotent. The https form an item API returns is NOT Spark-writable; convert once at the
    boundary and never let the raw property reach Spark. Cross-topic: lakehouse and mirrored
    database both address their Delta files this way.
    """
    if not onelake_url or onelake_url.startswith("abfss://"):
        return onelake_url
    match = re.match(r"https://([^/]+)/([^/]+)/(.*)$", onelake_url)
    if not match:
        return onelake_url
    host, workspace, rest = match.groups()
    return f"abfss://{workspace}@{host}/{rest}"


def tables_abfss_root(item_id, workspace_id=None):
    """ABFSS root of an item's replicated Delta tables (`Tables/<schema>/<table>`) — what
    `delta_scan` and Spark read.

    NOT the landing zone. A mirrored database has two storage surfaces under the same item
    id: `Files/LandingZone/...`, where a producer writes, and `Tables/...`, where the mirror
    publishes what it made of those files. Asserting contents against the first would grade
    the producer's intent rather than the platform's result, which is the whole distinction
    an open-mirroring check exists to make.
    """
    workspace = workspace_id or FABRIC_WORKSPACE_ID
    return f"abfss://{workspace}@{ONELAKE_DFS_HOST.split('//')[-1]}/{item_id}/Tables"
