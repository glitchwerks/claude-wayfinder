---
title: Telemetry enrichment v2 — bypass-cause taxonomy
date: 2026-05-19
tracking: glitchwerks/claude-wayfinder#143
supersedes: glitchwerks/claude-wayfinder#152 (abandoned)
postmortem: "#143 / abandoned PR #152 — lessons extracted to docs/design/methodology-lessons.md"
inquisitor_pass_1: 2026-05-19 (3 BLOCKING / 6 CONCERN / 2 NIT — addressed in v2-draft2)
inquisitor_pass_2: 2026-05-19 (2 BLOCKING / 5 CONCERN / 2 NIT — addressed in v2-draft3)
inquisitor_pass_3: 2026-05-19 (1 BLOCKING / 3 CONCERN / 1 NIT — addressed in v2-draft4, this revision; pattern converged)
status: draft
followups_filed:
  # See AC #9 for the merge gate. All three filed 2026-05-19, milestone
  # "Telemetry v2 baseline review" (#7) for F-1/F-2; F-3 milestone-less
  # (due date 2026-08-19, different cadence from the 2026-06-02 review).
  F-1: 159
  F-2: 160
  F-3: 161
touches:
  - hooks/check-agent-dispatch-pairing.js
  - hooks/lib/bypass-taxonomy.js
  - hooks/tests/bypass-taxonomy.test.js
  - scripts/analyze-drift-causes.py
  - tests/test_analyze_drift_causes.py
  - src/claude_wayfinder/_health.py
  - skills/router-health/SKILL.md
skills_relevant:
  - hook-authoring
  - python
---

# Telemetry enrichment v2 — bypass-cause taxonomy

## Motivation

v1 (PR #152, abandoned) tried to recover *what the matcher would have decided*
for the ~98% of drift events where the matcher never ran. That design died on
substrate confusion and cross-process contract fragility. See the postmortem
for full forensics: the six extracted lessons live in
`docs/design/methodology-lessons.md`; the originating postmortem was deleted
per the plan-file lifecycle rule (parent issue #143 / abandoned PR #152).

v2 has a narrower goal: **categorize *what actually happened* for each drift
event**, using only data the PreToolUse hook already has on the tool-call
shape. No user-prompt content is captured. No matcher counterfactual is
computed. No cross-event joining is attempted.

Current event distribution (`~/.claude/state/router-drift.jsonl`, 1132 events
from 2026-05-03 to 2026-05-19 = 16 days = **~71 events/day**):

| Hook category       | Share | Enriched by this spec? |
| ------------------- | ----: | ---------------------- |
| `skill_mediated`    | ~52%  | ✓                      |
| `bypass`            | ~46%  | ✓                      |
| `stale_dispatch`    | small (exact share TBD from analyzer first run) | ✓ |
| `router_mediated`   | not emitted as a drift event — included for completeness | n/a |
| `advisory_override` | ~0%   | ✗ (deferred; not load-bearing) |
| malformed           | ~2%   | n/a                    |

v1-postmortem volume math is re-validated: 71/day at ~250 B/event delta =
**~17 KB/day → ~6.3 MB/year**. No rotation needed in the foreseeable horizon.

## Goal

Enable the user to answer, from `~/.claude/state/router-drift.jsonl` and the
`router-health` report, questions of the form:

- "How often does the router fire a *second* Agent under a single dispatch
  authorization (`router_direct_after_consumed_dispatch`)? This is the
  hypothesized #1 router discipline failure mode."
- "What fraction of bypasses are skill-mediated by design vs. router-direct
  Agent calls without dispatch?"
- "Which sub-agent is most often invoked via a `router_direct_no_dispatch`
  path? (i.e., where is router discipline weakest?)"
- "Is the taxonomy itself adequate? (`unknown` share trend.)"

## Non-goals

- Recovering what the matcher *would* have decided for events it didn't see.
- Capturing user-prompt content in drift events.
- `advisory_override` enrichment.
- `matcher_decision` enrichment in `dispatch-log.jsonl` (the other half of
  issue #143; separate work if pursued).
- Cross-event joining via a `decision_id` field. v1 killed this.
- Drift-event rotation policy.
- Backfill of historical events.

## Design overview

```
                                                       ┌─────────────────────────┐
PreToolUse(Agent) ─► check-agent-dispatch-pairing.js ──┤ bypass-taxonomy.classify│
   (emits 3 enriched                                   │   (signals + cause)     │
    categories:                                        └────────────┬────────────┘
    bypass, skill_mediated,                                         │
    stale_dispatch)                       additive fields on existing drift event
                                                                    │
                                                                    ▼
                                                  ~/.claude/state/router-drift.jsonl
                                                                    │
                                ┌───────────────────────────────────┴────────────────────┐
                                ▼                                                        ▼
                  scripts/analyze-drift-causes.py                            router-health new section
                  (ad-hoc CLI, jq-friendly output)                           "Bypass causes (7-day window)"
```

Three new pieces, all additive:

1. `hooks/lib/bypass-taxonomy.js` — pure function with unit tests under
   `hooks/tests/bypass-taxonomy.test.js` (matches existing convention; see
   `hooks/tests/dispatch-log.test.js`).
2. `scripts/analyze-drift-causes.py` — ad-hoc CLI analyzer.
3. New "Bypass causes" section in `src/claude_wayfinder/_health.py`'s report
   output, inserted between "Runtime Telemetry" and "Informational Metrics".

## What the hook actually emits today (load-bearing read)

Inquisitor pass 1 surfaced that the hook (`hooks/check-agent-dispatch-pairing.js:150–217`,
`classifyDispatchRich()`) emits drift events in three structurally distinct
shapes — not the two v2-draft1 assumed:

| Hook `category`    | Triggered when                                                                                          |
| ------------------ | ------------------------------------------------------------------------------------------------------- |
| `bypass`           | Either: no dispatch in history, OR dispatch exists but a prior Agent already consumed it (`countAgent ≥ 1` since last dispatch) |
| `skill_mediated`   | No dispatch, but a non-`dispatch` Skill preceded                                                        |
| `stale_dispatch`   | Dispatch exists, not consumed, but past the hook's own staleness window                                 |

The "dispatch was consumed by a prior Agent" case is the hypothesized #1
router discipline failure — chaining a second Agent under one dispatch. The
v2-draft1 cause enum aliased it into the same bucket as the "no dispatch at
all" case. **v2-draft2 splits it.** That split is the single most important
change in this revision.

## Signal set

Every `bypass`, `skill_mediated`, and `stale_dispatch` drift event gains two
additive fields:

```json
"bypass_signals": {
  "subagent_type": "code-writer",
  "dispatch_skill_called_recently": true,
  "count_agent_since_dispatch": 1,
  "last_skill_call_name": "gh-pr-review-address",
  "last_skill_call_is_interactive": true,
  "turns_since_user_message": 3
},
"bypass_cause": "router_direct_after_consumed_dispatch"
```

Field-by-field derivation rules:

| Field                              | Source                                                                                                                                  |
| ---------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| `subagent_type`                    | Agent tool call's `subagent_type` parameter. Kept despite N2-style redundancy with the parent event because the analyzer cross-tabs by it heavily; saves a join. |
| `dispatch_skill_called_recently`   | Boolean — was **any** `claude-wayfinder:dispatch` skill_call observed in `conversation_history`? **The window must match the hook's window** (`classifyDispatchRich` lines 162-170): full history back to the most recent dispatch, no user-turn boundary. If the spec's signal uses a tighter window than the hook does, the two disagree and the decision tree re-creates the misclassification pass-1 B1 was meant to fix. |
| `count_agent_since_dispatch`       | Integer ≥ 0 **OR `null`**. When a dispatch skill_call exists in history: count of Agent tool_uses between that dispatch and this Agent call. When no dispatch exists in history: `null` (NOT `0`). The `null` sentinel preserves the discriminating information the analyzer's `--disagreements` check needs to audit the dominant `router_direct_no_dispatch` bucket — see pass-2 review. The decision tree explicitly skips this field when `dispatch_skill_called_recently == false`. **NEW signal added in v2-draft2 to address B1.** |
| `last_skill_call_name`             | String or `null` — name of the most recent skill_call in `conversation_history`. `null` if none.                                       |
| `last_skill_call_is_interactive`   | `last_skill_call_name ∈ INTERACTIVE_SKILLS` (see hardcoded set below).                                                                  |
| `turns_since_user_message`         | Integer — hop count to the most recent `user` turn in `conversation_history`. Not used by the decision tree; surfaced for analyzer use. |

**Signals dropped from v2-draft1**:

- `in_skill_context` — redundant with the hook's existing `category` field
  (`category === 'skill_mediated'` *is* the signal). Kept conceptually but not
  re-emitted.
- `preceded_by_hook_injection` — v2-draft1 asserted this was derivable from
  `conversation_history` but gave no parser. Rather than ship a hand-waved
  signal, v2-draft2 drops it and lets hook-injected dispatches flow to
  `unknown` (which has its own threshold, see § router-health integration).
  Future work can add a concrete derivation if `unknown` share is dominated
  by hook-injected events.

`INTERACTIVE_SKILLS` and any window constants are module-level constants in
`bypass-taxonomy.js` and are the spec's authoritative source for those values.

## Cause enum

```
skill_mediated_interactive               expected   — last skill was a known interactive skill
skill_mediated_other                     review     — skill_mediated category, skill not in interactive set
router_direct_after_consumed_dispatch    unwanted   — bypass category, count_agent_since_dispatch ≥ 1
                                                      (a prior Agent already used the dispatch authorization)
router_direct_no_dispatch                unwanted   — bypass category, no dispatch in history
stale_dispatch                           review     — stale_dispatch category (matches hook category;
                                                      no further sub-typing in v1)
unknown                                  review     — none of the above. Has its own threshold (see below).
```

**Naming note on `stale_dispatch`.** The cause name deliberately matches the
hook's existing category name to avoid the name collision inquisitor flagged.
The cause for a `stale_dispatch`-category event is just "stale_dispatch" —
no creative renaming, no false distinction.

**Hook category set evolution discipline.** Because cause names mirror hook
category names, the spec is coupled to a set the hook owns. If the hook
splits or renames a category (e.g., `stale_dispatch_by_count` vs
`stale_dispatch_by_time`), this spec **must** be revised in the same PR that
changes the hook — the migration is: (a) extend the cause enum, (b) update
the decision tree's `match` branches, (c) add a row to the router-health
table. **Until the spec is revised, new hook categories fall through to
`unknown` via the decision tree's `default:` branch**, which the
`_UNKNOWN_SHARE_WARN` threshold will surface within a 7-day window.

**Honest framing** (pass-3 CONCERN): the "must be revised" sentence above is
a goodwill clause with no CI enforcement. The threshold-based detection is
the only real backstop. F-2's scope is widened to include "should we add a
hook-side test enumerating categories against the spec enum?" as a
calibration-debt question once we have baseline data.

**Decision tree** — drives off `category` first (the hook fact), then off
signals. This eliminates the ordering ambiguity of the v2-draft1 tree.

```
input: category, signals
match category:
  case 'stale_dispatch':                          → stale_dispatch
  case 'skill_mediated':
    if signals.last_skill_call_is_interactive:    → skill_mediated_interactive
    else:                                         → skill_mediated_other
  case 'bypass':
    if not signals.dispatch_skill_called_recently:
                                                  → router_direct_no_dispatch
                                                    # signals.count_agent_since_dispatch is null here; not read.
    elif signals.count_agent_since_dispatch >= 1: → router_direct_after_consumed_dispatch
    else:                                         → unknown
                                                    # bypass + dispatch exists + count == 0 — should not arise
                                                    # per hook logic (verified against classifyDispatchRich
                                                    # lines 162-216: dispatch found + countAgent==0 returns
                                                    # router_mediated or stale_dispatch, never bypass).
                                                    # Defensive bucket; expected ~0.
  default:                                        → unknown
```

The `unknown` bucket is **observable via its own threshold** (see
router-health integration). If `unknown` share rises, the taxonomy is wrong
and the enum gets extended.

## Module API

```js
// hooks/lib/bypass-taxonomy.js

const INTERACTIVE_SKILLS = new Set([
  'gh-create-issue',
  'project-review',
  'gh-pr-review-address',
  'claude-audit',
  'gh-refresh-issues',
]);
// Caveat: this set has no automatic completeness gate. When a new
// interactive skill ships, its events will fall to `skill_mediated_other`
// until the set is updated. The analyzer surfaces `skill_mediated_other`
// share so this is observable; see § follow-ups, item F-3.

/**
 * Classify a drift event by inspecting only the tool-call shape and the
 * hook's already-computed `category`. No prompt content is read.
 *
 * Pure function — no I/O, no module-level mutation.
 *
 * @param {string} category                    Hook's category field —
 *                                              'bypass' | 'skill_mediated' | 'stale_dispatch'.
 * @param {{subagent_type: string}} toolCall   Agent tool-use parameters.
 * @param {Array<TranscriptEntry>} conversationHistory
 *                                              Recent turns (most-recent last).
 * @returns {{
 *   signals: BypassSignals,
 *   cause: 'skill_mediated_interactive' | 'skill_mediated_other'
 *        | 'router_direct_after_consumed_dispatch'
 *        | 'router_direct_no_dispatch'
 *        | 'stale_dispatch' | 'unknown',
 * }}
 *
 * @throws {Error} on malformed inputs (missing category, non-array history).
 *                  The hook integration catches and emits the event without
 *                  enrichment — see § Hook integration.
 */
function classify(category, toolCall, conversationHistory) { ... }

module.exports = { classify, INTERACTIVE_SKILLS };
```

The module has no I/O. Unit-testable with hand-crafted history arrays.

## Hook integration

`hooks/check-agent-dispatch-pairing.js` changes:

1. Load the module **once at hook startup**, outside the per-event try
   block, with explicit module-load error handling:
   ```js
   let bypassTaxonomyClassify;
   try {
     ({ classify: bypassTaxonomyClassify } = require('./lib/bypass-taxonomy'));
   } catch (err) {
     console.error('[bypass-taxonomy] module load failed; events will emit without enrichment:', err);
     bypassTaxonomyClassify = null;  // no-op fallback
   }
   ```
   This addresses C6: a `require`-time throw cannot kill the hook because
   the require runs inside its own try, and the fallback `null` is
   short-circuited at use-time.
2. Just before the existing drift-event emit (for `category ∈ {bypass,
   skill_mediated, stale_dispatch}`), call:
   ```js
   if (bypassTaxonomyClassify) {
     try {
       const result = bypassTaxonomyClassify(category, toolCall, conversationHistory);
       if (result && result.signals && result.cause) {
         event.bypass_signals = result.signals;
         event.bypass_cause = result.cause;
       } else {
         console.error('[bypass-taxonomy] classify returned malformed shape; emitting without enrichment');
       }
     } catch (err) {
       console.error('[bypass-taxonomy] classify threw; emitting without enrichment:', err);
     }
   }
   ```
3. The unenriched event still emits in all failure modes: module-load
   throw, classify throw, malformed return shape. **Telemetry enrichment
   never blocks dispatch.**

No change to:
- The pairing-check decision logic.
- Which events fire.
- The hook's `category` field's contract.
- `advisory_override` event shape.

## Analyzer script

`scripts/analyze-drift-causes.py`:

```
$ python scripts/analyze-drift-causes.py --days 7
Bypass cause distribution (last 7 days, 497 events; 12 pre-enrichment baseline):

  skill_mediated_interactive               260  52.3%   ✓ expected
  router_direct_after_consumed_dispatch    102  20.5%   ⚠ unwanted
  router_direct_no_dispatch                 71  14.3%   ⚠ unwanted
  skill_mediated_other                      40   8.0%   ? review
  stale_dispatch                            18   3.6%   ? review
  unknown                                    6   1.2%

Disagreement check: 2 events (0.4%) where re-derived cause from signals
  ≠ stored bypass_cause. → run with --disagreements to inspect.
```

Flags:

| Flag                | Behavior                                                                                  |
| ------------------- | ----------------------------------------------------------------------------------------- |
| `--days N`          | Window N days back from now (default: 7).                                                 |
| `--since ISO`       | Window from explicit ISO timestamp (overrides `--days`).                                  |
| `--disagreements`   | Print events where re-derived cause from signals ≠ stored `bypass_cause`.                 |
| `--by-agent`        | Cross-tab cause × `subagent_type`.                                                        |
| `--json`            | Machine-readable output.                                                                  |

Implementation: pure stdlib (`pathlib`, `json`, `dataclasses`, `argparse`).
Uses the same `~/.claude/.venv` Python the existing `_health.py` uses.
Pattern follows `_health.py:217–319` (`compute_metrics`).

Pre-enrichment events (no `bypass_signals` field) are silently skipped from
the cause-distribution counts but reported as a "pre-enrichment baseline"
count in the header.

## router-health integration

`src/claude_wayfinder/_health.py` `format_report_output()` gains one new
section, inserted between "Runtime Telemetry" (line 1030) and "Informational
Metrics" (line 1069):

```
## Bypass causes (7-day window, 497 events)

| Cause                                   |  Count |  Share | Disposition |
| --------------------------------------- | -----: | -----: | ----------- |
| skill_mediated_interactive              |    260 | 52.3%  | expected    |
| router_direct_after_consumed_dispatch   |    102 | 20.5%  | unwanted    |
| router_direct_no_dispatch               |     71 | 14.3%  | unwanted    |
| skill_mediated_other                    |     40 |  8.0%  | review      |
| stale_dispatch                          |     18 |  3.6%  | review      |
| unknown                                 |      6 |  1.2%  | review      |

PASS — unwanted-bypass share 34.8% (threshold: <50% bootstrap)
PASS — unknown share 1.2% (threshold: <10%)
PASS — sample size 497 (threshold: ≥100; report shows "N/A — insufficient data" below)
```

Two thresholds, both following the existing underscore-prefix constant
convention (`_health.py:36–39`). **Both are bootstrap values** — neither has
empirical grounding pre-deployment, and both are recalibrated together at the
2026-06-02 review (F-1/F-2):

- `_UNWANTED_BYPASS_SHARE_MAX = 0.50` (bootstrap; tighten after baseline).
- `_UNKNOWN_SHARE_WARN = 0.10` (bootstrap; the baseline `unknown` rate from
  hook-initiated dispatches alone is unknown until we ship — see § Follow-ups F-2).
  **Honest framing** (pass-3 CONCERN): 10% × 487 events/week ≈ 49 events/week
  can flow to `unknown` before this trips. That is deliberately loose so we
  can observe and learn the right value at the F-2 review. If `unknown` share
  is in the 1-5% range at F-2, tighten to 2× baseline. If it's >10%, the
  taxonomy is wrong and the enum gets extended in the same review.

Plus a low-N guard:

- `_BYPASS_CAUSE_MIN_SAMPLE = 100`. When the 7-day window contains fewer than
  100 enriched events, the section renders `N/A — insufficient post-enrichment
  data (have N, need 100)` and emits no PASS/FAIL. This protects against
  noisy early-deployment numbers driving threshold changes (addresses
  inquisitor's low-N concern adjacent to C5).

`skills/router-health/SKILL.md` gains one trigger phrase: "bypass causes."

## Forward-only migration

Existing events stay as-is. New events emitted after the hook ships carry the
new fields. No retroactive computation, no migration script.

Rationale: backfilling would require replaying `conversation_history` from
transcripts, which is expensive, lossy (transcripts get rotated), and
unnecessary — the analyzer naturally windows to recent data and the low-N
guard above prevents premature conclusions.

Analyzer + router-health silently skip events lacking `bypass_signals`
(treated as pre-enrichment baseline). Both display the pre-enrichment count
so the gap is visible.

## Storage growth

Revalidated against live file (`wc -l ~/.claude/state/router-drift.jsonl`):
**1132 events over 16 days = ~71 events/day** (v2-draft1 said 50; off by 30%).

Enriched events: ~98% of daily events get the new fields. Per-enriched-event
delta: ~250 B (6 fields + cause string + JSON overhead). Daily growth:
**~17 KB/day → ~6.3 MB/year.** Even at 2× current rate (12.6 MB/year) the file
stays well under 100 MB for years; **rotation stays out of scope for v1 with
the explicit horizon "drift-log file ≤ 100 MB"**. If the file approaches that
threshold, file a new issue at that time.

## Testing

Three layers:

1. **`hooks/tests/bypass-taxonomy.test.js`** — pure-function unit tests
   following the convention of `hooks/tests/dispatch-log.test.js`:
   - one per cause (6 tests, one per enum value)
   - one per signal-derivation rule (6 tests)
   - one per failure mode: throw on malformed category, return-shape integrity
     when category is unrecognized
   - `INTERACTIVE_SKILLS` set is the exhaustive list the v1 spec ships with
     (smoke test: assert set size matches documentation)

   Run via the existing Node test setup.

2. **`tests/test_analyze_drift_causes.py`** — Python tests with crafted JSONL
   fixtures:
   - each cause appears in distribution output
   - malformed events skipped silently
   - `--disagreements` flag surfaces disagreement events
   - window filtering (`--days`, `--since`) works
   - pre-enrichment events counted in baseline header, not in cause distribution
   - low-N case: window with < 100 enriched events renders N/A row

3. **Hook integration** — three failure-mode tests with **distinct
   behavioral assertions** (addresses C6, sharpened in pass 2 to avoid
   "checkbox-thrice" degeneration). Each test must assert a different
   observable consequence:
   - **Classify-time failure (parameterized over throw + malformed return)**:
     a single parameterized test exercises both `classify` throwing and
     `classify` returning `{}`. Both run through the same per-event try/catch
     and recovery path; the consolidation honestly reflects that they are
     one code path with two input shapes (pass-3 CONCERN). Assert
     `event.bypass_signals === undefined` AND `event.bypass_cause === undefined`
     AND the event JSON line in the drift log is well-formed AND stderr
     contains one of `{'classify threw', 'malformed shape'}` matching the
     branch under test.
   - **Module-load throw**: spawn the hook with a broken `bypass-taxonomy.js`
     (e.g., a file with a syntax error). Assert the hook process *starts*
     (does not exit nonzero), the first event still appends to the drift log,
     and `stderr` contains `'module load failed'`. This test exercises a
     genuinely distinct code path the classify-time test cannot reach
     (the require-time try/catch in step 1 of § Hook integration).

CI: existing Node + Python test jobs pick this up automatically.

## Acceptance criteria

1. New events in `~/.claude/state/router-drift.jsonl` with
   `category ∈ {bypass, skill_mediated, stale_dispatch}` carry the
   `bypass_signals` and `bypass_cause` fields described above.
2. `bypass_cause` values are drawn from the enum: `skill_mediated_interactive`,
   `skill_mediated_other`, `router_direct_after_consumed_dispatch`,
   `router_direct_no_dispatch`, `stale_dispatch`, `unknown`.
3. `hooks/lib/bypass-taxonomy.js` exists with the documented API; tests in
   `hooks/tests/bypass-taxonomy.test.js` (not `hooks/lib/`).
4. `scripts/analyze-drift-causes.py` exists and produces the report shape
   shown above for at least `--days N` and `--disagreements` flags.
5. `_health.py` (`format_report_output`) includes the new "Bypass causes"
   section with low-N guard and both thresholds.
6. `skills/router-health/SKILL.md` description mentions "bypass causes" as a
   trigger phrase.
7. Hook never blocks dispatch on enrichment failure — verified by three
   failure-mode tests (synchronous throw, malformed return, module-load throw).
8. `_health.py` existing parsing continues to work — verified by running the
   existing tests against a fixture file that mixes pre-enrichment and
   post-enrichment events.
9. **Follow-up issues filed before the implementing PR's merge, AND
   referenced by issue number in this spec before that PR merges** (so an
   AC reviewer can verify by reading the spec; addresses pass-2 CONCERN on
   F-1/F-2/F-3 traceability). Each issue gets label `telemetry-v2-followup`
   and milestone `Telemetry v2 baseline review` (created at the same time):
   - **F-1** (filed as #159): "Telemetry v2 — 2-week post-ship baseline
     review meeting" — assignee @cbeaulieu-gt, milestone #7, due 2026-06-02.
   - **F-2** (filed as #160): "Telemetry v2 — recalibrate
     `_UNWANTED_BYPASS_SHARE_MAX` and `_UNKNOWN_SHARE_WARN`" — blocks on
     #159, milestone #7, due 2026-06-02.
   - **F-3** (filed as #161): "Telemetry v2 — first quarterly
     INTERACTIVE_SKILLS audit" — assignee @cbeaulieu-gt, due 2026-08-19
     (separate cadence; no milestone).

   **Merge gate**: this spec's frontmatter gains `followups_filed:` listing
   the three issue numbers. The implementing PR's description must reference
   all three. Reviewer's verification is a one-line check, not a manual
   search.
10. The postmortem's load-bearing facts are not violated:
    - No user-prompt content is captured in drift events.
    - No `decision_id` contract is proposed.
    - No `agents/general-purpose.md` edits are required.
    - No cross-event joining is attempted.

## Follow-ups (filed; see AC #9)

| ID  | Issue | Title                                                                                | Due        |
| --- | ----- | ------------------------------------------------------------------------------------ | ---------- |
| F-1 | #159  | 2-week post-ship baseline review meeting                                             | 2026-06-02 |
| F-2 | #160  | Recalibrate `_UNWANTED_BYPASS_SHARE_MAX` and `_UNKNOWN_SHARE_WARN` (blocks on #159)  | 2026-06-02 |
| F-3 | #161  | First quarterly INTERACTIVE_SKILLS audit                                             | 2026-08-19 |

F-1 and F-2 share milestone #7 ("Telemetry v2 baseline review"). F-3 is on
a separate cadence.

## Out of scope

- `advisory_override` enrichment (~0% of events, not load-bearing).
- `matcher_decision` enrichment in `dispatch-log.jsonl` (separate work).
- Drift-event rotation policy.
- Backfill of historical events.
- Cross-event joining via `decision_id`.
- Dashboards beyond the router-health text section.
- Capturing any user-prompt content.
- Automatic detection of new interactive skills (handled via the F-3
  quarterly audit).
- Concrete derivation of `preceded_by_hook_injection` (deferred; hook-initiated
  events flow to `unknown` and surface via the `_UNKNOWN_SHARE_WARN` threshold).

## Relationship to existing artifacts

- **Issue #143** — this spec addresses Enrichment 1 ("advisory_override
  reliability") indirectly (by making the dominant 98% of events answer the
  same calibration questions) and **explicitly defers** Enrichments 2/3/4
  (matcher_decision shape changes) to a separate issue.
- **PR #152 (abandoned)** — superseded by this spec.
- **PR #155 (postmortem)** — the load-bearing-facts reference.
- **Issue #135 (AND-groups)** — independent. Its AC #7 adds `groups_fired` to
  the rationale string of `matcher_decision`; that is a `dispatch-log` field
  and unaffected by this spec.

## Revision log

- **v2-draft1** (initial brainstorm): 7 signals, 6-cause enum including
  `postuse_hook_initiated` and `router_direct_after_stale_dispatch`; tests
  under `hooks/lib/`; single unwanted-share threshold; 50/day volume.
- **v2-draft2** (post inquisitor pass 1):
  - **B1 fix**: new signal `count_agent_since_dispatch`; cause
    `router_direct_after_consumed_dispatch` added to capture chained-Agent
    discipline failures; `router_direct_after_stale_dispatch` removed (its
    semantics now live in the hook's `stale_dispatch` category).
  - **B2 fix**: enriched category set extended to include `stale_dispatch`;
    cause enum mirrors the hook category name to avoid collision.
  - **B3 fix**: `in_skill_context` dropped (redundant with hook category);
    `preceded_by_hook_injection` and `postuse_hook_initiated` dropped (no
    concrete signal derivation; events flow to `unknown` with threshold).
  - **C1 fix**: test path corrected to `hooks/tests/bypass-taxonomy.test.js`.
  - **C2 fix**: `_UNKNOWN_SHARE_WARN = 0.10` added.
  - **C3 fix**: volume math redone against live file (71/day).
  - **C4 fix**: F-3 follow-up filed for quarterly INTERACTIVE_SKILLS audit.
  - **C5 fix**: F-1/F-2 follow-ups committed as acceptance criteria with dates.
  - **C6 fix**: module load wrapped in dedicated try/catch; three failure-mode
    tests added.
  - **N1 fix**: decision tree drives off `category` first (hook fact), then
    signals; ordering ambiguity eliminated.
  - **N2 fix**: `subagent_type` retention in signals is now explicitly
    justified by analyzer-join convenience.
- **v2-draft3** (this revision, post inquisitor pass 2):
  - **pass-2 BLOCKING #1 fix (window mismatch)**: `dispatch_skill_called_recently`
    now explicitly defined against the hook's window (full history back to
    last dispatch, no user-turn boundary), matching `classifyDispatchRich`
    lines 162-170. Without this, signal extractor and hook would disagree
    and silently re-create pass-1 B1's misclassification.
  - **pass-2 BLOCKING #2 fix (count_0 convention)**: `count_agent_since_dispatch`
    is now `null` (not `0`) when no dispatch exists in history. The `0`
    convention collapsed the input space and disabled the analyzer's
    `--disagreements` self-audit for the dominant `router_direct_no_dispatch`
    bucket. The decision tree skips this field in the no-dispatch branch.
  - **pass-2 CONCERN fixes**:
    - `_UNKNOWN_SHARE_WARN = 0.10` re-labeled as bootstrap (with rationale)
      and folded into F-2's recalibration scope.
    - F-1/F-2/F-3 now have assignees, labels, milestone, and a merge gate
      (`followups_filed:` in this spec's frontmatter, referenced by the
      implementing PR description).
    - Three failure-mode tests now have distinct behavioral assertions
      (stderr message text differs; module-load test exercises a unique
      code path the other two never reach).
    - Volume math horizon named explicitly ("drift-log file ≤ 100 MB").
    - Hook category set evolution discipline now spelled out: spec must be
      revised in the same PR that changes the hook category set; until then,
      new categories fall through to `unknown` and surface via
      `_UNKNOWN_SHARE_WARN`.
  - **pass-2 NITs**: cosmetic (disposition column collision). Not addressed
    in v2-draft3 — table dispositions stay as-is; future revision can
    diversify the column.
- **v2-draft4** (this revision, post inquisitor pass 3 — pattern converged):
  - **pass-3 BLOCKING fix**: `followups_filed:` field added to frontmatter
    with `TBD` placeholders. AC #9 merge gate is now self-verifying.
  - **pass-3 CONCERN fixes**:
    - Classify-time failure tests consolidated from two (synchronous throw,
      malformed return) into one parameterized test, since both exercise
      the same code path. Module-load test stays separate (genuinely
      distinct code path).
    - `_UNKNOWN_SHARE_WARN = 0.10` framing made honest: "deliberately loose
      so we can observe and learn"; F-2 commits to either tighten (if rate
      is low) or extend enum (if rate is high).
    - Hook-category-set evolution discipline acknowledged as a goodwill
      clause; the threshold is the only real backstop; F-2's scope widened
      to include CI-enforcement question as calibration debt.
  - **pass-3 NIT**: volume-math horizon "≤ 100 MB" not changed — accepted
    as "effectively never" without further qualification.

---
🤖 _Generated by Claude Code on behalf of @cbeaulieu-gt_
