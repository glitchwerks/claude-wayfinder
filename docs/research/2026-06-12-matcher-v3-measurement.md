---
title: Matcher v3 Measurement Report — Semantic Two-Axis
date: 2026-06-12
issue: glitchwerks/claude-wayfinder#330
milestone: "Milestone 14 — Matcher v3 — semantic two-axis"
status: |
  PRE-REGISTERED — thresholds committed before measurement run
  MEASURED 2026-06-12 — verdict: NO-GO (see §7.5)
---

# Matcher v3 Measurement Report — Semantic Two-Axis (#330)

**PRE-REGISTERED — thresholds committed before measurement run (commit `ec2251d`, §1–§6).**
**MEASURED 2026-06-12 — verdict: NO-GO (§7.5).** §1–§6 are the frozen pre-registration; §7 holds the results.

**MEASURED 2026-06-12 — verdict: NO-GO (see §7.5).**

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

## 7. Results

_Measured 2026-06-12 against the frozen artifacts in §2. Numbers cross-checked three ways
(§7.6). The pre-registered thresholds in §5 were not altered after seeing these results._

### 7.1 Per-System Metrics Table

| Metric | Lexical (1) | Encoder (2) | Extractors (3) | Composed (4) |
|---|---|---|---|---|
| M1 Phi(sys, lexical) | n/a (baseline) | 0.0000 (degenerate — see §7.2) | −0.0040 | −0.0040 |
| M3 Tier-C decisiveness | n/a | n/a | 0.0357 | 0.0357 |
| M4 False-default-build rate | n/a | n/a | 0.5625 | 0.5625 |
| M5 Braked candidate quality | n/a | n/a | 0.0000 | 0.0000 |
| M6 Confident-wrong rate | 0.1507 | n/a (0 delegate decisions) | 0.3585 | 0.3585 |

_Delegate-band counts: Lexical 73, Extractors 53, Encoder 0, Composed 53 (of 168 entries)._

### 7.2 Decisive Phi — Encoder vs Extractors

| Comparison pair | Phi | Entries in intersection (delegate band) |
|---|---|---|
| Encoder (2) vs Extractors (3) | 0.0000 | 0 (degenerate — see note) |
| Lexical (1) vs Extractors (3) | −0.0040 | secondary baseline |

The decisive §8.4 test requires both axes to enter the delegate band so their error vectors can co-occur; because the encoder delegated on 0 of 168 entries, its error-indicator vector is all-zeros and Phi is mathematically undefined — the `metric_error_correlation` implementation hits its `denom_sq <= 0 → return 0.0` branch and returns 0.0000. Per the pre-registered §5.1 escape clause (the nan / insufficient-data case), this result is recorded as **"insufficient data — the premise was neither confirmed nor falsified"**, not as a Phi < 0.35 pass. The 0.0000 figure is a degenerate/undefined result, not a decorrelation pass.

### 7.3 Error Severity Distribution (Composed System)

| Severity class | Count | Share of delegate errors |
|---|---|---|
| adjacent | 0 | 0% |
| cross_posture | 16 | ~84.2% |
| cross_domain | 3 | ~15.8% |

Total delegate-band errors: 19. Composed severity is identical to extractors-alone across all three buckets — a direct consequence of the encoder delegating 0 times (§7.6): the composed system received only "domain-any" from the encoder axis and collapsed to pure posture routing, making it byte-identical to extractors-alone.

### 7.4 Per-Criterion Verdicts

| Criterion | Threshold | Measured value | Verdict |
|---|---|---|---|
| §5.1 Correlation kill | Phi(encoder, extractors) ≥ 0.60 | 0.0000 (degenerate — 0 shared delegate entries) | INSUFFICIENT DATA — not killed, not passed (premise untestable on this corpus) |
| §5.2 Tier-C decisiveness kill | > 0.30 | 0.0357 | PASS (well under threshold) |
| §5.3 Confident-wrong no-go | composed ≤ lexical | composed 0.3585 vs lexical 0.1507 | NO-GO (composed is 2.38× the baseline confident-wrong rate) |

### 7.5 Go / No-Go Recommendation

**NO-GO.**

The pre-registered §5.3 confident-wrong criterion is triggered: the composed system's
confident-wrong rate (0.3585) exceeds the lexical baseline (0.1507) by a factor of 2.38.
No hot-path integration proceeds. This finding is consistent with §8 Out of Scope — the
no-go and the out-of-scope rule independently reach the same conclusion.

**Dominant mechanism: the domain encoder is empirically inert on organic data.**

The potion-base-8M classifier produced a near-uniform 5-way domain distribution on every
one of the 168 prompts. Measured entropy ranged 2.3095–2.3214 bits against a 5-class
maximum of log₂5 ≈ 2.3219 bits; the top1−top2 margin had a median of 0.0193. Under the
v0 calibration (entropy-any > 1.5, margin-any < 0.04, both fixed in #340 / encoder spike
§7 and not tunable post-hoc), 100% of prompts were marked domain-any: 138 by both gates,
30 by the entropy gate alone, 0 by the margin gate alone. Domain-any routes to advisory
by construction (`run_encoder` only delegates on a confident domain), so the encoder
delegated 0 times across all 168 entries.

**Knock-on consequences.**

(a) The composed system, receiving only "any" from the domain axis, collapsed to pure
posture routing — it is byte-identical to extractors-alone across every metric (M1, M3,
M4, M5, M6, and the error-severity distribution in §7.3). (b) The decisive §8.4
correlation test (Phi(encoder, extractors)) could not be computed: with zero encoder
delegate entries the error-indicator intersection is empty, Phi is mathematically
undefined, and the §5.1 criterion is recorded as "insufficient data — premise untestable"
(§7.2, §7.4). (c) The composed system therefore inherits the extractor posture-router's
35.85% confident-wrong rate wholesale — worse than the lexical baseline's 15.07%.

**Honest scoping of the encoder finding.**

The near-uniform output must be reconciled against the accuracy reported in the #329 /
#335 encoder spikes that selected potion-base-8M. Three live hypotheses, none resolved
here: (i) the spikes' accuracy was measured on a synthetic or curated distribution that
does not resemble organic dispatch prompts; (ii) the classifier's centroids are not
loading or applying correctly in the harness path; (iii) potion-base-8M genuinely cannot
separate these five domains on short organic prompts. Root-causing the encoder is
explicitly out of scope for #330 (this issue measures; it does not fix) and belongs in a
separate follow-up issue. The no-go verdict stands regardless of which hypothesis holds,
because it rests on the composed system's measured confident-wrong rate, not on the
encoder diagnosis.

**What the kill criteria bought us.**

Written before the run, the pre-registered thresholds converted an ambiguous "the composed
numbers look bad" into a clean, unambiguous NO-GO with a precisely localized cause: the
domain axis carries no signal on organic data. The secondary subset cuts confirm the
picture rather than overturning it. On the 134-row no-mention cut (§6 "No-mention subset")
the extractor/composed confident-wrong rate rises to 0.8571 — the mention signal was
masking how often posture-only routing is wrong on genuinely hard prompts. On the no-smoke
cut (59 probe rows dropped), composed is unchanged at 0.3585 while lexical rises to 0.2558
— the gap narrows but composed remains materially worse. Neither subset cut reverses the
verdict.

### 7.6 Measurement Provenance

Run date: 2026-06-12. Interpreter: worktree `.venv` (model2vec 0.8.2 present; encoder and
composed systems both ran — no metric-skips due to missing dependencies).

Numbers were cross-checked two independent ways: the `scripts/corpus/eval` harness CLI
(#340) and a standalone driver `.tmp/run_330.py`; both produced identical per-system
metrics for all four systems. A third direct encoder probe confirmed the 100%-domain-any
finding and measured the entropy and margin distributions reported in §7.5.

The scratch artifacts (`.tmp/run_330.py`, `.tmp/run_330_results.json`,
`.tmp/probe_encoder.py`) are not committed — scratch-file discipline applies. The
committed report is the durable record. Anyone can reproduce via the one-command harness
CLI (`python -m scripts.corpus.eval`) in §2 against the frozen artifacts.

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
