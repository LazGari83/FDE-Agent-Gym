---
type: gym-rep-report
task_id: AG-{CODE}-{NNN} # canonical gym task id — the tracker parses this; required. {CODE} per topic: LAK=lakehouse, DAT=data-agent, ONT=ontology, OMR=openmirror, AKV=key-vault, ING=ingestion, APP=azure-app, PIP=pipelines, CICD=cicd
task: { task-folder } # e.g. AG-LAK-001
topic: { topic }
date: { YYYY-MM-DD }
fabric_release: { YYYY-MM }
status: passed # passed | failed | partial — the tracker counts only `passed`
validation: "{N}/{M} checks passed ({check names})" # the gym_validate.py tally
workspace_context: # the ids a future audit needs to find the built artifacts
  { key }: { value } # e.g. mirror_id / graph_id / lakehouse_id, workspace_id
---

# Gym rep report: {task title} (AG-{CODE}-{NNN})

## What it was

{The task, in one or two sentences. Link the gym task: `1_agent-gym/{topic}/AG-{CODE}-{NNN}/task.md`.}

## How I did it

{The actual path taken to solve it — the working approach, per phase. Concrete and specific:
which toolkit calls, which decisions, what the validator reported per check.

**Budget: ~15 bullets.** This section is the audit trail, not the knowledge — it exists so a
future reader can retrace the route, and a route is a list, not a narrative. Nothing here is
distilled into a wiki page. If a step taught you something, it belongs in `How it works`.}

## How it works

{**The capability, stated positively — write this section first and lead with it. Deliberately
unbudgeted:** the two sections around it are capped and this one is not, because this section
*is* the wiki. A report that hits 150 lines because `How it works` is rich is a good report; one
that hits 380 because the other sections narrated the run is not. What you now
know about how the Fabric surface actually behaves, phrased so a consuming agent could build with
it having hit no problems at all. This is the material that would still be worth writing if
nothing had gone wrong, and it is what most wiki pages are made of: `What broke` corrects pages,
this section *fills* them.

Cover whichever apply:

- **Granularity** — what unit each API addresses (a whole item? one statement? one row?), and what
  it therefore *cannot* address. Granularity mismatches are the most common wrong mental model.
- **What each call hands back** — status only, structured data, stdout, nothing? How a caller
  learns the outcome, and what it must read from somewhere else.
- **What persists and what does not** — which surfaces leave an artefact, run history or table
  behind, and which vanish with the session.
- **The trade between two ways of doing it** — when a rep exercises two surfaces, tools or shapes
  against the same job, put the head-to-head here as a table: what each is for, what it costs,
  which you would pick for which job. A rep that genuinely ran both arms is the evidence a
  `3_wiki/decisions/` page needs — say so explicitly, so ingest can spot it.
- **The mechanics that were simply not obvious** — the format, the sequence, the primitive that
  turned out to be the right one.

State the observed values (versions, timings, key names, exact response shapes) rather than
paraphrasing them, and mark anything reasoned-about-but-not-observed as such.}

## Code

{Any code or helper functions the run used that did not come from the `code/` library.}

## What broke

{The gotchas, dead ends, and quirks hit along the way — plus documented traps the run
_confirmed_ and anything that _contradicts_ current guidance (name the wiki page it targets).
Be honest about failures — hand-curation from real failure is the whole point, and a correction
that names its target page is directly actionable at ingest. Every claim must trace to real
evidence from this run.

Keep it proportionate. This section corrects the wiki; `How it works` fills it. A rep that went
smoothly should have a short `What broke` and a rich `How it works` — that is a good rep, not a
thin one. Do not inflate friction into findings to make this section look substantial, and do not
let a long trap register crowd out the capability knowledge above it. Close with two short
subsections: **Confirmed as documented** (traps the run re-proved, no change needed) and **Not
exercised** (claims you reasoned about but never observed, so no evidence is added for them).

**Budget: ~40 lines.** One failure gets its error, its cause and its fix — not a transcript of
the debugging. If a single failure needs more than that, what it taught you is capability
knowledge and belongs in `How it works`.}

## Provenance

- Task: `1_agent-gym/{topic}/AG-{CODE}-{NNN}/task.md`
- Validation: `python .claude/skills/execute-gym-rep/gym_validate.py AG-{CODE}-{NNN}` → exit 0 on {YYYY-MM-DD}
