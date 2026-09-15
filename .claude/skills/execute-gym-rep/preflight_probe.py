"""Feasibility probe — answer in seconds what otherwise surfaces 30 minutes into a build.

A gym task's environmental prerequisites (a capacity, the right to create a workspace,
an SPN grant on a different surface, a Key Vault) are cheap to test and expensive to
discover late. AG-LAK-004 burned ~29 minutes authoring a notebook before finding that
no Key Vault existed; `--checks key-vault` would have parked it immediately.

Run before spawning a rep.

    python preflight_probe.py --task AG-LAK-004      # the checks THAT task needs
    python preflight_probe.py --tasks AG-LAK-001,AG-LAK-006
    python preflight_probe.py --checks capacity,workspace-create
    python preflight_probe.py --all
    python preflight_probe.py --list

Exit 0 = every requested check passed. Exit 1 = at least one FAILED; park the task rather
than building against a prerequisite that is not there. Exit 2 = nothing failed but a check
was INCONCLUSIVE — it could not be decided from here, which is not the same as a failure and
must not be reported as one (a fresh workspace with no lakehouse yet cannot prove SQL
endpoint auth, and saying "FAIL" would park a runnable task for the wrong reason).

`--task` reads the `prerequisites` block from the task's `validate.json` and runs the
baseline plus whatever that task additionally needs. When a task is not runnable the output
ends with the tasks that *are* — a member who cannot run one task should be pointed at the
ones they can, not left holding a red exit code.

Checks are additive and side-effect-free apart from `workspace-create`, which creates a
throwaway workspace and deletes it again (and says so loudly if the delete fails).

Passing results are cached for 6h in `0_admin/logs/.preflight-cache.json` — gitignored, it
holds facts about your tenant (`--no-cache` to force a re-probe).
Only passes are cached, deliberately: a pass is a stable fact about the tenant, while a
failure is precisely the thing the member has just gone away to fix, and serving them a
cached FAIL after they fixed it would be worse than the latency it saves.
"""
import argparse
import json
import os
import re
import sys
import time

import requests

# The toolkit is the source of truth for credentials; import it rather than re-reading .env.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
_TOOLKIT = os.path.join(_REPO_ROOT, "code")
_SHARED = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_shared")
sys.path.insert(0, _TOOLKIT)
import toolkit_path  # noqa: F401,E402
sys.path.insert(0, _SHARED)

from kbmd import resolve_log_file  # noqa: E402

# The task-id -> validate.json map belongs to the validator (`gym_validate.py`), a SIBLING in
# this folder; import it rather than keeping a second copy of the topic table here. Two copies
# of that map is exactly the drift this repo keeps paying for. Inserting our own directory
# rather than relying on it being sys.path[0] keeps the import working however this module is
# reached — run as a script, imported by gym_validate, or exercised inside a built bundle.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

API = "https://api.fabric.microsoft.com/v1"
PROBE_WS = "ZZ-preflight-probe-delete-me"
CACHE_PATH = str(resolve_log_file(".preflight-cache.json"))
CACHE_TTL = 6 * 3600

# Checks whose answer is too volatile for a six-hour cached PASS to still be a fact. Two
# families, both re-run every time:
#
#   LOCAL state — an installed package version, a binary on PATH. These describe the machine
#   rather than the tenant, cost milliseconds, and change without any tenant event to
#   invalidate against. A cached `node-toolchain` PASS served six hours after Node was
#   uninstalled sends a rep to `npm install` and the late discovery this probe exists to
#   pre-empt.
#
#   CAPACITY state — pausing a capacity is routine cost control: one click, often overnight,
#   with no signal this probe could see. That makes it the single tenant fact most likely to
#   have changed since the cache was written, so `spark-capacity` is decided fresh every run.
#   A beta run was served a 123-minute-old PASS for a capacity that was by then Paused.
NEVER_CACHE = {"fabric-cicd-runtime", "node-toolchain", "browser-toolchain", "spark-capacity"}

# Every task that touches Fabric at all needs these; a spec's `prerequisites` block lists
# only what it needs *on top*, so the common case is an empty block and nothing to maintain.
BASELINE = ["fabric-auth", "capacity", "spark-capacity"]

# The catalogue — `CHECKS = {name: (description, runner)}` — is declared ONCE, at the foot of
# this file where every runner it names already exists. `--list` prints it.

results = []
QUIET = False  # set by --json

# Three states, not two. `None` means the check could not be decided from here — a distinct
# answer from "the prerequisite is absent", and conflating them parks runnable tasks.
INCONCLUSIVE = None


def label(status):
    return "PASS" if status is True else ("FAIL" if status is False else "INCONCLUSIVE")


def record(key, status, detail):
    results.append((key, status, detail))
    if not QUIET:  # --json must emit JSON and nothing else, or the caller cannot parse it
        print(f"[{label(status)}] {key}: {detail}", flush=True)


# ------------------------------------------------------- fabric-plane transport helpers

def _fabric_get(url, headers, *, params=None, timeout=60):
    """A Fabric-plane GET through the toolkit's shared transport retry.

    `fabric_http.transport_retry` retries 429 on any verb and 502/503/504 on reads, honouring
    Retry-After. Unretried, a single throttle or a dropped connection answers as a hard FAIL
    and parks a task that is perfectly runnable — the one sin this probe exists to avoid.
    Returns the Response, so every caller keeps its own verdict semantics and its own message.

    Non-Fabric planes (ARM, Graph, GitHub, Log Analytics, the token endpoint) deliberately
    stay on plain `requests`: this retry policy is the Fabric plane's, and those hosts have
    their own throttling contracts.

    Imported lazily so this module still imports with no toolkit environment configured.
    """
    from fabric_http import transport_retry
    return transport_retry("GET", url, headers=headers, params=params, timeout=timeout)


def _fabric_list(url):
    """`(items, None)` for a Fabric listing read to its LAST page, or `(None, (code, body))`.

    `r.json()["value"]` is the FIRST page only. On a tenant whose Active capacity sorts onto
    page two, that truncation makes a BASELINE check answer "none Active" and parks every
    task in the gym — so every listing whose contents can move a verdict goes through
    `fabric_http.paged`, which follows BOTH Fabric continuation conventions
    (`continuationUri` and `continuationToken`) and retries transport failures underneath.

    `code` is None when no HTTP answer was ever received (a transport failure that survived
    the retries, or a cursor that never advanced) — a state that proves nothing about the
    tenant and must not be reported as a status.
    """
    from fabric_http import paged
    try:
        return paged(url), None
    except requests.HTTPError as exc:
        resp = getattr(exc, "response", None)
        if resp is not None:
            return None, (resp.status_code, resp.text[:200])
        return None, (None, str(exc)[:200])
    except (requests.RequestException, RuntimeError) as exc:
        return None, (None, f"{type(exc).__name__}: {str(exc)[:200]}")


def _list_error(err, what):
    """One line saying why a `_fabric_list` read did not answer."""
    code, body = err
    return (f"HTTP {code} {what}: {body}" if code is not None
            else f"no HTTP answer {what} after retries: {body}")


# --------------------------------------------------------------------------- checks

def _cached_token():
    """A token already in the cache, or None. Never prompts."""
    from auth import get_token
    try:
        return get_token(prompt=False)
    except RuntimeError:
        return None


def check_auth_mode(ctx):
    """Report every sign-in route that works here, not just the configured one.

    The question a prospective member actually has is "can I start today?", and the honest
    answer depends on the tenant, not on us. A user route needs no app registration and no
    Entra administrator; a service principal needs both, and in many organisations that is a
    ticket rather than an afternoon. Reporting both means nobody buys and then discovers
    they cannot sign in.
    """
    from config import FABRIC_AUTH_MODE, AZURE_CLIENT_SECRET
    routes, working = [], []

    # prompt=False: a preflight must never open a browser or print a device code.
    if _cached_token():
        working.append("user (signed in)")
        routes.append("  user                     WORKS — signed in, no app registration needed")
    else:
        routes.append("  user                     not signed in HERE — an unattended run "
                      "cannot prompt; sign in once in this same shell "
                      "(`auth.py --status` says where it looked)")

    if AZURE_CLIENT_SECRET:
        working.append("service principal")
        routes.append("  service principal        credentials present"
                      + ("" if FABRIC_AUTH_MODE == "spn" else " (not the active mode)"))
    else:
        routes.append("  service principal        no AZURE_CLIENT_SECRET set")

    if not QUIET:
        for line in routes:
            print(f"     {line}")
    if not working:
        return False, ("no sign-in route works here. Sign in once with "
                       "`python code/core/auth.py` — in this same shell, since the cache "
                       "is per-HOME (add FABRIC_AUTH_DEVICE_CODE=1 where there is no "
                       "browser) — or set a service principal's credentials in .env")
    return True, f"active mode '{FABRIC_AUTH_MODE}'; routes available: {', '.join(working)}"


def check_fabric_auth(ctx):
    """The configured identity can mint a Fabric token and read its own workspace.

    Mints through `_cached_token` (`prompt=False`) rather than `auth.get_headers`, whose
    `get_token` defaults to `prompt=True`. In user mode with an empty token cache that
    default reaches `auth._sign_in` and BLOCKS on a browser or a device code — and this
    check is force-prepended to every invocation, so an unattended circuit hung here
    instead of parking the task. `prompt=False` is what the module contract has always
    said a preflight needs; this check was the one place not honouring it. SPN mode is
    unaffected: client-credentials never prompts.
    """
    from config import FABRIC_WORKSPACE_ID
    token = _cached_token()
    if token is None:
        from auth import CACHE_PATH
        # Naming the file, and whether it is there, splits the two failures a bare "not
        # signed in" conflates: nobody has signed in yet, versus somebody signed in into a
        # DIFFERENT environment (an agent's sandbox has its own HOME, so the cache a
        # terminal wrote is not on its filesystem at all). Only the second is common after
        # a sign-in that visibly succeeded, and re-running the sign-in does not fix it.
        where = (f"a cache exists at {CACHE_PATH} but holds no usable token for this "
                 "authority — sign in again" if CACHE_PATH.exists() else
                 f"no sign-in cache at {CACHE_PATH}")
        return False, (f"no Fabric token is available without prompting ({where}). A "
                       "preflight must never open a browser or print a device code, so "
                       "this is a FAIL rather than a sign-in. Run `python "
                       "code/core/auth.py` YOURSELF, in the same terminal and the same "
                       "Python you run this probe from (add FABRIC_AUTH_DEVICE_CODE=1 "
                       "where there is no browser), or set a service principal's "
                       "credentials in .env. If a sign-in already succeeded in another "
                       "shell, that shell has a different HOME and this one cannot see it: "
                       "put FABRIC_TOKEN_CACHE=.fabric_kb/msal_cache.json in the repo-root "
                       ".env (repo-relative, so both environments resolve it to the same "
                       "file) and sign in once more. `python code/core/auth.py --status` "
                       "run in each shell shows the two paths. `--checks auth-mode` reports "
                       "which routes this tenant permits")
    ctx["headers"] = {"Authorization": f"Bearer {token}"}
    ctx["workspace_id"] = FABRIC_WORKSPACE_ID
    r = _fabric_get(f"{API}/workspaces/{FABRIC_WORKSPACE_ID}", ctx["headers"])
    if r.status_code != 200:
        return False, f"HTTP {r.status_code} reading the configured workspace: {r.text[:200]}"
    return True, f"authenticated; workspace '{r.json().get('displayName')}' readable"


def _capacity_listing(ctx):
    """`/v1/capacities`, read once per probe run. `(caps, err)` exactly as `_fabric_list`.

    Three checks need this listing and it cannot change inside one run, so it is cached on the
    shared ctx rather than re-fetched — one throttled read is one throttled read, not three.
    """
    if "capacity_list" not in ctx:
        ctx["capacity_list"] = _fabric_list(f"{API}/capacities")
    return ctx["capacity_list"]


def _capacity_entry(ctx, capacity_id):
    """`(row, "")` for one capacity id in that listing, or `(None, why-it-is-undecidable)`.

    Both failure shapes are undecidable rather than absent: the listing may not have answered
    at all, or the capacity may sit outside what this identity can see. Neither proves anything
    about the capacity, so every caller turns this into INCONCLUSIVE, never FAIL.
    """
    caps, err = _capacity_listing(ctx)
    if err is not None:
        return None, _list_error(err, "listing capacities")
    match = next((c for c in caps
                  if str(c.get("id", "")).lower() == str(capacity_id).lower()), None)
    if match is None:
        return None, (f"capacity {capacity_id} is not in the capacity list this identity can "
                      "see, so its SKU and state cannot be read from here")
    return match, ""


def _preferred_capacity(ctx, active):
    """(the Active capacity to probe with, how it was chosen).

    "The first Active entry in /v1/capacities" is the wrong answer to "which capacity does
    this workspace's work run on": a tenant commonly carries several and they are NOT
    interchangeable (`code/clients/workspace_client.py` module docstring note 2;
    `3_wiki/cicd/gotchas.md:78`). The configured workspace's OWN capacity is the right one —
    the same read `capacity-sku` makes — so try that first and fall back to the guess only
    when no workspace capacity is readable or Active, saying so in the message so a member
    never reads the guess as a fact.
    """
    workspace_id = ctx.get("workspace_id")
    if workspace_id:
        own = None
        try:
            w = _fabric_get(f"{API}/workspaces/{workspace_id}", ctx["headers"])
            own = w.json().get("capacityId") if w.status_code == 200 else None
        except (requests.RequestException, ValueError):
            own = None  # a failed read here downgrades the choice; it does not decide the check
        if own:
            match = next((c for c in active
                          if str(c.get("id", "")).lower() == str(own).lower()), None)
            if match:
                return match, "the configured workspace's own capacity"
    return active[0], ("a GUESS — the first Active capacity, because the configured "
                       "workspace's own capacity is not readable or not Active; a tenant's "
                       "capacities are not interchangeable, so confirm before relying on it")


def check_capacity(ctx):
    caps, err = _capacity_listing(ctx)
    if err is not None:
        if err[0] is None:
            # No HTTP answer at all. That is not "no capacity" — nothing was learned.
            return INCONCLUSIVE, (_list_error(err, "listing capacities")
                                  + " — whether a usable capacity exists was not decided")
        return False, _list_error(err, "listing capacities")
    active = [c for c in caps if str(c.get("state", "")).lower() == "active"]
    for c in caps:
        if not QUIET:
            print(f"       capacity: {c.get('displayName')} sku={c.get('sku')} state={c.get('state')}")
    if not active:
        return False, f"{len(caps)} capacities visible, none Active"
    chosen, how = _preferred_capacity(ctx, active)
    ctx["capacity_id"] = chosen["id"]
    return True, (f"{len(active)} active — probing with '{chosen.get('displayName')}' "
                  f"({how}). This check is TENANT-WIDE and passing it does not mean the "
                  "configured workspace's own capacity is usable — `spark-capacity` is the "
                  "check that decides that")


def classify_sku(sku, min_f=2):
    """(status, note) for one capacity SKU literal. Pure — no network, no tenant.

    Three states, deliberately, matching this probe's contract:

      True  — the SKU is one the paid-tier requirement names: an F-SKU at or above `min_f`,
              or a Power BI Premium **per capacity** P-SKU (P1+).
      False — the SKU is one the requirement explicitly EXCLUDES: a trial capacity (`FT*`,
              e.g. `FTL64`) or Premium **per user** (`PP*`, e.g. `PP3`), or an F/P SKU
              below the floor.
      None  — the literal is not one of those shapes. Not a failure: an unrecognized SKU
              cannot be decided from here, and mis-parking a runnable task is the one sin
              this probe exists to avoid. Report the literal and let a human decide.

    Sources for the rule, not for the literals:
      - Microsoft's stated data-agent prerequisite — "A paid F2 or higher Fabric
        capacity, or a Power BI Premium per capacity (P1 or higher) capacity with Microsoft
        Fabric enabled."
      - `0_admin/capabilities/data-agent.md` (DA-C02) — a trial capacity supports none of it, and
        Premium per user (PP3) does not qualify.
    Sources for the literals themselves: `FTL64` (trial) and `PP3` (Premium per user) are
    SKU strings `/v1/capacities` returns; `F<n>` / `P<n>` are the Azure SKU names
    (`sku.name`, e.g. `F2`, `F64`, `P1`). Verify the paid-F spelling against what your own
    tenant's `/v1/capacities` reports the first time this check passes for you.

    DELIBERATE DUPLICATION, do not "fix" it by importing. `code/clients/capacity.py`'s
    `is_paid_tier` carries the same F/FT/P/PP literals, and the two must be kept in step by
    hand. They are not shared because this probe ships in EVERY tier including the trial,
    while `capacity.py` is Premium-only value (the ARM capacity lifecycle) — importing it
    would drag that module into the trial bundle via build.py's `imports` gate and hand a
    paid client to a trial member. Fifteen duplicated lines are cheaper than that leak.
    A boolean could not carry this function's third state anyway: `is_paid_tier` answers
    False both for "trial" and for "I have never seen this string", and collapsing those two
    would park a runnable task on an unrecognised SKU.
    """
    text = str(sku or "").strip().upper()
    if not text:
        return None, "the capacity reports no SKU"
    # `FTL64` is the literal observed in this tenant; the `FT*` family and a literal spelling
    # of "trial" are both treated as trial. UNVERIFIED that any other trial spelling exists —
    # but reading a trial as paid is the one error this check must never make, since it is
    # precisely the deep, late failure it exists to pre-empt.
    is_trial = text.startswith("FT") or "TRIAL" in text
    is_per_user = text.startswith("PP")
    m = re.fullmatch(r"([FP])(\d+)", text)
    if not (m or is_trial or is_per_user):
        return None, (f"'{sku}' is not an F-, P-, trial- or per-user-shaped SKU literal — "
                      "this check cannot classify it")

    if is_trial or is_per_user or not m:
        if is_trial:
            return False, (f"'{sku}' is a TRIAL capacity — a Fabric trial supports none of the "
                           "paid-tier-only features, whatever its apparent size")
        if is_per_user:
            return False, (f"'{sku}' is Power BI Premium PER USER — only Premium *per capacity* "
                           "(P1+) qualifies")
        family = m.group(1)
        return False, (f"'{sku}' is below the floor this check demands "
                       f"({family}{min_f if family == 'F' else 1} or higher)")

    # Shape-recognised F/P SKU. The floor this check demands may be higher than F2.
    family, size = m.group(1), int(m.group(2))
    floor = min_f if family == "F" else 1
    if size < floor:
        return False, (f"'{sku}' is below the floor this check demands "
                       f"({family}{floor} or higher)")
    return True, (f"'{sku}' is a paid {'Fabric F' if family == 'F' else 'Premium per-capacity P'}"
                  f"-SKU at or above {family}{floor}")


def check_capacity_sku(ctx, min_f=2, workspace=None):
    """Is the workspace's capacity a PAID tier, as opposed to trial or per-user?

    The gap `0_admin/capabilities/data-agent.md` names explicitly: nothing here asserted a capacity's
    TIER, only that some capacity was Active — so a data agent task would have authored a
    whole build and failed at create time, thirty minutes in, on a prerequisite decidable in
    one call. A data agent needs a paid F2+ (or P1+ Premium per capacity) capacity and
    **cannot be created on a trial capacity at all** (DA-C02).

    Reads the capacity **the workspace is actually on**, not "the first Active entry in
    /v1/capacities": a tenant commonly carries several and they are not interchangeable
    (this one carries `PP3` and `FTL64`), so the tenant-wide question answers nothing about
    where the build will run. `workspace` overrides the configured workspace GUID; `min_f`
    raises the floor for a task that needs more than F2.

    Side-effect-free. Two things it deliberately does NOT decide, and says so in its detail:

      1. **The AI tenant settings.** DA-C02 also requires cross-geo processing and cross-geo
         storing for AI to be on. Tenant settings are not readable by an SPN (the same
         reason `workspace-create` has to create a throwaway workspace), so this check
         reports them as unverified rather than implying they passed.
      2. **Region alignment.** A data agent cannot query a data source whose workspace
         capacity is in a different region from its own — not a permissions error, the query
         simply cannot execute. The agent's region is reported here; the data source's is
         only knowable once the task has chosen one.
    """
    workspace_id = workspace or ctx.get("workspace_id")
    if not workspace_id:
        return INCONCLUSIVE, "no workspace to read a capacity from (fabric-auth did not run)"
    w = _fabric_get(f"{API}/workspaces/{workspace_id}", ctx["headers"])
    if w.status_code != 200:
        return INCONCLUSIVE, f"HTTP {w.status_code} reading workspace {workspace_id}: {w.text[:200]}"
    capacity_id = w.json().get("capacityId")
    if not capacity_id:
        return False, (f"workspace '{w.json().get('displayName')}' has NO capacity assigned — "
                       "there is no tier to qualify. Assign it to a paid F2+ capacity")

    capacity, why = _capacity_entry(ctx, capacity_id)
    if capacity is None:
        return INCONCLUSIVE, f"the workspace is on capacity {capacity_id}, but {why}"

    sku = capacity.get("sku")
    name = capacity.get("displayName")
    region = capacity.get("region")
    state = capacity.get("state")
    status, note = classify_sku(sku, min_f=min_f)
    where = f"capacity '{name}' (sku={sku}, region={region}, state={state})"
    if status is False:
        return False, (f"{where}: {note}. Nothing on a paid-tier-only surface can be built "
                       "here — assign the workspace to a paid F2+ Fabric capacity (or a P1+ "
                       "Premium per-capacity), or run this task on a tenant that has one")
    if status is None:
        return INCONCLUSIVE, (f"{where}: {note}. Decide it by hand before building: the "
                              "requirement is a paid F2+ Fabric capacity or a P1+ Premium "
                              "per-capacity")
    if str(state or "").lower() not in ("active", ""):
        return False, (f"{where}: {note}, but the capacity is not Active — a paused capacity "
                       "serves nothing")
    return True, (f"{where}: {note}. NOT decided here: the cross-geo processing / cross-geo "
                  "storing for AI TENANT SETTINGS (this probe does not read them; an SPN CAN — "
                  "GET /v1/admin/tenantsettings, proven AG-DAT-009 — so check "
                  f"portal), and region alignment (this capacity is in '{region}'; a data "
                  "source whose workspace capacity is in another region cannot be queried at "
                  "all)")


def check_workspace_create(ctx):
    """The one check with a side effect. Creates and deletes a throwaway workspace,
    because the tenant setting that governs this cannot be read back by the SPN."""
    body = {"displayName": PROBE_WS, "description": "throwaway preflight probe"}
    if ctx.get("capacity_id"):
        body["capacityId"] = ctx["capacity_id"]
    r = requests.post(f"{API}/workspaces", headers=ctx["headers"], json=body, timeout=120)
    if r.status_code not in (200, 201):
        return False, (f"HTTP {r.status_code} {r.text[:300]} — most often the tenant setting "
                       "'Service principals can create workspaces' is off")
    ws_id = r.json()["id"]
    detail = f"created {ws_id}"
    try:
        g = _fabric_get(f"{API}/workspaces/{ws_id}", ctx["headers"])
        assigned = g.json().get("capacityId") if g.status_code == 200 else None
        detail += f"; capacityId={assigned}" if assigned else "; NO capacity assigned (Spark will refuse to start)"
        ok = bool(assigned) or not ctx.get("capacity_id")
    finally:
        d = requests.delete(f"{API}/workspaces/{ws_id}", headers=ctx["headers"], timeout=120)
        if d.status_code not in (200, 204):
            detail += f" — CLEANUP FAILED (HTTP {d.status_code}), DELETE {ws_id} MANUALLY"
        else:
            detail += "; cleaned up"
    return ok, detail


def check_sql_endpoint(ctx):
    """The SPN's grant on the SQL analytics endpoint, which is a different audience from the
    Fabric REST API (database.windows.net, not api.fabric.microsoft.com).

    What this proves is the **grant**, not the driver. The ODBC driver used here is whatever
    the local machine happens to have; the rep's notebook runs inside the Fabric Spark
    runtime, which carries **ODBC Driver 18 for SQL Server** and nothing else. A local box
    with only driver 17 can therefore prove the grant perfectly well while a notebook written
    against 17 still dies inside Fabric — so a missing driver here is INCONCLUSIVE, never a
    FAIL, and the pass text names the driver it actually used so the distinction stays visible.
    """
    import struct
    lakes, _err = _fabric_list(f"{API}/workspaces/{ctx['workspace_id']}/lakehouses")
    lakes = lakes or []
    server = database = name = None
    for lh in lakes:
        sqlep = ((lh.get("properties") or {}).get("sqlEndpointProperties") or {})
        if sqlep.get("connectionString"):
            server, database, name = sqlep["connectionString"], sqlep.get("id"), lh.get("displayName")
            break
    if not server:
        # A workspace with no lakehouse yet is the NORMAL state at the start of a rep that
        # creates one. Returning False here parked AG-LAK-006 for the wrong reason.
        return INCONCLUSIVE, (f"{len(lakes)} lakehouse(s) in the workspace, none exposing a "
                              "connectionString yet — endpoint auth cannot be decided until one "
                              "exists. Normal on a fresh workspace; re-run after the build "
                              "creates its lakehouse.")
    try:
        import pyodbc
    except ImportError:
        return INCONCLUSIVE, ("pyodbc not importable locally — cannot test the grant from here. "
                              "The rep itself runs inside the Fabric Spark runtime, which has it.")
    drivers = [d for d in pyodbc.drivers() if "SQL Server" in d]
    if not drivers:
        return INCONCLUSIVE, (f"no SQL Server ODBC driver on this machine (available: {pyodbc.drivers()}) — "
                              "cannot test the grant from here. Fabric's Spark runtime carries "
                              "'ODBC Driver 18 for SQL Server'; write the notebook against 18.")
    from config import AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET
    tr = requests.post(
        f"https://login.microsoftonline.com/{AZURE_TENANT_ID}/oauth2/v2.0/token",
        data={"grant_type": "client_credentials", "client_id": AZURE_CLIENT_ID,
              "client_secret": AZURE_CLIENT_SECRET, "scope": "https://database.windows.net/.default"},
        timeout=60)
    if tr.status_code != 200:
        return False, f"database.windows.net token refused: HTTP {tr.status_code} {tr.text[:250]}"
    raw = tr.json()["access_token"].encode("utf-16-le")
    cs = (f"Driver={{{drivers[-1]}}};Server={server},1433;Database={database};"
          "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=60;")
    try:
        cn = pyodbc.connect(cs, attrs_before={1256: struct.pack("<i", len(raw)) + raw}, timeout=60)
        cn.cursor().execute("SELECT 1").fetchone()
        cn.close()
    except Exception as e:
        # A refusal and a transport failure are different answers. SQL Server states an
        # authorization refusal in the SQLSTATE (28000 / 42000, "Login failed"), and that
        # genuinely proves the grant is absent — FAIL. A connect that never got that far (a
        # timeout, a DNS/TCP failure, an unclassified driver error) proves nothing about the
        # grant, and parking a rep on it is the sin this probe exists to avoid.
        text = str(e)
        refused = ("28000", "42000", "Login failed", "Cannot open server",
                   "not associated with a trusted SQL Server connection")
        if any(marker in text for marker in refused):
            return False, f"{type(e).__name__} connecting to {name}'s endpoint: {text[:300]}"
        return INCONCLUSIVE, (f"{type(e).__name__} reaching {name}'s endpoint: {text[:300]} — no "
                              "authorization answer came back (this reads as transport, not a "
                              "refusal), so the SPN's grant on the endpoint was not decided")
    return True, (f"SPN grant on '{name}' works (SELECT 1 via local {drivers[-1]}). "
                  "The grant is what this proves — inside Fabric the driver is 18.")


def check_key_vault(ctx, secret_name=None):
    """This is the check that would have parked AG-LAK-004 in ten seconds.

    With `secret_name`, the vault existing is not enough — the named secret must be in it.
    AG-LAK-004 asserts a *salted* digest, so the salt cannot be inlined and a reachable but
    empty vault fails the task just as surely as no vault at all.
    """
    url = os.environ.get("AZURE_KEYVAULT_URL")
    if not url:
        try:
            import config
            url = getattr(config, "AZURE_KEYVAULT_URL", None)
        except Exception:
            url = None
    if not url:
        return False, ("no AZURE_KEYVAULT_URL in the environment or .env — a task needing a "
                       "secret has nowhere to read it from. Create a vault, add the secret, grant the "
                       "SPN 'Key Vault Secrets User', and record the URI in .env")
    try:
        from azure.keyvault.secrets import SecretClient
        from auth import get_credential
        client = SecretClient(vault_url=url, credential=get_credential())
        names = [s.name for s in client.list_properties_of_secrets()]
    except ImportError as e:
        return False, f"azure-keyvault-secrets not installed ({e})"
    except Exception as e:
        # 401/403/404 from the vault IS the verdict, and so is a host that does not resolve
        # — that is AG-LAK-004's absence, the case this check was written for. Anything else
        # (a reset, a proxy hiccup, an unclassified client error) decided nothing about the
        # vault or the grant, and must not park the task on a transport event.
        status = (getattr(e, "status_code", None)
                  or getattr(getattr(e, "response", None), "status_code", None))
        unresolvable = any(marker in str(e) for marker in (
            "NameResolutionError", "Failed to resolve", "getaddrinfo",
            "Name or service not known", "nodename nor servname"))
        if status in (401, 403, 404) or unresolvable:
            return False, f"{type(e).__name__} against {url}: {str(e)[:300]} — check the vault exists and the SPN has Secrets User"
        return INCONCLUSIVE, (f"{type(e).__name__} against {url}: {str(e)[:300]} — the vault "
                              "returned no authorization verdict, so neither its existence nor "
                              "the SPN's Secrets User grant was decided from here")
    if secret_name and secret_name not in names:
        return False, (f"{url} is readable but holds no secret named '{secret_name}' "
                       f"(found: {names[:8] or '(none)'}) — the task reads it at run time")
    return True, (f"{url} readable; secret '{secret_name}' present" if secret_name
                  else f"{url} readable; {len(names)} secret(s) visible: {names[:5] or '(none)'}")


def check_arm_plane(ctx):
    """The key-vault gym's control-plane prerequisite (spec: `0_admin/capabilities/key-vault.md`).

    KV lifecycle tasks create vaults and role assignments in a dedicated resource group,
    which needs the ARM plane (`management.azure.com`) — a different audience from every
    Fabric check here. Proves three things in one GET: both env vars are set, the SPN can
    mint an ARM token, and the resource group is visible to it. What it deliberately does
    NOT prove is *create* rights (vaults, role assignments) — those cannot be tested
    side-effect-free, so a 200 here with a create-denied later means the SPN has read but
    not write on the group (it needs Owner, or Contributor + Role Based Access Control
    Administrator). A 403 here means not even read.
    """
    scope, why, verdict = _arm_token_and_scope(
        env_remedy=" — KV lifecycle tasks have no resource group to create vaults in. Set "
                   "both; the group should be dedicated to the gym with the SPN granted "
                   "rights to create vaults AND role assignments on it")
    if scope is None:
        if verdict is INCONCLUSIVE:
            return INCONCLUSIVE, (f"{why} — the identity provider never answered, so whether "
                                  "this SPN can read the resource group was not decided")
        return False, why
    token, sub, rg = scope
    r = requests.get(
        f"https://management.azure.com/subscriptions/{sub}/resourceGroups/{rg}",
        params={"api-version": "2021-04-01"},
        headers={"Authorization": f"Bearer {token}"}, timeout=60)
    if r.status_code == 403:
        return False, (f"ARM token acquired but resource group '{rg}' returned 403 — the SPN holds "
                       "no role on it. Grant Owner (or Contributor + Role Based Access Control "
                       "Administrator) on the dedicated group")
    if r.status_code == 404:
        return False, (f"resource group '{rg}' not found in subscription {sub} — create it (or fix "
                       "AZURE_RESOURCE_GROUP). 404 can also mean the SPN cannot see the "
                       "subscription at all")
    if r.status_code != 200:
        return False, f"HTTP {r.status_code} reading resource group '{rg}': {r.text[:250]}"
    loc = r.json().get("location")
    return True, (f"resource group '{rg}' readable ({loc}). Read proven; vault/role-assignment "
                  "CREATE rights are not testable side-effect-free — a later create-denied means "
                  "the SPN has read but not write here")


def check_onelake_storage(ctx):
    """Reach OneLake over the storage scope (dfs.fabric.microsoft.com), not the REST API.

    The path must name an item AND a folder inside it. Two shapes both return BadRequest and
    neither means the tenant refused anything:

    - workspace root       -> `Either WorkspaceId or ArtifactId are missing in the request`
    - item root (the GUID) -> `OperationNotAllowedOnThePath`
    - `<item-guid>/Tables` -> works

    (A display name is rejected too, with `FriendlyNameSupportDisabled` — OneLake wants the
    GUID.) Reporting either BadRequest as a failed prerequisite parked AG-LAK-011 on a tenant
    where eleven openmirror reps had already used this path successfully, which is the exact
    false-park this probe exists to prevent. The toolkit (`landing_zone.TableRef.folder`,
    `tables_abfss_root`) always addresses `<item-guid>/Files|Tables/...`; so does this.
    """
    from auth import get_datalake_client
    items, _err = _fabric_list(f"{API}/workspaces/{ctx['workspace_id']}/items")
    items = items or []
    target = next((i for i in items if i.get("type") in ("Lakehouse", "MirroredDatabase")), None)
    if not target:
        return INCONCLUSIVE, ("no lakehouse or mirrored database in the workspace to address — OneLake "
                              "paths name an item, so reach cannot be tested until one exists. Normal "
                              "on a fresh workspace.")
    try:
        fs = get_datalake_client().get_file_system_client(ctx["workspace_id"])
        fs.get_directory_client(f"{target['id']}/Tables").exists()
    except Exception as e:
        # 401/403 is the tenant/RBAC verdict this check exists to surface. Anything else —
        # a transport failure, or one of the BadRequest shapes the docstring above catalogues
        # — is exactly the answer that once parked AG-LAK-011 on a path eleven reps had used.
        status = (getattr(e, "status_code", None)
                  or getattr(getattr(e, "response", None), "status_code", None))
        if status in (401, 403):
            return False, (f"{type(e).__name__}: {str(e)[:300]} — usually the tenant setting 'Users can access "
                           "data stored in OneLake with apps external to Fabric' is off, or the SPN lacks workspace RBAC")
        return INCONCLUSIVE, (f"{type(e).__name__}: {str(e)[:300]} — OneLake returned no "
                              "authorization verdict over the storage scope, so neither the "
                              "external-apps tenant setting nor the SPN's workspace RBAC was "
                              "decided from here")
    return True, f"OneLake reachable over the storage scope (addressed '{target.get('displayName')}')"


def check_git_integration(ctx):
    """Can this tenant connect a workspace to a git provider at all?

    A task whose promotion mechanism is source control ("items flow into the repository",
    "arrive there through source control") cannot be satisfied by copying items over REST —
    that is a different build with the same end state, and the graded checks usually cannot
    tell them apart, so the task passes while teaching nothing.

    Connecting needs a git provider the SPN can actually reach: Azure DevOps (organization +
    project, bound to the caller's identity) or GitHub (which requires a Fabric *connection*
    object holding a PAT). Both are tenant-level setup, neither is SPN-self-serviceable, and
    a session-scoped agent token must never be persisted into a cloud tenant as one.

    Side-effect-free: reads the workspace's git connection state and the connection list.

    SCOPE: this check is about *connecting*, and says nothing about whether the credential may
    CREATE a repository — a separate grant that /user/repos cannot see. If the task provisions
    its own repository, run `git-repo-create` too; passing this one and failing that one is
    exactly how a repo-provisioning rep dies on its first action.
    """
    r = _fabric_get(f"{API}/workspaces/{ctx['workspace_id']}/git/connection", ctx["headers"])
    if r.status_code != 200:
        return INCONCLUSIVE, (f"git/connection returned {r.status_code}: {r.text[:200]} — the git API "
                              "could not be read, so nothing is proven either way")
    state = (r.json() or {}).get("gitConnectionState")
    if state and state != "NotConnected":
        return True, f"workspace is already git-connected (state '{state}')"

    conns, _err = _fabric_list(f"{API}/connections")
    conns = conns or []
    git_conns = [x for x in conns
                 if "git" in json.dumps(x).lower() or "hub" in json.dumps(x).lower()]
    if git_conns:
        return INCONCLUSIVE, (f"{len(git_conns)} candidate git connection(s) visible but the workspace is "
                              "NotConnected — a connect may work; decide from the connection's provider")
    # No connection object yet. That is NOT a tenant verdict: minting the credential-holder
    # connection is the deliverable of the topic's bootstrap task (AG-CICD-003), so failing
    # here would park the one task whose job is to fix it. What actually decides the question
    # is whether a durable provider credential exists for that task to hold — which is the
    # prerequisite the specs name and the check below is the only thing that tests.
    cred_ok, cred_detail = _git_credential_state()
    if cred_ok is False:
        return False, (f"workspace is '{state}', no git connection object exists, and no usable provider "
                       f"credential is available to create one: {cred_detail}")
    if cred_ok is True:
        return INCONCLUSIVE, (f"workspace is '{state}' and no git connection object exists yet, but a usable "
                              f"provider credential is present ({cred_detail}) — creating the holder is the "
                              "bootstrap task's own work, so this is the expected start state")
    return INCONCLUSIVE, (f"workspace is '{state}', no git connection object exists, and the provider "
                          f"credential could not be decided from here: {cred_detail}")


def _github_channel_is_mediated():
    """Is api.github.com reachable DIRECTLY, or does a session proxy own the channel?

    The decisive lesson: in a managed cloud session, an egress proxy can
    intercept api.github.com and answer with its own policy and its own credential,
    IGNORING any Authorization header the caller sends. Every in-container test of a PAT
    is then a test of the proxy, not the token — an in-container credential check
    (capability-call and scope-sniffing alike) confidently mis-attributes proxy
    policy to the token itself.

    The discriminator is a deliberately invalid token: GitHub itself always answers 401
    "Bad credentials"; anything else (200, or a 403 with proxy prose) proves mediation.
    Returns (True|False|None, detail); None = network failure, nothing proven.
    """
    try:
        r = requests.get("https://api.github.com/user", timeout=30, headers={
            "Authorization": "Bearer github_pat_deliberately_invalid_probe",
            "Accept": "application/vnd.github+json",
            "User-Agent": "fabric-gym-preflight-probe",
        })
    except Exception as e:
        return None, f"api.github.com unreachable ({type(e).__name__})"
    if r.status_code == 401:
        return False, "direct channel (junk token got GitHub's own 401)"
    return True, (f"junk token got {r.status_code}, not GitHub's 401 — a session proxy owns this "
                  "channel and ignores caller credentials")


def _github_pat_identity():
    """The preamble both PAT checks share: FABRIC_GIT_PAT presence, channel mediation, and
    the /user identity call. Returns (status, headers, login, detail):

      (False, None, None, detail)         the PAT is missing, or the provider rejected it
      ("mediated", None, None, detail)    a session proxy owns the channel — each caller
                                          keeps its own verdict and wording for this
      (INCONCLUSIVE, None, None, detail)  the channel or the identity call could not be
                                          decided from here
      (True, headers, login, "")          authenticated on a direct channel

    Never returns or logs the token itself.
    """
    pat = os.environ.get("FABRIC_GIT_PAT", "").strip()
    if not pat:
        return False, None, None, "FABRIC_GIT_PAT is not set in the environment or .env"

    mediated, chan_detail = _github_channel_is_mediated()
    if mediated is None:
        return INCONCLUSIVE, None, None, chan_detail
    if mediated:
        return "mediated", None, None, chan_detail

    h = {"Authorization": f"Bearer {pat}", "Accept": "application/vnd.github+json",
         "User-Agent": "fabric-gym-preflight-probe"}
    try:
        who = requests.get("https://api.github.com/user", headers=h, timeout=30)
    except Exception as e:  # network failure proves nothing about the token
        return INCONCLUSIVE, None, None, f"could not reach api.github.com ({type(e).__name__})"
    if who.status_code != 200:
        return False, None, None, f"FABRIC_GIT_PAT rejected by the provider (HTTP {who.status_code})"
    return True, h, (who.json() or {}).get("login", "?"), ""


def _git_credential_state():
    """Is FABRIC_GIT_PAT a durable provider credential the tenant may keep?

    Returns (True|False|INCONCLUSIVE, detail). Never returns or logs the token itself.

    Two traps, in the order they must be tested:

    1. **The channel may not be GitHub.** See _github_channel_is_mediated(). On a mediated
       channel no in-container answer about the PAT is trustworthy → INCONCLUSIVE, with the
       remedy that provider-side work must ride the platform's own GitHub route (the remote
       GitHub MCP acting as the user's GitHub identity), not a raw PAT.
    2. **A token that authenticates is not a token that can act.** On a direct channel the
       verdict is a CAPABILITY call (/user/repos), never the `x-oauth-scopes` header. The
       header CLASSIFIES the credential and never decides it: read directly from GitHub a
       fine-grained PAT OMITS it (proven — `scope_header_present:
       False`), while present-but-empty is the classic-no-scope / session-token signature and
       is a genuine park signal. (A note here once said fine-grained
       PATs return it "absent or empty depending on endpoint" — that reading came from
       proxy-generated observations. The capability call is still the right verdict, because
       the absent case is undecidable by reading; only the premise was wrong.)
    """
    status, h, login, detail = _github_pat_identity()
    if status == "mediated":
        return False, (f"{detail}. In a managed cloud session the container has NO "
                       "GitHub REST lane at all — even the session's OWN source repo returns 403 — so "
                       "github_client (and every provider-side check in gym_validate.py) cannot work here, "
                       "whatever the credentials. git-protocol and the GitHub MCP do work, but cannot serve "
                       "every asserted field. Run git-integration tasks from a LOCAL session where "
                       "api.github.com is directly reachable with FABRIC_GIT_PAT. The Fabric-side connection "
                       "credential is unaffected — Fabric calls GitHub from its own cloud, not this proxy")
    if status is not True:
        return status, detail

    # Capability, not scope strings: /user/repos is the cheapest authorized read that a
    # repo-creating token must be able to do.
    try:
        cap = requests.get("https://api.github.com/user/repos?per_page=1", headers=h, timeout=30)
    except Exception as e:
        return INCONCLUSIVE, f"identity call succeeded but the capability call failed ({type(e).__name__})"
    if cap.status_code != 200:
        msg = ""
        try:
            msg = str((cap.json() or {}).get("message", ""))[:120]
        except Exception:
            pass
        return False, f"authenticates as '{login}' but cannot list repositories: HTTP {cap.status_code} {msg}"

    # If the repositories live under an organization, authenticating is not the question —
    # whether this credential can see that org is. A fine-grained PAT awaiting org approval
    # authenticates perfectly and then 404s on the org, which reads as a typo, not a grant.
    owner = os.environ.get("FABRIC_GIT_OWNER", "").strip()
    if owner and owner.lower() != login.lower():
        try:
            o = requests.get(f"https://api.github.com/orgs/{owner}", headers=h, timeout=30)
        except Exception as e:
            return INCONCLUSIVE, f"credential works as '{login}', but the org lookup failed: {type(e).__name__}"
        if o.status_code != 200:
            return False, (f"authenticates as '{login}' and can act on its own repositories, but "
                           f"organization '{owner}' answers {o.status_code} — the org has not enabled "
                           f"fine-grained PAT access, or this token is still awaiting org-owner approval; "
                           f"creating repositories there may additionally need the token's Administration "
                           f"grant at organization level")
        return True, f"acts as '{login}', organization '{owner}' visible"
    return True, f"acts as '{login}', identity and repository-listing both succeed"


def check_git_repo_create(ctx, repo=None):
    """Can FABRIC_GIT_PAT CREATE a repository, as opposed to writing to granted ones?

    The expensive lesson: `git-integration` can pass every question it asks — the channel is
    direct, the PAT authenticates, `/user/repos` lists repositories with push on all
    of them, the org is visible — and the rep then dies on its FIRST action, because a
    fine-grained PAT scoped to *selected repositories* holds no organization-level
    `Administration: write`, and that is the only grant that authorizes creation. Half an
    hour of classify/draft/plan buys an answer this check returns in one call.

    The discriminating fact: a *list* of writable repositories proves nothing about creation,
    and neither does a contents `PUT` to a granted repo. Only `POST /orgs/{org}/repos` decides
    it, so that is what this calls.

    Side-effect-free by construction. It POSTs the name of a repository that ALREADY EXISTS,
    because GitHub authorizes before it validates: an unauthorized token gets 403 "Resource not
    accessible by personal access token"; an authorized one gets 422 "name already exists on
    this account" — and creates nothing either way. Existence is confirmed with a GET first, so
    the 201 branch is unreachable; it is still handled, loudly, rather than assumed away.
    """
    status, h, login, detail = _github_pat_identity()
    if status == "mediated":
        return INCONCLUSIVE, (f"{detail} — no in-container answer about this PAT is trustworthy. "
                              "Run from a local session; see git-integration's remedy")
    if status is not True:
        return status, detail
    owner = os.environ.get("FABRIC_GIT_OWNER", "").strip() or login
    is_org = owner.lower() != login.lower()

    # A repository that certainly exists under `owner` — the safe collision target.
    probe_repo = repo
    if not probe_repo:
        try:
            listing = requests.get("https://api.github.com/user/repos?per_page=100",
                                   headers=h, timeout=30)
            names = [r["name"] for r in (listing.json() if listing.status_code == 200 else [])
                     if (r.get("owner") or {}).get("login", "").lower() == owner.lower()]
            probe_repo = names[0] if names else None
        except Exception as e:
            return INCONCLUSIVE, f"could not list repositories to pick a collision target ({type(e).__name__})"
    if not probe_repo:
        return INCONCLUSIVE, (f"no existing repository under '{owner}' is visible to this token, so there is "
                              "no name that can be POSTed without risking a real create — pass "
                              "`--checks git-repo-create` with params {'repo': '<an existing repo>'} to decide it")
    try:
        exists = requests.get(f"https://api.github.com/repos/{owner}/{probe_repo}", headers=h, timeout=30)
    except Exception as e:
        return INCONCLUSIVE, f"could not confirm the collision target exists ({type(e).__name__})"
    if exists.status_code != 200:
        return INCONCLUSIVE, (f"'{owner}/{probe_repo}' did not confirm as existing (HTTP {exists.status_code}); "
                              "refusing to POST a name that might actually be created")

    url = (f"https://api.github.com/orgs/{owner}/repos" if is_org
           else "https://api.github.com/user/repos")
    try:
        r = requests.post(url, headers=h, timeout=30, json={"name": probe_repo, "private": True})
    except Exception as e:
        return INCONCLUSIVE, f"the create probe could not be sent ({type(e).__name__})"

    msg = ""
    try:
        msg = str((r.json() or {}).get("message", ""))[:160]
    except Exception:
        pass

    if r.status_code == 422:
        return True, (f"authorized to create under '{owner}' (the collision target '{probe_repo}' was "
                      f"rejected on the NAME, not the grant: 422 {msg[:60]})")
    if r.status_code == 403:
        where = f"organization '{owner}'" if is_org else f"account '{login}'"
        return False, (f"'{login}' CANNOT create repositories in {where}: 403 {msg}. A fine-grained PAT "
                       f"scoped to selected repositories has no org-level `Administration: write`, which is "
                       f"the only grant that authorizes creation — note this is invisible to /user/repos, "
                       f"which will still list every repository the token may WRITE to")
    if r.status_code == 404:
        return False, (f"POST to {url} returned 404 — '{owner}' is not visible to this token for writing "
                       f"(a fine-grained PAT awaiting org-owner approval 404s here rather than 403)")
    if r.status_code == 201:
        made = (r.json() or {}).get("full_name", f"{owner}/{probe_repo}")
        return INCONCLUSIVE, (f"UNEXPECTED: the probe CREATED '{made}'. The collision target was confirmed to "
                              f"exist beforehand, so this should be unreachable — delete it by hand and "
                              f"treat this probe's assumption as broken")
    return INCONCLUSIVE, f"create probe returned an unclassified HTTP {r.status_code} {msg}"


def check_variable_library(ctx):
    """Does the Variable Library item surface exist for this identity at all?

    In a tenant that has never created a Variable Library, a task
    whose contract needs one is betting on an unexercised surface — exactly
    the situation this probe exists for. The cheapest decisive read is the item-type list
    endpoint on the shared gym workspace: 200 (even with zero items) proves the surface
    exists and the SPN may address it (viewer role suffices per Learn); an `InvalidItemType`
    error proves the tenant does not serve the item type. Side-effect-free.

    Endpoint per Learn (rest/api/fabric/variablelibrary/items/list-variable-libraries;
    the API page lists service principals as supported):
    GET /v1/workspaces/{id}/variableLibraries
    """
    r = _fabric_get(f"{API}/workspaces/{ctx['workspace_id']}/variableLibraries", ctx["headers"])
    if r.status_code == 200:
        n = len((r.json() or {}).get("value", []))
        return True, f"variable-library surface answers on the gym workspace ({n} item(s) visible)"
    if "InvalidItemType" in r.text:
        return False, (f"the tenant does not serve the VariableLibrary item type "
                       f"({r.status_code}: {r.text[:200]})")
    return INCONCLUSIVE, (f"variableLibraries returned {r.status_code}: {r.text[:200]} — the "
                          "surface could not be read from here, so nothing is proven either way")


def check_fabric_cicd_runtime(ctx):
    """Can this interpreter run fabric-cicd at all? Entirely local, decided in seconds.

    fabric-cicd 1.2.0 hard-requires Python >=3.9,<3.14 (established by installing and
    introspecting it); an interpreter outside the
    range, or a missing package, fails at import time — before any Fabric call — so a
    correct two-workspace build discovers it mid-run unless it is checked first
    (CICD-C16's failure mode). Unlike every other check here this one probes the *local*
    runtime, not the tenant: no tenant change can lift or cause a failure it reports.
    Side-effect-free; needs no credential and no network.
    """
    import sys
    v = sys.version_info
    if not ((3, 9) <= (v.major, v.minor) < (3, 14)):
        return False, (f"Python {v.major}.{v.minor}.{v.micro} is outside fabric-cicd's "
                       "supported range (>=3.9,<3.14) — the publish fails at import, "
                       "before any Fabric call")
    from importlib.metadata import PackageNotFoundError, version
    try:
        pkg = version("fabric-cicd")
    except PackageNotFoundError:
        # The one exception that PROVES the absence this check is looking for.
        return False, ("the fabric-cicd package is not installed in this interpreter "
                       "(`pip install fabric-cicd`)")
    except Exception as e:
        return INCONCLUSIVE, (f"{type(e).__name__} reading the installed fabric-cicd version: "
                              f"{str(e)[:200]} — whether this interpreter can run the publish "
                              "library was not decided")
    try:
        import fabric_cicd  # noqa: F401
    except Exception as e:
        return False, f"fabric-cicd {pkg} is installed but fails to import: {e}"
    return True, f"Python {v.major}.{v.minor}.{v.micro} in range; fabric-cicd {pkg} imports"


def check_spark_capacity(ctx):
    """A workspace with no usable capacity accepts item creates and then fails every
    Spark start, with an error that never mentions capacity.

    "Usable" is TWO facts, and this check once asserted only the first — which is how a beta
    run got exit 0 on a PAUSED capacity and built for half an hour against a Spark runtime
    that could never start. The workspace must HAVE a capacity, and that capacity — **its
    own**, not some other Active one in the tenant — must be Active. Both halves were already
    on screen and nothing joined them: `capacity` above answers the tenant-wide question and
    says in its own text that it may be probing with a DIFFERENT capacity, while this check
    printed the workspace's real capacity id without ever reading its state. A paused capacity
    is invisible from here otherwise — the workspace read succeeds and reports the id exactly
    as a running one does.

    A state that cannot be read is INCONCLUSIVE, never FAIL: seeing state in `/v1/capacities`
    needs capacity admin (see `capacity-state-read`), and parking a runnable task on a
    permission is the sin this probe exists to avoid. In practice the branch is near
    unreachable from the baseline — `capacity` filters that same listing on `state == active`,
    so an identity that cannot read state has already failed it.
    """
    from livy_client import LivyClient  # noqa: F401  (import proves the client is present)
    r = _fabric_get(f"{API}/workspaces/{ctx['workspace_id']}", ctx["headers"])
    cap = r.json().get("capacityId") if r.status_code == 200 else None
    if not cap:
        return False, "the configured workspace has no capacity assigned — Spark will refuse to start"
    entry, why = _capacity_entry(ctx, cap)
    if entry is None:
        return INCONCLUSIVE, (f"the workspace is on capacity {cap}, but its STATE could not be "
                              f"established: {why} — whether Spark can start was NOT decided "
                              "here, so do not read this as a green light")
    name, state = entry.get("displayName"), str(entry.get("state") or "")
    if not state:
        return INCONCLUSIVE, (f"the workspace is on capacity '{name}' ({cap}), but the listing "
                              "reports no state for it — reading state needs capacity admin. "
                              "Whether Spark can start was NOT decided here")
    if state.lower() != "active":
        return False, (f"the workspace's OWN capacity '{name}' ({cap}) is {state}, not Active — "
                       "every Spark start will fail with an error that never mentions capacity, "
                       "and item creates will succeed right up until it does. Resume the "
                       "capacity (Azure portal > the Fabric capacity > Resume), then re-run "
                       "this probe with --no-cache. Another capacity in the tenant being "
                       "Active is irrelevant: capacities are not interchangeable and the "
                       "workspace runs on this one")
    return True, (f"workspace is on capacity '{name}' ({cap}), state {state} (a real session "
                  "start is proved by the rep itself)")


def check_node_toolchain(ctx):
    """Node, npm and a reachable registry — the azure-app application tasks' build plane.

    AG-APP-005/006/007 run `npm install` INSIDE the task folder (app-source resolves paths
    against the spec's own directory, so the rep's app has to live there), and AG-APP-006's
    fixture installs axe-core because the axe runner reads it off disk rather than a CDN.
    None of that is visible to any Fabric check, so without this probe a member with no
    toolchain discovers it half an hour into a correct build — the exact waste the scan
    exists to prevent. The registry reach is tested with a metadata HEAD, not an install:
    side-effect-free, and it is the leg that fails behind a proxy.
    """
    import shutil
    import subprocess
    if not shutil.which("node") or not shutil.which("npm"):
        missing = [n for n in ("node", "npm") if not shutil.which(n)]
        return False, (f"{', '.join(missing)} not on PATH. Remedy: install Node 20+ (the "
                       "application tasks build and install inside the task folder)")
    try:
        node_v = subprocess.run(["node", "-v"], capture_output=True, text=True,
                                timeout=30).stdout.strip()
        major = int(node_v.lstrip("v").split(".")[0])
    except Exception as exc:
        return INCONCLUSIVE, f"node is on PATH but did not report a version: {exc}"
    if major < 20:
        return False, (f"node {node_v} is below the 20 these tasks need. Remedy: upgrade Node")
    try:
        reg = requests.head("https://registry.npmjs.org/axe-core", timeout=30,
                            allow_redirects=True)
    except requests.RequestException as exc:
        return False, (f"npm registry unreachable ({exc}). Remedy: check egress/proxy — "
                       "AG-APP-006's fixture installs axe-core, which the axe check reads "
                       "off disk and never fetches from a CDN at check time")
    if reg.status_code >= 400:
        return INCONCLUSIVE, f"npm registry answered HTTP {reg.status_code} for a metadata HEAD"
    return True, f"node {node_v}, npm present, registry reachable"


def check_browser_toolchain(ctx):
    """Playwright imports AND a Chromium actually launches — not merely 'the package is there'.

    The two failure modes are different and only the second is common here: the pip package
    pins a build number the image does not ship, so `playwright` imports fine and the launch
    then fails asking for `playwright install`. The runner resolves the bundled binary and
    passes it as executable_path, so this probe exercises the SAME
    resolution rather than the default launch — a probe that passed where the runner fails
    would be worse than no probe.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False, ("playwright is not installed. Remedy: pip install -r "
                       "code/requirements.txt — do NOT run `playwright install`, the "
                       "image ships Chromium under PLAYWRIGHT_BROWSERS_PATH")
    sys.path.insert(0, _TOOLKIT)
    try:
        from test_webapp import _chromium_executable
    except ImportError:                       # pragma: no cover — subset bundle
        # The browser family is a web-application concern. A bundle that ships no azure-app
        # tasks ships no `test_webapp.py`, and this probe must then report a clean absence:
        # a bare ModuleNotFoundError out of `--all` reads as a broken tool rather than a
        # check that does not apply.
        return False, ("code/validation/test_webapp.py is not in this bundle, so the "
                       "browser toolchain cannot be probed. Nothing here needs it — the "
                       "check applies to the azure-app tasks only")
    exe = _chromium_executable()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=exe) if exe else pw.chromium.launch()
            version = browser.version
            browser.close()
    except Exception as exc:
        return False, (f"Chromium would not launch ({str(exc)[:160]}). Remedy: set "
                       "PLAYWRIGHT_CHROMIUM_EXECUTABLE to a Chromium binary, or "
                       "PLAYWRIGHT_BROWSERS_PATH to a directory holding one")
    return True, f"playwright imports; Chromium {version} launched from {exe or '(default)'}"


def check_entra_app_read(ctx):
    """Can this identity READ Entra app registrations? (spec: `0_admin/capabilities/azure-app.md`)

    The composed pattern's identity plane lives in Entra, not Fabric: the delegated
    `GraphQLApi.Execute.All` grant, the exposed `access_as_user` scope, the public-client
    flag and the path-bearing redirect URIs are all properties of an app registration, and
    reading them is a Microsoft Graph call against a different audience from every Fabric
    check here. The gym SPN is granted Fabric rights, and holding Graph's
    `Application.Read.All` does not follow from that — which is why AG-APP-003 cannot
    assume it. A 403 is a real, actionable answer: the task's registration assertions park
    and its behavioural ones still run.
    """
    try:
        from auth import GRAPH_SCOPE, get_token
        token = get_token(GRAPH_SCOPE)
    except RuntimeError as exc:
        # RuntimeError = the identity provider answered and refused the Graph audience.
        return False, (f"could not mint a Microsoft Graph token: {exc}. The SPN's own "
                       "credentials are fine for Fabric; Graph is a separate audience")
    except Exception as exc:
        return INCONCLUSIVE, (f"{type(exc).__name__} minting a Microsoft Graph token: "
                              f"{str(exc)[:250]} — no answer came back from the identity "
                              "provider, so whether this SPN holds Application.Read.All was "
                              "not decided")
    r = requests.get("https://graph.microsoft.com/v1.0/applications",
                     params={"$top": "1", "$select": "appId"},
                     headers={"Authorization": f"Bearer {token}"}, timeout=60)
    if r.status_code in (401, 403):
        return False, (f"HTTP {r.status_code} reading applications — the SPN holds no "
                       "Application.Read.All. Remedy: grant that Microsoft Graph APPLICATION "
                       "permission to the gym SPN's own registration and admin-consent it; "
                       "until then entra-app-registration checks SKIP rather than fail")
    if r.status_code != 200:
        return INCONCLUSIVE, f"HTTP {r.status_code} from Graph: {r.text[:200]}"
    return True, "Graph token minted and app registrations are readable"


def check_item_access_read(ctx):
    """WHICH access plane can this identity read? (spec: `0_admin/capabilities/azure-app.md`, APP-C16)

    Two planes, and the difference decides what a task may assert. Workspace
    `roleAssignments` is itself Admin-gated — a Contributor SPN gets 403
    InsufficientWorkspaceRole. ITEM-level grants (the API's "Run Queries and Mutations")
    have NO non-admin REST read at all: Learn documents them as a portal experience with
    the tenant-admin item-access endpoint as the only programmatic window. This probe says
    which of the two is open, because a task that can read neither must assert access
    behaviourally (can this principal actually execute a query?) rather than declaratively.

    The admin leg probes the admin surface generally rather than one item, since it needs
    an item id it does not have here; a 403 there means the whole admin plane is shut, which
    is the answer that matters.
    """
    planes = []
    r = _fabric_get(f"{API}/workspaces/{ctx['workspace_id']}/roleAssignments", ctx["headers"])
    if r.status_code == 200:
        planes.append(f"workspace roleAssignments ({len(r.json().get('value', []))} assignment(s))")
    workspace_detail = f"workspace plane HTTP {r.status_code}"
    a = _fabric_get(f"{API}/admin/workspaces", ctx["headers"], params={"$top": "1"})
    if a.status_code == 200:
        planes.append("tenant-admin item access")
    admin_detail = f"admin plane HTTP {a.status_code}"
    if planes:
        return True, f"readable: {', '.join(planes)} ({workspace_detail}; {admin_detail})"
    return False, (f"neither access plane is readable ({workspace_detail}; {admin_detail}). "
                   "Remedy: grant the SPN the workspace ADMIN role for the workspace plane, "
                   "or run access checks as a Fabric administrator for the item plane. "
                   "Without either, assert access behaviourally — whether the principal can "
                   "actually execute a query — not declaratively")


def _arm_token_and_scope(env_remedy=""):
    """`((token, subscription, resource_group), None, True)`, or `(None, why, verdict)`.

    `verdict` is the caller's status when the scope could not be assembled, and it is THREE
    -stated like every check here:

      False         — provably absent and actionable: an env var is unset, or the identity
                      provider ANSWERED and refused the ARM audience (`auth.get_token`
                      raises RuntimeError only once it has an answer, or has no credential
                      at all).
      INCONCLUSIVE  — the provider never answered. A DNS blip against
                      login.microsoftonline.com is not evidence that a subscription grant
                      is missing, and returning False for it parked tasks on transport.

    Every ARM check shares this preamble; each keeps its own verdict and its own wording,
    which is what makes them different questions rather than one merged check —
    `check_arm_plane` asks whether the KEY-VAULT resource group is visible, and a container
    check must never fail because a vault group was missing. `env_remedy` is appended to the
    missing-variable message so a caller can add that context without re-reading the env.
    """
    sub = os.environ.get("AZURE_SUBSCRIPTION_ID")
    rg = os.environ.get("AZURE_RESOURCE_GROUP")
    if not sub or not rg:
        try:
            import config  # noqa: F401 — imported for its .env-loading side effect
            sub = sub or os.environ.get("AZURE_SUBSCRIPTION_ID")
            rg = rg or os.environ.get("AZURE_RESOURCE_GROUP")
        except Exception:
            pass
    if not sub or not rg:
        missing = [n for n, v in (("AZURE_SUBSCRIPTION_ID", sub), ("AZURE_RESOURCE_GROUP", rg)) if not v]
        return None, f"missing {', '.join(missing)} (env or .env){env_remedy}", False
    from auth import ARM_SCOPE, get_token
    try:
        token = get_token(ARM_SCOPE)
    except RuntimeError as e:
        return None, f"ARM token refused: {type(e).__name__}: {str(e)[:160]}", False
    except Exception as e:
        return None, (f"{type(e).__name__} minting an ARM token: {str(e)[:160]}"), INCONCLUSIVE
    return (token, sub, rg), None, True


def _provider_state(token, sub, namespace):
    """A resource provider's registrationState, or None if the read itself failed."""
    r = requests.get(f"https://management.azure.com/subscriptions/{sub}/providers/{namespace}"
                     "?api-version=2021-04-01",
                     headers={"Authorization": f"Bearer {token}"}, timeout=40)
    if r.status_code != 200:
        return None
    return r.json().get("registrationState")


def check_container_image_build(ctx):
    """Can an image be built at all, by either route?

    Two independent routes and the check passes on EITHER, because they are alternatives
    rather than a stack: a local Docker daemon, or a registry that builds server-side
    (`az acr build` / ACR Tasks), which needs no local daemon and no registry egress.

    The distinction matters because the two fail for unrelated reasons. In a managed
    container the `docker` CLI can be present and answer `docker --version`
    happily while `/var/run/docker.sock` does not exist — the daemon simply is not started at
    boot. Where `dockerd` IS installed and starting it by hand works, "docker is missing"
    is the wrong diagnosis and "install docker" the wrong remedy. Hence the socket +
    `docker info` pair: a CLI that answers is not a daemon that builds. Even with a daemon
    running, such a container may be unable to pull a base image — an egress proxy answering
    403 to CONNECT for Docker Hub — which is why the server-side route is a first-class
    alternative and not a fallback.
    """
    import subprocess
    from pathlib import Path
    local_why = "absent"
    if Path("/var/run/docker.sock").exists():
        local_why = "present but `docker info` failed"
        try:
            p = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"],
                               capture_output=True, text=True, timeout=45)
            if p.returncode == 0:
                server = p.stdout.strip()
                # A daemon that answers is NOT a daemon that can build. Almost every Dockerfile
                # opens with a FROM that has to be fetched, so registry reachability is part of
                # the prerequisite, not a build-time detail. The first cut of this check passed on
                # `docker info` alone and reported the caveat in its DETAIL — but a circuit reads
                # the STATUS, so it would have sent a rep to build against an unreachable
                # registry: the exact false green preflight exists to prevent, reintroduced inside
                # preflight itself. `manifest inspect` asks the registry for a manifest and
                # downloads no layers, so this costs one request rather than an image pull.
                try:
                    m = subprocess.run(["docker", "manifest", "inspect", "hello-world:latest"],
                                       capture_output=True, text=True, timeout=75)
                except Exception as e:
                    m = None
                    local_why = f"daemon {server} answers, but the registry reachability test could not run: {type(e).__name__}"
                if m is not None and m.returncode == 0:
                    return True, (f"local Docker daemon answers (server {server}) AND a registry "
                                  "manifest was fetched, so a FROM can be resolved")
                if m is not None:
                    err = (m.stderr or m.stdout or "").strip().replace("\n", " ")[:200]
                    local_why = (f"daemon {server} answers but cannot reach a registry, so no FROM "
                                 f"can be resolved: {err}")
        except Exception as e:
            local_why = f"present but `docker info` raised {type(e).__name__}"

    scope, why, verdict = _arm_token_and_scope()
    if scope is None:
        return verdict, (f"no usable local Docker build ({local_why}), and the server-side route "
                         f"cannot be checked: {why}")
    token, sub, rg = scope
    state = _provider_state(token, sub, "Microsoft.ContainerRegistry")
    if state is None:
        return INCONCLUSIVE, (f"no usable local Docker build ({local_why}), and the Microsoft.ContainerRegistry "
                              "provider state could not be read with this identity — cannot tell "
                              "whether a registry could build for you")
    if state != "Registered":
        return False, (f"no usable local Docker build ({local_why}), and Microsoft.ContainerRegistry is {state} on the "
                       "subscription, so no registry can exist to build server-side either")
    r = requests.get(f"https://management.azure.com/subscriptions/{sub}/resourceGroups/{rg}"
                     "/providers/Microsoft.ContainerRegistry/registries?api-version=2023-07-01",
                     headers={"Authorization": f"Bearer {token}"}, timeout=40)
    if r.status_code != 200:
        return INCONCLUSIVE, (f"no usable local Docker build ({local_why}); Microsoft.ContainerRegistry is Registered "
                              f"but listing registries in {rg} answered HTTP {r.status_code}")
    names = [x.get("name") for x in r.json().get("value", [])]
    if not names:
        return False, (f"no usable local Docker build ({local_why}), and {rg} holds no container registry — nothing "
                       "can build the image by either route")
    return True, (f"no usable local Docker build ({local_why}), but {len(names)} registry/registries in {rg} ({', '.join(names[:3])}) "
                  "can build server-side. NOT proven here: that this identity may QUEUE a build — "
                  "that is a registry write (Container Registry Repository Writer on an "
                  "ABAC-enabled registry, AcrPush on a classic one) and cannot be tested "
                  "side-effect-free")


def check_capacity_state_read(ctx):
    """Can this identity establish a capacity's STATE from at least one plane?

    Distinct from `capacity`, which asks "is at least one capacity Active" as a precondition
    for doing work. This asks the operations question: can the identity find out that a
    capacity is *paused*? An ops task attributing a symptom to the capacity layer needs the
    second, and the two answers come apart — `capacities` can list an Active capacity while
    the workspace's own capacity is unreadable.

    Two planes, and EITHER suffices, because which one answers is a tenant property: the
    Fabric `capacities` listing (needs capacity admin to see state) or ARM
    `Microsoft.Fabric/capacities` (needs a subscription-scope reader role). Neither is
    settable from inside a task, which is why this is a prerequisite rather than something a
    rep can remediate.
    """
    ws_cap = None
    try:
        w = _fabric_get(f"{API}/workspaces/{ctx['workspace_id']}", ctx["headers"], timeout=45)
        if w.status_code == 200:
            ws_cap = w.json().get("capacityId")
    except Exception:
        pass

    fabric_states = {}
    try:
        caps, err = _fabric_list(f"{API}/capacities")
        if err is None:
            fabric_states = {c.get("id"): c.get("state") for c in caps if c.get("state")}
    except Exception:
        pass

    if ws_cap and ws_cap in fabric_states:
        return True, (f"Fabric plane reports the workspace's own capacity as "
                      f"'{fabric_states[ws_cap]}' — a paused state would be readable here")
    if fabric_states:
        return True, (f"Fabric plane reports a state for {len(fabric_states)} capacity/capacities"
                      + (f", but NOT for the workspace's own ({ws_cap}) — an ops check naming that "
                         "capacity would be undecidable" if ws_cap else
                         "; the workspace's own capacityId could not be read to confirm coverage"))

    scope, why, verdict = _arm_token_and_scope()
    if scope is None:
        return verdict, (f"the Fabric capacities listing returned no state for this identity, and "
                         f"the ARM plane cannot be checked: {why}")
    token, sub, _rg = scope
    r = requests.get(f"https://management.azure.com/subscriptions/{sub}/providers"
                     "/Microsoft.Fabric/capacities?api-version=2023-11-01",
                     headers={"Authorization": f"Bearer {token}"}, timeout=45)
    if r.status_code != 200:
        return False, (f"no capacity state is readable: the Fabric listing returned none and ARM "
                       f"Microsoft.Fabric/capacities answered HTTP {r.status_code}. An ops check "
                       "asserting a capacity verdict would FAIL on a permission rather than park")
    caps = r.json().get("value", [])
    if not caps:
        return INCONCLUSIVE, ("ARM answered 200 but lists no Fabric capacities in this "
                              "subscription — the gym's capacities may live elsewhere")
    return True, (f"ARM plane reports state for {len(caps)} Fabric capacity/capacities "
                  "(the Fabric listing returned none for this identity)")


def check_cost_management_read(ctx):
    """Can this identity run a Cost Management query at subscription scope?

    One of two routes to an app-layer cost figure; the other is reading the Capacity Metrics
    app as a person, which no runner can do (it is a semantic model, and DAX/XMLA is
    out of scope for this KB). A 403 here is the common case and is NOT a hard blocker: the
    only consumer is env-gated, so an absent cost route parks one check and costs only that
    evidence rather than the task.
    """
    scope, why, verdict = _arm_token_and_scope()
    if scope is None:
        return verdict, f"cannot reach the ARM plane: {why}"
    token, sub, _rg = scope
    body = {"type": "ActualCost",
            "timeframe": "MonthToDate",
            "dataset": {"granularity": "None",
                        "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}}}}
    url = (f"https://management.azure.com/subscriptions/{sub}/providers"
           "/Microsoft.CostManagement/query?api-version=2023-03-01")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # Cost Management throttles HARD, and the throttle answers BEFORE authorization does — so a
    # single attempt cannot tell "no Cost Management Reader" from "come back later". A
    # try-once-then-INCONCLUSIVE check would almost
    # never confirm access that is genuinely there: a tenant can need several attempts
    # over several minutes to get a 200, while the role listing ALSO fails to show the
    # grant, because a `principalId eq` filter at subscription scope sees neither a
    # management-group-inherited assignment nor one granted to a group the SPN belongs to. Both
    # signals say "absent" and both are wrong. Hence: retry within a bounded budget, and on
    # persistent 429 say plainly that this is throttling and NOT evidence of absence.
    delays = [0, 15, 30, 45]  # ~90s worst case, inside a preflight that already runs for minutes
    last = None
    for delay in delays:
        if delay:
            time.sleep(delay)
        r = requests.post(url, headers=headers, json=body, timeout=60)
        last = r
        if r.status_code == 429:
            continue
        break
    r = last
    if r.status_code == 200:
        return True, "Cost Management answered a month-to-date query at subscription scope"
    if r.status_code in (401, 403):
        return False, (f"Cost Management answered HTTP {r.status_code} — this identity lacks Cost "
                       "Management Reader at subscription scope. The cost check is env-gated, so "
                       "it PARKS rather than failing; only that evidence is lost")
    if r.status_code == 429:
        return INCONCLUSIVE, (f"Cost Management still answered 429 after {len(delays)} attempts "
                              "(~90s). This is THROTTLING, not a denial — the throttle answers "
                              "before authorization does, so this is NOT evidence that the grant "
                              "is missing, and the role listing is not a reliable second opinion "
                              "either (it does not show management-group or group-granted "
                              "assignments). Retry, or settle it with a direct query")
    return INCONCLUSIVE, (f"Cost Management answered HTTP {r.status_code}: {r.text[:160]}")


def check_container_apps_plane(ctx):
    """Is Microsoft.App registered, so a Container App can exist at all?

    Deliberately separate from `container-image-build`: an image you cannot build and a
    platform you cannot deploy to are different absences with different remedies, and a task
    blocked on both should say so twice. Reports the managed-environment count as detail
    because a registered provider with zero environments is still undeployable — a Container
    App cannot be created without one — but that is a note rather than a failure, since the
    rep may create the environment itself if it holds Contributor.
    """
    scope, why, verdict = _arm_token_and_scope()
    if scope is None:
        return verdict, f"cannot reach the ARM plane: {why}"
    token, sub, rg = scope
    state = _provider_state(token, sub, "Microsoft.App")
    if state is None:
        return INCONCLUSIVE, ("the Microsoft.App provider state could not be read with this "
                              "identity — cannot tell whether a Container App can exist")
    if state != "Registered":
        return False, (f"Microsoft.App is {state} on the subscription — no Container App can be "
                       "created until the provider is registered")
    r = requests.get(f"https://management.azure.com/subscriptions/{sub}/resourceGroups/{rg}"
                     "/providers/Microsoft.App/managedEnvironments?api-version=2024-03-01",
                     headers={"Authorization": f"Bearer {token}"}, timeout=40)
    if r.status_code != 200:
        return True, (f"Microsoft.App is Registered; listing managed environments in {rg} answered "
                      f"HTTP {r.status_code}, so whether one already exists is unknown")
    envs = [x.get("name") for x in r.json().get("value", [])]
    if not envs:
        return True, (f"Microsoft.App is Registered, but {rg} holds no managed environment — a "
                      "Container App cannot be created without one, so the rep must create it "
                      "(needs Contributor on the group) or be given one")
    return True, (f"Microsoft.App is Registered; {rg} holds {len(envs)} managed environment(s) "
                  f"({', '.join(envs[:3])}). NOT proven here: create rights on the group")


def check_azure_monitor_plane(ctx):
    """Are the three providers the Foundry-observability route needs registered?

    One check rather than three because they are not alternatives — the route needs all of
    them, and a task blocked on any one is blocked. Reported together with which one is
    missing, so the remedy names the provider to register rather than "something in Azure".

    `Microsoft.Insights` is worth calling out: it is registered by default on most
    subscriptions because Azure Monitor uses it, but a workspace-based Application Insights
    component ALSO needs `Microsoft.OperationalInsights`, and that one is not implied.
    """
    scope, why, verdict = _arm_token_and_scope()
    if scope is None:
        return verdict, f"cannot reach the ARM plane: {why}"
    token, sub, rg = scope
    wanted = ("Microsoft.OperationalInsights", "Microsoft.Insights", "Microsoft.CognitiveServices")
    states = {ns: _provider_state(token, sub, ns) for ns in wanted}
    unreadable = [ns for ns, s in states.items() if s is None]
    if unreadable:
        return INCONCLUSIVE, (f"provider state unreadable with this identity for "
                              f"{', '.join(unreadable)} — cannot tell whether the telemetry "
                              "plane can exist")
    missing = [f"{ns} is {s}" for ns, s in states.items() if s != "Registered"]
    if missing:
        return False, ("; ".join(missing) + " — register the provider(s) on the subscription "
                       "before a Log Analytics workspace, an Application Insights component "
                       "or a Foundry project can be created")
    return True, (f"all three providers Registered on {sub[:8]}…; resources would land in "
                  f"{rg}. NOT proven here: CREATE rights on the group, nor the ability to "
                  "make a role assignment (that needs `Role Based Access Control "
                  "Administrator` at the group — Owner and User Access Administrator are "
                  "NOT required)")


def check_logs_query_egress(ctx):
    """Can this host reach the Logs query API, and does this identity get a token for it?

    Two absences with one symptom, separated deliberately. Reading a Fabric data agent's
    spans means querying the Log Analytics workspace behind the connected Application
    Insights resource, and that is `api.loganalytics.azure.com` — a different host from
    `management.azure.com` and commonly absent from an egress allowlist. A token for the
    `api.loganalytics.io` audience is the second half.

    The probe deliberately does NOT run a query: with no workspace GUID to address it could
    only ask about a workspace it invented. A 400/404 from the host is therefore a PASS —
    the host answered, which is the whole question. Only a connection error is a failure.
    """
    try:
        from auth import get_token
        token = get_token("https://api.loganalytics.io/.default")
    except RuntimeError as e:
        # RuntimeError = the identity provider answered and refused this audience.
        return False, (f"could not mint a Logs-query token: {type(e).__name__}: {str(e)[:160]}"
                       " — the audience is api.loganalytics.io even though the host is "
                       "api.loganalytics.azure.com")
    except Exception as e:
        return INCONCLUSIVE, (f"{type(e).__name__} minting a Logs-query token: {str(e)[:160]} — "
                              "the identity provider never answered, so neither the token nor "
                              "the egress question was decided")
    try:
        r = requests.post("https://api.loganalytics.azure.com/v1/workspaces/"
                          "00000000-0000-0000-0000-000000000000/query",
                          json={"query": "print n = 1"},
                          headers={"Authorization": f"Bearer {token}"}, timeout=40)
    except requests.RequestException as e:
        return False, (f"api.loganalytics.azure.com is unreachable from here: "
                       f"{type(e).__name__}: {str(e)[:160]} — add the host to the egress "
                       "allowlist; management.azure.com being open does not cover it")
    return True, (f"token minted and the host answered (HTTP {r.status_code} for a "
                  "deliberately non-existent workspace — the status is not the point, the "
                  "answer is). NOT proven here: Log Analytics Reader on any real resource, "
                  "which is a per-resource role assignment")


# The catalogue: one entry per check, carrying BOTH what it advertises and what runs it.
#
# One dict rather than a name->description table beside a name->function table. Those two
# could disagree, and a name advertised with no runner surfaced as `FAIL — KeyError:
# '<name>'` at probe time — a claim about the TENANT ("this prerequisite is absent") where
# the truth was a claim about the PROBE ("this check does not exist yet"). A guard used to
# catch that at import; pairing the description with the function makes the disagreement
# unstateable instead, which is why the guard is gone rather than merely passing.
#
# `gym_validate._prereq_problems` reads the KEYS of this dict to validate a task's declared
# prerequisites, so a check is authorable the moment it is listed here.
CHECKS = {
    "auth-mode": (
        "which sign-in routes this tenant actually permits — your own sign-in "
        "and/or a service principal. Answers 'can I start today?' before anything "
        "is bought or configured", check_auth_mode),
    "fabric-auth": (
        "the configured identity can acquire a Fabric API token and read its "
        "own workspace", check_fabric_auth),
    "capacity": (
        "at least one Active capacity is visible to the SPN (TENANT-WIDE — says "
        "nothing about the configured workspace's own; spark-capacity decides that)",
        check_capacity),
    "capacity-sku": (
        "the workspace's capacity is a PAID F2+/P1+ tier, not a trial or "
        "per-user SKU (data agents and other paid-tier-only items)", check_capacity_sku),
    "workspace-create": (
        "SPN may create a workspace and assign it to a capacity (tenant setting)",
        check_workspace_create),
    "sql-endpoint": (
        "SPN can authenticate to a lakehouse SQL analytics endpoint (a separate grant)",
        check_sql_endpoint),
    "key-vault": (
        "an Azure Key Vault URI is configured and its secrets are readable by the SPN",
        check_key_vault),
    "onelake-storage": (
        "SPN can reach OneLake over the storage scope (external-apps tenant setting)",
        check_onelake_storage),
    "git-integration": (
        "a git provider is reachable, so a workspace can be put under source control",
        check_git_integration),
    "git-repo-create": (
        "the git credential may CREATE a repository, not just write to granted ones",
        check_git_repo_create),
    "variable-library": (
        "the tenant serves the VariableLibrary item type to this identity at all",
        check_variable_library),
    "fabric-cicd-runtime": (
        "the local interpreter can run the official publish library "
        "(Python >=3.9,<3.14 and fabric-cicd importable)", check_fabric_cicd_runtime),
    "spark-capacity": (
        "the CONFIGURED workspace has a capacity and that capacity is Active — a "
        "paused one accepts item creates and refuses every Spark start",
        check_spark_capacity),
    "arm-plane": (
        "SPN can mint an ARM token and read the dedicated vault resource group "
        "(AZURE_SUBSCRIPTION_ID + AZURE_RESOURCE_GROUP)", check_arm_plane),
    "entra-app-read": (
        "SPN can mint a Microsoft Graph token and READ app registrations "
        "(Application.Read.All) — the azure-app identity-plane checks", check_entra_app_read),
    "item-access-read": (
        "which access plane this identity can read: workspace roleAssignments "
        "(needs workspace Admin) and/or the tenant-admin item-access endpoint",
        check_item_access_read),
    "node-toolchain": (
        "Node >= 20 and npm are on PATH, and the npm registry is reachable "
        "(the azure-app application tasks build and install inside the task folder)",
        check_node_toolchain),
    "browser-toolchain": (
        "playwright is importable AND a Chromium actually launches "
        "(browser-render / axe-scan assert on a rendered page)", check_browser_toolchain),
    "container-image-build": (
        "an image can actually be built SOMEWHERE — a live local Docker "
        "daemon, or a registry in AZURE_RESOURCE_GROUP that can build for you",
        check_container_image_build),
    "container-apps-plane": (
        "Microsoft.App is Registered on the subscription, so a Container App "
        "can exist at all (the azure-app deployment tasks)", check_container_apps_plane),
    "capacity-state-read": (
        "this identity can establish a capacity's STATE from some plane, so an "
        "ops task can attribute a symptom to a paused capacity", check_capacity_state_read),
    "cost-management-read": (
        "a Cost Management query answers at subscription scope (one of the two "
        "routes to an app-layer cost figure)", check_cost_management_read),
    "azure-monitor-plane": (
        "Microsoft.OperationalInsights, Microsoft.Insights and "
        "Microsoft.CognitiveServices are Registered, so a Log Analytics "
        "workspace, an App Insights resource and a Foundry project can exist",
        check_azure_monitor_plane),
    "logs-query-egress": (
        "the Logs query API host answers this identity at all — a SEPARATE "
        "host from management.azure.com, and the only way to read a span",
        check_logs_query_egress),
}


# --------------------------------------------------------------------------- task prereqs

def task_prereqs(task_id):
    """The prerequisites a task declares, as [{check, params, why, on_fail, remedy}].

    The baseline is implicit — a spec lists only what it needs on top, so the common case is
    no block at all and nothing to keep in sync.
    """
    from gym_validate import _resolve_spec
    spec = json.loads(open(_resolve_spec(task_id), encoding="utf-8").read())
    declared = spec.get("prerequisites", [])
    seen, out = set(), []
    for check in BASELINE:
        seen.add(check)
        out.append({"check": check, "params": {}, "why": "every task that touches Fabric needs this",
                    "on_fail": "park", "remedy": ""})
    for pre in declared:
        if pre.get("check") in seen:
            continue
        seen.add(pre.get("check"))
        out.append(pre)
    return out


def sibling_tasks(task_id):
    """Every other task in the same topic gym, so a parked task can point somewhere."""
    from gym_validate import TOPIC_DIRS, _ID_RE
    m = _ID_RE.match(task_id.strip())
    topic = TOPIC_DIRS.get(m.group(1).upper()) if m else None
    if not topic:
        return []
    root = os.path.join(_REPO_ROOT, "1_agent-gym", topic)
    if not os.path.isdir(root):
        return []
    return sorted(d for d in os.listdir(root)
                  if os.path.isfile(os.path.join(root, d, "validate.json")) and d != task_id)


def runnable_instead(task_id, passed):
    """Sibling tasks whose every prerequisite is in `passed`.

    Only checks we actually ran can be counted, so this is conservative by construction —
    it never claims a task is runnable on the strength of a check nobody performed.
    """
    out = []
    for sibling in sibling_tasks(task_id):
        try:
            needs = {p["check"] for p in task_prereqs(sibling)}
        except Exception:
            continue
        if needs <= passed:
            out.append(sibling)
    return out


# --------------------------------------------------------------------------- cache

def cache_key(check, params):
    """Cache identity for one check result.

    SCOPED TO THE WORKSPACE, deliberately. A verdict is a claim about a specific
    tenant target, not about a check name: without the workspace in the key, a PASS
    cached against one workspace is served for another, and the check passes
    VACUOUSLY — a stale `capacity-sku` PASS naming one capacity (by then Inactive)
    served for a workspace that has since moved to another. When both are the same
    SKU nothing is misled — but
    the same hit after a move to a trial capacity would greenlight a task that
    cannot run at all, which is the exact failure this probe exists to prevent.

    Adding the component also invalidates every pre-existing entry, which is correct:
    none of them recorded what they were a claim about.
    """
    ws = os.environ.get("FABRIC_WORKSPACE_ID", "-")
    return check + "|" + ws + "|" + json.dumps(params or {}, sort_keys=True)


def load_cache(enabled):
    if not enabled or not os.path.exists(CACHE_PATH):
        return {}
    try:
        raw = json.loads(open(CACHE_PATH, encoding="utf-8").read())
    except (OSError, ValueError):
        return {}
    now = time.time()
    return {k: v for k, v in raw.items() if now - v.get("ts", 0) < CACHE_TTL}


def save_cache(cache):
    try:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        with open(CACHE_PATH, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, indent=2)
    except OSError:
        pass  # a probe that cannot write its cache still did its job


# --------------------------------------------------------------------------- main

def resolve_requested(args, parser):
    """(ordered [(check, params)], {check: prereq dict}) from whichever selector was given."""
    if args.task or args.tasks:
        ids = [args.task] if args.task else [t.strip() for t in args.tasks.split(",") if t.strip()]
        merged = {}
        for task_id in ids:
            for pre in task_prereqs(task_id):
                merged.setdefault(pre["check"], pre)
        return [(c, merged[c].get("params") or {}) for c in merged], merged
    names = list(CHECKS) if args.all else [c.strip() for c in (args.checks or "").split(",") if c.strip()]
    if not names:
        parser.error("give --task, --tasks, --checks or --all (or --list)")
    unknown = [c for c in names if c not in CHECKS]
    if unknown:
        parser.error(f"unknown check(s): {', '.join(unknown)}. Known: {', '.join(CHECKS)}")
    return [(c, {}) for c in names], {}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--task", help="task id (AG-LAK-004) — run the checks that task declares")
    p.add_argument("--tasks", help="comma-separated task ids — run the union of their checks")
    p.add_argument("--checks", help="comma-separated check names")
    p.add_argument("--all", action="store_true", help="run every check")
    p.add_argument("--list", action="store_true", help="list the checks and exit")
    p.add_argument("--json", action="store_true", dest="as_json", help="emit a machine-readable result")
    p.add_argument("--no-cache", action="store_true", dest="no_cache",
                   help="ignore cached passes and re-run every check")
    args = p.parse_args()

    global QUIET
    QUIET = args.as_json

    if args.list:
        for k, (description, _runner) in CHECKS.items():
            print(f"  {k:18s} {description}")
        return 0

    requested, prereqs = resolve_requested(args, p)
    order = [c for c, _ in requested]
    # fabric-auth seeds the shared context every other check reads; capacity seeds the id
    # workspace-create needs. Both are dependencies, not preferences.
    if "fabric-auth" in order:
        requested.insert(0, requested.pop(order.index("fabric-auth")))
    else:
        requested.insert(0, ("fabric-auth", {}))
    # ...and auth-mode goes ahead of even that. It reads no ctx and needs no credential —
    # its whole job is to answer "can I sign in here at all?" for someone who cannot. Behind
    # fabric-auth it was unreachable in exactly that case: fabric-auth fails, the loop breaks
    # below, and the one check written for an unconfigured tenant never ran. `--all` was no
    # escape, because the hoist above moved fabric-auth in front of it.
    order = [c for c, _ in requested]
    if "auth-mode" in order:
        requested.insert(0, requested.pop(order.index("auth-mode")))
    order = [c for c, _ in requested]
    if "workspace-create" in order and "capacity" not in order:
        requested.insert(order.index("workspace-create"), ("capacity", {}))

    cache = load_cache(not args.no_cache)
    ctx, fresh = {}, False
    for name, params in requested:
        key = cache_key(name, params)
        hit = cache.get(key)
        # A cached pass still has to seed ctx for the checks that read it, so fabric-auth and
        # capacity are always run for real — they are cheap and everything downstream needs them.
        # NEVER_CACHE checks are re-run too: they read *local* state that a `pip install` can
        # change between two runs of this probe, and they cost milliseconds. A cached local
        # verdict can keep reporting an old `fabric-cicd` for hours after a
        # major, breaking upgrade was installed — a change preflight would be structurally
        # blind to.
        if hit and name not in ("fabric-auth", "capacity") and name not in NEVER_CACHE:
            record(name, True, f"{hit['detail']}  (cached {int((time.time() - hit['ts']) / 60)}m ago)")
            continue
        if name not in CHECKS:
            # A task declared a prerequisite this probe cannot test. That is INCONCLUSIVE, never
            # False: `False` means "the prerequisite is provably absent → park", and the circuit
            # acts on it. Here nothing about the tenant was learned at all. A bare
            # KeyError here would read as a confident tenant failure.
            status, detail = INCONCLUSIVE, (
                f"no such check in this probe — '{name}' is declared as a prerequisite by a task "
                "but is not implemented here, so NOTHING was learned about the tenant. This is a "
                "gap in the probe, not a verdict on the environment: add the check (see the task's "
                f"own `why`/`remedy`) or fix the name. Known: {', '.join(sorted(CHECKS))}")
            record(name, status, detail)
            continue
        try:
            runner = CHECKS[name][1]
            status, detail = runner(ctx, **params) if params else runner(ctx)
        except TypeError as e:
            status, detail = False, f"check '{name}' does not accept params {list(params)}: {e}"
        except Exception as e:
            # An exception escaping a runner means the check did not COMPLETE — nothing was
            # learned about the tenant. `False` here would say "the prerequisite is provably
            # absent" and a circuit would park the task on a transport blip or a library bug,
            # which is the same inversion the "no such check" branch above refuses to make.
            status, detail = INCONCLUSIVE, (
                f"{type(e).__name__}: {str(e)[:300]} — the check did not complete, so NOTHING "
                "was decided about the tenant. This is a fault in the probe or its transport, "
                "not a verdict on the environment")
        record(name, status, detail)
        if status is True:
            cache[key], fresh = {"ts": time.time(), "detail": detail}, True
        if name == "fabric-auth" and status is not True:
            if not QUIET:
                print("\nfabric-auth failed — every other check depends on it. Stopping.")
            break
    if fresh and not args.no_cache:
        save_cache(cache)

    failed = [k for k, s, _ in results if s is False]
    unknown = [k for k, s, _ in results if s is INCONCLUSIVE]
    passed = {k for k, s, _ in results if s is True}

    if args.as_json:
        print(json.dumps({
            "task": args.task, "exit": 1 if failed else (2 if unknown else 0),
            "failed": failed, "inconclusive": unknown, "passed": sorted(passed),
            "runnableInstead": runnable_instead(args.task, passed) if (failed and args.task) else [],
            "checks": [{"check": k, "status": label(s), "detail": d,
                        "why": (prereqs.get(k) or {}).get("why", ""),
                        "remedy": (prereqs.get(k) or {}).get("remedy", "")} for k, s, d in results],
        }, indent=2))
        return 1 if failed else (2 if unknown else 0)

    print("\n===== SUMMARY =====")
    for k, s, d in results:
        print(f"{label(s):<12}  {k}  - {d}")

    if failed:
        print(f"\n{len(failed)} check(s) FAILED: {', '.join(failed)}. "
              "Park the task; do not build against a prerequisite that is not there.")
        for name in failed:
            pre = prereqs.get(name) or {}
            if pre.get("why"):
                print(f"\n  {name} — why the task needs it: {pre['why']}")
            if pre.get("remedy"):
                print(f"  remedy: {pre['remedy']}")
        if args.task:
            alt = runnable_instead(args.task, passed)
            print(f"\nRunnable instead: {', '.join(alt) if alt else '(none — fix the baseline first)'}")
        return 1
    if unknown:
        print(f"\n{len(unknown)} check(s) INCONCLUSIVE: {', '.join(unknown)}. "
              "Nothing failed — these could not be decided from here. Read the detail above "
              "before deciding whether to proceed; this is not a park signal on its own.")
        return 2
    print(f"\nAll {len(results)} check(s) passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
