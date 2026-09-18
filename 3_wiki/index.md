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

**First rep landed 2026-09-16 (AG-LAK-001); second landed 2026-09-18 (AG-LAK-002 — concurrent
submission + a second execution surface).** The six-page cluster shape is the worked
example: `prerequisites-and-fit` · `design-framework` · `build-framework` ·
`operate-framework` · `coding-guidance` · `gotchas` — the first four following the lifecycle an
agent walks (is this the right tool → decide the shape → build it → run it once it is live),
with `coding-guidance` the code for all of them and `gotchas` the trap register across all of
them. AG-LAK-002 re-proved LH-C08/LH-C14 with no deviation, exercised LH-C23 concretely
(notebook load + notebook probe + Livy summary — the design-framework split, applied), and
confirmed a draft LH-C27 atom; it also softened the "concurrent runs always collide" claim to
conditional (two concurrent identical `overwrite` submissions both completed clean). Claims
outside either rep's scope — LH-C06, LH-C12, LH-C15, LH-C18 — remain untested.
[`atoms/`](lakehouse/atoms/index.md) holds five rep-proven execution cards: LH-C02, LH-C07,
LH-C08, LH-C14, LH-C27.

| Page                                                              | Type       | Evidence   | Summary                                                                                                              | Updated    | Release |
| ----------------------------------------------------------------- | ---------- | ---------- | -------------------------------------------------------------------------------------------------------------------- | ---------- | ------- |
| [Prerequisites and fit](lakehouse/prerequisites-and-fit.md)       | capability | mixed | Two auth planes, the preflight probe that proves your tenant, the notebook-vs-alternative fit test, establishing the runtime, and what the API plane cannot do. | 2026-09-18 | 2026-09 |
| [Design framework](lakehouse/design-framework.md)                 | capability | mixed | The decisions taken before the first create call — several irreversible: schema-enabled, which schema, attached vs absolute addressing, execution surface. | 2026-09-18 | 2026-09 |
| [Build framework](lakehouse/build-framework.md)                   | capability | mixed | Ordered P1–P6: folder, schema-enabled lakehouse, SQL endpoint, notebook item, run, verify from an independent session.| 2026-09-18 | 2026-09 |
| [Operate framework](lakehouse/operate-framework.md)               | capability | mixed | Driving a run to a verified terminal state, the three degrees of acknowledgement, concurrency (collision is conditional, not guaranteed), run ledgers, the per-write SQL endpoint metadata sync, shortcuts once live. | 2026-09-18 | 2026-09 |
| [Coding guidance](lakehouse/coding-guidance.md)                   | capability | mixed | Call shapes: `notebook_content_py()`, run submission (parameters/defaultLakehouse/environment), abfss paths, Livy `kind="pyspark"`, environment items, shortcuts, T-SQL over pyodbc. | 2026-09-16 | 2026-09 |
| [Gotchas](lakehouse/gotchas.md)                                   | capability | mixed | Trap register: per-type name rules (and name reservation), prologue-only validation, `dbo` landings, SQL-endpoint staleness, shortcut conflict/path traps, Livy Scala default, conditional run collisions. | 2026-09-18 | 2026-09 |

## your other topics

Folders are already in place for `cicd` · `ingestion` · `key-vault` · `openmirror` · `pipelines`
(under `1_agent-gym/`); `azure-app` · `capacity` · `data-agent` · `graph` · `ontology` · `testing`
are declared in `CLAUDE.md`'s topic table but have no gym folder, spec, or code toolkit in this
repo yet. Each gets a section here — copied from the `lakehouse` shape above — once it has a
real page. Delete this whole section when they all do.
