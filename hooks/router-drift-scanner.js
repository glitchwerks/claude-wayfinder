// Stop hook: scan the completed session's transcript for routing-quality drift
// events and append them to ~/.claude/state/router-drift.jsonl.
//
// Produces five event types (v5 §3.3.1/3.3.2):
//   advisory_override          — router used a different agent than advisory recommended
//   self_handle_unaided_invocation — count of self_handle_unaided decisions per session
//   needs_more_detail_repeat   — consecutive needs_more_detail for same agent target
//   catalog_degraded_session   — [CATALOG ERROR] banner appeared in session context
//   skill_mediated_delegation  — informational count of skill-then-agent dispatch chains
//
// (bypass and stale_dispatch are emitted by the PreToolUse floor hook.)
//
// Failure modes: if anything goes wrong, log to stderr and exit 0 — never block
// session end.
//
// Environment overrides (for testing):
//   CLAUDE_HOME            — override ~/.claude base directory
//   ROUTER_DRIFT_LOG_PATH  — override the drift JSONL log path

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const parseInput = require("./parse-input");
const { appendLogLine } = require("./lib/dispatch-log");
const { scanSession } = require("./lib/router-drift-scanner");
const { getPluginVersion } = require("./lib/plugin-version");
const { computeProjectSlug } = require("./lib/session-slug");

// ---------------------------------------------------------------------------
// Path resolution
// ---------------------------------------------------------------------------

const claudeHome = process.env.CLAUDE_HOME || path.join(os.homedir(), ".claude");

const DEFAULT_DRIFT_LOG = path.join(claudeHome, "state", "router-drift.jsonl");
const driftLogPath = process.env.ROUTER_DRIFT_LOG_PATH || DEFAULT_DRIFT_LOG;

// ---------------------------------------------------------------------------
// Transcript location
//
// Claude Code stores transcripts at:
//   <claudeHome>/projects/<cwd-slug>/<session-id>.jsonl
//
// The slug is produced by computeProjectSlug from lib/session-slug.js.
// ---------------------------------------------------------------------------

/**
 * Locate the transcript .jsonl file for this session.
 *
 * @param {string} cwd        Working directory from the hook payload.
 * @param {string} sessionId  Session UUID from the hook payload.
 * @returns {string|null}  Absolute path, or null if the file is not found.
 */
function findTranscript(cwd, sessionId) {
  if (!cwd || !sessionId) return null;
  try {
    const slug = computeProjectSlug(cwd);
    const candidate = path.join(claudeHome, "projects", slug, `${sessionId}.jsonl`);
    if (fs.statSync(candidate).isFile()) return candidate;
  } catch (_) {}
  return null;
}

/**
 * Read a .jsonl file and return parsed entries. Lines that fail JSON.parse
 * are silently dropped.
 *
 * @param {string} filePath
 * @returns {object[]}
 */
function readTranscript(filePath) {
  const raw = fs.readFileSync(filePath, "utf8");
  const entries = [];
  for (const line of raw.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    try {
      entries.push(JSON.parse(trimmed));
    } catch (_) {
      // Tolerate individual-line failures
    }
  }
  return entries;
}

// ---------------------------------------------------------------------------
// Idempotency: track which sessions have already been scanned
//
// Before writing new events, check whether any event for this session_id
// already exists in the drift log. If so, skip.
// ---------------------------------------------------------------------------

/**
 * Return true if the drift log already contains events for the given session.
 *
 * @param {string} logPath
 * @param {string} sessionId
 * @returns {boolean}
 */
function sessionAlreadyScanned(logPath, sessionId) {
  if (!sessionId) return false;
  try {
    const raw = fs.readFileSync(logPath, "utf8");
    for (const line of raw.split("\n")) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      try {
        const obj = JSON.parse(trimmed);
        if (obj.session_id === sessionId) return true;
      } catch (_) {}
    }
  } catch (_) {
    // Log doesn't exist yet — not scanned
  }
  return false;
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

let stdinData = "";
process.stdin.on("data", (chunk) => (stdinData += chunk));
process.stdin.on("end", () => {
  // Resolve the plugin version once for this hook invocation, then run main.
  // getPluginVersion() never rejects — it always resolves (to version or "unknown").
  getPluginVersion()
    .then((pluginVersion) => {
      try {
        main(stdinData, pluginVersion);
      } catch (err) {
        // Top-level safety net — must never crash or block session end.
        process.stderr.write(
          `[router-drift-scanner] unexpected error: ${err.stack || err.message}\n`
        );
        process.exit(0);
      }
    })
    .catch(() => {
      // Defensive: getPluginVersion should never reject, but if it somehow does,
      // fall back to running main with "unknown".
      try {
        main(stdinData, "unknown");
      } catch (err) {
        process.stderr.write(
          `[router-drift-scanner] unexpected error: ${err.stack || err.message}\n`
        );
        process.exit(0);
      }
    });
});

/**
 * @param {string} rawStdin
 * @param {string} pluginVersion - Pre-resolved version string (or "unknown").
 */
function main(rawStdin, pluginVersion) {
  // Parse hook payload.
  let payload;
  try {
    payload = parseInput(rawStdin);
  } catch (err) {
    process.stderr.write(`[router-drift-scanner] could not parse hook stdin: ${err.message}\n`);
    return; // exit 0 implicitly
  }

  const cwd = payload?.cwd || "";
  const sessionId = payload?.session_id || "";

  if (!sessionId) {
    process.stderr.write("[router-drift-scanner] no session_id in hook payload — skipping\n");
    return;
  }

  // Idempotency guard: skip if this session was already scanned
  if (sessionAlreadyScanned(driftLogPath, sessionId)) {
    return;
  }

  // Locate and read transcript
  const transcriptPath = findTranscript(cwd, sessionId);
  if (!transcriptPath) {
    // No transcript found — nothing to scan. This is normal for very short
    // sessions or when cwd doesn't match any project directory.
    return;
  }

  let entries;
  try {
    entries = readTranscript(transcriptPath);
  } catch (err) {
    process.stderr.write(
      `[router-drift-scanner] could not read transcript ${transcriptPath}: ${err.message}\n`
    );
    return;
  }

  // Run all detectors
  let driftEvents;
  try {
    driftEvents = scanSession({ entries, sessionId, pluginVersion });
  } catch (err) {
    process.stderr.write(`[router-drift-scanner] error during scan: ${err.message}\n`);
    return;
  }

  // Append each event to the drift log
  for (const event of driftEvents) {
    appendLogLine(event, driftLogPath);
  }
}
