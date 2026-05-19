---
title: Telemetry enrichment v2 — bypass-cause taxonomy
date: 2026-05-19
tracking: glitchwerks/claude-wayfinder#143
supersedes: glitchwerks/claude-wayfinder#152 (abandoned)
postmortem: docs/superpowers/postmortems/2026-05-18-telemetry-enrichment-pivot/POSTMORTEM.md
status: draft
touches:
  - hooks/check-agent-dispatch-pairing.js
  - hooks/lib/bypass-taxonomy.js
  - hooks/lib/bypass-taxonomy.test.js
  - scripts/analyze-drift-causes.py
  - tests/test_analyze_drift_causes.py
  - scripts/router_health.py
  - skills/router-health/SKILL.md
skills_relevant:
  - hook-authoring
  - python
---

# Telemetry enrichment v2 — bypass-cause taxonomy

## Motivation

v1 (PR #152, abandoned) tried to recover *what the matcher would have decided*
for the 98% of drift events where the matcher never ran. That design died on
substrate confusion and cross-process contract fragility. See the postmortem
for full forensics: `docs/superpowers/postmortems/2026-05-18-telemetry-enrichment-pivot/POSTMORTEM.md`.

v2 has a narrower goal: **categorize *what actually happened* for each bypass
event**, using only data the PreToolUse hook already has on the tool-call shape.
No user-prompt content is captured. No matcher counterfactual is computed. No
cross-event joining is attempted.

Current event distribution (`~/.claude/state/router-drift.jsonl`, ~1086 events
as of 2026-05-19):

| Category            | Share |
| ------------------- | ----- |
| `skill_mediated`    | ~52%  |
| `bypass`            | ~46%  |
| `advisory_override` | ~0%   |
| malformed           | ~2%   |

The 98% of events in `bypass` + `skill_mediated` are the target of this work.
`advisory_override` is left alone — it is structurally rare and not load-bearing
for current questions.

## Goal

Enable the user to answer, from `~/.claude/state/router-drift.jsonl` and the
`router-health` report, questions of the form:

- "What fraction of bypasses are skill-mediated by design, vs. router-direct
  Agent calls without dispatch?"
- "Which sub-agent is most often invoked via a `router_direct_no_dispatch`
  path? (i.e., where is router discipline weakest?)"
- "Are there bypass causes the taxonomy doesn't recognize (`unknown` share)?"

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
                                                       │   (signals + hint)      │
                                                       └────────────┬────────────┘
                                                                    │
                                            additive fields on existing drift event
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

1. `hooks/lib/bypass-taxonomy.js` — pure function with unit tests.
2. `scripts/analyze-drift-causes.py` — ad-hoc CLI analyzer.
3. New section in the `router-health` report.

## Signal set

Every `bypass` and `skill_mediated` drift event gains two additive fields:

```json
"bypass_signals": {
  "subagent_type": "code-writer",
  "dispatch_skill_called_recently": false,
  "last_skill_call_name": "gh-pr-review-address",
  "last_skill_call_is_interactive": true,
  "in_skill_context": true,
  "turns_since_user_message": 3,
  "preceded_by_hook_injection": false
},
"bypass_cause": "skill_mediated_interactive"
```

Field-by-field derivation rules:

| Field                              | Source                                                                                                              |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| `subagent_type`                    | Agent tool call's `subagent_type` parameter (already available to the hook).                                        |
| `dispatch_skill_called_recently`   | Boolean — was a `claude-wayfinder:dispatch` skill_call observed in `conversation_history` within the last 3 turns?  |
| `last_skill_call_name`             | Name of the most recent skill_call in `conversation_history`, or `null` if none.                                    |
| `last_skill_call_is_interactive`   | `last_skill_call_name ∈ INTERACTIVE_SKILLS` (gh-create-issue, project-review, gh-pr-review-address, claude-audit, gh-refresh-issues). |
| `in_skill_context`                 | Boolean — is the Agent tool call lexically inside a Skill execution context? (Already a signal the existing hook computes for the `skill_mediated` vs `bypass` category split.) |
| `turns_since_user_message`         | Integer — hop count to the most recent `user` turn in `conversation_history`.                                       |
| `preceded_by_hook_injection`       | Boolean — did the immediately preceding turn carry an `additionalContext` block from a `PostToolUse` or similar hook? |

`STALE_DISPATCH_WINDOW_TURNS = 3` and `INTERACTIVE_SKILLS` are module-level
constants in `bypass-taxonomy.js` and are the spec's authoritative source for
those values.

## Cause enum

```
skill_mediated_interactive              expected   — last skill was a known interactive skill
skill_mediated_other                    review     — in_skill_context, skill not in interactive set
postuse_hook_initiated                  expected   — preceded by hook injection (e.g. ask-about-inquisitor)
router_direct_no_dispatch               unwanted   — router invoked Agent with no recent dispatch call
router_direct_after_stale_dispatch      unwanted   — dispatch called but > STALE_DISPATCH_WINDOW_TURNS turns ago,
                                                     fresh user turn intervened
unknown                                 review     — signals don't match any defined cause
```

**Crucial precondition.** Drift events from this hook only fire when pairing
*failed* — i.e., the dispatch skill call did not authorize this Agent
invocation. So an event arriving at `classify()` with `dispatch_skill_called_recently == true`
necessarily means the dispatch call exists in history but is *stale* (a user
turn intervened, or the window has expired in some other way that the hook's
own pairing check rejected). `classify()` does not re-derive staleness — it
takes the hook's "this event fires" as the staleness signal.

Decision tree (evaluated top-to-bottom; first match wins):

```
if preceded_by_hook_injection:                                  → postuse_hook_initiated
elif in_skill_context and last_skill_call_is_interactive:       → skill_mediated_interactive
elif in_skill_context:                                          → skill_mediated_other
elif dispatch_skill_called_recently:                            → router_direct_after_stale_dispatch
elif turns_since_user_message ≤ 2:                              → router_direct_no_dispatch
else:                                                           → unknown
```

`unknown` catches the case where no recent dispatch call exists AND the user
turn is far back (chained automation that isn't hook-injected). Conservative
on purpose — the analyzer will surface `unknown` share and the taxonomy can
evolve to absorb a recognizable subset of it.

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

const STALE_DISPATCH_WINDOW_TURNS = 3;

/**
 * Classify a bypass/skill_mediated drift event by inspecting only the tool-call
 * shape and recent conversation history. No prompt content is read.
 *
 * @param {{subagent_type: string, prompt: string}} toolCall
 * @param {Array<TranscriptEntry>} conversationHistory  Recent turns (most-recent last)
 * @param {{inSkillContext: boolean}} options           Caller-supplied facts the
 *                                                       existing pairing hook already
 *                                                       computes
 * @returns {{
 *   signals: {
 *     subagent_type: string,
 *     dispatch_skill_called_recently: boolean,
 *     last_skill_call_name: string|null,
 *     last_skill_call_is_interactive: boolean,
 *     in_skill_context: boolean,
 *     turns_since_user_message: number,
 *     preceded_by_hook_injection: boolean,
 *   },
 *   cause: 'skill_mediated_interactive' | 'skill_mediated_other'
 *        | 'postuse_hook_initiated' | 'router_direct_no_dispatch'
 *        | 'router_direct_after_stale_dispatch' | 'unknown'
 * }}
 */
function classify(toolCall, conversationHistory, options) { ... }

module.exports = { classify, INTERACTIVE_SKILLS, STALE_DISPATCH_WINDOW_TURNS };
```

The module has no I/O. It can be unit-tested with hand-crafted history arrays.

## Hook integration

`hooks/check-agent-dispatch-pairing.js` changes:

1. Add `const { classify } = require('./lib/bypass-taxonomy');` at the top.
2. Just before the existing drift-event `.emit()` for `category === 'bypass'`
   or `category === 'skill_mediated'`, call `classify(toolCall, conversationHistory, {inSkillContext})`.
3. Merge `{bypass_signals: result.signals, bypass_cause: result.cause}` into
   the event payload.
4. If `classify` throws (malformed input, missing fields), the hook logs a
   warning to stderr and emits the event without the new fields. **Telemetry
   enrichment never blocks dispatch.**

No change to:
- The pairing-check decision logic.
- Which events fire.
- The `category` field's contract.
- `advisory_override` event shape.

## Analyzer script

`scripts/analyze-drift-causes.py`:

```
$ python scripts/analyze-drift-causes.py --days 7
Bypass cause distribution (last 7 days, 3247 events):

  skill_mediated_interactive          1834  56.5%   ✓ expected
  router_direct_no_dispatch            612  18.8%   ⚠ unwanted
  postuse_hook_initiated               401  12.4%   ✓ expected
  skill_mediated_other                 287   8.8%   ? review
  router_direct_after_stale_dispatch    87   2.7%   ⚠ unwanted
  unknown                               26   0.8%

Disagreement check: 14 events (0.4%) where signals don't support the cause
  → run with --disagreements to inspect
```

Flags:

| Flag                | Behavior                                                                                  |
| ------------------- | ----------------------------------------------------------------------------------------- |
| `--days N`          | Window N days back from now (default: 7).                                                 |
| `--since ISO`       | Window from explicit ISO timestamp (overrides `--days`).                                  |
| `--disagreements`   | Print events where re-derived cause from signals ≠ stored `bypass_cause`.                 |
| `--by-agent`        | Cross-tab cause × `subagent_type`.                                                        |
| `--json`            | Machine-readable output for downstream tooling.                                           |

Implementation: pure stdlib + `pathlib` + `json`, no external dependencies.
Uses the same `~/.claude/.venv` Python the existing `_health.py` uses.

Pre-enrichment events (no `bypass_signals` field) are silently skipped from
the cause-distribution counts but counted in a `pre-enrichment baseline` line
in the report.

## router-health integration

`scripts/router_health.py --report` gains one new section, between the
existing "Drift events" and "Notable findings" sections:

```
## Bypass causes (7-day window)

| Cause                                  |  Count |   Share | Disposition |
| -------------------------------------- | -----: | ------: | ----------- |
| skill_mediated_interactive             |   1834 |  56.5%  | expected    |
| router_direct_no_dispatch              |    612 |  18.8%  | unwanted    |
| postuse_hook_initiated                 |    401 |  12.4%  | expected    |
| skill_mediated_other                   |    287 |   8.8%  | review      |
| router_direct_after_stale_dispatch     |     87 |   2.7%  | unwanted    |

PASS — unwanted-bypass share at 21.5% (threshold: <30% for now; tighten after baseline).
```

The threshold (`UNWANTED_BYPASS_SHARE_THRESHOLD = 0.30`) is intentionally loose
in v1. After ~2 weeks of post-ship data, the threshold is tightened based on
observed baseline via a follow-up PR.

`skills/router-health/SKILL.md` gains one trigger phrase: "bypass causes."

## Forward-only migration

Existing events stay as-is. New events emitted after the hook ships carry the
new fields. No retroactive computation, no migration script.

Rationale: backfilling would require replaying `conversation_history` from
transcripts, which is expensive, lossy (transcripts get rotated), and
unnecessary — the analyzer naturally windows to recent data.

Analyzer + router-health silently skip events lacking `bypass_signals` (treated
as pre-enrichment baseline). The "pre-enrichment baseline" row in analyzer
output makes this visible.

## Storage growth

Current drift event volume: ~1086 events accumulated to date, recent rate
~50/day across all categories, ~98% (~49/day) get enriched.

Per-enriched-event delta: ~250 bytes (7 small fields + cause string + JSON
overhead). Daily growth: **~12 KB/day** → **~4.4 MB/year**. No rotation
needed in the foreseeable horizon.

If volume rises 10×, rotation gets revisited; **rotation is explicitly out of
scope for v1**. The postmortem's volume-math flag (v1 was 12-60× off) is
re-validated: the 475/day figure in the postmortem counted dispatch-log not
drift-log; drift-log is much smaller.

## Testing

Three layers:

1. **`hooks/lib/bypass-taxonomy.test.js`** — pure-function unit tests:
   - one per cause (6 tests, one per enum value)
   - one for each signal-derivation rule (7 tests)
   - one per signal-disagreement edge case (e.g., `in_skill_context && !last_skill_call_name` — a malformed input case that should resolve to `unknown` rather than throw)

   Run via the existing Node test setup.

2. **`tests/test_analyze_drift_causes.py`** — Python tests with crafted JSONL
   fixtures:
   - each cause appears in distribution output
   - malformed events skipped silently
   - `--disagreements` flag surfaces disagreement events
   - window filtering (`--days`, `--since`) works
   - pre-enrichment events counted in baseline row, not in cause distribution

3. **No new integration test for the hook.** The existing pairing-hook tests
   cover the emission path; we add one assertion that the enriched payload
   shape is present when bypass_signals are computed successfully, and that
   the hook still emits an event when `classify` throws.

CI: existing Node + Python test jobs pick this up automatically.

## Acceptance criteria

1. New events in `~/.claude/state/router-drift.jsonl` with
   `category ∈ {bypass, skill_mediated}` carry the `bypass_signals` and
   `bypass_cause` fields described above.
2. `bypass_cause` values are drawn from the enum: `skill_mediated_interactive`,
   `skill_mediated_other`, `postuse_hook_initiated`,
   `router_direct_no_dispatch`, `router_direct_after_stale_dispatch`,
   `unknown`.
3. `hooks/lib/bypass-taxonomy.js` exists with the documented API and unit
   tests covering every cause and every signal-derivation rule.
4. `scripts/analyze-drift-causes.py` exists and produces the report shape
   shown above for at least the `--days N` and `--disagreements` flags.
5. `scripts/router_health.py --report` includes the new "Bypass causes"
   section with PASS/WARN/FAIL based on the configured threshold.
6. `skills/router-health/SKILL.md` description mentions "bypass causes" as a
   trigger phrase.
7. The hook never blocks dispatch on a classify error — verified by a test
   that injects a throw and asserts the drift event is still emitted (without
   the new fields).
8. `_health.py` existing parsing continues to work — verified by running the
   existing tests against a fixture file that mixes pre-enrichment and
   post-enrichment events.
9. The postmortem's load-bearing facts are not violated:
   - No user-prompt content is captured in drift events.
   - No `decision_id` contract is proposed.
   - No `agents/general-purpose.md` edits are required.
   - No cross-event joining is attempted.

## Out of scope

- `advisory_override` enrichment (~0% of events, not load-bearing).
- `matcher_decision` enrichment in `dispatch-log.jsonl` (separate work).
- Drift-event rotation policy.
- Backfill of historical events.
- Cross-event joining via `decision_id`.
- Dashboards beyond the router-health text section.
- Capturing any user-prompt content.

## Relationship to existing artifacts

- **Issue #143** — this spec addresses Enrichment 1 ("advisory_override
  reliability") indirectly (by making the dominant 98% of events answer the
  same calibration questions) and **explicitly defers** Enrichments 2/3/4
  (matcher_decision shape changes) to a separate issue.
- **PR #152 (abandoned)** — superseded by this spec.
- **PR #155 (postmortem)** — the load-bearing-facts reference for this spec.
- **Issue #135 (AND-groups)** — independent. Its AC #7 adds `groups_fired` to
  the rationale string of `matcher_decision`; that is a `dispatch-log` field
  and unaffected by this spec.

---
🤖 _Generated by Claude Code on behalf of @cbeaulieu-gt_
