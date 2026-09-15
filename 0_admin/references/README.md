# References — the KB's templates

One template per artifact the KB produces. Copy the template, fill the placeholders, land the
file where the row says — most of the time a skill does this for you.

| Template | What it produces | Where the output lands | Who fills it |
| -------- | ---------------- | ---------------------- | ------------ |
| [atom-template.md](atom-template.md) | One execution card per proven capability | `3_wiki/<topic>/atoms/<CAP-ID>.md` | `fabric-ingest`, when a passing rep proves a capability |
| [capability-template.md](capability-template.md) | A cluster page (one of the six per topic) | `3_wiki/<topic>/<page>.md` | `fabric-ingest` |
| [pattern-template.md](pattern-template.md) | A reusable cross-cutting technique page | `3_wiki/<topic>/<pattern>.md` | `fabric-ingest` |
| [decision-template.md](decision-template.md) | An evidence-based X-vs-Y verdict | `3_wiki/decisions/<x-vs-y>.md` | you + `fabric-ingest`, after a bake-off |
| [index-template.md](index-template.md) | Annotated router rows | `3_wiki/index.md` | `fabric-ingest` |
| [raw-template.md](raw-template.md) | An immutable evidence capture | `2_raw/<topic>/YYYY-MM-DD-<slug>.md` | you, or a capture |
| [task-template.md](task-template.md) | A gym practice task | `1_agent-gym/<topic>/AG-<CODE>-<NNN>/task.md` | you, via `create-gym-task` |
| [gym-rep-report-template.md](gym-rep-report-template.md) | The record of one gym rep | `2_raw/gym-rep-reports/<topic>/YYYY-MM-DD-<task>.md` | `execute-gym-rep` |
| [circuit-log-template.md](circuit-log-template.md) | The run record of one unattended batch | `0_admin/logs/circuits/YYYY-MM-DD-*.md` | `execute-gym-circuit` |

Three templates — atom, pattern, decision — have no live instance in the shipped repo yet;
your first ingests create them.
