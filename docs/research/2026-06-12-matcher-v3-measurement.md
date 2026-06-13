---
title: Matcher v3 Measurement Report — Semantic Two-Axis
date: 2026-06-12
issue: glitchwerks/claude-wayfinder#330
milestone: "Milestone 14 — Matcher v3 — semantic two-axis"
status: PRE-REGISTERED — thresholds committed before measurement run
---

# Matcher v3 Measurement Report — Semantic Two-Axis (#330)

**PRE-REGISTERED — thresholds committed before measurement run.**
Do not fill results sections until the harness has executed against the frozen artifacts below.

---

## 1. Context

This report is the evidence gate for the two-axis matcher design (domain encoder × posture
extractors) described in Spec E
(`docs/superpowers/specs/2026-06-08-semantic-routing-additive-evidence-synthesis.md`).
It is the capstone of the phase-A corpus construction (#338), gold-labeling (#339), encoder
spike (#335), and eval-harness (#340) work packages. The measurement establishes whether the
domain and posture signals are sufficiently decorrelated to justify additive composition
(§8.4 independence premise), and whether the composed system's confident-wrong rate is at
least no worse than the lexical baseline. No hot-path integration (`src/claude_wayfinder/`)
will proceed regardless of outcome until a separate integration issue is opened.

---

## 2. Artifact Identity

All artifacts must be verified against these checksums before the measurement run begins.

| Artifact | Path | Entries | SHA-256 |
|---|---|---|---|
| Corpus | `~/.claude/state/wayfinder-corpus/2026-06-12/wayfinder-corpus.jsonl` | 168 | `98454ca6544181118b7fb4870d3745be3146f56478f9b95c13f3c99ffa6cb090` |
| Gold labels (full, local-only) | `~/.claude/state/wayfinder-corpus/2026-06-12/gold-labels.jsonl` | 168 | `c38be6564b78e0de8a5358315783189bc9ff7ee548bb53924584e590c8de4cad` |
| Gold labels (redacted, committed) | `docs/research/2026-06-12-gold-labels-redacted.jsonl` | 168 | `e2be279be40037557d61a2079ca69d225fb323347e5815e4f7d69382a6e989d3` |
| Dispatch catalog | `~/.claude/state/dispatch-catalog.json` | live at run time | `4ac253647e8933dee7b4928644a1568dcbfeab0affd4b952bad7015904e728d0` |
| Eval harness | `scripts/corpus/eval/` | — | commit `8a35123` (#340) |

**Privacy note.** The corpus and full gold-label file contain `task_description` values (live
personal data). They are local-only artifacts and must not be committed to the repository.
Manifests and the redacted label file are the committed record.
Source: `docs/research/2026-06-12-corpus-manifest.json`; rubric §2 two-tier placement rule.

**Profiling prerequisite satisfied.** Per-field population profiling of the dispatch log was
completed before any analysis; findings are in `docs/research/2026-06-12-corpus-phase-a-profile.md`
(#338). This satisfies the §13.2 "pre-analysis per-field population profiling is mandatory"
requirement and the #288 lessons-learned that produced it.

---

## 3. Systems Under Test

Four systems run over the same 168-entry corpus in a single harness pass (Spec E §13.2).

| # | System | Description |
|---|---|---|
| 1 | Lexical baseline | Current matcher — keyword, glob, tool rules only. Baseline for all comparative metrics. |
| 2 | Encoder-alone | `potion-base-8M` centroid classifier, margin gate at 0.02, 5-way domain + domain-any signal. Selected over `potion-base-32M` per #335: identical accuracy, lower memory. |
| 3 | Extractors-alone | Posture extractors E1–E12 + R1–R3 (Spec E §10–§12.3); no domain signal. |
| 4 | Composed | Domain × posture: encoder output combined with extractor output via additive evidence synthesis (Spec E §2, §9.2). |

---

## 4. Metric Definitions

Implemented in `scripts/corpus/eval/_metrics.py` (commit `8a35123`, #340). Metric semantics
follow Spec E §13.3 exactly; the harness is the implementation of record.

**M1 — Error correlation (Phi coefficient).**
Binary error indicator vectors are built for two systems over the intersection of entries where
at least one system emitted `decision="delegate"`, conditioned on gold labels being present.
Phi = (n11·n00 − n10·n01) / sqrt((n11+n10)(n11+n01)(n00+n10)(n00+n01)).
An error is `decision="delegate"` and `agent ≠ gold_agent`. Returns `nan` when fewer than two
shared labeled delegate entries exist. Source: `_metrics.py` `metric_error_correlation` docstring;
Spec E §8.4.

**M2 — Error severity distribution (cell distance).**
Delegate-band errors are classified into three buckets — `adjacent` (low-harm posture pair,
i.e. assess↔critique per §12.3 R4), `cross_posture` (different posture, domain compatible),
`cross_domain` (different concrete domains) — using the §9.1 agent-cell map.
Source: `_metrics.py` `metric_error_severity`; Spec E §12.3 R4.

**M3 — Tier-C decisiveness rate.**
Fraction of extractor results where `tier_c_fired=True` in the result extras. Applies only to
systems 3 and 4 (the extractor systems); returns `nan` for systems 1 and 2.
Source: `_metrics.py` `metric_tier_c_decisiveness` docstring: "Above ~0.3 is a failing signal
(§10.3 g4)"; Spec E §10.3 guardrail 4.

**M4 — False-default-build rate.**
Among entries where `postures` in extras is empty (no extractor fired → build is the unmarked
default per §10.4), the rate at which the default-build route is wrong. Denominator is labeled
default-build rows only; unlabeled rows are excluded from both numerator and denominator.
Applies to systems 3 and 4 only; returns `nan` for systems 1 and 2.
Source: `_metrics.py` `metric_false_default_build`; Spec E §10.4.

**M5 — Braked-outcome candidate quality.**
Among entries where `extras["braked"]=True` (E12 fired and braked a confident result to
advisory), the fraction where `gold_agent` appears in the candidate alternatives list.
Applies to systems 3 and 4 only; returns `nan` for systems 1 and 2.
Source: `_metrics.py` `metric_braked_candidate_quality`; Spec E §12.3 R2 / P3 residual.

**M6 — Confident-wrong rate.**
Fraction of `decision="delegate"` entries where `agent ≠ gold_agent`. Computed per system.
Source: `_metrics.py` `metric_confident_wrong_rate`; Spec E §13.3 metric 6.

**The decisive error-correlation comparison (§8.4).**
The architecture premise is that the domain axis (encoder, system 2) and the posture axis
(extractors, system 3) are orthogonal — they ask different questions on different inputs
(Spec E §8.2, §8.3 Level 2/3). The decisive Phi is therefore
**Phi(encoder-alone errors, extractors-alone errors)** — systems 2 vs 3.
The harness's `compute_all_metrics` primary column reports Phi(lexical, extractors) —
systems 1 vs 3 — as the decorrelation baseline. Both columns will be reported; the kill
criterion is applied to Phi(encoder, extractors). Source: `_metrics.py` `compute_all_metrics`
docstring; Spec E §8.4.

---

## 5. Pre-Registered Kill Criteria

Thresholds below are fixed now, before any measurement run. They may not be adjusted after
seeing the data. For criteria where Spec E gives no numeric value, the derivation is marked
`unverified:` per the cite-sources standard.

### 5.1 Correlation kill — Phi(encoder-alone, extractors-alone)

**Kill threshold: Phi ≥ 0.60 → stop; architecture premise fails.**

**Pass band: Phi < 0.35 → the two signals are sufficiently independent.**

**Gray zone: 0.35 ≤ Phi < 0.60 → document; do not integrate without further analysis.**

Derivation: Spec E §8.4 states the independence ideal is Phi ≈ 0 (completely decorrelated
errors) and the failure case is identical errors (Phi → 1.0). The spec gives no numeric kill
threshold; the following derivation is `unverified:` (no corpus data to calibrate against).

- A Phi of 0.60 corresponds roughly to the point at which shared variance between the two
  error signals exceeds 35% (0.60² = 0.36). At that level the "agreement = confidence"
  safety net described in §4.2 and §8.2 is materially compromised — two signals agreeing
  on a delegation decision offer little more evidential weight than one signal alone.
  Threshold chosen conservatively toward the pass side (0.60 rather than 0.80) because
  the organic corpus is small (n=168; delegate entries are a subset) and Phi estimates at
  small n are noisy.
- A pass band ceiling of 0.35 is `unverified:` — chosen as the point below which shared
  variance is under 12% (0.35² ≈ 0.12), consistent with "low correlation" in the
  psychometric literature. At this level the additive combination recovers meaningful
  independence gain over either signal alone.
- The gray zone (0.35–0.60) does not produce a go or no-go; it produces a finding that
  the architecture needs redesign work before integration.

If `metric_error_correlation(encoder, extractors, labels)` returns `nan` (insufficient
labeled delegate entries in the intersection), this criterion is recorded as "insufficient
data — not falsified, not confirmed."

### 5.2 Tier-C decisiveness kill — M3

**Kill threshold: Tier-C decisiveness rate > 0.30 → extractor redesign required; do not
proceed to integration.**

Derivation: `_metrics.py` `metric_tier_c_decisiveness` docstring states explicitly:
"Above ~0.3 is a failing signal (§10.3 g4)." This document rounds the `~0.3` to the
strict inequality **> 0.30**, preserving the spec's intent without inflating the threshold.
A result of exactly 0.30 is in the gray zone; document it and flag for review.
Source: `_metrics.py` line 98–99; Spec E §10.3 guardrail 4.

Applies only to systems 3 (extractors-alone) and 4 (composed). The metric is `nan` for
systems 1 and 2, which do not run Tier-C extractors; `nan` is not a failing result for
those rows.

### 5.3 Confident-wrong no-go — M6

**No-go rule: composed system (system 4) confident-wrong rate must be ≤ lexical baseline
(system 1) confident-wrong rate on the same corpus. Strict ≤.**

If system 4 confident-wrong rate > system 1 confident-wrong rate → no-go, regardless of
aggregate hit rate or other metrics.

Derivation: Spec E §13.4 states "Confident-wrong rate not improved vs baseline → no-go
regardless of aggregate hit rate." "Not improved" is operationalized here as strict greater-
than (system 4 > system 1 = no-go); equal rates are a borderline pass — document the margin.
Source: Spec E §13.4; `_metrics.py` `metric_confident_wrong_rate`.

This criterion uses systems 1 and 4. Per the harness `compute_all_metrics` comment, metric 6
is computed per system; the comparison is a post-computation arithmetic check, not a single
metric output.

---

## 6. Secondary Interpretive Rules (pre-registered)

These rules govern how results are reported and interpreted; they do not change the kill criteria.

**Per-cell conclusions only where n ≥ 30.**
Only 3 of 17 corpus cells meet the floor-30 target: `needs_more_detail|short|fp=no`,
`delegate|short|fp=yes`, `delegate|long|fp=yes`. Per-cell metric breakdowns are reported
only for these three cells. For all other cells: "insufficient data — not reportable at
per-cell resolution." Source: `docs/research/2026-06-12-corpus-phase-a-profile.md` §6,
§9 ("only 3 of 17 cells meet the floor of 30").

**Smoke-test rows reported separately.**
59 of 168 records (35.1%) are one of two repeated harness probe strings ("implement the new
module" × 29, "update the docs" × 30). Per the gold-labeling report (finding 1, #339),
aggregate metrics must be reported both including and excluding these rows to give an accurate
picture of matcher performance on organic prompts.
Source: `docs/research/2026-06-12-gold-labeling-report.md` §Findings §1.

**No-mention subset (134 rows) for value-add measurement.**
E11 directive pass-through determined `gold_agent` on 31 rows. For measuring encoder and
extractor value-add independent of explicit-mention signal, use the 134-row no-mention cut
(rows where `agent_mentions` is empty). The 137-row "E11-not-fired" cut is available but
exposes mention signal the systems-under-test may act on at eval time.
Source: `docs/research/2026-06-12-gold-labeling-report.md` §Findings §4.

**`self_handle_unaided` band.**
Only 3 organic entries in this band (pre-corpus). Phase B gold-label distribution confirms
scarcity. Conclusions for this band are flagged as "insufficient data" in all metrics tables.
Source: `docs/research/2026-06-12-corpus-phase-a-profile.md` §9 caveat 4.

**`nan` handling.**
Any metric returning `float('nan')` is reported as "n/a" in the results table. `nan` is not
a failing result; it means the metric's evaluation condition was not met in this corpus
(e.g. no labeled delegate entries in the intersection for Phi, no braked outcomes for M5).

**Posture distribution note.**
Postures `critique` (0), `verify` (2), `assess` (2), and `diagnose` (4) are rare in the
organic corpus. Metrics involving these postures — especially Tier-C decisiveness for the
E10/E12-driven routes — may return `nan` or rest on very small denominators. Note the
denominator alongside any such metric.
Source: `docs/research/2026-06-12-gold-labeling-report.md` §Findings §3.

---

## 7. Results — EMPTY PLACEHOLDERS

_All sections below are to be filled after the measurement run. Do not populate before the
harness executes against the frozen artifacts in §2._

### 7.1 Per-System Metrics Table

| Metric | Lexical (1) | Encoder (2) | Extractors (3) | Composed (4) |
|---|---|---|---|---|
| M1 Phi(sys, lexical) | — | — | — | — |
| M3 Tier-C decisiveness | n/a | n/a | — | — |
| M4 False-default-build rate | n/a | n/a | — | — |
| M5 Braked candidate quality | n/a | n/a | — | — |
| M6 Confident-wrong rate | — | — | — | — |

_— to be filled after the measurement run —_

### 7.2 Decisive Phi — Encoder vs Extractors

| Comparison pair | Phi | Entries in intersection (delegate band) |
|---|---|---|
| Encoder (2) vs Extractors (3) | — | — |
| Lexical (1) vs Extractors (3) | — | — |

_— to be filled after the measurement run —_

### 7.3 Error Severity Distribution (Composed System)

| Severity class | Count | Share of delegate errors |
|---|---|---|
| adjacent | — | — |
| cross_posture | — | — |
| cross_domain | — | — |

_— to be filled after the measurement run —_

### 7.4 Per-Criterion Verdicts

| Criterion | Threshold | Measured value | Verdict |
|---|---|---|---|
| 5.1 Correlation kill | Phi(encoder, extractors) ≥ 0.60 | — | — |
| 5.2 Tier-C decisiveness kill | > 0.30 | — | — |
| 5.3 Confident-wrong no-go | composed > lexical | — | — |

_— to be filled after the measurement run —_

### 7.5 Go / No-Go Recommendation

_— to be filled after the measurement run —_

---

## 8. Out of Scope

Regardless of measurement outcome, this report does not authorize:

- Changes to `src/claude_wayfinder/` (the production matcher hot path).
- Hot-path integration of the encoder, extractors, or composed system.
- Any modification to the deployed agent roster or routing table.

A separate integration issue must be opened if the measurement warrants it. This is the
shadow-mode discipline stated in Spec E §13.4: "kill criteria are written down before the
experiment."

---

## 9. Related

#330 (this measurement) · #338 (corpus phase A) · #339 (gold labeling) · #340 (eval harness)
· #328 (extractor library) · #329 (potion-base-8M spike) · #335 (potion-base-32M spike)
· #325 (design convergence) · #293 (original dispatch-log substrate) · Spec E §13.
