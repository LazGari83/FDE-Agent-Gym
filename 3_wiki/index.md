# Fabric — Knowledge Base Index

The curated router for this wiki: one row per page, grouped by topic. Read this first, then
pull only the page the current step needs.

## Instructions — read before adding a row

**This index is mostly empty, and that is the correct starting state.** The topic folders below
exist but hold no pages yet. They fill up as you run gym tasks in _your_ Fabric environment and
ingest what those runs prove. `lakehouse` is filled in already, as a worked example of the shape
a finished section takes.

- **Row format:** [`0_admin/references/index-template.md`](../0_admin/references/index-template.md).
- **Who writes it:** **fabric-ingest** adds a row for each page it writes. **fabric-lint**
  reconciles the rows against the files on disk and reports any row pointing at a page that does
  not exist.
- **Add a topic section when you write its first page**, not before. An index that promises
  pages nobody has written is worse than a short one.
- **Keep every row annotated.** The Summary cell is what lets an agent choose a page _without
  opening it_ — an unannotated link dump is the most common failure mode of an agent-facing
  index. Lead a decision row with **Recommendation: …**.
- **Never write a claim here that is not on the page it links to.** This file is a router, not a
  second copy of the knowledge.

Every page carries `community: frontier-data-engineer | frontier-decision-engineer | both`.
The topic usually implies it (see `CLAUDE.md`); filter to one community or read across both.
Topic sections show an `Evidence` column instead of `Community` (the topic implies it); the
community itself is in each page's frontmatter.

## decisions

Experiment bake-offs — X versus Y, built both ways, resolved with evidence from your own runs.
The highest-signal pages in the wiki.

_None yet. Write one when a run settles a question you had a real choice about._

| Page | Type | Community | Summary | Updated | Release |
| ---- | ---- | ---- | ------- | ------- | ------- |

## lakehouse

Lakehouse development driven **from code over REST** — create items, land data in
OneLake, execute over the Jobs API (Fabric's run-an-item-on-demand REST endpoint), and verify Delta output. Schema-enabled lakehouses only.
Toolkit: [`code/clients/lakehouse_client.py`](../code/clients/lakehouse_client.py) +
[`code/clients/notebook_client.py`](../code/clients/notebook_client.py).
Spec: [`0_admin/capabilities/lakehouse.md`](../0_admin/capabilities/lakehouse.md).
Practice in [`1_agent-gym/lakehouse/`](../1_agent-gym/lakehouse/index.md).

**This is a seed cluster, and the shape is the lesson.** A topic's capability knowledge
decomposes into six pages — `prerequisites-and-fit` · `design-framework` · `build-framework` ·
`operate-framework` · `coding-guidance` · `gotchas` — the first four following the lifecycle an
agent walks (is this the right tool → decide the shape → build it → run it once it is live),
with `coding-guidance` the code for all of them and `gotchas` the trap register across all of
them. These six are scoped around what AG-LAK-001 and AG-LAK-002 need, they cite nothing because
`2_raw/` starts empty, and they hold `evidence: unverified` until your own reps convert them.
The `atoms/` folder is empty: your first passing rep mints the first atom.

| Page                                                              | Type       | Evidence   | Summary                                                                                                              | Updated    | Release |
| ----------------------------------------------------------------- | ---------- | ---------- | -------------------------------------------------------------------------------------------------------------------- | ---------- | ------- |
| [Prerequisites and fit](lakehouse/prerequisites-and-fit.md)       | capability | unverified | Two auth planes, the preflight probe that proves your tenant, the notebook-vs-alternative fit test, establishing the runtime, and what the API plane cannot do. | 2026-08-18 | 2026-08 |
| [Design framework](lakehouse/design-framework.md)                 | capability | unverified | The decisions taken before the first create call — several irreversible: schema-enabled, which schema, attached vs absolute addressing, execution surface. | 2026-08-18 | 2026-08 |
| [Build framework](lakehouse/build-framework.md)                   | capability | unverified | Ordered P1–P6: folder, schema-enabled lakehouse, SQL endpoint, notebook item, run, verify from an independent session.| 2026-08-20 | 2026-08 |
| [Operate framework](lakehouse/operate-framework.md)               | capability | unverified | Driving a run to a verified terminal state, the three degrees of acknowledgement, concurrency collisions, run ledgers, the per-write SQL endpoint metadata sync, shortcuts once live. | 2026-08-20 | 2026-08 |
| [Coding guidance](lakehouse/coding-guidance.md)                   | capability | unverified | Call shapes: `notebook_content_py()`, run submission (parameters/defaultLakehouse/environment), abfss paths, Livy `kind="pyspark"`, environment items, shortcuts, T-SQL over pyodbc. | 2026-08-20 | 2026-08 |
| [Gotchas](lakehouse/gotchas.md)                                   | capability | unverified | Trap register: per-type name rules (and name reservation), prologue-only validation, `dbo` landings, SQL-endpoint staleness, shortcut conflict/path traps, Livy Scala default. | 2026-08-20 | 2026-08 |

## azure-app

The app-serving surface: an API-for-GraphQL derived from the ontology, SPN-callable from application code.

Toolkit: [`code/clients/graphql_api_client.py`](../code/clients/graphql_api_client.py) + [`code/builders/graphql_api_generator.py`](../code/builders/graphql_api_generator.py). Spec: [`0_admin/capabilities/azure-app.md`](../0_admin/capabilities/azure-app.md). Practice in [`1_agent-gym/azure-app/`](../1_agent-gym/azure-app/index.md).

| Page | Type | Evidence | Summary | Updated | Release |
| ---- | ---- | ---- | ------- | ------- | ------- |
| [Build framework](azure-app/build-framework.md) | capability | unverified | The ontology→API-for-GraphQL derivation rules (`fieldMappings` required, `targetObject` in source format, cardinality from the contextualization's table), SQLEndpoint-id resolution, idempotent `sync_api`, querying `/graphql`. | 2026-08-20 | 2026-08 |
| [Gotchas](azure-app/gotchas.md) | capability | unverified | Trap register: `sourceItemId` wants the SQLEndpoint id never the lakehouse id, display-name charset → `400 InvalidInput`, `Query_by_pk` silent no-op, LRO-stub visibility, `create_api` takes no folderId. | 2026-08-20 | 2026-08 |

## capacity

Fabric capacities on the **ARM control plane** — lifecycle, suspend/resume, and the paid-tier discrimination paid-only items need.

Toolkit: [`code/clients/capacity.py`](../code/clients/capacity.py). Spec: [`0_admin/capabilities/capacity.md`](../0_admin/capabilities/capacity.md). No gym tasks yet — a declared gap.

| Page | Type | Evidence | Summary | Updated | Release |
| ---- | ---- | ---- | ------- | ------- | ------- |
| [Coding guidance](capacity/coding-guidance.md) | capability | unverified | `CapacityClient` call shapes — create/update/suspend/resume/wait, resource-group discovery, existence probing, and `is_paid_tier`. | 2026-08-20 | 2026-08 |
| [Gotchas](capacity/gotchas.md) | capability | unverified | Trap register: wrong-plane addressing, create-silently-becomes-resume (changed SKU/region/admins NOT applied), ~40 s settle, admin-gated Fabric-API visibility, paid-tier SKU discrimination. | 2026-08-20 | 2026-08 |

## cicd

Promotion three ways — git integration, deployment pipelines, `fabric-cicd` — plus connections, variable libraries, and workspace lifecycle.

Toolkit: [`code/clients/git_client.py`](../code/clients/git_client.py) · [`deployment_client.py`](../code/clients/deployment_client.py) · [`variable_library_client.py`](../code/clients/variable_library_client.py) · [`connection_client.py`](../code/clients/connection_client.py) · [`workspace_client.py`](../code/clients/workspace_client.py). Spec: [`0_admin/capabilities/cicd.md`](../0_admin/capabilities/cicd.md). Practice in [`1_agent-gym/cicd/`](../1_agent-gym/cicd/index.md).

| Page | Type | Evidence | Summary | Updated | Release |
| ---- | ---- | ---- | ------- | ------- | ------- |
| [Coding guidance](cicd/coding-guidance.md) | capability | unverified | Call shapes for the whole delivery toolkit — connection→connect→initialize sequence, selective commits, positional promotion, variable-library definitions, connection payloads, fabric-cicd publish flags. | 2026-08-20 | 2026-08 |
| [Operate framework](cicd/operate-framework.md) | capability | unverified | Running promotions once live — the three-mechanism comparison, per-item verification via `executionPlan.steps[]`, the deploy note as sole audit record, post-arrival value-set selection, the four-delete blast-radius table. | 2026-08-20 | 2026-08 |
| [Gotchas](cicd/gotchas.md) | capability | unverified | Trap register across all three promotion mechanisms — GitHub/ADO credential asymmetry, `mode: All` failing by succeeding, positional stages, deploys that succeed against nothing, silent fabric-cicd skips, non-unique connection names. | 2026-08-20 | 2026-08 |

## data-agent

The Fabric **data agent** item — definition round-trips, publishing, and the paid-capacity gate.

Toolkit: [`code/clients/data_agent_client.py`](../code/clients/data_agent_client.py). Spec: [`0_admin/capabilities/data-agent.md`](../0_admin/capabilities/data-agent.md). Practice in [`1_agent-gym/data-agent/`](../1_agent-gym/data-agent/index.md).

| Page | Type | Evidence | Summary | Updated | Release |
| ---- | ---- | ---- | ------- | ------- | ------- |
| [Coding guidance](data-agent/coding-guidance.md) | capability | unverified | Collection/type addressing, the `Files/Config` part-path map (both stages in one definition), whole-document `build_definition`/`update_definition`, and the synchronous `staging/publish` call. | 2026-08-20 | 2026-08 |
| [Gotchas](data-agent/gotchas.md) | capability | unverified | Trap register: trial-capacity refusal (paid F2+), updateDefinition-does-not-publish, the `staging/publish` 404 trap, runtime bound at publish, overwrite atomicity, delete ≠ decommission. | 2026-08-20 | 2026-08 |

## graph

Querying the graph an ontology projects — the Fabric IQ pair's second half (shares the [`ontology`](#ontology) spec).

Toolkit: [`code/clients/graph_client.py`](../code/clients/graph_client.py). Spec: [`0_admin/capabilities/ontology.md`](../0_admin/capabilities/ontology.md). Practice in [`1_agent-gym/ontology/`](../1_agent-gym/ontology/index.md).

| Page | Type | Evidence | Summary | Updated | Release |
| ---- | ---- | ---- | ------- | ------- | ------- |
| [Coding guidance](graph/coding-guidance.md) | capability | unverified | Resolving the auto-provisioned `<Ontology>_graph_<hash>` by prefix, `getQueryableGraphType` schema discovery, GQL `executeQuery`, and the 200-vs-202 refresh polling contract. | 2026-08-20 | 2026-08 |
| [Gotchas](graph/gotchas.md) | capability | unverified | Trap register: graph not named after the ontology, aliased edge endpoints, push-does-not-refresh, two refresh success shapes, beta query surfaces. | 2026-08-20 | 2026-08 |

## key-vault

Azure Key Vault on both planes — vault lifecycle over ARM, data-plane RBAC grants, secrets.

Toolkit: [`code/clients/key_vault.py`](../code/clients/key_vault.py). Spec: [`0_admin/capabilities/key-vault.md`](../0_admin/capabilities/key-vault.md). Practice in [`1_agent-gym/key-vault/`](../1_agent-gym/key-vault/index.md).

| Page | Type | Evidence | Summary | Updated | Release |
| ---- | ---- | ---- | ------- | ------- | ------- |
| [Coding guidance](key-vault/coding-guidance.md) | capability | unverified | Two planes/two token audiences, vault create payload, data-plane RBAC grants with role GUIDs, and secret set/get/list call shapes with their error semantics. | 2026-08-20 | 2026-08 |
| [Operate framework](key-vault/operate-framework.md) | capability | unverified | Secret lifecycle post-live: rotation as re-PUT with per-version attributes, the expiry sweep (absent `exp` is a finding), and delete→purge→poll decommission. | 2026-08-20 | 2026-08 |
| [Gotchas](key-vault/gotchas.md) | capability | unverified | Trap register: access-policy default at create, unenforced 201 grants (~12 s window), object-vs-app id, soft-delete name retention, subscription-scope purge, 403-vs-404-vs-DNS signals. | 2026-08-20 | 2026-08 |

## ontology

Fabric IQ ontology design and build — definition parts, bindings, contextualizations, atomic pushes (the pair's first half; [`graph`](#graph) queries what this projects).

Toolkit: [`code/clients/ontology_client.py`](../code/clients/ontology_client.py) + [`code/builders/definition_builder.py`](../code/builders/definition_builder.py) + [`lakehouse_sync.py`](../code/builders/lakehouse_sync.py). Spec: [`0_admin/capabilities/ontology.md`](../0_admin/capabilities/ontology.md). Practice in [`1_agent-gym/ontology/`](../1_agent-gym/ontology/index.md).

| Page | Type | Evidence | Summary | Updated | Release |
| ---- | ---- | ---- | ------- | ------- | ------- |
| [Design framework](ontology/design-framework.md) | capability | unverified | Definition part layout, 64-bit-string vs GUID id discipline, the six value types with their Spark mappings, and the binding/contextualization shapes fixed before the first push. | 2026-08-20 | 2026-08 |
| [Coding guidance](ontology/coding-guidance.md) | capability | unverified | Call shapes for `OntologyClient`, the config-driven build flow (`build_from_config` → tables → bindings → one atomic push), and `sync_all_entities` for evolving a live model. | 2026-08-20 | 2026-08 |
| [Gotchas](ontology/gotchas.md) | capability | unverified | Trap register: the unreadable `400 ALMOperationImportFailed` and its two causes, `BigInt`-not-`Integer`, whole-document overwrite, `dbo`/`ont_*` defaults, contextEntity-defaults-to-target. | 2026-08-20 | 2026-08 |

## openmirror

Open mirroring end to end: the landing-zone file protocol a producer writes, and the Mirrored Database item that replicates it into Delta.

Toolkit: [`code/clients/mirror_client.py`](../code/clients/mirror_client.py) + [`code/builders/landing_zone.py`](../code/builders/landing_zone.py). Spec: [`0_admin/capabilities/openmirror.md`](../0_admin/capabilities/openmirror.md). Practice in [`1_agent-gym/openmirror/`](../1_agent-gym/openmirror/index.md).

| Page | Type | Evidence | Summary | Updated | Release |
| ---- | ---- | ---- | ------- | ------- | ------- |
| [Coding guidance](openmirror/coding-guidance.md) | capability | unverified | GenericMirror definition and endpoints, the landing-zone protocol (folders create tables, `_metadata.json`, 20-digit sequential files, `__rowMarker__`), the declare·coerce·canonicalize parquet rule, and the delimited-text contract. | 2026-08-20 | 2026-08 |
| [Operate framework](openmirror/operate-framework.md) | capability | unverified | Post-live doctrine: stopped-table triage on the `error` object / `lastSyncDateTime` / landing-zone backlog, drop-and-recreate recovery, landing-zone transience (~7-day purge), and live-mirror change rules. | 2026-08-20 | 2026-08 |
| [Gotchas](openmirror/gotchas.md) | capability | unverified | The silent-failure register: no auto-start, `MirroringDefinitionMissing`, `Replicating` on a stopped table, cumulative `processedRows`, the snapshot boundary, write-once `keyColumns`, `SchemaMergeFailure` triggers. | 2026-08-20 | 2026-08 |

## pipelines

Data Pipelines over REST — definition round-trips, schedules, and Jobs-API runs.

Toolkit: [`code/clients/pipeline_client.py`](../code/clients/pipeline_client.py). Spec: [`0_admin/capabilities/pipelines.md`](../0_admin/capabilities/pipelines.md). Practice in [`1_agent-gym/pipelines/`](../1_agent-gym/pipelines/index.md).

| Page | Type | Evidence | Summary | Updated | Release |
| ---- | ---- | ---- | ------- | ------- | ------- |
| [Coding guidance](pipelines/coding-guidance.md) | capability | unverified | Definition parts payload, GET→merge→POST surgical patching, Job Scheduler schedules (`jobType=Pipeline`, Cron-only), Jobs-API run trigger/polling, and the `queryactivityruns` shape. | 2026-08-20 | 2026-08 |
| [Gotchas](pipelines/gotchas.md) | capability | unverified | The trap register: `DefaultJob` → `400 InvalidJobType`, schedule stacking, past `startDateTime` instant fire, full-replace `updateDefinition`, ambiguous `Failed`, vacuous empty activity-run windows. | 2026-08-20 | 2026-08 |

## your other topics

Folders are already in place for `capacity` · `cicd` · `ingestion` · `key-vault` · `openmirror` · `pipelines` · `testing`. Each gets a section here — copied from the `lakehouse` shape above — once it has a page. Delete this whole section when they all do.
