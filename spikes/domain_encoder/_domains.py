"""Frozen domain classes and seed phrases for the domain-encoder spike.

Defines the 5-way domain vocabulary from spec §9.3 and the prototype seed
phrases used to derive centroid embeddings for the classification head.

Versioning discipline (mirrors posture._markers):
- SEED_PHRASES_VERSION is bumped on ANY change to any seed phrase.
- Seed phrases are tuples (immutable).  Do not mutate at runtime.
- The model revision is documented in the spike report; this module
  tracks the vocabulary contract independently.

Source refs:
- §8.2: encoder owns DOMAIN; 5 classes named.
- §9.1: 13-agent domain × posture grid.
- §9.3: refined axis values (draft, pre-spike).
- §9.2 finding 4: four agents are domain-"any"; high entropy ↔ domain-any.
"""

from __future__ import annotations

import enum


class DomainLabel(str, enum.Enum):
    """Five-way coarse domain vocabulary from spec §9.3.

    Values are string-compatible so they can be used as dict keys and
    serialised to JSON without extra conversion.

    code:
        Code internals — functions, classes, algorithms, tests, builds.
    infra_deploy:
        Infrastructure and deployment — containers, CI/CD, cloud, networking.
    data:
        Data engineering — schemas, migrations, pipelines, databases, storage.
    docs_prose:
        Documentation and prose — READMEs, changelogs, specs, user-facing text.
    project_meta:
        Project management and meta — roadmaps, issues, milestones, ideas.
    """

    CODE = "code"
    INFRA_DEPLOY = "infra_deploy"
    DATA = "data"
    DOCS_PROSE = "docs_prose"
    PROJECT_META = "project_meta"


#: Ordered list of all domain classes — stable ordering for numpy indexing.
DOMAIN_CLASSES: tuple[DomainLabel, ...] = (
    DomainLabel.CODE,
    DomainLabel.INFRA_DEPLOY,
    DomainLabel.DATA,
    DomainLabel.DOCS_PROSE,
    DomainLabel.PROJECT_META,
)

# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------

#: Version for the seed-phrase vocabulary. Bump on ANY change.
SEED_PHRASES_VERSION: str = "2026-06-11-v1"

# ---------------------------------------------------------------------------
# Seed phrases
#
# Each class is represented by a tuple of short, representative phrases.
# These are used to compute class centroid embeddings in the classification
# head.  They are intentionally domain-salient, not overly specific — the
# model generalises from them.
#
# Design: centroid/nearest-prototype head (spec §AC: "centroid / nearest-
# prototype or equivalent — document the choice and why").
# Seed phrases are drawn from the §9.1 agent grid's domain column and from
# the §11 spike prompt vocabulary, grounded in that vocabulary.
# ---------------------------------------------------------------------------

SEED_PHRASES: dict[DomainLabel, tuple[str, ...]] = {
    DomainLabel.CODE: (
        "fix the bug in the Python function",
        "implement the new feature in the codebase",
        "refactor the class to reduce duplication",
        "add unit tests for the parser module",
        "update the API method signature",
        "review the pull request for correctness",
        "trace the stack overflow in the algorithm",
        "rename the variable across the module",
        "extract the helper function from the class",
        "write a failing test for the new behavior",
    ),
    DomainLabel.INFRA_DEPLOY: (
        "deploy the service to Kubernetes",
        "debug the failing CI pipeline",
        "configure the Dockerfile for production",
        "set up the GitHub Actions workflow",
        "provision the cloud infrastructure with Terraform",
        "investigate the networking error in the container",
        "update the deployment manifest",
        "check the failing GitHub Actions checks",
        "configure the load balancer rules",
        "fix the DNS resolution error in the cluster",
    ),
    DomainLabel.DATA: (
        "update the database schema migration",
        "query the SQL table for analytics",
        "design the ETL pipeline for the data warehouse",
        "validate the schema against the production database",
        "optimize the query performance",
        "migrate the data to the new storage format",
        "create the data pipeline for ingestion",
        "verify the schema is consistent with the migrations",
        "check the database constraints",
        "profile the slow query in PostgreSQL",
    ),
    DomainLabel.DOCS_PROSE: (
        "update the README with the new CLI usage",
        "write the changelog for the release",
        "revise the documentation for the API endpoint",
        "check if the docs still reflect the current behavior",
        "draft the specification document",
        "improve the onboarding guide",
        "write the tutorial for the new feature",
        "review the technical writing for clarity",
        "update the user guide with examples",
        "ensure the release notes are accurate",
    ),
    DomainLabel.PROJECT_META: (
        "plan the roadmap for the next milestone",
        "lay out the phases to implement caching",
        "file the issue for the new feature request",
        "what if we redesigned the dispatch approach",
        "is this architecture sound before we build it",
        "poke holes in the proposed design",
        "research existing solutions for distributed caching",
        "what are the alternatives to this approach",
        "scope the work for the upcoming sprint",
        "challenge the design before committing to it",
    ),
}
