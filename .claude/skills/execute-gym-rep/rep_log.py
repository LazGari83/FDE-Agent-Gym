"""
rep_log.py — the per-rep run ledger. Answers "where did the run time go?" with data.

Bundled with the `execute-gym-rep` skill. Every rep appends timestamped events to an
append-only JSONL ledger (`0_admin/logs/gym-run-log.jsonl`, committed — pipeline state,
not knowledge). Two writers feed it:

  - the AGENT marks phase boundaries as it walks the rep loop (one cheap call each):
        python rep_log.py AG-LAK-006 start
        python rep_log.py AG-LAK-006 phase classify
        python rep_log.py AG-LAK-006 note "waiting on graph refresh"
        python rep_log.py AG-LAK-006 end --status passed   # passed | failed | parked,
                                                          # required — never defaulted
  - gym_validate.py logs every live VALIDATOR run automatically (seconds, exit, tally).

The ENGINE (gym_run.py) does NOT auto-log: `log_engine` (fabric/local split, steps,
tally) exists for manual or future wiring, and no engine event lands in the ledger unless
something calls it — the engine's split lives in its printed telemetry and gym rep reports.

A phase runs from its mark to the next phase mark (or `end`). Canonical phase names —
free-form is accepted, but stick to these so cross-run aggregation means something:
    resolve · preflight · classify · draft · plan · payload · engine · build ·
    triage · debrief · report
(`build` is the hand-orchestrated construction on topics without engine executors —
exactly the segment the executors are meant to collapse.)

Reading it back:
    python rep_log.py AG-LAK-006 report          # latest run for the task, per-phase breakdown
    python rep_log.py --summary [--last N]       # one row per run + per-phase means

Set GYM_RUN_LOG to override the ledger path (tests do). `--self-test` is offline.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
REPO_ROOT = SKILL_DIR.parents[2]

sys.path.insert(0, str(SKILL_DIR.parent / "_shared"))
from kbmd import resolve_log_file  # noqa: E402

DEFAULT_LEDGER = resolve_log_file("gym-run-log.jsonl")

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass


def _ledger_path() -> Path:
    return Path(os.environ.get("GYM_RUN_LOG") or DEFAULT_LEDGER)


def _now_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_events() -> list[dict]:
    path = _ledger_path()
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue                      # a torn line never breaks reporting
    return events


def append_event(task: str, event: str, **fields) -> dict:
    """Append one event. Auto-writers (engine/validator) call this; never raises to them."""
    ts = fields.pop("ts", None) or time.time()
    run = fields.pop("run", None) or open_run(task) or f"{task}@{_now_iso(ts)}"
    obj = {"ts": round(ts, 3), "iso": _now_iso(ts), "task": task, "run": run,
           "event": event, **{k: v for k, v in fields.items() if v not in (None, "")}}
    path = _ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
    return obj


def open_run(task: str) -> str | None:
    """The run id of the task's last `start` that has no `end` yet (else None)."""
    open_id = None
    for e in _read_events():
        if e.get("task") != task:
            continue
        if e["event"] == "start":
            open_id = e["run"]
        elif e["event"] == "end" and e.get("run") == open_id:
            open_id = None
    return open_id


# ── safe hooks for gym_run.py / gym_validate.py ─────────────────────

def log_engine(task: str, telemetry: list[dict], tally, status: str):
    try:
        fabric = round(sum(t["seconds"] for t in telemetry if t["category"] == "fabric"), 1)
        local = round(sum(t["seconds"] for t in telemetry if t["category"] == "local"), 1)
        append_event(task, "engine", status=status, fabric_s=fabric, local_s=local,
                     steps=[{"name": t["name"], "s": round(t["seconds"], 1)} for t in telemetry],
                     tally=list(tally) if tally else None)
    except Exception:                     # telemetry must never fail a run
        pass


def log_validate(task: str, seconds: float, exit_code: int):
    try:
        append_event(task, "validate", seconds=round(seconds, 1), exit=exit_code)
    except Exception:
        pass


# ── reporting ───────────────────────────────────────────────────────

def _runs(events: list[dict]) -> dict[str, list[dict]]:
    runs: dict[str, list[dict]] = {}
    for e in events:
        runs.setdefault(e.get("run", "?"), []).append(e)
    return runs


def _analyse(run_events: list[dict]) -> dict:
    """One run's shape: ordered phases with durations, point events, totals."""
    run_events = sorted(run_events, key=lambda e: e["ts"])
    start = next((e for e in run_events if e["event"] == "start"), None)
    end = next((e for e in run_events if e["event"] == "end"), None)
    t0 = start["ts"] if start else run_events[0]["ts"]
    t_end = end["ts"] if end else run_events[-1]["ts"]

    marks = [e for e in run_events if e["event"] == "phase"]
    phases = []
    if marks and marks[0]["ts"] - t0 > 1:
        phases.append({"phase": "(untracked)", "seconds": marks[0]["ts"] - t0})
    for i, m in enumerate(marks):
        stop = marks[i + 1]["ts"] if i + 1 < len(marks) else t_end
        phases.append({"phase": m.get("phase", "?"), "seconds": max(0.0, stop - m["ts"])})

    return {
        "run": run_events[0].get("run", "?"),
        "task": run_events[0].get("task", "?"),
        "started": _now_iso(t0),
        "status": (end or {}).get("status", "unfinished" if not end else "?"),
        "total_s": max(0.0, t_end - t0),
        "phases": phases,
        "engine": [e for e in run_events if e["event"] == "engine"],
        "validates": [e for e in run_events if e["event"] == "validate"],
        "notes": [e for e in run_events if e["event"] == "note"],
    }


def _fmt_min(seconds: float) -> str:
    return f"{seconds / 60:.1f}m" if seconds >= 90 else f"{seconds:.0f}s"


def cmd_report(task: str) -> int:
    events = [e for e in _read_events() if e.get("task") == task]
    if not events:
        print(f"no runs logged for {task}")
        return 1
    latest_run = max(_runs(events).values(), key=lambda evs: evs[0]["ts"])
    a = _analyse(latest_run)
    print(f"== {a['task']} @ {a['started']} — {a['status']}, total {_fmt_min(a['total_s'])} ==")
    for p in a["phases"]:
        share = (p["seconds"] / a["total_s"] * 100) if a["total_s"] else 0
        print(f"  {p['phase']:<12} {_fmt_min(p['seconds']):>7}  {share:4.0f}%")
    for e in a["engine"]:
        print(f"  engine: {e.get('status')} — fabric {e.get('fabric_s')}s, "
              f"local {e.get('local_s')}s over {len(e.get('steps', []))} step(s)")
    if a["validates"]:
        total_v = sum(v.get("seconds", 0) for v in a["validates"])
        print(f"  validator: {len(a['validates'])} run(s), {_fmt_min(total_v)} total, "
              f"last exit {a['validates'][-1].get('exit')}")
    for n in a["notes"]:
        print(f"  note @ {n['iso']}: {n.get('note', '')}")
    return 0


def cmd_summary(last: int) -> int:
    runs = _runs(_read_events())
    if not runs:
        print("ledger is empty")
        return 1
    analysed = sorted((_analyse(evs) for evs in runs.values()), key=lambda a: a["started"])
    shown = analysed[-last:] if last else analysed
    print(f"== gym run ledger — {len(analysed)} run(s), showing {len(shown)} ==")
    print(f"  {'task':<12} {'started':<21} {'status':<10} {'total':>7}  top phase")
    for a in shown:
        top = max(a["phases"], key=lambda p: p["seconds"], default=None)
        top_txt = f"{top['phase']} ({_fmt_min(top['seconds'])})" if top else "—"
        print(f"  {a['task']:<12} {a['started']:<21} {a['status']:<10} "
              f"{_fmt_min(a['total_s']):>7}  {top_txt}")
    by_phase: dict[str, list[float]] = {}
    for a in shown:
        for p in a["phases"]:
            by_phase.setdefault(p["phase"], []).append(p["seconds"])
    if by_phase:
        print("  -- mean per phase across shown runs --")
        for name, vals in sorted(by_phase.items(), key=lambda kv: -sum(kv[1]) / len(kv[1])):
            print(f"  {name:<12} {_fmt_min(sum(vals) / len(vals)):>7}  (n={len(vals)})")
    return 0


# ── self-test (offline) ─────────────────────────────────────────────

def self_test() -> int:
    import tempfile
    failures = []

    def check(name, cond, detail=""):
        print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f" — {detail}" if detail and not cond else ""))
        if not cond:
            failures.append(name)

    with tempfile.TemporaryDirectory() as td:
        os.environ["GYM_RUN_LOG"] = str(Path(td) / "ledger.jsonl")
        t0 = 1_000_000.0
        append_event("AG-TST-001", "start", ts=t0)
        append_event("AG-TST-001", "phase", phase="classify", ts=t0 + 10)
        append_event("AG-TST-001", "phase", phase="plan", ts=t0 + 70)
        log_engine("AG-TST-001", [{"name": "x", "category": "fabric", "seconds": 30.0},
                                  {"name": "y", "category": "local", "seconds": 2.0}],
                   (5, 0, 0), "passed")
        log_validate("AG-TST-001", 12.3, 0)
        append_event("AG-TST-001", "end", status="passed", ts=t0 + 130)

        events = _read_events()
        check("six events written", len(events) == 6, f"got {len(events)}")
        one_run = {e["run"] for e in events}
        check("all events share one run id", len(one_run) == 1, str(one_run))
        a = _analyse(events)
        check("total is 130s", abs(a["total_s"] - 130) < 0.5, f"{a['total_s']}")
        durations = {p["phase"]: p["seconds"] for p in a["phases"]}
        check("classify ran 60s", abs(durations.get("classify", 0) - 60) < 0.5, str(durations))
        check("plan ran to end (60s)", abs(durations.get("plan", 0) - 60) < 0.5, str(durations))
        check("untracked pre-phase kept", "(untracked)" in durations, str(durations))
        check("engine event carries split",
              a["engine"] and a["engine"][0]["fabric_s"] == 30.0 and a["engine"][0]["local_s"] == 2.0)
        check("validator event carries exit", a["validates"] and a["validates"][0]["exit"] == 0)
        check("run closed", open_run("AG-TST-001") is None)
        append_event("AG-TST-001", "start", ts=t0 + 200)
        check("new start reopens", open_run("AG-TST-001") is not None)
        check("report exits 0", cmd_report("AG-TST-001") == 0)
        check("summary exits 0", cmd_summary(0) == 0)
    os.environ.pop("GYM_RUN_LOG", None)
    print(f"== self-test: {'PASS' if not failures else 'FAIL'} ({len(failures)} failure(s)) ==")
    return 0 if not failures else 1


# ── entry point ─────────────────────────────────────────────────────

def main(argv=None):
    p = argparse.ArgumentParser(description="Append-only run ledger for agent-gym reps")
    p.add_argument("task", nargs="?", help="task id (AG-LAK-006)")
    p.add_argument("cmd", nargs="?",
                   choices=["start", "phase", "note", "end", "report"],
                   help="event to append, or 'report' for the latest run")
    p.add_argument("arg", nargs="?", help="phase name (for 'phase') or note text (for 'note')")
    p.add_argument("--status", choices=["passed", "failed", "parked"],
                   help="REQUIRED for 'end': passed | failed | parked")
    p.add_argument("--note", help="optional annotation on any event")
    p.add_argument("--summary", action="store_true", help="one row per run, all tasks")
    p.add_argument("--last", type=int, default=0, help="with --summary: only the last N runs")
    p.add_argument("--self-test", action="store_true", help="offline ledger checks")
    args = p.parse_args(argv)

    if args.self_test:
        return self_test()
    if args.summary:
        return cmd_summary(args.last)
    if not args.task or not args.cmd:
        p.error("need <task> <start|phase|note|end|report> (or --summary / --self-test)")
    task = args.task.strip().upper()

    if args.cmd == "report":
        return cmd_report(task)
    if args.cmd == "phase" and not args.arg:
        p.error("'phase' needs a name, e.g.: rep_log.py AG-LAK-006 phase classify")
    if args.cmd == "note" and not (args.arg or args.note):
        p.error("'note' needs text")
    if args.cmd == "end" and not args.status:
        # The ledger is append-only, so a status written by default is a status nobody can
        # correct. Defaulting to "passed" meant a forgotten flag recorded a rep as passing —
        # the one value the tracker and the circuit both read as success.
        p.error("'end' needs --status passed|failed|parked — the ledger is append-only and "
                "a default would record an outcome nobody chose")

    fields = {"note": args.note}
    if args.cmd == "phase":
        fields["phase"] = args.arg
    elif args.cmd == "note":
        fields["note"] = args.arg or args.note
    elif args.cmd == "end":
        fields["status"] = args.status
    obj = append_event(task, args.cmd, **fields)
    print(f"logged: {obj['event']}"
          + (f" {obj.get('phase', '')}" if args.cmd == "phase" else "")
          + f" ({obj['run']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
