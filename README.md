# Frontier Data Engineer — Trial

Track: **Frontier Data Engineer** (the data-engineering track — get data right). Sign-in: **user**.

A starter repository for building your **own** Microsoft Fabric knowledge base: the distilled, evidence-backed judgment you (or your coding agent) can lean on while building real things in Fabric.

## What's in here

- **2 practice tasks** across 1 topic — `1_agent-gym/<topic>/index.md`
- **13 capabilities** — `0_admin/capabilities/`, the syllabus your progress is measured against
- **The check engine** — `code/validation/fabric_test.py`. Read-only: it issues GETs and cannot change your tenant.
- **2 check families** — lakehouse, notebook
- **13 toolkit modules** — `code/`, the proven Fabric REST call shapes (`code/README.md` is the map)
- **The skills** — `.claude/skills/` (mirrored to `.claude/skills/` for Claude Code): the operations your agent runs
- **One worked example** — the six-page `3_wiki/lakehouse/` cluster, which is the *shape* a finished topic takes, not the knowledge.

## What is not in here, and why

- **The knowledge.** `3_wiki/` is nearly empty and `2_raw/` is scaffolding. That is the intended starting state, not missing content: every claim in this KB traces to something that ran in a real tenant, and none of your runs have happened yet.
- **Your work, on an update.** Nothing in this bundle writes to `3_wiki/<topic>/` or `2_raw/`, so a later version can never overwrite the pages your agent builds there.

`BUNDLE.json` lists every file here, the manifest line that put it there, and a hash of it as shipped. This is **version 1.3** (built 2026-09-10).

## Taking a newer version

Point the `update-framework` skill at the new zip (or the folder you unzipped it into) and it merges the framework into this repo without touching your work:

```
python .claude/skills/update-framework/update_framework.py ~/Downloads/data-eng-trial-<newer version>.zip
```

Every download names its version — you are on `data-eng-trial-1.3.zip`.

It prints a plan and writes nothing until you pass `--apply`. Files you never edited are replaced; files you did edit are listed for you to decide about; `2_raw/` and `3_wiki/` are never written at all. **Commit your work first** — `git diff` afterwards is how you see what changed.

## Start here

1. `pip install -r code/requirements.txt` (Python 3.11+).
2. Copy `.env.example` to `.env` at the repo root and set `FABRIC_WORKSPACE_ID` to a workspace on your tenant. **Use a workspace you are willing to lose** — the tasks create, modify and delete real items.
3. `python code/core/auth.py` — signs you in (a browser opens; set `FABRIC_AUTH_DEVICE_CODE=1` where there is none, and use the URL it PRINTS, never one an agent types from memory) and caches it. Sign in YOURSELF, in your own terminal — an agent's shell usually has no browser to do it with. **If your agent runs commands anywhere other than that terminal** (a container, WSL, a sandbox), first set `FABRIC_TOKEN_CACHE=.fabric_kb/msal_cache.json` in `.env`: the cache is otherwise per-HOME, and everything the agent runs after this — step 4 and every rep — reports "not signed in" for a sign-in that plainly worked. `python code/core/auth.py --status`, run in each, shows the two paths.
4. `python .claude/skills/execute-gym-rep/preflight_probe.py --task AG-LAK-001` — proves your tenant before you spend a rep on it. (`--all` probes the whole estate, including lanes this bundle carries no tasks for.)
5. Read `3_wiki/lakehouse/prerequisites-and-fit.md`, then open `1_agent-gym/lakehouse/index.md` and run `AG-LAK-001`.
6. Capture what the run taught you with the `fabric-ingest` skill, then watch coverage move:

   ```
   python .claude/skills/check-training-progress/capability_coverage.py --whiteboard
   ```

## Where to read next

- `CLAUDE.md` — the schema: layers, communities, topics, conventions, operations. Your agent reads this first.
- `3_wiki/index.md` — the wiki router and the worked example's shape.
- `1_agent-gym/README.md` — how a practice rep works.
- `0_admin/capabilities/README.md` — how the spec and coverage model work.

## Licence

Copyright (c) 2026 UnifiedEducation. Licensed under the [PolyForm Internal Use License 1.0.0](LICENSE.md) — a commercial, source-available licence, not an open-source one. Use it and change it freely inside your own organisation, including commercially; do not redistribute it, resell it, or publish it or anything you build from it outside your company. See [`NOTICE.md`](NOTICE.md). **No warranty.**
