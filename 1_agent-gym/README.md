# Agent Gym

Practice projects, not tutorials: agent-actionable Fabric tasks with machine-checkable acceptance tests. Each topic folder holds its own progression — see its `index.md` for the topic's execution model, naming contract and syllabus. This file holds the contract every task shares.

## How every task works

Each task is a self-contained folder:

- `task.md` — a numbered list of plain-language outcomes, each closed by the `validate.json` check ids that grade it. It names **no capability and no mechanism** — classifying each bullet, pulling the matching atoms from the topic's `3_wiki/<topic>/atoms/`, and filling the gaps with draft atoms is the rep's graded work (see **execute-gym-rep**).
- `provision.py` — writes the task's deterministic fixture or source data. Where it runs (locally from the task folder, or inside a Fabric notebook) and what it may create are per-topic — see the topic's `index.md`.
- `validate.json` — the machine-checkable acceptance test, run as the plan's terminal step. **Exit 0 = done.** Its expected values are the answer key: a rep never reads them.

Run a task with `/execute-gym-rep <task-id>`; run batches unattended with **execute-gym-circuit**. Rep state lives in [`tracker.md`](tracker.md); capability coverage in `0_admin/capabilities/coverage.md` — both are authoritative, so no index file carries state of its own.

## Write your own tasks

The tasks that ship are a starting point, not the syllabus. **Any topic here can be extended,
and that is what [`create-gym-task`](../.claude/skills/create-gym-task/SKILL.md) is for** — it
authors the whole folder (scenario, deterministic fixture, machine-checkable acceptance test) and
wires it into the topic index, the validator and the tracker:

```
/create-gym-task  <topic>, <the thing you want an agent to get right>
```

Two rules keep an authored task honest, and they are the same ones the shipped tasks follow:

- **Target a capability row that already exists** in `0_admin/capabilities/<topic>.md`. A new
  task is usually a fresh *angle* on a known failure mode rather than a new one. Adding a row is
  a deliberate, human-owned decision — `check-training-progress` will help you make it, and will not
  make it for you.
- **Build the expected values from something deterministic** — a fixture `provision.py` writes,
  or a real captured response — never from what you assume the platform does. A check built on an
  assumption grades the assumption.

`check-training-progress --next` ranks the gaps, so "what should I build?" has an answer derived from
the spec rather than from taste.

## Sharpen the KB as you go

The gym exists to harden the wiki: `3_wiki/<topic>/` is the curated home, and what is *not* proven there is what your rep is for. A capability with no atom is a gap, the rep's draft atom is the hypothesis, and ingest promotes it only when the passing run confirms it.
