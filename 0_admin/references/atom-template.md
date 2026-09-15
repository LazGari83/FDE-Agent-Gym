---
type: atom                # lands at 3_wiki/<topic>/atoms/<CAP-ID>.md; every atom also gets a row in that folder's `atoms/index.md` (created by the first ingest)
topic: {topic}            # the topic whose atoms/ folder holds this card
community: {community}    # frontier-data-engineer | frontier-decision-engineer | both — same as the topic's cluster pages
capability: {CAP-ID}      # SINGULAR — exactly one capability id per card (never `capabilities:`)
title: {imperative, capability-shaped title}
requires:
  env: []                 # environmental prerequisites; use a preflight_probe.py check name where one exists (e.g. capacity, fabric-auth, arm-plane) so a circuit's feasibility scan can verify it mechanically
  atoms: []               # other atom ids this card assumes; a rep pulls the whole closure
updated: {YYYY-MM-DD}
fabric_release: {YYYY-MM}
status: current           # atoms are minted only from landed reps, so they start current
evidence: proven          # an atom exists only for a rep-proven capability
sources:                  # the SAME 2_raw/ sources as the cluster pages the card derives from
  - 2_raw/gym-rep-reports/{topic}/{YYYY-MM-DD-task}.md
derived_from: [{category}, {category}]  # which cluster pages (by category:) this card is the execution view of
---

# {CAP-ID} — {imperative title}

**When:** {the fires-when line — the concrete situation in a rep that makes this card apply}.

{Body ≤30 lines: the proven call shape — code/config verbatim from the landed rep — plus
the gotcha one-liners that bite, each linking the sibling atom or canonical cluster section
it condenses. Execution only: the cluster pages stay canonical for reasoning. Atoms are
refreshed at ingest by fabric-ingest, never minted speculatively, and never for design-gate
capabilities (those are judgment, argued against the design-framework page).}
