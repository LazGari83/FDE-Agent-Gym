"""fabric-lint: markdown anchor checker.

Every `[text](path#fragment)` link in the wiki must land on a real heading in the
target file. Atom cards compress on the premise that "the detail is one click away";
a dead anchor silently breaks that contract, and nothing else in the harness looks
for it — a compression refactor can ship dead anchors (a fragment pointing at a
heading that has since drifted, or an anchor built from a `3_wiki/log.md` entry title
instead of a heading) while every gate stays green. This check exists so that class
cannot ship.

Usage:
    python .claude/skills/fabric-lint/anchor_check.py            # scan 3_wiki/
    python .claude/skills/fabric-lint/anchor_check.py DIR [...]  # scan other roots
    python .claude/skills/fabric-lint/anchor_check.py --self-test

Exit codes: 0 clean · 1 dead anchors found · 2 usage/internal error.

Fragments are matched against GitHub-style heading slugs (lowercase; markdown
formatting stripped; everything but word chars, spaces and hyphens dropped; spaces
to hyphens; duplicate headings suffixed -1, -2, …). Matching is case-insensitive —
lenient on purpose: the class of defect this catches is a heading that does not
exist, not a capitalization nit. Links inside fenced code blocks and inline code
spans are ignored; external (`http…`)/`mailto:` targets and fragments on non-`.md`
files are skipped. A fragment link whose target *file* is missing is reported too
(DEAD-FILE) so this check stands alone.
"""
import difflib
import re
import sys
import urllib.parse
from pathlib import Path

FENCE_RE = re.compile(r"^(```|~~~)")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
MD_LINK_IN_HEADING_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
EXTERNAL_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")  # http:, https:, mailto:, …


def slugify(heading):
    """GitHub-style slug for one heading line's text (no duplicate suffix)."""
    text = MD_LINK_IN_HEADING_RE.sub(r"\1", heading)   # [text](url) -> text
    text = text.replace("`", "").replace("*", "")
    text = text.strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)               # drops (), :, —, …; keeps _ and -
    return text.replace(" ", "-")


def strip_code(lines, blank_inline_spans=True):
    """Blank out fenced code blocks, preserving line count. For link scanning also
    blank inline code spans; for heading slugs keep them — GitHub keeps the code
    text of a `` `span` `` in the anchor, only the backticks vanish."""
    out, in_fence = [], False
    for line in lines:
        if FENCE_RE.match(line.strip()):
            in_fence = not in_fence
            out.append("")
            continue
        if in_fence:
            out.append("")
        else:
            out.append(re.sub(r"`[^`]*`", "``", line) if blank_inline_spans else line)
    return out


def heading_slugs(path, cache={}):
    """All anchor slugs a file offers, with GitHub duplicate suffixes."""
    if path in cache:
        return cache[path]
    slugs, seen = set(), {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        cache[path] = slugs
        return slugs
    for line in strip_code(lines, blank_inline_spans=False):
        m = HEADING_RE.match(line)
        if not m:
            continue
        base = slugify(m.group(2))
        n = seen.get(base, 0)
        seen[base] = n + 1
        slugs.add(base if n == 0 else f"{base}-{n}")
    cache[path] = slugs
    return slugs


def check_file(md_file, findings):
    lines = md_file.read_text(encoding="utf-8").splitlines()
    for lineno, line in enumerate(strip_code(lines), start=1):
        for m in LINK_RE.finditer(line):
            target = m.group(1)
            if EXTERNAL_RE.match(target) or "#" not in target:
                continue
            path_part, frag = target.split("#", 1)
            frag = urllib.parse.unquote(frag).lower()
            if not frag:
                continue
            dest = md_file if not path_part else (md_file.parent / path_part).resolve()
            if path_part and dest.suffix.lower() != ".md":
                continue
            if not dest.is_file():
                findings.append((md_file, lineno, target, "DEAD-FILE", None))
                continue
            slugs = heading_slugs(dest)
            if frag not in slugs:
                hint = difflib.get_close_matches(frag, sorted(slugs), n=1, cutoff=0.5)
                findings.append((md_file, lineno, target, "DEAD-ANCHOR", hint[0] if hint else None))


def scan(roots):
    findings = []
    for root in roots:
        for md_file in sorted(Path(root).rglob("*.md")):
            check_file(md_file, findings)
    return findings


def self_test():
    import tempfile
    failures = []
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "sub").mkdir()
        (root / "sub" / "target.md").write_text(
            "# Target page\n"
            "## Changes invisible until refreshed (refresh is required, but now programmatic)\n"
            "### The surface decides whether anything is emitted (2026-08-14)\n"
            "## `code` heading with **bold** and [a link](x.md)\n"
            "## Duplicate\n"
            "## Duplicate\n"
            "```\n# not a heading — fenced\n```\n",
            encoding="utf-8")
        (root / "page.md").write_text(
            "# Page\n"
            "[ok-1](sub/target.md#changes-invisible-until-refreshed-refresh-is-required-but-now-programmatic)\n"
            "[ok-2](sub/target.md#the-surface-decides-whether-anything-is-emitted-2026-08-14)\n"
            "[ok-3](sub/target.md#code-heading-with-bold-and-a-link)\n"
            "[ok-dup](sub/target.md#duplicate-1)\n"
            "[ok-case](sub/target.md#Duplicate)\n"
            "[ok-self](#local-heading)\n"
            "## Local heading\n"
            "[bad-anchor](sub/target.md#graph-refresh)\n"
            "[bad-near-miss](sub/target.md#the-surface-decides-whether-anything-is-emitted)\n"
            "[bad-fenced](sub/target.md#not-a-heading--fenced)\n"
            "[bad-file](sub/missing.md#anything)\n"
            "[skip-external](https://example.com/a.md#frag)\n"
            "[skip-nonmd](sub/data.json#frag)\n"
            "```\n[ignored](sub/target.md#inside-fence)\n```\n"
            "`[ignored](sub/target.md#inside-span)`\n",
            encoding="utf-8")
        findings = scan([root])
        got = {(f[0].name, f[2], f[3]) for f in findings}
        want = {
            ("page.md", "sub/target.md#graph-refresh", "DEAD-ANCHOR"),
            ("page.md", "sub/target.md#the-surface-decides-whether-anything-is-emitted", "DEAD-ANCHOR"),
            ("page.md", "sub/target.md#not-a-heading--fenced", "DEAD-ANCHOR"),
            ("page.md", "sub/missing.md#anything", "DEAD-FILE"),
        }
        if got != want:
            failures.append(f"expected {sorted(want)}, got {sorted(got)}")
        hints = {f[2]: f[4] for f in findings if f[3] == "DEAD-ANCHOR"}
        near = hints.get("sub/target.md#the-surface-decides-whether-anything-is-emitted") or ""
        if not near.startswith("the-surface-decides"):
            failures.append(f"suggestion missing for near-miss: {hints}")
    if failures:
        print("anchor_check --self-test FAIL")
        for f in failures:
            print(f"  {f}")
        return 1
    print("anchor_check --self-test PASS (4 dead links caught, 6 good links accepted)")
    return 0


def main(argv):
    if "--self-test" in argv:
        return self_test()
    roots = [a for a in argv if not a.startswith("-")] or ["3_wiki"]
    for root in roots:
        if not Path(root).is_dir():
            print(f"anchor_check: no such directory: {root}")
            return 2
    findings = scan(roots)
    if not findings:
        print(f"anchor_check: clean ({', '.join(roots)})")
        return 0
    for path, lineno, target, kind, hint in findings:
        suffix = f"  (closest heading: #{hint})" if hint else ""
        print(f"{path}:{lineno}: {kind} {target}{suffix}")
    print(f"anchor_check: {len(findings)} dead link(s)")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
