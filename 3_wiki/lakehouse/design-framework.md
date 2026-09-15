---
type: capability
title: Design framework
category: design-framework
topic: lakehouse
community: frontier-data-engineer
capabilities: [LH-C02, LH-C06, LH-C10, LH-C14, LH-C18, LH-C23]
updated: 2026-08-18
fabric_release: 2026-08
status: current
evidence: unverified
sources: []
provenance_notes: "**Seed page** — see [prerequisites-and-fit](prerequisites-and-fit.md) for why these six pages cite nothing, and what converts them."
---

# Lakehouse — design framework

The decisions taken **before** the first create call, because each is expensive or impossible to reverse afterwards.

## 1. Schema-enabled, or not

**Schema-enabled is not the API default, and the choice is irreversible.** A bare create yields the legacy schemaless variant, and the only fix is to recreate the item and reload it. Everything in this cluster assumes schema-enabled.

Verify from `properties.defaultSchema` on a GET — the flag you sent is never echoed back, so asserting on your own request proves nothing.

## 2. Which schema the table lands in (LH-C02)

`dbo` is the default schema, and **an unqualified write lands there** while still passing a naive existence check. If the contract names a schema, the design has two assertions in it, not one: the table is present in the named schema **and** absent from `dbo`.

## 3. How storage is addressed

Two options, and the choice propagates into every read and write:

| | Attached default lakehouse | Absolute ABFS path |
|---|---|---|
| Binds | exactly one lakehouse | none |
| Set at | run submission | in the source |
| Survives redeploy to another workspace | no | yes |
| Reaches a second lakehouse | no | yes |

Attachment is simpler and correct for a single-lakehouse job. Choose absolute paths the moment the notebook must reach more than one lakehouse or survive redeployment — and note that a default lakehouse **cannot** be attached in the item definition over REST, only at submission, which no deployment carries. Call shapes in [coding-guidance](coding-guidance.md).

## 4. Explicit schema, or inference

Inference is a runtime decision made on the data that happened to arrive. Declare the schema explicitly wherever the table is a contract: a column that silently arrives as `string` instead of `double` is a defect that appears downstream, long after the run went green.

## 5. Which execution surface

Both run Spark; they differ in what they leave behind.

| | Jobs API (notebook item) | Livy session |
|---|---|---|
| Artefact | a notebook item, in git, redeployable | none — session dies |
| Output | none (land it as data) | inline stdout per statement |
| Cost | ~30–40 s session start per run | ~40 s once, then seconds per statement |
| Use for | the re-runnable job | probing an unknown surface, ad-hoc checks |

Probe with Livy, ship with a notebook item. A task that asks for a second, genuinely different surface is asking you to separate these two.

## 6. How the run reports its verdict

The job instance carries **no exit value** ([operate-framework](operate-framework.md)), so a notebook's own verdict only exists if the notebook persists it — an audit table, or a JSON blob under `Files/_diag/`. Decide this before building; it is not something to bolt on after a run has already failed silently.

## 7. What the session costs

A notebook that sizes its own session states its intent in the source rather than inheriting a workspace default that may change under it.

## Design-gate capabilities

`LH-C01` (is a lakehouse the right item at all — declared on [Prerequisites and fit](prerequisites-and-fit.md), which owns it) and `LH-C23` (which execution surface) are **judgment, argued at the design gate** — they are not mechanisms and never get an atom (the ≤30-line execution card a passing rep mints). The material above is the argument, not the answer.
