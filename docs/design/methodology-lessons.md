# Methodology lessons

This file is a permanent home for cross-cutting lessons surfaced by postmortems
in this repo. When a postmortem is being deleted per the plan-file lifecycle rule
(parent issue closed → extract durable content → delete source), its
project-scoped design lessons land here first. Each lesson cites its originating
postmortem and the parent issue/PR so the provenance trail survives the source
deletion. Lessons are project-scoped to claude-wayfinder's design choices
(matcher architecture, hook contracts, review-cycle workflow) rather than
general engineering wisdom; that scope makes the repo's own design corpus the
right permanent home. Future postmortem extractions append additional
`## Lesson N` sections.

---

## Lesson 1 — Read the data first

Before designing telemetry enrichment, count the events. The numbers should
drive the architecture, not impressions of which categories matter.

In the telemetry-enrichment v1 design, the architectural centerpiece —
`matcher_decision` persistence, `decision_id` cross-reference, audit-line
format change, Stop-hook join logic, version-skew degradation — was built to
enrich the `advisory_override` drift path. That path produced zero events in
the entire collected dataset (0 of 1068 events). The two categories that
produce 100% of real drift signal (`bypass` at 46%, `skill_mediated` at 51.5%)
got `raw_input` only, with no plan for `features` derivation.

The failure mode was a silent assumption: all three drift categories are roughly
equivalent in volume, so enriching them with one strategy is reasonable. That
assumption was never checked against `~/.claude/state/router-drift.jsonl`
before the design was committed to.

**Origin:** Telemetry enrichment pivot postmortem (#143 / abandoned PR #152),
2026-05-18. Lessons section. See also `docs/design/methodology-lessons.md`
(this file) and the successor spec at
`docs/superpowers/specs/2026-05-19-telemetry-bypass-taxonomy-design.md`.

---

## Lesson 2 — `PreToolUse raw_input` source matters

The PreToolUse hook (`check-agent-dispatch-pairing.js`) runs before the
matcher. The matcher's `raw_input` — the user prompt the router responded to —
lives in `conversation_history` at a turn N steps back, not in the Agent tool
call's `prompt` parameter, which is the router's dispatch brief.

Capturing the wrong field (`prompt` instead of `conversation_history`) produces
feature distributions describing the router's writing style rather than user
intent. In the v1 design, `raw_input` for `bypass`/`skill_mediated` events
would have captured the router's dispatch brief, contaminating 100% of
production-relevant telemetry with wrong-substrate features.

The distinction between "what the router sent the sub-agent" and "what the user
originally asked" is easy to conflate in hook code because both are strings that
look like natural language. The hook's vantage point (before the sub-agent
runs, seeing only the Agent tool call parameters) makes the confusion
structurally likely unless the data-source question is answered explicitly at
design time.

**Origin:** Telemetry enrichment pivot postmortem (#143 / abandoned PR #152),
2026-05-18. Lessons section.

---

## Lesson 3 — Cross-process contracts spanning trust boundaries are fragile

When a plugin proposes a contract that spans files the plugin does not ship,
the plan needs an enforcement mechanism — not goodwill.

The v1 `decision_id` design required three sites to agree on a string format:
the Python matcher, the router agent prose in `agents/general-purpose.md`, and
the Node scanner. `agents/general-purpose.md` lives in the user's `~/.claude/`
tree — outside the plugin's release boundary, with no test coverage and no
schema enforcement. There is no mechanism that prevents the file from drifting
out of sync with the plugin across upgrades.

The failure mode is silent: the three-site contract looks sound on paper
because all three sites are named in the spec. The gap is that naming a site is
not the same as controlling it. When plugin release boundaries and user-scope
files are mixed in the same contract, the plan must include concrete enforcement
(install-time injection, format validation, schema-pinned tests) or the contract
is goodwill at best.

**Origin:** Telemetry enrichment pivot postmortem (#143 / abandoned PR #152),
2026-05-18. Lessons section.

---

## Lesson 4 — Matcher architecture asymmetry

The matcher (`dispatch_to_agent.py`) does not run for `bypass` or
`skill_mediated` events — architecturally, those events are emitted by the
PreToolUse hook precisely because a dispatch did not happen. That means
`matcher_decision` rows have no peer for those events.

This is an asymmetry in the system's architecture, not a bug: `bypass` and
`skill_mediated` events exist because the router dispatched an agent without
going through the matcher, so the matcher never had the opportunity to produce
a decision row. Designs that hinge on cross-referencing `matcher_decision` rows
are structurally inapplicable to 96% of the drift dataset (the
`bypass`/`skill_mediated` share), not merely under-tested.

The practical implication: any enrichment design for `bypass`/`skill_mediated`
events must derive features from what the PreToolUse hook can observe directly —
the tool-call shape and `conversation_history` — rather than from a
`matcher_decision` join.

**Origin:** Telemetry enrichment pivot postmortem (#143 / abandoned PR #152),
2026-05-18. Lessons section.

---

## Lesson 5 — Three-field model framing was scaffolding, not load-bearing

"raw_input / features / score_components with distinct stability contracts"
sounded principled as a framing device but collapsed under operational
constraints. The v1 design then assigned different subsets of those three fields
to each emission path based on what was technically feasible at each site, not
based on the stability semantics the model implied. The model obscured rather
than clarified what was actually happening.

The lesson is not that named stability contracts are always wrong. It is that a
framing model earns its place only when it does consistent explanatory work
across every case it claims to cover. When the model requires a different story
for each emission path — "we assign `raw_input` here but not `features` because
the matcher didn't run" — it is scaffolding that was used to build the design
and should be removed from the spec before it ships, not preserved as an
organizing principle.

**Origin:** Telemetry enrichment pivot postmortem (#143 / abandoned PR #152),
2026-05-18. Lessons section.

---

## Lesson 6 — Reviewer is not adversary; both review modes are valuable

Project-reviewer and inquisitor serve different functions. Project-reviewer
found two real structural bugs (the two-emitter problem and the
catalog-coupled features schema). Inquisitor questioned the framing of the
problem itself — asked "is the problem actually shaped the way the spec
assumes?" — and counted the actual events.

Both passes were valuable. Only the adversarial pass surfaced the data finding
(the 0/1068 `advisory_override` event count) because it was the only pass
asking whether the premise held. A project-reviewer that accepts the framing and
works within it will find bugs in the design but will not falsify the premise.

The practical implication: for specs that depend on empirical claims about
production data (event counts, file sizes, volume rates), an adversarial review
pass should verify those claims directly, not just accept them from the spec's
author. Framing assumptions that go unquestioned in the first review pass can
survive multiple iterations and generate significant design work before they are
examined.

**Origin:** Telemetry enrichment pivot postmortem (#143 / abandoned PR #152),
2026-05-18. Lessons section.
