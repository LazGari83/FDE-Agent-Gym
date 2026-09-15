---
task_id: AG-{CODE}-{NNN}
---

# Task {NN}: {Scenario}

Difficulty: {starter | core | advanced | capstone}. {Optional single clause only when the task's *kind* differs — "A debug task: nothing is built from scratch." Never a skill-focus sentence: naming the discipline tells the agent the answer.}

## Tasks (and validation)

1. Run `python provision.py` ({local | needs `.env` | in a Fabric notebook}) — {one clause: what it writes or stands up}.

2. {One graded outcome in plain language, with the exact names validation asserts inline. State WHAT must be true, never WHICH mechanism achieves it — no API names, no surface names, no toolkit calls, no capability ids. Classifying the problem and choosing the atoms is the agent's graded work.} Validate: [{check-id}, {check-id}]

3. {…each numbered item carries its check ids; every check id in `validate.json` appears on exactly one bullet. Contract semantics a check asserts (a naming rule, a digest recipe, a reason code, a forbidden literal) are stated — as requirements, not method. Expected values are never stated: the agent derives them from `provision.py`.}
   - {Sub-bullets for multi-part contracts: column lists, rule tables, formats.}

{Optional final lines, only when load-bearing: a cleanup step that is itself a safety rule; an "Open question:" the task exists to settle, phrased as what-to-record, not how-to-do; a prerequisite beyond the probe's baseline.}
