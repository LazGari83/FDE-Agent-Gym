"""Environment configuration: .env loading, auth mode, and the endpoints every client reads."""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv, find_dotenv

# The clone this toolkit lives in, resolved from __file__ because scripts run from task
# folders. Also the one directory two environments running the same repo are guaranteed to
# agree on — each by its own path to it — which is what FABRIC_TOKEN_CACHE below rests on.
REPO_ROOT = Path(__file__).resolve().parents[2]

# Repo-root .env.
if not os.environ.get("FABRIC_DOTENV_DISABLE"):
    _root_env = REPO_ROOT / ".env"
    load_dotenv(_root_env if _root_env.exists() else find_dotenv(usecwd=True))

_placeholders = []


def _setting(name: str) -> str:
    """One environment value, with an unfilled `.env.example` placeholder read as unset.

    A placeholder is not empty, and that is the whole problem: `AZURE_CLIENT_SECRET=
    your-spn-client-secret`, left in place by anyone who copied the example and filled in
    only the workspace id, satisfies the `auto` rule below and silently switches the run to
    a SERVICE PRINCIPAL nobody has. The sign-in then dies inside MSAL on a tenant named
    `your-tenant-guid` — an error about authority configuration, three layers below the
    line that actually needs changing. Dropping them is right where failing is not: the
    member never chose these values, they came with the file.
    """
    value = (os.environ.get(name) or "").strip()
    if value.lower().startswith(("your-", "your_")):
        _placeholders.append(name)
        return ""
    return value


# user | spn | auto — spn is the only unattended mode. See .env.example.
FABRIC_AUTH_MODE = (os.environ.get("FABRIC_AUTH_MODE") or "auto").strip().lower()

AZURE_TENANT_ID = _setting("AZURE_TENANT_ID")
AZURE_CLIENT_ID = _setting("AZURE_CLIENT_ID")
AZURE_CLIENT_SECRET = _setting("AZURE_CLIENT_SECRET")

# Where a user-mode sign-in is cached. Empty = under the HOME of whoever runs the process,
# which is the trap the moment TWO things run this toolkit: you sign in in a terminal, and an
# agent runs everything after that in a shell with a DIFFERENT HOME (a container, WSL, a
# sandbox). Same repo, same .env, but the cache is somewhere the second shell cannot see — so
# it reports "not signed in" for a sign-in that plainly succeeded, and no amount of signing
# in again fixes it, because the agent's shell has no browser to sign in WITH.
#
# A RELATIVE value is resolved against REPO_ROOT, and that is the setting worth reaching for:
# an absolute path cannot serve both, since the two environments reach the same clone by
# different paths (`C:\...\repo` and `/mnt/c/.../repo` name one directory), while
# `.fabric_kb/msal_cache.json` resolves correctly in each. `.gitignore` matches the cache
# wherever it lands, so a cache inside the clone is never committed. An absolute value is
# still honoured — pathlib's `/` yields the right operand when it is absolute.
_cache = _setting("FABRIC_TOKEN_CACHE")
FABRIC_TOKEN_CACHE = str(REPO_ROOT / Path(_cache).expanduser()) if _cache else ""

if FABRIC_AUTH_MODE == "auto":
    FABRIC_AUTH_MODE = "spn" if AZURE_CLIENT_SECRET else "user"

# RuntimeError, not SystemExit — SystemExit is a BaseException and callers must be able to
# catch this.
FABRIC_WORKSPACE_ID = _setting("FABRIC_WORKSPACE_ID")
if not FABRIC_WORKSPACE_ID:
    raise RuntimeError(
        "FABRIC_WORKSPACE_ID is not set. It names the Fabric workspace this run builds in "
        "and asserts against.\nSet it in the environment or in the .env at the repo root "
        "(see .env.example). The placeholder `your-fabric-workspace-guid` counts as unset — "
        "replace it with the workspace's GUID."
    ) from None

if FABRIC_AUTH_MODE == "spn":
    _missing = [n for n, v in (("AZURE_TENANT_ID", AZURE_TENANT_ID),
                               ("AZURE_CLIENT_ID", AZURE_CLIENT_ID),
                               ("AZURE_CLIENT_SECRET", AZURE_CLIENT_SECRET)) if not v]
    if _missing:
        raise RuntimeError(
            f"FABRIC_AUTH_MODE=spn needs {', '.join(_missing)}.\n"
            "Set them, or set FABRIC_AUTH_MODE=user to sign in as yourself instead — "
            "user mode needs no app registration and no secret."
        )

FABRIC_API_BASE = "https://api.fabric.microsoft.com/v1"
ONELAKE_DFS_HOST = "https://onelake.dfs.fabric.microsoft.com"

# ── the Azure Resource Manager plane ──────────────────────────────────────────
# Fabric capacities, Key Vault lifecycle, container apps and Azure Monitor are ARM
# resources rather than Fabric items, and addressing one needs a subscription and a
# resource group. Deliberately OUTSIDE the mandatory block above: only the ARM-backed
# paths need them, and making them mandatory would break every other consumer.
ARM_BASE = "https://management.azure.com"
AZURE_SUBSCRIPTION_ID = _setting("AZURE_SUBSCRIPTION_ID")
AZURE_RESOURCE_GROUP = _setting("AZURE_RESOURCE_GROUP")


def require_arm_scope(subscription_id: str = None, resource_group: str = None,
                      *, needed_by: str = "") -> tuple[str, str]:
    """`(subscription, resource group)`, or a RuntimeError naming exactly which are missing.

    A function rather than an import-time assertion so the failure lands at the call: a
    consumer that never touches ARM must be able to import this module with neither
    variable set. Arguments override the environment, which is what lets a client take
    them per instance (`CapacityClient(subscription_id=...)`).

    `needed_by` names the caller in the message — the ARM-backed check families pass their
    own check ids, which config has no business knowing.
    """
    sub = subscription_id or AZURE_SUBSCRIPTION_ID
    rg = resource_group or AZURE_RESOURCE_GROUP
    missing = [n for n, v in (("AZURE_SUBSCRIPTION_ID", sub),
                              ("AZURE_RESOURCE_GROUP", rg)) if not v]
    if missing:
        hint = f" — needed by {needed_by}" if needed_by else ""
        raise RuntimeError(f"missing {', '.join(missing)} (set them in the process env "
                           f"or .env){hint}")
    return sub, rg


# At the FOOT of the module, so every _setting call above has run. One line, once,
# naming what was ignored. Silently dropping configuration is how a member
# spends an afternoon on a setting that was never in effect.
if _placeholders:
    print(f"  note: ignoring unfilled .env placeholders ({', '.join(_placeholders)}) — they "
          f"still hold their `your-…` example values. Delete or fill those lines.",
          file=sys.stderr, flush=True)
