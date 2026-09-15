# Fabric toolkit (REST clients + test harness)

Self-contained Python clients for driving Microsoft Fabric over the REST API, plus a declarative, **read-only** test harness (`validation/fabric_test.py`) that grades a workspace against a JSON contract. This folder is the source of truth for the code; a topic's wiki `coding-guidance` page documents how to use it. Nothing here depends on any other repository.

**This bundle carries a subset of the toolkit** — the modules its practice tasks reach. The map below lists exactly what is in the box; nothing here points at a file you do not have.

## Setup

1. `pip install -r requirements.txt` (`requests` + `python-dotenv` for the core clients; the azure-*, `pyarrow`, and `duckdb` extras are only needed for open mirroring). The official Fabric SDKs (`microsoft-fabric-api`, `azure-mgmt-fabric`) are pinned to exact versions by design — the API SDK is beta and regenerated monthly, so the pin is bumped monthly behind the CI gates, never floated.
2. Authentication, per `FABRIC_AUTH_MODE`:
   - **`user`** — sign in as yourself with `python core/auth.py`. A browser opens on the first run and the sign-in is cached at `~/.fabric_kb/msal_cache.json`; set `FABRIC_AUTH_DEVICE_CODE=1` where there is no browser (and use the URL it prints — a guessed device-login URL answers "You don't have access to this"). Needs no app registration and no client secret.

     **Sign in yourself, in the terminal you will run everything else from, and do not hand the sign-in to an agent.** The cache is keyed to the HOME of whoever runs the process, and an agent's shell frequently has a different one (a container, WSL, a sandbox) — so it cannot see your sign-in, and its own attempt cannot finish either: an interactive sign-in needs a browser it does not have, and a device-code sign-in must stay in one live process, which a sandbox that reaps the shell between commands does not give it. Two environments are usually unavoidable, though — the agent has to run the builds too, and every client in this toolkit goes through `auth`. Set `FABRIC_TOKEN_CACHE=.fabric_kb/msal_cache.json` in the repo-root `.env` and both share one sign-in: the value is resolved against the repo root, which is the one directory two environments reach by different paths (`C:\...\repo` and `/mnt/c/.../repo`) and agree on, so a single line serves both where an absolute path cannot. It is gitignored. Then sign in once, in your own terminal, and the agent inherits it. `python core/auth.py --status` prints the Python, the HOME, the cache path and whether a token is there — run it in both and compare.
   - **`spn`** — a service principal's client credentials (`AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`). Required for anything unattended.
   - **`auto`** (default) — `spn` when a client secret is present, `user` otherwise.
3. Set `FABRIC_WORKSPACE_ID` — the workspace a run builds in and asserts against. Copy the repo-root `.env.example` to `.env` **at the repo root** (that is where `config.py` looks, however deep the script that imports it), or set plain environment variables (headless environments need no `.env` file).
4. Put the toolkit on your path before importing any module — the imports are flat (`auth.py` does `from config import …`), so importing from elsewhere without this step fails with `ModuleNotFoundError`:

   ```python
   sys.path.insert(0, str(REPO_ROOT / "code"))
   import toolkit_path  # noqa: F401  — adds core/, clients/, builders/, validation/
   ```

Tokens are minted per scope and cached until shortly before expiry. The Fabric API scope is the default; `auth.py` also carries the ARM, Key Vault and Microsoft Graph scopes (the OneLake storage scope has no constant — the azure SDK path supplies it itself through `get_credential()`) — a token minted for the wrong audience fails as a 401 *at the endpoint*, which reads as a permissions problem rather than an audience one. The open-mirroring landing zone uses the storage scope over OneLake's ADLS Gen2 endpoint; that path needs two tenant settings enabled for the SPN's security group ("Service principals can access Fabric APIs" and "Users can access data stored in OneLake with apps external to Fabric") plus workspace Contributor.

## Tests and CI

Two offline layers — no tenant, no credentials, no network. Run them from this folder with `FABRIC_WORKSPACE_ID` set to any placeholder:

- `python validation/fabric_test.py --self-test` — the check engine's own fixtures, across every check family present.
- `python validation/fabric_test.py --list-checks` — the check-type vocabulary this bundle can grade against.

## The modules

Everything here drives **Fabric**. Nothing in this toolkit talks to a non-Fabric API.

**Transport.** The Fabric-plane clients are thin wrappers over the official `microsoft-fabric-api`
SDK (and `capacity.py` over `azure-mgmt-fabric`), reached only through the `sdk.py` seam below —
public signatures, return shapes (wire-format JSON dicts), and the encoded Fabric behaviour notes
are unchanged from the hand-rolled era. What DID change: errors are azure-core's
`HttpResponseError` (carrying the response body, with `.status_code`) instead of
`requests.HTTPError`; retries/paging/LRO polling are azure-core's, with every semantically unsafe
POST (job runs, deploys, git commits, non-idempotent creates) explicitly pinned single-shot
(`retry_total=0`) because azure-core WOULD otherwise status-retry POSTs on 500/503/504; and the
few endpoints the pinned SDK does not yet model remain direct calls, each marked
`# SDK gap (0.1.0b24)`. Each rebased client carries an UNVERIFIED-on-new-transport header until
its first passing gym rep clears it. Exempt from all of this by design: `livy_client` (statement
execution), `key_vault`, `github_client` (non-Fabric planes), `landing_zone` (ADLS protocol), and
the read-only validation surface (`fabric_read.py`).

Core plumbing:

- [toolkit_path.py](toolkit_path.py) — puts `core/`, `clients/`, `builders/` and `validation/` on `sys.path`. Import it once, first; every script in this repo (the gym runner, the validators, CI) reaches the toolkit through it.
- [config.py](core/config.py) — env + API base + OneLake DFS host + the ARM plane (`ARM_BASE`, and `require_arm_scope()` for the optional `AZURE_SUBSCRIPTION_ID`/`AZURE_RESOURCE_GROUP` pair, which fails at the call rather than at import so a consumer that never touches ARM need not set them). Loads the repo-root `.env` (resolved from the file's own location, so it works from any cwd). A missing `FABRIC_WORKSPACE_ID` raises a catchable `RuntimeError` at import.
- [sdk.py](core/sdk.py) — the official-SDK seam: the ONE module allowed to import `microsoft_fabric_api` (and construct `azure.mgmt.fabric`'s client), exposing the pinned `fabric_client()` / `arm_fabric_client()` factories, the credential adapter over `auth.get_token`, and a `transport=` hook for offline fake-transport tests (`tests/test_sdk_seam.py` enforces the seam). Also carries the three helpers every wrapper leans on: `to_wire()` (SDK model/pager → wire-format dict, pagers drained fully), `sdk_models(group)` (request-body models via `from_dict` on wire-format keys), and `lro_result()` (bounded wait on the SDK's LRO extractor that raises loudly on Failed/Cancelled — the SDK's own sync wrappers sleep unboundedly and swallow failures).
- [auth.py](core/auth.py) — `get_token()` / `get_headers()` with per-scope caching; the scope registry (Fabric, ARM, vault, Graph — the storage scope is supplied by the azure SDK itself through `get_credential()`); `get_principal_id()` (the SPN's **object** id — what `roleAssignments` actually returns, never the client id); `get_credential()` / `get_datalake_client()` for the azure-SDK paths.
- [fabric_http.py](core/fabric_http.py) — the requests-plane plumbing that outlived the SDK migration, serving the check families' own reads, `livy_client`/`key_vault`, and the clients' few `# SDK gap` direct calls: `raise_for_status_fabric` / `raise_for_status_arm` (keep the error body the stock raiser discards), `transport_retry` (retries transport failures and throttling — 429 any verb, gateway errors on reads — with a default `timeout=60` on every call), `paged` (follows either of Fabric's two continuation conventions; reading only the first page silently truncates), `retry_after_seconds`, `json_headers`, `encode_part`/`decode_parts`, `resolve_in`. The 202 LRO poller is gone — polling lives in azure-core and `sdk.lro_result`.

Build clients (each encodes the API's non-obvious behaviour in its docstrings):

- [livy_client.py](clients/livy_client.py) — `LivyClient`: Spark SQL against a Lakehouse (create tables, DESCRIBE pre-flight, seed data), bounded session/statement polling.
- [lakehouse_client.py](clients/lakehouse_client.py) — `LakehouseClient`: the Lakehouse item lifecycle, `valid_display_name()`, `wait_for_sql_endpoint()` (one-time provisioning), `refresh_sql_endpoint_metadata()` (the *per-write* sync a T-SQL consumer needs — a different thing), `abfss_paths()`, plus module-level `sql_endpoint_connection()` (pyodbc + Driver 18 + the token-packing dance).
- [notebook_client.py](clients/notebook_client.py) — `NotebookClient`: notebooks as item definitions plus the Job Scheduler. `notebook_content_py(cells, parameters_cell=0)` tags the injection target for `run(parameters=…)`; `run(parameters=…)` refuses a bare `{"name": value}` client-side with the typed wire shape in the message (the server's `NotebookBadWebRequest` refusal names the envelope, not the field, and leaves no run to inspect); `run_and_wait()` is the honest execution loop; job submissions are deliberately never blind-retried (a lost response may have started a real run).
- [workspace_folders.py](clients/workspace_folders.py) — `FolderClient`: workspace **folders** (the UI's item tree) — list/create/`ensure_folder`, `rename_item`, `move_item`, `place_item`. **`ensure_folder()` returns the folder id *string*; `create_folder()`/`resolve_by_name()` return the JSON object.** Distinct from landing-zone folders, which carry replication semantics.

The test harness:

- [fabric_test.py](validation/fabric_test.py) — the **testing harness** entry point: a declarative suite runner executing typed checks against ANY workspace, bound via `--workspace <guid-or-name>` → spec `"workspace"` → `FABRIC_WORKSPACE_ID`, with the resolved target always printed first. Check families load by name and an absent one degrades to a clear refusal, so the engine runs identically with two families installed or twelve. Unknown check-level keys and unknown `expect` keys are REFUSED, never skipped; the `requiresEnv` gate PARKS a check (a SKIP naming the missing variable and the remedy) rather than failing it, and `--no-park` inverts that for the run meant to prove a gated leg. `--json` emits machine-readable verdicts for a CI gate; `--self-test` and `--list-checks` need no network; `run_suite(...)` is the programmatic entry, the same code path the CLI uses.
- [fabric_read.py](core/fabric_read.py) — the harness's **read-only** Fabric surface: every function is a GET, or a POST to an endpoint that only reports. This is what backs "running a validation cannot change a tenant".
- [test_common.py](validation/test_common.py) — shared core for the check families: the expectation matchers, the `unknown_keys`/`fold` guards, and the ARM `role_assignment_hit` predicate the keyvault and azuremonitor families share (the ARM root and the subscription/resource-group pair are `config.ARM_BASE` / `config.require_arm_scope`, because the ARM clients need them too).

- `test_<family>.py` — one module per check family. This bundle carries 2: lakehouse, notebook. A family imports from core or from nothing, never from a sibling, and the engine loads whichever are present — so it runs identically with two families installed or twelve.

---

_Trimmed for this bundle: 22 module entries and the *Appendix — running inside a sandboxed or proxied container* · *The ontology build flow (which module does what)* · *The open-mirroring flow (which module does what)* section(s) document parts of the toolkit that are not in the box, and have been removed rather than left pointing at files you do not have._
