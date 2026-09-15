---
name: fabric-ingest
description: "Use when a Fabric build, gym rep, community article, thought, or experiment has produced something worth capturing into the Fabric KB. Triggers: 'ingest this', 'capture the gotchas', 'add to the wiki', 'log this gym rep', 'record the bake-off result', after running a gym task, or after an experiment finishes."
---

# fabric-ingest

The generation loop. Take a real source — a gym rep, an experiment result, a community article, or a captured thought — and distil it into the wiki, updating the index and log. Read `CLAUDE.md` (the schema) first; it defines the layers, topics, and conventions this skill assumes.

**The evidence rule:** wiki content comes only from building real things in Fabric. If a claim's source isn't in `2_raw/`, do not write it — get the source first. Templates live in `0_admin/references/`.

**The capability spec is human-owned.** `0_admin/capabilities/<topic>.md` is the user's file. Read it to pick the `capabilities:` a page or atom claims, and claim only ids it already contains — never add, reword, retire or seed a row, and never touch a `**Scope:**` marker, however good the evidence you just synthesized looks (see *The capability spec is human-owned* in `CLAUDE.md`). Ingest is the operation most likely to hit this: distilling a rep or an experiment is exactly what surfaces a real capability no row names. **That is a finding to report, not an edit to make** — leave the evidence in `2_raw/`, record the deferral (below), tell the user, and stop. If a page or atom you are writing wants an id the spec lacks, ship it without that id rather than inventing one.

**Where a deferral goes — one place, and it is a log.** Append one row to `0_admin/logs/capability-deferrals.md`, creating it on first use with this header:

```
# Capability deferrals

Records, not knowledge — never cited in `3_wiki/`, never a work queue. Append only.

| Date | Topic | Token | Evidence | What the evidence shows an agent can do |
| ---- | ----- | ----- | -------- | --------------------------------------- |
```

One row per deferral: `| YYYY-MM-DD | <topic> | needs-capability-decision | <2_raw/ path> | <one line> |`. **Record the observation, never a drafted row** — no proposed id, no proposed spec wording, nothing a later run could paste into `0_admin/capabilities/`. `0_admin/logs/` is the sanctioned home because a run log is a record and is read as one (`CLAUDE.md`: run logs live there *and only there*, and nothing in them is knowledge); a placeholder page, a TODO, or a "proposed capabilities" file is forbidden precisely because a proposal left in the repo becomes a proposal acted on by the next run. A later ingest reads this file for one reason only: to avoid re-reporting a gap already deferred. Then say it in the ingest summary, naming the topic and the evidence path — an unreported finding dies.

**Write dense, not readable.** The wiki is consumed by *agents*, not humans: every page is context someone pays for on every read. Every sentence must carry a fact an agent would otherwise get wrong; every token must earn its place. Cut: preambles, restatements of the heading, motivational framing, "it's important to note", narrative transitions, hedging, and any sentence that would survive deletion without losing a fact. Prefer the terse-but-complete register the existing pages use — a claim, its cause, its fix, its evidence tag, in as few words as the meaning allows. Density is not abbreviation: keep the specific names, values, error strings, and thresholds; drop the connective prose around them. A page that reads slightly blunt to a human is correct. **This applies to `3_wiki/` only** — `2_raw/` preserves its source verbatim, and `log.md` stays legible as a changelog.

**Run only on an explicit user instruction.** Ingest is the human-gated step of the pipeline: the user reviews the source — gym rep reports especially — before it is synthesized into the wiki. Completing a gym rep (**execute-gym-rep**) ends at the gym rep report + tracker and must **not** chain into this skill automatically; "after running a gym task" in the trigger list means *the user asks for ingest after a task*, not that a task run implies one. **One exception:** inside an **execute-gym-circuit** run, the user's invocation of the circuit *is* the explicit instruction for every task in its list. The subagent ingests its own gym rep report without asking, and the per-task commit is what keeps it reviewable.

## Two steps, always both

### 1. Land the source in `2_raw/`

Route by flavour:

- **Gym rep** (ran a task, learned something) → `2_raw/gym-rep-reports/<topic>/YYYY-MM-DD-<task>.md` using `0_admin/references/gym-rep-report-template.md`. Record what broke honestly — the failures are the gold.
- **Experiment / bake-off** → `2_raw/experiments/<YYYY-MM-exp-slug>/` — the raw logs + data. This feeds a `decisions/` page.
- **External / community article** → `2_raw/<topic>/YYYY-MM-DD-slug.md` using `0_admin/references/raw-template.md`. Preserve the text; clean noise only.
- **A thought / idea** → `2_raw/thoughts/YYYY-MM-DD-slug.md`.

Slug: kebab-case, ≤60 chars, from the title. Published date unknown → omit the date prefix, set `published: Unknown`. Same-name collision → append `-2`. Raw files are immutable once written.

### 2. Compile into `3_wiki/`

Decide where the knowledge belongs:

- **Extends an existing page** → merge in. Add the new source to that page's `sources:`. Refresh `updated:`. If the source contradicts what's there, annotate the disagreement with attribution — never silently overwrite.
- **New capability or pattern** → new page in the most relevant topic folder, using the matching template. Name the file after the concept, not the raw file.
- **A new topic, or a topic's first capability pages** → follow the **six-page cluster** that structures a topic's capability knowledge (see CLAUDE.md): `prerequisites-and-fit` · `design-framework` · `build-framework` · `operate-framework` · `coding-guidance` · `gotchas`, each tagged with its `category:` in frontmatter. Write only the cluster pages the raw evidence actually supports (a first rep typically fills `prerequisites-and-fit`, `build-framework`, and `gotchas`) and let later ingests add the rest — but do **not** collapse the cluster into one monolithic page; the cluster is what lets a consuming agent pull only the page its current phase needs.
- **An experiment that resolved an X-vs-Y question** → a `3_wiki/decisions/<slug>.md` page (`0_admin/references/decision-template.md`). These are the highest-signal output — capture both options, the logged evidence, and the verdict.

Set frontmatter honestly: `type`, `topic`, `community` (`frontier-data-engineer | frontier-decision-engineer | both` — the topic usually implies it; see `CLAUDE.md`), `fabric_release` (the release you validated against), `status: current`, `capabilities:` (the ids from `0_admin/capabilities/<topic>.md` this page is a canonical home for — only where an agent reading it learns to *do* the thing; a passing mention is not documentation), and `sources:` listing every `2_raw/` file that proves the page. **Every page must cite at least one raw source — two sanctioned exceptions, both from *Provenance is the trust mechanism* in `CLAUDE.md`:** a `status: placeholder` page (it asserts nothing, so `sources: []` is correct there — and it must **not** declare `capabilities:`), and a **seed page** — `status: current` with `sources: []` and a `provenance_notes` block declaring it ships pre-evidence, which is how the shipped worked examples start. A seed page's exemption **ends at the first ingest that gives the page real sources**: when your source extends one, fill `sources:`, drop the `provenance_notes` block, and it is an ordinary page from then on. Neither exemption licenses a new page written from general knowledge — you do not mint seed pages. No insider shorthand, and no filler — self-contained means every *fact* is present, not that every fact is explained at length.

Before saving a page, re-read it once and delete every sentence that adds no fact. Templates show the *sections*, not a word budget — a section with one fact gets one line.

### Cascade

**Atoms are part of the cascade.** If the topic carries an execution index (`3_wiki/<topic>/atoms/`), refresh it in the same ingest: for every capability the rep validated, mint or update its atom — a ≤30-line card (frontmatter: `type: atom`, singular `capability:`, `requires:` with `env:` prerequisites named by their `preflight_probe.py` check where one exists plus `atoms:` prerequisites, `evidence:`, `sources:`, `derived_from:`) carrying the when-it-fires line, the proven call shape, and the gotcha one-liners — and its row in `atoms/index.md`. In a fresh clone every `atoms/` folder is empty and no `atoms/index.md` exists — the topic's first ingest creates both. An atom cites the same `2_raw/` sources as the cluster page it derives from, and is only ever minted from rep-proven evidence; design-gate capabilities get no atom. When an ingest corrects a claim that an atom restates, correct the atom **and** its cluster page in the same pass — the atom is a view, never a second source of truth.

**Draft-atom promotion.** A gym rep report from a passed rep may embed **draft atoms** — hypotheses the agent wrote for capabilities that had no atom, tested by the run, each with a verdict. Promote a draft into `3_wiki/<topic>/atoms/` only when its verdict is **confirmed** (or corrected-then-confirmed — promote the corrected text) *and* the task's checks actually exercised it: set `evidence: proven`, cite the gym rep report as the source, and add the index row. An **untested** draft is not promotable — it is a hypothesis with no outcome, and promoting it is speculative generation; leave it in the report for a future task to test. Drafts from failed or parked reps are never promoted.

After the primary page, scan the same topic folder and the `index.md` entries in related topics for pages the new source materially affects. Update each and refresh its `updated:`. A new release or a new decision often supersedes older claims — mark the old page `status: superseded` and link forward rather than deleting.

**When you correct a claim rather than extend one, the gym is part of the blast radius.** A `task.md` that restated the old fact in its own prose has no backlink, so your correction leaves it teaching the refuted version — and a rep reads `task.md` *before* the wiki, so the stale copy wins. Two required follow-ups, in the same ingest:

1. `grep` `1_agent-gym/` for task prose repeating the claim you just corrected, and report every hit. Correcting the task text is task-authoring work and may be out of scope for the ingest — **but reporting the hits never is: an unreported finding dies.**
2. Add a `REFUTED` entry to `.claude/skills/fabric-lint/refuted_claims.py` naming the corrected claim, with a pattern and what is true instead, then run `python .claude/skills/fabric-lint/stale_task_claims.py --self-test`. Without an entry nothing watches the gym for that claim, and `fabric-lint` will report the omission as `UNREGISTERED` anyway.

## Post-ingest

- **`3_wiki/index.md`** — add/update a row for every touched page (link · type · one-line summary · updated · release). New topic → add its one-line description. See `0_admin/references/index-template.md`.
- **`3_wiki/log.md`** — append:

  ```
  ## [YYYY-MM-DD] ingest | <primary page title>
  - Updated: <cascade-updated page title>
  ```

  Omit `- Updated:` lines when nothing cascaded. This log is the wiki's changelog — keep entries readable.

## Initialization

On the first ingest, create anything missing without overwriting: `2_raw/` (+ `thoughts/`, `experiments/`, `gym-rep-reports/`), `1_agent-gym/`, `3_wiki/` with `3_wiki/index.md` (`# Fabric — Knowledge Base Index`) and `3_wiki/log.md` (`# Fabric — Wiki Log`), and `3_wiki/decisions/`.
