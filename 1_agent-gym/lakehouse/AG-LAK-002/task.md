---
task_id: AG-LAK-002
---

# Task 02: Ravensworth

Difficulty: starter+.

## Tasks (and validation)

1. Run `python provision.py` (local) — it writes `source-data/calibration_run.csv`.

2. Create a schema-enabled lakehouse named `AG_LAK_002_Ravensworth` (default schema `dbo`) in the workspace folder `Lakehouse`. Validate: [lakehouse-provisioned]

3. Create a notebook named `AG-LAK-002-Ravensworth` in the folder `Lakehouse` that loads the extract into `lab.calibration_run` — `run_id` string · `instrument_id` string · `technician` string · `run_date` date · `deviation_ppm` double · `passed` boolean. The notebook sizes its own session, addresses storage absolutely, reports its outcome as an exit value, installs nothing, infers no schema, and contains no summary logic. Validate: [load-notebook-contract]

4. Run it at least twice, submitting the second run while the first is still in flight; record exactly what comes back. The latest run must succeed. Validate: [load-notebook-ran-green, calibration-run-typed, run-not-in-dbo, q1-all-runs-loaded]

5. Produce `lab.calibration_summary` (`instrument_id` string · `runs` · `failed` · `max_deviation_ppm` double; one row per instrument) using a second, genuinely different execution surface — not the notebook. Validate: [q2-summary-from-livy, q3-surfaces-agree]

6. Create a second notebook named `AG-LAK-002-Ravensworth-Probe` (kept, not deleted) that records `lab.runtime_facts` (`fact_name` · `fact_value`, both string): `python_version` · `spark_version` · `runtime_jsonschema` (importable without installing anything, and at what version) · `default_lakehouse` — each established from the running session, never assumed. Validate: [probe-notebook-contract, probe-notebook-ran-green, q4-runtime-facts-recorded, q5-facts-have-values]
