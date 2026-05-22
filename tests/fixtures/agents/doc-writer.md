---
name: doc-writer
description: >
  Writes and edits documentation, READMEs, design docs, and prose
  content. Use for docs/**/*.md, README.md, and spec files.
routable: true
triggers:
  path_globs:
    - "docs/**/*.md"
    - "docs/*.md"
    - "README.md"
    - "**/*.md"
  path_globs_excluded:
    - "agents/**/*.md"
    - "agents/*.md"
    - "skills/**/*.md"
    - "skills/*.md"
  keywords:
    - { term: "docs", weight: 1.0 }
    - { term: "readme", weight: 1.0 }
    - { term: "prose", weight: 1.0 }
    - { term: "doc", weight: 1.0 }
    - { term: "edit", weight: 0.25 }
    - { term: "update", weight: 0.25 }
applicable_skills: ["*"]
---

Writes and edits prose content: design documents, READMEs, API docs,
ADRs, and plan files. Does NOT handle harness files such as
agents/**/*.md or skills/**/SKILL.md — those are handled by the router
with the agent-authoring skill. Exclusion is enforced via
path_globs_excluded (issue #24) rather than scope-by-omission.
