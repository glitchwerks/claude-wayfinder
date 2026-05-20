# Dispatch Catalog Schema — claude-wayfinder

This document is the stability contract for the catalog schema, dispatch context input, and decision output used by `claude-wayfinder`. It is the authoritative reference for consumers integrating the matcher into a router agent.

**Related documents:**
- [Integration guide](integration.md) — how to build the catalog, configure the dispatch loop, and handle decisions
- [Trigger schema](design/trigger-schema.md) — field-level reference for what goes inside a catalog entry's `triggers:` block (path glob footguns, keyword weight ladder, scoring formula)

---

## Schema version

The catalog JSON file carries a top-level `schema_version` integer field. The current value is **`1`** (set in `src/claude_wayfinder/build_catalog.py`).

The trigger format used to populate those entries is **v6 sidecar** — skills store trigger configuration in a `triggers.yml` file next to `SKILL.md`, and agents use either inline frontmatter or a colocated `<name>.triggers.yml` sidecar. When both are present the sidecar takes precedence. This supersedes v5, where skills used inline frontmatter as well. See [docs/design/trigger-schema.md](design/trigger-schema.md) for the full sidecar format reference.

### Stability

The four sections below — catalog entry schema, dispatch context schema, decision output schema, and catalog-level metadata — are **stable for v0.2 consumers**. The project will not remove or rename fields in this document without a major version bump accompanied by a migration note in `CHANGELOG.md`.

Fields marked **advisory** below are present in the current output but their exact values (e.g. rationale strings) may change across minor releases without a version bump. Consumers should read them for display and logging, not for branching logic.

Breaking changes are defined as: removing a field, renaming a field, or changing its JSON type. New optional fields are non-breaking. If a breaking change is required, the top-level `schema_version` integer will increment and `CHANGELOG.md` will include a migration guide.

---

## 1. Catalog entry schema

Each object in the `entries` array of `dispatch-catalog.json` represents one agent or skill.

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `name` | `string` | yes | — | Unique entry name. E.g. `"code-writer"`, `"python"`, `"superpowers:brainstorming"`. |
| `kind` | `string` | yes | — | `"agent"` or `"skill"`. |
| `description` | `string` | yes | `""` | Human-readable description. Sourced from frontmatter `description:` field. May be empty. |
| `source` | `string` | yes | `"owned"` | Provenance tag. See source values table below. |
| `triggers` | `object` | yes | empty trigger object | Trigger configuration. See [trigger-schema.md](design/trigger-schema.md) for the full field reference. |
| `applicable_agents` | `array[string]` | skills only | `[]` | Hard allowlist of agent names that may receive this skill. `["*"]` = any agent. `[]` = no agent (skill is router-only or dormant). Present on skill entries; absent on agent entries. |
| `applicable_skills` | `array[string]` | agents only | `[]` | Hard allowlist of skill names to attach when routing to this agent. `["*"]` = any. `[]` = no skills. Present on agent entries; absent on skill entries. |
| `routable` | `boolean` | agents only; optional | `true` | When `false`, the entry is excluded from the scored-agent pool at dispatch time. Set to `false` on the router agent itself so it is never selected as a delegation target. Absent on skill entries. |

### `source` field values

| Value | Meaning |
|---|---|
| `"owned"` | Scanned from the user's own `skills/` or `agents/` directory tree. The default for first-party content. Trigger configuration may come from inline frontmatter in the agent `.md` or from a colocated `<name>.triggers.yml` sidecar next to it; the sidecar takes precedence when both are present. |
| `"plugin"` | Discovered from an installed plugin. Entries land dormant (zero triggers) and never drive a routing decision unless activated by a plugin-override sidecar. Plugin agents with `source="plugin"` are additionally excluded from the agent scoring pool by `is_agent_routable`. |
| `"plugin-override"` | Loaded from a `triggers/<plugin>/<skill>.yml` override file (skills) or a `triggers/<plugin>/agents/<name>.yml` override file (agents), disambiguated by the `kind` field. For skills, replaces the matching `source="plugin"` entry or adds a new entry when no plugin-discovered entry exists. For agents, replaces the matching dormant `source="plugin"` agent entry only — no new entry is created when no match is found (strict Mode 2a: unmatched sidecars emit a warning and are dropped). `is_agent_routable` treats this source as routable. |
| `"builtin"` | Loaded from a `triggers/builtin/<Agent>.yml` operator sidecar. Represents runtime-embedded agents (e.g. `Explore`, `Plan`). Routable by default. Requires `min_claude_version` in the sidecar; entries are excluded if the running version is outside `[min, max]`. |
| `"project"` | Scanned from `<repo>/.claude/skills/` or `<repo>/.claude/agents/` when the generator runs inside a git repository. Project entries override user-global entries on name collision and carry the highest precedence in the source-tagged model. As with `"owned"`, trigger configuration may come from inline frontmatter or a colocated `<name>.triggers.yml` sidecar next to the agent `.md`; the sidecar takes precedence when both are present. |

### `triggers` object structure

The `triggers` field is an object whose sub-fields are all optional and default to empty lists. For full semantics (scoring formula, weight ladder, fnmatch footguns, `excludes` behavior) see [docs/design/trigger-schema.md §2d and §4](design/trigger-schema.md).

| Sub-field | Type | Notes |
|---|---|---|
| `command_prefixes` | `array[string]` | Slash commands that short-circuit to score `1.0`. |
| `agent_mentions` | `array[string]` | Agent names whose explicit mention short-circuits to score `1.0`. |
| `path_globs` | `array[string]` | `fnmatch`-style globs matched against `file_paths`. Each matched glob contributes `0.4` to the score. |
| `keywords` | `array[{term: string, weight: number}]` | Weighted keyword terms. Valid weights: `0.25`, `0.5`, `1.0`. Each matched term contributes `0.5 × weight`. |
| `keyword_groups` | `array[{slots: array[{name: string, terms: array[string]}], weight: number}]` | **AND-group conjunctive triggers** (added v0.6.0 per #135). A group fires only when all of its slots match — each slot must have ≥ 1 of its `terms` present in the input keywords. On match the group contributes `0.5 × weight` (same multiplier as flat `keywords`). Requires ≥ 2 slots per group. See [trigger-schema.md § 2i](design/trigger-schema.md). |
| `tool_mentions` | `array[string]` | Tool names. Each match contributes `0.5` to the score. |
| `excludes` | `array[string]` | Terms that hard-zero the entry's score when present in the task keywords. Matches `features.keywords` only. |

---

## 2. Dispatch context schema

The dispatcher reads a JSON object from stdin. This is the input shape the router agent must compose before invoking `/dispatch`.

| Field | Type | Required | Notes |
|---|---|---|---|
| `task_description` | `string` | yes | The task the user wants performed, expressed as a task sentence. Tokenized into keywords for matching. |
| `file_paths` | `array[string]` | no | File or directory paths mentioned or implied by the current turn. Used for path-glob scoring. Defaults to `[]` when absent. |
| `agent_mentions` | `array[string]` | no | Agent names the user explicitly named. Matched against `triggers.agent_mentions`. Defaults to `[]` when absent. |
| `tool_mentions` | `array[string]` | no | Tool names the user explicitly named (e.g. `"Bash"`, `"Grep"`). Matched against `triggers.tool_mentions`. Defaults to `[]` when absent. |
| `command_prefix` | `string\|null` | no | The slash command the user typed, if any. E.g. `"/refactor"`. `null` or absent when no slash command was used. |

**Minimum viable context:** the matcher requires at least 2 populated input dimensions before attempting to route. If fewer than 2 dimensions are populated, the matcher returns `needs_more_detail` regardless of catalog content. A `task_description` with at least one keyword counts as one dimension; each of `file_paths`, `agent_mentions`, `tool_mentions`, and a non-null `command_prefix` each count as one additional dimension when non-empty.

Example:

```json
{
  "task_description": "fix auth token expiry bug in src/auth/token.py",
  "file_paths": ["src/auth/token.py"],
  "agent_mentions": [],
  "tool_mentions": [],
  "command_prefix": null
}
```

---

## 3. Decision output schema

The matcher writes a JSON object to stdout. The `decision` field is always present. Other fields are conditional on the decision type.

### Common fields (all decision types)

| Field | Type | Always present | Notes |
|---|---|---|---|
| `decision` | `string` | yes | One of the 7 decision types listed below. |
| `confidence` | `number` | yes | Float in `[0.0, 1.0]`. The score of the top-matched entry, rounded to 6 decimal places. `0.0` for `needs_more_detail` and `self_handle_unaided`. |
| `rationale` | `string` | yes | Human-readable explanation of the decision. **Advisory** — contents may change across minor releases. Do not branch on rationale text. |
| `alternatives` | `array` | yes | Top alternatives considered. Empty array when not applicable. Each element is `{"agent": string, "score": number}`. |

### Fields by decision type

| Field | Type | Present on |
|---|---|---|
| `agent` | `string` | `delegate`, `advisory` |
| `skills` | `array[string]` | `delegate`, `self_handle`, `advisory` |

`skills` is an ordered list of skill names (up to 3) that scored above threshold and are applicable to the winning agent. Empty list when no skills qualified.

### Decision types

The seven decision types, in evaluation order:

#### `needs_more_detail`

Feature density was below the minimum threshold (fewer than 2 populated input dimensions). The matcher did not attempt scoring.

```json
{
  "decision": "needs_more_detail",
  "confidence": 0.0,
  "rationale": "Feature density below threshold: provide more context ...",
  "alternatives": []
}
```

**Handler guidance:** do not retry with the same context. Recompose `task_description` with explicit signals — name the verb, the target files, and any constraint. Add `file_paths` and `agent_mentions` if the user provided hints. Retry `/dispatch` once with richer context. If the retry also returns `needs_more_detail`, ask the user to clarify.

#### `delegate`

One agent scored >= 0.85 with a gap of >= 0.2 over the second-place agent. High-confidence single winner.

```json
{
  "decision": "delegate",
  "agent": "code-writer",
  "skills": ["python"],
  "confidence": 0.92,
  "rationale": "matched keywords: implement.",
  "alternatives": [{"agent": "debugger", "score": 0.41}]
}
```

**Handler guidance:** compose an Agent tool call for the named `agent`. If `skills` is non-empty, propagate those skill names into the sub-agent's prompt.

#### `ambiguous`

The top agent scored >= 0.5 but the gap between it and the second-place agent was < 0.2. Two or more agents tied.

```json
{
  "decision": "ambiguous",
  "confidence": 0.71,
  "rationale": "Multiple agents score similarly (gap=0.05); user input needed to disambiguate.",
  "alternatives": [{"agent": "code-writer", "score": 0.71}, {"agent": "debugger", "score": 0.66}]
}
```

**Handler guidance:** present the candidates from `alternatives` to the user and ask them to choose. Do not pick one unilaterally.

#### `self_handle`

No dominant agent, but at least one skill scored >= 0.5.

```json
{
  "decision": "self_handle",
  "skills": ["python", "github-actions"],
  "confidence": 0.75,
  "rationale": "No dominant agent; routing to self with skills: python, github-actions",
  "alternatives": []
}
```

**Handler guidance:** invoke the returned skills via the Skill tool and proceed without delegating to a sub-agent.

#### `advisory`

An agent scored >= 0.5 but below the `delegate` threshold (gap was >= 0.2, ruling out `ambiguous`). Delegation is suggested but not certain.

```json
{
  "decision": "advisory",
  "agent": "devops",
  "skills": [],
  "confidence": 0.61,
  "rationale": "Best agent 'devops' scores 0.61 but match is not conclusive.",
  "alternatives": [{"agent": "code-writer", "score": 0.30}]
}
```

**Handler guidance:** use the suggested agent, noting the uncertainty in your audit line. Overriding an advisory decision without a stated reason is logged as drift.

#### `ask_user`

**Reserved. Not produced by the v0.1 or v0.2 matcher.**

This decision type is defined in `VALID_DECISIONS` but the matcher's decision ladder does not emit it. It is reserved for a future mode where the matcher explicitly requests human input before proceeding — distinct from `ambiguous` (which signals "two strong candidates") and `needs_more_detail` (which signals "too little context").

**Handler guidance:** include a handler for forward compatibility. If your router receives `ask_user`, pause and ask the user to clarify before taking any action. Do not treat it as an error.

#### `self_handle_unaided`

No agent or skill scored above threshold. The matcher found no useful signal.

```json
{
  "decision": "self_handle_unaided",
  "confidence": 0.0,
  "rationale": "No agent or skill scored above threshold; proceeding without delegation or skill activation.",
  "alternatives": []
}
```

**Handler guidance:** handle the task directly without delegation or skill activation.

---

## 4. Scoring and decision algorithm

This section documents the algorithm `match.py` uses to convert catalog entries and dispatch context into a routing decision. It is the normative spec; the pseudocode is an exact transliteration of the implementation.

### Per-entry scoring

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
    s += sum(0.3 * k.weight for k in entry.triggers.keywords if k.term in features.keywords)
    s += 0.5 * len([t for t in entry.triggers.tool_mentions if t in features.tool_mentions])
    return min(s, 1.0)
```

Short-circuits fire before additive scoring. `command_prefixes` and `agent_mentions` short-circuit to `1.0`; `excludes` short-circuits to `0.0`. All three match against `features.keywords` only — `excludes` does not check `tool_mentions` or `agent_mentions`.

Coefficient summary: path glob match = `0.4` per glob; keyword match = `0.3 × weight` per term; tool mention match = `0.5` per tool. Score is clamped to `1.0`.

### Decision composition

After scoring all entries, the matcher selects a decision:

```python
def decide(scored_agents, scored_skills, features):
    if feature_count(features) < 2:
        return {"decision": "needs_more_detail", ...}

    best_agent = scored_agents[0] if scored_agents else None
    best_skills = [s for s in scored_skills if s.score >= 0.5][:3]

    if best_agent and best_agent.score >= 0.85 and gap(scored_agents) >= 0.2:
        return {"decision": "delegate", "agent": best_agent.name,
                "skills": skills_for_agent(best_agent, features), ...}

    if best_agent and best_agent.score >= 0.5 and gap(scored_agents) < 0.2:
        return {"decision": "ambiguous", "candidates": top_3_agents, ...}

    if best_skills:
        return {"decision": "self_handle", "skills": [s.name for s in best_skills], ...}

    if best_agent and best_agent.score >= 0.5:
        return {"decision": "advisory", "agent": best_agent.name,
                "skills": skills_for_agent(best_agent, features), ...}

    return {"decision": "self_handle_unaided", ...}
```

The router agent is excluded from the scored-agents pool via the `routable: false` flag. The `gap` function is the score difference between the top and second-place agent. `feature_count` counts populated input dimensions: `task_description` with at least one keyword = 1; each of `file_paths`, `agent_mentions`, `tool_mentions`, and a non-null `command_prefix` each add 1 when non-empty.

### Decision ladder

| Decision              | Condition                                                   | Confidence   |
| --------------------- | ----------------------------------------------------------- | ------------ |
| `needs_more_detail`   | Feature density < 2 populated dimensions                    | `0.0`        |
| `delegate`            | Best agent score ≥ 0.85, gap ≥ 0.2                          | best score   |
| `ambiguous`           | Best agent score ≥ 0.5, gap < 0.2                           | best score   |
| `self_handle`         | No dominant agent; ≥1 skill score ≥ 0.5                     | best score   |
| `advisory`            | Best agent score ≥ 0.5, gap ≥ 0.2 (below delegate threshold) | best score  |
| `self_handle_unaided` | No agent or skill above threshold                           | `0.0`        |

`ask_user` is reserved and not produced by the current decision ladder.

---

## 5. Observability

The matcher's observability layer tracks routing decisions against actual tool-use behavior. This section summarizes the telemetry shape; the full drift design rationale is in [`docs/design.md`](design.md).

### Drift event common fields

Every event written to `router-drift.jsonl` carries these top-level fields:

| Field           | Type     | Notes                                                                    |
| --------------- | -------- | ------------------------------------------------------------------------ |
| `type`          | `string` | Event type name (see table below).                                       |
| `ts`            | `string` | ISO 8601 timestamp of when the event was emitted.                        |
| `session_id`    | `string` | Claude Code session UUID.                                                |
| `plugin_version`| `string` | Plugin version string (e.g. `"claude-wayfinder@0.3.0"`) or `"unknown"`. |

> **Breaking change (pre-1.0):** The `plugin_version` field was named `harness_version` prior to
> this version. External consumers of `router-drift.jsonl` must update field references. See
> `CHANGELOG.md` for the version that introduced this rename.

### Drift event types and action thresholds

Drift events are written to `router-drift.jsonl` by a Stop hook and a PreToolUse floor hook.

| Event type                       | Producer            | Action threshold                                              |
| -------------------------------- | ------------------- | ------------------------------------------------------------- |
| `bypass`                         | PreToolUse hook     | ≥ 5 events with same agent type in 7 days                     |
| `stale_dispatch`                 | PreToolUse hook     | ≥ 3 events in 7 days (advisory-only until STALENESS_BOUND calibrated) |
| `advisory_override`              | Stop hook scanner   | ≥ 3 events with same router-vs-catalog choice in 7 days       |
| `self_handle_unaided_invocation` | Stop hook scanner   | ≥ 10 events in 7 days                                         |
| `needs_more_detail_repeat`       | Stop hook scanner   | ≥ 3 events in 7 days                                          |
| `catalog_degraded_session`       | Stop hook scanner   | ≥ 1 ever — immediate action                                   |

### Pre-ship CI invariants

These are verified at catalog generation time and on PRs that touch skill or agent frontmatter:

| Invariant                    | Pass condition                                                                    |
| ---------------------------- | --------------------------------------------------------------------------------- |
| Catalog stability            | Generator run twice on unchanged source; output identical byte-for-byte           |
| Trigger-rule firing accuracy | Per-entry smoke-test inputs produce expected matches                              |
| Schema validation            | Generator exits 0 with no per-entry fatal warnings on touched frontmatter         |

### Runtime telemetry (healthy ranges — starting hypothesis)

Computed by `src/claude_wayfinder/_health.py` from the drift log and dispatch log:

| Metric                   | Healthy range |
| ------------------------ | ------------- |
| Dispatch invocation rate | ≥ 80%         |
| Bypass rate              | ≤ 10%         |
| Advisory override rate   | ≤ 30%         |
| Catalog availability     | ≥ 99%         |

Drift trends are signal the operator interprets — the design does not claim that drift-going-down equals improved outcome quality. See [`docs/design.md`](design.md) for the design philosophy around this distinction.

---

## 6. Catalog-level metadata

The top-level `dispatch-catalog.json` object has these fields:

| Field | Type | Notes |
|---|---|---|
| `schema_version` | `integer` | Currently `1`. Incremented on breaking schema changes. |
| `router_agent` | `string\|null` | Name of the first catalog entry (sorted by `(kind, name)`) with `routable: false`. Informational — the per-entry `routable` flag is the actual exclusion gate at dispatch time. `null` when no entry declares `routable: false`. |
| `built_for_project` | `string\|null` | Resolved path of the git repository root when a project-local scan was performed; `null` when only the user-global tree was scanned. Used by the refresh hook to detect project switches. |
| `entries` | `array[object]` | The catalog entries. Sorted by `(kind, name)`. Each object matches the catalog entry schema in section 1. |

---

## 7. Minimal example catalog

The following illustrates the full catalog structure with five representative entries. Fields use their actual JSON types (booleans lowercase, numbers unquoted).

```json
{
  "schema_version": 1,
  "router_agent": "general-purpose",
  "built_for_project": "/home/user/myrepo",
  "entries": [
    {
      "name": "general-purpose",
      "kind": "agent",
      "description": "The dispatch router. Never a delegation target.",
      "source": "owned",
      "routable": false,
      "triggers": {
        "agent_mentions": [],
        "command_prefixes": [],
        "excludes": [],
        "keywords": [{"term": "route", "weight": 1.0}],
        "path_globs": [],
        "tool_mentions": []
      },
      "applicable_skills": ["*"]
    },
    {
      "name": "code-writer",
      "kind": "agent",
      "description": "Writes and edits code.",
      "source": "owned",
      "routable": true,
      "triggers": {
        "agent_mentions": ["code-writer"],
        "command_prefixes": [],
        "excludes": [],
        "keywords": [
          {"term": "implement", "weight": 1.0},
          {"term": "refactor", "weight": 0.5},
          {"term": "fix", "weight": 0.5}
        ],
        "path_globs": [],
        "tool_mentions": []
      },
      "applicable_skills": ["python", "github-actions"]
    },
    {
      "name": "python",
      "kind": "skill",
      "description": "Expert Python code writing.",
      "source": "owned",
      "triggers": {
        "agent_mentions": [],
        "command_prefixes": [],
        "excludes": [],
        "keywords": [
          {"term": "python", "weight": 1.0},
          {"term": "pytest", "weight": 0.5}
        ],
        "path_globs": ["**/*.py", "*.py"],
        "tool_mentions": []
      },
      "applicable_agents": ["code-writer", "debugger"]
    },
    {
      "name": "superpowers:brainstorming",
      "kind": "skill",
      "description": "Structured brainstorming from the superpowers plugin.",
      "source": "plugin-override",
      "triggers": {
        "agent_mentions": [],
        "command_prefixes": ["/brainstorm"],
        "excludes": [],
        "keywords": [
          {"term": "brainstorm", "weight": 1.0},
          {"term": "ideate", "weight": 0.5}
        ],
        "path_globs": [],
        "tool_mentions": []
      },
      "applicable_agents": ["*"]
    },
    {
      "name": "debugger",
      "kind": "agent",
      "description": "Diagnoses bugs and test failures.",
      "source": "project",
      "routable": true,
      "triggers": {
        "agent_mentions": ["debugger"],
        "command_prefixes": [],
        "excludes": [],
        "keywords": [
          {"term": "debug", "weight": 1.0},
          {"term": "error", "weight": 0.5},
          {"term": "traceback", "weight": 0.5}
        ],
        "path_globs": [],
        "tool_mentions": []
      },
      "applicable_skills": ["python"]
    }
  ]
}
```

Key points illustrated by this example:

- `general-purpose` has `routable: false` — it is the router itself and must never be selected as a delegation target. The top-level `router_agent` field names it.
- `code-writer` has `routable: true` (the default) — it participates normally in agent scoring.
- `python` is a skill; it has `applicable_agents` (not `applicable_skills`) and no `routable` field.
- `superpowers:brainstorming` has `source: "plugin-override"` with a command-prefix trigger and `applicable_agents: ["*"]`.
- `debugger` has `source: "project"` — it was scanned from the repo's `.claude/agents/` directory and overrides any user-global entry with the same name.
