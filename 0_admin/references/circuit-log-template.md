# Circuit {YYYY-MM-DD} — AG-{CODE}-{NNN} → AG-{CODE}-{NNN}

Tasks: {ids}. Skipped (already passed): {ids or none}.

{Optional: any deviation from the skill this run made, and why — e.g. no subagent-spawn tool
available, so the per-task cycle ran in-process.}

| Task | Outcome | Validation | Report | Wiki pages | Commit |
|---|---|---|---|---|---|
| AG-{CODE}-{NNN} | ✅ passed | {N}/{M} | [report](../../../2_raw/gym-rep-reports/{topic}/{YYYY-MM-DD}-{task-id-slug}.md) | {pages · atoms touched} | {sha} |
| AG-{CODE}-{NNN} | 🅿️ parked | {N}/{M} | [report](...) | — | {sha} |

{Append one row per task **as the circuit runs**, not at the end — an interrupted circuit still
has to leave a readable record.}

## Parked

### AG-{CODE}-{NNN} — {blocker one-liner}

{What was tried, the exact error, and the hypothesis for the fix. A parked task is a handover
note: the next person should not have to re-derive what already failed.}

## Lint findings (report-only, across the circuit)

- {What `fabric-lint` reported, accumulated over the whole circuit. Report-only — the circuit
  does not act on these. Note anything already reported by a previous run, and say how many runs
  it has survived.}

## Guidance gaps observed

- {Where `3_wiki/` was wrong, thin, or silent — named page or atom, and what the run actually
  found. This is the follow-up ingest backlog, and the reason the circuit log outlives the run.}
