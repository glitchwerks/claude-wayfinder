// Tests for refresh-catalog-on-stale.js — UserPromptSubmit hook that
// auto-regenerates the dispatch catalog when source files are newer than
// the catalog file.
//
// Issue #336.

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const HOOK = path.join(__dirname, "..", "refresh-catalog-on-stale.js");
// Sentinel written by the fake generator to prove it was invoked.
const SENTINEL_FILE_NAME = "generator-was-called.txt";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Build a minimal fake ~/.claude tree:
 *   <base>/state/dispatch-catalog.json  (not created here — tests do it)
 *   <base>/skills/example/SKILL.md
 *   <base>/agents/example.md
 *
 * @param {string} base - Temp directory to use as CLAUDE_HOME
 * @returns {{ skillFile: string, agentFile: string, catalogFile: string, sentinelFile: string }}
 */
function makeFakeClaudeHome(base) {
  fs.mkdirSync(path.join(base, "state"), { recursive: true });
  fs.mkdirSync(path.join(base, "skills", "example"), { recursive: true });
  fs.mkdirSync(path.join(base, "agents"), { recursive: true });

  const skillFile = path.join(base, "skills", "example", "SKILL.md");
  const agentFile = path.join(base, "agents", "example.md");
  const catalogFile = path.join(base, "state", "dispatch-catalog.json");
  const sentinelFile = path.join(base, SENTINEL_FILE_NAME);

  fs.writeFileSync(skillFile, "# example skill");
  fs.writeFileSync(agentFile, "# example agent");

  return { skillFile, agentFile, catalogFile, sentinelFile };
}

/**
 * Write a valid catalog JSON to a file.
 *
 * @param {string} filePath
 */
function writeCatalog(filePath) {
  fs.writeFileSync(
    filePath,
    JSON.stringify({ schema_version: 1, entries: [{ name: "x", kind: "skill" }] })
  );
}

/**
 * Build an absolute path to a fake Python generator script.
 * The generator writes a sentinel file on success (exit 0) or just exits non-zero.
 *
 * @param {string} tmpDir        - Directory to put the script in
 * @param {string} sentinelFile  - Path to touch on invocation
 * @param {number} exitCode      - Exit code the generator should return (default 0)
 * @returns {string}             Absolute path to the script
 */
function makeFakeGenerator(tmpDir, sentinelFile, exitCode = 0) {
  const scriptPath = path.join(tmpDir, "fake_generator.js");
  fs.writeFileSync(
    scriptPath,
    [
      "#!/usr/bin/env node",
      `const fs = require("node:fs");`,
      `fs.writeFileSync(${JSON.stringify(sentinelFile)}, "called");`,
      `process.exit(${exitCode});`,
    ].join("\n")
  );
  return scriptPath;
}

/**
 * Create a valid setup-state flag in a temp directory so the flag-guard
 * in refresh-catalog-on-stale.js passes. The venv python placeholder is a
 * real file (not executable — the default branch uses the flag's venv_path
 * to resolve spawnSync(venvPython, ...) which will fail/ENOENT, but that only
 * matters when the generator is actually invoked via the default path, not
 * the DISPATCH_GENERATOR_CMD test seam).
 *
 * @param {string} tmpDir - directory to create flag in (e.g. a per-test tmp)
 * @returns {{ pluginDataDir: string }} - directory containing setup-state.json
 */
function createValidSetupStateFlag(tmpDir) {
  const pluginDataDir = path.join(tmpDir, "plugin-data");
  const venvDir = path.join(pluginDataDir, "venv");
  const venvBin = path.join(venvDir, process.platform === "win32" ? "Scripts" : "bin");
  const pythonBin = path.join(venvBin, process.platform === "win32" ? "python.exe" : "python");
  fs.mkdirSync(venvBin, { recursive: true });
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
  return { pluginDataDir };
}

const REPO_ROOT = path.resolve(__dirname, "..", "..");

/**
 * Run the hook with the given env overrides and an empty UserPromptSubmit
 * payload on stdin. Automatically provides a valid setup-state flag so
 * the Phase 2 flag-guard passes for all existing catalog-health tests.
 *
 * @param {Record<string, string>} envOverrides
 * @returns {{ stdout: string, stderr: string, exitCode: number }}
 */
function runHook(envOverrides) {
  // Create a valid flag in a sub-dir of any provided CLAUDE_HOME, or a fresh tmp.
  const baseDir = envOverrides.CLAUDE_HOME
    ? envOverrides.CLAUDE_HOME
    : fs.mkdtempSync(path.join(os.tmpdir(), "rcos-flagtmp-"));
  const { pluginDataDir } = createValidSetupStateFlag(baseDir);

  const env = {
    ...process.env,
    CLAUDE_PLUGIN_DATA: pluginDataDir,
    CLAUDE_PLUGIN_ROOT: REPO_ROOT,
    ...envOverrides,
  };
  const r = spawnSync(process.execPath, [HOOK], {
    input: JSON.stringify({ prompt: "hello" }),
    encoding: "utf8",
    timeout: 15_000,
    env,
  });
  return { stdout: r.stdout ?? "", stderr: r.stderr ?? "", exitCode: r.status ?? 0 };
}

// ---------------------------------------------------------------------------
// RED: tests written before the hook implementation exists.
// ---------------------------------------------------------------------------

test("stale catalog triggers regeneration — source file newer than catalog", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "rcos-"));
  const { skillFile, catalogFile, sentinelFile } = makeFakeClaudeHome(tmp);
  const generatorScript = makeFakeGenerator(tmp, sentinelFile, 0);

  // Write catalog with an old mtime.
  writeCatalog(catalogFile);
  const pastTime = new Date(Date.now() - 10 * 60 * 1000); // 10 min ago
  fs.utimesSync(catalogFile, pastTime, pastTime);

  // Source file mtime defaults to now — newer than the catalog.
  // (skillFile was just written, so its mtime is "now")
  fs.utimesSync(skillFile, new Date(), new Date());

  const result = runHook({
    CLAUDE_HOME: tmp,
    DISPATCH_CATALOG_PATH: catalogFile,
    DISPATCH_GENERATOR_CMD: `node ${generatorScript}`,
  });

  // Generator must have been called (sentinel exists).
  assert.ok(
    fs.existsSync(sentinelFile),
    `Generator was not called. stdout: ${result.stdout}, stderr: ${result.stderr}`
  );
  // Hook must exit 0 — never blocks the prompt.
  assert.equal(result.exitCode, 0, `Expected exit 0 but got ${result.exitCode}`);
  // Stdout must be empty or contain only additionalContext (no deny).
  if (result.stdout.trim()) {
    const parsed = JSON.parse(result.stdout);
    assert.ok(
      !parsed.hookSpecificOutput?.permissionDecision,
      "Hook must not emit a permissionDecision"
    );
  }
});

test("fresh catalog — source files older than catalog — no regeneration", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "rcos-"));
  const { skillFile, agentFile, catalogFile, sentinelFile } = makeFakeClaudeHome(tmp);
  const generatorScript = makeFakeGenerator(tmp, sentinelFile, 0);

  // Source files are 10 minutes old.
  const pastTime = new Date(Date.now() - 10 * 60 * 1000);
  fs.utimesSync(skillFile, pastTime, pastTime);
  fs.utimesSync(agentFile, pastTime, pastTime);

  // Catalog written after source files, so it is the newest.
  writeCatalog(catalogFile);
  // (catalog mtime defaults to now)

  const result = runHook({
    CLAUDE_HOME: tmp,
    DISPATCH_CATALOG_PATH: catalogFile,
    DISPATCH_GENERATOR_CMD: `node ${generatorScript}`,
  });

  // Generator must NOT have been called.
  assert.ok(
    !fs.existsSync(sentinelFile),
    `Generator was called unexpectedly. stdout: ${result.stdout}`
  );
  // Silent no-op: empty stdout, exit 0.
  assert.equal(result.exitCode, 0);
  assert.equal(result.stdout.trim(), "");
});

test("missing catalog triggers regeneration", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "rcos-"));
  const { catalogFile, sentinelFile } = makeFakeClaudeHome(tmp);
  const generatorScript = makeFakeGenerator(tmp, sentinelFile, 0);

  // Do NOT write the catalog — it is absent.
  assert.ok(!fs.existsSync(catalogFile), "Test setup error: catalog should be absent");

  const result = runHook({
    CLAUDE_HOME: tmp,
    DISPATCH_CATALOG_PATH: catalogFile,
    DISPATCH_GENERATOR_CMD: `node ${generatorScript}`,
  });

  // Generator must have been called (catalog was missing).
  assert.ok(
    fs.existsSync(sentinelFile),
    `Generator was not called for missing catalog. stdout: ${result.stdout}`
  );
  assert.equal(result.exitCode, 0);
});

test("generator failure emits additionalContext with error details — does not block prompt", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "rcos-"));
  const { skillFile, catalogFile, sentinelFile } = makeFakeClaudeHome(tmp);
  // Generator exits 1 (failure).
  const generatorScript = makeFakeGenerator(tmp, sentinelFile, 1);

  // Stale catalog so regeneration is triggered.
  writeCatalog(catalogFile);
  const pastTime = new Date(Date.now() - 10 * 60 * 1000);
  fs.utimesSync(catalogFile, pastTime, pastTime);
  fs.utimesSync(skillFile, new Date(), new Date());

  const result = runHook({
    CLAUDE_HOME: tmp,
    DISPATCH_CATALOG_PATH: catalogFile,
    DISPATCH_GENERATOR_CMD: `node ${generatorScript}`,
  });

  // Hook must exit 0 — never blocks prompt even on generator failure.
  assert.equal(result.exitCode, 0, `Expected exit 0 but got ${result.exitCode}`);
  // Must emit additionalContext with error information.
  assert.ok(result.stdout.trim(), "Expected non-empty stdout on generator failure");
  const parsed = JSON.parse(result.stdout);
  assert.ok(parsed.hookSpecificOutput?.additionalContext, "Expected additionalContext in output");
  assert.match(
    parsed.hookSpecificOutput.additionalContext,
    /CATALOG/i,
    "additionalContext should mention CATALOG"
  );
  // Must NOT emit a deny decision.
  assert.ok(
    !parsed.hookSpecificOutput?.permissionDecision,
    "Hook must not emit permissionDecision on generator failure"
  );
});

test("CLAUDE_HOME env override controls which source files are scanned", () => {
  const _tmp1 = fs.mkdtempSync(path.join(os.tmpdir(), "rcos-home1-"));
  const tmp2 = fs.mkdtempSync(path.join(os.tmpdir(), "rcos-home2-"));

  // home2: stale catalog + newer skill.
  const {
    skillFile: skillFile2,
    catalogFile: catalogFile2,
    sentinelFile,
  } = makeFakeClaudeHome(tmp2);
  const generatorScript = makeFakeGenerator(tmp2, sentinelFile, 0);

  writeCatalog(catalogFile2);
  const pastTime = new Date(Date.now() - 10 * 60 * 1000);
  fs.utimesSync(catalogFile2, pastTime, pastTime);
  fs.utimesSync(skillFile2, new Date(), new Date());

  // Use CLAUDE_HOME=tmp2, DISPATCH_CATALOG_PATH pointing into tmp2.
  const result = runHook({
    CLAUDE_HOME: tmp2,
    DISPATCH_CATALOG_PATH: catalogFile2,
    DISPATCH_GENERATOR_CMD: `node ${generatorScript}`,
  });

  // Generator was called (tmp2 is stale).
  assert.ok(
    fs.existsSync(sentinelFile),
    `Generator was not called for tmp2. stdout: ${result.stdout}`
  );
  assert.equal(result.exitCode, 0);
});

test("legacy catalog without built_for_project field is treated as fresh", () => {
  // Regression guard for the fix introduced in #386: catalogs generated before
  // the built_for_project field was added must NOT trigger a rebuild.  Previously
  // the hook used `|| null` which conflated "field absent" with "field is null",
  // causing every legacy catalog to force a rebuild on first run after upgrade.
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "rcos-"));
  const { skillFile, agentFile, catalogFile, sentinelFile } = makeFakeClaudeHome(tmp);
  const generatorScript = makeFakeGenerator(tmp, sentinelFile, 0);

  // Source files are older than the catalog.
  const pastTime = new Date(Date.now() - 10 * 60 * 1000);
  fs.utimesSync(skillFile, pastTime, pastTime);
  fs.utimesSync(agentFile, pastTime, pastTime);

  // Write a catalog that intentionally lacks the built_for_project field
  // (simulating a catalog generated by an older version of the generator).
  fs.writeFileSync(
    catalogFile,
    JSON.stringify({ schema_version: 1, entries: [{ name: "x", kind: "skill" }] })
  );
  // (catalog mtime defaults to now — newer than source files)

  const result = runHook({
    CLAUDE_HOME: tmp,
    DISPATCH_CATALOG_PATH: catalogFile,
    DISPATCH_GENERATOR_CMD: `node ${generatorScript}`,
  });

  // Generator must NOT have been called — legacy catalog is treated as fresh.
  assert.ok(
    !fs.existsSync(sentinelFile),
    `Generator was called for a legacy catalog without built_for_project. stdout: ${result.stdout}`
  );
  assert.equal(result.exitCode, 0);
  assert.equal(result.stdout.trim(), "");
});

test("catalog built for different project root forces rebuild — real project-switch detection", () => {
  // Ensures the three-state fix did not break the actual project-switch case:
  // when built_for_project IS present but differs from the current project root,
  // the hook must force a rebuild even if source file mtimes are clean.
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "rcos-"));
  const { skillFile, agentFile, catalogFile, sentinelFile } = makeFakeClaudeHome(tmp);
  const generatorScript = makeFakeGenerator(tmp, sentinelFile, 0);

  // Source files are older than the catalog.
  const pastTime = new Date(Date.now() - 10 * 60 * 1000);
  fs.utimesSync(skillFile, pastTime, pastTime);
  fs.utimesSync(agentFile, pastTime, pastTime);

  // Write a catalog with built_for_project pointing at a *different* directory.
  const otherProjectRoot = path.join(tmp, "other-project");
  fs.writeFileSync(
    catalogFile,
    JSON.stringify({
      schema_version: 1,
      entries: [{ name: "x", kind: "skill" }],
      built_for_project: otherProjectRoot,
    })
  );
  // (catalog mtime defaults to now — source files are older, so mtime check alone
  //  would NOT trigger a rebuild; only the project-switch check should.)

  const result = runHook({
    CLAUDE_HOME: tmp,
    DISPATCH_CATALOG_PATH: catalogFile,
    DISPATCH_GENERATOR_CMD: `node ${generatorScript}`,
  });

  // Generator MUST have been called — the catalog was built for a different project.
  assert.ok(
    fs.existsSync(sentinelFile),
    `Generator was not called for a project-switch catalog. stdout: ${result.stdout}`
  );
  assert.equal(result.exitCode, 0);
});

test("DISPATCH_CATALOG_PATH env override is honoured", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "rcos-"));
  const { skillFile, sentinelFile } = makeFakeClaudeHome(tmp);
  const generatorScript = makeFakeGenerator(tmp, sentinelFile, 0);

  // Put the catalog at a non-default path.
  const customCatalogPath = path.join(tmp, "custom-catalog.json");
  writeCatalog(customCatalogPath);
  const pastTime = new Date(Date.now() - 10 * 60 * 1000);
  fs.utimesSync(customCatalogPath, pastTime, pastTime);
  fs.utimesSync(skillFile, new Date(), new Date());

  const result = runHook({
    CLAUDE_HOME: tmp,
    DISPATCH_CATALOG_PATH: customCatalogPath,
    DISPATCH_GENERATOR_CMD: `node ${generatorScript}`,
  });

  // Generator must have been called via the custom catalog path.
  assert.ok(
    fs.existsSync(sentinelFile),
    `Generator not called with custom catalog path. stdout: ${result.stdout}`
  );
  assert.equal(result.exitCode, 0);
});

// ---------------------------------------------------------------------------
// Plugin churn detection tests (Issue #479)
// ---------------------------------------------------------------------------

/**
 * Build a fake plugin cache tree rooted at <base>/plugins/cache/:
 *   <base>/plugins/cache/<publisher>/<plugin>/<version>/skills/<skill>/SKILL.md
 *   <base>/plugins/cache/<publisher>/<plugin>/<version>/agents/<agent>.md
 *   <base>/plugins/installed_plugins.json
 *
 * Returns file paths for use in mtime manipulation.
 *
 * @param {string} base - Temp directory to use as CLAUDE_HOME
 * @returns {{ pluginSkillFile: string, pluginAgentFile: string, installedPluginsFile: string }}
 */
function makeFakePluginCache(base) {
  const versionDir = path.join(base, "plugins", "cache", "mypublisher", "myplugin", "1.0.0");
  fs.mkdirSync(path.join(versionDir, "skills", "my-skill"), { recursive: true });
  fs.mkdirSync(path.join(versionDir, "agents"), { recursive: true });
  fs.mkdirSync(path.join(base, "plugins"), { recursive: true });

  const pluginSkillFile = path.join(versionDir, "skills", "my-skill", "SKILL.md");
  const pluginAgentFile = path.join(versionDir, "agents", "my-agent.md");
  const installedPluginsFile = path.join(base, "plugins", "installed_plugins.json");

  fs.writeFileSync(pluginSkillFile, "# my plugin skill");
  fs.writeFileSync(pluginAgentFile, "# my plugin agent");
  fs.writeFileSync(installedPluginsFile, JSON.stringify({ version: 2, plugins: {} }));

  return { pluginSkillFile, pluginAgentFile, installedPluginsFile };
}

test("plugin SKILL.md newer than catalog triggers regeneration", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "rcos-plugin-"));
  const { skillFile, agentFile, catalogFile, sentinelFile } = makeFakeClaudeHome(tmp);
  const { pluginSkillFile, installedPluginsFile } = makeFakePluginCache(tmp);
  const generatorScript = makeFakeGenerator(tmp, sentinelFile, 0);

  // All owned source files, plugin files, and installed_plugins.json are old.
  const pastTime = new Date(Date.now() - 10 * 60 * 1000);
  fs.utimesSync(skillFile, pastTime, pastTime);
  fs.utimesSync(agentFile, pastTime, pastTime);
  fs.utimesSync(pluginSkillFile, pastTime, pastTime);
  fs.utimesSync(installedPluginsFile, pastTime, pastTime);

  // Write catalog and backdate it to the past so the plugin file is clearly newer.
  writeCatalog(catalogFile);
  fs.utimesSync(catalogFile, pastTime, pastTime);

  // Plugin SKILL.md is "now" — clearly newer than the backdated catalog.
  fs.utimesSync(pluginSkillFile, new Date(), new Date());

  const result = runHook({
    CLAUDE_HOME: tmp,
    DISPATCH_CATALOG_PATH: catalogFile,
    DISPATCH_GENERATOR_CMD: `node ${generatorScript}`,
  });

  assert.ok(
    fs.existsSync(sentinelFile),
    `Generator was not called when plugin SKILL.md is newer than catalog. stdout: ${result.stdout}`
  );
  assert.equal(result.exitCode, 0);
});

test("plugin agents/*.md newer than catalog triggers regeneration", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "rcos-plugin-"));
  const { skillFile, agentFile, catalogFile, sentinelFile } = makeFakeClaudeHome(tmp);
  const { pluginSkillFile, pluginAgentFile, installedPluginsFile } = makeFakePluginCache(tmp);
  const generatorScript = makeFakeGenerator(tmp, sentinelFile, 0);

  // All owned source files and plugin files are old.
  const pastTime = new Date(Date.now() - 10 * 60 * 1000);
  fs.utimesSync(skillFile, pastTime, pastTime);
  fs.utimesSync(agentFile, pastTime, pastTime);
  fs.utimesSync(pluginSkillFile, pastTime, pastTime);
  fs.utimesSync(pluginAgentFile, pastTime, pastTime);
  fs.utimesSync(installedPluginsFile, pastTime, pastTime);

  // Write catalog and backdate it so the plugin agent file is clearly newer.
  writeCatalog(catalogFile);
  fs.utimesSync(catalogFile, pastTime, pastTime);

  // Plugin agent file is "now" — clearly newer than the backdated catalog.
  fs.utimesSync(pluginAgentFile, new Date(), new Date());

  const result = runHook({
    CLAUDE_HOME: tmp,
    DISPATCH_CATALOG_PATH: catalogFile,
    DISPATCH_GENERATOR_CMD: `node ${generatorScript}`,
  });

  assert.ok(
    fs.existsSync(sentinelFile),
    `Generator was not called when plugin agent file is newer than catalog. stdout: ${result.stdout}`
  );
  assert.equal(result.exitCode, 0);
});

test("installed_plugins.json mtime newer than catalog triggers regeneration", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "rcos-plugin-"));
  const { skillFile, agentFile, catalogFile, sentinelFile } = makeFakeClaudeHome(tmp);
  const { pluginSkillFile, pluginAgentFile, installedPluginsFile } = makeFakePluginCache(tmp);
  const generatorScript = makeFakeGenerator(tmp, sentinelFile, 0);

  // All owned source files and plugin files are old.
  const pastTime = new Date(Date.now() - 10 * 60 * 1000);
  fs.utimesSync(skillFile, pastTime, pastTime);
  fs.utimesSync(agentFile, pastTime, pastTime);
  fs.utimesSync(pluginSkillFile, pastTime, pastTime);
  fs.utimesSync(pluginAgentFile, pastTime, pastTime);
  fs.utimesSync(installedPluginsFile, pastTime, pastTime);

  // Write catalog and backdate it so installed_plugins.json can be clearly newer.
  writeCatalog(catalogFile);
  fs.utimesSync(catalogFile, pastTime, pastTime);

  // installed_plugins.json touched "now" — simulates install/uninstall/version-bump.
  fs.utimesSync(installedPluginsFile, new Date(), new Date());

  const result = runHook({
    CLAUDE_HOME: tmp,
    DISPATCH_CATALOG_PATH: catalogFile,
    DISPATCH_GENERATOR_CMD: `node ${generatorScript}`,
  });

  assert.ok(
    fs.existsSync(sentinelFile),
    `Generator was not called when installed_plugins.json is newer than catalog. stdout: ${result.stdout}`
  );
  assert.equal(result.exitCode, 0);
});

test("all plugin files older than catalog — no spurious regeneration from plugin tree", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "rcos-plugin-"));
  const { skillFile, agentFile, catalogFile, sentinelFile } = makeFakeClaudeHome(tmp);
  const { pluginSkillFile, pluginAgentFile, installedPluginsFile } = makeFakePluginCache(tmp);
  const generatorScript = makeFakeGenerator(tmp, sentinelFile, 0);

  // All source files (owned + plugin) are old.
  const pastTime = new Date(Date.now() - 10 * 60 * 1000);
  fs.utimesSync(skillFile, pastTime, pastTime);
  fs.utimesSync(agentFile, pastTime, pastTime);
  fs.utimesSync(pluginSkillFile, pastTime, pastTime);
  fs.utimesSync(pluginAgentFile, pastTime, pastTime);
  fs.utimesSync(installedPluginsFile, pastTime, pastTime);

  // Catalog is newest — should be treated as fresh.
  writeCatalog(catalogFile);
  // (catalog mtime defaults to now)

  const result = runHook({
    CLAUDE_HOME: tmp,
    DISPATCH_CATALOG_PATH: catalogFile,
    DISPATCH_GENERATOR_CMD: `node ${generatorScript}`,
  });

  // Generator must NOT have been called.
  assert.ok(
    !fs.existsSync(sentinelFile),
    `Generator was called unexpectedly when plugin files are older than catalog. stdout: ${result.stdout}`
  );
  assert.equal(result.exitCode, 0);
  assert.equal(result.stdout.trim(), "");
});

test("missing installed_plugins.json is silently skipped — no crash", () => {
  // When the plugins directory doesn't exist (e.g. no plugins ever installed),
  // the hook must not crash — it should behave identically to the no-plugin case.
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "rcos-plugin-"));
  const { skillFile, agentFile, catalogFile, sentinelFile } = makeFakeClaudeHome(tmp);
  const generatorScript = makeFakeGenerator(tmp, sentinelFile, 0);
  // Note: we do NOT call makeFakePluginCache — no plugins directory at all.

  // All owned source files are old.
  const pastTime = new Date(Date.now() - 10 * 60 * 1000);
  fs.utimesSync(skillFile, pastTime, pastTime);
  fs.utimesSync(agentFile, pastTime, pastTime);

  // Catalog is newest.
  writeCatalog(catalogFile);

  const result = runHook({
    CLAUDE_HOME: tmp,
    DISPATCH_CATALOG_PATH: catalogFile,
    DISPATCH_GENERATOR_CMD: `node ${generatorScript}`,
  });

  // Generator must NOT have been called — no staleness from missing plugin tree.
  assert.ok(
    !fs.existsSync(sentinelFile),
    `Generator was called unexpectedly when plugin tree is absent. stdout: ${result.stdout}`
  );
  assert.equal(result.exitCode, 0);
  assert.equal(result.stdout.trim(), "");
});

test("default generator command uses python module invocation — not legacy python script path", () => {
  // Regression guard for issue #76 (and the earlier #64 guard it extends):
  //
  //   v0.3.1 default:  python <CLAUDE_HOME>/scripts/build_dispatch_catalog.py
  //   v0.3.2 default:  claude-wayfinder catalog build   ← regressed ENOENT (#76)
  //   v0.3.3 default:  python -m claude_wayfinder catalog build  ← this fix
  //
  // The entry-point shim `claude-wayfinder` lives in the venv's bin/Scripts
  // directory and is only on PATH when the venv is activated — a condition the
  // hook's child process cannot rely on. Module invocation is robust whenever
  // `python` on PATH has `claude_wayfinder` importable (Pattern A install).
  //
  // This test asserts two things about the hook's DEFAULT_GENERATOR_CMD:
  //   1. It does NOT reference the legacy `build_dispatch_catalog.py` path.
  //   2. It spawns `python` with args that include `-m claude_wayfinder`.
  //
  // When DISPATCH_GENERATOR_CMD is NOT set and the spawn fails (e.g. `python`
  // not on PATH in the test runner, or claude_wayfinder not importable), the
  // hook must still exit 0 and emit additionalContext — never block the prompt.
  // We assert on the command the hook *attempts* to spawn, not on success.
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "rcos-default-cmd-"));
  const { skillFile, catalogFile } = makeFakeClaudeHome(tmp);

  // Stale catalog so a rebuild is attempted.
  writeCatalog(catalogFile);
  const pastTime = new Date(Date.now() - 10 * 60 * 1000);
  fs.utimesSync(catalogFile, pastTime, pastTime);
  fs.utimesSync(skillFile, new Date(), new Date());

  // Build env without DISPATCH_GENERATOR_CMD so the hook uses its default.
  const env = { ...process.env };
  delete env.DISPATCH_GENERATOR_CMD;

  const r = spawnSync("node", [HOOK], {
    input: JSON.stringify({ prompt: "hello" }),
    encoding: "utf8",
    timeout: 15_000,
    env: { ...env, CLAUDE_HOME: tmp, DISPATCH_CATALOG_PATH: catalogFile },
  });

  const stdout = r.stdout ?? "";
  const stderr = r.stderr ?? "";
  const exitCode = r.status ?? 0;

  // The hook must always exit 0 — never block the prompt.
  assert.equal(exitCode, 0, `Expected exit 0 but got ${exitCode}. stderr: ${stderr}`);

  // Inspect additionalContext when the spawn fails (expected in most CI/test
  // environments). The error message must:
  //   - NOT reference the legacy private-harness script.
  //   - NOT indicate `claude-wayfinder` (bare entry-point shim, v0.3.2 regression).
  //   - Reflect that `python` was the program attempted (module invocation).
  if (stdout.trim()) {
    const parsed = JSON.parse(stdout);
    const ctx = parsed.hookSpecificOutput?.additionalContext ?? "";

    // Guard 1 (issue #64 regression): no legacy script path.
    assert.ok(
      !ctx.includes("build_dispatch_catalog.py"),
      `additionalContext must not reference the legacy python script. Got: ${ctx}`
    );

    // Guard 2 (issue #76 regression): no bare entry-point shim.
    // If the error mentions 'claude-wayfinder' as the failed program, the hook
    // is still using the v0.3.2 shim invocation, not the module invocation.
    // Allow "claude_wayfinder" (module name with underscore) — only block the
    // hyphenated entry-point binary name appearing as the first token.
    assert.ok(
      !ctx.includes("'claude-wayfinder'") && !ctx.includes('"claude-wayfinder"'),
      `additionalContext must not indicate the bare entry-point shim failed. Got: ${ctx}`
    );

    // Must not emit a deny decision.
    assert.ok(
      !parsed.hookSpecificOutput?.permissionDecision,
      "Hook must not emit permissionDecision"
    );
  }

  // The combined output must not reference the old private-harness path.
  const combined = stdout + stderr;
  assert.ok(
    !combined.includes("build_dispatch_catalog.py"),
    `Hook output must not reference legacy python path. combined: ${combined}`
  );
});

test("DEFAULT_GENERATOR_CMD constant is python module invocation form", () => {
  // Structural assertion: read the hook source and verify the DEFAULT_GENERATOR_CMD
  // literal is the expected module-invocation string. This catches a regression
  // at the source level — independent of whether `python` is on PATH — so the
  // test is fully deterministic in all environments.
  //
  // This test would have caught the v0.3.2 regression (#76) where the constant
  // was changed to `claude-wayfinder catalog build` (bare shim invocation).
  const hookSource = fs.readFileSync(HOOK, "utf8");

  // The constant must be assigned the module-invocation form.
  assert.ok(
    hookSource.includes('DEFAULT_GENERATOR_CMD = "python -m claude_wayfinder catalog build"'),
    "DEFAULT_GENERATOR_CMD must be 'python -m claude_wayfinder catalog build'. " +
      "If you see this failure, the hook was changed back to a bare entry-point shim " +
      "(issue #76 regression) or some other non-module invocation."
  );

  // Belt-and-suspenders: must NOT be the v0.3.2 regression value.
  assert.ok(
    !hookSource.includes('DEFAULT_GENERATOR_CMD = "claude-wayfinder catalog build"'),
    "DEFAULT_GENERATOR_CMD must not be the bare entry-point shim 'claude-wayfinder catalog build' " +
      "(that was the v0.3.2 regression fixed in #76)."
  );
});

// ---------------------------------------------------------------------------
// CLAUDE_WAYFINDER_PYTHON env-var override tests (Issue #82, closes #80)
// ---------------------------------------------------------------------------
//
// These tests verify the new env-var override path introduced in v0.3.4 as a
// stopgap for consumers whose `python` on PATH does not have `claude_wayfinder`
// importable (e.g. installed into a non-activated venv). The canonical fix is
// tracked in #81 (${CLAUDE_PLUGIN_DATA} SessionStart-materialized venv).
//
// All three tests use the DISPATCH_GENERATOR_CMD integration seam to redirect
// to a fake generator that records its argv. They then verify the spawned
// program/args without requiring real Python or a real claude_wayfinder install.

/**
 * Build a fake generator that records the argv it was called with to a file.
 *
 * The recorded format is one JSON array per line in <argvFile>:
 *   ["node", "/path/to/fake_argv_gen.js", "-m", "claude_wayfinder", ...]
 *
 * We cannot use this to intercept the NEW args-array spawn path (which bypasses
 * DISPATCH_GENERATOR_CMD), but we can use it to verify the DISPATCH_GENERATOR_CMD
 * legacy path still works. For the new path tests, we inspect the banner emitted
 * when the spawn fails (ENOENT or ModuleNotFoundError) or we use a wrapper script
 * that records argv and exits 0.
 *
 * @param {string} tmpDir    - Directory to put the script in
 * @param {string} argvFile  - Path to write argv JSON to
 * @param {number} exitCode  - Exit code the generator should return (default 0)
 * @returns {string}         Absolute path to the script
 */
function makeFakeArgvGenerator(tmpDir, argvFile, exitCode = 0) {
  const scriptPath = path.join(tmpDir, "fake_argv_gen.js");
  fs.writeFileSync(
    scriptPath,
    [
      "#!/usr/bin/env node",
      `const fs = require("node:fs");`,
      // process.argv is ["node", scriptPath, ...extraArgs]
      `fs.writeFileSync(${JSON.stringify(argvFile)}, JSON.stringify(process.argv));`,
      `process.exit(${exitCode});`,
    ].join("\n")
  );
  return scriptPath;
}

test("hook uses venvPython from setup-state flag as the interpreter (v0.4 design)", () => {
  // Phase 2 (Issue #104): the hook now resolves the Python interpreter from the
  // setup-state.json flag (venvPython = getVenvPython(flag.venv_path)) rather than
  // the v0.3.x CLAUDE_WAYFINDER_PYTHON env-var approach. This test guards that
  // the source uses `venvPython` and not the legacy `pythonProg` / `CLAUDE_WAYFINDER_PYTHON`.
  const hookSource = fs.readFileSync(HOOK, "utf8");

  // The hook must reference venvPython (resolved from setup-state flag).
  assert.ok(
    hookSource.includes("venvPython"),
    "Hook source must use venvPython variable resolved from setup-state flag"
  );
  // The hook must NOT use the v0.3.x pythonProg variable outside of the parseCmd seam.
  // (pythonProg was the CLAUDE_WAYFINDER_PYTHON || "python" fallback, now removed.)
  assert.ok(
    !hookSource.includes("process.env.CLAUDE_WAYFINDER_PYTHON"),
    "Hook must not reference CLAUDE_WAYFINDER_PYTHON env var (v0.3.x removed in Phase 2)"
  );
  // The default spawn must use venvPython, not the old pythonProg.
  assert.ok(
    hookSource.includes("spawnSync(venvPython,"),
    "Hook must spawn using the venvPython variable (args-array form: spawnSync(venvPython, [...]))"
  );
  // The hook must still exit 0 fail-open (runtime guard).
  // When the generator fails (venv python doesn't exist or module not installed),
  // the hook must emit additionalContext but not block the prompt.
  const result = runHook({
    DISPATCH_CATALOG_PATH: "/nonexistent/path/catalog.json", // force stale/missing
  });
  assert.equal(result.exitCode, 0, `Hook must exit 0 (fail-open). stderr: ${result.stderr}`);
});

test("CLAUDE_WAYFINDER_PYTHON env var is no longer used — DISPATCH_GENERATOR_CMD seam still works", () => {
  // Regression guard: the v0.3.x CLAUDE_WAYFINDER_PYTHON env-var override is
  // removed in Phase 2. The DISPATCH_GENERATOR_CMD test seam must still work.
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "rcos-py-override-path-"));
  const { skillFile, catalogFile, sentinelFile } = makeFakeClaudeHome(tmp);

  writeCatalog(catalogFile);
  const pastTime = new Date(Date.now() - 10 * 60 * 1000);
  fs.utimesSync(catalogFile, pastTime, pastTime);
  fs.utimesSync(skillFile, new Date(), new Date());

  const generatorScript = makeFakeGenerator(tmp, sentinelFile, 0);

  // DISPATCH_GENERATOR_CMD seam must still work regardless of CLAUDE_WAYFINDER_PYTHON.
  const result = runHook({
    CLAUDE_HOME: tmp,
    DISPATCH_CATALOG_PATH: catalogFile,
    DISPATCH_GENERATOR_CMD: `node ${generatorScript}`,
  });

  assert.ok(
    fs.existsSync(sentinelFile),
    `Generator was not called via DISPATCH_GENERATOR_CMD seam. stdout: ${result.stdout}`
  );
  assert.equal(result.exitCode, 0);
});

// ---------------------------------------------------------------------------
// Issue #87 regression guard: bare DEFAULT_GENERATOR_CMD ships no extra args
// ---------------------------------------------------------------------------
//
// The bundled hook's DEFAULT_GENERATOR_CMD is `python -m claude_wayfinder
// catalog build`.  After issue #87's fix (CLI-side defaults for --skills-dir,
// --agents-dir, --out, --log), that bare invocation must succeed without any
// extra arguments appended by the hook.  This test locks in the contract:
//   - The hook spawns exactly ["python", "-m", "claude_wayfinder", "catalog",
//     "build", ...projectRootArgs] — no --skills-dir, --agents-dir, --out,
//     or --log appended at the hook layer.
//
// If this test fails, someone added argument injection to the hook, breaking
// the structural fix (defaults at the CLI, not at the hook).

test("DEFAULT_GENERATOR_CMD spawns bare invocation — no extra args injected by the hook", () => {
  // Structural assertion: verify the source does NOT append the four args.
  const hookSource = fs.readFileSync(HOOK, "utf8");

  // The hook must NOT pass --skills-dir, --agents-dir, --out, or --log
  // when building the args array for the default (non-DISPATCH_GENERATOR_CMD)
  // spawn path.  These must come from CLI defaults (issue #87), not from the hook.
  assert.ok(
    !hookSource.includes('"--skills-dir"'),
    'Hook must NOT inject "--skills-dir" into the spawn args. ' +
      "Issue #87 fix: defaults live at the CLI, not the hook."
  );
  assert.ok(
    !hookSource.includes('"--agents-dir"'),
    'Hook must NOT inject "--agents-dir" into the spawn args. ' +
      "Issue #87 fix: defaults live at the CLI, not the hook."
  );
  assert.ok(
    !hookSource.includes('"--out"'),
    'Hook must NOT inject "--out" into the spawn args. ' +
      "Issue #87 fix: defaults live at the CLI, not the hook."
  );
  assert.ok(
    !hookSource.includes('"--log"'),
    'Hook must NOT inject "--log" into the spawn args. ' +
      "Issue #87 fix: defaults live at the CLI, not the hook."
  );

  // The default spawn must be the bare module invocation with no extra path args.
  // (--project-root is permitted — it is not a defaulted arg.)
  // Phase 2: uses venvPython instead of the v0.3.x pythonProg variable.
  assert.ok(
    hookSource.includes(
      'spawnSync(venvPython, ["-m", "claude_wayfinder", "catalog", "build", ...projectRootArgs]'
    ),
    'Default spawn must be: spawnSync(venvPython, ["-m", "claude_wayfinder", "catalog", "build", ' +
      "...projectRootArgs]). The hook must not inject path args — those are now CLI defaults."
  );
});

test("venvPython path with spaces is passed as single argument, not split (v0.4 design)", () => {
  // Phase 2: venvPython replaces the v0.3.x pythonProg/CLAUDE_WAYFINDER_PYTHON approach.
  // The venv path (from setup-state.json flag.venv_path) may contain spaces on Windows
  // (e.g. "C:\\Users\\My User\\venv"). The spawnSync call must pass venvPython as a
  // single program argument — not split by parseCmd — to handle these paths correctly.
  //
  // Verification strategy: source inspection confirms the spawn uses an explicit
  // args array with venvPython (not parseCmd) for the non-DISPATCH_GENERATOR_CMD path.
  const hookSource = fs.readFileSync(HOOK, "utf8");

  // The default path must spawn with an explicit args array using venvPython (not parseCmd).
  assert.ok(
    hookSource.includes('spawnSync(venvPython, ["-m", "claude_wayfinder", "catalog", "build"'),
    'Hook must use explicit args array: spawnSync(venvPython, ["-m", "claude_wayfinder", "catalog", "build", ...]). ' +
      "This is the defense against Windows paths with spaces in the venv interpreter path."
  );
  // parseCmd must NOT be used on venvPython (only used for DISPATCH_GENERATOR_CMD seam).
  assert.ok(
    !hookSource.includes("parseCmd(venvPython)") && !hookSource.includes("parseCmd(pythonProg)"),
    "Hook must not pass venvPython through parseCmd — that would split paths with spaces."
  );

  // Runtime: hook exits 0 (fail-open) — the venv python fails to run the module
  // but the hook never blocks the prompt.
  const result = runHook({
    DISPATCH_CATALOG_PATH: "/nonexistent/path/catalog.json",
  });
  assert.equal(
    result.exitCode,
    0,
    `Hook must exit 0 (fail-open). stderr: ${result.stderr}`
  );
});

// ---------------------------------------------------------------------------
// Setup-state gate tests (Phase 2 — Issue #104)
// ---------------------------------------------------------------------------

test("refresh-catalog-on-stale exits silently when setup-state is MISSING", () => {
  // To get a reliable RED/GREEN distinction, set up a stale catalog scenario
  // AND no flag file. Without the flag guard, the hook would attempt a refresh
  // and either call the generator (which may emit output) or use the
  // DISPATCH_GENERATOR_CMD seam. Here we use a fake generator that writes a
  // sentinel — if the generator was called, the sentinel exists. The flag guard
  // must prevent that call when MISSING.
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "wayfinder-refreshtest-"));
  try {
    const { skillFile, catalogFile, sentinelFile } = makeFakeClaudeHome(tmp);
    const generatorScript = makeFakeGenerator(tmp, sentinelFile, 0);

    // Stale catalog — without the flag guard, the hook would call the generator.
    writeCatalog(catalogFile);
    const pastTime = new Date(Date.now() - 10 * 60 * 1000);
    fs.utimesSync(catalogFile, pastTime, pastTime);
    fs.utimesSync(skillFile, new Date(), new Date());

    // No flag file in tmp (pluginData) — MISSING state.
    const result = spawnSync(process.execPath, [HOOK], {
      input: "{}",
      env: {
        ...process.env,
        CLAUDE_PLUGIN_DATA: tmp,
        CLAUDE_PLUGIN_ROOT: path.resolve(__dirname, "..", ".."),
        CLAUDE_HOME: tmp,
        DISPATCH_CATALOG_PATH: catalogFile,
        DISPATCH_GENERATOR_CMD: `node ${generatorScript}`,
      },
      encoding: "utf8",
    });
    assert.equal(result.status, 0, `Hook should exit 0; got ${result.status}: ${result.stderr}`);
    assert.equal(result.stdout.trim(), "", "Hook should produce no stdout when flag MISSING");
    // Generator must NOT have been called — the flag guard should prevent it.
    assert.ok(
      !fs.existsSync(sentinelFile),
      "Generator must not be called when setup-state is MISSING"
    );
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

// ---------------------------------------------------------------------------
// Issue #140 — triggers/<plugin>/agents/ staleness detection
// ---------------------------------------------------------------------------

test("triggers/<plugin>/agents/*.yml newer than catalog triggers regeneration", () => {
  // When a user creates or modifies a plugin-agent sidecar override at
  // triggers/<plugin>/agents/<name>.yml, the catalog must be rebuilt.
  // This test verifies that maxSourceMtime() includes files under
  // triggers/<plugin>/agents/ in its staleness candidates.
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "rcos-triggers-"));
  const { skillFile, agentFile, catalogFile, sentinelFile } = makeFakeClaudeHome(tmp);
  const generatorScript = makeFakeGenerator(tmp, sentinelFile, 0);

  // Write a plugin-agent override sidecar.
  const triggersAgentsDir = path.join(tmp, "triggers", "superpowers", "agents");
  fs.mkdirSync(triggersAgentsDir, { recursive: true });
  const agentSidecarFile = path.join(triggersAgentsDir, "doc-writer.yml");
  fs.writeFileSync(
    agentSidecarFile,
    "triggers:\n  keywords:\n    - { term: \"document\", weight: 1.0 }\napplicable_skills: [\"*\"]\n"
  );

  // Backdate owned source files and the catalog so they are old.
  const pastTime = new Date(Date.now() - 10 * 60 * 1000);
  fs.utimesSync(skillFile, pastTime, pastTime);
  fs.utimesSync(agentFile, pastTime, pastTime);
  writeCatalog(catalogFile);
  fs.utimesSync(catalogFile, pastTime, pastTime);

  // Agent sidecar is "now" — newer than the backdated catalog.
  fs.utimesSync(agentSidecarFile, new Date(), new Date());

  const result = runHook({
    CLAUDE_HOME: tmp,
    DISPATCH_CATALOG_PATH: catalogFile,
    DISPATCH_GENERATOR_CMD: `node ${generatorScript}`,
  });

  assert.ok(
    fs.existsSync(sentinelFile),
    `Generator was not called when triggers/<plugin>/agents/*.yml is newer than catalog. stdout: ${result.stdout}, stderr: ${result.stderr}`
  );
  assert.equal(result.exitCode, 0);
});

test("triggers/<plugin>/agents/*.yml older than catalog — no spurious regeneration", () => {
  // When the agent override sidecar is older than the catalog, the hook
  // must NOT trigger a rebuild (no false positives).
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "rcos-triggers-fresh-"));
  const { skillFile, agentFile, catalogFile, sentinelFile } = makeFakeClaudeHome(tmp);
  const generatorScript = makeFakeGenerator(tmp, sentinelFile, 0);

  // Write a plugin-agent override sidecar.
  const triggersAgentsDir = path.join(tmp, "triggers", "superpowers", "agents");
  fs.mkdirSync(triggersAgentsDir, { recursive: true });
  const agentSidecarFile = path.join(triggersAgentsDir, "doc-writer.yml");
  fs.writeFileSync(
    agentSidecarFile,
    "triggers:\n  keywords:\n    - { term: \"document\", weight: 1.0 }\napplicable_skills: [\"*\"]\n"
  );

  // All source files (owned + agent sidecar) are old.
  const pastTime = new Date(Date.now() - 10 * 60 * 1000);
  fs.utimesSync(skillFile, pastTime, pastTime);
  fs.utimesSync(agentFile, pastTime, pastTime);
  fs.utimesSync(agentSidecarFile, pastTime, pastTime);

  // Catalog is "now" — newer than all source files.
  writeCatalog(catalogFile);
  // (catalog mtime defaults to now)

  const result = runHook({
    CLAUDE_HOME: tmp,
    DISPATCH_CATALOG_PATH: catalogFile,
    DISPATCH_GENERATOR_CMD: `node ${generatorScript}`,
  });

  assert.ok(
    !fs.existsSync(sentinelFile),
    `Generator was called unexpectedly when agent sidecar is older than catalog. stdout: ${result.stdout}`
  );
  assert.equal(result.exitCode, 0);
  assert.equal(result.stdout.trim(), "");
});

// Issue #148 — agents/*.triggers.yml colocated sidecar staleness detection
// ---------------------------------------------------------------------------

test("agents/*.triggers.yml newer than catalog triggers regeneration (owned)", () => {
  // When a user creates or modifies a colocated owned-agent sidecar at
  // agents/<name>.triggers.yml, the catalog must be rebuilt.
  // This test verifies that maxSourceMtime() includes *.triggers.yml files
  // in the owned agents directory in its staleness candidates.
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "rcos-owned-sidecar-"));
  const { skillFile, agentFile, catalogFile, sentinelFile } = makeFakeClaudeHome(tmp);
  const generatorScript = makeFakeGenerator(tmp, sentinelFile, 0);

  // Write a colocated owned-agent sidecar.
  const agentsDir = path.join(tmp, "agents");
  const colocatedSidecarFile = path.join(agentsDir, "example.triggers.yml");
  fs.writeFileSync(
    colocatedSidecarFile,
    "triggers:\n  keywords:\n    - { term: \"example\", weight: 1.0 }\napplicable_skills: [\"*\"]\n"
  );

  // Backdate owned source files and the catalog so they are old.
  const pastTime = new Date(Date.now() - 10 * 60 * 1000);
  fs.utimesSync(skillFile, pastTime, pastTime);
  fs.utimesSync(agentFile, pastTime, pastTime);
  writeCatalog(catalogFile);
  fs.utimesSync(catalogFile, pastTime, pastTime);

  // Colocated sidecar is "now" — newer than the backdated catalog.
  fs.utimesSync(colocatedSidecarFile, new Date(), new Date());

  const result = runHook({
    CLAUDE_HOME: tmp,
    DISPATCH_CATALOG_PATH: catalogFile,
    DISPATCH_GENERATOR_CMD: `node ${generatorScript}`,
  });

  assert.ok(
    fs.existsSync(sentinelFile),
    `Generator was not called when agents/*.triggers.yml is newer than catalog. stdout: ${result.stdout}, stderr: ${result.stderr}`
  );
  assert.equal(result.exitCode, 0);
});

test("agents/*.triggers.yml older than catalog — no spurious regeneration (owned)", () => {
  // When the owned colocated sidecar is older than the catalog, the hook
  // must NOT trigger a rebuild (no false positives).
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "rcos-owned-sidecar-fresh-"));
  const { skillFile, agentFile, catalogFile, sentinelFile } = makeFakeClaudeHome(tmp);
  const generatorScript = makeFakeGenerator(tmp, sentinelFile, 0);

  // Write a colocated owned-agent sidecar.
  const agentsDir = path.join(tmp, "agents");
  const colocatedSidecarFile = path.join(agentsDir, "example.triggers.yml");
  fs.writeFileSync(
    colocatedSidecarFile,
    "triggers:\n  keywords:\n    - { term: \"example\", weight: 1.0 }\napplicable_skills: [\"*\"]\n"
  );

  // All source files (owned + colocated sidecar) are old.
  const pastTime = new Date(Date.now() - 10 * 60 * 1000);
  fs.utimesSync(skillFile, pastTime, pastTime);
  fs.utimesSync(agentFile, pastTime, pastTime);
  fs.utimesSync(colocatedSidecarFile, pastTime, pastTime);

  // Catalog is "now" — newer than all source files.
  writeCatalog(catalogFile);
  // (catalog mtime defaults to now)

  const result = runHook({
    CLAUDE_HOME: tmp,
    DISPATCH_CATALOG_PATH: catalogFile,
    DISPATCH_GENERATOR_CMD: `node ${generatorScript}`,
  });

  assert.ok(
    !fs.existsSync(sentinelFile),
    `Generator was called unexpectedly when owned colocated sidecar is older than catalog. stdout: ${result.stdout}`
  );
  assert.equal(result.exitCode, 0);
  assert.equal(result.stdout.trim(), "");
});
