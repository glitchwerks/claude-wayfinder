# Integration Guide — claude-wayfinder

> **Prerequisite:** plugin installed via `/plugin install`, and `/setup-wayfinder` completed successfully. See [README.md](../README.md#install-claude-code-users) for install steps.

This guide is for consumers who want to use claude-wayfinder as the actual dispatch layer in their router agent, not just evaluate the demo. It assumes you have a Claude Code router agent in place and want to replace (or augment) its prose-policy routing with deterministic decisions from the matcher.

**Prerequisites:**

- Python >= 3.11 (does not need to be on `$PATH` — the setup skill discovers it)
- The plugin installed in your Claude Code environment (via `/plugin marketplace add glitchwerks/claude-wayfinder`)

---

## One-time setup

The plugin materializes its Python venv on demand via the `/setup-wayfinder` skill, not automatically on plugin install. This design eliminates the class of bootstrap failures that affected v0.3.x, where the hook had to discover a usable Python interpreter at hook-fire time (see epic [#99](https://github.com/glitchwerks/claude-wayfinder/issues/99) and the architecture spec at `docs/superpowers/specs/2026-05-17-setup-skill-architecture-design.md`).

### First-time install

1. Install the plugin: `/plugin marketplace add glitchwerks/claude-wayfinder`
2. Open a new session. The `check-catalog-health.js` SessionStart hook emits a banner via `additionalContext`:

   > ⚠ claude-wayfinder requires setup. Run /setup-wayfinder to materialize the Python venv.

3. Run `/setup-wayfinder`. The skill discovers Python >= 3.11, creates a venv at `~/.claude/plugins/data/claude-wayfinder-glitchwerks/venv/`, installs `claude-wayfinder` from PyPI, verifies the import, and writes a setup-state flag.
4. Open a new session — hooks read the flag at session start and proceed normally.

### After a plugin update

When you run `/plugin update`, the next SessionStart hook detects a version mismatch between the installed venv and the new plugin version and emits:

> ⚠ claude-wayfinder venv is for v0.4.0 but plugin is v0.4.1. Run /setup-wayfinder to refresh.

Run `/setup-wayfinder` again. The skill always wipes and rebuilds the venv, ensuring a clean state.

### Cross-machine setup

Per-machine setup is the supported model. If you share `~/.claude` across machines via OneDrive, Dropbox, or similar sync, the setup-state flag's recorded venv path will not resolve on a different machine and a `BROKEN` banner will fire. Run `/setup-wayfinder` once per machine. This is intentional — machine-agnostic venvs would re-introduce most of the complexity this architecture eliminates.

### Advanced: Python pipeline import

The setup skill's core pipeline is also importable as Python (`tests/integration/setup_pipeline.py`) for CI environments or advanced scripting. Most users should use `/setup-wayfinder` directly.

---

## Core integration

Required to make `/dispatch` route real traffic. Read end-to-end before attempting the optional extras.

### 1. Build a catalog

The matcher operates against a catalog you build from your own skill and agent frontmatter. There are no defaults for the output path or log path — both must be supplied explicitly.

The `catalog build` subcommand reads `SKILL.md` files and agent frontmatter `.md` files, applies source-tagged precedence, and writes a `dispatch-catalog.json` and a build log.

#### User-scope-only sources

Catalog built from user-level skills and agents only (nothing project-local):

```bash
claude-wayfinder catalog build \
  --skills-dir ~/.claude/skills \
  --agents-dir ~/.claude/agents \
  --out ~/.claude/dispatch-catalog.json \
  --log ~/.claude/dispatch-catalog-build.log
```

#### User-scope with project-local overlay

Adds repo-local `.claude/agents/` and `.claude/skills/` on top of the user-scope sources. Project-local entries take the highest precedence in the source-tagged model.

```bash
claude-wayfinder catalog build \
  --skills-dir ~/.claude/skills \
  --agents-dir ~/.claude/agents \
  --project-root /path/to/your/repo \
  --out ~/.claude/dispatch-catalog.json \
  --log ~/.claude/dispatch-catalog-build.log
```

`--project-root` may be omitted if the current working directory is the project root — the CLI auto-detects via `git rev-parse --show-toplevel`. Supply it explicitly when running from a worktree or from a script where the cwd is not the repo root.

#### User-scope with plugin overrides

Adds plugin-supplied skill overrides (trigger weight customizations) and plugin discovery:

```bash
claude-wayfinder catalog build \
  --skills-dir ~/.claude/skills \
  --agents-dir ~/.claude/agents \
  --plugin-overrides-dir ~/.claude/plugins/overrides \
  --plugins-dir ~/.claude/plugins \
  --out ~/.claude/dispatch-catalog.json \
  --log ~/.claude/dispatch-catalog-build.log
```

**No defaults exist** for `--out` or `--log`. The build exits with an error if either is omitted. This is intentional — see issue #10. Inspect the log file after any build to review name-collision warnings and source-precedence decisions.

---

### 2. Catalog path — canonical default and `$DISPATCH_CATALOG_PATH`

The `/dispatch` skill resolves the catalog path in this order (Issue #284):

1. **`$DISPATCH_CATALOG_PATH`** — if set, used directly (invalid path is a
   hard error).
2. **Canonical default** — `$CLAUDE_HOME/state/dispatch-catalog.json` (or
   `~/.claude/state/dispatch-catalog.json` when `$CLAUDE_HOME` is unset).
   If the file exists, real-catalog mode activates automatically.
3. **`[CATALOG ERROR]`** — if neither resolves to an existing file, the
   skill exits non-zero.  Demo mode is **never** the implicit fallback.

If you build your catalog with `catalog build --out ~/.claude/state/dispatch-catalog.json` (the default output path), the skill picks it up automatically — no env var needed.

To override the path explicitly:

```bash
export DISPATCH_CATALOG_PATH=~/.claude/state/dispatch-catalog.json
```

On PowerShell:

```powershell
$env:DISPATCH_CATALOG_PATH = "$env:USERPROFILE\.claude\state\dispatch-catalog.json"
```

To run the bundled fixture demos instead of real-catalog routing, pass `--demo` explicitly:

```bash
python -m claude_wayfinder dispatch --demo
```

---

### 3. Tools-frontmatter prerequisite

Your router agent must include `Skill` in its `tools:` frontmatter for `/dispatch` to be invocable. Example of correct frontmatter:

```
tools: Glob, Grep, Read, Edit, Write, Bash, Skill, ToolSearch
```

Without `Skill` in the tools list, the `/dispatch` slash command is not available to the agent and the dispatch loop cannot run.

---

### 4. Router-agent prompt snippet

Drop the following block into your router agent's system prompt or operational instructions. It covers the full dispatch loop: composing the context, invoking `/dispatch`, parsing the returned decision, and branching on all seven decision types.

```markdown
## Dispatch loop

Before handling any user task, compose a dispatch context from the current turn
and invoke `/dispatch` to get a routing decision.

### Step 1 — Compose the dispatch context

Extract five fields from the current turn and compose them as JSON:

- `task_description`: Your interpretation of what the user wants done,
  expressed as a task sentence (not the raw user message). Be explicit:
  include the verb ("implement", "fix", "refactor", "document"), the noun
  (the thing being changed), and any constraint. Example:
  "implement OAuth2 login in src/auth.py using the existing session model"

- `file_paths`: File paths mentioned or implied by the user, as a JSON array.
  Include paths inferred from context if confident. Empty array if none.

- `agent_mentions`: Agent names the user explicitly named, as a JSON array.
  Example: `["code-writer"]`. Empty array if none.

- `tool_mentions`: Tool names the user explicitly named (e.g. "Bash", "Grep"),
  as a JSON array. Empty array if none.

- `command_prefix`: The slash command the user typed, if any. Example:
  `"/refactor"`. `null` if the user did not type a slash command.

Concrete example for a turn where the user says
"fix the auth token expiry bug in src/auth/token.py":

```json
{
  "task_description": "fix auth token expiry bug in src/auth/token.py",
  "file_paths": ["src/auth/token.py"],
  "agent_mentions": [],
  "tool_mentions": [],
  "command_prefix": null
}
```

### Step 2 — Invoke /dispatch

Pass the JSON on stdin to `/dispatch`:

```
echo '<dispatch-context-json>' | /dispatch
```

The skill resolves the catalog path automatically: it checks
`$DISPATCH_CATALOG_PATH` first, then the canonical default
(`~/.claude/state/dispatch-catalog.json`). With a valid catalog present
it returns the matcher's decision JSON on stdout. If no catalog is found,
the skill emits `[CATALOG ERROR]` and exits non-zero — demo mode requires
passing `--demo` explicitly.

### Step 3 — Parse the decision JSON

The decision JSON has this shape:

```json
{
  "decision":     "delegate",
  "agent":        "code-writer",
  "skills":       ["python"],
  "confidence":   0.92,
  "rationale":    "matched keywords: implement.",
  "alternatives": [{"agent": "devops", "score": 0.41}]
}
```

Fields `agent`, `skills`, and `alternatives` are present when applicable to
the decision type.

### Step 4 — Branch on the decision

Handle each of the seven decision types as follows:

**`delegate`** — One agent scored decisively. Compose an Agent tool call for
the named agent. If `skills` is non-empty, propagate those skill names into
the sub-agent's prompt so it can invoke them. Emit the audit line (see below).

**`self_handle`** — No single agent dominates, but one or more skills scored
above threshold. Invoke the returned skills via the Skill tool and proceed
without delegating to a sub-agent. Emit the audit line.

**`self_handle_unaided`** — Sufficient context to proceed; no specialist agent
or skill applies. Handle the task directly without delegation or skill
activation. Emit the audit line.

**`advisory`** — An agent scored above the advisory floor but below the
`delegate` threshold. Delegation is suggested but not certain. Use the
suggested agent, note the uncertainty in your audit line. Overriding an
advisory decision without a stated reason is logged as drift.

**`advisory`** (gap-tied variant) — Two or more agents scored similarly above
the scoring floor (gap < 0.2). The top candidate is suggested via `agent`; use
it while noting the uncertainty in your audit line. The `alternatives` field
lists the close-scoring candidates for reference.

**`ask_user`** — Reserved in v0.1/v0.2. The matcher does not produce this
decision currently. Include a handler for forward compatibility: if received,
pause and ask the user to clarify before proceeding.

**`needs_more_detail`** — Feature density was too low to route confidently.
Do not retry with the same context. Recompose `task_description` with
explicit signals: name the verb, the files, the constraint. Include
`file_paths` and `agent_mentions` if the user gave any hint of them. Retry
`/dispatch` once with the richer context. If the retry also returns
`needs_more_detail`, ask the user for clarification.

### Step 5 — Emit the audit line

Emit one structured line per dispatch, before taking the routed action:

```
[dispatch] decision=<decision> agent=<agent|—> confidence=<0.xx> rationale="<rationale>"
```

Examples:

```
[dispatch] decision=delegate agent=code-writer confidence=0.92 rationale="matched keywords: implement."
[dispatch] decision=self_handle agent=— confidence=0.71 rationale="skill python matched on path glob **/*.py."
[dispatch] decision=advisory agent=devops confidence=0.61 rationale="advisory: devops matched on keyword 'deploy'."
[dispatch] decision=needs_more_detail agent=— confidence=0.20 rationale="feature density below threshold."
```

The audit line is the observable record of the dispatch decision. It appears
in the session transcript so operators can replay and inspect routing choices.
```

---

### 5. Troubleshooting

#### Catalog missing

**Symptom:** The skill emits a `[CATALOG ERROR]` banner on stderr and exits non-zero. The decision JSON is not produced. Routing falls back to LLM judgment.

**Cause — no catalog at the canonical path and no env var:** Neither
`$DISPATCH_CATALOG_PATH` nor `$CLAUDE_HOME/state/dispatch-catalog.json`
(or `~/.claude/state/dispatch-catalog.json`) points to an existing file.
As of Issue #284, demo mode is **no longer** the implicit fallback — the
skill emits `[CATALOG ERROR]` and exits non-zero.

Fix: build a catalog at the canonical default path:

```bash
claude-wayfinder catalog build \
  --skills-dir ~/.claude/skills \
  --agents-dir ~/.claude/agents \
  --out ~/.claude/state/dispatch-catalog.json \
  --log ~/.claude/state/catalog-generation.log
```

Or set `$DISPATCH_CATALOG_PATH` explicitly. To run demo fixtures
intentionally, pass `--demo` to the dispatch command.

**Cause — env var set but file is missing:** `$DISPATCH_CATALOG_PATH` points to a path that does not exist. The skill pre-validates the path and emits `[CATALOG ERROR] ... file not found at <path>`.

Fix: run `catalog build` to create the catalog at the configured path, then verify the file exists:

```bash
claude-wayfinder catalog build \
  --skills-dir ~/.claude/skills \
  --agents-dir ~/.claude/agents \
  --out "$DISPATCH_CATALOG_PATH" \
  --log ~/.claude/dispatch-catalog-build.log
```

**Cause — file present but invalid JSON:** The skill emits `[CATALOG ERROR] ... malformed JSON`. The catalog file may be truncated (interrupted build), corrupted, or contain a syntax error.

Fix: delete the catalog and rebuild from scratch. The build log at `--log` will indicate whether the build completed cleanly.

#### Catalog stale

**Symptom:** The skill emits a `[DISPATCH WARNING] Catalog mtime is older than source files: ...` to stderr. The dispatch proceeds with the stale catalog — routing is not blocked, but trigger weights may not reflect recent skill or agent edits.

**When it fires:** Only when both `$DISPATCH_SKILLS_DIR` and `$DISPATCH_AGENTS_DIR` (or at least one) are set and point to directories that contain files newer than the catalog. If neither env var is set, no staleness check runs.

Fix: rebuild the catalog:

```bash
claude-wayfinder catalog build \
  --skills-dir "$DISPATCH_SKILLS_DIR" \
  --agents-dir "$DISPATCH_AGENTS_DIR" \
  --out "$DISPATCH_CATALOG_PATH" \
  --log ~/.claude/dispatch-catalog-build.log
```

#### Decision unexpected

When the matcher returns a decision that does not match your expectation for a given task, inspect the decision at two levels.

**Level 1 — Read the rationale field.** The `rationale` string in the decision JSON names the specific triggers and weights that fired. It will tell you which keyword, path glob, or tool mention matched (or did not match) and which agent or skill scored highest.

**Level 2 — Inspect the catalog entry.** Open your catalog JSON at `$DISPATCH_CATALOG_PATH` and locate the entry for the agent or skill in question. The `triggers` block contains the keywords, path globs, tool names, and command prefixes that are scored against the dispatch context. Compare against the features you sent.

To inspect how the matcher evaluates a dispatch context, pipe a JSON object to `claude-wayfinder-match`. This command is a direct alias for `claude-wayfinder dispatch` — both read dispatch-context JSON from stdin and write decision JSON to stdout — and is convenient in shell pipelines where a shorter command is preferred:

```bash
echo '{"task_description": "implement auth module", "file_paths": ["src/auth.py"]}' \
  | DISPATCH_CATALOG_PATH=~/.claude/dispatch-catalog.json \
    claude-wayfinder-match
```

The `--catalog-path` flag can replace the env var: `claude-wayfinder-match --catalog-path /path/to/catalog.json`. Both entry points are documented in [`docs/api.md` — CLI entry points](api.md#cli-entry-points).

This returns the same decision JSON the router would receive. Adjust the dispatch context fields until the output matches the decision you expect, then verify that your router's composition step is producing equivalent context.

**No `--verbose` flag exists** in the current CLI. Feature-level inspection is available through the Python API (`build_features`, `score` from `claude_wayfinder`) if you need lower-level debugging — see [`docs/api.md`](api.md).

---

## Plugin manifest validation

`claude plugin validate` is the canonical check for `.claude-plugin/plugin.json`. It enforces the official schema — including `hooks/hooks.json` structure, `userConfig` blocks, and component-path overrides — and runs as a CI gate on every push and PR inside the `Validate Plugin Manifest` job (`.github/workflows/ci.yml`).

The project also ships `tests/test_plugin_manifests.py`, which covers field-level conventions the official validator does not (name, description, author, keywords). Both checks are complementary: the official validator knows the full schema; the homegrown test enforces project-specific field requirements.

To run locally (requires `@anthropic-ai/claude-code` installed globally):

```bash
claude plugin validate .claude-plugin/plugin.json
```

---

## Optional integrations

Operational extras. Reach for these once your core integration is working.

### Bundled hooks

The plugin ships five Claude Code hooks in `hooks/`. Once installed via `/plugin install`, they fire automatically — no manual wiring required.

| Event | Script | Purpose |
|---|---|---|
| `SessionStart` | `check-catalog-health.js` | Emit `[CATALOG ERROR]` or `[CATALOG STALE]` banner when the catalog is missing, empty, unparseable, or older than a source file. |
| `UserPromptSubmit` | `refresh-catalog-on-stale.js` | Auto-rebuild the catalog when a source file is newer or the current project has changed since the last build. Emits `[CATALOG REFRESH FAILED]` on generator error but never blocks the prompt. |
| `PreToolUse (Agent)` | `log-agent-dispatch.js` | Append an `agent_dispatch` event to `~/.claude/state/dispatch-log.jsonl` for every Agent tool call. |
| `PreToolUse (Agent)` | `check-agent-dispatch-pairing.js` | Classify each Agent call as `router_mediated`, `skill_mediated`, `bypass`, or `stale_dispatch`; write drift events to `router-drift.jsonl` for non-router-mediated cases. |
| `Stop` | `router-drift-scanner.js` | Scan the completed session transcript and append five additional drift event types (`advisory_override`, `self_handle_unaided_invocation`, `needs_more_detail_repeat`, `catalog_degraded_session`, `skill_mediated_delegation`) to `router-drift.jsonl`. |

All hooks:
- Exit 0 in all conditions — none ever block a session.
- Write only to `~/.claude/state/` (or paths overridden by env vars). No project files are modified.
- Accept env var overrides for testing (see each script's header for the full list).

**Required env var for catalog hooks:** `DISPATCH_CATALOG_PATH` must be set and point to a valid catalog (see [§2](#2-configure-dispatch_catalog_path)). Without it, the health check and auto-refresh hooks will report a catalog error on every session start.

---

### Catalog refresh — pre-commit hook

The catalog must be rebuilt whenever your skill or agent frontmatter changes. Add a git hook that regenerates the catalog when skill or agent files change. Using [pre-commit](https://pre-commit.com/):

```yaml
# .pre-commit-config.yaml
repos:
  - repo: local
    hooks:
      - id: wayfinder-catalog-build
        name: Rebuild dispatch catalog
        language: system
        entry: claude-wayfinder catalog build
        args:
          - --skills-dir
          - ~/.claude/skills
          - --agents-dir
          - ~/.claude/agents
          - --out
          - ~/.claude/dispatch-catalog.json
          - --log
          - ~/.claude/dispatch-catalog-build.log
        files: '(SKILL\.md|agents/.*\.md)$'
        pass_filenames: false
```

Or as a bare git hook in `.git/hooks/pre-commit`:

```bash
#!/usr/bin/env bash
# Regenerate dispatch catalog when skill/agent frontmatter changes.
if git diff --cached --name-only | grep -qE '(SKILL\.md|agents/.*\.md)$'; then
  claude-wayfinder catalog build \
    --skills-dir ~/.claude/skills \
    --agents-dir ~/.claude/agents \
    --out ~/.claude/dispatch-catalog.json \
    --log ~/.claude/dispatch-catalog-build.log
fi
```

Make the file executable: `chmod +x .git/hooks/pre-commit`

### Catalog refresh — GitHub Actions CI job

Add a step to your CI pipeline that rebuilds and validates the catalog on pull requests touching skill or agent files:

```yaml
# .github/workflows/catalog.yml
name: Catalog build

on:
  pull_request:
    paths:
      - '**/SKILL.md'
      - '**/agents/*.md'

jobs:
  build-catalog:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install claude-wayfinder
        run: pip install claude-wayfinder

      - name: Build dispatch catalog
        run: |
          claude-wayfinder catalog build \
            --skills-dir ~/.claude/skills \
            --agents-dir ~/.claude/agents \
            --out /tmp/dispatch-catalog.json \
            --log /tmp/dispatch-catalog-build.log

      - name: Upload catalog artifact
        uses: actions/upload-artifact@v4
        with:
          name: dispatch-catalog
          path: |
            /tmp/dispatch-catalog.json
            /tmp/dispatch-catalog-build.log
```

Adjust `--skills-dir` and `--agents-dir` to the paths relevant to your CI environment. If your skills and agents live inside the repo, use repo-relative paths (e.g. `${{ github.workspace }}/.claude/skills`).

### Catalog refresh — manual command

Run the same `catalog build` command used during initial setup:

```bash
claude-wayfinder catalog build \
  --skills-dir ~/.claude/skills \
  --agents-dir ~/.claude/agents \
  --out ~/.claude/dispatch-catalog.json \
  --log ~/.claude/dispatch-catalog-build.log
```

### Catalog refresh — `/refresh-catalog` skill

The plugin bundles a `/refresh-catalog` skill that wraps the manual command above with mtime-before/after capture, entry counting by `kind`, and warning-extraction from the build log. Trigger it via the slash command or natural-language phrasings like "regenerate catalog", "rebuild dispatch catalog", or "refresh the catalog". The skill is registered in the dispatch catalog via its own `triggers.yml` sidecar — see `skills/refresh-catalog/`.

Use this when the auto-refresh hook misses a source change (e.g. a sidecar edit that does not bump a watched path's mtime), when diagnosing a `[CATALOG ERROR]` or `[CATALOG STALE]` banner, or when you want a structured before/after report rather than just running `catalog build` and reading the exit code.

### Drift telemetry

After deploying the dispatch loop, the matcher's observability layer tracks routing decisions against actual tool-use behavior. The signal this produces — drift events — tells you whether the router is following the decisions the matcher returns.

The telemetry design, drift event types, action thresholds, and the health checker (`src/claude_wayfinder/_health.py`) are documented in full in:

[`docs/schema.md` — Observability (§5)](schema.md#5-observability)

Key points:

- **Seven drift event types** are tracked across two hooks:
  - `bypass` and `stale_dispatch` — written by `check-agent-dispatch-pairing.js` (PreToolUse) as each Agent call is classified.
  - `advisory_override`, `self_handle_unaided_invocation`, `needs_more_detail_repeat`, `catalog_degraded_session`, and `skill_mediated_delegation` — written by `router-drift-scanner.js` (Stop) by scanning the completed session transcript.
- All events are appended as JSONL lines to `~/.claude/state/router-drift.jsonl`.
- The session recap surfaces a recent drift summary; the health checker provides a full report on demand.
- Action thresholds by drift type are defined in §3.3.3. `catalog_degraded_session` events warrant immediate action; others are informational until thresholds are exceeded.
- **Staleness is not an error.** When any source file is newer than the catalog, the `check-catalog-health.js` hook emits a `[CATALOG STALE]` banner and the `refresh-catalog-on-stale.js` hook triggers a rebuild. Neither hook blocks session start or prompt submission.

---

## Auditing the dispatch catalog

Run `audit-catalog` before every release and as a pre-commit check whenever you modify skill sidecars or agent frontmatter — it catches structural and matcher-aware semantic problems that are difficult to spot by reading individual files.

| Exit | Meaning | Typical use case |
| ---- | ------- | --------------- |
| 0 | No findings (after severity/target filtering) | Clean catalog; safe to merge. |
| 1 | NIT findings only | Development-loop noise gate; acceptable before shipping. |
| 2 | CONCERN findings present (no BLOCKING) | Review before shipping, but not a hard block. |
| 3 | BLOCKING findings present | CI hard gate; do not merge until resolved. |

Use `--severity blocking` as the CI gate to fail only on structural violations that the matcher cannot work around:

```bash
# CI gate — fail only on BLOCKING findings
python -m claude_wayfinder audit-catalog --severity blocking

# Full report — all findings, machine-readable
python -m claude_wayfinder audit-catalog --json
```

See [`docs/dispatch-authoring-guide.md`](dispatch-authoring-guide.md) for the complete rule reference and worked examples.

---

## Cross-references

- **Schema documentation** — the catalog entry schema, dispatch context schema (5 input fields), and decision output schema (7 decision types) are documented in [`docs/schema.md`](schema.md).
- **Algorithm specification** — [`docs/schema.md §4`](schema.md#4-scoring-and-decision-algorithm)
- **Design rationale** — [`docs/design.md`](design.md)
- **v0.2 integration design rationale** — [`docs/design/2026-05-14-v0.2-integration-design.md`](design/2026-05-14-v0.2-integration-design.md)
- **Library API** — [`docs/api.md`](api.md)
