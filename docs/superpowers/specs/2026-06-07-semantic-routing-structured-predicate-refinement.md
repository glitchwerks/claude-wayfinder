---
title: Semantic routing — Spec C — Structured Intent Predicate Refinement (Orthogonal Signal)
date: 2026-06-07
tracking: glitchwerks/claude-wayfinder#325
status: proposal — methodology capture only; earlier-stage than Spec A and Spec B
touches: []
related:
  - glitchwerks/claude-wayfinder#325
  - docs/research/2026-06-07-semantic-routing.md
  - docs/superpowers/specs/2026-06-07-semantic-routing-dual-signal-ensemble.md
  - docs/superpowers/specs/2026-06-07-semantic-routing-classifier-prefilter.md
skills_relevant:
  - dispatch-authoring
  - project-review
---

# Semantic routing — Spec C — Structured Intent Predicate Refinement

Tracking issue: [#325](https://github.com/glitchwerks/claude-wayfinder/issues/325)
Sibling specs:
- [Spec A — Parallel Dual-Signal Ensemble](2026-06-07-semantic-routing-dual-signal-ensemble.md)
- [Spec B — Coarse-to-Fine Semantic Classifier Pre-Filter](2026-06-07-semantic-routing-classifier-prefilter.md)

Author: doc-writer (2026-06-07)

---

> **Status and maturity — read before proceeding**
>
> This is a **proposal-stage methodology capture** from the 2026-06-07 design session. It is **not** a third competitor on the same axis as Spec A and Spec B. Spec A and Spec B are two ways to compose a semantic signal with the lexical matcher; they are decision-ready pending an adversarial review pass and a labelled corpus. **Spec C is earlier-stage, less mature, and orthogonal.** It identifies a ceiling both A and B share — within-intent disambiguation among posture-adjacent agents — and proposes a third signal (structured intent predicates) to address it. The relationship is: **Spec C composes with whichever of A or B is chosen**, most naturally as the fine stage of Spec B's coarse-to-fine pipeline.
>
> For sections where the design is undeveloped, this document states "Proposed — to be developed" and lists the open question. Detail has not been invented to match A/B's maturity.

---

## 1. Summary

This document captures the observation, evidence base, and methodology direction for a third routing signal: **structured intent predicates** that disambiguate among posture-adjacent agents within an intent category.

The core thesis is that a slice of the routing problem — **within-intent disambiguation among agents that share a topic but differ in posture** — is not a similarity problem. Neither topic embeddings (semantic) nor keyword sets (lexical) represent the distinguishing feature between, for example, `code-reviewer` and `inquisitor` (both review code; they differ in how adversarially). This means both Spec A and Spec B hit the same ceiling when asked to make this discrimination. Spec C proposes a complementary signal layer — structured boolean or categorical predicates extracted from the dispatch context — to resolve the cases that similarity-based routing cannot.

Spec A and Spec B address the **between-intent** routing problem (reaching the right category). Spec C addresses the **within-intent** routing problem (picking the right agent inside the category). The two concerns compose rather than compete. Spec C does not replace A or B; it extends whichever is adopted.

---

## 2. Core Requirements

The same six requirements from Specs A and B apply here:

1. **Open source + fully locally runnable.** No hosted embedding APIs, no proprietary models. Offline on the user's machine.
2. **Deterministic encoding.** Same input → same output every run.
3. **No generative LLM in the hot path.** Deterministic extraction only; autoregressive classification is out.
4. **Auditability.** A human must be able to understand why a route was chosen.
5. **Latency.** Sub-millisecond ideal; ~10–50ms tolerable only if confined to a subset of dispatches.
6. **The lexical keyword model is currently too strong to be a sole confident primary.** Any design must address how it prevents confident-wrong lexical matches from misrouting.

Spec C's contribution to requirement 4 is a structural advantage: predicates are explicit boolean or categorical features ("failure_observed: true"), not similarity scores. This is arguably the most auditable of the three signals — the routing decision can be expressed as a decision table that any catalog author can read without numerical intuition.

---

## 3. Architecture Overview

### 3.1 The within-intent ceiling

During the 2026-06-07 session, a survey of the 12 owned routable agents in the wayfinder catalog produced the following intent-category mapping:

| Category | Agents |
|---|---|
| Implementation | code-writer |
| Investigation | debugger, investigator |
| Review / Evaluation | code-reviewer, inquisitor, project-reviewer, auditor |
| Planning / Design | project-planner, devops |
| Research / Discovery | researcher |
| Operations | ops |
| Documentation | doc-writer |

(Source: session survey, 2026-06-07. The exact catalog contents were not fully enumerated from on-disk sources during this session; agent names carried from session context. `unverified:` the category assignments reflect the session's understanding of the agents' described purposes, not a validated read of every agent's trigger definitions.)

Several observations followed from this mapping:

- **Single-agent categories** (Implementation, Research, Operations, Documentation) are where semantic and lexical classification deliver clean, high-confidence routing. The classifier narrows to a 1-entry candidate set; there is nothing left to disambiguate.
- **Multi-agent categories** are where classification pruning underdelivers. The 4-agent Review cluster is the sharpest example: classification narrows 12 agents to 4, but cannot select among `code-reviewer`, `inquisitor`, `project-reviewer`, and `auditor`. The within-cluster discrimination must come from elsewhere.
- **Multi-category agent rate is approximately 25%.** `debugger`, `auditor`, and `devops` each have genuine secondary intents; the rate reaches ~33% if "refactoring" is treated as distinct from "implementation." This straddles the ~30% line at which Spec B's pruning value begins to erode (independent comparison finding, 2026-06-07 session).

### 3.2 Why similarity fails for within-intent distinctions

The within-cluster distinctions are **posture distinctions**, not topic distinctions. The dispatch prompt "review this module for issues" is topically identical whether the user wants a standard code review (code-reviewer), an adversarially harsh critique (inquisitor), a conformance check against a style guide (auditor), or an architectural health evaluation (project-reviewer). The topic embedding of any of these prompts occupies essentially the same region of the embedding space. No amount of encoder quality improvement resolves this — the signal is not in the topic.

The same argument applies to the lexical signal. Keywords like `review`, `code`, `check`, and `module` fire across all four agents in the Review cluster. The distinguishing feature is not in the vocabulary of the prompt; it is in what the user has or hasn't supplied alongside it.

**The shared ceiling:** because within-intent distinctions are posture distinctions, similarity-based routing of any kind — semantic embedding (Spec A) or lexical keyword (Spec B) — is weak here. Spec A asks the fine encoder to make its hardest discrimination. Spec B's lexical refiner faces the same problem. The current keyword matcher faces it too. All three operate on the wrong feature type.

### 3.3 The harness as evidence

The strongest evidence that within-intent distinctions are predicate-shaped comes from the routing logic already in the system. `unverified:` the router's routing table (referenced in the design session as `agents/general-purpose.md`, "Mandatory Code Routing") disambiguates the posture-adjacent agents via structured questions — "failure observed? root cause localized to one layer? source of truth present?" — predicate logic, not similarity. (The file `agents/general-purpose.md` was not confirmed on disk in this worktree at spec-write time; the claim is carried from session context and marked unverified. The routing skill `skills/dispatch/SKILL.md`, confirmed on disk at 2026-06-07, encodes the 7-decision ladder but not the within-cluster disambiguation logic, which lives in the consumer router.)

If the routing logic the router already applies is predicate-shaped, the natural place to encode that logic in the matcher is as structured predicates — not as a coarser or finer embedding.

### 3.4 Proposed signal shape

Spec C proposes extracting a small set of **structured intent predicates** from the dispatch context — boolean or categorical features that map to posture distinctions. Candidate predicates identified in the session:

| Predicate | Description | Example within-intent disambiguation |
|---|---|---|
| `failure_observed` | A reported failure, error, or stacktrace is present in the context | code-writer (no failure) vs debugger (failure present) |
| `root_cause_known` | The dispatch states the cause vs asks "why?" | investigator (root cause unknown) vs code-writer (root cause known, fix needed) |
| `scope` | Code-only vs spans multiple layers (code + infra + data) | debugger (code-layer only) vs investigator (multi-layer) |
| `source_of_truth_present` | A spec, contract, release notes, or invariant to conform against is present | code-reviewer (general review) vs auditor (conformance against a standard) |
| `posture` | Build/construct vs assess/evaluate vs adversarially critique | code-writer vs code-reviewer vs inquisitor |

These predicates are not similarity scores. They are assertable facts about the dispatch context that can be extracted deterministically — by checking for the presence of stacktraces, "why" phrasing, referenced documents, or other structural signals already available in the `Features` object (see `src/claude_wayfinder/match/_match.py`, confirmed on disk 2026-06-07) or as additional dispatch context fields.

### 3.5 Composition with Spec A and Spec B

Spec C is orthogonal to both A and B. The composition works as follows:

**With Spec B (preferred):** Spec B's coarse stage handles between-intent routing (semantic encoder → category → candidate set). Spec C provides a structured predicate layer as the fine stage, replacing or augmenting the keyword-only lexical refiner for within-category disambiguation. The pipeline becomes: semantic (between-intent) → predicates (within-intent) → lexical (residual).

**With Spec A:** Spec C could serve as a tiebreaker on the disagreement path. When Spec A's ensemble produces `ambiguous` (signals disagree on a within-intent case), the predicate layer could resolve the tie without surfacing to the router.

The between-intent / within-intent division of labor is clean: Spec A or B own the part similarity is strong at (topic-level routing); Spec C owns the part similarity is weak at (posture-level routing within a category). Neither step requires the other to be correct for its own correctness — they operate on orthogonal evidence.

---

## 4. Pipeline / Control Flow

Proposed — to be developed.

**Open question:** Where exactly do predicates slot into the existing 7-decision ladder (`_decide.py:154–287`, confirmed on disk 2026-06-07), the `mixed_content`/lane branch, and the short-circuit signals (explicit agent mention, command prefix)? The natural placement is after candidate restriction (Stage 2 in Spec B's pipeline) and before or instead of the lexical scorer (Stage 3), but the interaction with the `mixed_content` lane branch and the feature density guard (`_MIN_FEATURE_DENSITY = 2`) is not yet worked out.

A worked trace analogous to Specs A and B §4.2 must be constructed once the predicate extraction mechanism and pipeline placement are decided.

---

## 5. Predicate Extraction & Index Shape

Proposed — to be developed.

**Open question:** Two extraction approaches are under consideration:

1. **Deterministic heuristics / regex on the dispatch context.** Check the `task_description` and available dispatch context fields for structural signals: presence of a stacktrace or traceback pattern (`failure_observed`), "why" phrasing (`root_cause_known = false`), a referenced document path with a known doc-type extension (`source_of_truth_present`), or co-occurrence of a verb like "build" / "review" / "critique" (`posture`). This approach is maximally deterministic, requires no encoder, and is fully auditable ("stacktrace pattern detected → failure_observed = true").

2. **A small classifier.** A lightweight model that assigns predicate values from the prompt text. This would need to satisfy the same determinism and local-runability constraints as the A/B encoders.

Approach 1 is directionally preferred given the project's requirements (determinism, auditability, no LLM in the hot path). The dispatch context already carries structured signals (`file_paths`, `tool_mentions`, `command_prefix` — `skills/dispatch/SKILL.md`, confirmed on disk 2026-06-07) that may partially derive some predicates, reducing new extraction burden. For example, `file_paths` containing `.github/workflows/` or terraform files is a structural signal for `scope = spans-layers`.

The index shape (if any — a purely heuristic extractor may need no persisted index) is undefined at this stage.

---

## 6. Decision & Scoring Contract

Proposed — to be developed.

**Open question:** How do predicates interact with the existing 7-decision ladder? Two approaches are plausible:

1. **Predicate-gated agent selection.** After candidate restriction (Spec B Stage 2), apply predicate logic to filter or rank the surviving candidate set before running the lexical scorer. An agent whose predicate requirements are not met by the current dispatch is demoted or excluded. The lexical scorer runs on the reduced set.

2. **Predicate override.** After the lexical scorer produces a winner, check whether the predicate evidence contradicts it (e.g., lexical top pick is `auditor` but `source_of_truth_present = false`). If so, promote the next-best candidate whose predicate requirements are met.

Approach 1 integrates more cleanly with Spec B's coarse-to-fine pipeline. Approach 2 is more backward-compatible with the existing ladder but introduces a post-hoc correction that may be harder to audit.

---

## 7. Keyword-Model Changes Required

The predicate layer is additive. The existing keyword model, `_KEYWORD_MULTIPLIER`, and scoring constants are not changed by this proposal. If Spec C is composed with Spec B, the changes from Spec B §7 apply; Spec C adds the predicate extraction and predicate-gate logic on top of them.

The predicate signal may reduce the need for within-cluster keyword coverage: if `source_of_truth_present` cleanly separates `auditor` from `code-reviewer`, fewer highly specific keywords (which often don't appear in real dispatch prompts) are needed to drive the within-cluster distinction.

---

## 8. Auditability Story

### 8.1 Structural advantage over similarity

Predicates are the most auditable of the three signals. A predicate decision is expressible as a human-readable rule:

> `failure_observed = true` and `root_cause_known = false` → route to `investigator` over `debugger`

This is legible to any catalog author without numerical intuition. The full rationale chain under Spec B + Spec C would read:

> Classified `investigation` (conf 0.71, margin 0.13) → restricted to {systematic-debugging, investigator}; `scope = spans-layers` (file paths include `.github/workflows/`) → promoted `investigator`; matched keywords: `why`, `broken` → `investigator` (score 0.75).

Each link in the chain is independently verifiable.

### 8.2 Predicate extraction transparency

When predicates are extracted by deterministic heuristics, the extraction step itself is auditable: the rationale can name the specific pattern that fired ("stacktrace pattern detected at line 3 of task_description"). This is stronger auditability than either a cosine score (Spec A) or a category classification (Spec B), both of which require trust in the encoder's embedding of concept space.

---

## 9. Determinism Profile

### 9.1 Deterministic heuristics path

If predicate extraction is implemented as regex / heuristic matching against the dispatch context, the determinism profile is trivially strong — equal to or stronger than the existing lexical scorer. Regex evaluation on a fixed string is deterministic on any machine.

### 9.2 Classifier path

If a classifier is used, the determinism analysis from Specs A and B §9 applies. The preferred heuristic path avoids this concern entirely.

---

## 10. Failure Modes & Recovery

### 10.1 Incorrect predicate extraction

**Failure:** A predicate is extracted incorrectly — a stacktrace is missed because it uses a non-standard format; a "why" question is phrased as "what causes"; a referenced document is not recognized as a source of truth.

**Severity:** Medium. Incorrect predicates produce within-category misrouting, which is less severe than between-category misrouting (the correct agent is still in a relevant cluster). The error is recoverable if the router observes the decision and can route manually.

**Recovery:** The predicate extraction must include a `predicate_confidence` or `predicate_absent` signal when a predicate cannot be reliably determined. Absent predicates should fall back to the lexical scorer's ranking within the candidate set, not to a default assumption.

### 10.2 Predicate-set coverage gaps

**Failure:** The identified predicates do not cover all within-intent distinctions. Some agent pairs in the Review cluster may require predicates not yet identified.

**Severity:** Medium. Uncovered distinctions degrade to keyword-only refinement — the same behavior as today, not a regression.

**Recovery:** Predicate-set design is an iterative artifact. Coverage gaps are diagnosable from dispatch logs: cases where the predicate layer abstains and the keyword scorer selects a wrong agent are the signal for a missing predicate.

### 10.3 Predicate requires absent context

**Failure:** Some predicates may require information not present in the dispatch context. `source_of_truth_present` requires that the user has either provided a document path or mentioned a spec — if they have not, the predicate cannot be determined.

**Severity:** Medium. See recovery above (absent predicate falls back to lexical).

**Recovery:** Proposed — to be developed. **Open question:** is there a mechanism to prompt the router (or user) for missing predicate-bearing context, analogous to the `ask_user` decision branch? If so, this is the natural trigger for that branch.

### 10.4 Predicate-set design errors

**Failure:** Predicates are designed to map to wrong agent distinctions, or the posture taxonomy is incorrect. For example, `failure_observed` is insufficient to separate `debugger` from `investigator` if both are invoked on failure paths.

**Severity:** High if systematic. The predicate-set design carries the same load-bearing responsibility as Spec B's taxonomy design — errors propagate to all dispatches that reach the within-intent stage.

**Recovery:** Predicate design must be validated against the full agent roster before deployment, with attention to cases where a predicate does not cleanly disambiguate its target agents.

---

## 11. Authoring & Maintenance Burden

### 11.1 Predicate-set design as the load-bearing authoring artifact

The predicate set — which predicates exist, how they are extracted, and how they map to within-category agent selection — is the highest-stakes authoring artifact in this design. Its correctness determines within-intent routing accuracy. Errors propagate silently to all dispatches that reach the within-intent stage. This mirrors Spec B's taxonomy-design risk, applied at finer granularity.

**Open question:** What is the full set of predicates needed to cover all agent pairs in every multi-agent category? The candidate predicates identified in session (`failure_observed`, `root_cause_known`, `scope`, `source_of_truth_present`, `posture`) were derived from the Review and Investigation clusters. The Planning/Design cluster (`project-planner`, `devops`) and multi-category agents may require additional predicates not yet identified.

### 11.2 Per-entry predicate requirements

Proposed — to be developed. Each agent entry in the catalog would require a predicate requirements declaration: which predicate values select or exclude this agent within its category. This is a new authoring field analogous to Spec B's `categories: [...]` tag. The authoring burden per entry is low (a small set of boolean/categorical values) but the field's design is not finalized.

### 11.3 Maintenance triggers

Proposed — to be developed. Analogous to Spec B §11.4: adding agents, adding categories, or changing posture boundaries may require new predicates or revised requirements. The predicate extractor itself (heuristics/regex) must also be maintained when the dispatch context format changes.

---

## 12. Open Questions

These are the primary open questions that must be resolved before Spec C can advance from proposal to a design-ready specification.

1. **Predicate extraction mechanism.** Deterministic heuristics/regex vs. a small classifier. The heuristic path is strongly preferred given the determinism and auditability requirements, but the coverage of heuristics for realistic dispatch prompts must be validated against a labelled sample.

2. **Predicate-set completeness.** The candidate predicates (`failure_observed`, `root_cause_known`, `scope`, `source_of_truth_present`, `posture`) were derived from the Review and Investigation clusters in the session. Are these sufficient? What predicates are needed for the Planning/Design cluster and for multi-category agents?

3. **Pipeline placement.** Where exactly does the predicate layer sit in the 7-decision ladder, the `mixed_content`/lane branch, and relative to the short-circuit signals? How does it interact with the `_MIN_FEATURE_DENSITY` guard?

4. **Failure mode when predicates are absent.** When the dispatch context does not contain sufficient information to determine a predicate, the layer must fall back gracefully. Is the fallback the lexical scorer's ranking, the `ask_user` branch, or something else?

5. **Dispatch context fields.** The existing dispatch context fields (`file_paths`, `tool_mentions`, `command_prefix` — `skills/dispatch/SKILL.md`, confirmed 2026-06-07) provide some predicate-bearing signals. Which predicates are fully derivable from existing fields, and which require new extraction from `task_description` or new context fields?

6. **Scope of the within-intent problem.** How much of the within-intent disambiguation that predicates cannot resolve remains after predicate application? Are there agent pairs where no deterministic predicate can cleanly separate them, and if so, which pairs and how often do they appear in real dispatch traffic?

7. **Interaction with the existing 7-decision ladder.** Spec C must not change the semantics of the 7 decision classes. The predicate layer should be a filter or ranker, not a new decision class. How this integrates without modifying `_types.py:37–47` is a design question. (Source: `src/claude_wayfinder/match/_types.py`, confirmed on disk 2026-06-07.)

---

## 13. Sources

Sources cited in this document are from the 2026-06-07 design session and sibling specs unless otherwise noted.

| Source | URL / path | Fetched / verified |
|---|---|---|
| Spec A — Parallel Dual-Signal Ensemble | `docs/superpowers/specs/2026-06-07-semantic-routing-dual-signal-ensemble.md` | on disk 2026-06-07 |
| Spec B — Coarse-to-Fine Classifier Pre-Filter | `docs/superpowers/specs/2026-06-07-semantic-routing-classifier-prefilter.md` | on disk 2026-06-07 |
| `docs/research/2026-06-07-semantic-routing.md` | (this repo) | on disk 2026-06-07 |
| `src/claude_wayfinder/match/_match.py` | (this repo) | on disk 2026-06-07 |
| `src/claude_wayfinder/match/_decide.py` | (this repo) | on disk 2026-06-07 |
| `src/claude_wayfinder/match/_types.py` | (this repo) | on disk 2026-06-07 |
| `skills/dispatch/SKILL.md` | (this repo) | on disk 2026-06-07 |

**Unverified claims in this spec:**

- `unverified:` The agent category mapping table in §3.1 reflects the session's understanding of agent purposes and was not validated by reading every agent's trigger definition file. Agent names (debugger, investigator, inquisitor, auditor, etc.) are carried from session context and may not exactly match catalog entry names on disk.
- `unverified:` The router's routing table (`agents/general-purpose.md`, "Mandatory Code Routing") uses predicate-style structured questions to disambiguate posture-adjacent agents. This file was not confirmed on disk in this worktree during spec writing; the claim is carried from session context.
- `unverified:` The multi-category agent rate (~25%, ~33% with refactoring split) is a session estimate based on the categorical mapping in §3.1; it has not been validated against actual dispatch log data.
- `unverified:` The "~30% line at which Spec B's pruning value begins to erode" is cited from the 2026-06-07 independent comparison finding in session context; the exact threshold and the analysis behind it were not reproduced in this spec.
