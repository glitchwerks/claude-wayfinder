const os = require("node:os");
const path = require("node:path");
const parseInput = require("./parse-input");
const { extractSkillsFromPrompt, appendLogLine } = require("./lib/dispatch-log");
const { getPluginVersion } = require("./lib/plugin-version");
const { getComponentVersion } = require("./lib/component-version");

/** Default log path — can be overridden in tests via DISPATCH_LOG_PATH. */
function resolveLogPath() {
  if (process.env.DISPATCH_LOG_PATH) return process.env.DISPATCH_LOG_PATH;
  const home = process.env.HOME || process.env.USERPROFILE || os.homedir();
  return path.join(home, ".claude", "state", "dispatch-log.jsonl");
}

/**
 * Collapse newlines to spaces and truncate to 200 characters.
 *
 * @param {string} text
 * @returns {string}
 */
function makeExcerpt(text) {
  if (!text || typeof text !== "string") return "";
  return text.replace(/[\r\n]+/g, " ").slice(0, 200);
}

/**
 * Build the agent_dispatch event object.
 *
 * Async-tolerant: plugin_version may be a string OR a Promise<string>.
 * Tests inject pre-resolved values; production passes the awaited Promise.
 * Same code path either way — await handles both.
 *
 * Sentinel discipline for component version fields:
 * - cv.rev === undefined  → omit agent_rev + agent_content_hash entirely
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
async function buildAgentDispatchEvent({
  tool_input,
  session_id,
  plugin_version,
  getComponentVersion: getCV = getComponentVersion,
}) {
  const agent = tool_input?.subagent_type ?? "unknown";
  const prompt = tool_input?.prompt ?? "";
  const cv = getCV(agent, "agent");
  const resolvedPluginVersion = await plugin_version;

  const event = {
    type: "agent_dispatch",
    ts: new Date().toISOString(),
    session_id,
    agent,
    plugin_version: resolvedPluginVersion,
    skills_in_prompt: extractSkillsFromPrompt(prompt),
    task_excerpt: makeExcerpt(prompt),
  };

  // Sentinel discipline: undefined means "did not try" — omit fields entirely.
  // null means "tried, no value" — include with null value.
  if (cv.rev !== undefined) event.agent_rev = cv.rev;
  if (cv.content_hash !== undefined) event.agent_content_hash = cv.content_hash;

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

        // Only handle Agent tool invocations.
        if (input?.tool_name !== "Agent") return;

        const event = await buildAgentDispatchEvent({
          tool_input: input.tool_input ?? {},
          session_id: input.session_id ?? "",
          plugin_version: getPluginVersion(), // Promise — builder awaits internally
        });

        appendLogLine(event, resolveLogPath());
      } catch (e) {
        // Parse or runtime error — log to stderr and exit 0; never block dispatch.
        process.stderr.write(`[log-agent-dispatch] error: ${e.message}\n`);
      }
    })();
  });
}

module.exports = { buildAgentDispatchEvent };
