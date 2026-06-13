---
title: "Root-cause: potion-base-8M near-random on organic prompts vs 14/14 on spike set"
date: 2026-06-13
issue: glitchwerks/claude-wayfinder#353
status: COMPLETE — root cause identified; see verdict §6
---

# Root-cause: potion-base-8M near-random on organic prompts vs 14/14 on spike set

Issue [#353](https://github.com/glitchwerks/claude-wayfinder/issues/353).
Authored 2026-06-13 on branch `feat/330-measurement-run`.

---

## Failure

`potion-base-8M` centroid classifier was reported as 14/14 (100%) accurate on the
P1-P14 spike set (#329 §4.2, #335 §4.2), but the #330 corpus measurement found it
near-random on 168 organic dispatch prompts: entropy 2.3095–2.3214 bits on every
entry (max `log2(5) ≈ 2.3219`), top-1 − top-2 margin median ≈ 0.019, confident-
wrong rate 0.2458 full / 0.4915 no-smoke, top-1 accuracy 51.6% vs a majority-class
baseline of 48.4% on the 93 labeled non-smoke non-any organic entries.

---

## Root cause

**H1 (curated-distribution mismatch) is the primary cause, confirmed.**
**H2 (model ceiling) is a real but secondary contributing factor.**
**H3 (centroid mis-placement) is ruled out.**

The spike set (P1-P14) consisted of 14 short, domain-salient prompts that are
semantically near-identical to the 50 seed phrases used to build the class centroids
— because both were authored in the same session from the same §9.1 / §11 agent-grid
vocabulary. This conflation of training vocabulary and test vocabulary inflated the
spike accuracy to 14/14. Organic dispatch prompts are systematically longer
(median 46 words vs 9 words for the spike set), more abstract, and contain far fewer
of the domain-salient tokens that the centroid head relies on for separation. The
centroid head's geometry is adequate (median inter-centroid cosine distance 0.58),
but the organic queries land close to the origin of the 5-class simplex because their
bag-of-tokens embeddings average out to a near-uniform position relative to all five
centroids. The resulting softmax over nearly-equal cosine similarities collapses to a
near-uniform distribution on every organic input.

The model ceiling (H2) compounds H1: potion-base-8M is a bag-of-tokens static model
in 256-dimensional space; its geometric structure cannot separate the five coarse
domains on abstract organic phrasing even if better seed phrases were chosen. But
H1 alone is sufficient to explain the spike-vs-organic gap.

---

## Evidence chain

### Probe 1: Spike accuracy reproduction (`.tmp/probe_353_spike_vs_organic.py`)

Run: `"<worktree>/.venv/Scripts/python.exe" .tmp/probe_353_spike_vs_organic.py`
(2026-06-13, worktree path confirmed by shadowing guard)

Using the same conceptual prompts described in the spike report §3 gold-label table:

- **Top-1 accuracy (is_any=False): 8/8 = 100%** — matches the spike's reported 100%.
- **Domain-any accuracy (entropy > 1.5, is_any=True): 6/6 = 100%** — matches.
- Spike entropy range: 2.2927–2.3202 bits. Organic entropy range: 2.3095–2.3214 bits.
  Both near-uniform; the spike "accuracy" is not caused by sharper distributions.
- Spike margin range: 0.005–0.097; organic margin range: 0.0003–0.0450. The spike
  margins are slightly higher because spike prompts contain more domain-salient tokens.

**Inference:** the spike accuracy reproduces. The classifier code path is correct.
The 14/14 result is real but driven by something about the test set, not the model.

### Probe 2: Organic corpus distribution (`.tmp/probe_353_spike_vs_organic.py`)

- 168/168 organic entries have entropy > 2.30 bits (0% below 2.30).
- Organic top-1 probability: min=0.208, median=0.232, max=0.248 (uniform = 0.200).
- Organic margin: min=0.0003, median=0.019, max=0.045.
- Organic top-1 accuracy on labeled non-smoke non-any entries (n=93): **51.6%**.
- Majority-class baseline (always predict "code"): **48.4%**.

**Inference:** 51.6% vs 48.4% baseline is a 3.2pp lift — statistically indistinguishable
from chance on this sample size. The encoder is a near-broken-clock on organic prompts.

### Probe 3: Distribution gap (`.tmp/probe_353_spike_vs_organic.py`)

Prompt length (words):

| Set | Min | Median | Max |
|-----|-----|--------|-----|
| Spike P1-P14 | 3 | 9 | 13 |
| Organic (all) | 3 | 34 | 128 |
| Organic (no-smoke) | 4 | 46 | 128 |

Domain-term density (fraction of tokens in seed-phrase vocabulary):

| Set | Min | Median | Max |
|-----|-----|--------|-----|
| Spike P1-P14 | 0.000 | 0.134 | 0.500 |
| Organic (all) | 0.000 | 0.094 | 0.500 |
| Organic (no-smoke) | 0.000 | 0.056 | 0.250 |

The no-smoke organic prompts have 2.4× lower domain-term density (0.056 vs 0.134
median) and 5× longer text. Bag-of-tokens models average over all tokens; more
non-domain words dilute the domain signal.

**Inference:** organic prompts are substantially different from the spike prompts in
exactly the dimension the centroid head relies on: the fraction of tokens that are
domain-salient. This is the primary mechanism of failure.

### Probe 4: Per-domain accuracy and baseline rates (`.tmp/probe_353_baseline_rate.py`)

| Domain | Gold% | Pred% | Correct | Acc |
|--------|-------|-------|---------|-----|
| code | 48.4% | 67.7% | 36 | 80.0% |
| docs_prose | 14.0% | 9.7% | 6 | 46.2% |
| infra_deploy | 5.4% | 15.1% | 2 | 40.0% |
| project_meta | 32.3% | 7.5% | 4 | 13.3% |

Key finding: **project_meta is gold 32.3% of organic entries but predicted only 7.5%
of the time** — the classifier almost never outputs "project_meta" on organic prompts
even though a third of the corpus belongs to that domain. "code" is predicted 67.7%
despite being the gold label only 48.4% of the time. The encoder is biased toward
"code" and "infra_deploy" on organic prompts because the seed phrases for those
domains contain tokens ("fix", "bug", "test", "deploy", "pipeline") that appear more
often in organic phrasing than the seed phrases for "project_meta" (which use meta-
planning vocabulary like "roadmap", "milestone", "poke holes", "challenge").

This "broken clock" structure — near-majority-class accuracy driven by class frequency,
not genuine domain discrimination — is the same finding reported in #330 §7.5.

### Probe 5: Centroid geometry (`.tmp/probe_353_centroids.py`)

- Centroid shape: (5, 256), all rows L2-normalised.
- Pairwise inter-centroid cosine distances: min=0.408, median=0.584, max=0.675.
  The centroids are not degenerate (not all pointing the same direction). The
  five class centroids are geometrically separated.
- Within-class seed-phrase cosine similarity: mean 0.16–0.30 per class — seed
  phrases within each class are not tightly clustered.
- Cross-class mean cosine similarity: 0.10–0.18 — cross-class overlap is low.

**Inference:** the centroid construction is geometrically sound. The five centroids
are reasonably separated (distance 0.4–0.7) and the seed phrases produce meaningful
centroids. H3 (mis-placed centroids) is ruled out. The problem is that organic query
embeddings land near the centre of the simplex, not near any centroid.

### Probe 6: Long vs short prompt behaviour (`.tmp/probe_353_centroids.py`)

| Prompt style | Length | Top-1 prob | Margin | Entropy |
|---|---|---|---|---|
| Spike-style ("fix the bug in the Python function") | 7 words | 0.259 | 0.055 | 2.304 |
| Smoke ("implement the new module") | 4 words | 0.234 | 0.022 | 2.314 |
| Long abstract (sprint planning + caching question) | 34 words | 0.291 | 0.094 | 2.286 |
| Domain-sparse ("help me think through this...") | 8 words | 0.231 | 0.017 | 2.314 |
| Very short ("review this") | 2 words | 0.237 | 0.022 | 2.312 |

The long abstract prompt actually gets a *higher* top-1 probability (0.291) and margin
(0.094) than most spike prompts — but this is because it happens to contain the phrase
"sprint" and "caching", which are seed-phrase tokens. Pure-abstract short prompts
("review this", "help me think through this") are the worst performers. This further
confirms H1: accuracy is driven by token overlap with seed phrases, not genuine semantic
understanding.

### Probe 7: Spike set design (spike reports §2, §5.1)

The 8M spike report §5.1 states explicitly:
> "The 10-phrase lists were authored in a single session; the §11 spike prompts were
> visible when writing them (potential optimism bias)."

This is the smoking gun. The spike prompts and the seed phrases share a vocabulary
because they were co-designed in a single session looking at the same §9.1 agent grid.
The spike's §5.2 warns: "n=14 is too small to support per-class accuracy estimates."
Both were known risks; the #330 corpus measurement was specifically called for to
close them (#329 §6 condition 3, #335 §7 condition 3).

**Inference:** the spike authors identified this risk in the report but could not
quantify it without #330 data. Now we have it: the overlap inflated spike accuracy
from ~52% (the organic number) to 100%.

---

## Hypotheses ruled out

- **H3: Centroid mis-placement** — ruled out by Probe 5. Pairwise inter-centroid
  cosine distances (0.41–0.67) show meaningful geometric separation. Seed phrase
  embeddings within each class are coherent (mean within-class sim 0.16–0.30). The
  centroids are correctly placed for the seed vocabulary; the problem is that organic
  embeddings land near the simplex centre, not that centroids are wrong.

- **"Inert encoder (never delegates)"** — ruled out by #351 fix (entropy gate
  dropped; margin-only gate; 118/168 organic entries delegated). Already ruled out
  in the #330 measurement.

- **Model size** — ruled out by #335: 32M produces identical near-uniform distributions
  (entropy 2.291–2.319 bits), same top-1 accuracy, same margin-gate best-F1 (0.73).
  Size is not the limiting factor.

---

## Per-Hypothesis verdicts

### H1: Curated-distribution mismatch — CONFIRMED (primary cause)

The spike's 14-prompt test set was drawn from the same §9.1/§11 vocabulary used to
write the seed phrases, inflating accuracy. Organic prompts are 5× longer (median 46
vs 9 words) and have 2.4× lower domain-term density (0.056 vs 0.134). The decisive
number: encoder top-1 accuracy on organic non-smoke non-any entries = **51.6% vs
48.4% majority baseline** — a 3.2pp lift, near-chance.

### H2: Model ceiling (potion-base-8M cannot separate domains on short organic text) — CONFIRMED (secondary, compounding)

The centroid head is geometrically correct, but bag-of-tokens embedding averages over
all tokens. On longer, more abstract organic phrasing the domain signal is diluted by
non-domain tokens and the softmax collapses to near-uniform. The 32M model (#335)
does not escape this (identical near-uniform distributions). This is structural to the
model family, not fixable by better seed phrases alone.

Evidence: 100% of 168 organic entries have entropy > 2.30 bits regardless of text
length, domain-term density, or prompt type. The ceiling is at `log2(5) ≈ 2.322` for
all organic inputs.

### H3: Centroid construction (mis-placed centroids) — RULED OUT

Centroid geometry is sound: inter-centroid distance median 0.58, within-class seed
coherence mean 0.16–0.30. The failure is in organic query embedding positions, not
in centroid placement.

---

## Salvage recommendation

**The domain encoder axis is a dead end for short organic dispatch prompts with the
current model family and seed-phrase approach.**

The specific failure mode is that organic dispatch prompts are substantially longer and
more abstract than the spike prompts used to validate the approach. A bag-of-tokens
static model averages diluted domain signal over all tokens; no centroid configuration
will resolve this without fundamentally more discriminative embeddings.

Viable paths forward (ranked by expected cost):

1. **Do not fix; remove the domain axis.** The #330 measurement showed that the
   architecture independence premise (Phi=0.06) holds — the domain and posture axes
   are near-decorrelated. But the domain encoder itself is not accurate enough to
   improve routing over the lexical baseline. Dropping the domain axis and relying
   on posture extractors + lexical matching is the current state, and it may be the
   correct long-term state until a better domain signal is available.

2. **Replace static bag-of-tokens with a contextual embedding model** (e.g.
   sentence-transformers `all-MiniLM-L6-v2`, ~80MB). These models capture semantic
   meaning rather than token bags, so domain-sparse abstract phrasing is less
   catastrophic. However: (a) cold-start budget is already exceeded, and (b) the
   accuracy improvement is unknown — the same organic corpus must be re-measured.

3. **Re-design seed phrases to match organic phrasing.** Use the organic corpus
   (168 entries) as training data for the centroid head — average the embeddings of
   organic prompts per gold domain label, rather than hand-authoring seed phrases.
   This directly addresses H1 (distribution mismatch) without changing the model.
   Risk: organic corpus is small (n=168; ~45 entries per domain), so centroids will
   be noisy. Known organic accuracy improvement is uncertain.

4. **A trained classification head** (logistic regression or similar) over frozen
   potion-base-8M embeddings, trained on the organic labels. This converts the
   centroid spike into a proper supervised classifier. Requires the gold labels (#339)
   and a held-out evaluation set — the 168-entry corpus is too small for a
   reliable train/test split.

**The domain axis is not inherently a dead end** — it is a dead end for this model
family and this test set design. The core finding is methodological: validating a
semantic encoder on a test set derived from the same vocabulary as the training signal
(seed phrases) produces optimistic accuracy that does not transfer to organic data.

---

## Open questions

- What is the organic top-1 accuracy floor for a contextual embedding model (e.g.
  MiniLM) on the same 168-entry corpus? This is the key unknown for path (2) above.
- Can organic-centroid construction (path 3) reach the #330 kill-criterion threshold
  (confident-wrong ≤ 0.1507) on a held-out split of the organic corpus?
- Is the `project_meta` under-prediction (7.5% predicted vs 32.3% gold) correctable
  with better seed vocabulary, or does it reflect a genuine vocabulary sparsity in
  organic meta-planning prompts?

---

## Reproducibility

Probe scripts:
- `.tmp/probe_353_spike_vs_organic.py` — spike reproduction, organic distribution, gap quantification, centroid pairwise distances
- `.tmp/probe_353_centroids.py` — centroid geometry, within-class seed-phrase spread, cross-class overlap
- `.tmp/probe_353_organic_accuracy.py` — per-domain organic accuracy, length buckets, seed coverage vs accuracy
- `.tmp/probe_353_baseline_rate.py` — baseline rate analysis, broken-clock confirmation

Interpreter: `<worktree>/.venv/Scripts/python.exe` (model2vec 0.8.2, confirmed by shadowing guard).
Platform: Windows 11 Pro, Python 3.12.13, numpy 2.4.6.
Corpus: `~/.claude/state/wayfinder-corpus/2026-06-12/wayfinder-corpus.jsonl` (168 entries, SHA `98454ca6...`).
Gold labels: `~/.claude/state/wayfinder-corpus/2026-06-12/gold-labels.jsonl` (168 entries, SHA `c38be656...`).
Model revision: `bf8b056651a2c21b8d2565580b8569da283cab23` (pinned in `_classifier.py`).
