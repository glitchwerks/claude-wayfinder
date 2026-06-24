// PostToolUse hook: attribute matcher_decision log entries with session_id.
//
// Fires after every Bash tool call. When the command contains the
// substring "claude_wayfinder dispatch" (the real-catalog dispatch form),
// this hook writes a matcher_decision log entry with session_id drawn from
// the CC hook payload — the only place session_id is reliably available.
//
// ## Why this hook fires on Bash, not Skill (issue #299 correction)
//
// The dispatch flow is TWO separate tool calls:
//   1. Skill(dispatch) → result is the SKILL.md instruction text (no JSON).
//   2. Bash(echo '<json>' | python -m claude_wayfinder dispatch) → result
//      is the decision JSON.
// Wiring to Skill(dispatch) never sees a decision and always falls through
// to the matcher_session_id fallback. The hook must fire on Bash.
//
// ## Early-return discipline (required for Bash PostToolUse)
//
// A Bash PostToolUse hook fires on EVERY bash command. The hook MUST do a
// cheap substring check on tool_input.command and return immediately for
// non-dispatch commands. The check runs before any I/O.
//
// Accepted dispatch forms (contain "claude_wayfinder dispatch"):
//   echo '<json>' | python -m claude_wayfinder dispatch
//   "$PY" -m claude_wayfinder dispatch
//
// Excluded forms (must not produce log entries):
//   python -m claude_wayfinder dispatch --help
//   python -m claude_wayfinder dispatch --demo
//   python -m claude_wayfinder catalog build
//
// ## Result field: tool_response (object shape, not a string)
//
// PostToolUse(Bash) payloads carry the tool's result in tool_response as an
// OBJECT, not a plain string. Verified live (issue #299):
//   tool_response: {
//     stdout: "<decision JSON string>",
//     stderr: "",
//     interrupted: false,
//     isImage: false,
//     noOutputExpected: false
//   }
//
// The decision JSON lives at tool_response.stdout. The hook extracts it via:
//   const tr = input.tool_response;
//   const toolResponse = typeof tr === "string" ? tr : (tr?.stdout ?? null);
// The string branch is a safety fallback; the primary live shape is the object.
// The prior implementation passed the whole object to parseDecisionFromOutput,
// which returned null at its typeof guard, causing the hook to always write
// the partial matcher_session_id fallback instead of a full matcher_decision.
//
// ## Input extraction from command string
//
// The Bash command for a real dispatch has the form:
//   echo '<input_json>' | "<py>" -m claude_wayfinder dispatch
// The hook extracts the echo'd JSON to recover the dispatch input context
// for the log entry, giving a fully-attributed record.
//
// ## De-duplication design (issue #299 / #440)
//
// The Python matcher's _write_log_entry also appends a matcher_decision
// entry when DISPATCH_LOG_PATH is set. This hook writes a second entry
// with session_id populated and:
//   attribution_source: "post_tool_use_hook"
// The Python entry carries attribution_source: "python_matcher" (#440
// Option A). Log consumers and corpus builders MUST still prefer the
// hook-attributed entry as the canonical organic record. The
// load_organic_decisions filter in log_filter.py now excludes
// "python_matcher" entries to avoid double-counting — consumers should
// use that filter rather than rolling their own attribution check.
//
// Rationale for keeping both writers: the hook fires AFTER the Python
// subprocess completes; there is no mechanism in CC's hook model to suppress
// the Python write before it happens. Modifying the Bash invocation to
// unset DISPATCH_LOG_PATH would suppress the Python write but would break
// the log write entirely if the hook also fails. Keeping both with
// distinguishable attribution_source fields is the safe, fail-open design.
//
// ## Concurrency safety
//
// Each PostToolUse invocation fires synchronously after its own Bash call,
// with its own session_id. Two concurrent CC sessions produce two separate
// PostToolUse hook processes with distinct session_ids — no cross-
// contamination. Appending to a JSONL file is safe on all platforms (each
// write is a single appendFileSync call with a trailing newline, which is
// atomic for small writes on common filesystems).
//
// ## Environment overrides (for testing)
//   DISPATCH_LOG_PATH    — override the default log path
//   DISPATCH_HOOK_DEBUG  — set to "1" to emit diagnostic messages to stderr
//                          AND write a full payload dump to a temp file

const fs = require("node:fs");
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
 * Return true if the Bash command is a real claude_wayfinder dispatch call.
 *
 * Accepts commands that contain "claude_wayfinder dispatch" but excludes:
 *   --help   (help text, no decision output)
 *   --demo   (demo mode, outputs human text not JSON)
 *   catalog  (catalog build subcommand, not a dispatch call)
 *
 * The check is cheap (indexOf) and must run before any I/O.
 *
 * @param {string|null|undefined} command - tool_input.command from the payload.
 * @returns {boolean}
 */
function isDispatchCommand(command) {
  if (!command || typeof command !== "string") return false;
  if (command.indexOf("claude_wayfinder dispatch") === -1) return false;
  // Exclude non-decision invocation forms.
  if (command.indexOf("--help") !== -1) return false;
  if (command.indexOf("--demo") !== -1) return false;
  if (command.indexOf("catalog") !== -1) return false;
  return true;
}

/**
 * Extract the dispatch input JSON from the echo'd portion of the command.
 *
 * The real-catalog dispatch command has the form:
 *   echo '<input_json>' | "<py>" -m claude_wayfinder dispatch
 *
 * This function finds the first "echo '" sequence and extracts the JSON
 * between the opening "'" and the " |" pipe separator. Returns null if
 * the command does not match the expected form.
 *
 * @param {string} command - The Bash command string.
 * @returns {object|null} Parsed input JSON, or null on parse failure.
 */
function extractInputFromCommand(command) {
  if (!command || typeof command !== "string") return null;
  // Find: echo '<json>' |
  // Match from the first ' after "echo " to the first ' | sequence.
  const echoIdx = command.indexOf("echo '");
  if (echoIdx === -1) {
    // Also try double-quote form: echo "<json>" |
    const dqIdx = command.indexOf('echo "');
    if (dqIdx === -1) return null;
    const jsonStart = dqIdx + 6; // after echo "
    const jsonEnd = command.indexOf('" |', jsonStart);
    if (jsonEnd === -1) return null;
    const raw = command.slice(jsonStart, jsonEnd);
    try {
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed === "object") return parsed;
    } catch (_) {
      // Not parseable — return null.
    }
    return null;
  }
  const jsonStart = echoIdx + 6; // after echo '
  const jsonEnd = command.indexOf("' |", jsonStart);
  if (jsonEnd === -1) return null;
  const raw = command.slice(jsonStart, jsonEnd);
  try {
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === "object") return parsed;
  } catch (_) {
    // Not parseable — return null.
  }
  return null;
}

/**
 * Attempt to parse the matcher's decision JSON from tool_response.
 *
 * The dispatch Bash command emits the decision JSON to stdout. In a
 * PostToolUse CC payload the tool_response field carries the tool's
 * result text. This function extracts the JSON defensively by scanning
 * for the first object that contains a "decision" field.
 *
 * Scans from the last occurrence of '{' backward to find the outermost
 * JSON object in the output, which handles cases where tool_response
 * includes stderr preamble before or after the JSON.
 *
 * @param {string|null|undefined} toolResponse - tool_response from the payload.
 * @returns {object|null} Parsed decision object, or null if not found.
 */
function parseDecisionFromOutput(toolResponse) {
  if (!toolResponse || typeof toolResponse !== "string") return null;

  // Try direct JSON parse first (most common — clean stdout).
  try {
    const parsed = JSON.parse(toolResponse.trim());
    if (parsed && typeof parsed === "object" && typeof parsed.decision === "string") {
      return parsed;
    }
    // Direct parse succeeded but no decision field — the input is pure JSON
    // without a decision. Scanning substrings would find the same object again
    // (since the outermost { is at position 0), producing an infinite loop.
    return null;
  } catch (_) {
    // Not pure JSON — try to extract JSON substring below.
  }

  // Scan for JSON objects embedded in mixed text (e.g. preamble + JSON).
  // Only reached when the full string is NOT valid JSON (catch above fired).
  const text = toolResponse.trim();
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
    // Guard: when start is 0, start-1 is -1. lastIndexOf("{", -1) returns 0
    // (not -1) on strings that begin with "{", creating an infinite loop.
    // Explicitly break when we have already tried position 0.
    if (start === 0) break;
    start = text.lastIndexOf("{", start - 1);
  }

  return null;
}

/**
 * Build the log entry for a session-attributed matcher decision.
 *
 * When the full decision JSON is available from tool_response, writes a
 * matcher_decision entry mirroring the Python matcher's schema. When the
 * decision JSON is not available, writes a matcher_session_id entry that
 * records only the session attribution.
 *
 * attribution_source: "post_tool_use_hook" marks this as hook-written.
 * Log consumers should prefer these entries (they carry session_id) over
 * Python-written entries (which have session_id="" and no attribution_source).
 *
 * @param {object} opts
 * @param {string}      opts.sessionId       - CC session ID from hook payload.
 * @param {string}      opts.ts              - ISO timestamp string.
 * @param {object|null} opts.decision        - Parsed decision JSON, or null.
 * @param {object|null} opts.inputContext    - Parsed dispatch input JSON, or null.
 * @param {string}      opts.pluginVersion   - Resolved plugin version string.
 * @returns {object} Log event object ready for appendLogLine.
 */
function buildLogEntry({ sessionId, ts, decision, inputContext, pluginVersion }) {
  if (decision !== null) {
    return {
      type: "matcher_decision",
      ts,
      session_id: sessionId,
      input: inputContext ?? {},
      output: decision,
      catalog_hash: decision.catalog_hash ?? null,
      matcher_version: decision.matcher_version ?? null,
      override_id: decision.override_id ?? null,
      // Mark as hook-written so log analysis prefers this record over
      // the Python-written entry (which has session_id="" and no field).
      attribution_source: "post_tool_use_hook",
      plugin_version: pluginVersion,
    };
  }

  // Partial attribution: tool_response did not contain parseable decision JSON.
  return {
    type: "matcher_session_id",
    ts,
    session_id: sessionId,
    attribution_source: "post_tool_use_hook",
    plugin_version: pluginVersion,
    note: "tool_response did not contain parseable matcher decision JSON; " +
          "see issue #299 live-integration-test requirement (field: tool_response)",
  };
}

// Only register stdin handlers when run as a script, not when imported as a
// module (e.g. by the test runner). Importing this file otherwise leaks an
// open stdin handle that keeps the Node event loop alive indefinitely.
if (require.main === module) {
  let data = "";
  process.stdin.on("data", (chunk) => (data += chunk));
  process.stdin.on("end", () => {
    (async () => {
      try {
        const input = parseInput(data);

        // --- EARLY RETURN: only handle Bash dispatch commands ---
        // This check MUST run first — PostToolUse(Bash) fires on every
        // bash command. Non-dispatch commands exit immediately.
        if (input?.tool_name !== "Bash") return;
        const command = input?.tool_input?.command ?? "";
        if (!isDispatchCommand(command)) return;
        // --- end early return guard ---

        const sessionId = input.session_id ?? "";
        // CC PostToolUse(Bash) delivers tool_response as an OBJECT:
        //   { stdout: "<decision json>", stderr: "", interrupted: false, ... }
        // The decision JSON lives at tool_response.stdout.
        // Verified live (issue #299): the previous string-read always returned
        // null from parseDecisionFromOutput's typeof guard, causing the hook
        // to fall through to the matcher_session_id partial path.
        // String fallback is kept for safety in case the shape changes.
        const tr = input.tool_response;
        const toolResponse = typeof tr === "string" ? tr : (tr?.stdout ?? null);
        const ts = new Date().toISOString();
        const logPath = resolveLogPath();

        const debug = process.env.DISPATCH_HOOK_DEBUG === "1";
        if (debug) {
          // Write full payload dump to a PRIVATE directory for live contract
          // verification.  Payloads contain prompt excerpts; they must NOT land
          // in the world-readable shared /tmp.
          //
          // Directory resolution (defense-in-depth):
          //   1. CLAUDE_PLUGIN_DATA env var when set and non-empty (test / CI override).
          //   2. ~/.claude/state/wayfinder-debug (user-private fallback).
          //
          // The directory is created with mode 0o700 (owner-only, ignored on Windows
          // where the redirect itself provides privacy — %TEMP% is per-user).
          // The file is written with mode 0o600 AND an explicit chmodSync immediately
          // after, because create-mode is masked by the process umask on POSIX.
          try {
            const pluginDataEnv = process.env.CLAUDE_PLUGIN_DATA;
            const dumpDir =
              pluginDataEnv && pluginDataEnv.trim() !== ""
                ? pluginDataEnv
                : path.join(os.homedir(), ".claude", "state", "wayfinder-debug");
            fs.mkdirSync(dumpDir, { recursive: true, mode: 0o700 });
            const dumpFile = path.join(dumpDir, `dispatch-hook-payload-${Date.now()}.json`);
            const dumpData = JSON.stringify(input, null, 2);
            fs.writeFileSync(dumpFile, dumpData, { encoding: "utf8", mode: 0o600 });
            // Explicit chmod enforces 0600 on POSIX regardless of umask.
            // On Windows this is a partial no-op; the redirect already provides privacy.
            fs.chmodSync(dumpFile, 0o600);
            process.stderr.write(
              `[log-dispatch-decision] DEBUG payload dump: ${dumpFile}\n`
            );
          } catch (_dumpErr) {
            // Never let debug writes break the hook.
          }
          process.stderr.write(
            `[log-dispatch-decision] session_id=${sessionId} ` +
            `tool_response_len=${typeof toolResponse === "string" ? toolResponse.length : "null"}\n`
          );
        }

        const decision = parseDecisionFromOutput(toolResponse);
        const inputContext = extractInputFromCommand(command);

        if (debug) {
          process.stderr.write(
            `[log-dispatch-decision] decision parsed=${decision !== null} ` +
            `decision_type=${decision?.decision ?? "none"} ` +
            `input_extracted=${inputContext !== null}\n`
          );
        }

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
          inputContext,
          pluginVersion: version,
        });
        appendLogLine(entry, logPath);
      } catch (e) {
        process.stderr.write(`[log-dispatch-decision] error: ${e.message}\n`);
      }
    })();
  });
}

module.exports = {
  parseDecisionFromOutput,
  buildLogEntry,
  extractInputFromCommand,
  isDispatchCommand,
};
