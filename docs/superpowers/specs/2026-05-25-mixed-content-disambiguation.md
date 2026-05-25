---
title: Mixed-content disambiguation — residual analysis post-#210
date: 2026-05-25
tracking: glitchwerks/claude-wayfinder#138
related:
  - glitchwerks/claude-wayfinder#210  # shipped Approach C as `mixed_content` decision
  - glitchwerks/claude-wayfinder#135  # AND-groups (orthogonal, keyword-side)
  - glitchwerks/claude-wayfinder#202  # decision-ladder v5 (collapsed `ambiguous` -> `advisory`)
status: closed — no further action; #138 closed as completed
touches:
  - src/claude_wayfinder/match/_match.py
  - src/claude_wayfinder/match/_decide.py
  - tests/test_match/test_mixed_content.py
  - tests/test_match/test_score.py
  - docs/schema.md
skills_relevant:
  - dispatch-authoring
---

# Mixed-content disambiguation — residual analysis post-#210

Tracking issue: [#138](https://github.com/glitchwerks/claude-wayfinder/issues/138) (closed 2026-05-25 as completed)
Author: project-planner (2026-05-25)
Status: **closed — #138 closed without implementing A/B/E. This spec is retained as the durable design record for the decision.**

> **Acknowledged caveats** (from `project-reviewer` pass, 2026-05-25): the headline 62.3% absorption figure has methodology gaps the spec did not close — the `mixed_content` / `advisory` filter predicates aren't symmetric, and the synthetic-vs-organic split (`session_id == ""`) was not verified beyond a 5-record spot-check. The 2-day post-#210 window is also shorter than the project's 14-day telemetry norm (#159). The closure decision is made with awareness of these gaps: if the residual ever proves substantive in a future review, this spec's § 4 candidate-tree (B as first-revival candidate per reviewer's note) is the entry point for re-opening.

---

## § 1. Premise shift: #210 already shipped Approach C

Issue [#138](https://github.com/glitchwerks/claude-wayfinder/issues/138) originally enumerated five candidate approaches (A: diminishing-returns scoring; B: path-domain ratio; C: composite-task acknowledgment; D: multi-agent routing; E: glob specificity weighting). [#210](https://github.com/glitchwerks/claude-wayfinder/issues/210) (closed 2026-05-23) shipped **Approach C structurally** as a new `mixed_content` decision class in the matcher ladder.

The implementation is at `src/claude_wayfinder/match/_decide.py:56-151` (`_detect_mixed_content`) and `src/claude_wayfinder/match/_decide.py:238-246` (ladder insertion between `self_handle` and `advisory`). Detection rule (`_decide.py:90-112`):

1. `>= 2` agents at score `>= 1.0 - _MIXED_CONTENT_SCORE_EPSILON` (default `0.05`, so any agent `>= 0.95`).
2. Every qualifying agent has non-zero path-glob contribution.
3. The matched-path sets of qualifying agents are pairwise disjoint.
4. Gap `< _DELEGATE_GAP` (`0.2`) — pre-condition for the advisory branch.

This narrows #138's surface to the **residual**: cases where the original failure mode (clamped score ceiling, code-writer ↔ doc-writer tie) still lands in `advisory` rather than `mixed_content`. This spec characterizes that residual and proposes a decision.

## § 2. Live-log measurement (post-#210)

**Source:** `~/.claude/state/dispatch-log.jsonl` (21,278 `matcher_decision` records total, dating from 2026-05-04 through 2026-05-25). Measured 2026-05-25.

**Methodology:** ripgrep counts against the JSONL file (one record per line), partitioned by `decision` and by ISO date prefix on `ts`. The full post-#210 window starts at the `mixed_content` rollout (first such record: line 23495, `2026-05-23T14:35:33Z` — verified by inspection).

### § 2.1 Whole-corpus decision distribution

| Decision | Count | Notes |
| --- | --- | --- |
| `advisory` | 3,515 | Includes legacy pre-#202 cases that would have been `ambiguous` |
| `ambiguous` | 431 | Pre-#202 records only; decision class removed by v5 ladder (`_decide.py:166-178`) |
| `mixed_content` | 205 | All post-2026-05-23 — every record post-#210 |
| (other decisions) | 17,127 | `delegate`, `self_handle`, `self_handle_unaided`, `needs_more_detail` |

unverified: the 431 `ambiguous` records ought to be ts < 2026-05-XX (v5 cutover). A spot-check at line 849 (`ts=2026-05-04T00:32:35Z`) confirms — full timeline audit was not performed.

### § 2.2 Post-#210 window (2026-05-23 to 2026-05-25)

| Bucket | Count |
| --- | --- |
| `advisory` (all agents) | 738 |
| `advisory` involving both `code-writer` and `doc-writer` in `agent` or `alternatives[]` | 146 |
| `mixed_content` (all 205 records involve `code-writer` + `doc-writer` lanes by construction) | 205 |

### § 2.3 Narrower post-rollout window (2026-05-24 to 2026-05-25)

A tighter window that excludes the day-of rollout (where some traffic still hit pre-#210 catalogs):

| Bucket | Count |
| --- | --- |
| `advisory` involving cw+dw | **106** |
| `mixed_content` (cw+dw lanes) | **175** |

`mixed_content` absorption rate (cw+dw cases that did NOT land in advisory): `175 / (175 + 106) = 62.3%`.

## § 3. Residual analysis

Inspection of the 106 residual advisory records on 2026-05-24/25 (`dispatch-log.jsonl` lines 23974, 23997, 23998, 24045, 24068, 24070 inspected directly) shows **two structurally distinct sub-populations**:

### § 3.1 Synthetic test-fixture replays (dominant)

Most residual `advisory` records are **replays of synthetic fixture prompts** against catalog hashes that pre-date #210's `mixed_content` semantics. Examples from direct inspection:

- Line 23997: `task_description = "update the project files"`, paths `[src/main.py, src/tests/test_main.py, lib/utils.py, docs/api.md, wiki/Home.md]`, **catalog_hash `8cc8ff85…`** → `advisory`, both agents at score 1.0, gap 0.0.
- Line 23495 (post-#210 catalog `26ddc063…`, **identical fixture inputs** with one extra path): → `mixed_content`, lanes partition correctly.

The differing decision arises from **catalog content**, not from the matcher residual #138 originally described. These are CI/replay artefacts of multiple catalog hashes co-existing in the log — not fresh ambiguity the matcher needs to learn to resolve. The fixture path `src/claude_wayfinder/fixtures/and_groups/catalog.json:28-56` confirms the doc-writer triggers shape that's being exercised.

unverified: I did not enumerate every distinct `catalog_hash` value in the 106-record residual to confirm what fraction are old-catalog replays. A 5-record spot-check (lines 23974, 23997, 23998, 24045, 24068, 24070) showed three distinct `catalog_hash` values appearing repeatedly with identical synthetic task_descriptions, consistent with playback. The exact synthetic vs. organic split would be confirmed by enumerating `(task_description, paths)` tuples and counting unique session_ids — recommended as part of the regression-test harness in § 8 if this spec progresses.

### § 3.2 Marginal-confidence advisory (non-tie)

Line 23974 / 24045 (the "Edit two CSS values in index.html" #138 fixture case from the original issue body):

```
input.paths    = ["index.html"]
input.tools    = ["git"]
output.agent   = "code-writer"  score=0.65
alternatives   = [{agent: "doc-writer", score: 0.125}]
gap            = 0.525  (well above _DELEGATE_GAP=0.2)
decision       = "advisory" (rationale: "...but match is not conclusive")
```

This is **not** a tie at all. The gap is 0.525 — vastly above the `0.2` `_DELEGATE_GAP` threshold (`_decide.py:41`). It lands in `advisory` because the top score (`0.65`) is below `_DELEGATE_THRESHOLD = 0.85` (`_decide.py:40`), not because two agents are clamped. The rationale string explicitly distinguishes this case ("but match is not conclusive") from the close-cluster case ("gap=0.00; top pick recommended, alternatives close behind"). See `_decide.py:256-266`.

**This case is structurally outside #138's scope.** The original issue framed the ceiling problem as `min(s, 1.0) = 1.00` for *both* agents simultaneously. A `0.65`/`0.125` split is a low-confidence single winner, not a tie. The fix for the `index.html` case (if one is desired) is to give `code-writer` more discriminating signal for short CSS-edit prompts (a triggers/keyword change in the catalog), not a matcher scoring change.

### § 3.3 Genuine clamped ties not absorbed by `mixed_content`

The remaining residual after § 3.1 and § 3.2 — clamped ties (`gap=0.00`, both at 1.0) where `_detect_mixed_content` returned `None` — must fail one of conditions 2 or 3 (`_decide.py:106-112`):

- **Condition 2 failure:** at least one of the two top agents has zero matched paths. Score reached 1.0 via keywords/tool_mentions/groups only. Example shape: prompt with strong "edit docs" keywords matching `doc-writer` at 1.0 and `tool_mentions: [git]` plus path-less code keywords matching `code-writer` at 1.0.
- **Condition 3 failure:** both agents matched at least one shared path (e.g., `README.md` claimed by both `code-writer` and `doc-writer` catalogs).

unverified: I did not directly observe a record in the residual that demonstrably triggers one of these conditions on the post-#210 catalog. Lines 23997, 23468-23472 all carry mixed-path inputs that DO partition cleanly when replayed against the `26ddc063…` catalog (per the duplicated input at line 23495 routing to `mixed_content`) — they only land in `advisory` because the catalog at the time of dispatch was older. Confirming a genuine § 3.3 case requires replaying the residuals against the *current* catalog, not the dispatch-time catalog. Proposed as part of the harness in § 8.

## § 4. Load-bearing decision

**Defer further matcher-scoring work** under #138's heading and **close #138** as substantively resolved by [#210](https://github.com/glitchwerks/claude-wayfinder/issues/210), with the caveat that the spec proposes follow-on work to *verify* the residual is non-substantive before closing.

### § 4.1 Decision criteria

| Criterion | Result |
| --- | --- |
| Is there a measurable post-#210 residual of clamped cw↔dw ties? | Yes — 106 records (2026-05-24/25), but **majority are synthetic fixture playback against old catalogs**, not fresh organic ambiguity. |
| Are the genuine residual cases (§ 3.3) frequent enough to justify scoring-math work? | **Unverified, likely no.** Spot-checks did not surface a single case that fails `_detect_mixed_content` conditions 2 or 3 on the current catalog. |
| Would Approach A/B/E change behavior on cases that #210 *already* resolves correctly? | Yes (this is the regression risk). Any change to `min(s, 1.0)` scoring or path-glob weighting touches the 21,278-record replay surface, not just the 106-record residual. |
| Is there a no-cost, schema-compatible win? | Not obviously. All of A, B, E are scoring-math changes with broad blast radius. |

The verification gap (§ 3.3 unverified) is the only loose thread. The proposed follow-on is a **measurement** task, not a matcher-change task.

### § 4.2 Why not pick A/B/E now

- **Approach A (diminishing-returns scoring).** Replaces additive path-glob contribution with a saturating function (e.g. `1 - exp(-k * n)`). Touches `_match.py:285`. Changes scores for every multi-path prompt in the corpus — including the 5,000+ records currently routing correctly to `delegate`. Justification requires evidence the clamp is harming routing decisions *post-#210*, which § 3 has not established.
- **Approach B (path-domain ratio).** Computes per-agent share of matched paths and penalizes lopsided wins. Functionally similar to the path-disjointness check `_detect_mixed_content` *already performs* (`_decide.py:108-112`). Adding it to scoring rather than to decision composition would either double-count or fight #210.
- **Approach E (glob specificity weighting).** Weight per-glob contributions by how rare the glob is across the catalog. Substantial new schema state. Justification absent.

### § 4.3 Closure conditions

#138 may be closed when:

1. A replay harness (§ 8) confirms that against the current catalog, the post-#210 residual of *genuine* clamped cw↔dw ties (§ 3.3) is `<= N`. Recommended `N = 5`, but the precise threshold is for review.
2. The harness is committed as a regression test so any future scoring/decision-ladder change is measured against the same baseline.
3. If the harness surfaces `> N` genuine § 3.3 cases, this spec is reopened (or a successor spec authored) and we pick from A/B/E with the residual shape in hand.

## § 5. Worked example: original #138 "siege-web find-and-replace" case

Issue #138's body cited a case description "siege-web find-and-replace" as one of 5 representative ambiguous cases. I do not have the original prompt or paths text — only the case name. unverified: I did not locate the original prompt body in the issue or in the dispatch-log.

**What I can say from § 3:** If the case had mixed `src/**` + `docs/**` paths and both agents scored 1.0 via path-globs only, **#210 now routes it to `mixed_content`** with `code-writer` and `doc-writer` lanes carrying their respective path partitions. If the case had keyword-driven 1.0 scores with empty `path_globs` matches, it falls into § 3.3 (genuine residual) and motivates the harness, not new scoring math.

The spec author requests the architectural reviewer or the user verify the actual prompt of "siege-web find-and-replace" so the worked example is grounded in real data rather than reconstructed.

## § 6. Acceptance criteria

This spec proposes acceptance criteria for **closing #138**, not for landing a code change:

1. A new test module (proposal: `tests/test_match/test_138_residual_replay.py`) replays the residual cw+dw advisory cases from `~/.claude/state/dispatch-log.jsonl` (post-#210 window) against the *current* catalog under test.
2. The harness reports, per residual record: (a) does it route to `mixed_content` against current catalog? (b) if not, which of `_detect_mixed_content` conditions 1-3 fails? (c) record `(task_description, paths_signature)` to deduplicate fixture playback noise.
3. A summary report attached to #138's closing comment quantifies the genuine § 3.3 residual (deduplicated, against current catalog).
4. If genuine residual `<= 5` distinct (task, paths) tuples, close #138 with "substantively resolved by #210; residual within tolerance."
5. If `> 5`, this spec is amended (or superseded) with a chosen approach from {A, B, E} and the residual cases as fixtures.

## § 7. Backward compatibility

This spec **proposes no source-code change.** All work product is measurement and tests. The catalog format is untouched. Existing `mixed_content` semantics from #210 are untouched. No regression risk against the existing 21,278-record corpus by construction.

## § 8. Regression-test harness (proposed deliverable)

Sketch — to be detailed in a follow-on implementation plan if this spec is approved:

- **Input:** path to `dispatch-log.jsonl` (default `~/.claude/state/dispatch-log.jsonl`), date window, current catalog under test.
- **Filter:** `type == "matcher_decision" AND decision in {"advisory","ambiguous"} AND code-writer ∈ {agent} ∪ alternatives[].agent AND doc-writer ∈ {agent} ∪ alternatives[].agent AND ts ∈ window`.
- **Replay:** for each record, reconstruct dispatch input `(task_description, file_paths, tool_mentions, agent_mentions, command_prefix)` and call the matcher's public dispatch entrypoint (the same surface `tests/test_and_groups_replay.py` already exercises against fixtures).
- **Output:** for each replayed record:
  - new decision against current catalog
  - if still `advisory`: which `_detect_mixed_content` condition (1, 2, or 3) caused fallthrough
  - dedup key `(task_description, frozenset(paths), frozenset(tools))`
- **Report:** counts by category, plus a deduplicated list of genuine § 3.3 residuals (clamped both agents, conditions 2 or 3 fail on current catalog).

This harness pattern is closely analogous to `tests/test_and_groups_replay.py` and should reuse its `_dispatch` invocation shape.

## § 9. Out of scope

- **Approach D (multi-agent routing).** Implementing parallel dispatch / two-agent task hand-off is router/companion-repo territory (`hooks/`, dispatch orchestration), not matcher territory. Multi-agent routing requires changes to `general-purpose` agent prompts and downstream consumer contracts — out of scope for matcher work. If #210's `lanes[]` output is intended to drive multi-agent dispatch, that's a separate spec against a separate consumer.
- **Schema changes for glob specificity (Approach E precondition).** No schema change is proposed; the weight ladder `{0.25, 0.5, 1.0}` at `src/claude_wayfinder/build_catalog/_validate.py` (referenced from `docs/schema.md:63`) remains the only validator-enforced weight surface.
- **Changing `_MIXED_CONTENT_SCORE_EPSILON` or `_DELEGATE_GAP`.** Threshold tuning is a separate concern. If the harness surfaces near-miss cases (e.g. agents at 0.93/0.96 that just miss the 0.95 epsilon), that's a parameter conversation, not an algorithm conversation, and belongs in a follow-on issue.
- **Skill attachment in mixed-content decisions.** #210 already wired `skills` into each `LaneInfo` (`_decide.py:113-121`) — skill-side ambiguity is out of scope here.

## § 10. Open questions for review

1. **Sufficient residual data?** Is the 106-record / 2-day post-#210 window enough to characterize the residual, or should the analysis wait for `>= 7` days of post-#210 traffic before deciding fix-now vs defer? (Brief stated `~468` enriched events on a 7-day window — that's drift-log, not dispatch-log; dispatch-log volume is higher.)
2. **Synthetic vs. organic split.** Should the harness explicitly filter out CI replay traffic (e.g., by `session_id == ""` heuristic — every observed residual carries empty `session_id`, which may itself be the synthetic-traffic signal)?
3. **Closure threshold N.** § 6.4 proposes `N = 5`. Is that the right number?
4. **Was the original 65-case figure measured post- or pre- some catalog change?** The #135 spec (§ 1) quoted "65 code-writer ↔ doc-writer ambiguous ties" against a 9,936-record corpus. The current 21,278-record corpus has 431 `ambiguous` + 3,515 `advisory` — the comparable population has grown. Re-baselining against the same dispatch-log query the original 65-case figure used would help calibrate "improvement vs. did the prompt distribution just shift."
5. **#138 case "siege-web find-and-replace" prompt body.** Needed for § 5's worked example. Should be reconstructed from issue history or dispatch-log search before the harness is implemented.
