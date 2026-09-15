---
name: check-training-progress
description: "How far along is the agent's training? The KB's derived-state skill: show the training whiteboard, audit the capability specs in 0_admin/capabilities/<topic>.md (the spec both the gym and the wiki answer to), and keep 1_agent-gym/tracker.md's rep counts honest. Helps the user author specs when they ask (the spec is human-owned; no unattended run adds a capability). Triggers: '/check-training-progress', 'what's our coverage', 'where are the gaps', 'what should I build next', 'write the capability list for lakehouse', 'is topic X done', 'audit the capability map', 'agent training progress', 'show the whiteboard', \"how's training going\", 'update the gym tracker', 'check the tracker', 'sync the rep counts'. Reports the two-axis state (gym: declared→covered→proven→hardened; wiki: documented) and ranks what to do next."
---

# check-training-progress

Everything this skill reports is **derived** — from `0_admin/capabilities/`, the task folders in
`1_agent-gym/`, the passed gym rep reports in `2_raw/gym-rep-reports/`, and wiki frontmatter. Nothing here is
hand-maintained, and the two artifacts it generates (`0_admin/capabilities/coverage.md` and
`1_agent-gym/tracker.md`) must never be hand-edited.

It carries two scripts, and they already shared a dependency before they shared a folder:

| Script | Answers | Writes |
|---|---|---|
| `capability_coverage.py` | which **capabilities** are proven and documented | `0_admin/capabilities/coverage.md` |
| `tracker_sync.py` | which **gym tasks** have passed | `1_agent-gym/tracker.md` |

`tracker_sync.py` also defines `TOPIC_META`, the canonical topic→code map that
`create-gym-task` and `execute-gym-rep` import — so it is a library as well as a command.

`0_admin/capabilities/<topic>.md` states what an expert Fabric agent must be able to **do** in a topic. It is the spec both working layers answer to:

```
capability  ──►  N gym tasks (different angles)  ──►  landed reps  ──►  wiki page
 (declared)          (covered)                          (proven)         (documented)
```

A capability is **done** only when it is *hardened* (landed in as many tasks as it has angles) **and** *documented* (a wiki page carries it). Anything else is a gap with a name.

Only the spec table is hand-written. Every state is derived — run the script, never write state into the spec file.

## Operations

| I want to… | Do this |
|---|---|
| See where a topic stands | `python .claude/skills/check-training-progress/capability_coverage.py [--topic <topic>]` |
| Know what to work on next | `… capability_coverage.py --next [N]` — typed, ranked actions |
| Refresh the derived report | `… capability_coverage.py --write` → `0_admin/capabilities/coverage.md` |
| Catch broken references | `… capability_coverage.py --check` — exits 1 on a broken join (below) |
| See whether ingest is appending instead of rewriting | `… capability_coverage.py --metrics` — pages per documented capability, plus topic pages declaring nothing |
| Track training progress at a glance | `… capability_coverage.py --whiteboard` — the countdown, grouped by community |
| Write a spec for a new topic | *Authoring a capability spec*, below |
| Check the gym tracker for drift | `python .claude/skills/check-training-progress/tracker_sync.py` — exits 1 if stale, 2 if it cannot be derived |
| Refresh the gym tracker | `… tracker_sync.py --write` → `1_agent-gym/tracker.md` |

### The whiteboard (`--whiteboard`)

`--whiteboard` answers a different question from the rest of this skill. Everywhere else, **done** means *hardened* — every angle landed — which is the right bar for "is this capability safe to rely on" but a poor progress signal: a capability proven once sits at zero until its second task lands, so the number stays flat for weeks while real work happens. `--whiteboard` counts **learned** instead — a passed rep exposed the capability *and* a wiki page carries it — and prints `mastered` underneath as the depth line. Use it for tracking momentum; use the default report and `--next` for deciding what to build.

It is **display-only**: run it and print the output verbatim in a fenced code block so the bars align. Do not re-summarize, re-count, or reformat the numbers — the whiteboard *is* the deliverable. After the block, one or two sentences of commentary are fine, but only if you actually know what moved; otherwise say nothing.

- **ROUND 1 · BREADTH** counts capabilities **learned**. This is the momentum number.
- **ROUND 2 · DEPTH** counts capabilities **mastered** (what the rest of this skill calls *hardened*).
- **awaiting ingest** is evidence earned but not banked — reps passed, wiki page not yet written.

For "what should I build next?" use `--next`; to actually close a gap, hand off to **create-gym-task** / **execute-gym-rep**.

### The gym tracker (`tracker_sync.py`)

`1_agent-gym/tracker.md` advertises four numbers, all derived, none ever hand-maintained:

- **total tasks per topic** — how many `AG-<CODE>-<NNN>` task folders sit in `1_agent-gym/<topic>/`.
- **total tasks overall** — the sum across topics.
- **completed per topic** — how many PASSED gym rep reports sit in `2_raw/gym-rep-reports/<topic>/`.
- **completed overall** — the sum across topics.

A gym rep report counts as a completion when its frontmatter has `status: passed` and a `task_id: AG-<TOPIC>-<NNN>` mapping to a real task (latest passed report per task wins). Same provenance rule as the rest of the KB: completion is proven by a `2_raw/gym-rep-reports/` entry, not a ticked checkbox. The script is a pure local file scan — no Fabric `.env`, no network.

**"A real task" is checked, both halves, against the filesystem** — the folder a report sits in never supplies the answer. The id's topic segment must be the code of the topic folder the report is filed under, *and* its number must be a task folder that exists in `1_agent-gym/<topic>/`. A report failing either is announced on stderr as `SKIPPED REPORT: <file> — <reason>` and counted nowhere. Treat one as a finding: it is a typo in the frontmatter or a report filed under the wrong topic, and until it is fixed that rep reads as never run. The same rule governs `capability_coverage.py`, which reads this same function — an unchecked report does not just move a tracker row, it flips capabilities to `proven` on another topic's evidence.

1. **Check first.** Run it with no flags. If it reports up to date, say so and stop.
2. **If stale, write.** Run `--write`, report the authoritative counts it prints, and skim the regenerated rows.
3. **In-progress is manual.** `--write` only emits ✅ passed / ⬜ not started — "in progress" has no filesystem signal. Set 🟡 by hand after the regenerate if a rep is mid-flight.

New topics are picked up automatically (an unmapped folder falls back to a 3-letter code); add it to `TOPIC_META` for a nicer label. Two folders whose codes collide — `lakehouse` and a new `lakehouse-v2` both derive `LAK` — are **refused**, not merged: the script prints `TRACKER CANNOT BE DERIVED` and exits 2, because one topic's totals would otherwise overwrite the other's while the file still renders both sections. Give one of them an explicit code in `TOPIC_META` (and the matching `TOPIC_DIRS` entry in `gym_validate.py`) and re-run. Run this on demand whenever the tracker might have drifted, and as the **tracker step of `execute-gym-rep`'s post-validation** (report → tracker → ingest).

`--next` types every action so it can be dispatched without re-deriving anything:

- **author-task** — nothing exercises the capability. The deepest hole; hand it to **create-gym-task**.
- **ingest** — reps landed but no wiki page carries it. Evidence earned and not banked; hand it to **fabric-ingest**.
- **run-rep** — a task exists and has never been run. Hand it to **execute-gym-rep**.
- **add-angle** — proven, but from too few angles. Needs a *materially different* task, not a re-run.

## Authoring a capability spec

**Only on the user's explicit instruction.** `0_admin/capabilities/` is human-owned (see *The capability spec is human-owned* in `CLAUDE.md`): the user decides what this KB covers, and no unattended run may add, seed, reword, or retire a row. This section is how to help *when they ask* — "write the capability list for lakehouse", "add a row for X", "should this be a capability?" — not a licence to keep the list growing on its own. If you arrive here from a scan, a release diff, or a coverage gap rather than from a request, stop and report instead.

Derive it; don't free-associate it. Four sources, in this order:

1. **Our own landed evidence** — `2_raw/gym-rep-reports/<topic>/` and the topic's wiki pages. What has already bitten us is the most certain content in the list.
2. **The platform surface** — the item's REST API and item-definition spec (`2_raw/specs/`). This is what stops the list being a record of only the parts we happen to have touched.
3. **The wiki's trap register** — `3_wiki/<topic>/gotchas.md`. Each trap implies a capability an agent lacks.
4. **Tutorials, last** — any human-facing tutorial catalogue for the topic. They are evidence of what the discipline contains, never the spec: written for humans, they under-cover agent-specific ground (API-driven work, failure triage, idempotency, recovery) and over-cover UI walkthroughs and language drills an LLM already knows. Their real value is negative — showing which ground is *already held*, so you know what is **not** worth a row.

**The granularity rule.** A capability is a thing the agent must be able to *do*, and it is distinct when getting it wrong **fails in its own way**. If two candidates fail the same way, they are one capability. Without this the list degenerates into a documentation index — and an unbounded one.

**Set `angles` honestly.** 2 is the default; 3 for the capabilities that carry the topic. An angle is a materially different situation — happy path · failure mode · at scale · in combination · inverted requirement — not a second run of the same shape. The script counts landed tasks as a proxy; enforcing that the angles actually differ is **create-gym-task**'s job at authoring time. Write it as a **plain integer in an `angles` column**: a range (`2-3`), a note, or a missing column is a `SPEC WARNING` and a red `--check`, because `angles` is the denominator of the DEPTH line and every unreadable cell shortens the distance to `hardened`.

**Size.** Expect 15–30 for a mature topic. A list of 60 has failed the granularity rule; a list of 6 is a heading, not a spec.

## Declaring coverage

- **Tasks** declare in `validate.json`: `"capabilities": ["OMR-C05", "OMR-C21"]` — the capabilities the task's *checks actually exercise*. Under-claim rather than over-claim: a claimed-but-unasserted capability reads as proven when it isn't, which is worse than a visible gap.
- **Wiki pages** declare in frontmatter: `capabilities: [OMR-C19, OMR-C20]` — only where the page is a canonical home for it. An agent reading that page should learn to *do* the thing. A passing mention is not documentation.

Ids are stable and never renumbered. To remove one, move its row to the spec's *Retired* section with a reason and date — landed reps and wiki pages still reference it.

## Scoping a whole topic out

*Retired* removes one capability that was wrong or merged. When an entire **Fabric area** is not a KB focus, put a topic-level marker in the spec's header instead:

```
**Scope:** out-of-scope — <why, and the date decided>
```

The script honours it: the topic's rows are still parsed and still `--check`ed (their ids stay valid references), but the topic is dropped from the gap counts, from `0_admin/capabilities/coverage.md`'s body, and from the `--next` queue, so nothing proposes work against it. It is listed in an *Out of scope* footer rather than hidden, because a silent exclusion is indistinguishable from a bug. `--include-out-of-scope` counts it anyway; `--topic <name>` reports it normally, since naming it is an explicit opt-in. To revive a topic, delete the marker line.

Scoping out is a **human decision** — never something an unattended run does on its own, in either direction. The current out-of-scope set is derived from the `**Scope:**` markers — run `capability_coverage.py` for the current list, never trust a list in prose. The shape this guard exists for is described in *The capability spec is human-owned* in `CLAUDE.md`.

## Invariants

- **The spec is human-owned; the state is derived.** Two separate rules, both enforced by stopping rather than writing. *Human-owned*: only the user adds, changes or retires a capability row — an unattended run that thinks one is missing reports it and stops (`CLAUDE.md`, *The capability spec is human-owned*). *Derived*: if you find yourself editing a coverage **state** into `0_admin/capabilities/<topic>.md`, stop — that is exactly how the tracker and the index drifted before this existed.
- **`0_admin/capabilities/coverage.md` is generated.** Never hand-edit it.
- **Capability state is coarser than per-claim evidence.** A landed rep marks the whole capability `proven`, while an individual trap inside it may still be untested — that finer state lives in the wiki's inline `proven`/`unverified` tags. The two axes are complementary; the capability map does not replace per-claim tagging, and `--next` will not surface a single unverified claim inside an otherwise-proven capability.
- **`--check` belongs in the lint pass.** It fails on the three ways the join can be broken, each of them silent before it: an id declared by a task or page that **no spec anywhere carries** (ids are one global namespace — `cicd.md` alone carries `CICD-*`, `GIT-*` and `AUTO-*` — so every declared id is checked against the union of all specs, not against one topic's prefix); a capability-spec row the parser cannot read (a table section missing its `| id | … |` header row, or a section with no `angles` column — both silently *raise* the reported coverage, so they print as `SPEC WARNING` and fail); and a `validate.json` that will not parse (which downgrades every capability that task declares back to `declared`, and surfaces in `--next` as "author-task" for a task that already exists). All three diagnostics go to **stderr**, so they never corrupt the stdout a caller renders verbatim. A broken reference is not a style issue — fix the declaration, never the human-owned spec.
