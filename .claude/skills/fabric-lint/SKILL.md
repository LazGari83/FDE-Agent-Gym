---
name: fabric-lint
description: "Use to health-check the Fabric wiki (both communities). Triggers: 'lint the wiki', after an ingest batch, and after each monthly Fabric release (to catch facts a new release may have superseded). NOT for auditing the tutorial catalogue — that's a different artifact."
---

# fabric-lint

The wiki's internal health check. Two categories with different authority: deterministic fixes the skill makes itself, and judgment calls it only reports. Run after ingest batches and after each monthly Fabric release — **not on a blind timer**. Read `CLAUDE.md` for conventions.

## Deterministic checks (auto-fix)

- **Index consistency** — reconcile `3_wiki/index.md` against actual `3_wiki/` files (excluding `index.md`/`log.md`):
  - File exists, missing from index → add a row (`(no summary)` placeholder; use the page's `updated:` for the date).
  - Index row → nonexistent file → mark `[MISSING]`; don't delete, let the maintainer decide.
- **Internal links** — every markdown link in page bodies and `See Also` (excluding `sources:` frontmatter, handled below):
  - Target missing → search `3_wiki/` for a same-named file. Exactly one match → fix the path. Zero/many → report.
- **Anchors** — run `python .claude/skills/fabric-lint/anchor_check.py`. Every `[text](path#fragment)` link in `3_wiki/` must land on a real heading in the target file (GitHub slug rules; links inside code blocks ignored). Exit 1 lists each dead link with the closest real heading — fix the fragment, or the heading drift that orphaned it. A card compressed on the premise "the detail is one click away" must have a click that lands. Carries `--self-test`.
- **Placeholders** — a `status: placeholder` page is an empty slot, exempt from the `sources:` rule below (`sources: []` is correct; it asserts nothing) and from the `evidence`/summary expectations. Two things to check instead: it must **not** declare `capabilities:` (that would mark them documented on an empty page), and its "what unlocks it" section must still name real, current evidence. Report any placeholder whose unlock has since landed — that is a page waiting to be written, not a gap. A seed page — `status: current` with `sources: []` and a `provenance_notes` block declaring it ships pre-evidence — is the one other sanctioned `sources: []` case: shipped worked examples start that way, and the exemption ends at the first ingest that gives the page real sources.
- **Provenance references** — every path in a page's `sources:` frontmatter must point to an existing `2_raw/` file:
  - Missing → search `2_raw/` for a same-named file. Exactly one → fix. Zero/many → report. **A page with an unresolvable source is a trust failure — always surface it.**
- **See Also** — within a topic folder, add obviously-missing cross-references between related pages; remove links to deleted files.
- **Community frontmatter** — every wiki page must carry a `community:` (`frontier-data-engineer | frontier-decision-engineer | both`). Missing → report; if the topic unambiguously implies the community (per the `CLAUDE.md` topic table), suggest the value.
- **Repo hygiene — build artefacts in `code/`.** The wiki holds runnable code, so compiled artefacts can end up committed beside it. Ask git, never the filesystem:

  ```
  git ls-files | grep -E '__pycache__|\.pyc$|\.pyo$'
  ```

  Output → those files are tracked; report them with the exact paths and propose `git rm -r --cached <path>` plus a `.gitignore` entry. **No output → there is nothing to report.** A `__pycache__/` directory sitting on disk is normal and expected; `ls` seeing it says nothing about git. **Present on disk ≠ tracked by git.**

- **Answer-sheet files in task folders** — run `python .claude/skills/execute-gym-rep/gym_run.py --self-test` (offline, no tenant). Among its checks: no `plan.json`/`manifest.json` exists in any task folder — the plan is the agent's output at rep time, and a checked-in one is the answer sheet. Non-zero exit → report the failing check line verbatim.
- **Task-list ↔ spec integrity** — for every `task.md` in the task-list format: every check id in the task's `validate.json` appears in exactly one `Validate: [...]` list, and no listed id is absent from the spec. A bullet grading a nonexistent check is a silent no-op; a spec check on no bullet is an ungraded requirement the agent cannot see. Also report any capability id (`LH-Cnn`-style) or mechanism vocabulary that has leaked into a task bullet — the task states outcomes; classification is the rep's graded work.
- **Atom health** — in every `3_wiki/<topic>/atoms/` folder: each atom's `capability:` id must exist in the topic's spec, and the standard `sources:` provenance rule applies (atoms are wiki pages). Report any atom body drifting well past ~30 lines — atoms are execution cards; reasoning belongs in the cluster page the atom's `derived_from:` names. Report as **atom debt** any capability that is rep-proven and validated but has no atom while its topic carries an atoms index — that is an ingest that skipped the atoms cascade.

- **Raw provenance integrity** — every `sources:` entry on a wiki page must name a file that exists in `2_raw/`. A citation pointing at a deleted capture is a claim with no evidence behind it, which is the one thing the pipeline exists to prevent, and it is invisible to `anchor_check.py` (frontmatter list entries are not markdown links). Report the page, the line, and the missing path. A seed page — `status: current` with `sources: []` and a `provenance_notes` block declaring it ships pre-evidence — is the one other sanctioned `sources: []` case: shipped worked examples start that way, and the exemption ends at the first ingest that gives the page real sources.

## Stale task claims (auto-fix the wiki side, report the task side)

Run `python .claude/skills/fabric-lint/stale_task_claims.py`. Exit 1 means a **gym task teaches something the wiki has since refuted** — the highest-severity drift this repo produces, because `task.md` is the first thing a rep reads, so a stale copy is trusted *more* than the wiki that corrected it.

Two failure classes:

- **EMPTY-WIKI** — a task tells the agent `3_wiki/<topic>/` is empty or absent when it holds pages. An agent told the answer key does not exist re-derives everything from scratch; this is a cost bug as much as a correctness one.
- **REFUTED** — a task repeats a claim the wiki explicitly corrected.

**Quoting a refuted claim in order to correct it is allowed** and is the house convention — the wiki does it with `~~claim~~ CORRECTED`, and a task's provenance note should record what was once believed and what refuted it. The check suppresses on a refutation marker in the same line. Deleting history silently is worse than annotating it.

**WARN / UNREGISTERED** means the wiki marks a claim corrected but no entry in the script's `REFUTED` registry watches the gym for it. Fixing this is part of ingest: **when an ingest corrects a wiki claim, add a `REFUTED` entry in the same pass**, or nothing is watching for tasks that still teach the old version.

The script carries `--self-test` (verify with `--self-test` after editing a pattern; it pins the pre-correction text the patterns must catch and the corrected text they must ignore). Do not loosen a pattern to make a scan pass — that is the same error as loosening a `validate.json` expectation.

## Capability references

Run `python .claude/skills/check-training-progress/capability_coverage.py --write --check`. A non-zero exit means a task or page declares a capability id no spec knows about — a broken reference. **Fix it by correcting the id in the task or page**, which is almost always a typo or a stale id. If the declaration is right and the *spec* is genuinely missing a row, that is a capability decision: report it and leave it, because `0_admin/capabilities/` is human-owned (see *The capability spec is human-owned* in `CLAUDE.md`). Never resolve a red `--check` by adding the row. The refreshed `0_admin/capabilities/coverage.md` also surfaces the ingest debt this skill should be reporting: capabilities **proven by a landed rep but carried by no wiki page**.

## Heuristic checks (report only — human judgment)

- **Factual contradictions** across pages.
- **Superseded / stale claims** — especially pages whose `fabric_release` predates a release that changed the underlying behaviour. This is the core freshness check; run it deliberately after each monthly release.
- **Missing conflict annotations** where sources disagree but the page doesn't say so.
- **Orphan pages** with no inbound links.
- **Missing `status` forward-links** — a `superseded`/`archived` page that doesn't point to its replacement.
- **Concepts frequently referenced but lacking a page** — candidates for the next ingest.
- **Prune, don't just accrete** — flag bloat, duplication, and contradictions that have crept in. Adding a rule per mistake without ever removing is how a wiki rots.

## Before you report anything

A report-only finding is a claim, and this skill's findings get read as facts — they propagate into `3_wiki/log.md`, into circuit logs, and into the maintainer's queue. Two rules:

- **A claim about repository or environment state must be established by running the command that decides it**, not by inference from a directory listing, a filename, or a page's prose. Tracked-by-git is `git ls-files`. Ignored is `git check-ignore`. Existence is a read. If you cannot run the deciding command, say the claim is unverified rather than asserting it.
- **Check whether the finding is already recorded before repeating it.** A finding logged in a previous run and re-reported verbatim reads as "still outstanding" and hardens into debt. If it is genuinely unchanged, say so once and reference the earlier entry; if a re-check shows the earlier report was wrong, withdraw it explicitly in `3_wiki/log.md` rather than dropping it silently.

## Post-lint

Append to `3_wiki/log.md`:

```
## [YYYY-MM-DD] lint | <N> issues found, <M> auto-fixed
```

Then list the report-only findings in the conversation — the human maintainer is the QA gate.
