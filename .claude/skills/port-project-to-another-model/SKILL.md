---
name: port-project-to-another-model
description: "Migrate this Claude-shaped KB to the layout another coding assistant expects — GitHub Copilot, OpenAI Codex, Cursor, or Gemini / Antigravity. Triggers: '/port-project-to-another-model', 'I use Copilot not Claude', 'convert this repo for Gemini', 'make this work with Codex', 'set this up for Cursor', 'port the KB', 'migrate away from Claude'. Moves the skills (scripts included) to .agents/skills/, rewrites CLAUDE.md as that runtime's root doc, rewrites every path reference, and deletes the Claude-specific files. One-way."
---

# port-project-to-another-model

This repository ships Claude-first: `CLAUDE.md` is the schema and the skills (with their
scripts) live in `.claude/skills/`. If you work in another runtime, this skill **migrates**
the repo to that runtime's layout. Not a copy, not a generated mirror — a move. Afterwards
there is exactly one of everything, where your runtime expects it, and nothing
Claude-specific remains.

## What a migration does

1. **Moves** the entire `.claude/skills/` tree — `SKILL.md` files AND the Python — to
   `.agents/skills/`, the directory every non-Claude runtime scans. The tree keeps its
   shape, so the scripts' own relative-path logic keeps working.
2. **Rewrites `CLAUDE.md` as your runtime's root doc** — `AGENTS.md` for Codex, Copilot and
   Cursor, `GEMINI.md` for Gemini / Antigravity — then deletes `CLAUDE.md`. The generated
   file is not a mirror: it becomes the **authored master**. Edit it directly from then on.
3. **Rewrites every path reference in the repo** — skill bodies, READMEs, wiki pages, gym
   fixtures, scripts — so `.claude/skills/...` becomes `.agents/skills/...` everywhere.
4. **Deletes the `.claude/` directory** and any stale root docs left by older tooling.
5. **Verifies**: scans the whole repo and refuses to declare success while a single
   `.claude` reference remains.

## Run it

```
python .claude/skills/port-project-to-another-model/port.py --target codex
python .claude/skills/port-project-to-another-model/port.py --target copilot
python .claude/skills/port-project-to-another-model/port.py --target cursor
python .claude/skills/port-project-to-another-model/port.py --target gemini
```

Add `--check` to print the migration plan and change nothing. `--self-test` runs offline
fixtures. Commit everything it changes — `git status` will be large, and that is correct:
this is a layout migration, not an edit.

| Target | Root doc | Skills |
| --- | --- | --- |
| `codex` · `copilot` · `cursor` | `AGENTS.md` | `.agents/skills/<name>/` (scripts included) |
| `gemini` | `GEMINI.md` | `.agents/skills/<name>/` (scripts included) |

After migration, commands run from the new home, e.g.:

```
python .agents/skills/check-training-progress/capability_coverage.py --whiteboard
python .agents/skills/execute-gym-rep/preflight_probe.py --task AG-LAK-001
```

## One-way — understand this before running it

There is no port back. Once migrated:

- **Claude Code loses the skills.** It scans only `.claude/skills/`, which no longer
  exists. If you might switch between Claude Code and another runtime, do not migrate —
  the Claude-first layout already works for Copilot (which scans `.claude/skills/` natively),
  and the scripts run under any runtime from wherever they live.
- **Your root doc is the master.** There is no `CLAUDE.md` to regenerate from. Schema
  changes go straight into `AGENTS.md` (or `GEMINI.md`).
- **Upgrade bundles still arrive Claude-shaped.** When you receive a new tier's bundle,
  run this port *inside the bundle* first, then merge it into your repo — never merge a
  Claude-shaped bundle into a migrated repo directly, or the `.claude` layout comes back.

## What it refuses to do

`port.py` translates `CLAUDE.md` by finding specific sentences — the title, the "guidance
to Claude Code" line, the skills-invocation paragraph, the port row in the operations
table. If `CLAUDE.md` has been reworded so one is missing, the script **exits with an error
naming the missing text** rather than writing a partial root doc. A half-translated schema
is worse than a refusal: it would present itself as your runtime's instructions while still
telling it to behave like Claude Code. When it fires, restore the sentence or update
`ROOT_EDITS` in `port.py` — do not hand-write the output.

It also refuses to run twice: on an already-migrated repo it reports so and exits.

## After migrating

Point your runtime at the new root doc and confirm it reads the schema — the layers, the
topics table, the conventions. Ask your agent for a tour of the repository; it should
describe the framework from the root doc alone. Then run the preflight probe. Nothing else
about the workflow changes: the gym, the evidence layer and the wiki are runtime-neutral by
construction.
