---
type: gym-rep-report
task_id: AG-LAK-002
task: AG-LAK-002
topic: lakehouse
date: 2026-09-18
fabric_release: 2026-09
status: passed
validation: "12/12 checks passed (lakehouse-provisioned, load-notebook-contract, probe-notebook-contract, load-notebook-ran-green, probe-notebook-ran-green, calibration-run-typed, run-not-in-dbo, q1-all-runs-loaded, q2-summary-from-livy, q3-surfaces-agree, q4-runtime-facts-recorded, q5-facts-have-values)"
workspace_context:
  workspace_id: 3897a34d-a240-4af8-a23e-86a5eeec7f02
  folder_id: b9850681-d03f-4ee0-8f33-35c19d3ef3c7
  lakehouse_id: dafcaa90-2eaa-4f2a-880a-b2279c02d3d7
  load_notebook_id: 52054d82-8488-4252-b82c-2599a3ece812
  probe_notebook_id: 8a04a179-e7f5-4a28-9a62-8c0b8972b111
---

# Gym rep report: Ravensworth (AG-LAK-002)

## What it was

A schema-enabled lakehouse load with two live capability tests beyond AG-LAK-001: concurrent
on-demand notebook submission, and a second, genuinely different execution surface (Livy)
producing a downstream summary the notebook is explicitly forbidden from computing. Task:
`1_agent-gym/lakehouse/AG-LAK-002/task.md`.

## How I did it

- Classified the four declared capabilities: LH-C08/LH-C14 already had atoms (reused as
  written); LH-C23 is a design-gate capability (argued below, never drafted); LH-C27 had no
  atom but had proven cluster material in `prerequisites-and-fit.md` — drafted as an atom
  (below), confirmed by this run.
- **Design gate (LH-C23):** load = Jobs-API notebook item (rebuildable `notebook-content.py`,
  real transformation — explicit typing + schema qualification, file-drop source); probe =
  Jobs-API notebook item because the contract requires a *named, kept* artifact, not a
  throwaway; summary = Livy, per `design-framework.md` §5 verbatim ("Probe with Livy, ship
  with a notebook item. A task that asks for a second, genuinely different surface is asking
  you to separate these two."). No ambiguity against existing guidance, so built without a
  pause.
- Ran `provision.py`, then an engine plan (`gym_run.py`) for the mechanical half: workspace
  folder, schema-enabled lakehouse, file upload, both notebook items (built via
  `notebook_content_py()`, linted offline with `--lint-source` first — one hit, fixed before
  upload: see *What broke*), and one `run-notebook` step (ran the probe, the last notebook
  created) — invoked with `--skip validate` since the two remaining task legs need
  hand-orchestration the engine's step types don't offer.
- Hand-orchestrated task 4: `NotebookClient.run()` (fire-and-forget) twice back to back —
  second submission issued before the first was polled at all — then `wait_for_job()` on
  each independently, recording exactly what came back (see *How it works*).
- Hand-orchestrated task 5: one `LivyClient` session, a `CREATE OR REPLACE TABLE ... USING
  DELTA AS SELECT` against `lab.calibration_run` (Livy sessions in this toolkit attach to the
  lakehouse's catalog, so the unqualified `lab.calibration_run` name resolved without an
  explicit path) — a surface the load notebook's contract (`sourceLacks: ["calibration_summary"]`)
  is asserted never to touch.
- `gym_validate.py AG-LAK-002` (live, direct — no engine `validate` step, consistent with the
  hand-orchestrated leg) — 12/12 on the first full run, no triage needed.

## How it works

**Concurrent on-demand submission did not collide this time — record the actual outcome, not
the worst case.** `operate-framework.md`'s "Concurrency" section (hedged: "may lose the
race") is not contradicted by this run, but it is worth recording precisely because the
_specific_ outcome differs from AG-LAK-001's era of guidance emphasis on collision: both
`run()` calls returned distinct job-instance ids immediately (fire-and-forget, no wait), and
both independent `wait_for_job()` polls terminated `Completed` — no `Cancelled`, no
`System_Cancelled_Session_Statements_Failed`, no Delta `ConcurrentAppendException`. Both
notebooks wrote the *same* `mode("overwrite")` Delta commit (identical source data), so even
if the two Spark sessions' writes were not perfectly simultaneous at the commit point,
overwriting with byte-identical output leaves nothing for a conflict detector to disagree
about — which is a plausible reason a genuinely different (non-idempotent-looking) concurrent
write onto the *same* path could still collide where this one did not. On-demand submissions
are confirmed, again, to be genuinely separate real runs (two distinct job-instance ids, two
real Spark sessions) — not deduplicated — which is what actually mattered for the
`load-notebook-ran-green` contract (`minRuns: 2`, latest `Completed`). No corrective
third run was needed; the orchestration script carried a fallback (submit one more,
sequential, run if the latest instance wasn't green) that never fired here — worth keeping in
any atom this promotes to, since a future rep's timing may differ.

**`notebookutils.runtime.context` is a plain subscriptable mapping populated at run time**,
confirmed again from a job whose submission passed `default_lakehouse` — `ctx["defaultLakehouseWorkspaceId"]`,
`ctx["defaultLakehouseId"]`, and `ctx["defaultLakehouseName"]` all resolved cleanly in both the
load and the probe notebook, with no separate lookup call. This is what let both notebooks
build their absolute ABFS root (`abfss://{workspaceId}@onelake.dfs.fabric.microsoft.com/{lakehouseId}`)
from the running session rather than a value baked in at authoring time — portable across a
redeploy to a different workspace/lakehouse pair with zero source changes.

**Draft atom — LH-C27 (confirmed by this run):**

```markdown
---
type: atom
topic: lakehouse
community: frontier-data-engineer
capability: LH-C27
title: Establish the runtime from the session; never assume a version
requires:
  env: [fabric-auth, spark-capacity]
  atoms: []
updated: 2026-09-18
fabric_release: 2026-09
status: current
evidence: proven
sources:
  - 2_raw/gym-rep-reports/lakehouse/2026-09-18-AG-LAK-002-ravensworth.md
derived_from: [prerequisites-and-fit]
---

# LH-C27 — Establish the runtime from the session; never assume a version

**When:** a task needs facts about the runtime (language/engine versions, an importable
library, the attached lakehouse) recorded rather than assumed.

```python
import importlib, platform
try:
    mod = importlib.import_module("jsonschema")     # never `pip install` first — check, then decide
    fact = f"available {getattr(mod, '__version__', 'unknown-version')}"
except ImportError as exc:
    fact = f"not importable: {exc}"
facts = [("python_version", platform.python_version()),
         ("spark_version", spark.version),
         ("runtime_jsonschema", fact),
         ("default_lakehouse", str(notebookutils.runtime.context["defaultLakehouseName"]))]
spark.createDataFrame(facts, ["fact_name", "fact_value"]).write.format("delta") \\
     .mode("overwrite").save(RUNTIME_FACTS_TABLE)
```

Land the answer in a **kept** notebook item that writes to a table — not a throwaway probe and
not a chat message. `importlib.import_module` over the candidate name costs nothing and never
risks a needless install or an unwanted downgrade.

**Proven** (AG-LAK-002, 2026-09-18): `python_version=3.11.8`, `spark_version=3.5.5.5.4.20260807.1`,
`runtime_jsonschema=available 4.19.2` (importable with **no install attempted**),
`default_lakehouse=AG_LAK_002_Ravensworth` — all four read back non-null from
`lab.runtime_facts` via an independent Livy session (`q4-runtime-facts-recorded`,
`q5-facts-have-values`).
```

## Code

The two payload-authoring and hand-orchestration scripts lived in the session scratchpad, not
in `code/` (nothing here is a reusable client addition — both compose existing `code/` clients
verbatim): a `notebook_content_py()`-based builder for the load and probe notebook sources, and
a hand-orchestration script for the concurrent submission (`NotebookClient.run()` x2 +
independent `wait_for_job()`) and the Livy `CREATE OR REPLACE TABLE ... AS SELECT` summary.

## What broke

- **Lint caught a self-inflicted false failure before any upload.** The load notebook's source
  comment explaining *why* `.save()` is used instead of `saveAsTable` contained the literal
  substring `saveAsTable` — `--lint-source` correctly failed `sourceLacks: ["saveAsTable"]` on
  a comment, not on actual catalog-addressed code. Fixed by rephrasing the comment without the
  banned token. This is a lint mechanic to note, not a wiki correction: `sourceLacks` is a
  plain substring scan with no comment-awareness, so any prose explaining a forbidden API by
  name will trip it — write around the literal string, not just the code.
- Everything else landed green on the first live run: no triage needed.

### Confirmed as documented

- LH-C08 (notebook-from-cells) and LH-C14 (submit/poll/verify-independently) held exactly as
  atomed — no deviation.
- `notebookutils.runtime.context` populated `defaultLakehouseWorkspaceId`/`defaultLakehouseId`/
  `defaultLakehouseName` cleanly (re-confirms the AG-LAK-001 note in
  `prerequisites-and-fit.md`), now from two separate notebooks in the same rep.
- On-demand runs are not deduplicated (`operate-framework.md`, "Concurrency") — two distinct
  job-instance ids for two identical concurrent submissions, confirmed again.

### Not exercised

None — every declared capability's checks ran ungated; no `requiresEnv` legs in this spec.

## Provenance

- Task: `1_agent-gym/lakehouse/AG-LAK-002/task.md`
- Validation: `python .claude/skills/execute-gym-rep/gym_validate.py AG-LAK-002` → exit 0 on 2026-09-18
