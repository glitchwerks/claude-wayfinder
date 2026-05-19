/**
 * router-drift-scanner — pure detection functions for the Stop hook.
 *
 * All functions are deterministic: no LLM calls, no probabilistic content
 * matching. Input is a parsed transcript entry array; output is structured
 * event objects that the hook script writes to the drift JSONL log.
 *
 * Event types produced (v5 §3.3.1):
 *   advisory_override          — router used different agent than advisory recommended
 *   self_handle_unaided_invocation — count of self_handle_unaided decisions per session
 *   needs_more_detail_repeat   — consecutive needs_more_detail for same agent target
 *   catalog_degraded_session   — [CATALOG ERROR] banner appeared in session context
 *   skill_mediated_delegation  — count of Agent calls immediately preceded by Skill calls
 *
 * Component version stamping (Task 5 — refs #395):
 *   advisory_override and needs_more_detail_repeat name specific agents and
 *   receive component version fields (see scanSession JSDoc for the full map).
 *   The other three event types are component-agnostic and receive no version fields.
 *
 * Issue #201 / #341.
 */

const { getComponentVersion: defaultGetComponentVersion } = require("./component-version");

// ---------------------------------------------------------------------------
// Dispatch audit line parser
// ---------------------------------------------------------------------------

/**
 * Parse the dispatch audit summary line emitted by general-purpose.md:
 *   🎯 Dispatch → <decision> [<agent>] (confidence: <n>) — <skills>
 *
 * The [<agent>] part is present only for delegate / advisory / ambiguous.
 * Returns null if the text does not contain an audit line.
 *
 * @param {string} text
 * @returns {{ decision: string, agent: string } | null}
 */
function parseAuditLine(text) {
  if (!text || typeof text !== "string") return null;
  // Match the emoji + "Dispatch →" header line.
  // decision: word chars and underscores (e.g. self_handle_unaided)
  // agent: optional bracketed section
  const m = text.match(/🎯 Dispatch → ([\w_]+)(?:\s+\[([^\]]+)\])?/);
  if (!m) return null;
  return {
    decision: m[1],
    agent: m[2] || "",
  };
}

// ---------------------------------------------------------------------------
// parseTranscript
// ---------------------------------------------------------------------------

/**
 * Reduce a raw transcript entry array into a flat, typed event sequence.
 *
 * Each returned event has a `kind` field:
 *   "dispatch"    — a dispatch audit line was found; has `.decision` and `.agent`
 *   "agent_call"  — an Agent tool_use; has `.subagent_type`
 *   "skill_call"  — a Skill tool_use; has `.skill`
 *
 * Non-assistant entries are walked only for [CATALOG ERROR] detection (handled
 * in detectCatalogDegraded separately). This function focuses on assistant-turn
 * events needed for the routing-quality detectors.
 *
 * @param {object[]} entries  Parsed JSONL lines from the session transcript.
 * @returns {object[]}        Flat ordered event list.
 */
function parseTranscript(entries) {
  const events = [];

  for (const entry of entries) {
    if (!entry || entry.type !== "assistant") continue;

    const msg = entry.message;
    if (!msg) continue;

    const content = Array.isArray(msg.content) ? msg.content : [];

    for (const item of content) {
      // Skip non-objects (null, number, string, etc.)
      if (!item || typeof item !== "object") continue;

      if (item.type === "text") {
        const parsed = parseAuditLine(item.text || "");
        if (parsed) {
          events.push({ kind: "dispatch", decision: parsed.decision, agent: parsed.agent });
        }
      } else if (item.type === "tool_use") {
        if (item.name === "Agent") {
          const input = item.input || {};
          events.push({ kind: "agent_call", subagent_type: input.subagent_type || "" });
        } else if (item.name === "Skill") {
          const input = item.input || {};
          events.push({ kind: "skill_call", skill: input.skill || "" });
        }
      }
    }
  }

  return events;
}

// ---------------------------------------------------------------------------
// detectAdvisoryOverride
// ---------------------------------------------------------------------------

/**
 * Find cases where the router received an `advisory` dispatch decision but
 * then called a different agent than the one recommended.
 *
 * Algorithm: scan the event list for `dispatch` events with decision="advisory".
 * For each advisory, scan forward past any interposed `skill_call` events to
 * find the first `agent_call` or `dispatch` event:
 *   - `agent_call` found: if its `subagent_type` differs from the advisory
 *     `agent`, emit a drift record.
 *   - `dispatch` found: the advisory was abandoned (a new routing decision
 *     arrived before any agent was called) — do not emit drift.
 *   - End of list: router chose not to delegate — no drift.
 *
 * This replaces the old single-event lookahead that silently skipped the
 * canonical sequence: advisory → skill_call → agent_call (issue #144).
 *
 * @param {object[]} events  Output of parseTranscript.
 * @returns {object[]}       Array of advisory_override event objects.
 */
function detectAdvisoryOverride(events) {
  const driftEvents = [];

  for (let i = 0; i < events.length; i++) {
    const ev = events[i];
    if (ev.kind !== "dispatch" || ev.decision !== "advisory") continue;

    // Scan forward past skill_calls to find the first agent_call or dispatch.
    for (let j = i + 1; j < events.length; j++) {
      const next = events[j];
      if (next.kind === "dispatch") break; // new routing decision — advisory abandoned
      if (next.kind === "agent_call") {
        if (next.subagent_type !== ev.agent) {
          driftEvents.push({
            type: "advisory_override",
            recommended_agent: ev.agent,
            actual_agent: next.subagent_type,
          });
        }
        break; // found the outcome either way
      }
      // skill_call (or any future event kind): keep scanning
    }
  }

  return driftEvents;
}

// ---------------------------------------------------------------------------
// detectSelfHandleUnaided
// ---------------------------------------------------------------------------

/**
 * Count how many times the router invoked self_handle_unaided in this session.
 * High rates indicate catalog coverage gaps.
 *
 * @param {object[]} events  Output of parseTranscript.
 * @returns {{ count: number }}
 */
function detectSelfHandleUnaided(events) {
  let count = 0;
  for (const ev of events) {
    if (ev.kind === "dispatch" && ev.decision === "self_handle_unaided") count++;
  }
  return { count };
}

// ---------------------------------------------------------------------------
// detectNeedsMoreDetailRepeat
// ---------------------------------------------------------------------------

/**
 * Detect cases where needs_more_detail was returned twice in a row for the
 * same agent target — indicating the router repeatedly failed to enrich the
 * dispatch context.
 *
 * Algorithm: track the last seen `needs_more_detail` dispatch event. When the
 * next `dispatch` event is also `needs_more_detail` with the same agent target,
 * emit a drift record.  Non-dispatch events between them are ignored (the
 * "repeat" is about consecutive dispatch decisions, not consecutive entries).
 *
 * @param {object[]} events  Output of parseTranscript.
 * @returns {object[]}       Array of needs_more_detail_repeat event objects.
 */
function detectNeedsMoreDetailRepeat(events) {
  const driftEvents = [];
  let lastNmd = null; // last seen needs_more_detail dispatch event

  for (const ev of events) {
    if (ev.kind !== "dispatch") continue;

    if (ev.decision === "needs_more_detail") {
      if (lastNmd !== null && lastNmd.agent === ev.agent) {
        driftEvents.push({
          type: "needs_more_detail_repeat",
          agent: ev.agent,
        });
        // Don't reset lastNmd — a third consecutive repeat should also be caught
      } else {
        lastNmd = ev;
      }
    } else {
      // Non-needs_more_detail dispatch resets the streak
      lastNmd = null;
    }
  }

  return driftEvents;
}

// ---------------------------------------------------------------------------
// detectCatalogDegraded
// ---------------------------------------------------------------------------

/**
 * Determine whether a [CATALOG ERROR] banner appeared anywhere in the session.
 *
 * The banner is written to stderr by match.py when the catalog is degraded
 * and relayed to the user by the router as a verbatim text block. It can also
 * appear in hook additionalContext attachments (from refresh-catalog-on-stale).
 *
 * Algorithm: scan all entries (not just assistant) for the literal string
 * "[CATALOG ERROR]" in any text or attachment content field.
 *
 * @param {object[]} entries  Raw transcript entries (before parseTranscript).
 * @returns {boolean}
 */
function detectCatalogDegraded(entries) {
  const MARKER = "[CATALOG ERROR]";

  for (const entry of entries) {
    if (!entry) continue;

    // Check assistant text content
    if (entry.type === "assistant") {
      const msg = entry.message;
      const content = Array.isArray(msg?.content) ? msg.content : [];
      for (const item of content) {
        if (item && item.type === "text" && typeof item.text === "string") {
          if (item.text.includes(MARKER)) return true;
        }
      }
    }

    // Check attachment entries (hook additionalContext arrives as attachments)
    if (entry.type === "attachment" && entry.attachment) {
      const att = entry.attachment;
      if (typeof att.content === "string" && att.content.includes(MARKER)) return true;
      if (typeof att.additionalContext === "string" && att.additionalContext.includes(MARKER))
        return true;
      // Also check nested stdout in hook_success shape
      if (typeof att.stdout === "string" && att.stdout.includes(MARKER)) return true;
    }
  }

  return false;
}

// ---------------------------------------------------------------------------
// detectSkillMediatedDelegation
// ---------------------------------------------------------------------------

/**
 * Count Agent calls that occurred immediately after a Skill call — i.e. the
 * most recent event before the Agent call was a skill_call.  This captures the
 * pattern: router invoked a Skill (e.g. dispatch), then immediately delegated
 * to an agent.
 *
 * "Immediately preceded" means: the event directly before the agent_call in
 * the flat event list (from parseTranscript) is a skill_call.
 *
 * @param {object[]} events  Output of parseTranscript.
 * @returns {{ count: number }}
 */
function detectSkillMediatedDelegation(events) {
  let count = 0;
  for (let i = 1; i < events.length; i++) {
    if (events[i].kind === "agent_call" && events[i - 1].kind === "skill_call") {
      count++;
    }
  }
  return { count };
}

// ---------------------------------------------------------------------------
// scanSession
// ---------------------------------------------------------------------------

/**
 * Run all detectors against a session's transcript entries and return the
 * complete list of drift/informational events to be written to the log.
 *
 * Events always include `type`, `ts` (ISO timestamp), `session_id`, and
 * `plugin_version` (plugin version string, or "unknown").
 *
 * Component version stamping:
 *   Two event types name specific agents and receive version fields:
 *
 *   advisory_override:
 *     recommended_agent_rev, recommended_agent_content_hash (for recommended_agent)
 *     actual_agent_rev,      actual_agent_content_hash      (for actual_agent)
 *
 *   needs_more_detail_repeat:
 *     agent_rev, agent_content_hash (for the repeated target agent)
 *
 *   Component-agnostic events (self_handle_unaided_invocation, catalog_degraded_session,
 *   skill_mediated_delegation) do NOT receive any component version fields.
 *
 *   Sentinel discipline:
 *     cv.rev === undefined → omit fields entirely
 *     cv.rev === null      → include fields with null values
 *     cv.rev is integer    → include fields with their values
 *
 * @param {{ entries: object[], sessionId: string, pluginVersion?: string, getComponentVersion?: Function }} opts
 *   - entries:             Parsed transcript entry array.
 *   - sessionId:           Session UUID.
 *   - pluginVersion:       Pre-resolved version string from getPluginVersion().
 *                          Defaults to "unknown" when absent.
 *   - getComponentVersion: Optional injected helper for testing. Defaults to the
 *                          real getComponentVersion from lib/component-version.js.
 * @returns {object[]}  Array of structured event objects ready for JSONL.
 */
function scanSession({ entries, sessionId, pluginVersion, getComponentVersion: getCV }) {
  const ts = new Date().toISOString();
  const sid = sessionId || "";
  // Resolve the plugin version once; default to "unknown" when not provided.
  const hv = pluginVersion || "unknown";
  // Use injected helper (for tests) or fall back to the real one.
  const resolveCV = getCV || defaultGetComponentVersion;
  const events = parseTranscript(entries);
  const result = [];

  // 1. advisory_override
  for (const e of detectAdvisoryOverride(events)) {
    const ev = {
      type: e.type,
      ts,
      session_id: sid,
      plugin_version: hv,
      recommended_agent: e.recommended_agent,
      actual_agent: e.actual_agent,
    };
    // Stamp component versions for both named agents.
    const cvRec = resolveCV(e.recommended_agent, "agent");
    if (cvRec.rev !== undefined) ev.recommended_agent_rev = cvRec.rev;
    if (cvRec.content_hash !== undefined) ev.recommended_agent_content_hash = cvRec.content_hash;
    const cvAct = resolveCV(e.actual_agent, "agent");
    if (cvAct.rev !== undefined) ev.actual_agent_rev = cvAct.rev;
    if (cvAct.content_hash !== undefined) ev.actual_agent_content_hash = cvAct.content_hash;
    result.push(ev);
  }

  // 2. self_handle_unaided_invocation (only emit if count > 0; component-agnostic)
  const shu = detectSelfHandleUnaided(events);
  if (shu.count > 0) {
    result.push({
      type: "self_handle_unaided_invocation",
      ts,
      session_id: sid,
      plugin_version: hv,
      count: shu.count,
    });
  }

  // 3. needs_more_detail_repeat
  for (const e of detectNeedsMoreDetailRepeat(events)) {
    const ev = { type: e.type, ts, session_id: sid, plugin_version: hv, agent: e.agent };
    // Stamp component version for the repeated target agent.
    const cv = resolveCV(e.agent, "agent");
    if (cv.rev !== undefined) ev.agent_rev = cv.rev;
    if (cv.content_hash !== undefined) ev.agent_content_hash = cv.content_hash;
    result.push(ev);
  }

  // 4. catalog_degraded_session (uses raw entries, not parsed events; component-agnostic)
  if (detectCatalogDegraded(entries)) {
    result.push({ type: "catalog_degraded_session", ts, session_id: sid, plugin_version: hv });
  }

  // 5. skill_mediated_delegation (informational — always emit when count > 0; component-agnostic)
  const smd = detectSkillMediatedDelegation(events);
  if (smd.count > 0) {
    result.push({
      type: "skill_mediated_delegation",
      ts,
      session_id: sid,
      plugin_version: hv,
      count: smd.count,
    });
  }

  return result;
}

module.exports = {
  parseTranscript,
  parseAuditLine,
  detectAdvisoryOverride,
  detectSelfHandleUnaided,
  detectNeedsMoreDetailRepeat,
  detectCatalogDegraded,
  detectSkillMediatedDelegation,
  scanSession,
};
