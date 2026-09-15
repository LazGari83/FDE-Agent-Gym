"""Catch gym tasks that teach something the wiki has since refuted.

The failure this exists to prevent: a task restates a wiki fact in its
own prose. The copy has no backlink, so `fabric-ingest` corrects the wiki and the copy
silently becomes a lie the agent trusts *more* than the wiki — because `task.md` is the first
thing it reads. A task can tell the agent a topic's wiki is "currently empty by design" while
it holds pages, or keep teaching a trigger the wiki has since refuted. An agent told the
answer key does not exist goes and derives everything from scratch, which is slow as well
as wrong.

Two checks:

  A. EMPTY-WIKI    a task claims a topic's wiki is empty/absent while `3_wiki/<topic>/` has pages.
  B. REFUTED       a task repeats a claim the wiki explicitly marks as corrected or refuted.

Check B works off a registry, not off fuzzy matching against wiki prose — task wording and wiki
wording differ, so string-matching the wiki would miss nearly everything and produce false
confidence. Instead the registry is hand-maintained, and a third check keeps it honest:

  C. UNREGISTERED  the wiki marks a claim CORRECTED/refuted but no registry entry covers it,
                   so nothing is watching the gym for that claim.

Run:  python .claude/skills/fabric-lint/stale_task_claims.py [--json]
Exit: 0 clean · 1 a task repeats a refuted claim or asserts an empty wiki · 2 only check C fired.
"""
import argparse
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
GYM = ROOT / "1_agent-gym"
WIKI = ROOT / "3_wiki"

# --------------------------------------------------------------------------- check A

# Phrases that assert a topic wiki does not exist or is empty. Deliberately narrow: a task
# saying "the lakehouse does not exist yet" is TRUE and must not fire (that was the whole
# false-positive class the first sweep hit). The alternation has to carry the repo's OWN
# house phrasing, not just the shapes the first sweep happened to meet: the topic indexes
# say "starts empty" / "ships no curated pages", and while those were missing this check
# reported Clean over twelve claims the seed-page migration had just made false.
EMPTY_WIKI = re.compile(
    r"`?3_wiki/(?P<topic>[a-z-]+)/`?[^.\n]{0,80}?"
    r"(is currently empty|which is currently empty|which does not exist yet|does not exist yet"
    r"|is empty by design|starts empty|ships no curated pages|holds no pages)",
    re.IGNORECASE,
)

# --------------------------------------------------------------------------- check B

# The registry itself — the REFUTED entries, the REFUTATION_MARKER suppression, and the
# self-test fixtures that pin them — is pure data and lives in refuted_claims.py beside this
# file, so the routine edit (add an entry whenever an ingest corrects a claim) happens there.
# The import is __file__-relative, never cwd-relative.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from refuted_claims import (REFUTED, REFUTATION_MARKER,  # noqa: E402
                            SHOULD_FIRE, SHOULD_NOT_FIRE)

# --------------------------------------------------------------------------- check C

# How the wiki flags its own corrections. If one of these matches and no REFUTED entry names
# the same page, the registry has fallen behind the wiki.
WIKI_CORRECTION = re.compile(r"~~(?P<claim>[^~]{10,200})~~\s*\**CORRECTED|previously described", re.IGNORECASE)


def gym_prose_files():
    """Every gym file a rep READS while classifying and building: task.md, the topic index,
    and — because execute-gym-rep step 1 tells the rep to read them — provision.py and
    validate.json.

    Excluding provision.py and validate.json on the reasoning that they "describe fixtures,
    not the wiki" is wrong: tasks have carried a refuted claim in exactly those files — in
    one case a sentence that was already a SHOULD_FIRE fixture for a registry entry that
    could not reach it.
    A comment a rep reads before classifying is agent guidance whatever file it sits in.

    Circuit logs are out of scope by construction — they live in `0_admin/logs/circuits/`, and
    a circuit log is supposed to quote the wrong claim it caught.
    """
    yield from sorted(GYM.glob("*/*/task.md"))
    yield from sorted(GYM.glob("*/index.md"))
    yield from sorted(GYM.glob("*/*/provision.py"))
    yield from sorted(GYM.glob("*/*/validate.json"))


# Prose-bearing JSON keys. A claim in one of these is guidance the rep reads; a claim-shaped
# string anywhere else in a validate.json is a query or an expectation, not a statement.
JSON_PROSE_KEY = re.compile(r'"(question|_note|note|description|why|remedy|title)"\s*:')


def docstring_lines(path):
    """Line numbers inside a .py file's module/function/class docstrings.

    A gym fixture's guidance lives in its MODULE DOCSTRING, not in `#` comments — provision.py
    files open with a long "what it stands up / determinism / run" docstring and carry barely a
    comment. Treating only `#` lines as prose leaves every one of those claims unwatched: a
    provision docstring can assert "Re-running is safe … the definition push is a
    whole-document overwrite" while the second run answers 400, with no registry entry able
    to reach it. Parsed with `ast` rather than matched with a regex so a triple-quoted
    string inside code is never mistaken for prose.
    """
    import ast
    lines = set()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return lines
    for node in [tree, *ast.walk(tree)]:
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            doc = node.body[0] if node.body else None
            if (isinstance(doc, ast.Expr) and isinstance(doc.value, ast.Constant)
                    and isinstance(doc.value.value, str)):
                lines.update(range(doc.lineno, (doc.end_lineno or doc.lineno) + 1))
    return lines


def is_prose_line(path, line, lineno=None, doc_lines=frozenset()):
    """Restrict matching to the parts of a file that actually carry prose.

    Without this, widening the scan set to code and data files fires on syntax: a
    `spark.sql("CREATE SCHEMA IF NOT EXISTS {SCHEMA}")` in a provision script matches the
    `EXISTS {` pattern written for GQL guidance, and a `gql` string in a validate.json
    matches whatever construct it legitimately demonstrates. Comments and prose fields are
    where an agent reads guidance; code and queries are where it reads mechanism.
    """
    if path.suffix == ".py":
        return line.lstrip().startswith("#") or (lineno in doc_lines)
    if path.suffix == ".json":
        return bool(JSON_PROSE_KEY.search(line))
    return True


def scan():
    findings = []

    for path in gym_prose_files():
        rel = path.relative_to(ROOT).as_posix()
        doc_lines = docstring_lines(path) if path.suffix == ".py" else frozenset()
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not is_prose_line(path, line, n, doc_lines):
                continue
            m = EMPTY_WIKI.search(line)
            if m:
                topic = m.group("topic")
                pages = sorted((WIKI / topic).glob("*.md")) if (WIKI / topic).is_dir() else []
                if pages:
                    findings.append({
                        "check": "EMPTY-WIKI", "severity": "fail", "file": rel, "line": n,
                        "detail": f"claims 3_wiki/{topic}/ is empty or absent, but it holds "
                                  f"{len(pages)} page(s): {', '.join(p.stem for p in pages[:4])}"
                                  f"{'…' if len(pages) > 4 else ''}",
                        "fix": f"Point the agent at 3_wiki/{topic}/ and tell it to read before building.",
                    })
            for entry in REFUTED:
                if entry["pattern"].search(line) and not REFUTATION_MARKER.search(line):
                    findings.append({
                        "check": "REFUTED", "severity": "fail", "file": rel, "line": n,
                        "detail": f"repeats the refuted claim '{entry['id']}' "
                                  f"(corrected {entry['corrected']} in {entry['wiki']})",
                        "fix": entry["truth"],
                    })

    covered_pages = {e["wiki"] for e in REFUTED}
    for page in sorted(WIKI.glob("*/*.md")):
        rel = page.relative_to(ROOT).as_posix()
        if rel in covered_pages:
            continue
        for n, line in enumerate(page.read_text(encoding="utf-8").splitlines(), 1):
            if WIKI_CORRECTION.search(line) and "provenance_notes" not in line:
                findings.append({
                    "check": "UNREGISTERED", "severity": "warn", "file": rel, "line": n,
                    "detail": "the wiki marks a claim as corrected/refuted, but no REFUTED entry "
                              "in refuted_claims.py watches the gym for it",
                    "fix": "Add an entry to REFUTED so a task repeating the old claim is caught.",
                })
                break
    return findings


# --------------------------------------------------------------------------- self-test

def _fires(line):
    return [e["id"] for e in REFUTED
            if e["pattern"].search(line) and not REFUTATION_MARKER.search(line)]


def self_test():
    failures = []
    for want_id, line in SHOULD_FIRE:
        hit = _fires(line)
        if want_id in hit:
            print(f"  PASS  fires[{want_id}]  {line[:58]}...")
        else:
            failures.append(f"expected {want_id} to fire, got {hit or 'nothing'}: {line[:70]}")
            print(f"  FAIL  fires[{want_id}]  {line[:58]}...")

    for line in SHOULD_NOT_FIRE:
        hit = _fires(line)
        empty = EMPTY_WIKI.search(line)
        if hit or empty:
            failures.append(f"false positive ({hit or 'EMPTY-WIKI'}): {line[:70]}")
            print(f"  FAIL  quiet          {line[:58]}...")
        else:
            print(f"  PASS  quiet          {line[:58]}...")

    # EMPTY-WIKI must fire on the real pre-fix line and only when the topic folder has pages.
    stale = ("only then does anything reach `3_wiki/lakehouse/`, which is currently empty by design.")
    if EMPTY_WIKI.search(stale):
        print(f"  PASS  empty-wiki      {stale[:58]}...")
    else:
        failures.append("EMPTY-WIKI failed to fire on the verbatim pre-fix line")
        print(f"  FAIL  empty-wiki      {stale[:58]}...")

    # ...and on the repo's OWN house phrasing. These three verbatim lines shipped for weeks
    # after the seed-page migration made them false, while this check reported Clean: the
    # alternation knew "is currently empty" but not "starts empty". Pinned so the wording
    # the topic indexes actually use can never fall out of the pattern again.
    for house in (
        "**`3_wiki/pipelines/` starts empty** — this topic ships no curated pages,",
        "**`3_wiki/azure-app/` starts empty too**: every capability is a gap,",
        "`3_wiki/data-agent/` starts empty; what this task is built on is Microsoft's docs",
    ):
        if EMPTY_WIKI.search(house):
            print(f"  PASS  empty-wiki      {house[:58]}...")
        else:
            failures.append(f"EMPTY-WIKI missed the repo's house phrasing: {house[:60]}")
            print(f"  FAIL  empty-wiki      {house[:58]}...")

    # The scan set must cover every file a rep reads while classifying. Dropping
    # provision.py or validate.json lets a task keep teaching a refuted claim the
    # registry already has a fixture for. Assert by suffix, so the
    # guard survives tasks being added or renamed.
    scanned = {p.name for p in gym_prose_files()}
    for required in ("task.md", "index.md", "provision.py", "validate.json"):
        if required in scanned:
            print(f"  PASS  scan-set       {required} is in the gym scan set")
        else:
            failures.append(f"gym_prose_files() no longer scans {required} — a rep reads it")
            print(f"  FAIL  scan-set       {required} is NOT in the gym scan set")

    print()
    if failures:
        for f in failures:
            print(f"  {f}")
        print(f"\nSELF-TEST FAILED ({len(failures)} problem(s))")
        return 1
    total = len(SHOULD_FIRE) + len(SHOULD_NOT_FIRE) + 1 + 4
    print(f"SELF-TEST PASSED ({total} checks)")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true",
                    help="verify the patterns fire on the pre-correction text and stay quiet on the fixed text")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    findings = scan()
    if args.json:
        print(json.dumps(findings, indent=2))
    else:
        fails = [f for f in findings if f["severity"] == "fail"]
        warns = [f for f in findings if f["severity"] == "warn"]
        for f in fails + warns:
            tag = "FAIL" if f["severity"] == "fail" else "WARN"
            print(f"[{tag}] {f['check']}  {f['file']}:{f['line']}")
            print(f"       {f['detail']}")
            print(f"       -> {f['fix']}\n")
        n_task_files = len(list(gym_prose_files()))
        if not findings:
            print(f"Clean. {n_task_files} gym prose files carry no refuted or empty-wiki claims.")
        else:
            print(f"{len(fails)} failure(s), {len(warns)} warning(s) across {n_task_files} gym prose files.")

    if any(f["severity"] == "fail" for f in findings):
        return 1
    return 2 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
