// Shared helpers for normalizing skill names across hook scripts.
//
// Consolidation note: isPluginNamespaced() remains a private utility in
// component-version.js because it is not exported from that module and is
// only used as an internal guard there. Adding it here would require a
// cross-require between two lib/ modules with no concrete consumer need —
// the churn outweighs the benefit. If a future caller needs isPluginNamespaced
// outside component-version.js, move it here at that time.

/**
 * Strip the plugin namespace prefix from a skill name.
 *
 * The Skill tool accepts names in both bare ("dispatch") and plugin-namespaced
 * ("claude-wayfinder:dispatch") form. The catalog and hook logic consistently use
 * bare names as sentinels (e.g. "dispatch", "gh-create-issue"). This helper
 * normalises any incoming skill name to its bare form so comparisons are reliable
 * regardless of whether the caller supplied the namespaced or bare form.
 *
 * Caveat: stripping the namespace risks bare-name collisions across plugins (two
 * plugins each shipping a same-named skill). That is acceptable here because the
 * comparison targets are a small, controlled set (dispatch, INTERACTIVE_SKILLS).
 *
 * @param {string} name  Skill name, optionally namespace-prefixed ("ns:skill").
 * @returns {string}     Bare skill name, or "" for non-string input.
 *
 * @example
 * bareSkillName("claude-wayfinder:dispatch")         // → "dispatch"
 * bareSkillName("claude-github-tools:gh-create-issue") // → "gh-create-issue"
 * bareSkillName("whats-next")                        // → "whats-next"
 * bareSkillName(null)                                // → ""
 */
function bareSkillName(name) {
  if (typeof name !== "string") return "";
  return name.split(":").pop();
}

module.exports = { bareSkillName };
