const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const ABSENT = { rev: undefined, content_hash: undefined };
const NULL_BOTH = { rev: null, content_hash: null };

const NAME_OK = /^[a-z0-9_-]+$/;
const NAME_MAX_LEN = 100;

function claudeHome() {
  return process.env.CLAUDE_CONFIG_DIR || path.join(os.homedir(), ".claude");
}

function isPluginNamespaced(name) {
  return typeof name === "string" && name.includes(":");
}

function isSafeOwnedName(name) {
  if (typeof name !== "string") return false;
  if (name.length === 0 || name.length > NAME_MAX_LEN) return false;
  if (!NAME_OK.test(name)) return false;
  return true;
}

function resolveFilePath(name, kind) {
  const home = claudeHome();
  if (kind === "agent") return path.join(home, "agents", `${name}.md`);
  if (kind === "skill") return path.join(home, "skills", name, "SKILL.md");
  return null;
}

function readSidecarHash(name, kind) {
  const sidecarPath = path.join(claudeHome(), "state", "component-revisions.json");
  let raw;
  try {
    raw = fs.readFileSync(sidecarPath, "utf8");
  } catch (_e) {
    return null;
  }
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch (_e) {
    return null;
  }
  const key = `${kind}:${name}`;
  const entry = parsed?.components?.[key];
  if (!entry || typeof entry.rev !== "number" || typeof entry.content_hash !== "string") {
    return null;
  }
  return entry;
}

/**
 * Resolve {rev, content_hash} for a named owned component.
 *
 * Sentinel discipline:
 * - Both fields undefined: did not try (plugin name, malformed input)
 * - Both fields null: tried, file unreadable
 * - rev=null, content_hash=hex: file edited since last catalog rebuild
 * - rev=integer, content_hash=hex: stable state, sidecar agrees
 *
 * @param {string} name
 * @param {"agent"|"skill"} kind
 * @returns {{ rev: number|null|undefined, content_hash: string|null|undefined }}
 */
function getComponentVersion(name, kind) {
  if (isPluginNamespaced(name)) return { ...ABSENT };
  if (!isSafeOwnedName(name)) return { ...ABSENT };
  if (kind !== "agent" && kind !== "skill") return { ...ABSENT };

  const filePath = resolveFilePath(name, kind);
  if (filePath === null) return { ...ABSENT };

  let bytes;
  try {
    bytes = fs.readFileSync(filePath);
  } catch (_e) {
    return { ...NULL_BOTH };
  }

  const hash = crypto.createHash("sha256").update(bytes).digest("hex").slice(0, 12);
  const sidecarEntry = readSidecarHash(name, kind);

  if (sidecarEntry === null) {
    return { rev: null, content_hash: hash };
  }
  if (sidecarEntry.content_hash === hash) {
    return { rev: sidecarEntry.rev, content_hash: hash };
  }
  return { rev: null, content_hash: hash };
}

module.exports = { getComponentVersion };
