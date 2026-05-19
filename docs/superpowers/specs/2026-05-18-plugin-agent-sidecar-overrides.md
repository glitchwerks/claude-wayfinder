# Plugin-agent sidecar overrides

Tracking issue: [#140](https://github.com/glitchwerks/claude-wayfinder/issues/140)
Author: design session 2026-05-18 (cbeaulieu-gt + Claude)
Status: **design approved — input to writing-plans**
Predecessors:
- `docs/schema.md` — catalog entry schema, `source` field values, `routable` semantics (background)
- `docs/design/trigger-schema.md` — trigger-block field reference, `kind: agent` plugin-override mechanics, `is_agent_routable` predicate (background)

---

## §1 Why this design exists

### The plugin-agent dormancy problem

When claude-wayfinder discovers an agent from an installed plugin, it emits a catalog entry tagged `source="plugin"`. The `is_agent_routable` predicate in `match_filters.py` unconditionally excludes these entries from the scored-agent pool at dispatch time. The result is a catalog that knows the agent exists but will never route work to it. This is intentional for skills — dormant skill entries are harmless, scoring zero and attaching to no agent — but for agents the dormancy is permanent unless the user can somehow activate the entry. Without an activation path, plugin-shipped agents are decoration: they appear in the catalog and nowhere else.

### Why editing plugin files is not the answer

Plugin updates overwrite everything under the plugin's install path. A user who edits an agent `.md` file directly to add `triggers:` frontmatter or set `routable: true` loses that edit on the next `/plugin update`. The existing skill-override mechanism (`triggers/<plugin>/<skill>.yml`) already solves this for skills: the user authors a sidecar file outside the plugin tree, and the catalog generator merges it over the dormant plugin entry at build time. Plugin updates cannot touch user-authored files under `~/.claude/triggers/`.

### Why mirroring the skill-override pattern is the obvious shape

The skill-override pattern is already implemented, understood, and documented. Extending it to agents requires a new subdirectory convention inside the per-plugin override folder and a matching discovery pass in the catalog builder. The core mechanics — scan, match against dormant plugin entry, replace with `source="plugin-override"`, propagate triggers — are identical. Reusing the same pattern minimizes new surface area, keeps the `triggers/` tree internally consistent, and means operator documentation for skill overrides transfers directly to agent overrides.

---

## §2 Design decisions (locked from discussion)

| # | Decision | Rationale |
| - | -------- | --------- |
| D1 | **Location: `~/.claude/triggers/<plugin>/agents/<name>.yml`** | Subdir variant (not `.agent.yml` suffix) — structural disambiguation from skill overrides, impossible to miss. |
| D2 | **Source tag: reuse `source="plugin-override"`, disambiguate by `kind="agent"`** | Avoids growing the `source` enum. The `kind` field already disambiguates rows. Update Section 1 of `docs/schema.md` accordingly. |
| D3 | **Strict override semantics (Mode 2a) — sidecar must match an installed plugin agent** | Ghost skill entries degrade gracefully (matcher returns `skills: []`); ghost agent entries cause hard `Agent({subagent_type: <ghost>})` failures the router can't recover from. Asymmetric blast radius justifies asymmetric rule. Reversible later. |
| D4 | **No `min_claude_version` requirement** | Unlike `triggers/builtin/` (which pins against Claude Code itself), plugin agents are versioned by their plugin manifest. No need to duplicate that pin in the sidecar. |
| D5 | **Unmatched sidecars emit a warning and are dropped** | Drift is visible, not silent. Use the same warning surface as existing `_logger.warning` calls in `build_catalog.py`. |
| D6 | **Refresh-catalog watcher and SessionStart staleness check cover the new subdir** | The watcher already walks `~/.claude/triggers/`; verify in the spec that nothing in the watcher hard-codes "skills only," and call out the assertion to validate at implementation time. |
| D7 | **Precedence: same slot as skill plugin-override** | Between `owned` and `plugin` (dormant). No new precedence rule. |

---

## §3 Architecture

### Directory layout

```
~/.claude/
├── skills/
│   └── <name>/
│       ├── SKILL.md
│       └── triggers.yml          ← owned-skill sidecar
├── agents/
│   └── <name>.md                 ← owned-agent (inline frontmatter)
├── plugins/
│   ├── installed_plugins.json
│   └── cache/
│       └── <plugin>/
│           ├── skills/
│           │   └── <name>/
│           │       └── SKILL.md  ← plugin-shipped skill (read-only)
│           └── agents/
│               └── <name>.md     ← plugin-shipped agent (read-only)
└── triggers/
    ├── builtin/
    │   └── <Agent>.yml           ← builtin-agent sidecar (operator-authored)
    └── <plugin>/
        ├── <skill>.yml           ← skill plugin-override sidecar (existing)
        └── agents/               ← NEW: agent plugin-override subdirectory
            └── <name>.yml        ← NEW: agent plugin-override sidecar
```

The `agents/` subdirectory sits one level inside the per-plugin override directory, parallel to the flat `<skill>.yml` files that handle skill overrides. The name in the sidecar filename matches the agent's base filename in the plugin's `agents/` directory (without the `.md` extension), and by convention also matches the agent's `name:` frontmatter field.

### Discovery flow

The catalog builder assembles entries in numbered passes. The new agent-sidecar walker inserts as a sub-pass within Pass 3 (plugin-override application), after skill sidecars are applied:

1. **Pass 2 (plugin discovery):** `discover_installed_plugins` reads `installed_plugins.json`; `discover_plugin_entries` globs `skills/*/SKILL.md` and `agents/*.md` under each plugin's install path. Each discovered agent is emitted as a dormant catalog entry with `source="plugin"`, `routable` absent (defaults to `true` in the schema but `is_agent_routable` filters it out at dispatch because of the `source="plugin"` tag), and empty trigger fields.

2. **Pass 3a (skill overrides — existing):** `discover_plugin_overrides` walks `triggers/<plugin>/*.yml` (skipping the `builtin/` directory). Each parsed sidecar is applied against the assembled entries: matching dormant `source="plugin"` entries are replaced with `source="plugin-override"`; unmatched sidecars are either appended as new entries (for skill injection) or emitted as warnings per D3 for agent sidecars (see below).

3. **Pass 3b (agent overrides — new):** A new walker, analogous to `discover_plugin_overrides`, walks `triggers/<plugin>/agents/*.yml`. For each file found:
   - Synthesize the target entry name as `<plugin>:<name>` where `<name>` is the file stem, matching the plugin-namespaced convention.
   - Look up the assembled entries for a match on name and `kind="agent"` with `source="plugin"`.
   - If a match is found: replace the dormant entry in place, setting `source="plugin-override"`, `routable: true` (explicit), and the sidecar's `triggers` and `applicable_skills`. Log an `info` line: `override layers on plugin-discovered agent '<name>'`.
   - If no match is found (ghost sidecar): emit a `_logger.warning` and drop the sidecar. Do not append a new entry. This is the strict Mode 2a behavior locked in D3.

4. **Subsequent passes (validation, resolution):** The `_resolve_applicable_references` pass and catalog validation run after all overrides are applied. Agent entries that emerged from Pass 3b participate normally — their `applicable_skills` lists are resolved against the known-skills universe, with plugin-namespaced references kept as unverified external pointers per the existing `_is_plugin_namespaced` rule.

### Routing behavior after activation

Once an agent sidecar is applied, the catalog entry has `source="plugin-override"` and `kind="agent"`. The `is_agent_routable` predicate in `match_filters.py` accepts `source="plugin-override"` as routable, so the agent enters the scored-agent pool at dispatch time. The trigger fields in the sidecar determine how the agent scores against incoming dispatch contexts. The `applicable_skills` field controls which skills the router attaches when delegating to this agent.

---

## §4 Sidecar file schema

An agent plugin-override sidecar lives at `triggers/<plugin>/agents/<name>.yml`. Its schema is the same trigger block used by skill sidecars, with one additional optional field:

```yaml
# triggers/superpowers/agents/doc-writer.yml
#
# Activates the dormant superpowers:doc-writer plugin agent for routing.
# The plugin agent must be installed; this sidecar will not create a new entry.

triggers:
  keywords:
    - { term: "document", weight: 1.0 }
    - { term: "readme",   weight: 1.0 }
    - { term: "spec",     weight: 0.5 }
    - { term: "prose",    weight: 0.5 }
  path_globs:
    - "**/*.md"
    - "*.md"
  command_prefixes: []
  agent_mentions: ["doc-writer"]
  tool_mentions: []
  excludes: []

applicable_skills: ["*"]
# NOTE: [] means the agent receives NO skills, which is almost always wrong.
# Use ["*"] to grant any applicable skill, or list specific skill names.
```

### Required fields

| Field | Type | Notes |
|---|---|---|
| `triggers` | `mapping` | Standard trigger block per `docs/design/trigger-schema.md §2d`. All sub-fields optional; absent block makes the entry dormant. |

### Optional fields

| Field | Type | Default | Notes |
|---|---|---|---|
| `applicable_skills` | `list[str]` | `[]` | Skills to attach when routing to this agent. `["*"]` = any applicable skill. `[]` = no skills. An empty list is the safe default but is almost always wrong for an agent sidecar — the agent will route correctly but receive no skills. Warn at build time when `triggers` is non-empty and `applicable_skills` is empty (same rule as existing skill/agent validation). |

### Fields NOT in the sidecar

The following fields are **inherited from the matched plugin agent entry** and must not appear in the sidecar:

- `name` — taken from the entry name (`<plugin>:<stem>`), not the sidecar.
- `description` — inherited from the plugin agent's frontmatter. The sidecar cannot override the description.
- `kind` — always `"agent"` for agent sidecars; not a sidecar field.
- `source` — set to `"plugin-override"` by the catalog builder; not a sidecar field.

This is a deliberate difference from the builtin-agent sidecar format (`triggers/builtin/<Agent>.yml`), which does carry `name`, `kind`, and `description` because builtin agents have no pre-existing catalog entry to inherit from.

---

## §5 Catalog builder changes

### Pass 3b: new agent-sidecar walker

The primary change is a new discovery function in `src/claude_wayfinder/build_catalog.py`, analogous to `discover_plugin_overrides` (~line 225). Where `discover_plugin_overrides` globs `*.yml` files directly inside `<plugin>/`, the new function globs `*.yml` files inside `<plugin>/agents/`. The `builtin/` skip guard already present in `discover_plugin_overrides` should be verified to cover the case where `triggers/builtin/agents/` might inadvertently exist — the guard must exclude the entire `builtin/` subtree, not just files directly inside it.

The new walker must return tuples with `kind="agent"` explicitly, so the application loop can route them to the agent-specific override path rather than the existing skill path.

### Override application loop

The existing loop that applies `discover_plugin_overrides` results operates on `kind="skill"` tuples. Two changes are needed:

- The loop (or a new sibling loop) must handle `kind="agent"` tuples with Mode 2a semantics: match required, no-match is a warning + drop rather than an append.
- The log message for a matched agent override should be consistent with the existing skill-override log: `info  <entry-name>  override layers on plugin-discovered agent '<name>'`.
- The log message for an unmatched agent override should use `_logger.warning` with a message identifying the ghost sidecar file path and the entry name that was expected but not found.

### `_BUILTIN_AGENTS_SUBDIR` skip guard

The constant `_BUILTIN_AGENTS_SUBDIR = "builtin"` (~line 773) is used in `discover_plugin_overrides` to skip the reserved `builtin/` directory. The new agent-sidecar walker must apply the same guard when iterating top-level plugin directories. Additionally, the guard should be confirmed to also exclude any `triggers/builtin/agents/` path from the new walker's glob — the `builtin/` exclusion applies to the entire subtree, not just files at depth 2.

### `is_agent_routable` — no change required

The predicate in `match_filters.py` already returns `True` for `source="plugin-override"`. No change is needed there. The implementation should confirm this at the start of the implementation plan by reading the current predicate body.

### Watcher / staleness check

The `refresh-catalog-on-stale.js` hook's `maxSourceMtime()` function currently walks owned skills, owned agents, project-local skills and agents, and the plugin cache tree. It does **not** walk `triggers/` in any form — confirmed by reading `hooks/refresh-catalog-on-stale.js` (the string "triggers" does not appear). This means that adding or modifying a file under `~/.claude/triggers/<plugin>/agents/` will not currently trigger a catalog rebuild.

Per D6, the new subdir should be covered. The implementation must add `triggers/` (or at least `triggers/<plugin>/agents/`) to the staleness candidates list in `maxSourceMtime()`. The specific walker call or path push should be documented in the implementation plan.

---

## §6 Schema-doc updates

The following changes to `docs/schema.md` are needed. They are listed here for the implementation plan; the edits should not be made until the implementation PR for this spec.

### §1 source field values table

The row for `"plugin-override"` currently reads:

> Loaded from a `triggers/<plugin>/<skill>.yml` override file. Replaces the matching `source="plugin"` entry, or adds a new entry when no plugin-discovered entry exists. `is_agent_routable` treats this source as routable.

This description must be extended to cover agent sidecars:

- Update the path pattern from `triggers/<plugin>/<skill>.yml` to include `triggers/<plugin>/agents/<name>.yml`.
- Clarify that skill sidecars may add a new entry when no plugin-discovered entry exists (existing behavior), but agent sidecars require a matching dormant plugin entry (D3 strict semantics).
- The `is_agent_routable` sentence remains accurate and needs no change.

### §1 example catalog (§7 in schema.md)

The minimal example catalog in Section 7 of `docs/schema.md` includes a `superpowers:brainstorming` entry with `source="plugin-override"` as a skill example. An analogous agent example (e.g., `superpowers:doc-writer` with `kind="agent"`, `source="plugin-override"`, `routable: true`) would clarify the agent case. This is optional for the schema doc but recommended for the integration guide (`docs/integration.md`).

### §3.1 precedence list (trigger-schema.md)

`docs/design/trigger-schema.md` does not contain a standalone precedence list section, but the collision-merge mechanics in §2f describe the source ordering. No changes are required to `trigger-schema.md` for this feature — the agent sidecar uses the same override mechanics and the same `source="plugin-override"` tag. The §2g section ("Plugin agents and `is_agent_routable`") and §10 ("Authoring guide") should be updated to mention the `agents/` subdirectory path, but these are doc-polish changes that belong in the implementation PR rather than a separate doc update.

---

## §7 Open implementation questions

The following questions surfaced during spec writing and depend on code reading or decisions the spec cannot lock down:

1. **Exact warning string format for unmatched agent sidecars.** The spec prescribes `_logger.warning` with a message identifying the ghost sidecar and the expected entry name, but does not lock the exact string. The implementation should follow the convention established by the existing owned-entry rejection messages (e.g., `"plugin override targets owned entry '%s' — rejected; owned entry preserved"`). The exact format should be chosen during implementation and then documented as a validation rule in `trigger-schema.md §6`.

2. **Trigger schema validation before or after matching.** The existing `discover_plugin_overrides` function parses the sidecar YAML but does not validate trigger schema fields — validation happens downstream in the `_process_file` or `build()` pipeline. The new agent-sidecar walker should follow the same pattern (parse first, validate in pipeline), but the implementation should confirm this by reading how the existing skill-override sidecars flow through `build()` before assuming the pattern transfers directly.

3. **Namespace collision: `<plugin>/<name>.yml` (skill) and `<plugin>/agents/<name>.yml` (agent) with the same stem.** Both would synthesize the entry name `<plugin>:<name>`. If `<name>` is `doc-writer`, the skill sidecar produces `superpowers:doc-writer` as a skill, and the agent sidecar produces `superpowers:doc-writer` as an agent. Whether this is a conflict depends on whether the catalog allows `(kind="skill", name="superpowers:doc-writer")` and `(kind="agent", name="superpowers:doc-writer")` to coexist. Reading the `build()` deduplication logic will answer this. The spec does not resolve the collision — it surfaces it for the implementer.

4. **`builtin/agents/` path protection.** The spec requires that `triggers/builtin/agents/` is excluded from the new agent-sidecar walker. The current `_BUILTIN_AGENTS_SUBDIR` guard in `discover_plugin_overrides` skips `plugin_dir.name == "builtin"` at the top-level directory iteration. The new walker must apply an equivalent guard. The implementation should verify that the guard covers the full subtree (i.e., not just the top-level directory check) and that no path like `triggers/builtin/agents/foo.yml` could slip through.

5. **`routable` field in the applied agent entry.** The spec states that a matched agent sidecar sets `routable: true` explicitly. However, `is_agent_routable` currently gates on `source`, not on the `routable` field, for plugin entries. The implementation should confirm whether the `routable: true` write is needed for correctness, or is purely defensive documentation. If `is_agent_routable` already returns `True` for `source="plugin-override"` regardless of the field's value, the explicit write is defensive but harmless.

---

## §8 Out of scope

The following are explicitly deferred and not addressed by this spec:

- **Injection-also semantics.** The ability to add a new agent entry that has no backing plugin-discovered entry (the "new entry" append path that skill sidecars already support). Agent sidecars require a matching dormant plugin entry (D3). Injection via `triggers/<plugin>/agents/<name>.yml` without a corresponding installed plugin agent is not supported in this design.
- **Plugin-author opt-in via plugin frontmatter.** A future mechanism could allow the plugin's own manifest to declare `routable: true` for a shipped agent, bypassing the need for a user-authored sidecar. This would complement the sidecar approach but is a separate feature with different trust and lifecycle implications.
- **Project-local agent overrides.** Project-scoped agents already land with `source="project"` and participate in routing normally. A project-local mechanism to override a plugin agent's triggers is a different feature shape and is covered conceptually by the existing `source="project"` precedence rule, not by this spec.
