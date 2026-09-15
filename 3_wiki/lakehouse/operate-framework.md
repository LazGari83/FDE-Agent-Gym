---
type: capability
title: Operate framework
category: operate-framework
topic: lakehouse
community: frontier-data-engineer
capabilities: [LH-C14, LH-C15]
updated: 2026-08-20
fabric_release: 2026-08
status: current
evidence: unverified
sources: []
provenance_notes: "**Seed page** — see [prerequisites-and-fit](prerequisites-and-fit.md) for why these six pages cite nothing, and what converts them."
---

# Lakehouse — operate framework

What happens once the notebook is live and running.

## Drive the run to a verified terminal state (LH-C14)

```python
instance = NotebookClient(ws).run_and_wait(nb_id, default_lakehouse={...})   # poll to terminal
latest = NotebookClient(ws).list_job_instances(nb_id)[0]   # client sorts newest-first; the raw API does not
assert latest["status"] == "Completed"
```

**Acknowledgement degrades three times, and only the third is proof:**

1. **202 = accepted.** Empty body; the instance id is only in the `Location` header.
2. **`Completed` = process exit.** A notebook that swallows its own exception completes green.
3. **The data is the proof.** Re-read from an independent session.

Terminal states: `Completed` · `Failed` · `Cancelled` · `Deduped` (treat as a no-op if it appears; whether Fabric ever fires it — for scheduled or pipeline invocations — is unverified, and on-demand runs are never deduplicated, below). Only the first is green. Live states: `NotStarted` · `InProgress`.

## Assert the *latest* run

The API returns instances unordered. An older green run will otherwise mask a newer failure — sort before asserting, and assert the newest.

## Concurrency

**On-demand runs are not deduplicated.** Concurrent identical submissions produce real, separate Spark sessions, and they **collide** over the same Delta path — one submission may lose the race; record the exact error your tenant returns. Idempotence and concurrency control are the caller's job: poll to terminal before resubmitting.

Submitting a second run while the first is in flight is therefore a legitimate thing to *measure*, and what comes back is worth recording.

## The run tells you almost nothing

The job instance carries **no exit value**, and `failureReason` is coarse — no cell, no exception, no stack. Everything you will want during an incident has to have been landed as data while the run was healthy:

- a **run ledger** written by the driver (which holds the instance id the API returned) — a notebook cannot see its own instance id, so nothing inside it can fabricate the row;
- the notebook's own verdict, in an audit table or a `Files/_diag/` JSON blob via `notebookutils.fs.put`.

## A transport error is not a run failure

If the **poller** dies — a connection reset, a token refresh — the run it was watching may well have succeeded. `list_job_instances(nb_id)` is the authority: check it before retrying anything with a side effect. A naive retry submits a second real run and corrupts exactly the audit contract the ledger exists to protect.

## SQL endpoint metadata sync — after *every* Spark write (LH-C15)

Two different endpoint waits, and confusing them is the trap:

| | `wait_for_sql_endpoint()` | `refresh_sql_endpoint_metadata()` |
|---|---|---|
| When | once, at item-create time | after **every** Spark write a T-SQL consumer reads |
| Waits for | the endpoint to be *provisioned* | the endpoint's metadata to catch up with the write |

Until the refresh runs, T-SQL answers `Invalid object name` for a new table and **pre-write values** for an existing one — while Spark reads the new data fine, so no Spark-side check catches the staleness.

- **Call it on the child SQLEndpoint item**: the id is `properties.sqlEndpointProperties.id`, **not** the lakehouse id.
- Observed **200 with the result inline** (~1.6 s), not the documented 202 LRO; the release endpoint (no `?preview=true`) wraps the array as `{"value": [...]}`.
- Returns a per-table array — `tableName` · `status` · `startDateTime` / `endDateTime` · `lastSuccessfulSyncDateTime`.
- **`status: "NotRun"` means already current, not failed** — the call is incremental. Treating it as an error is an infinite retry.

Client: `LakehouseClient.refresh_sql_endpoint_metadata()` (takes `lakehouse_id` and looks the endpoint id up; a `token=` lets an in-notebook caller pass `notebookutils.credentials.getToken("pbi")`).

## Shortcuts once live: nothing to operate

- **The reference is live, with nothing to refresh.** The producer's source file was replaced whole at the target path between runs; the next read through the shortcut saw the new bytes — no re-create, no cache invalidation, no staleness window.
- **The arrangement lives entirely on the consumer.** `list_shortcuts()` on the referenced item returns `[]` — referencing writes nothing into the target.
- **Deleting a shortcut deletes the reference, never the data** — the asymmetry that makes a reference cheap to undo where a copy is not.
