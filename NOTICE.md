# Licence in plain English

Copyright (c) 2026 UnifiedEducation. Licensed under the PolyForm Internal Use
License 1.0.0 — the full text is in [`LICENSE.md`](LICENSE.md), and that text is what
governs if anything here reads differently.

This is a **commercial product you bought a licence to use**, not an open-source project.
The summary below is written for speed of understanding, not to replace the licence.

## What you may do

- **Use it inside your own organisation**, including for commercial work. Building your
  employer's revenue-generating Fabric platform with it is squarely permitted.
- **Change anything.** Rewrite the schema, add topics, delete the tasks you don't need,
  rip out the toolkit and keep the specs. It is a starter — it is meant to be altered.
- **Share it with colleagues at the same organisation.** The licence covers you *and your
  company*, including its parent and subsidiaries, so a whole data team can work from one
  copy without buying a licence each.
- **Run it against your own tenant** and fill it with your own evidence and wiki pages.

## What you may not do

- **Distribute it.** Do not publish the repository or any part of it — not to a public
  GitHub repo, not to a blog post, not to a gist, not to a shared drive outside your
  organisation.
- **Resell or sublicense it**, repackage it as your own product, or bundle it into
  something you deliver to a customer.
- **Pass it to people outside your company**, including clients, contractors working for
  other firms, or another community.
- **Publish what you build on top of it.** A knowledge base you grow from this starter is
  a new work based on it, and it stays internal to your organisation too. Your *findings*
  about Fabric are yours to talk about freely — a page, a folder, or a fork of this
  framework carrying them is not.

If you want terms beyond these — to use it on client engagements, to redistribute it
inside a product, or to license it for a wider group — those are available. Ask.

## No warranty, and this code touches real infrastructure

The software is provided as is, with no warranty of any kind, and the licensor is not
liable for any damages arising from its use. Take that seriously here: the toolkit
authenticates against your Microsoft Fabric tenant and the practice tasks **create,
modify and delete real items** — workspaces, lakehouses, pipelines and their data.
Point it at a workspace you are willing to lose, never at production, and read
`provision.py` before you run it.

You are responsible for the cost of any Fabric capacity these runs consume, and for the
credentials you put in `.env` — that file is gitignored for a reason, and nothing in this
repository should ever be committed with a secret in it.
