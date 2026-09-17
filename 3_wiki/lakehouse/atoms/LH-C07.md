---
type: atom
topic: lakehouse
community: frontier-data-engineer
capability: LH-C07
title: Create a schema-enabled lakehouse and wait for its SQL endpoint
requires:
  env: [fabric-auth, capacity]
  atoms: []
updated: 2026-09-16
fabric_release: 2026-09
status: current
evidence: proven
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

**Proven** (AG-LAK-001, 2026-09-16): `AG_LAK_001_Foundry` created and endpoint reached
`Success` inside the single `lakehouse` step (28.8s total, endpoint wait included) — no
separate poll loop needed at this scale. `lakehouse-provisioned` passed first run:
schema-enabled true, `defaultSchema=dbo`, endpoint `Success`, folder `Lakehouse`.
