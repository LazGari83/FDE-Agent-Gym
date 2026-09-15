---
type: capability
title: Build framework
category: build-framework
topic: lakehouse
community: frontier-data-engineer
capabilities: [LH-C07, LH-C08, LH-C14]
updated: 2026-08-20
fabric_release: 2026-08
status: current
evidence: unverified
sources: []
provenance_notes: "**Seed page** — see [prerequisites-and-fit](prerequisites-and-fit.md) for why these six pages cite nothing, and what converts them."
---

# Lakehouse — build framework

The ordered build. Each phase has an assertion; a phase that cannot be asserted has not happened.

## P1 — Workspace folder

Create or resolve the target folder first, so every item created after it carries `folder_id` and nothing lands at the workspace root.

## P2 — Lakehouse item, schema-enabled (LH-C07)

```python
from lakehouse_client import LakehouseClient, valid_display_name
assert valid_display_name(name)                       # lakehouse names: ^\w+$ only
lh = LakehouseClient(ws)
item = lh.create(name, folder_id=folder_id, enable_schemas=True)   # 201; re-GETs for properties
```

- **Hyphens and spaces → `400 InvalidInput`** ("DisplayName is Invalid for ArtifactType"). The rule is **per item type** — a notebook beside it accepts hyphens.
- **The create response carries no `properties`.** Paths and the endpoint block need the follow-up GET; `create()` does it for you.
- **Assert `properties.defaultSchema`** to confirm you got the schema-enabled variant.

## P3 — SQL endpoint

```python
props = lh.wait_for_sql_endpoint(item["id"])          # async after create; poll, never sleep
```

Endpoint provisioning is asynchronous and completes after the create returns. Only the **T-SQL** surface waits on it — Spark and OneLake writes do not, so do not block a Spark-only build behind it.

- Terminal `provisioningStatus` values: `Success` · `Failed`. A timeout is a real finding, not something to swallow — every downstream T-SQL read fails with an error that never mentions provisioning.
- This wait happens **once**. Metadata catch-up after each Spark write is a different call — [operate-framework](operate-framework.md) ("SQL endpoint metadata sync").

## P4 — Notebook item (LH-C08)

```python
from notebook_client import NotebookClient, notebook_content_py
py = notebook_content_py([
    ("markdown", "Title"),
    "CONFIG = ...",                       # plain string = code cell
    "df = spark.read...",
], parameters_cell=1)                     # tags cell 1's delimiter as PARAMETERS CELL
nb = NotebookClient(ws).create(name, py_source=py, folder_id=folder_id)   # 202 LRO (long-running operation: accepted, not finished)
```

- **A bare `.py` script is not a notebook payload** — `notebook_content_py()` emits the `fabricGitSource` delimited grammar. The `py_source=` parameter reads as "Python source" and is not: it is `notebook-content.py` format.
- **Assert with `get_source()`, never on the create's status.** The prologue is the only thing validated, so a payload that creates successfully can still be empty — see [gotchas](gotchas.md).
- **No insert-a-cell or run-one-cell API.** An edit is a whole-file rebuild plus `update_definition()`.

## P5 — Run it

Submit over the Jobs API and poll to a terminal state — [operate-framework](operate-framework.md) covers what the run does and does not tell you.

## P6 — Verify from an independent session

`Completed` is a process exit, not a data assertion. Re-read the table from a session that did not write it, and check the values the contract names — row count, distinct keys, a known row, and **absence from `dbo`** where a named schema was required.
