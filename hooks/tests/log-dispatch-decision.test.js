/**
 * Tests for hooks/log-dispatch-decision.js
 *
 * Covers:
 *   - isDispatchCommand: command filter (accept/reject)
 *   - extractInputFromCommand: echo JSON extraction from command string
 *   - parseDecisionFromOutput: JSON extraction from tool_response
 *   - buildLogEntry: entry type selection (matcher_decision vs matcher_session_id)
 *   - Hook subprocess integration: session_id flows from Bash PostToolUse payload
 *
 * ## Contract-testing note (hook-authoring discipline §1)
 *
 * This hook's correctness depends on two PostToolUse(Bash) payload fields:
 *   - tool_input.command — the Bash command string (sync, reliable)
 *   - tool_response — an OBJECT { stdout, stderr, interrupted, isImage,
 *     noOutputExpected } in the live CC PostToolUse(Bash) payload.
 *     The decision JSON lives at tool_response.stdout.
 *
 * VERIFIED LIVE SHAPE (issue #299 — live payload captured via DISPATCH_HOOK_DEBUG):
 *   tool_response: {
 *     stdout: "<decision JSON string>",
 *     stderr: "",
 *     interrupted: false,
 *     isImage: false,
 *     noOutputExpected: false
 *   }
 *
 * The hook extracts: typeof tr === "string" ? tr : (tr?.stdout ?? null)
 * so it handles both the live object shape (primary) and the legacy string
 * shape (fallback, kept for safety).
 *
 * ## Wiring assumption verified by these tests
 *
 * The hook is wired to PostToolUse(Bash) via hooks.json. Integration tests
 * use the REAL object shape for tool_response (see makeToolResponse helper).
 * The legacy string fallback is covered by one dedicated test labeled
 * "legacy string fallback path".
 *
 * ## Breaking-test discipline (hook-authoring §1)
 *
 * Each critical behaviour has a corresponding negative test (or comment
 * noting which positive test would fail if the behaviour regressed):
 *   - "Bash tool filter" positive test FAILS if tool_name check removed.
 *   - "non-dispatch Bash" negative test FAILS if isDispatchCommand returns true.
 *   - "object-shape tool_response" test FAILS if hook reads object directly instead of .stdout.
 *   - "no-duplicate invariant" test FAILS if hook writes more than one entry.
 *
 * ## De-duplication design (documented here per requirement)
 *
 * The Python matcher _write_log_entry also appends a matcher_decision entry
 * (with session_id="") when DISPATCH_LOG_PATH is set. This hook writes a
 * second entry with:
 *   - session_id: populated from the CC hook payload
 *   - attribution_source: "post_tool_use_hook"
 *
 * Log consumers MUST prefer entries with attribution_source: "post_tool_use_hook"
 * over entries without it (the Python-written entries). This design keeps both
 * writers because the hook cannot suppress the Python write retroactively —
 * the Python subprocess completes before the PostToolUse hook fires.
 *
 * The "exactly one attributed entry per dispatch" invariant is encoded in the
 * "no-duplicate invariant" test below: a single dispatch Bash payload must
 * produce exactly one log entry from the hook.
 */

const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const HOOKS_DIR = path.resolve(__dirname, "..");

function loadHook() {
  return require("../log-dispatch-decision.js");
}

/**
 * Run the hook script as a real subprocess with the given payload.
 *
 * Simulates a PostToolUse(Bash) CC hook invocation. The payload shape
 * mirrors the VERIFIED live CC PostToolUse(Bash) contract:
 *   { tool_name, tool_input, tool_response: { stdout, stderr, ... }, session_id }
 *
 * Callers should build tool_response using makeToolResponse() to ensure
 * the correct object shape. String values are accepted as the legacy
 * fallback path — only use them in tests explicitly labeled "legacy string".
 */
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

// Canonical Bash command for a real-catalog dispatch call.
// Matches the pattern the router generates: echo '<json>' | <python> -m claude_wayfinder dispatch
const DISPATCH_COMMAND =
  "echo '{\"task_description\": \"implement auth\", \"file_paths\": [\"src/auth.py\"]}'" +
  " | \"C:/venv/Scripts/python.exe\" -m claude_wayfinder dispatch";

const SAMPLE_DECISION = {
  decision: "delegate",
  agent: "code-writer",
  confidence: 0.92,
  rationale: "matched keyword: implement",
  alternatives: [],
  disposition_source: "scored",
};
const SAMPLE_SESSION_ID = "test-session-abc-123";

/**
 * Build a tool_response object matching the VERIFIED live CC PostToolUse(Bash)
 * payload shape: { stdout, stderr, interrupted, isImage, noOutputExpected }.
 *
 * The decision JSON lives at .stdout. This is the primary fixture shape for
 * all integration tests. The legacy string fallback is tested separately.
 *
 * CONTRACT ASSUMPTION verified live (issue #299):
 *   tool_response is an object, not a plain string. The hook reads .stdout.
 *
 * @param {string} stdout - Content for stdout (typically the decision JSON).
 * @param {string} [stderr] - Content for stderr (default "").
 * @returns {{ stdout: string, stderr: string, interrupted: boolean, isImage: boolean, noOutputExpected: boolean }}
 */
function makeToolResponse(stdout, stderr = "") {
  return {
    stdout,
    stderr,
    interrupted: false,
    isImage: false,
    noOutputExpected: false,
  };
}

// ---------------------------------------------------------------------------
// isDispatchCommand — command filter
// ---------------------------------------------------------------------------

test("isDispatchCommand: null returns false", () => {
  const { isDispatchCommand } = loadHook();
  assert.equal(isDispatchCommand(null), false);
});

test("isDispatchCommand: empty string returns false", () => {
  const { isDispatchCommand } = loadHook();
  assert.equal(isDispatchCommand(""), false);
});

test("isDispatchCommand: real dispatch command returns true", () => {
  const { isDispatchCommand } = loadHook();
  assert.equal(isDispatchCommand(DISPATCH_COMMAND), true);
});

test("isDispatchCommand: --help form excluded", () => {
  const { isDispatchCommand } = loadHook();
  assert.equal(
    isDispatchCommand("python -m claude_wayfinder dispatch --help"),
    false
  );
});

test("isDispatchCommand: --demo form excluded", () => {
  const { isDispatchCommand } = loadHook();
  assert.equal(
    isDispatchCommand("python -m claude_wayfinder dispatch --demo"),
    false
  );
});

test("isDispatchCommand: catalog build form excluded", () => {
  const { isDispatchCommand } = loadHook();
  assert.equal(
    isDispatchCommand("python -m claude_wayfinder catalog build"),
    false
  );
});

test("isDispatchCommand: unrelated Bash command returns false", () => {
  const { isDispatchCommand } = loadHook();
  assert.equal(isDispatchCommand("git status"), false);
  assert.equal(isDispatchCommand("ls -la"), false);
  assert.equal(isDispatchCommand("node --test hooks/tests/*.test.js"), false);
});

// ---------------------------------------------------------------------------
// extractInputFromCommand — echo JSON extraction
// ---------------------------------------------------------------------------

test("extractInputFromCommand: single-quote echo form returns parsed JSON", () => {
  const { extractInputFromCommand } = loadHook();
  const ctx = { task_description: "implement auth", file_paths: ["src/auth.py"] };
  const cmd = `echo '${JSON.stringify(ctx)}' | python -m claude_wayfinder dispatch`;
  const result = extractInputFromCommand(cmd);
  assert.ok(result !== null);
  assert.equal(result.task_description, "implement auth");
  assert.deepEqual(result.file_paths, ["src/auth.py"]);
});

test("extractInputFromCommand: double-quote echo form returns parsed JSON", () => {
  const { extractInputFromCommand } = loadHook();
  const ctx = { task_description: "fix bug" };
  const cmd = `echo "${JSON.stringify(ctx)}" | python -m claude_wayfinder dispatch`;
  const result = extractInputFromCommand(cmd);
  assert.ok(result !== null);
  assert.equal(result.task_description, "fix bug");
});

test("extractInputFromCommand: command without echo returns null", () => {
  const { extractInputFromCommand } = loadHook();
  const result = extractInputFromCommand(
    "python -m claude_wayfinder dispatch"
  );
  assert.equal(result, null);
});

test("extractInputFromCommand: null input returns null", () => {
  const { extractInputFromCommand } = loadHook();
  assert.equal(extractInputFromCommand(null), null);
});

// ---------------------------------------------------------------------------
// parseDecisionFromOutput — reads tool_response (not tool_output)
//
// CONTRACT ASSUMPTION: CC PostToolUse sends the tool result in tool_response.
// These tests verify the hook parses that field correctly. A live payload
// dump (via DISPATCH_HOOK_DEBUG=1) must confirm the field name.
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
// buildLogEntry
// ---------------------------------------------------------------------------

test("buildLogEntry: decision provided writes matcher_decision entry", () => {
  const { buildLogEntry } = loadHook();
  const entry = buildLogEntry({
    sessionId: SAMPLE_SESSION_ID,
    ts: "2026-05-30T00:00:00.000Z",
    decision: SAMPLE_DECISION,
    inputContext: { task_description: "test" },
    pluginVersion: "1.1.0",
  });
  assert.equal(entry.type, "matcher_decision");
  assert.equal(entry.session_id, SAMPLE_SESSION_ID);
  assert.equal(entry.output.decision, "delegate");
  assert.equal(entry.attribution_source, "post_tool_use_hook");
  assert.deepEqual(entry.input, { task_description: "test" });
});

test("buildLogEntry: null decision writes matcher_session_id entry", () => {
  const { buildLogEntry } = loadHook();
  const entry = buildLogEntry({
    sessionId: SAMPLE_SESSION_ID,
    ts: "2026-05-30T00:00:00.000Z",
    decision: null,
    inputContext: null,
    pluginVersion: "1.1.0",
  });
  assert.equal(entry.type, "matcher_session_id");
  assert.equal(entry.session_id, SAMPLE_SESSION_ID);
  assert.ok(typeof entry.note === "string");
  assert.ok(
    entry.note.indexOf("tool_response") !== -1,
    "fallback note must reference tool_response field name"
  );
});

test("buildLogEntry: empty session_id is written as-is", () => {
  const { buildLogEntry } = loadHook();
  const entry = buildLogEntry({
    sessionId: "",
    ts: "2026-05-30T00:00:00.000Z",
    decision: SAMPLE_DECISION,
    inputContext: null,
    pluginVersion: "1.1.0",
  });
  assert.equal(entry.session_id, "");
});

test("buildLogEntry: null inputContext written as empty object", () => {
  const { buildLogEntry } = loadHook();
  const entry = buildLogEntry({
    sessionId: SAMPLE_SESSION_ID,
    ts: "2026-05-30T00:00:00.000Z",
    decision: SAMPLE_DECISION,
    inputContext: null,
    pluginVersion: "1.1.0",
  });
  assert.deepEqual(entry.input, {});
});

// ---------------------------------------------------------------------------
// Hook subprocess integration tests — Bash PostToolUse wiring
//
// These tests verify:
//   1. The hook fires correctly on Bash(dispatch) payloads.
//   2. The hook reads session_id from the payload.
//   3. The hook reads tool_response.stdout (object shape) for the decision.
//   4. The hook does NOT fire on Skill payloads (wrong event — old wiring).
//   5. The hook does NOT fire on non-dispatch Bash commands.
//   6. Exactly ONE log entry per dispatch (no-duplicate invariant).
//
// Breaking-test targets:
//   Test "Bash dispatch: object-shape tool_response produces matcher_decision"
//     → FAILS if hook reads tool_response as-is (object) instead of .stdout
//     → FAILS if Bash tool_name check removed (hook would also fire on non-Bash)
//   Test "Skill payload: no log write (old wiring regression)"
//     → FAILS if hook is re-wired back to Skill
//   Test "no-duplicate invariant: exactly one hook entry per dispatch"
//     → FAILS if hook writes more than one entry per invocation
// ---------------------------------------------------------------------------

test("Bash dispatch: object-shape tool_response produces matcher_decision with session_id", () => {
  // PRIMARY CONTRACT TEST: tool_response is an OBJECT in the live CC payload.
  // The decision JSON lives at tool_response.stdout.
  // VERIFIED LIVE (issue #299): { stdout: "<json>", stderr: "", interrupted: false, ... }
  //
  // BREAKING BEHAVIOR: if this test produces type=matcher_session_id, the hook
  // is reading tool_response directly (object) instead of tool_response.stdout.
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "log-dispatch-test-"));
  const logPath = path.join(tmpDir, "dispatch-log.jsonl");
  const payload = {
    tool_name: "Bash",
    tool_input: { command: DISPATCH_COMMAND },
    // Real CC PostToolUse(Bash) object shape — decision JSON is at .stdout.
    tool_response: makeToolResponse(JSON.stringify(SAMPLE_DECISION)),
    session_id: SAMPLE_SESSION_ID,
  };
  const result = runHook(payload, { DISPATCH_LOG_PATH: logPath });
  assert.equal(result.status, 0, "exit 0: " + result.stderr);
  assert.ok(fs.existsSync(logPath), "log file created");
  const entry = JSON.parse(fs.readFileSync(logPath, "utf8").trim());
  assert.equal(
    entry.type,
    "matcher_decision",
    "object-shape tool_response: decision must parse from .stdout; " +
    "if type is matcher_session_id the hook is reading the object directly"
  );
  assert.equal(
    entry.session_id,
    SAMPLE_SESSION_ID,
    "session_id must be populated from CC payload, not empty string"
  );
  assert.equal(entry.attribution_source, "post_tool_use_hook");
  fs.rmSync(tmpDir, { recursive: true, force: true });
});

test("Bash dispatch: legacy string tool_response fallback path still parses decision", () => {
  // FALLBACK PATH (not the primary live shape): if tool_response arrives as a
  // plain string (legacy or non-Bash tool), the hook must still extract the
  // decision JSON from the string directly. This is a safety fallback — the
  // primary live shape is the object (see test above).
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "log-dispatch-test-"));
  const logPath = path.join(tmpDir, "dispatch-log.jsonl");
  const payload = {
    tool_name: "Bash",
    tool_input: { command: DISPATCH_COMMAND },
    // Legacy string shape — not the live CC shape, kept as a fallback guard.
    tool_response: JSON.stringify(SAMPLE_DECISION),
    session_id: SAMPLE_SESSION_ID,
  };
  const result = runHook(payload, { DISPATCH_LOG_PATH: logPath });
  assert.equal(result.status, 0, "exit 0 on legacy string shape: " + result.stderr);
  assert.ok(fs.existsSync(logPath), "log file created for legacy string path");
  const entry = JSON.parse(fs.readFileSync(logPath, "utf8").trim());
  assert.equal(
    entry.type,
    "matcher_decision",
    "legacy string fallback: decision must parse from the string directly"
  );
  assert.equal(entry.session_id, SAMPLE_SESSION_ID);
  assert.equal(entry.attribution_source, "post_tool_use_hook");
  fs.rmSync(tmpDir, { recursive: true, force: true });
});

test("Bash dispatch: tool_output field (wrong field) produces no decision — regression guard", () => {
  // This test verifies what happens when the WRONG field is read.
  // A payload with tool_output but NO tool_response should produce
  // a matcher_session_id entry (decision=null fallback), not a matcher_decision.
  // This confirms parseDecisionFromOutput correctly reads tool_response, not tool_output.
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "log-dispatch-test-"));
  const logPath = path.join(tmpDir, "dispatch-log.jsonl");
  const payload = {
    tool_name: "Bash",
    tool_input: { command: DISPATCH_COMMAND },
    // Intentionally put decision in tool_output (wrong field), not tool_response.
    tool_output: JSON.stringify(SAMPLE_DECISION),
    // tool_response is absent — hook should read undefined → null decision.
    session_id: SAMPLE_SESSION_ID,
  };
  const result = runHook(payload, { DISPATCH_LOG_PATH: logPath });
  assert.equal(result.status, 0);
  assert.ok(fs.existsSync(logPath), "log file created (fallback path)");
  const entry = JSON.parse(fs.readFileSync(logPath, "utf8").trim());
  // With tool_response absent, decision is null → fallback entry type.
  assert.equal(
    entry.type,
    "matcher_session_id",
    "when tool_response is absent the hook must use the fallback path, " +
    "not silently read tool_output"
  );
  assert.equal(entry.session_id, SAMPLE_SESSION_ID);
  fs.rmSync(tmpDir, { recursive: true, force: true });
});

test("Bash dispatch: tool_response absent (null) writes matcher_session_id fallback", () => {
  // Graceful/partial path: tool_response not present at all → decision=null fallback.
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "log-dispatch-test-"));
  const logPath = path.join(tmpDir, "dispatch-log.jsonl");
  const payload = {
    tool_name: "Bash",
    tool_input: { command: DISPATCH_COMMAND },
    // tool_response not present — simulates a hook misconfiguration or unexpected shape
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

test("Bash dispatch: object-shape tool_response with non-JSON stdout writes matcher_session_id fallback", () => {
  // Graceful/partial path: tool_response is an object but stdout is not parseable
  // as decision JSON → hook falls back to matcher_session_id.
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "log-dispatch-test-"));
  const logPath = path.join(tmpDir, "dispatch-log.jsonl");
  const payload = {
    tool_name: "Bash",
    tool_input: { command: DISPATCH_COMMAND },
    // Object shape but stdout is not decision JSON (e.g. error output).
    tool_response: makeToolResponse("Error: catalog file not found"),
    session_id: SAMPLE_SESSION_ID,
  };
  const result = runHook(payload, { DISPATCH_LOG_PATH: logPath });
  assert.equal(result.status, 0, "exit 0 on unparseable stdout: " + result.stderr);
  assert.ok(fs.existsSync(logPath), "log file created in partial fallback path");
  const entry = JSON.parse(fs.readFileSync(logPath, "utf8").trim());
  assert.equal(
    entry.type,
    "matcher_session_id",
    "unparseable stdout must produce matcher_session_id fallback, not crash"
  );
  assert.equal(entry.session_id, SAMPLE_SESSION_ID);
  assert.ok(typeof entry.note === "string", "fallback note must be present");
  fs.rmSync(tmpDir, { recursive: true, force: true });
});

test("Skill payload: no log write (old wiring regression guard)", () => {
  // BREAKING TEST: if hook is re-wired back to Skill(dispatch), this test fails.
  // The hook must now filter on Bash — Skill payloads must produce no log entry.
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "log-dispatch-test-"));
  const logPath = path.join(tmpDir, "dispatch-log.jsonl");
  const payload = {
    // Old (broken) wiring: Skill tool with dispatch skill name.
    tool_name: "Skill",
    tool_input: { skill: "dispatch" },
    tool_response: JSON.stringify(SAMPLE_DECISION),
    session_id: SAMPLE_SESSION_ID,
  };
  const result = runHook(payload, { DISPATCH_LOG_PATH: logPath });
  assert.equal(result.status, 0);
  assert.ok(
    !fs.existsSync(logPath),
    "no log for Skill(dispatch) payload — hook must only fire on Bash; " +
    "if this fails the hook was re-wired to the wrong event (old bug #299)"
  );
  fs.rmSync(tmpDir, { recursive: true, force: true });
});

test("non-dispatch Bash command: no log write", () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "log-dispatch-test-"));
  const logPath = path.join(tmpDir, "dispatch-log.jsonl");
  const payload = {
    tool_name: "Bash",
    tool_input: { command: "git status" },
    tool_response: "On branch main",
    session_id: SAMPLE_SESSION_ID,
  };
  const result = runHook(payload, { DISPATCH_LOG_PATH: logPath });
  assert.equal(result.status, 0);
  assert.ok(!fs.existsSync(logPath), "no log for non-dispatch bash command");
  fs.rmSync(tmpDir, { recursive: true, force: true });
});

test("--demo dispatch command: no log write (excluded form)", () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "log-dispatch-test-"));
  const logPath = path.join(tmpDir, "dispatch-log.jsonl");
  const payload = {
    tool_name: "Bash",
    tool_input: { command: "python -m claude_wayfinder dispatch --demo" },
    tool_response: "demo mode output",
    session_id: SAMPLE_SESSION_ID,
  };
  const result = runHook(payload, { DISPATCH_LOG_PATH: logPath });
  assert.equal(result.status, 0);
  assert.ok(!fs.existsSync(logPath), "no log for --demo dispatch");
  fs.rmSync(tmpDir, { recursive: true, force: true });
});

test("--help dispatch command: no log write (excluded form)", () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "log-dispatch-test-"));
  const logPath = path.join(tmpDir, "dispatch-log.jsonl");
  const payload = {
    tool_name: "Bash",
    tool_input: { command: "python -m claude_wayfinder dispatch --help" },
    tool_response: "usage: ...",
    session_id: SAMPLE_SESSION_ID,
  };
  const result = runHook(payload, { DISPATCH_LOG_PATH: logPath });
  assert.equal(result.status, 0);
  assert.ok(!fs.existsSync(logPath), "no log for --help dispatch");
  fs.rmSync(tmpDir, { recursive: true, force: true });
});

test("Agent payload: no log write (wrong tool)", () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "log-dispatch-test-"));
  const logPath = path.join(tmpDir, "dispatch-log.jsonl");
  const payload = {
    tool_name: "Agent",
    tool_input: { subagent_type: "code-writer" },
    tool_response: "agent output",
    session_id: SAMPLE_SESSION_ID,
  };
  const result = runHook(payload, { DISPATCH_LOG_PATH: logPath });
  assert.equal(result.status, 0);
  assert.ok(!fs.existsSync(logPath), "no log for non-Bash tool");
  fs.rmSync(tmpDir, { recursive: true, force: true });
});

test("no-duplicate invariant: exactly one hook entry per dispatch call", () => {
  // De-duplication design: the hook writes exactly ONE entry per invocation.
  // The Python matcher may also write an entry (with session_id="", no
  // attribution_source). Log consumers prefer the hook entry.
  // This test verifies the hook itself does not write multiple entries.
  // Uses the real object shape for tool_response.
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "log-dispatch-test-"));
  const logPath = path.join(tmpDir, "dispatch-log.jsonl");
  const payload = {
    tool_name: "Bash",
    tool_input: { command: DISPATCH_COMMAND },
    // Real CC PostToolUse(Bash) object shape.
    tool_response: makeToolResponse(JSON.stringify(SAMPLE_DECISION)),
    session_id: SAMPLE_SESSION_ID,
  };
  runHook(payload, { DISPATCH_LOG_PATH: logPath });
  assert.ok(fs.existsSync(logPath), "log file created");
  const lines = fs.readFileSync(logPath, "utf8")
    .split("\n")
    .filter((l) => l.trim() !== "");
  assert.equal(
    lines.length,
    1,
    "exactly one entry per dispatch — hook must not write duplicate entries; " +
    "if this fails the hook is writing multiple entries per invocation"
  );
  const entry = JSON.parse(lines[0]);
  assert.equal(entry.attribution_source, "post_tool_use_hook");
  fs.rmSync(tmpDir, { recursive: true, force: true });
});

test("input_context extracted from echo command appears in log entry", () => {
  // Uses real object shape for tool_response.
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "log-dispatch-test-"));
  const logPath = path.join(tmpDir, "dispatch-log.jsonl");
  const inputCtx = { task_description: "implement auth", file_paths: ["src/auth.py"] };
  const command =
    `echo '${JSON.stringify(inputCtx)}' | "C:/venv/Scripts/python.exe" -m claude_wayfinder dispatch`;
  const payload = {
    tool_name: "Bash",
    tool_input: { command },
    // Real CC PostToolUse(Bash) object shape.
    tool_response: makeToolResponse(JSON.stringify(SAMPLE_DECISION)),
    session_id: SAMPLE_SESSION_ID,
  };
  const result = runHook(payload, { DISPATCH_LOG_PATH: logPath });
  assert.equal(result.status, 0);
  const entry = JSON.parse(fs.readFileSync(logPath, "utf8").trim());
  assert.equal(entry.type, "matcher_decision");
  assert.equal(entry.input.task_description, "implement auth");
  assert.deepEqual(entry.input.file_paths, ["src/auth.py"]);
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

// ---------------------------------------------------------------------------
// Issue #311 regression: catalog_hash / matcher_version propagation
//
// The JS hook reads decision.catalog_hash and decision.matcher_version via
// the null-coalescing pattern:
//   catalog_hash: decision.catalog_hash ?? null
//   matcher_version: decision.matcher_version ?? null
//
// Before the fix, the Python matcher omitted these fields from its stdout
// JSON, so the hook always wrote null. After the fix, these fields are
// present in the Python stdout and the hook writes them into the attributed
// log row — no hook logic change required.
//
// This test guards the end-to-end contract: when the parsed decision carries
// catalog_hash / matcher_version, the hook's written entry must carry them
// too (not null). If the hook is ever changed to stop reading these fields,
// this test will fail.
// ---------------------------------------------------------------------------

test("issue #311: catalog_hash from decision JSON flows into hook log entry", () => {
  // Simulate the enriched Python stdout after the fix: decision JSON now
  // includes catalog_hash and matcher_version.
  const ENRICHED_DECISION = {
    decision: "delegate",
    agent: "code-writer",
    confidence: 0.92,
    rationale: "matched keyword: implement",
    alternatives: [],
    disposition_source: "scored",
    catalog_hash: "sha256:abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
    matcher_version: "abc1234",
  };

  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "log-dispatch-311-"));
  const logPath = path.join(tmpDir, "dispatch-log.jsonl");
  const payload = {
    tool_name: "Bash",
    tool_input: { command: DISPATCH_COMMAND },
    tool_response: makeToolResponse(JSON.stringify(ENRICHED_DECISION)),
    session_id: SAMPLE_SESSION_ID,
  };
  const result = runHook(payload, { DISPATCH_LOG_PATH: logPath });
  assert.equal(result.status, 0, "exit 0: " + result.stderr);
  assert.ok(fs.existsSync(logPath), "log file must be created");
  const entry = JSON.parse(fs.readFileSync(logPath, "utf8").trim());
  assert.equal(entry.type, "matcher_decision", "must be a matcher_decision entry");
  // Core #311 contract: catalog_hash must flow from decision into the log entry.
  assert.equal(
    entry.catalog_hash,
    ENRICHED_DECISION.catalog_hash,
    "catalog_hash must be copied from decision into hook log entry (issue #311); " +
    "if null, the Python matcher is not including it in stdout"
  );
  // Core #311 contract: matcher_version must flow from decision into the log entry.
  assert.equal(
    entry.matcher_version,
    ENRICHED_DECISION.matcher_version,
    "matcher_version must be copied from decision into hook log entry (issue #311); " +
    "if null, the Python matcher is not including it in stdout"
  );
  fs.rmSync(tmpDir, { recursive: true, force: true });
});

test("issue #311: catalog_hash null when decision omits it (pre-fix guard)", () => {
  // Verify the baseline behaviour: when decision has no catalog_hash
  // (the old Python output), the hook writes null (not crashing, not
  // fabricating a value). This guards the null-coalescing logic.
  const LEGACY_DECISION = {
    decision: "delegate",
    agent: "code-writer",
    confidence: 0.92,
    rationale: "matched keyword: implement",
    alternatives: [],
    disposition_source: "scored",
    // No catalog_hash or matcher_version — simulates pre-fix Python output.
  };

  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "log-dispatch-311b-"));
  const logPath = path.join(tmpDir, "dispatch-log.jsonl");
  const payload = {
    tool_name: "Bash",
    tool_input: { command: DISPATCH_COMMAND },
    tool_response: makeToolResponse(JSON.stringify(LEGACY_DECISION)),
    session_id: SAMPLE_SESSION_ID,
  };
  const result = runHook(payload, { DISPATCH_LOG_PATH: logPath });
  assert.equal(result.status, 0, "exit 0 on legacy decision: " + result.stderr);
  assert.ok(fs.existsSync(logPath), "log file must be created");
  const entry = JSON.parse(fs.readFileSync(logPath, "utf8").trim());
  assert.equal(entry.type, "matcher_decision");
  // Without the fix: hook must write null (not fabricate a value).
  assert.equal(
    entry.catalog_hash,
    null,
    "catalog_hash must be null when decision omits it (null-coalescing guard)"
  );
  assert.equal(
    entry.matcher_version,
    null,
    "matcher_version must be null when decision omits it (null-coalescing guard)"
  );
  fs.rmSync(tmpDir, { recursive: true, force: true });
});

// ---------------------------------------------------------------------------
// Issue #345 security: DISPATCH_HOOK_DEBUG dump must land in a private dir
//
// CONTRACT ASSUMPTION this test verifies:
//   When DISPATCH_HOOK_DEBUG=1, the hook writes the payload dump to the
//   directory specified by CLAUDE_PLUGIN_DATA (or the ~/.claude/state/wayfinder-debug
//   fallback), NOT to os.tmpdir(). This is the cross-platform-observable
//   assertion: the redirect is testable on all platforms including Windows.
//
// On POSIX: additionally asserts the dump file has no group/other read bits
//   (mode & 0o077 === 0). This assertion is gated on process.platform !== "win32"
//   because Windows chmod is a partial no-op (the redirect provides privacy there).
//
// Breaking-test discipline: run with pre-fix code (dump goes to os.tmpdir())
//   → this test FAILS because the dump file is not found under CLAUDE_PLUGIN_DATA.
// ---------------------------------------------------------------------------

test("issue #345: DISPATCH_HOOK_DEBUG dump lands in CLAUDE_PLUGIN_DATA, not os.tmpdir()", () => {
  // Use a fresh temp dir as the private plugin data dir.
  // Must be distinct from os.tmpdir() — the test asserts the file is NOT in tmpdir
  // directly (it may be a subdir of tmpdir, which is fine; the key is the file
  // must be inside CLAUDE_PLUGIN_DATA, not a flat child of os.tmpdir()).
  const privateDataDir = fs.mkdtempSync(path.join(os.tmpdir(), "345-plugin-data-"));
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "345-dispatch-log-"));
  const logPath = path.join(tmpDir, "dispatch-log.jsonl");
  try {
    const payload = {
      tool_name: "Bash",
      tool_input: { command: DISPATCH_COMMAND },
      tool_response: makeToolResponse(JSON.stringify(SAMPLE_DECISION)),
      session_id: SAMPLE_SESSION_ID,
    };

    const result = runHook(payload, {
      DISPATCH_HOOK_DEBUG: "1",
      CLAUDE_PLUGIN_DATA: privateDataDir,
      // Isolate the normal log; this test only asserts the debug dump location.
      DISPATCH_LOG_PATH: logPath,
    });
    assert.equal(result.status, 0, "hook must exit 0 with debug enabled: " + result.stderr);

    // The stderr must contain the dump path announcement.
    assert.ok(
      result.stderr.includes("[log-dispatch-decision] DEBUG payload dump:"),
      "stderr must contain dump path announcement"
    );

    // Parse the dump file path from stderr.
    const dumpPathMatch = result.stderr.match(/DEBUG payload dump: (.+\.json)/);
    assert.ok(
      dumpPathMatch !== null,
      "must be able to parse dump path from stderr: " + result.stderr
    );
    const dumpFile = dumpPathMatch[1].trim();

    // PRIMARY ASSERTION (cross-platform): dump file is inside CLAUDE_PLUGIN_DATA.
    // path.relative() returns a path that doesn't start with ".." iff dumpFile
    // is under privateDataDir.
    const rel = path.relative(privateDataDir, dumpFile);
    assert.ok(
      !rel.startsWith("..") && !path.isAbsolute(rel),
      `dump file must be under CLAUDE_PLUGIN_DATA (${privateDataDir}), got: ${dumpFile}`
    );

    // PRIMARY ASSERTION (cross-platform): dump file must NOT be a direct child of
    // os.tmpdir(). (privateDataDir is a subdirectory of tmpdir, so the file is
    // transitively inside tmpdir — that's fine. The vuln was writing directly to
    // tmpdir/<filename>.json, world-readable on POSIX. We check the immediate parent.)
    const dumpParent = path.dirname(path.resolve(dumpFile));
    const resolvedTmpdir = path.resolve(os.tmpdir());
    assert.ok(
      dumpParent !== resolvedTmpdir,
      `dump file must NOT be a direct child of os.tmpdir() — found: ${dumpFile}. ` +
      "Pre-fix behavior: fs.writeFileSync to os.tmpdir() is world-readable on POSIX."
    );

    // SECONDARY ASSERTION (POSIX only): file mode must have no group/other access bits.
    // On Windows, chmod is a partial no-op; the redirect to a per-user dir provides
    // the privacy guarantee instead.
    if (process.platform !== "win32") {
      assert.ok(
        fs.existsSync(dumpFile),
        "dump file must exist on POSIX to check permissions"
      );
      const stat = fs.statSync(dumpFile);
      const otherBits = stat.mode & 0o077;
      assert.equal(
        otherBits,
        0,
        `dump file must have no group/other access bits (0600); got mode ${(stat.mode & 0o777).toString(8)}`
      );
    }

    // Sanity check: dump file is valid JSON and contains the hook's input payload.
    const dumpContent = JSON.parse(fs.readFileSync(dumpFile, "utf8"));
    assert.equal(
      dumpContent.session_id,
      SAMPLE_SESSION_ID,
      "dump must contain the hook's input payload (session_id mismatch)"
    );
  } finally {
    // Always clean up the private dir, even if the test fails.
    fs.rmSync(privateDataDir, { recursive: true, force: true });
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});
