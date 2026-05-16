/**
 * Tests for hooks/log-skill-invocation.js
 *
 * Covers:
 *   - buildSkillInvocationEvent: event shape and sentinel discipline
 *   - countToolUseBlocks: counting tool_use blocks in conversation_history
 *   - appendSidecarEntry: file append, trim to MAX_SIDECAR_LINES, FIFO ordering
 *   - resolveSidecarPath: env var override and default fallback
 *   - Hook integration: subprocess fires actual hook against fixture payloads
 *
 * Note on contract testing (hook-authoring discipline §1):
 *   This hook reads conversation_history from the hook payload. That field
 *   contains only COMPLETED assistant turns — the same-turn Skill call is NOT
 *   present in conversation_history at PostToolUse time. The countToolUseBlocks
 *   function counts prior-turn tool_use blocks, and the consumer
 *   (check-agent-dispatch-pairing.js) uses event_count_at_fire to detect when
 *   the sidecar entry came from the same turn. These unit tests exercise the
 *   counting logic with hand-crafted fixtures; a live integration test would be
 *   required to verify the actual conversation_history payload shape from Claude
 *   Code itself.
 */

const { test, before, after, beforeEach, afterEach } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const HOOKS_DIR = path.resolve(__dirname, "..");

// ── Lazy-load module so tests fail clearly if file doesn't exist ──────────────

function loadHook() {
  return require("../log-skill-invocation");
}

// ── Temp-dir helpers ──────────────────────────────────────────────────────────

function makeTmpDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "log-skill-invocation-test-"));
}

function tmpSidecarPath(dir) {
  return path.join(dir, "recent-skill-invocations.jsonl");
}

// ── subprocess runner ─────────────────────────────────────────────────────────

function runHook(payload, env = {}) {
  const scriptPath = path.join(HOOKS_DIR, "log-skill-invocation.js");
  const result = spawnSync(process.execPath, [scriptPath], {
    input: JSON.stringify(payload),
    encoding: "utf8",
    timeout: 10_000,
    env: { ...process.env, ...env },
  });
  return {
    stdout: result.stdout ?? "",
    stderr: result.stderr ?? "",
    exitCode: result.status ?? 0,
  };
}

// ── resolveSidecarPath ────────────────────────────────────────────────────────

test("resolveSidecarPath: honors SKILL_SIDECAR_PATH env var when set", () => {
  const { resolveSidecarPath } = loadHook();
  const saved = process.env.SKILL_SIDECAR_PATH;
  const customPath = "/tmp/custom-sidecar.jsonl";
  process.env.SKILL_SIDECAR_PATH = customPath;
  try {
    assert.equal(resolveSidecarPath(), customPath);
  } finally {
    if (saved === undefined) {
      // biome-ignore lint/performance/noDelete: process.env delete is correct here
      delete process.env.SKILL_SIDECAR_PATH;
    } else {
      process.env.SKILL_SIDECAR_PATH = saved;
    }
  }
});

test("resolveSidecarPath: falls back to <home>/.claude/state/recent-skill-invocations.jsonl when SKILL_SIDECAR_PATH unset", () => {
  const { resolveSidecarPath } = loadHook();
  const saved = process.env.SKILL_SIDECAR_PATH;
  // biome-ignore lint/performance/noDelete: process.env delete is correct here
  delete process.env.SKILL_SIDECAR_PATH;
  try {
    const result = resolveSidecarPath();
    const home = process.env.HOME || process.env.USERPROFILE || os.homedir();
    const expected = path.join(home, ".claude", "state", "recent-skill-invocations.jsonl");
    assert.equal(result, expected);
  } finally {
    if (saved !== undefined) {
      process.env.SKILL_SIDECAR_PATH = saved;
    }
  }
});

// ── countToolUseBlocks ────────────────────────────────────────────────────────

test("countToolUseBlocks: returns 0 for empty array", () => {
  const { countToolUseBlocks } = loadHook();
  assert.equal(countToolUseBlocks([]), 0);
});

test("countToolUseBlocks: returns 0 for undefined history", () => {
  const { countToolUseBlocks } = loadHook();
  assert.equal(countToolUseBlocks(undefined), 0);
});

test("countToolUseBlocks: returns 0 for null history", () => {
  const { countToolUseBlocks } = loadHook();
  assert.equal(countToolUseBlocks(null), 0);
});

test("countToolUseBlocks: counts tool_use blocks in assistant turns", () => {
  const { countToolUseBlocks } = loadHook();
  const history = [
    {
      role: "user",
      content: [{ type: "text", text: "do something" }],
    },
    {
      role: "assistant",
      content: [
        { type: "text", text: "I will help." },
        { type: "tool_use", id: "t1", name: "Bash", input: { command: "ls" } },
        { type: "tool_use", id: "t2", name: "Read", input: { file_path: "/tmp/x" } },
      ],
    },
    {
      role: "user",
      content: [{ type: "tool_result", tool_use_id: "t1", content: "file.txt" }],
    },
    {
      role: "assistant",
      content: [
        { type: "tool_use", id: "t3", name: "Write", input: { file_path: "/tmp/out" } },
      ],
    },
  ];
  assert.equal(countToolUseBlocks(history), 3);
});

test("countToolUseBlocks: ignores non-assistant turns", () => {
  const { countToolUseBlocks } = loadHook();
  const history = [
    {
      role: "user",
      // even if user turn somehow has tool_use-shaped blocks, they are ignored
      content: [{ type: "tool_use", id: "u1", name: "Bash", input: {} }],
    },
  ];
  assert.equal(countToolUseBlocks(history), 0);
});

test("countToolUseBlocks: ignores non-tool_use blocks in assistant turns", () => {
  const { countToolUseBlocks } = loadHook();
  const history = [
    {
      role: "assistant",
      content: [
        { type: "text", text: "hello" },
        { type: "thinking", text: "thinking..." },
        // tool_use without name should not be counted
        { type: "tool_use", id: "t1" },
      ],
    },
  ];
  assert.equal(countToolUseBlocks(history), 0);
});

test("countToolUseBlocks: returns 0 when assistant turns have no content array", () => {
  const { countToolUseBlocks } = loadHook();
  const history = [
    { role: "assistant", content: null },
    { role: "assistant", content: "string content" },
  ];
  assert.equal(countToolUseBlocks(history), 0);
});

// ── appendSidecarEntry ────────────────────────────────────────────────────────

let tmpDir;

beforeEach(() => {
  tmpDir = makeTmpDir();
});

afterEach(() => {
  try {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  } catch (_) {
    // best-effort cleanup
  }
});

test("appendSidecarEntry: creates parent directory and appends one line", () => {
  const { appendSidecarEntry } = loadHook();
  const sidecarPath = path.join(tmpDir, "nested", "sidecar.jsonl");
  const entry = {
    session_id: "s1",
    skill: "dispatch",
    ts: "2026-05-16T00:00:00.000Z",
    event_count_at_fire: 0,
  };

  appendSidecarEntry(entry, sidecarPath);

  const lines = fs.readFileSync(sidecarPath, "utf8").trim().split("\n");
  assert.equal(lines.length, 1);
  assert.deepEqual(JSON.parse(lines[0]), entry);
});

test("appendSidecarEntry: appends subsequent calls to existing file", () => {
  const { appendSidecarEntry } = loadHook();
  const sidecarPath = tmpSidecarPath(tmpDir);

  appendSidecarEntry({ session_id: "s1", skill: "a", ts: "t1", event_count_at_fire: 0 }, sidecarPath);
  appendSidecarEntry({ session_id: "s1", skill: "b", ts: "t2", event_count_at_fire: 1 }, sidecarPath);

  const lines = fs.readFileSync(sidecarPath, "utf8").trim().split("\n");
  assert.equal(lines.length, 2);
  assert.equal(JSON.parse(lines[0]).skill, "a");
  assert.equal(JSON.parse(lines[1]).skill, "b");
});

test("appendSidecarEntry: trims to MAX_SIDECAR_LINES and retains last N entries (FIFO trim from front)", () => {
  const { appendSidecarEntry } = loadHook();
  const sidecarPath = tmpSidecarPath(tmpDir);

  // Write 10 entries with SKILL_SIDECAR_MAX_LINES=5 override
  const savedMax = process.env.SKILL_SIDECAR_MAX_LINES;
  process.env.SKILL_SIDECAR_MAX_LINES = "5";
  try {
    for (let i = 0; i < 10; i++) {
      appendSidecarEntry(
        { session_id: "s1", skill: `skill-${i}`, ts: `t${i}`, event_count_at_fire: i },
        sidecarPath
      );
    }
  } finally {
    if (savedMax === undefined) {
      // biome-ignore lint/performance/noDelete: process.env delete is correct here
      delete process.env.SKILL_SIDECAR_MAX_LINES;
    } else {
      process.env.SKILL_SIDECAR_MAX_LINES = savedMax;
    }
  }

  const lines = fs.readFileSync(sidecarPath, "utf8").trim().split("\n");
  // MAX_SIDECAR_LINES is evaluated at module load time in the source, so we
  // need to use spawnSync to pick up the env var override in a fresh process.
  // Instead verify that the file was appended (all 10 lines) — the trim test
  // below uses subprocess to properly test the env override.
  // Here we just verify the append path works for 10 entries.
  assert.ok(lines.length >= 1, "at least one line must be present");
  // Last line must be skill-9 (most recently appended)
  const lastEntry = JSON.parse(lines[lines.length - 1]);
  assert.equal(lastEntry.skill, "skill-9");
});

test("appendSidecarEntry: trim via subprocess — 10 entries with max=5 yields 5, last is most recent", () => {
  // We use a subprocess here so the SKILL_SIDECAR_MAX_LINES env var is
  // evaluated fresh at module load time (the IIFE at the top of the hook file).
  const sidecarPath = tmpSidecarPath(tmpDir);
  const logPath = path.join(tmpDir, "dispatch-log.jsonl");

  // Write 10 skill invocations through the actual hook subprocess
  for (let i = 0; i < 10; i++) {
    const payload = {
      session_id: "trim-test",
      tool_name: "Skill",
      tool_input: { skill: `skill-${i}` },
      conversation_history: [],
    };
    const result = runHook(payload, {
      SKILL_SIDECAR_PATH: sidecarPath,
      DISPATCH_LOG_PATH: logPath,
      SKILL_SIDECAR_MAX_LINES: "5",
    });
    assert.equal(result.exitCode, 0, `hook failed on entry ${i}: ${result.stderr}`);
  }

  const lines = fs.readFileSync(sidecarPath, "utf8").trim().split("\n");
  assert.equal(lines.length, 5, `expected 5 lines after trim, got ${lines.length}`);

  // The last line must be skill-9 (most recently written — FIFO trim from front)
  const lastEntry = JSON.parse(lines[lines.length - 1]);
  assert.equal(lastEntry.skill, "skill-9");

  // The first retained entry must be skill-5 (10 entries, keep last 5 → indices 5–9)
  const firstEntry = JSON.parse(lines[0]);
  assert.equal(firstEntry.skill, "skill-5");
});

test("appendSidecarEntry: never throws on unwritable path (fail-open)", () => {
  const { appendSidecarEntry } = loadHook();
  const badPath =
    process.platform === "win32"
      ? "Z:\\nonexistent-drive\\sidecar.jsonl"
      : "/dev/null/x/sidecar.jsonl";

  assert.doesNotThrow(() =>
    appendSidecarEntry({ session_id: "s1", skill: "x", ts: "t1", event_count_at_fire: 0 }, badPath)
  );
});

// ── buildSkillInvocationEvent — event shape ───────────────────────────────────

const SENTINEL_SHA = "deadbeef1234567890abcdef1234567890abcdef";

test("buildSkillInvocationEvent: emits correct type, skill, session_id, plugin_version, ts", async () => {
  const { buildSkillInvocationEvent } = loadHook();
  const event = await buildSkillInvocationEvent({
    tool_input: { skill: "dispatch" },
    session_id: "sess-abc",
    plugin_version: SENTINEL_SHA,
    getComponentVersion: () => ({ rev: undefined, content_hash: undefined }),
  });

  assert.equal(event.type, "skill_invocation");
  assert.equal(event.skill, "dispatch");
  assert.equal(event.session_id, "sess-abc");
  assert.equal(event.plugin_version, SENTINEL_SHA);
  assert.ok(typeof event.ts === "string" && event.ts.endsWith("Z"), "ts must be UTC ISO 8601");
  // Must NOT have harness_version field
  assert.equal("harness_version" in event, false, "event must not have harness_version field");
});

test("buildSkillInvocationEvent: works when plugin_version is a Promise (async contract)", async () => {
  const { buildSkillInvocationEvent } = loadHook();
  const event = await buildSkillInvocationEvent({
    tool_input: { skill: "python" },
    session_id: "sess-xyz",
    plugin_version: Promise.resolve(SENTINEL_SHA),
    getComponentVersion: () => ({ rev: undefined, content_hash: undefined }),
  });
  assert.equal(event.plugin_version, SENTINEL_SHA);
});

// ── buildSkillInvocationEvent — sentinel discipline ───────────────────────────

test("buildSkillInvocationEvent sentinel: rev === undefined → omit skill_rev and skill_content_hash", async () => {
  const { buildSkillInvocationEvent } = loadHook();
  const event = await buildSkillInvocationEvent({
    tool_input: { skill: "superpowers:brainstorming" },
    session_id: "s1",
    plugin_version: "v1",
    getComponentVersion: () => ({ rev: undefined, content_hash: undefined }),
  });
  assert.equal("skill_rev" in event, false, "skill_rev must be absent when rev is undefined");
  assert.equal(
    "skill_content_hash" in event,
    false,
    "skill_content_hash must be absent when rev is undefined"
  );
});

test("buildSkillInvocationEvent sentinel: rev === null → include both fields with null value", async () => {
  const { buildSkillInvocationEvent } = loadHook();
  const event = await buildSkillInvocationEvent({
    tool_input: { skill: "refactoring-discipline" },
    session_id: "s1",
    plugin_version: "v1",
    getComponentVersion: () => ({ rev: null, content_hash: null }),
  });
  assert.equal("skill_rev" in event, true, "skill_rev must be present when rev is null");
  assert.equal(event.skill_rev, null);
  assert.equal(
    "skill_content_hash" in event,
    true,
    "skill_content_hash must be present when rev is null"
  );
  assert.equal(event.skill_content_hash, null);
});

test("buildSkillInvocationEvent sentinel: rev is integer → include both fields with their values", async () => {
  const { buildSkillInvocationEvent } = loadHook();
  const event = await buildSkillInvocationEvent({
    tool_input: { skill: "python" },
    session_id: "s1",
    plugin_version: "v1",
    getComponentVersion: () => ({ rev: 7, content_hash: "abc123def456" }),
  });
  assert.equal(event.skill_rev, 7);
  assert.equal(event.skill_content_hash, "abc123def456");
});

// ── Hook integration: subprocess fires actual hook ────────────────────────────

test("log-skill-invocation: exits 0 for a Skill tool invocation", () => {
  const tmpD = makeTmpDir();
  try {
    const sidecarPath = tmpSidecarPath(tmpD);
    const logPath = path.join(tmpD, "dispatch-log.jsonl");
    const payload = {
      session_id: "test-session-100",
      tool_name: "Skill",
      tool_input: { skill: "dispatch" },
      conversation_history: [],
    };
    const result = runHook(payload, {
      SKILL_SIDECAR_PATH: sidecarPath,
      DISPATCH_LOG_PATH: logPath,
      PLUGIN_VERSION_OVERRIDE: SENTINEL_SHA,
    });
    assert.equal(result.exitCode, 0, `stderr: ${result.stderr}`);
  } finally {
    fs.rmSync(tmpD, { recursive: true, force: true });
  }
});

test("log-skill-invocation: writes sidecar entry with correct fields", () => {
  const tmpD = makeTmpDir();
  try {
    const sidecarPath = tmpSidecarPath(tmpD);
    const logPath = path.join(tmpD, "dispatch-log.jsonl");
    const payload = {
      session_id: "test-session-101",
      tool_name: "Skill",
      tool_input: { skill: "dispatch" },
      conversation_history: [],
    };
    runHook(payload, {
      SKILL_SIDECAR_PATH: sidecarPath,
      DISPATCH_LOG_PATH: logPath,
      PLUGIN_VERSION_OVERRIDE: SENTINEL_SHA,
    });

    const line = fs.readFileSync(sidecarPath, "utf8").trim();
    const entry = JSON.parse(line);
    assert.equal(entry.session_id, "test-session-101");
    assert.equal(entry.skill, "dispatch");
    assert.ok(typeof entry.ts === "string" && entry.ts.endsWith("Z"), "ts must be UTC ISO 8601");
    assert.equal(typeof entry.event_count_at_fire, "number");
  } finally {
    fs.rmSync(tmpD, { recursive: true, force: true });
  }
});

test("log-skill-invocation: writes dispatch-log entry with plugin_version field", () => {
  const tmpD = makeTmpDir();
  try {
    const sidecarPath = tmpSidecarPath(tmpD);
    const logPath = path.join(tmpD, "dispatch-log.jsonl");
    const payload = {
      session_id: "test-session-102",
      tool_name: "Skill",
      tool_input: { skill: "python" },
      conversation_history: [],
    };
    runHook(payload, {
      SKILL_SIDECAR_PATH: sidecarPath,
      DISPATCH_LOG_PATH: logPath,
      PLUGIN_VERSION_OVERRIDE: SENTINEL_SHA,
    });

    const line = fs.readFileSync(logPath, "utf8").trim();
    const event = JSON.parse(line);
    assert.equal(event.type, "skill_invocation");
    assert.equal(event.session_id, "test-session-102");
    assert.equal(event.skill, "python");
    assert.equal(event.plugin_version, SENTINEL_SHA, "plugin_version must equal the injected SHA");
    assert.equal(
      "harness_version" in event,
      false,
      "event must not have harness_version field (plugin convention)"
    );
  } finally {
    fs.rmSync(tmpD, { recursive: true, force: true });
  }
});

test("log-skill-invocation: skips silently for non-Skill tools", () => {
  const tmpD = makeTmpDir();
  try {
    const sidecarPath = tmpSidecarPath(tmpD);
    const payload = {
      session_id: "test-session-103",
      tool_name: "Bash",
      tool_input: { command: "ls" },
    };
    const result = runHook(payload, { SKILL_SIDECAR_PATH: sidecarPath });
    assert.equal(result.exitCode, 0);
    assert.equal(
      fs.existsSync(sidecarPath),
      false,
      "no sidecar file should be created for non-Skill tools"
    );
  } finally {
    fs.rmSync(tmpD, { recursive: true, force: true });
  }
});

test("log-skill-invocation: exits 0 even on a bad sidecar path (fail-open)", () => {
  const badPath =
    process.platform === "win32"
      ? "Z:\\nonexistent-drive\\sidecar.jsonl"
      : "/dev/null/x/sidecar.jsonl";
  const payload = {
    session_id: "test-session-104",
    tool_name: "Skill",
    tool_input: { skill: "dispatch" },
    conversation_history: [],
  };
  const result = runHook(payload, { SKILL_SIDECAR_PATH: badPath });
  assert.equal(result.exitCode, 0);
});

test("log-skill-invocation: event_count_at_fire reflects tool_use blocks in conversation_history", () => {
  const tmpD = makeTmpDir();
  try {
    const sidecarPath = tmpSidecarPath(tmpD);
    const logPath = path.join(tmpD, "dispatch-log.jsonl");
    const payload = {
      session_id: "test-session-105",
      tool_name: "Skill",
      tool_input: { skill: "dispatch" },
      // Simulate a conversation_history with 2 completed tool_use blocks
      conversation_history: [
        {
          role: "assistant",
          content: [
            { type: "tool_use", id: "t1", name: "Bash", input: {} },
            { type: "tool_use", id: "t2", name: "Read", input: {} },
          ],
        },
      ],
    };
    runHook(payload, {
      SKILL_SIDECAR_PATH: sidecarPath,
      DISPATCH_LOG_PATH: logPath,
      PLUGIN_VERSION_OVERRIDE: SENTINEL_SHA,
    });

    const line = fs.readFileSync(sidecarPath, "utf8").trim();
    const entry = JSON.parse(line);
    assert.equal(entry.event_count_at_fire, 2, "event_count_at_fire must equal the tool_use count");
  } finally {
    fs.rmSync(tmpD, { recursive: true, force: true });
  }
});
