---
title: Semantic routing — Spec D — Predicate-First Additive Routing
date: 2026-06-08
tracking: glitchwerks/claude-wayfinder#325
status: draft — PREMISE CHALLENGED by approach-critic pass 2026-06-08 (empty-feasible-set / FLAW 3); see #325. Superseded pending field-wide additive-synthesis revisit
touches:
  - src/claude_wayfinder/match/_main.py
  - src/claude_wayfinder/match/_match.py
  - src/claude_wayfinder/match/_types.py
  - src/claude_wayfinder/match/_decide.py
  - src/claude_wayfinder/build_catalog/_main.py
  - src/claude_wayfinder/build_catalog/_process.py
  - src/claude_wayfinder/build_catalog/_validate.py
related:
  - glitchwerks/claude-wayfinder#325
  - docs/research/2026-06-07-semantic-routing.md
  - docs/superpowers/specs/2026-06-07-semantic-routing-dual-signal-ensemble.md
  - docs/superpowers/specs/2026-06-07-semantic-routing-classifier-prefilter.md
  - docs/superpowers/specs/2026-06-07-semantic-routing-structured-predicate-refinement.md
skills_relevant:
  - dispatch-authoring
  - project-review
---

# Semantic routing — Spec D — Predicate-First Additive Routing

Tracking issue: [#325](https://github.com/glitchwerks/claude-wayfinder/issues/325)
Sibling specs:
- [Spec A — Parallel Dual-Signal Ensemble](2026-06-07-semantic-routing-dual-signal-ensemble.md)
- [Spec B — Coarse-to-Fine Semantic Classifier Pre-Filter](2026-06-07-semantic-routing-classifier-prefilter.md)
- [Spec C — Structured Intent Predicate Refinement](2026-06-07-semantic-routing-structured-predicate-refinement.md)

Author: doc-writer (2026-06-08)
Status: **draft — high-level approach locked; calibration is the primary open work**

---

> **Maturity note — read before proceeding**
>
> The high-level approach described here — additive evidence routing with predicates as a co-primary signal, no destructive filtering at any stage — was locked during the 2026-06-08 design session. This spec is decision-ready at the architecture level. The open work is **calibration**: specifically, boost magnitudes for the predicate signal, and completeness of the predicate set for the full catalog. None of the open items are correctness cliffs. A bad boost weight degrades ranking gracefully; it does not erase a candidate or produce an unrecoverable mis-route.

---

## 1. Summary

**Spec D — Predicate-First Additive Routing** describes an additive evidence architecture for the claude-wayfinder dispatch matcher that eliminates all destructive filtering. Every agent and skill in the catalog starts from a zero baseline and is always scored — nothing is ever pruned before ranking. Multiple additive signals contribute to each entry's score:

- **Structured intent predicates** (the new primary signal) — boost agents whose declared predicate requirements are satisfied by facts extracted from the dispatch context.
- **Lexical keyword/glob/tool scoring** (the existing matcher, unchanged) — one co-equal additive contributor.
- **Semantic classification** (OPTIONAL, deferred to phase 2) — demoted from a gate to a non-destructive additive boost; never prunes.

Boosts are a **commutative sum** across all three signals. No signal is "first" or "last" in the architectural sense; they differ only in calibrated weight. The existing 7-decision ladder (`decide()` in `_decide.py:154–287`) ranks the summed scores unchanged: the top agent becomes a `delegate` decision when it clears `_DELEGATE_THRESHOLD` (0.85) with a `_DELEGATE_GAP` (0.2) lead, and close cases produce `ambiguous`/`advisory`. Because nothing is pruned, the `mixed_content`/lane branch and the gap thresholds keep their calibrated meaning.

**The load-bearing property:** a predicate boost must be large enough that an agent can clear `_DELEGATE_THRESHOLD` on predicate evidence alone, with zero keyword score. This is how a no-keyword prompt routes — it is the stated premise of issue [#325](https://github.com/glitchwerks/claude-wayfinder/issues/325). A predicate miss (the disambiguating fact is absent from context) means no boost: the agent competes on its remaining signals. This is monotonic and recoverable — adding evidence can only promote, never erase.

**This is not "no semantics."** Spec D demotes the semantic classifier to a contributor that can never erase a candidate. Semantics remain available as an additive boost in phase 2.

### Relationship to Specs A, B, and C

**Spec B** uses the semantic classifier as a destructive gate — prune the candidate set first, then run the keyword scorer within survivors. An adversarial review of B identified a premise failure: a pure pre-filter only subtracts candidates, so it cannot route to an agent that has no keyword match. A confident misclassification silently hard-prunes the correct agent with no recovery path.

**Spec C** is a layer on top of B: predicates as a within-category refiner, still downstream of B's hard prune. C rescues B's premise within a correctly-classified category but inherits the hard-prune failure for misclassified dispatches.

**Spec D promotes C's predicate signal**: runs it additively over the full roster (boost, never admit/remove), and demotes the classifier to an optional additive contributor. Removing the destructive prune simultaneously fixes B's premise failure (no-keyword reach), eliminates the hard-prune auditability hole where erased agents are invisible in the output, and incidentally preserves `mixed_content` and the gap thresholds. The change is architectural, not a detail: the distinction between "additive boost" and "destructive filter" is not tunable.

**Spec A** (Parallel Dual-Signal Ensemble) runs both signals at the same granularity and treats disagreement as a first-class signal. Spec D's predicate signal operates at finer granularity than A's semantic signal and requires no encoder in v1.

---

## 2. Core Requirements

The same six requirements from Specs A, B, and C apply:

1. **Open source + fully locally runnable.** No hosted embedding APIs, no proprietary models. Offline on the user's machine.
2. **Deterministic encoding.** Same input → same output every run. (Primary user concern.) Spec D in v1 is the strongest of the four designs on this dimension: predicate extraction is deterministic regex/heuristic on a fixed string. No encoder is required.
3. **No generative LLM in the hot path.** Deterministic extraction only; autoregressive classification is out.
4. **Auditability.** A human must be able to understand why a route was chosen. Spec D is the strongest of the four designs on this dimension: every contribution to the final score is a named, discrete, inspectable rule. The rationale shows the full additive breakdown and the full ranked roster — nothing is hidden.
5. **Latency.** Sub-millisecond ideal; ~10–50ms tolerable only if confined to a subset of dispatches. Spec D v1 adds only regex evaluation — sub-millisecond, always.
6. **The lexical keyword model is currently too strong to be a sole confident primary.** A single soft keyword can manufacture a confident wrong `delegate`. Spec D addresses this by making keywords one additive term among several. The gap threshold prevents lone-signal over-confidence because a predicate miss leaves keywords competing at their existing calibration, while a predicate hit lifts the correct agent above the threshold with the additive sum.

---

## 3. Architecture Overview

The architecture is a scored ranking over the full catalog, with three additive signal layers:

**Layer 1 — Predicate boost (new; primary for v1).** The dispatch context is analyzed by a deterministic extractor that derives structured predicate facts (`failure_observed`, `root_cause_known`, `scope`, `source_of_truth_present`, `posture`). Each catalog entry declares its predicate requirements in a new `predicate_requirements` field. When the dispatch's extracted predicates satisfy an entry's declared requirements, that entry receives a boost added to its running score.

**Layer 2 — Lexical keyword/glob/tool score (existing; unchanged).** The current `score()` function in `_match.py:276–383` produces a float per entry based on keyword set intersection, path glob matching, and tool mentions. This score continues to be computed for every entry, exactly as today.

**Layer 3 — Semantic classification boost (optional; phase 2 only).** A Model2Vec encoder could produce a cosine-similarity boost per entry. This layer is intentionally deferred. When added, it is additive — it contributes to the sum but never zeros out an entry. It is not described further in this spec; its shape would follow Spec A §5.

**Score composition:** Each entry's final score is the sum of all boost contributions from layers 1, 2, and 3, clamped to [0.0, 1.0]. The existing short-circuit paths (command prefix match or agent mention → score 1.0) remain unchanged and represent maximally confident evidence: the user named the target explicitly.

**Decision:** `decide()` (`_decide.py:154–287`) receives the re-ranked full roster and applies the 7-branch ladder unchanged. The ladder's thresholds retain their calibrated meaning because the full roster is always present.

---

## 4. Pipeline / Control Flow

### 4.1 Current orchestration (verified on disk 2026-06-08)

```
build_features(context)        # _match.py:128–178 — extracts Features; discards raw task_description
    ↓
score_entries(entries, features) # _match.py:469–502 — scores full catalog, returns (scored_agents, scored_skills)
    ↓
decide(scored_agents, scored_skills, features, entries)  # _decide.py:154–287
```

Orchestration is inlined in `match/_main.py:206–225` (verified 2026-06-08). The raw `task_description` is read inside `build_features()` and discarded after stemming (`_match.py:146–148`). After that point, the raw text is unavailable to the pipeline.

### 4.2 Spec D additions

Spec D inserts two new non-destructive stages in `_main.py`:

```
extract_predicates(context)      # NEW — off raw context before build_features discards text
    ↓
build_features(context)          # unchanged
    ↓
score_entries(entries, features) # unchanged — keyword/glob/tool scores only
    ↓
apply_predicate_boosts(scored_agents, predicates, catalog)  # NEW — additive boost, re-sorts, removes nothing
    ↓
decide(scored_agents, scored_skills, features, entries)     # unchanged
```

`extract_predicates(context)` reads from the raw context dict (the same dict passed to `build_features()`), before `build_features()` discards the raw `task_description`. It must run as a sibling to `build_features()`, not after it.

`apply_predicate_boosts(scored_agents, predicates, catalog)` receives the already-scored `ScoredEntry` list from `score_entries()`, adds to each entry's score where the dispatch predicates satisfy that entry's declared `predicate_requirements`, clamps the result to [0.0, 1.0], and re-sorts. It does not remove any entry.

`score()` and `score_entries()` in `_match.py` stay keyword/glob/tool-only. Predicates must not be threaded through `score()` — keeping the boost as its own stage preserves the clean separation of concerns and makes both layers independently auditable.

`decide()` in `_decide.py` is unchanged. A small addition to `_rationale_for()` (`_decide.py:295–350`) surfaces predicate contributions in the rationale string (e.g., `predicates: failure_observed, scope=spans-layers`), but the ranking logic and thresholds are untouched.

**Existing short-circuits stay.** Command prefix match and explicit agent mention hard-set score to 1.0 (`_match.py:309–314`, verified 2026-06-08). These are legitimately maximally confident signals — the user named the target. Predicate and keyword boosts are additive evidence below that ceiling.

**Skills.** `score_entries()` scores skills in parallel with agents and returns `scored_skills` separately. Skills are not pruned (consistent with the no-filter principle). Whether predicate boosts apply to skills is flagged as an open question (§12).

### 4.3 Worked trace 1 — no-keyword reach

**Prompt:** `"my CI went red after the last merge"`

**Predicate extraction:**
- Stacktrace absent, but "went red" and "after the last merge" are failure-observation patterns → `failure_observed=true`
- File paths include `.github/workflows/` (inferred from CI context) → `scope=spans-layers`
- No "why" phrasing → `root_cause_known` is unset (abstain)

**Keyword scoring:** `investigator` scores approximately 0.0 — "CI", "red", "merge" do not appear as trigger keywords. `code-writer`, `code-reviewer` also score near 0.0.

**Predicate boost:** `investigator` declares `failure_observed=true + scope=spans-layers → +B` (where B is the calibrated boost magnitude, to be determined). Both predicates fire. Boost lifts `investigator` above `_DELEGATE_THRESHOLD` (0.85) with the required gap.

**Decision:** `delegate investigator`. Keyword contribution: 0.0. Predicate contribution: sufficient to clear the threshold alone.

This trace demonstrates the premise fix. Spec B cannot produce this result: B's classifier might classify the prompt as `investigation`, but `investigator` still requires a keyword match inside the surviving candidate set. With zero keyword score, `investigator` routes as `self_handle_unaided` or `advisory` at best. Spec D routes `delegate investigator` on predicate evidence alone.

### 4.4 Worked trace 2 — additive arbitration

**Prompt:** `"review the python code to see why my answer doesn't return correctly"`

**Predicate extraction:**
- "doesn't return correctly" is a failure-observation pattern → `failure_observed=true`
- "why" phrasing → `root_cause_known=false`
- No infra file paths → `scope` unset (code-only default)

**Keyword scoring (selected entries):**
- `code-reviewer`: keywords `review`, `code` → score ≈ 0.75 (`unverified:` estimate based on scoring formula in `_match.py`; exact value depends on catalog trigger definitions not read in full)
- `code-writer`: keywords `code`, `python` → score ≈ 0.75 (`unverified:` same caveat)
- `investigator`/`systematic-debugging`: keywords `return`, `correct` may match partially → score ≈ 0.25–0.50 (`unverified:`)

**Predicate boost:** `investigator` declares `failure_observed=true + root_cause_known=false → +B`. Both predicates fire. `code-reviewer` declares no predicate requirements (or requirements not met) → no predicate boost.

**Additive sum:**
- `investigator`: ≈ 0.25–0.50 (keyword) + B (predicate) = potentially above `_DELEGATE_THRESHOLD` depending on B
- `code-reviewer`: ≈ 0.75 (keyword) + 0.0 (predicate)

If B is calibrated such that `0.25 + B > 0.85` and the gap to `code-reviewer` exceeds 0.2, the decision is `delegate investigator`. If the scores are within the 0.2 gap, the decision is `ambiguous` or `advisory`, surfacing both candidates. Neither agent is pruned.

This trace demonstrates additive arbitration without destructive filtering. Unlike Spec B's trace (§4.2 in `2026-06-07-semantic-routing-classifier-prefilter.md`), `code-writer` remains visible in the ranked roster even though predicates do not boost it.

---

## 5. Predicate Extraction & Index Shape

### 5.1 Predicate set

The candidate predicates identified in Spec C §3.4 (2026-06-07) apply here unchanged:

| Predicate | Type | Distinguishing example |
|---|---|---|
| `failure_observed` | boolean | code-writer (no failure) vs investigator (failure present) |
| `root_cause_known` | boolean | investigator (cause unknown) vs code-writer (cause known, fix needed) |
| `scope` | categorical: `code-only` / `spans-layers` | debugger (code-layer) vs investigator (multi-layer: code + infra + data) |
| `source_of_truth_present` | boolean | code-reviewer (general review) vs auditor (conformance against a standard) |
| `posture` | categorical: `build` / `assess` / `critique` | code-writer vs code-reviewer vs inquisitor |

Source: Spec C §3.4 (2026-06-07-semantic-routing-structured-predicate-refinement.md, on disk 2026-06-07).

### 5.2 Extraction mechanism

Extraction is deterministic heuristics and regex applied to the **raw context dict** — specifically the raw `task_description` string and existing structured context fields (`file_paths`, `tool_mentions`, `command_prefix` — `skills/dispatch/SKILL.md`, on disk 2026-06-07). No encoder and no labelled corpus are required for v1.

Representative extraction rules:

| Predicate | Signal | Pattern |
|---|---|---|
| `failure_observed` | `task_description` | stacktrace/traceback patterns; "went red", "is broken", "fails", "error"; past-tense failure verbs |
| `root_cause_known=false` | `task_description` | "why", "what causes", "figure out", "investigate" |
| `scope=spans-layers` | `file_paths` | paths matching `.github/workflows/**`, `terraform/**`, `*.tf`, `docker-compose.*`, infra glob patterns |
| `source_of_truth_present` | `task_description` or `file_paths` | a referenced document path with a known doc extension; explicit "per the spec", "according to the contract" phrasing |
| `posture=critique` | `task_description` | "adversarially", "tear apart", "be harsh", "find every flaw" |

A predicate for which no pattern fires is **absent** (not false). Absent predicates do not contribute a boost in either direction.

### 5.3 Index shape

A purely heuristic extractor has no persisted index. No `.npz` file, no centroid matrix, no build-time embedding step. The catalog build remains byte-deterministic. The float/encoder/centroid concerns from Specs A and B §9 do not apply in v1.

### 5.4 Per-entry predicate requirements (new catalog field)

Each catalog entry gains a new `predicate_requirements` field on `CatalogEntry` (`_types.py:179–207`). This field declares which predicate values, when present in the dispatch, trigger a boost for this entry. The schema must be designed and is flagged as an open question (§12). A minimal v1 shape:

```json
"predicate_requirements": {
  "failure_observed": true,
  "scope": "spans-layers"
}
```

An entry's boost fires when **all** declared requirements are satisfied by the dispatch's extracted predicates. When multiple predicates fire, the boosts stack (see §6).

`load_catalog()` must ignore unknown fields so that entries without `predicate_requirements` load without modification (backward-compatible). The catalog build pipeline (`build_catalog/_main.py`, `_process.py`, `_validate.py`) must be updated to parse, validate, and pass through the new field.

---

## 6. Decision & Scoring Contract

### 6.1 Score composition

Each entry's final score is:

```
final_score = min(keyword_score + predicate_boost_sum, 1.0)
```

Where `predicate_boost_sum` is the sum of all individual predicate boosts that fired for this entry. In v1, `semantic_boost` is 0.0 for all entries (phase 2 addition).

The existing short-circuit signals (command prefix, agent mention → 1.0) are treated as maximal evidence and are not subject to this formula — they bypass the boost stage.

### 6.2 Boost magnitude requirements

The boost magnitude B must satisfy two constraints simultaneously:

1. **Premise constraint:** an entry with zero keyword score must be able to clear `_DELEGATE_THRESHOLD` (0.85) on predicate evidence alone. This requires: `0.0 + B ≥ 0.85`. In practice, B must exceed 0.85 to also clear `_DELEGATE_GAP` (0.2) above the next-best entry.

2. **False-confidence constraint:** an entry with a low but non-zero keyword score (e.g., 0.25 from a partial match) must not produce a spurious `delegate` when only a single predicate fires. This sets a practical ceiling on B when multiple predicates can stack.

These two constraints create the calibration tension described in §10. Boost stacking (when multiple predicates fire) requires a cap or normalization to prevent inflation (open question, §12).

### 6.3 Mapping to the 7-decision ladder

`decide()` receives `scored_agents` after the predicate boost has been applied. From `decide()`'s perspective, it receives a re-ranked sorted list of `ScoredEntry` objects and applies the same thresholds:

| Condition | Decision |
|---|---|
| Top agent score ≥ 0.85, gap ≥ 0.2 | `delegate` |
| Top skill score ≥ 0.5 (evaluated before advisory) | `self_handle` |
| ≥ 2 agents clamped at 1.0 on disjoint path lanes | `mixed_content` |
| Top agent score ≥ 0.5, gap < 0.2 or score < 0.85 | `advisory` |
| Feature density < 2 | `needs_more_detail` |
| All scores < 0.5 | `self_handle_unaided` |

None of these branches change. The predicate boost affects which entry occupies the top position and what score it carries into the ladder — not the ladder's logic.

### 6.4 Agents vs skills scoring

Agents and skills are scored in parallel by `score_entries()` and feed different decision branches (top agent → `delegate`/`advisory`; a skill clearing its own bar → `self_handle`). "Highest wins" is per kind, composed by the existing ladder — not one global max across both pools. Predicate boosts that apply to skills would follow the same additive pattern, but skill-boost semantics are flagged as an open question (§12).

### 6.5 Rationale additions

`_rationale_for()` (`_decide.py:295–350`) should be extended to surface predicate contributions. When a predicate boost fired, the rationale segment reads:

> `predicates: failure_observed=true, scope=spans-layers (+B)`

When no predicate boost fired, the segment is omitted. The full rationale remains a legible chain: `keywords: …; predicates: …; globs: …`.

---

## 7. Keyword-Model Changes Required

Requirement 6 states: a single soft keyword must not alone produce a confident `delegate`. In Spec D, this is addressed by the additive structure rather than by changing the keyword model.

**No changes to `score()`, `_KEYWORD_MULTIPLIER`, or the weight ladder.** These constants were calibrated for the existing scoring contract. Changing them would invalidate threshold values and require full recalibration.

**No semantic-corroboration gate.** Unlike Spec A (which introduces a gate in `_decide.py:212`), Spec D does not add conditions to the keyword-only path. Instead, requirement 6 is addressed by the predicate signal making the correct agent's score *higher*, not by making the wrong agent's score lower.

**Effect on requirement 6:** If `code-reviewer` has a high keyword score (0.75) on a debugging prompt, and `investigator` has a lower keyword score (0.25) but receives a large predicate boost, the additive sum may place `investigator` above `code-reviewer` with the required gap. Alternatively, if scores remain close, the result is `advisory` — not a confident wrong `delegate`. The gap threshold continues to do its job.

**Blast radius of keyword changes:** none. The keyword model is unchanged.

---

## 8. Auditability Story

### 8.1 Structural advantage

Spec D provides the strongest auditability of the four designs. Every component of the final score is a named, discrete, inspectable rule:

- Keyword contribution: "matched keywords: review, code" (existing `_rationale_for()` format)
- Predicate contribution: "predicates: failure_observed=true, scope=spans-layers" (new segment)
- No cosine score, no category classification, no encoder-mediated claim

A catalog author can verify each rule by inspection: does the entry's trigger definition include the matched keywords? Does the entry's `predicate_requirements` declare the matched predicates? The full ranked roster is always present in the decision output — no candidate is hidden.

### 8.2 Full rationale example

For the no-keyword-reach trace (§4.3):

> `no keyword matches; predicates: failure_observed=true, scope=spans-layers; routing to investigator (score 0.87).`

For the additive arbitration trace (§4.4):

> `matched keywords: review, code; predicates: failure_observed=true, root_cause_known=false; routing to investigator (score 0.91). Alternatives: code-reviewer (score 0.75, keywords only).`

Both the winning entry and the alternatives expose their full score decomposition. A reviewer can reconstruct the decision without numerical intuition.

### 8.3 Comparison with Spec B's auditability hole

Spec B's rationale names the surviving candidate set ("restricted to {…}") but cannot name the agents that were hard-pruned: they were removed before scoring and appear nowhere in the output. If the correct agent was pruned, there is no signal in the rationale that it ever existed. Spec D's rationale always shows the full ranked roster, including agents whose predicate boost was zero — they simply rank lower.

---

## 9. Determinism Profile

### 9.1 v1 — heuristic extraction only

Predicate extraction is regex/heuristic evaluation on a fixed string. This is deterministic on any machine, any OS, any Python version — stronger than any of the four designs on this dimension. The catalog build pipeline is unchanged and produces byte-identical output run over run.

**Verdict:** same-input → same-output is a hard guarantee for v1, not a practical estimate. No float arithmetic, no BLAS, no encoder.

### 9.2 Boost application

`apply_predicate_boosts()` adds calibrated float constants to existing float scores and re-sorts. The arithmetic is pure Python float addition and comparison. The sort is deterministic (ties broken by name, as in the existing `score_entries()` sort key).

**Verdict:** deterministic given deterministic keyword scores (which the existing lexical scorer already provides).

### 9.3 Phase 2 — optional semantic boost

If the optional semantic boost is added in phase 2 (Model2Vec encoder), the determinism analysis from Spec A §9 applies. The heuristic and keyword layers remain deterministic regardless; the semantic layer introduces the same float variance caveats documented in Spec A. At that point, the overall profile becomes "strongly deterministic for heuristic and keyword contributions; practically deterministic for semantic contributions, subject to the same same-machine/same-binary constraints."

### 9.4 Per-run vs per-catalog-rebuild

Predicate boost magnitudes are constants defined in the implementation, not computed from the catalog. Changes to boost magnitudes require a code change and re-deployment, not a catalog rebuild. Changes to `predicate_requirements` on catalog entries require a catalog rebuild (the new field is in the catalog JSON). This is analogous to existing keyword trigger changes.

---

## 10. Failure Modes & Recovery

### 10.1 Incorrect predicate extraction — missed signal

**Failure:** A predicate fires incorrectly or fails to fire. For example, "went red" is not recognized as `failure_observed=true`; a "what causes" question is not recognized as `root_cause_known=false`.

**Severity:** Medium. An incorrect or absent boost leaves the entry competing on keyword score only — no worse than today's behavior. The failure is recoverable: the entry remains visible in the ranked roster and may still produce `advisory`, allowing the router to redirect.

**Recovery:** The predicate extractor should be conservative: prefer abstaining over false-positive extraction. An entry with no predicate boost simply ranks on its keyword score alone.

### 10.2 Predicate-set coverage gaps

**Failure:** A within-intent distinction exists in the catalog that no predicate covers. For example, two agents in the Planning/Design cluster are distinguished by a feature the current predicate set does not represent.

**Severity:** Low. Uncovered distinctions degrade to keyword-only arbitration — identical to today's behavior, not a regression.

**Recovery:** Coverage gaps are diagnosable from dispatch logs: cases where the predicate layer abstains and the keyword scorer selects a wrong agent signal a missing predicate. The predicate set is an iterative artifact.

### 10.3 Missing predicate context — abstention

**Failure:** A predicate requires information not present in the dispatch. `source_of_truth_present` requires the user to have provided a document path or explicit spec mention. If absent, the predicate cannot be determined.

**Severity:** Low to Medium. The entry falls back to keyword score. Open question: should absent predicates that are known to be relevant trigger the `needs_more_detail`/`ask_user` branch? (§12)

**Recovery:** Abstain — do not infer false when the signal is absent. The entry's score reflects only signals that fired.

### 10.4 Predicate-set design errors — systematic mis-ranking

**Failure:** A predicate maps to the wrong agent distinction, or a `predicate_requirements` declaration is incorrect. For example, `investigator` declares `failure_observed=true` as a requirement but the actual distinction is `root_cause_known=false`. All dispatches reaching this path are systematically mis-ranked.

**Severity:** High if systematic. The predicate design is the load-bearing artifact, analogous to Spec B's taxonomy-design risk. However, the rules are deterministic and inspectable — a systematic error is diagnosable from the rationale output and correctable by editing a catalog field or an extraction rule, without a corpus re-embedding.

**Recovery:** Pre-deployment: validate predicate-to-agent mappings against the full agent roster. Post-deployment: dispatch log inspection. Each wrong route exposes the predicate contribution in the rationale, making the error traceable to a specific rule.

### 10.5 Boost-magnitude calibration tension — the central failure mode

**Failure:** Boost magnitude B is set too high or too low.

- **Too high:** predicates dominate regardless of keyword signal. A single fired predicate lifts an agent above `_DELEGATE_THRESHOLD` even when keyword evidence clearly favors a different agent. False `delegate` decisions on weak predicate evidence.
- **Too low:** the premise fix fails. A no-keyword prompt cannot route correctly because the boost is insufficient to clear `_DELEGATE_THRESHOLD`. Issue [#325](https://github.com/glitchwerks/claude-wayfinder/issues/325) remains open.

**Critical contrast with Spec B:** Spec B's `_CAT_MARGIN_MIN` tension had no good operating point because the failure was a destructive hard-prune — the correct agent was removed and could not be recovered by any downstream signal. Here, a mis-calibrated boost weight degrades ranking but never erases a candidate. At worst, the decision degrades to `advisory` (the router sees the correct agent as an alternative and can redirect) rather than a silent hard-prune to the wrong agent with no recovery signal. The calibration tension here is tunable and recoverable; in Spec B it was a correctness cliff.

**Recovery:** Iterative calibration against dispatch logs. Start with a heuristic initial value (e.g., B = 0.6 per predicate, capped at 0.9 for stacking), observe the ratio of `delegate` decisions driven by predicate vs keyword signal, and adjust. No labelled corpus is required — dispatch logs are a soft signal.

### 10.6 Boost inflation — stacking predicates

**Failure:** When multiple predicates fire simultaneously (e.g., `failure_observed=true` and `scope=spans-layers` and `root_cause_known=false` all fire for one dispatch), the summed boost exceeds 1.0 or dominates the keyword score inappropriately.

**Severity:** Medium. Without a cap, three predicates firing at B = 0.35 each sum to 1.05, clamped to 1.0 — effectively the same as a short-circuit signal. An agent with zero keyword score and three firing predicates becomes indistinguishable from an explicit agent mention. Whether this is desirable depends on the predicate set's design.

**Recovery:** Apply a cap on the total predicate boost (e.g., max 0.9 regardless of how many predicates fire) or normalize across the fired set. The specific cap value is a calibration open question (§12).

---

## 11. Authoring & Maintenance Burden

### 11.1 What must be authored

**Per catalog entry:** a `predicate_requirements` declaration — a small set of predicate name/value pairs that, when satisfied, grant this entry a boost. The authoring burden per entry is low: typically one to three predicate-value pairs. The field is optional; entries without it receive no predicate boost and behave exactly as today.

**Predicate extractor:** the heuristic extraction rules (regex patterns, keyword lists) that derive predicate facts from the dispatch context. These are authored once in code and maintained when the extraction logic must be updated. No per-dispatch authoring burden.

**No utterance corpus.** Unlike Specs A and B, no collection of example utterances is required for v1. This is the most significant difference in authoring burden.

### 11.2 Comparison with A, B, and C

| Design | Per-entry authoring | Corpus required | Build-time encoder | On-disk index |
|---|---|---|---|---|
| Spec A | 5–10 utterances per entry (~500–1,000 strings) | Yes | Yes (Model2Vec) | `.npz` centroid matrix |
| Spec B | 8–10 utterances per category (~80–100 strings) + category tags | Yes | Yes (Model2Vec) | `.npz` category centroids |
| Spec C | Predicate requirements (small) | No (heuristic extraction) | No | None |
| **Spec D** | **Predicate requirements (small)** | **No (heuristic extraction)** | **No** | **None** |

Spec D's authoring burden is the lowest of the four designs for v1. Phase 2 (optional semantic boost) would add the utterance corpus authoring from Spec A or B.

### 11.3 Maintenance triggers

- **Adding a new catalog entry:** optionally declare `predicate_requirements`. If not declared, the entry receives no predicate boost and is invisible to the predicate layer. The entry remains fully functional via keyword scoring.
- **Adding a new predicate:** requires (a) a new extraction rule in the extractor, (b) new `predicate_requirements` declarations on relevant catalog entries, and (c) calibrating the new predicate's boost magnitude.
- **Changing boost magnitudes:** code change only. No catalog rebuild required.
- **Dispatch context format changes:** if new context fields are added that could inform predicate extraction, the extractor should be updated to leverage them.

### 11.4 Predicate set evolution

The initial predicate set (§5.1) was designed against the Review and Investigation clusters (source: Spec C §3.4, 2026-06-07). The Planning/Design cluster (`project-planner`, `devops`) and any multi-category agents may require additional predicates not yet identified. Coverage gap discovery is expected and is addressed iteratively through dispatch log inspection (§10.2).

---

## 12. Open Questions

The high-level architecture is locked. The following items are the primary open work, all of which are calibration or coverage decisions rather than architectural ones.

1. **Boost magnitude calibration.** What value(s) of B allow a no-keyword dispatch to clear `_DELEGATE_THRESHOLD` (requires B ≥ 0.85 for a single predicate, or stacking to reach that total) without causing predicate domination over strong keyword evidence? No dispatch logs are currently available for data-driven calibration; the initial value must be set heuristically and iterated. This is the most consequential open item.

2. **Boost stacking cap.** When multiple predicates fire simultaneously, should the total boost be capped (e.g., at 0.9) or normalized (e.g., split uniformly among fired predicates)? Without a cap, stacking can inflate the boost to a short-circuit-equivalent score.

3. **`predicate_requirements` schema shape.** What is the exact JSON structure of the new field? Minimum proposal: a flat object mapping predicate name to required value (`{"failure_observed": true, "scope": "spans-layers"}`). Richer options: lists of requirements with OR semantics, weighted requirements, or partial-match scoring. The right shape depends on how many agents require compound requirements.

4. **Predicate-set completeness for the full catalog.** The current predicate set was derived from the Review and Investigation clusters. A survey of every multi-agent category in the catalog is needed to identify missing predicates. This is a soft dependency — the system ships and improves coverage iteratively — but a baseline sweep before launch reduces early mis-routes.

5. **Skill-boost semantics.** Should skills receive predicate boosts? Skills are intent-independent in many cases (the `python` skill applies to any Python-touching task regardless of posture). But some skills are posture-specific. The current spec applies boosts only to agents; a design decision for skills is needed.

6. **Absent predicates and the `ask_user` branch.** When a predicate that would disambiguate two closely-ranked agents is absent from the dispatch context, should the matcher emit `needs_more_detail` to prompt the user for the missing information? Or always fall back silently to keyword arbitration? The `needs_more_detail` branch already exists in `decide()` (`_decide.py:188–195`); the question is whether absent disambiguating predicates qualify as a density-below-threshold condition.

7. **Interaction with the 1.0 short-circuits.** The existing short-circuit signals (command prefix, agent mention) hard-set score to 1.0 (`_match.py:309–314`). `apply_predicate_boosts()` must check for the 1.0 ceiling and skip applying boosts to already-maxed entries. This is a correctness detail, not an architectural question.

8. **Extraction-coverage validation.** Before launch, the predicate extractor should be validated against a labeled sample of dispatch logs (even a small manual sample of 20–30 dispatches) to confirm that the patterns fire correctly and do not over-fire. This is a soft dependency unlike Spec B's hard corpus requirement — the system can ship without it and improve the extractor iteratively.

9. **Phase 2 — optional semantic boost.** When and how should the optional semantic additive boost (Model2Vec encoder) be added? What boost magnitude should it carry relative to the predicate boost? Should it be always-on or conditional on the predicate layer abstaining? These questions are out of scope for v1.

---

## 13. Sources

Sources verified on disk unless otherwise noted.

| Source | Path / URL | Verified |
|---|---|---|
| `src/claude_wayfinder/match/_main.py` | this repo | on disk 2026-06-08 |
| `src/claude_wayfinder/match/_match.py` | this repo | on disk 2026-06-08 |
| `src/claude_wayfinder/match/_decide.py` | this repo | on disk 2026-06-08 |
| `src/claude_wayfinder/match/_types.py` | this repo | on disk 2026-06-08 |
| `src/claude_wayfinder/build_catalog/_main.py` | this repo | on disk 2026-06-08 |
| `src/claude_wayfinder/build_catalog/_process.py` | this repo | on disk 2026-06-08 |
| `src/claude_wayfinder/build_catalog/_validate.py` | this repo | on disk 2026-06-08 |
| `docs/research/2026-06-07-semantic-routing.md` | this repo | on disk 2026-06-08 |
| Spec A — Parallel Dual-Signal Ensemble | `docs/superpowers/specs/2026-06-07-semantic-routing-dual-signal-ensemble.md` | on disk 2026-06-08 |
| Spec B — Coarse-to-Fine Classifier Pre-Filter | `docs/superpowers/specs/2026-06-07-semantic-routing-classifier-prefilter.md` | on disk 2026-06-08 |
| Spec C — Structured Intent Predicate Refinement | `docs/superpowers/specs/2026-06-07-semantic-routing-structured-predicate-refinement.md` | on disk 2026-06-08 |
| `skills/dispatch/SKILL.md` | this repo | on disk 2026-06-07 (cited from Spec C; not re-read 2026-06-08) |
| `decide()` 7-decision ladder, v0.10.0 / #210 | `src/claude_wayfinder/match/_decide.py:154–287` | on disk 2026-06-08 |
| `score()` short-circuits at 1.0 | `src/claude_wayfinder/match/_match.py:309–314` | on disk 2026-06-08 |
| `build_features()` discards raw `task_description` | `src/claude_wayfinder/match/_match.py:146–148` | on disk 2026-06-08 |
| `score_entries()` scoring orchestration | `src/claude_wayfinder/match/_match.py:469–502` | on disk 2026-06-08 |
| `_main.py` orchestration | `src/claude_wayfinder/match/_main.py:206–225` | on disk 2026-06-08 |
| Predicate set candidate list | Spec C §3.4 (2026-06-07) | on disk 2026-06-07 |

**Unverified claims in this spec:**

- `unverified:` Lexical scores for the worked example in §4.4 (`code-reviewer` ≈ 0.75, `code-writer` ≈ 0.75, `investigator` ≈ 0.25–0.50) are estimates based on the scoring formula in `_match.py` and assumed keyword weights; exact values depend on the actual catalog trigger definitions, which were not read in full for this spec.
- `unverified:` The agent names used in predicate-to-agent examples (`investigator`, `auditor`, `inquisitor`, `debugger`, `systematic-debugging`) are carried from session context and Spec C's category mapping (§3.1); they have not been validated against the actual catalog entry names on disk.
- `unverified:` The predicate-to-agent mappings in §5.1 (e.g., `failure_observed=true` → investigator) are session estimates based on agent descriptions. They have not been validated against each agent's trigger definition file.
