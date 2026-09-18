---
type: atom
topic: lakehouse
community: frontier-data-engineer
capability: LH-C08
title: Build a notebook item from cells, never a bare script
requires:
  env: [fabric-auth]
  atoms: []
updated: 2026-09-18
fabric_release: 2026-09
status: current
evidence: proven
sources:
  - 2_raw/gym-rep-reports/lakehouse/2026-09-16-AG-LAK-001-foundry.md
  - 2_raw/gym-rep-reports/lakehouse/2026-09-18-AG-LAK-002-ravensworth.md
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

**Proven** (AG-LAK-001, 2026-09-16): `AG-LAK-001-Foundry` created in one call from
`notebook_content_py()` output; `notebook-exists` passed against the live `get_source()` read
(`sourceContains: ["retail.product"]`, `sourceLacks: ["!pip"]`) — the offline `--lint-source`
result and the live check agreed exactly, so linting before upload is a reliable predictor
here, not just a speed trick.

**Re-proven** (AG-LAK-002, 2026-09-18): two notebook items built from `notebook_content_py()`
in the same rep, both created cleanly in one call each. No deviation.
