/**
 * Tests for hooks/lib/dispatch-log.js
 *
 * Covers:
 *   - extractSkillsFromPrompt: skill detection from free-text prompts
 *   - appendLogLine: JSONL append with directory creation and round-trip
 *   - Integration: log-agent-dispatch.js reads stdin correctly and skips non-target tools
 *
 * Note: log-skill-invocation.js is not part of the Tier 1 hooks ported to this plugin.
 * Tests for the `lib/dispatch-log.js` helpers that the `log-agent-dispatch.js` hook uses to write `dispatch-log.jsonl` entries.
 */

const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

// ── Helper: path to a fresh temp JSONL file ──────────────────────────────────

function tmpLogPath() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "dispatch-log-"));
  return path.join(dir, "nested", "dispatch-log.jsonl");
}

// ── Helpers: catalog fixture builder ─────────────────────────────────────────

/**
 * Build the JSON string for a minimal dispatch-catalog.json fixture.
 *
 * @param {{ skills: string[], agents?: string[] }} opts
 * @returns {string}
 */
function buildCatalogJson({ skills = [], agents = [] }) {
  const entries = [
    ...skills.map((name) => ({ kind: "skill", name })),
    ...agents.map((name) => ({ kind: "agent", name })),
  ];
  return JSON.stringify({ entries });
}

/**
 * Write a temp catalog fixture and return its path.
 * Caller is responsible for cleaning up (fs.rmSync).
 *
 * @param {{ skills: string[], agents?: string[] }} opts
 * @returns {string} absolute path to the written file
 */
function writeTmpCatalog(opts) {
  const rand = Math.random().toString(36).slice(2, 10);
  const tmpFile = path.join(os.tmpdir(), `dispatch-catalog-fixture-${Date.now()}-${rand}.json`);
  fs.writeFileSync(tmpFile, buildCatalogJson(opts), "utf8");
  return tmpFile;
}

// ── Lazy-load lib so tests fail clearly if the module doesn't exist yet ──────

function loadLib() {
  return require("../lib/dispatch-log");
}

// ── extractSkillsFromPrompt ───────────────────────────────────────────────────

test("extractSkillsFromPrompt: empty string returns empty array", () => {
  const { extractSkillsFromPrompt } = loadLib();
  assert.deepEqual(extractSkillsFromPrompt(""), []);
});

test("extractSkillsFromPrompt: null/undefined input returns empty array", () => {
  const { extractSkillsFromPrompt } = loadLib();
  assert.deepEqual(extractSkillsFromPrompt(null), []);
  assert.deepEqual(extractSkillsFromPrompt(undefined), []);
});

test("extractSkillsFromPrompt: prompt with one backticked skill returns that skill", () => {
  const tmp = writeTmpCatalog({ skills: ["refactoring-discipline"] });
  try {
    const { extractSkillsFromPrompt } = loadLib();
    const result = extractSkillsFromPrompt(
      "Use the `refactoring-discipline` skill for this task.",
      tmp
    );
    assert.deepEqual(result, ["refactoring-discipline"]);
  } finally {
    fs.rmSync(tmp);
  }
});

test("extractSkillsFromPrompt: realistic refactor-shaped dispatch prompt", () => {
  const tmp = writeTmpCatalog({ skills: ["refactoring-discipline"] });
  try {
    const { extractSkillsFromPrompt } = loadLib();
    const prompt =
      "Use the `refactoring-discipline` skill for this task.\n\n" +
      "Reorganize the session context module.";
    const result = extractSkillsFromPrompt(prompt, tmp);
    assert.deepEqual(result, ["refactoring-discipline"]);
  } finally {
    fs.rmSync(tmp);
  }
});

test("extractSkillsFromPrompt: prompt with multiple distinct skills returns all in catalog", () => {
  const tmp = writeTmpCatalog({ skills: ["python", "git"] });
  try {
    const { extractSkillsFromPrompt } = loadLib();
    const prompt = "Invoke `python` and `git` skills to complete this work.";
    const result = extractSkillsFromPrompt(prompt, tmp);
    assert.deepEqual(result.sort(), ["git", "python"]);
  } finally {
    fs.rmSync(tmp);
  }
});

test("extractSkillsFromPrompt: duplicate skill mentions are deduplicated", () => {
  const tmp = writeTmpCatalog({ skills: ["python"] });
  try {
    const { extractSkillsFromPrompt } = loadLib();
    const prompt = "Use `python` for setup and `python` for testing.";
    const result = extractSkillsFromPrompt(prompt, tmp);
    assert.deepEqual(result, ["python"]);
  } finally {
    fs.rmSync(tmp);
  }
});

test("extractSkillsFromPrompt: single-letter backtick token is NOT matched (pattern requires min 2 chars)", () => {
  const { extractSkillsFromPrompt } = loadLib();
  const result = extractSkillsFromPrompt("Use `a` as a shorthand.");
  assert.deepEqual(result, []);
});

test("extractSkillsFromPrompt: backticked file paths with slashes are not matched", () => {
  const { extractSkillsFromPrompt } = loadLib();
  const result = extractSkillsFromPrompt("Edit `src/index.js` and `lib/utils.js`.");
  assert.deepEqual(result, []);
});

test("extractSkillsFromPrompt: backticked code snippets with uppercase are not matched", () => {
  const { extractSkillsFromPrompt } = loadLib();
  const result = extractSkillsFromPrompt("Call `MyClass.method()` here.");
  assert.deepEqual(result, []);
});

test("extractSkillsFromPrompt: junk token not in catalog is filtered out", () => {
  const tmp = writeTmpCatalog({ skills: ["refactoring-discipline", "python"] });
  try {
    const { extractSkillsFromPrompt } = loadLib();
    const result = extractSkillsFromPrompt("Merge into `main` branch.", tmp);
    assert.deepEqual(result, []);
  } finally {
    fs.rmSync(tmp);
  }
});

test("extractSkillsFromPrompt: un-backticked skill mention is NOT matched (accepted gap)", () => {
  const tmp = writeTmpCatalog({ skills: ["refactoring-discipline"] });
  try {
    const { extractSkillsFromPrompt } = loadLib();
    const result = extractSkillsFromPrompt("Use refactoring-discipline skill.", tmp);
    assert.deepEqual(result, []);
  } finally {
    fs.rmSync(tmp);
  }
});

test("extractSkillsFromPrompt: catalog hit returns the skill", () => {
  const tmp = writeTmpCatalog({ skills: ["python", "refactoring-discipline"] });
  try {
    const { extractSkillsFromPrompt } = loadLib();
    const result = extractSkillsFromPrompt("Use the `python` skill here.", tmp);
    assert.deepEqual(result, ["python"]);
  } finally {
    fs.rmSync(tmp);
  }
});

test("extractSkillsFromPrompt: catalog miss is filtered out", () => {
  const tmp = writeTmpCatalog({ skills: ["python"] });
  try {
    const { extractSkillsFromPrompt } = loadLib();
    const result = extractSkillsFromPrompt("Run `git` commands and use `python`.", tmp);
    assert.deepEqual(result, ["python"]);
  } finally {
    fs.rmSync(tmp);
  }
});

test("extractSkillsFromPrompt: namespaced skill is captured when in catalog", () => {
  const tmp = writeTmpCatalog({
    skills: ["superpowers:test-driven-development", "refactoring-discipline"],
  });
  try {
    const { extractSkillsFromPrompt } = loadLib();
    const result = extractSkillsFromPrompt("Invoke `superpowers:test-driven-development`.", tmp);
    assert.deepEqual(result, ["superpowers:test-driven-development"]);
  } finally {
    fs.rmSync(tmp);
  }
});

test("extractSkillsFromPrompt: catalog file missing returns [] without throwing", () => {
  const { extractSkillsFromPrompt } = loadLib();
  const nonexistentPath = path.join(os.tmpdir(), "does-not-exist-catalog.json");
  let result;
  assert.doesNotThrow(() => {
    result = extractSkillsFromPrompt("Use `python` skill.", nonexistentPath);
  });
  assert.deepEqual(result, []);
});

test("extractSkillsFromPrompt: CLAUDE_CONFIG_DIR is used to locate catalog when DISPATCH_CATALOG_PATH is absent", () => {
  const configDir = fs.mkdtempSync(path.join(os.tmpdir(), "dispatch-claude-home-"));
  try {
    fs.mkdirSync(path.join(configDir, "state"));
    const catalogPath = path.join(configDir, "state", "dispatch-catalog.json");
    fs.writeFileSync(
      catalogPath,
      JSON.stringify({ entries: [{ kind: "skill", name: "test-only-regression-462" }] })
    );

    const savedConfigDir = process.env.CLAUDE_CONFIG_DIR;
    const savedCatalogPath = process.env.DISPATCH_CATALOG_PATH;
    process.env.CLAUDE_CONFIG_DIR = configDir;
    // biome-ignore lint/performance/noDelete: process.env delete is correct here
    delete process.env.DISPATCH_CATALOG_PATH;

    try {
      const { extractSkillsFromPrompt } = loadLib();
      const result = extractSkillsFromPrompt("Use the `test-only-regression-462` skill.");
      assert.deepEqual(result, ["test-only-regression-462"]);
    } finally {
      if (savedConfigDir === undefined) {
        // biome-ignore lint/performance/noDelete: see above
        delete process.env.CLAUDE_CONFIG_DIR;
      } else {
        process.env.CLAUDE_CONFIG_DIR = savedConfigDir;
      }
      if (savedCatalogPath === undefined) {
        // biome-ignore lint/performance/noDelete: see above
        delete process.env.DISPATCH_CATALOG_PATH;
      } else {
        process.env.DISPATCH_CATALOG_PATH = savedCatalogPath;
      }
    }
  } finally {
    fs.rmSync(configDir, { recursive: true });
  }
});

test("extractSkillsFromPrompt: mtime-based cache reload picks up catalog changes", () => {
  const tmp = writeTmpCatalog({ skills: ["python"] });
  try {
    const { extractSkillsFromPrompt } = loadLib();
    // First load — only python in catalog
    const r1 = extractSkillsFromPrompt("Use `python` and `refactoring-discipline`.", tmp);
    assert.deepEqual(r1, ["python"]);

    // Write a new catalog with a newer mtime
    const newContent = buildCatalogJson({ skills: ["python", "refactoring-discipline"] });
    fs.writeFileSync(tmp, newContent, "utf8");
    // Bump mtime by 1 second to ensure it's detectable
    const now = Date.now();
    fs.utimesSync(tmp, new Date(now + 1000), new Date(now + 1000));

    // Second load — should reload and pick up refactoring-discipline
    const r2 = extractSkillsFromPrompt("Use `python` and `refactoring-discipline`.", tmp);
    assert.deepEqual(r2.sort(), ["python", "refactoring-discipline"]);
  } finally {
    fs.rmSync(tmp);
  }
});

test("extractSkillsFromPrompt: prompt with skills from a real session", () => {
  const tmp = writeTmpCatalog({
    skills: [
      "superpowers:test-driven-development",
      "superpowers:verification-before-completion",
      "refactoring-discipline",
    ],
  });
  try {
    const { extractSkillsFromPrompt } = loadLib();
    const prompt =
      "Skills to use:\n" +
      "- `superpowers:test-driven-development` — write the failing test first.\n" +
      "- `superpowers:verification-before-completion` — final quality gate.\n" +
      "Don't invoke `refactoring-discipline` — net-new code.";
    const result = extractSkillsFromPrompt(prompt, tmp);
    assert.deepEqual(result.sort(), [
      "refactoring-discipline",
      "superpowers:test-driven-development",
      "superpowers:verification-before-completion",
    ]);
  } finally {
    fs.rmSync(tmp);
  }
});

// ── appendLogLine ─────────────────────────────────────────────────────────────

test("appendLogLine: writes one JSON line followed by newline", () => {
  const { appendLogLine } = loadLib();
  const logPath = tmpLogPath();
  const event = { type: "agent_dispatch", ts: "2026-04-24T00:00:00.000Z", agent: "writer" };

  appendLogLine(event, logPath);

  const content = fs.readFileSync(logPath, "utf8");
  assert.equal(content, `${JSON.stringify(event)}\n`);
});

test("appendLogLine: creates parent directory if it does not exist", () => {
  const { appendLogLine } = loadLib();
  const logPath = tmpLogPath(); // nested subdir does not exist yet
  assert.equal(fs.existsSync(path.dirname(logPath)), false, "precondition: dir must not exist");

  appendLogLine({ test: true }, logPath);

  assert.equal(fs.existsSync(logPath), true);
});

test("appendLogLine: appends to existing file rather than overwriting", () => {
  const { appendLogLine } = loadLib();
  const logPath = tmpLogPath();
  appendLogLine({ n: 1 }, logPath);
  appendLogLine({ n: 2 }, logPath);

  const lines = fs.readFileSync(logPath, "utf8").trim().split("\n");
  assert.equal(lines.length, 2);
  assert.deepEqual(JSON.parse(lines[0]), { n: 1 });
  assert.deepEqual(JSON.parse(lines[1]), { n: 2 });
});

test("appendLogLine: round-trip — write event, read file, parse last line equals input", () => {
  const { appendLogLine } = loadLib();
  const logPath = tmpLogPath();
  const event = {
    type: "skill_invocation",
    ts: "2026-04-24T12:34:56.789Z",
    session_id: "sess-abc123",
    skill: "refactoring-discipline",
  };

  appendLogLine(event, logPath);

  const raw = fs.readFileSync(logPath, "utf8");
  const lastLine = raw.trim().split("\n").pop();
  const parsed = JSON.parse(lastLine);
  assert.deepEqual(parsed, event);
});

test("appendLogLine: never throws on a bad log path (fail-open)", () => {
  const { appendLogLine } = loadLib();
  const badPath =
    process.platform === "win32"
      ? "Z:\\nonexistent-drive\\dispatch-log.jsonl"
      : "/dev/null/x/dispatch-log.jsonl";

  assert.doesNotThrow(() => appendLogLine({ ok: true }, badPath));
});

// ── Hook integration: log-agent-dispatch.js ──────────────────────────────────

const HOOKS_DIR = path.resolve(__dirname, "..");

function runHook(scriptName, payload, env = {}) {
  const scriptPath = path.join(HOOKS_DIR, scriptName);
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

test("log-agent-dispatch: exits 0 for an Agent tool dispatch", () => {
  const logPath = tmpLogPath();
  const catalogPath = writeTmpCatalog({ skills: ["refactoring-discipline"] });
  const payload = {
    session_id: "test-session-001",
    tool_name: "Agent",
    tool_input: {
      subagent_type: "writer",
      prompt: "Use the `refactoring-discipline` skill to reorganize this module.",
    },
  };
  try {
    const result = runHook("log-agent-dispatch.js", payload, {
      DISPATCH_LOG_PATH: logPath,
      DISPATCH_CATALOG_PATH: catalogPath,
    });
    assert.equal(result.exitCode, 0, `stderr: ${result.stderr}`);
  } finally {
    fs.rmSync(catalogPath);
  }
});

test("log-agent-dispatch: writes agent_dispatch event to log", () => {
  const logPath = tmpLogPath();
  const catalogPath = writeTmpCatalog({ skills: ["refactoring-discipline"] });
  const payload = {
    session_id: "test-session-002",
    tool_name: "Agent",
    tool_input: {
      subagent_type: "writer",
      prompt: "Use the `refactoring-discipline` skill to reorganize this module.",
    },
  };
  try {
    runHook("log-agent-dispatch.js", payload, {
      DISPATCH_LOG_PATH: logPath,
      DISPATCH_CATALOG_PATH: catalogPath,
    });

    const line = fs.readFileSync(logPath, "utf8").trim();
    const event = JSON.parse(line);
    assert.equal(event.type, "agent_dispatch");
    assert.equal(event.session_id, "test-session-002");
    assert.equal(event.agent, "writer");
    assert.deepEqual(event.skills_in_prompt, ["refactoring-discipline"]);
    assert.ok(typeof event.ts === "string" && event.ts.endsWith("Z"), "ts must be UTC ISO 8601");
    assert.ok(event.task_excerpt.length <= 200, "task_excerpt must be at most 200 chars");
    assert.ok(!event.task_excerpt.includes("\n"), "task_excerpt must not contain newlines");
  } finally {
    fs.rmSync(catalogPath);
  }
});

test("log-agent-dispatch: skips silently for non-Agent tools", () => {
  const logPath = tmpLogPath();
  const payload = {
    session_id: "test-session-003",
    tool_name: "Bash",
    tool_input: { command: "ls" },
  };
  const result = runHook("log-agent-dispatch.js", payload, { DISPATCH_LOG_PATH: logPath });
  assert.equal(result.exitCode, 0);
  assert.equal(fs.existsSync(logPath), false, "no log file should be created for non-Agent tools");
});

test("log-agent-dispatch: empty skills_in_prompt when no backtick-quoted identifiers present", () => {
  const logPath = tmpLogPath();
  const payload = {
    session_id: "test-session-004",
    tool_name: "Agent",
    tool_input: {
      prompt: "Just do the thing without any skill mentions.",
    },
  };
  runHook("log-agent-dispatch.js", payload, { DISPATCH_LOG_PATH: logPath });

  const event = JSON.parse(fs.readFileSync(logPath, "utf8").trim());
  assert.deepEqual(event.skills_in_prompt, []);
});

test("log-agent-dispatch: task_excerpt is first 200 chars with newlines collapsed", () => {
  const logPath = tmpLogPath();
  const longMultiLine = "Line one.\n".repeat(30); // 300 chars with newlines
  const payload = {
    session_id: "test-session-005",
    tool_name: "Agent",
    tool_input: { prompt: longMultiLine },
  };
  runHook("log-agent-dispatch.js", payload, { DISPATCH_LOG_PATH: logPath });

  const event = JSON.parse(fs.readFileSync(logPath, "utf8").trim());
  assert.ok(event.task_excerpt.length <= 200);
  assert.ok(!event.task_excerpt.includes("\n"));
});

test("log-agent-dispatch: exits 0 even on a bad log path (fail-open)", () => {
  const badPath =
    process.platform === "win32"
      ? "Z:\\nonexistent-drive\\dispatch-log.jsonl"
      : "/dev/null/x/dispatch-log.jsonl";
  const payload = {
    session_id: "test-session-006",
    tool_name: "Agent",
    tool_input: { prompt: "Some task." },
  };
  const result = runHook("log-agent-dispatch.js", payload, { DISPATCH_LOG_PATH: badPath });
  assert.equal(result.exitCode, 0);
});

// ── plugin_version stamping ──────────────────────────────────────────────────

const SENTINEL_SHA = "deadbeef1234567890abcdef1234567890abcdef";

test("log-agent-dispatch: emits plugin_version field on agent_dispatch events", () => {
  const logPath = tmpLogPath();
  const payload = {
    session_id: "test-session-020",
    tool_name: "Agent",
    tool_input: {
      subagent_type: "writer",
      prompt: "Do some work.",
    },
  };
  runHook("log-agent-dispatch.js", payload, {
    DISPATCH_LOG_PATH: logPath,
    PLUGIN_VERSION_OVERRIDE: SENTINEL_SHA,
  });

  const event = JSON.parse(fs.readFileSync(logPath, "utf8").trim());
  assert.equal(event.plugin_version, SENTINEL_SHA, "plugin_version must equal the injected SHA");
});

// ── buildAgentDispatchEvent unit tests ───────────────────────────────────────

const { buildAgentDispatchEvent } = require("../log-agent-dispatch");

test("buildAgentDispatchEvent stamps agent_rev + agent_content_hash for owned agent", async () => {
  const event = await buildAgentDispatchEvent({
    tool_input: { subagent_type: "writer", prompt: "do the thing" },
    session_id: "s1",
    plugin_version: "deadbeef".repeat(5),
    getComponentVersion: () => ({ rev: 3, content_hash: "a3f9c1d2e4b8" }),
  });
  assert.equal(event.type, "agent_dispatch");
  assert.equal(event.agent, "writer");
  assert.equal(event.agent_rev, 3);
  assert.equal(event.agent_content_hash, "a3f9c1d2e4b8");
});

test("buildAgentDispatchEvent stamps null fields when helper returns null", async () => {
  const event = await buildAgentDispatchEvent({
    tool_input: { subagent_type: "ghost", prompt: "" },
    session_id: "s1",
    plugin_version: "abc",
    getComponentVersion: () => ({ rev: null, content_hash: null }),
  });
  assert.equal(event.agent_rev, null);
  assert.equal(event.agent_content_hash, null);
});

test("buildAgentDispatchEvent omits component fields entirely when helper returns absent", async () => {
  const event = await buildAgentDispatchEvent({
    tool_input: {
      subagent_type: "superpowers:brainstorming-as-agent",
      prompt: "",
    },
    session_id: "s1",
    plugin_version: "abc",
    getComponentVersion: () => ({ rev: undefined, content_hash: undefined }),
  });
  assert.equal("agent_rev" in event, false);
  assert.equal("agent_content_hash" in event, false);
});

test("buildAgentDispatchEvent works when plugin_version is a Promise (async contract)", async () => {
  const event = await buildAgentDispatchEvent({
    tool_input: { subagent_type: "writer", prompt: "x" },
    session_id: "s1",
    plugin_version: Promise.resolve("deadbeef".repeat(5)),
    getComponentVersion: () => ({ rev: 3, content_hash: "a3f9c1d2e4b8" }),
  });
  assert.equal(event.plugin_version, "deadbeef".repeat(5));
});

// ── E2E smoke test: subprocess fires actual hook against fixture tree ─────────

const crypto = require("node:crypto");

function makeFixtureHome({
  agentBody = "---\nname: writer\n---\nagent body\n",
  sidecar = null,
} = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "dispatch-e2e-"));
  fs.mkdirSync(path.join(dir, "agents"));
  fs.mkdirSync(path.join(dir, "skills"));
  fs.mkdirSync(path.join(dir, "state"));
  fs.writeFileSync(path.join(dir, "agents", "writer.md"), agentBody);
  if (sidecar) {
    fs.writeFileSync(path.join(dir, "state", "component-revisions.json"), JSON.stringify(sidecar));
  }
  return dir;
}

test("E2E: log-agent-dispatch stamps agent_rev + agent_content_hash for owned agent with matching sidecar", () => {
  const agentBody = "---\nname: writer\n---\nagent body\n";
  const hash = crypto.createHash("sha256").update(agentBody).digest("hex").slice(0, 12);
  const home = makeFixtureHome({
    agentBody,
    sidecar: {
      version: 1,
      components: {
        "agent:writer": { rev: 5, content_hash: hash },
      },
    },
  });
  const logPath = path.join(home, "dispatch.jsonl");

  const catalogPath = path.join(home, "state", "dispatch-catalog.json");
  fs.writeFileSync(
    catalogPath,
    JSON.stringify({ entries: [{ kind: "skill", name: "refactoring-discipline" }] })
  );

  const payload = {
    session_id: "e2e-session-001",
    tool_name: "Agent",
    tool_input: {
      subagent_type: "writer",
      prompt: "Use the `refactoring-discipline` skill to clean up.",
    },
  };

  const result = spawnSync(process.execPath, [path.join(HOOKS_DIR, "log-agent-dispatch.js")], {
    input: JSON.stringify(payload),
    encoding: "utf8",
    timeout: 10_000,
    env: {
      ...process.env,
      DISPATCH_LOG_PATH: logPath,
      CLAUDE_CONFIG_DIR: home,
      DISPATCH_CATALOG_PATH: catalogPath,
      PLUGIN_VERSION_OVERRIDE: SENTINEL_SHA,
    },
  });

  assert.equal(result.status ?? 0, 0, `stderr: ${result.stderr}`);

  const line = fs.readFileSync(logPath, "utf8").trim();
  const event = JSON.parse(line);

  assert.equal(event.type, "agent_dispatch");
  assert.equal(event.session_id, "e2e-session-001");
  assert.equal(event.agent, "writer");
  assert.equal(event.plugin_version, SENTINEL_SHA);
  assert.equal(event.agent_rev, 5, "agent_rev must equal sidecar rev when hashes match");
  assert.equal(
    event.agent_content_hash,
    hash,
    "agent_content_hash must equal the 12-char SHA-256 prefix"
  );
  assert.deepEqual(event.skills_in_prompt, ["refactoring-discipline"]);
});
