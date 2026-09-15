# Logs

Records of runs — **not knowledge, and not a source to answer questions from.** Nothing in here is
written by hand. The tools below create their own files on first use, so this folder starting
empty is the correct state.

## Instructions — read before writing anything here

- **This folder starts empty. It fills as you run reps in _your_ Fabric environment.** It holds
  no data from anyone else's tenant, by design: a ledger describing someone else's workspaces,
  capacities and timings would be noise at best and misleading at worst.
- **Never hand-edit a file in here.** These are append-only records written by tools. An entry
  you type is an entry nothing produced, which makes every measurement derived from the file a
  lie.
- **Never delete entries to tidy up.** If a run went wrong, the record of it going wrong is the
  point. Append, never rewrite.
- **Do not cite anything in here as evidence in `3_wiki/`.** Timings and cache hits are
  operational facts about one machine on one day. Wiki claims cite `2_raw/`.

## What lands here

| File | Written by | What it is |
| ---- | ---------- | ---------- |
| `gym-run-log.jsonl` | `execute-gym-rep` (`rep_log.py`), and `gym_validate.py` for validator runs | Append-only timing trail, one line per phase boundary per rep. Answers "where did the run time go?" with data instead of a guess. |
| `circuits/*.md` | `execute-gym-circuit` | One file per unattended batch — task outcomes, parked blockers, lint findings, guidance gaps. See [`circuits/README.md`](circuits/README.md). |
| `framework-updates.jsonl` | `update-framework` | Append-only, one line per applied update — versions moved between, which baseline rung judged the member's files, counts written and deleted, what was left for review, and any post-merge check that failed. Answers "what did the 1.1 → 1.2 update actually do to this repo?" months later. |
| `.preflight-cache.json` | `execute-gym-circuit` (`preflight_probe.py`) | Tenant probe results, cached 6h so a circuit does not re-probe on every rep. **Gitignored** — it holds your capacity and workspace ids. Only passes are cached; a stale cached *failure* would lie to you after you fixed it. |

`gym-run-log.jsonl` and the cache regenerate themselves if deleted; a circuit log is a record of a
past run — deleted is gone. Deleting `gym-run-log.jsonl` loses your timing history
and nothing else; deleting the cache costs one slow preflight.

## Reading the run log

```
python .claude/skills/execute-gym-rep/rep_log.py <TASK-ID> report   # one run, per-phase breakdown
python .claude/skills/execute-gym-rep/rep_log.py --summary          # one row per run + phase means
```

Canonical phase names, so that aggregating across runs means something:
`resolve` · `preflight` · `classify` · `draft` · `plan` · `payload` · `engine` · `build` ·
`triage` · `debrief` · `report`.

`build` is hand-orchestrated construction on topics with no engine executor — the segment the
executors exist to collapse. If it dominates your summary, that is the signal to write one.
