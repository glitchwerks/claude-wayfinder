// Bypass-cause taxonomy for router-drift.jsonl events.
//
// Pure module — no I/O, no module-level mutation. Consumed by
// hooks/check-agent-dispatch-pairing.js. See spec:
//   docs/superpowers/specs/2026-05-19-telemetry-bypass-taxonomy-design.md

const { bareSkillName } = require("./skill-name");

/**
 * Bare sentinel name for the router dispatch skill.
 *
 * Comparisons use bareSkillName() normalization (see hooks/lib/skill-name.js)
 * so both the plugin-namespaced form "claude-wayfinder:dispatch" and the bare
 * form "dispatch" map to this sentinel. The normalization is safe here because
 * the dispatch skill is the only plugin skill that needs to be identified by
 * the bypass-taxonomy logic. See issue #322.
 */
const DISPATCH_SKILL_NAME = "claude-wayfinder:dispatch";

/**
 * Skills the router does NOT delegate — they handle their own user-interactive
 * flow. Agent calls from inside one of these skills are expected bypasses.
 *
 * NOTE: this set has no automatic completeness gate. When a new interactive
 * skill ships, its Agent calls fall to `skill_mediated_other` until this set
 * is updated. The F-3 quarterly audit (issue #161) is the manual backstop.
 */
const INTERACTIVE_SKILLS = new Set([
  "gh-create-issue",
  "project-review",
  "gh-pr-review-address",
  "claude-audit",
  "gh-refresh-issues",
]);

/**
 * Classify a drift event by inspecting only the hook's already-computed
 * `category` field and the tool-call shape. No prompt content is read.
 *
 * Pure function — never throws on valid inputs; never performs I/O.
 *
 * @param {"bypass"|"skill_mediated"|"stale_dispatch"} category
 *        The hook's already-computed category for this event.
 * @param {{subagent_type?: string}} toolCall
 *        The Agent tool-call parameters (subagent_type is the only field read).
 * @param {Array<{toolName: string, skillName?: string}>} toolEvents
 *        Tool events extracted from conversation_history + sidecar. Must use
 *        the SAME window the hook uses: full history back to most recent
 *        dispatch, no user-turn boundary. (Spec §Signal set.)
 * @returns {{
 *   signals: {
 *     subagent_type: string,
 *     dispatch_skill_called_recently: boolean,
 *     count_agent_since_dispatch: number|null,
 *     last_skill_call_name: string|null,
 *     last_skill_call_is_interactive: boolean,
 *     turns_since_user_message: number
 *   },
 *   cause: "skill_mediated_interactive" | "skill_mediated_other"
 *        | "router_direct_after_consumed_dispatch"
 *        | "router_direct_no_dispatch"
 *        | "stale_dispatch" | "unknown"
 * }}
 */
function classify(category, toolCall, toolEvents) {
  const signals = extractSignals(toolCall, toolEvents);
  const cause = deriveCause(category, signals);
  return { signals, cause };
}

function extractSignals(toolCall, toolEvents) {
  const evts = Array.isArray(toolEvents) ? toolEvents : [];

  // Locate the most recent dispatch Skill call — matches the hook's
  // window (full history, no user-turn boundary).
  // bareSkillName() normalizes both "claude-wayfinder:dispatch" and bare
  // "dispatch" to "dispatch" before comparison. See hooks/lib/skill-name.js.
  let lastDispatchIdx = -1;
  for (let i = evts.length - 1; i >= 0; i--) {
    if (evts[i].toolName === "Skill" && bareSkillName(evts[i].skillName) === "dispatch") {
      lastDispatchIdx = i;
      break;
    }
  }

  let countAgentSinceDispatch = null;
  if (lastDispatchIdx !== -1) {
    countAgentSinceDispatch = 0;
    for (let i = lastDispatchIdx + 1; i < evts.length; i++) {
      if (evts[i].toolName === "Agent") {
        countAgentSinceDispatch++;
      }
    }
  }

  // Find the most recent non-dispatch Skill call (for skill_mediated cases).
  // bareSkillName() normalizes for the exclusion check; lastSkillCallName retains
  // the raw (namespaced) value for telemetry accuracy.
  let lastSkillCallName = null;
  for (let i = evts.length - 1; i >= 0; i--) {
    const e = evts[i];
    if (e.toolName === "Skill" && e.skillName && bareSkillName(e.skillName) !== "dispatch") {
      lastSkillCallName = e.skillName;
      break;
    }
  }

  return {
    subagent_type: (toolCall && toolCall.subagent_type) || "",
    dispatch_skill_called_recently: lastDispatchIdx !== -1,
    count_agent_since_dispatch: countAgentSinceDispatch,
    // last_skill_call_name retains the raw (namespaced) value for telemetry accuracy.
    last_skill_call_name: lastSkillCallName,
    // INTERACTIVE_SKILLS stores bare names; normalize lastSkillCallName before
    // the Set.has() lookup so plugin-namespaced forms are correctly resolved.
    // The raw namespaced value is preserved in last_skill_call_name above.
    last_skill_call_is_interactive:
      lastSkillCallName !== null && INTERACTIVE_SKILLS.has(bareSkillName(lastSkillCallName)),
    // turns_since_user_message — not currently computable from the
    // toolEvents shape alone (which lacks turn-role info). Surface as 0
    // for v1; the analyzer does not depend on it. F-2 review can revisit.
    turns_since_user_message: 0,
  };
}

function deriveCause(category, signals) {
  switch (category) {
    case "stale_dispatch":
      return "stale_dispatch";

    case "skill_mediated":
      return signals.last_skill_call_is_interactive
        ? "skill_mediated_interactive"
        : "skill_mediated_other";

    case "bypass":
      // Check dispatch presence FIRST so null count_agent_since_dispatch
      // is never reached by the >=1 comparison. (Spec §Cause enum, pass-2 fix.)
      if (!signals.dispatch_skill_called_recently) {
        return "router_direct_no_dispatch";
      }
      if (signals.count_agent_since_dispatch >= 1) {
        return "router_direct_after_consumed_dispatch";
      }
      return "unknown";

    default:
      return "unknown";
  }
}

module.exports = { classify, DISPATCH_SKILL_NAME, INTERACTIVE_SKILLS, _deriveCauseForTest: deriveCause };
