---
title: Held-out Validation — Milestone 14 / Issue #387
date: 2026-06-17
tracking: glitchwerks/claude-wayfinder#387
parent: glitchwerks/claude-wayfinder#386
status: COMPLETE — GO
---

# Held-out Validation: Phase 0b Two-Axis Labeler (Issue #387)

**Purpose.** Production gate for the Phase 0b two-axis GPT labeler.
Run the labeler on 150 unseen held-out contexts (ids 90001–90150),
compute RC/CW against the held-out gold, and compare to the in-sample
Phase 0b scoped reference to confirm the labeler generalizes.

---

## 1. Run Details

- **Date:** 2026-06-17
- **Labeler command:**
  ```
  PYTHONPATH="I:/ai/claude/claude-wayfinder/.worktrees/386-phase0b"
      .venv/Scripts/python.exe scripts/corpus/phase0_gpt_labeler.py
      --corpus docs/research/2026-06-17-heldout-contexts.jsonl
      --output docs/research/2026-06-17-heldout-gpt-labels.jsonl
  ```
- **Batches:** 6 × 25 entries. Each batch triggered the known
  template-echo artifact (first-seen kept; duplicates discarded by
  the accumulator). No genuine re-call needed for missing entries.
- **Labels written:** 150 / 150. Line count confirmed before scoring.
- **Module check:** `scripts.corpus` resolved to worktree
  (`I:\ai\claude\claude-wayfinder\.worktrees\386-phase0b\scripts\corpus`)
  — not the parent checkout.

---

## 2. Scoping Rule

Exclude entries whose `gold_agent` is in `{"self_handle",
"mixed_content"}` — decision classes the two-axis cell-map structurally
cannot emit, per #386 scoping.

| Category | Count |
|----------|-------|
| `self_handle` | 11 |
| `mixed_content` | 1 |
| **Total out-of-scope** | **12** |
| **Routable (scoped)** | **138** |

---

## 3. RC/CW Results

### 3a. Metric table

| Cut | N | RC | CW | Verdict |
|-----|---|----|----|---------|
| Unscoped (all 150) | 150 | 0.6800 | 0.2662 | reference |
| **Scoped (138 routable)** | **138** | **0.7391** | **0.2031** | **see §3b** |
| In-sample Phase 0b no_smoke | 166 | 0.7396 | 0.2022 | (reference) |

### 3b. GO/NO-GO (bar: RC ≥ 0.60 AND CW ≤ 0.2558)

| Axis | Scoped value | Bar | Delta vs bar | PASS/FAIL |
|------|-------------|-----|--------------|-----------|
| RC | 0.7391 | ≥ 0.60 | +0.1391 | **PASS** |
| CW | 0.2031 | ≤ 0.2558 | −0.0527 | **PASS** |

**Overall: GO.**

---

## 4. Per-Axis GPT-vs-Gold Agreement (Scoped, n=138)

| Axis | Matches | N | Agreement |
|------|---------|---|-----------|
| Domain | 96 | 138 | **69.6%** |
| Posture | 116 | 138 | **84.1%** |

### Top domain mismatches (gold → gpt)

| Gold domain | GPT domain | Count |
|-------------|------------|-------|
| None (is_any) | project_meta | 21 |
| docs_prose | project_meta | 5 |
| project_meta | code | 3 |
| project_meta | is_any | 3 |
| None (is_any) | is_any | 3 |
| code | project_meta | 2 |
| project_meta | docs_prose | 2 |
| docs_prose | is_any | 1 |

### Top posture mismatches (gold → gpt)

| Gold posture | GPT posture | Count |
|--------------|-------------|-------|
| plan | build | 5 |
| build | operate | 5 |
| diagnose | build | 4 |
| assess | build | 1 |
| verify | build | 1 |
| research | operate | 1 |
| build | verify | 1 |
| operate | assess | 1 |

---

## 5. CW-Miss Breakdown (Scoped)

Total delegates: **128** | Total CW misses: **26** (20.3% of delegates)

| Category | Count | % of CW misses |
|----------|-------|----------------|
| **B — cell-map fault** (labels matched, wrong route) | 4 | 15.4% |
| **C — mislabel** (GPT label ≠ gold on ≥1 axis) | 22 | 84.6% |
| C · domain-only | 2 | 7.7% |
| C · posture-only | 10 | 38.5% |
| C · both | 10 | 38.5% |

### CW misses by gold_agent (C mislabel + B cell-map combined)

| gold_agent | CW misses |
|------------|-----------|
| project-planner | 6 |
| inquisitor | 3 |
| code-writer | 3 |
| devops | 3 |
| researcher | 3 |
| doc-writer | 2 |
| debugger | 2 |
| auditor | 1 |
| code-reviewer | 1 |
| ops | 1 |
| investigator | 1 |

### CW misses by gold_posture

| gold_posture | CW misses |
|-------------|-----------|
| build | 8 |
| plan | 6 |
| critique | 3 |
| research | 3 |
| diagnose | 3 |
| verify | 1 |
| assess | 1 |
| operate | 1 |

**Dominant CW drivers:** `build` posture (8 misses — GPT confused
`build` with `operate` or misidentified the agent), and `plan` (6 misses
— GPT labeled `plan` as `build`, collapsing `project-planner` and
`devops` routes into `code-writer`/`doc-writer`). `plan→build` collapse
is the single biggest driver (5 domain+posture confusion pairs from the
mismatch table).

---

## 6. In-Sample vs Held-out Comparison

| | N | RC | CW |
|-|---|----|----|
| In-sample Phase 0b no_smoke | 166 | 0.7396 | 0.2022 |
| Held-out scoped (this run) | 138 | 0.7391 | 0.2031 |
| **Delta** | | **−0.0005** | **+0.0009** |

**Finding: near-zero degradation.** The held-out scoped numbers differ
from the in-sample reference by < 0.1 percentage points on both RC and
CW. This is well within noise; the labeler generalizes cleanly to unseen
contexts.

---

## 7. Caveats

1. **Held-out contexts lack `command_prefix` and `agent_mentions`
   fields.** These were not available at held-out construction time.
   `command_prefix` drives E8 (operate Tier-A), and `agent_mentions`
   drives E11 (near-dispositive pass-through). Both extractors default
   to no-fire when their inputs are empty/None. This makes the held-out
   a **conservative** (pessimistic) measure: real dispatch contexts
   with `command_prefix` or explicit agent mentions would route more
   accurately. The held-out RC/CW should be treated as a lower bound,
   not the operational ceiling.

2. **In-sample rubric tuning.** The Phase 0b labeler prompt was tuned
   on in-sample failure analysis (R1 operate broadening, R2 is_any
   domain). The near-zero held-out degradation confirms the tuning did
   not overfit, but the held-out is the honest generalization read.

3. **Gold label confidence.** 12 held-out gold entries carry
   `confidence: "medium"` (vs `"high"` for most); none are
   `disputed: true`. Confident-medium entries are not excluded — they
   represent genuine ambiguity in the routing spec, not errors.

4. **N=138 scoped.** The 12 out-of-scope entries (11 `self_handle` +
   1 `mixed_content`) are legitimate gold labels for the FULL routing
   system, but are excluded from the two-axis cell-map gate because the
   cell-map structurally cannot emit those agents. The unscoped
   RC=0.6800 includes those 12 entries and is expected to be lower.

---

## 8. Conclusion

The Phase 0b two-axis GPT labeler **passes the held-out validation gate
for Milestone 14 / Issue #387**:

- **Scoped RC = 0.7391** (bar: ≥ 0.60) — PASS, +13.91 pp over bar
- **Scoped CW = 0.2031** (bar: ≤ 0.2558) — PASS, −5.27 pp under bar
- **Held-out degradation vs in-sample: RC −0.0005, CW +0.0009** —
  essentially flat; the labeler generalizes.
- **GO verdict**: the two-axis labeler is production-ready as the
  oracle label supply for Phase 1 implementation.
