# Owned and project agent sidecar overrides

Tracking issue: [#148](https://github.com/glitchwerks/claude-wayfinder/issues/148)
Author: design session 2026-05-18 (cbeaulieu-gt + Claude)
Status: **design approved — input to writing-plans**
Predecessors:
- [`docs/superpowers/specs/2026-05-18-plugin-agent-sidecar-overrides.md`](2026-05-18-plugin-agent-sidecar-overrides.md) — the #140 spec; this document extends that design to user-authored agents
- `docs/schema.md` — catalog entry schema, `source` field values, `routable` semantics (background)
- `docs/design/trigger-schema.md` — trigger-block field reference, `kind: agent` mechanics, `is_agent_routable` predicate (background)

---

## §1 Why this design exists

### The dispatch-metadata-in-agent-frontmatter problem

Claude-wayfinder is now distributed as a plugin. Users who author their own agents under `~/.claude/agents/` or `<repo>/.claude/agents/` have historically embedded dispatch metadata — `triggers:` blocks, `applicable_skills:` lists — directly inside the agent's `.md` frontmatter. This conflates two concerns: the agent's identity and behavioral prose, which belongs to the author, and its dispatch surface, which belongs to the routing configuration. The conflation creates friction: experimenting with trigger weights or path globs requires editing the agent file itself, which can disrupt version-controlled prose or introduce unintended changes to the agent's description. The #140 spec solved this problem for plugin-shipped agents by introducing colocated sidecar files under `triggers/<plugin>/agents/`. This spec closes the same gap for user-authored agents.

### Why colocation is the natural pattern

The v6 sidecar format already places trigger configuration next to skills: `triggers.yml` lives adjacent to `SKILL.md` in the same directory. The centralized `~/.claude/triggers/` tree exists for a specific reason — it holds files users cannot edit (plugin agent activations require a path outside the plugin's read-only install tree). That motivation does not apply to user-authored agents: the agent `.md` file is already writable, so there is no compelling reason to send the sidecar somewhere else. Colocation keeps configuration close to the artifact it describes, matches the established v6 skill pattern, and avoids adding routing configuration to a directory tree whose primary purpose is overriding read-only plugin content.

### Why no new source values are needed

A sidecar is a delivery mechanism, not an authorship claim. The catalog entry the matcher consumes is identical whether the triggers arrived inline or via a colocated sidecar: same `name`, same `description`, same `kind`, same `source`. The `source` field encodes who authored the agent (`"owned"` for user-global, `"project"` for repo-local), and that fact is unchanged when the user chooses to move dispatch metadata into a separate file. Introducing `"owned-sidecar"` or `"project-sidecar"` values would split what is logically one population into two, complicate the `is_agent_routable` predicate, and add noise to any downstream consumer that switches on `source`.

---

## §2 Design decisions (locked from discussion)

| # | Decision | Rationale |
| - | -------- | --------- |
| D1 | **Sidecar location: colocated** — `agents/<name>.triggers.yml` for owned, `<repo>/.claude/agents/<name>.triggers.yml` for project | Matches v6 skill pattern (first-party content lives next to artifact). Centralized `triggers/` tree exists for files users can't edit; that motivation doesn't apply to user-authored agents. |
| D2 | **Sidecar wins over inline `triggers:` frontmatter; warn when both present** | Sidecar adoption is safe without forcing inline cleanup first. Warning makes silent override visible. |
| D3 | **Strict orphan handling — sidecar with no matching .md is warned and dropped** | Mirrors #140 Mode 2a. Ghost agent entries cause hard `Agent({subagent_type})` failures. |
| D4 | **Reuse `source="owned"` and `source="project"` — no new source values** | Source enum captures authorship/trust, not delivery mechanism. Both inline and sidecar are user-authored. |
| D5 | **No forced migration — inline and sidecar coexist indefinitely** | Inline `triggers:` keeps working; users opt in to sidecars at own pace. The D2 warning is the only nudge. |

---

## §3 Architecture

### Directory layout

```
~/.claude/
├── skills/
│   └── <name>/
│       ├── SKILL.md
│       └── triggers.yml              ← owned-skill sidecar (v6 pattern)
├── agents/
│   ├── <name>.md                     ← owned agent (.md file, inline or bare frontmatter)
│   └── <name>.triggers.yml           ← NEW: owned-agent colocated sidecar
└── triggers/
    ├── builtin/
    │   └── <Agent>.yml               ← builtin-agent sidecar (operator-authored)
    └── <plugin>/
        ├── <skill>.yml               ← skill plugin-override sidecar (existing)
        └── agents/
            └── <name>.yml            ← plugin-agent override sidecar (#140, shipped)

<repo>/
└── .claude/
    ├── skills/
    │   └── <name>/
    │       ├── SKILL.md
    │       └── triggers.yml          ← project-skill sidecar (v6 pattern)
    └── agents/
        ├── <name>.md                 ← project agent
        └── <name>.triggers.yml       ← NEW: project-agent colocated sidecar
```

The new sidecar file sits directly alongside its `.md` counterpart. The stem must match exactly: `code-writer.triggers.yml` pairs with `code-writer.md`. No subdirectory nesting is introduced.

### Discovery flow

The catalog builder's `build()` function assembles entries in numbered passes. The colocated-sidecar walker inserts as a sub-pass immediately after each existing agent discovery pass — once after Pass 2 (owned agents) and once inside Pass 4 (project-local agents).

1. **Pass 2 — owned agents (existing):** `build()` globs `agents_dir/*.md` and calls `_process_file()` for each file, producing entries with `source="owned"`. Triggers and `applicable_skills` come from inline frontmatter.

2. **Pass 2b — owned-agent colocated sidecars (new):** After Pass 2 completes, a new walker globs `agents_dir/*.triggers.yml`. For each sidecar found:
   - Derive the expected agent name from the file stem (e.g., `code-writer` from `code-writer.triggers.yml`).
   - Look up the assembled entries for a match on that name and `kind="agent"` with `source="owned"`.
   - If a match is found and the matched entry already has non-empty inline `triggers:`, emit `_logger.warning` (D2: sidecar shadows inline triggers). Then apply the sidecar's `triggers:` and `applicable_skills:` to the matched entry in place.
   - If a match is found and inline triggers are absent, apply the sidecar silently.
   - If no match is found (orphan sidecar — no `.md` with the same stem): emit `_logger.warning` and drop the sidecar. Do not create a new entry (D3).

3. **Pass 4 — project-local agents (existing):** `build()` globs `proj_agent_dir/*.md` and calls `_process_file()`, then sets `result["source"] = "project"` on each entry.

4. **Pass 4b — project-agent colocated sidecars (new):** Immediately after Pass 4's agent loop, an equivalent walker globs `proj_agent_dir/*.triggers.yml` and applies the same logic as Pass 2b, but scoped to the project entries list. Orphan sidecars in the project agents directory emit a warning and are dropped (D3).

5. **Downstream passes (existing — no change):** `_resolve_applicable_references()` and catalog validation run after all passes complete. Owned and project agent entries that received sidecar triggers participate normally; the `source` tag is unchanged (D4), so `is_agent_routable` behavior is unaffected.

### Watcher coverage

The `refresh-catalog-on-stale.js` hook's `maxSourceMtime()` function currently globs `agents/*.md` under both `~/.claude/agents/` and `<repo>/.claude/agents/`. The predicate `(p) => basename(p).endsWith(".md")` will not match `*.triggers.yml` files. The watcher must be extended to also stat `*.triggers.yml` files in those same directories so that adding or editing a colocated sidecar triggers a catalog rebuild. The extension is a targeted predicate change in `maxSourceMtime()` in `hooks/refresh-catalog-on-stale.js`.

---

## §4 Sidecar file schema

A colocated sidecar lives at `agents/<name>.triggers.yml` (owned) or `<repo>/.claude/agents/<name>.triggers.yml` (project). Its schema is the same trigger block used by plugin-agent override sidecars from #140, with no additional fields:

```yaml
# agents/code-writer.triggers.yml
#
# Dispatch metadata for the code-writer owned agent.
# This file overrides any triggers: block in code-writer.md (D2).
# The agent file itself is unchanged.

triggers:
  keywords:
    - { term: "implement", weight: 1.0 }
    - { term: "write",     weight: 0.8 }
    - { term: "fix",       weight: 0.6 }
  path_globs:
    - "src/**/*.py"
    - "src/**/*.ts"
  command_prefixes: []
  agent_mentions: ["code-writer"]
  tool_mentions: []
  excludes: []

applicable_skills: ["python"]
# NOTE: [] means the agent receives NO skills, which is almost always wrong.
# Use ["*"] to grant any applicable skill, or list specific skill names.
```

### Fields NOT in the sidecar

The following fields are inherited from the matched `.md` entry and must not appear in the sidecar:

- `name` — taken from the file stem, which must match the agent's `.md` filename.
- `description` — inherited from the agent's frontmatter. The sidecar cannot override the description.
- `kind` — always `"agent"` for colocated agent sidecars; not a sidecar field.
- `source` — remains `"owned"` or `"project"` per the agent's origin directory (D4); not a sidecar field.

### Required fields

| Field | Type | Notes |
|---|---|---|
| `triggers` | `mapping` | Standard trigger block per `docs/design/trigger-schema.md §2d`. All sub-fields optional; an absent or empty block makes the dispatch surface dormant. |

### Optional fields

| Field | Type | Default | Notes |
|---|---|---|---|
| `applicable_skills` | `list[str]` | `[]` | Skills to attach when routing to this agent. `["*"]` = any applicable skill. `[]` = no skills. An empty list is the safe default but is almost always wrong — the agent will route correctly but receive no skills. The builder emits a warning when `triggers` is non-empty and `applicable_skills` is empty, consistent with existing agent validation. |

---

## §5 Catalog builder changes

### Pass 2b: new owned-agent sidecar walker

The primary change is a new walker sub-pass inside `build()` in `src/claude_wayfinder/build_catalog.py`, inserted after the existing Pass 2 agent loop (around line 1747). The walker globs `agents_dir/*.triggers.yml` — a non-recursive glob, matching the existing `agents_dir/*.md` pattern — and applies sidecar data to already-assembled owned-agent entries. The walker is a standalone code block inside `build()`, not a new top-level function, because it operates on the in-progress `entries` list directly.

The lookup uses a name-keyed index over the current entries list (same shape as the project-override merge at the end of Pass 4, around line 2043). When a match is found, the walker writes `triggers:` and `applicable_skills:` onto the matched entry dict in place. When the matched entry already carries inline triggers, `_logger.warning` fires before the overwrite (D2). When no match is found, `_logger.warning` fires and no entry is created (D3).

### Pass 4b: new project-agent sidecar walker

An equivalent walker follows the existing Pass 4 agent loop (around line 2039). It globs `proj_agent_dir/*.triggers.yml` and applies the same logic against `project_entries` before those entries are merged into the main `entries` list. Orphan project sidecars emit a warning and are dropped.

### Integration with existing entry validation

Both walkers apply sidecar data before `_resolve_applicable_references()` runs (line 2058). This means sidecar-supplied `applicable_skills` references are resolved and validated in the same pipeline pass as inline-supplied references — no special handling is needed. Schema validation for the sidecar's trigger block follows the same downstream path as inline trigger validation; the walker does not need to validate schema fields itself.

### Watcher patch

`hooks/refresh-catalog-on-stale.js`, function `maxSourceMtime()`. The two `walkFiles` calls that scan `agentsDir` and `projAgentsDir` currently use the predicate `(p) => basename(p).endsWith(".md")`. Each must be extended (or a sibling `walkFiles` call added) to also match `(p) => basename(p).endsWith(".triggers.yml")`. The existing `triggersDir` walk in `maxSourceMtime()` covers `triggers/<plugin>/agents/*.yml` but does not cover the new colocated locations in `agentsDir` or `projAgentsDir`.

---

## §6 Schema-doc updates

The following changes to existing documentation are needed. They are listed here for the implementation plan; edits should not be made until the implementing PR for this spec.

### `docs/schema.md`

**`source` field values table.** The rows for `"owned"` and `"project"` currently describe inline frontmatter as the sole source of trigger and applicable_skills data. Each row should be extended with a note that trigger configuration may alternatively come from a colocated `<name>.triggers.yml` sidecar. No change to the `source` enum values themselves (D4).

**Schema version note.** The introductory paragraph states: "agents use inline frontmatter." This should be updated to reflect that agents may use either inline frontmatter or a colocated sidecar, with sidecar taking precedence when both are present.

### `docs/design/trigger-schema.md`

A new section should document the colocated-sidecar pattern as the recommended way to author trigger configuration for user-owned and project-local agents. The section should:
- Describe the file naming convention (`<name>.triggers.yml` adjacent to `<name>.md`).
- State that sidecar takes precedence over inline `triggers:` frontmatter, and that the builder warns when both are present.
- Note that inline `triggers:` in agent frontmatter continues to be supported indefinitely (D5); the sidecar is preferred for new authoring because it separates dispatch configuration from agent prose.
- Reference the schema in §4 of this spec (once promoted to permanent docs).

No changes are required to the trigger-block field definitions themselves — the sidecar uses the same schema as existing trigger files.

---

## §7 Open implementation questions

The following questions surfaced during spec writing and depend on code reading or implementation judgment that the spec cannot lock down:

1. **Exact warning string formats.** The spec prescribes `_logger.warning` for two cases — sidecar shadows inline triggers (D2), and orphan sidecar with no matching `.md` (D3) — but does not lock the exact strings. The implementation should follow the pattern of existing warning messages in `build_catalog.py` (e.g., `"project entry '%s' overrides user-global entry"` at line 2049). Suggested shapes: `"owned-agent sidecar '%s' shadows inline triggers in '%s' — sidecar takes precedence"` and `"owned-agent sidecar '%s' has no matching agent .md file — sidecar dropped"`. The exact strings should be chosen during implementation and then added as examples to the authoring section in `trigger-schema.md`.

2. **Trigger schema validation in the walker vs. downstream.** The walker's job is to find the sidecar, parse its YAML, and write the data onto the entry. Schema validation for trigger fields (weight ranges, glob syntax) currently happens downstream in the `build()` pipeline. The implementation should confirm that sidecar-applied triggers pass through the same validation path as inline triggers. If they do not — for example, if the validation step reads from frontmatter rather than the assembled entry dict — the walker may need to call the validation function explicitly before applying the data.

3. **Whether `*.triggers.yml` files could be accidentally matched by the existing `*.md` globs.** The owned-agent glob at line 1724 is `agents_dir.glob("*.md")` and the project-agent glob at line 2020 is `proj_agent_dir.glob("*.md")`. Neither pattern matches `*.triggers.yml`, so colocated sidecars will not be accidentally picked up as agent definitions. The implementation should assert this at the start of the implementation plan (a one-line grep for `.triggers.yml` in the glob patterns suffices). If a future refactor changes these globs to `*.{md,yml}` or similar, the assertion will surface the regression.

4. **Invalid YAML in a sidecar — warn and skip, or error the whole build?** The #140 spec adopted warn-and-skip for unmatched agent sidecars (D5 of that spec). By analogy, a colocated sidecar with invalid YAML should warn and skip — the matched agent entry is preserved with its inline triggers (or empty triggers if none), and the build continues. The implementation should confirm this matches the behavior of existing YAML-parse failure paths in `discover_plugin_overrides` and align accordingly.

5. **Behavior when a sidecar's stem matches a project entry but the project entry was itself overridden by another project entry.** Pass 4 merges project entries over user-global entries by name. If Pass 4b runs after that merge (against `project_entries`, before the merge step), orphan detection must use the pre-merge project list. If Pass 4b runs after the merge (against `entries`), it must filter to `source="project"` entries. The implementation should clarify the sequencing and document it in the implementation plan.

---

## §8 Out of scope

The following are explicitly not addressed by this spec:

- **Deprecating inline `triggers:` frontmatter.** Inline triggers continue to work indefinitely (D5). No deprecation timeline, no migration tooling, no warning-on-all-inline behavior. The D2 warning fires only when both inline and sidecar are present in the same agent.
- **Whole-agent declaration in YAML.** The sidecar carries only dispatch metadata (`triggers:`, `applicable_skills:`). Agent identity (`name`, `description`, prose body) lives in the `.md` file. Declaring an agent entirely in YAML — without a corresponding `.md` — is out of scope and is excluded by D3 (orphan sidecars are dropped).
- **Sidecars at arbitrary paths.** Only colocated sidecars (adjacent to the agent `.md`) are in scope. A centralized `~/.claude/agent-triggers/<name>.yml` directory structure is a different design that is not motivated by the problem statement.
- **Changes to the plugin-agent sidecar mechanism.** The `triggers/<plugin>/agents/<name>.yml` path and its discovery logic were shipped in #142. This spec does not modify that mechanism.
- **Project-local overrides of user-global agents.** A project sidecar at `<repo>/.claude/agents/code-writer.triggers.yml` applies only when there is a corresponding `<repo>/.claude/agents/code-writer.md`. Overriding a user-global agent's triggers from a project-local sidecar without a matching project-local `.md` is not supported in this design; that would require a new override-without-ownership mechanism that is out of scope here.
