"""
gym_run.py — deterministic plan engine for agent-gym atomic reps. All topics.

Bundled with the `execute-gym-rep` skill, beside `gym_validate.py`, and built on the same
idea as validate.json: a declarative spec of **typed steps**, executed by a generic engine.
A gym task's build decomposes into a deterministic *plan* (fixture, items, uploads, runs,
then the acceptance test) plus a task-specific *payload* (the one artifact an agent codes,
passed in at run time via --payload and consumed by whichever step declares it).

This file is the engine and knows nothing about any Fabric item type. Step types — their
executors, required fields, what they provide to and need from each other, and their extra
structural checks — are declared in the `gym_steps/` package, which topics extend (see
`gym_steps/__init__.py`). Plan validation is derived entirely from those declarations.

The plan is the AGENT'S OUTPUT, never the task's input. Drawing the plan from the task
statement is part of the exercise: the agent reads `task.md` (its brief), pulls the topic's
atoms, and *proposes* a plan — a scratch file passed via --plan — plus the payload. A task
folder must not contain a plan file (that would be the answer sheet; --self-test asserts
none does). The plan that passed is recorded in the gym rep report (`2_raw/gym-rep-reports/`, which reps
are already forbidden to read), so provenance survives without leaking into the exercise.

    {
      "task": "AG-LAK-006",
      "steps": [
        {"type": "provision", "script": "provision.py"},
        {"type": "workspace-folder", "name": "Lakehouse"},
        ...
        {"type": "validate"}
      ]
    }

Usage (from anywhere in the repo):
    python .claude/skills/execute-gym-rep/gym_run.py AG-LAK-006 --plan FILE
        [--payload FILE] [--skip TYPE ...] [--only TYPE ...] [--report PATH]
    python .claude/skills/execute-gym-rep/gym_run.py --self-test        # offline, no tenant

Exit codes: 0 = the plan ran to its terminal `validate` step and the acceptance test
passed; 1 = anything else — a step raised (the step, error and elapsed time are printed),
the checks failed, or the acceptance test never ran. One nonzero code, deliberately:
`SystemExit("...")` exits 1 whatever the message, so a documented 2 was never produced and
anything branching on it read the wrong answer. Match `gym_validate.py`'s 0-vs-nonzero.

Telemetry: every step is timed and labelled `fabric` (waiting on the platform) or `local`.
The summary separates the two so "agent time vs Fabric time" is measured, not estimated —
a roughly one-minute-of-agent-time-per-rep target is asserted against this output.
"""
import argparse
import functools
import inspect
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
REPO_ROOT = SKILL_DIR.parents[2]
CODE_DIR = REPO_ROOT / "code"
sys.path.insert(0, str(CODE_DIR))
import toolkit_path  # noqa: F401,E402
sys.path.insert(0, str(SKILL_DIR))

from gym_validate import _ID_RE, TOPIC_DIRS  # noqa: E402  (stdlib-only import; shares the id map + regex)
from gym_steps import load_all               # noqa: E402

print = functools.partial(print, flush=True)  # noqa: A001 — live output when redirected

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass


# ── task / plan resolution ──────────────────────────────────────

def resolve_task_dir(task_id: str) -> Path:
    m = _ID_RE.match(task_id.strip())
    if not m:
        raise SystemExit(f"'{task_id}' is not a task id (expected AG-<TOPIC>-<NNN>)")
    topic_code, num = m.group(1).upper(), int(m.group(2))
    topic = TOPIC_DIRS.get(topic_code)
    if not topic:
        raise SystemExit(f"Unknown gym topic code '{topic_code}'. Known: {sorted(TOPIC_DIRS)}")
    folder = REPO_ROOT / "1_agent-gym" / topic / f"AG-{topic_code}-{num:03d}"
    if not folder.is_dir():
        raise SystemExit(f"No task folder at {folder}")
    return folder


def check_plan(plan: dict, task_id: str = None) -> list[str]:
    """Structural validation of a proposed plan, derived entirely from the step-type
    declarations. Returns a list of problems (empty = valid)."""
    registry = load_all()
    errors = []
    if task_id and plan.get("task") != task_id:
        errors.append(f"plan task '{plan.get('task')}' != requested '{task_id}'")
    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        return errors + ["'steps' must be a non-empty list"]

    available: set = set()
    terminal_positions = []
    for i, spec in enumerate(steps):
        stype = spec.get("type") if isinstance(spec, dict) else None
        declared = registry.get(stype)
        if declared is None:
            errors.append(f"steps[{i}]: unknown type '{stype}' "
                          f"(known: {', '.join(sorted(registry))})")
            continue
        where = f"steps[{i}] ({stype})"
        for field in declared.required:
            if not spec.get(field):
                errors.append(f"{where}: missing required field '{field}'")
        if declared.check:
            errors.extend(f"{where}: {problem}" for problem in declared.check(spec))
        for need in declared.needs_for(spec):
            if need not in available:
                errors.append(f"{where}: needs a '{need}' from an earlier step")
        available |= set(declared.provides)
        if declared.terminal:
            terminal_positions.append(i)

    if not terminal_positions:
        errors.append("no terminal step — every plan must end at the acceptance test "
                      "('validate')")
    elif terminal_positions != [len(steps) - 1]:
        errors.append("the terminal 'validate' step must be the single final step")
    return errors


def load_plan(path: Path, task_id: str) -> dict:
    if not path.exists():
        raise SystemExit(f"plan not found: {path}")
    plan = json.loads(path.read_text(encoding="utf-8"))
    errors = check_plan(plan, task_id)
    if errors:
        raise SystemExit("plan invalid:\n  " + "\n  ".join(errors))
    return plan


# ── the engine ──────────────────────────────────────────────────────

class Runner:
    """Executes a plan's step list, timing every step. Step executors reach shared
    state through `context` (ids), `cache` (memoised clients), `task_dir`, and `args`."""

    def __init__(self, task_id: str, task_dir: Path, plan: dict, args):
        self.task_id = task_id
        self.task_dir = task_dir
        self.plan = plan
        self.args = args
        self.telemetry: list[dict] = []      # {name, category, seconds, note}
        self.context: dict = {}
        self.cache: dict = {}
        self.tally: tuple | None = None      # (passed, failed, skipped), set by validate

    def execute(self):
        registry = load_all()
        steps = self.plan["steps"]
        print(f"gym_run: {self.task_id} — {len(steps)} step(s)")
        for spec in steps:
            declared = registry[spec["type"]]
            if self.args.only and spec["type"] not in self.args.only:
                continue
            if self.args.skip and spec["type"] in self.args.skip:
                continue
            self._timed(declared.label_for(spec), declared.category,
                        lambda d=declared, s=spec: d.run(self, s))
        self.summary()
        if self.args.report:
            self.write_report(Path(self.args.report))

    def _timed(self, name: str, category: str, fn):
        start = time.monotonic()
        try:
            note = fn()
        except Exception as exc:
            seconds = time.monotonic() - start
            self.telemetry.append({"name": name, "category": category,
                                   "seconds": seconds, "note": f"FAILED: {exc}"})
            self.summary()
            raise SystemExit(f"\nPLAN FAILED at step '{name}' after {seconds:.1f}s: {exc}") from exc
        seconds = time.monotonic() - start
        self.telemetry.append({"name": name, "category": category,
                               "seconds": seconds, "note": note or ""})
        print(f"  [{seconds:6.1f}s] {name}" + (f" — {note}" if note else ""))

    def summary(self):
        fabric = sum(t["seconds"] for t in self.telemetry if t["category"] == "fabric")
        local = sum(t["seconds"] for t in self.telemetry if t["category"] == "local")
        print(f"\n== plan telemetry: total {fabric + local:.1f}s "
              f"(fabric {fabric:.1f}s, local {local:.1f}s) ==")

    def write_report(self, path: Path):
        """A gym-rep-report skeleton matching 0_admin/references/gym-rep-report-template.md — plan facts
        pre-filled, judgment sections left as TODO for the agent's deviations."""
        now = datetime.now(timezone.utc)
        passed, failed, skipped = self.tally if self.tally else (0, 0, 0)
        total = passed + failed
        status = "passed" if failed == 0 and total > 0 else "failed"
        # A skipped check is not a passed one, and this line is the PROVENANCE the coverage
        # report reads a landing from (`capability_coverage.py` joins a task's declared
        # capabilities to passed gym rep reports). A run that parked its gated legs — an absent
        # delegated credential, `requiresEnv` — still lands as passed, correctly: the parked
        # legs never claimed anything. What must not happen is that the record forgets they
        # did not run, so the count is stated here and the section below asks for the detail.
        parked_note = (f", {skipped} skipped/parked (NOT passed — assertion never made)"
                       if skipped else "")
        parked_todo = ("TODO" if not skipped else
                       f"TODO — and name the {skipped} check(s) this run SKIPPED/PARKED here "
                       "explicitly: which assertion did not run, which credential or "
                       "environment variable would have run it, and what therefore remains a "
                       "hypothesis. A parked check establishes nothing, and this section is "
                       "the only place the record says so in words.")
        ctx_lines = "\n".join(f"  {k}: {json.dumps(v)}" for k, v in self.context.items())
        rows = "\n".join(f"| {t['name']} | {t['category']} | {t['seconds']:.1f}s | {t['note']} |"
                         for t in self.telemetry)
        topic = self.task_dir.parent.name
        path.write_text(f"""---
type: gym-rep-report
task_id: {self.task_id}
task: {self.task_id}
topic: {topic}
date: {now:%Y-%m-%d}
fabric_release: {now:%Y-%m}
status: {status}
validation: "{passed}/{total} checks passed{parked_note}"
workspace_context:
{ctx_lines}
---

# Gym rep report: {self.task_id}

## What it was

TODO — one or two sentences. Task: `1_agent-gym/{topic}/{self.task_id}/task.md`.

## How I did it

Atomic rep — the agent proposed the plan below from `task.md` and its atoms, and
`gym_run.py` executed it deterministically. Plan telemetry:

| Step | Category | Seconds | Note |
|---|---|---|---|
{rows}

### The plan that passed (agent-proposed)

```json
{json.dumps(self.plan, indent=2)}
```

## How it works

TODO — deviations only: what surprised, what an atom did not cover. Delete the section's
TODO and write nothing more if the run matched the atoms exactly.

## Code

TODO — payload snippets that came from outside `code/` and the atoms, if any.

## What broke

TODO — only what actually broke; an atomic rep that ran green leaves one line saying so.

### Confirmed as documented

TODO — atoms/checks this run re-proved.

### Not exercised

{parked_todo}

## Provenance

- Task: `1_agent-gym/{topic}/{self.task_id}/task.md`
- Validation: `python .claude/skills/execute-gym-rep/gym_validate.py {self.task_id}` -> exit 0 on {now:%Y-%m-%d}
- Plan: `python .claude/skills/execute-gym-rep/gym_run.py {self.task_id}`
""", encoding="utf-8")
        print(f"report skeleton -> {path}")


# ── self-test (offline, table-driven) ───────────────────────────────

def _steps(*specs):
    return {"task": "AG-LAK-006", "steps": list(specs)}


_VALIDATE = {"type": "validate"}
_GOOD = _steps(
    {"type": "provision", "script": "provision.py"},
    {"type": "workspace-folder", "name": "Lakehouse"},
    {"type": "lakehouse", "name": "AG_LAK_006_Kilnworks"},
    {"type": "upload-files", "files": [{"local": "source-data/a.csv",
                                        "dest": "Files/raw/a.csv"}]},
    {"type": "notebook", "name": "AG-LAK-006-Kilnworks"},
    {"type": "run-notebook", "runs": 2},
    _VALIDATE,
)

# (name, plan, task_id, expected error substring — None = must be valid)
_CASES = [
    ("valid plan accepted", _GOOD, "AG-LAK-006", None),
    ("task id mismatch caught", _GOOD, "AG-LAK-999", "!="),
    ("empty steps caught", {"task": "AG-LAK-006", "steps": []}, "AG-LAK-006",
     "non-empty list"),
    ("unknown step type caught", _steps({"type": "warehouse"}, _VALIDATE), "AG-LAK-006",
     "unknown type"),
    ("missing required field caught", _steps({"type": "lakehouse"}, _VALIDATE),
     "AG-LAK-006", "missing required field 'name'"),
    ("step check hook runs", _steps(
        {"type": "lakehouse", "name": "X"},
        {"type": "upload-files", "files": [{"local": "a", "dest": "2_raw/a"}]},
        _VALIDATE), "AG-LAK-006", "must start with 'Files/'"),
    ("unmet need caught (upload w/o lakehouse)", _steps(
        {"type": "upload-files", "files": [{"local": "a", "dest": "Files/a"}]},
        _VALIDATE), "AG-LAK-006", "needs a 'lakehouse'"),
    ("need waived by explicit field", _steps(
        {"type": "upload-files", "lakehouse": "Other_LH",
         "files": [{"local": "a", "dest": "Files/a"}]},
        _VALIDATE), "AG-LAK-006", None),
    ("unmet need caught (run w/o notebook)", _steps(
        {"type": "run-notebook"}, _VALIDATE), "AG-LAK-006", "needs a 'notebook'"),
    ("conditional need respected", _steps(
        {"type": "notebook", "name": "N"},
        {"type": "run-notebook", "attachLakehouse": False},
        _VALIDATE), "AG-LAK-006", None),
    ("step check hook: bad runs", _steps(
        {"type": "notebook", "name": "N"},
        {"type": "run-notebook", "runs": 0},
        _VALIDATE), "AG-LAK-006", "positive integer"),
    ("expectStatus: terminal state accepted", _steps(
        {"type": "notebook", "name": "N"},
        {"type": "run-notebook", "attachLakehouse": False, "expectStatus": "Failed"},
        _VALIDATE), "AG-LAK-006", None),
    ("expectStatus: list accepted", _steps(
        {"type": "notebook", "name": "N"},
        {"type": "run-notebook", "attachLakehouse": False,
         "expectStatus": ["Completed", "Deduped"]},
        _VALIDATE), "AG-LAK-006", None),
    ("expectStatus: non-terminal state caught", _steps(
        {"type": "notebook", "name": "N"},
        {"type": "run-notebook", "attachLakehouse": False, "expectStatus": "InProgress"},
        _VALIDATE), "AG-LAK-006", "non-terminal state"),
    ("expectStatus: empty list caught", _steps(
        {"type": "notebook", "name": "N"},
        {"type": "run-notebook", "attachLakehouse": False, "expectStatus": []},
        _VALIDATE), "AG-LAK-006", "non-empty list"),
    ("missing terminal step caught", _steps({"type": "workspace-folder", "name": "X"}),
     "AG-LAK-006", "no terminal step"),
    ("terminal-not-last caught", _steps(_VALIDATE, {"type": "workspace-folder", "name": "X"}),
     "AG-LAK-006", "single final step"),
    # ── the workspace/git family (cicd.py) ──────────────────────────
    ("workspace: missing name caught", _steps({"type": "workspace"}, _VALIDATE),
     "AG-LAK-006", "missing required field 'name'"),
    ("workspace-scoped step needs a workspace step", _steps(
        {"type": "lakehouse", "name": "LH", "workspace": "TEST"},
        _VALIDATE), "AG-LAK-006", "needs a 'workspace'"),
    ("two-environment git-promotion plan accepted", _steps(
        {"type": "provision", "script": "provision.py"},
        {"type": "workspace", "name": "AG_X_DEV", "alias": "DEV"},
        {"type": "workspace", "name": "AG_X_TEST", "alias": "TEST"},
        {"type": "lakehouse", "name": "LH", "workspace": "DEV"},
        {"type": "notebook", "name": "NB", "workspace": "DEV"},
        {"type": "git-bind", "workspace": "DEV", "repo": "r", "branch": "dev",
         "connection": "conn"},
        {"type": "git-commit", "comment": "release", "items": ["NB"]},
        {"type": "git-merge", "repo": "r", "base": "main", "head": "dev"},
        {"type": "git-bind", "workspace": "TEST", "repo": "r", "branch": "main",
         "connection": "conn"},
        {"type": "git-update", "workspace": "TEST", "allowOverrideItems": True},
        {"type": "run-notebook",
         "parameters": {"run_ids": {"value": "$ctx:job_instances", "type": "string"}}},
        _VALIDATE), "AG-LAK-006", None),
    ("git-bind: missing connection caught", _steps(
        {"type": "git-bind", "repo": "r"}, _VALIDATE),
     "AG-LAK-006", "missing required field 'connection'"),
    ("git-bind: bad strategy caught", _steps(
        {"type": "git-bind", "repo": "r", "connection": "c", "strategy": "PreferBoth"},
        _VALIDATE), "AG-LAK-006", "PreferWorkspace or PreferRemote"),
    ("git-commit without a bind caught", _steps(
        {"type": "git-commit", "comment": "x"}, _VALIDATE),
     "AG-LAK-006", "needs a 'git'"),
    ("git-commit: bad items caught", _steps(
        {"type": "git-bind", "repo": "r", "connection": "c"},
        {"type": "git-commit", "comment": "x", "items": []},
        _VALIDATE), "AG-LAK-006", "non-empty list of item display names"),
    ("git-update: bad policy caught", _steps(
        {"type": "git-bind", "repo": "r", "connection": "c"},
        {"type": "git-update", "policy": "PreferBoth"},
        _VALIDATE), "AG-LAK-006", "PreferRemote or PreferWorkspace"),
    ("git-merge: missing base caught", _steps(
        {"type": "git-merge", "repo": "r", "head": "dev"}, _VALIDATE),
     "AG-LAK-006", "missing required field 'base'"),
    # build-publish topology (CICD-C18): sources onto the line, checkout, per-label publish
    ("build-publish plan accepted", _steps(
        {"type": "provision", "script": "provision.py"},
        {"type": "github-repo", "name": "r", "directories": ["press/catalogue"]},
        {"type": "github-put-dir", "repo": "r", "source": "repository",
         "path": "press/catalogue"},
        {"type": "workspace", "name": "AG_X_DESK", "alias": "DESK"},
        {"type": "workspace", "name": "AG_X_PROOF", "alias": "PROOF"},
        {"type": "line-checkout", "repo": "r", "path": "press/catalogue"},
        {"type": "fabric-cicd-publish", "workspace": "PROOF", "environment": "proof-room"},
        {"type": "delivery-record", "repo": "r", "path": "press/rec.json",
         "references": [{"reference": "feed", "rewritten": True}]},
        {"type": "git-bind", "workspace": "DESK", "repo": "r", "connection": "c"},
        _VALIDATE), "AG-LAK-006", None),
    ("github-put-dir: missing source caught", _steps(
        {"type": "github-put-dir", "repo": "r"}, _VALIDATE),
     "AG-LAK-006", "missing required field 'source'"),
    ("publish without a checkout or source caught", _steps(
        {"type": "workspace", "name": "W", "alias": "W"},
        {"type": "fabric-cicd-publish", "workspace": "W", "environment": "e"},
        _VALIDATE), "AG-LAK-006", "needs a 'checkout'"),
    ("publish from an explicit source waives the checkout", _steps(
        {"type": "workspace", "name": "W", "alias": "W"},
        {"type": "fabric-cicd-publish", "workspace": "W", "source": "repository"},
        _VALIDATE), "AG-LAK-006", None),
    ("publish: bad itemTypes caught", _steps(
        {"type": "workspace", "name": "W", "alias": "W"},
        {"type": "line-checkout", "repo": "r"},
        {"type": "fabric-cicd-publish", "workspace": "W", "itemTypes": "Notebook"},
        _VALIDATE), "AG-LAK-006", "itemTypes must be a list"),
    ("delivery-record without a delivery caught", _steps(
        {"type": "delivery-record", "repo": "r", "path": "p.json"}, _VALIDATE),
     "AG-LAK-006", "needs a 'delivery'"),
    ("delivery-record: bad references caught", _steps(
        {"type": "workspace", "name": "W", "alias": "W"},
        {"type": "line-checkout", "repo": "r"},
        {"type": "fabric-cicd-publish", "workspace": "W"},
        {"type": "delivery-record", "repo": "r", "path": "p.json",
         "references": [{"reference": "feed"}]},
        _VALIDATE), "AG-LAK-006", "boolean 'rewritten'"),
    # ── the azure-app family (azure_app.py): write the app, serve it, probe it ──
    ("azure-app plan accepted", _steps(
        {"type": "provision", "script": "provision.py"},
        {"type": "app-write", "dest": "app", "source": "/scratch/app",
         "commands": [["npm", "install"], ["npm", "run", "build"]]},
        {"type": "app-serve", "command": ["node", "server.mjs"],
         "env": {"FENWICK_PORT": "{port}"}, "readyPath": "/dashboard"},
        {"type": "app-probe", "path": "/dashboard",
         "expect": {"status": 200, "bodyContains": ["Fenwick"]}},
        {"type": "app-probe", "mode": "render", "path": "/dashboard",
         "expect": {"selectors": [".chart"], "noConsoleErrors": True}},
        _VALIDATE), "AG-LAK-006", None),
    ("app-write: missing dest caught", _steps(
        {"type": "app-write", "source": "/scratch/app"}, _VALIDATE),
     "AG-LAK-006", "missing required field 'dest'"),
    ("app-write: nothing to write caught", _steps(
        {"type": "app-write", "dest": "app"}, _VALIDATE),
     "AG-LAK-006", "'source' directory or a 'files' list"),
    ("app-write: dest escaping the task folder caught", _steps(
        {"type": "app-write", "dest": "../../etc", "source": "s"}, _VALIDATE),
     "AG-LAK-006", "relative path inside the task folder"),
    ("app-serve: missing command caught", _steps(
        {"type": "app-serve", "port": 8606}, _VALIDATE),
     "AG-LAK-006", "missing required field 'command'"),
    ("app-serve: shell-string command caught", _steps(
        {"type": "app-serve", "command": "node server.mjs"}, _VALIDATE),
     "AG-LAK-006", "argv list"),
    ("app-probe without a serve caught", _steps(
        {"type": "app-probe", "path": "/", "expect": {"status": 200}}, _VALIDATE),
     "AG-LAK-006", "needs a 'app'"),
    ("app-probe: explicit url waives the serve", _steps(
        {"type": "app-probe", "url": "http://127.0.0.1:9/", "expect": {"status": 200}},
        _VALIDATE), "AG-LAK-006", None),
    ("app-probe: empty expect caught", _steps(
        {"type": "app-serve", "command": ["node", "s.mjs"]},
        {"type": "app-probe", "path": "/"}, _VALIDATE),
     "AG-LAK-006", "missing required field 'expect'"),
    ("app-probe: unknown expect key caught", _steps(
        {"type": "app-serve", "command": ["node", "s.mjs"]},
        {"type": "app-probe", "path": "/", "expect": {"bodyContans": ["x"]}}, _VALIDATE),
     "AG-LAK-006", "bodyContans"),
    ("app-probe: render matcher in http mode caught", _steps(
        {"type": "app-serve", "command": ["node", "s.mjs"]},
        {"type": "app-probe", "path": "/", "expect": {"noConsoleErrors": True}}, _VALIDATE),
     "AG-LAK-006", "noConsoleErrors"),
    # ── the openmirror family (openmirror.py): item -> table folder -> files -> replicating ──
    ("openmirror plan accepted", _steps(
        {"type": "provision", "script": "provision.py"},
        {"type": "workspace-folder", "name": "OpenMirror"},
        {"type": "mirrored-database", "name": "AG-OMR-001-Brightwell", "defaultSchema": "dbo"},
        {"type": "landing-zone-table", "table": "store", "keyColumns": ["StoreID"]},
        {"type": "landing-zone-push", "table": "store", "file": "source-data/store.parquet"},
        {"type": "mirror-table-wait", "table": "store"},
        {"type": "validate", "args": ["--mirror-id", "$ctx:mirror_id"]}),
     "AG-LAK-006", None),
    ("openmirror ops plan accepted", _steps(
        {"type": "mirrored-database", "name": "AG-OMR-009-Ledgerline"},
        {"type": "mirror-control", "action": "stop"},
        {"type": "landing-zone-push", "table": "invoice", "schema": "fin",
         "existingTable": True,
         "rows": [{"InvoiceID": "INV-1", "amount": 10.5, "asOf": "2026-08-15"}],
         "columns": [{"name": "InvoiceID", "type": "string"},
                     {"name": "amount", "type": "double"},
                     {"name": "asOf", "type": "date"}]},
        {"type": "mirror-retention", "retentionInDays": 3},
        {"type": "landing-zone-drop", "table": "scratch_ledger", "schema": "fin"},
        {"type": "mirror-control", "action": "start"},
        _VALIDATE), "AG-LAK-006", None),
    ("landing-zone-table without a mirror caught", _steps(
        {"type": "landing-zone-table", "table": "store"}, _VALIDATE),
     "AG-LAK-006", "needs a 'mirror'"),
    ("landing-zone-push without a table folder caught", _steps(
        {"type": "mirrored-database", "name": "M"},
        {"type": "landing-zone-push", "table": "store", "file": "d.parquet"}, _VALIDATE),
     "AG-LAK-006", "needs a 'lz-table'"),
    ("landing-zone-push: two data sources caught", _steps(
        {"type": "mirrored-database", "name": "M"},
        {"type": "landing-zone-table", "table": "store"},
        {"type": "landing-zone-push", "table": "store", "file": "d.parquet", "text": "a,b"},
        _VALIDATE), "AG-LAK-006", "exactly one of file/files/text/rows"),
    ("landing-zone-push: inline rows without a declared schema caught", _steps(
        {"type": "mirrored-database", "name": "M"},
        {"type": "landing-zone-table", "table": "store"},
        {"type": "landing-zone-push", "table": "store", "rows": [{"a": 1}]}, _VALIDATE),
     "AG-LAK-006", "EXPLICIT schema"),
    ("landing-zone-push: __rowMarker__ off the end caught", _steps(
        {"type": "mirrored-database", "name": "M"},
        {"type": "landing-zone-table", "table": "store"},
        {"type": "landing-zone-push", "table": "store",
         "rows": [{"StoreID": "S1", "__rowMarker__": 4}],
         "columns": [{"name": "__rowMarker__", "type": "int32"},
                     {"name": "StoreID", "type": "string"}]}, _VALIDATE),
     "AG-LAK-006", "must be the LAST column"),
    ("mirrored-database: retention out of range caught", _steps(
        {"type": "mirrored-database", "name": "M", "retentionInDays": 90}, _VALIDATE),
     "AG-LAK-006", "integer 1-30"),
    ("mirror-control: unknown action caught", _steps(
        {"type": "mirrored-database", "name": "M"},
        {"type": "mirror-control", "action": "pause"}, _VALIDATE),
     "AG-LAK-006", "'start' or 'stop'"),
    # ── the ontology family (ontology.py): item -> definition -> refresh gate ──
    ("ontology plan accepted", _steps(
        {"type": "workspace-folder", "name": "ONT"},
        {"type": "lakehouse", "name": "FleetLink"},
        {"type": "livy-sql", "statements": ["SELECT 1"]},
        {"type": "ontology", "name": "FleetLink"},
        {"type": "ontology-definition", "config": {
            "entities": [
                {"name": "Driver", "keyProperty": "DriverID", "table": "driver",
                 "properties": [{"name": "DriverID", "valueType": "String"},
                                {"name": "fullName", "valueType": "String"}]},
                {"name": "Vehicle", "keyProperty": "VehicleID", "table": "vehicle",
                 "properties": [{"name": "VehicleID", "valueType": "String"},
                                {"name": "registration", "valueType": "String"}]}],
            "relationships": [{"name": "assignedTo", "source": "Vehicle",
                               "target": "Driver", "contextEntity": "Vehicle",
                               "targetKeyColumn": "assignedDriverID"}]}},
        {"type": "graph-refresh"},
        {"type": "validate", "args": ["--graph-id", "$ctx:graph_id",
                                      "--lakehouse-id", "$ctx:lakehouse_id"]}),
     "AG-LAK-006", None),
    ("ontology-definition without an ontology caught", _steps(
        {"type": "lakehouse", "name": "LH"},
        {"type": "ontology-definition", "source": "design.json"}, _VALIDATE),
     "AG-LAK-006", "needs a 'ontology'"),
    ("ontology-definition without a lakehouse caught", _steps(
        {"type": "ontology", "name": "O"},
        {"type": "ontology-definition", "source": "design.json"}, _VALIDATE),
     "AG-LAK-006", "needs a 'lakehouse'"),
    ("ontology-definition: no design at all caught", _steps(
        {"type": "lakehouse", "name": "LH"}, {"type": "ontology", "name": "O"},
        {"type": "ontology-definition"}, _VALIDATE),
     "AG-LAK-006", "'config' object or a 'source' file"),
    ("ontology-definition: a key that is not a property caught", _steps(
        {"type": "lakehouse", "name": "LH"}, {"type": "ontology", "name": "O"},
        {"type": "ontology-definition", "config": {
            "entities": [{"name": "Driver", "keyProperty": "DriverID",
                          "properties": [{"name": "fullName", "valueType": "String"}]}],
            "relationships": []}}, _VALIDATE),
     "AG-LAK-006", "is not among its properties"),
    ("ontology-definition: a relationship naming an unknown entity caught", _steps(
        {"type": "lakehouse", "name": "LH"}, {"type": "ontology", "name": "O"},
        {"type": "ontology-definition", "config": {
            "entities": [{"name": "Driver", "keyProperty": "DriverID",
                          "properties": [{"name": "DriverID", "valueType": "String"}]}],
            "relationships": [{"name": "drives", "source": "Vehicle", "target": "Driver"}]}},
        _VALIDATE), "AG-LAK-006", "names unknown entity 'Vehicle'"),
    ("graph-refresh without an ontology caught", _steps(
        {"type": "graph-refresh"}, _VALIDATE), "AG-LAK-006", "needs a 'ontology'"),
    ("ontology-definition: source-side FK left to default caught", _steps(
        {"type": "lakehouse", "name": "LH"}, {"type": "ontology", "name": "O"},
        {"type": "ontology-definition", "config": {
            "entities": [{"name": "Driver", "keyProperty": "DriverID",
                          "properties": [{"name": "DriverID", "valueType": "String"}]},
                         {"name": "Vehicle", "keyProperty": "VehicleID",
                          "properties": [{"name": "VehicleID", "valueType": "String"}]}],
            "relationships": [{"name": "assignedTo", "source": "Vehicle",
                               "target": "Driver", "contextEntity": "Vehicle"}]}},
        _VALIDATE), "AG-LAK-006", "zero edges and no error"),
    # ── the data-agent family (data_agent.py): item -> draft config -> publish ──
    ("data-agent plan accepted", _steps(
        {"type": "provision", "script": "provision.py"},
        {"type": "workspace-folder", "name": "DataAgent"},
        {"type": "lakehouse", "name": "AG_DAT_001_Wexford"},
        {"type": "data-agent", "name": "AG-DAT-001-Wexford"},
        {"type": "data-agent-config", "instructions": "Answer in GBP.",
         "sources": [{"lakehouse": "AG_DAT_001_Wexford", "type": "lakehouse_tables",
                      "schemas": [{"name": "sales", "selected": ["book_sale", "title"],
                                   "excluded": ["import_scratch"]}]}]},
        {"type": "data-agent-publish", "description": "Wexford shop-floor sales Q&A"},
        {"type": "data-agent-config", "instructions": "Answer in GBP. Returns net off."},
        _VALIDATE), "AG-LAK-006", None),
    ("data-agent-config without an agent caught", _steps(
        {"type": "data-agent-config", "instructions": "x"}, _VALIDATE),
     "AG-LAK-006", "needs a 'dataagent'"),
    ("data-agent-config: nothing to configure caught", _steps(
        {"type": "data-agent", "name": "A"},
        {"type": "data-agent-config"}, _VALIDATE),
     "AG-LAK-006", "nothing to configure"),
    ("data-agent-config: an immutable-type typo caught", _steps(
        {"type": "data-agent", "name": "A"},
        {"type": "data-agent-config", "sources": [
            {"lakehouse": "LH", "type": "lakehouseTables"}]}, _VALIDATE),
     "AG-LAK-006", "IMMUTABLE once written"),
    ("data-agent-config: a schema selecting nothing caught", _steps(
        {"type": "data-agent", "name": "A"},
        {"type": "data-agent-config", "sources": [
            {"lakehouse": "LH", "schemas": [{"name": "sales"}]}]}, _VALIDATE),
     "AG-LAK-006", "selects no tables"),
    ("data-agent-publish without an agent caught", _steps(
        {"type": "data-agent-publish"}, _VALIDATE),
     "AG-LAK-006", "needs a 'dataagent'"),
    # ── the pipelines family (pipelines.py): notebooks -> definition -> (run) ──
    ("multi-notebook pipeline plan accepted (contextKey)", _steps(
        {"type": "workspace-folder", "name": "Pipelines"},
        {"type": "notebook", "name": "N1", "source": "nb1.py", "contextKey": "nb_a_id"},
        {"type": "notebook", "name": "N2", "source": "nb2.py", "contextKey": "nb_b_id"},
        {"type": "pipeline", "name": "AG_PIP_003_Meridian", "folder": "Pipelines",
         "definition": "pipeline-definition.json"},
        _VALIDATE), "AG-LAK-006", None),
    ("pipeline step without a definition caught", _steps(
        {"type": "pipeline", "name": "P"}, _VALIDATE),
     "AG-LAK-006", "needs 'definition'"),
    ("pipeline step with both definition and properties caught", _steps(
        {"type": "pipeline", "name": "P", "definition": "d.json",
         "properties": {"activities": []}}, _VALIDATE),
     "AG-LAK-006", "not both"),
    ("pipeline-run without a pipeline caught", _steps(
        {"type": "pipeline-run"}, _VALIDATE), "AG-LAK-006", "needs a 'pipeline'"),
]


def self_test() -> int:
    # Offline by contract: nothing here talks to a tenant, but step modules imported lazily
    # below pull in code/ clients whose config requires FABRIC_WORKSPACE_ID at import. Same
    # placeholder convention as code/tests/conftest.py, so the self-test runs on a fresh
    # clone with no .env.
    os.environ.setdefault("FABRIC_WORKSPACE_ID", "00000000-0000-0000-0000-000000000000")
    failures = []

    def check(name, cond, detail=""):
        print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f" — {detail}" if detail and not cond else ""))
        if not cond:
            failures.append(name)

    registry = load_all()
    for spec in registry.values():
        check(f"registry sanity: '{spec.name}'",
              callable(spec.run) and spec.category in ("fabric", "local"),
              f"category={spec.category!r}")

    # A step module may export SELF_TESTS — a list of callables returning [(name, ok)] — for
    # behaviour no plan-shape case can reach. `azure_app.py` uses it to prove its port
    # resolution, readiness wait and teardown by starting a REAL local server: process
    # lifetime is exactly the class of bug a fixture hides, since a mocked child always dies
    # obediently. Same convention as the engine's `test_*` modules, run in the same pass so a
    # family cannot land untested. Fabric-touching steps have nothing to contribute here and
    # export nothing. The snapshot is deliberate: a self-test may import a module lazily.
    for module_name, module in sorted(list(sys.modules.items())):
        if not module_name.startswith("gym_steps.") or not hasattr(module, "SELF_TESTS"):
            continue
        family = module_name.split(".")[-1]
        for fn in module.SELF_TESTS:
            for case_name, ok in fn():
                check(f"{family}: {case_name}", ok)

    # A bundle ships the step modules for the topics it carries, so `registry` is a subset in
    # every bundle below the top tier. A case whose plan names a step type that is not here is
    # asserting on a family that did not ship: it would fail on the absence rather than on the
    # behaviour, and a red self-test out of the box is worse than a smaller one. The one case
    # that must still run is the one whose whole subject IS an unknown type.
    known, skipped = set(registry), []
    for name, plan, task_id, expected in _CASES:
        absent = {s.get("type") for s in plan.get("steps", ())} - known - {None}
        if absent and expected != "unknown type":
            skipped.append(name)
            continue
        errors = check_plan(plan, task_id)
        if expected is None:
            check(name, errors == [], "; ".join(errors))
        else:
            check(name, any(expected in e for e in errors),
                  f"expected {expected!r} in {errors}")
    if skipped:
        print(f"  SKIP  {len(skipped)} plan case(s) — this bundle carries {len(known)} step "
              f"type(s) ({', '.join(sorted(known))}) and these assert on ones it does not")

    # A plan is the agent's proposed output — a task folder holding one is shipping the
    # answer sheet with the exercise.
    strays = sorted(p for pat in ("*/AG-*/plan.json", "*/AG-*/manifest.json")
                    for p in (REPO_ROOT / "1_agent-gym").glob(pat))
    check("no plan file checked into any task folder", strays == [],
          f"found: {[str(s) for s in strays]}")

    # A run whose terminal `validate` never executed must NOT exit 0. `check_plan` enforces
    # the terminal step on the plan FILE; `--skip validate` / `--only <other>` defeat it at
    # run time, and exit 0 is what tracker.md and the circuit both read as "this rep
    # passed". The guard is in main(); this pins that main() still refuses.
    guard = inspect.getsource(main)
    check("a filtered-out acceptance test cannot exit 0",
          "runner.tally is None" in guard and "NO ACCEPTANCE TEST RAN" in guard,
          "main() no longer refuses a run whose validate step was skipped")

    # A record step must guard its required keys on *presence*, not truthiness: [], false and 0
    # are the honest answers a converged re-run and a deliberate keep have to be able to report
    # (AG-CICD-019). `if not record.get(k)` rejects exactly those runs.
    steps_dir = Path(__file__).resolve().parent / "gym_steps"
    truthy = []
    for mod in sorted(steps_dir.glob("*.py")):
        for n, line in enumerate(mod.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"required_?[Kk]eys.*if not record\.get\(", line):
                truthy.append(f"{mod.name}:{n}")
    check("record steps test key presence, not truthiness", truthy == [],
          f"truthiness guard at: {truthy}")

    print(f"== self-test: {'PASS' if not failures else 'FAIL'} "
          f"({len(failures)} failure(s)) ==")
    return 0 if not failures else 1


# ── entry point ─────────────────────────────────────────────────────

def main(argv=None):
    p = argparse.ArgumentParser(description="Deterministic plan engine for agent-gym atomic reps")
    p.add_argument("task", nargs="?", help="task id (AG-LAK-006)")
    p.add_argument("--plan", help="the agent-proposed plan for this task (a scratch "
                                      "file — never checked into the task folder)")
    p.add_argument("--payload", help="the agent-authored artifact, consumed by whichever "
                                     "step declares it (e.g. a notebook-content.py)")
    p.add_argument("--skip", action="append", metavar="TYPE",
                   help="skip steps of this type (repeatable)")
    p.add_argument("--only", action="append", metavar="TYPE",
                   help="run only steps of these types (repeatable), e.g. --only validate")
    p.add_argument("--report", help="write a gym-rep-report skeleton to this path")
    p.add_argument("--self-test", action="store_true", help="offline registry/plan checks")
    args = p.parse_args(argv)

    if args.self_test:
        return self_test()
    if not args.task:
        p.error("a task id is required (or --self-test)")
    if not args.plan:
        p.error("--plan is required: propose the plan from task.md and pass it here")

    task_id = args.task.strip().upper()
    task_dir = resolve_task_dir(task_id)
    plan = load_plan(Path(args.plan), task_id)
    runner = Runner(task_id, task_dir, plan, args)
    runner.execute()
    # `tally` is set by the terminal `validate` step and by nothing else, so its absence
    # means the acceptance test did not run — `--skip validate` / `--only <other>` defeat
    # at run time the invariant `check_plan` enforces on the file. Exit 0 is what the
    # tracker and the circuit both read as "this rep passed" (tracker.md: "a rep counts as
    # passed when its validate.json exits 0"), so a green here on nothing asserted would
    # put a rep report and, downstream, wiki pages behind evidence that was never gathered.
    if runner.tally is None:
        raise SystemExit(
            f"\nNO ACCEPTANCE TEST RAN — {task_id} is NOT green. The terminal 'validate' "
            "step was filtered out by --skip/--only. Re-run without that filter; a rep is "
            "green only when its validate.json has actually asserted against the build.")
    print(f"\nPLAN GREEN — {task_id} validated"
          f" ({runner.tally[0]}/{runner.tally[0] + runner.tally[1]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
