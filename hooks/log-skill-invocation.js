const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const parseInput = require("./parse-input");
const { appendLogLine } = require("./lib/dispatch-log");
const { getPluginVersion } = require("./lib/plugin-version");
const { getComponentVersion } = require("./lib/component-version");

/**
 * Maximum number of lines to retain in the sidecar file.
 *
 * Overridable via SKILL_SIDECAR_MAX_LINES env var (parsed as int).
 * If the env var is set but not a valid integer, the default is used.
 * This override exists for tests — it avoids writing 1000+ lines per test.
 *
 * Follows the same pattern as SKILL_SIDECAR_PATH.
 */
const MAX_SIDECAR_LINES = (() => {
  const raw = process.env.SKILL_SIDECAR_MAX_LINES;
  if (raw !== undefined) {
    const parsed = Number.parseInt(raw, 10);
    if (Number.isFinite(parsed) && parsed > 0) return parsed;
  }
  return 1000;
})();

/** Default log path — can be overridden in tests via DISPATCH_LOG_PATH. */
function resolveLogPath() {
  if (process.env.DISPATCH_LOG_PATH) return process.env.DISPATCH_LOG_PATH;
  const home = process.env.HOME || process.env.USERPROFILE || os.homedir();
  return path.join(home, ".claude", "state", "dispatch-log.jsonl");
}

/**
 * Default sidecar path for recent skill invocations.
 *
 * This sidecar is the fix for issue #65 — it provides a synchronous signal
 * source for hooks/check-agent-dispatch-pairing.js to read in PreToolUse(Agent).
 *
 * The sidecar is written by this hook (PostToolUse on Skill) before the next
 * tool's PreToolUse fires. PostToolUse → PreToolUse is sequential in the Claude
 * Code hook lifecycle, so the sidecar is guaranteed to be on disk when the
 * Agent's PreToolUse reads it.
 *
 * Overridable via SKILL_SIDECAR_PATH env var (used in tests).
 */
function resolveSidecarPath() {
  if (process.env.SKILL_SIDECAR_PATH) return process.env.SKILL_SIDECAR_PATH;
  const home = process.env.HOME || process.env.USERPROFILE || os.homedir();
  return path.join(home, ".claude", "state", "recent-skill-invocations.jsonl");
}

/**
 * Count the number of tool_use blocks in conversation_history.
 *
 * Used to record event_count_at_fire in the sidecar entry. The consumer
 * (hooks/check-agent-dispatch-pairing.js) uses this to determine whether a sidecar
 * entry is from the current turn (same-turn) or a prior completed turn already
 * represented in conversation_history.
 *
 * A sidecar entry is "same-turn" (not yet in conversation_history) when
 * entry.event_count_at_fire >= current conversation_history tool_use count.
 *
 * @param {Array<object>|undefined} history - conversation_history from hook payload
 * @returns {number}
 */
function countToolUseBlocks(history) {
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

/**
 * Append a sidecar entry to the recent-skill-invocations JSONL, then trim the
 * file to at most MAX_SIDECAR_LINES lines if it has grown beyond the bound.
 *
 * ## Trim strategy — trim on append, atomic via rename
 *
 * After each append we check the line count. If it is within the bound we
 * return immediately (no .tmp file is written). If it exceeds the bound:
 *
 *   1. Read the whole file (synchronous — tiny file; < 1 MB at the bound).
 *   2. Split on "\n", drop trailing empty element, keep the LAST N lines.
 *   3. Write to <sidecarPath>.tmp then fs.renameSync(.tmp → sidecarPath).
 *      On Windows, renameSync can throw EEXIST if the destination exists; in
 *      that case we unlink the destination first, then rename.
 *   4. Any error during trim is silently swallowed — the append already
 *      succeeded, so the hook result is correct; the file simply gets trimmed
 *      on the next invocation instead.
 *
 * ## Env-var override
 *
 * MAX_SIDECAR_LINES respects the SKILL_SIDECAR_MAX_LINES env var (parsed as
 * int; falls back to 1000 if unset or invalid). This override is for tests.
 *
 * Never throws — sidecar write failures must not break the hook.
 *
 * @param {object} entry       - { session_id, skill, ts, event_count_at_fire }
 * @param {string} sidecarPath
 */
function appendSidecarEntry(entry, sidecarPath) {
  try {
    fs.mkdirSync(path.dirname(sidecarPath), { recursive: true });
    fs.appendFileSync(sidecarPath, `${JSON.stringify(entry)}\n`);
  } catch (_) {
    // Logging must never break the hook. Silently swallow disk errors.
    return;
  }

  // Trim if over bound. Only reads and writes when the limit is exceeded.
  try {
    const raw = fs.readFileSync(sidecarPath, "utf8");
    const lines = raw.split("\n");
    // Drop trailing empty element from split() (a file ending in "\n" produces
    // a trailing empty string). `contentLines` is the canonical content count;
    // we apply the bound to it, not to the raw split length, so the comparison
    // is exact regardless of whether the file ended in "\n" or not.
    const contentLines =
      lines.length > 0 && lines[lines.length - 1] === "" ? lines.slice(0, -1) : lines;

    if (contentLines.length <= MAX_SIDECAR_LINES) return; // within bound — no-op

    const trimmed = `${contentLines.slice(-MAX_SIDECAR_LINES).join("\n")}\n`;
    const tmpPath = `${sidecarPath}.tmp`;
    fs.writeFileSync(tmpPath, trimmed, "utf8");
    atomicRename(tmpPath, sidecarPath);
  } catch (_) {
    // Trim failed — silently swallow. The append already succeeded; the file
    // will be trimmed on a future invocation. Must never throw.
  }
}

/**
 * Atomically replace `destPath` with `tmpPath` via fs.renameSync, handling
 * the Windows-specific EEXIST case where rename fails when the destination
 * already exists.
 *
 * Strategy:
 *   1. Try fs.renameSync directly (POSIX-atomic; works on Windows when dest
 *      doesn't exist).
 *   2. On any failure, unlink the destination and retry once (Windows EEXIST).
 *   3. If the retry also fails, best-effort cleanup of the orphaned .tmp.
 *
 * Never throws — failure is silent. The caller is responsible for the larger
 * try/catch that ensures the hook never breaks. Returns nothing.
 *
 * @param {string} tmpPath
 * @param {string} destPath
 */
function atomicRename(tmpPath, destPath) {
  try {
    fs.renameSync(tmpPath, destPath);
    return;
  } catch (_renameErr) {
    // First attempt failed — Windows EEXIST is the common cause. Try unlink + retry.
  }
  try {
    fs.unlinkSync(destPath);
    fs.renameSync(tmpPath, destPath);
    return;
  } catch (_retryErr) {
    // Retry also failed — best-effort cleanup of the orphaned .tmp.
  }
  try {
    fs.unlinkSync(tmpPath);
  } catch (_cleanupErr) {
    // Ignore — orphaned .tmp will be overwritten on the next trim.
  }
}

/**
 * Build the skill_invocation event object.
 *
 * Async-tolerant: plugin_version may be a string OR a Promise<string>.
 * Tests inject pre-resolved values; production passes the awaited Promise.
 * Same code path either way — await handles both.
 *
 * Sentinel discipline for component version fields:
 * - cv.rev === undefined  → omit skill_rev + skill_content_hash entirely
 * - cv.rev === null       → include both fields with null value
 * - cv.rev is integer     → include both fields with their values
 *
 * @param {object} opts
 * @param {object} opts.tool_input        - Raw tool_input from the hook payload
 * @param {string} opts.session_id        - Session ID from the hook payload
 * @param {string|Promise<string>} opts.plugin_version - Plugin version string or promise
 * @param {Function} [opts.getComponentVersion] - Injected for testing; defaults to real helper
 * @returns {Promise<object>}
 */
async function buildSkillInvocationEvent({
  tool_input,
  session_id,
  plugin_version,
  getComponentVersion: getCV = getComponentVersion,
}) {
  const skill = tool_input?.skill ?? "";
  const cv = getCV(skill, "skill");
  const resolvedPluginVersion = await plugin_version;

  const event = {
    type: "skill_invocation",
    ts: new Date().toISOString(),
    session_id,
    plugin_version: resolvedPluginVersion,
    skill,
  };

  // Sentinel discipline: undefined means "did not try" — omit fields entirely.
  // null means "tried, no value" — include with null value.
  if (cv.rev !== undefined) event.skill_rev = cv.rev;
  if (cv.content_hash !== undefined) event.skill_content_hash = cv.content_hash;

  return event;
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

        // Only handle Skill tool invocations.
        if (input?.tool_name !== "Skill") return;

        const sessionId = input.session_id ?? "";
        const toolInput = input.tool_input ?? {};
        const history = input.conversation_history;

        const event = await buildSkillInvocationEvent({
          tool_input: toolInput,
          session_id: sessionId,
          plugin_version: getPluginVersion(), // Promise — builder awaits internally
        });

        appendLogLine(event, resolveLogPath());

        // Write sidecar entry for dispatch enforcement (closes #65).
        //
        // hooks/check-agent-dispatch-pairing.js (PreToolUse on Agent) reads this
        // sidecar to detect same-turn Skill(dispatch) → Agent sequences.
        // conversation_history contains only COMPLETED turns, so the same-turn
        // dispatch isn't visible there. PostToolUse(Skill) fires before the next
        // tool's PreToolUse, making this sidecar a reliable synchronous signal source.
        //
        // event_count_at_fire: number of tool_use blocks in conversation_history at
        // the time this PostToolUse fires. The consumer uses this to distinguish
        // same-turn sidecar entries (event_count_at_fire >= history length at read time)
        // from prior-turn entries already represented in conversation_history.
        const sidecarEntry = {
          session_id: sessionId,
          skill: toolInput.skill ?? "",
          ts: new Date().toISOString(),
          event_count_at_fire: countToolUseBlocks(history),
        };
        appendSidecarEntry(sidecarEntry, resolveSidecarPath());
      } catch (e) {
        // Parse or runtime error — log to stderr and exit 0; never block the call.
        process.stderr.write(`[log-skill-invocation] error: ${e.message}\n`);
      }
    })();
  });
}

module.exports = {
  buildSkillInvocationEvent,
  countToolUseBlocks,
  appendSidecarEntry,
  resolveSidecarPath,
};
