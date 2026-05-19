---
title: Telemetry enrichment design — pivot postmortem
date: 2026-05-18
tracking: glitchwerks/claude-wayfinder#143
abandoned_pr: glitchwerks/claude-wayfinder#152
status: archived-for-reference
---

# Telemetry enrichment design — pivot postmortem

This folder holds the abandoned first attempt at issue #143 (dispatch
telemetry enrichment). The design ran through two iterations and two
reviewer passes before adversarial review surfaced a fact that invalidated
the entire premise. The artifacts are kept here as a **"what not to do"
reference** for the second attempt.

## What's in this folder

| File                     | What it is                                                                                          |
| ------------------------ | --------------------------------------------------------------------------------------------------- |
| `spec-final.md`          | The v2 spec after addressing all 11 project-reviewer findings. The document inquisitor tore apart.  |
| `project-reviewer.md`    | Project-reviewer's findings against spec v1 (2 BLOCKING, 6 CONCERN, 3 NIT). All addressed in v2.    |
| `inquisitor.md`          | Inquisitor's findings against spec v2 (4 BLOCKING, 6 CONCERN). The pivot trigger.                   |
| `POSTMORTEM.md`          | This file.                                                                                          |

## The headline fact that invalidated the design

`~/.claude/state/router-drift.jsonl` contains **1068 drift events**:

| Category            | Count | Share        |
| ------------------- | ----: | ------------ |
| `skill_mediated`    |   550 | 51.5%        |
| `bypass`            |   491 | 46.0%        |
| `advisory_override` |     0 | **0.0%**     |
| malformed           |    27 | 2.5%         |

The spec's architectural centerpiece — `matcher_decision` persistence,
`decision_id` cross-reference, audit-line format change, Stop-hook join
logic, version-skew degradation, dedicated acceptance criteria — was built
to enrich the `advisory_override` path. **That path produced zero events
in the entire collected dataset.** The two categories that produce 100% of
real drift signal got `raw_input` only with no plan for `features` derivation.

## How we got here

1. **Original premise (good)**: drift telemetry tells us *that* drift
   happened but not *what was in play*. Enrich it.
2. **Wrong assumption (silent)**: all three drift categories are roughly
   equivalent in volume, so enriching them with one strategy is
   reasonable. Never checked the actual distribution.
3. **First spec (v1)**: built a uniform enrichment architecture across all
   three categories, batch-shell-out to Python for feature extraction in
   the Stop hook.
4. **Project-reviewer pass**: caught two real structural bugs — the
   two-emitter problem (advisory_override at Stop time, bypass/skill_mediated
   at PreToolUse time, different lifecycles) and the catalog-coupled
   features schema problem. Did NOT check the data distribution.
5. **Spec v2**: split enrichment by emitter, made features catalog-agnostic.
   Looked clean. Still treated all three categories as deserving design
   attention.
6. **Inquisitor pass**: counted the actual events. Saw the 0/1068 split.
   Surfaced three more BLOCKINGs that compounded:
   - `raw_input` for bypass/skill_mediated would have captured the
     router's dispatch brief, not the user prompt — contaminating 100%
     of production-relevant telemetry with wrong-substrate features.
   - `decision_id` contract spans `agents/general-purpose.md`, which is
     user-scope and outside the plugin's release boundary. No enforcement
     mechanism.
   - Storage growth math was 12-60x off (real dispatch volume is
     ~475/day, not the assumed 50/day). The "no rotation needed" decision
     was invalid.

## Lessons for the second attempt

1. **Read the data first.** Before designing telemetry enrichment, count
   the events. The numbers should drive the architecture, not impressions
   of which categories matter.

2. **PreToolUse `raw_input` source matters.** The PreToolUse hook
   (`check-agent-dispatch-pairing.js`) runs BEFORE the matcher. The
   matcher's `raw_input` (the user prompt the router responded to) lives
   in `conversation_history` at a turn N steps back, NOT in the Agent tool
   call's `prompt` parameter (which is the router's dispatch brief).
   Capturing the wrong field produces feature distributions describing the
   router's writing style, not user intent.

3. **Cross-process contracts spanning trust boundaries are fragile.** The
   `decision_id` design required three sites (Python matcher, router agent
   prose, Node scanner) to agree on a string format. One of those sites
   lives in the user's `~/.claude/` tree — outside the plugin's release
   boundary, with no test coverage and no schema enforcement. When
   plugins propose contracts on files they don't ship, the plan needs an
   enforcement mechanism, not goodwill.

4. **The matcher didn't run for bypass/skill_mediated.** Architecturally,
   that means `matcher_decision` rows have no peer for those events.
   Designs that hinge on cross-referencing matcher_decision rows are
   structurally inapplicable to 96% of the dataset.

5. **Three-field model framing was scaffolding.** "raw_input / features /
   score_components with distinct stability contracts" sounded principled
   but the design then assigned different subsets to each emission path
   based on operational constraints, not stability semantics. The model
   obscured rather than clarified what was actually happening.

6. **Reviewer ≠ adversary.** Project-reviewer found real structural bugs
   but accepted the framing of the problem. Inquisitor questioned the
   framing itself. Both passes were valuable; only the adversarial pass
   would have surfaced the data finding because it was the only one
   asking "is the problem actually shaped the way the spec assumes?"

## What the second attempt should probably do

(Not prescriptive — the second brainstorm should derive its own answers.)

- Start from the data: 91% of drift events come from PreToolUse-emitted
  `bypass`/`skill_mediated`. Whatever architecture ships v1, that's the
  load-bearing path.
- Whatever `raw_input` means for those events, it must come from
  `conversation_history` (the prior user turn) — NOT the Agent tool call's
  `prompt` parameter.
- `matcher_decision` persistence may not be needed at all in v1, because
  the matcher didn't run for the events that matter. If it's added, the
  motivation should be its own analysis use case, not as scaffolding for
  drift-event enrichment.
- Volume math against real telemetry (`wc -l ~/.claude/state/dispatch-log.jsonl`,
  `wc -l ~/.claude/state/router-drift.jsonl`) before any rotation
  decision.
- If the contract must span `agents/general-purpose.md`, the plan must
  include enforcement (install-time injection, format validation, or
  schema-pinned tests) — not "we'll edit the file too."

## Provenance

| Item                              | Reference                                                                                  |
| --------------------------------- | ------------------------------------------------------------------------------------------ |
| Tracking issue                    | https://github.com/glitchwerks/claude-wayfinder/issues/143                                 |
| Abandoned PR (this folder is the PR's terminal state) | https://github.com/glitchwerks/claude-wayfinder/pull/152                       |
| Project-reviewer agent            | `agents/project-reviewer.md` (Sonnet 4.6, run 2026-05-18)                                  |
| Inquisitor agent                  | `agents/inquisitor.md` (Opus, run 2026-05-18)                                              |
| Drift event count source          | `~/.claude/state/router-drift.jsonl` (local, snapshot 2026-05-18)                          |
| Dispatch volume source            | `~/.claude/state/dispatch-log.jsonl` (local, 14,805 events over 23 days as of 2026-05-18)  |
