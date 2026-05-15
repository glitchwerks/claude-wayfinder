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
//                             Defaults to: python <scripts path>/build_dispatch_catalog.py

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const claudeHome = process.env.CLAUDE_HOME || path.join(os.homedir(), ".claude");

const DEFAULT_CATALOG_PATH = path.join(claudeHome, "state", "dispatch-catalog.json");
const catalogPath = process.env.DISPATCH_CATALOG_PATH || DEFAULT_CATALOG_PATH;

// Default generator: invoke `python` from PATH running the build script.
// The python binary is resolved via PATH — no hardcoded venv path.
// DISPATCH_GENERATOR_CMD overrides this entirely (e.g. for tests: `node fake_gen.js`).
const DEFAULT_GENERATOR_CMD =
  `python "${path.join(claudeHome, "scripts", "build_dispatch_catalog.py")}"`;
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
  ];

  // Add project-local source files when a project root is detected.
  if (projectRoot) {
    const projSkillsDir = path.join(projectRoot, ".claude", "skills");
    const projAgentsDir = path.join(projectRoot, ".claude", "agents");
    candidates.push(
      ...walkFiles(projSkillsDir, (p) => basename(p) === "SKILL.md"),
      ...walkFiles(projAgentsDir, (p) => basename(p).endsWith(".md"))
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
function parseCmd(cmd) {
  const tokens = [];
  const re = /"([^"]+)"|(\S+)/g;
  let m;
  while ((m = re.exec(cmd)) !== null) {
    tokens.push(m[1] !== undefined ? m[1] : m[2]);
  }
  return tokens;
}

const [prog, ...args] = parseCmd(generatorCmd);

// Append --project-root when a project root is detected so the generator
// produces a catalog tagged with the correct built_for_project path.
if (currentProjectRoot) {
  args.push("--project-root", currentProjectRoot);
}

const result = spawnSync(prog, args, {
  encoding: "utf8",
  timeout: 60_000, // 60s hard ceiling
  shell: false,
});

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
