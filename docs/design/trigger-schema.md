# Trigger Schema

> Status: design · Companion doc: [Design rationale](../design.md)

## 1. Purpose and non-goals

### Purpose

Define the **trigger schema** for skill and agent catalog entries. The catalog generator (`src/claude_wayfinder/build_catalog.py`) reads this schema, validates it, and emits `dispatch-catalog.json`. The matcher (`src/claude_wayfinder/match.py`) consumes that catalog at dispatch time.

The schema replaces "router scans prose `description:` fields with an LLM" with "matcher scores against structured trigger fields." That is the load-bearing change the v5 design commits to.

### Non-goals (deferred)

- **Catalog generator implementation** — see `src/claude_wayfinder/build_catalog.py`.
- **Matcher** — see `src/claude_wayfinder/match.py`.
- **Per-skill/per-agent frontmatter migration** — tracked separately; each becomes a small "add `triggers.yml`" PR.
- **Plugin override authoring** — mechanism described in §2b; first concrete overrides follow separately.

---

## 2. Schema reference

### 2a. Skill trigger files (sidecar format)

Each skill stores its trigger configuration in a **sidecar file** named `triggers.yml` placed next to `SKILL.md` in the same directory:

```
skills/
  python/
    SKILL.md        ← runtime fields only (name, description, etc.)
    triggers.yml    ← trigger config
```

The `SKILL.md` frontmatter **must not** contain `triggers:`, `applicable_agents:`, or `applicable_skills:` blocks. If these keys are found in a `SKILL.md`, the generator emits a **warning** and ignores them — the sidecar is the authoritative source.

A `triggers.yml` file has the following structure:

```yaml
triggers:
  command_prefixes: []
  agent_mentions: []
  path_globs: []
  keywords:
    - { term: "...", weight: 0.25 | 0.5 | 1.0 }
  tool_mentions: []
  excludes: []
applicable_agents: []   # hard allowlist of agent names that may receive this skill
```

### 2b. Plugin override files

Plugin-owned skills ship in plugin cache directories and cannot be edited (plugin updates overwrite changes). These skills can be given trigger config via **plugin override files** at:

```
triggers/<plugin>/<skill>.yml
```

For example:

```
triggers/
  my-plugin/
    my-skill.yml
  another-plugin/
    broken-skill.yml   ← tombstone: retires broken skill
```

Override files use the same schema as owned-skill sidecar files. The catalog entry name is synthesised as `<plugin>:<skill>` to match the plugin loader convention.

#### Plugin-override `kind:` field

By default a plugin override produces a **skill** entry. Set `kind: agent` to produce an agent entry instead:

```yaml
# triggers/my-plugin/my-agent.yml
kind: agent
triggers:
  keywords:
    - { term: "example", weight: 1.0 }
applicable_skills: ["linter-skill"]
```

| Value     | Meaning                                                                          |
| --------- | -------------------------------------------------------------------------------- |
| `"skill"` | Default. Entry emitted as `kind="skill"`.                                        |
| `"agent"` | Entry emitted as `kind="agent"`. Required for plugin agents to participate in routing (see §2g). |

Any other value is a **fatal** `ValidationIssue`; the entry is excluded from the catalog.

#### Tombstone sidecar fields

A plugin override can **remove** a plugin-discovered entry by declaring it disabled. The entry will not appear in the catalog and will never match at dispatch time — treated as nonexistent by subsequent `applicable_skills` resolution.

```yaml
disabled: true
reason: "permanently broken — use local skills/replacement instead."
```

| Field      | Type   | Required | Notes                                                    |
| ---------- | ------ | -------- | -------------------------------------------------------- |
| `disabled` | `bool` | yes      | Must be `true`. Any other value is not a tombstone.      |
| `reason`   | `str`  | no       | Human-readable explanation. Included in the catalog log. |

A tombstone targeting an entry that does not exist (e.g. the plugin was uninstalled) logs a `warning`; no other action is taken.

### 2c. Agent files

<!-- D2/D3/D5 extracted from #148 owned-project-agent-sidecars spec (audit #216 → fix #221): sidecar wins over inline triggers (warn when both present); strict orphan handling (no matching .md → warn and drop); inline triggers coexist indefinitely, no forced migration -->
<!-- D3/D5 extracted from #140 plugin-agent-sidecar-overrides spec (audit #216 → fix #221): strict Mode 2a semantics apply to colocated sidecars too; unmatched sidecars emit warnings and are dropped -->
Agents support two equivalent trigger sources. Inline frontmatter is the original form; the **colocated sidecar** (Issue #148) is the recommended path for new authoring because it separates dispatch configuration from agent prose.

#### Inline frontmatter (supported indefinitely)

```yaml
---
name: linter-agent
description: Use this agent when the task involves running linters or formatters.
triggers:
  keywords:
    - { term: "lint", weight: 1.0 }
    - { term: "format", weight: 0.5 }
applicable_skills: ["python", "github-actions"]
---
```

#### Colocated sidecar (recommended for new authoring — Issue #148)

Place a `<name>.triggers.yml` file next to the agent `<name>.md` in the same directory:

```
~/.claude/agents/
  linter-agent.md              ← agent identity and prose
  linter-agent.triggers.yml    ← dispatch configuration (new, recommended)

<repo>/.claude/agents/
  linter-agent.md
  linter-agent.triggers.yml
```

The sidecar carries only the dispatch fields (`triggers:` and `applicable_skills:`); the agent identity (`name`, `description`, prose body) lives in the `.md` file and is not affected. The stem of the sidecar filename must match the stem of the `.md` file exactly — `linter-agent.triggers.yml` pairs with `linter-agent.md`.

```yaml
# agents/linter-agent.triggers.yml
#
# Dispatch configuration for linter-agent.
# Overrides any triggers: block in linter-agent.md (D2).

triggers:
  keywords:
    - { term: "lint",   weight: 1.0 }
    - { term: "format", weight: 0.5 }
applicable_skills: ["python", "github-actions"]
# NOTE: ["*"] grants any applicable skill; [] means no skills (almost always wrong).
```

**Fields NOT in the sidecar:**

| Field       | Source          |
| ----------- | --------------- |
| `name`      | Taken from the `.md` frontmatter; the sidecar's stem must match it. |
| `description` | Inherited from the `.md` frontmatter; cannot be overridden by a sidecar. |
| `kind`      | Always `"agent"` for agent sidecars; not a sidecar field. |
| `source`    | Remains `"owned"` or `"project"` per the agent's origin; the sidecar is a delivery mechanism, not an authorship claim. |

**Precedence and warnings:**

- When both a sidecar and inline `triggers:` are present the sidecar wins. The catalog builder emits a `warning`-level log entry identifying the agent:
  ```
  warning  <name>  colocated agent sidecar '<name>.triggers.yml' shadows inline triggers in '<name>.md' — sidecar takes precedence
  ```
- Inline `triggers:` in agent frontmatter continues to be supported indefinitely. The sidecar is preferred for new authoring but there is no deprecation timeline for inline.
- A sidecar with no matching `.md` file (orphan) is dropped with a warning:
  ```
  warning  <name>  colocated agent sidecar '<name>.triggers.yml' has no matching agent .md file — sidecar dropped
  ```
- A sidecar with malformed YAML is skipped with a warning; the agent's inline triggers (or empty triggers if none) are preserved:
  ```
  warning  <name>  YAML parse error in colocated agent sidecar '<name>.triggers.yml': <detail>
  ```

### 2c-a. `routable` field (agents only)

| Field      | Type   | Required | Default | Scope        |
| ---------- | ------ | -------- | ------- | ------------ |
| `routable` | `bool` | no       | `true`  | agents only  |

**Purpose:** marks the router agent (or any non-scoreable agent) so it is excluded from
the `is_agent_routable` selection in the matcher. The matcher caller is, by definition,
not a candidate for delegation — it must never appear in the scored-agents pool.

**How the flag is used:** `is_agent_routable` in `src/claude_wayfinder/match_filters.py`
accepts a `routable` keyword argument. The matcher passes the catalog entry's `routable`
field through at dispatch time. This replaces the former hardcoded name check
(`name == "general-purpose"`), making the system usable by teams whose router has any
name (issue #19).

**Catalog metadata:** the top-level `router_agent` key in `dispatch-catalog.json` is set
to the name of the first agent with `routable: false` (sorted by `(kind, name)`). This
field is **informational only** — the per-entry flag is the actual exclusion gate. When no
agent declares `routable: false`, `router_agent` is `null` and the catalog generator emits
a warning to stderr.

**Example — router agent frontmatter:**

```yaml
---
name: general-purpose
description: The dispatch router. Never a delegation target.
routable: false
triggers:
  keywords:
    - { term: "route", weight: 1.0 }
applicable_skills: ["*"]
---
```

**If multiple agents declare `routable: false`:** each is individually excluded from
scoring. The catalog's `router_agent` metadata field names only the first one found (in
sort order); the flag on each entry is authoritative for that entry.

### 2d. Field reference

| Field                               | Type                   | Required | Match target                                                                     | Default            |
| ----------------------------------- | ---------------------- | -------- | -------------------------------------------------------------------------------- | ------------------ |
| `triggers.command_prefixes`         | `list[str]`            | no       | `features.command_prefix` (single value, exact, case-insensitive)                | `[]`               |
| `triggers.agent_mentions`           | `list[str]`            | no       | `features.agent_mentions` (set, exact, case-insensitive)                         | `[]`               |
| `triggers.path_globs`               | `list[str]`            | no       | each glob matched via `fnmatch.fnmatch` against any element of `features.paths`  | `[]`               |
| `triggers.path_globs_excluded`      | `list[str]`            | no       | each glob matched via `fnmatch.fnmatch` against elements of `features.paths`; a path matching any excluded glob contributes 0 to this agent's path score — other paths in the same input are unaffected (#287). | `[]` |
| `triggers.keywords`                 | `list[{term, weight}]` | no       | `features.keywords` (set, case-insensitive exact token)                          | `[]`               |
| `triggers.keyword_groups`           | `list[{slots: list, weight: float}]` | no | each slot matched against `features.keywords`; group fires only when **every** slot has ≥ 1 matching term. See § 2i for shape, § 4 for matching rules, § 6 for validation. | `[]` |
| `triggers.tool_mentions`            | `list[str]`            | no       | `features.tool_mentions` (set, exact, case-insensitive)                          | `[]`               |
| `triggers.excludes`                 | `list[str]`            | no       | `features.keywords` (same matching as `keywords`)                                | `[]`               |
| `applicable_agents` _(skills only)_ | `list[str]`            | no       | hard allowlist of agent names; `["*"]` = any                                     | `[]` (= **none**) |
| `applicable_agents_intentional` _(skills only)_ | `string` | no  | non-empty rationale string; suppresses the `empty-applicable-agents` audit NIT when `applicable_agents` is intentionally `[]`. See `docs/dispatch-authoring-guide.md:327–332`. | absent |
| `applicable_skills` _(agents only)_ | `list[str]`            | no       | hard allowlist of skill names; `["*"]` = any                                     | `[]` (= **none**) |

> **Removed field:** `triggers.file_extensions` was present in an earlier version of this schema but is **removed**. Use `triggers.path_globs` instead (e.g. `"**/*.py"` instead of `["py"]` in `file_extensions`). Sidecars that still declare `file_extensions` will receive a **warning** from the catalog generator and the field will be dropped.

The whole `triggers:` block is optional. A missing block means the entry is **dormant** (catalog includes it; it never matches at dispatch time). See §8.

#### `source` field values

Each catalog entry carries a `source` field indicating its origin:

| Value               | Meaning                                                                                                                                           |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| `"owned"`           | Scanned from the user's own `skills/` or `agents/` tree.                                                                                          |
| `"plugin"`          | Discovered from an installed plugin. Entries land **dormant** (zero triggers) and are inert at dispatch time unless activated by a plugin-override sidecar. |
| `"plugin-override"` | Loaded from a `triggers/<plugin>/<skill>.yml` override file. Replaces the matching `source="plugin"` entry, or adds a new entry when no plugin-discovered entry exists. |
| `"builtin"`         | Loaded from a `triggers/builtin/<Agent>.yml` operator sidecar. Represents runtime-embedded agents. Entries are **routable by default** and must declare `min_claude_version`. See §2h. |
| `"project"`         | Scanned from `<repo>/.claude/skills/` or `<repo>/.claude/agents/` when the generator is run inside a git repo. Project entries override user-global entries on name collision. |

### 2e. Plugin manifest dependency

Plugin discovery reads the installed-plugins manifest. The catalog generator requires **version >= 2** of that manifest format.

Failure modes — each appends a `ValidationIssue` to the catalog log and returns an empty plugin list rather than aborting the build:

| Condition                                | Severity  | Effect               |
| ---------------------------------------- | --------- | -------------------- |
| Manifest file absent                     | `info`    | No plugins loaded    |
| Manifest JSON malformed                  | `warning` | No plugins loaded    |
| `version` absent or `< 2`               | `warning` | No plugins loaded    |
| `plugins` key absent or not a mapping    | `warning` | No plugins loaded    |
| Install entry missing `installPath`      | `warning` | That install skipped |
| `installPath` does not exist on disk     | `warning` | That install skipped |
| `scope != "user"` (e.g. workspace scope) | `info`    | Skipped silently     |

Implementation: `discover_installed_plugins` in `src/claude_wayfinder/build_catalog.py`.

### 2f. Collision-merge mechanics

When a plugin-override sidecar is applied, the catalog generator merges it against previously assembled entries according to the following rules.

#### Owned-entry protection

An override (regular or tombstone) that targets an entry with `source="owned"` is **rejected**:

```
warning  <entry-name>  plugin override targets owned entry '<name>' — rejected; owned entry preserved
warning  <entry-name>  disable override targets owned entry '<name>' — rejected; owned entry preserved
```

The owned entry is left unchanged. This prevents plugin-authored overrides from silently replacing user-authored skills or agents.

#### Tombstone deletion

A tombstone sidecar (`disabled: true`) removes the matching entry from the catalog:

```
info  <entry-name>  plugin entry disabled by override (reason: <reason>)
```

If no entry with that name exists (plugin was uninstalled, name is misspelled):

```
warning  <entry-name>  disable override targets nonexistent entry '<name>'
```

No entry is created; the warning is purely diagnostic.

#### Regular override on a plugin-discovered entry

When a non-tombstone sidecar targets an entry with `source="plugin"`, the plugin-discovered entry is replaced in place:

```
info  <entry-name>  override layers on plugin-discovered entry '<name>'
```

The replaced entry inherits `source="plugin-override"`. If no plugin-discovered entry with that name exists, the override is appended as a new entry (no warning — this is the expected path for opt-in plugin-agent routing via `kind: agent`).

### 2g. Plugin agents and `is_agent_routable`

<!-- D3/D4/D6 extracted from #140 plugin-agent-sidecar-overrides spec (audit #216 → fix #221): strict Mode 2a (ghost sidecars warn and drop, not append); no min_claude_version in agent sidecars (plugin versioning via manifest); watcher coverage verified — hooks/refresh-catalog-on-stale.js walks triggers/<plugin>/agents/*.yml (lines 208-223) -->
Plugin-discovered agents (`kind="agent"`, `source="plugin"`) are **inert by default**: `is_agent_routable` in `src/claude_wayfinder/match_filters.py` filters them out of the scoring pool at dispatch time. They appear in the catalog but never drive a routing decision.

**Activating a plugin agent via an agent sidecar (Issue #140):** create an agent plugin-override sidecar at `triggers/<plugin>/agents/<name>.yml` with a non-empty `triggers:` block. The catalog builder walks this subdirectory during Pass 3b and replaces the matching dormant plugin entry with `source="plugin-override"` and `routable: true`. The sidecar must match an installed plugin agent — unmatched sidecars (ghost sidecars) emit a warning and are dropped (strict Mode 2a semantics). `is_agent_routable` treats `source="plugin-override"` as routable.

**Legacy path (still supported):** create a flat sidecar at `triggers/<plugin>/<name>.yml` with `kind: agent`. This triggers Pass 3 (skill override path) and appends a new entry when no matching dormant agent exists. The `agents/` subdirectory form (Issue #140) is preferred for agent overrides because it provides structural disambiguation and enforces match-required semantics.

The predicate also unconditionally excludes the router agent itself from the scored pool, regardless of source.

```python
# src/claude_wayfinder/match_filters.py
def is_agent_routable(*, name: str, kind: str, source: str) -> bool:
    if name == "router-agent":       # the router itself is never a delegation target
        return False
    if kind == "agent" and source == "plugin":
        return False
    return True
```

Plugin **skills** with `source="plugin"` are not filtered by this predicate — they remain in the skill pool, score 0.0 because they are dormant, and can be activated by a plugin-override sidecar.

**Namespace collision:** when a plugin ships both a skill named `foo` and an agent named `foo`, the catalog dedup key is `(kind, name)` — both `(kind="skill", name="p:foo")` and `(kind="agent", name="p:foo")` may coexist. Override sidecars targeting each are independent: `triggers/p/foo.yml` matches the skill entry, `triggers/p/agents/foo.yml` matches the agent entry. This is documented by the Pass 3b test suite (Issue #140, §7 Q3).

### 2h. Builtin agents and version pinning

Some agent frameworks ship **built-in agents** embedded in the binary (e.g. `Explore`, `Plan`). Unlike owned agents (which live in the `agents/` directory) or plugin agents (discovered from installed plugins), built-in agents cannot be edited. Their trigger surface is defined by **operator-authored sidecars** under:

```
triggers/builtin/<Agent>.yml
```

#### Sidecar schema

Builtin sidecars use the same trigger schema as plugin-override sidecars, with two additional fields:

```yaml
name: ExploreAgent
kind: agent
description: >
  Read-only search agent for navigating codebases.
min_claude_version: "2.1"   # required — see version pinning below
max_claude_version: "3.0"   # optional upper bound
triggers:
  keywords:
    - { term: "locate", weight: 1.0 }
    - { term: "find", weight: 0.5 }
  agent_mentions: ["ExploreAgent"]
  command_prefixes: []
  path_globs: []
  tool_mentions: []
  excludes: []
applicable_skills: ["*"]
```

| Field                | Type    | Required | Notes                                                         |
| -------------------- | ------- | -------- | ------------------------------------------------------------- |
| `name`               | `str`   | yes      | Must match the built-in agent name exactly.                   |
| `kind`               | `str`   | yes      | Must be `"agent"`.                                            |
| `description`        | `str`   | no       | Human-readable description for the catalog.                   |
| `min_claude_version` | `str`   | **yes**  | Semver string; entry excluded if running version is below it. |
| `max_claude_version` | `str`   | no       | Semver string; entry excluded if running version exceeds it.  |
| `triggers`           | mapping | no       | Standard trigger block — same rules as §2a / §2b apply.       |
| `applicable_skills`  | `list`  | no       | Skills to attach when routing to this agent. `["*"]` = any.   |

#### Version pinning rationale

Built-in agents are embedded in the runtime binary and ship with no changelog the matcher can read. The sidecar author must declare which binary versions the trigger surface is valid for. Without a pin, the catalog generator has no safe way to determine whether the built-in agent still exists or has changed behavior.

**Resolution order for the running version:**

1. Shell out to the version command and parse the first token.
2. Fall back to a `CLAUDE_VERSION` environment variable.
3. If neither succeeds, emit a **fatal** `ValidationIssue` and exclude all builtin entries from the build.

#### Version pinning validation rules

| Condition                               | Severity  | Effect                  |
| --------------------------------------- | --------- | ----------------------- |
| `min_claude_version` absent             | `fatal`   | Entry excluded          |
| Running version `< min_claude_version`  | `warning` | Entry excluded          |
| Running version `> max_claude_version`  | `warning` | Entry excluded          |
| Running version within `[min, max]`     | —         | Entry included normally |
| Version detection fails and no env var  | `fatal`   | All builtins excluded   |

#### `is_agent_routable` for builtin agents

Builtin agents with `source="builtin"` are **routable by default** — unlike plugin agents (`source="plugin"`) which are inert until activated by an override. This reflects the fact that sidecar authors have explicitly opted the built-in into routing by writing a trigger block.

### 2i. `keyword_groups` (added 2026-05-18 per #135)

```yaml
triggers:
  keyword_groups:
    - slots:
        - {name: verbs, terms: [update, edit, modify, change]}
        - {name: nouns, terms: [docs, readme, spec]}
      weight: 1.0
    - slots:
        - [github]
        - [issue, pr, workflow]
      weight: 1.0
```

A `keyword_group` expresses a **conjunctive trigger** — *"all of slot A AND all of slot B"* — that fires only when every slot has at least one matching term in `features.keywords`.

- `slots`: list of 2–8 slot objects. Each slot is either:
  - A bare list of alternative term strings (`[a, b, c]`), or
  - A dict `{terms: [a, b, c], name: "verbs"}` with an optional human-readable `name`.
- `weight`: float in `{0.25, 0.5, 1.0}`. A satisfied group contributes `_GROUP_MULTIPLIER × weight` to the entry's score (currently `_GROUP_MULTIPLIER = 1.0`).
- See §4 (keyword_groups matching) for matching rules; §6 (keyword_groups validation) for validation; §9.10 for a worked example.

Authoritative design: `docs/superpowers/specs/2026-05-18-and-groups-design.md`.

---

## 3. The `features` JSON contract

The matcher fires on a context object that the dispatch skill body composes. Authors writing triggers must know what shape they are matching against:

```jsonc
{
  "command_prefix":  "string|null",  // single value: "/whats-next", "/loop", null
  "agent_mentions":  ["string"],     // explicit "@agent" or unambiguous prose mentions
  "keywords":        ["string"],     // LLM-extracted terms; lowercased; deduplicated
  "paths":           ["string"],     // file/dir paths named in prompt
  "tool_mentions":   ["string"],     // explicit tool names: "git", "az", "gh", "bash", ...
}
```

The dispatch skill body is responsible for **normalization**: lowercasing, stripping punctuation, dropping common stopwords. Trigger authors should list **the tokens a careful reader would extract from the prompt** — not synonyms, not stems, not regexes.

### Field population

| Feature bucket   | Populated from                                   | Notes                                         |
| ---------------- | ------------------------------------------------ | --------------------------------------------- |
| `command_prefix` | router's `command_prefix` JSON field             | normalized to lowercase                       |
| `agent_mentions` | router's `agent_mentions` JSON field             | does **not** tokenize from `task_description` |
| `tool_mentions`  | router's `tool_mentions` JSON field              | does **not** tokenize from `task_description` |
| `keywords`       | tokenized from `task_description` text           | lowercased, hyphen-preserving, single tokens  |
| `paths`          | router's `file_paths` JSON field                 | passed through as-is                          |

Authors should be aware that `keywords` is the only bucket fed by free-form text. Tool and agent mentions are explicit-only — they reach the matcher only when the router populates the dedicated JSON fields.

---

## 4. Matching rules

Per the scoring algorithm in [`docs/schema.md §4`](../schema.md#4-scoring-and-decision-algorithm), the per-entry score is:

```python
def score(entry, features):
    if features.command_prefix in entry.triggers.command_prefixes:
        return 1.0
    if any(m in features.agent_mentions for m in entry.triggers.agent_mentions):
        return 1.0
    if any(x in features.keywords for x in entry.triggers.excludes):
        return 0.0

    s = 0.0
    s += 0.4 * matched_glob_count(entry, features)
    s += sum(0.5 * k.weight for k in entry.triggers.keywords if k.term in features.keywords)
    s += 0.5 * len([t for t in entry.triggers.tool_mentions if t in features.tool_mentions])
    return min(s, 1.0)
```

### Matching rules — `excludes` matches `keywords` only

The exclude short-circuit checks `features.keywords` and **only** `features.keywords`. Terms in `features.tool_mentions` and `features.agent_mentions` are not consulted.

This is deliberate: keywords are the dense, free-form bucket — anything an author plausibly wants to exclude on (e.g. `openai`, `aws`, `gpt`) shows up there when the router composes a concrete `task_description`. Tool and agent mentions are explicit, single-purpose signals that authors do not typically want to negate.

**Common case:** the router's `task_description` describes what the user wants ("call the OpenAI API from..."), `openai` tokenizes into `features.keywords`, and a `claude-api` entry with `excludes: ["openai"]` correctly scores `0.0`.

**Edge case:** when you need to exclude a term that may only appear in `tool_mentions` or `agent_mentions`, add the same term to `keywords` as a defensive marker AND keep it in `excludes`. The dispatch skill's authoring rules already push routers to mention tools in `task_description`, so this case should be rare — surface it as a router-discipline bug if it recurs.

### Matching rules — keywords MUST be single tokens

Keywords MUST be single tokens — entries containing whitespace are dropped at build time with a warning. Author multi-word triggers as separate single-token synonyms.

For example, instead of `{term: "type hints", weight: 0.5}`, write two separate keywords: `{term: "type", weight: 0.25}` and `{term: "hints", weight: 0.25}` — or omit the weaker one.

### Weight clamp — IMPORTANT footgun

**Weights are clamped to the ladder {0.25, 0.5, 1.0}.** Any value in `[0.0, 1.0]` that is NOT on this ladder is silently normalized to the nearest ladder value at build time. A weight of `0.75` becomes `1.0`. A weight of `0.4` becomes `0.5`. The catalog generator emits a `warning` when this happens, but the entry is kept.

Do not rely on intermediate weight values — they will be normalized. Use only `0.25`, `0.5`, or `1.0`.

### fnmatch path glob footgun — IMPORTANT

Path globs use Python's `fnmatch.fnmatch` semantics, not shell globbing. A critical consequence: **`**` does NOT match across directory separators in Python's `fnmatch`**. The pattern `"**/*.py"` does NOT match `"src/foo.py"` in a naive `fnmatch` call.

The catalog generator addresses this by testing each glob pattern against both:
1. The full path as given.
2. The basename of the path.

This means **root-level files require both forms**: to match a file named `pyproject.toml` that could appear either at the repo root or nested, declare:

```yaml
path_globs:
  - "**/*.toml"    # matches nested paths via the generator's full-path test
  - "*.toml"       # matches bare filenames via the generator's basename test
```

If you declare only `"**/*.toml"`, a bare `pyproject.toml` at the repo root may not match depending on how the path was reported. When in doubt, include both forms. The `matched_glob_count` function deduplicates so both matching does not double-count.

### Field-by-field semantics

- **`command_prefixes`** — short-circuit to `1.0`. Use for skills invoked exclusively via slash commands.
- **`agent_mentions`** — short-circuit to `1.0` when the prompt explicitly names an agent. Use sparingly; this is for skills tightly coupled to a single named agent.
- **`excludes`** — short-circuit to `0.0`. Hard zero-out. Used to disambiguate skills with overlapping keywords (e.g. `claude-api` excludes `openai`). Matches `keywords` only — see above.
- **`path_globs`** — `0.4` per matched glob (each glob counted at most once even if it matches multiple paths). Uses `fnmatch` syntax — see footgun above.
- **`path_globs_excluded`** — path-level exclusion gate. If **any** glob in this list matches **any** candidate file path, the entire entry is dropped from the scored pool before any additive scoring. Exclusion wins over inclusion: an entry that matches both `path_globs` and `path_globs_excluded` is dropped. Uses the same `fnmatch.fnmatch` semantics as `path_globs`. See §4a.
- **`keywords`** — `0.5 × weight` per matched term, accumulated. Weight ladder: `0.25` (weak), `0.5` (normal), `1.0` (strong). Terms must be single tokens (no whitespace).
- **Satisfied keyword group** (AND-group all-terms-match) — `1.0 × group.weight` per satisfied group (additional bonus beyond per-term contributions; see `_match.py:_GROUP_MULTIPLIER`).
- **`tool_mentions`** — `0.5` per matched tool name. Highest per-element coefficient because tool mentions are unambiguous high-precision signals.

Total score is clamped to `1.0`.

### Author guidance — picking weights

| Weight | Use for                                                                                                                               |
| ------ | ------------------------------------------------------------------------------------------------------------------------------------- |
| `1.0`  | The skill's defining concept. The term whose absence means the skill should not match.                                                |
| `0.5`  | Strong supporting term. Frequent co-occurrence with the topic.                                                                        |
| `0.25` | Weak hint. Common across many topics; included for recall, not precision.                                                             |

PR review heuristic: if you can't justify "removing this `1.0` term means the skill should not match," it should be `0.5`.

### `keyword_groups` matching

A group is **satisfied** when *every* slot contains at least one term in `features.keywords`. Strict all-of; no partial credit.

When satisfied:

1. The group contributes `_GROUP_MULTIPLIER × group.weight` to the entry's pre-clamp score.
2. **Replacement rule:** every singleton keyword on the same entry whose `term` appears in any slot of the satisfied group has its contribution **suppressed** (set to zero) for this scoring pass.

When unsatisfied: zero contribution, no suppression. Singletons score normally.

Multiple satisfied groups on the same entry **sum independently** — each adds its weight. The final `min(s, 1.0)` clamp absorbs any overflow.

Authoritative pseudocode: `docs/superpowers/specs/2026-05-18-and-groups-design.md` § 5.

---

## 4a. `path_globs_excluded` — per-path-subtractive exclusion (#287)

`path_globs_excluded` is the path-level analog of `excludes`. Where `excludes` operates on
keyword tokens, `path_globs_excluded` operates on file paths. A path matching any glob in
the list contributes **0** to that agent's path score. Other paths in the same input are
unaffected — their positive contributions remain intact. (#287)

**Semantics:** per-path-subtractive. Each path is evaluated independently against the
exclusion globs. A matching path is skipped when tallying glob hits; a non-matching path
scores normally. This is distinct from a hard-exclude: an agent with five input paths, one
of which is excluded, still scores on the remaining four.

**Exclusion wins over inclusion per path.** An entry with both `path_globs: ["**/*.md"]`
and `path_globs_excluded: ["agents/**/*.md"]` will assign zero path-score contribution to
`agents/foo.md` even though the broad `**/*.md` glob would otherwise match it. Paths that
do not match any exclusion glob are unaffected.

**Use case: "applies everywhere except X" patterns.** The canonical use-case is an
agent with broad path coverage that must not activate for a specific sub-tree. Instead
of omitting that sub-tree from `path_globs` (fragile; must be re-verified when globs
change), authors can add the sub-tree to `path_globs_excluded` and get explicit,
auditable, self-documenting exclusion. Other files in the same task (outside the excluded
sub-tree) still contribute to the score normally.

```yaml
# doc-writer: catches broad **/*.md but excludes harness files
triggers:
  path_globs:
    - "docs/**/*.md"
    - "docs/*.md"
    - "README.md"
    - "**/*.md"
  path_globs_excluded:
    - "agents/**/*.md"
    - "agents/*.md"     # bare form needed — see fnmatch footgun below
    - "skills/**/*.md"
    - "skills/*.md"
```

### fnmatch footgun: include both `**/` and bare forms

The same footgun that applies to `path_globs` applies to `path_globs_excluded`. Python's
`fnmatch.fnmatch` does not treat `**` as a recursive wildcard across directory separators.
The pattern `agents/**/*.md` does **not** match a path reported as just `agents/foo.md` in
all contexts.

**Rule: always include both the nested and bare forms:**

```yaml
path_globs_excluded:
  - "agents/**/*.md"   # matches agents/subdir/foo.md
  - "agents/*.md"      # matches agents/foo.md (bare, one level deep)
```

This mirrors the guidance for `path_globs` in the fnmatch footgun section above (see also
`agent-memory/general-purpose/feedback_fnmatch_path_globs_need_both_forms.md`).

---

## 5. `applicable_agents` / `applicable_skills` — hard filter, strict empty

These fields express the agent-skill compatibility constraint. They live in the sidecar file for skills and in inline frontmatter for agents.

Two examples:

- **Tool-coupled skills:** a `powershell` skill's body assumes the `PowerShell` tool. A sub-agent that only has `Bash` cannot use it. A skill with `applicable_agents: ["planner-agent"]` will **never** be attached when delegating to `coder-agent` or `debugger-agent`, even if every keyword matches.
- **Out-of-scope skills:** an interactive skill that is router-only should have `applicable_agents: []` (the default), meaning no sub-agent ever receives it — only the router itself can activate it via direct invocation.

### The empty-list rule

| Form                                          | Meaning                                                                   |
| --------------------------------------------- | ------------------------------------------------------------------------- |
| Field absent                                  | Same as `[]` — applies to **no** agent (skill is router-only or dormant). |
| `applicable_agents: []`                       | **None** — explicitly no agents.                                          |
| `applicable_agents: ["*"]`                    | **Any** — explicit broadcast.                                             |
| `applicable_agents: ["coder-agent", "reviewer-agent"]` | Only these named agents.                               |

Empty default = none allowed is the **safe default**: a freshly-migrated skill with no `applicable_agents:` line is harmless until the author opts agents in.

The same rules apply symmetrically to `applicable_skills` on agent files.

### Generator validation

- Reference to a non-existent agent or skill name → **warning** (entry kept; bad reference dropped from the resolved list).
- Entry has triggers but `applicable_*: []` (or absent) → **warning** ("triggers declared but no applicable target — entry will never match").
- Cycle detection: not applicable; `applicable_*` is a one-step relation, not a graph.

---

## 6. Validation rules and log severity

All log lines are written to the catalog generation log. Format: `<ISO-8601> <severity> <entry-name> <message>`.

| Severity          | Triggers                                                                                                                                                                                                                                                                                                                                                                                               | Effect on entry                                            |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------- |
| **fatal**         | YAML parse error; `triggers:` is not a mapping; `keywords` element is not `{term, weight}`; `weight` not a number; `weight` outside `[0.0, 1.0]`; plugin-override sidecar has invalid `kind` value | Entry **excluded** from catalog |
| **warning**       | `weight` in `[0.0, 1.0]` but not in `{0.25, 0.5, 1.0}` (clamped to nearest); duplicate `term` in `keywords` (deduplicated, last wins); `keywords` term contains whitespace (keyword dropped); `triggers.file_extensions` declared (field dropped — use `path_globs`); `applicable_*` references non-existent name (dropped); entry has triggers but `applicable_*: []`; `SKILL.md` contains trigger keys (ignored); plugin override targets owned entry (rejected); tombstone targets owned entry (rejected); tombstone targets nonexistent entry; plugin manifest malformed/version-unsupported; **agent override sidecar has no matching plugin-discovered agent entry** (ghost sidecar — format: `"agent override sidecar '<plugin>:<name>' has no matching plugin-discovered agent entry — sidecar dropped"`); **agent override sidecar targets owned entry** (format: `"agent override sidecar targets owned entry '<name>' — rejected; owned entry preserved"`) | Entry **kept** (or override rejected), mutation logged |
| **info**          | Entry has no `triggers:` block (dormant); plugin manifest absent; plugin entry disabled by tombstone override; override layers on plugin-discovered entry; override layers on plugin-discovered agent (format: `"override layers on plugin-discovered agent '<name>'"`); `applicable_skills` contains plugin-namespaced reference (kept as external reference) | Entry kept or removed per tombstone semantics |
| **catalog-level** | >25% of entries excluded fatally; entire catalog empty | `[CATALOG ERROR]` banner at session start |

Ties (equidistant values) resolve to the larger weight.

### Forward compatibility

- **Unknown keys inside `triggers:`** are silently ignored. This lets future versions add fields without breaking older generators.
- **Unknown top-level keys** in sidecar files and agent frontmatter are silently ignored.

### `keyword_groups` validation

| Condition | Severity |
| --- | --- |
| `slots:` missing or has 0 or 1 entries | **fatal** — use `keywords:` for single-term triggers |
| `slots:` has more than 8 entries | **fatal** — max is 8 |
| `slots:` has 4–8 entries | warning — real prompts rarely contain that many roles |
| Slot has 0 terms or missing `terms:` | **fatal** |
| Slot has exactly 1 term | warning — consider merging or using `keywords:` |
| Same term in ≥ 2 slots of the SAME group | **fatal** — a term cannot fill two roles |
| `weight` outside `{0.25, 0.5, 1.0}` | **fatal** |
| `name:` on slot contains whitespace or non-identifier chars | warning |

Cross-group term overlap on the same entry is allowed.

---

## 7. EXCLUDE_DEAD_ZONE detection

For each `excludes` term, the generator can simulate routing against a captured corpus of past routing decisions (a JSONL file of `RoutingDecisionEvent` records):

1. With the exclude in place, find prompts where this skill is the top match by score.
2. Re-score those prompts with the exclude removed.
3. If removing the exclude does **not** change the decision (because no other entry was within 0.5 of this skill's score), emit:

   ```
   2026-04-30T14:22:01Z warning <skill-name> EXCLUDE_DEAD_ZONE: term 'X' never affected a decision against captured corpus (N=147)
   ```

Warning only — does not exclude the entry, does not fail CI.

If the corpus file is missing or empty, EXCLUDE_DEAD_ZONE is skipped entirely with one info-line: `corpus unavailable; skipping EXCLUDE_DEAD_ZONE checks`.

---

## 8. Backward compatibility — dormant entries

An entry is **dormant** when:

- No `triggers.yml` sidecar file exists next to a `SKILL.md` (owned skill), or
- No override file exists for a plugin skill, or
- A sidecar file exists but contains an empty `triggers:` block.

In all dormant cases, the generator emits a fully-formed catalog entry with empty trigger fields. The matcher will:

- Score the entry as 0 (no matches possible).
- Never apply its excludes (none declared).
- Never attach the skill to any agent (`applicable_agents: []` default).

The catalog stability invariant treats dormant entries no differently than active ones: dormant entries appear in the JSON output with the same keys and default values, byte-for-byte stable across runs.

---

## 9. Examples

### 9.1 Minimal valid skill

A skill with one keyword and one applicable agent.

`skills/csv-utils/SKILL.md`:

```yaml
---
name: csv-utils
description: Helpers for reading and writing CSV files in Python.
---
```

`skills/csv-utils/triggers.yml`:

```yaml
triggers:
  keywords:
    - { term: "csv", weight: 1.0 }
applicable_agents: ["coder-agent"]
```

### 9.2 Tool-coupled skill

The `powershell` skill assumes a `PowerShell` tool. Only certain agents have access to it.

`skills/powershell/triggers.yml`:

```yaml
triggers:
  path_globs:
    - "**/*.ps1"
    - "*.ps1"          # also match root-level .ps1 files — see fnmatch footgun in §4
  keywords:
    - { term: "powershell", weight: 1.0 }
    - { term: "pwsh", weight: 1.0 }
    - { term: "ps1", weight: 0.5 }
    - { term: "cmdlet", weight: 0.5 }
    - { term: "splatting", weight: 0.25 }
  tool_mentions: ["powershell", "pwsh"]
applicable_agents: ["planner-agent"]
```

### 9.3 Excludes example

`skills/claude-api/triggers.yml`:

```yaml
triggers:
  keywords:
    - { term: "claude", weight: 1.0 }
    - { term: "anthropic", weight: 1.0 }
    - { term: "messages-api", weight: 0.5 }
  excludes: ["openai", "gpt", "azure-openai"]
applicable_agents: ["coder-agent", "debugger-agent"]
```

### 9.4 Command-prefix-driven skill

`skills/whats-next/triggers.yml`:

```yaml
triggers:
  command_prefixes: ["/whats-next"]
applicable_agents: []   # router-only
```

### 9.5 Agent example

Agents keep inline frontmatter.

`agents/reviewer-agent.md` (frontmatter excerpt):

```yaml
---
name: reviewer-agent
description: Use this agent when the task involves reviewing code.
triggers:
  keywords:
    - { term: "review", weight: 1.0 }
    - { term: "audit", weight: 0.5 }
    - { term: "check", weight: 0.5 }
applicable_skills:
  - python
  - github-actions
---
```

### 9.6 Dormant entry (pre-migration)

A skill that hasn't been given a triggers file yet. The catalog includes it as inert.

`skills/legacy-skill/SKILL.md`:

```yaml
---
name: legacy-skill
description: Some pre-existing skill.
---
```

Generator log:

```
2026-04-30T14:22:00Z info legacy-skill no triggers block — entry dormant
```

### 9.7 Plugin override

A skill from an installed plugin. The plugin file cannot be edited, so we create an override:

`triggers/my-plugin/my-skill.yml`:

```yaml
triggers:
  keywords:
    - { term: "brainstorm", weight: 1.0 }
    - { term: "ideate", weight: 0.5 }
  command_prefixes: ["/brainstorm"]
applicable_agents: ["*"]
```

The catalog entry name will be `my-plugin:my-skill` and will carry `"source": "plugin-override"`.

### 9.8 Tombstone

A plugin skill that is permanently broken. Create a tombstone to remove it from the catalog:

`triggers/my-plugin/broken-skill.yml`:

```yaml
disabled: true
reason: "permanently broken — use local skills/replacement instead."
```

Generator log when applied:

```
2026-05-09T00:00:00Z info  my-plugin:broken-skill  plugin entry disabled by override (reason: permanently broken — use local skills/replacement instead.)
```

If the plugin is later uninstalled before the tombstone is removed:

```
2026-05-09T00:00:00Z warning  my-plugin:broken-skill  disable override targets nonexistent entry 'my-plugin:broken-skill'
```

No action required — the warning is advisory. Remove the tombstone file if the entry is gone permanently.

### 9.9 Validation failure cases

**Fatal (entry excluded) — malformed keyword:**

`skills/broken-skill/triggers.yml`:

```yaml
triggers:
  keywords:
    - "just-a-string-not-an-object"
```

```
2026-04-30T14:22:02Z fatal broken-skill keywords[0] is not a {term, weight} mapping — entry excluded
```

**Fatal (entry excluded) — weight outside `[0.0, 1.0]`:**

```yaml
triggers:
  keywords:
    - { term: "foo", weight: -0.5 }
```

```
2026-04-30T14:22:02Z fatal bad-weight-skill keywords[0].weight -0.5 is outside [0.0, 1.0] — entry excluded
```

**Warning (entry kept) — off-ladder weight:**

```
2026-04-30T14:22:03Z warning noisy-skill keywords[0].weight 0.75 not in {0.25, 0.5, 1.0} — clamped to 1.0
```

**Warning (entry kept) — duplicate term:**

```
2026-04-30T14:22:03Z warning noisy-skill keywords duplicate term 'foo' — deduplicated (last wins, weight 0.5)
```

**Warning (entry kept) — triggers declared but no applicable target:**

```
2026-04-30T14:22:03Z warning noisy-skill triggers declared but applicable_agents is empty — entry will never match
```

### 9.10 `keyword_groups` example — doc-writer

```yaml
# Adds a verb-noun conjunctive trigger to doc-writer.
# Existing singletons (docs, readme, spec, update, edit) preserved for
# weak attachment signal when no verb is present.
triggers:
  keywords:
    - {term: docs,   weight: 1.0}
    - {term: readme, weight: 1.0}
    - {term: spec,   weight: 1.0}
    - {term: update, weight: 0.25}
    - {term: edit,   weight: 0.25}
  keyword_groups:
    - slots:
        - {name: verbs, terms: [update, edit, modify, change]}
        - {name: nouns, terms: [docs, readme, spec]}
      weight: 1.0
```

Behavior on representative prompts:

| Prompt | doc-writer score | Reason |
| --- | --- | --- |
| `update the docs` | 1.00 | Group fires; both singletons suppressed (replacement rule) |
| `edit the readme` | 1.00 | Group fires |
| `the docs are great` | 0.50 | Group does NOT fire (no verb); singleton `docs@1.0` contributes 0.5 |
| `modify the spec document` | 1.00 | Group fires |

---

## 10. Authoring guide

### Adding triggers to an owned skill

1. Create `skills/<name>/triggers.yml`.
2. **Inventory the prompts that should match.** Read 5–10 prompts from your session history that called for this skill. Write down the tokens that appear in most of them.
3. **Pick the defining term.** One or two terms whose absence means the skill should not match. These are weight `1.0`.
4. **List supporting terms.** Strongly correlated terms get `0.5`. Loosely correlated terms get `0.25`.
   - **Keywords MUST be single tokens — no whitespace.** The validator drops any keyword whose term contains whitespace and emits a warning.
5. **Use `path_globs` — `file_extensions` is removed.** Use `path_globs` exclusively (e.g. `"**/*.py"` instead of `["py"]` in `file_extensions`).
   - **Remember the fnmatch footgun:** for root-level files, include both `"**/*.ext"` AND `"*.ext"` forms (see §4).
6. **Add `excludes` for known overlaps.** If your skill shares vocabulary with another, exclude the competing terms. Remember: `excludes` matches `features.keywords` only.
7. **Pick `applicable_agents`.** Default to the smallest set of sub-agents that have the tools your skill needs. Use `["*"]` only when truly tool-agnostic.
8. **Run the generator locally** and check the log for warnings:
   ```bash
   python -m src.claude_wayfinder.build_catalog
   ```
9. **Verify the entry** in the output catalog JSON.
10. **Open a PR.** The catalog stability CI invariant verifies determinism.

### Adding triggers for a plugin skill (override)

1. Create `triggers/<plugin>/<skill>.yml` (create the plugin subdirectory if needed).
2. Follow steps 2–8 above — same schema, same rules.
3. The entry name in the catalog will be `<plugin>:<skill>` and carry `"source": "plugin-override"`.

### Opting a plugin agent into routing

Plugin agents land dormant and are excluded from the scoring pool. To activate one:

**Preferred (Issue #140 — agent sidecar subdirectory):**

1. Create `triggers/<plugin>/agents/<agent>.yml` with a non-empty `triggers:` block and `applicable_skills:`.
   The sidecar must target an installed plugin agent — ghost sidecars (no matching dormant entry) are warned and dropped.
2. The catalog builder (Pass 3b) replaces the dormant plugin entry in place, setting `source="plugin-override"` and `routable: true`.
   `is_agent_routable` returns `True` for the resulting entry.

The agent sidecar schema (§4 of the spec) does **not** include `kind`, `name`, `description`, or `source` — those are inherited from the matched plugin entry. Include only `triggers:` and `applicable_skills:`.

**Legacy path (still supported):**

1. Create `triggers/<plugin>/<agent>.yml` with `kind: agent` and a non-empty `triggers:` block.
2. The override is processed by Pass 3 (skill override path); if no matching dormant entry exists, a new entry is appended.
   `is_agent_routable` will return `True` for the resulting `source="plugin-override"` entry.

Prefer the `agents/` subdirectory form for new overrides — it enforces match-required semantics and provides structural disambiguation from skill overrides.

### Retiring a broken plugin skill (tombstone)

1. Create `triggers/<plugin>/<skill>.yml` with `disabled: true` and an optional `reason:`.
2. Confirm the tombstone is applied by checking the catalog log for the `info` line.
3. Do not delete the tombstone while the plugin is installed — without it the dormant entry reappears on the next catalog build.

### Anti-patterns

- **Over-broad keywords.** `code` on a code-writing skill matches every prompt. Prefer `implement`, `feature`, `function`, `class`.
- **Multi-word keywords.** `type hints` contains a space. The validator drops the keyword and warns. Split into single-token synonyms or omit the weaker one.
- **Using `file_extensions`.** This field is removed. The generator drops it with a warning. Use `path_globs` instead.
- **Off-ladder weights.** Values like `0.75` or `0.4` will be silently clamped. Use only `0.25`, `0.5`, or `1.0` — see the weight clamp footgun in §4.
- **Forgetting both glob forms.** For root-level files, `"**/*.ext"` alone may not match due to `fnmatch` semantics. Always include the bare `"*.ext"` form as well — see the fnmatch footgun in §4.
- **Synonym overload.** Listing every variant of a term bloats the catalog. Pick the noun form.
- **Forgetting `applicable_*`.** A skill with a perfect trigger set but `applicable_agents: []` matches in scoring but is filtered out at decision time — invisible failure.
- **Trigger keys in `SKILL.md`.** The inline-frontmatter pattern is deprecated. The generator will warn and ignore them. Move all trigger config to `triggers.yml`.

---

## 11. Forward work pointers

- **Per-entry migration:** each unmigrated skill becomes a small "add `triggers.yml`" PR.
- **Plugin discovery:** reads the installed-plugins manifest to discover dormant plugin entries. See §2e.
- **Plugin override surface:** create `triggers/<plugin>/<skill>.yml` to activate or tombstone a plugin skill. See §2b and §2f.
- **Builtin-agent catalog:** operator sidecars under `triggers/builtin/` let you route to runtime-embedded agents. See §2h.
- **EXCLUDE_DEAD_ZONE validation:** runs against a captured routing corpus when one is available. See §7.
- **Expanded keyword matching modes.** Evaluate stemming/substring after corpus data accumulates.
- **Schema versioning.** Add `schema_version: 1` to sidecar files when a breaking schema change ships.
- **Agent sidecar support.** Shipped in Issue #148. Agents may use a colocated `<name>.triggers.yml` sidecar next to their `.md` file; see §2c for the full reference.
