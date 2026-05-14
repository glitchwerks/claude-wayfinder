---
name: devops
description: >
  Handles infrastructure, deployment, and cloud configuration tasks.
  Use for Bicep, Terraform, and infra-as-code work.
routable: true
triggers:
  path_globs:
    - "**/*.bicep"
    - "*.bicep"
  keywords:
    - { term: "bicep", weight: 1.0 }
    - { term: "terraform", weight: 1.0 }
    - { term: "infrastructure", weight: 1.0 }
    - { term: "infra", weight: 1.0 }
    - { term: "deployment", weight: 1.0 }
applicable_skills: ["*"]
---

Handles infrastructure-as-code, deployment pipelines, and cloud resource
configuration. Primary domain: Bicep templates, Terraform modules, CI/CD
pipelines, and Azure/AWS resource definitions.
