# Capability specs

What an expert Fabric agent must be able to **do**, per topic. One file per topic, one row per
capability. This is the spec both working layers answer to — the gym proves a capability by
exercise, the wiki documents it for a consuming agent, and a capability is finished only when
**both** have happened.

```
capability  ──►  N gym tasks (different angles)  ──►  landed reps  ──►  wiki page
 (declared)          (covered)                          (proven)         (documented)
```

A row is a **statement of intent, written before it is proven** — never a claim that anything
has been built. Proof state is derived, never written into these files:

```
python .claude/skills/check-training-progress/capability_coverage.py              # every topic
python .claude/skills/check-training-progress/capability_coverage.py --topic lakehouse
python .claude/skills/check-training-progress/capability_coverage.py --write      # refresh coverage.md
python .claude/skills/check-training-progress/capability_coverage.py --check      # exit 1 if a task/page declares an id no spec knows
python .claude/skills/check-training-progress/capability_coverage.py --next       # the ranked gap queue: what to build next
python .claude/skills/check-training-progress/capability_coverage.py --whiteboard # the training-progress display
```

`coverage.md` in this folder is that report. It is generated — never hand-edit it.

## The columns

- **id** — stable, and never renumbered. Tasks declare ids in `validate.json`
  (`"capabilities": [...]`); wiki pages declare them in frontmatter (`capabilities: [...]`).
  To remove a capability, move its row to the file's *Retired* section — landed reps and wiki
  pages still reference the id.
- **capability** — a thing the agent must be able to do. Not a fact it must know.
- **phase** — `design` · `build` · `operate`, matching the wiki cluster's lifecycle pages.
  Assigned by **where the failure is introduced, not where it surfaces**. `operate` means after
  go-live: running it, changing it live, incidents, environment failures.
- **distinct failure mode** — the granularity test. Two candidates that fail the *same* way are
  one capability. This is what keeps a spec bounded instead of becoming a documentation index.
- **angles** — how many *materially different* tasks must exercise it before the experience is
  hardened. An angle is a different situation — happy path · failure mode · at scale · in
  combination · inverted requirement — not a second run of the same shape. 1 where a single
  exercise settles it; 2 is the default; 3–4 for the capabilities that carry the topic.
- **evidence** — `machine-checkable` (a `validate.json` check *could* assert it) or
  `design-gate` (only a reasoned argument can judge it, evidenced by the design sign-off and the
  gym rep report). This is a statement about the *evidence type*, not about build state.

Build state is the separate `declared → covered → proven → hardened` axis in the derived report.

## Two header markers the tooling reads

- `**Community:**` — which community track the topic serves: `frontier-data-engineer` (data
  engineering — get data right), `frontier-decision-engineer` (analytics and apps — get
  value from data), or `both`. The whiteboard groups by it.
- `**Scope:** out-of-scope — <why>` — an entire Fabric area this KB has decided not to pursue.
  Its rows stay valid as references but drop out of the gap counts and the `--next` queue.

## These files are human-owned

The capability list decides what this KB covers, so no unattended run may add, reword, retire or
seed a row. An agent that believes one is missing reports it and stops. See *The capability spec
is human-owned* in [`CLAUDE.md`](../../CLAUDE.md), and the **check-training-progress** skill for how
to help author one when asked.
