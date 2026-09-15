"""
fabric_test.py — declarative, read-only test harness for Microsoft Fabric infrastructure.

Point it at ANY workspace — a planned environment, a live one, someone else's — with a JSON
spec of typed checks, and it answers one question per check: does the live environment match
the contract? It prints PASS/FAIL/SKIP per check and exits 0 iff every check passes.

This module lives in `code/` and composes its sibling clients (graph_client,
livy_client, mirror_client, landing_zone, github_client, workspace_client, ...) directly.

This file is the ENTRY POINT of a module family. The check runners and their offline
fixtures live in flat sibling modules, one per check family — each exposes its slice of
the check-type registry as `RUNNERS` and its fixtures as `SELF_TESTS`, and this file
merges them (a duplicate check type across modules is a startup error):

    test_common.py     — the shared expectation matchers + cross-family helpers
    test_ontology.py   — item · schema · preflight · gql
    test_openmirror.py — mirror-item · mirror-status · table-status · landing-zone · delta-sql
    test_lakehouse.py  — lakehouse-item · lakehouse-table · spark-sql (+ the read-only guard)
    test_notebook.py   — notebook-item · job-run (+ the --lint-source flow)
    test_keyvault.py   — keyvault-vault · keyvault-secret · keyvault-secret-list
    test_cicd.py       — deployment-pipeline · stage-items · workspace-items · deployment-operation
    test_git.py        — github-repo · github-tree · github-file · git-connection · git-status
                         · fabric-connection · git-credentials · workspace-item
    test_dataagent.py  — data-agent-item · data-agent-definition · data-agent-datasource
                         · data-agent-fewshots · data-agent-stage-diff
    test_azureapp.py   — graphql-api-item · graphql-definition · graphql-query
                         · entra-app-registration · item-permission · container-app
    test_webapp.py     — app-source · http-probe · browser-render · axe-scan
                         · duckdb-parquet (the application half: no Fabric calls)

Usage:
    python fabric_test.py <spec.json> [--workspace <guid-or-name>]
                          [--graph-id G] [--lakehouse-id L] [--mirror-id M]
                          [--json <out.json>] [--allow-write] [--no-park]
    python fabric_test.py --self-test        # offline fixture checks, no network, no tenant
    python fabric_test.py --list-checks      # offline: print the check-type vocabulary

    Also available, all offline-friendly:
      --checks a,b       re-run named checks only (a PARTIAL run — loudly labelled as such)
      --dry-run          structural authoring gate: types known, required fields present
      --lint-source [NB=]PATH   run a spec's notebook sourceContains/sourceLacks against a
                                local file before it is ever uploaded

Programmatic entry: `run_suite(spec, *, workspace=..., graph_id=..., lakehouse_id=...,
mirror_id=..., allow_write=False, only_checks=None, json_out=None, tolerated_keys=())`
returns the verdict dict (the `--json` shape) and is the SAME code path the CLI uses, so
composing the engine programmatically asserts exactly what the CLI would.
`collect_self_test_checks()` is the aggregate self-test's programmatic face.

Workspace binding (resolution order):
    1. `--workspace <guid-or-name>`   — the CLI override
    2. spec top-level `"workspace"`   — a guid or display name carried by the spec itself
    3. `FABRIC_WORKSPACE_ID`          — the environment default (process env or .env)
A display name is resolved over every workspace the identity can see; an ambiguous name is
refused (pass the GUID). The resolved target — display name + GUID — is ALWAYS the first
line of output: running a test suite against the wrong workspace is the disaster case, so
the target must be legible before any verdict is. (Note: `config.py` currently requires
`FABRIC_WORKSPACE_ID` at import even when `--workspace` overrides it — point it at any
workspace the identity can read.)

Read-only discipline: the engine only READS. It never triggers notebook or pipeline runs
(`job-run` asserts on run *history*), never writes to OneLake, never mutates items. The one
caller-supplied exception is `spark-sql`, whose statement is executed verbatim: its first
keyword (after leading whitespace and `--`/`/* */` comments) must be SELECT / WITH /
DESCRIBE / SHOW unless `--allow-write` is passed — a refused statement is a FAILED check
with the reason stated, never a crash. Two cost notes: any Spark-needing check
(`preflight`, `lakehouse-table`, `spark-sql`) opens ONE shared Livy session (~35s to start,
holding Spark compute on the capacity until the run closes it — which the run always does);
and `delta-sql` runs caller SQL in a local duckdb over `delta_scan` (reads OneLake; the
guard does not apply there because a write statement would hit local duckdb state, not
Fabric — the OneLake access is read-only by construction).

Machine-readable verdicts: `--json <path>` writes
    {"workspace": {"id": ..., "displayName": ...},
     "passed": n, "failed": n, "skipped": n, "parked": ["<check id>", ...],
     "checks": [{"id", "type", "ok", "reason", "seconds"}, ...]}
with `ok` true/false/null (null = skipped) — the surface a CI gate consumes. `parked` is the
subset of skips an environment gate held back (see `requiresEnv` below).

Exit codes: 0 = every check passed · 1 = at least one check failed · 2 = usage/spec error
(bad path, malformed JSON, no workspace resolvable, unknown --checks id, dry-run problems).

Spec format — unknown top-level keys are ignored with a one-line notice:

    {"task": "...",                      # banner label (optional)
     "workspace": "<guid-or-name>",      # optional — see the binding order above
     "lakehouse": "...", "mirror": "...", "graphModel": "...", "vault": "...",
     "notebook": "...", "defaultSchema": "...",                 # per-family targets
     "gitRepo": "...", "gitOwner": "...", "gitBranch": "...", "gitConnection": "...",
     "graphqlApi": "...",
     "checks": [{"id": "...", "type": "<check type>", ..., "expect": {...}}, ...]}

Check types — ontology / graph family, runners in test_ontology.py (run `--list-checks`
for the one-line index):
    item       — the named ontology and graph model exist in the workspace
    schema     — getQueryableGraphType has the expected node types (label + primary key)
                 and edge types (label, source, destination)
    preflight  — DESCRIBE TABLE shows every expected bound column (needs a lakehouse id)
    gql        — executeQuery result matches an expected `rows` / `scalar` / `count`

Check-level keys — the vocabulary ON the check dict, beside `expect`:
    Every type declares its own in its module's RUNNERS entry, and a key outside that
    vocabulary is REFUSED as a failed check naming the offending key — the same house rule
    `expect` keys obey, one level up. Four keys are universal (`type`, `id`, `question`,
    `expect`); anything starting with `_` is an author annotation the engine ignores
    (`_evidence`, `_note`, `_spoiler`). Run `--list-checks` for the per-type table, and
    `--dry-run` to catch a bad key offline at authoring time. A key no runner reads
    asserts nothing — a spec asking for a phone-sized accessibility scan would quietly get
    a desktop one and pass. Declaring the vocabulary is NOT optional: a registry entry
    that omits it is a startup error, because an "undeclared means don't check" fallback
    would preserve exactly that hole.

The environment gate — `requiresEnv` + `{env:NAME}` (universal, every check type):
    A check may reference an environment variable in any of its string fields as
    `{env:NAME}`, resolved before the runner sees it, and must declare every variable it
    references in its own gate:

        {"id": "…", "type": "http-probe", "method": "POST", "path": "/api/query",
         "requestHeaders": {"Authorization": "Bearer {env:APP_USER_TOKEN}"},
         "requiresEnv": {"vars": ["APP_USER_TOKEN"],
                         "remedy": "acquire a delegated token for a real user and export it"},
         "expect": {"status": 200}}

    All declared variables set  -> the check RUNS, substituted.
    One or more absent (or "")  -> the check is PARKED: a SKIP whose reason names the missing
                                   variable AND the author's remedy. Never a pass (the tally
                                   counts `ok is True` only) and never a failure.
    An undeclared `{env:}` ref  -> REFUSED. The placeholder text would otherwise be sent as
                                   though it were the value, and a server correctly rejecting
                                   that garbage returns exactly the 401 the spec hoped for.
    A gate with no `remedy`     -> REFUSED. A park that says nothing actionable is a silent gap.

    Why both halves ship together: an assertion whose input is a *delegated* (real-user)
    credential cannot be driven by a service-principal harness — OBO preserves user identity
    by design — so without the gate such a leg is either asserted nowhere or fails every
    run that lacks the credential; the gate makes it expressible. `--no-park` is the
    inverse mode: it FAILS a parked check, so the run that claims to prove the gated leg
    cannot pass without the credential. A parked run also prints a closing PARKED banner and
    lists the ids in the `--json` verdict's `parked` field: a SKIP has to be legible in the
    report as well as absent from the tally, because a landed run whose gated legs skipped
    must not be read as having established them.

Expectation matchers (shared by every result-returning check — gql, delta-sql, spark-sql;
engine in test_common.py; unknown keys here are REFUSED too, so a typo beside a real
matcher cannot pass in silence):
    exact      — `rows` (optionally `ordered`), `scalar`, `count`. Pin a value the spec
                 controls. Use against a frozen fixture, where the answer is knowable
    invariant  — `minCount` / `maxCount` (row-count bounds), `minScalar` / `maxScalar`
                 (numeric bounds on the single-column scalar), `unique` (a column name or
                 list — a composite key must not repeat), `columns` (the exact column-name
                 set, and every row must agree on it), `nonNull` (named columns hold no
                 nulls; an absent column counts as null). Use against a LIVE source, where
                 no exact value is knowable — a view count changes hourly, so `scalar`
                 cannot be written down but "at least one page, keys unique, schema
                 unchanged" can. `columns` against zero rows is a FAILURE, not a vacuous
                 pass: that is precisely how a well-formed empty result slips through
    Any combination may be given; every matcher present is evaluated and ANDed, so adding
    one only tightens. Stacking is the point — `minCount` + `unique` + `columns` is the
    standard "pagination ran to exhaustion and the schema held" assertion.

Open-mirroring check types (specs with a top-level "mirror": "<displayName>"; runners in
test_openmirror.py):
    mirror-item    — the mirrored database exists; optional decoded-definition expectations
                     (sourceType / defaultSchema / retentionInDays), and `folder`
    mirror-status  — getMirroringStatus matches `status` (or is in `statusIn`)
    table-status   — getTablesMirroringStatus has (or with `absent`, lacks) the table;
                     optional `status`, `minProcessedRows` (cumulative!), `noError` and
                     `hasError` expectations. `status` alone is WEAK in both directions:
                     a SchemaMergeFailure-stopped table still reads "Replicating", and a
                     healthy table mid-first-load reads "Snapshotting". Assert `noError`
                     to mean "healthy"; `hasError` for triage suites that must see a break.
    landing-zone   — the table's landing-zone folder `contains`/lacks (`absent`) named
                     entries, `exists` true/false, and `_metadata.json` matches `metadata`
                     (case-insensitive keys — the spec shows both keyColumns and KeyColumns)
    delta-sql      — run SQL over the mirror's replicated Delta tables via duckdb
                     delta_scan (SPN secret); `{table}` / `{table:<schema>/<name>}`
                     placeholders expand to delta_scan('<Tables root>/<schema>/<name>');
                     result matched with the same `rows` / `scalar` / `count` matchers.
                     This — not `processedRows`, which is cumulative — is the sanctioned
                     way to assert a mirror table's CURRENT content

Lakehouse check types (specs with a top-level "lakehouse": "<displayName>"; runners in
test_lakehouse.py — notebook-item and job-run in test_notebook.py):
    lakehouse-item   — the lakehouse exists with the creation options the contract demands.
                       `schemaEnabled` is read from `properties.defaultSchema` (the flag passed
                       at create time is never echoed back); optional `defaultSchema`,
                       `sqlEndpoint` provisioning status, and `folder`
    notebook-item    — the notebook exists, is filed in the expected `folder`, and its decoded
                       source satisfies `sourceContains` / `sourceLacks`. `sourceLacks` is the
                       sharper of the two: it catches the hardcoded GUID, the `!pip`, the
                       `inferSchema` that leave a run green but unportable or wrong. Both are
                       pure string matches, so `--lint-source` runs them offline pre-upload
    job-run          — the notebook actually ran and reached the expected terminal state.
                       `status` is asserted against the LATEST run, not "any run ever passed";
                       `statusIn` and `minRuns` also available. Catches fire-and-forget builds.
                       Assertion-only: this engine never TRIGGERS a run
    lakehouse-table  — a table exists (or with `expect: {exists: false}`, does not) and its
                       columns match. A missing SCHEMA counts as absence — stronger evidence
                       than an empty table list, and the cross-lakehouse assertion needs it
    spark-sql        — Spark SQL over the lakehouse, matched with the same
                       `rows` / `scalar` / `count` matchers. `{table}` expands to
                       `<schema>.<table>`; `{table:<schema>/<name>}` names its own, so one
                       statement can span two tables (a reconciliation between two landed
                       cuts). Shares ONE Livy session with every other check.
                       READ-ONLY unless `--allow-write`: the first keyword must be
                       SELECT / WITH / DESCRIBE / SHOW or the check FAILS as refused
    shortcut         — the item's OneLake shortcuts: the named one exists (or with
                       `expect: {exists: false}`, does not) at `path`, referencing
                       `targetType` (compared case-insensitively), `targetItem` (a
                       LAKEHOUSE display name, resolved to its item id) and
                       `targetPathContains` (substring of the target's path); `count` is
                       every shortcut on the item, whatever its name. This is the ONLY
                       check that distinguishes referenced-in-place from copied-in:
                       `lakehouse-table` / `spark-sql` pass just as happily when the build
                       uploaded the file — reference-vs-copy is exactly what they cannot
                       see. Two guards: unknown expectation keys are REFUSED, not skipped,
                       and a shortcut expected to EXIST must state `targetType` — a name
                       alone proves something is there, not that it references anything.
                       `lakehouse` (display name) targets another lakehouse, so a suite can
                       assert the item being referenced INTO gained nothing. Verified live
                       (AG-ING-006, 2026-08-27): both directions — the reference found on
                       the consumer, the target listing empty

Azure-app check types (specs with a top-level "graphqlApi", "entraApp" and/or
"containerApp"; runners in test_azureapp.py):
    graphql-api-item — the API-for-GraphQL item exists (or with `exists: false`, does not),
                       filed in the expected `folder`, with `description`
    graphql-definition — the DECODED definition against the ontology it was generated from:
                       `types` (exact set), `typesContain`/`typesAbsent`, `typeCount`,
                       `sourceType`, `sourceObjects` (type -> `dbo.table`), `relationships`
                       (`cardinality` and `targetObject` pinned) and `readOnly` (no
                       Create/Update/Delete enabled anywhere). The datasources part is found
                       by SHAPE, not by path, so a service-side rename cannot blind it
    graphql-query    — executeQuery for one `collection`, matched with the shared matchers.
                       Rows come from `data.<collection>.items`; `errors` and an absent
                       collection surface as failures, never as a vacuous empty result
    entra-app-registration — the registration behind the app's identity plane, over Microsoft
                       Graph: `requiredScopes` (resolved from the resource's OWN service
                       principal, so no well-known GUID is hardcoded), `exposedScopes` (the
                       `access_as_user` an OBO exchange needs), `publicClientFlows`,
                       `identifierUriPresent`, `signInAudience`, and the redirect-URI pair
                       `redirectUrisWithPath` / `redirectUrisContain` — the first being the
                       AADSTS9002326 trap, where a bare origin fails and a path-bearing URI
                       does not. A 401/403 from Graph SKIPs with the remedy: an identity that
                       may not read registrations has said nothing about whether one is right
    item-permission  — who holds access, from whichever plane the identity can read.
                       `source: "workspace"` (default) reads workspace roleAssignments (itself
                       Admin-gated); `source: "admin"` reads the tenant-admin item-access
                       endpoint, the only documented programmatic read of ITEM-level grants.
                       `assignments` and `absent` are the two directions that matter — the
                       intended principal can, and the automation principal was not handed
                       more than Run. Both planes SKIP on 403 rather than reporting grants wrong
    container-app    — the Container App the app shipped in, over ARM: `image`, `external`,
                       `targetPort`, `fqdnPresent`, `identityType`, `provisioningState`,
                       `corsPolicyAbsent`, and the hardening pair `envSecretRefs` (a named env
                       var must be backed by a secretRef, not a literal `value`) and
                       `envValuesLack`. 'The app works' is true with a secret in the
                       environment; only the resource body tells the two deployments apart

Web-application check types (specs with a top-level "app": "<directory>" and/or
"baseUrl": "<origin>"; runners in test_webapp.py). None of these call Fabric, so a suite
made ONLY of them binds no workspace and needs no Fabric credentials:
    app-source       — static assertions over the app's own tree: a file, or a directory +
                       `glob`. `contains` (some matched file), `eachContains` (every one),
                       `lacks` (none) are deliberately three vocabularies, because the
                       decomposition assertions run in both directions; plus `maxLines`
                       budgets, `matches`/`lacksMatch` regexes, `fileCount`/`minFileCount`,
                       and `filesContaining` with a `count` — the "exactly one module exports
                       the shared hook" assertion. No path may escape the app root
    http-probe       — one request against a RUNNING app: `status`/`statusIn`, `headers`
                       (substring), `headersAbsent`, `bodyContains`/`bodyLacks`, `jsonKeys`,
                       `json`, `maxSeconds`. The engine never starts the app — a rep does,
                       and a transport failure is a FAILED check, not a crash. `headersAbsent`
                       is the sharp one: serving app and API from one origin means no CORS
                       header should be there at all
    browser-render   — the page as a user meets it, in Chromium: `selectors`, `text`,
                       `textMatches`, `elementCount`/`minElements`, `noConsoleErrors`
                       (resource 404s included — filter known noise with the check's
                       `ignoreConsole`), `noHorizontalScroll`. `viewports` repeats the whole
                       collection at each size and keeps the TIGHTEST count, so a chart that
                       renders at 1280 and vanishes at 375 fails. A `waitFor` that never
                       arrives is reported as a verdict, not a timeout stack
    axe-scan         — axe-core over the rendered page: `violations`/`maxViolations`,
                       `maxImpact` (the worst tolerated — "moderate" fails on serious and
                       critical), `rulesAbsent`, and `minFocusable`, which counts the distinct
                       elements successive Tab presses actually reach. axe is read from disk
                       (`axeSource`, AXE_CORE_JS, or the app's node_modules), never a CDN
    duckdb-parquet   — duckdb SQL over the parquet a build-time extraction emitted, with
                       `{parquet:<relpath>}` placeholders and the shared matchers. Same
                       read-only first-keyword guard as spark-sql. Rows are normalized the
                       way delta-sql normalizes them, because duckdb returns a DECIMAL column
                       as a Python Decimal and Decimal('12.4') != 12.4

Key Vault check types (specs with a top-level "vault": "<vaultName>"; runners in
test_keyvault.py):
    keyvault-vault   — the vault exists in AZURE_RESOURCE_GROUP (ARM GET; or with
                       `expect: {exists: false}`, does not — beware that soft-deleted
                       counts as absent here while still holding the name). Optional
                       `enableRbacAuthorization`, `location`, and `roleAssigned` (a role
                       *name*, e.g. "Key Vault Secrets User" — resolved to its definition
                       GUID at the vault's own scope, then matched against assignments AT
                       that scope; an inherited resource-group grant does not satisfy it).
                       Network boundary: `defaultAction` ("Allow"/"Deny"),
                       `publicNetworkAccess` ("Enabled"/"Disabled"), `bypass`
                       ("AzureServices"/"None") and `minIpRules` (an invariant — the
                       runner's own egress address is not deterministic, so a task demands
                       that SOME exception exists, never a literal address). Unknown
                       expectation keys are REFUSED, not skipped: silently ignored, they
                       would let a wide-open vault satisfy a spec demanding
                       `defaultAction: "Deny"`
    keyvault-secret  — the named secret reads over the data plane (or with
                       `expect: {exists: false}`, does not); optional `value` (compared but
                       NEVER echoed into the check output), `contentType`, and `expirySet`
                       (attributes.exp present — the value itself is run-date-relative, so
                       only presence is deterministic). Both planes are plain REST with
                       azure-identity tokens — no azure-keyvault-* package needed
    keyvault-name-availability — whether a vault NAME is free, via the subscription-scope
                       `checkNameAvailability` POST: `nameAvailable` (required — a
                       `reason`-only expectation grades the wording and not the fact) and
                       optional `reason` (`AlreadyExists` when something holds the name;
                       compared case-insensitively). The name comes from the check's own
                       `name` field and never from the spec's top-level `vault` — the point
                       is to ask about a name that may belong to no live vault. This is the
                       ONLY window an RG-scoped identity has onto soft-delete state
                       (`deletedVaults` reads and purge are subscription-scope and 403 for
                       it), and so the only way to assert "the delete did not free the
                       name". `keyvault-vault {exists: false}` cannot:
                       soft-deleted, purged and never-created all read as absent there.
                       Unknown expectation keys are REFUSED, not skipped
    keyvault-secret-list — the vault's secret names via the data-plane LIST endpoint (paged),
                       matched against `names` (exact set) and/or `count`. LIST and GET share
                       no RBAC data-action by default in a hand-rolled role, so this is the
                       only check that actually exercises enumeration rather than a
                       remembered name — a vault where GET works but LIST 403s fails here

CI/CD check types (specs with a top-level "deploymentPipeline": "<displayName>" — or, for
suites with no pipeline, a top-level "workspaces": [<displayName>, ...]; every check also
names its own `pipeline`/`workspace`, because a deployment pipeline is tenant-scoped rather
than workspace-scoped; runners in test_cicd.py):
    deployment-pipeline  — the pipeline exists with `stageCount` stages whose `stageNames`
                       match BY POSITION (stage identity is `order`, 0-based; the display
                       name means nothing to the API), and `stageWorkspaces` maps an order to
                       the workspace display name assigned there (null = must be unassigned)
    stage-items      — items the pipeline reports in the stage at order `stage`:
                       `contains` / `absent` (a bare name or {name, type}) and `count`
    workspace-items  — the same matchers against `workspaces/{id}/items` for a workspace
                       resolved by display name — the INDEPENDENT read. "The deploy reported
                       success" and "the target workspace holds the item" are different
                       claims, and only this one settles the second
    deployment-operation — one deployment-history entry (`index`, newest = 0) with
                       its per-item `executionPlan.steps[]`: `stepItems`, `allStepsStatus`,
                       `preDeploymentDiffState`, plus `status`, `performedByPresent`,
                       `notePresent`/`noteContains` and `operationCount`/`minOperations`.
                       The top-level status is ONE verdict over every item — assert the steps

Git-integration check types (specs with a top-level "gitRepo": "<repository name>"; optional
"gitOwner", "gitBranch" (default "main") and "gitConnection"; runners in test_git.py):
    github-repo      — the repository exists on the PROVIDER (or with `exists: false`, does
                       not), with `private`, `defaultBranch`, `branches` (all present),
                       `branchesAbsent` (none present — the teardown direction) and
                       `nonEmpty` (a commit is reachable on the ref — an auto_init=False repo
                       has no branch ref for initializeConnection to point at)
    github-tree      — the repository tree at `ref`, optionally under `subdir`: `contains` /
                       `absent` path SUBSTRINGS, `minPaths`, `count`, and `contentContains` /
                       `contentLacks` (searched across the files' text, one GET each).
                       `contentPaths` / `contentExclude` narrow WHICH files the content
                       search reads (path substrings), leaving the path assertions over the
                       full tree — needed whenever a directory must hold both the sources a
                       `contentLacks` polices and a settings sheet that legitimately carries
                       the forbidden values. A content assertion scoped to zero files FAILS
                       rather than passing vacuously. Substring, not exact: Fabric's exported
                       item layout is documented but unobserved, and an exact path would be
                       a guess wearing an acceptance test's clothes. THIS is how a commit is
                       proven — see git-status's note
    github-file      — one file's decoded text: `exists`, `contains`, `lacks`, `jsonKeys`
                       (parses and asserts keys), and `noTokenLiteral` (fails if a GitHub
                       token prefix was committed). The evidence-artifact check
    git-connection   — GET .../git/connection: `state` / `stateIn`, `provider`, `owner`,
                       `organization`, `project`, `repository`, `branch`, `directory`.
                       `connect` alone leaves the workspace reading as connected and syncing
                       nothing; only initializeConnection moves it to ConnectedAndInitialized.
                       The intermediate literal observed live is `Connected` (NOT
                       `ConnectedAndUninitialized` as Learn documents) — assert it with
                       `stateIn` against both if a spec needs the intermediate state.
                       Targets the run's bound workspace by default; a multi-workspace
                       topology suite instead names each binding's own `workspace` (display
                       name, resolved like workspace-items)
    git-status       — GET .../git/status: `changeCount` / `minChanges` / `maxChanges`,
                       `conflictTypesAbsent`, `hasRemoteCommit`, `headsEqual`. A 200 from
                       commitToGit is Fabric acknowledging the REQUEST, not evidence anything
                       moved: pair this with github-tree, never trust it alone. Accepts the
                       same optional `workspace` display name as git-connection
    fabric-connection— the tenant's /v1/connections entry by display name: `exists`, `type`
                       (GitHubSourceControl), `connectivityType`, `credentialType`. Secrets
                       are never echoed back, so only the SHAPE is assertable — which is the
                       failure: a wrong-creationMethod connection exists and cannot serve git.
                       Optional `count` lists the FULL tenant connections and counts matches
                       by this display name — a single-lookup check cannot see a connection
                       created once per caller instead of reused; `count` is what does
    git-credentials  — GET .../git/myGitCredentials for the calling principal: `source`
                       (ConfiguredConnection | Automatic | None) and `connection` (a tenant
                       connection DISPLAY NAME — the check resolves it over /v1/connections
                       and requires this workspace's binding to route through exactly that
                       object). Read once per workspace across a set, this is the
                       one-connection-per-repository detector: a loop that mints a connection
                       per workspace leaves each binding pointing at a different id, and
                       every binding but one fails here even though every git-connection
                       check reads green. Accepts `workspace` like git-connection
    workspace-item   — any workspace item by display name (optional `itemType` filter):
                       `exists` true/false and `type`. `exists: false` is the sharp direction —
                       it is how a suite asserts an untracked item survived (or did not) an
                       updateFromGit run carrying `allowOverrideItems`

Data agent check types (specs with a top-level "dataAgent": "<displayName>", or a per-check
`agent`; runners in test_dataagent.py). Nothing here asserts an agent's ANSWER — the whole
family reads the multi-part item definition, where draft and published configuration both
live, so configuration is exact and draft-vs-published divergence is visible. Behavioural
quality is graded by the existing `spark-sql` runner over the `evaluation_output` Delta table
(`minScalar` on accuracy), deliberately NOT by a check type here:
    data-agent-item       — the item exists by display name, is the right item `type`, carries
                            a routable `description`, and sits in the expected `folder`
    data-agent-definition — the decoded parts for a `stage` (draft|published): `schemaVersion`,
                            `published` (publish_info.json + published/stage_config.json —
                            an updateDefinition does NOT publish), `partsPresent`/`partsAbsent`,
                            `aiInstructions` (contains/lacks/matches/nonEmpty), and the stage's
                            data sources (`sources`/`sourcesContain`/`sourcesAbsent`/
                            `sourceCount`/`maxSources`, the last against the ceiling of five)
    data-agent-datasource — one source's datasource.json: `type` (enum), `artifactId`,
                            `workspaceId`, `dataSourceInstructions`, `userDescription`, and the
                            `elements` tree — `selected` (exact set, by element name OR path),
                            `selectedCount`/`minSelected`/`maxSelected`, `selectedTypes`, and
                            `typesAbsent` (how "selected lakehouse FILES, which are not
                            supported" is caught). `elements` takes a nested expect block
                            evaluated with the shared matchers over the flattened tree
    data-agent-fewshots   — one source's fewshots.json, as rows carrying the shared matchers
                            (`count`/`minCount`/`maxCount`/`unique`/`nonNull`), plus `exists`
                            and `questionsContain`/`questionsAbsent`. An absent part reads as
                            ZERO few-shots, which is the correct state for a semantic model
                            source, where example pairs are not supported at all
    data-agent-stage-diff — draft vs published INSIDE the one definition: `same` / `differs`
                            over `instructions`, `runtime`, `stageConfig`, `sources`,
                            `sourceConfig`, `selectedElements`, `fewshots`. This is the only
                            check that can tell "I published that" from "the published stage
                            still holds last week's configuration"
    Every one of these REFUSES unknown expectation keys rather than skipping them.

Fabric-app (composed pattern) check types (specs with a top-level "graphqlApi":
"<displayName>", or a per-check `api`; runners in test_azureapp.py). The family asserts
the API-for-GraphQL item the ontology GENERATES:
    graphql-api-item    — the item exists (or with `exists: false`, does not), `folder`
                          filing and `description` substring
    graphql-definition  — the decoded definition (one getDefinition LRO per API per run,
                          cached): `types` (exact set) / `typesContain` / `typesAbsent` /
                          `typeCount`, datasource `sourceType`, `readOnly` (no
                          Create/Update/Delete action enabled on any type — the generated
                          posture), `sourceObjects` ({type: "dbo.table"} subset) and
                          `relationships` ([{type, field, cardinality?, targetObject?}] —
                          each must exist on the named type). THIS is the ontology-mirror
                          assertion: expectations are derived from the ontology definition
                          the API was generated from
    graphql-query       — POST executeQuery as the calling principal (SPN-callable) for one
                          `collection`; rows are `data.<collection>.items`, GraphQL `errors`
                          and absent collections surface as failures, and the rows carry
                          the shared exact/invariant matchers
    Every one of these REFUSES unknown expectation keys rather than skipping them.
"""
import argparse
import difflib
import importlib
import json
import os
import re
import sys
import time
from pathlib import Path

# Self-location: put code/ on the path, then let toolkit_path add the layers, so the sibling
# imports below resolve when this file is run from any cwd.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import toolkit_path  # noqa: F401,E402

# Check questions and error text carry em dashes; a cp1252 console would mangle or crash on them.
# stderr too: usage errors carry them as well, and it is the stream a failing run is read from.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover - non-reconfigurable stream
        pass

import test_common                                           # noqa: E402
from test_common import (_GUID_RE, _dp_workspace_names,      # noqa: E402
                         _load_spec, _usage_error)

# Check families are optional: a distribution ships the ones its tasks need, so this engine
# runs identically whether eleven families are present or two. Each loads by name and an
# absent one is skipped. Asking for a check type whose family is absent IS an error, and the
# refusal names what is missing so the message is actionable. `test_common` is not a family
# — it holds the helpers every family uses, and is always present.
_FAMILY_NAMES = ("test_ontology", "test_openmirror", "test_lakehouse", "test_notebook",
                 "test_keyvault", "test_cicd", "test_git", "test_dataagent",
                 "test_azureapp", "test_webapp", "test_azuremonitor", "test_pipeline")

_FAMILY, ABSENT_FAMILIES = {}, []
for _family in _FAMILY_NAMES:
    try:
        _FAMILY[_family] = importlib.import_module(_family)
    except ModuleNotFoundError:
        ABSENT_FAMILIES.append(_family)
_MODULES = tuple(_FAMILY.values())


def family(name):
    """The named family module, or None when this bundle did not ship it.

    Call sites that reach into ONE family by name (rather than through the merged check-type
    registry) go through here, so a subset bundle degrades to a clear refusal instead of a
    NameError at import time.
    """
    return _FAMILY.get(name)

# Ontology's graph-name matcher is reached only from a branch already guarded by "does this
# spec carry a graph check type", so its absence is unreachable rather than merely tolerated.
try:
    from test_ontology import _graph_name_matches             # noqa: E402
except ModuleNotFoundError:                                   # pragma: no cover - subset bundle
    _graph_name_matches = None


# ── the check-type registry ───────────────────────────────────────────────────
# One declaration per type, owned by its topic module: (runner, required fields, one-line
# description, other check-level keys) in the module's RUNNERS dict. This file only
# MERGES — everything that needs the type list (dispatch, --dry-run's required-field table,
# the check-level key vocabulary, --list-checks) derives from the merged dict so they can
# never drift apart. A type declared by two modules is a startup error, never a silent
# last-writer-wins; so is a type that declares no key vocabulary.

# Check-level keys EVERY type accepts, whatever its runner does with them — declared once
# here rather than repeated in eleven modules:
#     type      the dispatch key
#     id        the check's name in the output, in `--checks`, in the --json verdict
#     question  the human sentence the check answers (printed; never asserted on)
#     expect    the expectation block — its own vocabulary is the runner's business
#     requiresEnv  the environment gate — {"vars": [...], "remedy": "..."} (see env_gate)
# Everything else a check may carry must be declared by its type. Keys beginning with `_`
# are author annotations (`_evidence`, `_note`, `_spoiler`), ignored by the engine by
# design — the same convention the top-level `_specDir` follows.
_UNIVERSAL_CHECK_KEYS = frozenset({"type", "id", "question", "expect", "requiresEnv"})


def _merge_registry(modules):
    """(check-type entries, check-level key vocabulary, `expect` key vocabulary) over `modules`.

    Every check type states its own vocabulary at BOTH levels — the keys beside `expect` (the
    4th `RUNNERS` field) and the keys inside it (the module's `EXPECT_KEYS`) — and this
    function is the only place the three tables are built, so they cannot drift apart.

    Four structural refusals, all LOUD at startup rather than silent at run time. A fixture
    hands this a malformed stub module to prove each one fires:
      · a type declared by two modules          — never last-writer-wins
      · a `RUNNERS` entry that is not a 4-tuple — an undeclared check-level vocabulary
      · a vocabulary that is not strings        — `"viewports"` is 9 one-letter keys
      · a type with no `EXPECT_KEYS` entry      — an unread key inside `expect` asserts
                                                  nothing, and would pass in silence

    The last is the reason neither field is optional. An "undeclared means don't check"
    fallback is exactly the false green both vocabularies exist to end, so a type that
    declares nothing is refused rather than exempted.
    """
    types, keys, expect_keys = {}, {}, {}
    for mod in modules:
        where = getattr(mod, "__name__", mod)
        declared = getattr(mod, "EXPECT_KEYS", {})
        for name, entry in mod.RUNNERS.items():
            if name in types:
                raise RuntimeError(f"duplicate check type {name!r} declared by more than one "
                                   "test_* module — every type must have exactly one home")
            if len(entry) != 4:
                raise RuntimeError(
                    f"check type {name!r} in {where} declares {len(entry)} registry "
                    "field(s), not 4 — every type must state (runner, required fields, "
                    "description, other check-level keys). An undeclared vocabulary would "
                    "let an unread key pass in silence, which is the bug this field closes")
            extra = entry[3]
            if isinstance(extra, str) or not all(isinstance(k, str) for k in extra):
                raise RuntimeError(f"check type {name!r} must declare its other check-level "
                                   f"keys as a tuple of strings, got {extra!r}")
            if name not in declared:
                raise RuntimeError(
                    f"check type {name!r} in {where} declares no EXPECT_KEYS entry — every "
                    "type must state the keys its runner reads INSIDE `expect`. A "
                    "pure-query type whose whole block goes to the shared matcher declares "
                    "MATCHER_KEYS; every other type declares the set it reads")
            types[name] = entry
            keys[name] = frozenset(_UNIVERSAL_CHECK_KEYS) | set(entry[1]) | set(extra)
            expect_keys[name] = frozenset(declared[name])
    return types, keys, expect_keys


_CHECK_TYPES, _CHECK_KEYS, _EXPECT_KEYS = _merge_registry(_MODULES)

_RUNNERS = {name: entry[0] for name, entry in _CHECK_TYPES.items()}


def absent_family_hint():
    """The trailing clause an unknown-check-type message carries in a subset bundle.

    Empty in a full checkout, so the message is unchanged there. In a bundle that ships
    only some families, an unknown type is far more often "this bundle does not carry that
    family" than "you typo'd the type", and saying so is the difference between an
    actionable message and a mystery. Derived from what actually failed to import rather
    than a type -> family table, which would drift the moment a family gains a type.
    """
    if not ABSENT_FAMILIES:
        return ""
    return (f" — this bundle does not carry the {', '.join(ABSENT_FAMILIES)} "
            "check famil" + ("y" if len(ABSENT_FAMILIES) == 1 else "ies") +
            "; a fuller bundle ships them")


def unknown_check_keys(check):
    """Sorted check-level keys this check's type does not read (its `expect` block aside).

    An unknown type has no vocabulary to judge against and returns [] — the unknown-type
    verdict is the louder message and gets to be the one reported.
    """
    allowed = _CHECK_KEYS.get(check.get("type"))
    if allowed is None:
        return []
    return sorted(k for k in check
                  if k not in allowed and not str(k).startswith("_"))


def expect_key_refusal(check):
    """A REFUSED reason for unknown keys INSIDE `expect`, or None — offline.

    The companion to `check_key_refusal`, one level down: check-level keys are validated
    offline; without this, the keys inside `expect` would be validated only at RUN time, by
    whichever matcher happened to receive them — so an authored-but-never-run spec could
    carry an expectation no runner reads and only fail on first execution.

    Every registered type has a vocabulary — `_merge_registry` refuses a module that omits
    one — so this needs no special case for the pure-query types. Those declare
    `MATCHER_KEYS`, which is the same set their runner would enforce on a live run; saying
    so is what lets the identical refusal happen offline.
    """
    expect = check.get("expect")
    ctype = check.get("type")
    if not isinstance(expect, dict) or ctype not in _EXPECT_KEYS:
        return None                     # an unknown type: the unknown-type verdict is louder
    allowed = _EXPECT_KEYS[ctype]
    unknown = sorted(set(expect) - allowed)
    if not unknown:
        return None
    hints = []
    for key in unknown:
        near = difflib.get_close_matches(key, sorted(allowed), n=1, cutoff=0.6)
        elsewhere = sorted(t for t, ks in _EXPECT_KEYS.items() if key in ks and t != ctype)
        if near:
            hints.append(f"{key!r} (did you mean {near[0]!r}?)")
        elif elsewhere:
            hints.append(f"{key!r} (read by {', '.join(elsewhere[:3])}, not {ctype})")
        else:
            hints.append(repr(key))
    return (f"REFUSED: {ctype} does not read " + ", ".join(hints)
            + " — a key no runner reads asserts nothing. Accepts: "
            + ", ".join(sorted(allowed)))


def check_key_refusal(check):
    """A REFUSED reason naming the offending check-level key(s), or None when the check is clean.

    The house rule for `expect` keys — unknown means REFUSED, never skipped — applied one
    level up, to the keys on the check dict itself. Unvalidated, a runner can silently
    ignore an option its sibling honours: a spec asking for a phone-sized accessibility
    scan quietly gets a desktop one and passes. A key a runner never reads asserts
    nothing, and a spec that believes it does is a false green.

    The reason names each offending key with the likeliest intent: a near-miss spelling in
    this type's own vocabulary, else the sibling types that DO honour the key (the
    `viewports`-on-`http-probe` case), else the full vocabulary to choose from.
    """
    unknown = unknown_check_keys(check)
    if not unknown:
        return None
    ctype = check["type"]
    allowed = _CHECK_KEYS[ctype]
    hints = []
    for key in unknown:
        near = difflib.get_close_matches(key, sorted(allowed), n=1, cutoff=0.7)
        if near:
            hints.append(f"{key!r} (did you mean {near[0]!r}?)")
            continue
        elsewhere = sorted(t for t, ks in _CHECK_KEYS.items() if key in ks)
        hints.append(f"{key!r}" + (f" (honoured by {', '.join(elsewhere)}, not {ctype})"
                                   if elsewhere else ""))
    return (f"REFUSED: {ctype} does not read check key(s) " + ", ".join(hints)
            + f" — a key no runner reads asserts nothing. {ctype} accepts: "
            + ", ".join(sorted(allowed)))


# ── the environment gate: {env:NAME} substitution + PARKED-not-failed ─────────
# Two mechanisms, deliberately paired, because either alone is a trap.
#
# `{env:NAME}` in any string field of a check is replaced by that environment variable
# before the runner sees the check. It is the only way to express an assertion whose input
# is a credential the repository cannot hold — a delegated (real-user) access token being
# the case that forced it: OBO preserves USER identity by design, so no service-principal
# harness can mint one, and until 2026-08-13 the engine read no environment variable into
# any check field at all.
#
# `requiresEnv` is the gate that makes such a check SKIP — never fail — when the variable
# is absent. Substitution WITHOUT the gate would be worse than the gap it closes: the
# assertion could only ever be unconditional, so it would fail every rep that has no
# credential, which is the opposite of parking. So the two ship together and the invariant
# below ties them: EVERY `{env:NAME}` reference must be declared in its own check's
# `requiresEnv.vars`. An undeclared reference is REFUSED, because an absent variable would
# otherwise be substituted as the literal text `{env:NAME}` and posted as if it were a
# credential — and a gateway correctly refusing that garbage returns exactly the 401 the
# spec was hoping to see. That is a false green with a credential's name on it.
#
# The reverse direction is allowed on purpose: a variable may be DECLARED without being
# referenced, for a check whose runner reads the environment itself (the ARM/Key Vault
# families do) and whose author wants the park to be explicit and carry a stated remedy.
_ENV_REF_RE = re.compile(r"\{env:([A-Za-z_][A-Za-z0-9_]*)\}")
_GATE_KEYS = frozenset({"vars", "remedy"})


def _env_strings(node):
    """Every string in a check's fields, annotations (`_`-prefixed keys) excluded.

    The exclusion matters both ways: a `_note` that DOCUMENTS the `{env:NAME}` syntax must
    not be read as a reference (it would demand a gate for a variable nothing asserts on),
    and must not be rewritten by substitution either.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if not str(key).startswith("_"):
                yield from _env_strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _env_strings(value)
    elif isinstance(node, str):
        yield node


def env_references(check):
    """Sorted environment variable names this check's fields reference via `{env:NAME}`."""
    names = set()
    for text in _env_strings(check):
        names.update(_ENV_REF_RE.findall(text))
    return sorted(names)


def env_gate(check, env=None):
    """(outcome, reason) for a check's environment gate: 'run' | 'park' | 'refuse'.

    'refuse' is a FAILED check (a malformed gate, or an ungated `{env:}` reference — both
    are authoring errors that would otherwise assert something other than what the spec
    says). 'park' is a SKIP whose reason names the missing variable(s) AND the author's
    remedy: a park with no remedy tells the next run nothing actionable, which is why
    `remedy` is required rather than optional. 'run' means the check may be substituted
    and dispatched.

    Pass `env={}` to evaluate the structural half offline (the authoring gates do), where
    every declared variable is absent and only real authoring errors surface as 'refuse'.
    """
    env = os.environ if env is None else env
    gate = check.get("requiresEnv")
    declared, remedy = [], ""
    if gate is not None:
        if not isinstance(gate, dict):
            return "refuse", ("REFUSED: 'requiresEnv' must be an object "
                              '{"vars": ["NAME", ...], "remedy": "how to supply it"}, got '
                              f"{type(gate).__name__} — the remedy is not optional, because a "
                              "check that parks without saying how to un-park it is a silent gap")
        unknown = sorted(set(gate) - _GATE_KEYS)
        if unknown:
            return "refuse", (f"REFUSED: 'requiresEnv' does not read key(s) {unknown} "
                              f"(it accepts {sorted(_GATE_KEYS)})")
        declared = gate.get("vars")
        remedy = gate.get("remedy")
        if (not isinstance(declared, list) or not declared
                or not all(isinstance(v, str) and v.strip() for v in declared)):
            return "refuse", ("REFUSED: 'requiresEnv'.vars must be a non-empty list of "
                              f"environment variable names, got {declared!r}")
        if not isinstance(remedy, str) or not remedy.strip():
            return "refuse", ("REFUSED: 'requiresEnv'.remedy must be a non-empty string — a "
                              "parked check has to tell the next run what would run it")
    ungated = [ref for ref in env_references(check) if ref not in declared]
    if ungated:
        return "refuse", (f"REFUSED: check field(s) reference {{env:...}} for undeclared "
                          f"variable(s) {ungated} — every environment reference must be named "
                          "in this check's 'requiresEnv'.vars, or an absent variable would be "
                          "substituted as the literal placeholder text and asserted with as "
                          "though it were the real value")
    # An empty or whitespace-only variable counts as ABSENT: the empty string is not a
    # credential, and treating it as one turns a mis-set variable into a transport-level
    # failure that reads like a contract failure.
    missing = [v for v in declared if not str(env.get(v) or "").strip()]
    if missing:
        return "park", (f"PARKED, not passed — {', '.join(missing)} "
                        f"{'is' if len(missing) == 1 else 'are'} not set in the environment, so "
                        f"this check made no assertion at all. Remedy: {remedy.strip()}")
    return "run", ""


def substitute_env(check, env=None):
    """A copy of `check` with every `{env:NAME}` in a string field replaced by its value.

    Only string LEAVES are rewritten, and only under non-annotation keys, so a `_note` or
    `_evidence` that documents the syntax survives verbatim. A name with no value in the
    environment is left as-is: `env_gate` has already parked or refused every such check, so
    reaching a runner with an unresolved placeholder is impossible by construction — and if
    a future caller substitutes without gating, leaving the placeholder visible in the
    request is what makes that mistake legible instead of silent.
    """
    env = os.environ if env is None else env

    def walk(node):
        if isinstance(node, dict):
            return {k: (v if str(k).startswith("_") else walk(v)) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v) for v in node]
        if isinstance(node, str):
            return _ENV_REF_RE.sub(
                lambda m: str(env[m.group(1)]) if m.group(1) in env else m.group(0), node)
        return node

    return walk(check)


# Check types that never call Fabric — the whole test_webapp family. A suite made only of
# these binds no workspace (see run_suite). Derived from the module, never hand-listed, so
# a runner added there cannot drift out of this set.
_WORKSPACE_FREE_TYPES = frozenset(family("test_webapp").RUNNERS
                                  if family("test_webapp") else ())

# The spec keys that name a TARGET — what --dry-run requires at least one of. Exported
# because composing callers gate on the same question, and kept here because this module
# owns the vocabulary: a copy that must be "kept identical" is a copy that will drift.
TARGET_KEYS = ("workspace", "graphModel", "mirror", "lakehouse", "vault",
               "deploymentPipeline", "gitRepo", "workspaces", "dataAgent", "graphqlApi",
               "entraApp", "containerApp", "app", "baseUrl",
               "logAnalytics", "appInsights", "foundryAccount", "foundryProject", "pipeline")


# ── workspace binding + spec loading ──────────────────────────────────────────

# Top-level spec keys the engine consumes. Anything else — a composing caller's own
# metadata — is ignored with a one-line notice, so such a spec runs here unchanged.
_KNOWN_TOP_KEYS = {
    "task", "workspace", "checks",
    "graphModel", "mirror", "defaultSchema", "lakehouse", "notebook", "vault",
    "deploymentPipeline", "workspaces", "gitRepo", "gitOwner", "gitBranch", "gitConnection",
    "dataAgent", "graphqlApi", "entraApp", "containerApp",
    "app", "baseUrl",
    # Azure Monitor / Foundry targets (test_azuremonitor). All four are ARM resource NAMES
    # in AZURE_RESOURCE_GROUP; the subscription and group come from the environment exactly
    # as they do for the Key Vault and Container App families.
    "logAnalytics", "appInsights", "foundryAccount", "foundryProject",
    # The DataPipeline target (test_pipeline). A key in TARGET_KEYS must be known here too
    # or the engine would consume it for the banner while reporting it unknown;
    # `target-keys-are-known-top-keys` makes that pairing an invariant.
    "pipeline",
    # `_specDir` is injected by run_suite itself (so a relative "app" resolves against the
    # spec's own directory); named here so the engine does not nag about a key the spec
    # author never wrote.
    "_specDir",
}


def _unknown_top_keys(spec, tolerated=()):
    """Sorted top-level keys the engine does not consume (noticed, never rejected).

    `tolerated` names caller-owned keys to keep quiet about, so a composing caller with
    its own spec vocabulary is not nagged about its own schema.
    """
    return sorted(k for k in spec if k not in _KNOWN_TOP_KEYS and k not in tolerated)


def _pick_workspace(cli_workspace, spec):
    """(value, source-label) per the binding order: --workspace > spec "workspace" > env.

    The env step imports config purely for its .env-loading side effect; a partial
    environment (SPN vars unset) falls through to whatever the process already holds.
    """
    if cli_workspace:
        return cli_workspace, "--workspace"
    if spec.get("workspace"):
        return spec["workspace"], "spec 'workspace'"
    import os
    try:
        import config  # noqa: F401 — importing it loads .env into the process env
    except (KeyError, RuntimeError):
        pass
    ws = os.environ.get("FABRIC_WORKSPACE_ID")
    if ws:
        return ws, "FABRIC_WORKSPACE_ID env"
    return None, None


def _resolve_workspace(value, source):
    """(workspace_id, display_name) for a guid-or-name binding; exits 2 when unresolvable.

    A GUID is used as-is (its display name is read back for the banner; a failed read
    leaves the name unresolved rather than killing the run). A display name is resolved
    over every workspace the identity can see, and an ambiguous name is refused — the
    whole point of the banner is that the target must be unmistakable.
    """
    if not value:
        _usage_error("no workspace to bind to — pass --workspace <guid-or-name>, set a "
                     "top-level \"workspace\" in the spec, or set FABRIC_WORKSPACE_ID "
                     "in the environment (or .env)")
    try:
        import fabric_read as _fr
    except (KeyError, RuntimeError) as exc:
        _usage_error(f"cannot construct the Fabric client — missing environment variable: {exc} "
                     "(AZURE_TENANT_ID / AZURE_CLIENT_ID / AZURE_CLIENT_SECRET / "
                     "FABRIC_WORKSPACE_ID, from the process env or .env)")
    value = value.strip()
    if _GUID_RE.match(value):
        try:
            name = _fr.get_workspace(value).get("displayName")
        except Exception:
            name = None
        return value, name
    # Resolve the name over the PAGED workspace list: a single-page read would let a
    # duplicate name on a later page slip past the ambiguity refusal — the one guarantee
    # this banner exists to give.
    try:
        names = _dp_workspace_names({})
    except Exception as exc:
        _usage_error(f"could not list workspaces to resolve {value!r} [{source}]: {exc}")
    matches = [wid for wid, wname in names.items() if wname == value]
    if not matches:
        _usage_error(f"no workspace named {value!r} [{source}] is visible to this identity")
    if len(matches) > 1:
        _usage_error(f"workspace name {value!r} [{source}] is ambiguous: "
                     f"{matches} — pass the GUID instead")
    return matches[0], value


def _verdict(workspace, results, parked=()):
    """The machine-readable run verdict — the shape `--json` writes and a CI gate consumes.

    `passed` counts `ok is True` only, so a SKIP has never been able to inflate it. `parked`
    is the sub-list of skipped check ids that an environment gate held back (see `env_gate`)
    — the machine-readable half of "this run asserted nothing about those legs". It is a
    subset of `skipped`, which also counts a runner's own skips (no Chromium, a 403 from
    Graph); the two are different statements and are reported as two.
    """
    return {
        "workspace": workspace,
        "passed": sum(1 for r in results if r["ok"] is True),
        "failed": sum(1 for r in results if r["ok"] is False),
        "skipped": sum(1 for r in results if r["ok"] is None),
        "parked": list(parked),
        "checks": results,
    }


# ── orchestration ─────────────────────────────────────────────────────────────

def _env_indirect(value):
    """Resolve a spec target of the form ``${ENV_VAR}`` from the environment.

    Lets a spec name a tenant-specific target — an Entra app registration id is the
    canonical case — without hardcoding one tenant's literal into a committed file:
    ``"entraApp": "${AZUREAPP_CLIENT_ID}"`` reads the id from the runner's environment.
    Unset resolves to None, so the owning checks give their normal "no appId" refusal
    (with the variable named in the spec) instead of asserting against a wrong literal.
    Any other value is returned unchanged.
    """
    if isinstance(value, str):
        m = re.fullmatch(r"\$\{([A-Z][A-Z0-9_]*)\}", value.strip())
        if m:
            return os.environ.get(m.group(1)) or None
    return value


def _build_context(spec, args, workspace_id):
    ctx = {
        "workspace_id": workspace_id,
        "allow_write": bool(getattr(args, "allow_write", False)),
        "graph": None,
        "graph_id": args.graph_id,
        "lakehouse_id": args.lakehouse_id,
        # open-mirroring context (populated when the spec targets a mirror)
        "mirror": None,
        "mirror_id": args.mirror_id,
        "mirror_name": spec.get("mirror"),
        "default_schema": spec.get("defaultSchema", "dbo"),
        "lz": None,
        "duck": None,
        # lakehouse context (populated when the spec targets a lakehouse)
        "lakehouse": None,
        "lakehouse_name": spec.get("lakehouse"),
        "notebook_name": spec.get("notebook"),
        "notebook_ids": {},
        "livy": None,
        # key-vault context (populated when the spec targets a vault)
        "vault_name": spec.get("vault"),
        # git-integration context (populated when the spec targets a repository)
        "git_repo": spec.get("gitRepo"),
        "git_owner": spec.get("gitOwner"),
        "git_branch": spec.get("gitBranch", "main"),
        "git_connection_name": spec.get("gitConnection"),
        # deployment-pipeline / data-pipeline context: the display names the cicd and
        # pipeline families fall back to when a check names no `pipeline` of its own.
        # Wired here because the doc promises the top-level fallback; without these two
        # lines the fallback reads None and the refusal blames the spec for a key it has.
        "pipeline_name": spec.get("deploymentPipeline"),
        "pipeline": spec.get("pipeline"),
        # data agent context: the display name every data-agent-* check falls back to when
        # it does not name its own `agent`. No client is constructed here — the definition
        # is fetched lazily by the first check that needs it (one LRO, cached per run).
        "data_agent_name": spec.get("dataAgent"),
        # azure-app context: the API-for-GraphQL display name every graphql-* check falls
        # back to when it does not name its own `api`. No client is constructed here — the
        # item and its definition are fetched lazily and cached per run (one LRO per API).
        "graphql_api_name": spec.get("graphqlApi"),
        # azure-app identity/hosting targets: the registration and the deployment a check
        # falls back to when it names neither itself.
        "entra_app_id": _env_indirect(spec.get("entraApp")),
        "container_app_name": spec.get("containerApp"),
        # web-application context (test_webapp): the source tree and the running origin.
        # `spec_dir` is what makes a relative "app" mean the same thing wherever the
        # validator is invoked from — a task folder's own directory, not the process cwd.
        "app_root": spec.get("app"),
        "base_url": spec.get("baseUrl"),
        "spec_dir": spec.get("_specDir"),
        # Azure Monitor / Foundry context (test_azuremonitor): the ARM resource names every
        # check of that family falls back to when it names none of its own. No client is
        # constructed — each runner is one ARM GET with the shared cached token.
        "log_analytics_name": spec.get("logAnalytics"),
        "app_insights_name": spec.get("appInsights"),
        "foundry_account_name": spec.get("foundryAccount"),
        "foundry_project_name": spec.get("foundryProject"),
    }
    if spec.get("graphModel") or any(c["type"] in ("item", "schema", "gql")
                                     for c in spec.get("checks", [])):
        ctx["graph"] = family("test_ontology").GraphReads(workspace_id)
        if ctx["graph_id"] is None and spec.get("graphModel"):
            match = next((g for g in ctx["graph"].list_graph_models()
                          if _graph_name_matches(g.get("displayName", ""), spec["graphModel"])), None)
            if match:
                ctx["graph_id"] = match["id"]
    if spec.get("mirror"):
        ctx["mirror"] = family("test_openmirror").MirrorReads(workspace_id)
        if ctx["mirror_id"] is None:
            match = ctx["mirror"].resolve_by_name(spec["mirror"])
            if match:
                ctx["mirror_id"] = match["id"]
    if spec.get("lakehouse") and ctx["lakehouse_id"] is None:
        import fabric_read as _fr
        match = _fr.resolve_by_name(spec["lakehouse"], "Lakehouse", workspace_id)
        if match:
            ctx["lakehouse_id"] = match["id"]
    return ctx


def _select_checks(checks, wanted):
    """Narrow a spec's checks to the named ids (`--checks`). Returns (selected, unknown).

    Order is the spec's, not the argument's — a check may populate ctx for a later one
    (`notebook-item` caches the notebook id `job-run` reads), so reordering them by what the
    user happened to type would break that.
    """
    if not wanted:
        return checks, []
    known = {c.get("id", c["type"]) for c in checks}
    unknown = [w for w in wanted if w not in known]
    return [c for c in checks if c.get("id", c["type"]) in wanted], unknown


def run_suite(spec, *, workspace=None, graph_id=None, lakehouse_id=None, mirror_id=None,
              allow_write=False, only_checks=None, json_out=None, tolerated_keys=(),
              no_park=False):
    """Programmatic entry: run a suite and return the verdict dict (see `_verdict`).

    `spec` is a path to a spec file or an already-parsed spec dict. This is the SAME code
    path the CLI uses — `run()` is a thin wrapper over it — so the two can never fork.
    `only_checks` narrows to named check ids (a list, or the CLI's comma string);
    `tolerated_keys` suppresses the unknown-top-level-key notice for exactly those keys
    (a composing caller's own top-level vocabulary). Usage/spec errors
    raise SystemExit(2) exactly as the CLI does.

    `no_park=True` turns an environment gate's SKIP into a FAILURE — the mode for the run
    that is supposed to PROVE a gated leg. Parking exists so a missing credential does not
    fail a rep that never claimed the leg; it must not also let the run that does claim it
    pass without the credential.
    """
    if isinstance(spec, (str, Path)):
        spec_label, spec = spec, _load_spec(spec)
        # Relative app roots resolve against the SPEC's directory, never the process cwd.
        spec = dict(spec, _specDir=str(Path(spec_label).resolve().parent))
    else:
        spec_label = "(inline spec)"
    checks = spec.get("checks")
    if not isinstance(checks, list) or not checks:
        _usage_error(f"spec {spec_label} has no 'checks' list")
    for i, check in enumerate(checks):
        if not isinstance(check, dict) or not check.get("type"):
            _usage_error(f"spec {spec_label}: checks[{i}] names no 'type'")

    # The disaster case for a test harness is a green run against the WRONG workspace, so
    # the resolved target — display name + GUID + where the binding came from — is always
    # the first line of output. The one exception is a suite whose checks never touch
    # Fabric at all (the test_webapp family: a source tree, an HTTP endpoint, a rendered
    # page, a local parquet). Demanding Fabric credentials to assert on a directory would
    # make the application half of the composed pattern untestable offline, so such a
    # suite skips the binding — and says so as loudly as it would name a workspace.
    # An UNKNOWN type binds nothing either, and for a stronger reason: run_suite refuses it
    # below without calling anything, so a suite with nothing runnable in it must never open
    # a sign-in prompt. That is not hypothetical — a bundle carrying no test_webapp has an
    # empty _WORKSPACE_FREE_TYPES, and this engine's own `--self-test` fixtures (built on
    # `app-source`) then asked for Fabric credentials and hung on a device-code flow.
    def _binds(check):
        return check["type"] in _RUNNERS and check["type"] not in _WORKSPACE_FREE_TYPES

    if any(_binds(c) for c in checks):
        value, source = _pick_workspace(workspace, spec)
        workspace_id, workspace_name = _resolve_workspace(value, source)
        print(f"== target workspace: {workspace_name or '(display name unresolved)'} "
              f"({workspace_id}) [from {source}] ==")
    else:
        workspace_id, workspace_name = None, None
        free = sorted({c["type"] for c in checks} & _WORKSPACE_FREE_TYPES)
        print("== no workspace target: no check in this suite can reach Fabric"
              + (f" (workspace-free: {', '.join(free)})" if free else
                 " (no check names a type this bundle carries — every one will be REFUSED)")
              + " ==")
    ignored = _unknown_top_keys(spec, tolerated_keys)
    if ignored:
        print(f"== ignoring unknown top-level key(s): {', '.join(ignored)} ==")

    # Derived from TARGET_KEYS for the same reason --dry-run is: this banner carried its own
    # transcription and silently printed "(?)" for entraApp, a target the engine accepts.
    _LABELS = {"graphModel": "graph", "mirror": "mirror", "lakehouse": "lakehouse",
               "vault": "vault", "deploymentPipeline": "pipeline", "gitRepo": "repo",
               "dataAgent": "data agent", "graphqlApi": "graphql api",
               "entraApp": "entra app", "containerApp": "container app",
               "baseUrl": "app at", "app": "app source", "workspaces": "workspaces",
               "logAnalytics": "log analytics", "appInsights": "app insights",
               "foundryAccount": "foundry account", "foundryProject": "foundry project"}
    target = next((f"{_LABELS.get(k, k)}: {spec[k]}"
                   for k in TARGET_KEYS if k != "workspace" and spec.get(k)), "?")
    print(f"== validating {spec.get('task', spec_label)} ({target}) ==")
    ctx = _build_context(spec, argparse.Namespace(graph_id=graph_id, lakehouse_id=lakehouse_id,
                                                 mirror_id=mirror_id, allow_write=allow_write),
                         workspace_id)

    if isinstance(only_checks, str):
        wanted = [c.strip() for c in only_checks.split(",") if c.strip()]
    else:
        wanted = list(only_checks or [])
    selected, unknown = _select_checks(spec["checks"], wanted)
    if unknown:
        _usage_error(f"no check with id {unknown} in this spec. "
                     f"Known: {sorted(c.get('id', c['type']) for c in spec['checks'])}")
    if wanted:
        print(f"== --checks: running {len(selected)} of {len(spec['checks'])} "
              "— a PARTIAL run, which never means the suite passes ==")
        spec = dict(spec, checks=selected)

    graph_checks = any(c["type"] in ("gql", "schema") for c in spec["checks"])
    if graph_checks and not ctx["graph_id"]:
        _usage_error(f"could not resolve a graph id for {spec.get('graphModel')!r} "
                     "(pass --graph-id) — gql/schema checks cannot run")

    results, parked = [], []
    try:
        for check in spec["checks"]:
            cid = check.get("id", check["type"])
            started = time.monotonic()
            try:
                runner = _RUNNERS.get(check["type"])
                refusal = check_key_refusal(check) or expect_key_refusal(check)
                gate, gate_reason = env_gate(check)
                if runner is None:
                    ok, reason = False, (f"unknown check type {check['type']!r} "
                                         "(see --list-checks for the vocabulary)"
                                         + absent_family_hint())
                elif refusal:
                    # A key outside the type's declared vocabulary is REFUSED, not ignored:
                    # the runner would silently drop it and pass on a weaker assertion than
                    # the spec states. Applies at BOTH levels — the keys beside `expect` and
                    # the keys inside it.
                    ok, reason = False, refusal
                elif gate == "refuse":
                    ok, reason = False, gate_reason
                elif gate == "park":
                    # The credential-absent path: a SKIP naming the variable and the remedy.
                    # It never becomes a pass — `_verdict` counts `ok is True` only — and it
                    # is listed in the verdict's `parked` so the run says out loud which
                    # assertions were not made. `--no-park` is how a run demands them.
                    if no_park:
                        ok, reason = False, ("FAILED (--no-park, which demands the gated "
                                             f"leg): {gate_reason}")
                    else:
                        ok, reason = None, gate_reason
                        parked.append(cid)
                else:
                    ok, reason = runner(substitute_env(check), ctx)
            except Exception as exc:  # a thrown check is a failed check, with the cause shown
                ok, reason = False, f"ERROR {type(exc).__name__}: {exc}"
            results.append({"id": cid, "type": check["type"], "ok": ok, "reason": reason,
                            "seconds": round(time.monotonic() - started, 3)})
            tag = "SKIP" if ok is None else ("PASS" if ok else "FAIL")
            print(f"  {tag}  {cid}: {reason}")
    finally:
        # A Livy session left open holds Spark compute on the capacity until it times out.
        if ctx.get("livy"):
            try:
                ctx["livy"].close_session()
            except Exception as exc:
                print(f"  (warning: could not close Livy session: {exc})")
        # A Chromium left running outlives the process that started it. A bundle without the
        # webapp family can never have opened one.
        try:
            if family("test_webapp"):
                family("test_webapp").close_browser(ctx)
        except Exception as exc:
            print(f"  (warning: could not close the browser: {exc})")

    verdict = _verdict({"id": workspace_id, "displayName": workspace_name}, results, parked)
    print(f"== {verdict['passed']} passed, {verdict['failed']} failed, "
          f"{verdict['skipped']} skipped ==")
    if parked:
        # Exit 0 with a parked check must not be read as "the suite passes": the parked
        # legs asserted NOTHING, and the only honest record of that is a line as loud as the
        # tally. A gym rep report for such a run has to say the leg was not attempted, and a
        # capability must never be called proven on the strength of a check that never ran.
        print(f"== PARKED ({len(parked)} check(s), no credential): {', '.join(parked)} "
              "— A SKIP IS NOT A PASS. Nothing these checks assert is established by this "
              "run; record the leg as not attempted. Re-run with the variable(s) set, or "
              "with --no-park to require them ==")
    if wanted:
        # Exit 0 on a partial run must not be read as "the suite passes" — it passes only
        # on a full run, and this is the one place that distinction can be made loudly.
        print("== PARTIAL RUN (--checks) — re-run without --checks before trusting the verdict ==")
    if json_out:
        Path(json_out).write_text(json.dumps(verdict, indent=2), encoding="utf-8")
        print(f"== verdict written to {json_out} ==")
    return verdict


def run(spec_path, args):
    """CLI wrapper over `run_suite` — same code path, exit-code shaped."""
    verdict = run_suite(spec_path, workspace=args.workspace, graph_id=args.graph_id,
                        lakehouse_id=args.lakehouse_id, mirror_id=args.mirror_id,
                        allow_write=getattr(args, "allow_write", False),
                        only_checks=getattr(args, "checks", None),
                        json_out=getattr(args, "json_out", None),
                        no_park=getattr(args, "no_park", False))
    return 0 if verdict["failed"] == 0 else 1


# ── offline self-test (no network, no tenant) ─────────────────────────────────
# The strategy:
# no HTTP mocking — every matcher is a pure function tested over literal fixtures copied
# from observed API shapes, plus two tiny duck-typed Livy stand-ins. The fixtures live
# with their family modules (each module's SELF_TESTS); this driver aggregates them, then
# adds the core fixtures below for what this file itself owns.

def _core_self_test_checks():
    checks = []

    def expect(name, cond, needs=None):
        """Record a fixture's verdict — or that this bundle cannot run it.

        `needs` names a check family. A fixture whose SUBJECT is a check type from a family
        this bundle did not ship asserts nothing about this bundle: it would fail on the
        absence rather than on the behaviour, and a red self-test out of the box is worse
        than a smaller one. Declared per fixture rather than inferred from the types the
        fixture mentions, so a fixture cannot fall out of the suite by accident.
        """
        if needs and family(needs) is None:
            checks.append((f"{name} — SKIPPED, no {needs} in this bundle", None))
            return
        checks.append((name, bool(cond)))

    # Every target the banner can resolve must also be a key the engine admits it knows —
    # otherwise it would print `== ignoring unknown top-level key(s) ==` for a key it had
    # just used to label the run. Pairing the two lists is the invariant.
    expect("target-keys-are-known-top-keys",
           set(TARGET_KEYS) <= _KNOWN_TOP_KEYS)

    # `expect` keys, one level below the check-level vocabulary: the refusal exists at run
    # time; this is the offline gate that catches an authored-but-never-run spec.
    expect("expect-refusal-catches-a-non-matcher-key",
           expect_key_refusal({"type": "spark-sql", "id": "t",
                               "expect": {"rowCount": 0}}) is not None)
    expect("expect-refusal-leaves-a-valid-block-alone",
           expect_key_refusal({"type": "spark-sql", "id": "t",
                               "expect": {"scalar": 50, "columns": ["a"]}}) is None)
    # A runner that reads its OWN keys and also calls the matcher for a sub-part accepts the
    # WIDER vocabulary; refusing it against MATCHER_KEYS alone would reject correct specs.
    expect("expect-refusal-accepts-a-partly-matcher-routed-type",
           expect_key_refusal({"type": "data-agent-datasource", "id": "t",
                               "expect": {"elementsPresent": ["X"]}}) is None,
           needs="test_dataagent")
    # Every registered type declares an `expect` vocabulary — enforced at import by
    # _merge_registry, so by the time this runs there is nothing left to scan for.
    expect("every-check-type-declares-an-expect-vocabulary",
           set(_EXPECT_KEYS) == set(_RUNNERS))
    # A declared vocabulary must actually refuse. Pick any declared type and prove a typo dies.
    _sample = sorted(_EXPECT_KEYS)[0]
    expect("a-declared-vocabulary-refuses-a-typo",
           expect_key_refusal({"type": _sample, "id": "t",
                               "expect": {"definitelyNotAKey": 1}}) is not None)
    expect("a-declared-vocabulary-accepts-its-own-keys",
           expect_key_refusal({"type": _sample, "id": "t",
                               "expect": {sorted(_EXPECT_KEYS[_sample])[0]: 1}}) is None)
    # The near-miss hint is what turns a refusal into a fix. The case is derived from
    # whatever vocabularies are loaded — never from one named family — so the fixture
    # holds in a subset install too: find a (type, key) pair where dropping the key's last
    # character can only suggest that key back.
    def _near_miss_case():
        for _ctype in sorted(_EXPECT_KEYS):
            for _key in sorted(_EXPECT_KEYS[_ctype]):
                _miss = _key[:-1]
                if len(_miss) < 4 or any(_miss in ks for ks in _EXPECT_KEYS.values()):
                    continue
                if difflib.get_close_matches(_miss, sorted(_EXPECT_KEYS[_ctype]),
                                             n=1, cutoff=0.6) == [_key]:
                    return _ctype, _key, _miss
        return None

    _near = _near_miss_case()
    _hint = _near and expect_key_refusal({"type": _near[0], "id": "t",
                                          "expect": {_near[2]: 3}})
    expect("refusal-suggests-the-near-miss",
           _near is not None and _hint is not None and _near[1] in _hint)

    # The pure-query types declare exactly MATCHER_KEYS — the whole `expect` block goes to
    # the shared matcher, so a narrower or wider set would mean the offline refusal and the
    # run-time one disagree. Intersected with the loaded registry so the assertion is
    # estate-relative: it holds whether two families are installed or twelve.
    from test_common import MATCHER_KEYS as _MK
    _PURE_QUERY = {"delta-sql", "duckdb-parquet", "gql", "graphql-query", "kql-query",
                   "spark-sql"} & set(_RUNNERS)
    expect("pure-query-types-declare-the-matcher-vocabulary",
           all(_EXPECT_KEYS[t] == _MK for t in _PURE_QUERY))


    # --checks selection: spec order is preserved regardless of argument order, because a
    # check may populate ctx for a later one (notebook-item caches the id job-run reads).
    _checks = [{"id": "a", "type": "lakehouse-item"}, {"id": "b", "type": "notebook-item"},
               {"type": "job-run"}]
    _sel, _unk = _select_checks(_checks, ["b", "a"])
    expect("select-checks-keeps-spec-order", [c.get("id") for c in _sel] == ["a", "b"] and not _unk)
    _sel, _unk = _select_checks(_checks, ["job-run"])
    expect("select-checks-falls-back-to-type-as-id", len(_sel) == 1 and not _unk)
    _sel, _unk = _select_checks(_checks, ["nope"])
    expect("select-checks-reports-unknown-id", _unk == ["nope"])
    expect("select-checks-empty-means-all", _select_checks(_checks, [])[0] == _checks)

    # ── the check-level key vocabulary ──
    # Every type must declare one: an entry that omits it is a LOUD startup error, never an
    # "undeclared means don't check" fallback — that fallback IS the bug.
    class _Stub:
        __name__ = "test_stub"
        RUNNERS = {"stub-check": (lambda c, x: (True, "ok"), (), "a stub")}
    try:
        _merge_registry((_Stub,))
        expect("registry-refuses-3-tuple-entry", False)
    except RuntimeError as _exc:
        expect("registry-refuses-3-tuple-entry", "not 4" in str(_exc))

    class _StubStr:
        __name__ = "test_stub"
        RUNNERS = {"stub-check": (lambda c, x: (True, "ok"), (), "a stub", "viewports")}
    try:
        _merge_registry((_StubStr,))
        expect("registry-refuses-string-vocabulary", False)
    except RuntimeError as _exc:
        expect("registry-refuses-string-vocabulary", "tuple of strings" in str(_exc))

    class _StubDup:
        __name__ = "test_stub"
        RUNNERS = {"gql": (lambda c, x: (True, "ok"), (), "a stub", ())}
        EXPECT_KEYS = {"gql": frozenset()}
    try:
        # Any loaded family collides with the stub's 'gql'; use whichever this bundle has
        # rather than naming one, so the fixture survives a subset bundle.
        _merge_registry((_StubDup, _StubDup))
        expect("registry-refuses-duplicate-type", False)
    except RuntimeError as _exc:
        expect("registry-refuses-duplicate-type", "duplicate check type" in str(_exc))

    # The `expect` vocabulary is as mandatory as the check-level one, and for the same
    # reason: a type that declares neither lets an unread key pass in silence. A module
    # whose RUNNERS entry is otherwise perfect but carries no EXPECT_KEYS is refused at
    # IMPORT, which is why nothing downstream has to scan for the gap.
    class _StubNoExpect:
        __name__ = "test_stub"
        RUNNERS = {"stub-check": (lambda c, x: (True, "ok"), (), "a stub", ())}
    try:
        _merge_registry((_StubNoExpect,))
        expect("registry-refuses-missing-expect-vocabulary", False)
    except RuntimeError as _exc:
        expect("registry-refuses-missing-expect-vocabulary",
               "declares no EXPECT_KEYS entry" in str(_exc))

    class _StubOk:
        __name__ = "test_stub"
        RUNNERS = {"stub-check": (lambda c, x: (True, "ok"), ("sql",), "a stub", ("schema",))}
        EXPECT_KEYS = {"stub-check": frozenset({"scalar"})}
    _t, _k, _e = _merge_registry((_StubOk,))
    expect("registry-merges-a-well-formed-module",
           set(_t) == {"stub-check"} and _e["stub-check"] == frozenset({"scalar"})
           and _k["stub-check"] == _UNIVERSAL_CHECK_KEYS | {"sql", "schema"})

    expect("every-type-declares-a-vocabulary",
           set(_CHECK_KEYS) == set(_CHECK_TYPES)
           and all(_UNIVERSAL_CHECK_KEYS <= _CHECK_KEYS[t] for t in _CHECK_TYPES))
    # a required field is part of the vocabulary automatically — never restated
    expect("required-fields-are-in-the-vocabulary",
           all(set(entry[1]) <= _CHECK_KEYS[name] for name, entry in _CHECK_TYPES.items()))
    # the universal keys pass on every type, with nothing else on the check
    expect("universal-keys-accepted-everywhere",
           all(check_key_refusal({"type": t, "id": "x", "question": "q?", "expect": {}}) is None
               for t in _CHECK_TYPES))
    # an option a sibling runner honours is refused, and the refusal
    # names the siblings that do honour it rather than leaving the author guessing
    _r = check_key_refusal({"type": "http-probe", "id": "x", "viewports": [{"width": 375}]})
    expect("refuses-sibling-only-key",
           _r and "REFUSED" in _r and "'viewports'" in _r and "browser-render" in _r
           and "axe-scan" in _r, needs="test_webapp")
    # a near-miss spelling is named with the key it was probably meant to be
    _r = check_key_refusal({"type": "browser-render", "id": "x", "viewport": [{"width": 375}]})
    expect("refuses-typo-with-suggestion", _r and "did you mean 'viewports'" in _r,
           needs="test_webapp")
    _r = check_key_refusal({"type": "spark-sql", "id": "x", "sql": "SELECT 1", "shema": "dbo"})
    expect("refuses-typo-on-required-family", _r and "did you mean 'schema'" in _r)
    # `_`-prefixed keys are author annotations (_evidence/_note/_spoiler)
    expect("annotation-keys-tolerated",
           check_key_refusal({"type": "gql", "id": "x", "gql": "MATCH", "_evidence": "e",
                              "_note": "n"}) is None)
    # an unknown TYPE has no vocabulary to judge; the unknown-type verdict is the louder one
    expect("unknown-type-defers-to-type-verdict",
           unknown_check_keys({"type": "no-such-check", "viewports": []}) == [])
    # and the refusal reaches a real run: run_suite reports it as a FAILED check, by id.
    # (Its own output is captured — a self-test must not emit a second `== N passed ... ==`
    # tally line, which is what downstream parsers read a real run's verdict out of.)
    import contextlib as _ctx
    import io as _io
    with _ctx.redirect_stdout(_io.StringIO()):
        _vd = run_suite({"task": "t", "app": ".",
                         "checks": [{"id": "c1", "type": "app-source", "path": "x",
                                     "viewports": [{"width": 375}],
                                     "expect": {"contains": "y"}}]})
    expect("run-suite-refuses-unknown-check-key",
           _vd["failed"] == 1 and "REFUSED" in _vd["checks"][0]["reason"]
           and "'viewports'" in _vd["checks"][0]["reason"], needs="test_webapp")

    # ── the environment gate: {env:NAME} substitution + PARKED-not-failed ──
    # The mechanism that makes a delegated (real-user) assertion expressible at all. Every
    # leg below is pure, so the whole thing is fixture-tested with no credential in sight —
    # which is the situation it exists to handle.
    _gated = {"id": "delegated", "type": "http-probe", "method": "POST", "path": "/api/query",
              "requestHeaders": {"Authorization": "Bearer {env:AG_SELFTEST_TOKEN}"},
              "requiresEnv": {"vars": ["AG_SELFTEST_TOKEN"], "remedy": "acquire one"},
              "expect": {"status": 200},
              "_note": "documents the {env:AG_SELFTEST_UNDECLARED} syntax in prose"}
    expect("env-refs-found-in-nested-fields",
           env_references(_gated) == ["AG_SELFTEST_TOKEN"])
    # an annotation key documenting the syntax is NOT a reference (else every _note that
    # explains the mechanism would demand a gate for a variable nothing asserts on)
    expect("env-refs-ignore-annotations",
           "AG_SELFTEST_UNDECLARED" not in env_references(_gated))
    expect("env-gate-parks-when-absent",
           env_gate(_gated, env={})[0] == "park"
           and "AG_SELFTEST_TOKEN" in env_gate(_gated, env={})[1]
           and "acquire one" in env_gate(_gated, env={})[1])
    expect("env-gate-parks-on-empty-value",
           env_gate(_gated, env={"AG_SELFTEST_TOKEN": "   "})[0] == "park")
    expect("env-gate-runs-when-present",
           env_gate(_gated, env={"AG_SELFTEST_TOKEN": "ey.J"})[0] == "run")
    expect("env-substitution-rewrites-the-field",
           substitute_env(_gated, {"AG_SELFTEST_TOKEN": "ey.J"})
           ["requestHeaders"]["Authorization"] == "Bearer ey.J"
           # ... and leaves the annotation alone, so the docs are not rewritten
           and "{env:AG_SELFTEST_UNDECLARED}" in substitute_env(
               _gated, {"AG_SELFTEST_TOKEN": "ey.J"})["_note"])
    expect("env-substitution-does-not-mutate-the-spec",
           _gated["requestHeaders"]["Authorization"] == "Bearer {env:AG_SELFTEST_TOKEN}")
    # THE INVARIANT: an ungated reference is REFUSED, not silently posted as the literal
    # placeholder — a server refusing that garbage returns the very 401 a spec might expect
    _ungated = dict(_gated)
    _ungated.pop("requiresEnv")
    _r = env_gate(_ungated, env={})
    expect("env-refuses-ungated-reference",
           _r[0] == "refuse" and "AG_SELFTEST_TOKEN" in _r[1] and "REFUSED" in _r[1])
    # a declared-but-unreferenced variable is ALLOWED (a runner may read the env itself)
    expect("env-allows-declared-unreferenced",
           env_gate({"type": "container-app",
                     "requiresEnv": {"vars": ["AG_SELFTEST_SUB"], "remedy": "set it"}},
                    env={"AG_SELFTEST_SUB": "x"})[0] == "run")
    # malformed gates are REFUSED, and a remedy is not optional
    for _bad, _why in (
            ({"vars": ["A"]}, "no remedy"),
            ({"vars": [], "remedy": "r"}, "empty vars"),
            ({"vars": "A", "remedy": "r"}, "vars not a list"),
            ({"vars": ["A"], "remedy": "  "}, "blank remedy"),
            ({"vars": ["A"], "remedy": "r", "when": "later"}, "unknown gate key"),
            (["A"], "gate not an object")):
        expect(f"env-gate-refuses-{_why.replace(' ', '-')}",
               env_gate({"type": "http-probe", "requiresEnv": _bad}, env={"A": "1"})[0]
               == "refuse")
    # `requiresEnv` is universal: every check type accepts it, no module restates it
    expect("requires-env-is-universal",
           all(check_key_refusal({"type": t, "requiresEnv": {"vars": ["A"], "remedy": "r"}})
               is None for t in _CHECK_TYPES))
    # End to end through run_suite, over a throwaway source tree (so the assertions are
    # cheap and the fixture owns everything it reads): the park is a SKIP, is NOT counted as
    # a pass, is listed in the verdict's `parked`, and the banner says so — while an ungated
    # sibling check in the same suite still runs and passes.
    import tempfile as _tf
    _park_check = {"id": "delegated", "type": "app-source", "path": "one.txt",
                   "requiresEnv": {"vars": ["AG_SELFTEST_TOKEN"], "remedy": "acquire one"},
                   "expect": {"contains": ["{env:AG_SELFTEST_TOKEN}"]}}
    with _tf.TemporaryDirectory() as _td:
        (Path(_td) / "one.txt").write_text("a fixture line\n", encoding="utf-8")
        _suite = {"task": "t", "app": _td,
                  "checks": [{"id": "ungated", "type": "app-source", "path": "one.txt",
                              "expect": {"contains": ["a fixture line"]}}, _park_check]}
        with _ctx.redirect_stdout(_io.StringIO()) as _out:
            _vd = run_suite(_suite)
        _text = _out.getvalue()
        expect("run-suite-parks-a-gated-check",
               _vd["skipped"] == 1 and _vd["parked"] == ["delegated"]
               and _vd["passed"] == 1 and _vd["failed"] == 0
               and any(c["ok"] is None and "PARKED" in c["reason"] for c in _vd["checks"]),
               needs="test_webapp")
        expect("run-suite-shouts-about-parked-checks",
               "PARKED (1 check(s), no credential): delegated" in _text
               and "A SKIP IS NOT A PASS" in _text, needs="test_webapp")
        # --no-park is the inverse: the run that CLAIMS the gated leg cannot pass without it
        with _ctx.redirect_stdout(_io.StringIO()):
            _vd = run_suite({"task": "t", "app": _td, "checks": [_park_check]}, no_park=True)
        expect("run-suite-no-park-fails-instead",
               _vd["failed"] == 1 and _vd["skipped"] == 0 and _vd["parked"] == []
               and "--no-park" in _vd["checks"][0]["reason"], needs="test_webapp")
        # and the authoring gate catches an UNGATED reference offline, before any run
        _spec_path = Path(_td) / "validate.json"
        _bad_check = {k: v for k, v in _park_check.items() if k != "requiresEnv"}
        _spec_path.write_text(json.dumps({"task": "t", "app": _td, "checks": [_bad_check]}),
                              encoding="utf-8")
        with _ctx.redirect_stdout(_io.StringIO()) as _out:
            _rc = _dry_run(str(_spec_path))
        expect("dry-run-refuses-ungated-reference",
               _rc == 2 and "REFUSED" in _out.getvalue()
               and "AG_SELFTEST_TOKEN" in _out.getvalue(), needs="test_webapp")

    # workspace binding: GUID detection and the resolution order's pure half
    expect("workspace-guid-detected", bool(_GUID_RE.match("12345678-abcd-4bcd-9def-0123456789ab")))
    expect("workspace-name-is-not-guid", not _GUID_RE.match("Contoso BI [Prod]"))
    expect("workspace-arg-beats-spec", _pick_workspace("A", {"workspace": "B"})[0] == "A")
    expect("workspace-spec-beats-env", _pick_workspace(None, {"workspace": "B"})[0] == "B")
    import os as _os
    _prev = _os.environ.get("FABRIC_WORKSPACE_ID")
    _os.environ["FABRIC_WORKSPACE_ID"] = "env-guid"
    try:
        expect("workspace-env-fallback", _pick_workspace(None, {}) == ("env-guid", "FABRIC_WORKSPACE_ID env"))
    finally:
        if _prev is None:
            _os.environ.pop("FABRIC_WORKSPACE_ID", None)
        else:
            _os.environ["FABRIC_WORKSPACE_ID"] = _prev
    # caller-owned top-level keys are noticed, known keys are quiet
    expect("caller-keys-ignored-with-notice",
           _unknown_top_keys({"task": "t", "checks": [], "capabilities": ["X-C01"],
                              "prerequisites": []}) == ["capabilities", "prerequisites"])
    expect("known-keys-are-quiet",
           _unknown_top_keys({"task": "t", "workspace": "w", "lakehouse": "lh",
                              "gitRepo": "r", "checks": []}) == [])
    # the --json verdict: counts derived from tri-state ok, shape locked for CI gates
    _res = [{"id": "a", "type": "gql", "ok": True, "reason": "r", "seconds": 0.1},
            {"id": "b", "type": "spark-sql", "ok": False, "reason": "r", "seconds": 0.2},
            {"id": "c", "type": "preflight", "ok": None, "reason": "r", "seconds": 0.0}]
    _vd = _verdict({"id": "w", "displayName": "W"}, _res, ["c"])
    expect("verdict-counts", _vd["passed"] == 1 and _vd["failed"] == 1 and _vd["skipped"] == 1)
    expect("verdict-shape",
           set(_vd) == {"workspace", "passed", "failed", "skipped", "parked", "checks"}
           and _vd["checks"] is _res and bool(json.dumps(_vd)))
    # `parked` is a SUBSET of skipped, never an addition to it: a parked check is one of the
    # skips, so counting it twice would overstate what a run did.
    expect("verdict-parked-is-a-subset-of-skipped",
           _vd["parked"] == ["c"] and _vd["skipped"] == 1
           and all(any(r["id"] == p and r["ok"] is None for r in _res) for p in _vd["parked"]))

    # Every reference a family makes into another toolkit module must resolve TODAY —
    # not just `fr.<attr>`. The families reach their transport lazily (inside runners), so
    # a name a refactored module no longer exports is invisible offline and dies live as
    # an ImportError/AttributeError mid-check (proven AG-OMR-001, 2026-08-27: three
    # families still reached the requests-era `fr.` re-exports after the SDK rebase
    # removed them; the class recurred live 2026-08-29 on the first real build to run
    # these check types — and the FIX pattern, a lazy `from fabric_http import ...`,
    # is itself invisible offline, hence the widened net: every module-alias attribute
    # AND every lazy from-import against any toolkit module, verified per family).
    import ast as _ast
    _toolkit = {p.stem for d in ("core", "clients", "builders", "validation")
                for p in (Path(__file__).resolve().parents[1] / d).glob("*.py")}

    def _stale_toolkit_refs(mod):
        tree = _ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
        aliases, rebound, stale = {}, set(), []
        for node in _ast.walk(tree):
            if isinstance(node, _ast.Import):
                for a in node.names:
                    if a.name in _toolkit:
                        aliases[a.asname or a.name] = a.name
            elif isinstance(node, _ast.Name) and isinstance(node.ctx, _ast.Store):
                rebound.add(node.id)
        for node in _ast.walk(tree):
            if isinstance(node, _ast.ImportFrom) and not node.level and node.module in _toolkit:
                target = importlib.import_module(node.module)
                stale += [f"from {node.module} import {a.name}" for a in node.names
                          if a.name != "*" and not hasattr(target, a.name)]
            elif (isinstance(node, _ast.Attribute) and isinstance(node.value, _ast.Name)
                  and node.value.id in aliases and node.value.id not in rebound
                  and not hasattr(importlib.import_module(aliases[node.value.id]), node.attr)):
                stale.append(f"{node.value.id}.{node.attr}")
        return sorted(set(stale))

    for _mod in (test_common,) + _MODULES:
        _stale = _stale_toolkit_refs(_mod)
        expect(f"toolkit-surface-references-resolve[{_mod.__name__}]", not _stale
               or bool(print(f"    stale toolkit references in {_mod.__name__}: {_stale}")))

    return checks


def collect_self_test_checks():
    """Every offline fixture across the module family, as [(name, ok)] — the programmatic
    face of `--self-test`, so a composing caller can run the aggregate and report its
    count without shelling out."""
    checks = []
    for mod in (test_common,) + _MODULES:
        for fixture in mod.SELF_TESTS:
            checks.extend(fixture())
    checks.extend(_core_self_test_checks())
    return checks


def _self_test():
    # `ok is None` is a fixture this bundle cannot run — its subject is a check family that
    # did not ship. It is neither a pass nor a failure, and conflating it with either is how
    # a subset bundle ends up shipping a red self-test (a fixture asserting on an absent
    # family) or a quietly smaller green one (a skip counted as a pass).
    checks = collect_self_test_checks()
    failures = [n for n, ok in checks if ok is not None and not ok]
    skipped = [n for n, ok in checks if ok is None]
    for name, ok in checks:
        print(f"  {'SKIP' if ok is None else 'PASS' if ok else 'FAIL'}  {name}")
    if failures:
        print(f"SELF-TEST FAILED: {failures}")
        return 1
    print(f"SELF-TEST PASSED ({len(checks) - len(skipped)} checks"
          + (f", {len(skipped)} skipped — "
             f"{', '.join(ABSENT_FAMILIES)} not in this bundle)" if skipped else ")"))
    return 0


# ── offline structural gate + vocabulary listing + CLI ────────────────────────

def _dry_run(spec_path):
    """Offline structural check of a spec — no tenant, no network.

    An authoring-time gate: the spec parses, every check names a known type and carries
    that type's required fields, and every check states an expectation. It says nothing
    about whether the expected VALUES are right — only a real run against a real
    environment proves those. Problems exit 2 (a spec error), a clean structure exits 0.
    """
    spec = _load_spec(spec_path)
    print(f"== dry run {spec.get('task', '?')} ({spec_path}) ==")
    ignored = _unknown_top_keys(spec)
    if ignored:
        print(f"== ignoring unknown top-level key(s): {', '.join(ignored)} ==")
    problems = []
    # One assignment over every target kind — keep it one expression: a second
    # `target = ...` line silently shadows the first and drops a target kind.
    target = next((spec[k] for k in TARGET_KEYS if spec.get(k)), None)
    if not target:
        problems.append("spec names no target — one of "
                        + ", ".join(repr(k) for k in TARGET_KEYS) + " at the top level")
    checks = spec.get("checks", [])
    if not checks:
        problems.append("spec has no checks")
    required = {name: entry[1] for name, entry in _CHECK_TYPES.items()}
    for i, check in enumerate(checks):
        cid = check.get("id", f"#{i}")
        ctype = check.get("type")
        if ctype not in required:
            problems.append(f"{cid}: unknown check type {ctype!r} "
                            f"(known: {sorted(required)})" + absent_family_hint())
            continue
        for field in required[ctype]:
            if not check.get(field):
                problems.append(f"{cid}: {ctype} check needs a '{field}'")
        # The same refusal a live run makes, made offline at authoring time — this is where
        # a spec asking a runner for something it never reads should be caught.
        refusal = check_key_refusal(check)
        if refusal:
            problems.append(f"{cid}: {refusal}")
        # ...and the same refusal one level down, inside `expect`.
        expect_refusal = expect_key_refusal(check)
        if expect_refusal:
            problems.append(f"{cid}: {expect_refusal}")
        # The environment gate's STRUCTURAL half, evaluated against an empty environment so
        # the answer does not depend on what this machine happens to hold: a malformed
        # `requiresEnv` and an ungated `{env:NAME}` reference are authoring errors and show up
        # here; a merely-absent variable is a park, which is not a problem.
        gate, gate_reason = env_gate(check, env={})
        if gate == "refuse":
            problems.append(f"{cid}: {gate_reason}")
        if not check.get("expect"):
            problems.append(f"{cid}: no 'expect' — a check that asserts nothing always passes")
        gated = " [gated: " + ",".join((check.get("requiresEnv") or {}).get("vars", [])) + "]" \
            if isinstance(check.get("requiresEnv"), dict) else ""
        print(f"  {ctype:<13} {cid}:{gated} {check.get('question', '')[:80]}")
    for problem in problems:
        print(f"  PROBLEM  {problem}")
    print(f"== {len(checks)} checks, {len(problems)} problems "
          f"(structure only — expected values are not verified) ==")
    return 2 if problems else 0


def _list_checks():
    """Print the check-type vocabulary — offline, no tenant, no environment needed."""
    print("Check types (name · [required fields] · what it asserts):")
    for name, (_fn, req, doc, _keys) in _CHECK_TYPES.items():
        req_s = ", ".join(req) if req else "-"
        print(f"  {name:<22} [{req_s:<8}] {doc}")
    print()
    print("Check-level keys per type (anything else on the check dict is REFUSED; keys")
    print("beginning with '_' are author annotations the engine ignores):")
    for name in _CHECK_TYPES:
        extra = sorted(_CHECK_KEYS[name] - _UNIVERSAL_CHECK_KEYS)
        print(f"  {name:<22} {', '.join(extra) if extra else '-'}")
    print(f"  (every type also accepts: {', '.join(sorted(_UNIVERSAL_CHECK_KEYS))})")
    print()
    print("The environment gate (universal, any check type):")
    print('  "requiresEnv": {"vars": ["NAME", ...], "remedy": "how to supply it"}')
    print("  A check whose declared variables are all set RUNS, with every {env:NAME} in its")
    print("  fields substituted first. A check missing one is PARKED — a SKIP naming the")
    print("  variable and the remedy, never a pass and never a failure. Every {env:NAME}")
    print("  reference must be declared in its own check's requiresEnv.vars (an undeclared one")
    print("  is REFUSED: the literal placeholder would be asserted with as if it were real).")
    print("  --no-park turns a park into a FAILURE, for the run meant to prove the gated leg.")
    print()
    print("Result-returning checks (gql / delta-sql / spark-sql) share the expect matchers:")
    print("  exact:     scalar · count · rows (+ ordered)")
    print("  invariant: minCount · maxCount · minScalar · maxScalar · unique · columns · nonNull")
    print("Every matcher present is evaluated and ANDed; an empty expect block FAILS; `columns`")
    print("against zero rows FAILS (a well-formed empty result must not slip through).")
    print()
    print("The engine is read-only: spark-sql statements must begin with SELECT/WITH/DESCRIBE/SHOW")
    print("unless --allow-write is passed, and job-run asserts on run history — it never triggers runs.")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Run a declarative suite of typed checks against a Fabric workspace.")
    p.add_argument("spec", nargs="?", help="path to a check-spec JSON (see --list-checks)")
    p.add_argument("--workspace", help="target workspace GUID or display name "
                                       "(overrides the spec's \"workspace\" and FABRIC_WORKSPACE_ID)")
    p.add_argument("--graph-id", dest="graph_id", help="graph model GUID (skips name lookup)")
    p.add_argument("--mirror-id", dest="mirror_id", help="mirrored database GUID (skips name lookup)")
    p.add_argument("--lakehouse-id", dest="lakehouse_id", help="lakehouse GUID (required for preflight checks)")
    p.add_argument("--checks", help="comma-separated check ids to re-run after a fix "
                                    "(a PARTIAL run — never sufficient to call the suite green)")
    p.add_argument("--json", dest="json_out", metavar="PATH",
                   help="write the machine-readable verdict (workspace, counts, per-check "
                        "ok/reason/seconds) to this file")
    p.add_argument("--allow-write", dest="allow_write", action="store_true",
                   help="let spark-sql statements start with something other than "
                        "SELECT/WITH/DESCRIBE/SHOW (the engine is read-only without it)")
    p.add_argument("--no-park", dest="no_park", action="store_true",
                   help="FAIL a check whose 'requiresEnv' variables are absent instead of "
                        "parking it — the mode for a run that is meant to PROVE a gated leg")
    p.add_argument("--self-test", action="store_true", help="run offline fixture checks and exit")
    p.add_argument("--list-checks", dest="list_checks", action="store_true",
                   help="print the check-type vocabulary and exit (offline)")
    p.add_argument("--dry-run", action="store_true", dest="dry_run",
                   help="offline: check the spec parses and every check is well-formed (authoring gate)")
    p.add_argument("--lint-source", dest="lint_source", action="append", metavar="[NOTEBOOK=]PATH",
                   help="offline: run the spec's sourceContains/sourceLacks assertions against a "
                        "local notebook file BEFORE uploading it (repeatable; prefix NOTEBOOK= "
                        "when the spec asserts on more than one notebook)")
    args = p.parse_args(argv)

    if args.self_test:
        return _self_test()
    if args.list_checks:
        return _list_checks()
    if not args.spec:
        p.error("a check-spec path is required (or use --self-test / --list-checks)")
    if args.lint_source:
        if not family("test_notebook"):
            p.error("--lint-source needs the notebook check family, which this bundle does "
                    "not carry" + absent_family_hint())
        return family("test_notebook")._lint_source(args.spec, args.lint_source)
    if args.dry_run:
        return _dry_run(args.spec)
    return run(args.spec, args)


if __name__ == "__main__":
    sys.exit(main())
