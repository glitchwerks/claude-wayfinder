// UserPromptSubmit hook: auto-refresh the dispatch catalog when any source file
// (skills/**/SKILL.md or agents/*.md) has a newer mtime than the catalog,
// or when the catalog was built for a different project than the current cwd.
//
// Behavior:
//   - Determine the current project root via 'git rev-parse --show-toplevel'
//     from process.cwd().  If the resolved root equals claudeHome, treat it
//     as "no project" (avoid double-scanning the user-global tree).
//   - Stat all source files (user-global + project-local when applicable).
//   - Also stat all plugin cache files: <CLAUDE_HOME>/plugins/cache/**/SKILL.md
//     and <CLAUDE_HOME>/plugins/cache/**/agents/*.md.
//   - Also stat <CLAUDE_HOME>/plugins/installed_plugins.json as a manifest
//     change sentinel (install / uninstall / version bump all touch this file).
//   - If newest source mtime > catalog mtime (or catalog is missing), rebuild.
//   - If catalog exists but its built_for_project field differs from the
//     current project root (project-switch), force a rebuild even when all
//     mtimes are clean.
//   - If the generator fails, emit `additionalContext` with error details but
//     exit 0 — never block the prompt.
//   - If catalog is fresh and project matches, silent no-op.
//
// Environment overrides (for testing):
//   CLAUDE_HOME             — override the ~/.claude base directory used when
//                             scanning for source files. Defaults to
//                             os.homedir()/.claude
//   DISPATCH_CATALOG_PATH   — override the catalog file path. Defaults to
//                             <CLAUDE_HOME>/state/dispatch-catalog.json
//   DISPATCH_GENERATOR_CMD  — override the generator command (for testing).
//                             Defaults to:
//                               python -m claude_wayfinder catalog build

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const { readSetupState, getCurrentVersion, getVenvPython } = require("./lib/setup-state.js");

const claudeHome = process.env.CLAUDE_HOME || path.join(os.homedir(), ".claude");

const DEFAULT_CATALOG_PATH = path.join(claudeHome, "state", "dispatch-catalog.json");
const catalogPath = process.env.DISPATCH_CATALOG_PATH || DEFAULT_CATALOG_PATH;

// ---------------------------------------------------------------------------
// Setup-state gate (Phase 2 — Issue #104)
// ---------------------------------------------------------------------------
// If setup has not been completed (MISSING), is out of date (STALE), or the
// venv is broken (BROKEN), exit silently. The SessionStart banner in
// check-catalog-health.js surfaces the issue to the user. Per spec § 4.4.
const setupState = readSetupState(getCurrentVersion());
if (setupState.status !== "VALID") {
  process.exit(0);
}

// Setup is VALID: resolve the venv Python interpreter for catalog generation.
// This replaces the v0.3.x CLAUDE_WAYFINDER_PYTHON discovery scaffolding.
const venvPython = getVenvPython(setupState.flag.venv_path);

// Default generator: invoke the plugin's CLI as a Python module.
//
// We use `python -m claude_wayfinder catalog build` rather than the bare
// `claude-wayfinder` entry-point shim. The shim is registered by
// pyproject.toml's [project.scripts] and lives in the venv's bin/Scripts
// directory — it is on PATH only when the venv is activated, which the
// plugin's hook child process cannot rely on. Invoking the module directly
// works as long as `python` on PATH has the `claude_wayfinder` package
// importable, which is the documented Pattern A install (README → Install).
//
// No extra path args are needed here (issue #87, v0.3.5 fix): the CLI now
// ships sensible defaults for --skills-dir, --agents-dir, --out, and --log,
// all anchored to ${CLAUDE_HOME} (or ~/.claude when unset). Consumers who
// need custom paths can still set DISPATCH_GENERATOR_CMD to a full command
// with explicit flags.  The hook intentionally delegates default resolution
// to the CLI — "defaults at the CLI, not at the hook" is the durable fix
// that prevents the class of regression seen in v0.3.2 (ENOENT), v0.3.3
// (wrong interpreter), and v0.3.4 (missing args, issue #87).
//
// venvPython is now resolved from the setup-state flag (Phase 2, Issue #104).
// This replaces the v0.3.x CLAUDE_WAYFINDER_PYTHON env-var override approach.
//
// DISPATCH_GENERATOR_CMD overrides the generator entirely (e.g. for tests:
// `node fake_gen.js`). The override path is the primary integration seam
// for the test suite — see hooks/tests/refresh-catalog-on-stale.test.js.
// When DISPATCH_GENERATOR_CMD is set, the hook uses the string-parse path
// (parseCmd) to preserve the existing test seam unchanged.
const DEFAULT_GENERATOR_CMD = "python -m claude_wayfinder catalog build";
const generatorCmd = process.env.DISPATCH_GENERATOR_CMD || DEFAULT_GENERATOR_CMD;

// ---------------------------------------------------------------------------
// Project root detection
// ---------------------------------------------------------------------------

/**
 * Detect the git repository root for the current working directory.
 *
 * Returns the resolved absolute path string when inside a git repo and the
 * repo root is not the user-global claudeHome (to avoid double-scanning).
 * Returns null otherwise.
 *
 * @returns {string|null}
 */
function detectProjectRoot() {
  try {
    const result = spawnSync("git", ["rev-parse", "--show-toplevel"], {
      encoding: "utf8",
      cwd: process.cwd(),
    });
    if (result.status !== 0 || !result.stdout) {
      return null;
    }
    const root = result.stdout.trim();
    // Normalise separators for comparison on Windows
    const resolvedRoot = path.resolve(root);
    const resolvedHome = path.resolve(claudeHome);
    if (resolvedRoot === resolvedHome) {
      return null; // user-global home — don't double-scan
    }
    return resolvedRoot;
  } catch (_e) {
    return null;
  }
}

// ---------------------------------------------------------------------------
// File walking (same pattern as check-catalog-health.js, copied not refactored)
// ---------------------------------------------------------------------------

/**
 * Walk a directory recursively, yielding file paths where predicate(fullPath)
 * returns true. Silently skips directories that cannot be read.
 *
 * The predicate receives the full absolute file path so callers can inspect
 * both the file name and its parent directory segments.
 *
 * @param {string} dir
 * @param {(fullPath: string) => boolean} predicate
 * @returns {Generator<string>}
 */
function* walkFiles(dir, predicate) {
  let entries;
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch (_e) {
    return; // directory doesn't exist — skip silently
  }
  for (const entry of entries) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      yield* walkFiles(full, predicate);
    } else if (entry.isFile() && predicate(full)) {
      yield full;
    }
  }
}

/**
 * Return the max mtime (ms) and its file path across all source files:
 *   - user-global owned tree: skills/[**]/SKILL.md and agents/*.md
 *   - project-local owned tree (when a project root is set)
 *   - plugin cache tree: plugins/cache/[**]/SKILL.md and agents/*.md
 *   - manifest file: plugins/installed_plugins.json (sentinel for plugin
 *     install/uninstall/version-bump churn)
 *
 * Returns { maxMs: null, maxFile: null } when no source files exist.
 *
 * @param {string|null} projectRoot
 * @returns {{ maxMs: number|null, maxFile: string|null }}
 */
function maxSourceMtime(projectRoot) {
  const skillsDir = path.join(claudeHome, "skills");
  const agentsDir = path.join(claudeHome, "agents");

  const basename = (p) => path.basename(p);
  const parentName = (p) => path.basename(path.dirname(p));

  const candidates = [
    ...walkFiles(skillsDir, (p) => basename(p) === "SKILL.md"),
    ...walkFiles(agentsDir, (p) => basename(p).endsWith(".md")),
    // Issue #148: colocated owned-agent sidecars (<name>.triggers.yml next to
    // <name>.md).  A new or modified sidecar in the agents directory must
    // trigger a catalog rebuild just like editing the agent .md itself.
    ...walkFiles(agentsDir, (p) => basename(p).endsWith(".triggers.yml")),
  ];

  // Add project-local source files when a project root is detected.
  if (projectRoot) {
    const projSkillsDir = path.join(projectRoot, ".claude", "skills");
    const projAgentsDir = path.join(projectRoot, ".claude", "agents");
    candidates.push(
      ...walkFiles(projSkillsDir, (p) => basename(p) === "SKILL.md"),
      ...walkFiles(projAgentsDir, (p) => basename(p).endsWith(".md")),
      // Issue #148: colocated project-agent sidecars.
      ...walkFiles(projAgentsDir, (p) => basename(p).endsWith(".triggers.yml"))
    );
  }

  // Plugin cache tree: walk cache/<glob>/SKILL.md and cache/<glob>/agents/*.md.
  const pluginCacheDir = path.join(claudeHome, "plugins", "cache");
  candidates.push(
    ...walkFiles(pluginCacheDir, (p) => basename(p) === "SKILL.md"),
    ...walkFiles(pluginCacheDir, (p) => parentName(p) === "agents" && basename(p).endsWith(".md"))
  );

  // Manifest mtime watch: any install/uninstall/version-bump touches this file.
  const installedPluginsFile = path.join(claudeHome, "plugins", "installed_plugins.json");
  candidates.push(installedPluginsFile);

  // Plugin-agent sidecar overrides (Issue #140): walk
  // triggers/<plugin>/agents/*.yml. A new or modified sidecar file here
  // activates a dormant plugin agent — the catalog must be rebuilt.
  // The reserved triggers/builtin/ subtree is excluded (those sidecars
  // are handled by Pass 2.6 and are not plugin-agent overrides).
  const triggersDir = path.join(claudeHome, "triggers");
  const normalise = (p) => p.replace(/\\/g, "/");
  const builtinPrefix = normalise(path.join(claudeHome, "triggers", "builtin")) + "/";
  candidates.push(
    ...walkFiles(
      triggersDir,
      (p) =>
        parentName(p) === "agents" &&
        basename(p).endsWith(".yml") &&
        !normalise(p).startsWith(builtinPrefix)
    )
  );

  let maxMs = null;
  let maxFile = null;

  for (const filePath of candidates) {
    try {
      const { mtimeMs } = fs.statSync(filePath);
      if (maxMs === null || mtimeMs > maxMs) {
        maxMs = mtimeMs;
        maxFile = filePath;
      }
    } catch (_e) {
      // file disappeared between readdir and stat — skip
    }
  }

  return { maxMs, maxFile };
}

// ---------------------------------------------------------------------------
// Staleness check
// ---------------------------------------------------------------------------

const currentProjectRoot = detectProjectRoot();

let needsRefresh = false;
let catalogMtimeMs = null;

try {
  catalogMtimeMs = fs.statSync(catalogPath).mtimeMs;
} catch (_e) {
  // Catalog missing — always regenerate.
  needsRefresh = true;
}

if (!needsRefresh) {
  // Project-switch detection: if the catalog was built for a different project
  // root than the current cwd's project root, force a rebuild even when all
  // source file mtimes are clean.
  try {
    const catalogJson = JSON.parse(fs.readFileSync(catalogPath, "utf8"));
    // Three-state check for built_for_project:
    //   1. Field absent (legacy catalog, pre-#385) → treat as fresh; do not
    //      force rebuild on first run after upgrade.
    //   2. Field present, equals current project root → fresh, no rebuild.
    //   3. Field present, differs from current root → real project switch,
    //      force rebuild.
    if ("built_for_project" in catalogJson) {
      const builtFor = catalogJson.built_for_project;
      // Normalise both to resolved strings (or null) before comparing.
      const normalised = (p) => (p ? path.resolve(p) : null);
      if (normalised(builtFor) !== normalised(currentProjectRoot)) {
        needsRefresh = true;
      }
    }
    // Field absent → legacy catalog; accept as fresh without forcing rebuild.
  } catch (_e) {
    // Catalog unreadable or malformed — treat as stale.
    needsRefresh = true;
  }
}

if (!needsRefresh) {
  const { maxMs: sourceMtimeMs } = maxSourceMtime(currentProjectRoot);
  if (sourceMtimeMs !== null && sourceMtimeMs > catalogMtimeMs) {
    needsRefresh = true;
  }
}

if (!needsRefresh) {
  // Fresh catalog for the correct project — silent no-op.
  process.exit(0);
}

// ---------------------------------------------------------------------------
// Invoke the generator synchronously
// ---------------------------------------------------------------------------

// Split the command string into program + args. We support the common case of
// a quoted path followed by a quoted path (the default), as well as simple
// space-separated tokens (test overrides use `node /path/to/script.js`).
// Strategy: split on spaces, but keep quoted segments together.
//
// Only used when DISPATCH_GENERATOR_CMD is set (the test-override path).
// The default (non-override) path uses an explicit args array with pythonProg
// so that interpreter paths with spaces are never split on whitespace.
function parseCmd(cmd) {
  const tokens = [];
  const re = /"([^"]+)"|(\S+)/g;
  let m;
  while ((m = re.exec(cmd)) !== null) {
    tokens.push(m[1] !== undefined ? m[1] : m[2]);
  }
  return tokens;
}

// projectRootArgs appended to both spawn paths when a project root is detected.
const projectRootArgs = currentProjectRoot ? ["--project-root", currentProjectRoot] : [];

let result;
if (process.env.DISPATCH_GENERATOR_CMD) {
  // Test-override path: preserve the existing parseCmd seam so that all
  // existing tests (which inject `node fake_gen.js`) continue to work.
  const [prog, ...args] = parseCmd(generatorCmd);
  if (currentProjectRoot) {
    args.push("--project-root", currentProjectRoot);
  }
  result = spawnSync(prog, args, {
    encoding: "utf8",
    timeout: 60_000, // 60s hard ceiling
    shell: false,
  });
} else {
  // Default path: explicit args array with venvPython resolved from the
  // setup-state flag. Passing the program as a separate argument to spawnSync
  // — not through parseCmd — means interpreter paths with spaces are never
  // split on whitespace (see issue #82). The CLAUDE_WAYFINDER_PYTHON env-var
  // override (v0.3.x stopgap) is no longer used; the venv path comes from the
  // setup-state.json flag written by /setup-wayfinder (Phase 2, Issue #104).
  result = spawnSync(venvPython, ["-m", "claude_wayfinder", "catalog", "build", ...projectRootArgs], {
    encoding: "utf8",
    timeout: 60_000, // 60s hard ceiling
    shell: false,
  });
}

if (result.status === 0) {
  // Success — silent no-op (catalog is now fresh).
  process.exit(0);
}

// Generator failed — emit additionalContext so the router is aware, but do
// NOT block the prompt (exit 0, no permissionDecision field).
const errDetail = result.stderr?.trim() || (result.error ? result.error.message : "unknown error");
const exitCodeInfo = result.status !== null ? ` (exit ${result.status})` : "";

const additionalContext = `[CATALOG REFRESH FAILED] The dispatch catalog could not be regenerated${exitCodeInfo}. Routing will use the existing (stale) catalog. Error: ${errDetail || "generator produced no stderr output"}`;

process.stdout.write(
  JSON.stringify({
    hookSpecificOutput: {
      hookEventName: "UserPromptSubmit",
      additionalContext,
    },
  })
);

process.exit(0);
