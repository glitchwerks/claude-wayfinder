// SessionStart hook: emit [CATALOG ERROR] additionalContext when the
// dispatch catalog at ~/.claude/state/dispatch-catalog.json is missing,
// empty, or otherwise unreadable. Emit [CATALOG STALE] when any source
// file (skills/**/SKILL.md or agents/*.md) is newer than the catalog.
// Silent no-op when the catalog is healthy and up-to-date.
//
// Environment overrides (for testing):
//   DISPATCH_CATALOG_PATH — override the catalog file path
//   CLAUDE_HOME           — override the ~/.claude base directory used when
//                           scanning for source files (skills/**/SKILL.md,
//                           agents/*.md). Defaults to os.homedir()/.claude

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const { readSetupState, getCurrentVersion, getVenvPython } = require("./lib/setup-state.js");

// ---------------------------------------------------------------------------
// Setup-state gate (Phase 2 — Issue #104)
// ---------------------------------------------------------------------------
// If setup has not been completed (MISSING), is out of date (STALE), or the
// venv is broken (BROKEN), emit a banner and exit cleanly — skip catalog checks.
// If VALID, run a one-per-session import probe; if that fails, delete the flag
// so the next session sees MISSING and prompts the user to re-run setup.

/**
 * Emit a SessionStart banner to both the model's context (stdout JSON
 * envelope) and the user's terminal (stderr plain text).
 *
 * The additionalContext payload in the stdout envelope only reaches the
 * model's context; the user sees nothing in the terminal (#185). Writing
 * the same text to stderr surfaces the banner in the Claude Code terminal
 * output alongside the session startup messages.
 *
 * @param {string} text - the banner text to emit
 */
function emitBoth(text) {
  process.stdout.write(
    JSON.stringify({
      hookSpecificOutput: {
        hookEventName: "SessionStart",
        additionalContext: text,
      },
    })
  );
  process.stderr.write(text + "\n");
}

(function checkSetupState() {
  const currentVersion = getCurrentVersion();
  const setupState = readSetupState(currentVersion);

  if (setupState.status !== "VALID") {
    let banner;
    if (setupState.status === "MISSING") {
      banner =
        "⚠ claude-wayfinder requires setup. Run /setup-wayfinder to materialize the Python venv. The dispatch matcher and catalog refresh are disabled until setup completes.";
    } else if (setupState.status === "STALE") {
      banner = `⚠ claude-wayfinder venv is for v${setupState.flag.version} but plugin is v${currentVersion}. Run /setup-wayfinder to refresh.`;
    } else if (setupState.status === "BROKEN") {
      banner = `⚠ claude-wayfinder venv at ${setupState.flag.venv_path} is unreachable or corrupt. Run /setup-wayfinder.`;
    }
    emitBoth(banner);
    process.exit(0);
  }

  // VALID case: run one-per-session import probe.
  // If it fails, downgrade to MISSING by deleting the flag, then emit banner.
  //
  // CLAUDE_WAYFINDER_PROBE_CMD env override (test seam): when set, the probe
  // runs the given command instead of the venv Python. Split on whitespace
  // like DISPATCH_GENERATOR_CMD — program + args. Intended for CI/test use
  // where fake-python shims (Node scripts) replace the real venv interpreter.
  let probeResult;
  if (process.env.CLAUDE_WAYFINDER_PROBE_CMD) {
    // Test seam: value is a JSON array ["prog", "arg1", ...]
    let probeProg, probeArgs;
    try {
      [probeProg, ...probeArgs] = JSON.parse(process.env.CLAUDE_WAYFINDER_PROBE_CMD);
    } catch (err) {
      // Malformed JSON in the test seam — fall through to the default probe path
      // rather than crashing, so the hook remains usable even with a bad override.
      emitBoth(`⚠ claude-wayfinder internal error: CLAUDE_WAYFINDER_PROBE_CMD malformed JSON — ${err.message}. Falling back to default probe.`);
      probeProg = null; // sentinel: skip the CLAUDE_WAYFINDER_PROBE_CMD branch
    }
    if (probeProg !== null) {
      probeResult = spawnSync(probeProg, probeArgs, { encoding: "utf8" });
    }
  }
  if (!probeResult) {
    probeResult = spawnSync(
      getVenvPython(setupState.flag.venv_path),
      ["-c", "import claude_wayfinder"],
      { encoding: "utf8" }
    );
  }
  if (probeResult.status !== 0 || probeResult.error) {
    // Flag is structurally valid but the venv is corrupt. Delete the flag so the
    // next session sees MISSING and re-prompts the user.
    const { _computePluginDataDir } = require("./lib/setup-state.js");
    const flagDir = process.env.CLAUDE_PLUGIN_DATA || _computePluginDataDir();
    const flagPath = path.join(flagDir, "setup-state.json");
    try {
      fs.unlinkSync(flagPath);
    } catch (_err) {
      // best-effort cleanup; ignore
    }
    const banner = `⚠ claude-wayfinder venv at ${setupState.flag.venv_path} fails import probe (likely corrupt). Run /setup-wayfinder to rebuild.`;
    emitBoth(banner);
    process.exit(0);
  }

  // VALID + probe passed: fall through to existing catalog-health logic.
})();

const claudeHome = process.env.CLAUDE_HOME || path.join(os.homedir(), ".claude");
const DEFAULT_PATH = path.join(claudeHome, "state", "dispatch-catalog.json");

function emitBanner(detail) {
  const text = `[CATALOG ERROR] Dispatch catalog is degraded: ${detail}. Until restored, routing falls back to LLM judgment per the legacy prose-policy.`;
  emitBoth(text);
}

function emitStaleBanner(newerFile) {
  const text = `[CATALOG STALE] Dispatch catalog is out of date — at least one source file is newer: ${newerFile}. Re-run the catalog generator to refresh routing.`;
  emitBoth(text);
}

// Walk a directory recursively, yielding file paths that match a predicate.
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
    } else if (entry.isFile() && predicate(entry.name)) {
      yield full;
    }
  }
}

// Return the max mtime (ms) across all source files, or null if none exist.
function maxSourceMtime() {
  const skillsDir = path.join(claudeHome, "skills");
  const agentsDir = path.join(claudeHome, "agents");

  let maxMs = null;
  let maxFile = null;

  const candidates = [
    ...walkFiles(skillsDir, (name) => name === "SKILL.md"),
    ...walkFiles(agentsDir, (name) => name.endsWith(".md")),
  ];

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

const target = process.env.DISPATCH_CATALOG_PATH || DEFAULT_PATH;

let raw;
try {
  raw = fs.readFileSync(target, "utf8");
} catch (_e) {
  emitBanner(`catalog file not found at ${target}`);
  process.exit(0);
}

let parsed;
try {
  parsed = JSON.parse(raw);
} catch (e) {
  emitBanner(`catalog JSON parse error: ${e.message}`);
  process.exit(2); // parse failure is a system-integrity error — exit 2
}

if (!parsed || !Array.isArray(parsed.entries) || parsed.entries.length === 0) {
  emitBanner("catalog has zero entries");
  process.exit(0);
}

// Healthy catalog — check for staleness.
const { maxMs: sourceMtimeMs, maxFile: newerFile } = maxSourceMtime();
if (sourceMtimeMs !== null) {
  const catalogMtimeMs = fs.statSync(target).mtimeMs;
  if (sourceMtimeMs > catalogMtimeMs) {
    emitStaleBanner(newerFile);
    process.exit(0);
  }
}

// Healthy and fresh — silent no-op.
process.exit(0);
