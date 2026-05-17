// hooks/lib/setup-state.js
// Shared helper for plugin hooks. Pure functions; no subprocess.
// Spec § 4.2: hooks read the setup-state.json flag at ${CLAUDE_PLUGIN_DATA}/
// to determine whether to spawn Python (VALID) or short-circuit (MISSING/STALE/BROKEN).

const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");

/**
 * Read and classify the setup-state flag.
 * @param {string} currentVersion - the plugin version from pyproject.toml
 * @returns {{status: "VALID"|"MISSING"|"STALE"|"BROKEN", flag?: object}}
 */
function readSetupState(currentVersion) {
  const flagPath = path.join(getPluginDataDir(), "setup-state.json");
  if (!fs.existsSync(flagPath)) {
    return { status: "MISSING" };
  }
  let flag;
  try {
    flag = JSON.parse(fs.readFileSync(flagPath, "utf8"));
  } catch (err) {
    process.stderr.write(`[setup-state] flag file unparseable: ${err.message}\n`);
    return { status: "MISSING" };
  }
  // Required fields per spec § 4.2
  if (typeof flag.version !== "string" || !flag.version.trim()) {
    // Flag is present but version field is malformed (empty, null, non-string).
    // This is corrupt-flag territory, not first-install — log a diagnostic so the
    // user can see something is wrong (e.g., interrupted setup writes).
    if (flag.version !== undefined) {
      process.stderr.write(
        `[setup-state] flag has malformed version field: ${JSON.stringify(flag.version)}\n`
      );
    }
    return { status: "MISSING" };
  }
  if (typeof flag.venv_path !== "string" || !flag.venv_path.trim()) {
    if (flag.venv_path !== undefined) {
      process.stderr.write(
        `[setup-state] flag has malformed venv_path field: ${JSON.stringify(flag.venv_path)}\n`
      );
    }
    return { status: "MISSING" };
  }
  if (flag.version !== currentVersion) {
    return { status: "STALE", flag };
  }
  const venvPython = getVenvPython(flag.venv_path);
  if (!fs.existsSync(venvPython)) {
    return { status: "BROKEN", flag };
  }
  return { status: "VALID", flag };
}

/**
 * Return the path to the venv's python binary, platform-aware.
 * @param {string} venvPath
 * @returns {string}
 */
function getVenvPython(venvPath) {
  if (process.platform === "win32") {
    return path.join(venvPath, "Scripts", "python.exe");
  }
  return path.join(venvPath, "bin", "python");
}

/**
 * Read the plugin version from pyproject.toml (preferred) or plugin.json (fallback).
 * @returns {string} semver-like version string (e.g., "0.4.0")
 * @throws {Error} if neither file is readable or contains a version
 */
function getCurrentVersion() {
  const pluginRoot = getPluginRoot();
  const pyprojectPath = path.join(pluginRoot, "pyproject.toml");
  if (fs.existsSync(pyprojectPath)) {
    const content = fs.readFileSync(pyprojectPath, "utf8");
    // Match `version = "X.Y.Z"` inside the [project] table
    const match = content.match(/\[project\][\s\S]*?^version\s*=\s*"([^"]+)"/m);
    if (match) return match[1].trim();
  }
  const pluginJsonPath = path.join(pluginRoot, ".claude-plugin", "plugin.json");
  if (fs.existsSync(pluginJsonPath)) {
    const pluginJson = JSON.parse(fs.readFileSync(pluginJsonPath, "utf8"));
    if (pluginJson.version) return String(pluginJson.version).trim();
  }
  throw new Error(
    "Cannot resolve plugin version: pyproject.toml and plugin.json both unreadable or version-less"
  );
}

/**
 * Honor $CLAUDE_PLUGIN_DATA env var (test seam) or compute deterministic path.
 * @returns {string}
 */
function getPluginDataDir() {
  if (process.env.CLAUDE_PLUGIN_DATA) {
    return process.env.CLAUDE_PLUGIN_DATA;
  }
  return _computePluginDataDir();
}

/**
 * Compute ${CLAUDE_PLUGIN_DATA} deterministically per Anthropic's plugin docs:
 * ~/.claude/plugins/data/{slug}/ where slug = plugin-id with non-[a-zA-Z0-9_-] → -
 * Spec § 4.2. Exported for testing.
 * @returns {string}
 */
function _computePluginDataDir() {
  const pluginId = "claude-wayfinder@claude-wayfinder";
  const slug = pluginId.replace(/[^a-zA-Z0-9_-]/g, "-");
  return path.join(os.homedir(), ".claude", "plugins", "data", slug);
}

/**
 * Honor $CLAUDE_PLUGIN_ROOT env var (test seam) or compute from __dirname.
 * @returns {string}
 */
function getPluginRoot() {
  if (process.env.CLAUDE_PLUGIN_ROOT) {
    return process.env.CLAUDE_PLUGIN_ROOT;
  }
  // This file is at <root>/hooks/lib/setup-state.js → root is two levels up
  return path.resolve(__dirname, "..", "..");
}

module.exports = { readSetupState, getVenvPython, getCurrentVersion, _computePluginDataDir };
