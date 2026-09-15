"""
Wrapper over the Fabric Notebook item + Job Scheduler REST APIs — authoring a notebook as
an *item definition* and driving a run to a verified terminal state, with no UI involved.


API reference:
  - https://learn.microsoft.com/en-us/rest/api/fabric/notebook/items
  - https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/notebook-definition
  - https://learn.microsoft.com/en-us/rest/api/fabric/core/job-scheduler/run-on-demand-item-job

Operations (SDK groups behind the seam):
  - List / Create / Get / Delete:  ``fabric_client().notebook.items``
  - Get / Update definition:       ``fabric_client().notebook.items`` (definition ops)
  - Run on demand / job instance:  ``fabric_client().core.job_scheduler``

The traps this client encodes are documented in the wiki — read those, not this file:
  - ``3_wiki/lakehouse/gotchas.md``          — the trap register (execution, LROs, the toolkit)
  - ``3_wiki/lakehouse/operate-framework.md`` — driving a run to a *verified* terminal state
  - ``3_wiki/lakehouse/coding-guidance.md``   — authoring and parameterizing the item

In short: build the `.py` payload with `notebook_content_py()` (it is a delimited
`notebook-content.py`, not a plain script), and treat a run as accepted, not completed,
until polled — payload grammar and run submission are in coding-guidance, run semantics
and job states in operate-framework.

Errors surface as ``azure.core.exceptions.HttpResponseError`` (carries ``.status_code`` and
the Fabric error body) rather than ``requests.HTTPError``.
"""
import base64
import json
import time

from azure.core.exceptions import ServiceRequestError, ServiceResponseError

from config import FABRIC_WORKSPACE_ID
from sdk import fabric_client, lro_result, sdk_models, to_wire

JOB_TERMINAL = ("Completed", "Failed", "Cancelled", "Deduped")


_PLATFORM_SCHEMA = ("https://developer.microsoft.com/json-schemas/fabric/gitIntegration/"
                    "platformProperties/2.0.0/schema.json")


def notebook_content_py(cells: list, language: str = "python",
                        kernel: str = "synapse_pyspark",
                        parameters_cell: int = None) -> str:
    """Build a `notebook-content.py` payload (the `fabricGitSource` format) from cell sources.

    `cells` is a list of either plain strings (code cells) or ``("markdown", text)`` /
    ``("code", text)`` tuples. ``parameters_cell`` is the index of the *code* cell to tag as
    the parameters cell — the injection target for ``NotebookClient.run(parameters=...)``.
    Grammar, tagging rule, and the defaults-that-raise discipline:
    ``3_wiki/lakehouse/coding-guidance.md`` ("Notebook payload").
    """
    def _meta(obj: dict) -> str:
        lines = json.dumps(obj, indent=2).splitlines()
        return "\n".join(f"# META {line}" for line in lines)

    out = ["# Fabric notebook source", "",
           "# METADATA ********************", "",
           _meta({"kernel_info": {"name": kernel}, "dependencies": {}}), ""]
    cell_meta = _meta({"language": language, "language_group": kernel})
    for index, cell in enumerate(cells):
        kind, source = ("code", cell) if isinstance(cell, str) else cell
        if kind == "markdown":
            marker = "# MARKDOWN ********************"
        elif index == parameters_cell:
            marker = "# PARAMETERS CELL ********************"
        else:
            marker = "# CELL ********************"
        body = source if kind == "code" else "\n".join(f"# {ln}" for ln in source.splitlines())
        out += [marker, "", body, "", "# METADATA ********************", "", cell_meta, ""]
    return "\n".join(out)


def platform_part(display_name: str, description: str = "") -> str:
    """The `.platform` metadata file for a notebook item, base64-encoded."""
    payload = {
        "$schema": _PLATFORM_SCHEMA,
        "metadata": {"type": "Notebook", "displayName": display_name,
                     "description": description},
        "config": {"version": "2.0", "logicalId": "00000000-0000-0000-0000-000000000000"},
    }
    return base64.b64encode(json.dumps(payload, indent=2).encode("utf-8")).decode("ascii")


def notebook_definition(py_source: str, display_name: str = None,
                        description: str = "") -> dict:
    """Wrap a `notebook-content.py` string as a `fabricGitSource` definition body."""
    parts = [{"path": "notebook-content.py",
              "payload": base64.b64encode(py_source.encode("utf-8")).decode("ascii"),
              "payloadType": "InlineBase64"}]
    if display_name:
        parts.append({"path": ".platform", "payload": platform_part(display_name, description),
                      "payloadType": "InlineBase64"})
    return {"format": "fabricGitSource", "parts": parts}


def decode_definition(definition: dict) -> str:
    """Pull the decoded `notebook-content.py` source out of a getDefinition response.

    Raises rather than returning "" when no `.py` part exists: empty source satisfies any
    `sourceLacks`-style scan, so a silently absent part would read as a clean notebook.
    """
    parts = definition.get("definition", definition).get("parts", [])
    for part in parts:
        if str(part.get("path", "")).endswith(".py"):
            return base64.b64decode(part["payload"]).decode("utf-8")
    raise ValueError("definition has no .py part (parts: "
                     f"{sorted(str(p.get('path', '')) for p in parts) or 'none'})")


class NotebookClient:

    def __init__(self, workspace_id: str = FABRIC_WORKSPACE_ID, *, sdk=None):
        self.workspace_id = workspace_id
        self._sdk = sdk

    @property
    def _api(self):
        if self._sdk is None:
            self._sdk = fabric_client()
        return self._sdk

    # ── item CRUD ───────────────────────────────────────────────────

    def list_notebooks(self) -> list[dict]:
        return to_wire(self._api.notebook.items.list_notebooks(self.workspace_id))

    def resolve_by_name(self, display_name: str) -> dict | None:
        return next((nb for nb in self.list_notebooks()
                     if nb.get("displayName") == display_name), None)

    def get(self, notebook_id: str) -> dict:
        return to_wire(self._api.notebook.items.get_notebook(self.workspace_id, notebook_id))

    def create(self, display_name: str, py_source: str = None, folder_id: str = None,
               description: str = None) -> dict:
        """Create a notebook item, optionally with its content in the same call."""
        body: dict = {"displayName": display_name}
        if description:
            body["description"] = description
        if folder_id:
            body["folderId"] = folder_id
        if py_source is not None:
            body["definition"] = notebook_definition(py_source, display_name, description or "")
        request = sdk_models("notebook").CreateNotebookRequest.from_dict(body)
        # begin_* + lro_result, not the SDK's sync wrapper: that one sleeps unboundedly
        # and hands back the raw operation state on a Failed LRO. This bounds the wait
        # and raises on Failed/Cancelled (the old handle_lro contract).
        return to_wire(lro_result(
            self._api.notebook.items.begin_create_notebook(self.workspace_id, request),
            timeout=600, label="notebook create")) or {}

    def get_definition(self, notebook_id: str) -> dict:
        # getDefinition is a POST that may answer 202; begin_* + lro_result polls it
        # bounded (3600 is the old handle_lro deadline) and raises on Failed/Cancelled.
        return to_wire(lro_result(
            self._api.notebook.items.begin_get_notebook_definition(self.workspace_id,
                                                                   notebook_id),
            timeout=3600, label="notebook getDefinition")) or {}

    def get_source(self, notebook_id: str) -> str:
        """The decoded `notebook-content.py` of a live notebook — what CI/CD diffs."""
        return decode_definition(self.get_definition(notebook_id))

    def update_definition(self, notebook_id: str, py_source: str,
                          display_name: str = None) -> int:
        """Replace the notebook's content; returns the status code of the initial response
        (202 when Fabric took it as an LRO, polled to completion before this returns).

        The `.platform` part is sent only when a display name is supplied — the
        `?updateMetadata=true` rule: ``3_wiki/lakehouse/coding-guidance.md``.
        """
        request = sdk_models("notebook").UpdateNotebookDefinitionRequest.from_dict(
            {"definition": notebook_definition(py_source, display_name)})
        seen: list[int] = []
        # This LRO carries no result, so `lro_result` (extractor-based) does not apply;
        # the begin_* op's plain LROPoller is waited on bounded instead of the SDK's
        # unbounded sync wrapper. `wait()` re-raises a Failed/Cancelled operation's
        # HttpResponseError; a deadline overrun raises TimeoutError.
        poller = self._api.notebook.items.begin_update_notebook_definition(
            self.workspace_id, notebook_id, request,
            update_metadata=True if display_name else None,
            raw_response_hook=lambda resp: seen.append(resp.http_response.status_code))
        poller.wait(timeout=3600)
        if not poller.done():
            raise TimeoutError("notebook updateDefinition did not complete within 3600s")
        return seen[0] if seen else 200

    def delete(self, notebook_id: str) -> int:
        return self._api.notebook.items.delete_notebook(
            self.workspace_id, notebook_id,
            cls=lambda pipeline_response, _, __: pipeline_response.http_response.status_code)

    # ── running ─────────────────────────────────────────────────────

    def run(self, notebook_id: str, parameters: dict = None,
            default_lakehouse: dict = None, configuration: dict = None) -> str:
        """Start a run and return the job instance id. **Does not wait** — see `wait_for_job`.

        `parameters` / `default_lakehouse` / `configuration` wire shapes and their traps:
        ``3_wiki/lakehouse/coding-guidance.md`` ("Run submission").
        """
        exec_data: dict = {}
        if parameters:
            # Fail fast on the wire shape: `executionData.parameters` is TYPED, and the
            # server's rejection names the envelope, not the field — a bare {"name": value}
            # is refused before the notebook starts as `NotebookBadWebRequest: "Run
            # notebook payload is not well formatted."` with no run, no cell log and no
            # stack to read. Raising here puts the actual fix in the message instead.
            for name, spec in parameters.items():
                if not (isinstance(spec, dict) and "value" in spec and "type" in spec):
                    raise ValueError(
                        f"notebook run parameter {name!r} must use the typed wire shape "
                        '{"value": <v>, "type": "string"|"int"|"float"|"bool"} — a bare '
                        "value is refused server-side as NotebookBadWebRequest with no "
                        'run to inspect (3_wiki/lakehouse/coding-guidance.md, "Run '
                        'submission").')
            exec_data["parameters"] = parameters
        conf = dict(configuration or {})
        if default_lakehouse:
            conf["defaultLakehouse"] = default_lakehouse
        if conf:
            exec_data["configuration"] = conf
        request = (sdk_models("core").RunOnDemandItemJobRequest.from_dict(
            {"executionData": exec_data}) if exec_data else None)
        # DELIBERATELY `retry_total=0`: this is the ONE mutating call in this client whose
        # retry is unsafe, and azure-core's default policy WOULD replay it (it retries any
        # verb on transport errors, and even a POST on 500/503/504). A lost response to a
        # job submission does not mean the job was not started — retrying blind starts a
        # second real run (on-demand runs are not deduplicated), which breaks any run
        # ledger built on distinct instance ids (`3_wiki/lakehouse/operate-framework.md`).
        # Retry the POLL, never the submission.
        location = self._api.core.job_scheduler.run_on_demand_item_job(
            self.workspace_id, notebook_id, "RunNotebook", request, retry_total=0,
            cls=lambda pipeline_response, _, __:
                pipeline_response.http_response.headers.get("Location", ""))
        return location.rstrip("/").split("/")[-1].split("?")[0]

    def list_job_instances(self, notebook_id: str) -> list[dict]:
        """Every run of this notebook, newest first (sorted by `startTimeUtc` descending
        here — the API returns them unordered: ``3_wiki/lakehouse/operate-framework.md``)."""
        instances = to_wire(self._api.core.job_scheduler.list_item_job_instances(
            self.workspace_id, notebook_id))
        return sorted(instances, key=lambda i: i.get("startTimeUtc") or "", reverse=True)

    def job_instance(self, notebook_id: str, job_instance_id: str) -> dict:
        return to_wire(self._api.core.job_scheduler.get_item_job_instance(
            self.workspace_id, notebook_id, job_instance_id))

    def wait_for_job(self, notebook_id: str, job_instance_id: str, timeout: int = 1800,
                     poll_interval: int = 15, raise_on_failure: bool = True) -> dict:
        """Poll a run to a terminal state and return the job instance.

        Returns rather than raises for `Failed`/`Cancelled` when `raise_on_failure=False` —
        the debug tasks need to *inspect* a failure, not be stopped by it. `failureReason`
        on the returned instance carries the error code and message.

        Azure-core transport errors (`ServiceRequestError` / `ServiceResponseError` — no
        HTTP answer received) are absorbed and the poll continues until the deadline; an
        `HttpResponseError` still raises. Rationale — a transport error is not a run
        failure; retry the poll, never the submission:
        ``3_wiki/lakehouse/operate-framework.md``.
        """
        deadline = time.time() + timeout
        instance: dict = {}
        while time.time() < deadline:
            try:
                instance = self.job_instance(notebook_id, job_instance_id)
            except (ServiceRequestError, ServiceResponseError):
                time.sleep(poll_interval)
                continue
            status = instance.get("status")
            if status in JOB_TERMINAL:
                if status != "Completed" and raise_on_failure:
                    reason = instance.get("failureReason") or {}
                    raise RuntimeError(f"notebook run {status}: "
                                       f"{reason.get('errorCode', '?')} - {reason.get('message', '')}")
                return instance
            time.sleep(poll_interval)
        raise TimeoutError(f"run {job_instance_id} still '{instance.get('status')}' after {timeout}s")

    def run_and_wait(self, notebook_id: str, parameters: dict = None,
                     default_lakehouse: dict = None, timeout: int = 1800,
                     raise_on_failure: bool = True, configuration: dict = None) -> dict:
        """The whole honest loop: start, poll to terminal, return the job instance.

        `configuration` is passed straight through to `run()` — the pass-through matters
        for `environment`, the only third-party-library route on an unattended run:
        ``3_wiki/lakehouse/coding-guidance.md`` ("Run submission") and ``gotchas.md``.
        """
        job_id = self.run(notebook_id, parameters=parameters,
                          default_lakehouse=default_lakehouse, configuration=configuration)
        return self.wait_for_job(notebook_id, job_id, timeout=timeout,
                                 raise_on_failure=raise_on_failure)
