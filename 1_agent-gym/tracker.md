# Agent-gym rep tracker

**Your** record of gym reps run to completion, one row per task. It starts at 0 and fills as you run reps in your own Fabric environment — every row below is a task waiting for you, not a task someone else finished. A rep counts as **passed** when its bundled `validate.json` exits 0 against the built artifact and a gym rep report exists in `2_raw/gym-rep-reports/<topic>/`. Wiki ingest of the findings is a separate, user-triggered step after review of the report.

**Exit 0 does not mean every check ran.** A check gated on a credential this repository cannot hold (`requiresEnv`, e.g. a delegated real-user token) is SKIPPED when the credential is absent — parked, not failed, and never counted as a pass. A passed row therefore means "nothing asserted failed"; which assertions were never made is recorded in the task's gym rep report, and a parked leg is not evidence.

**Overall: 2 / 2 tasks passed** (lakehouse 2/2). Totals come from the task folders and completions from your `2_raw/gym-rep-reports/` reports, so a fresh clone reads 0. Refresh with `python .claude/skills/check-training-progress/tracker_sync.py --write`; never hand-edit the counts.

## Lakehouse (`AG-LAK`) — 2 / 2

| Task | Name | Status | Passed | Report |
|---|---|---|---|---|
| AG-LAK-001 | Foundry | ✅ passed | 2026-09-16 | [report](../2_raw/gym-rep-reports/lakehouse/2026-09-16-AG-LAK-001-foundry.md) |
| AG-LAK-002 | Ravensworth | ✅ passed | 2026-09-18 | [report](../2_raw/gym-rep-reports/lakehouse/2026-09-18-AG-LAK-002-ravensworth.md) |

## Status key

- ✅ passed — validated (exit 0), gym rep report written (wiki ingest follows human review).
- 🟡 in progress — rep started, not yet validated (set by hand; not auto-derived).
- ⬜ not started.
