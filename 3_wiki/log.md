# Fabric — Wiki Log

Append-only operation log, newest at the bottom. A readable changelog of every change to this
wiki, so that you — or an agent picking up cold weeks later — can see how the brain reached its
current state, and why.

## Instructions — read before appending

**This log starts empty. It is yours, not a shared history.** Append one entry per operation, in
your own environment, as you go.

- **Append only.** Never edit or delete an earlier entry. If something later turned out wrong,
  add a new entry saying so and link back. A log that gets tidied is a log nobody can trust.
- **Newest at the bottom**, so the file reads as a chronology.
- **Written by:** **fabric-ingest** (one entry per ingest) and **fabric-lint** (one per lint run
  that found something). Add your own entries for anything else that changed the wiki.
- **Name the evidence.** An entry that says what changed but not what proved it cannot be
  checked later. Cite the `2_raw/` file(s) the new or corrected pages now carry in `sources:`.
- **Record what is still open.** The gap you name here is what the next run picks up.
- **Keep it legible.** This is prose for a reader, not a machine format — no page is generated
  from it.

### Entry format

```
## [YYYY-MM-DD] <op> | <one-line headline>
- What changed, and where (which pages, which topic).
- What proved it — the raw source(s) the pages now cite.
- Anything corrected: state the old claim and the new one. Never silently overwrite.
- Anything left open, so the next run has somewhere to start.
```

`<op>` is one of: `init` · `ingest` · `refactor` · `lint` · `archive`.

---

## [2026-08-18] init | knowledge base scaffolded
- Topic folders in place under `3_wiki/`; no pages yet beyond the `lakehouse` worked example.
- Leave this entry where it is; append your first real one below it.

## [2026-08-20] refactor | client docstrings migrated into seed cluster pages
- 22 new seed pages across `openmirror` (3), `pipelines` (2), `key-vault` (3), `ontology` (3),
  `graph` (2), `data-agent` (2), `azure-app` (2), `cicd` (3), `capacity` (2); the `lakehouse`
  cluster gained sections on 4 existing pages (per-write SQL endpoint sync, run submission,
  environments, shortcuts, T-SQL over pyodbc). Index sections added for all nine topics.
- What proved it: nothing yet — these are **seed pages** (`sources: []`, `evidence:
  unverified`, provenance_notes declaring the pre-evidence exemption). The knowledge is
  migrated verbatim from the toolkit's client docstrings (shipped knowledge, pre-dating this
  tenant's evidence); each page converts at the first ingest that gives it real sources.
  Client docstrings now carry pointers to these pages; code-shape constraints stayed in code.
- Corrected: one stale pointer in `lakehouse_client.list_tables` (referenced a nonexistent
  gotchas section; now points at prerequisites-and-fit "What the API plane cannot do").
- Left open: (1) `graphql_api_generator` says a blind create makes a SECOND item while
  `graphql_api_client` says Fabric refuses duplicate display names — contradiction NOT
  asserted on any page; both code comments kept; a live rep must settle it. (2) capacity
  pages declare `capabilities: []` — CAP-C01/C02 cover metrics-app metering the toolkit does
  not document; declaring them would be false coverage. (3) `capabilities:` ids elsewhere
  declared conservatively; the owner may widen them at first real ingest.

## [2026-08-30] lint | 1 issue found, 1 auto-fixed
- Deep-dive on `code/` cross-file imports after the SDK-seam refactor (user report: cross-file
  references failing in production, `fabric_http` named). Verified, by execution and by AST:
  all 44 toolkit modules import cleanly against the pinned SDKs from a foreign cwd; 738
  from-imports + 460 scope-resolved attribute/method references across every repo consumer
  (code/, tests/, validation/, skills, gym provision scripts) resolve; all 45
  `sdk_models(group).Model` references and 385 SDK operation-chain attributes
  (`client.<workload>.<ops>.<method>`, property/alias/call-rooted idioms included) exist on
  the installed `microsoft-fabric-api==0.1.0b24` / `azure-mgmt-fabric==1.0.0` surfaces; 34
  toolkit imports + 31 attribute refs in wiki/task python snippets and all 71 `code/*.py`
  path references in markdown resolve. Symbols removed by the refactor (`handle_lro`,
  `item_url`, `fake_response`, `summarise`, `make_kql_binding`, `make_warehouse_binding`,
  `WORKSPACES`) have no live references anywhere — remaining mentions are explanatory prose.
- Gates run clean: ruff E9,F over code/, skills, gym, tools; pytest tests (71 passed);
  `fabric_test.py --self-test` (704 checks); `gym_run.py --self-test`; `anchor_check.py`;
  `stale_task_claims.py` (333 files clean); `capability_coverage.py --check` (exit 0);
  no tracked `__pycache__`/`.pyc` (git ls-files).
- Auto-fixed: `tools/release/build.py:985` F541 (f-prefix on a placeholder-less string) —
  cosmetic, ruff-fixed.
- Left open: nothing. The reported production import failures do not reproduce from repo
  state at this commit; if they persist, the failing environment is likely missing the
  `sys.path` bootstrap (`import toolkit_path` after inserting `code/` — README step 4) or
  running unpinned SDK versions, not carrying a broken cross-file reference.

## [2026-08-30] refactor | production issue classes from fabric-frontend-kb rescoped into checks
- Source: the `fabric-frontend-kb` branch `claude/azure-keyvault-env-setup-d38fhi` (the first
  real builds on the KB) and its build reports. Direction check first: every code fix on that
  branch (the stale `fr.*` seam repairs in test_ontology/test_openmirror) is ALREADY here via
  the Phase-1 backport — this repo is ahead of that branch, so what ported was the issue
  CLASSES, as checks, not the diffs.
- New: `code/tests/test_import_surface.py` — CI gate verifying every cross-file toolkit
  reference in `code/` (lazy from-imports and module-alias attributes, AST-driven) resolves
  on the imported module; includes a negative self-check so the detector cannot silently
  match nothing. Pins the class that killed the mirror-status checks live twice.
- Generalised: `fabric_test.py --self-test`'s `read-seam-references-resolve` (fr.-only regex)
  → `toolkit-surface-references-resolve` — every toolkit module, lazy from-imports included
  (the fix pattern itself was previously unchecked). Verified it fails on an injected
  `fr.transport_retry` and recovers clean. Self-test count 704 → 705.
- Fixed: `test_git.py` `git-connection` directory compare now normalises the leading slash
  (Fabric reads `directory_name="solution"` back as `"/solution"`; the exact compare failed a
  correct binding with failure text that reads like a wrong bind path). Fixture added.
- Guarded: `notebook_client.run` refuses untyped parameters client-side (server refusal is
  `NotebookBadWebRequest` naming the envelope, no run to inspect); `landing_zone.ensure_table`
  is converge-or-refuse on re-run (identical → no-op; changed → raise; write-once
  keyColumns/fileDetectionStrategy). Docstrings carry the writer-count framing for
  `LastUpdateTimeFileDetection` (derivable from `next_filename`'s max()+1 scan).
- Confirmed already covered, no action: PyToIPynbFailure prologue (wiki + skill + client),
  cumulative `processedRows` (openmirror gotchas/operate), `(name, type)` item identity
  (all listings are typed).
- Left open, for the owner: (1) a git-protocol sibling for the `github-tree`/`github-file`
  checks — in a managed cloud session a proxy answers `api.github.com` with the session's own
  identity (a fabricated token authenticates), so a REST-based provider check can go green
  while asserting nothing; a `git ls-remote`/`ls-tree`-based check survives the runtime but
  needs an auth-plumbing decision (PAT-in-URL vs env). (2) The wiki reframings the build
  reports argue for (LastUpdateTimeFileDetection as a writer-count decision on
  openmirror/coding-guidance; the credential-step-outcome pattern; the NotebookBadWebRequest
  error string beside lakehouse/coding-guidance's correct shape) need those reports ingested
  as 2_raw evidence first — they carry tenant identifiers, so importing them into this
  shareable repo is the owner's call.

## [2026-08-30] lint | frontend merge-wave review: 2 shipped files ported, 1 seed claim softened
- Reviewed the fabric-frontend-kb merge wave (99c18da..c4225d3, the ue-platform ingest) for
  changes to surfaces this repo ships. The two fr.* validation fixes in the wave are already
  here (Phase-1 backport); the rest of the wave is their private wiki growth (matured cluster
  pages citing tenant build reports) and run ledgers — not transferable framework.
- Ported verbatim: `code/validation/test_pipeline.py` — the pipeline-schedule / pipeline-run
  runners now honour a check-level `itemType` (default DataPipeline, so existing specs keep
  their meaning); before this, a schedule on a NOTEBOOK was unassertable — the check failed
  reporting a missing pipeline against a correctly configured schedule. Ships with its own
  offline self-test fixtures (engine self-test 705 → 708).
- Ported verbatim: `.claude/skills/fabric-lint/refuted_claims.py` — the REFUTED registry, 8
  entries with embedded self-test fixtures (was empty here). The scanner engine was already
  identical; the gym scan stays clean across 333 files with the entries live. Four entries
  point at wiki pages this repo ships; the other four name pages that arrive as those topics
  mature (the pointer is informational — the scanner never reads it from disk).
- Softened: `3_wiki/lakehouse/operate-framework.md` line 34 asserted `Deduped` fires when
  "Fabric matched an in-flight scheduled run" — production evidence (four concurrent identical
  manual submissions, four real sessions, none Deduped) downgraded the trigger semantics to
  unverified. The line now says treat-as-no-op / trigger-unverified, consistent with the
  page's own line 42 and the shipped notebook_client docstring. Seed-page exemption applies.
- Explicitly NOT transferred, with reasons: their matured wiki pages (cicd/operate-framework's
  new git-sync-drift section and the pipelines/ingestion/testing pages) cite tenant build
  reports this repo does not carry — content transfer is an ingest decision needing sanitised
  evidence in 2_raw first; 0_admin/logs (tenant-local run ledgers, never transferred);
  0_admin/capabilities (human-owned — no change needed: the LH-C14 row matches frontend's).

## [2026-08-30] lint | correction: the REFUTED registry port is withdrawn
- The previous entry ported fabric-frontend-kb's 8 REFUTED entries into
  `.claude/skills/fabric-lint/refuted_claims.py`. That was wrong, and the file's own
  docstring says why: "The registry starts empty. Add an entry when an ingest corrects a
  claim the gym still teaches." The entries are tenant knowledge, not framework — the same
  fresh-clone principle as the tracker reading 0 and the empty atoms/ folders — and the
  release manifest ships `.claude/skills/fabric-lint/` whole into every bundle, so ported
  entries would have propagated to every member's build. Registry restored to empty; the
  scanner engine (which IS framework, and identical upstream) is untouched.
- Kept from the previous entry, unaffected by this correction: the `test_pipeline.py`
  itemType fix (framework code) and the lakehouse operate-framework `Deduped` softening
  (removal of an unverified assertion, not an addition of knowledge).

## [2026-09-17] ingest | AG-LAK-001 (Foundry) converts the lakehouse seed cluster
- All six `3_wiki/lakehouse/` cluster pages (`prerequisites-and-fit`, `design-framework`,
  `build-framework`, `operate-framework`, `coding-guidance`, `gotchas`) moved off the seed
  exemption: `sources:` now cites the rep report, `provenance_notes` dropped, `evidence:
  unverified` → `mixed` (each page still carries claims this rep did not touch). Inline
  **Confirmed**/**Proven** tags added at the specific claims this rep exercised.
- What proved it: `2_raw/gym-rep-reports/lakehouse/2026-09-16-AG-LAK-001-foundry.md` — 10/10
  validation checks passed, no triage, first attempt.
- New: `3_wiki/lakehouse/atoms/index.md` and four rep-proven execution cards — LH-C02
  (schema-qualified landing), LH-C07 (schema-enabled lakehouse + SQL endpoint), LH-C08
  (notebook item from cells), LH-C14 (submit/poll/verify-independently). LH-C01 stays
  argued-only on `design-framework.md`/`prerequisites-and-fit.md` (design-gate, no atom).
- Corrected: nothing — every claim the rep touched (LH-C01, LH-C02, LH-C07, LH-C08, LH-C10,
  LH-C14, LH-C27) matched the seed pages exactly as written. This is signal, not a null
  result: the docstring-migrated seed content held up against a real tenant on first contact.
- Left open: LH-C06 (path-binding choice under real portability pressure), LH-C12 (Environment
  item / `%pip` route), LH-C15 (SQL-endpoint metadata sync), LH-C18 (session sizing), LH-C23's
  non-notebook alternatives, and four-part `%%sql`-style addressing all remain untested —
  candidates for AG-LAK-002 and later lakehouse reps.

## [2026-09-17] lint | 23 issues found, 22 auto-fixed
- Deterministic checks run clean: `anchor_check.py` (0 dead anchors), `stale_task_claims.py`
  (7 gym prose files, no REFUTED/EMPTY-WIKI hits), `gym_run.py --self-test` (0 failures, no
  checked-in plan/manifest files), `capability_coverage.py --write --check` (lakehouse: 13/13
  documented, 0 broken capability-id references), `git ls-files | grep __pycache__|.pyc|.pyo`
  (no tracked build artefacts). `1_agent-gym/lakehouse/AG-LAK-001/task.md` ↔ `validate.json`:
  all 10 check ids appear in exactly one `Validate:` list, none orphaned either direction; no
  capability id or mechanism vocabulary leaked into the task bullets. Atom health: all 4
  lakehouse atoms (LH-C02/07/08/14) cite a valid spec id, cite the same `2_raw/` source as
  their `derived_from:` cluster pages, and stay well under the ~30-line body budget (21-25
  lines each). No atom debt — every `proven` capability (LH-C01 excepted, design-gate) has a
  card.
- **Auto-fixed (index consistency): 22 dead rows in `3_wiki/index.md` marked `[MISSING]`.**
  Every page row under `## azure-app`, `## capacity`, `## cicd`, `## data-agent`, `## graph`,
  `## key-vault`, `## ontology`, `## openmirror`, `## pipelines` points at a file that does not
  exist anywhere in this repo's git history (`git log --all --diff-filter=A --name-only --
  '3_wiki/*.md'` shows only the `lakehouse` cluster + `index.md` + `log.md` were ever
  committed). Rows left in place per convention, not deleted — maintainer's call.
- **Report only — not auto-fixed:** the same 9 sections' intro paragraphs link a "Toolkit"
  (`code/clients/*.py`, `code/builders/*.py`) and a "Spec" (`0_admin/capabilities/<topic>.md`)
  that are equally absent — `code/` ships only the lakehouse clients (`lakehouse_client.py`,
  `notebook_client.py`, `livy_client.py`, `workspace_folders.py`) plus core infra, and
  `0_admin/capabilities/` ships only `lakehouse.md`. `1_agent-gym/` has empty (`.gitkeep`-only)
  folders for `cicd`, `ingestion`, `key-vault`, `openmirror`, `pipelines`, `testing`; no
  `azure-app`, `data-agent`, `graph`, or `ontology` folder exists there at all. This directly
  contradicts this same file's own `## Instructions` section ("This index is mostly empty...
  `lakehouse` is filled in already, as a worked example") and CLAUDE.md's stated fresh-clone
  design ("the wiki holds one seed topic"). The 2026-08-20 `refactor` entry above claims "22
  new seed pages across [9 topics]" — that work is not present in this repository's single
  commit (`9f193ea`). **Withdrawing that claim rather than deleting it**: the entry stands as
  written above (append-only), but per that same entry's own count (2+2+3+2+2+3+3+3+2 = 22)
  it matches exactly the 22 rows just marked `[MISSING]` here — strong evidence the described
  work happened in a different environment/branch and never reached this repo's history.
- Left open, for the maintainer: decide per topic whether to (a) delete the phantom section
  from `index.md` and its files-that-never-existed claim from the 2026-08-20 log entry's
  practical effect, or (b) actually port the described seed content from wherever it was
  produced. Until then, treat every `[MISSING]` row as documentation debt, not knowledge.

## [2026-09-18] ingest | AG-LAK-002 (Ravensworth) — concurrency softened, LH-C27 atom promoted
- Updated: `lakehouse/prerequisites-and-fit.md`, `lakehouse/design-framework.md`,
  `lakehouse/build-framework.md`, `lakehouse/operate-framework.md`, `lakehouse/gotchas.md`,
  `lakehouse/atoms/LH-C08.md`, `lakehouse/atoms/LH-C14.md`, `lakehouse/atoms/index.md`.
- New: `lakehouse/atoms/LH-C27.md` — promoted from the rep's confirmed draft (establish the
  runtime from the session: `notebookutils.runtime.context`, `importlib.import_module` before
  assuming a library, a kept probe notebook writing to a table).
- What proved it: `2_raw/gym-rep-reports/lakehouse/2026-09-18-AG-LAK-002-ravensworth.md`.
- Corrected: `operate-framework.md` and `gotchas.md` stated concurrent identical on-demand
  submissions **collide** over the same Delta path as the default outcome — a different claim
  from the 2026-08-30 "Deduped-on-trigger" softening above, this one about on-demand-vs-
  on-demand collision. AG-LAK-002 ran two concurrent `mode("overwrite")` submissions of
  byte-identical output and both reached `Completed` with no conflict. Softened to
  conditional: non-dedup is still confirmed (two distinct job-instance ids every time),
  collision is possible but not guaranteed — depends on whether the concurrent writes actually
  conflict at the Delta commit. Old claim not deleted, both outcomes now stated with evidence.
- Registered `on-demand-concurrent-collision-not-guaranteed` in
  `.claude/skills/fabric-lint/refuted_claims.py` so the gym is watched for the old
  unconditional wording; ran `stale_task_claims.py --self-test` clean.
- Also re-proved with no deviation: LH-C08 (notebook-from-cells), LH-C14 (submit/poll/verify).
  LH-C23 (design-gate, no atom) got its first concrete evidence of the notebook+notebook+Livy
  split predicted in `design-framework.md` §5.
- Reported, not fixed (task-authoring, out of scope for this ingest):
  `1_agent-gym/lakehouse/AG-LAK-002/validate.json` line 45's `question` text says "the second
  submission is where Deduped is observed" — this run's own evidence (and the wiki, both
  before and after this ingest) says on-demand runs are never deduplicated; neither submission
  in this rep reached `Deduped`. The check itself only asserts `minRuns`/latest-status, so
  nothing mis-graded — the stale wording is in the human-readable `question` field only.
- Left open: LH-C06, LH-C12, LH-C15, LH-C18 still untested by any rep. No capability gap
  surfaced — all four capabilities AG-LAK-002 declared already existed in the spec.

## [2026-09-18] lint | 2 issues found, 0 auto-fixed
- Deterministic checks run clean: `anchor_check.py` (0 dead anchors across `3_wiki`),
  `gym_run.py --self-test` (0 failures, no checked-in plan/manifest), `git ls-files | grep
  __pycache__|.pyc|.pyo` (no tracked build artefacts), `capability_coverage.py --write
  --check` (lakehouse 13/13 documented, 0 broken capability-id references, `coverage.md`
  rewritten). `AG-LAK-002/task.md` ↔ `validate.json`: all 12 check ids appear in exactly one
  `Validate:` list, none orphaned either direction; no capability id or mechanism vocabulary
  leaked into the task bullets. Every internal `3_wiki` markdown link (fragment and plain)
  resolves except the 22 already-known phantom rows below. Provenance: every `sources:` entry
  across the lakehouse cluster and its 5 atoms resolves to a real `2_raw/` file
  (`2026-09-16-AG-LAK-001-foundry.md`, `2026-09-18-AG-LAK-002-ravensworth.md`). Atom health:
  all 5 atoms (LH-C02/07/08/14/27) cite a valid spec id and stay under the ~30-line budget
  (LH-C27 at 29 lines, worth watching on its next revision); no atom debt — LH-C01/LH-C23 are
  the only other `proven` capabilities and both are design-gate (no atom by design, per
  `atoms/index.md`'s own note). Community frontmatter present and correct
  (`frontier-data-engineer`) on all 6 cluster pages and all 5 atoms.
- **Not new — reconfirming two already-recorded issues, still outstanding:**
  1. `stale_task_claims.py` still fails (exit 1): `1_agent-gym/lakehouse/AG-LAK-002/validate.json:45`'s
     `question` text ("the second submission is where Deduped is observed") repeats the claim
     the wiki refuted this same day. This is the exact issue the 2026-09-18 ingest entry above
     already reported as "task-authoring, out of scope for this ingest" and registered in
     `refuted_claims.py` (`on-demand-concurrent-collision-not-guaranteed`) — the registry is
     doing its job by continuing to flag it. Still needs a maintainer edit to
     `validate.json`'s prose (the check logic itself is unaffected: it only asserts
     `minRuns`/latest-status).
  2. The 22 `[MISSING]` wiki-page rows plus their sections' dangling Toolkit/Spec/Practice
     links across `azure-app`, `capacity`, `cicd`, `data-agent`, `graph`, `key-vault`,
     `ontology`, `openmirror`, `pipelines` — first reported 2026-09-17, unchanged since (no
     commits touched those sections; confirmed via `git diff HEAD -- 3_wiki/index.md`, which
     shows only the `lakehouse` section changed this session). Still documentation debt, not
     a regression — see that entry for the full breakdown and the maintainer decision it's
     waiting on.
- Left open: same two items above, both awaiting the maintainer (not an unattended fix).

## [2026-09-18] refactor | maintainer resolved the phantom index sections — deleted
- Maintainer decision on the 2026-09-17 open item: delete, not port. Removed the `## azure-app`,
  `## capacity`, `## cicd`, `## data-agent`, `## graph`, `## key-vault`, `## ontology`,
  `## openmirror`, `## pipelines` sections from `3_wiki/index.md` — all 22 rows and their
  Toolkit/Spec/Practice intro links pointed at files with no commit in this repo's history
  (confirmed 2026-09-17 via `git log --all --diff-filter=A --name-only -- '3_wiki/*.md'`).
  `decisions` and `lakehouse` are the only sections left; both are real.
- Rewrote `## your other topics` to name what's actually on disk: `1_agent-gym/` folders exist
  for `cicd` · `ingestion` · `key-vault` · `openmirror` · `pipelines`; `azure-app` · `capacity` ·
  `data-agent` · `graph` · `ontology` · `testing` are declared in `CLAUDE.md`'s topic table but
  have no gym folder, spec, or code toolkit here yet. (Previous wording listed `capacity` and
  `testing` alongside the real folders — neither has a `1_agent-gym/` folder either; corrected.)
- Effect on the 2026-08-20 `refactor` entry above: its "22 new seed pages across 9 topics" claim
  was already withdrawn in the 2026-09-17 entry as never-landed-here; this entry is the
  practical cleanup that withdrawal was waiting on. Not re-withdrawing again — same fact,
  now acted on.
- What proved it: no new evidence: this is a structural cleanup of the index against the
  2026-09-17/2026-09-18 lint findings, not a knowledge change. No `2_raw/` citation applies.
- Left open: if/when any of these 9 topics gets a real spec (`0_admin/capabilities/<topic>.md`,
  human-owned) and a `1_agent-gym/<topic>/` task, re-add its section here per the `lakehouse`
  shape — do not restore the deleted rows verbatim, they described pages that were never
  written.

## [2026-09-18] lint | 1 issue found, 0 auto-fixed
- Re-ran the full deterministic suite after the phantom-section deletion above to confirm the
  fix and re-check nothing else moved: `anchor_check.py` (0 dead anchors), `git ls-files | grep
  __pycache__|.pyc|.pyo` (no tracked build artefacts), `capability_coverage.py --write --check`
  (lakehouse 13/13 documented, exit 0), `gym_run.py --self-test` (0 failures). **Every internal
  `3_wiki` markdown link now resolves** — re-ran the same link scan from the prior lint run,
  which previously listed 55 dangling links across the 9 deleted sections; zero remain.
  Index-to-disk reconciliation: all 11 files under `3_wiki/lakehouse/` (6 cluster pages + 5
  atoms) have a row or index entry; no orphans, no dangling `[MISSING]` rows left anywhere.
- **Not new — same outstanding issue:** `stale_task_claims.py` still fails on
  `1_agent-gym/lakehouse/AG-LAK-002/validate.json:45` (the `Deduped`-on-second-run claim). Fix
  instructions were handed to the maintainer this same session; not applied yet as of this run.
- Left open: the `validate.json:45` edit, per the instructions already on record.
