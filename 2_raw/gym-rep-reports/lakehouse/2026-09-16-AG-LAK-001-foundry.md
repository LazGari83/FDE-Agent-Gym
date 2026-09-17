---
type: gym-rep-report
task_id: AG-LAK-001
task: AG-LAK-001
topic: lakehouse
date: 2026-09-16
fabric_release: 2026-09
status: passed
validation: "10/10 checks passed"
workspace_context:
  workspace_id: "3897a34d-a240-4af8-a23e-86a5eeec7f02"
  folder_id: "b9850681-d03f-4ee0-8f33-35c19d3ef3c7"
  lakehouse_id: "2dc052de-9644-423c-af80-5c034501d774"
  lakehouse_name: "AG_LAK_001_Foundry"
  lakehouse_workspace_id: "3897a34d-a240-4af8-a23e-86a5eeec7f02"
  sql_endpoint_id: "56f4e9e4-74a6-40a8-88ad-a59addfdfdcb"
  notebook_id: "dd4cf64b-7bc9-4379-901e-a4d8a1348cf9"
  notebook_name: "AG-LAK-001-Foundry"
  notebook_workspace_id: "3897a34d-a240-4af8-a23e-86a5eeec7f02"
  job_instances: ["ec601588-8f8c-42d7-b725-a901b1da3f9d"]
---

# Gym rep report: Foundry (AG-LAK-001)

## What it was

The topic's starter task: stand up a schema-enabled lakehouse and a notebook item that
reads an 8-row retail CSV and lands it as `retail.product`, typed and qualified so an
unqualified `dbo` landing is provably absent. Task: `1_agent-gym/lakehouse/AG-LAK-001/task.md`.

## How I did it

- `3_wiki/lakehouse/atoms/` was empty (fresh clone) — every capability the task exercises
  (LH-C01, LH-C02, LH-C07, LH-C08, LH-C14) was a gap; classification was reading the six
  lakehouse cluster pages before the first tenant call, not pulling atoms.
- Ran `provision.py` locally first — 8 rows, 3 categories, `SUM(unit_price)=186.84`,
  `SUM(in_stock)=412` — these became the expected values for the acceptance checks.
- `preflight_probe.py --task AG-LAK-001` passed all 3 baseline checks (auth, capacity,
  spark-capacity) before anything was designed.
- Design gate (LH-C01, argued, no atom): a notebook is the right tool here — the artefact
  must be rebuildable (a git-tracked `notebook-content.py`), there is a real transformation
  (explicit typing + schema qualification, not a byte copy), and the source is a file drop.
  Matches `design-framework.md`'s "choose a notebook when" table exactly.
- Built the payload with the repo's own `notebook_content_py()` helper (never hand-typed the
  delimiter grammar) — one markdown cell, one code cell, no parameters cell (the task takes
  none).
- Wrote to OneLake via absolute ABFS path (`.save(TABLE)`, not `saveAsTable`) per
  `coding-guidance.md`'s explicit warning that `saveAsTable` resolves against the attached
  default lakehouse's catalog and would risk `dbo`.
- Linted the payload offline (`gym_validate.py --lint-source`) before any upload — 0 s cost,
  caught nothing wrong, confirmed `sourceContains: ["retail.product"]` /
  `sourceLacks: ["!pip"]` before spending the first Spark session.
- Ran the plan through `gym_run.py` (lakehouse-family steps: `provision` → `workspace-folder`
  → `lakehouse` → `upload-files` → `notebook` → `run-notebook` → `validate`). Plan telemetry:

| Step | Category | Seconds | Note |
|---|---|---|---|
| provision fixture (provision.py) | local | 0.0s | provision.py exit 0 |
| ensure workspace folder 'Lakehouse' | fabric | 2.5s | 'Lakehouse' -> b9850681-d03f-4ee0-8f33-35c19d3ef3c7 |
| ensure lakehouse 'AG_LAK_001_Foundry' | fabric | 28.8s | created, endpoint Success — 2dc052de-9644-423c-af80-5c034501d774 |
| upload 1 file(s) (OneLake DFS) | local | 2.2s | 1 file(s) -> Files/ |
| ensure notebook 'AG-LAK-001-Foundry' | fabric | 22.8s | created — dd4cf64b-7bc9-4379-901e-a4d8a1348cf9 |
| run notebook x1 (Jobs API, poll to terminal) | fabric | 61.1s | 1 run(s): Completed (expected ['Completed']) |
| validate (gym_validate.py) | fabric | 71.2s | 10/10 checks passed |

Total 188.5s (fabric 186.3s, local 2.2s) — one pass, no triage needed.

### The plan that passed (agent-proposed)

```json
{
  "task": "AG-LAK-001",
  "steps": [
    {"type": "provision", "script": "provision.py"},
    {"type": "workspace-folder", "name": "Lakehouse"},
    {"type": "lakehouse", "name": "AG_LAK_001_Foundry"},
    {"type": "upload-files", "files": [
      {"local": "source-data/product.csv", "dest": "Files/raw/product.csv"}
    ]},
    {"type": "notebook", "name": "AG-LAK-001-Foundry"},
    {"type": "run-notebook"},
    {"type": "validate"}
  ]
}
```

## How it works

Every draft atom below confirmed the seed cluster pages exactly as written — this is a
clean run, and the value is in what it proves rather than what it corrects. `3_wiki/lakehouse/`
had no atoms yet, so drafting these five cards (LH-C01 argued, four drafted) *is* the research
this rep produced.

**LH-C01 — design gate, argued, not drafted.** `design-framework.md` states this is judgment,
never a mechanism. Argument used: rebuildable artefact + real transformation (typed columns,
schema qualification) + file-drop source → notebook is correct. Confirmed by the run: no
alternative surface (pipeline copy, shortcut, Livy-only) would have produced a git-tracked,
redeployable definition that also enforces the `retail` vs `dbo` contract.

### Draft atom — LH-C02: address tables by schema and four-part name

```markdown
---
type: atom
topic: lakehouse
community: frontier-data-engineer
capability: LH-C02
title: Land a table in a named schema, never the default
requires:
  env: []
  atoms: []
updated: 2026-09-16
fabric_release: 2026-09
status: current
evidence: draft
sources:
  - 2_raw/gym-rep-reports/lakehouse/2026-09-16-AG-LAK-001-foundry.md
derived_from: [design-framework, coding-guidance]
---

# LH-C02 — Land a table in a named schema, never the default

**When:** the contract names a schema other than `dbo` on a schema-enabled lakehouse.

Write by absolute ABFS path, not `saveAsTable`:

```python
ROOT = f"abfss://{workspace_id}@onelake.dfs.fabric.microsoft.com/{lakehouse_id}"
TABLE = f"{ROOT}/Tables/retail/product"
df.write.format("delta").mode("overwrite").save(TABLE)   # never saveAsTable("product")
```

`Tables/{schema}/{table}` auto-registers on a schema-enabled lakehouse — no `CREATE SCHEMA`,
no attachment needed for the write. The directory *is* the schema. Verify with **two**
assertions, not one: table present in the named schema, AND absent from `dbo` — a naive
existence check alone cannot tell the two apart.
```

**Verdict: confirmed as written.** `table-in-retail-schema` and `table-not-in-dbo` both
passed on the first run; the `.save()`-not-`saveAsTable()` rule held exactly as the
cluster page states.

### Draft atom — LH-C07: provision a schema-enabled lakehouse

```markdown
---
type: atom
topic: lakehouse
community: frontier-data-engineer
capability: LH-C07
title: Create a schema-enabled lakehouse and wait for its SQL endpoint
requires:
  env: []
  atoms: []
updated: 2026-09-16
fabric_release: 2026-09
status: current
evidence: draft
sources:
  - 2_raw/gym-rep-reports/lakehouse/2026-09-16-AG-LAK-001-foundry.md
derived_from: [build-framework]
---

# LH-C07 — Create a schema-enabled lakehouse and wait for its SQL endpoint

**When:** a task names a schema-enabled lakehouse with a default schema and a working SQL
endpoint.

```python
assert valid_display_name(name)                       # ^\w+$ only — hyphens 400
lh = LakehouseClient(ws)
item = lh.create(name, folder_id=folder_id, enable_schemas=True)
props = lh.wait_for_sql_endpoint(item["id"])           # async — poll, never sleep
assert props.get("defaultSchema") == "dbo"             # the flag sent is never echoed back
```

Create response carries no `properties` — the client's follow-up GET supplies them. Endpoint
`provisioningStatus` terminal values: `Success` / `Failed`.
```

**Verdict: confirmed as written.** `AG_LAK_001_Foundry` created and endpoint reached
`Success` inside the single `lakehouse` step (28.8 s total, endpoint wait included) — no
separate poll loop was needed at this scale. `lakehouse-provisioned` passed on the first
run: schema-enabled true, `defaultSchema=dbo`, endpoint `Success`, folder `Lakehouse`.

### Draft atom — LH-C08: produce a runnable notebook item

```markdown
---
type: atom
topic: lakehouse
community: frontier-data-engineer
capability: LH-C08
title: Build a notebook item from cells, never a bare script
requires:
  env: []
  atoms: []
updated: 2026-09-16
fabric_release: 2026-09
status: current
evidence: draft
sources:
  - 2_raw/gym-rep-reports/lakehouse/2026-09-16-AG-LAK-001-foundry.md
derived_from: [build-framework, coding-guidance]
---

# LH-C08 — Build a notebook item from cells, never a bare script

**When:** a task needs a re-runnable notebook item created or replaced over REST.

```python
py = notebook_content_py([
    ("markdown", "Title"),
    "CONFIG = ...",           # plain string = code cell
], parameters_cell=None)      # only set when the task actually parameterizes the run
nb = NotebookClient(ws).create(name, py_source=py, folder_id=folder_id)
src = NotebookClient(ws).get_source(nb["id"])   # assert on THIS, never on create's status
```

A bare `.py` script is rejected outright (`PyToIPynbFailure`); `notebook_content_py()` is the
only correct way to build the payload. The prologue is the only thing the service validates —
a payload with the prologue but no `# CELL` blocks creates *successfully and empty*.
```

**Verdict: confirmed as written.** `AG-LAK-001-Foundry` created in one call from
`notebook_content_py()` output; `notebook-exists` passed against the live `get_source()`
read (`sourceContains: ["retail.product"]`, `sourceLacks: ["!pip"]`) — the offline
`--lint-source` result and the live check agreed exactly, so linting before upload is a
reliable predictor here, not just a speed trick.

### Draft atom — LH-C14: drive a run to a verified terminal state

```markdown
---
type: atom
topic: lakehouse
community: frontier-data-engineer
capability: LH-C14
title: Submit, poll to terminal, then verify from an independent session
requires:
  env: []
  atoms: []
updated: 2026-09-16
fabric_release: 2026-09
status: current
evidence: draft
sources:
  - 2_raw/gym-rep-reports/lakehouse/2026-09-16-AG-LAK-001-foundry.md
derived_from: [build-framework, operate-framework]
---

# LH-C14 — Submit, poll to terminal, then verify from an independent session

**When:** any notebook run whose result the task actually grades.

```python
instance = NotebookClient(ws).run_and_wait(nb_id, default_lakehouse={
    "name": lh_name, "id": lh_id, "workspaceId": ws})
assert instance["status"] == "Completed"
```

`Completed` is a process exit, not a data assertion — re-read the table from a session that
did not write it (a fresh Livy session, in this rep) and check the values the contract
names: row count, distinct keys, a known row, and absence from `dbo`.
```

**Verdict: confirmed as written.** The single run reached `Completed` in 61.1 s; the
validator's independent Livy session then re-read `retail.product` and matched all five
`spark-sql` checks (row count 8, category count 3, stock total 412, price total 186.84,
known row `FS-1003`/`Tools`) — the "don't trust the process exit" discipline is what made
the CRLF/dtype checks in `q3-stock-total` meaningful, even though this run had no CRLF
issue to catch.

## Code

Notebook body (novel, not from `code/` — authored from `coding-guidance.md`'s absolute-ABFS
and explicit-schema patterns):

```python
from pyspark.sql.types import StructType, StructField, StringType, DoubleType, IntegerType

ctx = notebookutils.runtime.context
workspace_id = ctx["defaultLakehouseWorkspaceId"]
lakehouse_id = ctx["defaultLakehouseId"]
ROOT = f"abfss://{workspace_id}@onelake.dfs.fabric.microsoft.com/{lakehouse_id}"

schema = StructType([
    StructField("sku", StringType(), False),
    StructField("name", StringType(), False),
    StructField("category", StringType(), False),
    StructField("unit_price", DoubleType(), False),
    StructField("in_stock", IntegerType(), False),
])

df = (spark.read.format("csv")
      .option("header", True)
      .schema(schema)
      .load(f"{ROOT}/Files/raw/product.csv"))

TABLE = f"{ROOT}/Tables/retail/product"
df.write.format("delta").mode("overwrite").save(TABLE)

verify = spark.sql("SELECT COUNT(*) AS n FROM retail.product").collect()[0]["n"]
print(f"retail.product row count: {verify}")
```

`notebookutils.runtime.context` supplied `defaultLakehouseWorkspaceId` /
`defaultLakehouseId` cleanly because `run-notebook` attached the lakehouse at submission
(`attachLakehouse: true` default) — confirms `prerequisites-and-fit.md`'s description of
that context dict.

## What broke

Nothing — the plan ran green on the first attempt, 10/10 checks, no triage.

### Confirmed as documented

- `saveAsTable`-vs-`.save()` schema-landing rule (`gotchas.md`, "Writing tables").
- `Tables/{schema}/{table}` auto-registration on a schema-enabled lakehouse, no `CREATE
  SCHEMA` needed (`coding-guidance.md`).
- The create-response-carries-no-`properties` / follow-up-GET pattern for the lakehouse item
  (`gotchas.md`, "Item creation").
- The prologue-only-validated notebook-creation risk was avoided, not hit, by using
  `notebook_content_py()` throughout — no hand-typed grammar was ever at risk.
- Offline `--lint-source` result matched the live `notebook-exists` check exactly.
- The default schema is confirmed as `dbo` (`lakehouse-provisioned` check), matching
  `design-framework.md` §1.

### Not exercised

- LH-C15 (SQL-endpoint metadata sync) — verification went through Spark/Livy, never T-SQL,
  so the per-write staleness trap was never triggered or observed here.
- LH-C12 (Environment item / `%pip` route) — the task needed no third-party package; the
  runtime's built-in libraries covered everything.
- Run concurrency / collision behaviour (`gotchas.md`, "Running") — only one run was
  submitted; no concurrent-submission collision was exercised.
- Four-part display-name addressing in a `%%sql`-style cell — verification used
  `spark.sql("... FROM retail.product")` against the attached catalog, not the explicit
  ``` `workspace`.lakehouse.schema.table ``` form `coding-guidance.md` documents; that path
  remains a hypothesis until a task forces cross-lakehouse addressing.

## Provenance

- Task: `1_agent-gym/lakehouse/AG-LAK-001/task.md`
- Validation: `python .claude/skills/execute-gym-rep/gym_validate.py AG-LAK-001` -> exit 0 on 2026-09-16
- Plan: `python .claude/skills/execute-gym-rep/gym_run.py AG-LAK-001`
