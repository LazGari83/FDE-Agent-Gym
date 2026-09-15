# Capability spec: Lakehouse development in notebooks (`LH`)

**Community:** frontier-data-engineer. **Topic:** `lakehouse`.

The capability spec — what an expert Fabric agent must be able to *do* in this topic. Every id is a target your agent can be graded against, and the count is the true denominator of your progress board (`check-training-progress`). Only the capabilities this bundle's tasks exercise are listed.

This is the **id list**, with the phase each capability fails in and the number of materially different reps that harden it. The reasoning behind the syllabus — the distinct failure mode each one guards against — ships with the paid tiers.

## The capabilities

| id | capability | phase | angles |
|---|---|---|---|
| LH-C01 | Judge whether a notebook + lakehouse is the right tool at all | design | 2 |
| LH-C02 | Address tables by schema and four-part name | build | 2 |
| LH-C06 | Choose the cross-environment path-binding strategy — absolute ABFS vs attached lakehouse | design | 3 |
| LH-C07 | Provision a lakehouse item over REST with the right creation options | build | 2 |
| LH-C08 | Produce a runnable notebook / compute artifact through the chosen surface | build | 3 |
| LH-C09 | Address lakehouse data by absolute path when portability requires it | build | 3 |
| LH-C10 | Declare explicit Spark schemas and coerce on read/write, nested types included | build | 3 |
| LH-C12 | Manage session packages — inline `%pip` vs an Environment item, and the pipeline-trigger contract | build | 2 |
| LH-C14 | Execute against Fabric Spark compute and drive the run to a verified terminal state | operate | 4 |
| LH-C15 | Force or await the SQL analytics endpoint metadata sync before a downstream T-SQL read | operate | 2 |
| LH-C18 | Size and configure the Spark session for the workload | operate | 2 |
| LH-C23 | Choose the notebook authoring & execution surface | design | 4 |
| LH-C27 | Instrument the execution environment before building on it, and record what it found | build | 2 |

_13 live capabilities._
