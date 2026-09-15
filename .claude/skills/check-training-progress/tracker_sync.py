"""
tracker_sync.py — keep 1_agent-gym/tracker.md honest.

Derives the authoritative gym-rep counts from the filesystem — nothing is hand-counted:

  - total tasks per topic  = number of AG-<TOPIC>-<NNN> folders (converted shape) plus any
                             legacy `task-<number>-<slug>` files/folders in 1_agent-gym/<topic>/
  - total tasks overall    = sum of the above across topics
  - completed per topic    = number of PASSED gym rep reports in 2_raw/gym-rep-reports/<topic>/
  - completed overall      = sum of the above

A gym rep report counts as a completion when its frontmatter has `status: passed` and a
`task_id: AG-<TOPIC>-<NNN>` that maps to a real task — BOTH halves are checked, and neither is
inferred from where the file sits: the id's topic segment must be the code of the folder the
report is filed under, and its number must be an actual task folder in `1_agent-gym/<topic>/`.
A report failing either is announced on stderr and not counted, because a completion credited
to the wrong topic (or to no task at all) is a number nothing downstream can un-trust — it also
flips capabilities to `proven` in `capability_coverage.py`, which reads this same function.
The latest passed report per task wins.

Modes:
  python tracker_sync.py            # CHECK: report drift vs the current tracker; exit 1 if stale
  python tracker_sync.py --check    # same as above (explicit)
  python tracker_sync.py --write    # regenerate 1_agent-gym/tracker.md from the filesystem

CHECK compares only the four derived numbers the tracker advertises (overall + per-topic
totals and completions). WRITE regenerates the whole file in canonical form (task rows,
statuses, report links, and the counts). In-progress status is not auto-derivable, so WRITE
emits only passed / not-started — set 🟡 by hand after a regenerate if you want it.
"""
import argparse
import os
import re
import sys
from pathlib import Path

# <skills>/check-training-progress/ -> repo root is three parents up (mirrors execute-gym-rep).
REPO_ROOT = Path(__file__).resolve().parents[3]
AGENT_GYM = REPO_ROOT / "1_agent-gym"
REP_REPORTS = REPO_ROOT / "2_raw" / "gym-rep-reports"
TRACKER = AGENT_GYM / "tracker.md"

# Shared frontmatter parsing lives in <skills>/_shared/kbmd.py. The insert is
# __file__-relative, never cwd-relative: this module is imported by capability_coverage,
# gym_new and gym_validate from THEIR directories, and run from the repo root and from here.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_shared"))
from kbmd import frontmatter_map  # noqa: E402

# Topic -> (task-id code, display name). Unknown topics fall back to first-3-letters / titlecase,
# so a new topic folder is picked up without editing this map.
TOPIC_META = {
    # frontier-decision-engineer
    "ontology": {"code": "ONT", "name": "Ontology"},
    "graph": {"code": "GRA", "name": "Graph"},
    "reports": {"code": "REP", "name": "Reports"},
    "visuals": {"code": "VIS", "name": "Visuals"},
    "direct-lake": {"code": "DL", "name": "Direct Lake"},
    "data-agent": {"code": "DAT", "name": "Data agent"},
    "azure-app": {"code": "APP", "name": "Azure app"},
    # frontier-data-engineer
    "lakehouse": {"code": "LAK", "name": "Lakehouse"},
    "openmirror": {"code": "OMR", "name": "Open Mirroring"},
    "key-vault": {"code": "AKV", "name": "Key Vault"},
    "pipelines": {"code": "PIP", "name": "Pipelines"},
    "notebooks": {"code": "NB", "name": "Notebooks"},
    "warehouse": {"code": "WH", "name": "Warehouse"},
    "connections": {"code": "CON", "name": "Connections"},
    "data-quality": {"code": "DQ", "name": "Data quality"},
    "ingestion": {"code": "ING", "name": "Ingestion"},
    # both
    "semantic-models": {"code": "SEM", "name": "Semantic models"},
    "security": {"code": "SEC", "name": "Security"},
    "performance": {"code": "PERF", "name": "Performance"},
    "cicd": {"code": "CICD", "name": "CI/CD"},
    "admin": {"code": "ADM", "name": "Admin"},
}

# A task is either a converted folder named by its canonical id (AG-ONT-001, optional
# descriptive suffix) or a legacy flat file / old folder (`task-<number>-<slug>[.md]`) not
# yet migrated.
_IDFOLDER_RE = re.compile(r"^AG-[A-Za-z]+-(\d+)(?:-.+)?$")
_TASK_RE = re.compile(r"^task-(\d+)-(.+?)(?:\.md)?$")
_TID_RE = re.compile(r"^AG-([A-Za-z]+)-(\d+)$")


def _skip(report, reason):
    """A rejected rep report, announced. Never silent: a report that does not count is either
    a typo to fix or a file in the wrong folder, and both look identical to a missing rep.
    stderr, so a rejection cannot corrupt stdout that a caller renders verbatim (the
    whiteboard) or parses."""
    try:
        where = report.relative_to(REPO_ROOT).as_posix()
    except ValueError:                                   # a report outside the repo root
        where = report.as_posix()
    print(f"SKIPPED REPORT: {where} — {reason}", file=sys.stderr)


def topic_meta(topic):
    if topic in TOPIC_META:
        return TOPIC_META[topic]
    return {"code": topic[:3].upper(), "name": topic.replace("-", " ").capitalize()}


# ── discovery ─────────────────────────────────────────────────────────────────

def _read_h1_title(path):
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            s = line.strip()
            if s.startswith("# "):
                return re.sub(r"^Task\s+\d+\s*[:\-]\s*", "", s[2:].strip(), flags=re.I)
    except OSError:
        pass
    return None


def _slug_title(slug):
    return slug.replace("-", " ").title()


def discover_tasks(topic_dir):
    """{num: {'slug', 'name'}} for one topic folder. A task is an AG-<TOPIC>-<NNN> folder
    (the converted shape) or a legacy `task-<number>-<slug>` file/folder not yet migrated."""
    tasks = {}
    for entry in sorted(topic_dir.iterdir()):
        if entry.name == "index.md" or entry.name.startswith("."):
            continue
        mid = _IDFOLDER_RE.match(entry.name)
        if mid:
            num, slug = int(mid.group(1)), ""
            name = _read_h1_title(entry / "task.md") or f"Task {num:02d}"
        else:
            m = _TASK_RE.match(entry.name)
            if not m:
                continue
            num, slug = int(m.group(1)), m.group(2)
            src = (entry / "task.md") if entry.is_dir() else entry
            name = _read_h1_title(src) or _slug_title(slug)
        # A converted id-folder wins over a stray same-numbered legacy file left behind.
        if num not in tasks or entry.is_dir():
            tasks[num] = {"slug": slug, "name": name}
    return tasks


def _frontmatter(path):
    return frontmatter_map(path.read_text(encoding="utf-8", errors="replace"))


def discover_completions():
    """{topic: {num: {'date', 'report': Path}}} from PASSED gym rep reports.

    The report's `task_id` is the claim; the filesystem is the check. Both halves of the id
    are verified against the folder the report sits in — the topic code must be that folder's
    code, and the number must be one of that folder's task folders — so a report cannot be
    credited to whichever topic directory it happens to have been filed under. Every rejection
    prints (stderr), because an uncounted report is otherwise indistinguishable from a rep
    nobody ran."""
    out = {}
    if not REP_REPORTS.exists():
        return out
    for topic_dir in sorted(REP_REPORTS.iterdir()):
        if not topic_dir.is_dir():
            continue
        topic = topic_dir.name
        code = topic_meta(topic)["code"].upper()
        gym_dir = AGENT_GYM / topic
        real_tasks = discover_tasks(gym_dir) if gym_dir.is_dir() else {}
        for report in sorted(topic_dir.glob("*.md")):
            fm = _frontmatter(report)
            if fm.get("status", "").lower() != "passed":
                continue
            raw = fm.get("task_id", "").strip().strip("'\"")
            m = _TID_RE.match(raw)
            if not m:
                _skip(report, f"status: passed but task_id {raw!r} is not AG-<TOPIC>-<NNN>")
                continue
            if m.group(1).upper() != code:
                _skip(report, f"task_id {raw} is a {m.group(1).upper()} task but the report is "
                              f"filed under {topic}/ (code {code}) — file it under the topic "
                              f"whose task it is")
                continue
            num, date = int(m.group(2)), fm.get("date", "")
            if num not in real_tasks:
                _skip(report, f"task_id {raw} names no task — 1_agent-gym/{topic}/ has no "
                              f"task {num:03d}")
                continue
            bucket = out.setdefault(topic, {})
            prev = bucket.get(num)
            if prev is None or date >= prev["date"]:
                bucket[num] = {"date": date, "report": report}
    return out


def build_model():
    topics = []
    for topic_dir in sorted(AGENT_GYM.iterdir()):
        if not topic_dir.is_dir() or topic_dir.name.startswith("."):
            continue
        tasks = discover_tasks(topic_dir)
        if tasks:
            topics.append((topic_dir.name, tasks))
    return topics, discover_completions()


class DuplicateTopicCode(RuntimeError):
    """Two topic folders derive the same task-id code — the counts cannot be keyed by code."""


def topic_codes(topics):
    """{topic folder: task-id code}, refusing a collision.

    `topic_meta()` derives a code for an unmapped folder (`topic[:3].upper()`), so two folders
    can land on one code — `lakehouse` and `lakehouse-v2` both give `LAK`. Codes are a DISPLAY
    key (they are what tracker.md's headings advertise); the folder name is the identity, and
    everything derived is keyed by it. A collision is refused rather than silently merged,
    because the merge loses one topic's totals into the other's while the file still renders
    two sections."""
    codes, seen = {}, {}
    for name, _ in topics:
        code = topic_meta(name)["code"]
        if code in seen:
            raise DuplicateTopicCode(
                f"topic folders '{seen[code]}' and '{name}' both derive the task-id code "
                f"'{code}'. Give one of them an explicit, distinct code in TOPIC_META in "
                f".claude/skills/check-training-progress/tracker_sync.py (and the matching "
                f"entry in TOPIC_DIRS in .claude/skills/execute-gym-rep/gym_validate.py) "
                f"before the tracker can be derived.")
        seen[code] = name
        codes[name] = code
    return codes


def authoritative(topics, completions):
    """The four derived numbers, keyed by TOPIC FOLDER NAME (never by code — see topic_codes)."""
    topic_codes(topics)          # refuse a colliding code before any number is derived
    per = {}
    for name, tasks in topics:
        per[name] = {"done": len(completions.get(name, {})), "total": len(tasks)}
    return {
        "topics": per,
        "overall": {
            "done": sum(v["done"] for v in per.values()),
            "total": sum(v["total"] for v in per.values()),
        },
    }


# ── render (WRITE) ─────────────────────────────────────────────────────────────

def _relpath(report):
    return Path(os.path.relpath(report, AGENT_GYM)).as_posix()


def render(topics, completions):
    out = ["# Agent-gym rep tracker", ""]
    out.append(
        "**Your** record of gym reps run to completion, one row per task. It starts at 0 and "
        "fills as you run reps in your own Fabric environment — every row below is a task "
        "waiting for you, not a task someone else finished. A rep counts as **passed** when its "
        "bundled `validate.json` exits 0 against the built artifact and a gym rep report exists "
        "in `2_raw/gym-rep-reports/<topic>/`. Wiki ingest of the findings is a separate, "
        "user-triggered step after review of the report."
    )
    out.append("")
    out.append(
        "**Exit 0 does not mean every check ran.** A check gated on a credential this "
        "repository cannot hold (`requiresEnv`, e.g. a delegated real-user token) is SKIPPED "
        "when the credential is absent — parked, not failed, and never counted as a pass. A "
        "passed row therefore means \"nothing asserted failed\"; which assertions were never "
        "made is recorded in the task's gym rep report, and a parked leg is not evidence."
    )
    out.append("")
    auth = authoritative(topics, completions)
    frags = []
    for name, tasks in topics:
        t = auth["topics"][name]
        frags.append(f"{name} {t['done']}/{t['total']}")
    ov = auth["overall"]
    out.append(
        f"**Overall: {ov['done']} / {ov['total']} tasks passed** ({', '.join(frags)}). Totals come "
        "from the task folders and completions from your `2_raw/gym-rep-reports/` reports, so a "
        "fresh clone reads 0. Refresh with `python "
        ".claude/skills/check-training-progress/tracker_sync.py --write`; never hand-edit the counts."
    )
    out.append("")
    for name, tasks in topics:
        meta = topic_meta(name)
        comp = completions.get(name, {})
        out.append(f"## {meta['name']} (`AG-{meta['code']}`) — {len(comp)} / {len(tasks)}")
        out.append("")
        out.append("| Task | Name | Status | Passed | Report |")
        out.append("|---|---|---|---|---|")
        for num in sorted(tasks):
            tid = f"AG-{meta['code']}-{num:03d}"
            nm = tasks[num]["name"]
            if num in comp:
                status, passed = "✅ passed", (comp[num]["date"] or "—")
                report = f"[report]({_relpath(comp[num]['report'])})"
            else:
                status, passed, report = "⬜ not started", "—", "—"
            out.append(f"| {tid} | {nm} | {status} | {passed} | {report} |")
        out.append("")
    out.append("## Status key")
    out.append("")
    out.append("- ✅ passed — validated (exit 0), gym rep report written (wiki ingest follows human review).")
    out.append("- 🟡 in progress — rep started, not yet validated (set by hand; not auto-derived).")
    out.append("- ⬜ not started.")
    out.append("")
    return "\n".join(out)


# ── parse current + compare (CHECK) ────────────────────────────────────────────

def parse_tracker():
    if not TRACKER.exists():
        return None
    text = TRACKER.read_text(encoding="utf-8", errors="replace")
    parsed = {"overall": None, "topics": {}}
    m = re.search(r"Overall:\s*(\d+)\s*/\s*(\d+)\s*tasks passed", text)
    if m:
        parsed["overall"] = {"done": int(m.group(1)), "total": int(m.group(2))}
    for m in re.finditer(r"^##\s+.+?\(`AG-([A-Za-z]+)`\)\s*[—-]\s*(\d+)\s*/\s*(\d+)", text, re.M):
        parsed["topics"][m.group(1)] = {"done": int(m.group(2)), "total": int(m.group(3))}
    return parsed


def compare(auth, cur, codes):
    """Drift between the derived numbers (keyed by topic folder) and what tracker.md says
    (keyed by the `AG-<CODE>` in its headings). `codes` maps one to the other."""
    diffs = []
    if cur is None:
        return ["tracker.md does not exist — run with --write to create it."]
    a_ov, c_ov = auth["overall"], cur.get("overall")
    if c_ov != a_ov:
        diffs.append(f"overall: tracker says {_fmt(c_ov)}, filesystem says {_fmt(a_ov)}")
    for name, a in auth["topics"].items():
        code = codes[name]
        c = cur["topics"].get(code)
        if c != a:
            diffs.append(f"{code} ({name}): tracker says {_fmt(c)}, filesystem says {_fmt(a)}")
    for code in cur["topics"]:
        if code not in set(codes.values()):
            diffs.append(f"{code}: present in tracker but no matching agent-gym topic folder")
    return diffs


def _fmt(v):
    return "missing" if not v else f"{v['done']}/{v['total']} passed"


def _summary(auth, codes):
    lines = [f"  overall: {_fmt(auth['overall'])}"]
    for name, v in sorted(auth["topics"].items(), key=lambda kv: codes[kv[0]]):
        lines.append(f"  {codes[name]}: {_fmt(v)}")
    return "\n".join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(description="Check or refresh 1_agent-gym/tracker.md counts.")
    p.add_argument("--check", action="store_true", help="report drift only (default); exit 1 if stale")
    p.add_argument("--write", action="store_true", help="regenerate tracker.md from the filesystem")
    args = p.parse_args(argv)

    topics, completions = build_model()
    try:
        codes = topic_codes(topics)
        auth = authoritative(topics, completions)
    except DuplicateTopicCode as exc:
        print(f"TRACKER CANNOT BE DERIVED: {exc}", file=sys.stderr)
        return 2

    if args.write:
        TRACKER.write_text(render(topics, completions), encoding="utf-8")
        print("tracker.md regenerated. Authoritative counts:")
        print(_summary(auth, codes))
        return 0

    diffs = compare(auth, parse_tracker(), codes)
    if diffs:
        print("Tracker OUT OF DATE:")
        for d in diffs:
            print(f"  - {d}")
        print("Run: python .claude/skills/check-training-progress/tracker_sync.py --write")
        return 1
    print("Tracker is up to date. Authoritative counts:")
    print(_summary(auth, codes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
