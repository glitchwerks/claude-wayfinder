// PostToolUse hook: attribute matcher_decision log entries with session_id.
//
// Fires after every Skill tool call. When the invoked skill is "dispatch"
// (the claude-wayfinder deterministic matcher), this hook writes a
// matcher_decision log entry with session_id drawn from the CC hook payload.
//
// ## Why this hook exists (issue #299)
//
// The Python matcher subprocess has no access to the CC session_id. The
// four-tier resolution chain in _catalog.py always falls through to "" in
// production because:
//   - Tier 1 (caller-supplied): the router does not pass it.
//   - Tier 2 (CLAUDE_SESSION_ID env var): not exported to the matcher.
//   - Tier 3 (PID-keyed state file): broken — the SessionStart hook keys
//     the file on node.exe's PID, but node.exe is a dead sibling in the
//     matcher's ancestor chain, so it is never found.
//   - Tier 4: empty string.
//
// The CC PostToolUse hook payload always includes session_id per the
// documented hook contract ("Per session, stable"). This hook uses that
// guaranteed field to write a session-attributed matcher_decision entry.
//
// ## Concurrency safety
//
// Each PostToolUse invocation fires synchronously after its own Skill call,
// with its own session_id. Two concurrent CC sessions produce two separate
// PostToolUse hook processes with distinct session_ids — no cross-
// contamination. Appending to a JSONL file is safe on all platforms (each
// write is a single appendFileSync call with a trailing newline, which is
// atomic for small writes on common filesystems).
//
// ## tool_output shape — live integration test requirement
//
// Per hook-authoring §1, any hook whose correctness depends on the CC input
// payload shape MUST have a live integration test. The `tool_output` field
// in PostToolUse payloads has not been verified in a live CC session for the
// Skill tool specifically. This hook parses tool_output defensively:
//   - If it contains valid JSON with a "decision" field → write the full
//     matcher_decision log entry using that JSON.
//   - Otherwise → write a session_attribution entry (type=matcher_session_id)
//     with just the session_id and timestamp. This allows session attribution
//     even if the tool_output format differs from expectation.
//
// ## Verification
//
// A live integration test is implemented in hooks/tests/log-dispatch-decision.test.js.
// The test instruments the hook and verifies behavior against real subprocess
// output. Section §5 (Breaking-test requirement) confirms the test fails when
// the hook is deliberately broken.
//
// Environment overrides (for testing):
//   DISPATCH_LOG_PATH — override the default log path
//   DISPATCH_HOOK_DEBUG — set to "1" to emit diagnostic messages to stderr

const os = require("node:os");
const path = require("node:path");
const parseInput = require("./parse-input");
const { appendLogLine } = require("./lib/dispatch-log");
const { getPluginVersion } = require("./lib/plugin-version");

/** Default log path — matches the Python matcher's default. */
function resolveLogPath() {
  if (process.env.DISPATCH_LOG_PATH) return process.env.DISPATCH_LOG_PATH;
  const home = process.env.HOME || process.env.USERPROFILE || os.homedir();
  return path.join(home, ".claude", "state", "dispatch-log.jsonl");
}

/**
 * Attempt to parse tool_output as the matcher's decision JSON.
 *
 * The dispatch skill's primary output is the matcher decision JSON emitted
 * to stdout. In a PostToolUse CC payload the `tool_output` field carries
 * the tool's result text. For Skill tools the shape is unverified — this
 * function extracts the JSON defensively by scanning for the first object
 * that contains a "decision" field.
 *
 * Scans from the last occurrence of '{' backward to find the outermost
 * JSON object in the output, which handles cases where tool_output includes
 * other text (preamble, stderr) before or after the JSON.
 *
 * @param {string|null|undefined} toolOutput - The tool_output field from the PostToolUse payload.
 * @returns {object|null} Parsed decision object, or null if no valid JSON found.
 */
function parseDecisionFromOutput(toolOutput) {
  if (!toolOutput || typeof toolOutput !== "string") return null;

  // Try direct JSON parse first (most common case when tool_output is pure JSON).
  try {
    const parsed = JSON.parse(toolOutput.trim());
    if (parsed && typeof parsed === "object" && typeof parsed.decision === "string") {
      return parsed;
    }
  } catch (_) {
    // Not pure JSON — try to extract JSON substring below.
  }

  // Scan for JSON objects embedded in mixed text (e.g. preamble + JSON).
  // Find the last '{' and try progressively shorter substrings.
  const text = toolOutput.trim();
  let start = text.lastIndexOf("{");
  while (start >= 0) {
    try {
      const candidate = text.slice(start);
      const parsed = JSON.parse(candidate);
      if (parsed && typeof parsed === "object" && typeof parsed.decision === "string") {
        return parsed;
      }
    } catch (_) {
      // Not valid JSON from this position — try earlier.
    }
    start = text.lastIndexOf("{", start - 1);
  }

  return null;
}

/**
 * Build the log entry for a session-attributed matcher decision.
 *
 * When the full decision JSON is available from tool_output, writes a
 * matcher_decision entry mirroring the Python matcher's schema. When the
 * decision JSON is not available (tool_output format differs from
 * expectation), writes a matcher_session_id entry recording only the
 * session attribution.
 *
 * @param {object} opts
 * @param {string}      opts.sessionId       - CC session ID from hook payload.
 * @param {string}      opts.ts              - ISO timestamp string.
 * @param {object|null} opts.decision        - Parsed decision JSON, or null.
 * @param {object}      opts.toolInput       - tool_input from hook payload.
 * @param {string}      opts.pluginVersion   - Resolved plugin version string.
 * @returns {object} Log event object ready for appendLogLine.
 */
function buildLogEntry({ sessionId, ts, decision, toolInput, pluginVersion }) {
  if (decision !== null) {
    // Full attribution: write a matcher_decision entry with session_id.
    // Mirrors the Python _write_log_entry schema (issue #294 / #296).
    return {
      type: "matcher_decision",
      ts,
      session_id: sessionId,
      // Reconstruct the input context from tool_input args where available.
      // tool_input.args is the JSON string passed to the dispatch skill by the
      // router agent (e.g. '{"task_description": "..."}').
      input: tryParseArgs(toolInput),
      output: decision,
      catalog_hash: decision.catalog_hash ?? null,
      matcher_version: decision.matcher_version ?? null,
      override_id: decision.override_id ?? null,
      // Mark as hook-written so log analysis can distinguish from Python-written
      // entries and prefer this record when session_id is needed.
      attribution_source: "post_tool_use_hook",
      plugin_version: pluginVersion,
    };
  }

  // Partial attribution: tool_output did not contain parseable decision JSON.
  // Write a lighter entry that records the session_id binding so downstream
  // analysis can correlate matcher_decision entries by timestamp proximity.
  return {
    type: "matcher_session_id",
    ts,
    session_id: sessionId,
    attribution_source: "post_tool_use_hook",
    plugin_version: pluginVersion,
    note: "tool_output did not contain parseable matcher decision JSON; " +
          "see issue #299 live-integration-test requirement",
  };
}

/**
 * Attempt to parse the dispatch skill's args JSON from tool_input.
 *
 * The router agent passes the dispatch context as a JSON string (the stdin
 * of the matcher). If tool_input includes that arg (as tool_input.args or
 * tool_input.context_json or similar), extract it as the input dict.
 *
 * Returns an empty object if parsing fails or the field is absent.
 *
 * @param {object|null} toolInput - tool_input from hook payload.
 * @returns {object} Parsed dispatch context dict, or {}.
 */
function tryParseArgs(toolInput) {
  if (!toolInput || typeof toolInput !== "object") return {};
  // The dispatch skill receives its context as stdin to the Python subprocess.
  // The Skill tool_input typically contains { skill, args } where args is a
  // free-form string of additional arguments or stdin content. Try to parse
  // it as JSON to recover the dispatch context.
  const rawArgs = toolInput.args ?? toolInput.input ?? toolInput.stdin ?? null;
  if (!rawArgs || typeof rawArgs !== "string") return {};
  try {
    const parsed = JSON.parse(rawArgs);
    if (parsed && typeof parsed === "object") return parsed;
  } catch (_) {
    // Not JSON — e.g. it is just CLI flags. Return {}.
  }
  return {};
}

// Only register stdin handlers when run as a script, not when imported as a
// module (e.g. by the test runner). Importing this file otherwise leaks an
// open stdin handle that keeps the Node event loop alive indefinitely.
if (require.main === module) {
  let data = "";
  process.stdin.on("data", (chunk) => (data += chunk));
  process.stdin.on("end", () => {
    // Wrap the entire handler in an async IIFE so we can await getPluginVersion()
    // without leaving unresolved promises that could cause premature process exit.
    (async () => {
      try {
        const input = parseInput(data);

        // Only handle Skill tool invocations for the "dispatch" skill.
        if (input?.tool_name !== "Skill") return;
        const skillName = input?.tool_input?.skill ?? "";
        if (skillName !== "dispatch") return;

        const sessionId = input.session_id ?? "";
        const toolOutput = input.tool_output ?? null;
        const toolInput = input.tool_input ?? {};
        const ts = new Date().toISOString();
        const logPath = resolveLogPath();

        const debug = process.env.DISPATCH_HOOK_DEBUG === "1";
        if (debug) {
          process.stderr.write(
            `[log-dispatch-decision] session_id=${sessionId} ` +
            `tool_output_len=${typeof toolOutput === "string" ? toolOutput.length : "null"}\n`
          );
        }

        const decision = parseDecisionFromOutput(toolOutput);

        if (debug) {
          process.stderr.write(
            `[log-dispatch-decision] decision parsed=${decision !== null} ` +
            `decision_type=${decision?.decision ?? "none"}\n`
          );
        }

        // Await the plugin version before writing — keeps the log entry complete.
        let version = "unknown";
        try {
          version = await getPluginVersion();
        } catch (_) {
          // getPluginVersion() never rejects by design; guard anyway.
        }

        const entry = buildLogEntry({
          sessionId,
          ts,
          decision,
          toolInput,
          pluginVersion: version,
        });
        appendLogLine(entry, logPath);
      } catch (e) {
        // Parse or runtime error — log to stderr and exit 0; never block the call.
        process.stderr.write(`[log-dispatch-decision] error: ${e.message}\n`);
      }
    })();
  });
}

module.exports = {
  parseDecisionFromOutput,
  buildLogEntry,
  tryParseArgs,
};
