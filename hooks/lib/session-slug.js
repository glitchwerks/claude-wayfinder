/**
 * session-slug.js — minimal slug helper for the router-drift-scanner hook.
 *
 * Inlined from skills/lib/session-context.js#computeProjectSlug.
 * Only this one function is needed; the rest of session-context.js is not ported.
 *
 * @param {string} absPath  Absolute path to the project root (or cwd).
 * @returns {string}        Slug where every non-alphanumeric character is replaced
 *                          by a single hyphen.
 */
function computeProjectSlug(absPath) {
  return String(absPath).replace(/[^A-Za-z0-9]/g, "-");
}

module.exports = { computeProjectSlug };
