"""
capability_coverage.py — derive capability coverage across the gym and the wiki.

Bundled with the `check-training-progress` skill. The capability specs in `0_admin/capabilities/<topic>.md`
are hand-written; EVERY state below is derived, so nothing has to be reconciled by hand.

Three inputs are joined on the capability id:

  spec    0_admin/capabilities/<topic>.md         the table of ids (hand-written — the only hand-written part)
  gym     1_agent-gym/<topic>/AG-*/validate.json   "capabilities": [...] declared by each task
          2_raw/gym-rep-reports/<topic>/*.md         which of those tasks have actually landed (status: passed)
  wiki    3_wiki/**/*.md                    capabilities: [...] in page frontmatter

Two axes, because a capability is not finished until both are satisfied:

  gym   declared -> covered (>=1 task) -> proven (>=1 landed) -> hardened (>= its `angles` landed)
  wiki  undocumented -> documented (>=1 page declares it)

`hardened` counts landed tasks as a proxy for distinct angles; that the angles genuinely differ
is `create-gym-task`'s job to enforce when authoring, not something a script can judge.

ATTRIBUTION IS PER TASK, NOT PER CHECK — the one thing to know before trusting `proven`. A
landed task marks EVERY id on its `capabilities` list proven; nothing here reads which check
asserted what, and a validate.json cannot say. Two consequences the gym enforces upstream so
this report stays honest: a task must not declare a capability its checks do not exercise, and
a check that PARKS when a credential is absent (the engine's `requiresEnv` gate — a SKIP, never
a pass) must never be a declared capability's only evidence. `gym_validate.py --dry-run` refuses
a spec whose every check is gated while it declares capabilities, because a rep with no
credential would park them all, exit 0, land a passed report, and be read here as proof of a run
that asserted nothing. Which assertions a landed run skipped is recorded in its gym rep report.

Usage (from the repo root):
    python .claude/skills/check-training-progress/capability_coverage.py                # report every topic
    python .claude/skills/check-training-progress/capability_coverage.py --topic openmirror
    python .claude/skills/check-training-progress/capability_coverage.py --next [N]     # the ranked gap queue
    python .claude/skills/check-training-progress/capability_coverage.py --write        # refresh 0_admin/capabilities/coverage.md
    python .claude/skills/check-training-progress/capability_coverage.py --check        # exit 1 on a broken join
    python .claude/skills/check-training-progress/capability_coverage.py --metrics      # wiki weight per capability
    python .claude/skills/check-training-progress/capability_coverage.py --whiteboard   # the training countdown, by community

`--check` fails on any of the three ways the join can be broken, each of which used to pass
silently: a declared id NO spec carries (wiki or task, checked against the union of every
spec's ids — `cicd.md` alone carries three prefixes), a capability-spec row this parser cannot
read (which drops it from the denominator and reads as higher coverage), and a `validate.json`
it cannot parse (which downgrades every capability that task declares back to `declared`).
Those diagnostics go to STDERR so they cannot corrupt stdout a caller renders verbatim.
"""
import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CAPABILITIES = REPO_ROOT / "0_admin" / "capabilities"
AGENT_GYM = REPO_ROOT / "1_agent-gym"
WIKI = REPO_ROOT / "3_wiki"
COVERAGE_MD = CAPABILITIES / "coverage.md"

# Family segment: `C` = generic failure-mode row, `M` = method row, and a
# source code (`YTR`, `SKL`, ...) = source-specific row. All three share the
# topic prefix and are counted together; the `family` column carries the axis.
_CAP_ID = re.compile(r"^([A-Z]{2,5})-([A-Z]{1,4})(\d+)$")
_TASK_ID = re.compile(r"^AG-([A-Za-z]+)-(\d+)")

# Shared helpers (frontmatter parsing, console setup, completion discovery) live in
# .claude/skills/_shared/kbmd.py. The insert is __file__-relative, never cwd-relative —
# this script runs both from the repo root and from its own folder.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_shared"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from kbmd import (frontmatter_list as _frontmatter_list,  # noqa: E402
                  load_completions as _load_completions, utf8_stdout)
# The topic->code map has one definition, in the sibling script that owns the tracker.
from tracker_sync import topic_meta  # noqa: E402

utf8_stdout()


# ── inputs ────────────────────────────────────────────────────────────────────

def parse_scope(text):
    """The topic-level scope marker -> reason string, or None when the topic is in scope.

    A spec header carrying `**Scope:** out-of-scope — <reason>` declares a whole Fabric area
    the KB has decided not to pursue. The rows stay (they are real capabilities, and their ids
    stay valid references) but nothing chases them: they are excluded from the gap counts and
    from the `--next` queue that ranks what to build next."""
    m = re.search(r"^\*\*Scope:\*\*\s*out-of-scope\s*[—-]?\s*(.*)$", text, re.M | re.I)
    return (m.group(1).strip() or "no reason recorded") if m else None


def parse_community(text):
    """The topic-level `**Community:**` marker -> frontier-data-engineer |
    frontier-decision-engineer | both, or None.

    Every wiki page carries a community; the spec header carries the topic's. It is what
    lets the whiteboard view group by community track rather than by topic alphabetics."""
    m = re.search(r"^\*\*Community:\*\*\s*([\w-]+)", text, re.M)
    return m.group(1).lower() if m else None


def parse_angles(raw):
    """(angles, problem or None) for one `angles` cell.

    `angles` is the DEPTH denominator — `hardened` means this many landed reps — so every way
    of not reading a plain integer inflates the report. Scrubbing every non-digit (the old
    behaviour) turns `2-3` into **23**, and an absent cell into a silent **1**, which marks a
    capability finished after a single rep. Both now come back as a stated problem."""
    text = (raw or "").strip()
    if re.fullmatch(r"\d+", text) and int(text) > 0:
        return int(text), None
    m = re.search(r"\d+", text)
    if m and int(m.group(0)) > 0:
        scrubbed = re.sub(r"\D", "", text)
        return int(m.group(0)), (f"is not a plain integer (a digit scrub reads it as "
                                 f"{scrubbed}) — counting it as {m.group(0)}")
    return 1, ("holds no usable number — counted as 1, which makes a single landed rep read "
               "as `hardened`")


def parse_spec(path):
    """Parse a 0_admin/capabilities/<topic>.md table -> (code, [{id, num, capability, failure,
    angles, evidence}], scope, community, warnings).

    Rows below a '## Retired' heading are parsed but flagged retired: their ids stay valid
    references (landed reps and wiki pages still cite them) but they are not chased.

    Every way this parser can fail to read a LIVE row now comes back as a warning rather than
    a quiet omission, because each of them moves the numbers the same direction — up. A row it
    cannot map to columns is dropped, which shrinks the denominator and RAISES the coverage
    percentage; an unreadable `angles` cell shortens the distance to `hardened`. Retired rows
    warn about neither: nothing counts them, and their tables legitimately carry fewer columns."""
    rows, code, retired, header = [], None, False, None
    warnings, header_line, header_has_angles, warned_no_angles = [], 0, True, False
    text = path.read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), 1):
        if line.startswith("## "):
            retired = line[3:].strip().lower().startswith("retired")
            header = None          # each section carries its own header row
            warned_no_angles = False
            continue
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        m = _CAP_ID.match(cells[0].strip("` "))
        if not m:
            # Header row -> remember the column order, so the table can gain columns
            # without this parser needing to know their positions.
            if cells and cells[0].lower() == "id":
                header = [c.lower() for c in cells]
                header_line, header_has_angles = lineno, "angles" in header
            continue
        if not header:
            if not retired:
                warnings.append(
                    f"{path.name}:{lineno}: capability row {cells[0].strip('` ')} sits in a "
                    f"table with no `| id | … |` header row, so it is DISCARDED — it is "
                    f"missing from the denominator and every count above it reads high. "
                    f"Repeat the header row at the top of this section's table.")
            continue
        if not retired and not header_has_angles and not warned_no_angles:
            warnings.append(
                f"{path.name}:{header_line}: this section's header row has no `angles` "
                f"column, so every row under it counts as 1 angle — one landed rep reads as "
                f"`hardened`. Add the column, or the DEPTH line is fiction.")
            warned_no_angles = True
        col = dict(zip(header, cells))
        code = code or m.group(1)
        angles, problem = parse_angles(col.get("angles", ""))
        # Only per-cell problems here: a whole section missing the column has warned once
        # already, and repeating it per row would bury the rest.
        if problem and not retired and header_has_angles:
            warnings.append(f"{path.name}:{lineno}: {cells[0].strip('` ')} angles cell "
                            f"{col.get('angles', '')!r} {problem}.")
        rows.append({
            "id": cells[0].strip("` "), "num": int(m.group(3)),
            "family": m.group(2),
            "capability": col.get("capability", ""),
            "phase": (col.get("phase") or "unphased").lower(),
            "failure": col.get("distinct failure mode", ""),
            "angles": angles, "evidence": col.get("evidence", ""),
            "retired": retired,
        })
    return code, rows, parse_scope(text), parse_community(text), warnings


def load_specs():
    """{topic: {'code', 'caps': [...], 'path', 'out_of_scope', 'community', 'warnings'}} for
    every 0_admin/capabilities/<topic>.md.

    Always ALL of them, never one: capability ids are a single global namespace (`cicd.md`
    alone carries three prefixes), so the set of valid ids can only be built from every spec.
    `--topic` filters what is REPORTED, downstream of this."""
    out = {}
    if not CAPABILITIES.is_dir():
        return out
    for path in sorted(CAPABILITIES.glob("*.md")):
        topic = path.stem
        if topic == "coverage":
            continue
        code, caps, scope, community, warnings = parse_spec(path)
        if caps:
            out[topic] = {"code": code, "caps": caps, "path": path,
                          "out_of_scope": scope, "community": community,
                          "warnings": warnings}
    return out


def load_task_declarations():
    """({topic: {task_id: [capability ids]}}, [unreadable spec messages]) across EVERY gym
    topic folder — including one no capability spec covers, whose declared ids would otherwise
    never be checked against anything.

    A `validate.json` that will not parse is returned as a failure rather than shrugged off:
    ignoring it silently downgrades every capability that task declares back to `declared`, so
    the report starts recommending "author-task" for a task that is sitting right there."""
    decls, broken = {}, []
    if not AGENT_GYM.is_dir():
        return decls, broken
    for topic_dir in sorted(AGENT_GYM.iterdir()):
        if not topic_dir.is_dir() or topic_dir.name.startswith("."):
            continue
        out = {}
        for spec in sorted(topic_dir.glob("AG-*/validate.json")):
            try:
                data = json.loads(spec.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                broken.append(f"{spec.relative_to(REPO_ROOT).as_posix()} is not valid JSON "
                              f"({exc}) — every capability it declares reads as unexercised")
                continue
            task_id = data.get("task") or spec.parent.name
            out[task_id] = list(data.get("capabilities", []))
        decls[topic_dir.name] = out
    return decls, broken


def load_wiki_declarations():
    """{capability id: [page paths]} across the whole wiki (joined by id, not by folder,
    so a decisions/ page or a cross-topic pattern page counts wherever it lives)."""
    out = {}
    for page in sorted(WIKI.rglob("*.md")):
        if "code" in page.relative_to(WIKI).parts:
            continue
        for cap in _frontmatter_list(page.read_text(encoding="utf-8", errors="replace"), "capabilities"):
            out.setdefault(cap, []).append(page.relative_to(REPO_ROOT).as_posix())
    return out


# ── derivation ────────────────────────────────────────────────────────────────

def gym_state(cap, tasks, landed):
    if not tasks:
        return "declared"
    if not landed:
        return "covered"
    return "hardened" if len(landed) >= cap["angles"] else "proven"


def build(topic, spec, completions, wiki_decl, declarations, known_ids):
    """Join the three inputs for one topic -> (rows, orphan ids).

    `known_ids` is the union across EVERY spec, not this topic's own: a task or page declaring
    a valid id that belongs to another spec is a cross-reference, not a broken one, and the
    only thing worth failing on is an id **no spec anywhere carries**."""
    passed_nums = set(completions.get(topic, {}))
    code = topic_meta(topic)["code"].upper()

    by_cap = {}
    for task_id, caps in declarations.items():
        # BOTH halves of the id, never the number alone: `AG-OMR-003` sitting in the lakehouse
        # gym would otherwise land on lakehouse's passed task 003 and mark its capabilities
        # proven on another topic's rep. (tracker_sync applies the same rule to the reports.)
        m = _TASK_ID.match(task_id)
        has_landed = (bool(m) and m.group(1).upper() == code
                      and int(m.group(2)) in passed_nums)
        for cap_id in caps:
            entry = by_cap.setdefault(cap_id, {"tasks": [], "landed": []})
            entry["tasks"].append(task_id)
            if has_landed:
                entry["landed"].append(task_id)

    rows = []
    for cap in spec["caps"]:
        hit = by_cap.get(cap["id"], {"tasks": [], "landed": []})
        pages = wiki_decl.get(cap["id"], [])
        rows.append({
            **cap,
            "tasks": sorted(hit["tasks"]), "landed": sorted(hit["landed"]), "pages": pages,
            "gym": gym_state(cap, hit["tasks"], hit["landed"]),
            "wiki": "documented" if pages else "undocumented",
        })

    orphans = [f"{cap_id} declared by {', '.join(sorted(hit['tasks']))} — no capability spec "
               f"carries it"
               for cap_id, hit in sorted(by_cap.items()) if cap_id not in known_ids]
    return rows, orphans


# ── output ────────────────────────────────────────────────────────────────────

_GYM_ORDER = ["declared", "covered", "proven", "hardened"]


def summarise(rows):
    live = [r for r in rows if not r["retired"]]
    counts = {s: sum(1 for r in live if r["gym"] == s) for s in _GYM_ORDER}
    documented = sum(1 for r in live if r["wiki"] == "documented")
    complete = sum(1 for r in live if r["gym"] == "hardened" and r["wiki"] == "documented")
    return len(live), counts, documented, complete


_PHASES = ["design", "build", "operate"]


def phase_balance(rows):
    """Per-phase coverage. A topic strong in one phase and hollow in another has a blind
    spot no overall percentage will show — and it is nearly always `operate`, because
    tutorials and docs teach building, not running."""
    live = [r for r in rows if not r["retired"]]
    out = {}
    for phase in _PHASES + (["unphased"] if any(r["phase"] == "unphased" for r in live) else []):
        caps = [r for r in live if r["phase"] == phase]
        if not caps:
            continue
        out[phase] = {
            "total": len(caps),
            "hardened": sum(1 for r in caps if r["gym"] == "hardened"),
            "landed": sum(len(r["landed"]) for r in caps),
            "documented": sum(1 for r in caps if r["wiki"] == "documented"),
        }
    return out


def print_report(topic, rows, orphans):
    total, counts, documented, complete = summarise(rows)
    print(f"== {topic} — {total} capabilities ==")
    print(f"  gym  : {counts['hardened']} hardened, {counts['proven']} proven, "
          f"{counts['covered']} covered, {counts['declared']} declared (nothing built)")
    print(f"  wiki : {documented} documented, {total - documented} undocumented")
    print(f"  done : {complete}/{total} (hardened AND documented)")
    balance = phase_balance(rows)
    for phase, b in balance.items():
        print(f"  {phase:<9}: {b['total']:>2} capabilities, {b['hardened']} hardened, "
              f"{b['landed']} landed reps, {b['documented']} documented")
    weakest = min(balance.items(), key=lambda kv: kv[1]["landed"] / max(kv[1]["total"], 1)) if balance else None
    if weakest and len(balance) > 1:
        print(f"  THINNEST PHASE: {weakest[0]} "
              f"({weakest[1]['landed']} landed reps across {weakest[1]['total']} capabilities)")
    if "unphased" in balance:
        print(f"  {balance['unphased']['total']} capabilities have no phase — add the column")
    for r in rows:
        if r["retired"]:
            continue
        flag = " " if (r["gym"] == "hardened" and r["wiki"] == "documented") else "!"
        print(f"  {flag} {r['id']}  {r['gym']:<9} {r['wiki']:<12} "
              f"reps {len(r['landed'])}/{r['angles']}  {r['capability'][:58]}")
        if r["gym"] == "declared":
            print("        GAP: no task exercises this")
        elif r["landed"] and r["wiki"] == "undocumented":
            print(f"        GAP: proven by {', '.join(r['landed'])} but no wiki page documents it")
    for o in orphans:
        print(f"  ORPHAN {o}")
    print()


def print_out_of_scope(out_of_scope):
    """Out-of-scope topics, listed but not counted. They are shown rather than hidden so the
    exclusion stays a visible, reversible decision instead of a silent hole in the spec."""
    print("== out of scope (declared, not chased) ==")
    for topic, rows, reason in out_of_scope:
        live = [r for r in rows if not r["retired"]]
        ids = ", ".join(r["id"] for r in live)
        first = reason.split(". ")[0].rstrip(".")
        print(f"  {topic} — {len(live)} capabilities excluded ({ids})")
        print(f"      {first[:120]}")
    print("  Re-include with --include-out-of-scope, or drop the '**Scope:**' line from the spec.")
    print()


def next_actions(rows, topic):
    """Rank what to do next for one topic — the priority function.

    Each capability yields at most one action, typed so a caller (or an unattended loop)
    can dispatch it without re-deriving anything. Ordered worst-coverage-first:

      author-task  nothing exercises it at all — the deepest hole
      ingest       reps have landed but no wiki page carries it (evidence earned, not banked)
      run-rep      a task exists and has never been run
      add-angle    proven, but from fewer angles than it needs to count as experience
    """
    actions = []
    for r in rows:
        if r["retired"]:
            continue
        if r["gym"] == "declared":
            actions.append((0, "author-task", r, "no task exercises this capability"))
        elif r["landed"] and r["wiki"] == "undocumented":
            actions.append((1, "ingest", r, f"proven by {', '.join(r['landed'])}, no wiki page carries it"))
        elif r["gym"] == "covered":
            pending = [t for t in r["tasks"] if t not in r["landed"]]
            actions.append((2, "run-rep", r, f"task(s) {', '.join(pending)} authored but never landed"))
        elif r["gym"] == "proven":
            actions.append((3, "add-angle", r,
                            f"{len(r['landed'])}/{r['angles']} angles — needs a materially different task"))
    # worst coverage first; within a tier, the capability with fewest landed reps
    actions.sort(key=lambda a: (a[0], len(a[2]["landed"]), a[2]["num"]))
    return [(kind, r, why) for _, kind, r, why in actions]


def print_next(all_rows, limit):
    ranked = []
    for topic, rows, _ in all_rows:
        ranked += [(topic, kind, r, why) for kind, r, why in next_actions(rows, topic)]
    if not ranked:
        print("Nothing to do — every capability is hardened and documented.")
        return
    print(f"== next {min(limit, len(ranked))} actions (worst coverage first, {len(ranked)} outstanding) ==")
    for i, (topic, kind, r, why) in enumerate(ranked[:limit], 1):
        print(f"  {i:>2}. [{kind:<11}] {r['id']}  ({topic})  {r['capability']}")
        print(f"      why: {why}")
        if kind in ("author-task", "add-angle"):
            print(f"      failure mode to build around: {r['failure']}")
    print()


_COMMUNITY_ORDER = ["frontier-data-engineer", "frontier-decision-engineer", "both"]


def _bar(done, total, width=20):
    if total <= 0:
        return "░" * width
    filled = int(round(done / total * width))
    filled = max(filled, 1) if done else filled          # real progress never renders as nothing
    filled = min(filled, width - 1) if done < total else width   # nor a gap as finished
    return "█" * filled + "░" * (width - filled)


def training_counts(rows):
    """(total, exposed, learned, mastered) for one topic's rows.

    `learned` is the whiteboard's headline: the agent met the capability in a gym task that
    **passed**, and that lesson reached the wiki. Deliberately laxer than `done` everywhere
    else here — `done` also demands every angle (`hardened`), which measures depth, not
    exposure, and so sits flat for weeks while real work lands.

    `mastered` is the same capability with every angle landed. The two are reported as
    separate rounds rather than one blended score: breadth is the current target (100% of
    every topic), depth is the round that follows it."""
    live = [r for r in rows if not r["retired"]]
    seen = [r for r in live if r["gym"] in ("proven", "hardened")]
    return (len(live), len(seen),
            sum(1 for r in seen if r["wiki"] == "documented"),
            sum(1 for r in live if r["gym"] == "hardened" and r["wiki"] == "documented"))


def _pct(done, total):
    return f"{round(done / total * 100)}%" if total else "—"


def _community_bars(comms, pick):
    """The community → topic bars for one metric. `pick(rows) -> (done, total)`.

    Both rounds render through this, so the two blocks stay visually comparable and a
    topic sits in the same place on each."""
    def _ratio(rows):
        done, total = pick(rows)
        return done / total if total else 0

    width = max([len(c) for c in comms] + [9]) + 2
    lines = []
    for community in [c for c in _COMMUNITY_ORDER if c in comms] + sorted(set(comms) - set(_COMMUNITY_ORDER)):
        entries = comms[community]
        done, total = pick([r for _, rs in entries for r in rs])
        lines.append(f"  {community.upper():<{width}}{_bar(done, total)}  {done:>3}/{total:<4}{_pct(done, total):>5}")
        for topic, rows in sorted(entries, key=lambda e: -_ratio(e[1])):
            done, total = pick(rows)
            lines.append(f"    {topic:<13}{_bar(done, total)}  {done:>3}/{total:<4}{_pct(done, total):>5}"
                         + ("  ✔" if total and done == total else ""))
        lines.append("")
    return lines


def print_whiteboard(all_rows, specs, completions):
    """The countdown view — how much of the spec the agent has actually been trained on.

    Two rounds, reported separately and never blended. Round 1 is breadth: every capability
    proven by a passed rep and ingested, the target being 100% of every topic. Round 2 is
    depth: the same capabilities with every angle landed, which only becomes the goal once
    round 1 is closed. Grouped by community because the two tracks train independently —
    a finished frontier-data-engineer track and an untouched frontier-decision-engineer
    track should read that way."""
    comms = {}
    for topic, rows, _ in all_rows:
        comms.setdefault(specs.get(topic, {}).get("community") or "unassigned", []).append((topic, rows))

    every = [r for _, rows, _ in all_rows for r in rows]
    total, exposed, learned, mastered = training_counts(every)
    reps = sum(len(completions.get(topic, {})) for topic, _, _ in all_rows)
    rate = learned / reps if reps else 0
    eta = (f"   {total - learned} to go · ~{max(1, round((total - learned) / rate))} reps"
           if rate and total > learned else "")

    print()
    print("  ROUND 1 · BREADTH — proven once and ingested          [ TARGET: 100% ]")
    print(f"  {_bar(learned, total, 24)}  {learned}/{total}  {_pct(learned, total)}{eta}")
    print()
    print("\n".join(_community_bars(comms, lambda rows: (training_counts(rows)[2], training_counts(rows)[0]))))
    if exposed - learned:
        print(f"  awaiting ingest: {exposed - learned} proven by a passed rep, not yet written up")
        print()

    print("  ROUND 2 · DEPTH — every angle proven                  [ secondary ]")
    print(f"  {_bar(mastered, total, 24)}  {mastered}/{total}  {_pct(mastered, total)}")
    print()
    print("\n".join(_community_bars(comms, lambda rows: (training_counts(rows)[3], training_counts(rows)[0]))))


def print_metrics(topic, rows):
    """Wiki weight per capability — the consolidation trigger. A curated brain is meant to
    stay small; if pages-per-capability keeps climbing, ingest is appending where it should
    be rewriting.

    Pages are compared as full repo-relative paths. Matching on the BASENAME (the old
    behaviour) crossed topics wholesale — every cluster uses the same six filenames, so
    `3_wiki/graph/gotchas.md` satisfied ontology's `gotchas.md` and the topic reported more
    pages than its folder holds."""
    live = [r for r in rows if not r["retired"]]
    documented = [r for r in live if r["pages"]]
    pages = {p for r in live for p in r["pages"]}      # repo-relative, from load_wiki_declarations
    topic_pages = (sorted(p.relative_to(REPO_ROOT).as_posix() for p in (WIKI / topic).glob("*.md"))
                   if (WIKI / topic).is_dir() else [])
    undeclared = [p for p in topic_pages if p not in pages]
    per_cap = (sum(len(r["pages"]) for r in documented) / len(documented)) if documented else 0
    print(f"== {topic} wiki weight ==")
    print(f"  pages carrying capabilities : {len(pages)}  (wherever they live — joined by id, "
          f"not by folder)")
    print(f"  pages per documented capability: {per_cap:.2f}  (climbing = ingest is appending, not rewriting)")
    if undeclared:
        print(f"  pages declaring nothing     : {len(undeclared)}")
        for p in undeclared:
            print(f"      {Path(p).name} — declare its capabilities, or it is a consolidation candidate")
    print()


def render_markdown(all_rows, out_of_scope=()):
    lines = [
        "# Capability coverage",
        "",
        "**Generated — do not edit.** `python .claude/skills/check-training-progress/capability_coverage.py --write`",
        "",
        "Derived by joining the hand-written specs in `0_admin/capabilities/<topic>.md` with each task's "
        "`validate.json` declarations, the passed gym rep reports in `2_raw/gym-rep-reports/`, and the "
        "`capabilities:` frontmatter on wiki pages. A capability is **done** when it is *hardened* "
        "(landed in as many tasks as it has angles) **and** *documented* (a wiki page carries it).",
        "",
    ]
    for topic, rows, _ in all_rows:
        total, counts, documented, complete = summarise(rows)
        lines += [
            f"## {topic} — {complete}/{total} done",
            "",
            f"Gym: **{counts['hardened']}** hardened · {counts['proven']} proven · "
            f"{counts['covered']} covered · {counts['declared']} declared. "
            f"Wiki: **{documented}** documented · {total - documented} undocumented.",
            "",
            " · ".join(f"**{p}** {b['total']} caps, {b['hardened']} hardened, {b['landed']} landed reps"
                       for p, b in phase_balance(rows).items()),
            "",
            "| id | capability | phase | gym | reps | wiki | tasks |",
            "|---|---|---|---|---|---|---|",
        ]
        for r in rows:
            if r["retired"]:
                continue
            pending = [t for t in r["tasks"] if t not in r["landed"]]
            tasks = ", ".join(r["landed"] + [f"_{t}_" for t in pending]) or "—"
            lines.append(f"| {r['id']} | {r['capability']} | {r['phase']} | {r['gym']} | "
                         f"{len(r['landed'])}/{r['angles']} | {r['wiki']} | {tasks} |")
        if any(r["tasks"] for r in rows if not r["retired"]):
            lines += ["", "Italicised tasks exist but have not landed a passed gym rep report.", ""]
        else:
            lines.append("")
    if out_of_scope:
        lines += [
            "## Out of scope",
            "",
            "Topics whose spec carries a `**Scope:** out-of-scope` marker. Their capabilities are "
            "real and their ids stay valid references, but they are excluded from every count "
            "above and from the `--next` queue, so no unattended run proposes work against them.",
            "",
            "| topic | capabilities | reason |",
            "|---|---|---|",
        ]
        for topic, rows, reason in out_of_scope:
            live = [r for r in rows if not r["retired"]]
            ids = ", ".join(r["id"] for r in live)
            lines.append(f"| {topic} | {len(live)} ({ids}) | {reason} |")
        lines.append("")
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Derive capability coverage across the gym and the wiki.")
    ap.add_argument("--topic", help="limit to one topic")
    ap.add_argument("--write", action="store_true", help="refresh 0_admin/capabilities/coverage.md")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 on a broken join: a declared id no spec carries, a spec row "
                         "this parser cannot read, or an unparseable validate.json")
    ap.add_argument("--next", nargs="?", type=int, const=10, default=None, metavar="N",
                    help="print the N worst-covered capabilities as typed actions (default 10)")
    ap.add_argument("--metrics", action="store_true", help="wiki weight per capability (the consolidation trigger)")
    ap.add_argument("--whiteboard", action="store_true",
                    help="the training countdown by community — capabilities a passed rep exposed and the wiki carries")
    ap.add_argument("--include-out-of-scope", action="store_true",
                    help="count topics marked '**Scope:** out-of-scope' as if they were in scope")
    args = ap.parse_args(argv)

    # Every spec is parsed, always: capability ids are one global namespace (`cicd.md` alone
    # carries CICD-*, GIT-* and AUTO-*), so the set of ids that EXIST can only be the union.
    # `--topic` narrows what is reported, never what an id is checked against.
    all_specs = load_specs()
    specs = {t: s for t, s in all_specs.items() if not args.topic or t == args.topic}
    if not specs:
        print(f"No capability specs found in {CAPABILITIES}"
              f"{f' for topic {args.topic}' if args.topic else ''}.")
        return 1
    known_ids = {c["id"] for s in all_specs.values() for c in s["caps"]}
    spec_warnings = [w for s in specs.values() for w in s["warnings"]]
    for w in spec_warnings:
        print(f"SPEC WARNING: {w}", file=sys.stderr)

    completions = _load_completions()
    wiki_decl = load_wiki_declarations()
    declarations, broken_specs = load_task_declarations()
    for b in broken_specs:
        print(f"UNREADABLE TASK SPEC: {b}", file=sys.stderr)

    alt_view = args.metrics or args.whiteboard   # these replace the per-topic report, not add to it
    all_rows, out_of_scope, total_orphans = [], [], 0
    for topic, spec in specs.items():
        rows, orphans = build(topic, spec, completions, wiki_decl,
                              declarations.get(topic, {}), known_ids)
        total_orphans += len(orphans)
        # An out-of-scope topic is still parsed and still `--check`ed — its ids stay valid
        # references — but it is kept out of every count and every queue, so nothing
        # downstream proposes work against a Fabric area the KB has decided not to pursue.
        if spec.get("out_of_scope") and not args.include_out_of_scope and not args.topic:
            out_of_scope.append((topic, rows, spec["out_of_scope"]))
            continue
        all_rows.append((topic, rows, orphans))
        if args.next is None and not alt_view:
            print_report(topic, rows, orphans)
        if args.metrics:
            print_metrics(topic, rows)
    if out_of_scope and args.next is None and not alt_view:
        print_out_of_scope(out_of_scope)

    # The ids no per-topic pass can reach: wiki declarations (a page may declare any topic's
    # ids, and a page in no topic's folder at all), and tasks in a gym folder no spec covers.
    # Checked against the union, so an id belonging to NO spec is caught wherever it was written.
    loose = [(cap_id, pages) for cap_id, pages in sorted(wiki_decl.items())
             if cap_id not in known_ids]
    for topic, decl in sorted(declarations.items()):
        if topic in all_specs:
            continue
        for task_id, caps in sorted(decl.items()):
            loose += [(cap_id, [task_id]) for cap_id in sorted(set(caps) - known_ids)]
    total_orphans += len(loose)
    for cap_id, declarers in loose:
        print(f"  ORPHAN {cap_id} declared by {', '.join(declarers)} — no capability spec "
              f"carries it")

    if args.whiteboard:
        print_whiteboard(all_rows, specs, completions)

    if args.next is not None:
        print_next(all_rows, args.next)
    if args.write:
        COVERAGE_MD.write_text(render_markdown(all_rows, out_of_scope), encoding="utf-8")
        print(f"Wrote {COVERAGE_MD.relative_to(REPO_ROOT).as_posix()}")
    if args.check:
        # Three ways the join can be broken, all of them silent until now, all of them a
        # broken reference rather than a style point: an id no spec carries, a spec row this
        # parser could not read, and a task spec it could not read.
        failures = []
        if total_orphans:
            failures.append(f"{total_orphans} declared capability id(s) no spec carries")
        if spec_warnings:
            failures.append(f"{len(spec_warnings)} capability-spec row(s) this parser cannot "
                            f"read correctly (see SPEC WARNING above)")
        if broken_specs:
            failures.append(f"{len(broken_specs)} unreadable validate.json — the capabilities "
                            f"they declare are missing from this report")
        if failures:
            print("CHECK FAILED: " + "; ".join(failures))
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
