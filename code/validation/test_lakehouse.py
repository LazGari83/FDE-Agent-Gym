"""
test_lakehouse.py — the lakehouse check family for the fabric_test harness.

Part of the fabric_test module family (fabric_test.py is the entry point — run that, not
this). Runners: lakehouse-item, lakehouse-table, spark-sql, shortcut — plus the Spark probe
over the run's shared Livy session (`_spark_rows`, `_spark_probe_code`, the result marker),
the spark-sql read-only guard (`_first_keyword`), and the missing-schema-is-absence
helper. RUNNERS declares this family's slice of the check-type registry; SELF_TESTS its
offline fixtures.
"""
import json
import re

import fabric_read as fr
from test_common import (MATCHER_KEYS, _READONLY_KEYWORDS, _first_keyword, _folder_check,
                         _livy, _match_expect, unknown_keys)

SHORTCUT_ROOTS = ("Files", "Tables")


def valid_display_name(name):
    """Fabric rejects hyphens and spaces in a lakehouse display name; underscores are fine."""
    return bool(name) and " " not in name and "-" not in name


def shortcut_path(path):
    """Normalize a shortcut's parent path and refuse one that names no OneLake root.

    `/Files/raw/` -> `Files/raw`. A path not beginning with Files or Tables is rejected here
    rather than at the API, where it is a generic 400 naming neither the rule nor the value.
    """
    cleaned = str(path or "").strip().strip("/")
    root = cleaned.split("/", 1)[0]
    if root not in SHORTCUT_ROOTS:
        raise ValueError(
            f"invalid shortcut path {path!r}: a shortcut's path must begin with one of "
            f"{' or '.join(SHORTCUT_ROOTS)} — those are the only two OneLake roots")
    return cleaned


def onelake_target(item_id, path, workspace_id):
    """The target block of a OneLake-to-OneLake shortcut reference."""
    return {"oneLake": {"workspaceId": workspace_id, "itemId": item_id,
                        "path": shortcut_path(path)}}


def list_shortcuts(item_id, workspace_id, parent_path=None):
    """Every shortcut on the item, following continuation tokens.

    `parent_path` filters CLIENT-side. The API documents a parentPath query parameter, but a
    server-side filter silently ignored would turn "no shortcut under Tables" into a pass —
    filtering what we were actually handed cannot lie in that direction.

    The `.strip("/")` on both sides is load-bearing: LIST returns `path` as `/Files` while
    CREATE and GET return `Files`, so a literal comparison against either spelling silently
    matches nothing.
    """
    from fabric_http import paged
    from config import FABRIC_API_BASE
    out = paged(f"{FABRIC_API_BASE}/workspaces/{workspace_id}/items/{item_id}/shortcuts")
    if parent_path:
        wanted = shortcut_path(parent_path)
        out = [s for s in out if str(s.get("path", "")).strip("/") == wanted]
    return out


# ── lakehouse check runners ───────────────────────────────────────────────────

def _need_lakehouse(ctx):
    if not ctx.get("lakehouse_id"):
        return f"lakehouse '{ctx.get('lakehouse_name')}' not resolved in workspace " \
               f"(create it, or pass --lakehouse-id)"
    return None


def lakehouse_item(lakehouse_id, workspace_id):
    """The lakehouse-TYPED GET — the read that carries `properties` (defaultSchema, paths,
    the SQL endpoint block).

    The generic `fr.get_item` cannot serve this check on the SDK transport: the core `Item`
    model has no `properties` field, so the wire body's lakehouse properties are dropped in
    deserialization and the check reads `defaultSchema=None` on a correctly provisioned
    lakehouse (first live run on the rebased seam, AG-LAK-001 2026-08-27). Per-item-type
    reads live with their check family, so the typed endpoint is addressed here via the
    raw-body escape hatch.
    """
    return fr.get_json(f"/workspaces/{workspace_id}/lakehouses/{lakehouse_id}")


def _run_lakehouse_item(check, ctx):
    """The lakehouse exists with the creation options the contract demands.

    `schemaEnabled` is read from `properties.defaultSchema` — the only trustworthy tell,
    since the flag passed at create time is not echoed back anywhere.
    """
    err = _need_lakehouse(ctx)
    if err:
        return False, err
    item = lakehouse_item(ctx["lakehouse_id"], ctx["workspace_id"])
    props = item.get("properties", {})
    exp = check.get("expect", {})
    reasons = [f"lakehouse '{ctx['lakehouse_name']}' found ({ctx['lakehouse_id']})"]
    ok = True
    if "schemaEnabled" in exp:
        got = bool(props.get("defaultSchema"))
        hit = got == exp["schemaEnabled"]
        ok = ok and hit
        reasons.append(f"schema-enabled got={got} expected={exp['schemaEnabled']}"
                       + ("" if hit else "  (a schemaless lakehouse cannot be converted — recreate it)"))
    if "defaultSchema" in exp:
        got = props.get("defaultSchema")
        hit = got == exp["defaultSchema"]
        ok = ok and hit
        reasons.append(f"defaultSchema got={got!r} expected={exp['defaultSchema']!r}")
    if "sqlEndpoint" in exp:
        got = (props.get("sqlEndpointProperties") or {}).get("provisioningStatus")
        hit = got == exp["sqlEndpoint"]
        ok = ok and hit
        reasons.append(f"sqlEndpoint provisioning got={got!r} expected={exp['sqlEndpoint']!r}")
    if "folder" in exp:
        f_ok, f_reason = _folder_check(ctx, item.get("folderId"), exp["folder"])
        ok = ok and f_ok
        reasons.append(f_reason)
    return ok, "; ".join(reasons)


def _is_missing_schema_error(text):
    """True if a Spark error says the *schema* does not exist (as opposed to the table).

    Matters only for `expect: {exists: false}`. A cross-lakehouse contract asserts that the
    OTHER lakehouse's schema is not present here — e.g. that `raw.policy_event` is absent
    from the silver lakehouse. A correct build never creates a `raw` schema in silver at
    all, so `SHOW TABLES IN raw` throws `[SCHEMA_NOT_FOUND]` instead of returning an empty
    list, and a runner that treats the throw as a failed check makes the assertion
    unpassable. A missing schema is *stronger* evidence of absence than an empty one.
    """
    t = str(text)
    return "SCHEMA_NOT_FOUND" in t or "Database" in t and "not found" in t


def _columns_expect(got, want):
    """Compare a `{column: type}` description against a `columns:` expectation.

    Entries are either a bare name (presence only) or `{"name": ..., "type": ...}`. Pure,
    so it is the same comparison whether the description came from Spark's
    `DESCRIBE TABLE` or from a mirrored Delta table.
    """
    ok, reasons = True, []
    for item in want:
        if isinstance(item, str):
            hit = item in got
            ok = ok and hit
            reasons.append(f"column {item!r}: {'ok' if hit else 'MISSING'}")
            continue
        name, wtype = item["name"], item.get("type")
        if name not in got:
            ok = False
            reasons.append(f"column {name!r}: MISSING")
        elif wtype and got[name].lower() != str(wtype).lower():
            ok = False
            reasons.append(f"column {name!r} type got={got[name]!r} expected={wtype!r}")
        else:
            reasons.append(f"column {name!r} ok ({got[name]})")
    return ok, reasons


def _mirror_table_check(check, ctx):
    """The lakehouse-table assertion answered from a MIRRORED database's Delta layer.

    A suite whose subject is a mirrored database has no lakehouse and therefore no Spark
    session to `DESCRIBE` in, so the check would fail on "lakehouse 'None' not resolved"
    however correct the build was. The replicated Delta table carries exactly the fact the
    check asserts — the columns and their types — so read it there, through the same
    duckdb / data-plane path `delta-sql` uses. Absence is a read error on the Delta log,
    which is the mirror-side equivalent of a table missing from `SHOW TABLES`.
    """
    try:
        from test_openmirror import delta_table_columns
    except ModuleNotFoundError:  # pragma: no cover
        # Only reachable from a spec that names a mirror; if the openmirror check family is
        # not importable, say so plainly rather than dying on the import.
        return False, ("this bundle does not carry the openmirror check family, which a "
                       "mirror-backed lakehouse-table check needs")
    exp = check.get("expect", {})
    schema = check.get("schema") or ctx.get("default_schema") or "dbo"
    table = check["table"]
    label = f"'{schema}.{table}' (mirror '{ctx.get('mirror_name')}')"
    try:
        got = delta_table_columns(ctx, schema, table)
    except Exception as exc:  # noqa: BLE001 — any failure to reach the table is absence
        if exp.get("exists") is False:
            return True, f"table {label} absent as expected ({type(exc).__name__})"
        return False, (f"table {label} could not be read from the mirror's Delta layer: "
                       f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}")
    if exp.get("exists") is False:
        return False, f"table {label} PRESENT but expected absent"
    reasons = [f"table {label} found with {len(got)} column(s)"]
    ok, col_reasons = _columns_expect(got, exp.get("columns") or [])
    return ok, "; ".join(reasons + col_reasons)


def _run_lakehouse_table(check, ctx):
    """A table exists in the lakehouse under the right schema, with the expected columns.

    Goes through **Spark, not REST**: the Lakehouse tables API returns 400
    ``UnsupportedOperationForSchemasEnabledLakehouse`` on a schema-enabled lakehouse, and
    schema-enabled is the variant that matters. `SHOW TABLES IN <schema>` and
    `DESCRIBE TABLE` are the equivalents that do work.

    When the suite names no lakehouse but does name a **mirrored database**, the same
    assertion is answered from the mirror's replicated Delta layer instead — see
    `_mirror_table_check`.
    """
    err = _need_lakehouse(ctx)
    if err:
        if ctx.get("mirror_id") or ctx.get("mirror_name"):
            return _mirror_table_check(check, ctx)
        return False, err
    exp = check.get("expect", {})
    schema = check.get("schema") or ctx.get("default_schema") or "dbo"
    table = check["table"]
    try:
        listed = _spark_rows(ctx, f"SHOW TABLES IN {schema}")
    except Exception as exc:
        if exp.get("exists") is False and _is_missing_schema_error(exc):
            return True, (f"schema '{schema}' does not exist in this lakehouse, so "
                          f"'{schema}.{table}' is absent as expected")
        raise
    names = {str(r.get("tableName", "")).lower() for r in listed}
    present = table.lower() in names
    if exp.get("exists") is False:
        return (not present), (f"table '{schema}.{table}' absent as expected" if not present
                               else f"table '{schema}.{table}' PRESENT but expected absent")
    if not present:
        return False, f"table '{schema}.{table}' MISSING (schema '{schema}' has {sorted(names)})"
    reasons, ok = [f"table '{schema}.{table}' found"], True
    if exp.get("columns"):
        described = _spark_rows(ctx, f"DESCRIBE TABLE {schema}.{table}")
        got = {str(r.get("col_name", "")): str(r.get("data_type", "")) for r in described}
        ok, col_reasons = _columns_expect(got, exp["columns"])
        reasons += col_reasons
    return ok, "; ".join(reasons)


_SPARK_MARKER = "<<<FABRICTESTJSON>>>"


def _spark_probe_code(sql, limit=200):
    """The Python snippet submitted to Livy for one SQL probe (split out so it is testable)."""
    return (f'_rows = spark.sql("""{sql}""").limit({limit}).toJSON().collect()\n'
            f'print("{_SPARK_MARKER}" + "[" + ",".join(_rows) + "]")')


def _spark_rows(ctx, sql, limit=200):
    """Run Spark SQL over one shared Livy session and return rows as plain dicts.

    `LivyClient.sql()` returns `.show()` text, which is unparseable for assertions — so the
    query is submitted as `toJSON().collect()` inside a marker and the structured rows are
    parsed back out. The session is created once per validation run and closed in `run()`.

    **`kind="pyspark"` is not optional.** Livy's `kind="spark"` — the `LivyClient.execute`
    default — is the **Scala** REPL, not Python. `LivyClient.sql()` gets away with the
    default because `spark.sql("...").show()` happens to be valid Scala as well as Python;
    this probe does not, and submitting it to a Scala session fails with a bare
    `<console>:NN: error: not found: value _rows` that names neither Livy nor the language.
    """
    text = _livy(ctx).execute(_spark_probe_code(sql, limit), kind="pyspark") or ""
    if _SPARK_MARKER not in text:
        raise RuntimeError(f"no result marker in Spark output: {text[:300]}")
    return json.loads(text.split(_SPARK_MARKER, 1)[1].strip().splitlines()[0])


_TBL_PLACEHOLDER = re.compile(r"\{table(?::([A-Za-z0-9_./\-]+))?\}")


def _spark_sql_render(sql, default_schema, check):
    """Expand `{table}` / `{table:<schema>/<name>}` into two-part `schema.table` names.

    Same placeholder vocabulary the `delta-sql` runner speaks (see
    `test_openmirror.py::_delta_sql_render`) — the bare form uses the check's own
    `schema`/`table`, the named form carries its own. The named form is what lets one
    statement span two tables — exactly what a reconciliation check is; otherwise the
    placeholder reaches Spark verbatim and dies as `PARSE_SYNTAX_ERROR ... at or near '{'`
    — a runner gap that reads like a broken build.
    """
    def repl(m):
        spec = m.group(1)
        if spec:
            schema, _, table = spec.partition("/")
        else:
            schema = check.get("schema") or default_schema
            table = check.get("table") or ""
            if not table:
                return ""
        return f"{schema}.{table}"
    return _TBL_PLACEHOLDER.sub(repl, sql)


def _effective_limit(check, default=200):
    """How many rows this probe must actually fetch to decide its own expectation.

    The cap exists so a probe cannot drag a whole table back through a Livy `print`, and for
    every matcher that reads *fields* a sample is enough. `minCount` is different: it is a
    statement about the RESULT SET, and it is asserted against the rows that came back — so a
    floor above the cap is unsatisfiable no matter what the build landed. Fetch enough rows
    to settle the floor (never fewer than the cap, and an explicit `limit` on the check
    still raises it), and leave every other matcher's sampling behaviour untouched.
    """
    expect = check.get("expect") or {}
    wanted = [int(check.get("limit", default))]
    if isinstance(expect.get("minCount"), int):
        wanted.append(expect["minCount"])
    return max(wanted)


def _run_spark_sql(check, ctx):
    """Run Spark SQL in the lakehouse and match the rows — the content check for a build.

    `{table}` expands to the fully qualified `schema.table` so a spec's SQL stays readable;
    `{table:<schema>/<name>}` names its own table, so one statement can span two of them.
    The statement is executed verbatim, so the engine's read-only discipline is enforced
    HERE: unless the run carries --allow-write, the first keyword must be one of
    SELECT/WITH/DESCRIBE/SHOW, and a refused statement is a FAILED check, not a crash.
    """
    err = _need_lakehouse(ctx)
    if err:
        return False, err
    if not ctx.get("allow_write"):
        kw = _first_keyword(check["sql"])
        if kw not in _READONLY_KEYWORDS:
            return False, (f"REFUSED: spark-sql statement begins with {kw or '(nothing)'!r}, "
                           "not one of SELECT/WITH/DESCRIBE/SHOW — the engine is read-only; "
                           "pass --allow-write if the write is deliberate")
    schema = check.get("schema") or ctx.get("default_schema") or "dbo"
    sql = _spark_sql_render(check["sql"], schema, check)
    rows = _spark_rows(ctx, sql, limit=_effective_limit(check))
    return _match_expect(rows, check.get("expect", {}))


# ── OneLake shortcuts: the reference half of the copy boundary ────────────────

_SHORTCUT_EXPECT_KEYS = frozenset({
    "exists",                                        # the named shortcut is (or is not) there
    "path",                                          # the OneLake root it hangs under
    "targetType", "targetItem", "targetPathContains",  # WHAT it references
    "count",                                         # how many shortcuts the item carries in total
})


def _shortcut_target_type(target):
    """The target's kind, as a bare word.

    The API returns **both** shapes in one body — ``{"type": "OneLake", "oneLake": {...}}``
    — so the discriminator is always present for a OneLake target and the sub-object-key
    fallback is the belt to its braces (kept for the target kinds not yet observed live).
    Case-insensitive comparison is required, not merely prudent: the two spellings differ
    within that single body ("OneLake" vs "oneLake").
    """
    if not isinstance(target, dict):
        return None
    if target.get("type"):
        return str(target["type"])
    for key, value in target.items():
        if isinstance(value, dict) and value:
            return key
    return None


def _shortcut_target_block(target):
    """The populated target sub-object — the block carrying workspaceId / itemId / path."""
    if not isinstance(target, dict):
        return {}
    for key, value in target.items():
        if key != "type" and isinstance(value, dict) and value:
            return value
    return {}


def _shortcut_expect(name, shortcuts, exp, resolve_item=None):
    """Pure matcher over a listed-shortcuts array. Split out for --self-test.

    Why this check type exists at all: asserting that the data is merely READABLE at a path
    (`lakehouse-table`, `spark-sql`) passes just as happily when the build copied the file
    in. Only the shortcut listing distinguishes "referenced in place" from "copied and
    renamed".

    Two guards:

    * **Unknown expectation keys are REFUSED, not skipped.** A silently ignored key is a
      check that asserts nothing while reading as proof.
    * **A shortcut expected to EXIST must state `targetType`.** The name alone says only
      that something called `name` is there; it says nothing about what it references, and a
      decoy shortcut beside a copied file would satisfy a name-only expectation. `targetType`
      is the key that carries the actual fact. It is not required for `exists: false`, where
      there is no target to describe.

    ``resolve_item`` maps a lakehouse display name to its item id (the runner passes the
    workspace's lakehouse lookup); it is only consulted for `targetItem`.
    """
    ok, why = unknown_keys(exp, _SHORTCUT_EXPECT_KEYS, "shortcut")
    if not ok:
        return False, why
    want_exists = exp.get("exists", True)
    if want_exists and "targetType" not in exp:
        return False, (f"shortcut check for {name!r} expects it to EXIST but states no "
                       "'targetType' — a name alone proves something is there, not that it "
                       "REFERENCES anything, and a copy with a shortcut-shaped name beside it "
                       "is exactly what this check exists to catch")

    by_name = [s for s in shortcuts if str(s.get("name", "")) == name]
    if "path" in exp:
        wanted_path = str(exp["path"]).strip("/")
        matches = [s for s in by_name if str(s.get("path", "")).strip("/") == wanted_path]
    else:
        wanted_path, matches = None, by_name

    reasons, ok = [], True
    if "count" in exp:
        got = len(shortcuts)
        hit = got == exp["count"]
        ok = ok and hit
        reasons.append(f"shortcut count got={got} expected={exp['count']}"
                       + ("" if hit else "  (every reference on this item is counted, "
                                         "whatever its name or path)"))
    if not matches:
        seen = sorted({f"{s.get('path')}/{s.get('name')}" for s in shortcuts})
        if want_exists is False:
            reasons.append(f"no shortcut named {name!r}"
                           + (f" under {wanted_path!r}" if wanted_path else "")
                           + " — absent as expected")
            return ok, "; ".join(reasons)
        reasons.append(f"shortcut {name!r} MISSING"
                       + (f" under path {wanted_path!r}" if wanted_path else "")
                       + f" (this item carries {seen or 'no shortcuts at all'})"
                       + " — data that is only readable is not evidence of a reference: a "
                         "copy reads the same")
        return False, "; ".join(reasons)
    if want_exists is False:
        reasons.append(f"shortcut {name!r} PRESENT but expected absent")
        return False, "; ".join(reasons)

    shortcut = matches[0]
    reasons.append(f"shortcut {name!r} found at {shortcut.get('path')!r}")
    target = shortcut.get("target") or {}
    block = _shortcut_target_block(target)
    if "targetType" in exp:
        got = _shortcut_target_type(target)
        hit = str(got).lower() == str(exp["targetType"]).lower()
        ok = ok and hit
        reasons.append(f"target type got={got!r} expected={exp['targetType']!r}"
                       + ("" if hit else "  (the reference points at a different kind of "
                                         "store than the contract calls for)"))
    if "targetItem" in exp:
        wanted_id = resolve_item(exp["targetItem"]) if resolve_item else None
        got = block.get("itemId")
        if not wanted_id:
            ok = False
            reasons.append(f"targetItem {exp['targetItem']!r}: no lakehouse with that display "
                           "name in the workspace, so the reference cannot be checked against it")
        else:
            hit = str(got).lower() == str(wanted_id).lower()
            ok = ok and hit
            reasons.append(f"target item got={got!r} expected={wanted_id!r} "
                           f"({exp['targetItem']})"
                           + ("" if hit else "  (the reference points at some other item — "
                                             "including, possibly, this lakehouse's own copy)"))
    if "targetPathContains" in exp:
        got = str(block.get("path", ""))
        hit = str(exp["targetPathContains"]).lower() in got.lower()
        ok = ok and hit
        reasons.append(f"target path {got!r} {'contains' if hit else 'DOES NOT CONTAIN'} "
                       f"{exp['targetPathContains']!r}")
    return ok, "; ".join(reasons)


def _run_shortcut(check, ctx):
    """List an item's OneLake shortcuts and match one against the contract.

    Targets the run's bound lakehouse by default; a check may name its own `lakehouse`
    (display name) to assert about another one — which is how a suite says "the item we
    reference INTO gained nothing".
    """
    if check.get("lakehouse"):
        item = fr.resolve_by_name(check["lakehouse"], "Lakehouse", ctx["workspace_id"])
        if not item:
            return False, f"lakehouse '{check['lakehouse']}' not found in workspace"
        item_id, where = item["id"], check["lakehouse"]
    else:
        err = _need_lakehouse(ctx)
        if err:
            return False, err
        item_id, where = ctx["lakehouse_id"], ctx.get("lakehouse_name")

    shortcuts = list_shortcuts(item_id, ctx["workspace_id"])

    def _resolve(name):
        found = fr.resolve_by_name(name, "Lakehouse", ctx["workspace_id"])
        return found.get("id") if found else None

    ok, reason = _shortcut_expect(check["name"], shortcuts, check.get("expect", {}), _resolve)
    return ok, f"[{where}] {reason}"


# ── this family's slice of the check-type registry ────────────────────────────
# (runner, required fields, one-line description, other check-level keys) per type —
# merged by fabric_test.py; a check key outside the declared vocabulary is REFUSED at
# run time, because a key the runner never reads asserts nothing.


# ── Expect-key vocabulary ─────────────────────────────────────────────────────
#
# One entry per check type this module's runners own that does NOT already refuse unknown
# `expect` keys itself. Declared so `fabric_test.expect_key_refusal` can refuse a typo — in
# `--dry-run`, at authoring time, and again before a live run dispatches.
#
# A key here is one the runner reads; anything else asserts nothing.
EXPECT_KEYS = {
    "lakehouse-item": frozenset({"defaultSchema", "folder", "schemaEnabled", "sqlEndpoint"}),
    "lakehouse-table": frozenset({"columns", "exists"}),
    "shortcut": frozenset({
        "count", "exists", "path", "targetItem", "targetPathContains", "targetType",
    }),
    # A pure-query type: its whole `expect` block goes to the shared matcher, so its
    # vocabulary IS MATCHER_KEYS. Declared rather than detected.
    "spark-sql": MATCHER_KEYS,
}

RUNNERS = {
    "lakehouse-item":  (_run_lakehouse_item, (), "the lakehouse exists with the creation options the contract demands", ()),
    "lakehouse-table": (_run_lakehouse_table, ("table",), "a table exists (or does not) with the expected columns (Spark)", ("schema",)),
    "spark-sql":       (_run_spark_sql, ("sql",), "Spark SQL over the lakehouse (read-only unless --allow-write)", ("schema", "table", "limit")),
    "shortcut":        (_run_shortcut, ("name",), "a OneLake shortcut exists (or does not) with the expected path and target", ("lakehouse",)),
}


# ── offline fixtures ──────────────────────────────────────────────────────────

def _raiser(exc):
    """A stand-in whose only behaviour is to raise — used to fake an unreadable table."""
    def _fn(*_args, **_kwargs):
        raise exc
    return _fn


def _self_test_checks():
    checks = []

    def expect(name, cond):
        checks.append((name, bool(cond)))

    # the https-DFS -> abfss conversion the Spark write depends on
    expect("abfss-conversion",
           fr.to_abfss("https://onelake.dfs.fabric.microsoft.com/WS/ITEM/Tables")
           == "abfss://WS@onelake.dfs.fabric.microsoft.com/ITEM/Tables")
    expect("abfss-idempotent",
           fr.to_abfss("abfss://WS@onelake.dfs.fabric.microsoft.com/ITEM/Tables")
           == "abfss://WS@onelake.dfs.fabric.microsoft.com/ITEM/Tables")
    # lakehouse display names: hyphens and spaces are rejected by the API, underscores are not
    expect("lh-name-underscore-ok", valid_display_name("Foundry_Lakehouse_01"))
    expect("lh-name-hyphen-rejected", not valid_display_name("Foundry-Lakehouse-01"))
    expect("lh-name-space-rejected", not valid_display_name("Foundry Lakehouse"))
    # the Spark probe must be submitted as PYSPARK — Livy's default kind is Scala, and the
    # probe is Python. A fake session records what the runner actually asked for.
    class _FakeLivy:
        def __init__(self):
            self.kind = None
        def execute(self, code, kind="spark"):
            self.kind = kind
            return f'{_SPARK_MARKER}[{{"n": 8}}]'
    _fake = _FakeLivy()
    _rows_out = _spark_rows({"livy": _fake}, "SELECT COUNT(*) AS n FROM retail.product")
    expect("spark-probe-uses-pyspark-kind", _fake.kind == "pyspark")
    expect("spark-probe-parses-marker", _rows_out == [{"n": 8}])
    # a cross-lakehouse `exists: false` must pass when the SCHEMA itself is absent — the
    # throw is stronger evidence than an empty table list, not a failed check
    expect("missing-schema-is-absence", _is_missing_schema_error(
        "AnalysisException: [SCHEMA_NOT_FOUND] The schema "
        "`default.Ironclad_Silver.raw` cannot be found."))
    expect("table-not-found-is-not-absence", not _is_missing_schema_error(
        "AnalysisException: [TABLE_OR_VIEW_NOT_FOUND] The table or view "
        "`curated`.`policy_state` cannot be found."))

    # lakehouse-item must read through the TYPED lakehouse GET: the generic item read
    # returns no `properties` on the SDK transport, which failed a correctly provisioned
    # lakehouse as schemaEnabled=False (AG-LAK-001, 2026-08-27). The second fixture pins
    # the regression shape: a properties-less body must FAIL the check, never pass it.
    global lakehouse_item
    _saved_lh_item = lakehouse_item
    _lh_ctx = {"lakehouse_id": "LH", "lakehouse_name": "AG_LAK_001_Foundry",
               "workspace_id": "WS"}
    _lh_expect = {"expect": {"schemaEnabled": True, "defaultSchema": "dbo",
                             "sqlEndpoint": "Success"}}
    try:
        lakehouse_item = lambda lid, wid: {
            "id": lid, "displayName": "AG_LAK_001_Foundry", "type": "Lakehouse",
            "properties": {"defaultSchema": "dbo",
                           "sqlEndpointProperties": {"id": "EP",
                                                     "provisioningStatus": "Success"}}}
        expect("lakehouse-item-typed-read-passes",
               _run_lakehouse_item(_lh_expect, _lh_ctx)[0])
        lakehouse_item = lambda lid, wid: {
            "id": lid, "displayName": "AG_LAK_001_Foundry", "type": "Lakehouse"}
        _ok, _r = _run_lakehouse_item(_lh_expect, _lh_ctx)
        expect("lakehouse-item-propertyless-body-fails",
               not _ok and "schema-enabled got=False" in _r)
    finally:
        lakehouse_item = _saved_lh_item

    # the columns comparison, shared by the Spark and mirrored-Delta descriptions
    _desc = {"group_id": "string", "total_members": "int", "category": "string"}
    expect("columns-bare-name-is-presence-only",
           _columns_expect(_desc, ["group_id", "category"])[0])
    expect("columns-typed-match", _columns_expect(
        _desc, [{"name": "total_members", "type": "int"}])[0])
    _ok, _r = _columns_expect(_desc, [{"name": "total_members", "type": "bigint"}])
    expect("columns-typed-mismatch-fails", not _ok and "got='int'" in "; ".join(_r))
    _ok, _r = _columns_expect(_desc, [{"name": "price_amount_minor", "type": "int"}])
    expect("columns-missing-fails", not _ok and "MISSING" in "; ".join(_r))

    # a mirror-only suite (no lakehouse) answers lakehouse-table from the Delta layer —
    # otherwise a correct build fails on "lakehouse 'None' not resolved".
    # These fixtures cross into the openmirror family, so when that module is not importable
    # they are skipped rather than failing the self-test: the runtime path they cover is
    # unreachable then (it needs a spec naming a mirror), and the runner returns a named
    # refusal there instead of an ImportError.
    try:
        import test_openmirror as _omr
    except ModuleNotFoundError:  # pragma: no cover
        _omr = None
    if _omr is None:
        return checks
    _saved = _omr.delta_table_columns
    _omr.delta_table_columns = lambda ctx, schema, table: dict(_desc)
    try:
        _ctx = {"mirror_id": "MIR", "mirror_name": "AG-ING-009-Culverhouse",
                "default_schema": "dbo"}
        _ok, _r = _run_lakehouse_table(
            {"table": "groups", "schema": "src",
             "expect": {"columns": [{"name": "total_members", "type": "int"}]}}, _ctx)
        expect("mirror-backed-lakehouse-table-passes", _ok and "mirror" in _r)
        _ok, _r = _run_lakehouse_table(
            {"table": "groups", "schema": "src",
             "expect": {"columns": [{"name": "total_members", "type": "bigint"}]}}, _ctx)
        expect("mirror-backed-lakehouse-table-still-asserts-types", not _ok)
        # no lakehouse AND no mirror is still the original hard error, not a silent pass
        _ok, _r = _run_lakehouse_table({"table": "groups", "expect": {}}, {})
        expect("no-lakehouse-no-mirror-still-fails", not _ok and "not resolved" in _r)
        # absence: the Delta table cannot be read, and that is what `exists: false` means
        _omr.delta_table_columns = _raiser(FileNotFoundError("no Delta table at src.groups"))
        expect("mirror-backed-absence-expected-passes", _run_lakehouse_table(
            {"table": "groups", "schema": "src", "expect": {"exists": False}}, _ctx)[0])
        _ok, _r = _run_lakehouse_table({"table": "groups", "schema": "src",
                                        "expect": {"columns": ["group_id"]}}, _ctx)
        expect("mirror-backed-unreadable-table-fails",
               not _ok and "FileNotFoundError" in _r)
    finally:
        _omr.delta_table_columns = _saved

    # the duckdb -> Spark type vocabulary the mirrored description is translated through,
    # reached through `_omr`, the module the guard above already resolved.
    duck_type_to_spark = _omr.duck_type_to_spark
    expect("ducktype-varchar", duck_type_to_spark("VARCHAR") == "string")
    expect("ducktype-integer-is-int", duck_type_to_spark("INTEGER") == "int")
    expect("ducktype-bigint", duck_type_to_spark("BIGINT") == "bigint")
    expect("ducktype-timestamptz",
           duck_type_to_spark("TIMESTAMP WITH TIME ZONE") == "timestamp")
    expect("ducktype-decimal-keeps-precision",
           duck_type_to_spark("DECIMAL(18,2)") == "decimal(18,2)")
    expect("ducktype-unknown-falls-through-visibly",
           duck_type_to_spark("STRUCT(a INTEGER)") == "struct(a integer)")

    class _ThrowLivy:
        def execute(self, code, kind="spark"):
            raise RuntimeError("Spark error: AnalysisException: [SCHEMA_NOT_FOUND] "
                               "The schema `ws.lh.raw` cannot be found.")
    _ok, _reason = _run_lakehouse_table(
        {"table": "policy_event", "schema": "raw", "expect": {"exists": False}},
        {"livy": _ThrowLivy(), "lakehouse_id": "x", "default_schema": "dbo"})
    expect("absent-schema-passes-exists-false", _ok)

    expect("spark-probe-code-is-python",
           '",".join(_rows)' in _spark_probe_code("SELECT 1", 10)
           and "limit(10)" in _spark_probe_code("SELECT 1", 10))

    # the fetch cap must never be the answer to a row-count floor: a minCount above it
    # would be undecidable, reporting the cap itself as the count
    expect("limit-default-when-no-expect", _effective_limit({"sql": "SELECT 1"}) == 200)
    expect("limit-unchanged-under-cap",
           _effective_limit({"expect": {"minCount": 60}}) == 200)
    expect("limit-raised-to-mincount",
           _effective_limit({"expect": {"minCount": 300}}) == 300)
    expect("limit-explicit-still-wins",
           _effective_limit({"limit": 500, "expect": {"minCount": 300}}) == 500)
    expect("limit-ignores-non-int-mincount",
           _effective_limit({"expect": {"minCount": "300"}}) == 200)
    expect("limit-untouched-by-scalar-matchers",
           _effective_limit({"expect": {"minScalar": 900}}) == 200)

    # ── the read-only guard ──
    # the guard's keyword scanner: whitespace and comments never hide (or fake) the verb
    expect("guard-select-ok", _first_keyword("SELECT * FROM t") == "SELECT")
    expect("guard-with-ok", _first_keyword("  WITH x AS (SELECT 1) SELECT * FROM x") == "WITH")
    expect("guard-line-comment-skipped", _first_keyword("-- setup\n  SHOW TABLES IN dbo") == "SHOW")
    expect("guard-block-comment-skipped", _first_keyword("/* note */ DESCRIBE TABLE t") == "DESCRIBE")
    expect("guard-drop-detected", _first_keyword("DROP TABLE t") == "DROP")
    expect("guard-empty-statement", _first_keyword("  -- only a comment\n") == "")
    # bracketed comments nest in Spark SQL (SPARK-28880): '/* /* */ SELECT */' is ONE
    # comment to Spark's lexer, so the verb of this statement is DELETE, not SELECT
    expect("guard-nested-block-comment-not-fooled",
           _first_keyword("/* /* */ SELECT */ DELETE FROM t") == "DELETE")
    expect("guard-unterminated-nested-comment-empty",
           _first_keyword("/* /* */ SELECT") == "")
    # {table} placeholder rendering: the bare form uses the check's own schema/table, the
    # named form carries its own — the same vocabulary delta-sql speaks.
    expect("spark-render-bare",
           _spark_sql_render("SELECT * FROM {table}", "dbo", {"table": "store"})
           == "SELECT * FROM dbo.store")
    expect("spark-render-bare-check-schema",
           _spark_sql_render("SELECT * FROM {table}", "dbo",
                             {"schema": "ops", "table": "ledger"})
           == "SELECT * FROM ops.ledger")
    expect("spark-render-named-two-tables",
           _spark_sql_render("SELECT * FROM {table:live/channel_daily} d CROSS JOIN "
                             "{table:live/traffic_source} t", "dbo", {})
           == "SELECT * FROM live.channel_daily d CROSS JOIN live.traffic_source t")
    expect("spark-render-mixed",
           _spark_sql_render("SELECT (SELECT COUNT(*) FROM {table}) FROM {table:crm/account}",
                             "dbo", {"table": "store"})
           == "SELECT (SELECT COUNT(*) FROM dbo.store) FROM crm.account")
    expect("spark-render-no-placeholder-untouched",
           _spark_sql_render("SELECT 1", "dbo", {}) == "SELECT 1")
    # the runner refuses a write BEFORE any Livy session is created (a session costs ~35s
    # and holds capacity — refusing after paying for one would add injury to insult)
    class _NeverLivy:
        def execute(self, code, kind="spark"):
            raise AssertionError("the guard must refuse before Livy is touched")
    _wctx = {"lakehouse_id": "x", "lakehouse_name": "lh", "default_schema": "dbo",
             "livy": _NeverLivy(), "allow_write": False}
    _ok, _r = _run_spark_sql({"sql": "DELETE FROM {table}", "table": "t"}, _wctx)
    expect("guard-refuses-write", _ok is False and "REFUSED" in _r and "--allow-write" in _r)
    _ok, _r = _run_spark_sql({"sql": "INSERT INTO t VALUES (1)"}, _wctx)
    expect("guard-refuses-insert", _ok is False and "REFUSED" in _r)
    _ok, _r = _run_spark_sql({"sql": "/* /* */ SELECT */ DELETE FROM {table}", "table": "t"}, _wctx)
    expect("guard-refuses-nested-comment-smuggle", _ok is False and "REFUSED" in _r)
    # --allow-write unlocks it (the fake session proves the statement went through)
    _wfake = _FakeLivy()
    _run_spark_sql({"sql": "DELETE FROM t WHERE 1=0"},
                   {"lakehouse_id": "x", "lakehouse_name": "lh", "default_schema": "dbo",
                    "livy": _wfake, "allow_write": True})
    expect("allow-write-unlocks", _wfake.kind == "pyspark")
    # reads never needed the flag
    _rfake = _FakeLivy()
    _run_spark_sql({"sql": "SELECT COUNT(*) AS n FROM {table}", "table": "t"},
                   {"lakehouse_id": "x", "lakehouse_name": "lh", "default_schema": "dbo",
                    "livy": _rfake, "allow_write": False})
    expect("guard-lets-reads-through", _rfake.kind == "pyspark")

    # ── OneLake shortcuts ──
    # the API returns BOTH shapes at once — a `type` discriminator ("OneLake") beside the
    # `oneLake` sub-object.
    _typed = [{"path": "Files", "name": "partner_exports",
               "target": {"type": "OneLake",
                          "oneLake": {"workspaceId": "WS", "itemId": "SRC-ITEM",
                                      "path": "Files/catalogue-exports"}}}]
    _bare = [{"path": "Files", "name": "partner_exports",
              "target": {"oneLake": {"workspaceId": "WS", "itemId": "SRC-ITEM",
                                     "path": "Files/catalogue-exports"}}}]
    _resolver = lambda n: {"Partner_LH": "SRC-ITEM", "Other_LH": "OTHER-ITEM"}.get(n)
    _full = {"path": "Files", "targetType": "OneLake", "targetItem": "Partner_LH",
             "targetPathContains": "catalogue-exports", "count": 1}
    expect("shortcut-typed-target-matches", _shortcut_expect(
        "partner_exports", _typed, _full, _resolver)[0])
    expect("shortcut-bare-target-matches", _shortcut_expect(
        "partner_exports", _bare, _full, _resolver)[0])
    expect("shortcut-target-type-read-from-discriminator",
           _shortcut_target_type(_typed[0]["target"]) == "OneLake")
    expect("shortcut-target-type-falls-back-to-populated-key",
           _shortcut_target_type(_bare[0]["target"]) == "oneLake")
    expect("shortcut-target-type-case-insensitive", _shortcut_expect(
        "partner_exports", _bare, {"targetType": "OneLake"}, _resolver)[0])
    expect("shortcut-target-block-skips-discriminator",
           _shortcut_target_block(_typed[0]["target"]).get("itemId") == "SRC-ITEM")
    # THE headline fixture: a build that copied the file in instead of referencing it has
    # no shortcut to list, so the check fails — every "is the data readable" assertion in
    # this engine would have passed that build.
    _ok, _r = _shortcut_expect("partner_exports", [], _full, _resolver)
    expect("shortcut-copy-instead-of-reference-fails", not _ok and "MISSING" in _r)
    expect("shortcut-missing-reason-names-what-is-there", "no shortcuts at all" in _r)
    # a reference to the WRONG item — the build's own copy, say — is not a pass
    _wrong_item = [{"path": "Files", "name": "partner_exports",
                    "target": {"oneLake": {"workspaceId": "WS", "itemId": "OTHER-ITEM",
                                           "path": "Files/catalogue-exports"}}}]
    _ok, _r = _shortcut_expect("partner_exports", _wrong_item, _full, _resolver)
    expect("shortcut-wrong-target-item-fails", not _ok and "OTHER-ITEM" in _r)
    # a target path that is not the agreed folder
    _ok, _r = _shortcut_expect("partner_exports", _typed,
                               {"targetType": "OneLake", "targetPathContains": "Tables/gold"},
                               _resolver)
    expect("shortcut-wrong-target-path-fails", not _ok and "DOES NOT CONTAIN" in _r)
    # the same name under a different root is not the contracted shortcut
    _ok, _r = _shortcut_expect("partner_exports", _typed,
                               {"path": "Tables", "targetType": "OneLake"}, _resolver)
    expect("shortcut-path-filter-is-real", not _ok and "MISSING" in _r)
    # count is over EVERY shortcut on the item, whatever its name
    _two = _typed + [{"path": "Files", "name": "stray",
                      "target": {"oneLake": {"workspaceId": "WS", "itemId": "SRC-ITEM",
                                             "path": "Files/other"}}}]
    _ok, _r = _shortcut_expect("partner_exports", _two, _full, _resolver)
    expect("shortcut-count-catches-extra-reference", not _ok and "count got=2" in _r)
    # the absence direction — "no shortcuts here", made assertable
    expect("shortcut-absent-as-expected", _shortcut_expect(
        "partner_exports", [], {"exists": False, "count": 0}, _resolver)[0])
    _ok, _r = _shortcut_expect("partner_exports", _typed, {"exists": False}, _resolver)
    expect("shortcut-present-when-absence-expected-fails", not _ok and "PRESENT" in _r)
    # the two guards
    _ok, _r = _shortcut_expect("partner_exports", _typed,
                               {"targetType": "OneLake", "targetPath": "Files/x"}, _resolver)
    expect("shortcut-unknown-key-refused", not _ok and "targetPath" in _r)
    _ok, _r = _shortcut_expect("partner_exports", _typed, {"path": "Files"}, _resolver)
    expect("shortcut-exists-without-targettype-refused", not _ok and "targetType" in _r)
    expect("shortcut-absence-needs-no-targettype",
           _shortcut_expect("partner_exports", [], {"exists": False}, _resolver)[0])
    # an unresolvable targetItem is a failure, never a silent skip
    _ok, _r = _shortcut_expect("partner_exports", _typed,
                               {"targetType": "OneLake", "targetItem": "Nope_LH"}, _resolver)
    expect("shortcut-unresolvable-target-item-fails", not _ok and "Nope_LH" in _r)
    # VERBATIM listing body from the live API. LIST returns `path` with a LEADING SLASH
    # while create/get return it without — same shortcut, same session. A matcher that
    # compared literally would silently match nothing, so the strip("/") on both sides is
    # behaviour under test, not defensive coding.
    _live = [{"name": "distribution_exports", "path": "/Files",
              "target": {"type": "OneLake",
                         "oneLake": {"workspaceId": "WS", "itemId": "SRC-ITEM",
                                     "path": "Files/catalogue-exports"}}}]
    expect("shortcut-live-listing-leading-slash-path-matches", _shortcut_expect(
        "distribution_exports", _live,
        {"path": "Files", "targetType": "OneLake", "targetItem": "Partner_LH",
         "targetPathContains": "Files/catalogue-exports", "count": 1}, _resolver)[0])
    expect("shortcut-live-listing-carries-both-target-shapes",
           _shortcut_target_type(_live[0]["target"]) == "OneLake"
           and _shortcut_target_block(_live[0]["target"])["itemId"] == "SRC-ITEM")

    # the client's encoded rules (offline, no tenant)
    expect("shortcut-path-normalised", shortcut_path("/Files/raw/") == "Files/raw")
    expect("shortcut-path-tables-ok", shortcut_path("Tables") == "Tables")
    try:
        shortcut_path("Documents/raw")
        expect("shortcut-path-rejects-non-root", False)
    except ValueError as exc:
        expect("shortcut-path-rejects-non-root", "Files" in str(exc) and "Tables" in str(exc))
    expect("onelake-target-shape",
           onelake_target("ITEM", "/Files/exports/", "WS")
           == {"oneLake": {"workspaceId": "WS", "itemId": "ITEM", "path": "Files/exports"}})

    return checks


SELF_TESTS = [_self_test_checks]
