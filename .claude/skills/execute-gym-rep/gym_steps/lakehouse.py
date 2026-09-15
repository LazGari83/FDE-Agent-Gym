"""
Lakehouse-family step types. Thin compositions of the proven `code/` clients, in the
order `3_wiki/lakehouse/build-framework.md` prescribes (P1 folder -> P2/P3 lakehouse + endpoint
-> P5 upload -> P6 notebook -> P7 run). No new Fabric claims; the traps each call encodes
are documented in `3_wiki/lakehouse/gotchas.md` and the atoms.

Every step here takes an optional `workspace:` field — the alias of an earlier `workspace`
step (see `cicd.py`) — and defaults to the configured `FABRIC_WORKSPACE_ID`, so every plan
written before multi-workspace support runs unchanged. This is what lets one plan build DEV
and TEST side by side — a shape earlier reps had to hand-orchestrate.
"""
from pathlib import Path

from . import resolve_ctx, step


def _client(runner, key, factory):
    if key not in runner.cache:
        runner.cache[key] = factory()
    return runner.cache[key]


def _workspace_id(runner, spec=None):
    """The workspace a step targets: its `workspace:` alias if it names one, else the
    configured default. Aliases are declared by earlier `workspace` steps (cicd.py); an
    unknown alias is a loud error, not a fall-through to the default — falling through
    would build the item in the wrong environment and fail at validate, later and worse."""
    alias = (spec or {}).get("workspace")
    if alias:
        declared = runner.context.get("workspaces", {})
        if alias not in declared:
            raise RuntimeError(f"workspace alias '{alias}' not declared by an earlier "
                               f"'workspace' step (known: {sorted(declared)})")
        return declared[alias]["id"]
    from config import FABRIC_WORKSPACE_ID
    runner.context.setdefault("workspace_id", FABRIC_WORKSPACE_ID)
    return FABRIC_WORKSPACE_ID


def _scoped_needs(spec, *rest):
    """Steps that name a `workspace:` alias need a workspace step to have run first."""
    return (("workspace",) if spec.get("workspace") else ()) + rest


@step(name="workspace-folder", category="fabric", required=("name",), provides=("folder",),
      needs=lambda s: _scoped_needs(s),
      label=lambda s: f"ensure workspace folder '{s['name']}'")
def run_workspace_folder(runner, spec):
    """Idempotent ensure_folder; later item-creating steps file into it at creation time."""
    from workspace_folders import FolderClient
    ws = _workspace_id(runner, spec)
    folder_id = _client(runner, f"folder:{ws}", lambda: FolderClient(ws)).ensure_folder(spec["name"])
    runner.context["folder_id"] = folder_id
    return f"'{spec['name']}' -> {folder_id}"


@step(name="lakehouse", category="fabric", required=("name",), provides=("lakehouse",),
      needs=lambda s: _scoped_needs(s),
      label=lambda s: f"ensure lakehouse '{s['name']}'")
def run_lakehouse(runner, spec):
    """Create-or-reuse a schema-enabled lakehouse; waits for SQL endpoint provisioning
    unless waitSqlEndpoint is false."""
    from lakehouse_client import LakehouseClient
    ws = _workspace_id(runner, spec)
    client = _client(runner, f"lakehouse:{ws}", lambda: LakehouseClient(ws))
    existing = client.resolve_by_name(spec["name"])
    if existing:
        item, verb = client.get(existing["id"]), "reused"
    else:
        item = client.create(spec["name"], folder_id=runner.context.get("folder_id"),
                             enable_schemas=spec.get("enableSchemas", True))
        verb = "created"
    runner.context["lakehouse_id"] = item["id"]
    runner.context["lakehouse_name"] = spec["name"]
    runner.context["lakehouse_workspace_id"] = ws
    if spec.get("waitSqlEndpoint", True):
        props = client.wait_for_sql_endpoint(item["id"])
        runner.context["sql_endpoint_id"] = props.get("id")
        verb += f", endpoint {props.get('provisioningStatus')}"
    return f"{verb} — {item['id']}"


def _check_upload_files(spec):
    errors = []
    for i, entry in enumerate(spec.get("files") or []):
        if not isinstance(entry, dict) or not entry.get("local") or not entry.get("dest"):
            errors.append(f"files[{i}] needs 'local' and 'dest'")
        elif not str(entry["dest"]).startswith("Files/"):
            errors.append(f"files[{i}].dest must start with 'Files/' (got '{entry['dest']}')")
    return errors


@step(name="upload-files", category="local", required=("files",),
      needs=lambda s: _scoped_needs(s) if s.get("lakehouse") else _scoped_needs(s, "lakehouse"),
      check=_check_upload_files,
      label=lambda s: f"upload {len(s.get('files', []))} file(s) (OneLake DFS)")
def run_upload_files(runner, spec):
    """Upload local files verbatim to the lakehouse's Files/ over the OneLake DFS endpoint.
    Targets the context lakehouse, or one named on the step."""
    from auth import get_datalake_client
    ws = _workspace_id(runner, spec) if spec.get("workspace") \
        else runner.context.get("lakehouse_workspace_id", _workspace_id(runner))
    lakehouse_id = runner.context.get("lakehouse_id")
    if spec.get("lakehouse"):
        from lakehouse_client import LakehouseClient
        target = _client(runner, f"lakehouse:{ws}",
                         lambda: LakehouseClient(ws)).resolve_by_name(spec["lakehouse"])
        if not target:
            raise RuntimeError(f"named lakehouse '{spec['lakehouse']}' not found")
        lakehouse_id = target["id"]
    fs = _client(runner, f"dfs:{ws}", lambda: get_datalake_client()
                 .get_file_system_client(ws))
    for entry in spec["files"]:
        # Fixture files are task-relative. An ABSOLUTE path is how the agent's own authored
        # artifacts reach the lakehouse (a config file the payload is driven by, say) without
        # being checked into the task folder, where they would be part of the answer sheet.
        candidate = Path(entry["local"])
        local = candidate if candidate.is_absolute() else runner.task_dir / entry["local"]
        if not local.exists():
            raise RuntimeError(f"local file missing: {local} (did provision run?)")
        fs.get_file_client(f"{lakehouse_id}/{entry['dest']}").upload_data(
            local.read_bytes(), overwrite=True)
    return f"{len(spec['files'])} file(s) -> Files/"


@step(name="notebook", category="fabric", required=("name",), provides=("notebook",),
      needs=lambda s: _scoped_needs(s),
      label=lambda s: f"ensure notebook '{s['name']}'")
def run_notebook_item(runner, spec):
    """Create-or-reuse a notebook item. --payload (a ready notebook-content.py) creates or
    replaces the definition; without it the existing item is reused unchanged (regression
    mode) and a missing item is an error.

    `source:` names a payload file for THIS step and overrides `--payload`. `--payload` is one
    file for the whole plan, which is exactly right for the one-artifact task it was built for
    and cannot express a plan whose notebooks differ (AG-CICD-013: two items whose distinct
    marker strings are the whole point — one must reach Prod and the other must not). Relative
    paths resolve against the working directory, so a scratch plan and its scratch payloads sit
    together; both files stay out of the task folder.

    `contextKey:` files this notebook's id under an EXTRA context key of the plan's choosing,
    on top of the rolling `notebook_id`. `notebook_id` names "the last notebook created", which
    a plan with one notebook can rely on and a plan with several cannot: AG-PIP-003 wires three
    distinct notebooks into one pipeline definition and needs all three ids alive at once
    (`$ctx:nb_ingest_id`, …). Without it the only routes are three separate engine runs or a
    hand-orchestrated build.
    """
    from notebook_client import NotebookClient
    ws = _workspace_id(runner, spec)
    client = _client(runner, f"notebook:{ws}", lambda: NotebookClient(ws))
    source = spec.get("source") or runner.args.payload
    payload = Path(source).read_text(encoding="utf-8") if source else None
    existing = client.resolve_by_name(spec["name"])
    if existing is None:
        if payload is None:
            raise RuntimeError(
                f"notebook '{spec['name']}' does not exist and no --payload was given. "
                f"Atomic build mode needs a payload; regression mode needs the item.")
        item = client.create(spec["name"], py_source=payload,
                             folder_id=runner.context.get("folder_id"))
        if not item.get("id"):
            item = client.resolve_by_name(spec["name"]) or {}
        verb = "created"
    elif payload is not None:
        client.update_definition(existing["id"], payload)
        item, verb = existing, "definition updated"
    else:
        item, verb = existing, "reused unchanged (regression mode)"
    if not item.get("id"):
        raise RuntimeError(f"could not resolve notebook '{spec['name']}' after create")
    runner.context["notebook_id"] = item["id"]
    runner.context["notebook_name"] = spec["name"]
    runner.context["notebook_workspace_id"] = ws
    if spec.get("contextKey"):
        runner.context[spec["contextKey"]] = item["id"]
    return f"{verb} — {item['id']}"


def _check_livy_sql(spec):
    statements = spec.get("statements")
    if not isinstance(statements, list) or not statements:
        return ["statements must be a non-empty list of SQL strings"]
    return [f"statements[{i}] must be a string" for i, s in enumerate(statements)
            if not isinstance(s, str) or not s.strip()]


@step(name="livy-sql", category="fabric", required=("statements",), needs=("lakehouse",),
      check=_check_livy_sql,
      label=lambda s: f"livy: {len(s['statements'])} statement(s) (one session)")
def run_livy_sql(runner, spec):
    """Run SQL against the context lakehouse over the Livy API — the surface for work that
    must NOT live in a notebook (a one-off reconciliation, a question rather than a
    pipeline). One session for the whole batch: session start dominates the cost.

    Uses `LivyClient.execute(..., kind="pyspark")` rather than `.sql()`, whose default
    `kind="spark"` is the SCALA REPL (3_wiki/lakehouse/gotchas.md).
    """
    from livy_client import LivyClient
    ws = runner.context.get("lakehouse_workspace_id", _workspace_id(runner))
    with LivyClient(ws, runner.context["lakehouse_id"]) as livy:
        for i, statement in enumerate(spec["statements"], 1):
            code = f'spark.sql("""{statement}""")'
            if spec.get("show"):
                code += ".show(truncate=False)"
            livy.execute(code, kind="pyspark")
            print(f"      stmt {i}/{len(spec['statements'])}: ok", flush=True)
    return f"{len(spec['statements'])} statement(s) over one Livy session"


JOB_TERMINAL_STATES = ("Completed", "Failed", "Cancelled", "Deduped")


def _check_run_notebook(spec):
    errors = []
    runs = spec.get("runs", 1)
    if not isinstance(runs, int) or runs < 1:
        errors.append("runs must be a positive integer")
    expect = spec.get("expectStatus")
    if expect is not None:
        wanted = [expect] if isinstance(expect, str) else expect
        if not isinstance(wanted, list) or not wanted:
            errors.append("expectStatus must be a status string or a non-empty list")
        else:
            bad = [s for s in wanted if s not in JOB_TERMINAL_STATES]
            if bad:
                errors.append(f"expectStatus has non-terminal state(s) {bad} "
                              f"(known: {list(JOB_TERMINAL_STATES)})")
    return errors


@step(name="run-notebook", category="fabric",
      needs=lambda s: ("notebook",) + (("lakehouse",) if s.get("attachLakehouse", True) else ()),
      check=_check_run_notebook,
      label=lambda s: f"run notebook x{s.get('runs', 1)} (Jobs API, poll to terminal)")
def run_run_notebook(runner, spec):
    """Run the context notebook via the Jobs API, each run polled to a terminal state.
    Sequential on purpose: on-demand runs are NOT deduplicated, and two concurrent sessions
    collide over the same Delta path (3_wiki/lakehouse/gotchas.md).

    `expectStatus` (a terminal state, or a list of them) declares what the step considers a
    pass. It defaults to `Completed`; a task whose contract is that a bad batch *must* fail
    the run — a quality gate an orchestrator has to see raise — sets `expectStatus: "Failed"`
    so the expected failure is asserted rather than merely tolerated.

    `parameters` values may be `"$ctx:<key>"` references to earlier steps' context output —
    the run-ledger shape (AG-LAK-013): a driver notebook consuming the job-instance ids the
    previous `run-notebook` collected. Non-scalar context values arrive JSON-encoded.
    """
    from notebook_client import NotebookClient
    ws = runner.context.get("notebook_workspace_id", _workspace_id(runner))
    client = _client(runner, f"notebook:{ws}", lambda: NotebookClient(ws))
    runs = spec.get("runs", 1)
    expect = spec.get("expectStatus", "Completed")
    wanted = [expect] if isinstance(expect, str) else list(expect)
    default_lakehouse = None
    if spec.get("attachLakehouse", True):
        # The lakehouse's own workspace, not the notebook's: a promoted notebook attached to
        # a lakehouse in another workspace resolves relative paths against the wrong side if
        # this id is guessed from the run target.
        default_lakehouse = {"name": runner.context["lakehouse_name"],
                             "id": runner.context["lakehouse_id"],
                             "workspaceId": runner.context.get("lakehouse_workspace_id",
                                                               _workspace_id(runner))}
    # `environment` attaches a published Environment item to the run. It is the only way to
    # put a third-party library on an unattended run: %pip is rejected outright on a Jobs-API
    # submission and _inlineInstallationEnabled does not rescue it (proven, AG-LAK-009).
    configuration = {"environment": spec["environment"]} if spec.get("environment") else None
    statuses = []
    runner.context.setdefault("job_instances", [])
    parameters = resolve_ctx(runner, spec.get("parameters")) if spec.get("parameters") else None
    for i in range(1, runs + 1):
        instance = client.run_and_wait(
            runner.context["notebook_id"], parameters=parameters,
            default_lakehouse=default_lakehouse, timeout=spec.get("timeout", 1800),
            raise_on_failure=False, configuration=configuration)
        runner.context["job_instances"].append(instance.get("id"))
        status = instance.get("status")
        statuses.append(status)
        print(f"      run {i}/{runs}: {status}", flush=True)
        if status not in wanted:
            raise RuntimeError(
                f"run {i}/{runs} reached '{status}', expected one of {wanted}"
                f" — failureReason={instance.get('failureReason')}")
    return f"{runs} run(s): {', '.join(statuses)} (expected {wanted})"
