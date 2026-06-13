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
**H2 (model ceiling) is plausible but NOT confirmed — no larger/contextual model was tested on the organic distribution.**
**H3 (centroid mis-placement) is ruled out.**

> **Correction (2026-06-13, PR #354 review).** The original probe computed the spike
> length/density stats from *reconstructed* approximate prompts (the spike reports do
> not embed full prompt text). Recomputed from the canonical `SPIKE_GOLD_FOR_EVAL` in
> `spikes/domain_encoder/_eval.py`: spike length median is **15.5** (not 9 — the gap is
> **~3×**, not 5×), and generic domain-term density is **0.051**, essentially *equal* to
> organic's 0.056 — so **density is not the differentiator** (the earlier "2.4× lower"
> figure was an artifact of the reconstructed prompts). The canonical prompts still
> reproduce **14/14**. The mechanism and verdicts below are corrected accordingly.

The spike set (P1-P14) consisted of 14 prompts that are semantically near-identical to
the 50 seed phrases used to build the class centroids — because both were authored in
the same session from the same §9.1 / §11 agent-grid vocabulary (the 8M spike §5.1
admits this "optimism bias" explicitly; Probe 7). This conflation of training and test
vocabulary inflated the spike accuracy to 14/14. Organic dispatch prompts are ~3×
longer (median 46 vs 15.5 words) and were authored independently of the seed
vocabulary. **Generic domain-term density is *not* the differentiator** — it is
essentially equal between the two sets (spike 0.051 vs organic 0.056 median; Probe 3) —
so the gap is not token-dilution but the *specific* seed-phrase token overlap that
spike prompts share and organic prompts do not, compounded by raw length. The centroid
head's geometry is adequate (median inter-centroid cosine distance 0.58), but organic
query embeddings land near the centre of the 5-class simplex — their cosine similarities
to all five centroids are nearly equal — so the softmax collapses to a near-uniform
distribution on every organic input (entropy > 2.30 on 100% of 168 entries).

The model ceiling (H2) is a plausible compounding factor — the equal-density-yet-near-
random result points away from pure token-dilution and toward genuine inseparability of
the five domains in this embedding space. But it is **not confirmed**: #335 compared
32M to 8M only on the 14-prompt spike set (corpus measurement was explicitly out of
scope there), so no larger or contextual model has been measured on the organic
distribution. H1 alone is sufficient to explain the spike-vs-organic *gap*.

---

## Evidence chain

### Probe 1: Spike accuracy reproduction (`.tmp/probe_353_spike_vs_organic.py`)

Run: `"<worktree>/.venv/Scripts/python.exe" .tmp/probe_353_spike_vs_organic.py`
(2026-06-13, worktree path confirmed by shadowing guard)

This probe originally used *reconstructed* approximate prompts (the spike reports do
not embed full prompt text). The recomputation against the **canonical**
`SPIKE_GOLD_FOR_EVAL` (`spikes/domain_encoder/_eval.py`, Probe 3 correction) yields the
same headline — **8/8 deterministic + 6/6 domain-any = 14/14** — so the reproduction
holds on the canonical prompts, not just the reconstructions.

- **Top-1 accuracy (is_any=False): 8/8 = 100%** — matches the spike's reported 100%.
- **Domain-any accuracy (entropy > 1.5, is_any=True): 6/6 = 100%** — matches.
- Spike entropy range: 2.2927–2.3202 bits. Organic entropy range: 2.3095–2.3214 bits.
  Both near-uniform; the spike "accuracy" is not caused by sharper distributions.
- Spike margin range: 0.005–0.097; organic margin range: 0.0003–0.0450. The spike
  margins are slightly higher because spike prompts share specific seed-phrase tokens.

**Inference:** the spike accuracy reproduces. The classifier code path is correct.
The 14/14 result is real but driven by something about the test set, not the model.

### Probe 2: Organic corpus distribution (`.tmp/probe_353_spike_vs_organic.py`)

The entropy/probability/margin stats below are over the **full 168-row corpus, which
includes the 59 repeated harness-probe ("smoke") rows** (the #330 no-smoke cut drops
these; gold-labeling report §Findings). They are full-corpus distribution stats, not
no-smoke. Accuracy is reported separately on the no-smoke non-any subset; note the
encoder's confident-wrong rate is **cut-sensitive** (#330: 0.2458 full → 0.4915 no-smoke),
so do not read the full-corpus numbers as substantive-prompt behaviour.

- 168/168 entries **(full corpus, incl. 59 smoke)** have entropy > 2.30 bits (0% below 2.30).
- Top-1 probability **(full corpus)**: min=0.208, median=0.232, max=0.248 (uniform = 0.200).
- Margin **(full corpus)**: min=0.0003, median=0.019, max=0.045.
- Top-1 accuracy on labeled **non-smoke non-any** entries (n=93): **51.6%**.
- Majority-class baseline (always predict "code"): **48.4%**.

**Inference:** 51.6% vs 48.4% baseline is a 3.2pp lift — statistically indistinguishable
from chance on this sample size. The encoder is a near-broken-clock on organic prompts.

### Probe 3: Distribution gap (`.tmp/probe_354_canonical.py`)

> Recomputed from the **canonical** `SPIKE_GOLD_FOR_EVAL` prompts in
> `spikes/domain_encoder/_eval.py` (PR #354 review). The earlier figures came from
> reconstructed approximate prompts and overstated both gaps.

Prompt length (words):

| Set | Min | Median | Max |
|-----|-----|--------|-----|
| Spike P1-P14 (canonical) | 9 | 15.5 | 29 |
| Organic (all) | 3 | 34 | 128 |
| Organic (no-smoke) | 4 | 46 | 128 |

Domain-term density (fraction of tokens in the domain-term set):

| Set | Min | Median | Max |
|-----|-----|--------|-----|
| Spike P1-P14 (canonical) | 0.000 | 0.051 | 0.100 |
| Organic (all) | 0.000 | 0.094 | 0.500 |
| Organic (no-smoke) | 0.000 | 0.056 | 0.250 |

Two corrections vs the original draft: organic no-smoke prompts are **~3× longer**
(46 vs 15.5 median, not 5×), and **domain-term density is essentially equal**
(spike 0.051 vs organic 0.056 median) — **not 2.4× lower**, as the reconstructed-prompt
draft claimed. Generic token-dilution is therefore **not** the mechanism.

**Inference:** the spike and organic sets differ mainly in raw length (~3×) and in the
*specific* overlap with the hand-authored seed-phrase vocabulary (the spike prompts
were co-designed with the seeds; Probe 7) — **not** in generic domain-term density. The
failure mechanism is co-design overlap + length, not dilution by non-domain words.

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

- **Model size** — **NOT ruled out on organic** (corrected, PR #354 review). #335 found
  32M near-identical to 8M (entropy 2.291–2.319 bits, same top-1 accuracy, same
  margin-gate best-F1 0.73) — but **only on the 14-prompt P1-P14 spike set**; #335
  explicitly scoped out corpus measurement, so 32M was **never run on the 168 organic
  prompts**. On the spike set the two sizes are indistinguishable; on organic, only 8M
  has been measured (near-random). Whether a larger or contextual model separates the
  organic domains is an **open question** (Salvage path 2), not a settled one.

---

## Per-Hypothesis verdicts

### H1: Curated-distribution mismatch — CONFIRMED (primary cause)

The spike's 14-prompt test set was drawn from the same §9.1/§11 vocabulary used to
write the seed phrases, inflating accuracy (the 8M spike §5.1 admits the co-design
"optimism bias"; Probe 7). Organic prompts are ~3× longer (median 46 vs 15.5 words)
and were authored independently of the seed vocabulary. Note: generic domain-term
density is **essentially equal** (organic 0.056 vs spike 0.051) — **not** a
differentiator (corrected from the reconstructed-prompt draft); the mismatch is in
length and *specific* seed-phrase token overlap, not token-dilution. The decisive
number is unchanged: encoder top-1 accuracy on organic non-smoke non-any entries =
**51.6% vs 48.4% majority baseline** — a 3.2pp lift, near-chance.

### H2: Model ceiling (the model family cannot separate domains on organic text) — PLAUSIBLE, NOT CONFIRMED

The 8M centroid head is geometrically sound, yet organic query embeddings land near
the simplex centre and the softmax collapses to near-uniform on **100% of 168 organic
entries**, regardless of length or domain-term density. Because density is equal to the
spike set yet organic accuracy is near-random, the cause is *not* token-dilution — it
points toward genuine inseparability of the five domains in this embedding space, which
is consistent with a model-family ceiling.

**But this is not confirmed.** #335 compared 32M to 8M only on the 14-prompt spike set
(corpus measurement was explicitly out of scope there), so **no larger or contextual
model has been measured on the organic distribution**. The "32M doesn't escape it"
claim holds only for the spike set, not organic. Whether a larger/contextual model
clears the ceiling on organic prompts is the key open question (Salvage path 2). What
*is* established: 8M is near-random on organic regardless of prompt length or density.

### H3: Centroid construction (mis-placed centroids) — RULED OUT

Centroid geometry is sound: inter-centroid distance median 0.58, within-class seed
coherence mean 0.16–0.30. The failure is in organic query embedding positions, not
in centroid placement.

---

## Salvage recommendation

**The domain encoder axis is a dead end *as tested* — potion-base-8M with
hand-authored seed-phrase centroids — on organic dispatch prompts.** (Larger and
contextual models remain untested on the organic distribution; see path 2.)

The specific failure mode is that organic dispatch prompts — though no less domain-dense
than the spike prompts — do not share the *specific* seed-phrase token vocabulary the
centroids were built from, and are ~3× longer. **The seed-phrase centroids tested here**
do not separate the five domains on organic phrasing. This is scoped to the configuration
actually measured — it does **not** rule out other centroid configurations:
organic-derived centroids (path 3) and larger/contextual embedding models (path 2) are
both untested on organic. The established claim is "the hand-authored seed-phrase
centroids fail on organic", **not** "no centroid can work".

Viable paths forward (ranked by expected cost):

1. **Do not fix; remove the domain axis.** The #330 measurement showed the architecture
   independence premise (Phi=0.06) holds — the domain and posture axes are
   near-decorrelated — but the domain encoder is not accurate enough to beat the lexical
   baseline. **Current state (corrected):** the production matcher today is the **lexical
   matcher alone** — neither the encoder nor the posture extractors are wired into the
   hot path (the `src/claude_wayfinder/posture/` package is an offline library, not
   imported by the matcher; #330 §8 keeps hot-path integration out of scope). So "drop
   the domain axis" really means "do not integrate the encoder"; whether to *add* the
   posture extractors is a separate, still-open integration question (see the posture
   extractors' own 35.85% confident-wrong rate, #330 §7).

2. **Replace static bag-of-tokens with a contextual embedding model** (e.g.
   sentence-transformers `all-MiniLM-L6-v2`, ~80MB). These models capture semantic
   meaning rather than token bags, so domain-sparse abstract phrasing is less
   catastrophic. However: (a) cold-start budget is already exceeded, and (b) the
   accuracy improvement is unknown — the same organic corpus must be re-measured.

3. **Re-build centroids from organic prompts.** Average the embeddings of organic
   prompts per gold domain label as the centroid head, rather than hand-authoring seed
   phrases. **Use the no-smoke subset only** — drop the 59 duplicated harness probes
   (29× "implement the new module" → `code`, 30× "update the docs" → `docs_prose`).
   Training on all 168 would let those duplicates dominate the `code`/`docs_prose`
   centroids and reintroduce the very synthetic-vocabulary bias this report identifies
   as the root cause. This addresses H1 without changing the model. Risk: the no-smoke
   labeled distribution is small **and highly uneven** — non-any counts are code 45,
   project_meta 30, docs_prose 13, infra_deploy 5, **data 0** (the 16 `is_any` rows carry
   no single domain). So this path **cannot build a `data` centroid at all**, and
   `docs_prose` (13) and `infra_deploy` (5) are too sparse for stable centroids —
   workable only for code / project_meta without sourcing more labeled data.

4. **A trained classification head** (logistic regression or similar) over frozen
   potion-base-8M embeddings, trained on the organic labels. This converts the
   centroid spike into a proper supervised classifier. Requires the gold labels (#339)
   and a held-out evaluation set — the 168-entry corpus is too small for a
   reliable train/test split.

**The domain axis is not inherently a dead end.** What is established is narrower: *the
tested setup* — potion-base-8M with hand-authored seed-phrase centroids — fails on
organic prompts. Larger or contextual embedding models have **not** been measured on the
organic distribution (only 8M-vs-32M on the 14-prompt spike set), so the model-family
ceiling is suggested, not proven (see H2 verdict, path 2). The core finding is
methodological: validating a semantic encoder on a test set derived from the same
vocabulary as the training signal (seed phrases) produces optimistic accuracy that does
not transfer to organic data.

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
- `.tmp/probe_353_spike_vs_organic.py` — spike reproduction (reconstructed prompts), organic distribution, original gap quantification, centroid pairwise distances
- `.tmp/probe_354_canonical.py` — **PR #354 correction**: recompute spike length/density/accuracy from the canonical `SPIKE_GOLD_FOR_EVAL` (length median 15.5, density 0.051, 14/14)
- `.tmp/probe_353_centroids.py` — centroid geometry, within-class seed-phrase spread, cross-class overlap
- `.tmp/probe_353_organic_accuracy.py` — per-domain organic accuracy, length buckets, seed coverage vs accuracy
- `.tmp/probe_353_baseline_rate.py` — baseline rate analysis, broken-clock confirmation

> Note: the original `probe_353_spike_vs_organic.py` used reconstructed approximate
> spike prompts; `probe_354_canonical.py` supersedes its length/density figures with the
> canonical `SPIKE_GOLD_FOR_EVAL` strings. Accuracy (14/14) and all organic numbers are
> unchanged.

Interpreter: `<worktree>/.venv/Scripts/python.exe` (model2vec 0.8.2, confirmed by shadowing guard).
Platform: Windows 11 Pro, Python 3.12.13, numpy 2.4.6.
Corpus: `~/.claude/state/wayfinder-corpus/2026-06-12/wayfinder-corpus.jsonl` (168 entries, SHA `98454ca6...`).
Gold labels: `~/.claude/state/wayfinder-corpus/2026-06-12/gold-labels.jsonl` (168 entries, SHA `c38be656...`).
Model revision: `bf8b056651a2c21b8d2565580b8569da283cab23` (pinned in `_classifier.py`).
