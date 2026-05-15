/**
 * plugin-version.js — plugin version resolver.
 *
 * In the plugin context, there is no git repo to hash. Version info is read
 * from the plugin's own plugin.json instead.
 *
 * It resolves to a version string of the form "<name>@<version>" using the
 * plugin.json at the plugin root.
 *
 * Test injection:
 *   PLUGIN_VERSION_OVERRIDE — when set, returned immediately without reading
 *   plugin.json. Lets hook tests inject deterministic values without touching
 *   the filesystem.
 *
 * On any failure (file missing, parse error, missing version field), resolves
 * to the literal string "unknown" — never rejects, never throws.
 *
 * @returns {Promise<string>}  e.g. "claude-wayfinder@0.2.0" or "unknown".
 */

const path = require("node:path");
const fs = require("node:fs");

async function getPluginVersion() {
  if (process.env.PLUGIN_VERSION_OVERRIDE) {
    return process.env.PLUGIN_VERSION_OVERRIDE;
  }

  try {
    // Plugin root is two directories up from this file:
    //   hooks/lib/plugin-version.js  →  .claude-plugin/plugin.json
    const pluginJsonPath = path.resolve(__dirname, "..", "..", ".claude-plugin", "plugin.json");
    const raw = fs.readFileSync(pluginJsonPath, "utf8");
    const parsed = JSON.parse(raw);
    const name = parsed.name || "claude-wayfinder";
    const version = parsed.version;
    if (typeof version === "string" && version.length > 0) {
      return `${name}@${version}`;
    }
    return "unknown";
  } catch (_e) {
    return "unknown";
  }
}

module.exports = { getPluginVersion };
