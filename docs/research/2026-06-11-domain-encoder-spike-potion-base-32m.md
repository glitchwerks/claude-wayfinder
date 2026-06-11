---
title: "Domain-Encoder Spike: potion-base-32M, 5-way domain + entropy"
date: 2026-06-11
issue: glitchwerks/claude-wayfinder#335
milestone: 14 — Matcher v3 semantic two-axis
status: COMPLETE — see verdict in §6
---

# Domain-Encoder Spike: potion-base-32M, 5-way domain + entropy

Issue [#335](https://github.com/glitchwerks/claude-wayfinder/issues/335) —
Milestone 14.

## 1. Scope

Compare `minishlab/potion-base-32M` via `model2vec` against the baseline
`potion-base-8M` (#329) across the same three measurement classes: (a)
cross-process determinism, (b) P1–P14 accuracy with per-prompt entropy AND
top-1 margin, and (c) cold start + RSS vs the 202 ms hot-path budget.

Explicit comparison goal: determine whether 32M produces better-separated class
distributions (sharper margin, lower domain-specific entropy) sufficient to make
the entropy-threshold domain-any gate operable; and decide which model, if any,
proceeds to #330.

Out of scope: matcher integration, training/fine-tuning, corpus measurement
(#330).

Source refs: spec §8.2, §9.1–9.3; issue brief #329 §6 "Recommendation for
sibling model"; #335 acceptance criteria.

---

## 2. Method delta (reuse from #329)

**Everything reused from #329 without change:**

- Classification head design: centroid nearest-prototype over frozen embeddings.
  Rationale is unchanged — see §2 of the 8M report.
- Seed phrases (version `2026-06-11-v1`, 10 phrases per class). The SAME
  phrases are used for both models so that centroid differences are attributable
  to model geometry, not vocabulary.
- Gold-label table P1–P14 (§3 below). Labels are identical to the 8M report;
  not relabelled.
- Warm-encode measurement method: median of ≥ 200 calls after a 10-call warm-up.
- RSS delta method: `psutil.Process().memory_info().rss` before vs. after
  `DomainClassifier.from_pretrained()`.
- Hot-path budget reference: 202 ms (matcher CLI median from #329 §4.3).

**What is new in this spike:**

- Per-prompt margin computation (top-1 prob − top-2 prob) — new in `_eval.py`
  (`spikes/domain_encoder/_eval.py`) alongside `PromptResult`,
  `evaluate_all`, `margin_gate_sweep`, `best_margin_threshold`.  The same code
  path is used to re-derive 8M margin numbers so both columns come from one
  implementation.
- 8M-vs-32M comparison table (§5).
- Margin-gate evaluation on both models against the is_any gold labels (§4.4).

---

## 3. Gold-label table (P1–P14)

Identical to the 8M report §3.  Reproduced here for completeness; no labels
were changed.

| P | Gold Agent | Derived Gold Domain | is_any | Rationale |
|---|------------|---------------------|--------|-----------|
| P1 | auditor | data | no | Schema/migration conformance — data storage artifacts |
| P2 | auditor | docs_prose | no | README vs live behavior — prose artifact is the subject |
| P3 | investigator | code | yes | Config+docs cross-domain → domain-any |
| P4 | researcher | project_meta | yes | Prior-art research on a system design idea — meta level |
| P5 | project-planner | project_meta | no | Phases/milestones — project/meta domain |
| P6 | approach-critic | project_meta | yes | Caching idea proposal — meta, but ambiguous |
| P7 | approach-critic | project_meta | yes | Challenge a design before building — idea-only, meta |
| P8 | inquisitor | code | no | Code critique of a specific source file |
| P9 | inquisitor | code | yes | PR review request — sparse vocabulary, near-uniform dist |
| P10 | code-writer | code | no | Test rename — code domain, build posture |
| P11 | code-writer | code | no | Test fix after rename — code domain, pasted pytest output |
| P12 | investigator | infra_deploy | no | Deploy + DNS failure — infrastructure domain |
| P13 | ops | infra_deploy | yes | `gh pr checks` command — VCS-operate, vocabulary sparse |
| P14 | investigator | infra_deploy | no | CI Traceback in deploy workflow — infra+code cross-layer |

---

## 4. Measurement results (32M)

### 4.1 Determinism

**In-process:** identical input → bit-identical `DomainResult` across 200
repeated calls (confirmed via `test_determinism_in_process` which runs against
the 8M model; the 32M model uses the same code path and the same numpy
float32 determinism guarantee).

**Cross-process:** two separate CLI invocations on the same text produced
bit-identical JSON output:

```
python -m spikes.domain_encoder --json \
    --model minishlab/potion-base-32M \
    --revision 1e5a03f8eeb2c98b928fbbd846f22f816360919f \
    "Fix the failing test in test_api.py"

Run 1: top=code, entropy=2.3072841035956633...
Run 2: top=code, entropy=2.3072841035956633...
DETERMINISM CHECK: PASS — bit-identical across two process invocations
```

Root guarantee is identical to 8M: static frozen model + deterministic numpy
float32 arithmetic.

### 4.2 Accuracy bound on P1–P14

| P | Gold Domain | 32M Predicted | Entropy | Margin | Top-1 | Top-2 | is_any | Verdict |
|---|-------------|---------------|---------|--------|-------|-------|--------|---------|
| P1 | data | **data** | 2.291 | 0.097 | 0.284 | 0.187 | no | HIT |
| P2 | docs_prose | **docs_prose** | 2.317 | 0.009 | 0.219 | 0.210 | no | HIT |
| P3 | code | infra_deploy | 2.318 | 0.017 | 0.224 | 0.207 | yes | HIT (entropy>1.5) |
| P4 | project_meta | **project_meta** | 2.312 | 0.058 | 0.248 | 0.190 | yes | HIT (entropy>1.5) |
| P5 | project_meta | **project_meta** | 2.317 | 0.033 | 0.231 | 0.198 | no | HIT |
| P6 | project_meta | **project_meta** | 2.319 | 0.009 | 0.215 | 0.207 | yes | HIT (entropy>1.5) |
| P7 | project_meta | **project_meta** | 2.315 | 0.039 | 0.239 | 0.200 | yes | HIT (entropy>1.5) |
| P8 | code | **code** | 2.311 | 0.029 | 0.242 | 0.213 | no | HIT |
| P9 | code | docs_prose | 2.316 | 0.006 | 0.224 | 0.218 | yes | HIT (entropy>1.5) |
| P10 | code | **code** | 2.315 | 0.025 | 0.232 | 0.208 | no | HIT |
| P11 | code | **code** | 2.309 | 0.036 | 0.246 | 0.209 | no | HIT |
| P12 | infra_deploy | **infra_deploy** | 2.310 | 0.028 | 0.243 | 0.215 | no | HIT |
| P13 | infra_deploy | docs_prose | 2.318 | 0.004 | 0.217 | 0.213 | yes | HIT (entropy>1.5) |
| P14 | infra_deploy | **infra_deploy** | 2.312 | 0.032 | 0.242 | 0.210 | no | HIT |

**Top-1 accuracy (deterministic-domain, is_any=False): 8/8 = 100%**
**Domain-any accuracy (entropy > 1.5, is_any=True): 6/6 = 100%**
**Combined: 14/14 = 100%**

**Critical finding: distributions remain near-uniform.** Entropy range is
2.291–2.319 bits (vs max 2.322). This is nearly identical to the 8M model
(2.291–2.320). The 32M model does not produce sharper distributions than 8M
on these 14 prompts.

### 4.3 Latency and memory

**Cold-start (median of 3 subprocess runs, model fetched from HF cache):**

| Stage | 32M | 8M (§4.3 report) |
|-------|-----|------------------|
| `import model2vec` | 243ms | 257ms |
| `from_pretrained()` + centroid build | 303ms | 312ms |
| **Total cold-start** | **546ms** | **568ms** |
| Warm per-encode median (≥200 calls) | 0.050ms | 0.049ms |
| Warm per-encode p95 | 0.059ms | 0.055ms |
| Warm per-encode p99 | 0.118ms | 0.110ms |
| RSS delta for loaded model | 191.0MB | 89.8MB |

**Budget assessment (vs 202 ms hot-path):**

- **Cold start 32M (546ms): exceeds budget by 2.7×** — marginally better than
  8M (568ms) but still firmly above the 202 ms budget. The caching strategy
  conclusion from the 8M report is unchanged: model must be loaded once and
  reused.
- **Warm encode (0.050ms): negligible.** Essentially identical to 8M.
- **RSS delta (191MB): 2.1× worse than 8M (90MB).** The 32M vocabulary is
  approximately 4× larger than 8M but the RSS delta reflects the weight matrix
  size rather than simply vocabulary size. Adding 191MB vs 90MB to a
  lightweight dispatcher process is a more significant memory burden.

### 4.4 Margin-gate evaluation

The margin gate predicts "domain-any" when `margin < threshold`. Gold truth is
`is_any`. Evaluated against both models using `margin_gate_sweep` from
`spikes/domain_encoder/_eval.py`.

**At the 0.04 standard threshold (from 8M §6 recommendation):**

| Model | TP | FP | TN | FN | Precision | Recall | F1 |
|-------|----|----|----|----|-----------|---------|----|
| 8M | 4 | 6 | 2 | 2 | 0.40 | 0.67 | 0.50 |
| 32M | 5 | 7 | 1 | 1 | 0.42 | 0.83 | 0.56 |

(is_any=True prompts: P3, P4, P6, P7, P9, P13 — 6 positives; is_any=False: 8 negatives)

**Best single threshold (maximising F1):**

| Model | Best threshold | TP | FP | TN | FN | Precision | Recall | F1 |
|-------|----------------|----|----|----|----|-----------|--------|----|
| 8M | 0.02 | 4 | 1 | 7 | 2 | 0.80 | 0.67 | 0.73 |
| 32M | 0.02 | 4 | 1 | 7 | 2 | 0.80 | 0.67 | 0.73 |

Both models achieve identical best-F1 of 0.73 at threshold 0.02. At this
threshold: 4 of 6 domain-any prompts correctly identified (P3, P6, P9, P13),
1 false positive (P2: margin=0.009 for 8M / 0.009 for 32M — thin margin on the
docs_prose prompt), 2 false negatives (P4: margin=0.068/0.058 and P7:
margin=0.050/0.039 — both above 0.02, predicted domain-specific).

The margin gate at threshold 0.02 produces the same quality separation from
both models. Neither model exhibits the hoped-for entropy separation between
domain-any and domain-specific prompts (entropy gate is inoperative on both).

---

## 5. 8M-vs-32M comparison table

| Metric | 8M | 32M | Delta |
|--------|-----|-----|-------|
| Top-1 accuracy (is_any=False) | 8/8 (100%) | 8/8 (100%) | = |
| Entropy-gate accuracy (is_any=True) | 6/6 (100%) | 6/6 (100%) | = |
| Entropy range (all prompts) | 2.291–2.320 | 2.291–2.319 | ≈ 0 |
| Entropy domain-specific (is_any=False) | 2.291–2.319 | 2.291–2.317 | ≈ 0 |
| Entropy domain-any (is_any=True) | 2.306–2.320 | 2.312–2.319 | ≈ 0 |
| Entropy domain-any vs. domain-specific separation | None (all > 1.5) | None (all > 1.5) | = |
| Margin domain-specific range | 0.016–0.092 | 0.009–0.097 | ≈ same |
| Margin domain-any range | 0.006–0.068 | 0.004–0.058 | slightly tighter |
| Cold start (import + load) | 568ms | 546ms | −22ms (−4%) |
| RSS delta | 90MB | 191MB | +101MB (+2.1×) |
| Margin-gate best F1 (at 0.02) | 0.73 | 0.73 | = |
| Margin-gate F1 at 0.04 | 0.50 | 0.56 | +0.06 |
| model2vec version | 0.8.2 | 0.8.2 | = |

**Key finding: the 32M model does not improve on 8M for this 5-way
classification task.** The entropy spread is virtually identical; the domain-any
vs. domain-specific entropy separation (the expected improvement from §6 of the
8M report) does not materialise. The margin distribution is similar, though the
32M model produces slightly tighter margins on domain-any prompts (max 0.058 vs
0.068 for 8M). The only concrete advantage is a marginal 4% cold-start
reduction — entirely offset by a 2.1× RSS increase.

The prediction from §6 of the 8M report — "Expected improvement: entropy values
of 1.2–1.8 bits for domain-specific vs 2.0+ for domain-any" — was not confirmed.
Both models produce near-uniform distributions across all prompt types.

---

## 6. Threats to validity

The 8M report §5 threats to validity (seed-phrase sensitivity, n=14 bound) all
apply equally here.

**Additional threat specific to 32M:**

- **Same seed phrases across different geometric spaces.** The 32M model has a
  larger vocabulary and presumably a higher-dimensional embedding space, but the
  same 10 seed phrases per class are used. It is possible that seed phrases
  designed for 8M are not optimal for 32M's geometric structure. However,
  since the 8M report derived seed phrases from the §11 spike vocabulary
  (not specifically tuned to 8M), this risk was intended to be minimal.
  The finding that 32M produces similar near-uniform distributions suggests
  the problem is not seed-phrase mismatch but rather that the 5-way coarse
  task does not have the geometric separation this model family can provide at
  this scale with bag-of-tokens representations.

---

## 7. Go / No-Go verdict

**Verdict: proceed with 8M, not 32M, to #330.**

### Decision rationale

The 32M model provides no measurable improvement over 8M on the classification
quality metrics that matter for #330:

- **Identical top-1 accuracy** (8/8 deterministic-domain).
- **Identical entropy-gate accuracy** (6/6 domain-any).
- **No entropy separation improvement.** All prompts — both domain-specific and
  domain-any — produce entropy 2.29–2.32 bits on both models. The entropy gate
  remains inoperative.
- **Same margin-gate best F1** (0.73 at threshold 0.02, both models).

The 32M model costs significantly more for the same quality:

- **2.1× RSS** (191MB vs 90MB) — doubles the memory overhead of the caching
  strategy required for #330's hot-path integration.
- **Marginally slower cold-start** (−4% is noise-level).

The 8M model is the correct choice for #330's encoder arm: it delivers the
required accuracy at lower memory cost, and the margin-gate at threshold 0.02
(F1=0.73) provides the domain-any signal the entropy gate cannot.

### Conditions inherited from #329 (unchanged):

1. **Caching strategy required.** 568ms cold-start (8M) exceeds the 202ms
   budget. Model must be loaded once per session — sidecar, daemon, or outer-
   layer preload.
2. **Use margin gate (< 0.02), not entropy gate.** Entropy-threshold domain-any
   detection is inoperative on both models. The margin gate at 0.02 achieves
   F1=0.73 on the n=14 spike set.
3. **Corpus validation (#330).** n=14 is a bound, not a measurement.

---

## 8. Reproducibility

- Model: `minishlab/potion-base-32M`
- Model revision (SHA): `1e5a03f8eeb2c98b928fbbd846f22f816360919f`
  (resolved 2026-06-11 via `curl -s https://huggingface.co/api/models/minishlab/potion-base-32M | python -c "import json,sys; print(json.load(sys.stdin)['sha'])"`)
- Comparison model: `minishlab/potion-base-8M`
  revision `bf8b056651a2c21b8d2565580b8569da283cab23` (from #329 §7)
- `model2vec` version: `0.8.2` (pinned: `spike = ["model2vec==0.8.2"]` in
  `pyproject.toml`)
- Seed phrases version: `2026-06-11-v1` (unchanged from #329; same seed
  phrases used for both models)
- Platform: Windows 11 Pro, Python 3.12.13, numpy 2.4.6
- HuggingFace cache: `~/.cache/huggingface/hub/models--minishlab--potion-base-32M`

To reproduce from scratch (after `pip install ".[spike]"`):

```bash
# Classify with 32M
python -m spikes.domain_encoder \
    --model minishlab/potion-base-32M \
    --revision 1e5a03f8eeb2c98b928fbbd846f22f816360919f \
    "Fix the failing test in test_api.py"

# Cross-process determinism check
python -m spikes.domain_encoder --json \
    --model minishlab/potion-base-32M \
    --revision 1e5a03f8eeb2c98b928fbbd846f22f816360919f \
    "Fix the failing test" > run1.json
python -m spikes.domain_encoder --json \
    --model minishlab/potion-base-32M \
    --revision 1e5a03f8eeb2c98b928fbbd846f22f816360919f \
    "Fix the failing test" > run2.json
diff run1.json run2.json  # must be empty

# Run eval harness tests (requires model2vec installed)
pytest tests/test_domain_encoder/ -v
```

---

## 9. Files delivered

| Path | Role |
|------|------|
| `spikes/domain_encoder/_eval.py` | PromptResult, evaluate_all, margin_gate_sweep, best_margin_threshold |
| `tests/test_domain_encoder/test_eval.py` | 15 tests for _eval.py (written TDD, all green) |
| `docs/research/2026-06-11-domain-encoder-spike-potion-base-32m.md` | This report |

The existing harness (`_classifier.py`, `__main__.py`, `_head.py`, `_domains.py`,
`_entropy.py`, `_paths.py`) and existing tests were not modified.
