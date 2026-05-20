const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const HOOK = path.join(__dirname, "..", "check-catalog-health.js");
const REPO_ROOT_FOR_HELPER = path.resolve(__dirname, "..", "..");

// Build a minimal fake ~/.claude tree rooted at `base`:
//   base/state/dispatch-catalog.json
//   base/skills/example/SKILL.md
//   base/agents/example.md
function makeFakeClaudeHome(base) {
  fs.mkdirSync(path.join(base, "state"), { recursive: true });
  fs.mkdirSync(path.join(base, "skills", "example"), { recursive: true });
  fs.mkdirSync(path.join(base, "agents"), { recursive: true });
}

const FAKE_PYTHON_OK = path.resolve(__dirname, "fixtures", "fake-python-ok.js");
const FAKE_PYTHON_FAIL = path.resolve(__dirname, "fixtures", "fake-python-fail.js");

/**
 * Plant a VALID setup-state flag into `pluginDataDir`.
 * Creates a fake venv structure so getVenvPython() resolves to a real file.
 * Also returns the CLAUDE_WAYFINDER_PROBE_CMD env value to inject (cross-platform probe override).
 *
 * @param {string} pluginDataDir
 * @param {"ok"|"fail"} probeOutcome - whether the import probe should pass or fail
 * @returns {{ pluginDataDir: string, probeCmdEnv: string }}
 */
function plantValidFlag(pluginDataDir, probeOutcome = "ok") {
  const venvDir = path.join(pluginDataDir, "venv");
  const venvBin = path.join(venvDir, process.platform === "win32" ? "Scripts" : "bin");
  const pythonBin = path.join(venvBin, process.platform === "win32" ? "python.exe" : "python");
  fs.mkdirSync(venvBin, { recursive: true });
  // Write a placeholder file so getVenvPython() path exists (readSetupState VALID check).
  // The probe itself is overridden via CLAUDE_WAYFINDER_PROBE_CMD (cross-platform seam).
  fs.writeFileSync(pythonBin, "placeholder");
  const { getCurrentVersion } = require("../lib/setup-state.js");
  fs.writeFileSync(
    path.join(pluginDataDir, "setup-state.json"),
    JSON.stringify({
      version: getCurrentVersion(),
      venv_path: venvDir,
      interpreter: pythonBin,
      installed_at: new Date().toISOString(),
    })
  );
  const shimPath = probeOutcome === "ok" ? FAKE_PYTHON_OK : FAKE_PYTHON_FAIL;
  // JSON array format: ["node_path", "shim_path"] — avoids whitespace-splitting issues
  // on paths with spaces (e.g. "C:\Program Files\nodejs\node.exe").
  const probeCmdEnv = JSON.stringify([process.execPath, shimPath]);
  return { pluginDataDir, probeCmdEnv };
}

function runHook(catalogPath, claudeHome) {
  // Plant a valid setup-state flag so the gate passes and catalog checks run.
  const pluginDataDir = fs.mkdtempSync(path.join(os.tmpdir(), "wayfinder-hookpd-"));
  const { probeCmdEnv } = plantValidFlag(pluginDataDir, "ok");
  const env = {
    ...process.env,
    DISPATCH_CATALOG_PATH: catalogPath,
    CLAUDE_PLUGIN_DATA: pluginDataDir,
    CLAUDE_PLUGIN_ROOT: REPO_ROOT_FOR_HELPER,
    CLAUDE_WAYFINDER_PROBE_CMD: probeCmdEnv,
  };
  if (claudeHome !== undefined) {
    env.CLAUDE_HOME = claudeHome;
  }
  const r = spawnSync(process.execPath, [HOOK], { env, input: "{}", encoding: "utf8" });
  fs.rmSync(pluginDataDir, { recursive: true, force: true });
  return { stdout: r.stdout, stderr: r.stderr, status: r.status };
}

// ---------------------------------------------------------------------------
// Existing tests (preserved)
// ---------------------------------------------------------------------------

test("emits banner when catalog file is missing", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "cat-"));
  const out = runHook(path.join(tmp, "absent.json"));
  assert.equal(out.status, 0);
  const parsed = JSON.parse(out.stdout);
  assert.equal(parsed.hookSpecificOutput.hookEventName, "SessionStart");
  assert.match(parsed.hookSpecificOutput.additionalContext, /\[CATALOG ERROR\]/);
});

test("emits banner when catalog has zero entries", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "cat-"));
  const f = path.join(tmp, "empty.json");
  fs.writeFileSync(f, JSON.stringify({ schema_version: 1, entries: [] }));
  const out = runHook(f);
  const parsed = JSON.parse(out.stdout);
  assert.match(parsed.hookSpecificOutput.additionalContext, /\[CATALOG ERROR\]/);
});

test("silent no-op when catalog has entries", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "cat-"));
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "claude-home-"));
  makeFakeClaudeHome(home);

  const f = path.join(tmp, "ok.json");
  fs.writeFileSync(
    f,
    JSON.stringify({
      schema_version: 1,
      entries: [{ name: "x", kind: "skill" }],
    })
  );

  // Set catalog mtime to now, source files to 5 minutes ago — catalog is fresh.
  const pastTime = new Date(Date.now() - 5 * 60 * 1000);
  const skillFile = path.join(home, "skills", "example", "SKILL.md");
  const agentFile = path.join(home, "agents", "example.md");
  fs.writeFileSync(skillFile, "# skill");
  fs.writeFileSync(agentFile, "# agent");
  fs.utimesSync(skillFile, pastTime, pastTime);
  fs.utimesSync(agentFile, pastTime, pastTime);
  // catalog written after source files, so it is newer
  fs.writeFileSync(
    f,
    JSON.stringify({ schema_version: 1, entries: [{ name: "x", kind: "skill" }] })
  );

  const out = runHook(f, home);
  assert.equal(out.stdout.trim(), "");
});

// ---------------------------------------------------------------------------
// New tests
// ---------------------------------------------------------------------------

test("catalog parse-error exits 2 with banner", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "cat-"));
  const f = path.join(tmp, "broken.json");
  fs.writeFileSync(f, "this is not json {{{");
  const out = runHook(f);
  assert.equal(out.status, 2);
  const parsed = JSON.parse(out.stdout);
  assert.match(parsed.hookSpecificOutput.additionalContext, /\[CATALOG ERROR\]/);
});

test("stale catalog emits [CATALOG STALE] banner and exits 0", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "cat-"));
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "claude-home-"));
  makeFakeClaudeHome(home);

  const catalogFile = path.join(tmp, "catalog.json");
  fs.writeFileSync(
    catalogFile,
    JSON.stringify({ schema_version: 1, entries: [{ name: "x", kind: "skill" }] })
  );

  // Set catalog mtime to 10 minutes ago, then write a SKILL.md with a newer mtime.
  const pastTime = new Date(Date.now() - 10 * 60 * 1000);
  fs.utimesSync(catalogFile, pastTime, pastTime);

  const skillFile = path.join(home, "skills", "example", "SKILL.md");
  fs.writeFileSync(skillFile, "# newer skill");
  // skillFile's mtime defaults to now — newer than the catalog

  const out = runHook(catalogFile, home);
  assert.equal(out.status, 0);
  const parsed = JSON.parse(out.stdout);
  assert.match(parsed.hookSpecificOutput.additionalContext, /\[CATALOG STALE\]/);
});

test("fresh catalog (newer than all sources) is silent", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "cat-"));
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "claude-home-"));
  makeFakeClaudeHome(home);

  const skillFile = path.join(home, "skills", "example", "SKILL.md");
  const agentFile = path.join(home, "agents", "example.md");
  fs.writeFileSync(skillFile, "# skill");
  fs.writeFileSync(agentFile, "# agent");

  // Source files are 5 minutes old; catalog written after → catalog is newest.
  const pastTime = new Date(Date.now() - 5 * 60 * 1000);
  fs.utimesSync(skillFile, pastTime, pastTime);
  fs.utimesSync(agentFile, pastTime, pastTime);

  const catalogFile = path.join(tmp, "catalog.json");
  fs.writeFileSync(
    catalogFile,
    JSON.stringify({ schema_version: 1, entries: [{ name: "x", kind: "skill" }] })
  );

  const out = runHook(catalogFile, home);
  assert.equal(out.status, 0);
  assert.equal(out.stdout.trim(), "");
});

// ---------------------------------------------------------------------------
// Setup-state gate tests (Phase 2 — Issue #104)
// ---------------------------------------------------------------------------

/**
 * Run the hook with a CLAUDE_PLUGIN_DATA directory override.
 * Accepts an optional probeCmdEnv for the import probe override.
 */
function runHookWithPluginData({ pluginData, catalogPath, claudeHome, probeCmdEnv } = {}) {
  const env = {
    ...process.env,
    CLAUDE_PLUGIN_DATA: pluginData,
    CLAUDE_PLUGIN_ROOT: REPO_ROOT_FOR_HELPER,
  };
  if (catalogPath !== undefined) env.DISPATCH_CATALOG_PATH = catalogPath;
  if (claudeHome !== undefined) env.CLAUDE_HOME = claudeHome;
  if (probeCmdEnv !== undefined) env.CLAUDE_WAYFINDER_PROBE_CMD = probeCmdEnv;
  const r = spawnSync(process.execPath, [HOOK], {
    input: "{}",
    env,
    encoding: "utf8",
  });
  return { stdout: r.stdout, stderr: r.stderr, status: r.status };
}

test("check-catalog-health emits MISSING banner when no setup-state flag", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "wayfinder-hooktest-"));
  try {
    const result = runHookWithPluginData({ pluginData: tmp });
    assert.equal(result.status, 0, `Hook exited non-zero: ${result.stderr}`);
    assert.match(
      result.stdout,
      /claude-wayfinder requires setup.*\/setup-wayfinder/s,
      "Expected MISSING-state banner in stdout"
    );
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test("check-catalog-health emits BROKEN banner when venv path doesn't exist", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "wayfinder-hooktest-"));
  try {
    const { getCurrentVersion } = require("../lib/setup-state.js");
    fs.writeFileSync(
      path.join(tmp, "setup-state.json"),
      JSON.stringify({
        version: getCurrentVersion(),
        venv_path: "/nonexistent/path",
        interpreter: "/usr/bin/python3.12",
        installed_at: "2026-05-17T19:00:00Z",
      })
    );
    const result = runHookWithPluginData({ pluginData: tmp });
    assert.equal(result.status, 0, `Hook exited non-zero: ${result.stderr}`);
    assert.match(result.stdout, /unreachable or corrupt.*\/setup-wayfinder/s);
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test("check-catalog-health emits STALE banner when flag version differs from plugin version", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "wayfinder-hooktest-"));
  try {
    const venvDir = path.join(tmp, "venv");
    fs.mkdirSync(path.join(venvDir, process.platform === "win32" ? "Scripts" : "bin"), {
      recursive: true,
    });
    fs.writeFileSync(
      path.join(tmp, "setup-state.json"),
      JSON.stringify({
        version: "0.0.0-old",
        venv_path: venvDir,
        interpreter: "/usr/bin/python3.12",
        installed_at: "2026-05-17T19:00:00Z",
      })
    );
    const result = runHookWithPluginData({ pluginData: tmp });
    assert.equal(result.status, 0, `Hook exited non-zero: ${result.stderr}`);
    assert.match(result.stdout, /venv is for v0\.0\.0-old but plugin is v/s);
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test("check-catalog-health proceeds silently when flag VALID and import probe passes", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "wayfinder-hooktest-"));
  try {
    const { pluginDataDir, probeCmdEnv } = plantValidFlag(tmp, "ok");
    // Point catalog to a non-existent file — if the probe gate doesn't short-circuit,
    // the hook will emit a CATALOG ERROR banner (not a setup banner).
    // We just verify no setup banner appears.
    const result = runHookWithPluginData({
      pluginData: pluginDataDir,
      probeCmdEnv,
    });
    assert.equal(result.status, 0, `Hook exited non-zero: ${result.stderr}`);
    // No setup banner should appear when probe passes.
    assert.doesNotMatch(result.stdout, /requires setup/s);
    assert.doesNotMatch(result.stdout, /fails import probe/s);
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test("check-catalog-health falls back to default probe when PROBE_CMD contains malformed JSON", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "wayfinder-hooktest-"));
  try {
    const { pluginDataDir } = plantValidFlag(tmp, "ok");
    const result = runHookWithPluginData({
      pluginData: pluginDataDir,
      probeCmdEnv: "this is not valid json {{{",
    });
    assert.equal(result.status, 0, `Hook exited non-zero: ${result.stderr}`);
    // Should emit the internal-error warning about the malformed seam.
    assert.match(result.stdout, /CLAUDE_WAYFINDER_PROBE_CMD malformed JSON/);
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test("check-catalog-health deletes flag and emits BROKEN banner when import probe fails", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "wayfinder-hooktest-"));
  try {
    const { pluginDataDir, probeCmdEnv } = plantValidFlag(tmp, "fail");
    const flagPath = path.join(pluginDataDir, "setup-state.json");
    assert.ok(fs.existsSync(flagPath), "Flag should exist before hook run");
    const result = runHookWithPluginData({
      pluginData: pluginDataDir,
      probeCmdEnv,
    });
    assert.equal(result.status, 0, `Hook exited non-zero: ${result.stderr}`);
    assert.match(result.stdout, /fails import probe.*\/setup-wayfinder/s);
    // Flag should have been deleted on probe failure.
    assert.ok(!fs.existsSync(flagPath), "flag file should have been deleted on probe failure");
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

// ---------------------------------------------------------------------------
// Issue #185 — banners must also surface to stderr
// ---------------------------------------------------------------------------

test("emits banner to stderr when catalog file is missing", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "cat-"));
  const out = runHook(path.join(tmp, "absent.json"));
  assert.ok(out.stderr && out.stderr.length > 0, "stderr should be non-empty for missing catalog");
  assert.match(out.stderr, /\[CATALOG ERROR\]/);
});

test("emits banner to stderr when catalog has zero entries", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "cat-"));
  const f = path.join(tmp, "empty.json");
  fs.writeFileSync(f, JSON.stringify({ schema_version: 1, entries: [] }));
  const out = runHook(f);
  assert.match(out.stderr, /\[CATALOG ERROR\]/);
});

test("emits [CATALOG STALE] banner to stderr when source newer than catalog", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "cat-"));
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "claude-home-"));
  makeFakeClaudeHome(home);

  const catalogFile = path.join(tmp, "catalog.json");
  fs.writeFileSync(
    catalogFile,
    JSON.stringify({ schema_version: 1, entries: [{ name: "x", kind: "skill" }] })
  );

  const pastTime = new Date(Date.now() - 10 * 60 * 1000);
  fs.utimesSync(catalogFile, pastTime, pastTime);

  const skillFile = path.join(home, "skills", "example", "SKILL.md");
  fs.writeFileSync(skillFile, "# newer skill");

  const out = runHook(catalogFile, home);
  assert.ok(out.stderr && out.stderr.length > 0, "stderr should be non-empty for stale catalog");
  assert.match(out.stderr, /\[CATALOG STALE\]/);
});

test("stderr matches additionalContext for catalog-missing banner", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "cat-"));
  const out = runHook(path.join(tmp, "absent.json"));
  const parsed = JSON.parse(out.stdout);
  const bannerText = parsed.hookSpecificOutput.additionalContext;
  // stderr should contain the same text that's in additionalContext
  assert.ok(out.stderr.includes(bannerText), "stderr should contain the full banner text");
});

test("setup-state MISSING banner surfaces to stderr", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "wayfinder-hooktest-"));
  try {
    const result = runHookWithPluginData({ pluginData: tmp });
    assert.ok(result.stderr && result.stderr.length > 0, "stderr should be non-empty for MISSING");
    assert.match(result.stderr, /requires setup/);
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});
