/**
 * Tests for hooks/log-dispatch-decision.js
 *
 * Covers:
 *   - parseDecisionFromOutput: JSON extraction from tool_output
 *   - buildLogEntry: entry type selection (matcher_decision vs matcher_session_id)
 *   - tryParseArgs: tool_input.args parsing
 *   - Hook subprocess integration: session_id flows from payload to log entry
 *
 * ## Contract-testing note (hook-authoring discipline §1)
 *
 * This hook's correctness depends on the PostToolUse `tool_output` field shape
 * for Skill(dispatch) calls, which is not formally documented. Per hook-authoring
 * §1, this requires a live integration test. The subprocess tests below are the
 * closest approximation to a live CC session achievable in a test runner:
 * they run the actual hook script as a child process with realistic payloads.
 *
 * What is NOT verified here (requires a real CC session):
 *   The actual `tool_output` field shape Claude Code sends in PostToolUse for
 *   Skill(dispatch) calls. The hook handles both "pure JSON" and "JSON embedded
 *   in text" cases, and falls back to `matcher_session_id` when neither matches.
 */

const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const HOOKS_DIR = path.resolve(__dirname, "..");

// Lazy-load hook module — fails clearly if the file doesn't exist.
function loadHook() {
  return require("../log-dispatch-decision.js");
}

// Run the hook script as a real subprocess with the given payload.
function runHook(payload, env = {}) {
  const scriptPath = path.join(HOOKS_DIR, "log-dispatch-decision.js");
  const result = spawnSync(process.execPath, [scriptPath], {
    input: JSON.stringify(payload),
    encoding: "utf8",
    timeout: 8000,
    env: { ...process.env, ...env },
  });
  return {
    stdout: result.stdout ?? "",
    stderr: result.stderr ?? "",
    status: result.status ?? 0,
  };
}

const SAMPLE_DECISION = {
  decision: "delegate",
  agent: "code-writer",
  confidence: 0.92,
  rationale: "matched keyword: implement",
  alternatives: [],
  disposition_source: "scored",
};
const SAMPLE_SESSION_ID = "test-session-abc-123";

// ---------------------------------------------------------------------------
// parseDecisionFromOutput — pure JSON path
// ---------------------------------------------------------------------------

test("parseDecisionFromOutput: null input returns null", () => {
  const { parseDecisionFromOutput } = loadHook();
  assert.equal(parseDecisionFromOutput(null), null);
});

test("parseDecisionFromOutput: empty string returns null", () => {
  const { parseDecisionFromOutput } = loadHook();
  assert.equal(parseDecisionFromOutput(""), null);
});

test("parseDecisionFromOutput: non-string returns null", () => {
  const { parseDecisionFromOutput } = loadHook();
  assert.equal(parseDecisionFromOutput(42), null);
  assert.equal(parseDecisionFromOutput({}), null);
});

test("parseDecisionFromOutput: pure JSON with decision field returns object", () => {
  const { parseDecisionFromOutput } = loadHook();
  const result = parseDecisionFromOutput(JSON.stringify(SAMPLE_DECISION));
  assert.ok(result !== null);
  assert.equal(result.decision, "delegate");
  assert.equal(result.agent, "code-writer");
});

test("parseDecisionFromOutput: JSON without decision field returns null", () => {
  const { parseDecisionFromOutput } = loadHook();
  assert.equal(
    parseDecisionFromOutput(JSON.stringify({ agent: "code-writer", confidence: 0.9 })),
    null
  );
});

test("parseDecisionFromOutput: JSON embedded in preamble text returns decision", () => {
  const { parseDecisionFromOutput } = loadHook();
  const input = "[dispatch] overrides: 0 rules loaded\n" + JSON.stringify(SAMPLE_DECISION);
  const result = parseDecisionFromOutput(input);
  assert.ok(result !== null);
  assert.equal(result.decision, "delegate");
});

test("parseDecisionFromOutput: malformed JSON returns null", () => {
  const { parseDecisionFromOutput } = loadHook();
  assert.equal(parseDecisionFromOutput("{not valid json"), null);
});

// ---------------------------------------------------------------------------
// tryParseArgs
// ---------------------------------------------------------------------------

test("tryParseArgs: null returns empty object", () => {
  const { tryParseArgs } = loadHook();
  assert.deepEqual(tryParseArgs(null), {});
});

test("tryParseArgs: tool_input.args parsed as dispatch context JSON", () => {
  const { tryParseArgs } = loadHook();
  const context = { task_description: "implement auth" };
  assert.deepEqual(tryParseArgs({ skill: "dispatch", args: JSON.stringify(context) }), context);
});

test("tryParseArgs: non-JSON args returns empty object", () => {
  const { tryParseArgs } = loadHook();
  assert.deepEqual(tryParseArgs({ skill: "dispatch", args: "--demo --verbose" }), {});
});

// ---------------------------------------------------------------------------
// buildLogEntry
// ---------------------------------------------------------------------------

test("buildLogEntry: decision provided writes matcher_decision entry", () => {
  const { buildLogEntry } = loadHook();
  const entry = buildLogEntry({
    sessionId: SAMPLE_SESSION_ID,
    ts: "2026-05-30T00:00:00.000Z",
    decision: SAMPLE_DECISION,
    toolInput: {},
    pluginVersion: "1.1.0",
  });
  assert.equal(entry.type, "matcher_decision");
  assert.equal(entry.session_id, SAMPLE_SESSION_ID);
  assert.equal(entry.output.decision, "delegate");
  assert.equal(entry.attribution_source, "post_tool_use_hook");
});

test("buildLogEntry: null decision writes matcher_session_id entry", () => {
  const { buildLogEntry } = loadHook();
  const entry = buildLogEntry({
    sessionId: SAMPLE_SESSION_ID,
    ts: "2026-05-30T00:00:00.000Z",
    decision: null,
    toolInput: {},
    pluginVersion: "1.1.0",
  });
  assert.equal(entry.type, "matcher_session_id");
  assert.equal(entry.session_id, SAMPLE_SESSION_ID);
  assert.ok(typeof entry.note === "string");
});

test("buildLogEntry: empty session_id is written as-is", () => {
  const { buildLogEntry } = loadHook();
  const entry = buildLogEntry({
    sessionId: "",
    ts: "2026-05-30T00:00:00.000Z",
    decision: SAMPLE_DECISION,
    toolInput: {},
    pluginVersion: "1.1.0",
  });
  assert.equal(entry.session_id, "");
});

// ---------------------------------------------------------------------------
// Hook subprocess integration tests
//
// These run the actual hook script as a subprocess with realistic PostToolUse
// payloads. This is the closest approximation to a live CC session achievable
// in a test runner.
//
// Breaking-test discipline (hook-authoring §1):
// - "session_id written" FAILS if skill filter is removed or inverted.
// - "non-dispatch skill no log" FAILS if all skills are handled.
// Combined, they verify the skill filter is exercised both ways.
// ---------------------------------------------------------------------------

test("hook subprocess: session_id in log when Skill(dispatch) with JSON output", () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "log-dispatch-test-"));
  const logPath = path.join(tmpDir, "dispatch-log.jsonl");
  const payload = {
    tool_name: "Skill",
    tool_input: { skill: "dispatch", args: JSON.stringify({ task_description: "test" }) },
    tool_output: JSON.stringify(SAMPLE_DECISION),
    session_id: SAMPLE_SESSION_ID,
    conversation_history: [],
  };
  const result = runHook(payload, { DISPATCH_LOG_PATH: logPath });
  assert.equal(result.status, 0, "exit 0: " + result.stderr);
  assert.ok(fs.existsSync(logPath), "log file created");
  const entry = JSON.parse(fs.readFileSync(logPath, "utf8").trim());
  assert.equal(entry.type, "matcher_decision");
  assert.equal(entry.session_id, SAMPLE_SESSION_ID,
    "session_id must be populated from CC payload, not empty");
  assert.equal(entry.attribution_source, "post_tool_use_hook");
  fs.rmSync(tmpDir, { recursive: true, force: true });
});

test("hook subprocess: session_id in log when tool_output absent (fallback path)", () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "log-dispatch-test-"));
  const logPath = path.join(tmpDir, "dispatch-log.jsonl");
  const payload = {
    tool_name: "Skill",
    tool_input: { skill: "dispatch" },
    session_id: SAMPLE_SESSION_ID,
  };
  const result = runHook(payload, { DISPATCH_LOG_PATH: logPath });
  assert.equal(result.status, 0);
  assert.ok(fs.existsSync(logPath), "log file created even in fallback path");
  const entry = JSON.parse(fs.readFileSync(logPath, "utf8").trim());
  assert.equal(entry.type, "matcher_session_id");
  assert.equal(entry.session_id, SAMPLE_SESSION_ID);
  fs.rmSync(tmpDir, { recursive: true, force: true });
});

test("hook subprocess: non-dispatch Skill produces no log write", () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "log-dispatch-test-"));
  const logPath = path.join(tmpDir, "dispatch-log.jsonl");
  const payload = {
    tool_name: "Skill",
    tool_input: { skill: "python" },
    tool_output: "skill output",
    session_id: SAMPLE_SESSION_ID,
  };
  const result = runHook(payload, { DISPATCH_LOG_PATH: logPath });
  assert.equal(result.status, 0);
  assert.ok(!fs.existsSync(logPath), "no log for non-dispatch skill");
  fs.rmSync(tmpDir, { recursive: true, force: true });
});

test("hook subprocess: non-Skill tool produces no log write", () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "log-dispatch-test-"));
  const logPath = path.join(tmpDir, "dispatch-log.jsonl");
  const payload = {
    tool_name: "Agent",
    tool_input: { subagent_type: "code-writer" },
    session_id: SAMPLE_SESSION_ID,
  };
  const result = runHook(payload, { DISPATCH_LOG_PATH: logPath });
  assert.equal(result.status, 0);
  assert.ok(!fs.existsSync(logPath), "no log for non-Skill tool");
  fs.rmSync(tmpDir, { recursive: true, force: true });
});

test("hook subprocess: malformed stdin exits 0 (fail-open)", () => {
  const scriptPath = path.join(HOOKS_DIR, "log-dispatch-decision.js");
  const result = spawnSync(process.execPath, [scriptPath], {
    input: "not valid json {{{",
    encoding: "utf8",
    timeout: 8000,
    env: { ...process.env },
  });
  assert.equal(result.status, 0, "must exit 0 on malformed stdin");
});

test("hook subprocess: empty stdin exits 0 (fail-open)", () => {
  const scriptPath = path.join(HOOKS_DIR, "log-dispatch-decision.js");
  const result = spawnSync(process.execPath, [scriptPath], {
    input: "",
    encoding: "utf8",
    timeout: 8000,
    env: { ...process.env },
  });
  assert.equal(result.status, 0, "must exit 0 on empty stdin");
});
