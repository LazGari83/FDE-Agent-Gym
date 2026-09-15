"""
gym_validate.py — the agent-gym adapter over the Fabric test engine in code/.

Bundled with the `execute-gym-rep` skill. Reads a task's `validate.json` (a declarative
spec of typed checks), prints PASS/FAIL per check, and exits 0 iff every check passes —
by DELEGATING to the engine: `code/validation/fabric_test.py` and its `test_*` module family,
the single home of every check runner, expectation matcher and offline fixture. For the
check-type vocabulary run `python code/validation/fabric_test.py --list-checks`; every type and
`expect` matcher is documented in the engine's module docstring. This file contains NO
check semantics — it is the gym welding only:

    task-id resolution      AG-<TOPIC>-<NNN> -> 1_agent-gym/<topic>/<id>/validate.json
                            (TOPIC_DIRS, the code->folder map, lives here and is imported
                            by gym_run.py and preflight_probe.py)
    gym spec keys           a gym validate.json legitimately carries `capabilities`,
                            `prerequisites`, `_note` and the annotation keys `_spoiler`
                            and `_evidence` on top of the engine's spec schema; the
                            adapter tells the engine to tolerate exactly those
                            (GYM_SPEC_KEYS), so a gym run's output is never nagged
                            about the gym's own vocabulary
    prerequisites check     --dry-run validates a spec's `prerequisites` block against
                            preflight_probe.py's check catalogue — the authoring-time
                            half of the circuit's park mechanism (the probe runs the
                            checks; the park exit path is the circuit's)
    gated-check honesty     the engine's `requiresEnv` gate parks a check whose credential
                            is absent (a SKIP, never a failure). Because coverage attributes
                            capabilities at TASK granularity, --dry-run refuses a spec whose
                            EVERY check is gated while it declares capabilities — see
                            `_gate_attribution_problems`. `--no-park` is the inverse mode for
                            the rep that means to PROVE a gated leg: a park becomes a failure
    gym exit-code contract  0 = every check passed · 1 = anything else (a failed check,
                            a bad task id, dry-run problems, an engine usage error).
                            gym_steps/common.py and the circuit branch on 0-vs-nonzero
                            and parse the engine's `== N passed, M failed, K skipped ==`
                            tally line, which the engine prints and this adapter never
                            wraps or reformats. (The engine's own CLI distinguishes
                            usage/spec errors as exit 2; the gym contract predates that
                            split and keeps 1.)
    run ledger              every live run is logged via rep_log.py (telemetry never
                            fails a validation)

Usage (from anywhere in the repo):
    python .claude/skills/execute-gym-rep/gym_validate.py AG-ONT-001 [--graph-id GUID] [--lakehouse-id GUID]
    python .claude/skills/execute-gym-rep/gym_validate.py path/to/validate.json [--graph-id GUID]
    python .claude/skills/execute-gym-rep/gym_validate.py AG-LAK-001 --lint-source build/notebook.py
    python .claude/skills/execute-gym-rep/gym_validate.py AG-CICD-001 --dry-run   # offline authoring gate
    python .claude/skills/execute-gym-rep/gym_validate.py --self-test             # offline: gym welds + engine aggregate

The positional argument is either a task id (`AG-<TOPIC>-<NNN>`) or a path to a
validate.json. Three modes need no tenant and no .env: `--self-test`, `--dry-run` and
`--lint-source`. Live runs bind to `--workspace-id` (default FABRIC_WORKSPACE_ID, from the
process env or .env) and inherit the engine's read-only discipline: a
`spark-sql` statement not beginning with SELECT/WITH/DESCRIBE/SHOW is refused as a failed
check — validation asserts, it never mutates, and no gym spec writes through the
validator (all 100+ existing spark-sql checks are reads).

`from gym_validate import TOPIC_DIRS` must stay stdlib-light — gym_run.py imports the id
map at startup — so the engine import is lazy: inside functions, never at module level.
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

# Bootstrap: make the engine (and the Fabric toolkit it composes) importable. This adapter
# lives at <skills>/execute-gym-rep/, so the repo root is three parents up. The
# insert is cheap; actually importing fabric_test is deferred to the functions that use it.
REPO_ROOT = Path(__file__).resolve().parents[3]
CODE_DIR = REPO_ROOT / "code"
sys.path.insert(0, str(CODE_DIR))
import toolkit_path  # noqa: F401,E402
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_shared"))
from kbmd import SKILLS_DIR  # noqa: E402

# Check questions and error text carry em dashes; a cp1252 console would mangle or crash on them.
# stderr too: SystemExit messages carry them as well, and it is the stream a failing
# usage error is read from.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover - non-reconfigurable stream
        pass

# Gym topic code -> agent-gym subfolder. Extend as topics gain gyms (GRA=graph, ...).
TOPIC_DIRS = {"ONT": "ontology", "OMR": "openmirror", "LAK": "lakehouse", "AKV": "key-vault",
              "CICD": "cicd", "ING": "ingestion", "DAT": "data-agent", "APP": "azure-app",
              "PIP": "pipelines"}
_ID_RE = re.compile(r"^AG-([A-Za-z]+)-(\d+)$")

# Top-level validate.json keys that belong to the GYM, not the engine's spec schema. The
# engine notices unknown top-level keys with a one-line banner; these are passed as its
# `tolerated_keys` so a gym run is never nagged about the gym's own vocabulary. Beyond the
# structural three, tasks carry underscore annotation keys (`_spoiler` on AG-LAK-011,
# `_evidence` on AG-LAK-012/013); the self-test sweeps every shipped spec so a new
# annotation key can't drift past this tuple unnoticed.
GYM_SPEC_KEYS = ("capabilities", "prerequisites", "_note", "_spoiler", "_evidence")


def _resolve_spec(arg):
    """Turn a task id (AG-ONT-001) into its validate.json path; pass paths through."""
    m = _ID_RE.match(arg.strip())
    if not m:
        return Path(arg)
    topic_code, num = m.group(1).upper(), int(m.group(2))
    topic = TOPIC_DIRS.get(topic_code)
    if not topic:
        raise SystemExit(f"Unknown gym topic code '{topic_code}' in id '{arg}'. Known: {sorted(TOPIC_DIRS)}")
    # Task folders are named by their canonical id (AG-ONT-001). A trailing '*' tolerates an
    # optional descriptive suffix (AG-ONT-001-fleetlink) if one is ever added.
    folder = f"AG-{topic_code}-{num:03d}"
    matches = sorted((REPO_ROOT / "1_agent-gym" / topic).glob(f"{folder}*/validate.json"))
    if not matches:
        raise SystemExit(f"No task for id '{arg}' (looked for 1_agent-gym/{topic}/{folder}*/validate.json)")
    if len(matches) > 1:
        raise SystemExit(f"Ambiguous id '{arg}': {[str(p) for p in matches]}")
    return matches[0]


def _read_spec(spec_path):
    """Parse a spec the GYM way: a missing file raises (traceback, exit 1) rather than the
    engine's exit-2 usage error — the historical contract the circuit may branch on."""
    return json.loads(Path(spec_path).read_text(encoding="utf-8"))


# ── prerequisites (the park mechanism's structural half) ──────────────────────

def _prereq_problems(spec):
    """Structural check of a spec's `prerequisites` block (see preflight_probe.py).

    A prerequisite naming a check the probe does not implement is a broken reference exactly
    like a bad capability id: it reads as covered and tests nothing. The check catalogue is
    imported from the probe rather than copied — one map, one place to change it.
    """
    prereqs = spec.get("prerequisites")
    if prereqs is None:
        return []  # the baseline covers most tasks; no block is the normal case
    if not isinstance(prereqs, list):
        return ["'prerequisites' must be a list"]
    try:
        sys.path.insert(0, str(SKILLS_DIR / "execute-gym-rep"))
        from preflight_probe import CHECKS, BASELINE
    except Exception as e:  # the probe is optional tooling; never block authoring on it
        return [f"could not import preflight_probe to verify prerequisites ({e})"]
    problems = []
    for i, pre in enumerate(prereqs):
        name = pre.get("check")
        where = f"prerequisites[{i}]"
        if name not in CHECKS:
            problems.append(f"{where}: unknown check {name!r} (known: {', '.join(CHECKS)})")
        elif name in BASELINE:
            problems.append(f"{where}: {name!r} is already in the baseline — drop it")
        for field in ("why", "remedy"):
            if not pre.get(field):
                problems.append(f"{where} ({name}): needs a '{field}' — a member who fails this "
                                "check gets told nothing actionable without it")
    return problems


# ── offline gates (gym exit contract: problems exit 1) ────────────────────────

# Item types whose DISPLAY NAME the Fabric service constrains, and the constraint. The gym's
# own `<task-id>-<Scenario>` convention uses hyphens and is therefore unusable for these —
# a hyphenated name fails its first provision run with `400 BadArtifactCreateRequest`, and
# the rejected create still reserves the name so the retry 409s. This gate is the
# enforcement, so a bad name fails offline in a second instead of live in twenty minutes.
_NAME_CONSTRAINED = {
    "ontology":   "letters, digits and underscores only, must start with a letter",
    "graphModel": "letters, digits and underscores only, must start with a letter",
    "lakehouse":  "letters, digits and underscores only, must start with a letter",
    "graphqlApi": "letters, digits and underscores only, must start with a letter",
}

# This map is a WHITELIST, and a whitelist is only as good as its maintenance. It once
# missed `graphqlApi`, and a rep duly parked on a hyphenated GraphQLApi display name --
# the exact failure this gate exists to catch offline in a
# second. A denylist-by-default would be wrong (data agents and
# semantic models legitimately take hyphens), so the rule is: WHEN A TOPIC ADDS A TARGET KEY
# NAMING A FABRIC ITEM, decide whether that item type constrains its display name and add it
# here if it does. `python -c "..."` over 1_agent-gym/*/*/validate.json lists the keys in use.


# The same gate for AZURE resource targets, whose naming rules are the provider's and have
# nothing to do with Fabric's. Each entry is (regex, min, max, rule text, Learn reference).
# Only rules Microsoft actually publishes are here: `appInsights` is deliberately ABSENT
# because the Microsoft.Insights/components property table states no name pattern, and
# inventing one would reject names the service accepts.
_AZURE_NAME_CONSTRAINED = {
    "logAnalytics": (r"[A-Za-z0-9][A-Za-z0-9-]+[A-Za-z0-9]", 4, 63,
                     "letters, digits and hyphens; must start and end alphanumeric",
                     "learn.microsoft.com/azure/templates/microsoft.operationalinsights/"
                     "2023-09-01/workspaces"),
    "foundryAccount": (r"[a-z0-9][a-z0-9-]*", 2, 64,
                       "LOWERCASE letters, digits and hyphens only — and GLOBALLY unique, "
                       "so a name free in one tenant may be taken in another",
                       "learn.microsoft.com/azure/foundry/how-to/create-resource-template "
                       "(the AccountNameInvalid troubleshooting row)"),
    "foundryProject": (r"[a-zA-Z0-9][a-zA-Z0-9_.\-]*", 2, 64,
                       "letters, digits, underscore, dot and hyphen; must start alphanumeric",
                       "learn.microsoft.com/azure/templates/microsoft.cognitiveservices/"
                       "accounts/projects"),
}


def _azure_name_problems(spec):
    """Flag an Azure target whose name the provider will reject at create time.

    The Fabric gate above exists because AG-DAT-004 died on a hyphenated ontology name that
    the wiki had already documented. This is the same failure one plane over: an ARM create
    with an illegal name is a 400 the rep meets after it has already built everything else,
    and the rules differ per provider (Foundry accounts are lowercase-only, Log Analytics
    workspaces are not, and neither matches the gym's `AG-DAT-011-Scenario` convention).
    """
    out = []
    for key, (pattern, lo, hi, rule, ref) in _AZURE_NAME_CONSTRAINED.items():
        name = spec.get(key)
        if not isinstance(name, str) or not name:
            continue
        if not re.fullmatch(pattern, name) or not (lo <= len(name) <= hi):
            out.append(f"{key} {name!r} is not a legal Azure resource name ({rule}; "
                       f"{lo}-{hi} characters). See {ref}. The gym's <task-id>-<Scenario> "
                       f"convention does not survive here — derive a compliant name instead.")
    return out


def _item_name_problems(spec):
    """Flag a spec naming a constrained item type with characters the service rejects."""
    out = []
    for key, rule in _NAME_CONSTRAINED.items():
        name = spec.get(key)
        if not isinstance(name, str) or not name:
            continue
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name):
            out.append(
                f"{key} {name!r} is not a legal Fabric item name ({rule}). The gym's "
                f"<task-id>-<Scenario> convention cannot be applied literally here — use "
                f"underscores. A create with an illegal name is 400 BadArtifactCreateRequest "
                f"AND reserves the name, so the retry is 409 "
                f"ItemDisplayNameNotAvailableYet.")
    return out


def _gate_attribution_problems(spec):
    """The gym-side honesty gate over the engine's `requiresEnv` parks.

    A parked check asserts NOTHING, and the gym's bookkeeping attributes capabilities at TASK
    granularity: `capability_coverage.py` joins a task's `capabilities: [...]` to the passed
    gym rep reports in `2_raw/gym-rep-reports/` and calls every id on that list `proven` the moment the
    task lands — it has no per-check attribution and cannot get one from a validate.json.

    So a spec whose EVERY check is gated is a trap: a rep with none of the variables set
    parks all of them, exits 0 (parks are not failures, which is the point), writes a
    `status: passed` gym rep report, and the coverage report then reads its declared
    capabilities as proven on a run that asserted nothing — precisely the false green
    `status: placeholder` wiki pages are forbidden from creating on the wiki axis.

    The rule is therefore: a spec that declares capabilities must keep at least one UNGATED
    check. Gated checks then only ever ADD evidence to a capability the ungated ones already
    exercise. The residual that no script can judge — that no single declared capability
    rests on a gated check ALONE — is the author's, and belongs in the spec's `_note`.
    """
    checks = spec.get("checks") or []
    caps = spec.get("capabilities") or []
    if not checks or not caps:
        return []
    if all(c.get("requiresEnv") for c in checks):
        return [f"every check is gated on 'requiresEnv' while the spec declares {caps} — a rep "
                "with none of those variables set would park every check, exit 0, land a "
                "passed gym rep report, and capability_coverage.py would read those capabilities "
                "as `proven` on a run that asserted nothing. Keep at least one ungated check, "
                "or move the gated ones into a task that declares no capabilities"]
    return []


def _dry_run(spec_path):
    """Offline structural check of a validate.json — no tenant, no network.

    Authoring-time gate (the `create-gym-task` skill runs it): the id resolves to a spec,
    the spec parses, every check names a known type and carries that type's required
    fields, every check states an expectation, and the `prerequisites` block (gym-only) is
    well-formed. It says nothing about whether the expected VALUES are right — only a real
    run against a real build proves those. The check-type table and each type's required
    fields come from the ENGINE's registry, so the gate can never drift from the runners.
    """
    from fabric_test import (_CHECK_TYPES, TARGET_KEYS, check_key_refusal,
                             expect_key_refusal, env_gate)
    required = {name: entry[1] for name, entry in _CHECK_TYPES.items()}
    spec = _read_spec(spec_path)
    print(f"== dry run {spec.get('task', '?')} ({spec_path}) ==")
    problems = []
    # The target list is the ENGINE's, imported rather than copied. This block used to carry
    # its own transcription with a comment telling the next editor to keep the two identical,
    # and it drifted three times regardless: `deploymentPipeline` was shadowed away by two
    # concurrent worktrees, `workspace`/`dataAgent` went missing so no data-agent task could
    # pass the gate (AG-DAT-002), and `app`/`baseUrl` were absent when the web-application
    # family landed, making a pure test_webapp task unauthorable (AG-APP-005). A gate that
    # rejects a target the engine accepts silently un-authors valid tasks, so the duplication
    # is gone instead of re-synchronised.
    target = next((spec[k] for k in TARGET_KEYS if spec.get(k)), None)
    if not target:
        problems.append("spec names no target — one of "
                        + ", ".join(repr(k) for k in TARGET_KEYS) + " at the top level")
    problems.extend(_item_name_problems(spec))
    problems.extend(_azure_name_problems(spec))
    checks = spec.get("checks", [])
    if not checks:
        problems.append("spec has no checks")
    problems.extend(_prereq_problems(spec))
    problems.extend(_gate_attribution_problems(spec))
    for i, check in enumerate(checks):
        cid = check.get("id", f"#{i}")
        ctype = check.get("type")
        if ctype not in required:
            problems.append(f"{cid}: unknown check type {ctype!r} (known: {sorted(required)})")
            continue
        for field in required[ctype]:
            if not check.get(field):
                problems.append(f"{cid}: {ctype} check needs a '{field}'")
        # Check-level keys are the engine's vocabulary too (a key the runner never reads
        # asserts nothing — the axe-scan/`viewports` hole AG-APP-006 found). Imported, not
        # transcribed, for the same reason the target list above is.
        refusal = check_key_refusal(check)
        if refusal:
            problems.append(f"{cid}: {refusal}")
        # ...and the same refusal one level down, inside `expect` — the gate that catches an
        # expect key no matcher reads at authoring time, instead of shipping a spec that can
        # never pass.
        expect_refusal = expect_key_refusal(check)
        if expect_refusal:
            problems.append(f"{cid}: {expect_refusal}")
        # The environment gate's structural half (a malformed `requiresEnv`, an ungated
        # `{env:NAME}` reference), evaluated against an EMPTY environment so the gate's verdict
        # does not depend on what this machine happens to hold. A merely-absent variable is a
        # park, which is authoring-legal — that is the whole mechanism.
        gate, gate_reason = env_gate(check, env={})
        if gate == "refuse":
            problems.append(f"{cid}: {gate_reason}")
        if not check.get("expect"):
            problems.append(f"{cid}: no 'expect' — a check that asserts nothing always passes")
        gated = (" [gated: " + ",".join((check.get("requiresEnv") or {}).get("vars", [])) + "]"
                 if isinstance(check.get("requiresEnv"), dict) else "")
        print(f"  {ctype:<13} {cid}:{gated} {check.get('question', '')[:80]}")
    for problem in problems:
        print(f"  PROBLEM  {problem}")
    print(f"== {len(checks)} checks, {len(problems)} problems "
          f"(structure only — expected values are not verified) ==")
    return 1 if problems else 0


def _lint_source(spec_path, targets):
    """Offline: run a spec's notebook source assertions against a LOCAL file.

    The matchers (`_source_checks` / `_bind_source_targets` / `_source_expect`) live in the
    engine's test_notebook.py — this wrapper only keeps the gym's usage-error contract
    (SystemExit message, exit 1, not the engine's exit 2). Discovering a violation through
    a real run costs an upload, a ~35s Spark session start and a full job poll; running the
    assertions against the file before it is ever uploaded turns that loop into a
    sub-second one. Exit 0 = the assertions hold, not = the task passes.
    """
    from test_notebook import _bind_source_targets, _source_checks, _source_expect
    spec = _read_spec(spec_path)
    pairs = _source_checks(spec)
    print(f"== lint-source {spec.get('task', '?')} ({spec_path}) ==")
    if not pairs:
        print("  no notebook-item check in this spec asserts sourceContains/sourceLacks — nothing to lint")
        return 0
    bindings, error = _bind_source_targets(pairs, targets)
    if error:
        raise SystemExit(f"  USAGE  {error}")
    failures = 0
    for name, check, path in bindings:
        source_path = Path(path)
        if not source_path.is_file():
            raise SystemExit(f"  USAGE  no such file: {path}")
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


# ── the live run: delegate to the engine ──────────────────────────────────────

def _live_run(spec_path, args):
    """Run the task's checks against the tenant through `fabric_test.run_suite`.

    The engine prints everything (target-workspace banner, per-check PASS/FAIL lines, the
    `== N passed, ... ==` tally gym_steps/common.py parses) — nothing is wrapped or
    reformatted here. Gym weldings around it: the spec is read gym-side first (missing
    file keeps the historical traceback-exit-1), unknown `--checks` ids keep the
    historical FATAL-exit-1, GYM_SPEC_KEYS are tolerated, and an engine usage error
    (SystemExit 2 — e.g. an unresolvable graph id) is mapped back to the gym's exit 1.
    """
    spec = _read_spec(spec_path)
    wanted = [c.strip() for c in (getattr(args, "checks", None) or "").split(",") if c.strip()]
    if wanted:
        known = {c.get("id", c["type"]) for c in spec.get("checks", [])}
        unknown = [w for w in wanted if w not in known]
        if unknown:
            print(f"FATAL: no check with id {unknown} in this spec. "
                  f"Known: {sorted(c.get('id', c['type']) for c in spec.get('checks', []))}")
            return 1
    import fabric_test
    try:
        verdict = fabric_test.run_suite(
            spec_path, workspace=args.workspace_id, graph_id=args.graph_id,
            lakehouse_id=args.lakehouse_id, mirror_id=args.mirror_id,
            only_checks=wanted or None, tolerated_keys=GYM_SPEC_KEYS,
            no_park=getattr(args, "no_park", False))
    except SystemExit as exc:
        return 1 if exc.code else 0
    return 0 if verdict["failed"] == 0 else 1


# ── offline self-test (no network): the gym welds, then the engine aggregate ──

def _self_test():
    # Offline by contract ("no tenant and no .env"), but the engine aggregate below imports
    # check families whose config requires FABRIC_WORKSPACE_ID at import. Same placeholder
    # convention as code/tests/conftest.py, so the promise holds on a fresh clone.
    os.environ.setdefault("FABRIC_WORKSPACE_ID", "00000000-0000-0000-0000-000000000000")
    checks = []

    def expect(name, cond):
        checks.append((name, bool(cond)))

    # task-id resolution (uses the real repo layout; these tasks must exist)
    for tid in ("AG-ONT-001", "AG-OMR-001", "AG-AKV-001", "AG-CICD-001"):
        name = f"id-resolve-{tid.lower()}"
        try:
            resolved = _resolve_spec(tid)
            expect(name, resolved.name == "validate.json" and tid in str(resolved))
        except SystemExit:
            expect(name, False)
    # a bare path passes through untouched
    expect("path-passthrough",
           str(_resolve_spec("some/validate.json")) in ("some/validate.json", "some\\validate.json"))
    try:
        _resolve_spec("AG-ZZZ-001")
        expect("id-unknown-topic-rejected", False)
    except SystemExit as exc:
        expect("id-unknown-topic-rejected", "Unknown gym topic code" in str(exc))
    # TOPIC_DIRS and the tracker's topic table must agree — one code per folder, no drift
    sys.path.insert(0, str(SKILLS_DIR / "check-training-progress"))
    import tracker_sync
    expect("topic-tables-agree",
           all(tracker_sync.topic_meta(folder)["code"] == code
               for code, folder in TOPIC_DIRS.items()))

    # prerequisites parsing — the structural half of the circuit's park mechanism,
    # verified against preflight_probe.py's REAL check catalogue (no copy to drift)
    expect("prereq-absent-is-clean", _prereq_problems({}) == [])
    expect("prereq-non-list-rejected",
           _prereq_problems({"prerequisites": {"check": "x"}}) == ["'prerequisites' must be a list"])
    probs = _prereq_problems({"prerequisites": [{"check": "no-such-check", "why": "w", "remedy": "r"}]})
    expect("prereq-unknown-check-flagged", any("unknown check" in p for p in probs))
    probs = _prereq_problems({"prerequisites": [{"check": "fabric-auth", "why": "w", "remedy": "r"}]})
    expect("prereq-baseline-restated-flagged", any("already in the baseline" in p for p in probs))
    probs = _prereq_problems({"prerequisites": [{"check": "key-vault"}]})
    expect("prereq-missing-why-and-remedy-flagged",
           any("needs a 'why'" in p for p in probs) and any("needs a 'remedy'" in p for p in probs))

    # the gated-check honesty gate: a spec may not rest its declared capabilities on checks
    # that can all park, because coverage attributes capabilities at TASK granularity
    _gate = {"vars": ["APP_USER_TOKEN"], "remedy": "acquire a delegated token"}
    expect("gate-attribution-clean-when-one-check-is-ungated",
           _gate_attribution_problems(
               {"capabilities": ["APP-C24"],
                "checks": [{"id": "a", "type": "http-probe"},
                           {"id": "b", "type": "http-probe", "requiresEnv": _gate}]}) == [])
    probs = _gate_attribution_problems(
        {"capabilities": ["APP-C24"],
         "checks": [{"id": "b", "type": "http-probe", "requiresEnv": _gate}]})
    expect("gate-attribution-refuses-all-gated-with-capabilities",
           len(probs) == 1 and "asserted nothing" in probs[0] and "APP-C24" in probs[0])
    expect("gate-attribution-allows-all-gated-without-capabilities",
           _gate_attribution_problems(
               {"checks": [{"id": "b", "type": "http-probe", "requiresEnv": _gate}]}) == [])
    # and the same invariant swept across every SHIPPED task, so a future all-gated spec
    # cannot land without the dry-run gate having refused it
    all_gated = {}
    for vpath in sorted((REPO_ROOT / "1_agent-gym").glob("*/*/validate.json")):
        found = _gate_attribution_problems(_read_spec(vpath))
        if found:
            all_gated[vpath.parent.name] = found
    expect("no-shipped-spec-rests-capabilities-on-gated-checks-alone", all_gated == {})
    if all_gated:
        print(f"        specs whose capabilities could be 'proven' by parked checks: {all_gated}")

    # engine welding: gym keys are suppressed for the engine, and only gym keys
    import fabric_test
    gym_spec = {"task": "t", "checks": [], "capabilities": ["X-C01"],
                "prerequisites": [], "_note": "n", "_spoiler": "s", "_evidence": "e"}
    expect("gym-keys-suppressed-for-engine",
           fabric_test._unknown_top_keys(gym_spec, GYM_SPEC_KEYS) == [])
    expect("gym-keys-noticed-without-toleration",
           fabric_test._unknown_top_keys(gym_spec)
           == ["_evidence", "_note", "_spoiler", "capabilities", "prerequisites"])
    expect("gym-key-set-locked",
           set(GYM_SPEC_KEYS) == {"capabilities", "prerequisites", "_note", "_spoiler", "_evidence"})
    # every SHIPPED spec's vocabulary is tolerated — the live-path notice must never fire
    # on a real task (this is the sweep that would have caught _spoiler/_evidence drifting
    # in on AG-LAK-011..013 while the dry-run gate, which skips the notice, stayed green)
    nagging = {}
    for vpath in sorted((REPO_ROOT / "1_agent-gym").glob("*/*/validate.json")):
        unk = fabric_test._unknown_top_keys(_read_spec(vpath), GYM_SPEC_KEYS)
        if unk:
            nagging[vpath.parent.name] = unk
    expect("gym-vocabulary-fully-tolerated", nagging == {})
    # The same sweep for keys INSIDE `expect`. A check expecting a key no matcher reads
    # (e.g. `rowCount` on `spark-sql`) fails on first execution with nothing having ever
    # said so — this sweep says so offline.
    unreadable = {}
    for vpath in sorted((REPO_ROOT / "1_agent-gym").glob("*/*/validate.json")):
        for chk in _read_spec(vpath).get("checks", []):
            if fabric_test.expect_key_refusal(chk):
                unreadable.setdefault(vpath.parent.name, []).append(chk.get("id"))
    expect("shipped-specs-carry-no-unreadable-expect-key", unreadable == {})
    if unreadable:
        print(f"        specs with unreadable expect keys: {unreadable}")
    if nagging:
        print(f"        specs with untolerated keys (extend GYM_SPEC_KEYS): {nagging}")
    # The same sweep one level down: every CHECK-level key every shipped task uses must be
    # declared by its check type. The engine REFUSES an undeclared key (a key no runner
    # reads asserts nothing), so an undeclared-but-used key would fail 105 tasks' worth of
    # reps at once — and a runner that quietly gains an option without declaring it would
    # be caught here instead of by the next rep to point it at a real target (AG-APP-006).
    undeclared = {}
    for vpath in sorted((REPO_ROOT / "1_agent-gym").glob("*/*/validate.json")):
        for check in _read_spec(vpath).get("checks", []):
            for key in fabric_test.unknown_check_keys(check):
                undeclared.setdefault(f"{check.get('type')}.{key}", []).append(vpath.parent.name)
    expect("gym-check-keys-all-declared", undeclared == {})
    if undeclared:
        print(f"        check keys no runner declares: {undeclared}")
    # the dry-run gate derives its required-field table from the engine registry
    required = {name: entry[1] for name, entry in fabric_test._CHECK_TYPES.items()}
    expect("engine-registry-supplies-required-fields",
           required.get("gql") == ("gql",) and required.get("spark-sql") == ("sql",)
           and required.get("workspace-item") == ("item",) and len(required) >= 29)
    expect("engine-run-suite-exposed", callable(fabric_test.run_suite))
    # the engine owns the gate; the adapter only welds it. If `requiresEnv` ever stops being
    # universal, every gated task would be REFUSED rather than parked — catch that here.
    expect("engine-env-gate-exposed",
           callable(fabric_test.env_gate) and callable(fabric_test.substitute_env)
           and "requiresEnv" in fabric_test._UNIVERSAL_CHECK_KEYS)
    _outcome, _why = fabric_test.env_gate(
        {"type": "http-probe",
         "requiresEnv": {"vars": ["AG_GYM_ABSENT_VAR"], "remedy": "say how"}}, env={})
    expect("engine-park-carries-the-remedy",
           _outcome == "park" and "AG_GYM_ABSENT_VAR" in _why and "say how" in _why
           and "not passed" in _why)
    # the tally line gym_steps/common.py parses is the engine's == N passed, ... == print
    tally_re = re.compile(r"== (\d+) passed, (\d+) failed, (\d+) skipped ==")
    m = tally_re.search("== 5 passed, 1 failed, 2 skipped ==")
    expect("tally-line-format-parses", m is not None and m.groups() == ("5", "1", "2"))

    gym_failures = [n for n, ok in checks if not ok]
    for name, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")

    engine_checks = fabric_test.collect_self_test_checks()
    engine_failures = [n for n, ok in engine_checks if not ok]
    for name, ok in engine_checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")

    if gym_failures or engine_failures:
        print(f"SELF-TEST FAILED: {gym_failures + engine_failures}")
        return 1
    print(f"SELF-TEST PASSED ({len(checks)} gym + {len(engine_checks)} engine checks)")
    return 0


# ── CLI ───────────────────────────────────────────────────────────────────────

def main(argv=None):
    p = argparse.ArgumentParser(description="Validate an agent-gym task over the Fabric REST API.")
    p.add_argument("spec", nargs="?", help="task id (AG-ONT-001) or path to a validate.json")
    p.add_argument("--graph-id", dest="graph_id", help="graph model GUID (skips name lookup)")
    p.add_argument("--mirror-id", dest="mirror_id", help="mirrored database GUID (skips name lookup)")
    p.add_argument("--workspace-id", dest="workspace_id", help="workspace GUID (defaults to FABRIC_WORKSPACE_ID)")
    p.add_argument("--lakehouse-id", dest="lakehouse_id", help="lakehouse GUID (required for preflight checks)")
    p.add_argument("--checks", help="comma-separated check ids to re-run after a fix "
                                    "(a PARTIAL run — never sufficient to call the rep done)")
    p.add_argument("--no-park", dest="no_park", action="store_true",
                   help="FAIL a check whose 'requiresEnv' variables are absent instead of "
                        "parking it — use it on the rep that is meant to PROVE a gated leg, "
                        "so a missing credential cannot pass as a parked one")
    p.add_argument("--self-test", action="store_true", help="run offline gym + engine checks and exit")
    p.add_argument("--dry-run", action="store_true", dest="dry_run",
                   help="offline: check the spec parses and every check is well-formed (authoring gate)")
    p.add_argument("--lint-source", dest="lint_source", action="append", metavar="[NOTEBOOK=]PATH",
                   help="offline: run the spec's sourceContains/sourceLacks assertions against a "
                        "local notebook file BEFORE uploading it (repeatable; prefix NOTEBOOK= "
                        "when the spec asserts on more than one notebook)")
    args = p.parse_args(argv)

    if args.self_test:
        return _self_test()
    if not args.spec:
        p.error("a task id or validate.json path is required (or use --self-test)")
    if args.lint_source:
        return _lint_source(_resolve_spec(args.spec), args.lint_source)
    if args.dry_run:
        return _dry_run(_resolve_spec(args.spec))
    spec_path = _resolve_spec(args.spec)
    started = time.monotonic()
    rc = _live_run(spec_path, args)
    try:                                  # run ledger; telemetry never fails a validation
        import rep_log
        rep_log.log_validate(Path(spec_path).parent.name, time.monotonic() - started, rc)
    except Exception:
        pass
    return rc


if __name__ == "__main__":
    sys.exit(main())
