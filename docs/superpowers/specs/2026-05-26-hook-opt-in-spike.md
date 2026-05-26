---
title: Hook opt-in mechanism spike (issue #57)
status: closed — premise invalidated
touches:
  - hooks/log-agent-dispatch.js
  - hooks/check-agent-dispatch-pairing.js
  - hooks/router-drift-scanner.js
  - src/claude_wayfinder/_health/_metrics.py
  - src/claude_wayfinder/_health/_report.py
  - docs/superpowers/specs/2026-05-26-hook-opt-in-spike.md
skills_relevant:
  - claude-code-plugin-authoring
  - hook-authoring
---

# Hook opt-in mechanism spike — design spec for issue #57

**Status:** Closed — premise invalidated. See § 2 (Decision).
**Issue:** [#57](https://github.com/glitchwerks/claude-wayfinder/issues/57) — "Spike: make agent-dispatch logging hook opt-in (off by default)"
**Author:** project-planner (delegated by router); revised by doc-writer sub-agent 2026-05-26
**Date:** 2026-05-26

---

**Recommendation: do not implement.** The spike's premise — that `log-agent-dispatch.js` is pure observability — is false; all three hooks under examination produce data consumed by `/router-health` (a shipped, functional tool). Making any of them opt-in would degrade `/router-health` for users who don't opt in. Closing #57 with no implementation.

---

## 1. Background and goals

Issue #57 asks: make `hooks/log-agent-dispatch.js` opt-in (off by default), pick a mechanism that fits how Claude Code plugins are supposed to be configured, and audit the two telemetry-adjacent hooks (`check-agent-dispatch-pairing.js`, `router-drift-scanner.js`) for the same treatment.

Acceptance criteria for the spike (verbatim from the issue):
- Design doc / ADR with mechanism survey + chosen approach + rationale
- Each of the three hooks categorized as core / observability-only / mixed
- README update deferred to follow-up issue
- Follow-up implementation issue composed

This spec satisfies the design-doc criterion. The follow-up implementation issue is not filed — investigation shows no implementation is indicated (see § 2).

---

## 2. Decision

**Rule applied:** "If there is any documented degradation we won't enable the option."

All three hooks feed `/router-health`, a shipped functional tool. Making any of them opt-in defaults-off degrades `/router-health` for users who don't opt in.

| Hook | Logs to | Consumed by `/router-health`? | Decision |
|---|---|---|---|
| `hooks/log-agent-dispatch.js` | `~/.claude/state/dispatch-log.jsonl` | Yes — harness-version detection (`_health/_report.py:most_recent_harness_version`) + dispatch-volume denominator (`_health/_metrics.py:compute_metrics`) | **Keep default-on. Do NOT add opt-in.** |
| `hooks/check-agent-dispatch-pairing.js` | `~/.claude/state/router-drift.jsonl` (`router_drift` events) | Yes — bypass classification denominator (`_health/_metrics.py:compute_metrics`) | **Keep default-on. Do NOT add opt-in.** |
| `hooks/router-drift-scanner.js` | `~/.claude/state/router-drift.jsonl` (5 drift event types) | Yes — drift metrics (`_health/_metrics.py:compute_metrics`) | **Keep default-on. Do NOT add opt-in.** |

The original framing in #57 ("`log-agent-dispatch.js` is pure observability — useful for debugging routing behavior, but not required for the dispatch system to function") was based on an incomplete understanding of the data dependencies. The spike's own data-dependency analysis (§ 4 below) confirms that both log files are functional input to a shipped tool.

---

## 3. Code-vs-issue-body reconciliation

The issue body claims `check-agent-dispatch-pairing.js` "can enforce dispatch pairing (may block tool calls)". The code says otherwise.

Verified at `hooks/check-agent-dispatch-pairing.js:L19`:

> `// IMPORTANT: This hook NEVER blocks (always exits 0) and NEVER augments tool input.`

Verified at `hooks/check-agent-dispatch-pairing.js:L1-L28` — the header documents four classifications (`router_mediated`, `skill_mediated`, `bypass`, `stale_dispatch`) and explicitly characterises the hook as "PreToolUse floor hook" that writes drift events. Output is JSONL only; no `permissionDecision` is emitted.

**Resolution:** today the hook is pure observability (write-only side effects). The issue body is stale relative to the code (or was written aspirationally — the "floor hook" name implies enforcement potential, but the code never realised it). All three hooks under examination are JSONL-write-only as of v0.11.0.

A separate hook, `check-no-self-dispatch.js`, is referenced in the router agent body — that one *does* enforce. It is unrelated to #57 and out of scope.

---

## 4. Inventory: the three target hooks

Verified via `Read` on each file, 2026-05-26.

| Hook | LOC | Event | Output sink | Blocks? | Source citation |
|---|---|---|---|---|---|
| `hooks/log-agent-dispatch.js` | 103 | `PreToolUse:Agent` | `~/.claude/state/dispatch-log.jsonl` | Never | `hooks/log-agent-dispatch.js:L96-L98` (catch → stderr → exit 0) |
| `hooks/check-agent-dispatch-pairing.js` | 394 | `PreToolUse:Agent` | `~/.claude/state/router-drift.jsonl` | Never | `hooks/check-agent-dispatch-pairing.js:L19` |
| `hooks/router-drift-scanner.js` | 212 | `Stop` | `~/.claude/state/router-drift.jsonl` | Never | `hooks/router-drift-scanner.js:L13-L14` |

**Categorization (revised in light of § 2 data-dependency finding):**

- **`hooks/log-agent-dispatch.js` → mixed (observability + functional).** Appears observability-only at the hook level, but its output (`dispatch-log.jsonl`) is a functional input to `/router-health`. Harness-version detection and the dispatch-volume denominator both depend on it.
- **`hooks/check-agent-dispatch-pairing.js` → mixed (observability + functional).** JSONL-write-only at the hook level; bypass classification in `/router-health` depends on `router-drift.jsonl`. If a future version reintroduces enforcement (via `permissionDecision: "deny"`), it would become fully "core".
- **`hooks/router-drift-scanner.js` → mixed (observability + functional).** Stop hook, idempotent per session, JSONL-write-only; drift metrics in `/router-health` depend on its output.

None of the three are "pure observability" in the sense required by the original #57 framing — all three produce output consumed by a shipped functional tool.

---

## 5. Data dependencies — what `/router-health` needs

Verified via `Grep` on `dispatch-log` and `router-drift` references across the repo. Both telemetry files feed `/router-health`:

- `dispatch-log.jsonl` is consumed by `_health/_metrics.py:compute_metrics` (dispatch-volume denominator) and `_health/_report.py:most_recent_harness_version` (harness-version detection in report header).
- `router-drift.jsonl` is consumed in parallel by `_health/_metrics.py:compute_metrics` (bypass, skill-mediated, and other drift counts).

The module-level docstring of `_health/__init__.py:L18-L21` documents both files as explicit log inputs to the tool.

**Consequence of opting out of any hook:** `/router-health` loses one or more of harness-version detection, dispatch-volume denominator, or drift metrics. For `log-agent-dispatch.js` specifically: drift events still flow but lack the denominator that turns "5 bypass events" into "5 bypass events out of 200 dispatches" — a non-trivial degradation in signal interpretability.

This is documented degradation. Per the rule in § 2, the option is not enabled.

---

## 6. Mechanism survey (retained for future reference)

The mechanism analysis below is preserved because it would apply directly if a *different* hook — one with no functional consumers downstream — were ever considered for opt-in. The `userConfig`-in-`plugin.json` approach would be the right choice in that scenario. It did not apply here because the premise (pure observability) proved false.

Grounded in `claude-code-plugin-authoring` skill (fetched 2026-05-16, anchored to https://code.claude.com/docs/en/plugins-reference). Each row evaluates fit for the "hook opt-in" use case.

| Mechanism | What it is | Fit for hook opt-in | Notes |
|---|---|---|---|
| **`userConfig` block in `plugin.json`** | Documented per [Plugins reference § User configuration](https://code.claude.com/docs/en/plugins-reference#user-configuration) (fetched 2026-05-16). Claude Code prompts at plugin enable, stores values, substitutes `${user_config.<key>}` into hook commands. | **Strong fit.** Boolean type exists; substituted into the command string *before* subprocess launch, so no env-var propagation issue. Discoverable at enable time. | Requires shell-level conditional in the hook command string OR an in-hook env read of the substituted value. See wiring pattern below. |
| **Env vars read inside the hook** | Each hook calls `process.env.X` at startup. The plugin already does this 15+ times. | **Workable but inferior.** No discoverability — users must read the source to find the flag. No enable-time prompt. Default-off requires every user to read the README before they get any telemetry, which kills the data set. | Existing precedent is path/threshold overrides (`DISPATCH_LOG_PATH`, `ROUTER_STALENESS_BOUND`) — feature *tuning*, not feature *gating*. Conflating the two muddies the contract. |
| **Plugin-level `settings.json`** | Only `agent` and `subagentStatusLine` keys are honored per the `claude-code-plugin-authoring` skill § 6 (anchored to plugins-reference). Unknown keys silently ignored. | **Does not apply.** Not a free-form config file. | This is a common misreading — flag it in any future opt-in issue's "rejected alternatives" section so it doesn't get re-litigated. |
| **`${CLAUDE_PLUGIN_DATA}/config.json`** | Plugin writes its own JSON file inside the data dir, reads it on each hook invocation. | **Workable, weak fit.** No enable-time prompt, no schema validation, no UI integration. Reinvents `userConfig` poorly. | The `claude-prospector` v0.4.0 archaeology took this path and the `claude-code-plugin-authoring` skill explicitly flags it as "I didn't read the docs" — see § 5 of that skill. |
| **Hook-internal heuristic (skip if no `state/` dir, etc.)** | Hook detects user-intent signals (file present? feature flag in env?). | **Rejected.** Implicit, opaque, surprising. Users cannot reason about whether the hook is on. | Anti-pattern. |

**Wiring pattern (retained for future reference):** if the mechanism were applied, the hook command in `hooks/hooks.json` would short-circuit via shell-level conditional, per `claude-code-plugin-authoring` § 3 (variable substitution into hook commands) and § 5 (`userConfig`). Example for one hook:

```json
{
  "type": "command",
  "command": "[ \"${user_config.enable_dispatch_log}\" = \"true\" ] && node \"${CLAUDE_PLUGIN_ROOT}/hooks/log-agent-dispatch.js\" || true"
}
```

An open question for any future implementer: whether the `[ ... ] && ... || true` shell-conditional idiom is portable across Windows Git Bash and POSIX shells. If it is not, the fallback is to pass the toggle as a Node CLI flag and gate inside each hook script instead.

---

## 7. Out of scope

Per #57 explicitly:
- Implementation of the opt-in switch (moot — no implementation indicated)
- README updates (moot)
- Changing defaults of `hooks/check-catalog-health.js` or `hooks/refresh-catalog-on-stale.js`

Additionally out of scope for this spike:
- Enforcement behavior in `hooks/check-agent-dispatch-pairing.js` (the "floor hook" name suggests aspirational enforcement; if reintroduced, the opt-in question should be revisited with the new data-dependency map)
- Per-session `userConfig` overrides (no Claude Code mechanism for this today)
- Telemetry export / upload (logs are local-only; if a future feature uploads them, that needs its own opt-in design)

---

## 8. Follow-up: close #57

No implementation issue is filed. The closure comment for #57 follows.

---

**Closure comment for issue #57:**

Investigation complete — closing with no implementation.

**Finding:** the spike's premise was that `log-agent-dispatch.js` is "pure observability — useful for debugging routing behavior, but not required for the dispatch system to function." That premise is false.

All three hooks under examination produce output that is a functional input to `/router-health`:

- `hooks/log-agent-dispatch.js` → `dispatch-log.jsonl` → consumed by `_health/_report.py:most_recent_harness_version` (harness-version detection) and `_health/_metrics.py:compute_metrics` (dispatch-volume denominator for bypass-rate calculation)
- `hooks/check-agent-dispatch-pairing.js` → `router-drift.jsonl` → consumed by `_health/_metrics.py:compute_metrics` (bypass and skill-mediated classification counts)
- `hooks/router-drift-scanner.js` → `router-drift.jsonl` → consumed by `_health/_metrics.py:compute_metrics` (all drift metric types)

The data dependencies were confirmed against the live source: `src/claude_wayfinder/_health/_metrics.py:compute_metrics` and `src/claude_wayfinder/_health/_report.py:most_recent_harness_version`. The module-level docstring at `src/claude_wayfinder/_health/__init__.py:L18-L21` explicitly lists both log files as inputs.

**Rule applied:** "If there is any documented degradation we won't enable the option." All three hooks produce documented degradation in a shipped tool when disabled. The opt-in mechanism would be technically correct (`userConfig`-in-`plugin.json` is the right surface), but applying it to these hooks would silently degrade `/router-health` for any user who doesn't opt in — which is the default state.

**Durable evidence:** `docs/superpowers/specs/2026-05-26-hook-opt-in-spike.md` (merged on branch `57-hooks-opt-in-spike`).

Closing as completed — investigated, no implementation indicated.

> 🤖 _Generated by Claude Code on behalf of @cbeaulieu-gt_
