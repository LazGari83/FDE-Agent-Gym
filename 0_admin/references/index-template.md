# Fabric — Knowledge Base Index

Curated router for the wiki. One row per page, grouped by topic. Keep it annotated —
an unannotated link dump is the most common failure mode of an agent-facing index.
Two communities, one brain: every page carries `community: frontier-data-engineer |
frontier-decision-engineer | both` (see CLAUDE.md).

## decisions

Experiment bake-offs — the highest-signal pages. Evidence-based X-vs-Y calls. Lead a
decision row's Summary with **Recommendation: …**.

| Page | Type | Community | Summary | Updated | Release |
|------|------|------|---------|---------|---------|
| [{Option A} vs {Option B} for {job}](decisions/{slug}.md) | decision | {community} | **Recommendation: {chosen}** — {one-line why} | {YYYY-MM-DD} | {YYYY-MM} |

## {topic-name}

{One-line description of this Fabric topic.}

Topic sections carry the page's `evidence:` value as a column (most sections); a section
whose pages span communities may carry a Community column instead (as the `decisions` section may).

| Page | Type | Evidence | Summary | Updated | Release |
|------|------|----------|---------|---------|---------|
| [{Page Title}]({topic-name}/{page}.md) | capability | {proven\|mixed\|unverified} | {one-line summary} | {YYYY-MM-DD} | {YYYY-MM} |
| [{Pattern Title}]({topic-name}/{pattern}.md) | pattern | {proven\|mixed\|unverified} | {one-line summary} | {YYYY-MM-DD} | {YYYY-MM} |
