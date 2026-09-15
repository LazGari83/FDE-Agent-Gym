# 2_raw — the evidence layer

Immutable, dated sources. Read, never edit. Every claim in the wiki (`3_wiki/`) must trace
back to a file in this layer — if the evidence isn't here, the claim doesn't get written.

Name every capture `YYYY-MM-DD-<slug>.md` and start from the template at
`0_admin/references/raw-template.md` — its frontmatter (source, collected date, Fabric
release) is what makes a file citable later.

## Empty is correct

Most of these folders ship empty on purpose. They fill from **your** builds, captures and
rep reports:

- `<topic>/` — one folder per KB topic (`lakehouse/`, `pipelines/`, …): docs captures,
  release notes, community articles, evidence from your own builds.
- `experiments/` — raw logs and data from A/B bake-offs.
- `thoughts/` — raw idea dumps, before anything is proven.
- `gym-rep-reports/<topic>/` — the report each passing gym rep writes.
- `specs/` — the one area that ships populated: dated Microsoft Learn spec snapshots.
  See [`specs/README.md`](specs/README.md).

## Folder naming: why `pipelines/` and `specs/pipeline/` coexist

Topic folders use the KB **topic** name, plural where the topic is (`pipelines/`,
`semantic-models/`). `specs/` subfolders use the Fabric **item-type** name, singular
(`specs/pipeline/` tracks the `dataPipeline` item). Different namespaces, so both exist.
