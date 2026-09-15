---
type: capability
title: Prerequisites and fit
category: prerequisites-and-fit
topic: lakehouse
community: frontier-data-engineer
capabilities: [LH-C01, LH-C12, LH-C27]
updated: 2026-08-18
fabric_release: 2026-08
status: current
evidence: unverified
sources: []
provenance_notes: "**Seed page.** Every other wiki page cites a file in `2_raw/`; the six lakehouse cluster pages ship before your first rep, when `2_raw/` is empty by design. `evidence: unverified` is *your* position, not a claim about the content — treat it as a baseline to test. AG-LAK-001 and AG-LAK-002 are what convert it: when they land, `fabric-ingest` rewrites these pages from your own gym rep reports and the citations appear."
---

# Lakehouse + notebook — prerequisites and fit

Scope: driving a lakehouse **from code over REST** — create the lakehouse and notebook as *items*, land data, execute, verify. Not the portal. Schema-enabled lakehouses only; schemaless is legacy and out of scope.

## Prerequisites

- Workspace on an **active capacity**.
- **Sign-in: your own is enough.** The default `FABRIC_AUTH_MODE=auto` signs in as you (a browser on the first run, cached afterwards) unless a service-principal secret is set — so `FABRIC_WORKSPACE_ID` is the only value you must set. Signing in as yourself needs **no app registration and no Entra administrator** — which is the difference between starting today and raising a ticket.
  A **service principal** (`spn` mode: `AZURE_TENANT_ID` · `AZURE_CLIENT_ID` · `AZURE_CLIENT_SECRET`, workspace **Contributor**, plus the tenant setting *Service principals can access Fabric APIs*) is required only for what must run **unattended** — a user token is short-lived and cannot be renewed without a person. Everything else in the toolkit is identical either way: a check asserts on the state of a workspace, and who asked is a question for the token.
- Uploading source files into `Files/` additionally needs the **storage plane**: the `storage.azure.com/.default` scope, and the tenant setting *Users can access data stored in OneLake with apps external to Fabric*. The two planes fail independently — an API-plane call proves nothing about the storage plane.
- `workspaceId` up front; `lakehouseId` after create. Both appear in every ABFS path (`abfss://…` — OneLake's Spark-addressable storage URL).
- Toolkit: [`lakehouse_client.py`](../../code/clients/lakehouse_client.py) (item lifecycle, paths, endpoint readiness), [`notebook_client.py`](../../code/clients/notebook_client.py) (item definition + Jobs API), [`livy_client.py`](../../code/clients/livy_client.py) (Spark without a notebook), [`workspace_folders.py`](../../code/clients/workspace_folders.py) (files items into workspace folders).

## Prove the tenant before you build

An environmental prerequisite costs seconds to test and half an hour to discover. A task declares extra `prerequisites` in its `validate.json` only when it needs more than the baseline every Fabric task needs; the probe runs the baseline plus whatever the task added. Copy the repo-root `.env.example` to `.env` (also at the repo root) first — the probe and every client read it from there:

```
python .claude/skills/execute-gym-rep/preflight_probe.py --task AG-LAK-001
```

Read the three exit codes as three different answers: `0` clear · `1` a prerequisite is provably absent · **`2` inconclusive** — nothing failed, something could not be decided from here (most often because the workspace has no lakehouse yet, which is the normal state at the start of a rep that creates one). Treating a `2` as a failure parks a task that would have run.

## Fit — is a notebook the right tool?

A Spark notebook costs a session start (**~30–40 s** before any work) plus capacity. Data volume alone rarely justifies it: a load of eight rows can still be the right call when the requirement is reproducibility rather than throughput.

Choose a notebook when:

- **The artefact must be rebuildable.** A notebook is an item definition (`notebook-content.py`) that lives in git and redeploys across environments. This is usually the real reason.
- **There is a transformation** — explicit typing, schema qualification, quality gates — rather than a byte copy.
- **The source is a file drop** that needs reading, not a queryable system.

Choose something else when:

| Requirement | Better tool | Why |
|---|---|---|
| Scheduled copy, no transformation | Pipeline Copy activity | No Spark session cost |
| Source is a queryable database | Shortcut or mirrored database | Removes the copy entirely |
| Genuine one-off or an ad-hoc check | Livy statement ([coding-guidance](coding-guidance.md)) | Authoring an item is the expensive part |
| Sub-minute freshness | Open mirroring | Batch cannot meet it |

Reaching for Spark reflexively is the failure mode this test exists to catch — a build that later goes green does not retire the question.

## Establish the runtime; do not assume it (LH-C27)

The runtime ships a great deal, and **checking costs one Livy session** (`importlib.import_module` over the candidate names) against a needless install — or, if version-pinned, a downgrade the platform did not ask for.

`notebookutils.runtime.context` carries `defaultLakehouseId` · `defaultLakehouseName` · `defaultLakehouseWorkspaceId` · `defaultLakehouseWorkspaceName` when the job supplies a `defaultLakehouse`.

Two constraints follow, and both are fit questions rather than build details:

- **On an unattended run you cannot install anything.** `%pip` is rejected outright on a Jobs-API submission — even `%pip list` fails. A package the runtime lacks must arrive through a published **Environment item** attached at run submission, which changes what "create the notebook and run it" costs. See capability LH-C12 in [`0_admin/capabilities/lakehouse.md`](../../0_admin/capabilities/lakehouse.md).
- **An absent library is a finding to report, not a problem to install your way out of.**

Establish versions from the running session rather than quoting any figure here, and record what the probe proved from a **kept notebook item that writes its answers to a table** — not a throwaway. A recorded environment finding is re-derivable next quarter against next quarter's runtime; a chat message is not.

## What the API plane cannot do

Two hard limits shape every design here; both push work into Spark.

- **No table enumeration.** `GET .../lakehouses/{id}/tables` returns **400 `UnsupportedOperationForSchemasEnabledLakehouse`** on a schema-enabled lakehouse — the only variant worth building on. Enumeration goes through Spark: `SHOW TABLES IN <schema>`.
- **No run result.** The job instance carries no exit value and only a coarse `failureReason` — see [operate-framework](operate-framework.md).

## Unprobed

Git integration, pipeline-triggered runs, Key Vault, cross-workspace deployment and Delta maintenance are all unexercised here. Treat guidance on them as absent, not implied — they are what the remaining lakehouse gym tasks are for.
