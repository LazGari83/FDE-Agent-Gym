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
    mod = importlib.import_module("jsonschema")     # check, then decide — never `pip install` first
    fact = f"available {getattr(mod, '__version__', 'unknown-version')}"
except ImportError as exc:
    fact = f"not importable: {exc}"
facts = [("python_version", platform.python_version()),
         ("spark_version", spark.version),
         ("runtime_jsonschema", fact),
         ("default_lakehouse", str(notebookutils.runtime.context["defaultLakehouseName"]))]
spark.createDataFrame(facts, ["fact_name", "fact_value"]).write.format("delta") \
     .mode("overwrite").save(RUNTIME_FACTS_TABLE)
```

Land the answer in a **kept** notebook item that writes to a table — not a throwaway probe and
not a chat message. `importlib.import_module` over the candidate name costs nothing and never
risks a needless install or an unwanted downgrade. `notebookutils.runtime.context` is a plain
subscriptable mapping populated at run submission — no separate lookup call needed.

**Proven** (AG-LAK-002, 2026-09-18): `python_version=3.11.8`, `spark_version=3.5.5.5.4.20260807.1`,
`runtime_jsonschema=available 4.19.2` (importable with no install attempted),
`default_lakehouse=AG_LAK_002_Ravensworth` — all four read back non-null from
`lab.runtime_facts` via an independent Livy session.
