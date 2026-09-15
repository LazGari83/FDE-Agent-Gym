"""
test_common.py — shared matcher engine and cross-family helpers for the fabric_test harness.

Part of the fabric_test module family (fabric_test.py is the entry point — run that, not
this). Holds the expectation matchers every result-returning check shares (`_match_expect`
and the exact/invariant vocabulary), plus the helpers more than one topic module needs:
the workspace-folder matcher, the run's shared Livy session, the GUID regex, spec loading
with the exit-2 usage error, the paged {workspace id -> displayName} map, and the ARM
role-assignment predicate the keyvault and azuremonitor families share. Topic modules
(test_ontology, test_lakehouse, ...) import from here — never from fabric_test.py and
never from each other, with one sanctioned exception: test_lakehouse's mirror-backed
table check borrows test_openmirror's Delta reader, degrading to a clear refusal when
that family is absent from the bundle.
"""
import json
import re
import sys
from pathlib import Path

_EPS = 1e-6


# ── value / row comparison ────────────────────────────────────────────────────

def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _norm(v):
    """Normalize a scalar for comparison: numbers to float, strings stripped."""
    if _is_number(v):
        return float(v)
    if isinstance(v, str):
        return v.strip()
    return v


def _values_equal(a, b):
    a, b = _norm(a), _norm(b)
    if _is_number(a) and _is_number(b):
        return abs(a - b) < _EPS
    return a == b


def _row_key(row):
    """Canonical, order-independent key for a result row dict."""
    return tuple(sorted((str(k), None if v is None else _norm(v)) for k, v in row.items()))


def _rows_match(actual, expected, ordered=False):
    if len(actual) != len(expected):
        return False
    if ordered:
        return all(_row_key(a) == _row_key(e) for a, e in zip(actual, expected))
    remaining = [_row_key(e) for e in expected]
    for a in actual:
        ka = _row_key(a)
        if ka in remaining:
            remaining.remove(ka)
        else:
            return False
    return not remaining


def _scalar_of(rows):
    """First value of the first row (single-column RETURN convention)."""
    if not rows:
        return None
    first = rows[0]
    vals = list(first.values())
    return vals[0] if vals else None


def _plain_rows(cols, raw_rows):
    """duckdb rows -> list[dict] with JSON-comparable values (dates/decimals -> str/float).

    Shared by openmirror's `delta-sql` and webapp's `duckdb-parquet`, so it lives here in
    core rather than in either family.
    """
    out = []
    for r in raw_rows:
        row = {}
        for k, v in zip(cols, r):
            if v is None or isinstance(v, (str, bool, int, float)):
                row[k] = v
            else:
                try:
                    row[k] = float(v)  # Decimal and friends
                except (TypeError, ValueError):
                    row[k] = str(v)    # date/datetime/uuid -> canonical string
        out.append(row)
    return out


# ── the SQL read-only guard ───────────────────────────────────────────────────
#
# Shared by every runner that accepts caller-supplied SQL — spark-sql (test_lakehouse),
# delta-sql (test_openmirror) and duckdb-parquet (test_webapp). It is what backs the
# product claim that a validation run cannot change a tenant, so it lives in ONE place:
# a second copy is a copy that misses the next lexer trap.

_READONLY_KEYWORDS = ("SELECT", "WITH", "DESCRIBE", "SHOW")


def _first_keyword(sql):
    """The statement's first keyword, uppercased — after leading whitespace and comments.

    Both `--` line comments and `/* */` block comments are skipped, so a statement cannot
    smuggle its verb past the guard behind a comment. Bracketed comments NEST in Spark SQL
    (3.x, SPARK-28880), so the skipper tracks depth: to Spark's lexer `/* /* */ SELECT */`
    is one comment, and a naive non-nesting scan would read the inner 'SELECT' as the verb
    and wave a trailing write through. An unterminated comment consumes the rest of the
    statement. Returns '' when nothing but whitespace/comments remains.
    """
    s = str(sql)
    i, n = 0, len(s)
    while i < n:
        if s[i].isspace():
            i += 1
        elif s.startswith("--", i):
            j = s.find("\n", i)
            i = n if j < 0 else j + 1
        elif s.startswith("/*", i):
            depth, i = 1, i + 2
            while i < n and depth:
                if s.startswith("/*", i):
                    depth, i = depth + 1, i + 2
                elif s.startswith("*/", i):
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
            if depth:  # unterminated — treat everything after it as comment
                i = n
        else:
            break
    m = re.match(r"[A-Za-z_]+", s[i:])
    return m.group(0).upper() if m else ""


_EXACT_KEYS = ("scalar", "count", "rows")
_INVARIANT_KEYS = ("minCount", "maxCount", "minScalar", "maxScalar",
                   "unique", "columns", "nonNull")

# The whole shared expect vocabulary — every matcher plus `ordered`, the modifier `rows`
# takes. THE canonical home: test_dataagent and test_azureapp import it rather than
# restating it, because a second copy of a vocabulary is a vocabulary that drifts.
MATCHER_KEYS = frozenset(_EXACT_KEYS + _INVARIANT_KEYS + ("ordered",))


def _as_columns(v):
    """A column name or a list of them -> list."""
    return [v] if isinstance(v, str) else list(v)


def unknown_keys(exp, known, label):
    """(ok, reason) refusing unknown expectation keys — the one guard every runner shares.

    Unknown keys are REFUSED, not skipped: a silently ignored key is a check that asserts
    nothing while reading as proof. The reason always carries the substring
    "unknown expectation key", the marker refusals are recognised by across families.
    """
    unknown = sorted(set(exp) - set(known))
    if unknown:
        return False, (f"{label}: unknown expectation key(s) {unknown} "
                       f"(known: {sorted(known)}) — refused rather than skipped, because "
                       "a silently ignored key is a check that asserts nothing")
    return True, ""


def fold(results, prefix=None, mark_misses=False):
    """AND a list of (ok, reason) pairs into one (ok, reason), reasons joined with "; ".

    `mark_misses=True` prefixes each failing reason with "MISS " so a long conjunction
    reads at a glance.
    """
    ok = all(hit for hit, _ in results)
    text = "; ".join((("" if hit else "MISS ") + why) if mark_misses else why
                     for hit, why in results)
    if prefix:
        text = f"{prefix}; {text}" if text else prefix
    return ok, text


def _match_invariants(rows, expect):
    """Value-independent assertions -> [(ok, reason), ...].

    An exact matcher is unusable against a live source: a view count changes hourly, so
    there is no deterministic expected value to write down. These assert what must hold
    regardless of the values — how much came back, that keys do not repeat, that the shape
    did not move. Stack them: minCount + unique + columns is the standard "pagination ran
    to exhaustion and the schema held" assertion, and each one narrows what a wrong run
    can still look like.
    """
    out = []
    if "minCount" in expect:
        got, want = len(rows), expect["minCount"]
        out.append((got >= want, f"minCount got={got} want>={want}"))
    if "maxCount" in expect:
        got, want = len(rows), expect["maxCount"]
        out.append((got <= want, f"maxCount got={got} want<={want}"))
    if "minScalar" in expect:
        got, want = _scalar_of(rows), expect["minScalar"]
        n = _norm(got)
        ok = _is_number(n) and n >= _norm(want) - _EPS
        out.append((ok, f"minScalar got={got!r} want>={want!r}"))
    if "maxScalar" in expect:
        got, want = _scalar_of(rows), expect["maxScalar"]
        n = _norm(got)
        ok = _is_number(n) and n <= _norm(want) + _EPS
        out.append((ok, f"maxScalar got={got!r} want<={want!r}"))
    if "unique" in expect:
        cols = _as_columns(expect["unique"])
        absent = [c for c in cols if any(c not in r for r in rows)]
        if absent:
            out.append((False, f"unique{cols}: column(s) {absent} absent from some row"))
        else:
            keys = [tuple(_norm(r.get(c)) for c in cols) for r in rows]
            dupes = len(keys) - len(set(keys))
            out.append((dupes == 0, f"unique{cols} {len(set(keys))}/{len(keys)} distinct"
                                    + (f" — {dupes} DUPLICATE(S)" if dupes else "")))
    if "columns" in expect:
        want = set(expect["columns"])
        shapes = {frozenset(r.keys()) for r in rows}
        if not rows:
            # Deliberately a failure, not a vacuous pass: asserting a schema against zero
            # rows is exactly how a well-formed empty result slips through.
            out.append((False, f"columns: no rows to check shape against (want {sorted(want)})"))
        elif len(shapes) > 1:
            out.append((False, "columns: rows disagree on shape — "
                               f"{[sorted(s) for s in shapes]}"))
        else:
            got = set(next(iter(shapes)))
            missing, extra = sorted(want - got), sorted(got - want)
            out.append((got == want, f"columns got={sorted(got)}"
                                     + (f" MISSING={missing}" if missing else "")
                                     + (f" UNEXPECTED={extra}" if extra else "")))
    if "nonNull" in expect:
        cols = _as_columns(expect["nonNull"])
        # An absent column reads as null here — the conservative direction. (Load-bearing
        # for spark-sql: Spark's toJSON() drops null-valued fields, so a null Spark column
        # arrives as an absent key.)
        offenders = {c: n for c, n in
                     ((c, sum(1 for r in rows if r.get(c) is None)) for c in cols) if n}
        out.append((not offenders,
                    f"nonNull{cols}" + (f" NULLS={offenders}" if offenders else " clean")))
    return out


def _match_expect(rows, expect):
    """Return (ok, reason) comparing result rows against an expect block.

    Exact matchers (`scalar`/`count`/`rows`) pin values a fixture controls; invariant
    matchers assert value-independent properties for live sources. Any combination may be
    given — every matcher present is evaluated and ANDed, so adding one only ever tightens
    the check.

    **Unknown keys are REFUSED, not skipped** — the house rule, in the one place every
    result-returning check passes through. An empty block is refused, and so is a typo
    ALONGSIDE a real matcher: `{"count": 3, "minCoutn": 500}` would otherwise assert only
    the count while the floor the spec author wrote was never evaluated — the same false
    green as an unread check-level key, one level down.
    """
    unknown = sorted(set(expect) - MATCHER_KEYS)
    if unknown:
        return False, (f"unknown expectation key(s) {unknown} — known matchers: "
                       + ", ".join(sorted(MATCHER_KEYS)))
    results = []
    if "scalar" in expect:
        got = _scalar_of(rows)
        results.append((_values_equal(got, expect["scalar"]),
                        f"scalar got={got!r} expected={expect['scalar']!r}"))
    if "count" in expect:
        got = len(rows)
        results.append((got == expect["count"], f"count got={got} expected={expect['count']}"))
    if "rows" in expect:
        ordered = expect.get("ordered", False)
        results.append((_rows_match(rows, expect["rows"], ordered=ordered),
                        f"rows got={rows} expected={expect['rows']} ordered={ordered}"))
    results += _match_invariants(rows, expect)
    if not results:
        return False, "expect block has none of " + "/".join(_EXACT_KEYS + _INVARIANT_KEYS)
    return all(ok for ok, _ in results), "; ".join(reason for _, reason in results)


# ── cross-family helpers ──────────────────────────────────────────────────────

def _folder_expect(folder_name, expected):
    """Compare an item's resolved workspace-folder name to the contract.

    ``folder_name`` None/'' means the item sits at the workspace root — which is a
    failure whenever a folder is named in the contract, since 'someone left it in the
    root' is exactly the drift the folder check exists to catch.
    """
    got = folder_name or "(root)"
    hit = got == expected
    return hit, f"folder {got!r} {'==' if hit else '!='} expected {expected!r}"


def _folder_check(ctx, folder_id, expected):
    """Resolve `folder_id` through the workspace's folder list and match the contract.

    The shared shape of the lakehouse/notebook/mirror folder assertions. Deliberately
    uncached: each call re-reads the folder list, so a check always sees the placement
    as it is now.
    """
    import fabric_read as fr
    names = {f["id"]: f["displayName"] for f in fr.list_folders(ctx["workspace_id"])}
    return _folder_expect(names.get(folder_id), expected)


def _livy(ctx):
    """The run's single Livy session, created on first use and closed once in `run()`.

    A Livy session costs ~35s to start and holds Spark compute on the capacity until it is
    closed or times out, so every check in a run shares one — per-check sessions cost
    minutes of pure wall clock on a spec with several Spark checks. Any check needing
    Spark goes through here.
    """
    if ctx.get("livy") is None:
        from livy_client import LivyClient
        session = LivyClient(ctx["workspace_id"], ctx["lakehouse_id"])
        session.create_session()
        ctx["livy"] = session
    return ctx["livy"]


def _dp_workspace_names(ctx):
    """{workspace id -> displayName} over every workspace this identity can see (paged).

    Deployment stages carry a `workspaceId`, never a name, so asserting "position 2 holds the
    Prod workspace" needs this map. It is also how `workspace-items` resolves its target — the
    read that stays independent of whatever workspace the run itself is bound to.
    """
    if ctx.get("workspace_names") is None:
        from config import FABRIC_API_BASE
        from fabric_http import paged
        # Via `paged`, not a local cursor loop: this endpoint has been observed returning
        # `continuationToken`, but Fabric also paginates with `continuationUri` and a loop
        # that follows one convention silently truncates on the other. A missing workspace
        # here reads as "that stage points nowhere", which is a wrong verdict, not an error.
        ctx["workspace_names"] = {ws.get("id"): ws.get("displayName")
                                  for ws in paged(f"{FABRIC_API_BASE}/workspaces")}
    return ctx["workspace_names"]


def _resolve_ws_by_name(ctx, name):
    """Workspace GUID for a display name, over every workspace this identity can see.

    Raises (rather than returns) on the two resolution failures — absent and ambiguous —
    leaving the caller to decide whether a throw is a failed check (the git surface) or
    feeds an `exists: false` verdict (the cicd surface).
    """
    matches = [wid for wid, wname in _dp_workspace_names(ctx).items() if wname == name]
    if not matches:
        raise RuntimeError(f"workspace '{name}' MISSING (or not visible to this identity)")
    if len(matches) > 1:
        raise RuntimeError(f"workspace name '{name}' is ambiguous: {matches}")
    return matches[0]


# ── the ARM plane (shared by the ARM-backed families) ─────────────────────────
# Three families assert on resources that live in ARM rather than in Fabric:
#
#     test_keyvault      keyvault-vault · keyvault-secret · keyvault-secret-list
#     test_azureapp      container-app
#     test_azuremonitor  log-analytics-workspace · app-insights-component · foundry-*
#
# The root and the subscription/resource-group pair come from `config` (ARM_BASE,
# require_arm_scope) because the ARM clients in code/clients need them too; the bearer
# header is `auth.get_headers(scope)`. What is left is validation-only and lives here.

AUTHZ_APIVER = "2022-04-01"          # Microsoft.Authorization (roleDefinitions/roleAssignments)


def role_assignment_hit(assignments, role_def_id, scope=None):
    """True if any assignment carries the role (compared by definition GUID, the id's last
    segment — ARM returns full paths whose casing varies), optionally at exactly `scope`.
    The scope filter is what makes 'granted ON the vault' assertable: atScope() listings
    include inherited assignments, and an Owner inherited from the resource group must not
    satisfy a contract that says the build granted a data-plane role on the vault itself."""
    guid = str(role_def_id).rstrip("/").split("/")[-1].lower()
    for a in assignments:
        props = a.get("properties", {})
        if str(props.get("roleDefinitionId", "")).rstrip("/").split("/")[-1].lower() != guid:
            continue
        if scope and str(props.get("scope", "")).lower() != scope.lower():
            continue
        return True
    return False


# ── spec loading + usage errors (shared by the CLI and --lint-source) ─────────

_GUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
                      r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _usage_error(msg):
    """Print a usage/spec error and exit 2 — never a traceback, never exit 1."""
    print(f"ERROR: {msg}", file=sys.stderr)
    raise SystemExit(2)


def _load_spec(spec_path):
    """Parse a spec file, exiting 2 (with the cause) on a bad path or malformed JSON."""
    path = Path(spec_path)
    if not path.is_file():
        _usage_error(f"no such spec file: {spec_path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _usage_error(f"spec {spec_path} is not valid JSON: {exc}")


# ── offline fixtures: the matcher vocabulary ──────────────────────────────────

def _self_test_checks():
    checks = []

    def expect(name, cond):
        checks.append((name, bool(cond)))

    # rows, order-insensitive
    expect("rows-unordered", _rows_match(
        [{"registration": "XYZ-889"}, {"registration": "ABC-123"}],
        [{"registration": "ABC-123"}, {"registration": "XYZ-889"}]))
    # rows, ordered — must fail when order differs
    expect("rows-ordered-mismatch", not _rows_match(
        [{"r": "B"}, {"r": "A"}], [{"r": "A"}, {"r": "B"}], ordered=True))
    expect("rows-ordered-match", _rows_match(
        [{"r": "A"}, {"r": "B"}], [{"r": "A"}, {"r": "B"}], ordered=True))
    # scalar numeric with epsilon (Decimal-as-double)
    ok, _ = _match_expect([{"amount": 89.25}], {"scalar": 89.25})
    expect("scalar-decimal", ok)
    # the Decimal-returns-null gotcha: null must NOT equal the expected number
    ok, _ = _match_expect([{"amount": None}], {"scalar": 89.25})
    expect("scalar-null-fails", not ok)
    # count aggregate returned as a scalar row
    ok, _ = _match_expect([{"n": 3}], {"scalar": 3})
    expect("scalar-count", ok)
    # count matcher = number of rows
    ok, _ = _match_expect([{"r": "A"}, {"r": "B"}], {"count": 2})
    expect("count-rows", ok)
    # wrong count fails
    ok, _ = _match_expect([{"r": "A"}], {"count": 2})
    expect("count-wrong-fails", not ok)

    # ── invariant matchers (live sources, where no exact value is knowable) ──
    _page = [{"id": "a", "views": 10}, {"id": "b", "views": 20}, {"id": "c", "views": 30}]
    # lower bound: "more than one page came back" without pinning how many
    ok, _ = _match_expect(_page, {"minCount": 2})
    expect("minCount-met", ok)
    ok, _ = _match_expect([{"id": "a"}], {"minCount": 2})
    expect("minCount-short-fails", not ok)
    # the read-page-one-and-stop failure: exactly the silent partial load ING-C03 names
    ok, _ = _match_expect(_page[:1], {"minCount": 50})
    expect("minCount-partial-load-fails", not ok)
    ok, _ = _match_expect(_page, {"maxCount": 3})
    expect("maxCount-met", ok)
    ok, _ = _match_expect(_page, {"maxCount": 2})
    expect("maxCount-over-fails", not ok)
    # numeric bound on a scalar that moves between runs
    ok, _ = _match_expect([{"n": 1234}], {"minScalar": 1})
    expect("minScalar-met", ok)
    ok, _ = _match_expect([{"n": 0}], {"minScalar": 1})
    expect("minScalar-zero-fails", not ok)
    # a null scalar must never satisfy a bound (the Decimal-returns-null gotcha again)
    ok, _ = _match_expect([{"n": None}], {"minScalar": 1})
    expect("minScalar-null-fails", not ok)
    ok, _ = _match_expect([{"n": 5}], {"maxScalar": 10})
    expect("maxScalar-met", ok)
    # uniqueness: the cursor-never-advances loop re-emits the same key
    ok, _ = _match_expect(_page, {"unique": "id"})
    expect("unique-met", ok)
    ok, _ = _match_expect(_page + [{"id": "a", "views": 10}], {"unique": "id"})
    expect("unique-dupe-fails", not ok)
    # composite key
    ok, _ = _match_expect([{"a": 1, "b": 1}, {"a": 1, "b": 2}], {"unique": ["a", "b"]})
    expect("unique-composite-met", ok)
    ok, _ = _match_expect([{"a": 1, "b": 1}, {"a": 1, "b": 1}], {"unique": ["a", "b"]})
    expect("unique-composite-dupe-fails", not ok)
    # a key column that isn't there cannot be unique
    ok, _ = _match_expect(_page, {"unique": "missing"})
    expect("unique-absent-column-fails", not ok)
    # schema equality, values ignored
    ok, _ = _match_expect(_page, {"columns": ["id", "views"]})
    expect("columns-met", ok)
    ok, _ = _match_expect(_page, {"columns": ["views", "id"]})
    expect("columns-order-insensitive", ok)
    # source drift: a renamed/added field must fail loudly (ING-C10)
    ok, _ = _match_expect([{"id": "a", "viewCount": 10}], {"columns": ["id", "views"]})
    expect("columns-drift-fails", not ok)
    # pages that disagree on shape are a failure even if each page looks fine alone
    ok, _ = _match_expect([{"id": "a"}, {"id": "b", "extra": 1}], {"columns": ["id"]})
    expect("columns-unstable-shape-fails", not ok)
    # a schema assertion over zero rows must NOT pass vacuously (ING-C12)
    ok, _ = _match_expect([], {"columns": ["id", "views"]})
    expect("columns-empty-fails", not ok)
    # null keys
    ok, _ = _match_expect(_page, {"nonNull": "id"})
    expect("nonNull-met", ok)
    ok, _ = _match_expect([{"id": "a"}, {"id": None}], {"nonNull": "id"})
    expect("nonNull-null-fails", not ok)
    ok, _ = _match_expect([{"id": "a"}, {"other": 1}], {"nonNull": "id"})
    expect("nonNull-absent-column-fails", not ok)
    # stacking: every matcher present is ANDed, so one bad invariant sinks the check
    ok, _ = _match_expect(_page, {"minCount": 2, "unique": "id", "columns": ["id", "views"]})
    expect("invariants-stacked-all-pass", ok)
    ok, _ = _match_expect(_page, {"minCount": 99, "unique": "id"})
    expect("invariants-stacked-one-fails", not ok)
    # exact and invariant matchers compose too
    ok, _ = _match_expect(_page, {"count": 3, "unique": "id"})
    expect("exact-plus-invariant", ok)
    ok, _ = _match_expect(_page, {"count": 3, "unique": "missing"})
    expect("exact-ok-invariant-fails", not ok)
    # an empty expect block is still a hard failure, and names every matcher
    ok, why = _match_expect(_page, {})
    expect("empty-expect-fails", not ok and "minCount" in why and "scalar" in why)
    # a typo ALONGSIDE a real matcher used to pass in silence: the count was asserted and
    # the floor the author wrote was never evaluated. Unknown keys are REFUSED here too.
    ok, why = _match_expect(_page, {"count": 3, "minCoutn": 500})
    expect("unknown-matcher-refused", not ok and "minCoutn" in why)
    ok, why = _match_expect(_page, {"minCoutn": 500})
    expect("unknown-matcher-refused-alone", not ok and "minCoutn" in why)
    expect("matcher-vocabulary-is-the-canonical-one",
           MATCHER_KEYS == frozenset(_EXACT_KEYS + _INVARIANT_KEYS + ("ordered",)))
    # `ordered` is part of the vocabulary, not an unknown key riding along with `rows`
    ok, _ = _match_expect([{"r": "A"}], {"rows": [{"r": "A"}], "ordered": True})
    expect("ordered-modifier-accepted", ok)

    # workspace-folder placement: the naming/filing contract, enforced not just documented
    ok, _r = _folder_expect("OpenMirror", "OpenMirror")
    expect("folder-match", ok)
    ok, _r = _folder_expect("ONT", "OpenMirror")
    expect("folder-wrong-folder", not ok)
    # an item left at the workspace root must fail a contract that names a folder
    ok, _r = _folder_expect(None, "OpenMirror")
    expect("folder-root-fails", not ok and "(root)" in _r)

    return checks


SELF_TESTS = [_self_test_checks]
