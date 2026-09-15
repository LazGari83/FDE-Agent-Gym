# 2_raw/specs — Fabric item spec snapshots

Immutable, dated snapshots of the **item-definition schema** and **workload API spec** for the
Fabric items this KB's framework depends on. This is a `2_raw/` source layer: read-only provenance,
never hand-edited, and cited by wiki pages as `sources:`.

## Why this exists

Much of the framework is contingent on item definitions and API contracts. When Microsoft changes
an item schema between monthly releases, downstream wiki claims (build-framework, coding-guidance,
gotchas) can silently go stale. This layer makes those changes **visible and diffable**: the spec
dump itself is free Microsoft Docs, but the *delta between releases* is the high-signal artifact —
and that delta is what gets ingested into the wiki, never the raw dump.

## Layout

    2_raw/specs/
      <item>/
        YYYY-MM-DD-definition.md    # item-definition article (Microsoft Learn)
        YYYY-MM-DD-api-spec.md      # REST API operation-group page (Microsoft Learn)
        CHANGELOG.md                # per-item, human-readable diff trail (newest at bottom)

The filename carries the date; `CHANGELOG.md` carries the provenance and the interpreted delta. Each
snapshot keeps only the article body (Learn's volatile build-metadata frontmatter is stripped to keep
diffs clean) with a small HTML-comment header for the source URL and dates.

## Tracked items

| Folder | Workload type | Topic | Fabric status |
|---|---|---|---|
| `semantic-model` | `semanticModel` | (no topic yet) | GA |
| `report` | `report` | (no topic yet) | GA |
| `ontology` | `ontology` | ontology | Preview |
| `graph-model` | `graphModel` | (feeds `ontology`/`graph`) | GA |
| `data-agent` | `dataAgent` | data-agent | GA |
| `pipeline` | `dataPipeline` | pipelines | GA |

Source: Microsoft Learn — each snapshot's two-line HTML-comment header records its exact URL, the
page's own dates, the capture date and the route used.

## Taking your own snapshot

1. Fetch the item's Learn *item-definition* article and its *REST operation-group* page — any
   route works: a browser save, `curl`, an agent's fetch tool.
2. Keep only the article body, stripping navigation chrome and volatile build metadata.
3. Top the file with the two-line HTML-comment header (source URL; ms.date · updated_at ·
   captured · route).
4. Save as `YYYY-MM-DD-definition.md` / `YYYY-MM-DD-api-spec.md` beside the previous pair.
5. Diff against the previous snapshot and append what changed (or "no change") to the family's
   CHANGELOG — the delta, not the dump, is what feeds the wiki.

## Cadence

Re-snapshot after each monthly Fabric release — align to the release, not a blind timer.
Snapshotting is cheap and idempotent; the expensive step — ingesting a change into the wiki —
only fires when a diff is actually detected.
