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
 * Run the hook with the given env overrides and an empty UserPromptSubmit
 * payload on stdin.
 *
 * @param {Record<string, string>} envOverrides
 * @returns {{ stdout: string, stderr: string, exitCode: number }}
 */
function runHook(envOverrides) {
  const env = { ...process.env, ...envOverrides };
  const r = spawnSync("node", [HOOK], {
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
