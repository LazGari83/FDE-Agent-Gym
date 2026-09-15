"""
kbmd.py — the frontmatter parsers, path resolution, and console boilerplate shared by the KB's skill scripts.

Not a skill: this folder carries no SKILL.md, so the skill loader ignores it. Each consumer
puts this directory on sys.path with a __file__-relative insertion — never cwd-relative,
because the scripts are run both from the repo root and from their own folders, and
tracker_sync is additionally *imported* by scripts living in other directories:

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_shared"))
    from kbmd import frontmatter_list, utf8_stdout, REPO_ROOT, SKILLS_DIR, resolve_log_file

Two parsing behaviours coexist here deliberately, verbatim from the scripts they came from:
`frontmatter()` (capability_coverage lineage) treats an unterminated frontmatter
block as running to end-of-text, while `frontmatter_map()` (tracker_sync lineage) treats it
as empty — changing either would change what its consumers count.
"""
import re
import sys
from pathlib import Path

# Path resolution: repo root is three parents up from <root>/skills/_shared/
REPO_ROOT = Path(__file__).resolve().parents[3]


# Self-locating rather than a hardcoded `<root>/.claude/skills`: after the
# port-project-to-another-model migration this same file serves from `.agents/skills/`.
SKILLS_DIR = Path(__file__).resolve().parents[1]

# Every pipeline ledger lives in the top-level `0_admin/logs/`. There is exactly one
# location by design: these files are append-only run records, so a second copy
# does not merge — it silently loses entries.
LOGS_DIR = REPO_ROOT / "0_admin" / "logs"


def resolve_log_file(filename: str) -> Path:
    """Path to a pipeline ledger in `0_admin/logs/`, creating the directory if needed."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return LOGS_DIR / filename


def frontmatter(text):
    """Frontmatter body of a page, or '' when there is none."""
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    return text[3:end if end > 0 else len(text)]


def frontmatter_list(text, key):
    """Values of a frontmatter list, in either `key: [a, b]` or block form."""
    fm = frontmatter(text)
    inline = re.search(rf"^{key}:\s*\[(.*?)\]", fm, re.M)
    if inline:
        return [v.strip().strip("'\"") for v in inline.group(1).split(",") if v.strip()]
    block = re.search(rf"^{key}:\s*$((?:\n\s*-\s*.+)+)", fm, re.M)
    if block:
        return [v.strip().lstrip("-").strip().strip("'\"") for v in block.group(1).splitlines() if v.strip()]
    return []


def frontmatter_map(text):
    """Top-level frontmatter key -> value dict. Nested (indented) lines are skipped —
    tracker_sync's completion discovery depends on exactly this top-level-keys-only read."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    block = text[3:end] if end != -1 else ""
    fm = {}
    for line in block.splitlines():
        m = re.match(r"^([A-Za-z_]+):\s*(.*)$", line)  # top-level keys only (nested lines are indented)
        if m:
            fm[m.group(1)] = m.group(2).strip()
    return fm


def utf8_stdout():
    """KB prose carries em dashes and arrows; a cp1252 console would crash on them."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass


def load_completions():
    """{topic: {task_num: {...}}} — passed gym rep reports, from the check-training-progress skill.
    Reused rather than reimplemented so 'what has landed' has exactly one definition."""
    sync_dir = SKILLS_DIR / "check-training-progress"
    sys.path.insert(0, str(sync_dir))
    from tracker_sync import discover_completions  # noqa: E402
    return discover_completions()
