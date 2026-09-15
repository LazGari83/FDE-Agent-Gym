"""
test_notebook.py — the notebook check family for the fabric_test harness.

Part of the fabric_test module family (fabric_test.py is the entry point — run that, not
this). Runners: notebook-item, job-run — plus the offline `--lint-source` flow, which runs
a spec's sourceContains/sourceLacks assertions against a local file before it is ever
uploaded. RUNNERS declares this family's slice of the check-type registry; SELF_TESTS its
offline fixtures.
"""
from pathlib import Path

from test_common import _folder_check, _load_spec, _usage_error


# ── notebook pure matchers (offline-testable) ─────────────────────────────────

def _source_expect(source, exp):
    """Static assertions over a notebook's decoded `notebook-content.py`.

    `sourceLacks` is the sharper of the two: it catches the anti-patterns that leave a run
    green while making it unportable or wrong — a hardcoded workspace GUID, `!pip` instead
    of `%pip`, `inferSchema`. A notebook can pass every data check and still fail these.
    """
    reasons, ok = [], True
    for needle in exp.get("sourceContains", []):
        hit = needle in source
        ok = ok and hit
        reasons.append(f"contains {needle!r}: {'yes' if hit else 'NO'}")
    for needle in exp.get("sourceLacks", []):
        hit = needle in source
        ok = ok and not hit
        reasons.append(f"lacks {needle!r}: {'NO - found it' if hit else 'yes'}")
    return ok, "; ".join(reasons) or "no source expectations"


def _source_checks(spec):
    """Every notebook-item check in a spec that asserts something about notebook source.

    Returns [(notebook_name, check)]. The name falls back to the spec's top-level
    `notebook`, matching what `_run_notebook_item` does at run time.
    """
    out = []
    for check in spec.get("checks", []):
        if check.get("type") != "notebook-item":
            continue
        exp = check.get("expect", {})
        if not (exp.get("sourceContains") or exp.get("sourceLacks")):
            continue
        out.append((check.get("notebook") or spec.get("notebook"), check))
    return out


def _bind_source_targets(pairs, targets):
    """Map `--lint-source` arguments onto the spec's source-bearing checks.

    Each target is `PATH` or `NOTEBOOK=PATH`. Ambiguity is counted in *notebooks*, not
    checks: one notebook may carry several source checks (e.g. portability and
    secret-freedom asserted separately) and a bare path binds to all of them. A bare path
    is refused only when the spec asserts on two different notebooks, where guessing would
    lint the wrong contract and print a confident PASS for a file nothing checked.

    Returns (bindings, error) where bindings is [(name, check, path)].
    """
    by_name = {}
    for name, check in pairs:
        by_name.setdefault(name, []).append(check)
    bindings, bare = [], []
    for target in targets:
        name, sep, path = target.partition("=")
        if sep:
            if name not in by_name:
                return None, (f"spec has no notebook-item source check for {name!r} "
                              f"(known: {sorted(n for n in by_name if n)})")
            bindings.extend((name, check, path) for check in by_name[name])
        else:
            bare.append(target)
    if bare:
        if len(bare) > 1 or bindings or len(by_name) != 1:
            return None, ("this spec asserts on more than one notebook — name it explicitly, "
                          f"e.g. --lint-source {sorted(n for n in by_name if n)[0]}=path/to/file.py")
        name = next(iter(by_name))
        bindings.extend((name, check, bare[0]) for check in by_name[name])
    return bindings, None


def _lint_source(spec_path, targets):
    """Offline: run a spec's notebook source assertions against a LOCAL file.

    The `sourceContains`/`sourceLacks` checks are pure string matches over notebook source,
    so they need no tenant — but discovering a violation through a real run costs an upload,
    a ~35s Spark session start and a full job poll, and every fix pays it again. Running them
    against the file before it is ever uploaded turns that loop into a sub-second one.

    It proves the source obeys the contract, never that the build works: the data checks
    still need a real run. Exit 0 = the assertions hold, not = the suite passes.
    """
    spec = _load_spec(spec_path)
    pairs = _source_checks(spec)
    print(f"== lint-source {spec.get('task', '?')} ({spec_path}) ==")
    if not pairs:
        print("  no notebook-item check in this spec asserts sourceContains/sourceLacks — nothing to lint")
        return 0
    bindings, error = _bind_source_targets(pairs, targets)
    if error:
        _usage_error(f"USAGE  {error}")
    failures = 0
    for name, check, path in bindings:
        source_path = Path(path)
        if not source_path.is_file():
            _usage_error(f"USAGE  no such file: {path}")
        ok, reason = _source_expect(source_path.read_text(encoding="utf-8"), check.get("expect", {}))
        failures += not ok
        print(f"  {'PASS' if ok else 'FAIL'}  {check.get('id', name)}  [{name} <- {path}]\n        {reason}")
    linted = {name for name, _c, _p in bindings}
    for name, check in pairs:
        if name not in linted:
            print(f"  SKIP  {check.get('id', name)}  [{name}] — no --lint-source given for this notebook")
    print(f"== {len(bindings)} linted, {failures} failing "
          f"(source contract only — the data checks still need a real run) ==")
    return 1 if failures else 0


def _job_expect(instances, exp):
    """Match the job-run history of a notebook against expectations.

    `Completed` is asserted against the LATEST run, not "any run ever completed" — a build
    whose final state is a failure must not pass on the strength of an earlier green run.
    (The newest-first ordering is NotebookClient.list_job_instances' sort, not the API's.)
    """
    if not instances:
        return False, "no job instances — the notebook has never been run"
    latest = instances[0]
    status = latest.get("status")
    reasons = [f"{len(instances)} run(s), latest {status!r}"]
    ok = True
    if "status" in exp:
        hit = status == exp["status"]
        ok = ok and hit
        if not hit:
            reasons.append(f"expected {exp['status']!r}; failureReason={latest.get('failureReason')}")
    if "statusIn" in exp:
        hit = status in exp["statusIn"]
        ok = ok and hit
        reasons.append(f"status in {exp['statusIn']}: {'yes' if hit else 'NO'}")
    if "minRuns" in exp:
        hit = len(instances) >= exp["minRuns"]
        ok = ok and hit
        reasons.append(f"at least {exp['minRuns']} run(s): {'yes' if hit else 'NO'}")
    return ok, "; ".join(reasons)


# ── notebook check runners ────────────────────────────────────────────────────

def _run_notebook_item(check, ctx):
    """The notebook exists, is filed correctly, and its source obeys the contract."""
    import fabric_read as fr
    name = check.get("notebook") or ctx.get("notebook_name")
    if not name:
        return False, "check names no notebook and the spec has no top-level 'notebook'"
    item = fr.resolve_by_name(name, "Notebook", ctx["workspace_id"])
    if not item:
        return False, f"notebook '{name}' MISSING in workspace"
    exp = check.get("expect", {})
    reasons = [f"notebook '{name}' found ({item['id']})"]
    ok = True
    if "folder" in exp:
        f_ok, f_reason = _folder_check(ctx, item.get("folderId"), exp["folder"])
        ok = ok and f_ok
        reasons.append(f_reason)
    if exp.get("sourceContains") or exp.get("sourceLacks"):
        source = fr.get_part(item["id"], "notebook-content.py", ctx["workspace_id"], None)
        if source is None:
            # An absent part must not read as empty source: "" satisfies every
            # sourceLacks assertion, which is a false green over a notebook nothing read.
            ok = False
            reasons.append("definition has no notebook-content.py part — source unreadable, "
                           "so the source contract is NOT established")
        else:
            s_ok, s_reason = _source_expect(source, exp)
            ok = ok and s_ok
            reasons.append(s_reason)
    ctx.setdefault("notebook_ids", {})[name] = item["id"]
    return ok, "; ".join(reasons)


def _run_job_run(check, ctx):
    """The notebook was actually executed and reached the expected terminal state.

    This is the check that catches fire-and-forget: a build that POSTs the run and never
    polls leaves either no instance at all or one still `InProgress`. Assertion-only:
    the engine reads run history and never triggers a run itself.
    """
    import fabric_read as fr
    name = check.get("notebook") or ctx.get("notebook_name")
    notebook_id = (ctx.get("notebook_ids") or {}).get(name)
    if not notebook_id:
        item = fr.resolve_by_name(name, "Notebook", ctx["workspace_id"])
        if not item:
            return False, f"notebook '{name}' MISSING in workspace"
        notebook_id = item["id"]
    return _job_expect(fr.list_job_instances(notebook_id, ctx["workspace_id"]),
                       check.get("expect", {}))


# ── this family's slice of the check-type registry ────────────────────────────
# (runner, required fields, one-line description, other check-level keys) per type —
# merged by fabric_test.py; a check key outside the declared vocabulary is REFUSED at
# run time, because a key the runner never reads asserts nothing.


# ── Expect-key vocabulary ─────────────────────────────────────────────────────
#
# One entry per check type this module's runners own that does NOT already refuse unknown
# `expect` keys itself. Declared so `fabric_test.expect_key_refusal` can refuse a typo — in
# `--dry-run`, at authoring time, and again before a live run dispatches.
#
# A key here is one the runner reads; anything else asserts nothing.
EXPECT_KEYS = {
    "job-run": frozenset({"minRuns", "status", "statusIn"}),
    "notebook-item": frozenset({"folder", "sourceContains", "sourceLacks"}),
}

RUNNERS = {
    "notebook-item":   (_run_notebook_item, (), "the notebook exists, is filed right, and its source obeys the contract", ("notebook",)),
    "job-run":         (_run_job_run, (), "the notebook's LATEST run reached the expected terminal state (assert-only)", ("notebook",)),
}


# ── offline fixtures ──────────────────────────────────────────────────────────

def _self_test_checks():
    checks = []

    def expect(name, cond):
        checks.append((name, bool(cond)))

    # notebook source assertions: sourceLacks is what catches the unportable-but-green build
    ok, _r = _source_expect('df = spark.read.schema(s).csv(p)\n%pip install x',
                            {"sourceContains": ["spark.read.schema"], "sourceLacks": ["!pip", "inferSchema"]})
    expect("source-contains-and-lacks", ok)
    ok, _r = _source_expect('df = spark.read.option("inferSchema", True).csv(p)',
                            {"sourceLacks": ["inferSchema"]})
    expect("source-lacks-catches-antipattern", not ok and "found it" in _r)
    ok, _r = _source_expect('!pip install pandas', {"sourceLacks": ["!pip"]})
    expect("source-lacks-bang-pip", not ok)
    # job history: no run at all, and a stale green run under a failed latest run, both fail
    expect("job-no-runs", not _job_expect([], {"status": "Completed"})[0])
    _instances = [{"status": "Failed", "startTimeUtc": "2026-07-25T10:00:00Z",
                   "failureReason": {"errorCode": "X"}},
                  {"status": "Completed", "startTimeUtc": "2026-07-25T09:00:00Z"}]
    expect("job-latest-run-decides", not _job_expect(_instances, {"status": "Completed"})[0])
    expect("job-completed-passes",
           _job_expect([{"status": "Completed", "startTimeUtc": "2026-07-25T10:00:00Z"}],
                       {"status": "Completed", "minRuns": 1})[0])

    # --lint-source binding: a bare path is only safe when the spec asserts on ONE notebook.
    # Binding it to the wrong contract would print a confident PASS for a file that was never
    # checked against the assertions that matter — refusing is the correct failure.
    _one = [("Load_Foundry", {"id": "nb-source", "expect": {"sourceLacks": ["inferSchema"]}})]
    _two = _one + [("Load_Silver", {"id": "nb2-source", "expect": {"sourceContains": ["MERGE"]}})]
    _b, _e = _bind_source_targets(_one, ["build/nb.py"])
    expect("lint-source-bare-path-binds-when-unambiguous",
           _e is None and _b == [("Load_Foundry", _one[0][1], "build/nb.py")])
    _b, _e = _bind_source_targets(_two, ["build/nb.py"])
    expect("lint-source-bare-path-refused-when-ambiguous", _b is None and "name it explicitly" in _e)
    _b, _e = _bind_source_targets(_two, ["Load_Silver=build/silver.py"])
    expect("lint-source-named-target-binds", _e is None and _b[0][0] == "Load_Silver")
    _b, _e = _bind_source_targets(_two, ["Load_Bronze=build/bronze.py"])
    expect("lint-source-unknown-notebook-rejected", _b is None and "no notebook-item source check" in _e)
    # two checks on ONE notebook is not ambiguity — a spec may assert portability and
    # secret-freedom separately, and a bare path must lint both, not refuse
    _same = _one + [("Load_Foundry", {"id": "nb-no-secret", "expect": {"sourceLacks": ["salt-2026"]}})]
    _b, _e = _bind_source_targets(_same, ["build/nb.py"])
    expect("lint-source-one-notebook-many-checks-binds-all",
           _e is None and [c["id"] for _n, c, _p in _b] == ["nb-source", "nb-no-secret"])
    # only notebook-item checks that actually assert on source are linted — a folder-only
    # notebook check has nothing offline to prove and must not be reported as skipped work
    _spec = {"notebook": "Load_Foundry", "checks": [
        {"type": "notebook-item", "id": "nb-folder", "expect": {"folder": "Build"}},
        {"type": "notebook-item", "id": "nb-source", "expect": {"sourceLacks": ["!pip"]}},
        {"type": "job-run", "id": "run", "expect": {"status": "Completed"}}]}
    expect("lint-source-selects-only-source-checks",
           [c["id"] for _n, c in _source_checks(_spec)] == ["nb-source"])
    expect("lint-source-falls-back-to-spec-notebook",
           _source_checks(_spec)[0][0] == "Load_Foundry")

    return checks


SELF_TESTS = [_self_test_checks]
