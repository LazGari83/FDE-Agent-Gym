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
