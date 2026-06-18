---
title: Phase 0 — Failure Decomposition (Mislabel vs Cell-Map Fault)
date: 2026-06-15
tracking: glitchwerks/claude-wayfinder#382
parent: glitchwerks/claude-wayfinder#362
status: COMPLETE
---

# Phase 0 — Failure Decomposition: Mislabel vs Cell-Map Fault

**Purpose.** Extend the Phase 0 independent-floor report by decomposing every RC miss and CW (confident-wrong) delegate miss from the GPT run1 supplied-compose run into:

- **B — cell-map/compose fault:** GPT label matches gold on BOTH axes (domain AND posture), yet the routing system still produced the wrong agent. The labeler was correct; the cell-map logic failed.
- **C — mislabel:** GPT label differs from gold on at least one axis. The labeler contributed to the miss.

This decomposition answers: is Phase 0's shortfall primarily a labeler problem (a better labeler / Phase 0b could help) or a cell-map problem (the labeler is not the bottleneck; fix the routing cells)?

---

## Full Cut

### Cut: `full` — 168 entries (168 GPT-labeled, 168 gold-labeled)

**RC:** 0.7083  |  **CW:** 0.2579

#### 1. RC-Miss Decomposition

Total labeled entries: 168  |  Total RC misses: 49 (29.2%)

| Category | Count | % of misses |
|----------|-------|-------------|
| **B — cell-map/compose fault** (labels matched, wrong route) | 11 | 22.4% |
| **C — mislabel** (GPT label != gold on >=1 axis) | 38 | 77.6% |
|   C · domain-only wrong | 11 | 22.4% |
|   C · posture-only wrong | 13 | 26.5% |
|   C · both wrong | 14 | 28.6% |

**B:C split — 22.4% cell-map fault vs 77.6% mislabel**

#### 2. Confident-Wrong (CW) Decomposition

Total delegate decisions: 159  |  Total CW misses: 41 (25.8% of delegates)

| Category | Count | % of CW misses |
|----------|-------|----------------|
| **B — cell-map/compose fault** | 8 | 19.5% |
| **C — mislabel** | 33 | 80.5% |
|   C · domain-only | 10 | 24.4% |
|   C · posture-only | 11 | 26.8% |
|   C · both | 12 | 29.3% |

**B:C split — 19.5% cell-map fault vs 80.5% mislabel**

#### 3. Top Confusion Pairs

**Domain mismatches** (gold -> gpt):

*Note: `gold_domain=None` entries (16 total in corpus) are `is_any=True` entries — the gold rubric intentionally left domain unset for cross-domain prompts. GPT always assigns a concrete domain, so all 16 systematically disagree. The largest single confusion bucket (14 entries: None -> project_meta) is driven by these.*

| gold domain | gpt domain | count |
|-------------|------------|-------|
| None (is_any) | project_meta | 14 |
| project_meta | docs_prose | 8 |
| code | project_meta | 5 |
| docs_prose | project_meta | 2 |
| None (is_any) | code | 2 |
| infra_deploy | code | 2 |
| code | infra_deploy | 1 |
| docs_prose | code | 1 |

**Posture mismatches** (gold -> gpt):

*`operate` is the most confused posture: gold assigns operate in 16 cases, GPT maps to assess (10), build (3), research (2), or verify (1). The `operate -> assess` collapse (10 cases) is the single largest confusion pair. GPT also over-collapses research and plan into build.*

| gold posture | gpt posture | count |
|--------------|-------------|-------|
| operate | assess | 10 |
| operate | build | 3 |
| research | build | 3 |
| operate | research | 2 |
| research | verify | 2 |
| plan | build | 2 |
| diagnose | verify | 1 |
| operate | verify | 1 |

#### 4. Gold-Suspect Entries (Charge 1 Probe)

Entries where GPT label differs from gold, but GPT's domain choice has plausible lexical/extension support: **30**

*Many of these 30 include the 16 `is_any=True` gold entries (GPT assigned a concrete domain that plausibly fits the text). A smaller subset are genuine ambiguities. The 30 count is an upper bound — most are GPT mismatches, not gold errors.*

**Examples (first 5):**

1. **corpus_id 33609** — `Resolve new merge conflicts in the local git branch for warpdotdev/warp PR #1144...`
   - file_paths: `**/*.rs, Cargo.lock`
   - GPT: `project_meta`/`operate` — routed to `ops`
   - Gold: `code`/`operate` — expected `ops`
   - Note: same route outcome (`ops`); gold-suspect but not an RC miss. Domain disagrees but agent agrees.

2. **corpus_id 33622** — `Rename the Claude Code skill session-variance to session-analysis in the claude-...`
   - file_paths: `skills/session-variance/SKILL.md, README.md`
   - GPT: `docs_prose`/`build` — routed to `doc-writer`
   - Gold: `project_meta`/`build` — expected `self_handle`
   - Note: `.md` extension plausibly supports `docs_prose`; gold expects `self_handle` (project management action).

3. **corpus_id 33660** — `Investigate why a specific GitHub repo is not being discovered by the buildwithc...`
   - file_paths: `.github/workflows, scripts`
   - GPT: `infra_deploy`/`verify` — routed to `auditor`
   - Gold: `code`/`diagnose` — expected `investigator`
   - Note: `.github/workflows` plausibly supports `infra_deploy`; gold disagrees on both domain and posture.

4. **corpus_id 33679** — `Prepare release v1.2.0: bump version string 1.1.1 to 1.2.0 in pyproject.toml and...`
   - file_paths: `pyproject.toml, .claude-plugin/plugin.json, CHANGELOG.md`
   - GPT: `project_meta`/`build` — routed to `None` (no agent for project_meta/build cell)
   - Gold: `docs_prose`/`build` — expected `doc-writer`
   - Note: `CHANGELOG.md` supports `docs_prose`; `pyproject.toml` supports `project_meta`. Genuinely ambiguous.

5. **corpus_id 33683** — `Remove dead context-switch: true frontmatter line from skills/gh-pr-review-addre...`
   - file_paths: `skills/gh-pr-review-address/SKILL.md`
   - GPT: `docs_prose`/`build` — routed to `doc-writer`
   - Gold: `project_meta`/`build` — expected `self_handle`
   - Note: `.md` extension supports `docs_prose`; gold says `project_meta` because it is a skill-file edit (project management).

---

## No-Smoke Cut (Primary)

### Cut: `no_smoke` — 109 entries (109 GPT-labeled, 109 gold-labeled)

**RC:** 0.5505  |  **CW:** 0.4100

#### 1. RC-Miss Decomposition

Total labeled entries: 109  |  Total RC misses: 49 (45.0%)

*All 49 misses come from the 109 non-smoke entries — the 59 removed smoke entries routed correctly on both cuts, so the B/C decomposition is identical to the full cut. The no_smoke numbers express the same 49 misses as a fraction of a smaller denominator (109 vs 168), raising the miss rate from 29% to 45%.*

| Category | Count | % of misses |
|----------|-------|-------------|
| **B — cell-map/compose fault** (labels matched, wrong route) | 11 | 22.4% |
| **C — mislabel** (GPT label != gold on >=1 axis) | 38 | 77.6% |
|   C · domain-only wrong | 11 | 22.4% |
|   C · posture-only wrong | 13 | 26.5% |
|   C · both wrong | 14 | 28.6% |

**B:C split — 22.4% cell-map fault vs 77.6% mislabel**

#### 2. Confident-Wrong (CW) Decomposition

Total delegate decisions: 100  |  Total CW misses: 41 (41.0% of delegates)

| Category | Count | % of CW misses |
|----------|-------|----------------|
| **B — cell-map/compose fault** | 8 | 19.5% |
| **C — mislabel** | 33 | 80.5% |
|   C · domain-only | 10 | 24.4% |
|   C · posture-only | 11 | 26.8% |
|   C · both | 12 | 29.3% |

**B:C split — 19.5% cell-map fault vs 80.5% mislabel**

#### 3. Top Confusion Pairs

**Domain mismatches** (gold -> gpt):

*`None` = `is_any=True` entries in gold (16 total in corpus). GPT always assigns a concrete domain, so all 16 are mismatches. `project_meta` is GPT's most common "anchor" when it cannot determine domain.*

| gold domain | gpt domain | count |
|-------------|------------|-------|
| None (is_any) | project_meta | 14 |
| project_meta | docs_prose | 8 |
| code | project_meta | 5 |
| docs_prose | project_meta | 2 |
| None (is_any) | code | 2 |
| infra_deploy | code | 2 |
| code | infra_deploy | 1 |
| docs_prose | code | 1 |

**Posture mismatches** (gold -> gpt):

*`operate -> assess` (10 cases) is the single dominant confusion. Together, all `operate` mismatches account for 16 of 24 total posture mismatches — the `operate` posture is poorly internalized by GPT from a one-pass rubric read.*

| gold posture | gpt posture | count |
|--------------|-------------|-------|
| operate | assess | 10 |
| operate | build | 3 |
| research | build | 3 |
| operate | research | 2 |
| research | verify | 2 |
| plan | build | 2 |
| diagnose | verify | 1 |
| operate | verify | 1 |

#### 4. Gold-Suspect Entries (Charge 1 Probe)

Entries where GPT label differs from gold, but GPT's domain choice has plausible lexical/extension support: **30**

*Upper bound: many of the 30 are `is_any=True` gold entries where GPT assigning any concrete domain passes the heuristic. Genuine ambiguities (e.g. examples 2, 4, 5 below) are a subset.*

**Examples (first 5):**

1. **corpus_id 33609** — `Resolve new merge conflicts in the local git branch for warpdotdev/warp PR #1144...`
   - file_paths: `**/*.rs, Cargo.lock`
   - GPT: `project_meta`/`operate` — routed to `ops`
   - Gold: `code`/`operate` — expected `ops`
   - Note: domain disagrees but routing outcome matches; not an RC miss.

2. **corpus_id 33622** — `Rename the Claude Code skill session-variance to session-analysis in the claude-...`
   - file_paths: `skills/session-variance/SKILL.md, README.md`
   - GPT: `docs_prose`/`build` — routed to `doc-writer`
   - Gold: `project_meta`/`build` — expected `self_handle`

3. **corpus_id 33660** — `Investigate why a specific GitHub repo is not being discovered by the buildwithc...`
   - file_paths: `.github/workflows, scripts`
   - GPT: `infra_deploy`/`verify` — routed to `auditor`
   - Gold: `code`/`diagnose` — expected `investigator`

4. **corpus_id 33679** — `Prepare release v1.2.0: bump version string 1.1.1 to 1.2.0 in pyproject.toml and...`
   - file_paths: `pyproject.toml, .claude-plugin/plugin.json, CHANGELOG.md`
   - GPT: `project_meta`/`build` — routed to `None` (no agent for project_meta/build cell)
   - Gold: `docs_prose`/`build` — expected `doc-writer`

5. **corpus_id 33683** — `Remove dead context-switch: true frontmatter line from skills/gh-pr-review-addre...`
   - file_paths: `skills/gh-pr-review-address/SKILL.md`
   - GPT: `docs_prose`/`build` — routed to `doc-writer`
   - Gold: `project_meta`/`build` — expected `self_handle`

---

## Bottom Line

On the primary `no_smoke` cut, **78% of RC misses are mislabel (C) and 22% are cell-map/compose fault (B)**. Within C, no single axis dominates cleanly: 29% are domain-only wrong, 34% posture-only wrong, and 37% have both axes wrong. Accounting for the 16 `is_any=True` gold entries (where GPT always assigns a concrete domain, inflating domain-mismatch counts artificially), the posture axis is the more impactful labeler weakness — the `operate -> assess` collapse alone (10 cases) accounts for 26% of all C-type RC misses. The CW decomposition is consistent: 81% mislabel vs 20% cell-map fault. The gold-suspect probe found 30 entries (of 109 no_smoke labeled) with surface support for GPT's domain choice; most are `is_any` entries rather than genuine gold errors, but examples 2, 4, and 5 are real ambiguities where a reasonable human might agree with GPT over gold.

**Bottom line:** Phase 0's no_smoke shortfall is **labeler-dominated** (78% of misses = C, mislabel). A better labeler — specifically one that applies the `operate` posture correctly and understands the `is_any` concept — is the highest-leverage intervention. The compose cell-map logic accounts for only 22% of the failure (11 B-type misses), so the cell-map is a secondary fix target, not the primary one. Gold errors are a minor contributor: the 30 gold-suspect entries are mostly borderline `is_any` cases, not evidence of systematic gold inflation from same-family bias.
