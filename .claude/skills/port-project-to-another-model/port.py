#!/usr/bin/env python
"""port.py — migrate this repo from the Claude-first layout to another runtime's layout.

A MIGRATION, not a projection: the skills tree (scripts included) MOVES to `.agents/skills/`,
CLAUDE.md is rewritten as the runtime's root doc (AGENTS.md or GEMINI.md) and then deleted,
every path reference in the repo is rewritten, and the Claude-specific directory is removed.
One way only — there is no port back. After migration the generated root doc is the authored
schema: edit it directly.

Usage:
    python .claude/skills/port-project-to-another-model/port.py --target codex
    python .claude/skills/port-project-to-another-model/port.py --target copilot
    python .claude/skills/port-project-to-another-model/port.py --target cursor
    python .claude/skills/port-project-to-another-model/port.py --target gemini
    ... --check       # print the migration plan, change nothing
    ... --self-test   # offline fixtures, no repo state touched
"""
import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_shared"))
from kbmd import REPO_ROOT, utf8_stdout  # noqa: E402

# The legacy layout's directory name, split so this file's own migration rewrite (which
# replaces the joined string everywhere, this script included) leaves the constant intact.
# Post-migration the guard below must still spell the OLD path to detect "already migrated".
_LEGACY = ".cla" + "ude"

SRC_SKILLS = REPO_ROOT / _LEGACY / "skills"
DEST_SKILLS = REPO_ROOT / ".agents" / "skills"
SOURCE_DOC = REPO_ROOT / ("CLA" + "UDE.md")

TARGETS = {
    "codex":   {"root": "AGENTS.md", "runtime": "OpenAI Codex"},
    "copilot": {"root": "AGENTS.md", "runtime": "GitHub Copilot"},
    "cursor":  {"root": "AGENTS.md", "runtime": "Cursor"},
    "gemini":  {"root": "GEMINI.md", "runtime": "Gemini / Antigravity"},
}

BANNER = ("<!-- This file is the schema. Migrated from the Claude-first layout by\n"
          "     port-project-to-another-model; it is now the authored master — edit it "
          "directly. -->\n\n")

# Targeted rewrites of the source doc's Claude-specific sentences, applied before the blanket
# path rewrite. A find string that is absent is a HARD ERROR, never a silent skip: the source
# doc has drifted from what this script knows how to translate, and a half-translated root
# file is worse than a refusal — it would present itself as the runtime's instructions while
# still telling it to behave like Claude Code.
ROOT_EDITS = [
    ("# " + "CLA" + "UDE.md",
     "# {root}"),
    ("This file provides guidance to Claude Code (claude.ai/code) when working in this "
     "repository.",
     "This file provides guidance to {runtime} when working in this repository."),
    ("Skills live in `" + _LEGACY + "/skills/`. Claude Code loads them from there and they "
     "are invoked as\n`/skill-name`.",
     "Skills live in `.agents/skills/` — scripts included. Your runtime scans that directory "
     "and\noffers each skill by its description; invoke one by naming it."),
    # Post-migration the "port this repo" operation is done; what remains useful is porting
    # the NEXT Claude-shaped drop (tier-upgrade bundles arrive Claude-first) before merging.
    ("| Use this KB with Copilot, Gemini or Codex instead of Claude Code                 | "
     "**port-project-to-another-model** — migrates the repo to that runtime's layout  |",
     "| Port a fresh Claude-shaped bundle (tier upgrades arrive Claude-first)            | "
     "`python .agents/skills/port-project-to-another-model/port.py --target <runtime>` "
     "inside the bundle, then merge |"),
]

# Dropped entirely: in a migrated repo it would tell the reader to migrate a repo they have
# already migrated.
DROP_PARAGRAPH_START = "**Using a different assistant?**"

# Extensions (plus any dotfile) treated as rewritable text during the repo-wide pass.
TEXT_SUFFIXES = {".md", ".py", ".json", ".yaml", ".yml", ".txt", ".toml", ".cfg", ".ini",
                 ".csv", ".gitignore", ".gitattributes", ".env", ".example"}
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv"}


def rewrite_text(text, root_name):
    """The blanket migration rewrite: legacy paths and the old root doc's name."""
    return text.replace(_LEGACY, ".agents").replace("CLA" + "UDE.md", root_name)


def _drop_paragraph(text, marker):
    """Remove the blank-line-delimited paragraph beginning with `marker`."""
    start = text.find(marker)
    if start == -1:
        raise SystemExit(f"the source doc no longer carries the paragraph starting "
                         f"{marker!r} — port.py needs updating before it can translate "
                         "this file")
    end = text.find("\n\n", start)
    return text[:start] + text[end + 2:] if end != -1 else text[:start]


def render_root(text, target):
    """The source doc rewritten as `target`'s root instruction file."""
    spec = TARGETS[target]
    text = _drop_paragraph(text, DROP_PARAGRAPH_START)
    for find, template in ROOT_EDITS:
        if find not in text:
            raise SystemExit(
                f"the source doc no longer contains:\n  {find[:80]}...\n"
                "port.py cannot translate a file it does not recognise. Update ROOT_EDITS "
                "to match the current source doc, then re-run.")
        text = text.replace(find, template.format(root=spec["root"], runtime=spec["runtime"]))
    return BANNER + rewrite_text(text, spec["root"])


def _text_files():
    """Every rewritable text file in the repo, migration-relevant dirs only."""
    for path in sorted(REPO_ROOT.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in TEXT_SUFFIXES or path.name.startswith("."):
            yield path


def _stale_generated_roots(root_name):
    """Root docs left behind by the old projection-style port (banner-marked, generated)."""
    stale = []
    for name in {"AGENTS.md", "GEMINI.md"} - {root_name}:
        path = REPO_ROOT / name
        if path.exists() and path.read_text(encoding="utf-8").startswith("<!-- GENERATED"):
            stale.append(path)
    return stale


def migrate(target, check=False):
    spec = TARGETS[target]
    if SRC_SKILLS.resolve() == DEST_SKILLS.resolve() or not SRC_SKILLS.exists():
        if DEST_SKILLS.exists():
            raise SystemExit("already migrated: skills live in .agents/skills and the "
                             "legacy tree is gone. There is no port back.")
        raise SystemExit(f"no skills tree at {SRC_SKILLS.relative_to(REPO_ROOT)} — "
                         "nothing to migrate")
    if not SOURCE_DOC.exists():
        raise SystemExit(f"no {SOURCE_DOC.name} at the repo root — nothing to migrate from")

    root_path = REPO_ROOT / spec["root"]
    root_content = render_root(SOURCE_DOC.read_text(encoding="utf-8"), target)
    stale_roots = _stale_generated_roots(spec["root"])
    legacy_dir = SRC_SKILLS.parent
    legacy_extras = [p for p in legacy_dir.iterdir() if p != SRC_SKILLS]

    print(f"== migrate to {target} ({spec['runtime']}) ==")
    print(f"  move    {SRC_SKILLS.relative_to(REPO_ROOT)}/  ->  "
          f"{DEST_SKILLS.relative_to(REPO_ROOT)}/  (scripts included)")
    if DEST_SKILLS.exists():
        print(f"  delete  {DEST_SKILLS.relative_to(REPO_ROOT)}/  "
              "(old projection-generated tree, replaced by the move)")
    print(f"  write   {spec['root']}  (the schema — {SOURCE_DOC.name} rewritten)")
    print(f"  delete  {SOURCE_DOC.name}")
    for extra in legacy_extras:
        print(f"  delete  {extra.relative_to(REPO_ROOT)}  (Claude-specific)")
    for stale in stale_roots:
        print(f"  delete  {stale.name}  (stale generated root from the old port)")
    print(f"  delete  {legacy_dir.relative_to(REPO_ROOT)}/")

    if check:
        pending = [p for p in _text_files()
                   if _LEGACY in p.read_text(encoding="utf-8", errors="ignore")]
        print(f"  rewrite {len(pending)} file(s) referencing {_LEGACY}/...")
        print("== --check: nothing changed ==")
        return 0

    if DEST_SKILLS.exists():
        shutil.rmtree(DEST_SKILLS)
    DEST_SKILLS.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(SRC_SKILLS), str(DEST_SKILLS))
    shutil.rmtree(legacy_dir)
    for cache in DEST_SKILLS.rglob("__pycache__"):
        shutil.rmtree(cache)  # pre-migration bytecode embeds the old paths

    root_path.write_text(root_content, encoding="utf-8")
    SOURCE_DOC.unlink()
    for stale in stale_roots:
        stale.unlink()

    rewritten = 0
    for path in _text_files():
        text = path.read_text(encoding="utf-8", errors="surrogateescape")
        new = rewrite_text(text, spec["root"])
        if new != text:
            path.write_text(new, encoding="utf-8", errors="surrogateescape")
            rewritten += 1
    print(f"== rewrote {rewritten} file(s) ==")

    leftovers = [p.relative_to(REPO_ROOT).as_posix() for p in _text_files()
                 if _LEGACY in p.read_text(encoding="utf-8", errors="ignore")]
    if leftovers:
        print(f"!! {len(leftovers)} file(s) still reference {_LEGACY}:")
        for rel in leftovers:
            print(f"     {rel}")
        print("!! MIGRATION INCOMPLETE — fix these, they name paths that no longer exist.")
        return 1

    print(f"== verified: zero {_LEGACY} references remain ==")
    print(f"== DONE. {spec['root']} is your schema now — authored, not generated: edit it "
          "directly. ==")
    print("== Commit everything (`git status` first). This migration is one-way. ==")
    return 0


def main(argv=None):
    utf8_stdout()
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--target", choices=sorted(TARGETS))
    p.add_argument("--check", action="store_true", help="print the plan, change nothing")
    p.add_argument("--self-test", dest="self_test", action="store_true")
    args = p.parse_args(argv)

    if args.self_test:
        return _self_test()
    if not args.target:
        p.error("--target is required (or use --self-test)")
    return migrate(args.target, check=args.check)


def _self_test():
    """Offline fixtures — no repo state read, nothing moved or written."""
    checks = []

    def expect(name, cond):
        checks.append((name, bool(cond)))

    expect("legacy-constant-spells-the-old-layout",
           _LEGACY.startswith(".cla") and _LEGACY.endswith("ude") and len(_LEGACY) == 7)

    out = rewrite_text("run python " + _LEGACY + "/skills/x/y.py and read " + "CLA" + "UDE.md",
                       "AGENTS.md")
    expect("blanket-rewrites-legacy-paths", ".agents/skills/x/y.py" in out)
    expect("blanket-rewrites-root-doc-name", "read AGENTS.md" in out)
    expect("blanket-leaves-no-legacy", _LEGACY not in out)
    expect("blanket-is-noop-without-matches",
           rewrite_text("nothing to see", "AGENTS.md") == "nothing to see")

    sample = ("# " + "CLA" + "UDE.md" + "\n\nThis file provides guidance to Claude Code "
              "(claude.ai/code) when working in this repository.\n\n"
              "**Using a different assistant?** Run the port.\nIt migrates things.\n\n"
              "## Operations\n\n"
              "Skills live in `" + _LEGACY + "/skills/`. Claude Code loads them from there "
              "and they are invoked as\n`/skill-name`.\n\n"
              "| Use this KB with Copilot, Gemini or Codex instead of Claude Code           "
              "      | **port-project-to-another-model** — migrates the repo to that "
              "runtime's layout  |\n")

    out = render_root(sample, "codex")
    expect("banner-is-first", out.startswith("<!-- This file is the schema"))
    expect("root-is-now-the-authored-master", "edit it directly" in out)
    expect("title-renamed", "# AGENTS.md" in out)
    expect("runtime-named", "guidance to OpenAI Codex" in out)
    expect("claude-code-not-mentioned", "Claude Code" not in out)
    expect("port-paragraph-dropped", "Using a different assistant" not in out)
    expect("slash-invocation-replaced", "/skill-name" not in out)
    expect("no-legacy-paths-survive", _LEGACY not in out)
    expect("skills-live-in-agents", ".agents/skills/" in out)
    expect("ops-row-points-at-bundle-porting", "inside the bundle, then merge" in out)

    gem = render_root(sample, "gemini")
    expect("gemini-root-is-gemini-md", "# GEMINI.md" in gem)
    expect("gemini-runtime-named", "Gemini / Antigravity" in gem)
    cur = render_root(sample, "cursor")
    expect("cursor-root-is-agents-md", "# AGENTS.md" in cur)
    expect("cursor-runtime-named", "guidance to Cursor" in cur)

    # A source doc this script no longer recognises must REFUSE, not half-translate.
    try:
        render_root("# " + "CLA" + "UDE.md" + "\n\nSomething else entirely.\n", "codex")
        expect("refuses-an-unrecognised-source", False)
    except SystemExit:
        expect("refuses-an-unrecognised-source", True)

    expect("every-target-declares-a-root", all(t.get("root") for t in TARGETS.values()))
    expect("root-doc-is-the-only-per-target-difference",
           {t: s["root"] for t, s in TARGETS.items()}
           == {"codex": "AGENTS.md", "copilot": "AGENTS.md", "cursor": "AGENTS.md",
               "gemini": "GEMINI.md"})
    expect("src-and-dest-differ-pre-migration", SRC_SKILLS != DEST_SKILLS)

    for name, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    failures = [n for n, ok in checks if not ok]
    if failures:
        print(f"SELF-TEST FAILED: {failures}")
        return 1
    print(f"SELF-TEST PASSED ({len(checks)} checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
