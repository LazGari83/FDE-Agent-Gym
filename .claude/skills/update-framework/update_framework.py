#!/usr/bin/env python3
"""update_framework.py — merge a newer framework bundle into this repository.

WHAT THIS IS

A member's repo is two things mixed in one tree: the FRAMEWORK we shipped them (the schema,
the toolkit, the skills, the gym tasks, the syllabus) and THEIR WORK (wiki pages, raw
evidence, rep reports, their own tasks and code). A framework update has to replace the first
without ever touching the second — and the member is the only one who knows which of their
edits to a framework file was deliberate.

So this script decides nothing it cannot prove. It classifies every path, writes only the
classes that are provably safe, and hands the rest to the agent and the member as a review
list. The classification rests on one fact: `BUNDLE.json` records the sha256 of every file as
we shipped it. Local file == recorded hash means the member never edited it, so the new
version can land. Local file != recorded hash means they did, and nothing overwrites it
without them saying so.

THE BASELINE LADDER (printed on every run, because it decides how cautious the plan is)

    bundle-hashes   `BUNDLE.json` carries `sha256` per file — exact, both ways. Bundles from
                    v1.3 on. The only rung that lets a changed file be replaced automatically.
    git-history     no hashes, but the repo is a git repo. Detects EDITS only: dirty in the
                    worktree, or touched by more than the import commit, means edited. It never
                    reports the opposite, because a member who commits the bundle and their own
                    changes together has both in one commit, and reading that as untouched
                    overwrites their work.
    none            neither. Same outcome as above for anything that changed upstream.

    On the two weaker rungs a changed file goes to review rather than being written. That is
    correct, not broken: with no record of what we shipped, "unedited" is a guess, and a wrong
    guess destroys the work the product exists to accumulate. New files still land normally —
    a file the member does not have cannot be their work.

THE ZONES (what may be written, and what may never be)

    never, whatever the flags say
        2_raw/**            their evidence layer
        3_wiki/**           their pages, atoms, decisions, log and router — including the seed
                            cluster we shipped, which their ingests rewrite
        .env                their tenant
        0_admin/logs/**     append-only ledgers; a copied ledger is a lost ledger
        anything not in either BUNDLE.json — a file we never shipped is theirs by definition

    re-derived, never copied
        1_agent-gym/tracker.md · 0_admin/capabilities/coverage.md — computed from their own
        filesystem by their own tools. Copying ours would import someone else's numbers.

    merged, never overwritten
        .env.example · .gitignore        block union, keyed on the var / the pattern
        0_admin/capabilities/<topic>.md  new rows appended by id; existing rows untouched

    replaced when untouched, reviewed when edited
        everything else — CLAUDE.md, code/, .claude/skills/, 1_agent-gym/<topic>/AG-*/,
        0_admin/references/, README.md, LICENCE, requirements.txt

`--scaffold` is the one flag that writes inside `2_raw/` or `3_wiki/`, and all it can do is
create an empty topic folder that does not exist yet. Off by default.

USAGE

    python .claude/skills/update-framework/update_framework.py <zip-or-folder>
    python .claude/skills/update-framework/update_framework.py <src> --diff        # + the diffs
    python .claude/skills/update-framework/update_framework.py <src> --apply       # write
    python .claude/skills/update-framework/update_framework.py <src> --apply \
        --only code/core/auth.py CLAUDE.md      # a resolved conflict, applied by name
    python .claude/skills/update-framework/update_framework.py --restore latest
    python .claude/skills/update-framework/update_framework.py --self-test

EXIT  0 the plan printed, or the update applied and verified · 1 a guard refused the source,
      or the post-update verification failed (the backup path is printed)
"""

import argparse
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

# Shared console setup and path resolution live in <skills>/_shared/kbmd.py. The insert is
# __file__-relative, never cwd-relative: this runs from the repo root and from its own folder,
# and it keeps working after port-project-to-another-model moves the tree to .agents/skills/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_shared"))
from kbmd import REPO_ROOT, utf8_stdout                                     # noqa: E402

SKILL_DIR = Path(__file__).resolve().parent
LEDGER = "framework-updates.jsonl"
BACKUPS = ".framework-backup"

# Written by nothing here, whatever a manifest or a flag says. `.git/` is on the list because a
# bundle has no business carrying one and a merged history is not a merge.
PROTECTED_PREFIXES = ("2_raw/", "3_wiki/", "0_admin/logs/", ".git/", ".claude/worktrees/",
                      BACKUPS + "/")
PROTECTED_FILES = (".env", ".preflight-cache.json")

# Computed from the member's own filesystem by tools that ship in the box. Ours describe our
# tree: a copied tracker tells a member they have done reps they have not.
DERIVED_FILES = ("1_agent-gym/tracker.md", "0_admin/capabilities/coverage.md")

# The human-owned syllabus. Row-append only — see merge_capability_spec.
SPEC_RE = re.compile(r"^0_admin/capabilities/(?!README\.md|coverage\.md)[a-z0-9._-]+\.md$")

TIERS = ("trial", "standard", "premium", "vip")

# Class -> (writes?, one-line meaning). The order here is the order the report prints.
CLASSES = {
    "add":         (True,  "new in this version — not in your repo yet"),
    "update":      (True,  "changed upstream, and you never edited it"),
    "merge-lines": (True,  "merged: upstream blocks appended, your lines kept"),
    "merge-spec":  (True,  "merged: new capability rows appended, yours untouched"),
    "conflict":    (False, "changed upstream AND edited here — needs a decision"),
    "unknown":     (False, "changed upstream, no baseline to judge your copy against"),
    "keep":        (False, "you edited it; upstream did not change it"),
    "gone":        (False, "dropped upstream — delete only with --prune"),
    "rederive":    (False, "re-derived from your filesystem after the merge"),
    "protected":   (False, "differs upstream; this zone is never written"),
    "scaffold":    (True,  "empty topic folder for a topic this version adds"),
    "same":        (False, "identical"),
}


# ── source ────────────────────────────────────────────────────────────────────

def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def resolve_source(arg, keep):
    """(root of the incoming bundle, temp dir to clean up or None).

    Accepts the two things a member actually has: the zip they downloaded, or the folder they
    unzipped it into. A zip is extracted somewhere temporary — never into their repo, where a
    half-extracted tree would look like content.
    """
    # Absolute from the start: the handoff re-invokes a child with cwd set to the TARGET
    # repo, where a relative source path would resolve against the wrong directory.
    src = Path(arg).expanduser().resolve()
    if not src.exists():
        raise SystemExit(f"refused: no such path — {src}")

    tmp = None
    if src.is_file() and src.suffix.lower() != ".zip":
        raise SystemExit(f"refused: {src.name} is a file but not a .zip. Point this at the "
                         f"bundle zip or the folder you unzipped it into.")
    try:
        if src.is_file():
            tmp = Path(tempfile.mkdtemp(prefix="fabric-kb-update-"))
            with zipfile.ZipFile(src) as zf:
                for name in zf.namelist():
                    # A downloaded archive is untrusted input. Python sanitises these
                    # already; refusing loudly beats extracting a surprise quietly.
                    if name.startswith(("/", "\\")) or ".." in Path(name).parts or ":" in name:
                        raise SystemExit(f"refused: {src.name} contains an unsafe path — {name}")
                zf.extractall(tmp)
            if keep:
                # The whole point of --keep-temp: the member can open the source's copy of a
                # conflicted file beside their own. Useless unless the path is said out loud.
                print(f"extracted to {tmp}")
            root = tmp
        else:
            root = src

        # The zip holds `<bundle>/...`; an unzipped folder may or may not. Find the tree that
        # has a BUNDLE.json, or — for a source built before the manifest existed — a CLAUDE.md.
        for marker in ("BUNDLE.json", "CLAUDE.md"):
            if (root / marker).exists():
                return root, tmp
            hits = sorted(root.glob(f"*/{marker}")) + sorted(root.glob(f"*/*/{marker}"))
            if len(hits) == 1:
                return hits[0].parent, tmp
            if len(hits) > 1:
                raise SystemExit(f"refused: {len(hits)} bundles under {src} — point this at "
                                 f"one of them: " + ", ".join(str(h.parent.name) for h in hits[:4]))
        raise SystemExit(f"refused: {src} does not look like a framework bundle (no "
                         f"BUNDLE.json and no CLAUDE.md at its root)")
    except SystemExit:
        # A refusal must not leak the extraction: main()'s cleanup only guards the success
        # path, since the (source, tmp) pair never reaches it from here.
        if tmp and not keep:
            shutil.rmtree(tmp, ignore_errors=True)
        raise


def read_manifest(root):
    path = root / "BUNDLE.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"refused: {path} is not readable JSON ({exc})")


def manifest_paths(manifest):
    """{path -> entry} from a BUNDLE.json, or {} — the framework's own idea of what it shipped."""
    return {e["path"]: e for e in (manifest or {}).get("contents", []) if e.get("path")}


# ── identity: is this bundle even a successor to that one? ────────────────────

def check_identity(local, incoming, force):
    """Refuse the updates that are not updates. Returns (mode, notes)."""
    notes, problems = [], []
    if not incoming:
        if (local or {}).get("version"):
            # The likely story here is swapped arguments: the member ran the NEW bundle's
            # updater and pointed it at their OLD repo, so "the repo" resolved to the bundle
            # itself and this plan would overwrite the fresh download with their old files.
            problems.append(
                f"the source carries no BUNDLE.json, but this repo is already version "
                f"{local['version']} — an unversioned source is by definition older (1.2 or "
                f"earlier), so nothing in it moves you forward. If you meant to update your "
                f"repo, the source argument must be the NEW bundle:\n"
                f"    cd <your repo>\n"
                f"    python <this script> <path to the new bundle zip or folder>")
        else:
            notes.append("the source carries no BUNDLE.json — every path is compared by "
                         "content, and nothing is ever deleted")
        if problems and force:
            notes.extend("--force overrode: " + p for p in problems)
            problems = []
        return "unversioned", notes, problems
    if not local:
        notes.append("this repo carries no BUNDLE.json — it was cloned rather than unzipped, "
                     "or predates the manifest. Nothing is deleted, and every changed file "
                     "goes to review.")

    lv, iv = (local or {}).get("version"), incoming.get("version")
    lb, ib = (local or {}).get("bundle"), incoming.get("bundle")
    lc, ic = (local or {}).get("community"), incoming.get("community")
    lt, it = (local or {}).get("tier"), incoming.get("tier")

    # `both` is the VIP community — the union of the two tracks — so it is compatible with
    # either of them: a single-track member merging the VIP bundle in is the sanctioned
    # cross-community upgrade. Two different SINGLE tracks stay refused.
    if lc and ic and lc != ic and "both" not in (lc, ic):
        problems.append(f"community mismatch: your repo is `{lc}`, the source is `{ic}`. These "
                        f"are different curricula with different topic folders — a merge would "
                        f"cross-contaminate both. Start a fresh clone for the other track.")
    elif lc and ic and lc != ic and ic == "both":
        notes.append(f"cross-track upgrade: `{lc}` -> `both` (VIP). The other track's topics, "
                     f"tasks and toolkit arrive as adds; everything you built stays put.")
    if lt in TIERS and it in TIERS and TIERS.index(it) < TIERS.index(lt):
        problems.append(f"tier downgrade: your repo is `{lt}`, the source is `{it}`. Merging "
                        f"down cannot add anything and `--prune` would delete what you paid "
                        f"for. Point this at a {lt} bundle.")

    mode = "update"
    if lb and ib and lb != ib and not problems:
        mode = "upgrade"
        notes.append(f"tier upgrade: `{lb}` -> `{ib}`. Additive by construction (`extends`), "
                     f"so expect a large `add` list.")
    if lv and iv and lv == iv and mode != "upgrade":
        # A tier upgrade at the same version number is still an upgrade, not drift.
        mode = "same-version"
        notes.append(f"both sides are version {iv} — this is a drift check, not an update.")
    elif lv and iv and _older(iv, lv):
        problems.append(f"version {iv} is older than the {lv} you already have. Nothing here "
                        f"moves you forward.")

    if problems and force:
        notes.extend("--force overrode: " + p for p in problems)
        problems = []
    return mode, notes, problems


def _older(a, b):
    """`a` sorts before `b` as a dotted version. Unparseable parts compare as text."""
    def key(v):
        return [int(p) if p.isdigit() else p for p in re.split(r"[.\-+]", str(v))]
    try:
        return key(a) < key(b)
    except TypeError:
        return False


# ── the baseline ladder ──────────────────────────────────────────────────────

class Baseline:
    """Answers one question: did the member edit this file after we shipped it?

    `untouched(path, digest)` returns True, False, or None for "no way to know" — and the
    caller must treat None as "do not overwrite". Which rung answered is printed, because a
    plan built on `git-history` deserves more of the member's attention than one built on
    recorded hashes.
    """

    def __init__(self, repo, local_manifest):
        self.hashes = {p: e["sha256"] for p, e in manifest_paths(local_manifest).items()
                       if e.get("sha256")}
        self.repo = repo
        self.dirty, self.touch_counts = set(), {}
        self.git = _git_ok(repo)
        if self.git:
            self.dirty = _git_dirty(repo)
            if not self.hashes:
                self.touch_counts = _git_touch_counts(repo)
        self.rung = ("bundle-hashes" if self.hashes else
                     "git-history" if self.touch_counts else "none")

    def untouched(self, rel, digest):
        if self.rung == "bundle-hashes":
            shipped = self.hashes.get(rel)
            return None if shipped is None else shipped == digest
        if self.rung == "git-history":
            # This rung can prove a file WAS edited. It cannot prove one was not, and must
            # never claim to: the member who unzips the bundle, tinkers, and only then runs
            # their first `git add -A` has the bundle and their own edits in ONE commit. A
            # count of 1 therefore means "imported, possibly with their changes baked in" —
            # indistinguishable from untouched, and reading it as untouched overwrites exactly
            # the work this skill exists to protect. Returning None sends it to review instead.
            if rel in self.dirty:
                return False
            count = self.touch_counts.get(rel)
            return False if count is not None and count > 1 else None
        return None


def _git(repo, *args, ok=(0,)):
    try:
        run = subprocess.run(["git", "-c", "core.quotepath=false", *args], cwd=repo,
                             capture_output=True, text=True, encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return None
    return run.stdout if run.returncode in ok else None


def _git_ok(repo):
    return (_git(repo, "rev-parse", "--is-inside-work-tree") or "").strip() == "true"


def _git_dirty(repo):
    out = _git(repo, "status", "--porcelain") or ""
    paths = set()
    for line in out.splitlines():
        entry = line[3:].strip().strip('"')
        paths.update(p.strip().strip('"') for p in entry.split(" -> ") if p.strip())
    return paths


def _git_touch_counts(repo):
    """{path -> number of commits that touched it}. One `git log`, not one per file."""
    out = _git(repo, "log", "--format=%x00", "--name-only", "HEAD")
    if out is None:
        return {}
    counts = {}
    for line in out.splitlines():
        line = line.strip().strip('"')
        if line and line != "\x00":
            counts[line] = counts.get(line, 0) + 1
    return counts


# ── the ported-layout remap ──────────────────────────────────────────────────

def remapper(repo):
    """Path rewriter for a repo that ran port-project-to-another-model.

    That migration MOVES `.claude/skills/` to `.agents/skills/` and rewrites `CLAUDE.md` into
    `AGENTS.md` / `GEMINI.md` as the authored master. An incoming bundle is still Claude-shaped,
    so without this every skill file would land in a `.claude/` tree the runtime no longer
    scans, and the member's update would appear to do nothing.
    """
    if (repo / ".claude" / "skills").exists() or not (repo / ".agents" / "skills").exists():
        return (lambda p: p), None
    root_doc = next((n for n in ("AGENTS.md", "GEMINI.md") if (repo / n).exists()), None)

    def remap(rel):
        if rel.startswith(".claude/skills/"):
            return ".agents/skills/" + rel[len(".claude/skills/"):]
        if rel == "CLAUDE.md" and root_doc:
            return root_doc
        return rel
    return remap, root_doc


def detect_port_target(repo, root_doc):
    """The port target the member chose, sniffed from their root doc.

    `GEMINI.md` is unambiguous. The three `AGENTS.md` runtimes are told apart by the runtime
    name port.py wrote into the doc's guidance line; a wrong guess only changes the root
    doc's prose, which is a hand-merge conflict by design either way.
    """
    if root_doc == "GEMINI.md":
        return "gemini"
    text = _read(repo / root_doc) or ""
    for target, runtime in (("copilot", "GitHub Copilot"), ("codex", "OpenAI Codex"),
                            ("cursor", "Cursor")):
        if runtime in text:
            return target
    return "codex"


def port_source(source, repo, root_doc, keep):
    """Port a COPY of the incoming bundle to the member's layout, so the merge compares
    like with like.

    A ported repo went through port-project-to-another-model: the skills tree moved, and
    every text file's legacy-path and root-doc references were rewritten. Comparing the
    Claude-shaped bundle against that tree misreads the port as member edits (false
    conflicts on every rewritten file) and misreads the moved paths as files dropped
    upstream (false `gone` — which --prune would then delete). The honest comparison is to
    run the SAME migration on the source first, using the bundle's own port.py — it ships
    at every tier and is about to be installed anyway, the handoff's trust argument. The
    incoming manifest is then re-hashed from the ported bytes, so the baseline this update
    stamps matches what actually lands and the NEXT update is exact too.

    Always works on a fresh copy — never the member's unzipped folder, and never the
    extracted tree the fallback path would still need. Returns (source root, temp dir to
    clean or None, manifest or None, ported?); on any failure the original source comes
    back untouched and the caller falls back to path remapping.
    """
    portpy = source / ".claude" / "skills" / "port-project-to-another-model" / "port.py"
    if not portpy.is_file():
        return source, None, None, False
    target = detect_port_target(repo, root_doc)
    work = Path(tempfile.mkdtemp(prefix="fabric-kb-update-ported-"))
    ported = work / (source.name or "bundle")
    try:
        shutil.copytree(source, ported)
        run = subprocess.run(
            [sys.executable,
             str(ported / ".claude" / "skills" / "port-project-to-another-model" / "port.py"),
             "--target", target],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError) as exc:
        run = subprocess.CompletedProcess([], 1, "", str(exc))
    if run.returncode != 0:
        tail = [l for l in (run.stdout + run.stderr).strip().splitlines() if l.strip()][-1:]
        print(f"  warn: could not port the source bundle ({(tail or ['no output'])[0][:120]})"
              f" — falling back to path remapping")
        shutil.rmtree(work, ignore_errors=True)
        return source, None, None, False
    manifest = read_manifest(ported)
    for entry in (manifest or {}).get("contents", []):
        emitted = ported / entry["path"]
        if entry.get("sha256") and emitted.is_file():
            entry["sha256"] = sha256(emitted)
    if keep:
        print(f"  ported source kept at {ported}")
    return ported, work, manifest, True


def rewrite_ported_text(path, root_doc):
    """Fix the path references inside a file that just landed in a ported tree.

    The file we copied says `.claude/skills/...` in its prose and its commands. port.py did
    this rewrite for the files it moved; an update has to do it for the files it brings.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return False
    fixed = text.replace(".claude/skills/", ".agents/skills/")
    if root_doc:
        fixed = fixed.replace("CLAUDE.md", root_doc)
    if fixed != text:
        path.write_text(fixed, encoding="utf-8", newline="")
        return True
    return False


# ── the merges ───────────────────────────────────────────────────────────────

def _blocks(text):
    """A file split into blank-line-separated blocks, so a var keeps its comment."""
    out, cur = [], []
    for line in text.splitlines():
        if line.strip():
            cur.append(line)
        elif cur:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


_ENV_VAR = re.compile(r"^#?\s*([A-Z][A-Z0-9_]*)=")


def env_key(line):
    """The variable a `.env.example` line declares, commented out or not."""
    found = _ENV_VAR.match(line)
    return found.group(1) if found else None


def ignore_key(line):
    """The pattern a `.gitignore` line declares. Comments key on nothing, so they travel
    with the pattern block below them instead of being compared."""
    return None if line.lstrip().startswith("#") else (line.strip() or None)


def merge_blocks(local, incoming, key_of, banner):
    """Append whole blocks from `incoming` that declare a key `local` does not have.

    Additive by construction: the member's file is prefix-identical afterwards. A block rather
    than a line, because `.env.example` explains each variable above it and a bare `VAR=` a
    member cannot interpret is worse than no line at all. A new key living in a block that
    ALSO holds a key the member already has is not appended — splitting the block would strand
    it from its comment — but it is returned in `partial` so the plan can name it instead of
    losing it silently.

    Returns (merged text | None, keys appended, keys needing a hand-merge).
    """
    have = {k for line in local.splitlines() for k in [key_of(line)] if k}
    new, partial = [], []
    for block in _blocks(incoming):
        keys = [k for line in block for k in [key_of(line)] if k]
        fresh = [k for k in keys if k not in have]
        if not fresh:
            continue
        if set(keys) & have:
            partial.extend(fresh)
        else:
            new.append(block)
    if not new:
        return None, [], partial
    tail = "\n".join("\n".join(b) for b in new)
    body = local if local.endswith("\n") else local + "\n"
    added = [k for b in new for line in b for k in [key_of(line)] if k]
    return f"{body}\n{banner}\n{tail}\n", added, partial


def _table(text):
    """(start, end, header, rows) of the first markdown table whose first column is `id`."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells and cells[0].lower() == "id" and i + 1 < len(lines) and set(
                lines[i + 1].strip()) <= set("|-: "):
            end = i + 2
            while end < len(lines) and lines[end].lstrip().startswith("|"):
                end += 1
            return i, end, cells, lines[i + 2:end]
    return None


def _row_cells(row):
    return [c.strip() for c in row.strip().strip("|").split("|")]


def _row_id(row):
    cells = _row_cells(row)
    return cells[0] if cells else ""


def merge_capability_spec(local, incoming):
    """Append the rows this version adds. Never reword, retire, reorder or drop one.

    `0_admin/capabilities/` is human-owned (CLAUDE.md, Conventions): the KB's owner writes it,
    and no unattended run edits it. An update is the one sanctioned exception and it is
    deliberately the weakest possible edit — new ids appended to the end of the table, on a run
    the owner asked for and confirmed. Everything else the diff shows (a reworded row, a
    retirement, changed prose above the table) is reported for them to apply by hand.

    Returns (text|None, report dict).
    """
    lt, it = _table(local), _table(incoming)
    report = {"added": [], "yours_only": [], "reworded": [], "prose_changed": False,
              "reason": None}
    if not lt or not it:
        report["reason"] = "no `id` table found on one side"
        return None, report
    l_start, l_end, l_head, l_rows = lt
    i_start, i_end, i_head, i_rows = it
    if [h.lower() for h in l_head] != [h.lower() for h in i_head]:
        # A tier upgrade changes the render (trimmed -> full), so the columns differ and a row
        # append would produce a ragged table. Upstream is the superset; the member re-adds
        # their own rows, which we name for them.
        report["reason"] = (f"the columns changed ({len(l_head)} -> {len(i_head)}): "
                            f"{', '.join(i_head)}")
        report["yours_only"] = sorted({_row_id(r) for r in l_rows} - {_row_id(r) for r in i_rows})
        return None, report

    have = {_row_id(r) for r in l_rows}
    new = [r for r in i_rows if _row_id(r) and _row_id(r) not in have]
    report["added"] = [_row_id(r) for r in new]
    report["yours_only"] = sorted(have - {_row_id(r) for r in i_rows} - {""})
    # A row both sides have whose cells differ was reworded upstream. Never applied — the
    # spec is human-owned — but it must be NAMED, or the owner has no way to know it exists.
    l_by = {_row_id(r): _row_cells(r) for r in l_rows if _row_id(r)}
    i_by = {_row_id(r): _row_cells(r) for r in i_rows if _row_id(r)}
    report["reworded"] = sorted(k for k in l_by.keys() & i_by.keys() if l_by[k] != i_by[k])
    l_lines, i_lines = local.splitlines(), incoming.splitlines()
    report["prose_changed"] = (l_lines[:l_start] + l_lines[l_end:]
                               != i_lines[:i_start] + i_lines[i_end:])
    if not new:
        return None, report
    merged = l_lines[:l_end] + sorted(new, key=_row_id) + l_lines[l_end:]
    return "\n".join(merged) + ("\n" if local.endswith("\n") else ""), report


# ── the plan ─────────────────────────────────────────────────────────────────

def zone(rel):
    """The zone a path sits in, before anything is compared."""
    if rel in DERIVED_FILES:
        return "rederive"
    if Path(rel).name in PROTECTED_FILES:      # at the root or anywhere below it
        return "protected"
    if rel.startswith(PROTECTED_PREFIXES):
        return "protected"
    if rel in (".env.example", ".gitignore"):
        return "merge-lines"
    if SPEC_RE.match(rel):
        return "merge-spec"
    return "normal"


def build_plan(repo, source, local_manifest, incoming_manifest, baseline, remap, scaffold):
    """One classified item per path, over the union of both manifests and the source tree."""
    incoming = manifest_paths(incoming_manifest)
    if not incoming:                                    # unversioned source: walk it instead
        incoming = {p.relative_to(source).as_posix(): {"from": "unknown"}
                    for p in source.rglob("*")
                    if p.is_file() and "__pycache__" not in p.parts
                    and p.name not in ("BUNDLE.json", ".DS_Store")}
    local = manifest_paths(local_manifest)
    items, merges = [], {}

    for rel, entry in sorted(incoming.items()):
        if rel == "BUNDLE.json":
            continue
        src = source / rel
        if not src.is_file():                           # a manifest entry with no file behind it
            continue
        dest_rel = remap(rel)
        dest = repo / dest_rel
        z = zone(dest_rel)
        item = {"path": dest_rel, "source": rel, "from": entry.get("from", "?"), "zone": z}

        if z == "rederive":
            items.append({**item, "cls": "rederive"})
            continue

        digest = sha256(dest) if dest.is_file() else None
        if digest and digest == (sha256(src) if src.is_file() else None):
            items.append({**item, "cls": "same"})
            continue

        if not dest.exists():
            # Nothing to protect: a file the member does not have cannot be their work. The one
            # exception is a protected zone, where an absent file is a page they deleted or
            # never wrote, and re-adding our copy would put content back under their name.
            items.append({**item, "cls": "protected" if z == "protected" else "add"})
            continue

        if z == "protected":
            items.append({**item, "cls": "protected"})
            continue

        untouched = baseline.untouched(dest_rel, digest)

        if z in ("merge-lines", "merge-spec"):
            # A merge is for a file the member has made theirs. If they never touched it there
            # is nothing to preserve, and a clean replace is strictly better: it keeps the file
            # byte-identical to what we shipped, so the NEXT update can still prove it untouched.
            # A merged file can never match a shipped hash again.
            if untouched is True:
                items.append({**item, "cls": "update", "note": "you never edited it"})
                continue
            text, inc_text = _read(dest), _read(src)
            if text is None or inc_text is None:
                items.append({**item, "cls": "conflict", "note": "not text"})
                continue
            if z == "merge-lines":
                key = env_key if dest_rel == ".env.example" else ignore_key
                banner = f"# ── added by update-framework " \
                         f"{(incoming_manifest or {}).get('version', 'update')} ──"
                merged, added, partial = merge_blocks(text, inc_text, key, banner)
                stranded = (f" · not auto-merged (shares a block with an entry you have): "
                            f"{', '.join(partial[:6])}" if partial else "")
                if merged is None:
                    items.append({**item, "cls": "keep",
                                  "note": "no new entries upstream" + stranded})
                else:
                    merges[dest_rel] = merged
                    items.append({**item, "cls": "merge-lines",
                                  "note": f"appends {len(added)}: {', '.join(added[:6])}"
                                          + (" …" if len(added) > 6 else "") + stranded})
            else:
                merged, rep = merge_capability_spec(text, inc_text)
                note = (f"appends {len(rep['added'])} row(s): {', '.join(rep['added'][:8])}"
                        if rep["added"] else rep["reason"] or "no new rows")
                if rep["reworded"]:
                    note += (f" · reworded upstream, NOT applied — yours to decide: "
                             f"{', '.join(rep['reworded'][:6])}"
                             + (" …" if len(rep["reworded"]) > 6 else ""))
                if rep["prose_changed"]:
                    note += " · the prose above the table also changed upstream"
                if rep["yours_only"]:
                    note += f" · rows only in yours, kept: {', '.join(rep['yours_only'][:6])}"
                if merged is None:
                    items.append({**item, "cls": "keep" if not rep["reason"] else "conflict",
                                  "note": note})
                else:
                    merges[dest_rel] = merged
                    items.append({**item, "cls": "merge-spec", "note": note})
            continue

        if untouched is True:
            items.append({**item, "cls": "update"})
        elif untouched is False:
            items.append({**item, "cls": "conflict"})
        else:
            items.append({**item, "cls": "unknown"})

    # Dropped upstream: in the manifest we shipped, absent from the new one. Compared in the
    # REPO's layout: on a ported repo the local manifest names `.agents/...` while a
    # Claude-shaped incoming names `.claude/...` — raw string comparison would read the
    # member's whole live skills tree as dropped, and --prune would then delete it.
    incoming_here = {remap(rel) for rel in incoming}
    for rel in sorted(set(local) - incoming_here):
        if rel == "BUNDLE.json":
            continue
        dest_rel = remap(rel)
        dest = repo / dest_rel
        if not dest.is_file() or zone(dest_rel) != "normal":
            continue
        untouched = baseline.untouched(dest_rel, sha256(dest))
        items.append({"path": dest_rel, "source": None, "from": "-", "zone": "normal",
                      "cls": "gone",
                      "note": "you edited it" if untouched is False else
                              "matches what we shipped" if untouched else "no baseline"})

    # Empty topic folders for topics this version adds. The only thing that ever writes inside
    # 2_raw/ or 3_wiki/, it only ever creates a directory, and it is opt-in.
    for folder in sorted(_scaffold_dirs(source)):
        if not (repo / folder).exists():
            items.append({"path": folder + "/", "source": None, "from": "scaffold",
                          "zone": "scaffold", "cls": "scaffold" if scaffold else "protected",
                          "note": "empty folder" + ("" if scaffold else " — needs --scaffold")})
    return items, merges


def _scaffold_dirs(source):
    return {p.parent.relative_to(source).as_posix() for p in source.rglob(".gitkeep")
            if p.parent.relative_to(source).as_posix().startswith(("2_raw/", "3_wiki/"))}


def _read(path):
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


# ── the report ───────────────────────────────────────────────────────────────

def report(items, local_manifest, incoming_manifest, baseline, notes,
           show_diff, show_all, repo, source, root_doc):
    lv = (local_manifest or {}).get("version", "unrecorded")
    iv = (incoming_manifest or {}).get("version", "unversioned")
    print(f"\n  {(local_manifest or {}).get('bundle', '?')} {lv}"
          f"  ->  {(incoming_manifest or {}).get('bundle', '?')} {iv}")
    print(f"  baseline: {baseline.rung}"
          f"{'  (exact)' if baseline.rung == 'bundle-hashes' else ''}")
    if root_doc:
        print(f"  ported layout: skills -> .agents/skills/, root doc -> {root_doc}")
    for note in notes:
        print(f"  note: {note}")

    by_cls = {}
    for item in items:
        by_cls.setdefault(item["cls"], []).append(item)
    print()
    for cls, (writes, meaning) in CLASSES.items():
        rows = by_cls.get(cls, [])
        if not rows or cls == "same":
            continue
        print(f"  {'WRITE ' if writes else 'REVIEW'}  {cls:<12} {len(rows):>4}   {meaning}")
    print(f"  {'      '}  {'same':<12} {len(by_cls.get('same', [])):>4}   identical\n")

    limit = 10_000 if show_all else 25
    for cls in CLASSES:
        rows = by_cls.get(cls, [])
        if not rows or cls == "same":
            continue
        print(f"── {cls} ({len(rows)}) ──")
        for item in rows[:limit]:
            print(f"   {item['path']}" + (f"   [{item['note']}]" if item.get("note") else ""))
        if len(rows) > limit:
            print(f"   … and {len(rows) - limit} more (--all)")
        print()

    if show_diff:
        # merge-spec is included because its note can name reworded rows the merge will not
        # apply — the diff is the only place the owner can actually read them. A spec with
        # nothing to append lands in `keep`, so spec-zone keeps are included for the same
        # reason; merge-lines keeps ("no new entries upstream") stay out.
        for cls in ("conflict", "unknown", "protected", "update", "merge-spec", "keep"):
            for item in by_cls.get(cls, []):
                if cls == "keep" and item.get("zone") != "merge-spec":
                    continue
                if not item.get("source"):
                    continue
                mine, theirs = _read(repo / item["path"]), _read(source / item["source"])
                if mine is None or theirs is None:
                    continue
                diff = list(difflib.unified_diff(
                    mine.splitlines(), theirs.splitlines(),
                    fromfile=f"yours/{item['path']}", tofile=f"{iv}/{item['source']}",
                    lineterm="", n=2))
                if diff:
                    print(f"── diff [{cls}] {item['path']} ──")
                    print("\n".join(diff[:400] if not show_all else diff))
                    if len(diff) > 400 and not show_all:
                        print(f"   … {len(diff) - 400} more diff lines (--all)")
                    print()
    return by_cls


# ── apply ────────────────────────────────────────────────────────────────────

def apply(repo, source, items, merges, only, prune, root_doc, incoming_manifest, stamp):
    """Write the safe classes (plus anything `--only` names), backing up every file first.

    The backup is what makes this reversible for a member who does not use git: every file
    modified or deleted is copied to `.framework-backup/<stamp>/`, alongside a `restore.json`
    that also records the files we ADDED, so `--restore` can take them away again.
    """
    backup = repo / BACKUPS / stamp
    actions, written, deleted, failed = [], [], [], []
    chosen = {i["path"] for i in items if CLASSES[i["cls"]][0]}
    if prune:
        # `gone` is a review class, so it is never in the write set — deleting it is what
        # --prune opts into, so the flag is what puts those paths on the worklist.
        chosen |= {i["path"] for i in items if i["cls"] == "gone"}
    if only:
        chosen = {p.replace("\\", "/") for p in only}
        # A mistyped `--only` would otherwise write nothing and report success — the one
        # outcome indistinguishable from "there was nothing to do".
        for missing in sorted(chosen - {i["path"] for i in items}):
            failed.append((missing, "no such path in this update's plan"))

    for item in items:
        rel = item["path"]
        if rel not in chosen:
            continue
        if zone(rel.rstrip("/")) == "protected" and item["cls"] != "scaffold":
            failed.append((rel, "protected zone — this script never writes here"))
            continue
        if item["cls"] == "rederive":
            # Reachable only via --only, so the member named it — tell them why nothing landed.
            failed.append((rel, "re-derived from your filesystem after the merge, never copied"))
            continue
        dest = repo / rel

        if item["cls"] == "scaffold":
            dest.mkdir(parents=True, exist_ok=True)
            (dest / ".gitkeep").write_text("", encoding="utf-8")
            actions.append({"path": rel, "action": "added"})
            written.append(rel)
            continue

        if item["cls"] == "gone":
            if not prune:
                # Reachable only via --only (prune puts gone paths in `chosen` itself).
                failed.append((rel, "dropped upstream — deleting it needs --prune"))
                continue
            _backup(repo, dest, backup)
            actions.append({"path": rel, "action": "deleted"})
            dest.unlink()
            deleted.append(rel)
            continue

        existed = dest.is_file()
        if existed:
            _backup(repo, dest, backup)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if rel in merges:
            _write_like(dest, merges[rel])
        else:
            src = source / (item["source"] or rel)
            if not src.is_file():
                failed.append((rel, "no such file in the source bundle"))
                continue
            shutil.copy2(src, dest)
            if root_doc and dest.suffix in (".md", ".py", ".json"):
                rewrite_ported_text(dest, root_doc)
        actions.append({"path": rel, "action": "modified" if existed else "added"})
        written.append(rel)

    if actions:
        record_backup(backup, actions, stamp, (incoming_manifest or {}).get("version"))
    return written, deleted, failed, backup


def _write_like(path, text):
    """Write `text` with the line endings the file already uses.

    A merge rewrites a file the member has, and `build.py` emits its generated files with
    native endings — CRLF on Windows. Normalising to LF would turn a two-line append into a
    whole-file diff, burying the change we want them to look at.
    """
    crlf = path.is_file() and b"\r\n" in path.read_bytes()
    path.write_text(text, encoding="utf-8", newline="\r\n" if crlf else "\n")


def _backup(repo, path, backup):
    rel = path.relative_to(repo)
    target = backup / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, target)


def record_backup(backup, actions, stamp, version):
    """Merge actions into `<backup>/restore.json` — the file `--restore` replays."""
    backup.mkdir(parents=True, exist_ok=True)
    record = backup / "restore.json"
    data = (json.loads(record.read_text(encoding="utf-8")) if record.exists()
            else {"stamp": stamp, "from": version, "actions": []})
    known = {a["path"] for a in data["actions"]}
    data["actions"].extend(a for a in actions if a["path"] not in known)
    record.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def restore(repo, stamp):
    """Undo an apply: put back what it modified or deleted, remove what it added."""
    root = repo / BACKUPS
    if stamp in (None, "latest"):
        stamps = sorted(p.name for p in root.glob("*") if (p / "restore.json").exists())
        if not stamps:
            raise SystemExit(f"refused: no backups under {root}")
        stamp = stamps[-1]
    record = root / stamp / "restore.json"
    if not record.exists():
        raise SystemExit(f"refused: no backup {stamp} under {root}")
    data = json.loads(record.read_text(encoding="utf-8"))
    back, gone = 0, 0
    for action in data["actions"]:
        dest = repo / action["path"]
        if action["action"] == "added":
            if dest.is_dir():
                # A scaffolded folder we added may since have gained the member's own pages —
                # removing those would make the undo the destructive act. Only the empty
                # scaffold (at most its .gitkeep) is taken away.
                if any(p.name != ".gitkeep" for p in dest.rglob("*")):
                    print(f"  kept {action['path']} — you have added content there")
                else:
                    shutil.rmtree(dest)
                    gone += 1
            elif dest.is_file():
                dest.unlink()
                gone += 1
        else:
            src = root / stamp / action["path"]
            if src.is_file():
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
                back += 1
    print(f"restored {back} file(s) and removed {gone} added path(s) from backup {stamp}")
    manifest_action = next((a["action"] for a in data["actions"]
                            if a["path"] == "BUNDLE.json"), None)
    if manifest_action == "added":
        print("the BUNDLE.json this update created was removed — the repo has no manifest "
              "again, exactly as before the update")
    else:
        print("BUNDLE.json went back with them, so this repo is on its previous version again")
    return 0


# ── after the write: re-derive, then prove it still runs ─────────────────────

def run_in(repo, args):
    env = dict(os.environ)
    env.setdefault("FABRIC_WORKSPACE_ID", "00000000-0000-0000-0000-000000000000")
    try:
        return subprocess.run([sys.executable, *[str(a) for a in args]], cwd=repo, env=env,
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=900)
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(args, 1, "", str(exc))


def rederive(repo, skills):
    """Recompute the two files that are the member's own numbers, using their own tools.

    A new version usually changes the denominators — more tasks, more capability rows — so the
    tracker and the coverage report are stale the moment the merge lands.
    """
    out = []
    for dest, script, flag in (
            ("1_agent-gym/tracker.md", "check-training-progress/tracker_sync.py", "--write"),
            ("0_admin/capabilities/coverage.md",
             "check-training-progress/capability_coverage.py", "--write")):
        path = repo / skills / script
        if not path.exists():
            continue
        run = run_in(repo, [path, flag])
        tail = (run.stdout + run.stderr).strip().splitlines()[-1:] or [""]
        out.append((dest, run.returncode == 0, tail[0][:160]))
    return out


def verify(repo, skills):
    """The same questions the release build's `--smoke` gate asks, asked after a merge.

    Every one is offline and read-only: nothing here touches a tenant.
    """
    checks = []
    trees = [t for t in ("code", skills) if (repo / t).exists()]
    if trees:
        checks.append(("every shipped module compiles", ["-m", "compileall", "-q", *trees]))
    engine = repo / "code/validation/fabric_test.py"
    if engine.exists():
        checks.append(("the check engine's offline fixtures", [engine, "--self-test"]))
        checks.append(("the engine lists its check types", [engine, "--list-checks"]))
    runner = repo / skills / "execute-gym-rep/gym_run.py"
    if runner.exists():
        checks.append(("the rep engine's step registry", [runner, "--self-test"]))
    board = repo / skills / "check-training-progress/capability_coverage.py"
    if board.exists():
        checks.append(("the progress board's references", [board, "--check"]))

    results = []
    for label, args in checks:
        run = run_in(repo, args)
        tail = [l for l in (run.stdout + run.stderr).strip().splitlines() if l.strip()][-1:]
        results.append((label, run.returncode == 0, (tail or [""])[0][:200]))

    # Every task in the box must still dry-run: a task whose validate.json a new version
    # changed, against a check family it did not, fails here instead of mid-rep.
    if engine.exists():
        bad = []
        for spec in sorted(repo.glob("1_agent-gym/*/AG-*/validate.json")):
            run = run_in(repo, [engine, spec, "--dry-run"])
            if run.returncode != 0:
                bad.append(spec.parent.name)
        results.append((f"every task dry-runs ({len(list(repo.glob('1_agent-gym/*/AG-*/validate.json')))})",
                        not bad, ", ".join(bad[:8])))
    return results


def stamp_manifest(repo, incoming_manifest, remap, applied, unapplied, stamp, local_version,
                   backup):
    """Record what we shipped them, so the NEXT update has an exact baseline.

    The hashes written are the incoming bundle's, for every path it carries — not the hashes of
    what is now on disk. That is the point of a baseline: it answers "what did we give you?",
    so a conflict the member resolved in their own favour still reads as edited next time,
    instead of being silently overwritten by v1.4.
    """
    manifest = dict(incoming_manifest)
    manifest["contents"] = [{**e, "path": remap(e["path"])}
                            for e in incoming_manifest.get("contents", [])]
    history = list(incoming_manifest.get("update_history") or [])
    history.append({"at": stamp, "from_version": local_version,
                    "to_version": incoming_manifest.get("version"),
                    "applied": len(applied), "unapplied": sorted(unapplied)[:200]})
    manifest["update_history"] = history
    # Backed up like any other write, so `--restore` returns the baseline too. Without this a
    # restored repo holds 1.1 files under a 1.2 manifest, and every one of them reads as an
    # edit on the next run — safe, but a review list of a hundred files nobody edited.
    current = repo / "BUNDLE.json"
    if current.is_file():
        _backup(repo, current, backup)
        record_backup(backup, [{"path": "BUNDLE.json", "action": "modified"}], stamp,
                      local_version)
    else:
        # First-ever manifest (a 1.1/1.2 repo had none). Recorded as an add so --restore
        # removes it — a restored repo left holding the NEW hashes over the OLD files would
        # read every framework file as edited on the next run.
        record_backup(backup, [{"path": "BUNDLE.json", "action": "added"}], stamp,
                      local_version)
    current.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def log_run(repo, payload):
    """Append to the target repo's ledger — `0_admin/logs/`, the one sanctioned location.

    Resolved against `repo` rather than this file's own root, because under a handoff the
    running script lives in the extracted source and its own `0_admin/` is a temp directory.
    """
    try:
        logs = repo / "0_admin" / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        with open(logs / LEDGER, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload) + "\n")
    except OSError as exc:
        print(f"  warn: could not append to {LEDGER}: {exc}")


# ── the handoff ──────────────────────────────────────────────────────────────

HANDOFF_ENV = "FABRIC_KB_UPDATE_HANDOFF"


def handoff(repo, source, args, incoming_version):
    """Re-run the merge using the INCOMING version's updater, if it brought a different one.

    The member starts this from the copy they already have — an older one, by definition. It is
    the newer release that knows what changed: a zone that must not be written, a file that now
    needs merging rather than replacing, a bug in this very classification. Handing the work to
    the updater in the box means a member on 1.1 gets 1.2's rules for the 1.1 -> 1.2 merge,
    rather than on the one after.

    Returns an exit code, or None to carry on in this process. No new trust boundary: the
    script handed to is the one about to be installed anyway. It must compile first.
    """
    incoming = source / ".claude" / "skills" / "update-framework" / "update_framework.py"
    mine = Path(__file__).resolve()
    if os.environ.get(HANDOFF_ENV) or not incoming.is_file():
        return None
    if incoming.read_bytes() == mine.read_bytes():
        return None
    probe = subprocess.run([sys.executable, "-m", "py_compile", str(incoming)],
                           capture_output=True, text=True)
    if probe.returncode != 0:
        print(f"  warn: the {incoming_version} bundle's own updater does not compile — "
              f"carrying on with this one")
        return None

    print(f"  handing off to the updater in the {incoming_version} bundle "
          f"(it knows what {incoming_version} changed; this copy does not)")
    # Rebuilt from the parsed arguments rather than sliced out of argv: the source is passed
    # already-extracted so a zip is not unpacked twice, and `--repo` is ours to set.
    flags = [name for name, on in (("--apply", args.apply), ("--prune", args.prune),
                                   ("--scaffold", args.scaffold), ("--diff", args.diff),
                                   ("--all", args.all), ("--json", args.json),
                                   ("--no-verify", args.no_verify),
                                   ("--allow-dirty", args.allow_dirty),
                                   ("--force", args.force), ("--keep-temp", args.keep_temp))
             if on]
    if args.only:
        flags += ["--only", *args.only]
    env = {**os.environ, HANDOFF_ENV: "1"}
    return subprocess.run([sys.executable, str(incoming), str(source), *flags,
                           "--repo", str(repo)], cwd=repo, env=env).returncode


def is_kb_repo(path):
    """A knowledge-base repo has a root instruction file — whichever runtime wrote it."""
    return any((path / name).exists() for name in ("CLAUDE.md", "AGENTS.md", "GEMINI.md"))


def target_repo(default, source, script):
    """The repo to update when `--repo` was not given.

    A member on a version that predates this skill has no copy of it to run, so they run the
    one in the NEW bundle — and that script's own root is the source. Left alone, the merge
    would compare the bundle with itself and report every file identical: a clean "you are up
    to date" for a repo it never opened. That is the one failure mode worse than a crash, so
    it is resolved here rather than reported as a result.
    """
    if default.resolve() != source.resolve():
        return default
    cwd = Path.cwd().resolve()
    if cwd != source.resolve() and is_kb_repo(cwd):
        print(f"  running from the bundle in {source.name}, so the repo to update is the one "
              f"you ran this from: {cwd}")
        return cwd
    raise SystemExit(
        f"refused: this script lives inside the bundle you pointed it at, so there is nothing "
        f"for it to update.\n"
        f"\nRunning the NEW bundle's updater is right when your version has no "
        f"update-framework skill of its own — it just has to be told which repo to update. "
        f"Either run it from your repo:\n"
        f"    cd <your repo>\n"
        f"    python {script} {source}\n"
        f"or name the repo:\n"
        f"    python {script} {source} --repo <your repo>")


def _me(repo):
    """This script, as the member should type it. Absolute under a handoff or --repo."""
    me = Path(__file__).resolve()
    try:
        return me.relative_to(repo).as_posix()
    except ValueError:
        return str(repo / ".claude/skills/update-framework/update_framework.py")


# ── main ─────────────────────────────────────────────────────────────────────

def main(argv=None):
    utf8_stdout()
    p = argparse.ArgumentParser(
        description="Merge a newer framework bundle into this repository.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("source", nargs="?", help="the newer bundle: a .zip or an unzipped folder")
    p.add_argument("--apply", action="store_true", help="write the plan (default: print it)")
    p.add_argument("--only", nargs="+", metavar="PATH",
                   help="apply exactly these paths — how a resolved conflict lands")
    p.add_argument("--prune", action="store_true",
                   help="also delete files this version dropped (default: report them)")
    p.add_argument("--scaffold", action="store_true",
                   help="create empty 2_raw/ and 3_wiki/ folders for topics this version adds "
                        "— the only write inside those trees")
    p.add_argument("--diff", action="store_true", help="print the diffs behind the review list")
    p.add_argument("--all", action="store_true", help="no truncation, anywhere")
    p.add_argument("--json", action="store_true", help="machine-readable plan on stdout")
    p.add_argument("--no-verify", action="store_true", help="skip the post-merge checks")
    p.add_argument("--allow-dirty", action="store_true",
                   help="apply even where the worktree has uncommitted changes")
    p.add_argument("--force", action="store_true", help="override an identity refusal")
    p.add_argument("--keep-temp", action="store_true", help="leave an extracted zip on disk")
    p.add_argument("--repo", help="the repo to update (default: the one this script lives in)")
    p.add_argument("--no-handoff", dest="no_handoff", action="store_true",
                   help="do the merge with THIS updater, not the one in the source bundle")
    p.add_argument("--restore", nargs="?", const="latest", metavar="STAMP",
                   help="undo an apply from .framework-backup/")
    p.add_argument("--self-test", dest="self_test", action="store_true",
                   help="offline fixtures — no source needed")
    args = p.parse_args(argv)
    if args.only:
        # Normalised once, here, so the dirty-worktree gate, the apply worklist and the
        # handoff all see the same form — a Windows `code\core\auth.py` must not slip past
        # the gate that a `code/core/auth.py` is refused by.
        args.only = [p_.replace("\\", "/") for p_ in args.only]

    repo = Path(args.repo).expanduser().resolve() if args.repo else REPO_ROOT
    if args.self_test:
        return _self_test()
    if args.restore:
        return restore(repo, args.restore)
    if not args.source:
        p.error("a source bundle is required (a .zip or an unzipped folder)")

    source, tmp = resolve_source(args.source, args.keep_temp)
    ported_tmp = None
    try:
        if not args.repo:
            # Absolute, not repo-relative: in the case this refusal fires, "the repo" is the
            # thing being disambiguated, so a relative path is the one form that cannot help.
            repo = target_repo(repo, source, Path(__file__).resolve())
        elif repo.resolve() == source.resolve():
            raise SystemExit(f"refused: --repo names the source bundle itself ({repo}). It "
                             f"wants the repo you are updating.")
        if not is_kb_repo(repo):
            raise SystemExit(f"refused: {repo} has no root instruction file (CLAUDE.md, "
                             f"AGENTS.md or GEMINI.md) — it is not a knowledge-base repo. "
                             f"Pass --repo <path to your repo>.")
        local_manifest = read_manifest(repo)
        incoming_manifest = read_manifest(source)
        if not args.no_handoff:
            code = handoff(repo, source, args,
                           (incoming_manifest or {}).get("version", "incoming"))
            if code is not None:
                return code
        mode, notes, problems = check_identity(local_manifest, incoming_manifest, args.force)
        if problems:
            print("\n".join("refused: " + x for x in problems))
            print("\n(--force overrides, and is almost always the wrong answer here.)")
            return 1

        baseline = Baseline(repo, local_manifest)
        if local_manifest and baseline.rung != "bundle-hashes":
            # The 1.1 and 1.2 bundles shipped before per-file hashes existed, so the first
            # update off one of them is judged by the weaker rung. Said out loud, because it is
            # why the review list is long — and it is a one-off: this run writes a manifest
            # with hashes, and the update after it is exact.
            notes.append(f"your BUNDLE.json records no file hashes (1.2 and earlier did not), "
                         f"so this plan is judged by `{baseline.rung}` and errs towards review. "
                         f"This update records them — the next one will be exact.")
        remap, root_doc = remapper(repo)
        skills = ".agents/skills" if root_doc else ".claude/skills"
        src_ported = False
        if root_doc:
            source, ported_tmp, ported_manifest, src_ported = port_source(
                source, repo, root_doc, args.keep_temp)
            if src_ported:
                # Both trees are in the member's layout now: paths align, and the port's
                # rewrites appear on both sides, so an unedited file compares byte-identical
                # instead of reading as a member edit. Remapping has nothing left to do.
                incoming_manifest = ported_manifest
                remap = (lambda p: p)
                notes.append("the source bundle was ported to your layout first "
                             "(port-project-to-another-model), so both sides compare in the "
                             "same shape")
        items, merges = build_plan(repo, source, local_manifest, incoming_manifest, baseline,
                                   remap, args.scaffold)

        if args.json:
            print(json.dumps({"mode": mode, "baseline": baseline.rung,
                              "from": (local_manifest or {}).get("version"),
                              "to": (incoming_manifest or {}).get("version"),
                              "notes": notes, "items": items}, indent=2))
            return 0

        report(items, local_manifest, incoming_manifest, baseline,
               notes, args.diff, args.all, repo, source, root_doc)

        writes = [i for i in items if CLASSES[i["cls"]][0]]
        review = [i for i in items if i["cls"] in ("conflict", "unknown", "protected", "gone")]
        if not args.apply:
            print("  Nothing was written. To apply the WRITE classes above:")
            print(f"    python {_me(repo)} "
                  f"{args.source} --apply")
            if review:
                print(f"\n  {len(review)} path(s) need a decision first — read them with "
                      f"--diff. Resolve a file by hand, then land it with:")
                print(f"    … --apply --only <path> [<path> …]")
            return 0

        # The review surface for an apply is `git diff`, so a dirty tree in a path we are about
        # to write means the member loses their own uncommitted change with no way to see it.
        targets = {i["path"] for i in (writes if not args.only else
                                       [i for i in items if i["path"] in set(args.only)])}
        clash = sorted(targets & baseline.dirty)
        if clash and not args.allow_dirty:
            print(f"\nrefused: {len(clash)} path(s) this update would write have uncommitted "
                  f"changes — commit or stash them first, or pass --allow-dirty:")
            for rel in clash[:10]:
                print(f"   {rel}")
            return 1

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        # A ported source needs no per-file text rewriting on land — the port already did it.
        written, deleted, failed, backup = apply(repo, source, items, merges, args.only,
                                                 args.prune,
                                                 None if src_ported else root_doc,
                                                 incoming_manifest, stamp)
        print(f"\n  wrote {len(written)} · deleted {len(deleted)} · backup {backup}")
        for rel, why in failed:
            print(f"  skipped {rel}: {why}")

        derived = rederive(repo, skills)
        for dest, ok, tail in derived:
            print(f"  {'re-derived' if ok else 'FAILED   '} {dest}" + ("" if ok else f" — {tail}"))

        results = verify(repo, skills) if not args.no_verify else []
        if results:
            print()
            for label, ok, tail in results:
                print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f" — {tail}" if not ok else ""))

        unapplied = {i["path"] for i in items
                     if i["cls"] in ("conflict", "unknown", "protected", "keep", "gone")}
        if incoming_manifest:
            stamp_manifest(repo, incoming_manifest, remap, written, unapplied, stamp,
                           (local_manifest or {}).get("version"), backup)
        log_run(repo, {"at": stamp, "mode": mode, "baseline": baseline.rung,
                 "from": (local_manifest or {}).get("version"),
                 "to": (incoming_manifest or {}).get("version"),
                 "source": str(Path(args.source).name),
                 "written": len(written), "deleted": len(deleted),
                 "review": sorted(unapplied)[:200],
                 "verify_failed": [l for l, ok, _ in results if not ok]})

        bad = [l for l, ok, _ in results if not ok] + [d for d, ok, _ in derived if not ok]
        if bad:
            print(f"\n  {len(bad)} check(s) failed after the merge. Undo the whole thing with:")
            print(f"    python {_me(repo)} "
                  f"--restore {stamp}")
            return 1
        print("\n  Done. `git diff` is the review surface; the backup above is the undo.")
        if review:
            print(f"  Still yours to decide: {len(review)} path(s) — see the review list above.")
        return 0
    finally:
        if tmp and not args.keep_temp:
            shutil.rmtree(tmp, ignore_errors=True)
        if ported_tmp and not args.keep_temp:
            shutil.rmtree(ported_tmp, ignore_errors=True)


# ── offline fixtures ─────────────────────────────────────────────────────────

def _self_test():
    """Fixture repos in a temp dir. Asserts the classifications the whole design rests on."""
    failures = []

    def check(label, cond):
        if not cond:
            failures.append(label)
        print(f"  {'ok  ' if cond else 'FAIL'} {label}")

    spec_head = ("# Capability spec\n\nProse.\n\n## The capabilities\n\n"
                 "| id | capability | phase | angles |\n|---|---|---|---|\n")
    with tempfile.TemporaryDirectory() as tmp:
        repo, src = Path(tmp) / "member", Path(tmp) / "v2"
        files = {
            "CLAUDE.md": "schema v1\n",
            "code/core/auth.py": "print('v1')\n",
            "code/core/config.py": "print('config v1')\n",
            "code/clients/old_client.py": "print('dropped in v2')\n",
            ".env.example": "# workspace\nFABRIC_WORKSPACE_ID=\n",
            ".gitignore": "node_modules/\n",
            "0_admin/capabilities/lakehouse.md": spec_head + "| LH-C01 | one | build | 2 |\n",
            "3_wiki/lakehouse/gotchas.md": "their page, rewritten by an ingest\n",
            "2_raw/README.md": "their evidence rules\n",
            "1_agent-gym/tracker.md": "their counts: 7 reps\n",
        }
        for rel, text in files.items():
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (repo / rel).write_text(text, encoding="utf-8")
        # v2: auth changed, config unchanged, old_client dropped, a new module and task, a new
        # env var, a new capability row, and a rewritten seed page.
        v2 = {
            "CLAUDE.md": "schema v2\n",
            "code/core/auth.py": "print('v2')\n",
            "code/core/config.py": "print('config v1')\n",
            "code/clients/new_client.py": "print('new in v2')\n",
            ".env.example": "# workspace\nFABRIC_WORKSPACE_ID=\n\n# spn\nFABRIC_CLIENT_ID=\n",
            ".gitignore": "node_modules/\n\n# new upstream\ndist/\n",
            "0_admin/capabilities/lakehouse.md": spec_head + "| LH-C01 | one | build | 2 |\n"
                                                             "| LH-C02 | two | build | 3 |\n",
            "3_wiki/lakehouse/gotchas.md": "our improved seed page\n",
            "2_raw/README.md": "our improved evidence rules\n",
            "1_agent-gym/tracker.md": "0 reps\n",
            "1_agent-gym/lakehouse/AG-LAK-003/task.md": "a new task\n",
        }
        for rel, text in v2.items():
            (src / rel).parent.mkdir(parents=True, exist_ok=True)
            (src / rel).write_text(text, encoding="utf-8")
        (src / "3_wiki/graph").mkdir(parents=True, exist_ok=True)
        (src / "3_wiki/graph/.gitkeep").write_text("", encoding="utf-8")

        # The member has edited auth.py, added their own env var, and — as the spec's human
        # owner — written a capability row of their own. `.gitignore` they never touched.
        shipped = {rel: sha256(repo / rel) for rel in files}
        (repo / "code/core/auth.py").write_text("print('v1 — my fix')\n", encoding="utf-8")
        (repo / ".env.example").write_text("# workspace\nFABRIC_WORKSPACE_ID=\n\n# mine\n"
                                           "MY_OWN_VAR=\n", encoding="utf-8")
        (repo / "0_admin/capabilities/lakehouse.md").write_text(
            spec_head + "| LH-C01 | one | build | 2 |\n| LH-C50 | mine | build | 1 |\n",
            encoding="utf-8")
        local_manifest = {"bundle": "data-eng-trial", "version": "1.1", "tier": "trial",
                          "community": "frontier-data-engineer",
                          "contents": [{"path": r, "sha256": h} for r, h in shipped.items()]}
        incoming_manifest = {"bundle": "data-eng-trial", "version": "1.2", "tier": "trial",
                             "community": "frontier-data-engineer",
                             "contents": [{"path": r} for r in v2]}

        base = Baseline(repo, local_manifest)
        check("baseline rung is bundle-hashes when the manifest carries them",
              base.rung == "bundle-hashes")
        items, merges = build_plan(repo, src, local_manifest, incoming_manifest, base,
                                   lambda p: p, False)
        cls = {i["path"]: i["cls"] for i in items}

        check("an unedited changed file updates", cls.get("CLAUDE.md") == "update")
        check("an edited changed file conflicts", cls.get("code/core/auth.py") == "conflict")
        check("an unchanged file is same", cls.get("code/core/config.py") == "same")
        check("a new module is add", cls.get("code/clients/new_client.py") == "add")
        check("a new task is add", cls.get("1_agent-gym/lakehouse/AG-LAK-003/task.md") == "add")
        check("a dropped module is gone", cls.get("code/clients/old_client.py") == "gone")
        check("a changed seed wiki page is protected",
              cls.get("3_wiki/lakehouse/gotchas.md") == "protected")
        check("2_raw is protected", cls.get("2_raw/README.md") == "protected")
        check("the tracker is re-derived, never copied",
              cls.get("1_agent-gym/tracker.md") == "rederive")
        check("an edited .env.example merges", cls.get(".env.example") == "merge-lines")
        check("the merged .env.example keeps the member's var and gains the new one",
              "MY_OWN_VAR=" in merges[".env.example"]
              and "FABRIC_CLIENT_ID=" in merges[".env.example"])
        check("an UNtouched merge-zone file is replaced, not merged — so the next update can "
              "still prove it untouched", cls.get(".gitignore") == "update")
        check("an edited capability spec merges",
              cls.get("0_admin/capabilities/lakehouse.md") == "merge-spec")
        spec = merges["0_admin/capabilities/lakehouse.md"]
        check("the spec merge appends the release's row and keeps the owner's own",
              "LH-C02" in spec and "LH-C01" in spec and "LH-C50" in spec
              and spec.count("| LH-C") == 3)
        check("a new topic folder needs --scaffold",
              next(i["cls"] for i in items if i["path"] == "3_wiki/graph/") == "protected")
        items_s, _ = build_plan(repo, src, local_manifest, incoming_manifest, base,
                               lambda p: p, True)
        check("--scaffold turns it into a write",
              next(i["cls"] for i in items_s if i["path"] == "3_wiki/graph/") == "scaffold")

        # No baseline at all: nothing may be silently overwritten.
        blind = Baseline(Path(tmp) / "nowhere", None)
        blind.rung = "none"
        items_b, _ = build_plan(repo, src, None, incoming_manifest, blind, lambda p: p, False)
        cls_b = {i["path"]: i["cls"] for i in items_b}
        check("with no baseline a changed file goes to review, never to write",
              cls_b.get("CLAUDE.md") == "unknown" and CLASSES["unknown"][0] is False)

        # Rung 2: no hashes, but a git repo. The bundle is one commit; their edit is a second.
        if shutil.which("git"):
            gitrepo = Path(tmp) / "gitmember"
            shutil.copytree(repo, gitrepo)
            shutil.rmtree(gitrepo / ".framework-backup", ignore_errors=True)
            for cmd in (["init", "-q"], ["add", "-A"],
                        ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "bundle"]):
                subprocess.run(["git", *cmd], cwd=gitrepo, capture_output=True)
            edited = gitrepo / "code/core/config.py"
            edited.write_text(edited.read_text(encoding="utf-8") + "# mine\n", encoding="utf-8")
            for cmd in (["add", "-A"],
                        ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "tweak"]):
                subprocess.run(["git", *cmd], cwd=gitrepo, capture_output=True)
            unhashed = {**local_manifest,
                        "contents": [{"path": e["path"]} for e in local_manifest["contents"]]}
            gitbase = Baseline(gitrepo, unhashed)
            check("no hashes + a git repo falls back to git-history",
                  gitbase.rung == "git-history")
            check("git-history spots the file they committed a second change to",
                  gitbase.untouched("code/core/config.py",
                                    sha256(gitrepo / "code/core/config.py")) is False)
            check("git-history NEVER claims a file is untouched — one commit can hold the "
                  "bundle and their edits together",
                  gitbase.untouched("CLAUDE.md", sha256(gitrepo / "CLAUDE.md")) is None)
            items_g, _ = build_plan(gitrepo, src, unhashed, incoming_manifest, gitbase,
                                    lambda p: p, False)
            cls_g = {i["path"]: i["cls"] for i in items_g}
            check("so on the git rung a changed file is reviewed, not overwritten",
                  cls_g.get("CLAUDE.md") == "unknown")
            check("but a new file still lands on the git rung",
                  cls_g.get("code/clients/new_client.py") == "add")

        # Identity guards.
        _, _, cross = check_identity(local_manifest,
                                     {**incoming_manifest,
                                      "community": "frontier-decision-engineer"}, False)
        check("a different community is refused", bool(cross))
        _, _, down = check_identity({**local_manifest, "tier": "premium"},
                                    {**incoming_manifest, "tier": "trial"}, False)
        check("a tier downgrade is refused", bool(down))
        _, _, back = check_identity({**local_manifest, "version": "1.3"}, incoming_manifest,
                                    False)
        check("an older source is refused", bool(back))
        mode_up, _, none = check_identity(local_manifest,
                                          {**incoming_manifest, "bundle": "data-eng-premium",
                                           "tier": "premium"}, False)
        check("a tier upgrade is allowed", mode_up == "upgrade" and not none)
        _, _, swapped = check_identity(local_manifest, None, False)
        check("a versioned repo refuses an unversioned source (probable argument swap)",
              bool(swapped))
        _, _, legacy = check_identity(None, None, False)
        check("an unversioned repo still accepts an unversioned source", not legacy)
        vip = {**incoming_manifest, "bundle": "vip", "tier": "vip", "community": "both"}
        mode_vip, _, vip_problems = check_identity(
            {**local_manifest, "bundle": "data-eng-premium", "tier": "premium"}, vip, False)
        check("the VIP bundle (community `both`) upgrades a single-track repo",
              mode_vip == "upgrade" and not vip_problems)
        _, _, vip_down = check_identity(
            {**local_manifest, "bundle": "vip", "tier": "vip", "community": "both"},
            {**incoming_manifest, "bundle": "data-eng-premium", "tier": "premium",
             "version": "9.9"}, False)
        check("a single-track premium cannot land on a VIP repo (tier downgrade)",
              bool(vip_down))

        # A zip resolves, and so does a nested folder.
        archive = Path(tmp) / "bundle.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            for path in sorted(src.rglob("*")):
                if path.is_file():
                    zf.write(path, f"data-eng-trial/{path.relative_to(src)}")
        (src / "BUNDLE.json").write_text(json.dumps(incoming_manifest), encoding="utf-8")
        with zipfile.ZipFile(archive, "a") as zf:
            zf.writestr("data-eng-trial/BUNDLE.json", json.dumps(incoming_manifest))
        root, zip_tmp = resolve_source(str(archive), False)
        check("a zip resolves to the tree inside it", (root / "CLAUDE.md").exists())
        shutil.rmtree(zip_tmp, ignore_errors=True)

        # A refused zip must not leak its extraction directory.
        bad = Path(tmp) / "not-a-bundle.zip"
        with zipfile.ZipFile(bad, "w") as zf:
            zf.writestr("readme.txt", "no bundle here")
        before = set(Path(tempfile.gettempdir()).glob("fabric-kb-update-*"))
        try:
            resolve_source(str(bad), False)
            check("a zip with no bundle inside is refused", False)
        except SystemExit:
            check("a zip with no bundle inside is refused", True)
        check("the refusal cleans up its extraction",
              set(Path(tempfile.gettempdir()).glob("fabric-kb-update-*")) == before)

        # The merge reporters: what is never applied must still be named.
        merged_r, rep_r = merge_capability_spec(
            spec_head + "| A1 | old text | build | 1 |\n",
            spec_head + "| A1 | NEW TEXT | build | 1 |\n| A2 | added | build | 1 |\n")
        check("a reworded upstream row is named in the report", rep_r["reworded"] == ["A1"])
        check("a reworded row is still never applied",
              merged_r is not None and "NEW TEXT" not in merged_r and "old text" in merged_r)
        m_b, added_b, partial_b = merge_blocks("# ws\nA=\n", "# ws\nA=\nB=\n\n# other\nC=\n",
                                               env_key, "# banner")
        check("a new var sharing a block with an existing one is reported, not dropped",
              partial_b == ["B"] and added_b == ["C"] and "C=" in m_b)
        mode_sv, _, sv_problems = check_identity(
            local_manifest, {**incoming_manifest, "version": "1.1",
                             "bundle": "data-eng-premium", "tier": "premium"}, False)
        check("a same-version tier upgrade reads as an upgrade, not drift",
              mode_sv == "upgrade" and not sv_problems)

        # ── the apply layer: prune, backup, the stamped manifest, and restore ──
        repo2 = Path(tmp) / "member2"
        shutil.copytree(repo, repo2)
        base2 = Baseline(repo2, local_manifest)
        items2, merges2 = build_plan(repo2, src, local_manifest, incoming_manifest, base2,
                                     lambda p: p, True)
        stamp = "20200101T000000Z"
        written, deleted, _failed, backup = apply(repo2, src, items2, merges2, None, True,
                                                  None, incoming_manifest, stamp)
        check("--apply --prune deletes a dropped file",
              "code/clients/old_client.py" in deleted
              and not (repo2 / "code/clients/old_client.py").exists())
        check("an update lands on --apply",
              (repo2 / "CLAUDE.md").read_text(encoding="utf-8") == "schema v2\n")
        check("a conflict is never written by --apply",
              (repo2 / "code/core/auth.py").read_text(encoding="utf-8")
              == "print('v1 — my fix')\n")
        check("the backup holds the pre-merge copy",
              (backup / "CLAUDE.md").read_text(encoding="utf-8") == "schema v1\n")
        stamp_manifest(repo2, incoming_manifest, lambda p: p, written, set(), stamp, "1.1",
                       backup)
        record = json.loads((backup / "restore.json").read_text(encoding="utf-8"))
        check("a first-ever BUNDLE.json is recorded as an add",
              any(a["path"] == "BUNDLE.json" and a["action"] == "added"
                  for a in record["actions"]))
        (repo2 / "3_wiki/graph/my-page.md").write_text("mine\n", encoding="utf-8")
        restore(repo2, stamp)
        check("restore puts the pre-merge file back",
              (repo2 / "CLAUDE.md").read_text(encoding="utf-8") == "schema v1\n")
        check("restore removes the added module",
              not (repo2 / "code/clients/new_client.py").exists())
        check("restore returns the pruned file",
              (repo2 / "code/clients/old_client.py").exists())
        check("restore removes a first-ever BUNDLE.json",
              not (repo2 / "BUNDLE.json").exists())
        check("restore keeps a scaffolded folder the member wrote into",
              (repo2 / "3_wiki/graph/my-page.md").exists())

        # ── a ported repo: .agents layout vs a Claude-shaped source ──────────────
        ported_repo = Path(tmp) / "ported"
        (ported_repo / ".agents/skills/execute-gym-rep").mkdir(parents=True)
        (ported_repo / "AGENTS.md").write_text(
            "This file provides guidance to GitHub Copilot when working in this repo.\n",
            encoding="utf-8")
        (ported_repo / ".agents/skills/execute-gym-rep/gym_run.py").write_text(
            "print('engine')\n", encoding="utf-8")
        p_remap, p_root = remapper(ported_repo)
        check("a ported repo is detected", p_root == "AGENTS.md")
        check("the port target is sniffed from the root doc",
              detect_port_target(ported_repo, "AGENTS.md") == "copilot"
              and detect_port_target(ported_repo, "GEMINI.md") == "gemini")
        p_src = Path(tmp) / "psrc"
        (p_src / ".claude/skills/execute-gym-rep").mkdir(parents=True)
        (p_src / "CLAUDE.md").write_text("schema v2\n", encoding="utf-8")
        (p_src / ".claude/skills/execute-gym-rep/gym_run.py").write_text(
            "print('engine')\n", encoding="utf-8")
        p_local = {"bundle": "b", "version": "1.3", "contents": [
            {"path": ".agents/skills/execute-gym-rep/gym_run.py",
             "sha256": sha256(ported_repo / ".agents/skills/execute-gym-rep/gym_run.py")},
            {"path": "AGENTS.md"}]}
        p_incoming = {"bundle": "b", "version": "1.4", "contents": [
            {"path": ".claude/skills/execute-gym-rep/gym_run.py"}, {"path": "CLAUDE.md"}]}
        items_p, _ = build_plan(ported_repo, p_src, p_local, p_incoming,
                                Baseline(ported_repo, p_local), p_remap, False)
        cls_p = {i["path"]: i["cls"] for i in items_p}
        check("the member's live skills are never `gone` on a ported repo — the gone pass "
              "compares in the repo's layout",
              not any(i["cls"] == "gone" for i in items_p))
        check("an identical ported skill file is `same`, landing at the remapped path",
              cls_p.get(".agents/skills/execute-gym-rep/gym_run.py") == "same")
        check("the incoming root doc remaps onto the authored master",
              "AGENTS.md" in cls_p and "CLAUDE.md" not in cls_p)

    print(f"\n{'FAILED: ' + '; '.join(failures) if failures else 'all self-tests passed'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
