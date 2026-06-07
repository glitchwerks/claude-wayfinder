// PreToolUse floor hook: detect Agent dispatch that bypasses the dispatch skill.
//
// Behavior (v5 §3.2.1 positional pairing):
//   Scans conversation_history + sidecar to classify:
//
//   router_mediated — most recent dispatch Skill call found, count_Agent = 0,
//                     count_other ≤ STALENESS_BOUND → no event (this is correct routing)
//
//   skill_mediated  — no dispatch Skill in window but most recent un-paired tool
//                     invocation before this Agent is a Skill call (any skill) →
//                     write informational "skill_mediated" event
//
//   bypass          — count_Agent ≥ 1 after last dispatch, OR no dispatch/Skill at all
//                     → write "bypass" drift event
//
//   stale_dispatch  — dispatch found but count_other > STALENESS_BOUND → write
//                     "stale_dispatch" drift event
//
// IMPORTANT: This hook NEVER blocks (always exits 0) and NEVER augments tool input.
//
// Output:
//   Drift events written as JSONL to ROUTER_DRIFT_PATH (env override) or
//   ~/.claude/state/router-drift.jsonl (default).
//
// Configuration:
//   ROUTER_STALENESS_BOUND — override the default staleness threshold (default: 15)
//   ROUTER_DRIFT_PATH      — override the default drift log path
//   SKILL_SIDECAR_PATH     — override the sidecar path (default: ~/.claude/state/recent-skill-invocations.jsonl)

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const parseInput = require("./parse-input");

// Lazy load bareSkillName for dispatch sentinel normalization. Fail-open:
// if the module is unavailable, _bareSkillName falls back to the identity
// function so classifyDispatchRich degrades to bare-name-only matching.
let _bareSkillName = (name) => (typeof name === "string" ? name : "");
try {
  ({ bareSkillName: _bareSkillName } = require("./lib/skill-name"));
} catch (_) {
  // Fallback identity — hook continues without normalization.
}

// Lazy load the bypass-taxonomy module with explicit module-load error
// handling. A require-time throw cannot kill the hook because the require
// runs inside its own try; the fallback `null` is short-circuited at
// use-time (see emit path below). Spec §Hook integration.
let _bypassTaxonomyClassify = null;
try {
  ({ classify: _bypassTaxonomyClassify } = require("./lib/bypass-taxonomy"));
} catch (err) {
  process.stderr.write(
    `[bypass-taxonomy] module load failed; events will emit without enrichment: ${err.message}\n`
  );
  _bypassTaxonomyClassify = null;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/**
 * Number of non-Agent, non-Skill tool invocations allowed between the most
 * recent dispatch Skill call and the current Agent call before the dispatch
 * is considered "stale". Configurable via ROUTER_STALENESS_BOUND env var.
 *
 * @type {number}
 */
const STALENESS_BOUND = (() => {
  const val = Number.parseInt(process.env.ROUTER_STALENESS_BOUND ?? "", 10);
  return Number.isFinite(val) && val >= 0 ? val : 15;
})();

/** Default drift log path — overridable via ROUTER_DRIFT_PATH env var. */
function resolveDriftPath() {
  if (process.env.ROUTER_DRIFT_PATH) return process.env.ROUTER_DRIFT_PATH;
  const home = process.env.HOME || process.env.USERPROFILE || os.homedir();
  return path.join(home, ".claude", "state", "router-drift.jsonl");
}

/**
 * Default sidecar path — overridable via SKILL_SIDECAR_PATH env var.
 * Must match the path used by log-skill-invocation.js.
 */
function resolveSidecarPath() {
  if (process.env.SKILL_SIDECAR_PATH) return process.env.SKILL_SIDECAR_PATH;
  const home = process.env.HOME || process.env.USERPROFILE || os.homedir();
  return path.join(home, ".claude", "state", "recent-skill-invocations.jsonl");
}

/**
 * Read same-turn sidecar entries for the given session.
 *
 * A sidecar entry is "same-turn" when its event_count_at_fire is ≥ the current
 * tool_use count in conversation_history. Prior-turn entries (which are already
 * represented in conversation_history) are filtered out to avoid double-counting.
 *
 * Fail-open: if the file is missing or unreadable, returns [] without throwing.
 *
 * @param {string} sessionId           - Current session ID from the hook payload
 * @param {number} currentHistoryCount - Tool-use block count in conversation_history
 * @param {string} sidecarPath         - Path to sidecar JSONL
 * @returns {Array<{toolName: string, skillName?: string}>} Synthetic tool events
 */
function readSameTurnSidecarEvents(sessionId, currentHistoryCount, sidecarPath) {
  try {
    if (!fs.existsSync(sidecarPath)) return [];
    const raw = fs.readFileSync(sidecarPath, "utf8").trim();
    if (!raw) return [];

    const entries = raw
      .split("\n")
      .filter(Boolean)
      .map((line) => {
        try {
          return JSON.parse(line);
        } catch (_) {
          return null;
        }
      })
      .filter(Boolean);

    // Filter to: correct session, same-turn only
    const sameTurn = entries.filter(
      (e) =>
        e.session_id === sessionId &&
        typeof e.event_count_at_fire === "number" &&
        e.event_count_at_fire >= currentHistoryCount
    );

    // Convert to the same shape as extractToolEventsRich output
    return sameTurn.map((e) => {
      const ev = { toolName: "Skill" };
      if (typeof e.skill === "string") ev.skillName = e.skill;
      return ev;
    });
  } catch (_) {
    // Fail-open: sidecar read errors must never block the hook.
    return [];
  }
}

// ---------------------------------------------------------------------------
// History analysis
// ---------------------------------------------------------------------------

/**
 * Extract richer tool events including skill names for Skill calls.
 *
 * @param {Array<object>} history
 * @returns {Array<{toolName: string, skillName?: string}>}
 */
function extractToolEventsRich(history) {
  if (!Array.isArray(history)) return [];
  const events = [];
  for (const turn of history) {
    if (turn?.role !== "assistant") continue;
    const content = turn?.content;
    if (!Array.isArray(content)) continue;
    for (const block of content) {
      if (block?.type === "tool_use" && typeof block?.name === "string") {
        const entry = { toolName: block.name };
        if (block.name === "Skill" && typeof block.input?.skill === "string") {
          entry.skillName = block.input.skill;
        }
        events.push(entry);
      }
    }
  }
  return events;
}

/**
 * Classify the current Agent invocation using rich tool events.
 *
 * Returns an object with:
 *   category — one of "router_mediated", "skill_mediated", "bypass",
 *              or "stale_dispatch"
 *   parentSkill — for skill_mediated, the name of the enclosing Skill;
 *                 empty string otherwise.
 *
 * @param {Array<{toolName: string, skillName?: string}>} toolEvents
 * @returns {{ category: "router_mediated" | "skill_mediated" | "bypass" | "stale_dispatch", parentSkill: string }}
 */
function classifyDispatchRich(toolEvents) {
  // Find the most recent "dispatch" Skill invocation index.
  // _bareSkillName() normalizes both "claude-wayfinder:dispatch" and bare
  // "dispatch" to "dispatch" for the comparison. See hooks/lib/skill-name.js.
  let lastDispatchIdx = -1;
  for (let i = toolEvents.length - 1; i >= 0; i--) {
    if (toolEvents[i].toolName === "Skill" && _bareSkillName(toolEvents[i].skillName) === "dispatch") {
      lastDispatchIdx = i;
      break;
    }
  }

  if (lastDispatchIdx === -1) {
    // No dispatch Skill found in session history.
    // Walk backwards to find the nearest un-paired Skill or Agent tool invocation.
    for (let i = toolEvents.length - 1; i >= 0; i--) {
      const ev = toolEvents[i];
      if (ev.toolName === "Agent") {
        // An Agent call appears before this one without any dispatch → bypass
        return { category: "bypass", parentSkill: "" };
      }
      if (ev.toolName === "Skill") {
        // Most recent un-paired tool is a Skill (not dispatch) → skill_mediated
        return { category: "skill_mediated", parentSkill: ev.skillName ?? "" };
      }
      // Any other tool → continue walking back to find if there's a Skill behind it
    }
    // Walked all events without finding a Skill or Agent → bypass.
    return { category: "bypass", parentSkill: "" };
  }

  // Dispatch found — examine the window AFTER the dispatch.
  const window = toolEvents.slice(lastDispatchIdx + 1);

  let countAgent = 0;
  let countOther = 0;

  for (const ev of window) {
    if (ev.toolName === "Agent") {
      countAgent++;
    } else if (ev.toolName !== "Skill") {
      // Skill tool calls in the window don't count toward staleness
      countOther++;
    }
  }

  if (countAgent >= 1) {
    // A prior Agent dispatch happened after the dispatch Skill call.
    // This current call lacks a fresh dispatch authorization → bypass.
    return { category: "bypass", parentSkill: "" };
  }

  if (countOther > STALENESS_BOUND) {
    return { category: "stale_dispatch", parentSkill: "" };
  }

  return { category: "router_mediated", parentSkill: "" };
}

// ---------------------------------------------------------------------------
// Event writing
// ---------------------------------------------------------------------------

/**
 * Append one drift event as a JSONL line to the drift log.
 * Never throws — IO failures must not block the hook.
 *
 * @param {object} event
 * @param {string} driftPath
 */
function appendDriftEvent(event, driftPath) {
  try {
    fs.mkdirSync(path.dirname(driftPath), { recursive: true });
    fs.appendFileSync(driftPath, `${JSON.stringify(event)}\n`);
  } catch (_) {
    // Logging must never break the hook. Silently swallow disk errors.
  }
}

/**
 * Count the total number of tool_use blocks in conversation_history.
 *
 * Used to compute the current history tool-use count for sidecar filtering.
 *
 * @param {Array<object>} history
 * @returns {number}
 */
function countHistoryToolUseBlocks(history) {
  if (!Array.isArray(history)) return 0;
  let count = 0;
  for (const turn of history) {
    if (turn?.role !== "assistant") continue;
    const content = turn?.content;
    if (!Array.isArray(content)) continue;
    for (const block of content) {
      if (block?.type === "tool_use" && typeof block?.name === "string") {
        count++;
      }
    }
  }
  return count;
}

// ---------------------------------------------------------------------------
// Exports (for unit testing the pure logic without stdin)
// ---------------------------------------------------------------------------

module.exports = {
  extractToolEventsRich,
  classifyDispatchRich,
  STALENESS_BOUND,
  readSameTurnSidecarEvents,
  countHistoryToolUseBlocks,
};

// ---------------------------------------------------------------------------
// Main stdin handler
// ---------------------------------------------------------------------------
//
// Guarded by require.main === module so the side-effecting stdin listener only
// runs when invoked as a script (not when require()-ed by tests).

if (require.main === module) {
  let data = "";
  process.stdin.on("data", (chunk) => (data += chunk));
  process.stdin.on("end", () => {
    try {
      const input = parseInput(data);

      // Only handle Agent tool invocations.
      if (input?.tool_name !== "Agent") return;

      const sessionId = input.session_id ?? "";
      const history = input.conversation_history ?? [];

      // Extract tool events from completed conversation history.
      const historyEvents = extractToolEventsRich(history);

      // Merge same-turn sidecar events.
      // conversation_history contains only COMPLETED turns. In a same-turn
      // Skill(dispatch) → Agent sequence, the Skill is not yet in history.
      // The sidecar captures it via PostToolUse(Skill), which fires before
      // this PreToolUse(Agent) hook.
      const currentHistoryCount = countHistoryToolUseBlocks(history);
      const sidecarEvents = readSameTurnSidecarEvents(
        sessionId,
        currentHistoryCount,
        resolveSidecarPath()
      );

      // Append sidecar events AFTER history events so the classifier sees them
      // as the most recent tool invocations (which they are — same-turn).
      const toolEvents = historyEvents.concat(sidecarEvents);

      const { category, parentSkill } = classifyDispatchRich(toolEvents);

      const driftPath = resolveDriftPath();

      if (category === "router_mediated") {
        // Correct routing — no event, silent no-op.
        return;
      }

      if (category === "skill_mediated") {
        // Informational: log to stderr but do NOT block. The skill authored
        // this Agent delegation as part of its deterministic recipe.
        const parentLabel = parentSkill || "(unknown)";
        process.stderr.write(
          `[SKILL-MEDIATED] Agent call dispatched from active Skill scope (parent: ${parentLabel})\n`
        );
        // Still write a drift event so router_health.py can surface the count.
      }

      // Write a drift event for all non-router-mediated categories.
      const event = {
        type: "router_drift",
        ts: new Date().toISOString(),
        session_id: sessionId,
        category,
      };

      // Enrich with bypass_signals + bypass_cause when possible.
      // Three guarded failure modes: module-load throw (handled above by
      // _bypassTaxonomyClassify=null), per-event classify throw (try/catch
      // below), and malformed return shape (if-checks below). All three
      // recover by emitting the event without enrichment.
      if (_bypassTaxonomyClassify) {
        try {
          const result = _bypassTaxonomyClassify(
            category,
            { subagent_type: input?.tool_input?.subagent_type ?? "" },
            toolEvents
          );
          if (
            result &&
            typeof result.cause === "string" &&
            result.signals &&
            typeof result.signals === "object"
          ) {
            event.bypass_signals = result.signals;
            event.bypass_cause = result.cause;
          } else {
            process.stderr.write(
              "[bypass-taxonomy] classify returned malformed shape; emitting without enrichment\n"
            );
          }
        } catch (err) {
          process.stderr.write(
            `[bypass-taxonomy] classify threw; emitting without enrichment: ${err.message}\n`
          );
        }
      }

      appendDriftEvent(event, driftPath);
    } catch (e) {
      // Parse or runtime error — write to stderr and exit 0; never block dispatch.
      process.stderr.write(`[check-agent-dispatch-pairing] error: ${e.message}\n`);
    }
    // Always exit 0 — this hook NEVER blocks.
  });
}
