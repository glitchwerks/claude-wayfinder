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
| `applicable_agents_intentional` | `string` | skills only; optional | absent | Rationale string that suppresses the `empty-applicable-agents` audit NIT when `applicable_agents` is intentionally `[]`. Must be a non-empty string; an empty string does not suppress the NIT. See `docs/dispatch-authoring-guide.md:327–332` for a worked example. |
| `applicable_skills` | `array[string]` | agents only | `[]` | Hard allowlist of skill names to attach when routing to this agent. `["*"]` = any. `[]` = no skills. Present on agent entries; absent on skill entries. |
| `routable` | `boolean` | agents only; optional | `true` | When `false`, the entry is excluded from the scored-agent pool at dispatch time. Set to `false` on the router agent itself so it is never selected as a delegation target. Absent on skill entries. |

### `source` field values

<!-- D1/D4 extracted from #148 owned-project-agent-sidecars spec (audit #216 → fix #221): sidecar is a delivery mechanism, not an authorship claim; source encodes who authored the agent, unchanged by sidecar adoption -->
<!-- D1/D2/D7 extracted from #140 plugin-agent-sidecar-overrides spec (audit #216 → fix #221): agent sidecar path is triggers/<plugin>/agents/<name>.yml, disambiguated by kind; same precedence slot as skill plugin-override -->
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
| `keyword_groups` | `array[{slots: array[{name: string, terms: array[string]}], weight: number}]` | **AND-group conjunctive triggers** (added v0.6.0 per #135). A group fires only when all of its slots match — each slot must have ≥ 1 of its `terms` present in the input keywords. On match the group contributes `1.0 × weight` (via `_GROUP_MULTIPLIER`, deliberately distinct from the `0.5` `_KEYWORD_MULTIPLIER` so a satisfied weight-1.0 group can solo-decide `delegate`). Requires ≥ 2 slots per group. See [trigger-schema.md § 2i](design/trigger-schema.md). |
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

**`extensions` — internally derived dimension.** When `file_paths` is non-empty, the matcher also derives an `extensions` dimension by extracting the file-extension suffix of each path (e.g. `"src/foo.py"` → `"py"`). This dimension is counted separately by `feature_count` — so a context with only `task_description` and `file_paths` yields `feature_count = 3` (keywords + paths + extensions), not 2. The `extensions` dimension is not a caller-supplied field; it is computed internally from `file_paths` and is transparent to callers composing context objects. It affects whether the density floor is cleared and contributes to per-entry scoring via the scoring formula in §4.

For a conceptual explanation with good/sparse examples, see the [What is a dispatch context?](../README.md#what-is-a-dispatch-context) section in the README.

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
| `disposition_source` | `string` | yes | Enum: `"scored"` or `"override"`. Indicates whether the decision came from the normal scoring pipeline or from a matched override rule. Always present on every return (shipped v0.11.0 — downstream tooling may rely on this field always being present). See `docs/dispatch-overrides.md` for override semantics. |

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

#### `mixed_content`

Two or more agents each scored at or near 1.0 (within the `_MIXED_CONTENT_SCORE_EPSILON` tolerance of 0.05) on path-disjoint lanes — every qualifying agent has at least one path-glob match, and no single input path is claimed by more than one agent. This fires after `self_handle` but before `advisory`, and only when the gap between the top two agents is < 0.2 (the advisory pre-condition). It represents a structurally split task where different parts of the input clearly belong to different specialists.

```json
{
  "decision": "mixed_content",
  "confidence": 1.0,
  "rationale": "2 agents clamped at 1.0 on path-disjoint lanes; structural mixed-content task.",
  "lanes": [
    {"agent": "code-writer", "score": 1.0, "matched_paths": ["src/auth.py"], "skills": ["python"]},
    {"agent": "doc-writer",  "score": 1.0, "matched_paths": ["docs/auth.md"], "skills": []}
  ],
  "unassigned_paths": [],
  "alternatives": []
}
```

**Additional output fields (present only on `mixed_content`):**

| Field | Type | Notes |
|---|---|---|
| `lanes` | `array[object]` | One entry per qualifying agent. Each lane object has `agent` (string), `score` (number), `matched_paths` (array of strings from the input `file_paths` that matched this agent's path globs), and `skills` (array of skill names applicable to this agent). |
| `unassigned_paths` | `array[string]` | Input paths not claimed by any qualifying lane. May be empty. |

**Handler guidance:** dispatch each lane to its named agent independently. Pass `matched_paths` and `skills` from the lane into the sub-agent's context. Any `unassigned_paths` were not claimed by a specialist — handle them in the router or surface them to the user.

#### `advisory`

An agent scored >= 0.5 but below the `delegate` threshold. This covers both gap-tied / close-scoring cases (gap < 0.2) and cases where the gap is sufficient but confidence is below the delegate floor. Delegation is suggested but not certain.

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

This decision type is defined in `VALID_DECISIONS` but the matcher's decision ladder does not emit it. It is reserved for a future mode where the matcher explicitly requests human input before proceeding — distinct from `advisory` (which signals a gap-tied or below-threshold match) and `needs_more_detail` (which signals "too little context").

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
    s += sum(0.5 * k.weight for k in entry.triggers.keywords if k.term in features.keywords)
    s += 0.5 * len([t for t in entry.triggers.tool_mentions if t in features.tool_mentions])
    return min(s, 1.0)
```

Short-circuits fire before additive scoring. `command_prefixes` and `agent_mentions` short-circuit to `1.0`; `excludes` short-circuits to `0.0`. All three match against `features.keywords` only — `excludes` does not check `tool_mentions` or `agent_mentions`.

Coefficient summary: path glob match = `0.4` per glob; keyword match = `0.5 × weight` per term; tool mention match = `0.5` per tool. Score is clamped to `1.0`.

Satisfied keyword **groups** (`triggers.keyword_groups`, AND-conjunction) contribute `1.0 × weight` per group — a distinct multiplier from the per-keyword `0.5` (see `src/claude_wayfinder/match/_match.py:L38,L46`).

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

    if best_skills:
        return {"decision": "self_handle", "skills": [s.name for s in best_skills], ...}

    # Step 3.5: mixed_content — fires when gap < 0.2 and >= 2 agents clamp
    # at 1.0 on path-disjoint lanes (inserted between self_handle and advisory).
    if best_agent and gap(scored_agents) < 0.2:
        mixed = detect_mixed_content(scored_agents, scored_skills, features)
        if mixed is not None:
            return mixed  # {"decision": "mixed_content", "lanes": [...], "unassigned_paths": [...], ...}

    if best_agent and best_agent.score >= 0.5:
        return {"decision": "advisory", "agent": best_agent.name,
                "skills": skills_for_agent(best_agent, features), ...}

    return {"decision": "self_handle_unaided", ...}
```

The router agent is excluded from the scored-agents pool via the `routable: false` flag. The `gap` function is the score difference between the top and second-place agent. `feature_count` counts populated input dimensions: `task_description` with at least one keyword = 1; each of `file_paths`, `agent_mentions`, `tool_mentions`, and a non-null `command_prefix` each add 1 when non-empty. See also: `extensions` is an internally-derived dimension counted by `feature_count` — refer to the §2 dispatch context note for details.

### Decision ladder

| Decision              | Condition                                                                                                                    | Confidence   |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------- | ------------ |
| `needs_more_detail`   | Feature density < 2 populated dimensions                                                                                     | `0.0`        |
| `delegate`            | Best agent score ≥ 0.85, gap ≥ 0.2                                                                                           | best score   |
| `self_handle`         | No dominant agent; ≥1 skill score ≥ 0.5                                                                                      | best score   |
| `mixed_content`       | Gap < 0.2; ≥ 2 agents clamped at 1.0 on path-disjoint lanes (fires after `self_handle`, before `advisory`)                  | best score   |
| `advisory`            | Best agent score ≥ 0.5 (gap-tied or below delegate threshold); `mixed_content` conditions not met                           | best score   |
| `self_handle_unaided` | No agent or skill above threshold                                                                                            | `0.0`        |

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

---

## 8. Platform agents (Explore and Plan)

Claude Code ships two built-in agents, `Explore` and `Plan`, that can be
invoked by the router as `delegate` targets alongside user-authored agents.
Unlike owned or project agents, they have no backing `.md` file in `agents/`
— their trigger configuration is provided by sidecar files. See §2h for the
`source="builtin"` source value and the `min_claude_version` / `max_claude_version`
version-pinning requirements.

### In-package fixtures (Issue #286)

`claude_wayfinder` ships `Explore.yml` and `Plan.yml` as **in-package fixtures**
at `claude_wayfinder/fixtures/builtin/`. These files are the default builtin-
agent source on a fresh install, so platform agents appear in the catalog
automatically without any operator configuration.

The resolver follows a **three-level cascade** (implemented in
`build_catalog._discover._resolve_catalog_build_defaults`):

1. **Explicit `--builtin-agents-dir` argument** — always wins.
2. **User directory `~/.claude/triggers/builtin/`** (or `$CLAUDE_HOME/triggers/builtin/`)
   when it exists on disk — the operator has placed custom sidecars there.
3. **Bundled in-package fixtures** — `claude_wayfinder/fixtures/builtin/` — used
   when neither of the above is available.

Operators who want to override the bundled trigger weights (e.g. to increase
`locate` to weight `1.0` after observing calibration data) can create
`~/.claude/triggers/builtin/Explore.yml` with their own content. The user
directory takes precedence over the bundled defaults once it exists.

### Bundled trigger weights

The bundled fixtures ship a reasonable starting set of trigger weights
calibrated against code-recon and strategy prompts. These are intentionally
conservative first-guess values; the calibration note in Issue #286 says
"ship a reasonable starting set; calibration can come later from telemetry."

**Explore** — read-only code reconnaissance:
- `locate: 1.0`, `find_files: 1.0`, `where_is: 1.0` (primary signals)
- `grep: 0.5`, `search_for: 0.5` (secondary signals)
- `find: 0.25`, `search: 0.25`, `codebase: 0.25`, `explore: 0.5`
- `agent_mentions: ["Explore"]` — explicit `@Explore` routes immediately

**Plan** — architecture and strategy design:
- `design_strategy: 1.0`, `implementation_plan: 1.0`, `strategy: 1.0` (primary)
- `architect: 0.5`, `design: 0.5`, `architecture: 0.5`, `approach: 0.5`,
  `tradeoffs: 0.5`, `outline: 0.5`
- `breakdown: 0.25`
- `agent_mentions: ["Plan"]` — explicit `@Plan` routes immediately

Note: `plan` (the bare word) is intentionally absent from Plan's keywords to
avoid conflict with the `project-planner` agent, which owns `plan: 1.0`. The
`@Plan` agent mention is the primary routing signal for the built-in Plan agent;
strategy/architecture keywords are the secondary signals.
