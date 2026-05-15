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

const claudeHome = process.env.CLAUDE_HOME || path.join(os.homedir(), ".claude");
const DEFAULT_PATH = path.join(claudeHome, "state", "dispatch-catalog.json");

function emitBanner(detail) {
  const text = `[CATALOG ERROR] Dispatch catalog is degraded: ${detail}. Until restored, routing falls back to LLM judgment per the legacy prose-policy.`;
  process.stdout.write(
    JSON.stringify({
      hookSpecificOutput: {
        hookEventName: "SessionStart",
        additionalContext: text,
      },
    })
  );
}

function emitStaleBanner(newerFile) {
  const text = `[CATALOG STALE] Dispatch catalog is out of date — at least one source file is newer: ${newerFile}. Re-run the catalog generator to refresh routing.`;
  process.stdout.write(
    JSON.stringify({
      hookSpecificOutput: {
        hookEventName: "SessionStart",
        additionalContext: text,
      },
    })
  );
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
