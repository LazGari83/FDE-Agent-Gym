---
type: capability
title: Gotchas
category: gotchas
topic: lakehouse
community: frontier-data-engineer
capabilities: [LH-C02, LH-C07, LH-C08, LH-C14, LH-C27]
updated: 2026-08-20
fabric_release: 2026-08
status: current
evidence: unverified
sources: []
provenance_notes: "**Seed page** — see [prerequisites-and-fit](prerequisites-and-fit.md) for why these six pages cite nothing, and what converts them. The trap register is the page a rep is most likely to *correct*: when one of these behaves differently in your tenant, that contradiction is the finding worth writing up."
---

# Lakehouse — gotchas

The trap register. Each entry is a thing that looks like it worked.

## Item creation

- **Display-name rules are per item type.** A lakehouse takes `^\w+$` only — hyphens and spaces give `400 InvalidInput` ("DisplayName is Invalid for ArtifactType") — while a notebook beside it accepts hyphens. The same name is legal for one item and rejected for the other. An environment follows the lakehouse rule (`^\w+$`).
- **A rejected environment create still reserves the name.** A hyphen is rejected at create — and a retry under the same name then answers `409 ItemDisplayNameNotAvailableYet` on the very name the 400 just refused. Validate the name before sending, not after the 400.
- **Schema-enabled is not the API default, and cannot be changed later.** A bare create silently yields the legacy schemaless variant. The flag you sent is never echoed back, so verify `properties.defaultSchema` on a GET rather than trusting your own request.
- **The create response carries no `properties`.** Paths and the SQL endpoint block only exist after a follow-up GET.

## Notebook definitions

- **Without the prologue the create fails outright** — the LRO reaches `Failed` with `PyToIPynbFailure … prologue is invalid. Expected prologue: # Fabric notebook source`, and no item is created. It does not half-work.
- **The prologue is the *only* thing validated.** A payload starting `# Fabric notebook source` with no `# CELL ***` blocks creates **successfully and empty**. Silent content loss — assert with `get_source()`, never on the create's status.
- **The 202 LRO `Location` is on a region-stamped host** (observed: `wabi-…-redirect.analysis.windows.net`). Follow the header; never reconstruct the URL.
- **Round-trip is line-survival, not byte-equality.** Observed: Fabric strips metadata blocks — all code intact, and the normalised form is a fixed point. Diff against the read-back, not against what you sent.
- **A default lakehouse cannot be attached in the item definition over REST.** Observed: patched into the definition it is discarded, on create and on update, both returning `Succeeded` and both absent from every read-back. Attachment happens at run submission only.

## Writing tables

- **Unqualified writes land in `dbo`** and pass a naive existence check. Where the contract names a schema, assert absence from `dbo` too.
- **An omitted workspace segment resolves to the LOCAL lakehouse, silently.** A *wrong* name errors; an *omitted* one returns the local workspace's data with a plausible row count.
- **GUIDs are rejected in four-part names** (`DoesNotExistException: Artifact not found`) — display names only, the inverse of every other Fabric surface.
- **The `https://onelake.dfs…` form the item API returns is not Spark-writable.**
- **`saveAsTable` addresses the catalog**, so it resolves against the attached default lakehouse — not the path you meant.

## Running

- **`Completed` is a process exit, not a data assertion.** A notebook that swallows its exception completes green.
- **Job instances come back unordered.** An older green run will mask a newer failure unless you sort.
- **On-demand runs are not deduplicated.** Concurrent identical submissions run for real and collide over the same Delta path — one dies with `System_Cancelled_Session_Statements_Failed`.
- **A transport error from the poller is not a run failure.** The run may have succeeded; check `list_job_instances` before retrying anything with a side effect.
- **`failureReason` names no cell, no exception and no stack**, and there is no exit value at all.

## SQL endpoint

- **T-SQL reads stale after a Spark write until the metadata sync runs** — `Invalid object name` for a new table, pre-write values for an existing one, while Spark reads the new data fine. Canonical detail: [operate-framework](operate-framework.md) ("SQL endpoint metadata sync").
- **`status: "NotRun"` from the refresh means already current, not failed.** Treating it as an error is an infinite retry.

## Shortcuts

- **`path` comes back with a leading slash from LIST and without one from CREATE/GET.** Same shortcut, same session: list → `"/Files"`, create and get → `"Files"`. Undocumented, and self-consistent within any one call — every comparison must `.strip("/")` both sides or it silently matches nothing.
- **The default conflict policy makes a re-run fail.** A second identical create under the default `Abort` is `409 EntityConflict`; `moreDetails[0].errorCode` is `ShorcutsOperationNotAllowed` — Microsoft's own typo, one `t` short of "Shortcuts"; match it as spelled. Pass `CreateOrOverwrite` for re-runnable builds ([coding-guidance](coding-guidance.md)).
- **The response carries both target spellings at once**: `"target": {"type": "OneLake", "oneLake": {…}}` — capitalized discriminator, camelCase sub-object key. Compare case-insensitively; both are live in one body.

## Spark session

- **Livy's default `kind` is Scala.** Python submitted there fails with `<console>:NN: error: not found: value …`, naming neither Livy, Python, nor the kind. `<console>:NN` in any Fabric Spark error means a Scala interpreter answered.
- **`%pip` is rejected outright on a Jobs-API submission** — even `%pip list` fails, and `_inlineInstallationEnabled: true` does not rescue it. `!pip` reaches the driver alone, so executors never see the library.
