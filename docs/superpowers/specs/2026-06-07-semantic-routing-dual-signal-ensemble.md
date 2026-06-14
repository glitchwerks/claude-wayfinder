---
title: Semantic routing — Spec A of 2 — Parallel Dual-Signal Ensemble
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
sibling: docs/superpowers/specs/2026-06-07-semantic-routing-classifier-prefilter.md
skills_relevant:
  - dispatch-authoring
  - project-review
---

# Semantic routing — Spec A of 2 — Parallel Dual-Signal Ensemble

Tracking issue: [#325](https://github.com/glitchwerks/claude-wayfinder/issues/325)
Sibling spec: [Spec B — Coarse-to-Fine Semantic Classifier Pre-Filter](2026-06-07-semantic-routing-classifier-prefilter.md)
Author: doc-writer (2026-06-07)
Status: **draft — independent comparison pending**

---

## 1. Summary

This is **Spec A of 2** for issue [#325](https://github.com/glitchwerks/claude-wayfinder/issues/325). It describes a Parallel Dual-Signal Ensemble architecture for adding semantic routing to claude-wayfinder. In this design both the existing lexical scorer and a new semantic encoder run on every dispatch, each producing a ranked list across the full ~100-entry agent catalog. Agreement between the two signals is treated as high confidence; disagreement is surfaced explicitly as an `ambiguous` output for the router to adjudicate. The design is deliberately agnostic about which signal is "primary" — their outputs are peers.

The sibling spec ([Spec B — Coarse-to-Fine Semantic Classifier Pre-Filter](2026-06-07-semantic-routing-classifier-prefilter.md)) takes a different structural approach: a semantic stage filters the candidate set by intent category, and the lexical scorer refines within that restricted set. These two specs are inputs to an independent comparison; this document does not advocate for a winner.

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

The ensemble runs two independent scorers in parallel on every dispatch:

- **Lexical scorer** — the existing token-set intersection engine (`src/claude_wayfinder/match/_match.py`). Produces a float score per catalog entry using weighted keywords `{0.25, 0.5, 1.0}`, AND-conjunctive keyword groups, path globs, and tool mentions. Reads from `Features` extracted from the dispatch context.
- **Semantic scorer** — a new component. Embeds the prompt text using Model2Vec `potion-base-8M` and computes cosine similarity against per-entry embedding centroids precomputed at catalog-build time. Produces a float score per catalog entry in [0.0, 1.0].

Both scorers produce a ranking over the same catalog. The decision module compares their top picks:

- **Agreement** (same top entry): the concurrence is a high-confidence signal. The confidence reported in the output is the average (or minimum) of the two scores.
- **Disagreement** (different top entries): the matcher emits `ambiguous` or `advisory` with both candidates surfaced, and delegates conflict resolution to the router. The router has access to the full prompt and can apply judgment the matcher cannot.

Requirement 6 is addressed by recalibrating the lexical ladder: short-circuit paths (explicit agent mention, command prefix, high-specificity path globs) retain their `1.0` score and strong `delegate` behavior; soft single-keyword matches — those that currently can alone produce a confident `delegate` — are demoted so they require semantic corroboration before reaching the `_DELEGATE_THRESHOLD`.

The two signals operate at the **same granularity**: both score every catalog entry individually. This is the defining structural property of the ensemble and distinguishes it from Spec B, where the two stages operate at different granularities (category vs. entry).

---

## 4. Pipeline / Control Flow

### 4.1 Step-by-step

1. **Context parsing** — unchanged. `build_features()` in `_match.py` produces a `Features` object from the dispatch context JSON.
2. **Lexical scoring** — unchanged call to `score_entries()`. Returns `scored_agents` and `scored_skills` sorted by lexical score descending.
3. **Semantic encoding** — the prompt text (`features.keywords` reconstructed, or the raw `task_description`) is passed to a loaded Model2Vec `potion-base-8M` instance. Produces a 256-dimensional float32 vector. Estimated latency: sub-millisecond on the same machine (research report §Candidate Evaluation Matrix, fetched 2026-06-07, https://github.com/MinishLab/model2vec).
4. **Cosine comparison** — the prompt vector is compared against each catalog entry's precomputed centroid vector via `np.dot(prompt_vec, centroid_matrix.T) / norms`. Returns a float score per entry.
5. **Agreement check** — identify `lex_top` (lexical top agent) and `sem_top` (semantic top agent by cosine). If `lex_top.name == sem_top.name`, signals agree. If they differ, signals disagree.
6. **Decision composition** — see §6 for the full ladder. Disagreement emits `ambiguous`/`advisory`; agreement continues to the existing thresholds against the recalibrated lexical score.
7. **Rationale construction** — includes both matched keywords (lexical) and nearest example utterance (semantic). See §8.

### 4.2 Worked trace — shared example prompt

**Input prompt:** `"review the python code to see why my answer doesn't return correctly"`

**Step 1 — Feature extraction:**
- Stemmed keywords (Porter2): `{review, python, code, answer, return, correct, ...}` (exact stems depend on `_stem.py`; verified structure in `src/claude_wayfinder/match/_match.py:93-120`)
- No file paths, no tool mentions, no command prefix.

**Step 2 — Lexical scoring (selected entries):**
- `code-reviewer`: keyword `review` (weight 1.0 → contributes 0.5), keyword `code` (weight 0.5 → contributes 0.25). Lexical score ≈ 0.75.
- `code-writer`: keyword `code` (weight 1.0 → contributes 0.5), keyword `python` (weight 0.5 → contributes 0.25). Lexical score ≈ 0.75.
- `python` (skill): keyword `python` (weight 1.0 → contributes 0.5). Lexical score ≈ 0.5.
- `systematic-debugging` / investigator: keywords `return`, `correct` may match partially. Lexical score ≈ 0.25–0.5 depending on catalog.

Lexical `lex_top` candidates: `code-reviewer` and `code-writer` tied or near-tied. Both ≈ 0.75 but gap < `_DELEGATE_GAP` (0.2). The existing ladder would emit `advisory`. Under the recalibrated ladder (§7), neither soft-keyword cluster reaches `_DELEGATE_THRESHOLD` (0.85) without semantic corroboration.

**Step 3 — Semantic encoding:**
Model2Vec `potion-base-8M` encodes `"review the python code to see why my answer doesn't return correctly"` → 256-dim float32 vector `q`.

**Step 4 — Cosine comparison:**
Precomputed centroids for each catalog entry. The prompt's semantic content — diagnosing an incorrect result — clusters near investigation/debugging entries in embedding space. Hypothetical cosine scores (illustrative; exact values require calibration against real utterances):
- `systematic-debugging`: cosine ≈ 0.72
- `code-reviewer`: cosine ≈ 0.54
- `code-writer`: cosine ≈ 0.48

Semantic `sem_top`: `systematic-debugging`.

**Step 5 — Agreement check:**
`lex_top` = `code-reviewer` (or `code-writer`, tied). `sem_top` = `systematic-debugging`. **Signals disagree.**

**Step 6 — Decision output:**
The matcher emits an `ambiguous` decision surfacing both candidates:
```json
{
  "decision": "ambiguous",
  "candidates": [
    {
      "agent": "systematic-debugging",
      "signal": "semantic",
      "cosine": 0.72,
      "nearest_utterance": "find out why my function returns the wrong value"
    },
    {
      "agent": "code-reviewer",
      "signal": "lexical",
      "score": 0.75,
      "rationale": "matched keywords: review, code"
    }
  ],
  "confidence": 0.0,
  "rationale": "Lexical and semantic signals disagree; router arbitration required.",
  "disposition_source": "ensemble_disagreement"
}
```

The router, which has the full prompt, redirects to `systematic-debugging` or invokes `ask_user` as appropriate. The matcher's job — detecting the conflict — is complete.

---

## 5. Encoder & Index Shape

### 5.1 Encoder

**Model:** Model2Vec `potion-base-8M` (MIT license). Inference: token lookup in a fixed float32 weight matrix + `np.mean(rows, axis=0)`. No neural forward pass at query time. Dependency: `numpy` only (no PyTorch, no ONNX runtime). Source: https://github.com/MinishLab/model2vec (fetched 2026-06-07); PyPI: https://pypi.org/project/model2vec/ (fetched 2026-06-07).

Model dimensions: `unverified:` 256 — inferred from documentation patterns; the HF model card for `potion-base-8M` did not explicitly state the dimension count in the fetched content. The `potion-code-16M` variant is confirmed 256-dim (source: https://huggingface.co/minishlab/potion-code-16M, fetched 2026-06-07). Disk footprint: ~30 MB.

MTEB classification score: 70.34 for `potion-base-8M`. General MTEB avg: 51.32, approximately 92% of MiniLM quality. (Source: https://huggingface.co/minishlab/potion-base-8M, fetched 2026-06-07.)

### 5.2 What gets embedded

At **catalog-build time** (in `build_catalog/_main.py` or `_process.py`):
- For each catalog entry, a set of **example utterances** is embedded. These are short natural-language phrasings of the tasks that entry handles (e.g., for `systematic-debugging`: `"find why my function returns wrong output"`, `"debug a test failure I don't understand"`, etc.).
- The per-utterance embeddings are averaged into a single **centroid vector** per entry. This is the O(1) lookup pattern documented in `semroute` (https://github.com/HansalShah007/semroute, fetched 2026-06-07) and described in the research report §5.

At **dispatch time**:
- The raw `task_description` string (or a normalized form) is embedded once.
- One cosine similarity is computed per catalog entry against its stored centroid.

### 5.3 Per-entry centroids: all entries including fine-grained ones

In this design, every catalog entry — all ~100 agents and skills — gets its own centroid vector. This is the critical structural difference from Spec B, which only embeds ~6–10 category centroids.

The research report flags that fine-grained retrieval is where Model2Vec's ~8% MTEB gap below MiniLM bites hardest: "for a 100-entry catalog of short technical phrases, this gap may matter for low-frequency synonyms." (Source: research report §Model2Vec entry, fetched 2026-06-07.) With ~100 entries sharing a 256-dimensional space, inter-centroid distances can be narrow, and the cosine margin between adjacent entries may be small.

### 5.4 On-disk storage

Index file (alongside the generated catalog JSON):
- A `.npz` file containing: `centroids` array (shape `[N_entries, 256]`, float32), `entry_names` array (string), `model_id` string, `model_version` string, `utterances` dict (entry name → list of strings, for nearest-utterance auditability).
- Approximate size: 100 entries × 256 dims × 4 bytes = ~102 KB. Trivially small.
- Staleness: the file carries `model_id` + `model_version`. The catalog generator must re-embed when either the catalog changes or the encoder model changes. A hash of the catalog content + model identifier should gate regeneration. (Pattern observed in semantic-router's `_get_hash()` / `_write_hash()` implementation, https://github.com/aurelio-labs/semantic-router, fetched 2026-06-07.)

---

## 6. Decision & Scoring Contract

### 6.1 Mapping to the 7-decision ladder

The existing ladder in `_decide.py:154-287` is preserved. The ensemble adds a pre-step and modifies one threshold constraint.

**Pre-step (new): ensemble agreement gate.**
Before the existing ladder runs, the matcher checks whether lexical and semantic top picks agree.

| Signal state | Action |
|---|---|
| Both agree on top entry; recalibrated lexical ≥ `_DELEGATE_THRESHOLD` (0.85); gap ≥ `_DELEGATE_GAP` (0.2) | Proceed to `delegate` via existing ladder |
| Both agree on top entry; lexical < 0.85 or gap < 0.2 | Proceed to `advisory` via existing ladder (semantic agreement as a note in rationale) |
| Disagree; both top entries score ≥ `_ADVISORY_MIN` (0.5) | Emit `ambiguous` (new sub-variant of `advisory`) with both candidates |
| Disagree; one or both top entries score < 0.5 | Proceed to `self_handle_unaided` (disagreement on weak signals is noise) |
| Semantic encoder unavailable (model not loaded) | Degrade gracefully to lexical-only path; log absence |

**The `ambiguous` state** is not a new enum value — it is surfaced as an `advisory` decision with `disposition_source: "ensemble_disagreement"` and a `candidates[]` array carrying both contenders. This preserves backward compatibility with consumers that parse the 7-decision enum. Router implementors who want to act on disagreement check `disposition_source`.

### 6.2 Score reconciliation

The two signals are **not blended into a single float**. Blending would obscure which signal drove the decision and would require calibrating a blend weight empirically (a labelled corpus dependency noted in the research report §Open Questions, question 2). Instead:

- Lexical score drives threshold comparisons for the agree-path (using the recalibrated ladder, §7).
- Semantic score provides corroboration (agree) or contradiction (disagree).
- Both scores are surfaced in the decision output for auditability.

A future iteration could introduce a weighted blend for the agree-path (analogous to Reciprocal Rank Fusion in hybrid RAG, mentioned in the research report §RQ3), but that introduces the calibration dependency and is deferred.

### 6.3 Skill attachment

Skill scoring is unchanged. Skills are cross-cutting and intent-independent in many cases. The skill layer runs against the full unfiltered candidate set regardless of ensemble outcome. The ensemble gate applies only to agent routing.

---

## 7. Keyword-Model Changes Required

Requirement 6 states: a single soft keyword must not alone produce a confident `delegate`. The current scorer allows this: a single weight-1.0 keyword contributes `_KEYWORD_MULTIPLIER * 1.0 = 0.5`, and two such keywords reach `1.0`, clearing `_DELEGATE_THRESHOLD` (0.85) easily.

### 7.1 Proposed recalibration

The recalibration preserves high-precision signals and demotes soft single-keyword paths:

1. **Retain `1.0` short-circuits:** explicit agent mention, command prefix, high-specificity path globs (e.g., `agents/**/*.md`) remain `1.0` and continue to produce confident `delegate` without semantic input. These are deterministic signals with negligible false-positive rate.
2. **Introduce a semantic-corroboration gate for keyword-driven paths:** a keyword-only path (no path globs, no tool mentions, no command prefix) reaching `_DELEGATE_THRESHOLD` (0.85) is held at `advisory` unless the semantic signal also places the same agent at the top. Mechanically: in `_decide.py:212`, the `delegate` branch adds a condition `or signals_agree` before emitting `delegate` for keyword-only matches above threshold.
3. **Do not change `_KEYWORD_MULTIPLIER` or the weight ladder.** Changing multipliers would affect the calibrated threshold values (0.85 / 0.5 / 0.2) and invalidate the existing scoring contracts without a full re-calibration. The gate in step 2 is additive — it adds a condition, not changes scores.

### 7.2 What stays the same

- All path-glob, tool-mention, and group-based scoring is unchanged.
- `score()` in `_match.py` is unchanged.
- The `_DELEGATE_THRESHOLD`, `_DELEGATE_GAP`, and `_ADVISORY_MIN` constants are unchanged.
- Skill scoring and `_SKILL_MIN` are unchanged.
- The feature density guard (`_MIN_FEATURE_DENSITY = 2`) is unchanged.

### 7.3 Blast radius

The semantic-corroboration gate affects only dispatches where: (a) no short-circuit signal fires, (b) the keyword-only score reaches ≥ 0.85, and (c) the semantic signal disagrees. This is a narrow subset of all dispatches. Dispatches that the lexical scorer currently handles confidently and correctly (short-circuit paths, strong multi-signal matches) are unaffected.

---

## 8. Auditability Story

The design must reconcile the human-legible keyword rationale (current gold standard per requirement 4) with a cosine score, which is weaker on its own.

### 8.1 Agree-path rationale

When signals agree, the rationale reads:

> Matched keywords: `review`, `code`; semantic signal confirms: nearest utterance `"find why my function returns the wrong value"` (cosine 0.71). Routing to `code-reviewer` with high confidence (lexical 0.75, semantic 0.71).

This extends the existing `_rationale_for()` format in `_decide.py:295-350` with one semantic segment. The nearest utterance is the specific example string whose embedding was closest to the prompt's embedding — retrieved by `argmax` over per-utterance cosine similarities after scoring.

### 8.2 Disagree-path rationale

When signals disagree, the output surfaces both candidates' reasoning:

> Lexical signal: matched keywords: `review`, `code` → `code-reviewer` (score 0.75).
> Semantic signal: nearest utterance `"find why my function returns the wrong value"` → `systematic-debugging` (cosine 0.72).
> Signals disagree. Router arbitration required.

Each candidate's rationale uses the signal's own evidence: matched keyword terms for lexical; nearest example utterance for semantic. Neither requires the reader to interpret a raw cosine number in isolation.

### 8.3 Tension and mitigations

A cosine score of 0.72 is not as self-explanatory as "matched keyword: review." Mitigations:

1. **Nearest-utterance attribution** (described in research report §RQ4): show the closest example utterance, not just the cosine number. "Nearest utterance: 'find why my function returns the wrong value'" anchors the score to a concrete case the catalog author can recognize.
2. **Score banding** (research report §RQ4, pattern 4): annotate the cosine with a band (`high: ≥ 0.8`, `moderate: 0.6–0.8`, `low: < 0.6`) in the output. Readers see `confidence_band: "moderate"` rather than a raw float.
3. **Threshold margin surfacing:** include the delta between the top-2 semantic candidates in the output. A narrow margin (e.g., `delta: 0.03`) signals low semantic confidence to the router.

These mitigations do not eliminate the tension between a keyword match (directly verifiable) and a cosine match (requires trust in the encoder). They reduce it to a level appropriate for a secondary signal.

---

## 9. Determinism Profile

Source for all claims: `docs/research/2026-06-07-semantic-routing.md` §Determinism Deep-Dive (fetched 2026-06-07).

### 9.1 Encoder determinism

Model2Vec `potion-base-8M` inference is: (1) tokenize → token IDs; (2) row-index lookups in a fixed float32 weight matrix; (3) `np.mean(rows, axis=0)` over ≤15 rows for a short prompt.

Step 2 is pure memory reads — no floating-point computation. Step 3 is a mean over a small, fixed-size array. On the same machine with the same numpy version, numpy's sequential pairwise summation over a 15-element float32 array is deterministic because the reduction tree is a fixed depth with no opportunity for threading reorder at that array size. (Source: https://github.com/numpy/numpy/issues/661, fetched 2026-06-07; https://randomascii.wordpress.com/2013/07/16/floating-point-determinism/, fetched 2026-06-07.)

**Verdict (from research report):** "the strongest determinism profile of all surveyed encoders. On the same machine, same numpy version, same OS: run-to-run determinism is a very strong practical guarantee." Cross-platform determinism (Windows vs macOS vs Linux) is not guaranteed due to float32 FMA rounding differences, but the research report notes this should not affect routing decisions above the margin threshold.

### 9.2 Cosine similarity determinism

`np.dot(prompt_vec, centroid_matrix.T) / norms` for a 256-dim vector against a 100 × 256 matrix involves BLAS-backed 2D matrix multiply. The research report flags that `np.dot` for 2D matmul can be non-deterministic across different BLAS builds (MKL vs OpenBLAS) because they reorder float operations for SIMD efficiency. (Source: research report §Model2Vec, citing https://github.com/numpy/numpy/issues/661.)

Mitigation: normalize both the prompt vector and the centroid matrix at precomputation time and store normalized centroids on disk. Then the cosine similarity reduces to a dot product of unit vectors: `np.dot(prompt_unit, centroid_matrix_normalized.T)`. This still uses BLAS 2D matmul, so the BLAS caveat applies. In practice, the variance is ~1e-6 — unlikely to change a routing decision given reasonable centroid margins, but this is the primary determinism risk for the cosine stage.

### 9.3 Decision determinism

Given deterministic embeddings and stable cosine scores, the agreement check (comparing top-1 entry names) is a pure string equality — trivially deterministic. The threshold comparisons in the decision ladder are pure arithmetic on fixed constants. Decision determinism holds as long as encoder and cosine determinism hold.

### 9.4 Per-run vs per-catalog-rebuild

Determinism in this spec means: same prompt + same catalog + same encoder version → same decision on the same machine. When the catalog is rebuilt (utterances added or changed) or the encoder model is updated, the embedding index changes and decisions can change. This is expected and analogous to the lexical scorer's behavior when keyword triggers are edited.

---

## 10. Failure Modes & Recovery

### 10.1 Agreement on the wrong answer

**Failure:** Both signals agree on the same (wrong) agent. The ensemble has no internal disagreement signal — the router's own judgment is the only backstop.

**Severity:** High. This is the hardest failure for the design to recover from internally. The lexical signal may be wrong due to surface-string overlap; the semantic signal may be wrong due to the ~8% MTEB quality gap or because the utterance corpus did not cover this prompt variation.

**Recovery:** The router can apply its own judgment after receiving a confident `delegate` that turns out wrong. But the matcher has no mechanism to flag "agreed but unsure." One partial mitigation: surface the margin between the top-2 semantic candidates. A narrow semantic margin (both signals agree but semantic 1st and 2nd are close) is a softer signal of uncertainty even on the agree path. This does not prevent wrong-confident routing; it only gives the router more information.

### 10.2 Fine-grained catalog, narrow semantic margins

**Failure:** With ~100 catalog entries sharing a 256-dimensional space, the cosine margins between adjacent fine-grained entries can be small. The research report states: "for a 100-entry catalog of short technical phrases, this gap may matter for low-frequency synonyms." Narrow margins increase the probability that small encoding variations (e.g., cross-platform float32 differences) flip the semantic top pick.

**Severity:** Medium. Small margin flips cause spurious disagreements rather than confident wrong routes. The design's response is `ambiguous` → router arbitration, which is a safe failure mode but increases router load.

**Recovery:** Increase the margin threshold for semantic "confident" classification. If the top-2 cosine scores differ by less than some `_SEM_MARGIN_MIN` (e.g., 0.05), treat the semantic signal as inconclusive rather than contributing a clean top pick. This reduces false disagreements at the cost of reduced semantic coverage.

### 10.3 Lexical-strong, semantically-ambiguous prompts

**Failure:** The lexical signal produces a strong, high-confidence match. The semantic signal is noisy (low margins, multiple near-equal candidates). The gate in §7.1 holds the lexical result at `advisory` waiting for semantic corroboration, but the semantic signal cannot provide clean corroboration — emitting `ambiguous` unnecessarily.

**Severity:** Medium. These are cases the existing lexical-only matcher handles correctly today. The ensemble degrades them from `delegate` to `ambiguous`, increasing router load without benefit.

**Recovery:** The semantic corroboration gate (§7.1, step 2) should apply only when the semantic signal is itself confident (top-2 margin ≥ `_SEM_MARGIN_MIN`). If the semantic signal is inconclusive, the lexical result is allowed to proceed through the existing ladder unimpeded. This requires defining "semantic confidence" as a precondition for the gate, not just disagreement.

### 10.4 Utterance corpus gaps

**Failure:** A catalog entry has insufficient or unrepresentative example utterances. Its centroid is a poor embedding of the concept it represents. Prompts that should route to this entry instead route semantically to the nearest neighbor with a better corpus.

**Severity:** Medium. This is an authoring failure, not a design failure. But it produces confident (wrong) semantic routing with no visible internal signal.

**Recovery:** Nearest-utterance attribution (§8) helps diagnose this post-hoc: if the nearest utterance shown in the rationale is obviously unrelated to the prompt, a catalog author knows to add better utterances. A corpus quality metric (average intra-entry centroid distance, measured at catalog-build time) can flag entries whose utterances are scattered in embedding space.

### 10.5 Encoder model unavailability

**Failure:** The Model2Vec model file is missing (not downloaded, deleted, or corrupted). The semantic scorer cannot run.

**Severity:** Low for routing correctness; the system degrades to lexical-only. High for requirement 1 (locally runnable) if the model cannot be included in the package.

**Recovery:** The semantic stage degrades gracefully to the lexical-only path. The output carries `disposition_source: "lexical_only"` and a warning in the rationale. This is the intended fallback path. The 30 MB model file should be bundled with the package or downloaded at catalog-load time with a progress indicator.

### 10.6 Latency budget exceeded

**Failure:** On some machines, Model2Vec encoding or the 100-entry cosine comparison takes longer than the ~50ms tolerance.

**Severity:** Low probability for Model2Vec (estimated sub-millisecond; research report §Candidate Evaluation Matrix). Higher for the cosine comparison against 100 normalized 256-dim vectors (trivial BLAS call — likely < 1ms even on slow hardware). Total semantic stage estimated < 2ms on modern CPU. However, no official single-sentence benchmark for 5–15 token inputs is published for `potion-base-8M`. (Source: research report §Model2Vec entry: "no sub-10-word latency benchmark is published.")

**Recovery:** If latency proves problematic in practice, the semantic stage can be made opt-in per dispatch (e.g., only when lexical produces `advisory` or `self_handle_unaided`). This would convert the design from "always parallel" to a conditional parallel, changing its structural character but not its encoder or decision contract.

### 10.7 BLAS non-determinism in cosine comparison

**Failure:** On some platforms or BLAS configurations, `np.dot(prompt_vec, centroid_matrix.T)` produces marginally different results run-to-run, flipping the semantic top pick when margins are narrow.

**Severity:** Low. The research report §Model2Vec section notes this as the primary risk: BLAS-backed 2D matmul can be non-deterministic across BLAS builds. The variance is ~1e-6, which should not affect routing above reasonable margin thresholds.

**Recovery:** Enforce a minimum margin threshold `_SEM_MARGIN_MIN` (see §10.2). Any flip caused by BLAS float variance will only matter when the top-2 cosine scores are within ~1e-5 of each other, which the margin threshold prevents from driving routing decisions.

---

## 11. Authoring & Maintenance Burden

### 11.1 What must be authored

**Per catalog entry:** a set of example utterances. These are short natural-language phrasings that describe the tasks the entry handles. The quality of the semantic signal is entirely determined by utterance coverage. The research report §Open Questions question 1 frames this as the critical unknown: "the semantic layer's quality ceiling is determined by whether the utterance examples adequately cover the variation space of real dispatch prompts."

With ~100 catalog entries and a target of 5–10 utterances per entry, the total corpus is approximately 500–1,000 short strings. This is the full authoring cost for this design at the **entry-level granularity**.

### 11.2 Authoring location

Utterances are added to the catalog entry definition format, likely as a new `semantic_examples` or `utterances` field in the trigger schema (analogous to the `triggers` block in existing catalog sources). The catalog generator (`build_catalog/_process.py`) reads these at build time, embeds them, and writes the `.npz` index alongside the catalog JSON.

### 11.3 Maintenance triggers

- **Adding a new catalog entry:** must include utterances. A catalog entry without utterances gets no semantic centroid and is invisible to the semantic scorer. The catalog auditor (`audit_catalog.py`) should warn on entries with zero utterances.
- **Renaming or restructuring an entry:** centroid must be regenerated. Covered by the catalog-build staleness check (§5.4).
- **Encoder model upgrade:** all centroids must be regenerated. This is the highest-cost maintenance event; it requires re-embedding all utterances and re-validating threshold margins.
- **Semantic drift:** if real dispatch prompts shift in vocabulary over time (new tools, new agent names), utterances may become stale. No automated detection is currently scoped; periodic review is a manual process.

### 11.4 Comparison with current burden

The existing lexical scorer requires keyword authoring per entry (existing practice). This design adds utterance authoring per entry — a parallel corpus at the same granularity. Total burden: roughly double the per-entry authoring effort.

---

## 12. Open Questions

1. **Semantic-corroboration gate scope (§7.1).** Should the gate apply only when the semantic signal is itself "confident" (top-2 margin ≥ some threshold), or unconditionally when the semantic and lexical signals disagree? Conditional application reduces false escalations to `ambiguous` (§10.3); unconditional application is simpler but noisier.

2. **`ambiguous` as a new decision variant vs reusing `advisory`.** The decision ladder has 7 variants (`VALID_DECISIONS` in `_types.py:37-47`). Surfacing ensemble disagreement as `advisory` with `disposition_source: "ensemble_disagreement"` is backward-compatible but requires consumers to check `disposition_source` to distinguish agree-advisory from disagree-advisory. Adding a new `ambiguous` decision class is cleaner for consumers but breaks the 7-decision contract. Which is preferable?

3. **Semantic score normalization.** Raw cosine scores are not calibrated against the lexical score's [0.0, 1.0] range. Should the semantic score be normalized (e.g., min-max across the catalog per dispatch) before reporting in the output? Normalization aids comparability but introduces a dependency on the full-catalog score distribution per dispatch.

4. **Utterance corpus bootstrapping.** With ~100 catalog entries and ~5–10 utterances each, an initial corpus of 500–1,000 utterances must be authored before the semantic scorer provides useful signal. Is this a prerequisite for shipping, or can the design degrade gracefully to lexical-only for entries without utterances?

5. **Agree-path confidence formula.** When signals agree, should confidence be `min(lex_score, sem_score)`, `(lex_score + sem_score) / 2`, or something else? `min` is conservative; `avg` rewards corroboration. The choice affects how the output confidence reads to consumers.

6. **`potion-base-8M` vs `potion-code-16M`.** The research report (§Open Question 6) recommends evaluating both on 50 labelled prompts. The wayfinder dispatch domain is specifically software/devops; `potion-code-16M` may outperform `potion-base-8M` on code-adjacent prompts. This decision is a prerequisite for committing the model to the catalog build pipeline.

---

## 13. Sources

All sources are from `docs/research/2026-06-07-semantic-routing.md` (fetched 2026-06-07) unless otherwise noted. Source URLs and fetch dates are carried from the research report verbatim.

| Source | URL | Fetched |
|---|---|---|
| MinishLab/model2vec repo | https://github.com/MinishLab/model2vec | 2026-06-07 |
| model2vec PyPI | https://pypi.org/project/model2vec/ | 2026-06-07 |
| minishlab/potion-base-8M model card | https://huggingface.co/minishlab/potion-base-8M | 2026-06-07 |
| minishlab/potion-code-16M model card | https://huggingface.co/minishlab/potion-code-16M | 2026-06-07 |
| aurelio-labs/semantic-router repo | https://github.com/aurelio-labs/semantic-router | 2026-06-07 |
| HansalShah007/semroute | https://github.com/HansalShah007/semroute | 2026-06-07 |
| numpy np.dot precision issue #661 | https://github.com/numpy/numpy/issues/661| 2026-06-07 |
| Floating-point determinism (Bruce Dawson) | https://randomascii.wordpress.com/2013/07/16/floating-point-determinism/ | 2026-06-07 |
| ONNX Runtime issue #12086 (reproducibility) | https://github.com/microsoft/onnxruntime/issues/12086 | 2026-06-07 |
| Explainable Model Routing (arXiv:2604.03527) | https://arxiv.org/pdf/2604.03527 | 2026-06-07 |
| Toward Super Agent System with Hybrid AI Routers (arXiv:2504.10519) | https://arxiv.org/pdf/2504.10519 | 2026-06-07 |
| On Reproducibility Limitations of RAG Systems (arXiv:2509.18869) | https://arxiv.org/pdf/2509.18869 | 2026-06-07 |
| Outcome-Aware Tool Selection (arXiv:2603.13426) | https://arxiv.org/pdf/2603.13426 | 2026-06-07 |
| src/claude_wayfinder/match/_match.py | (this repo, read 2026-06-07) | 2026-06-07 |
| src/claude_wayfinder/match/_decide.py | (this repo, read 2026-06-07) | 2026-06-07 |
| src/claude_wayfinder/match/_types.py | (this repo, read 2026-06-07) | 2026-06-07 |
| docs/research/2026-06-07-semantic-routing.md | (this repo, read 2026-06-07) | 2026-06-07 |

**Unverified claims in this spec:**

- `unverified:` potion-base-8M has 256 embedding dimensions — inferred from documentation patterns; HF model card did not explicitly state this in fetched content. Carried from the research report's unverified claim.
- `unverified:` Illustrative cosine scores in §4.2 (0.72, 0.54, 0.48) are hypothetical examples constructed to illustrate disagreement; real scores require calibration against actual utterances and real dispatch prompts.
- `unverified:` Lexical scores for the worked example (code-reviewer ≈ 0.75, code-writer ≈ 0.75) are estimates based on the scoring formula in `_match.py` and assumed keyword weights; exact values depend on the actual catalog trigger definitions, which were not read in full for this spec.
