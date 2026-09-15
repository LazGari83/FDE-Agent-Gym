---
type: capability          # capability | pattern
title: {Human Title}
category: {category}      # the six-page cluster slot this page fills: prerequisites-and-fit | design-framework | build-framework | operate-framework | coding-guidance | gotchas (see CLAUDE.md "Page types")
topic: {topic}            # one of the canonical topics (see CLAUDE.md)
community: {community}    # frontier-data-engineer | frontier-decision-engineer | both — the topic usually implies it (see CLAUDE.md)
capabilities: []          # ids from 0_admin/capabilities/<topic>.md this page documents — drives the coverage report's "documented" axis. A `status: placeholder` page must NOT declare capabilities (an empty page must not count as documentation).
updated: {YYYY-MM-DD}     # when the knowledge last changed, not the file timestamp
fabric_release: {YYYY-MM} # the Fabric monthly release this was validated against
status: current           # placeholder | current | superseded | archived (see CLAUDE.md "Status lifecycle"; placeholder = an empty slot stating what belongs there and what evidence unlocks it — never the content)
evidence: {evidence}      # proven | mixed | unverified — distinct from status: proven = confirmed in your Fabric testing; unverified = documented but not retested in your tenant. Per-claim tags inline in the body.
sources:                  # provenance — every claim traces to a 2_raw/ file. At least one; only a status: placeholder page may carry `sources: []` (it asserts nothing).
                          # A seed page — `status: current` with `sources: []` and a `provenance_notes` block declaring it ships pre-evidence — is the one other sanctioned `sources: []` case: shipped worked examples start that way, and the exemption ends at the first ingest that gives the page real sources.
  - 2_raw/{topic}/{YYYY-MM-DD-slug}.md
  - 2_raw/gym-rep-reports/{topic}/{YYYY-MM-DD-task}.md
provenance_notes: ""      # optional free-text: where these claims stand relative to evidence (required on a seed page)
see_also: []              # optional: sibling cluster pages and cross-topic pages — the live pages cross-link in prose instead; use whichever the page reads better with
---

# {Human Title}

{NOTE — the cluster convention: a topic's capability knowledge decomposes into the six-page
cluster named by `category:` above, never a single monolithic capability page (CLAUDE.md
"Page types"). A new topic starts with whichever cluster pages its raw evidence supports.
The skeleton below is the generic shape for ONE such page; let the category steer what the
sections contain (a gotchas page is a trap register of symptom/cause/fix entries; a
coding-guidance page is code to copy; frameworks are ordered phases with gates).}

## Overview

{One paragraph: what this Fabric capability is, and what an agent most needs to know to drive it. Write for an agent with no prior context.}

## How to drive it

{The concrete, agent-actionable path — the portal/model/API steps that actually work. Distil from the sources; do not paste Microsoft Docs. Only include what a real build proved.}

## Gotchas

{The non-obvious failures and quirks — the delta between a generic agent and an expert's. Each gotcha should be traceable to a source above.}
- **{Gotcha}** — {what breaks, why, and the fix}. Proven in `{raw source}`.

## See Also

{OPTIONAL — only when cross-references exist. Relative links:
- Same topic: [Other Page](other-page.md)
- Different topic: [Other Page](../other-topic/other-page.md)}
