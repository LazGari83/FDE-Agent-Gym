---
type: atom
topic: lakehouse
community: frontier-data-engineer
capability: LH-C02
title: Land a table in a named schema, never the default
requires:
  env: [fabric-auth, capacity, spark-capacity]
  atoms: []
updated: 2026-09-16
fabric_release: 2026-09
status: current
evidence: proven
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

**Proven** (AG-LAK-001, 2026-09-16): `table-in-retail-schema` and `table-not-in-dbo` both
passed first run; the `.save()`-not-`saveAsTable()` rule held exactly as written.
