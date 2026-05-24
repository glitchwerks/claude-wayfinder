# Dispatch Discipline — Routing-Shape Rules

This document describes four rules that govern the *shape* of dispatch calls in a router that uses `claude-wayfinder`. The matcher produces decisions; these rules govern how the router consumes those decisions and how it delegates to sub-agents. `claude-wayfinder` does not enforce these rules — the matcher is a decision producer, not a policy enforcer. A consumer's router agent definition and its hooks tree are the appropriate home for enforcement. This document is for power-users who are authoring a router agent against the wayfinder matcher and want to understand what discipline the matcher implicitly assumes.

---

## Why these rules exist

Without these rules, a dispatch loop can route correctly — choosing the right agent and passing the right skills — and still produce wasteful or degenerate behavior: an agent that spawns a copy of itself, a nested dispatch chain that grows without bound, a skill recommendation that never reaches the sub-agent, or a second Agent call issued against a routing decision that was already consumed. Each rule below prevents one of these failure modes. They are not academic constraints; each one emerged from patterns observed in reference router implementations.

---

## The four rules

### 1. Self-dispatch prohibited

**Rule:** A router agent must not dispatch to its own type. If the router is implemented as an agent named `general-purpose` (or any name), it must not issue `Agent({subagent_type: "general-purpose"})` — or whatever its own type name is.

**Failure mode without this rule:** The router spawns a context-isolated copy of itself. The copy receives the same task and no additional specialist capability, so it will likely produce the same routing decision again, or attempt to handle the task directly with no specialist forcing function. Every call in this pattern wastes a tool-call hop and a fresh context window without gaining anything. The forcing function — "pick a real specialist or handle it yourself" — collapses because the router can always retreat to a generic worker instead of committing.

**Reference-implementation pointer:** A PreToolUse hook on the `Agent` tool that reads `subagent_type` from the tool input and denies the call when it matches the router's own type. The hook can hard-code the router's agent name, or derive it from a configurable env var. One example is `check-no-self-dispatch.js` in the `glitchwerks/claude-configs` repo. The matcher itself excludes the router agent from the scored-agents pool (any entry with `routable: false` in the catalog), so the deterministic route never produces this pattern — but the LLM-judgment path, which bypasses the matcher, can. The hook catches that path.

---

### 2. Opus-native nested dispatch carve-out

**Rule:** Certain high-cost sub-agents may dispatch a narrow set of read-only sub-agents directly — without routing through the router — but only to specific, depth-bounded targets.

For example, a router might allow `strategic-planner` and `adversarial-reviewer` (Opus-tier) to dispatch read-only `explorer` and `ops-query` agents directly. The allowlist is consumer-defined; substitute your topology's names. Allowed targets should be read-only agents whose own `tools:` lists exclude `Agent`, so the dispatch tree is depth-bounded at 2 by typing. Any other target — including the router itself or write-capable agents — is denied.

**Failure mode without this rule:** Opus-tier agents perform expensive synthesis and critique. Without the carve-out, every codebase exploration call during a planning or review session must route through the full dispatch loop, burning router tokens for a decision that is obvious (read-only exploration). The cost mounts quickly for tasks that require many exploration calls. The carve-out converts in-context Opus tokens to lighter-model tokens by routing codebase reads to `Explore` (cheaper model, isolated context, summary-only return). Without a hard allowlist, this carve-out can expand: callers begin dispatching `code-writer`, `debugger`, or other agents directly, which re-introduces the audit-fracture and recursion problems that centralized routing was designed to prevent.

**Reference-implementation pointer:** A PreToolUse hook on the `Agent` tool that detects when the active session is an Opus-native caller (by reading the `{"type":"agent-setting","agentSetting":"<name>"}` line at the top of the session JSONL transcript) and denies any `subagent_type` not in the allowed set. Fail-open on transcript read errors so a hook bug never blocks legitimate dispatch. One example is `check-opus-native-allowlist.js` in the `glitchwerks/claude-configs` repo. Note that skill propagation does not cross this boundary — when an Opus-native agent dispatches its allowed read-only targets directly, the wayfinder matcher does not fire, and no skills are injected. Any context the sub-agent needs must be stated inline by the caller. This is intentional; it matches the read-only, domain-agnostic shape of the allowed targets.

---

### 3. Skill propagation

**Rule:** When the matcher returns a `delegate` or `advisory` decision with a non-empty `skills` array, the router must propagate those skill names into the sub-agent's prompt as an explicit instruction block.

The block should instruct the sub-agent to invoke each named skill via the Skill tool at the appropriate phase — for example, `superpowers:test-driven-development` before writing implementation code, `python` when authoring Python files. The matcher resolved these skills from the catalog; they are part of the routing decision, not optional flavor. If `skills` is empty (`[]`), the router must not emit the block.

**Failure mode without this rule:** The matcher's catalog encodes which skills a sub-agent should activate for a given task — this is part of the routing decision. If the router discards the `skills` field, the sub-agent receives no instruction to invoke those skills and will not activate them. The drift telemetry captures this as `skill_mediated_delegation` events when a skill fires correctly, but the failure mode is the inverse: no skill fires when one should have, and the session produces lower-quality output without any observable error signal. The failure is silent.

**Reference-implementation pointer:** The router agent's prompt should include a branch in its dispatch-decision handler: after composing the `Agent` call, if `skills` is non-empty, append a paragraph to the sub-agent's prompt instructing it to invoke each skill via the Skill tool. The exact wording is consumer-defined; one approach is a plain-text block appended to the sub-agent brief. A reference-implementation PostToolUse hook (e.g. `inject-subagent-preamble.js` in `glitchwerks/claude-configs`) can verify the block is present, but the primary enforcement is in the router agent's prompt logic, not the hook.

---

### 4. One dispatch authorizes one Agent call

**Rule:** A single dispatch invocation authorizes one Agent tool call. Back-to-back delegations require back-to-back dispatches — one dispatch per Agent call. The matcher's output is single-use.

**Failure mode without this rule:** A router that caches a dispatch result and issues multiple Agent calls against it produces two distinct problems. First, the second and subsequent Agent calls are effectively un-audited: the audit line records one dispatch but multiple delegations occur, making the session transcript misleading. Second, the router can delegate two tasks simultaneously — an implementation task and an unrelated ops query, for example — when the matcher only authorized one of them. The "feels routine" intuition is the failure mode this rule exists to prevent: reads to `ops` are the most-bypassed class, and "obviously ops" is exactly the rationalization that produces bypass events in the drift log.

**Reference-implementation pointer:** A PreToolUse hook on the `Agent` tool that tracks whether a dispatch event (a Skill invocation of `/dispatch`) occurred in the same turn as the Agent call. If an Agent call fires without a preceding dispatch in the current turn, it is classified as `bypass` and logged as a drift event. The source harness implements this as part of `check-agent-dispatch-pairing.js` (a Tier 1 hook that already ships with wayfinder — see `docs/integration.md` § Bundled hooks). Note that this is the one rule in this document where partial enforcement is already provided by a shipped wayfinder hook. The hook classifies and logs bypass events; it does not block them. Blocking is the consumer's policy decision.

---

## Relationship to the matcher

The matcher produces decisions. It does not enforce how those decisions are consumed, and it has no visibility into what the router does after a decision is returned. The matcher cannot detect self-dispatch (it has already returned its result before the `Agent` tool fires), cannot verify that skills were propagated (it does not see the sub-agent's prompt), and cannot confirm that only one Agent call was issued per dispatch.

These rules describe *post-decision behavior* — what the router does with the matcher's output. The matcher's job ends when it writes the decision JSON to stdout. The router's job is to consume that decision correctly.

For the design rationale on why the matcher is post-cognitive and does not intercept raw prompts, see [`docs/design.md` § "Why post-cognitive"](design.md#why-post-cognitive).

---

## Why wayfinder does not ship enforcement hooks for these rules

Three reasons, each load-bearing:

**Scope discipline.** Wayfinder's stated scope is the matcher and drift telemetry (Tier 1 hooks, issue #53). Enforcement hooks that govern *which* agents are allowed to dispatch *which other* agents are policy — they belong to the consumer's router definition, not to the plugin that produces decisions. Shipping policy enforcement inside a decision-production tool conflates two distinct responsibilities.

**Consumer router topology varies.** Rules 1 and 2 are directly dependent on agent names: rule 1 requires knowing the router's own type; rule 2 requires knowing which callers qualify as Opus-native and which targets are read-only. These names differ across consumers. A consumer whose router is named `router` and whose Opus-tier planner is named `strategic-planner` needs different values than the source harness. Shipping hardcoded hooks with the wayfinder plugin would either force consumers to adopt the source harness's naming conventions or require a config schema that is non-trivial to design and maintain — a scope expansion that is not justified by the matcher's value proposition.

**Reference implementations are sufficient.** The four rules are documented here. A consumer who reads this document has enough information to implement enforcement hooks for their specific topology. The source harness implementations (`check-no-self-dispatch.js`, `check-opus-native-allowlist.js`, `inject-subagent-preamble.js`) are reference implementations — they encode the pattern without being the canonical deliverable for every consumer.

This decision is recorded as issue #54 (Option C — Document the boundary, do not ship).
