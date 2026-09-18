---
type: atom
topic: lakehouse
community: frontier-data-engineer
capability: LH-C14
title: Submit, poll to terminal, then verify from an independent session
requires:
  env: [fabric-auth, spark-capacity]
  atoms: []
updated: 2026-09-18
fabric_release: 2026-09
status: current
evidence: proven
sources:
  - 2_raw/gym-rep-reports/lakehouse/2026-09-16-AG-LAK-001-foundry.md
  - 2_raw/gym-rep-reports/lakehouse/2026-09-18-AG-LAK-002-ravensworth.md
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
did not write it (a fresh Livy session, in this rep) and check the values the contract names:
row count, distinct keys, a known row, and absence from `dbo`.

**Proven** (AG-LAK-001, 2026-09-16): the single run reached `Completed` in 61.1s; the
validator's independent Livy session then re-read `retail.product` and matched all five
`spark-sql` checks (row count 8, category count 3, stock total 412, price total 186.84, known
row `FS-1003`/`Tools`).

**Re-proven, concurrency case** (AG-LAK-002, 2026-09-18): `run()` (fire-and-forget) called
twice back to back — second submission issued before the first was even polled — returned two
distinct job-instance ids; both independent `wait_for_job()` polls reached `Completed`, no
collision. See [operate-framework](../operate-framework.md) ("Concurrency") — this is one
data point, not a guarantee that a genuinely non-idempotent concurrent write behaves the same.
