# claude-wayfinder v0.1 API Reference

`claude-wayfinder` exposes a small, typed surface for embedding the deterministic dispatch matcher in your own code. The public API is defined by `__all__` in `src/claude_wayfinder/__init__.py`.

For the matcher's design rationale, see [`docs/design.md`](design.md). For the seven-decision contract and the scoring algorithm, see [`docs/schema.md`](schema.md).

## CLI entry points

The plugin installs two console scripts on package install (declared in `pyproject.toml` under `[project.scripts]`):

| Command | Underlying module | Use when |
| --- | --- | --- |
| `claude-wayfinder` | `claude_wayfinder.cli:main` | You want the full CLI surface — subcommands like `dispatch`, `catalog build`, `health`. |
| `claude-wayfinder-match` | `claude_wayfinder.match:main` | You want a direct shortcut to the matcher in shell pipelines. Equivalent to `claude-wayfinder dispatch`; reads dispatch-context JSON from stdin and writes decision JSON to stdout. |

Both honor `$DISPATCH_CATALOG_PATH` for catalog resolution. `claude-wayfinder-match` also accepts a `--catalog-path <path>` flag that overrides the env var (resolution order: `--catalog-path` > `DISPATCH_CATALOG_PATH` > error).

### Example: `claude-wayfinder-match`

```bash
echo '{"task_description": "implement auth module", "file_paths": ["src/auth.py"]}' \
  | DISPATCH_CATALOG_PATH=~/.claude/dispatch-catalog.json \
    claude-wayfinder-match
```

The stdin JSON shape: `task_description` is required; `file_paths`, `agent_mentions`, `tool_mentions`, and `command_prefix` are optional and default to empty/null when omitted.

## Public surface

Every name listed here can be imported directly from the package:

```python
from claude_wayfinder import <name>
```

### `load_catalog`

```python
def load_catalog(path: Path) -> list[CatalogEntry]
```

Loads and parses a `dispatch-catalog.json` file from disk. Returns a list of `CatalogEntry` objects ready to be passed to `score` and `decide`.

Raises `FileNotFoundError` if the file does not exist, `json.JSONDecodeError` if it is malformed, and `ValueError` if the catalog contains zero entries.

```python
from pathlib import Path
from claude_wayfinder import load_catalog

entries = load_catalog(Path("/path/to/dispatch-catalog.json"))
```

### `build_features`

```python
def build_features(context: dict[str, Any]) -> Features
```

Extracts and normalises the feature set from a dispatch context dict. All string values are lowercased and deduplicated. File extensions are derived from `file_paths` entries.

The expected shape of `context` mirrors the matcher's stdin JSON contract:

```python
# Substitute your own agent names
{
    "task_description": "implement the login page",  # required
    "file_paths":       ["src/auth/login.py"],        # optional
    "agent_mentions":   ["code-writer"],              # optional
    "tool_mentions":    ["Edit"],                     # optional
    "command_prefix":   None                          # optional
}
```

Returns a `Features` instance with all fields populated from the context.

```python
from claude_wayfinder import build_features

features = build_features({
    "task_description": "write pytest tests for the auth module",
    "file_paths": ["src/auth/login.py"],
})
```

### `score`

```python
def score(entry: CatalogEntry, features: Features) -> float
```

Computes the match score for one catalog entry against a feature set. Returns a float in `[0.0, 1.0]`.

The scoring formula (v5 §3.1.2):

- Command prefix match → `1.0` (short-circuit)
- Agent mention match → `1.0` (short-circuit)
- Any exclude term in keywords → `0.0` (hard zero)
- Otherwise: `min(0.4 × matched_glob_count + 0.5 × weighted_keyword_score + 0.5 × matched_tool_count, 1.0)`

```python
from claude_wayfinder import load_catalog, build_features, score

entries = load_catalog(catalog_path)
features = build_features(context)
scored = [(e, score(e, features)) for e in entries]
```

### `decide`

```python
def decide(
    scored_agents: list[ScoredEntry],
    scored_skills: list[ScoredEntry],
    features: Features,
    catalog_entries: list[CatalogEntry],
) -> dict[str, Any]
```

Composes the routing decision from pre-scored agents and skills. Implements the seven-decision ladder from v5 §3.1.3–§3.1.4 in order:

1. `needs_more_detail` — feature density below threshold (fewer than 2 populated input dimensions)
2. `delegate` — best agent scores ≥ 0.85 with a gap ≥ 0.2 over second place
3. `mixed_content` — two or more agents clamp at 1.0 on path-disjoint lanes
4. `self_handle` — at least one skill scores ≥ 0.5 and no dominant agent
5. `advisory` — agent exists above 0.5 but match is not conclusive
6. `self_handle_unaided` — no useful signal

`scored_agents` must exclude the `general-purpose` router agent before this call. Returns a decision dict matching the output JSON schema:

```python
# Substitute your own agent names
{
    "decision":     "delegate",
    "agent":        "code-writer",
    "skills":       ["python"],
    "confidence":   0.92,
    "rationale":    "matched keywords: implement.",
    "alternatives": [{"agent": "debugger", "score": 0.5}]
}
```

The `agent`, `skills`, and `alternatives` keys are present only when the decision type calls for them.

### `VALID_DECISIONS`

```python
VALID_DECISIONS: frozenset[str]
```

The complete set of seven valid routing decision strings:

```python
frozenset({
    "delegate",
    "self_handle",
    "self_handle_unaided",
    "advisory",
    "ask_user",
    "needs_more_detail",
    "mixed_content",
})
```

Use this constant to validate decision strings rather than hardcoding the set in your code.

### `CatalogEntry`

```python
@dataclass(frozen=True)
class CatalogEntry:
    name: str
    kind: str
    triggers: Triggers
    applicable_agents: tuple[str, ...]
    applicable_skills: tuple[str, ...]
    source: str = "owned"
    routable: bool = True
```

Represents one entry (agent or skill) from the dispatch catalog. Produced by `load_catalog`; consumed by `score` and `decide`.

- `name` — unique entry identifier (e.g. `"code-writer"`, `"python"`)
- `kind` — either `"agent"` or `"skill"`
- `triggers` — parsed trigger configuration as a `Triggers` instance
- `applicable_agents` — for skills: which agents may receive this skill; `("*",)` means all
- `applicable_skills` — for agents: which skills are applicable
- `source` — provenance: `"owned"` for first-party, `"plugin"` for third-party
- `routable` — whether the entry participates in agent routing; the router agent sets this to `False` to exclude itself

### `Features`

```python
@dataclass
class Features:
    command_prefix: str | None = None
    agent_mentions: frozenset[str] = field(default_factory=frozenset)
    keywords: frozenset[str] = field(default_factory=frozenset)
    paths: tuple[str, ...] = field(default_factory=tuple)
    extensions: frozenset[str] = field(default_factory=frozenset)
    tool_mentions: frozenset[str] = field(default_factory=frozenset)
```

The normalised feature set extracted from a dispatch context. Produced by `build_features`; consumed by `score` and `decide`. All string fields are lowercased.

- `command_prefix` — slash command string (e.g. `"python"`), or `None`
- `agent_mentions` — explicit agent names referenced in the context
- `keywords` — individual tokens extracted from `task_description`
- `paths` — file and directory paths from `file_paths`
- `extensions` — file extensions derived from `paths`, dot-stripped and lowercased
- `tool_mentions` — explicit tool names mentioned in the context

### `ScoredEntry`

```python
@dataclass(frozen=True)
class ScoredEntry:
    entry: CatalogEntry
    score: float
```

A catalog entry paired with its computed score. Intended as the element type for the sorted lists passed to `decide`.

### `Keyword`

```python
@dataclass(frozen=True)
class Keyword:
    term: str
    weight: float
```

A single keyword trigger from a catalog entry. Valid `weight` values are `0.25`, `0.5`, and `1.0`. Accessible via `CatalogEntry.triggers.keywords`.

### `Triggers`

```python
@dataclass(frozen=True)
class Triggers:
    command_prefixes: frozenset[str]
    agent_mentions: frozenset[str]
    path_globs: tuple[str, ...]
    keywords: tuple[Keyword, ...]
    tool_mentions: frozenset[str]
    excludes: frozenset[str]
```

Parsed trigger configuration for one catalog entry. Accessible via `CatalogEntry.triggers`. The `excludes` set contains terms that hard-zero the entry's score when present in the task keywords.

## Stability promise for v0.1

The names in `__all__` form the stable public API for the v0.1 series. Patch releases (0.1.x) will not rename, remove, or alter the signatures of any public name.

Minor releases (0.2, 0.3, ...) may restructure or rename public names. Changes of that kind will be documented in the changelog with a migration note. Once 1.0 ships, standard semver applies: no breaking changes within a major version.

## Internal API and direct submodule access

### The `build_catalog` function

`build_catalog.build_catalog` (the function inside the submodule of the same name) is public in intent but cannot be re-exported at the package level. When Python resolves `claude_wayfinder.build_catalog`, it returns the submodule — exposing the function under the same name at the package level would shadow the submodule and break `import claude_wayfinder.build_catalog` patterns. The comment in `__init__.py` reads:

> `build_catalog.build_catalog` is public but cannot be re-exported here because the name `build_catalog` at the package level refers to the submodule. Public access path: `from claude_wayfinder.build_catalog import build_catalog`.

To use the catalog builder:

```python
from claude_wayfinder.build_catalog import build_catalog
```

This import is supported and will not be removed in patch releases, but it is not part of the `__all__`-guarded surface. Treat it with the same stability expectations as other submodule-direct imports below.

### Submodules accessed directly

Importing from submodules directly — `match`, `build_catalog`, `match_filters` — rather than through the package-level re-exports is not covered by the stability promise. Internal names, call signatures, and module structure may change in any release.

```python
# Not covered by the stability promise:
from claude_wayfinder.match import extract_keywords
from claude_wayfinder.match_filters import is_agent_routable
```

If you find yourself needing a submodule-only name in production code, open an issue requesting that it be promoted to `__all__`.

## What is not public

The following exist and work but carry no stability promise:

| Name | Why it is excluded |
|------|--------------------|
| `claude_wayfinder._health` | Internal module (leading underscore). Contains health-reporting and CI-invariant tooling intended for the harness CLI, not for embedding. |
| `claude_wayfinder._health.MetricResult` | Internal dataclass used by the health reporter. |
| Any name starting with `_` | Convention: underscore prefix signals internal use throughout the codebase. |
| `claude_wayfinder.match` (direct import) | Submodule-direct access; not covered by stability promise. |
| `claude_wayfinder.build_catalog` (direct import) | Submodule-direct access; see the `build_catalog` note above. |
| `claude_wayfinder.match_filters` (direct import) | Submodule-direct access; not covered by stability promise. |

To check whether a name is public before depending on it, inspect `claude_wayfinder.__all__` at runtime or consult this document.
