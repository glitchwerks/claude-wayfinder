/**
 * Shared library for the dispatch-log hooks.
 *
 * Two pure-ish functions:
 *   - extractSkillsFromPrompt(prompt, catalogPath?) → string[]
 *   - appendLogLine(event, logPath)                → void
 *
 * Both are designed for easy unit testing and zero external dependencies.
 */

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

/**
 * Skill name pattern: lowercase letters, digits, hyphens, with optional
 * `namespace:` prefix for plugin-namespaced skills.
 *
 * Matches backtick-quoted identifiers like:
 *   `python`, `refactoring-discipline`, `git`
 *   `superpowers:test-driven-development`, `microsoft-docs:microsoft-docs`
 *
 * Structure:
 *   - Optional namespace: `[a-z][a-z0-9-]*[a-z0-9]:` (at least 2 chars before colon)
 *   - Local name:         `[a-z][a-z0-9-]*[a-z0-9]`  (at least 2 chars)
 *
 * Requires at least 2 characters in each segment (one start + one end char).
 *
 * @type {RegExp}
 */
const SKILL_PATTERN = /`([a-z][a-z0-9-]*(?::[a-z][a-z0-9-]*(?:-[a-z0-9]+)*)?[a-z0-9])`/g;

/**
 * Default path to the dispatch catalog.
 *
 * Resolution order:
 *   1. CLAUDE_CONFIG_DIR env  → <CLAUDE_CONFIG_DIR>/state/dispatch-catalog.json
 *   2. HOME / USERPROFILE     → ~/.claude/state/dispatch-catalog.json
 *
 * @returns {string}
 */
function defaultCatalogPath() {
  if (process.env.CLAUDE_CONFIG_DIR) {
    return path.join(process.env.CLAUDE_CONFIG_DIR, "state", "dispatch-catalog.json");
  }
  const home = process.env.HOME || process.env.USERPROFILE || os.homedir();
  return path.join(home, ".claude", "state", "dispatch-catalog.json");
}

/**
 * Module-level catalog cache. Keyed by resolved catalog path.
 * Each entry stores the loaded skill Set and the mtime at load time.
 *
 * @type {Map<string, { skills: Set<string>, mtimeMs: number }>}
 */
const _catalogCache = new Map();

/**
 * Load (or return cached) the skill catalog from the given path.
 *
 * Reloads the catalog if the file's mtime is newer than the cached load time,
 * so live catalog updates are picked up without restarting the hook process.
 *
 * If the file is missing or fails to parse, logs to stderr and returns an empty Set.
 * The hook must never crash because of catalog issues.
 *
 * @param {string} catalogPath - Absolute path to dispatch-catalog.json.
 * @returns {Set<string>} Set of real skill names.
 */
function loadCatalog(catalogPath) {
  // Check mtime to decide whether to use the cached version.
  let fileMtimeMs;
  try {
    fileMtimeMs = fs.statSync(catalogPath).mtimeMs;
  } catch (_) {
    // File doesn't exist or is unreadable — evict any stale cache entry.
    _catalogCache.delete(catalogPath);
    process.stderr.write(`[dispatch-log] catalog not found: ${catalogPath}\n`);
    return new Set();
  }

  const cached = _catalogCache.get(catalogPath);
  if (cached && fileMtimeMs <= cached.mtimeMs) {
    return cached.skills;
  }

  // Cache miss or stale — reload.
  try {
    const raw = fs.readFileSync(catalogPath, "utf8");
    const data = JSON.parse(raw);
    const skills = new Set(
      (data.entries || [])
        .filter((e) => e.kind === "skill" && typeof e.name === "string")
        .map((e) => e.name)
    );
    _catalogCache.set(catalogPath, { skills, mtimeMs: fileMtimeMs });
    return skills;
  } catch (err) {
    process.stderr.write(`[dispatch-log] failed to parse catalog ${catalogPath}: ${err.message}\n`);
    return new Set();
  }
}

/**
 * Extract skill identifiers from a free-text prompt string.
 *
 * Applies SKILL_PATTERN against the full prompt, then validates each match
 * against the real skill catalog. Tokens not in the catalog are dropped.
 *
 * Returns deduplicated matches. If the prompt is null/undefined/empty, returns [].
 *
 * If no catalogPath is provided, uses the DISPATCH_CATALOG_PATH environment
 * variable, falling back to ~/.claude/state/dispatch-catalog.json.
 *
 * @param {string|null|undefined} prompt      - The Agent tool's `prompt` parameter.
 * @param {string} [catalogPath]              - Optional override for the catalog file path.
 * @returns {string[]} Deduplicated array of catalog-validated skill names.
 */
function extractSkillsFromPrompt(prompt, catalogPath) {
  if (!prompt || typeof prompt !== "string") return [];

  // Resolve catalog path: explicit arg > env var > default.
  const resolvedPath = catalogPath || process.env.DISPATCH_CATALOG_PATH || defaultCatalogPath();

  const catalog = loadCatalog(resolvedPath);

  // If catalog is empty (missing/parse error), return [] — fail open.
  if (catalog.size === 0) return [];

  const matches = [];
  const seen = new Set();

  // Reset lastIndex before each use because the regex has the /g flag.
  SKILL_PATTERN.lastIndex = 0;
  while (true) {
    const m = SKILL_PATTERN.exec(prompt);
    if (m === null) break;
    const name = m[1];
    if (!seen.has(name) && catalog.has(name)) {
      seen.add(name);
      matches.push(name);
    }
  }

  return matches;
}

/**
 * Append one structured event as a JSONL line to the given log path.
 *
 * Creates the parent directory if it does not exist. Never throws — logging
 * failures must not interrupt the hook that called this.
 *
 * @param {object} event   - Structured event object to serialize.
 * @param {string} logPath - Absolute path to the JSONL log file.
 */
function appendLogLine(event, logPath) {
  try {
    fs.mkdirSync(path.dirname(logPath), { recursive: true });
    fs.appendFileSync(logPath, `${JSON.stringify(event)}\n`);
  } catch (_) {
    // Logging must never break the hook. Silently swallow disk errors.
  }
}

module.exports = { extractSkillsFromPrompt, appendLogLine, _catalogCache };
