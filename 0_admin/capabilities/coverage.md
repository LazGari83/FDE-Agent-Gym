# Capability coverage

**Generated — do not edit.** `python .claude/skills/check-training-progress/capability_coverage.py --write`

Derived by joining the hand-written specs in `0_admin/capabilities/<topic>.md` with each task's `validate.json` declarations, the passed gym rep reports in `2_raw/gym-rep-reports/`, and the `capabilities:` frontmatter on wiki pages. A capability is **done** when it is *hardened* (landed in as many tasks as it has angles) **and** *documented* (a wiki page carries it).

## lakehouse — 0/13 done

Gym: **0** hardened · 0 proven · 7 covered · 6 declared. Wiki: **13** documented · 0 undocumented.

**design** 3 caps, 0 hardened, 0 landed reps · **build** 7 caps, 0 hardened, 0 landed reps · **operate** 3 caps, 0 hardened, 0 landed reps

| id | capability | phase | gym | reps | wiki | tasks |
|---|---|---|---|---|---|---|
| LH-C01 | Judge whether a notebook + lakehouse is the right tool at all | design | covered | 0/2 | documented | _AG-LAK-001_ |
| LH-C02 | Address tables by schema and four-part name | build | covered | 0/2 | documented | _AG-LAK-001_ |
| LH-C06 | Choose the cross-environment path-binding strategy — absolute ABFS vs attached lakehouse | design | declared | 0/3 | documented | — |
| LH-C07 | Provision a lakehouse item over REST with the right creation options | build | covered | 0/2 | documented | _AG-LAK-001_ |
| LH-C08 | Produce a runnable notebook / compute artifact through the chosen surface | build | covered | 0/3 | documented | _AG-LAK-001_, _AG-LAK-002_ |
| LH-C09 | Address lakehouse data by absolute path when portability requires it | build | declared | 0/3 | documented | — |
| LH-C10 | Declare explicit Spark schemas and coerce on read/write, nested types included | build | declared | 0/3 | documented | — |
| LH-C12 | Manage session packages — inline `%pip` vs an Environment item, and the pipeline-trigger contract | build | declared | 0/2 | documented | — |
| LH-C14 | Execute against Fabric Spark compute and drive the run to a verified terminal state | operate | covered | 0/4 | documented | _AG-LAK-001_, _AG-LAK-002_ |
| LH-C15 | Force or await the SQL analytics endpoint metadata sync before a downstream T-SQL read | operate | declared | 0/2 | documented | — |
| LH-C18 | Size and configure the Spark session for the workload | operate | declared | 0/2 | documented | — |
| LH-C23 | Choose the notebook authoring & execution surface | design | covered | 0/4 | documented | _AG-LAK-002_ |
| LH-C27 | Instrument the execution environment before building on it, and record what it found | build | covered | 0/2 | documented | _AG-LAK-002_ |

Italicised tasks exist but have not landed a passed gym rep report.

