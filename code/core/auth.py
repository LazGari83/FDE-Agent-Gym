"""Tokens for the Fabric REST API and the neighbouring Azure planes.

MSAL keeps public (user) and confidential (service principal) clients deliberately separate,
so `get_token` builds one or the other and makes the matching acquire call. Everything else
here is built on that one function.
"""
import atexit
import base64
import json
import os
import platform
import sys
from pathlib import Path

import msal
from config import (ARM_BASE, AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET,
                    FABRIC_AUTH_MODE, FABRIC_TOKEN_CACHE, ONELAKE_DFS_HOST)

SCOPE = "https://api.fabric.microsoft.com/.default"
ARM_SCOPE = f"{ARM_BASE}/.default"                                    # capacities live on ARM
VAULT_SCOPE = "https://vault.azure.net/.default"                      # secrets; lifecycle is ARM
GRAPH_SCOPE = "https://graph.microsoft.com/.default"                  # Entra app registrations

# The Azure CLI's own client id — a pre-consented Microsoft public client, which is what lets
# user mode work with no app registration. Set AZURE_CLIENT_ID to use your own.
_PUBLIC_CLIENT_ID = "04b07795-8ddb-461a-bbee-02f9e1bf7b46"
# Public: a "not signed in" that names the file it looked in is answerable; one that does
# not sends the reader to re-run a sign-in that already worked. `preflight_probe.py` and
# `--status` both quote it.
CACHE_PATH = (Path(FABRIC_TOKEN_CACHE) if FABRIC_TOKEN_CACHE          # already absolute
              else Path.home() / ".fabric_kb" / "msal_cache.json")
_AUTHORITY = f"https://login.microsoftonline.com/{AZURE_TENANT_ID or 'organizations'}"
_APP = None


def _persist(cache):
    """Write the token cache to disk. It holds refresh tokens, so user-only.

    Called both eagerly (the moment a sign-in lands) and at exit (for the refresh-token
    rotation a later silent call performs). `serialize()` clears `has_state_changed`, so
    the two never write the same state twice."""
    if cache.has_state_changed:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(cache.serialize(), encoding="utf-8")
        try:
            CACHE_PATH.chmod(0o600)
        except OSError as e:
            # The whole point of FABRIC_TOKEN_CACHE is to put this file where two
            # environments can both reach it, and those filesystems are exactly the ones
            # that do not carry POSIX modes (a Windows drive mounted into WSL, a network
            # share). Losing the sign-in over a permission bit would be the worse outcome —
            # but this file holds REFRESH tokens, so say so rather than swallowing it.
            print(f"  warning: could not restrict permissions on {CACHE_PATH} ({e}). It "
                  f"holds refresh tokens — check who else can read that location.",
                  file=sys.stderr, flush=True)


def _application():
    """The MSAL app for the active mode, built once. A user sign-in persists at
    `CACHE_PATH` so it outlives the process — otherwise every script would prompt."""
    global _APP
    if _APP is not None:
        return _APP
    try:
        if FABRIC_AUTH_MODE == "spn":
            _APP = msal.ConfidentialClientApplication(
                AZURE_CLIENT_ID, client_credential=AZURE_CLIENT_SECRET, authority=_AUTHORITY)
        else:
            cache = msal.SerializableTokenCache()
            if CACHE_PATH.exists():
                cache.deserialize(CACHE_PATH.read_text(encoding="utf-8"))
            atexit.register(_persist, cache)
            _APP = msal.PublicClientApplication(
                AZURE_CLIENT_ID or _PUBLIC_CLIENT_ID, authority=_AUTHORITY, token_cache=cache)
    except ValueError as e:
        # MSAL resolves the authority over the network at construction, so a tenant that does
        # not exist surfaces here as a ValueError about "authority configuration" — three
        # layers below the .env line that needs changing, and naming neither the variable nor
        # the file. Say which setting produced this URL.
        raise RuntimeError(
            f"the authority {_AUTHORITY} is not a real tenant — MSAL says: {e}\n"
            "AZURE_TENANT_ID must be a tenant GUID or a domain (contoso.onmicrosoft.com). "
            "For a normal user sign-in leave it unset: the toolkit then signs in against "
            "'organizations', which resolves your tenant from the account you pick."
        ) from e
    return _APP


def _sign_in(app, scopes):
    """Browser sign-in, or a device code when FABRIC_AUTH_DEVICE_CODE is set (no browser)."""
    if not os.environ.get("FABRIC_AUTH_DEVICE_CODE"):
        return app.acquire_token_interactive(scopes, prompt="select_account")
    flow = app.initiate_device_flow(scopes=scopes)
    if "user_code" not in flow:
        raise RuntimeError(f"could not start a device-code sign-in: {flow}")
    # The URI printed as its own labelled line, ahead of the server's prose. Whoever relays
    # this to the browser is often an agent summarising what it read, and a plausible guess
    # (`login.microsoft.com/device`) is a real Microsoft page that answers "You don't have
    # access to this" — a dead end that reads like a tenant permissions problem rather than
    # a wrong URL. Only the verification_uri Entra returned here works.
    print(f"\n  Open this URL, and no other : {flow['verification_uri']}"
          f"\n  Enter this code             : {flow['user_code']}"
          f"\n  Expires in                  : {flow.get('expires_in', 900) // 60} minutes"
          f"\n\n  ({flow['message']})"
          "\n\n  Leave THIS process running until you have finished in the browser — the"
          "\n  sign-in completes here, in this process, and nowhere else.\n", flush=True)
    return app.acquire_token_by_device_flow(flow)


def get_token(scope: str = SCOPE, *, prompt: bool = True) -> str:
    """An access token for `scope`, as whoever FABRIC_AUTH_MODE says we are.

    `prompt=False` answers from the cache only and never opens a browser — what a preflight
    needs. A token minted for the wrong audience fails as a 401 at the *endpoint*, which
    reads as a permissions problem rather than an audience one.
    """
    app = _application()
    if FABRIC_AUTH_MODE == "spn":
        result = app.acquire_token_for_client([scope])
    else:
        accounts = app.get_accounts()
        result = app.acquire_token_silent([scope], account=accounts[0]) if accounts else None
        if not result and prompt:
            result = _sign_in(app, [scope])
            # Write it NOW rather than leaving it to the atexit hook. A sign-in is slow — a
            # browser, or a code typed on a phone — and the process running it is often on a
            # leash: an agent's command timeout, a Ctrl-C, a sandbox reaping the shell
            # between turns. atexit does not run when a process is killed, so the member
            # watches the sign-in succeed and the next command still says "not signed in".
            _persist(app.token_cache)
    if not result or "access_token" not in result:
        raise RuntimeError(f"no {FABRIC_AUTH_MODE} token for {scope}: "
                           f"{(result or {}).get('error_description') or 'not signed in'}")
    return result["access_token"]


def get_headers(scope: str = SCOPE) -> dict:
    return {"Authorization": f"Bearer {get_token(scope)}"}


def _claims(token):
    """The JWT payload of a token we just minted ourselves — no signature check needed."""
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def get_principal_id() -> str:
    """The caller's **object id** (the `oid` claim) — what Fabric's roleAssignments returns as
    principal.id, never AZURE_CLIENT_ID. Matching on the client id silently finds nothing on
    a workspace the identity owns, which reads as "no permission"."""
    return _claims(get_token())["oid"]


class Credential:
    """Three third-party SDKs — OneLake's DataLakeServiceClient, Key Vault's SecretClient and
    fabric-cicd — take a credential OBJECT, not a token. This is that object, deferring to
    `get_token` so a run has one identity and one cache. Call it as `get_credential()`."""

    def get_token(self, *scopes, **_kwargs):
        from azure.core.credentials import AccessToken
        token = get_token(scopes[0])          # the module function above, not this method
        return AccessToken(token, int(_claims(token)["exp"]))


get_credential = Credential


def get_datalake_client():
    """DataLakeServiceClient against OneLake's DFS endpoint (storage scope, not Fabric API)."""
    from azure.storage.filedatalake import DataLakeServiceClient
    return DataLakeServiceClient(account_url=ONELAKE_DFS_HOST, credential=get_credential())


def _status() -> int:
    """Everything needed to answer "why does it say I am not signed in?" — above all, WHICH
    environment this is. The usual answer is two environments rather than a broken sign-in:
    a terminal signs in, and an agent runs the next command in a shell whose HOME is
    somewhere else, reading a cache that was never written there. Run this in both and
    compare the first four lines; where the cache paths differ, set
    FABRIC_TOKEN_CACHE=.fabric_kb/msal_cache.json in the repo-root .env — repo-relative, so
    both resolve it to one file — and sign in once more. Never prompts."""
    print(f"python      : {sys.executable}")
    print(f"platform    : {platform.system()} {platform.release()}")
    print(f"home        : {Path.home()}")
    print(f"cache file  : {CACHE_PATH}"
          f"{'  (from FABRIC_TOKEN_CACHE)' if FABRIC_TOKEN_CACHE else ''}")
    print(f"cache exists: {CACHE_PATH.exists()}")
    print(f"auth mode   : {FABRIC_AUTH_MODE}")
    print(f"authority   : {_AUTHORITY}")
    if FABRIC_AUTH_MODE != "spn":
        try:
            signed_in = [a.get("username") for a in _application().get_accounts()]
            print(f"accounts    : {signed_in or 'none'}")
        except RuntimeError as e:
            # A misconfigured authority makes the MSAL app itself unbuildable. That is a
            # finding to PRINT — a diagnostic that dies on the fault it exists to describe
            # tells the reader nothing they could not already see.
            print(f"accounts    : cannot build the MSAL client — {e}")
            return 1
    try:
        token = get_token(prompt=False)
    except RuntimeError as e:
        print(f"silent token: NO — {e}")
        return 1
    # A diagnostic that raises is worthless — this is what somebody runs precisely when the
    # state is strange, and a cache holding something that is not a JWT is one such state.
    try:
        claims = _claims(token)
        who = f"{claims.get('upn') or claims.get('app_displayname') or '?'} " \
              f"(tenant {claims.get('tid')})"
    except Exception:
        who = f"unreadable claims — {len(token)} chars, not a JWT"
    print(f"silent token: yes — {who}")
    return 0


if __name__ == "__main__":
    if "--status" in sys.argv:
        raise SystemExit(_status())
    print(f"auth mode: {FABRIC_AUTH_MODE}")
    print(f"Token acquired ({len(get_token())} chars)")
    if FABRIC_AUTH_MODE != "spn":
        print(f"Sign-in cached at: {CACHE_PATH}")
        print("Run everything that follows — the preflight probe included — from THIS same\n"
              "terminal and this same Python, or it will not find this sign-in.\n"
              "`python core/auth.py --status` prints where any given shell is looking.")
