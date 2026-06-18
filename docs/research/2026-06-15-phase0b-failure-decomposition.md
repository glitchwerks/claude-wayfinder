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

This decomposition answers: is Phase 0's shortfall primarily a labeler problem (→ a better labeler / Phase 0b could help) or a cell-map problem (→ the labeler isn't the bottleneck; fix the routing cells)?

---

## Full Cut

### Cut: `full` — 168 entries (168 GPT-labeled, 168 gold-labeled)

**RC:** 0.7738  |  **CW:** 0.1925

#### 1. RC-Miss Decomposition

Total labeled entries: 168  |  Total RC misses: 38 (22.6%)

| Category | Count | % of misses |
|----------|-------|-------------|
| **B — cell-map/compose fault** (labels matched, wrong route) | 9 | 23.7% |
| **C — mislabel** (GPT label != gold on >=1 axis) | 29 | 76.3% |
|   C · domain-only wrong | 11 | 28.9% |
|   C · posture-only wrong | 8 | 21.1% |
|   C · both wrong | 10 | 26.3% |

**B:C split — 23.7% cell-map fault vs 76.3% mislabel**

#### 2. Confident-Wrong (CW) Decomposition

Total delegate decisions: 161  |  Total CW misses: 31 (19.3% of delegates)

| Category | Count | % of CW misses |
|----------|-------|----------------|
| **B — cell-map/compose fault** | 6 | 19.4% |
| **C — mislabel** | 25 | 80.6% |
|   C · domain-only | 10 | 32.3% |
|   C · posture-only | 7 | 22.6% |
|   C · both | 8 | 25.8% |

**B:C split — 19.4% cell-map fault vs 80.6% mislabel**

#### 3. Top Confusion Pairs

**Domain mismatches** (gold → gpt):

| gold domain | gpt domain | count |
|-------------|------------|-------|
| code | is_any | 32 |
| None | project_meta | 14 |
| project_meta | docs_prose | 8 |
| code | project_meta | 3 |
| None | code | 2 |
| code | infra_deploy | 1 |
| docs_prose | project_meta | 1 |
| docs_prose | code | 1 |

**Posture mismatches** (gold → gpt):

| gold posture | gpt posture | count |
|--------------|-------------|-------|
| research | build | 7 |
| diagnose | build | 4 |
| plan | build | 3 |
| verify | assess | 1 |
| build | verify | 1 |
| research | verify | 1 |
| operate | build | 1 |
| plan | operate | 1 |

#### 4. Gold-Suspect Entries (Charge 1 Probe)

Entries where GPT label differs from gold, but GPT's domain choice has plausible lexical/extension support: **28**

**Examples (first 5):**

1. **corpus_id 33622** — `Rename the Claude Code skill session-variance to session-analysis in the claude-…`
   - file_paths: `skills/session-variance/SKILL.md, README.md`
   - GPT: `docs_prose`/`build` → routed to `doc-writer`
   - Gold: `project_meta`/`build` → expected `self_handle`

2. **corpus_id 33660** — `Investigate why a specific GitHub repo is not being discovered by the buildwithc…`
   - file_paths: `.github/workflows, scripts`
   - GPT: `infra_deploy`/`build` → routed to `investigator`
   - Gold: `code`/`diagnose` → expected `investigator`

3. **corpus_id 33679** — `Prepare release v1.2.0: bump version string 1.1.1 to 1.2.0 in pyproject.toml and…`
   - file_paths: `pyproject.toml, .claude-plugin/plugin.json, CHANGELOG.md`
   - GPT: `project_meta`/`build` → routed to `None`
   - Gold: `docs_prose`/`build` → expected `doc-writer`

4. **corpus_id 33683** — `Remove dead context-switch: true frontmatter line from skills/gh-pr-review-addre…`
   - file_paths: `skills/gh-pr-review-address/SKILL.md`
   - GPT: `docs_prose`/`build` → routed to `doc-writer`
   - Gold: `project_meta`/`build` → expected `self_handle`

5. **corpus_id 34638** — `Extract three situational sections out of the global ~/.claude/CLAUDE.md harness…`
   - file_paths: `CLAUDE.md, standards/artifact-persistence.md, skills/python/SKILL.md`
   - GPT: `docs_prose`/`build` → routed to `doc-writer`
   - Gold: `project_meta`/`build` → expected `self_handle`


---

## No-Smoke Cut (Primary)

### Cut: `no_smoke` — 109 entries (109 GPT-labeled, 109 gold-labeled)

**RC:** 0.6514  |  **CW:** 0.3039

#### 1. RC-Miss Decomposition

Total labeled entries: 109  |  Total RC misses: 38 (34.9%)

| Category | Count | % of misses |
|----------|-------|-------------|
| **B — cell-map/compose fault** (labels matched, wrong route) | 9 | 23.7% |
| **C — mislabel** (GPT label != gold on >=1 axis) | 29 | 76.3% |
|   C · domain-only wrong | 11 | 28.9% |
|   C · posture-only wrong | 8 | 21.1% |
|   C · both wrong | 10 | 26.3% |

**B:C split — 23.7% cell-map fault vs 76.3% mislabel**

#### 2. Confident-Wrong (CW) Decomposition

Total delegate decisions: 102  |  Total CW misses: 31 (30.4% of delegates)

| Category | Count | % of CW misses |
|----------|-------|----------------|
| **B — cell-map/compose fault** | 6 | 19.4% |
| **C — mislabel** | 25 | 80.6% |
|   C · domain-only | 10 | 32.3% |
|   C · posture-only | 7 | 22.6% |
|   C · both | 8 | 25.8% |

**B:C split — 19.4% cell-map fault vs 80.6% mislabel**

#### 3. Top Confusion Pairs

**Domain mismatches** (gold → gpt):

| gold domain | gpt domain | count |
|-------------|------------|-------|
| None | project_meta | 14 |
| project_meta | docs_prose | 8 |
| code | is_any | 3 |
| code | project_meta | 3 |
| None | code | 2 |
| code | infra_deploy | 1 |
| docs_prose | project_meta | 1 |
| docs_prose | code | 1 |

**Posture mismatches** (gold → gpt):

| gold posture | gpt posture | count |
|--------------|-------------|-------|
| research | build | 7 |
| diagnose | build | 4 |
| plan | build | 3 |
| verify | assess | 1 |
| build | verify | 1 |
| research | verify | 1 |
| operate | build | 1 |
| plan | operate | 1 |

#### 4. Gold-Suspect Entries (Charge 1 Probe)

Entries where GPT label differs from gold, but GPT's domain choice has plausible lexical/extension support: **28**

**Examples (first 5):**

1. **corpus_id 33622** — `Rename the Claude Code skill session-variance to session-analysis in the claude-…`
   - file_paths: `skills/session-variance/SKILL.md, README.md`
   - GPT: `docs_prose`/`build` → routed to `doc-writer`
   - Gold: `project_meta`/`build` → expected `self_handle`

2. **corpus_id 33660** — `Investigate why a specific GitHub repo is not being discovered by the buildwithc…`
   - file_paths: `.github/workflows, scripts`
   - GPT: `infra_deploy`/`build` → routed to `investigator`
   - Gold: `code`/`diagnose` → expected `investigator`

3. **corpus_id 33679** — `Prepare release v1.2.0: bump version string 1.1.1 to 1.2.0 in pyproject.toml and…`
   - file_paths: `pyproject.toml, .claude-plugin/plugin.json, CHANGELOG.md`
   - GPT: `project_meta`/`build` → routed to `None`
   - Gold: `docs_prose`/`build` → expected `doc-writer`

4. **corpus_id 33683** — `Remove dead context-switch: true frontmatter line from skills/gh-pr-review-addre…`
   - file_paths: `skills/gh-pr-review-address/SKILL.md`
   - GPT: `docs_prose`/`build` → routed to `doc-writer`
   - Gold: `project_meta`/`build` → expected `self_handle`

5. **corpus_id 34638** — `Extract three situational sections out of the global ~/.claude/CLAUDE.md harness…`
   - file_paths: `CLAUDE.md, standards/artifact-persistence.md, skills/python/SKILL.md`
   - GPT: `docs_prose`/`build` → routed to `doc-writer`
   - Gold: `project_meta`/`build` → expected `self_handle`


---

## Bottom Line

On the primary `no_smoke` cut, **76% of RC misses are mislabel (C) and 24% are cell-map/compose fault (B)**. Within C: 38% are domain-only wrong, 28% posture-only wrong, 34% both wrong — so domain confusion is the dominant mislabel axis, not posture. The CW split is similar: 81% mislabel vs 19% cell-map fault. The gold-suspect probe found 28 entries (of 109 no_smoke labeled) where GPT's label has plausible surface support — a minority, but non-trivial. **Bottom line:** Phase 0's no_smoke shortfall is dominated by GPT mislabeling (76% of misses), not cell-map faults (24%). This means a better labeler or improved rubric fidelity is the highest-leverage fix — the compose cell-map logic itself accounts for only 24% of the failure. However, 28 gold-suspect entries suggest the gold labels may themselves carry ~28 errors, which partially offsets the mislabel attribution (if those gold labels are wrong, some C-labeled misses are actually correct routes misflagged as errors).
