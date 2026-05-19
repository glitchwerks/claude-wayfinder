# Inquisitor findings — spec v2

Run: 2026-05-18, against `docs/superpowers/specs/2026-05-18-telemetry-enrichment-design.md`
at commit `8938e5c` (spec v2, after project-reviewer findings were addressed).

The 4 BLOCKING findings below triggered the pivot. See `POSTMORTEM.md` for the
narrative; see `spec-final.md` for the spec under critique.

---

**CHARGE 1 (BLOCKING)**: The Stop-hook enrichment branch is built for an event class that does not occur in production

**The problem**: `~/.claude/state/router-drift.jsonl` contains 1068 events: 550 `skill_mediated`, 491 `bypass`, 27 malformed. **Zero `advisory_override`.** Yet the spec's architectural centerpiece — `matcher_decision` persistence, `decision_id` cross-reference, audit-line format change, Stop-hook join logic, version-skew degradation, version-skew acceptance test (AC#10), audit-line parser test (AC#9), and the entire "three-field model" diagram — exists to enrich the `advisory_override` path. The two categories that produce 100% of real signal get one field (`raw_input`) and a hand-wave: "features derivable on demand via offline pass." No consumer, no key, no plan.

**Why it matters**: The spec is shaped by the elegant case, not the load-bearing one. After implementation, the analytical question stated in §Motivation — "did a matcher change reduce drift for feature combination X?" — remains unanswerable for 96%+ of drift events. The complexity (new file, new id format, three-site format contract, scanner join, version skew handling) lands without paying for itself.

**The question that must be answered**: Given live data shows `advisory_override` at 0/1068, why is the design optimizing for it instead of the bypass/skill_mediated path that produces every actual drift event? If the answer is "advisory_override will become common once #145 lands," cite that and quantify the projection. If the answer is "we expect this to be rare but high-signal," then say so explicitly and accept that v1 ships with the 96% path unanalyzable.

---

**CHARGE 2 (BLOCKING)**: PreToolUse `raw_input` reconstruction captures the wrong artifact

**The problem**: The spec (§2b table) says `task_description` comes from "the Agent tool call's `prompt` parameter (the brief the router wrote)." This is not the matcher's `raw_input`. The matcher's `raw_input` is the **user prompt that the router consumed**, which is what the matcher scored to recommend a category in the first place. The brief the router wrote to dispatch the Agent is a downstream artifact — typically several hundred words of structured instruction, in the router's voice, after the routing decision was already made. Putting it in a field named `raw_input` and feeding it to `build_features()` produces feature distributions describing **the router's writing style**, not user intent.

**Why it matters**: Cross-version drift attribution by feature combination (the spec's headline goal #3) is meaningless if the features describe the brief rather than the prompt. Every `bypass`/`skill_mediated` row — i.e., 100% of production drift telemetry — gets contaminated input. AC#5's round-trip identity test passes trivially because `build_features()` is deterministic; it does not detect that the wrong substrate is being fed in.

**The question that must be answered**: Where in the PreToolUse hook input is the original user prompt the router responded to? Read `hooks/check-agent-dispatch-pairing.js:293` — the hook has `conversation_history` and the current tool call's `input`. The user prompt that triggered the routing turn is a `user`-role turn N steps back in history. Is the spec's intent to extract that turn, and if so, which turn, with what disambiguation rule when the router did multiple dispatches per turn? If the intent is to capture the brief, rename the field — `dispatch_brief` is not `raw_input` and must not feed the same extractor.

---

**CHARGE 3 (BLOCKING)**: The audit-line / `decision_id` contract has three producers, no single owner, and the proposed owner is outside the plugin's release boundary

**The problem**: `decision_id` must be produced identically at three sites: (1) Python matcher writing `matcher-decisions.jsonl`, (2) router emitting it in its audit text, (3) Stop hook parsing it back. Site (2) is `agents/general-purpose.md` — which the spec's `touches:` header lists. **That file does not exist in this repository.** It lives in `~/.claude/agents/general-purpose.md` (user-scope, outside `claude-wayfinder`). The plugin cannot ship a change to a file it doesn't own. The router's audit-line format is set by user-authored prose with no schema enforcement, no test coverage, and no version coupling to `feature_schema_version` or `matcher_version`.

**Why it matters**: When the user edits their own router agent (renames a field, drops the bracket, changes "id:" to "decision:") the scanner silently produces no joins — `parseAuditLine` (`router-drift-scanner.js:44`) still matches the existing regex and returns a parsed event without the id, so `advisory_override` rows get emitted with no `decision_id`, and analysis silently degrades. There is no failure mode. AC#9 tests "a representative transcript fixture" — i.e., a fixture authored by the same person writing the spec. The router-side contract has no contract.

**The question that must be answered**: How does this plugin enforce a format on a file outside its own tree? Options: (a) install-time injection into user's router agent (high-risk; rejected by precedent), (b) have the matcher emit the audit line itself rather than relying on the router to template it (changes the architecture but eliminates one of three sites), (c) reconstruct `decision_id` deterministically at the scanner from `session_id + ts + canonical_json(raw_input)` rather than transmitting via audit text (eliminates site (2) entirely — but then why is it in the audit line at all?). Pick one. The current design assumes a contract holds across a trust boundary that doesn't exist.

---

**CHARGE 4 (BLOCKING)**: Storage growth math is off by 12-60x; rotation-deferred decision is invalid

**The problem**: Spec §1 Volume claims "~50 dispatches/day." Actual count from `~/.claude/state/dispatch-log.jsonl`: 14,805 events across 23 days. **Median 475/day, mean 644/day, max 3039/day.** At median, projected `matcher-decisions.jsonl` growth is ~950 KB/day, **~350 MB/year**. At max-day rate sustained, >2 GB/year. The spec uses the fabricated 50/day number to justify (a) no rotation, (b) linear scan of the file for every `advisory_override` join, (c) "~30s scan time per year-long file" assertion.

**Why it matters**: §2d frames linear-scan join cost as a non-issue because the file is small. At real volume, the year-end scan is minutes, not seconds. Worse, §6 Non-goals defers rotation indefinitely while §1 implementation writes `unconditionally (no selective emission — selective sampling would bias aggregate analysis)`. The two together guarantee that a year out, every Stop-hook run linearly scans hundreds of MB of JSON looking for one id, on the synchronous session-end path.

**The question that must be answered**: Re-do the volume math against `wc -l ~/.claude/state/dispatch-log.jsonl` and re-decide whether the file needs (a) rotation in v1, (b) the index in v1 (currently labeled "rotation prerequisite"), or (c) some emission filter (the `bypass`/`skill_mediated` paths don't need persistence at all — they're handled inline by the PreToolUse hook). The "no rotation, linear scan" position is defensible only at the fabricated volume; at the real volume it's a future incident.

---

**CHARGE 5 (CONCERN)**: The three-field model is sound until you read which paths get which fields

**The problem**: §"The three-field model" is structured as a stability-laddered contract: `raw_input` (highest) / `features` (semi-stable) / `score_components` (volatile). The diagram then shows: `matcher_decision` gets all three; `advisory_override` gets two + decision_id pointer; `bypass`/`skill_mediated` gets one. So the "model" is in fact: matcher rows get the model, advisory rows get most of it via cross-ref, and the dominant production case gets `raw_input` only with no plan for the rest. This isn't a model — it's a description of three different choices justified by a shared diagram.

**Why it matters**: The model exists to make readers feel the design is principled. The actual emission decisions are driven by Python-spawn cost in PreToolUse (legitimate) and join feasibility in Stop (legitimate) — neither has anything to do with stability laddering. The stability vocabulary obscures rather than clarifies what's actually going on. A reader who internalizes "we have a layered stability contract" will mis-predict what queries are answerable.

**The question that must be answered**: Drop the three-field model framing, or commit to it. If you keep it, explain how `bypass`/`skill_mediated` events — which carry only `raw_input` — get joined back into stability-laddered analysis when `feature_schema_version` is on the matcher row they never produced. The spec says "derivable on demand" but: derived where, written where, keyed by what, validated against which catalog snapshot? §3 explicitly says the batch CLI "does NOT spawn from any hot path" and that scanner-time enrichment is "scope of a follow-up issue, not this one" — so the answer is "nowhere, by design." Then say so up front.

---

**CHARGE 6 (CONCERN)**: AC#5 round-trip identity proves nothing about cross-version stability

**The problem**: AC#5: "feeding the `raw_input` from a `matcher_decision` row through the batch CLI produces a `features` object equal to the one in that same row." Within a single matcher version this is `build_features(x) == build_features(x)` — true by determinism, not by design quality. The acceptance criterion does not exercise the actual contract (`feature_schema_version` is meaningful), it exercises function purity (already true).

**Why it matters**: The CI test gives false confidence in the cross-version replay story. The real risk is `feature_schema_version=1` rows persisting past a v2 deploy and producing silently-wrong joins. Nothing in §AC verifies the v1-row + v2-extractor case fails loudly. AC#10 covers the scanner's degrade path, but not the batch CLI's — `features --batch` will happily produce v2 output from v1 input with no version annotation on stdin and no rejection.

**The question that must be answered**: What test exercises that `build_features()` at HEAD cannot be silently used to "refresh" features on a `matcher_decision` row written under a prior `feature_schema_version`? At minimum the batch CLI input should accept (or require) an expected-`feature_schema_version` and fail when the running extractor doesn't match.

---

**CHARGE 7 (CONCERN)**: Audit-line regex change is under-specified

**The problem**: Current regex `/🎯 Dispatch → ([\w_]+)(?:\s+\[([^\]]+)\])?/` (`router-drift-scanner.js:44`) is anchored at the agent bracket. The spec example adds `, id: <decision_id>` inside the existing `(confidence: ...)` parens. Two future authors will not pick the same separator: comma-in-parens vs. trailing `[id: ...]` bracket vs. tab-separated suffix vs. new line. Spec gives one example string, no grammar, no failing-case enumeration. The §AC#9 "audit-line parser test" tests the one shape the author wrote.

**Why it matters**: The regex needs to extract a UUID-shaped token (containing `-` and `:`) from inside the existing line without breaking the existing field captures. `[\w_]+` doesn't match colons or hyphens; the new id format `9337dfe9-2026-05-18T22:15:30.123456Z-a8f3c1d2` contains both. The proposed parser change is non-trivial and the spec doesn't show the new regex, only the new line.

**The question that must be answered**: What is the new regex, in the spec, with a comment naming each captured group? What characters are forbidden in `decision_id`? If the answer requires changing `[\w_]+` because the new id pollutes the decision-name capture group, that's a coupled change that needs to be specified, not discovered at implementation.

---

**CHARGE 8 (CONCERN)**: No kill switch

**The problem**: Spec §1 specifies `matcher_decision` emission is unconditional, follows `_write_log_entry()` pattern (no fsync, append). What happens when the file becomes unwriteable (disk full, perms wrong, path symlinked to nothing, antivirus quarantines `state/`)? `match.py:345` is the existing pattern — let me note: it does swallow IOError silently in the existing usage, which is the right call for telemetry but means failure is invisible. The spec doesn't address detection, alerting, or recovery. The PreToolUse hook's `appendDriftEvent` already swallows errors (`check-agent-dispatch-pairing.js:234`).

**Why it matters**: Silent telemetry loss is the worst failure mode — you analyze a dataset that is incomplete in unknown ways. A `matcher_decision` write failing means the corresponding advisory_override drift events (when they eventually occur) will silently fail to join — looking exactly like the audit-line bug or the version-skew bug.

**The question that must be answered**: What instrument fires when `matcher-decisions.jsonl` writes fail repeatedly? The minimum bar: count failed writes in a session-scoped counter, surface it in the existing SessionStart catalog-health hook, and stop pretending telemetry is a fire-and-forget pipe.

---

**CHARGE 9 (CONCERN)**: §7 Open Questions hides three load-bearing decisions behind "follow-up"

**The problem**: §7 declares "None blocking" and then lists:
1. Rotation policy + index — load-bearing per CHARGE 4
2. PII / redaction — `raw_input.task_description` is user-typed prose; the spec stores it verbatim in `~/.claude/state/` forever. The non-goals section says "for now, the file is local-only … and not transmitted." That stops being true the moment a user attaches a state file to an issue, a support request, or a router-health report.
3. Cross-session correlation — `decision_id` collision risk is "negligible single-machine, out of scope cross-machine." It's also out of scope for any analysis that aggregates across machines, which is the only setting where #143's motivation (cross-release drift) holds.

**Why it matters**: Calling these "open questions" while saying "none blocking" is having it both ways. They're either resolved (state the resolution) or they're open (then they're blocking on something — name it).

**The question that must be answered**: For PII specifically — is there a single contract in the spec asserting that telemetry is local-only and may contain unredacted user prompts, and that any tooling that uploads it must redact first? Without that, the first time someone runs `claude diagnostics` or pipes state to a bug report, they ship every prompt they've typed for the past N months.

---

**CHARGE 10 (NIT)**: §AC#7 says feature_schema_version=1 today and #135 "will bump it to 2 as a follow-up PR" — but the bump policy in §4 requires CI to assert any `build_features()` change comes with a version bump. #135 is a related work item whose extractor change is the trigger. Either the v1 ships with the CI rule active (and #135 must bump as part of merging), or the rule is theatre. Spec doesn't say which.

---

## Gaps in the Explore map (inquisitor's note to future explore briefs)

The recon was good but missed one thing that turned out to be decisive: **the actual distribution of drift event categories in `~/.claude/state/router-drift.jsonl` (advisory_override = 0/1068)**. The map mentioned hook surfaces and emission paths but did not check which categories actually fire. That single fact reframes the entire design (CHARGE 1). Future explore briefs for telemetry-shaped specs should include a row-count breakdown of the targeted telemetry files.

The recon also flagged "does the PreToolUse hook have raw_input" as a question — the answer turned out to be richer than "no, it has the brief": the hook does have `conversation_history` containing the user prompt at some prior turn (CHARGE 2). The map asked the right question but didn't dig.

---

## Verdict

**Do not ship this spec as drafted.** Four blocking issues: (1) the design is optimized for an event class with zero production occurrences while the dominant 96% path gets a hand-wave; (2) `raw_input` for `bypass`/`skill_mediated` is the router's brief, not the user's prompt, contaminating the headline analytical use case; (3) the `decision_id` contract spans a file the plugin doesn't ship and has no enforcement mechanism; (4) the volume math is off by more than an order of magnitude, invalidating the no-rotation decision. The three-field model framing is intellectual scaffolding that the actual emission decisions then dismantle, and the acceptance criteria test determinism and happy paths rather than the cross-version contract the spec claims to provide. The right next move is to put the spec back on the bench, decide whether the goal is "make advisory_override analyzable" (in which case acknowledge it's a future-tense investment and trim everything justified by current motivation) or "make bypass/skill_mediated analyzable" (in which case the design changes substantially — the `matcher_decision` file may not be needed at all, since the matcher didn't run for those events).

Load-bearing references:
- `I:/other/claude-wayfinder/.worktrees/spec-143-telemetry-enrichment/docs/superpowers/specs/2026-05-18-telemetry-enrichment-design.md`
- `I:/other/claude-wayfinder/hooks/check-agent-dispatch-pairing.js` (lines 230-345 — PreToolUse emission surface)
- `I:/other/claude-wayfinder/hooks/lib/router-drift-scanner.js` (lines 30-50 — audit-line regex)
- `I:/other/claude-wayfinder/src/claude_wayfinder/match.py` (lines 468-510 — `build_features`)
- `C:/Users/chris/.claude/agents/general-purpose.md` (lines 30-56 — audit-line format spec, *user-scope, outside plugin*)
- `C:/Users/chris/.claude/state/dispatch-log.jsonl` (14,805 events, median 475/day — real volume)
- `C:/Users/chris/.claude/state/router-drift.jsonl` (1068 events: 550 skill_mediated, 491 bypass, 0 advisory_override)
