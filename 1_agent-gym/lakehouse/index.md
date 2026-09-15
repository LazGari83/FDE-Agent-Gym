# Agent gym: lakehouse

2 practice tasks in this bundle — case studies, not tutorials. Each one builds something real in *your* Fabric tenant and is graded by the `validate.json` in its folder.

## Before the first rep

Two commands, both cheap, that between them turn "will this work in my tenant?" into an answer rather than a half-hour discovery:

```
cp .env.example .env        # FABRIC_WORKSPACE_ID is the only one you need
python .claude/skills/execute-gym-rep/preflight_probe.py --task AG-LAK-001
```

Your own sign-in is enough — no app registration, no service principal, no tenant administrator.

## How a rep works

The three task files, how rep state and coverage are kept: [`1_agent-gym/README.md`](../README.md). With an agent, invoke the `execute-gym-rep` skill and give it the task id; by hand, follow `task.md` and validate with the command it names.

The guidance to work from is [`3_wiki/lakehouse/`](../../3_wiki/lakehouse). Its **`atoms/` folder is empty** — every capability is a gap, so each rep researches and drafts its own atoms, and the first passing rep mints the first one.

## The tasks

- `AG-LAK-001` **Foundry**
- `AG-LAK-002` **Ravensworth**
