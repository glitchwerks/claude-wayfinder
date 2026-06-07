/**
 * Tests for hooks/check-agent-dispatch-pairing.js
 *
 * Written before implementation (TDD — red phase).
 *
 * The hook is a PreToolUse floor observer that fires on Agent tool calls.
 * It scans conversation history to classify the dispatch as one of:
 *   - router_mediated  — follows a dispatch Skill decision (no event written)
 *   - skill_mediated   — follows a non-dispatch Skill invocation (informational event)
 *   - bypass           — no dispatch or Skill authorization at all (drift event)
 *   - stale_dispatch   — dispatch present but too many tool events elapsed (drift event)
 *
 * Acceptance criteria (issue #200):
 *   - Never blocks (always exits 0)
 *   - Never augments tool input
 *   - Writes drift events to ROUTER_DRIFT_PATH (env-override of ~/.claude/state/router-drift.jsonl)
 *   - STALENESS_BOUND defaults to 15; configurable via ROUTER_STALENESS_BOUND env var
 *
 * References: issue #200, #341, #322
 */

const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const HOOKS_DIR = path.resolve(__dirname, "..");
const HOOK_SCRIPT = path.join(HOOKS_DIR, "check-agent-dispatch-pairing.js");

// ── Helpers ───────────────────────────────────────────────────────────────────

function tmpDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "drift-test-"));
}

function tmpDriftPath() {
  const dir = tmpDir();
  return path.join(dir, "nested", "router-drift.jsonl");
}

/**
 * Build a minimal PreToolUse hook payload for an Agent invocation,
 * with conversation_history supplied.
 *
 * @param {Array<object>} history - Conversation history entries.
 * @param {object} [toolInput] - Optional Agent tool_input overrides.
 */
function agentPayload(history = [], toolInput = {}) {
  return {
    session_id: "test-session",
    tool_name: "Agent",
    tool_input: {
      subagent_type: "writer",
      prompt: "Do some work.",
      ...toolInput,
    },
    conversation_history: history,
  };
}

/**
 * Build a conversation_history entry representing a Skill tool call.
 *
 * @param {string} skillName - e.g. "claude-wayfinder:dispatch" or "python"
 */
function skillEntry(skillName) {
  return {
    role: "assistant",
    content: [
      {
        type: "tool_use",
        name: "Skill",
        input: { skill: skillName },
      },
    ],
  };
}

/**
 * Build a conversation_history entry representing a generic (non-Skill/non-Agent) tool call.
 *
 * @param {string} toolName - e.g. "Read", "Bash"
 */
function toolEntry(toolName) {
  return {
    role: "assistant",
    content: [
      {
        type: "tool_use",
        name: toolName,
        input: {},
      },
    ],
  };
}

/**
 * Build a conversation_history entry representing an Agent tool call.
 */
function agentEntry() {
  return {
    role: "assistant",
    content: [
      {
        type: "tool_use",
        name: "Agent",
        input: { subagent_type: "reader", prompt: "prior dispatch" },
      },
    ],
  };
}

/**
 * Spawn the hook with the given payload.
 *
 * By default sets SKILL_SIDECAR_PATH to a non-existent path so these unit tests
 * are isolated from the real sidecar file. Tests in this file exercise the
 * conversation_history-based classification logic; the sidecar integration is
 * covered by dispatch-enforcement-integration.test.js.
 *
 * Pass `SKILL_SIDECAR_PATH` explicitly in `env` to override.
 *
 * @param {unknown} payload
 * @param {object} [env]
 * @returns {{ stdout: string, stderr: string, exitCode: number }}
 */
function runHook(payload, env = {}) {
  const input = typeof payload === "string" ? payload : JSON.stringify(payload);
  // Provide a non-existent sidecar path by default so unit tests are sidecar-isolated.
  const noSidecarDir = fs.mkdtempSync(path.join(os.tmpdir(), "no-sidecar-"));
  const noSidecarPath = path.join(noSidecarDir, "nonexistent-sidecar.jsonl");
  const result = spawnSync(process.execPath, [HOOK_SCRIPT], {
    input,
    encoding: "utf8",
    timeout: 10_000,
    env: { ...process.env, SKILL_SIDECAR_PATH: noSidecarPath, ...env },
  });
  return {
    stdout: result.stdout ?? "",
    stderr: result.stderr ?? "",
    exitCode: result.status ?? 0,
  };
}

/**
 * Read drift events from the log path. Returns parsed objects.
 */
function readDriftEvents(driftPath) {
  if (!fs.existsSync(driftPath)) return [];
  const raw = fs.readFileSync(driftPath, "utf8").trim();
  if (!raw) return [];
  return raw
    .split("\n")
    .filter(Boolean)
    .map((line) => JSON.parse(line));
}

// ── Always exits 0 ────────────────────────────────────────────────────────────

test("check-agent-dispatch-pairing: always exits 0 for Agent tool call", () => {
  const driftPath = tmpDriftPath();
  const payload = agentPayload([]);
  const result = runHook(payload, { ROUTER_DRIFT_PATH: driftPath });
  assert.equal(result.exitCode, 0, `stderr: ${result.stderr}`);
});

test("check-agent-dispatch-pairing: always exits 0 for non-Agent tool call (ignored)", () => {
  const driftPath = tmpDriftPath();
  const payload = {
    session_id: "test-session",
    tool_name: "Read",
    tool_input: { file_path: "/foo" },
    conversation_history: [],
  };
  const result = runHook(payload, { ROUTER_DRIFT_PATH: driftPath });
  assert.equal(result.exitCode, 0, `stderr: ${result.stderr}`);
});

test("check-agent-dispatch-pairing: always exits 0 even on malformed JSON", () => {
  const driftPath = tmpDriftPath();
  const result = runHook("not valid json {{", { ROUTER_DRIFT_PATH: driftPath });
  assert.equal(result.exitCode, 0);
});

// ── Never blocks / never augments ─────────────────────────────────────────────

test("check-agent-dispatch-pairing: does not emit permissionDecision deny", () => {
  const driftPath = tmpDriftPath();
  // Worst-case bypass: no history at all
  const payload = agentPayload([]);
  const result = runHook(payload, { ROUTER_DRIFT_PATH: driftPath });
  if (result.stdout.trim()) {
    const out = JSON.parse(result.stdout.trim());
    const decision = out?.hookSpecificOutput?.permissionDecision;
    assert.notEqual(decision, "deny", "Hook must never deny");
    assert.notEqual(decision, "block", "Hook must never block");
  }
  // If no stdout, that's fine too
  assert.equal(result.exitCode, 0);
});

test("check-agent-dispatch-pairing: does not emit updatedInput (never augments tool input)", () => {
  const driftPath = tmpDriftPath();
  // Use the namespaced dispatch skill name — this is the real form used in practice.
  const payload = agentPayload([skillEntry("claude-wayfinder:dispatch")]);
  const result = runHook(payload, { ROUTER_DRIFT_PATH: driftPath });
  if (result.stdout.trim()) {
    const out = JSON.parse(result.stdout.trim());
    const updated = out?.hookSpecificOutput?.updatedInput;
    assert.equal(updated, undefined, "Hook must not emit updatedInput");
  }
  assert.equal(result.exitCode, 0);
});

// ── Case 1: router_mediated — dispatch Skill was the last non-Agent tool ──────
// dispatch invocation is present AND count_Agent == 0 AND count_other <= STALENESS_BOUND
// → no event written

test("check-agent-dispatch-pairing: dispatch immediately before Agent → no drift event", () => {
  const driftPath = tmpDriftPath();
  const history = [skillEntry("claude-wayfinder:dispatch")];
  const result = runHook(agentPayload(history), { ROUTER_DRIFT_PATH: driftPath });
  assert.equal(result.exitCode, 0, `stderr: ${result.stderr}`);
  const events = readDriftEvents(driftPath);
  assert.equal(
    events.length,
    0,
    "No event should be written when dispatch immediately precedes Agent"
  );
});

test("check-agent-dispatch-pairing: dispatch with a few tool calls between → no drift event (within staleness bound)", () => {
  const driftPath = tmpDriftPath();
  // dispatch + 3 non-Agent tool calls (well within default bound of 15)
  const history = [
    skillEntry("claude-wayfinder:dispatch"),
    toolEntry("Read"),
    toolEntry("Grep"),
    toolEntry("Glob"),
  ];
  const result = runHook(agentPayload(history), { ROUTER_DRIFT_PATH: driftPath });
  assert.equal(result.exitCode, 0, `stderr: ${result.stderr}`);
  const events = readDriftEvents(driftPath);
  assert.equal(events.length, 0, "3 tools after dispatch is within staleness bound");
});

// ── Regression #322 + normalization: bare "dispatch" is now treated as the dispatch skill ──

test("check-agent-dispatch-pairing: bare 'dispatch' skillName IS recognized as dispatch via normalization → no event (router_mediated)", () => {
  // After the normalization refactor: bareSkillName() maps both
  // "claude-wayfinder:dispatch" and bare "dispatch" to "dispatch".
  // A history containing bare "dispatch" therefore produces router_mediated
  // (no event written), not skill_mediated as it did in the initial point-fix.
  const driftPath = tmpDriftPath();
  const history = [skillEntry("dispatch")];
  const result = runHook(agentPayload(history), { ROUTER_DRIFT_PATH: driftPath });
  assert.equal(result.exitCode, 0, `stderr: ${result.stderr}`);
  const events = readDriftEvents(driftPath);
  assert.equal(
    events.length,
    0,
    "Bare 'dispatch' is recognized as the router dispatch skill via normalization — no event written"
  );
});

// ── Case 2: bypass — Agent with no dispatch and no enclosing Skill ────────────
// No dispatch Skill in session history AND most recent un-paired tool is not a Skill
// → write bypass event

test("check-agent-dispatch-pairing: empty history → bypass event written", () => {
  const driftPath = tmpDriftPath();
  const result = runHook(agentPayload([]), { ROUTER_DRIFT_PATH: driftPath });
  assert.equal(result.exitCode, 0, `stderr: ${result.stderr}`);
  const events = readDriftEvents(driftPath);
  assert.equal(events.length, 1, "One drift event should be written for empty history");
  assert.equal(events[0].category, "bypass");
});

test("check-agent-dispatch-pairing: no dispatch and only non-Skill tools → bypass event", () => {
  const driftPath = tmpDriftPath();
  const history = [toolEntry("Read"), toolEntry("Bash"), toolEntry("Grep")];
  const result = runHook(agentPayload(history), { ROUTER_DRIFT_PATH: driftPath });
  assert.equal(result.exitCode, 0, `stderr: ${result.stderr}`);
  const events = readDriftEvents(driftPath);
  assert.equal(events.length, 1, "One drift event expected");
  assert.equal(events[0].category, "bypass");
});

// ── Case 3: stale_dispatch — dispatch present but too many tool events elapsed ─

test("check-agent-dispatch-pairing: dispatch with 16 tool calls after → stale_dispatch (exceeds default bound 15)", () => {
  const driftPath = tmpDriftPath();
  const history = [skillEntry("claude-wayfinder:dispatch")];
  for (let i = 0; i < 16; i++) {
    history.push(toolEntry("Read"));
  }
  const result = runHook(agentPayload(history), { ROUTER_DRIFT_PATH: driftPath });
  assert.equal(result.exitCode, 0, `stderr: ${result.stderr}`);
  const events = readDriftEvents(driftPath);
  assert.equal(events.length, 1, "One drift event expected");
  assert.equal(events[0].category, "stale_dispatch");
});

test("check-agent-dispatch-pairing: dispatch with exactly STALENESS_BOUND tools after → no event (boundary inclusive)", () => {
  const driftPath = tmpDriftPath();
  const history = [skillEntry("claude-wayfinder:dispatch")];
  for (let i = 0; i < 15; i++) {
    history.push(toolEntry("Bash"));
  }
  const result = runHook(agentPayload(history), { ROUTER_DRIFT_PATH: driftPath });
  assert.equal(result.exitCode, 0, `stderr: ${result.stderr}`);
  const events = readDriftEvents(driftPath);
  assert.equal(events.length, 0, "Exactly 15 tools is at the bound — no stale event");
});

test("check-agent-dispatch-pairing: ROUTER_STALENESS_BOUND env var overrides default", () => {
  const driftPath = tmpDriftPath();
  // Set bound to 3 — 4 tools after dispatch should be stale
  const history = [skillEntry("claude-wayfinder:dispatch")];
  for (let i = 0; i < 4; i++) {
    history.push(toolEntry("Read"));
  }
  const result = runHook(agentPayload(history), {
    ROUTER_DRIFT_PATH: driftPath,
    ROUTER_STALENESS_BOUND: "3",
  });
  assert.equal(result.exitCode, 0, `stderr: ${result.stderr}`);
  const events = readDriftEvents(driftPath);
  assert.equal(events.length, 1, "One drift event expected with bound=3");
  assert.equal(events[0].category, "stale_dispatch");
});

// ── Case 4: count_Agent >= 1 before new Agent → bypass (prior Agent without dispatch) ─

test("check-agent-dispatch-pairing: prior Agent call between dispatch and now → bypass", () => {
  const driftPath = tmpDriftPath();
  // dispatch → Agent (prior, already dispatched) → now dispatching again without new dispatch
  const history = [skillEntry("claude-wayfinder:dispatch"), agentEntry()];
  const result = runHook(agentPayload(history), { ROUTER_DRIFT_PATH: driftPath });
  assert.equal(result.exitCode, 0, `stderr: ${result.stderr}`);
  const events = readDriftEvents(driftPath);
  assert.equal(
    events.length,
    1,
    "One drift event expected when prior Agent exists without new dispatch"
  );
  assert.equal(events[0].category, "bypass");
});

test("check-agent-dispatch-pairing: no dispatch and prior Agent call → bypass", () => {
  const driftPath = tmpDriftPath();
  const history = [toolEntry("Bash"), agentEntry(), toolEntry("Read")];
  const result = runHook(agentPayload(history), { ROUTER_DRIFT_PATH: driftPath });
  assert.equal(result.exitCode, 0, `stderr: ${result.stderr}`);
  const events = readDriftEvents(driftPath);
  assert.equal(events.length, 1);
  assert.equal(events[0].category, "bypass");
});

// ── Case 5 (#341): skill_mediated — most recent un-paired tool is a Skill ─────
// The Agent call immediately follows a non-dispatch Skill invocation → skill_mediated

test("check-agent-dispatch-pairing: non-dispatch Skill immediately before Agent → skill_mediated event", () => {
  const driftPath = tmpDriftPath();
  const history = [skillEntry("python")];
  const result = runHook(agentPayload(history), { ROUTER_DRIFT_PATH: driftPath });
  assert.equal(result.exitCode, 0, `stderr: ${result.stderr}`);
  const events = readDriftEvents(driftPath);
  assert.equal(events.length, 1, "One informational event expected for skill_mediated");
  assert.equal(events[0].category, "skill_mediated");
});

test("check-agent-dispatch-pairing: non-dispatch Skill then tools then Agent → skill_mediated", () => {
  // The most recent non-paired tool before the Agent is still a Skill (the last Skill)
  // because Skill is the last un-paired tool invocation in the stream
  const driftPath = tmpDriftPath();
  const history = [
    skillEntry("python"),
    toolEntry("Read"),
    toolEntry("Grep"),
    skillEntry("refactoring-discipline"),
  ];
  const result = runHook(agentPayload(history), { ROUTER_DRIFT_PATH: driftPath });
  assert.equal(result.exitCode, 0, `stderr: ${result.stderr}`);
  const events = readDriftEvents(driftPath);
  assert.equal(events.length, 1, "One informational event");
  assert.equal(events[0].category, "skill_mediated");
});

test("check-agent-dispatch-pairing: dispatch Skill (namespaced) before Agent → no event (router_mediated wins)", () => {
  // The namespaced dispatch Skill is a Skill, but because it IS the dispatch skill,
  // it's router_mediated not skill_mediated — the dispatch case takes priority.
  const driftPath = tmpDriftPath();
  const history = [skillEntry("claude-wayfinder:dispatch")];
  const result = runHook(agentPayload(history), { ROUTER_DRIFT_PATH: driftPath });
  assert.equal(result.exitCode, 0, `stderr: ${result.stderr}`);
  const events = readDriftEvents(driftPath);
  // dispatch skill = router_mediated = no event
  assert.equal(events.length, 0, "dispatch Skill before Agent means router_mediated, no event");
});

// ── AC1 additional edge cases (#341) ─────────────────────────────────────────

test("check-agent-dispatch-pairing: [SKILL-MEDIATED] emitted on stderr for skill_mediated with parent name", () => {
  // Verify the observability stderr line is written for skill-mediated calls.
  // The parent skill name should appear in the message.
  const driftPath = tmpDriftPath();
  const history = [skillEntry("whats-next")];
  const result = runHook(agentPayload(history), { ROUTER_DRIFT_PATH: driftPath });
  assert.equal(result.exitCode, 0, `exitCode should be 0; stderr: ${result.stderr}`);
  assert.ok(
    result.stderr.includes("[SKILL-MEDIATED]"),
    `Expected [SKILL-MEDIATED] in stderr; got: ${result.stderr}`
  );
  assert.ok(
    result.stderr.includes("whats-next"),
    `Expected parent skill name 'whats-next' in stderr; got: ${result.stderr}`
  );
  // Must still write an informational event (not block)
  const events = readDriftEvents(driftPath);
  assert.equal(events.length, 1, "One informational event expected for skill_mediated");
  assert.equal(events[0].category, "skill_mediated");
});

test("check-agent-dispatch-pairing: nested skills (Skill → Skill → Agent) → skill_mediated, most recent Skill is parent", () => {
  // Skill A invokes Skill B which then dispatches an Agent call.
  // Walking backwards: most recent tool before Agent is Skill B → skill_mediated.
  // This is the correct behavior: the immediately enclosing skill is identified as parent.
  const driftPath = tmpDriftPath();
  const history = [skillEntry("superpowers:test-driven-development"), skillEntry("python")];
  const result = runHook(agentPayload(history), { ROUTER_DRIFT_PATH: driftPath });
  assert.equal(result.exitCode, 0, `stderr: ${result.stderr}`);
  const events = readDriftEvents(driftPath);
  assert.equal(events.length, 1, "One informational event expected for nested skill_mediated");
  assert.equal(events[0].category, "skill_mediated");
  // stderr should identify the immediately enclosing Skill (python, the last one)
  assert.ok(
    result.stderr.includes("[SKILL-MEDIATED]"),
    `Expected [SKILL-MEDIATED] in stderr; got: ${result.stderr}`
  );
  assert.ok(
    result.stderr.includes("python"),
    `Expected parent skill 'python' in stderr for nested case; got: ${result.stderr}`
  );
});

test("check-agent-dispatch-pairing: Skill then non-Skill tools then Agent → skill_mediated (Skill still most-recent Skill in walk)", () => {
  // Skill call, then several non-Skill tools, then an Agent call (no dispatch found).
  // Walking backwards: we pass non-Skill tools until we hit the Skill → skill_mediated.
  // This documents the approximation: we can't tell if the Skill "closed scope"
  // so the presence of intermediate non-Skill tools doesn't change the classification.
  const driftPath = tmpDriftPath();
  const history = [skillEntry("context-switch-check"), toolEntry("Read"), toolEntry("Bash")];
  const result = runHook(agentPayload(history), { ROUTER_DRIFT_PATH: driftPath });
  assert.equal(result.exitCode, 0, `stderr: ${result.stderr}`);
  const events = readDriftEvents(driftPath);
  assert.equal(
    events.length,
    1,
    "skill_mediated expected even with non-Skill tools between Skill and Agent"
  );
  assert.equal(events[0].category, "skill_mediated");
});

test("check-agent-dispatch-pairing: Agent before Skill before another Agent → bypass (Agent found before Skill in backward walk)", () => {
  // History: Agent (prior), then Skill, then (current) Agent.
  // Walking backwards from the new Agent call: first hit is Skill → skill_mediated?
  // But wait — the dispatch path: no dispatch Skill found (the dispatch check is first).
  // In classifyDispatchRich with lastDispatchIdx === -1:
  //   walk backwards: hits Skill (the non-dispatch one) → skill_mediated.
  // This confirms: even if there was a prior Agent call, if there's a Skill between
  // that prior Agent and the current one, the current call is classified skill_mediated.
  const driftPath = tmpDriftPath();
  const history = [agentEntry(), skillEntry("python")];
  const result = runHook(agentPayload(history), { ROUTER_DRIFT_PATH: driftPath });
  assert.equal(result.exitCode, 0, `stderr: ${result.stderr}`);
  const events = readDriftEvents(driftPath);
  // The most recent tool before this Agent is Skill (python) → skill_mediated.
  // The prior Agent doesn't break the skill_mediated classification because Skill
  // was invoked AFTER that Agent.
  assert.equal(events.length, 1, "One event expected");
  assert.equal(
    events[0].category,
    "skill_mediated",
    "Skill after prior Agent but before current Agent → skill_mediated"
  );
});

// ── Drift event shape ─────────────────────────────────────────────────────────

test("check-agent-dispatch-pairing: drift event has required fields", () => {
  const driftPath = tmpDriftPath();
  const result = runHook(agentPayload([]), { ROUTER_DRIFT_PATH: driftPath });
  assert.equal(result.exitCode, 0, `stderr: ${result.stderr}`);
  const events = readDriftEvents(driftPath);
  assert.equal(events.length, 1);
  const ev = events[0];
  assert.ok(typeof ev.ts === "string" && ev.ts.endsWith("Z"), "ts must be UTC ISO 8601");
  assert.ok(typeof ev.session_id === "string", "session_id must be present");
  assert.ok(typeof ev.category === "string", "category must be present");
  assert.ok(
    ["bypass", "skill_mediated", "stale_dispatch"].includes(ev.category),
    "category must be valid"
  );
});

test("check-agent-dispatch-pairing: drift event includes session_id from input", () => {
  const driftPath = tmpDriftPath();
  const payload = {
    session_id: "specific-session-xyz",
    tool_name: "Agent",
    tool_input: { subagent_type: "writer", prompt: "task" },
    conversation_history: [],
  };
  runHook(payload, { ROUTER_DRIFT_PATH: driftPath });
  const events = readDriftEvents(driftPath);
  assert.equal(events[0].session_id, "specific-session-xyz");
});

// ── Edge cases ────────────────────────────────────────────────────────────────

test("check-agent-dispatch-pairing: no conversation_history field → bypass event (treated as empty)", () => {
  const driftPath = tmpDriftPath();
  const payload = {
    session_id: "test-no-history",
    tool_name: "Agent",
    tool_input: { subagent_type: "reader", prompt: "task" },
    // no conversation_history field
  };
  const result = runHook(payload, { ROUTER_DRIFT_PATH: driftPath });
  assert.equal(result.exitCode, 0, `stderr: ${result.stderr}`);
  const events = readDriftEvents(driftPath);
  assert.equal(events.length, 1);
  assert.equal(events[0].category, "bypass");
});

test("check-agent-dispatch-pairing: conversation_history with non-assistant entries → works without throw", () => {
  const driftPath = tmpDriftPath();
  const history = [
    { role: "user", content: "Hello" },
    skillEntry("claude-wayfinder:dispatch"),
    { role: "user", content: "Thanks" },
  ];
  const result = runHook(agentPayload(history), { ROUTER_DRIFT_PATH: driftPath });
  assert.equal(result.exitCode, 0, `stderr: ${result.stderr}`);
  // dispatch is present and no Agent calls after it → no event
  const events = readDriftEvents(driftPath);
  assert.equal(events.length, 0, "dispatch present → no drift event");
});

test("check-agent-dispatch-pairing: conversation_history with text-only content (no tool_use blocks) → handled without throw", () => {
  const driftPath = tmpDriftPath();
  const history = [
    { role: "assistant", content: "I'll help you with that." },
    { role: "user", content: "Please do." },
  ];
  const result = runHook(agentPayload(history), { ROUTER_DRIFT_PATH: driftPath });
  assert.equal(result.exitCode, 0, `stderr: ${result.stderr}`);
  // No dispatch in history → bypass
  const events = readDriftEvents(driftPath);
  assert.equal(events.length, 1);
  assert.equal(events[0].category, "bypass");
});

test("check-agent-dispatch-pairing: bad ROUTER_DRIFT_PATH (unwriteable) → exits 0 silently (fail-open)", () => {
  const badPath =
    process.platform === "win32"
      ? "Z:\\nonexistent-drive\\router-drift.jsonl"
      : "/dev/null/x/router-drift.jsonl";
  const payload = agentPayload([]);
  const result = runHook(payload, { ROUTER_DRIFT_PATH: badPath });
  assert.equal(result.exitCode, 0, `Hook must exit 0 even on IO error; stderr: ${result.stderr}`);
});

test("check-agent-dispatch-pairing: appends to existing drift log (does not overwrite)", () => {
  const driftPath = tmpDriftPath();
  // Write two bypass events
  runHook(agentPayload([]), { ROUTER_DRIFT_PATH: driftPath });
  runHook(agentPayload([]), { ROUTER_DRIFT_PATH: driftPath });
  const events = readDriftEvents(driftPath);
  assert.equal(events.length, 2, "Two invocations should produce two lines");
});

test("check-agent-dispatch-pairing: non-Agent tool call → no drift event written", () => {
  const driftPath = tmpDriftPath();
  const payload = {
    session_id: "test-non-agent",
    tool_name: "Bash",
    tool_input: { command: "ls" },
    conversation_history: [],
  };
  const result = runHook(payload, { ROUTER_DRIFT_PATH: driftPath });
  assert.equal(result.exitCode, 0);
  assert.equal(
    fs.existsSync(driftPath),
    false,
    "No drift log should be created for non-Agent tools"
  );
});

// ── Multiple dispatch invocations ──────────────────────────────────────────────

test("check-agent-dispatch-pairing: most recent dispatch is used (older dispatch + new Agent between = bypass)", () => {
  // Sequence: dispatch → Agent (consumed) → dispatch → Agent (current)
  // The second dispatch was followed by an Agent, so we look AFTER the second dispatch
  // count_Agent between second dispatch and now = 1 → bypass
  const driftPath = tmpDriftPath();
  const history = [
    skillEntry("claude-wayfinder:dispatch"), // first dispatch
    agentEntry(), // first agent (consumed first dispatch)
    skillEntry("claude-wayfinder:dispatch"), // second dispatch
    agentEntry(), // second agent (consumed second dispatch) — now history has another Agent
  ];
  // current call is a third Agent — most recent dispatch was second dispatch,
  // but there's already an Agent after it → bypass
  const result = runHook(agentPayload(history), { ROUTER_DRIFT_PATH: driftPath });
  assert.equal(result.exitCode, 0, `stderr: ${result.stderr}`);
  const events = readDriftEvents(driftPath);
  assert.equal(events.length, 1);
  assert.equal(events[0].category, "bypass");
});

test("check-agent-dispatch-pairing: fresh dispatch after consumed prior dispatch → no event", () => {
  // dispatch → Agent (consumed) → dispatch (fresh) → current Agent
  // count_Agent after second dispatch = 0, count_other = 0 → router_mediated
  const driftPath = tmpDriftPath();
  const history = [
    skillEntry("claude-wayfinder:dispatch"), // first dispatch
    agentEntry(), // consumed by first dispatch
    skillEntry("claude-wayfinder:dispatch"), // fresh dispatch for current Agent
  ];
  const result = runHook(agentPayload(history), { ROUTER_DRIFT_PATH: driftPath });
  assert.equal(result.exitCode, 0, `stderr: ${result.stderr}`);
  const events = readDriftEvents(driftPath);
  assert.equal(events.length, 0, "Fresh dispatch before current Agent → router_mediated, no event");
});

// ── bypass-taxonomy enrichment ────────────────────────────────────────────────

test("check-agent-dispatch-pairing: bypass event is enriched with bypass_signals and bypass_cause", () => {
  // A plain bypass (no history) should produce an event with bypass_signals
  // and bypass_cause fields added by the taxonomy module.
  const driftPath = tmpDriftPath();
  const result = runHook(agentPayload([]), { ROUTER_DRIFT_PATH: driftPath });
  assert.equal(result.exitCode, 0, `stderr: ${result.stderr}`);
  const events = readDriftEvents(driftPath);
  assert.equal(events.length, 1, "One bypass event expected");
  const ev = events[0];
  assert.equal(ev.category, "bypass");
  // Enrichment fields must be present
  assert.ok(
    ev.bypass_signals !== undefined,
    `bypass_signals must be present on enriched event; got: ${JSON.stringify(ev)}`
  );
  assert.ok(
    ev.bypass_cause !== undefined,
    `bypass_cause must be present on enriched event; got: ${JSON.stringify(ev)}`
  );
  assert.equal(
    typeof ev.bypass_cause,
    "string",
    "bypass_cause must be a string"
  );
  // For no-history bypass, cause should be router_direct_no_dispatch
  assert.equal(
    ev.bypass_cause,
    "router_direct_no_dispatch",
    "Empty history bypass → router_direct_no_dispatch"
  );
});

test("check-agent-dispatch-pairing: dispatch in history → bypass_signals shows dispatch_skill_called_recently=true", () => {
  // Regression #322: before the fix, dispatch_skill_called_recently was always
  // false because the comparison used the bare "dispatch" name. Now that the
  // namespaced form is recognized, this must be true.
  const driftPath = tmpDriftPath();
  // dispatch → Agent (consumed) → current Agent triggers bypass
  const history = [skillEntry("claude-wayfinder:dispatch"), agentEntry()];
  const result = runHook(agentPayload(history), { ROUTER_DRIFT_PATH: driftPath });
  assert.equal(result.exitCode, 0, `stderr: ${result.stderr}`);
  const events = readDriftEvents(driftPath);
  assert.equal(events.length, 1, "One bypass event expected");
  assert.equal(events[0].category, "bypass");
  assert.ok(
    events[0].bypass_signals !== undefined,
    "bypass_signals must be present"
  );
  assert.equal(
    events[0].bypass_signals.dispatch_skill_called_recently,
    true,
    "dispatch_skill_called_recently must be true when 'claude-wayfinder:dispatch' is in history"
  );
  assert.equal(
    events[0].bypass_cause,
    "router_direct_after_consumed_dispatch",
    "Consumed dispatch → router_direct_after_consumed_dispatch cause"
  );
});

test("check-agent-dispatch-pairing: taxonomy module-load failure → exits 0, unenriched event written, stderr warning", () => {
  // Simulate a broken taxonomy module by pointing NODE_PATH so that require(./lib/bypass-taxonomy)
  // resolves to a module that throws at load time. We do this by setting a custom env
  // variable that the hook checks: instead, we inject a broken module via a temp directory.
  const driftPath = tmpDriftPath();
  const os = require("node:os");

  // Create a broken bypass-taxonomy.js that throws at require time
  const tmpHooksDir = fs.mkdtempSync(path.join(os.tmpdir(), "broken-taxonomy-"));
  const brokenLibDir = path.join(tmpHooksDir, "lib");
  fs.mkdirSync(brokenLibDir, { recursive: true });
  fs.writeFileSync(
    path.join(brokenLibDir, "bypass-taxonomy.js"),
    'throw new Error("simulated module-load failure");\n'
  );
  // Also copy parse-input.js so the hook can load (it's required unconditionally)
  const parseInputSrc = path.join(HOOKS_DIR, "parse-input.js");
  fs.copyFileSync(parseInputSrc, path.join(tmpHooksDir, "parse-input.js"));

  // Copy the hook itself to tmpHooksDir so ./lib/bypass-taxonomy resolves there
  const hookSrc = fs.readFileSync(HOOK_SCRIPT, "utf8");
  const tmpHookPath = path.join(tmpHooksDir, "check-agent-dispatch-pairing.js");
  fs.writeFileSync(tmpHookPath, hookSrc);

  const input = JSON.stringify(agentPayload([]));
  const noSidecarDir = fs.mkdtempSync(path.join(os.tmpdir(), "no-sidecar-"));
  const noSidecarPath = path.join(noSidecarDir, "nonexistent-sidecar.jsonl");
  const result = require("node:child_process").spawnSync(
    process.execPath,
    [tmpHookPath],
    {
      input,
      encoding: "utf8",
      timeout: 10_000,
      env: { ...process.env, ROUTER_DRIFT_PATH: driftPath, SKILL_SIDECAR_PATH: noSidecarPath },
    }
  );
  const exitCode = result.status ?? 0;
  const stderr = result.stderr ?? "";

  // Hook must still exit 0
  assert.equal(exitCode, 0, `Hook must exit 0 even when taxonomy module fails to load; stderr: ${stderr}`);

  // Must still write the unenriched event
  const events = readDriftEvents(driftPath);
  assert.equal(events.length, 1, "Unenriched drift event must still be written");
  assert.equal(events[0].category, "bypass", "category must still be present");

  // Must warn on stderr
  assert.ok(
    stderr.includes("bypass-taxonomy"),
    `Expected bypass-taxonomy warning in stderr; got: ${stderr}`
  );
});

test("check-agent-dispatch-pairing: classify() returns malformed shape → exits 0, unenriched event, stderr 'malformed shape'", () => {
  // Simulate a taxonomy module that loads fine and returns successfully but
  // omits both `signals` and `cause` keys (malformed shape).
  const driftPath = tmpDriftPath();

  // Create a temp hooks dir with a malformed-returning bypass-taxonomy module
  const tmpHooksDir = fs.mkdtempSync(path.join(os.tmpdir(), "malformed-classify-"));
  const malformedLibDir = path.join(tmpHooksDir, "lib");
  fs.mkdirSync(malformedLibDir, { recursive: true });
  fs.writeFileSync(
    path.join(malformedLibDir, "bypass-taxonomy.js"),
    'module.exports = { classify: () => ({}), INTERACTIVE_SKILLS: new Set() };\n'
  );
  const parseInputSrc = path.join(HOOKS_DIR, "parse-input.js");
  fs.copyFileSync(parseInputSrc, path.join(tmpHooksDir, "parse-input.js"));

  const hookSrc = fs.readFileSync(HOOK_SCRIPT, "utf8");
  const tmpHookPath = path.join(tmpHooksDir, "check-agent-dispatch-pairing.js");
  fs.writeFileSync(tmpHookPath, hookSrc);

  // agentPayload([]) → no history → bypass category
  const input = JSON.stringify(agentPayload([]));
  const noSidecarDir = fs.mkdtempSync(path.join(os.tmpdir(), "no-sidecar-"));
  const noSidecarPath = path.join(noSidecarDir, "nonexistent-sidecar.jsonl");
  const result = require("node:child_process").spawnSync(
    process.execPath,
    [tmpHookPath],
    {
      input,
      encoding: "utf8",
      timeout: 10_000,
      env: { ...process.env, ROUTER_DRIFT_PATH: driftPath, SKILL_SIDECAR_PATH: noSidecarPath },
    }
  );
  const exitCode = result.status ?? 0;
  const stderr = result.stderr ?? "";

  assert.equal(exitCode, 0, `Hook must exit 0 even when classify returns malformed shape; stderr: ${stderr}`);

  // Must still write the unenriched event (no bypass_signals, no bypass_cause)
  const events = readDriftEvents(driftPath);
  assert.equal(events.length, 1, "Unenriched drift event must still be written when classify returns malformed shape");
  assert.equal(events[0].bypass_signals, undefined, "bypass_signals must be absent when classify returns malformed shape");
  assert.equal(events[0].bypass_cause, undefined, "bypass_cause must be absent when classify returns malformed shape");

  // Must warn on stderr with "malformed shape" (distinct from the "classify threw" message)
  assert.match(stderr, /malformed shape/);
});

test("check-agent-dispatch-pairing: classify() throw at event time → exits 0, unenriched event, stderr warning", () => {
  // Simulate a taxonomy module that loads fine but throws when classify() is called.
  const driftPath = tmpDriftPath();
  const os = require("node:os");

  // Create a module that exports a throwing classify function
  const tmpHooksDir = fs.mkdtempSync(path.join(os.tmpdir(), "throwing-classify-"));
  const throwingLibDir = path.join(tmpHooksDir, "lib");
  fs.mkdirSync(throwingLibDir, { recursive: true });
  fs.writeFileSync(
    path.join(throwingLibDir, "bypass-taxonomy.js"),
    'module.exports = { classify: () => { throw new Error("simulated classify failure"); } };\n'
  );
  const parseInputSrc = path.join(HOOKS_DIR, "parse-input.js");
  fs.copyFileSync(parseInputSrc, path.join(tmpHooksDir, "parse-input.js"));

  const hookSrc = fs.readFileSync(HOOK_SCRIPT, "utf8");
  const tmpHookPath = path.join(tmpHooksDir, "check-agent-dispatch-pairing.js");
  fs.writeFileSync(tmpHookPath, hookSrc);

  const input = JSON.stringify(agentPayload([]));
  const noSidecarDir = fs.mkdtempSync(path.join(os.tmpdir(), "no-sidecar-"));
  const noSidecarPath = path.join(noSidecarDir, "nonexistent-sidecar.jsonl");
  const result = require("node:child_process").spawnSync(
    process.execPath,
    [tmpHookPath],
    {
      input,
      encoding: "utf8",
      timeout: 10_000,
      env: { ...process.env, ROUTER_DRIFT_PATH: driftPath, SKILL_SIDECAR_PATH: noSidecarPath },
    }
  );
  const exitCode = result.status ?? 0;
  const stderr = result.stderr ?? "";

  assert.equal(exitCode, 0, `Hook must exit 0 even when classify() throws; stderr: ${stderr}`);

  // Must still write the unenriched event
  const events = readDriftEvents(driftPath);
  assert.equal(events.length, 1, "Unenriched drift event must still be written when classify throws");
  assert.equal(events[0].category, "bypass");

  // Must warn on stderr
  assert.ok(
    stderr.includes("bypass-taxonomy"),
    `Expected bypass-taxonomy warning in stderr; got: ${stderr}`
  );
});
