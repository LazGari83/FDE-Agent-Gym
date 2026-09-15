---
type: decision
topic: {topic}            # or "cross-cutting"
community: {community}    # frontier-data-engineer | frontier-decision-engineer | both — the community this decision most serves (or both if it straddles)
title: "{Option A} vs {Option B} for {job}"
updated: {YYYY-MM-DD}
fabric_release: {YYYY-MM}
status: current           # current | superseded | archived
options: [{option-a}, {option-b}]
recommendation: {option-a}
capabilities: []          # the capability id(s) this decision settles (e.g. the surface-choice row) — always declare these
sources:                  # the experiment or gym rep that produced the evidence — 2_raw/experiments/ and/or 2_raw/gym-rep-reports/ (decisions are also settled by gym reps)
  - 2_raw/experiments/{YYYY-MM-exp-slug}/
  - 2_raw/gym-rep-reports/{topic}/{YYYY-MM-DD-task}.md
see_also: []              # optional: capability/pattern pages affected, atom indexes that route by this decision
---

# {Option A} vs {Option B} for {job}

> **Recommendation:** {one line — which, and the single most important reason.}

## The decision

{The real Fabric-community argument being settled, e.g. "Import or Direct Lake for this model?" Why it matters.}

## How it was tested

{Both options built in Fabric, run in parallel for {period}, logging {what}. The bake-off setup — enough that the evidence is trustworthy and reproducible.}

## Evidence

{The logged data and what it showed. Tables/numbers where possible. This is the high-signal, differentiated content — evidence, not opinion.}

| Dimension | {Option A} | {Option B} |
|---|---|---|
| {refresh / query latency / DX / …} | {result} | {result} |

## Verdict

{The evidence-based recommendation and the conditions under which it flips. When would you choose the other option?}

## See Also

{OPTIONAL — capability/pattern pages affected by this decision.}
