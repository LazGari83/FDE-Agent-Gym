# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working in this repository.

It is also **the schema** — the framework the whole repo hangs off — and the repo's single
root instruction file. It is a router, not a dump: read it first, then follow the pointer to
the skill that runs the operation you need. (`README.md` is the human front door; this file is
the agent's.)

**Using a different assistant?** Run the **port-project-to-another-model** skill. It
migrates this repo to the layout your runtime expects — `AGENTS.md` for Codex, Cursor and
Copilot, `GEMINI.md` for Gemini / Antigravity — moving the skills (scripts included) to
`.agents/skills/`, rewriting every path reference, and deleting the Claude-specific files.
One-way: after migration the generated root file is your schema, and this file is gone.

## What this repo is

**A set of tools for building and growing an expert knowledge base that automates the
design, build, and operation of Microsoft Fabric solutions.** The knowledge base itself is
the distilled residue of actually solving Fabric problems — and a fresh clone ships the
framework with almost none of the knowledge: the tracker reads 0, the wiki holds one seed
topic, and every count moves only when something runs in **your** tenant.

**The core process.** In the wiki training phase, for each topic:

1. **Write a capability index** — the human-owned list of what an expert agent must be able
   to do (`0_admin/capabilities/<topic>.md`).
2. **Create agent-gym tasks that cover the full capability index** — every capability
   exercised at least once.
3. **Execute each agent-gym task `(/execute-gym-rep)` or `(/execute-gym-circuit)`** — each one builds and validates something real in Fabric,
   using thin repo-maintained wrappers over the official Microsoft Fabric Python SDK,
   plus the protocol and execution code no SDK ships (`code/`).
4. **A gym-rep report is the output of each gym task** — documenting the gotchas, the useful
   code generated, and much more.
5. **That knowledge is ingested and synthesized `(/fabric-ingest)` into the wiki** — an ever-maintained
   collection of high-quality learnings through experience.

This process is repeated over many gym tasks, across many topics, until your wiki is mature
and a real asset. At that stage, you use the wiki on your own projects: it becomes the
context an agent reads while designing, building and operating real Fabric solutions.

## The layers (the map)

The top-level folders are numbered as the path you walk them in:

```
0_admin/        the governance layer — what "done" means, and the shapes everything follows
1_agent-gym/    the practice layer   — where capabilities are proven, in your tenant
2_raw/          the evidence layer   — immutable dated sources; everything the wiki cites
3_wiki/         the knowledge layer  — the curated deliverable an agent builds from
```

Work flows **1 → 2 → 3**: a gym rep produces evidence (its report lands in
`2_raw/gym-rep-reports/`), and ingest turns evidence into knowledge (`3_wiki/` pages) —
while **0** defines what finished looks like and is never itself cited as knowledge.

Inside each layer:

```
0_admin/
  capabilities/<topic>.md    THE SPEC (human-owned): what an expert Fabric agent must be
                             able to DO — id · failure mode · angles · evidence type per row
  capabilities/coverage.md   derived two-axis report — generated, never hand-edited
  references/                the page templates, one per artifact type (see that README)
  logs/                      run ledgers — records, never knowledge

1_agent-gym/
  tracker.md                 the training scoreboard (starts at 0; tracker_sync.py keeps it honest)
  <topic>/index.md           the topic's syllabus
  <topic>/<task>/
    task.md                  plain-language numbered outcomes with check ids inline — no
                             mechanisms, no capability ids; classifying them is the rep's work
    provision.py             self-contained sample-data fixture
    validate.json            declarative typed checks + the `capabilities:` the task exercises

2_raw/
  <topic>/YYYY-MM-DD-*.md    captured articles, docs, release notes — read, never edit, always dated
  thoughts/ · experiments/   raw expertise dumps · A/B bake-off logs
  gym-rep-reports/<topic>/   the report each passing rep writes — what wiki pages cite
  specs/<item>/              dated Learn spec snapshots + per-item CHANGELOG (see that README)

3_wiki/
  index.md                   curated router: one row per page — read this first
  log.md                     append-only op log; doubles as a readable changelog
  <topic>/<page>.md          the topic page clusters (type + community in frontmatter)
  <topic>/atoms/             ≤30-line per-capability execution cards — the one sanctioned subfolder
  decisions/<x-vs-y>.md      experiment bake-offs — highest-signal, top-level for prominence
```

Supporting the four layers:

```
code/               the approved, runnable toolkit — shared across topics, LLM-owned (see its README)
.claude/skills/     THE SKILLS (source of truth): check-training-progress · create-gym-task ·
                    execute-gym-rep · execute-gym-circuit · fabric-ingest · fabric-lint ·
                    port-project-to-another-model · review-codebase · update-framework
tools/release/      cuts the distribution bundles (see its README); never ships in one
CLAUDE.md           this file — the schema, and the only root instruction file
README.md           the human front door
```

## Topics (the shipped set)

| Community                     | Topics                                                               |
| ----------------------------- | -------------------------------------------------------------------- |
| `frontier-data-engineer`      | `lakehouse` · `pipelines` · `openmirror` · `ingestion` · `key-vault` |
| `frontier-decision-engineer`  | `ontology` · `graph` · `data-agent` · `azure-app`                    |
| `both`                        | `cicd` · `capacity` · `testing`                                      |

`frontier-data-engineer` is the data-engineering track (get data right);
`frontier-decision-engineer` is the analytics-and-applications track (get value from
data). Every wiki page names its track in a required `community:` frontmatter field (see
Conventions), so a consuming agent can filter to its own track or read across both.

**A topic's scope and boundaries live in its capability spec.** Each
`0_admin/capabilities/<topic>.md` opens with what the topic covers, where it ends, and its
prerequisites — read that before working a topic; this file does not restate it. Two
structural notes: `ontology` and `graph` are the **Fabric IQ pair** (design the ontology,
then query the graph it projects) and share one spec, `ontology.md`; `capacity` and
`testing` ship specs but no gym tasks yet — declared gaps the coverage report shows honestly.

**Growing the set.** Reuse an existing topic if it is close enough. Create a new one only for
a genuinely distinct Fabric area (`semantic-models` is already scaffolded): agree its
capability spec first (the spec is human-owned — see Conventions), give it a `community:`,
and add its one-line description to `3_wiki/index.md`.

## Page types (`type:` frontmatter, all inside topic folders)

- **capability** — one per Fabric area: what it does, how to drive it, the gotchas.
- **pattern** — a cross-cutting reusable technique or recurring gotcha (`dynamic-rls`,
  `spn-auth`, `incremental-load`).
- **blueprint** — an end-to-end solution (`executive-dashboard`, `medallion-lakehouse`,
  `metadata-driven-ingestion`).
- **decision** — the output of an experiment bake-off (X vs Y, built both ways, run in
  parallel, resolved with evidence). Lives in `3_wiki/decisions/`. The highest-signal pages.

**Topic cluster convention (the default page structure):** a topic's capability knowledge
decomposes into a six-page cluster — `prerequisites-and-fit` · `design-framework` ·
`build-framework` · `operate-framework` · `coding-guidance` · `gotchas` — recorded in a
`category:` field. The first four follow the lifecycle an agent actually walks (is this the
right tool → decide the shape → build it → run it once it is live); `coding-guidance` is the
code for all of them and `gotchas` the trap register across all of them. The shipped
`3_wiki/lakehouse/` cluster is the worked example of the shape.

**`operate-framework`** covers what happens _after_ a build is live and is the cluster's
most-neglected page: monitoring and what a healthy system actually looks like, lifecycle
changes on a running item, incident entry points, decommissioning, and the environment-level
failures that masquerade as application bugs. A capability belongs here when its failure is
introduced while operating rather than while designing or building. A new topic starts with
whichever cluster pages its raw evidence supports and grows the rest over later ingests —
never a single monolithic capability page. Cluster pages also carry an `evidence:` axis
(`proven` | `mixed` | `unverified`) _distinct from_ the lifecycle `status:`: `proven` =
confirmed in your own Fabric testing (a rep report or experiment in `2_raw/`); `unverified` =
documented but not retested in your tenant — flag it before relying. Per-claim tags appear
inline in the page body.

**Cluster section or its own pattern page?** The cluster is the default home; a topic that
sprouts pattern pages for every finding loses its shape. Split material out into a `pattern`
page only when **both** hold:

1. **It is a different kind of thing to read.** A cluster page is read _before_ building; a
   pattern page earns its place when the material is a procedure followed _during_ a specific
   situation — an incident runbook, a modelling technique applied only when the requirement
   calls for it — so an agent would arrive at it by need, not by reading the topic through.
2. **It has outgrown a section.** Roughly a fifth of its host page, or more than three or
   four H2s' worth. Below that, a section with a clear heading beats a new file.

When you do split, leave a one-line pointer in the cluster page rather than a summary — a
summary is the thing that drifts. Anything cited from two or more cluster pages belongs in
**one** canonical home with the others pointing at it; state which home is canonical in the
page itself.

Rep reports are **not** wiki pages — they are the `2_raw/gym-rep-reports/` source-summary
layer that wiki pages cite.

## Operations → skills

Skills live in `.claude/skills/`. Claude Code loads them from there and they are invoked as
`/skill-name`.

| I want to…                                                                       | Use                                                                             |
| -------------------------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| Prove my tenant can run a task before starting                                   | **execute-gym-rep**'s `preflight_probe.py` — cached 6h, per-task or `--all`     |
| Run one gym practice task end to end (classify → plan → engine → validate)       | **execute-gym-rep** — e.g. `AG-LAK-001`                                         |
| Run a batch of gym tasks back to back, unattended (rep → ingest → lint → commit) | **execute-gym-circuit** — e.g. `AG-OMR-003 to AG-OMR-006`                       |
| Author a new gym task (from a tutorial, a raw source, or a coverage gap)         | **create-gym-task** — e.g. `/create-gym-task from this tutorial: <path>`        |
| Capture what a build/rep/experiment just taught me into the wiki                 | **fabric-ingest**                                                               |
| Health-check the wiki (links, index, stale-vs-release, contradictions)           | **fabric-lint** — after each ingest batch and after each monthly Fabric release |
| Review source under `code/` — duplication, placement, silent failure            | **review-codebase** — `<path>`, plus `report-then-fix` to apply the safe ones  |
| See where coverage stands, what to build next, or the training whiteboard        | **check-training-progress** — `--check` · `--next` · `--whiteboard`             |
| Reconcile the gym tracker's counts with reality                                  | **check-training-progress** — `tracker_sync.py --write`                         |
| Use this KB with Copilot, Gemini or Codex instead of Claude Code                 | **port-project-to-another-model** — migrates the repo to that runtime's layout  |
| Move to a newer framework release without losing the work in this repo           | **update-framework** — `<path to the new .zip or folder>`                       |

Two operations need no skill: **consuming** the wiki is just reading — start at
`3_wiki/index.md` and pull only the page the current step needs — and **refreshing spec
snapshots** is the five-step manual recipe in `2_raw/specs/README.md`.

## Conventions

- **The capability spec is human-owned.** Only the KB's owner writes `0_admin/capabilities/`;
  an unattended run may read it but never add, reword, retire, or seed a row, or change a
  `**Scope:**` marker — no matter how good the evidence looks. A missing capability is a
  finding to report, not an edit to make: leave the evidence in `2_raw/`, record a deferral
  (`needs-capability-decision`), tell the owner, and stop. Never park the proposal somewhere
  else (a placeholder page, a TODO, a "proposed capabilities" file) — a proposal left in the
  repo becomes a proposal acted on by the next run. Coverage gaps are only meaningful against
  a list a person agreed to; a spec that grows itself measures nothing but its own appetite.
  **One exception, and only one:** `update-framework` appends the rows a newer framework
  release adds, on a run the owner started and confirmed. That is the syllabus arriving from
  outside rather than a run growing its own — and it is still the weakest possible edit: new
  ids appended, never a reword, a retirement, a reorder, or a row the owner wrote. Everything
  else that release changed in the spec is reported for the owner to apply by hand.
- **Wiki content comes only from building real things in Fabric.** Every page traces to a
  real solved task, build, or experiment in `2_raw/` — never invent Fabric facts, commands,
  or gotchas, and never generate a page speculatively from general knowledge. If the source
  material for a claim isn't in `2_raw/`, the claim doesn't get written — ask for the source
  instead.
- **Write dense, not readable.** Pages are consumed by agents; prefer exact payloads, error
  codes, and thresholds over narrative filler.
- **Run logs live in `0_admin/logs/`, and only there** — the append-only ledgers
  `gym-run-log.jsonl` and `framework-updates.jsonl`, the gitignored `.preflight-cache.json`,
  and the per-batch circuit logs in `logs/circuits/`. One location by design: these are append-only, so a second copy does
  not merge, it silently loses entries. Nothing there is knowledge — never hand-edited, never
  cited in `3_wiki/`.
- **Tag every page with a `community:`** (`frontier-data-engineer |
  frontier-decision-engineer | both`) — the community track the page serves; the topic
  usually implies it (see the Topics table). It is required frontmatter.
- **One level of nesting** in `3_wiki/` topic folders (pages sit directly under the topic),
  with one sanctioned exception: `<topic>/atoms/`, the execution-card subfolder. Top-level
  `3_wiki/decisions/` is the only non-topic wiki folder.
- **Atoms, drafts, and plans (the rep machinery).** `3_wiki/<topic>/atoms/` holds one
  ≤30-line execution card per _rep-proven_ capability (`type: atom`, singular `capability:`,
  a `requires:` block) — the execution-optimized view of the cluster pages, which stay
  canonical for reasoning; refreshed at ingest, never minted speculatively, and never for
  design-gate capabilities. In a fresh clone every `atoms/` folder is empty — the first ingest
  creates the first card and its `atoms/index.md`. Every rep runs one loop: classify each task
  bullet (the task names no capability or mechanism — that is the exercise), pull the matching
  atoms, and fill each **gap** (a capability with no atom) by researching and writing a
  **draft atom** — a hypothesis, kept out of `3_wiki/` and embedded in the rep report; ingest
  promotes only drafts the passing run confirmed. A rep's **plan** — the typed step list
  `gym_run.py` executes — is always the agent's output, proposed from `task.md` at rep time;
  it is never checked into a task folder, where it would be the answer sheet
  (`gym_run.py --self-test` enforces this).
- **Approved code lives in `code/`, not `2_raw/`.** When a build produces a runnable toolkit
  the KB endorses, it belongs in the top-level `code/` folder — LLM-owned, shared across
  topics, evolving as tasks improve it; a consuming agent calls it directly while building.
  The immutable evidence in `2_raw/` that _proved_ it stays put and is what pages cite as
  `sources:` (`2_raw/` is read-only provenance; code that changes cannot live there). A module
  for a non-Fabric API stays self-contained — importing nothing from the Fabric toolkit, not
  even `config.py`, whose import-time environment read would couple a third-party consent
  flow to Fabric credentials.
- **Date everything.** Raw files are date-prefixed. Pages carry `updated:` and
  `fabric_release:` (the Fabric monthly release the page was validated against — Fabric ships
  monthly, and a dated page is what lets a later reader judge staleness).
- **Provenance is the trust mechanism.** Every wiki page's `sources:` frontmatter lists the
  `2_raw/` file(s) that prove its claims; a consuming agent must be able to trace any claim to
  "proven in build X, dated Y." Two sanctioned exceptions: a `status: placeholder` page
  (below), and a **seed page** — `status: current` with `sources: []` and a
  `provenance_notes` block declaring it ships pre-evidence; shipped worked examples start
  that way, and the exemption ends at the first ingest that gives the page real sources.
- **Status lifecycle:** `placeholder` (an empty slot that makes the gap countable — see
  below) → `current` → `superseded` (a newer release/decision replaced it; keep it, link
  forward) → `archived`. Never silently delete a superseded fact.
- **Placeholders.** A `status: placeholder` page states _what belongs there_ and _what
  evidence unlocks it_ — never the content itself. It asserts nothing, so `sources: []` is
  correct there. A placeholder must **not** declare `capabilities:` — that would mark them
  `documented` in the coverage report on the strength of an empty page, which is worse than
  the gap it was meant to expose. Delete a placeholder rather than fill it if the evidence
  shows the material belongs in a section of a sibling page.
