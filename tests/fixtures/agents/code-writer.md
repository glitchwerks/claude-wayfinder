---
name: code-writer
description: Writes, edits, and implements code in any language.
routable: true
triggers:
  path_globs:
    - "**/*.py"
    - "*.py"
    - "**/*.html"
    - "*.html"
  keywords:
    - { term: "implement", weight: 1.0 }
    - { term: "edit", weight: 0.5 }
    - { term: "update", weight: 0.5 }
    - { term: "script", weight: 0.25 }
applicable_skills: ["csv-utils"]
---

Writes and edits code across languages. Use for implementation tasks,
bug fixes, and feature additions in source files.
