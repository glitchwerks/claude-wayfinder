# claude-wayfinder

> A typed, auditable dispatch matcher for Claude Code — post-cognitive routing with a deterministic-first scoring kernel.

## What this is — and why it matters

A conventional LLM router enforces routing policy through prose instructions read at every decision point. In practice, this means the routing decision is made by the same model that's about to do the work — using the same signal that drove the original request. It drifts silently, makes the same decision differently across turns, and leaves no structured artifact you can inspect or replay.

`claude-wayfinder` replaces that loop for the mechanical cases — the ones that don't need judgment. It scores agents and skills against a structured task description composed by the router agent, not the raw user prompt, and returns one of seven typed decisions with confidence, rationale, and alternatives.

**Three properties that matter:**

- **Auditable.** Every dispatch decision is a structured artifact. Given the same context and catalog, the matcher returns the same answer. You can replay any past decision.
- **Post-cognitive.** The matcher fires after the router agent has read the conversation and extracted intent, file paths, and tools. Raw prompts are signal-poor; the router's interpretation is richer — same model, more signal.
- **Auto-generated catalog.** Built from skill sidecars and agent frontmatter at session start. No hand-curated rule config to drift out of sync.

The decision contract is a seven-member typed enum: `delegate` / `self_handle` / `self_handle_unaided` / `advisory` / `ask_user` / `needs_more_detail` / `mixed_content`.

**Advanced reading:** for the design rationale, see [`docs/design.md`](docs/design.md). For the algorithm specification, see [`docs/schema.md`](docs/schema.md).

## Install (Claude Code users)

**Requires Python >= 3.11.** The setup skill discovers Python automatically; it does not need to be on your `$PATH`.

Inside Claude Code, run these two commands:

```
/plugin marketplace add glitchwerks/claude-wayfinder
/plugin install claude-wayfinder@glitchwerks
```

### Troubleshooting

#### `claude-wayfinder requires setup` banner on session start

The plugin uses a venv-based architecture introduced in v0.4 ([#99](https://github.com/glitchwerks/claude-wayfinder/issues/99)). On first install you will see a SessionStart banner:

> ⚠ claude-wayfinder requires setup. Run /setup-wayfinder to materialize the Python venv.

Run `/setup-wayfinder` once. The skill will:

1. Discover a Python >= 3.11 on your machine.
2. Create a venv at `~/.claude/plugins/data/claude-wayfinder-glitchwerks/venv/`.
3. Install `claude-wayfinder` from PyPI.
4. Write a setup-state flag so subsequent sessions know setup is complete.

The same skill runs again after plugin updates — a `STALE` banner will prompt you.

#### "No Python >= 3.11 found"

The skill will ask you for an absolute path. Provide one such as `/usr/local/bin/python3.12` or `C:\Python313\python.exe`. The path is persisted in the setup-state flag for re-runs.

#### Setup completed but dispatch still does not fire

Open a new session. Hooks read the setup-state flag at session start; an in-progress session does not pick up the flag retroactively.

## How to use it

There are two distinct paths depending on your goal.

### What is a dispatch context?

Before using either path, it helps to understand what the matcher actually reads. When the router agent invokes `/dispatch`, it passes a **dispatch context** — a small JSON object with up to five fields:

| Field | Type | What it carries |
|---|---|---|
| `task_description` | string | The task sentence. Tokenized into keywords for matching. Required. |
| `file_paths` | array of strings | File or directory paths mentioned or implied by the current turn. |
| `agent_mentions` | array of strings | Agent names the user explicitly named (e.g. `"code-writer"`). |
| `tool_mentions` | array of strings | Tool names the user explicitly named (e.g. `"Bash"`, `"Grep"`). |
| `command_prefix` | string or null | The slash command the user typed, if any (e.g. `"/refactor"`). |

**The ≥ 2 dimensions rule.** The matcher counts how many of these fields are populated — each non-empty field is one "input dimension". If fewer than 2 dimensions are populated, the matcher returns `needs_more_detail` without attempting to score any catalog entries. This is not an error; it means the context is too sparse to route reliably. A `task_description` with at least one keyword counts as one dimension; each of `file_paths`, `agent_mentions`, `tool_mentions`, and a non-null `command_prefix` each add one dimension when non-empty. In addition, when `file_paths` is non-empty the matcher internally derives an `extensions` dimension (file-suffix set from the provided paths) that counts as a separate populated dimension — so a context with only `task_description` and `file_paths` yields three dimensions (keywords + paths + extensions), not two. See `docs/schema.md §2` for details.

**Good context** (2 dimensions — `task_description` + `file_paths`):

```json
{
  "task_description": "implement OAuth login flow",
  "file_paths": ["src/auth/oauth.py"],
  "agent_mentions": [],
  "tool_mentions": [],
  "command_prefix": null
}
```

**Too sparse** (1 dimension — `task_description` only):

```json
{
  "task_description": "help",
  "file_paths": [],
  "agent_mentions": [],
  "tool_mentions": [],
  "command_prefix": null
}
```

The first example scores against the catalog and produces a routing decision. The second returns `needs_more_detail` — the task sentence carries no useful keyword signal and no other dimensions provide context. When you see `needs_more_detail`, recompose the context: name the verb, the target files, and any explicit agent or tool mentions, then retry.

For the full field-level reference and the decision-composition rules, see [`docs/schema.md` §2](docs/schema.md#2-dispatch-context-schema).

### Try the demo without integrating

`/dispatch` ships with bundled demo fixtures (a small pre-built catalog of sample agents and skills) so you can see all seven decision branches in action without touching your own agents or building a catalog. Pass `--demo` to activate this mode — the matcher runs against the bundled fixtures, not your live session traffic. It does not intercept your session or route your tasks automatically. After running it, you decide whether to wire the matcher into your own router agent.

When invoked with `--demo`, the skill runs the matcher against the bundled demo catalog and returns all seven decision branches with inputs, decisions, confidence scores, and rationale. A single decision block looks like this:

```
# illustrative — agent names and rationale text will differ in your catalog
[1/7] Branch: delegate
  input       : 'implement the authentication module'
  file_paths  : ['src/auth.py']
  decision    : delegate
  confidence  : 0.9000
  agent       : code-writer
  rationale   : matched keywords: implement.
  skills      : ['python']
```

**How you know it's working:** the decision field contains one of the seven typed strings; confidence is a float between 0 and 1; rationale names the specific triggers that fired. An `ask_user` result is valid in the contract but reserved in v0.1 — the matcher will not produce it against real input.

#### Difference between `/dispatch --demo` and `python -m claude_wayfinder demo`

| | `/dispatch --demo` (in Claude Code) | `python -m claude_wayfinder demo` (CLI) |
|---|---|---|
| Catalog | Bundled demo fixtures | Bundled demo fixtures |
| Invocation | Skill triggered by router agent | Direct CLI invocation |
| Use case | Evaluate the matcher inside your Claude Code session | Evaluate the matcher without installing Claude Code |
| Output | Same seven-decision output | Same seven-decision output |

Both run the same matcher against the same bundled fixtures. The CLI path is the faster evaluation route if you are deciding whether to install the plugin.

### Integrate into your router

Once you have seen demo mode and want the matcher routing your real tasks, you need to build a **real catalog** (a `dispatch-catalog.json` generated from your own agent and skill frontmatter). By default, `/dispatch` resolves the catalog from the canonical path (`~/.claude/state/dispatch-catalog.json`, or `$CLAUDE_HOME/state/dispatch-catalog.json` when `$CLAUDE_HOME` is set) — real routing is the default behavior. If no catalog exists at that path, the skill emits `[CATALOG ERROR]` and exits non-zero. Set `$DISPATCH_CATALOG_PATH` to override the canonical default with a custom path.

**Feature density** — the number of populated input dimensions in a dispatch context — determines whether the matcher attempts scoring. Provide at least two dimensions (for example, a `task_description` plus `file_paths`) or the matcher returns `needs_more_detail` without scoring.

Minimum path from zero to a working real-catalog `/dispatch`:

**1. Install the plugin** — inside Claude Code:

```
/plugin marketplace add glitchwerks/claude-wayfinder
```

**2. One-time setup** — when the next session starts, the SessionStart hook will show a setup banner. Run `/setup-wayfinder` once to materialize the Python venv. See [Troubleshooting](#troubleshooting) for details.

**Step 2.5 — Do you have catalog-ready skills/agents?** The catalog builder scans your skill and agent files for a `triggers:` block. Without trigger frontmatter, `catalog build` completes but produces an empty or near-empty catalog and `/dispatch` will not route anything useful.

Each skill or agent you want the matcher to consider needs a `triggers.yml` sidecar declaring at least one of: `keywords`, `path_globs`, `agent_names`, `tool_names`, or `command_prefixes`. See [`docs/dispatch-authoring-guide.md`](docs/dispatch-authoring-guide.md) for field definitions and worked examples of adding triggers to existing skills and agents.

If you do not have any trigger frontmatter yet, skip ahead and run `/dispatch --demo` — it runs against bundled fixtures so you can see all seven decision branches in action without a real catalog. Come back to this step once you have added triggers.

**3. Build a catalog** — run this console script once (and again whenever your skill or agent frontmatter changes):

```bash
claude-wayfinder catalog build \
  --skills-dir ~/.claude/skills \
  --agents-dir ~/.claude/agents \
  --out ~/.claude/dispatch-catalog.json \
  --log ~/.claude/dispatch-catalog-build.log
```

On success, this writes `~/.claude/dispatch-catalog.json` — a JSON file listing every skill and agent entry the matcher will score.

**4. (Optional) Set `$DISPATCH_CATALOG_PATH`** — if your catalog lives somewhere other than the canonical default (`~/.claude/state/dispatch-catalog.json`), point to it explicitly in your shell profile:

```bash
export DISPATCH_CATALOG_PATH=~/.claude/dispatch-catalog.json
```

Without this env var, `/dispatch` resolves to the canonical default (`~/.claude/state/dispatch-catalog.json`). If that file exists, real routing proceeds. If it does not, the skill emits `[CATALOG ERROR]` — build the catalog first (step 3) or pass `--demo` to run against bundled fixtures instead.

**5. Add `Skill` to your router agent's `tools:` frontmatter** — `/dispatch` is invoked as a skill, so the router must have `Skill` in its tool list. See [`docs/integration.md`](docs/integration.md) for the full router-agent prompt snippet and catalog refresh patterns.

## Try it (no Claude Code required)

The CLI demo evaluates the matcher against bundled fixtures without requiring a Claude Code install. It covers all seven decision branches.

```bash
python -m claude_wayfinder demo
```

Expected output (truncated — seven decision blocks):

```
# illustrative — agent names and rationale text will differ in your catalog
[1/7] Branch: delegate
  input       : 'implement the authentication module'
  file_paths  : ['src/auth.py']
  decision    : delegate
  confidence  : 0.9000
  agent       : code-writer

[2/7] Branch: self_handle
  ...

[6/7] Branch: ask_user
  decision    : ask_user
  rationale   : Reserved — not produced by the v0.1 matcher. ask_user is
                part of the 7-decision contract and reserved for future
                clarification flows.

[7/7] Branch: needs_more_detail
  ...
```

`ask_user` is a valid member of `VALID_DECISIONS` but is reserved in v0.1 — the matcher never produces it.

### CLI subcommands

The full CLI surface is documented via `python -m claude_wayfinder --help`. Key subcommands:

- `demo` — run the matcher against bundled demo fixtures; covers all seven decision branches.
- `dispatch` — run the matcher against a live catalog; reads dispatch context JSON from stdin.
  - `--batch` — NDJSON batch mode: read one context object per line from stdin, write one decision per line to stdout. Each output line includes an `"input_index"` field (0-based). Blank lines are skipped; malformed lines produce an error record without aborting the batch. The catalog is loaded once per invocation. Example:
    ```bash
    printf '{"task_description":"implement auth","file_paths":["src/auth.py"]}\n{"task_description":"fix css bug"}\n' \
      | python -m claude_wayfinder dispatch --batch
    ```
- `catalog build` — scan skill sidecars and agent frontmatter and write a `dispatch-catalog.json`.
- `audit-catalog` — catalog-wide static analysis (conflict pairs, structural checks, matcher-aware semantic rules). See [`docs/dispatch-authoring-guide.md`](docs/dispatch-authoring-guide.md).
- `health` — router health report and observability drill-downs. Key subcommands (see [`skills/router-health/SKILL.md`](skills/router-health/SKILL.md) for the full playbook):
  - `health --report` — print a full health summary covering dispatch invocation rate, bypass rate, advisory override rate, catalog availability, and catalog stability.
  - `health drill --metric <name> --window <period>` — drill into a specific metric (e.g. `bypass`, `advisory-override`, `recent-drift`) to surface event distributions and top-offending sessions.
  - `health top --kind <agents|skills> --window <period> --limit <n>` — list the most-dispatched agents or most-invoked skills over a time window.
  - `health catalog-status` — report catalog entry counts (agents, skills, routable agents) and flag unexpected zeros.

## Bundled skills

The plugin ships two skills usable inside Claude Code:

- `claude-wayfinder:dispatch` — runs the matcher against your live catalog by default (canonical path: `~/.claude/state/dispatch-catalog.json`; override with `$DISPATCH_CATALOG_PATH`). Pass `--demo` to run against bundled fixtures instead. Emits `[CATALOG ERROR]` and exits non-zero if no catalog is found and `--demo` is not passed. See the [Try the demo](#try-the-demo-without-integrating) section above.
- `claude-wayfinder:dispatch-authoring` — matcher-aware authoring and troubleshooting knowledge for the full dispatch authoring surface (trigger frontmatter, applicable_agents, applicable_skills, routable). Covers the seven-decision ladder, scoring math, weight ladder, path-glob footguns, conflict-pair detection, and the audit-catalog CLI pointer. See [`docs/dispatch-authoring-guide.md`](docs/dispatch-authoring-guide.md).

## Dispatch overrides

Dispatch overrides are hard-coded routing decisions that bypass the scoring pipeline entirely: when an override rule's predicates match the dispatch context, the matcher returns the rule's pre-declared `(decision, agent, skills, confidence, rationale)` verbatim and skips all scoring. Use them for routes you want pinned unconditionally — a `/deploy` command that must never be delegated, or all Python files that should always reach `code-writer` — and not as a substitute for a well-tuned catalog. Overrides take precedence over scored decisions; they fire first, and a matched override short-circuits the rest of the pipeline.

Set the env var to point at your rule file:

```bash
export DISPATCH_OVERRIDES_PATH=/path/to/dispatch-overrides.json
```

A minimal two-rule file covering the two most common predicates (substitute your own agent names):

```json
{
  "version": 1,
  "rules": [
    {
      "id": "deploy-command",
      "decision": "self_handle_unaided",
      "agent": null,
      "skills": [],
      "confidence": 1.0,
      "rationale": "/deploy is always handled manually",
      "predicates": { "command_prefix": "/deploy" }
    },
    {
      "id": "py-files-to-code-writer",
      "decision": "delegate",
      "agent": "code-writer",
      "skills": ["python"],
      "confidence": 0.99,
      "rationale": "All Python edits go to code-writer unconditionally",
      "predicates": { "path_globs": ["**/*.py"] }
    }
  ]
}
```

When `$DISPATCH_OVERRIDES_PATH` is unset, scored matching runs unchanged. On load failure, `[OVERRIDES ERROR]` is emitted to stderr and the matcher falls back to scoring. For the full predicate vocabulary, audit rules, telemetry schema, and design rationale, see [`docs/dispatch-overrides.md`](docs/dispatch-overrides.md).

## Privacy / local logs

There are two independent writers of the dispatch log.

**Writer 1 — Python matcher (opt-in).** When `DISPATCH_LOG_PATH` is set, `_resolve_log_path` in `src/claude_wayfinder/match/_catalog.py` appends one `matcher_decision` JSON row per dispatch to that path. When the env var is unset, `_resolve_log_path` returns `None` immediately and no write occurs — there is no `~/.claude/` fallback on this path. Each row contains:

- `type: "matcher_decision"`, `ts` (UTC timestamp), `session_id`
- `input` — the full dispatch context, including `task_description` (a prompt excerpt), `file_paths`, `agent_mentions`, `tool_mentions`, and `command_prefix`
- `output` — the decision: `decision`, `agent`, `confidence`, `rationale`, `alternatives`
- `catalog_hash`, `matcher_version`, `override_id`

**Writer 2 — PostToolUse hook (default-on).** The `PostToolUse(Bash) → log-dispatch-decision.js` entry in `hooks/hooks.json` fires after every real `claude_wayfinder dispatch` Bash call whenever the plugin is installed. Its `resolveLogPath()` (`hooks/log-dispatch-decision.js`) uses `DISPATCH_LOG_PATH` when set, but falls back to `~/.claude/state/dispatch-log.jsonl` when the env var is absent. The hook-written row is a `matcher_decision` entry that includes the extracted dispatch input context (including the prompt excerpt) plus a populated `session_id` and `attribution_source: "post_tool_use_hook"`. This means **prompt context is recorded by default for any installed user**, even when `DISPATCH_LOG_PATH` is unset.

Setting `DISPATCH_LOG_PATH` controls where the hook writes, not whether it writes. There is no env-var opt-out for the hook.

The `health` read tooling uses a separate env var (`DISPATCH_LOG`) to locate the log for analysis; the two names do not need to match unless you want `health` to read the same path you are writing.

**Debug payload dumps.** When `DISPATCH_HOOK_DEBUG=1`, the hook writes a full JSON payload dump (the complete hook input, which contains a prompt excerpt) to a private directory:

1. `$CLAUDE_PLUGIN_DATA` if set and non-empty.
2. `~/.claude/state/wayfinder-debug/` otherwise.

The directory is created with `0700` permissions and each dump file is written with `0600` (owner-only) — the file is not placed in a world-readable shared directory.

**How to disable hook logging:**

Unsetting `DISPATCH_LOG_PATH` stops the opt-in Python matcher write and relocates the hook write back to `~/.claude/state/dispatch-log.jsonl` — it does not stop the hook from writing. To fully disable local dispatch logging, remove or disable the `PostToolUse(Bash) → log-dispatch-decision.js` entry in `hooks/hooks.json`.

```bash
# Suppress debug payload dumps only (default when unset):
unset DISPATCH_HOOK_DEBUG
```

**How to clear the log:**

```bash
rm ~/.claude/state/dispatch-log.jsonl   # default hook path when DISPATCH_LOG_PATH is unset
rm "$DISPATCH_LOG_PATH"                 # if you have set a custom path
```

## Shadow-mode dispatch telemetry

When enabled, the matcher also computes a second, telemetry-only routing decision — the Matcher-v3 two-axis "Compose" route — alongside the live lexical decision, and logs both. Compose never changes what gets dispatched: the routing decision — the selected `decision`/`agent`/`skills` values — is always the live decision, unchanged from earlier releases. (The serialized stdout JSON itself is not fully byte-identical to 1.2.0 — this release also adds `matcher_version` and `catalog_hash` fields — but which agent or skill gets dispatched is unaffected.) Shadow compute exists so Compose's decisions can be compared against live traffic offline before any future release lets it steer routing.

Shadow compute is gated by the `DISPATCH_SHADOW` env var, fail-open to ON:

```bash
export DISPATCH_SHADOW=0   # or "false" / "no" — disables shadow compute
```

- Absent, truthy, unrecognized, or malformed values all resolve to **ON**.
- Only an exact case-insensitive match of `0`, `false`, or `no` resolves to **OFF**.
- When OFF, shadow compute is skipped entirely — not computed and then discarded — and the log entry omits the `shadow` key.

The same toggle is exposed as the `shadow_enabled` plugin `userConfig` field for users who configure the plugin through Claude Code's settings UI rather than a shell env var; it is plumbed through to `DISPATCH_SHADOW` at the matcher-launch site.

## What's next

If you want to use the matcher for real routing in your own Claude Code setup, there are two paths:

- **Integrate now via the contributor path.** Clone the repo, build your own catalog from your agent and skill frontmatter, and call the library API from your router agent. The [Contributing](#contributing) section covers the mechanics, and `python -m claude_wayfinder --help` documents the CLI surface.
- **Wait for the bundled runtime.** Issue [#6](https://github.com/glitchwerks/claude-wayfinder/issues/6) tracks the zero-friction-install spike — a bundled router agent and catalog generator that would make daily-driver routing available without manual integration. That work is scoped to v0.2.

## Library API

The public API is documented in [`docs/api.md`](docs/api.md). A minimal integration looks like:

```python
from pathlib import Path
from claude_wayfinder import load_catalog, build_features, score, decide, ScoredEntry

catalog = load_catalog(Path("/path/to/dispatch-catalog.json"))
features = build_features({
    "task_description": "implement the login page",
    "file_paths": ["src/auth/login.py"],
})

agents = [ScoredEntry(e, score(e, features)) for e in catalog if e.kind == "agent" and e.routable]
skills = [ScoredEntry(e, score(e, features)) for e in catalog if e.kind == "skill"]

result = decide(agents, skills, features, catalog)
# result["decision"] is one of the seven decision strings
```

The `__all__`-guarded surface (`load_catalog`, `build_features`, `score`, `decide`, `VALID_DECISIONS`, and the supporting dataclasses) is stable for the v0.1 series: patch releases will not rename, remove, or alter any public signature.

## Prior art

- [`wwadley-lucas/claude-dispatch`](https://github.com/wwadley-lucas/claude-dispatch) — pioneered hook-based pre-cognitive matching with zero-LLM-in-default-path principles. Operates at a different lifecycle point (raw user prompt, not router-composed task description).
- [`darco81/skills-radar`](https://github.com/darco81/skills-radar) — lazy skill loading via embedding retrieval (BM25 + dense). Adjacent problem space, different mechanism.
- [Anthropic Tool Search Tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool) — upstream pattern for the MCP-tools case.

## Contributing

**Requirements:** Python >= 3.11, [`uv`](https://github.com/astral-sh/uv).

```bash
git clone https://github.com/glitchwerks/claude-wayfinder.git
cd claude-wayfinder
uv venv .venv
uv pip install -e ".[dev]"
```

If you add or remove dependencies in `pyproject.toml`, regenerate the lockfile and commit it:

```bash
uv lock
```

Run the test suite:

```bash
python -m pytest
```

Run the linter:

```bash
python -m ruff check src/ tests/
```

Validate the plugin manifest:

```bash
claude plugin validate .claude-plugin/plugin.json
```

`claude plugin validate` is the canonical manifest check and runs as a CI gate on every push and PR (inside the `Validate Plugin Manifest` job). The project also ships `tests/test_plugin_manifests.py`, which covers field-level conventions (name, description, author, etc.) that the official validator does not enforce — both checks are complementary.

The `@anthropic-ai/claude-code` version pinned in `.github/workflows/ci.yml` is tracked by [Renovate](https://github.com/apps/renovate) via `renovate.json`. Bump PRs are opened weekly and require manual review — schema changes in `claude plugin validate` are exactly what the CI gate exists to surface.

Run the demo (confirms the matcher works end-to-end against bundled fixtures):

```bash
python -m claude_wayfinder demo
```

**Filing issues:** Use [GitHub Issues](https://github.com/glitchwerks/claude-wayfinder/issues). Before opening a new issue, check that one does not already exist for the same problem.

**Workflow:** Create a branch per issue, open a PR that references the issue number in its body (`Closes #N`). For non-trivial work, set up a git worktree per branch (see the CLAUDE.md contributor notes for the worktree convention used in this repo).

## License

MIT — see [`LICENSE`](./LICENSE).
