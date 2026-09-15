---
type: pattern
topic: {topic}            # the primary topic; note cross-cutting reach in the body
community: {community}    # frontier-data-engineer | frontier-decision-engineer | both — the topic usually implies it (see CLAUDE.md)
title: {Human Title}
capabilities: []          # ids from 0_admin/capabilities/<topic>.md this pattern carries — always declare these
updated: {YYYY-MM-DD}
fabric_release: {YYYY-MM}
status: current           # placeholder | current | superseded | archived
evidence: {evidence}      # proven | mixed | unverified — per-claim tags inline in the body
sources:
  - 2_raw/gym-rep-reports/{topic}/{YYYY-MM-DD-task}.md
see_also: []              # optional: the cluster pages this pattern split out of / relates to
provenance_notes: ""      # optional free-text: e.g. why this split out of a cluster page, what "proven" rests on
---

# {Human Title}

## Problem

{The recurring situation this pattern solves — the cross-cutting gotcha or need. When does an agent hit this?}

## The pattern

{The reusable technique, step by step and agent-actionable. Distilled from real builds. Self-contained.}

## Why it works / when it fails

{The judgment layer: the conditions under which this holds, and the edge cases where it doesn't. Each claim traceable to a source.}

## See Also

{OPTIONAL — relative links to related capability/decision pages.}
