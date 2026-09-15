---
name: update-framework
description: "Migrate this repo from the framework version it is on to a newer one — merge a bundle zip or folder in, replacing framework files while leaving everything the member built untouched. Triggers: '/update-framework <path>', 'update the framework', 'I downloaded the new version', 'migrate me from 1.1 to 1.2', 'merge this new bundle in', 'apply the latest release', 'is my version current'. Takes the path to the NEW version (a .zip or an unzipped folder). Never writes 2_raw/ or 3_wiki/."
---

# update-framework

The member's repo is two things in one tree: **the framework we shipped** (the schema, the
toolkit, the skills, the gym tasks, the syllabus) and **their work** (wiki pages, raw
evidence, rep reports, their own tasks and code). This skill moves the first forward to a
newer release and does not touch the second.

**The member supplies the new version** — a bundle `.zip` they downloaded, or the folder they
unzipped it into. Everything else is derived.

```
python .claude/skills/update-framework/update_framework.py <zip-or-folder>
```

That prints a plan and **writes nothing**. Read it, resolve what needs a decision, then apply.

## The one fact the whole design rests on

Every bundle carries `BUNDLE.json`: every file in it, and a `sha256` of that file as shipped.

> **local file == the recorded hash** → the member never edited it → the new version may land
> **local file != the recorded hash** → they edited it → nothing overwrites it without them

There is no other way to tell "the member changed this" from "we changed this", and getting it
wrong destroys exactly the work the product exists to accumulate. So the script never guesses:
where the baseline cannot answer, the file goes to the review list.

The plan prints which rung of the baseline ladder it used, and it changes how you read it:

| Rung | When | How to treat the plan |
| --- | --- | --- |
| `bundle-hashes` | the repo's `BUNDLE.json` carries hashes (v1.3 and later) | exact, both ways. The `update` list is genuinely untouched files, and lands automatically |
| `git-history` | no hashes, but it is a git repo | **detects edits only.** Dirty, or more than one commit, means edited → `conflict`. Everything else changed upstream is `unknown` → review |
| `none` | neither | as above: anything changed upstream is `unknown` |

**Why the weaker rungs never auto-replace.** A one-commit file is not evidence of an untouched
one: the member who unzips the bundle, tinkers, and only then runs their first `git add -A` has
our files *and* their edits in a single commit. Reading that as untouched overwrites exactly the
work this skill exists to protect, so it goes to review instead. New files still land normally —
a file the member does not have cannot be their work. This is why the first update off a 1.1 or
1.2 repo has a long review list, and why every update after it does not.

## Run it

```
python .claude/skills/update-framework/update_framework.py <src>                # the plan
python .claude/skills/update-framework/update_framework.py <src> --diff         # + the diffs
python .claude/skills/update-framework/update_framework.py <src> --apply        # write it
python .claude/skills/update-framework/update_framework.py <src> --apply \
    --only code/core/auth.py CLAUDE.md          # land specific paths, after resolving them
python .claude/skills/update-framework/update_framework.py --restore latest     # undo
python .claude/skills/update-framework/update_framework.py --self-test          # offline
```

Other flags: `--prune` (also delete files the new version dropped — reported, never deleted, by
default) · `--scaffold` (create empty `2_raw/<topic>/` and `3_wiki/<topic>/` folders for topics
the new version adds — the **only** thing that ever writes inside those trees, and all it can do
is create an empty directory) · `--all` (no truncation) · `--json` (the plan as data) ·
`--allow-dirty` · `--force` (override an identity refusal — almost always the wrong answer) ·
`--no-verify` · `--keep-temp` (leave an extracted zip on disk, so both versions of a conflicted
file can be opened side by side) · `--repo <path>` · `--no-handoff`.

**It hands off to the newer updater.** The member starts this from the copy they already have,
which is by definition the older one — and it is the *new* release that knows what changed in
it. So if the source bundle carries a different `update_framework.py`, this one re-runs the
merge with that one (`--repo` pointed back here) and prints a line saying so. A member on 1.2
therefore gets 1.3's rules for the 1.2 → 1.3 merge, not on the update after. `--no-handoff`
keeps the work in the local copy; the handoff is skipped anyway if the incoming script does not
compile.

## Bootstrapping: a repo whose version has no `update-framework`

This skill first shipped in **1.3**. A member on 1.1 or 1.2 has no copy of it — and does not
need to hand-copy one. They run the updater **out of the new bundle**, pointed at their repo,
and it installs itself as part of the merge (it lands in the `add` list like any other new
file). One command, from inside their repo:

```
cd <their repo>
python /path/to/data-eng-trial-1.3/.claude/skills/update-framework/update_framework.py \
       /path/to/data-eng-trial-1.3
```

Running from the bundle means the script's own root *is* the source, so it resolves the target
to the directory the member ran it from and prints which repo that is. `--repo <path>` names it
explicitly and works from anywhere. If neither can be resolved — running it while standing in
the bundle itself — it refuses with both commands rather than comparing the bundle with itself
and reporting "all identical" for a repo it never opened.

After that first update the skill is in their repo, and every later version is the plain
`python .claude/skills/update-framework/update_framework.py <new bundle>`.

## What the classes mean, and who decides

**Written on `--apply`:**

| Class | Meaning |
| --- | --- |
| `add` | new in this version, absent here — lands as-is |
| `update` | changed upstream, provably untouched here — replaced |
| `merge-lines` | `.env.example` · `.gitignore` — upstream blocks appended, the member's lines kept verbatim |
| `merge-spec` | `0_admin/capabilities/<topic>.md` — new rows appended by id; existing rows never reworded, retired or reordered |
| `scaffold` | an empty topic folder, `--scaffold` only |

**Never written — the member's call, and the reason this skill needs an agent:**

| Class | Meaning | How to work it |
| --- | --- | --- |
| `conflict` | changed upstream **and** edited here | read both, decide, below |
| `unknown` | changed upstream, no baseline to judge their copy | same as `conflict`, with less information — lean on the diff |
| `keep` | they edited it, upstream did not change it | nothing to do; mention it and move on |
| `gone` | dropped upstream | leave it unless it breaks an import — then `--prune` |
| `protected` | differs upstream, but the zone is never written | report the paths, never edit them |
| `rederive` | `tracker.md` · `coverage.md` | nothing — recomputed from their filesystem after the merge |

## The zones (hard, and not overridable by any flag)

**Never written.** `2_raw/**` · `3_wiki/**` — including the seed cluster we shipped, because
their ingests rewrite those pages and a newer "better" version of `gotchas.md` would delete
real evidence · `.env` · `0_admin/logs/**` (append-only; a copied ledger is a lost ledger) ·
any file neither `BUNDLE.json` names, which is theirs by definition.

**Re-derived, never copied.** `1_agent-gym/tracker.md` and `0_admin/capabilities/coverage.md`
are the member's own numbers, computed from their own filesystem by their own tools. The script
runs `tracker_sync.py --write` and `capability_coverage.py --write` after the merge, because a
new version usually changes the denominators.

**The capability spec is the one sanctioned automated write to a human-owned file.**
`0_admin/capabilities/` is the member's (CLAUDE.md, Conventions), and no unattended run edits
it. An update appends the rows the new release adds, on a run the owner asked for and confirmed
— the weakest possible edit. A reworded row, a retirement, or changed prose above the table is
**reported, never applied**; tell the member and let them decide. If the columns changed (a tier
upgrade re-renders the spec), the merge refuses and the report names the rows only they have, so
nothing of theirs can be lost by taking upstream's file.

## The protocol

1. **Commit or stash first.** `git diff` after the merge is how the member sees what changed,
   and the script refuses to write over an uncommitted change in a file it is about to touch.
   Not a git user? Every modified file is backed up to `.framework-backup/<stamp>/` and
   `--restore latest` puts it back.
2. **Run the plan.** No flags. Read the counts, the baseline rung, and any `note:` lines — a
   tier upgrade, a missing manifest and a same-version drift check all read differently.
3. **Work the review list.** For each `conflict` / `unknown`, read the diff (`--diff`, or open
   both files — the source stays on disk with `--keep-temp`) and apply the defaults below.
4. **Tell the member what you propose**, grouped, before writing anything: what lands
   automatically, what you propose to do with each conflict, and what you are leaving alone.
   Keep it short — counts plus the conflicts by name.
5. **Apply.** `--apply` for the safe classes. For conflicts you resolved by hand, edit the file
   yourself and leave it out of `--only`; for conflicts where upstream wins, land them with
   `--apply --only <path> …`.
6. **Read the verification.** The script re-derives the two derived files and then asks the same
   offline questions the release build's smoke gate asks: everything compiles, the check engine
   self-tests, the rep engine's step registry loads, every task still dry-runs, the progress
   board's references resolve. Any `FAIL` and it prints the `--restore` command — use it rather
   than debugging a half-merged tree.
7. **Report.** What changed, what needs their attention, and anything the new version wants of
   them that a file merge cannot do: new `.env` variables (the merge adds them to
   `.env.example`, but their `.env` is untouched — name them), new `pip install -r
   code/requirements.txt`, new topics with no folders yet (`--scaffold`), and any prose drift in
   their capability specs.

## Resolving a conflict — the defaults

Apply these unless the diff says otherwise. State which one you used.

- **`code/`** — upstream wins. The toolkit is LLM-owned and shared; a member's local fix is
  usually the same bug we just fixed properly. **Unless** their edit is a real customisation
  (their own function, their own endpoint, a pin they need) — then merge both sides by hand.
- **`.claude/skills/**` and `CLAUDE.md`** — upstream wins. This is the framework's own
  machinery and the schema; a stale skill is the thing the update exists to fix. Carry across
  any local edit that is genuinely theirs (a note they added to CLAUDE.md).
- **`1_agent-gym/<topic>/AG-*/`** — upstream wins. Tasks are ours; `task.md`, `validate.json`
  and `provision.py` are a contract, and a member's edit to a grader means their passing reps
  proved something other than the task. Say so plainly if you overwrite one.
- **`1_agent-gym/<topic>/index.md`** — hand-merge. It is generated per bundle, but
  `create-gym-task` writes the member's own tasks into it. Take upstream's prose, re-add their
  rows.
- **`.gitignore` · `.env.example` · `requirements.txt`** — additive. The first two merge
  automatically; for `requirements.txt` take upstream's pins and keep any line only they have.
- **`0_admin/references/`** — upstream wins (page templates).
- **Anything in `2_raw/` or `3_wiki/`** — theirs, always. Never a conflict to resolve: report
  that a newer seed page exists and leave it. If they want it, that is a `fabric-ingest`
  decision, not a file copy.

## Refusals, and why

- **A different community.** Data-eng and decision-eng are different curricula with different
  topic folders; merging one into the other cross-contaminates both. Start a fresh clone.
  **The one sanctioned cross: VIP.** A `community: both` bundle is the union of the two
  tracks, so it merges onto *either* premium repo as a tier upgrade (`vip` sits above
  `premium` on the ladder) — the other track's topics, tasks and toolkit arrive as adds, and
  a single-track bundle can never land on a VIP repo (that would be a tier downgrade).
- **A lower tier.** Merging down adds nothing, and `--prune` would delete what they paid for.
- **An older version.** Nothing in it moves them forward.
- **Not a bundle.** No `BUNDLE.json` and no `CLAUDE.md` at the root of what they pointed at.
  A source with a `CLAUDE.md` but no `BUNDLE.json` still works — every file is compared by
  content, nothing is deleted, and the plan says so.

## Ported repos

If the member ran `port-project-to-another-model`, the skills live in `.agents/skills/` and the
root doc is `AGENTS.md` or `GEMINI.md`. The script detects that and **ports a copy of the
incoming bundle first** — running the bundle's own `port.py` with the target sniffed from the
member's root doc — so both trees compare in the same shape: paths align, the port's rewrites
appear on both sides (an unedited file is byte-identical, not a false conflict), and the
manifest it stamps is re-hashed from the ported bytes so the next update is exact. If the
source carries no `port.py`, it falls back to per-file path remapping. Either way the root doc
becomes a conflict rather than an overwrite: after a port, that file is the **authored
master**, so upstream's `CLAUDE.md` changes need hand-merging into it. The first update after
a port may show a few conflicts on files that genuinely changed upstream — the pre-port
baseline cannot vouch for them — and is exact from then on.

## What it records

One line per applied update in `0_admin/logs/framework-updates.jsonl` — versions, baseline
rung, counts, the review list, any failed check. A record, not knowledge: never cite it in
`3_wiki/`, never hand-edit it.
