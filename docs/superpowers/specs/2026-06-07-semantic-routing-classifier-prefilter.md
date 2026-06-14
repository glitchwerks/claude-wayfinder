---
title: Semantic routing — Spec B of 2 — Coarse-to-Fine Semantic Classifier Pre-Filter
date: 2026-06-07
tracking: glitchwerks/claude-wayfinder#325
status: draft — awaiting independent comparison pass
touches:
  - src/claude_wayfinder/match/_match.py
  - src/claude_wayfinder/match/_decide.py
  - src/claude_wayfinder/match/_types.py
  - src/claude_wayfinder/build_catalog/_main.py
  - src/claude_wayfinder/build_catalog/_process.py
related:
  - glitchwerks/claude-wayfinder#288  # lexical normalization prior evaluation
  - docs/research/2026-06-07-semantic-routing.md
sibling: docs/superpowers/specs/2026-06-07-semantic-routing-dual-signal-ensemble.md
skills_relevant:
  - dispatch-authoring
  - project-review
---

# Semantic routing — Spec B of 2 — Coarse-to-Fine Semantic Classifier Pre-Filter

Tracking issue: [#325](https://github.com/glitchwerks/claude-wayfinder/issues/325)
Sibling spec: [Spec A — Parallel Dual-Signal Ensemble](2026-06-07-semantic-routing-dual-signal-ensemble.md)
Author: doc-writer (2026-06-07)
Status: **draft — independent comparison pending**

---

## 1. Summary

This is **Spec B of 2** for issue [#325](https://github.com/glitchwerks/claude-wayfinder/issues/325). It describes a Coarse-to-Fine Semantic Classifier Pre-Filter architecture for adding semantic routing to claude-wayfinder. In this design a semantic intent classifier runs first on every dispatch, assigning the prompt to one of ~6–10 intent categories and restricting the agent candidate set to entries within those categories. The existing lexical scorer then runs within the surviving (narrowed) candidate set to select the specific agent. The two stages operate at different granularities — category vs. entry — so they compose rather than conflict.

The sibling spec ([Spec A — Parallel Dual-Signal Ensemble](2026-06-07-semantic-routing-dual-signal-ensemble.md)) takes a different structural approach: both signals score every catalog entry independently at the same granularity, and disagreement between them is treated as a first-class signal. These two specs are inputs to an independent comparison; this document does not advocate for a winner.

---

## 2. Core Requirements

1. **Open source + fully locally runnable.** No hosted embedding APIs, no proprietary models. Offline on the user's machine.
2. **Deterministic encoding.** Same input → same vector every run. (Primary user concern.)
3. **No generative LLM in the hot path.** A small deterministic *encoder* is in-scope; autoregressive intent classification is out.
4. **Auditability.** A human must be able to understand why a route was chosen. "Matched keyword X" is the current gold standard; raw cosine scores are weaker.
5. **Latency.** Sub-millisecond ideal; ~10–50ms tolerable only if confined to a subset of dispatches.
6. **The lexical keyword model is currently too strong to be a sole confident primary.** A single soft keyword can manufacture a confident (wrong) `delegate`. Any design must address how it prevents a confident-wrong lexical match from misrouting.

---

## 3. Architecture Overview

The pipeline has three sequential stages:

**Stage 1 — Semantic intent classification (coarse).**
Model2Vec `potion-base-8M` encodes the prompt. The resulting vector is compared against a small set of precomputed intent-category centroids (~6–10 categories). Output: top-K categories plus a confidence margin. This stage is coarse: it does not select an agent; it identifies the intent class.

**Stage 2 — Candidate restriction (pre-filter).**
The top-K categories determine which agents are eligible for routing. Each catalog entry is tagged with one or more intent categories at catalog-build time. The union of agents in the top-K categories forms the surviving candidate set for Stage 3. Two important invariants:
- If classification confidence is low (narrow top-1 margin), Stage 2 does not prune — the full candidate set passes through.
- Skills are never category-filtered. Cross-cutting skills (`python`, `azure`, `refactoring-discipline`) are intent-independent and always participate in lexical scoring across the full skill catalog regardless of the Stage 1 output.

**Stage 3 — Lexical refinement (fine).**
The existing lexical scorer runs exclusively within the surviving agent candidate set from Stage 2. The scoring formula and all constants are unchanged; only the input set is narrowed. The existing decision ladder composes the output.

Requirement 6 is addressed by construction: the lexical scorer can no longer cause a spurious confident `delegate` by matching surface strings from a wrong intent class, because those agents have been removed from the candidate set. A spurious keyword like `review` cannot cross-classify a debugging prompt into `code-reviewer` if the semantic classifier correctly placed the prompt in the `investigation` category.

---

## 4. Pipeline / Control Flow

### 4.1 Step-by-step

1. **Context parsing** — unchanged. `build_features()` in `_match.py` produces a `Features` object.
2. **Short-circuit check** — before Stage 1, the existing short-circuit signals (explicit agent mention, command prefix) are evaluated. If they fire, skip all three stages and emit `delegate` at score `1.0`. Path-glob short-circuits at score `1.0` similarly bypass the pipeline.
3. **Stage 1 — Semantic encoding and classification:**
   a. The raw `task_description` string is encoded by Model2Vec `potion-base-8M` → 256-dim float32 vector `q`.
   b. Compute cosine similarity of `q` against each of the ~6–10 category centroids.
   c. Identify the top-K categories (K ≥ 2 for multi-intent robustness; see §11.3).
   d. Compute the confidence margin: `score[rank_1] - score[rank_2]`. If margin < `_CAT_MARGIN_MIN` (to be calibrated; suggested starting value 0.10), set K to "all categories" (no pruning).
4. **Stage 2 — Candidate restriction:**
   a. Build `candidate_agents` = union of all agents tagged with any of the top-K categories.
   b. `candidate_skills` = all skills (never filtered).
5. **Stage 3 — Lexical refinement:**
   a. Call `score_entries()` with `candidate_agents` instead of the full agent list. Score formula and `Features` object are unchanged.
   b. Run the existing `decide()` ladder on the narrowed scored lists.
6. **Decision output** — unchanged from the existing format. Additions: `classification` segment in the rationale naming the category, confidence, and top-K set; `disposition_source: "classifier_prefilter"`.

### 4.2 Worked trace — shared example prompt

**Input prompt:** `"review the python code to see why my answer doesn't return correctly"`

**Step 2 — Short-circuit check:**
No explicit agent mention, no command prefix, no path globs. Short-circuit does not fire.

**Step 3a — Semantic encoding:**
Model2Vec `potion-base-8M` encodes the prompt → 256-dim float32 vector `q`.

**Step 3b — Category cosine comparison:**
Hypothetical category centroids (illustrative — exact values require calibration):

| Category | Cosine score |
|---|---|
| `investigation` | 0.71 |
| `review` | 0.58 |
| `implementation` | 0.44 |
| `conformance` | 0.31 |
| `planning` | 0.24 |

Top-1: `investigation` (0.71). Top-2: `review` (0.58). Margin: 0.13.

**Step 3c-d — Confidence gating:**
Margin 0.13 ≥ assumed `_CAT_MARGIN_MIN` (0.10). Classification is confident. K = 2 (top-2 categories selected because multi-intent prompts motivate K > 1; see §11.3).

**Step 4 — Candidate restriction:**
Top-K = {`investigation`, `review`}. Agents tagged `investigation`: `systematic-debugging`, `investigator`. Agents tagged `review`: `code-reviewer`, `security-review`. Candidate set: {`systematic-debugging`, `investigator`, `code-reviewer`, `security-review`}.

`code-writer` is tagged `implementation` only → **pruned from the candidate set.** The keyword `code` can no longer manufacture a spurious high-score match for `code-writer` in this dispatch.

**Step 5 — Lexical refinement within candidate set:**
Lexical scoring against the 4-entry candidate set:
- `code-reviewer`: keywords `review` (weight 1.0 → 0.5), `code` (weight 0.5 → 0.25). Score ≈ 0.75.
- `systematic-debugging`: keywords `return`, `correct` may match partially. Score ≈ 0.25–0.50 depending on catalog.
- `investigator`: keywords may include `why`, `answer`. Score ≈ 0.25–0.50.
- `security-review`: low keyword overlap. Score ≈ 0.25.

Lexical top pick within candidate set: `code-reviewer` (score ≈ 0.75). Gap to second: varies. If `systematic-debugging` or `investigator` score ≈ 0.50, gap < `_DELEGATE_GAP` (0.2) → `advisory`. If they score ≈ 0.25, gap = 0.50 → `delegate` if score ≥ 0.85 (it is not; 0.75 < 0.85 → `advisory`).

**Step 6 — Decision output:**
```json
{
  "decision": "advisory",
  "agent": "code-reviewer",
  "skills": [],
  "confidence": 0.75,
  "rationale": "Classified investigation (conf 0.71, margin 0.13) → restricted to {systematic-debugging, investigator, code-reviewer, security-review}; matched keywords: review, code.",
  "alternatives": [
    {"agent": "systematic-debugging", "score": 0.50},
    {"agent": "investigator", "score": 0.25}
  ],
  "disposition_source": "classifier_prefilter"
}
```

The router sees `advisory` with `code-reviewer` as the recommendation and `systematic-debugging` as the closest alternative. The rationale chain is human-legible. The lexical scorer never saw `code-writer` — it was category-pruned before the keyword `code` could manufacture a false signal.

Note: whether the final route is `code-reviewer` or `systematic-debugging` depends on the actual catalog keyword definitions and the exact category tagging of each agent — both of which require the intent taxonomy design decision (§11.1). The trace illustrates the structural flow, not a calibrated recommendation.

---

## 5. Encoder & Index Shape

### 5.1 Encoder

**Model:** Model2Vec `potion-base-8M` (MIT license). Inference: token lookup in a fixed float32 weight matrix + `np.mean(rows, axis=0)`. No neural forward pass at query time. Dependency: `numpy` only. Source: https://github.com/MinishLab/model2vec (fetched 2026-06-07); PyPI: https://pypi.org/project/model2vec/ (fetched 2026-06-07).

Model dimensions: `unverified:` 256 — inferred from documentation patterns; the HF model card for `potion-base-8M` did not explicitly state the dimension count in the fetched content. The `potion-code-16M` variant is confirmed 256-dim (source: https://huggingface.co/minishlab/potion-code-16M, fetched 2026-06-07). Disk footprint: ~30 MB.

MTEB classification score: 70.34 for `potion-base-8M`. General MTEB avg: 51.32, approximately 92% of MiniLM quality. (Source: https://huggingface.co/minishlab/potion-base-8M, fetched 2026-06-07.)

### 5.2 What gets embedded

At **catalog-build time:**
- For each intent category (~6–10), a set of representative example utterances is embedded and averaged into a single **category centroid** vector. These are broad phrasings of the general intent: e.g., for `investigation`: `"why is this failing"`, `"find the root cause"`, `"figure out what's broken"`.
- The category centroids are the only embeddings computed for routing. Individual catalog entries do not get their own centroid vectors in this design. This is the key structural difference from Spec A.

At **dispatch time:**
- The raw `task_description` is encoded once.
- One cosine similarity is computed per category (~6–10 comparisons), not per entry (~100 comparisons).

### 5.3 Category centroid count and margins

With only ~6–10 centroids in a 256-dimensional space, the inter-centroid distances are much wider than in Spec A's 100-entry setup. Wider margins mean:
- The cosine comparison is more robust to small float32 rounding differences.
- The confidence margin (top-1 minus top-2 score) is more likely to exceed `_CAT_MARGIN_MIN`, enabling confident pruning.
- The encoder's ~8% MTEB quality gap below MiniLM is less consequential: classification among a small set of well-separated categories is an easier problem than fine-grained retrieval among 100 closely spaced entries. (Source: research report §Model2Vec entry, noting "this gap may matter for low-frequency synonyms" specifically in the fine-grained retrieval case.)

The research report's prior art note on `NadirClaw` (https://github.com/NadirRouter/NadirClaw, fetched 2026-06-07) validates this latency floor: centroid-based routing with similar embeddings achieves ~10ms. With Model2Vec's sub-millisecond encoder and only 6–10 centroid comparisons, Stage 1 latency is expected to be well under 1ms.

### 5.4 On-disk storage

Index file (alongside the generated catalog JSON):
- A `.npz` file containing: `category_centroids` array (shape `[N_categories, 256]`, float32), `category_names` array (string), `category_utterances` dict (category name → list of strings), `model_id` string, `model_version` string.
- Approximate size: 10 categories × 256 dims × 4 bytes = ~10 KB. Negligible.
- Staleness: carries `model_id` + `model_version`. A hash of the category definitions + model identifier gates regeneration. When categories are added or redefined, centroids must be regenerated.
- Additionally, the catalog JSON must carry per-entry category tags (one or more categories per agent). These are the lookup tables for Stage 2's candidate restriction.

---

## 6. Decision & Scoring Contract

### 6.1 Mapping to the 7-decision ladder

The existing ladder in `_decide.py:154-287` is preserved intact. Stage 3 passes a narrowed `scored_agents` list (containing only category-candidate agents) to `decide()`. From `decide()`'s perspective, nothing has changed — it still receives a sorted list of `ScoredEntry` objects and applies the same thresholds.

The only structural addition is a preamble that:
1. Runs the classifier (Stage 1) and candidate restriction (Stage 2).
2. Filters the agent list before calling `score_entries()`.
3. Adds a `classification` segment to the output rationale and sets `disposition_source: "classifier_prefilter"`.

When Stage 1 falls back (low-confidence classification, margin < `_CAT_MARGIN_MIN`), Stage 2 passes the full agent list unchanged, and the pipeline behaves identically to today's lexical-only path.

### 6.2 Score reconciliation

In this design there is no score reconciliation problem between the two stages. Stage 1 outputs a categorical decision (which categories survive), not a float to blend. Stage 3 outputs float scores within the surviving set. The two stages are compositional — they do not produce competing float scores for the same entity, so the blending problem that arises in Spec A does not arise here.

The lexical score for the winning agent within the narrowed set has the same interpretation as today: `score ≥ 0.85` with `gap ≥ 0.2` → `delegate`; etc. The decision ladder is unchanged.

### 6.3 Skill attachment

Skills are never category-filtered (§3). The `score_entries()` call in Stage 3 passes the full skill list alongside the restricted agent list. Skill attachment (`_skills_for_agent`) and the `_SKILL_MIN` threshold are unchanged.

### 6.4 Confidence in the output

The classifier's category confidence (top-1 cosine score and margin) is surfaced in the output rationale but does not directly set the `confidence` field of the decision. The `confidence` field retains its current meaning: the lexical score of the winning agent from `decide()`. This preserves backward compatibility for consumers that parse `confidence` against the existing [0.0, 1.0] scale.

---

## 7. Keyword-Model Changes Required

Requirement 6 is addressed by construction in this design, which changes how the lexical scorer is invoked rather than changing its internals.

### 7.1 No changes to `_match.py`

The `score()` function, `_KEYWORD_MULTIPLIER`, and the keyword weight ladder are unchanged. The lexical scorer is called with a restricted candidate set rather than the full catalog. Spurious keyword cross-firing across categories is eliminated by the candidate restriction step, not by tuning multipliers.

### 7.2 No changes to `_decide.py` thresholds

`_DELEGATE_THRESHOLD` (0.85), `_DELEGATE_GAP` (0.2), and `_ADVISORY_MIN` (0.5) are unchanged. These constants were calibrated against the full catalog; they remain valid within a restricted candidate set where the candidate agents are semantically relevant.

### 7.3 Changes required

The changes required to implement this design are additions, not modifications to existing scoring logic:

1. **New classifier stage** in the dispatch pipeline (before `score_entries()` is called): encode the prompt, compute category cosine scores, apply confidence gating, build the restricted agent list.
2. **Category tagging in the catalog schema**: each agent entry gains a `categories: [...]` field (one or more category names). The catalog generator and auditor must validate this field.
3. **Category centroid index** at catalog-build time: `build_catalog/_main.py` or `_process.py` embeds category utterances and writes the `.npz` index.
4. **Fallback path**: when classification confidence is below `_CAT_MARGIN_MIN`, the restricted agent list is the full agent list (no pruning). This path must be explicitly coded and tested.

---

## 8. Auditability Story

Auditability in this design is a strength: the pipeline emits a legible chain of reasoning at each stage.

### 8.1 Stage 1 rationale

> Classified intent: `investigation` (cosine 0.71, margin 0.13 above `review`)

This tells a catalog author: the prompt was classified as an investigation-type task with moderate confidence. The margin figure tells them how close the next category was — a useful diagnostic when the classification is borderline.

### 8.2 Stage 2 rationale

> Restricted agent set to {`systematic-debugging`, `investigator`, `code-reviewer`, `security-review`} (categories: `investigation` + `review`).

This tells the author which agents were in scope. If the correct agent was pruned, they can see immediately which category it should have been tagged with.

### 8.3 Stage 3 rationale

The existing `_rationale_for()` format is unchanged: `"matched keywords: review, code."` This is the keyword-level explanation the project established as the gold standard.

### 8.4 Full chain

The complete rationale:

> Classified `investigation` (conf 0.71, margin 0.13) → restricted to {`systematic-debugging`, `investigator`, `code-reviewer`, `security-review`}; matched keywords: `review`, `code` → `code-reviewer` (score 0.75).

This is a fully human-legible chain. Each link (classification → restriction → keyword match → winner) can be verified independently:
- Does the classification make sense? The author can check the category utterances.
- Were the right agents in the candidate set? The author can check the category tags.
- Was the keyword match correct? The author can read the existing trigger definitions.

### 8.5 Comparison with current gold standard

The current auditability gold standard is "matched keyword X" from `_rationale_for()`. This design extends that chain with one upstream step (classification) rather than replacing it. A category classification is still a human-legible assertion ("this looks like an investigation task"), whereas a raw cosine score is not. The tension between float scores and auditability is lower here than in Spec A, because the classification stage outputs a category name rather than a score.

---

## 9. Determinism Profile

Source for all claims: `docs/research/2026-06-07-semantic-routing.md` §Determinism Deep-Dive (fetched 2026-06-07).

### 9.1 Encoder determinism

Identical to Spec A: Model2Vec `potion-base-8M` uses static matrix lookup + `np.mean`. On the same machine, same numpy version, same OS, run-to-run determinism is a very strong practical guarantee. (Source: research report §Model2Vec, citing https://github.com/numpy/numpy/issues/661, fetched 2026-06-07, and https://randomascii.wordpress.com/2013/07/16/floating-point-determinism/, fetched 2026-06-07.) Cross-platform determinism is not guaranteed due to float32 FMA rounding differences but should not affect routing decisions above reasonable margin thresholds.

### 9.2 Category cosine comparison: stronger determinism than Spec A

Stage 1 computes cosine similarity against only ~6–10 category centroids. The matrix multiply is `np.dot(prompt_vec, category_matrix.T)` where `category_matrix` has shape `[6–10, 256]` — a small-matrix operation. The BLAS non-determinism risk (research report §Model2Vec: "BLAS-backed operations can reorder float operations") applies here as in Spec A, but the practical consequence is reduced: with only 6–10 comparisons and wide inter-centroid margins, a float32 rounding difference of ~1e-6 is far less likely to flip the category classification than it is to flip a fine-grained entry ranking.

**Stronger determinism-in-practice** is a structural property of the coarse granularity: when two category centroids differ in cosine by 0.13 (as in the worked example), a variation of 1e-6 cannot flip the classification. Spec A's per-entry cosine comparison operates with margins that may be much smaller (research report: "100-entry catalog of short technical phrases ... this gap may matter").

### 9.3 Stage 2 and Stage 3 determinism

Stage 2 (candidate restriction from category names) is a pure set membership operation — trivially deterministic. Stage 3 (lexical scoring within the surviving set) is the existing deterministic lexical scorer — no change to its determinism profile.

### 9.4 Overall profile

This design's determinism profile is stronger in practice than Spec A's, because the only probabilistic stage (the cosine comparison) operates at coarse granularity with wider margins. The formal guarantees are the same (same-machine, same-binary, Model2Vec), but the practical risk of a determinism-breaking float flip is lower.

---

## 10. Failure Modes & Recovery

### 10.1 Misclassification: correct agent pruned from candidate set

**Failure:** Stage 1 assigns the prompt to the wrong category (or to a category that does not include the correct agent). Stage 2 removes the correct agent from the candidate set. Stage 3 cannot route to it regardless of keyword signal.

**Severity:** High. This is the design's central failure mode. Unlike Spec A's wrong-confident failure (§10.1 there), this failure is invisible: the decision ladder produces a confident `delegate` to a wrong-but-in-category agent with no signal that the correct agent was pruned. There is no lexical keyword that can rescue it once it is out of the candidate pool.

**Recovery:**
1. **Top-K with K ≥ 2** (mandatory): selecting the top-2 categories instead of top-1 substantially reduces misclassification risk by including the second-most-likely intent. Multi-intent prompts also motivate K > 1. The worked example uses K = 2.
2. **Confidence gating** (mandatory): if the top-1 margin is below `_CAT_MARGIN_MIN`, Stage 2 passes the full agent list — no pruning. This ensures low-confidence classifications degrade gracefully to the existing lexical-only behavior rather than hard-pruning on uncertain signal.
3. **Category tagging review**: if an agent spans multiple intent categories (see §10.4), it must be tagged with all of them. Missing a category tag is a silent misclassification risk.

### 10.2 Low-confidence fallback frequency

**Failure:** The classifier frequently falls below `_CAT_MARGIN_MIN`, causing Stage 2 to pass the full candidate set. The classifier adds latency without providing a benefit.

**Severity:** Medium. The design degrades to lexical-only behavior, not wrong routing — so it is safe but not useful. Requirement 6 is not addressed for low-confidence dispatches.

**Recovery:** Calibrate `_CAT_MARGIN_MIN` against a labelled corpus (research report §Open Questions, question 2). If low-confidence fallback is frequent on real dispatch prompts, it indicates the category taxonomy needs refinement (fewer categories, better-separated centroids, or more representative utterances per category). The fallback frequency is a directly measurable metric from dispatch logs once deployed.

### 10.3 Category taxonomy design errors

**Failure:** The intent taxonomy is designed without matching the actual agent roster. Some agents span multiple categories or do not fit any category cleanly. The taxonomy is the load-bearing artifact of this design; errors here propagate to all dispatches.

**Severity:** High. An incorrectly designed taxonomy can cause systematic misclassification across a class of prompts. For example, if `code-writer` is tagged only `implementation` but is also the correct agent for `refactoring` tasks, all prompts classified as `refactoring` that should route to `code-writer` will miss it.

**Recovery:** The taxonomy must be designed against the full agent roster with explicit attention to multi-category agents. A review step in the catalog authoring workflow (analogous to the current catalog audit in `audit_catalog.py`) should verify that every agent has at least one category tag and that no category is empty. Agents that span categories must be tagged with all applicable categories rather than one.

### 10.4 Agents that span intent categories

**Failure:** Several agents legitimately handle multiple intent types. `code-writer` performs both `implementation` (new features) and `refactoring` (restructuring existing code). An auditor agent performs both `conformance` (checking against rules) and `investigation` (examining the codebase). If these agents are tagged with only one category, they are invisible to prompts classified under their other categories.

**Severity:** Medium to High (agent-specific). The severity depends on how common the cross-category prompts are and whether other correctly-tagged agents can substitute.

**Recovery:** Multi-category tagging is the designated mitigation. The catalog schema must support a `categories: [cat1, cat2]` list, not a single-value field. The Stage 2 union operation naturally handles multi-category agents: an agent tagged `[investigation, review]` appears in the candidate set whenever either category survives Stage 1. The burden is on catalog authors to tag correctly.

### 10.5 Category granularity mismatch

**Failure:** The ~6–10 category taxonomy is either too coarse (multiple semantically different agent groups collapse into one category, and Stage 3 lexical refinement cannot distinguish them) or too fine (adjacent categories are nearly parallel in embedding space, margin is always low, and Stage 2 frequently falls back).

**Severity:** Medium. Too coarse reduces the benefit of the pre-filter (large surviving candidate sets). Too fine increases misclassification and fallback frequency.

**Recovery:** The taxonomy must be calibrated against a sample of real dispatch prompts before committing. The research report §Open Questions question 1 identifies this calibration as a prerequisite: "the semantic layer's quality ceiling is determined by whether the utterance examples adequately cover the variation space." Category count can be tuned iteratively using the `_CAT_MARGIN_MIN` fallback rate as a signal.

### 10.6 Skills excluded from category filtering: cross-cutting coverage gap

**Failure:** Skills are never category-filtered by design. A cross-cutting skill like `python` is matched lexically against the full skill catalog regardless of the intent category. This is correct for truly cross-cutting skills, but some skills may be intent-specific. A `security-review` skill might be spuriously attached when the intent is `investigation` with code paths.

**Severity:** Low. Skill attachment uses `_SKILL_MIN` (0.5) as a threshold — spurious skill attachment requires the skill to have matching keywords in the dispatch. Unrelated skills rarely score above 0.5 without relevant keywords.

**Recovery:** A future iteration could add optional category tags to skills, enabling category-filtered skill attachment. This is deliberately out of scope for the initial design; skills are treated as intent-independent to avoid the complexity of skill taxonomy design alongside agent taxonomy design.

### 10.7 Encoder unavailability

**Failure:** Model2Vec model file is missing. Stage 1 cannot run.

**Severity:** Low for routing correctness; degrades to lexical-only. Same as Spec A §10.5.

**Recovery:** Graceful degradation to the lexical-only path (Stage 2 passes the full agent list; Stage 3 runs on the full catalog). Output carries `disposition_source: "lexical_only"` and a warning. The 30 MB model file should be bundled with the package or downloaded at catalog-load time.

---

## 11. Authoring & Maintenance Burden

### 11.1 The intent taxonomy: the load-bearing authoring artifact

The intent taxonomy — a list of ~6–10 category names, their definitions, and their representative example utterances — is the single highest-stakes authoring artifact in this design. Its correctness determines the accuracy of Stage 1. Errors propagate to all dispatches.

The taxonomy must be designed against the actual agent roster, with explicit handling of multi-category agents (§10.4). Suggested initial categories (illustrative; requires validation against the full catalog):

| Category | Agent examples |
|---|---|
| `investigation` | systematic-debugging, investigator |
| `implementation` | code-writer |
| `review` | code-reviewer, security-review |
| `conformance` | auditor (if present), linter |
| `planning` | project-planner |
| `infra` | azure, bicep |
| `ops` | git, CI/CD related |
| `documentation` | doc-writer |

Multi-category agents (e.g., `code-writer` also handles refactoring → potentially both `implementation` and `refactoring`) complicate this taxonomy and must be resolved before the taxonomy is finalized.

### 11.2 Example utterances per category

Each category requires a set of representative example utterances — short natural-language phrasings of the general intent type. With ~8–10 categories, the total corpus is approximately **80–100 utterances** (roughly 8–10 utterances per category). This is the full authoring cost for this design.

By contrast, Spec A requires utterances per catalog entry (~100 entries × 5–10 utterances = 500–1,000 strings). This design's authoring burden is approximately one order of magnitude smaller, because utterances are authored at category granularity rather than entry granularity.

The research report notes this pattern as prior art: centroid-classifier routers (NadirClaw, semroute) do the classification half at category granularity. (Source: research report §RQ2, citing https://github.com/NadirRouter/NadirClaw and https://github.com/HansalShah007/semroute, fetched 2026-06-07.) What is novel here is using the classification output as a pre-filter for a lexical second stage rather than as the terminal router.

### 11.3 Per-agent category tags

Each agent entry in the catalog gains a `categories: [...]` field listing its applicable intent categories. This is a low-burden annotation: one or a few category names per entry. The catalog auditor should warn on agents with zero category tags.

Unlike Spec A's utterance authoring (which requires domain knowledge about what prompts each entry handles), category tagging only requires knowing which category each agent belongs to — a simpler judgment.

### 11.4 Maintenance triggers

- **Adding a new catalog entry:** must include category tags. Auditor warns if absent. No new utterances required unless the entry introduces a new category.
- **Adding a new category:** requires authoring ~8–10 utterances, computing a new centroid, and tagging relevant agents. The centroid index must be regenerated.
- **Renaming a category:** requires updating all agent tags and regenerating the centroid index. A category rename affects all agents tagged with it — the blast radius is wider than a single entry change.
- **Encoder model upgrade:** all category centroids must be regenerated. Same cost as Spec A, but the quantity is ~10 centroids rather than ~100.
- **Semantic drift:** if real dispatch prompts shift in vocabulary, category centroids may need re-centering with updated utterances. The fallback rate (`low-confidence classification frequency`) is the primary diagnostic signal for drift.

---

## 12. Open Questions

1. **Taxonomy design and category count.** How many categories, and what are they? The worked example uses 8 categories (illustrative). The correct number is determined by the agent roster shape — too few categories (e.g., 3) and Stage 2 doesn't prune enough to help; too many (e.g., 20) and adjacent categories confuse the classifier. This is a design decision that requires iterating against real dispatch prompts and the full catalog, not derivable from this spec alone.

2. **K value for top-K selection.** Using K = 2 (top-2 categories) is safer than K = 1 (hard top-1) because multi-intent prompts (`"refactor the module and add tests"` spans `implementation` and potentially `review`) motivate including the second category. But K = 2 reduces the pre-filter's pruning effectiveness. What is the right K, and should K be adaptive (based on the margin) rather than fixed?

3. **`_CAT_MARGIN_MIN` calibration.** The confidence gate threshold determines how often Stage 2 falls back to the full catalog. Too high: frequent fallback, little benefit. Too low: hard pruning on uncertain signal, misclassification risk. This must be calibrated against a labelled corpus. The research report §Open Questions question 2 identifies this as a prerequisite for any semantic pilot.

4. **Multi-category agents: tagging burden and category design tension.** Agents like `code-writer` that span multiple intent types require explicit multi-category tags. How many agents in the actual catalog span categories? If the majority of agents are multi-category, the Stage 2 pre-filter prunes less aggressively, reducing the design's benefit. A catalog survey before committing to this design would quantify the actual pruning rate.

5. **Category centroid initialization.** Who authors the initial utterances per category, and how many are sufficient for a stable centroid? The centroid is an average of utterance embeddings — a small utterance count (~3–5) may produce a centroid that is poorly representative of the full category space, leading to misclassification on uncommon phrasings. The semantic-router framework (research report §3) uses multiple utterances per route precisely to stabilize the centroid; the same applies here.

6. **`potion-base-8M` vs `potion-code-16M`.** The research report §Open Questions question 6 recommends evaluating both on 50 labelled prompts. For category-level classification (coarser than per-entry retrieval), `potion-base-8M`'s general English understanding may be sufficient — the categories are broad intent types phrased in natural language, not code-specific. `potion-code-16M`'s code-domain specialization may be more valuable in Spec A's fine-grained entry retrieval than in this design's coarse classification step.

---

## 13. Sources

All sources are from `docs/research/2026-06-07-semantic-routing.md` (fetched 2026-06-07) unless otherwise noted. Source URLs and fetch dates are carried from the research report verbatim.

| Source | URL | Fetched |
|---|---|---|
| MinishLab/model2vec repo | https://github.com/MinishLab/model2vec | 2026-06-07 |
| model2vec PyPI | https://pypi.org/project/model2vec/ | 2026-06-07 |
| minishlab/potion-base-8M model card | https://huggingface.co/minishlab/potion-base-8M | 2026-06-07 |
| minishlab/potion-code-16M model card | https://huggingface.co/minishlab/potion-code-16M | 2026-06-07 |
| NadirRouter/NadirClaw | https://github.com/NadirRouter/NadirClaw | 2026-06-07 |
| HansalShah007/semroute | https://github.com/HansalShah007/semroute | 2026-06-07 |
| aurelio-labs/semantic-router repo | https://github.com/aurelio-labs/semantic-router | 2026-06-07 |
| numpy np.dot precision issue #661 | https://github.com/numpy/numpy/issues/661 | 2026-06-07 |
| Floating-point determinism (Bruce Dawson) | https://randomascii.wordpress.com/2013/07/16/floating-point-determinism/ | 2026-06-07 |
| Explainable Model Routing (arXiv:2604.03527) | https://arxiv.org/pdf/2604.03527 | 2026-06-07 |
| Toward Super Agent System with Hybrid AI Routers (arXiv:2504.10519) | https://arxiv.org/pdf/2504.10519 | 2026-06-07 |
| On Reproducibility Limitations of RAG Systems (arXiv:2509.18869) | https://arxiv.org/pdf/2509.18869 | 2026-06-07 |
| src/claude_wayfinder/match/_match.py | (this repo, read 2026-06-07) | 2026-06-07 |
| src/claude_wayfinder/match/_decide.py | (this repo, read 2026-06-07) | 2026-06-07 |
| src/claude_wayfinder/match/_types.py | (this repo, read 2026-06-07) | 2026-06-07 |
| docs/research/2026-06-07-semantic-routing.md | (this repo, read 2026-06-07) | 2026-06-07 |

**Unverified claims in this spec:**

- `unverified:` potion-base-8M has 256 embedding dimensions — inferred from documentation patterns; HF model card did not explicitly state this in fetched content. Carried from the research report's unverified claim.
- `unverified:` Illustrative cosine scores in §4.2 (investigation: 0.71, review: 0.58, etc.) and lexical scores (code-reviewer ≈ 0.75) are hypothetical examples constructed to illustrate the pipeline flow; real scores require calibration against actual category utterances and catalog trigger definitions.
- `unverified:` NadirClaw achieves ~10ms latency with all-MiniLM-L6-v2 centroid routing — cited from the research report's unverified claim (source: search result description of the repo; the repo itself was not directly fetched and validated in the research session).
- `unverified:` The illustrative intent taxonomy table in §11.1 is constructed by the spec author based on visible agent names in the codebase; it has not been validated against the full catalog content or the actual dispatch-log distribution.
