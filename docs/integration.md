# Integration Guide — claude-wayfinder

This guide is for consumers who want to use claude-wayfinder as the actual dispatch layer in their router agent, not just evaluate the demo. It assumes you have a Claude Code router agent in place and want to replace (or augment) its prose-policy routing with deterministic decisions from the matcher.

**Prerequisites:**

- Python >= 3.11 on your `$PATH`
- `claude-wayfinder` installed:  `pip install claude-wayfinder` or `pip install -e ".[dev]"` from a clone
- The `/dispatch` skill installed in your Claude Code environment (via `/plugin install`)

---

## 1. One-time catalog build

The matcher operates against a catalog you build from your own skill and agent frontmatter. There are no defaults for the output path or log path — both must be supplied explicitly.

The `catalog build` subcommand reads `SKILL.md` files and agent frontmatter `.md` files, applies source-tagged precedence, and writes a `dispatch-catalog.json` and a build log.

### User-scope-only sources

Catalog built from user-level skills and agents only (nothing project-local):

```bash
claude-wayfinder catalog build \
  --skills-dir ~/.claude/skills \
  --agents-dir ~/.claude/agents \
  --out ~/.claude/dispatch-catalog.json \
  --log ~/.claude/dispatch-catalog-build.log
```

### User-scope with project-local overlay

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

### User-scope with plugin overrides

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

## 2. Router-agent prompt snippet

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

The skill reads `$DISPATCH_CATALOG_PATH` from the environment to locate your
catalog. With the env var set and a valid catalog present, it returns the
matcher's decision JSON on stdout. If `$DISPATCH_CATALOG_PATH` is not set,
the skill runs in demo mode (bundled fixtures) — that is not routing your task.

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

**`ambiguous`** — Two or more agents tied above the scoring floor (gap < 0.2).
Present the candidates from the `alternatives` field to the user and ask them
to choose. Do not pick one unilaterally.

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

## 3. Tools-frontmatter prerequisite

Your router agent must include `Skill` in its `tools:` frontmatter for `/dispatch` to be invocable. Example of correct frontmatter:

```
tools: Glob, Grep, Read, Edit, Write, Bash, Skill, ToolSearch
```

Without `Skill` in the tools list, the `/dispatch` slash command is not available to the agent and the dispatch loop cannot run.

---

## 4. Catalog refresh

The catalog must be rebuilt whenever your skill or agent frontmatter changes. Three patterns are supported.

### Manual refresh

Run the same `catalog build` command used during initial setup:

```bash
claude-wayfinder catalog build \
  --skills-dir ~/.claude/skills \
  --agents-dir ~/.claude/agents \
  --out ~/.claude/dispatch-catalog.json \
  --log ~/.claude/dispatch-catalog-build.log
```

### Pre-commit hook

Add a git hook that regenerates the catalog when skill or agent files change. Using [pre-commit](https://pre-commit.com/):

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

### CI job

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

---

## 5. Drift telemetry

After deploying the dispatch loop, the matcher's observability layer tracks routing decisions against actual tool-use behavior. The signal this produces — drift events — tells you whether the router is following the decisions the matcher returns.

The telemetry design, drift event types, action thresholds, and the health checker (`src/claude_wayfinder/_health.py`) are documented in full in:

[`docs/design/2026-04-30-deterministic-first-router-design-v5.md` — Layer 3: Drift telemetry (§3.3)](docs/design/2026-04-30-deterministic-first-router-design-v5.md)

Key points:

- Drift events are written to a log (`router-drift.jsonl`) by a Stop hook.
- The session recap surfaces a recent drift summary; the health checker provides a full report on demand.
- Action thresholds by drift type are defined in §3.3.3. `catalog_degraded_session` events warrant immediate action; others are informational until thresholds are exceeded.
- **Staleness is not an error.** When `$DISPATCH_SKILLS_DIR` and/or `$DISPATCH_AGENTS_DIR` are set and any source file is newer than the catalog, the skill emits a `[DISPATCH WARNING]` to stderr and proceeds. Rebuild the catalog to clear the warning.

---

## Troubleshooting

### Catalog missing

**Symptom:** The skill emits a `[CATALOG ERROR]` banner on stderr and exits non-zero. The decision JSON is not produced. Routing falls back to LLM judgment.

**Cause — env var not set:** `$DISPATCH_CATALOG_PATH` is absent from the environment. The skill runs in demo mode (bundled fixtures) instead of routing your task. Demo mode prints `no catalog configured — running in demo mode` to stdout.

Fix: set the env var before starting Claude Code:

```bash
export DISPATCH_CATALOG_PATH=~/.claude/dispatch-catalog.json
```

Or add it to your shell profile / Claude Code env configuration so it is available in every session.

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

### Catalog stale

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

### Decision unexpected

When the matcher returns a decision that does not match your expectation for a given task, inspect the decision at two levels.

**Level 1 — Read the rationale field.** The `rationale` string in the decision JSON names the specific triggers and weights that fired. It will tell you which keyword, path glob, or tool mention matched (or did not match) and which agent or skill scored highest.

**Level 2 — Inspect the catalog entry.** Open your catalog JSON at `$DISPATCH_CATALOG_PATH` and locate the entry for the agent or skill in question. The `triggers` block contains the keywords, path globs, tool names, and command prefixes that are scored against the dispatch context. Compare against the features you sent.

To inspect how features are extracted from your dispatch context, run the matcher directly against `claude-wayfinder-match` (the lower-level entry point) and examine the output:

```bash
echo '{"task_description": "implement auth module", "file_paths": ["src/auth.py"], "agent_mentions": [], "tool_mentions": [], "command_prefix": null}' \
  | DISPATCH_CATALOG_PATH=~/.claude/dispatch-catalog.json \
    claude-wayfinder dispatch
```

This returns the same decision JSON the router would receive. Adjust the dispatch context fields until the output matches the decision you expect, then verify that your router's composition step is producing equivalent context.

**No `--verbose` flag exists** in the current CLI. Feature-level inspection is available through the Python API (`build_features`, `score` from `claude_wayfinder`) if you need lower-level debugging — see [`docs/api.md`](api.md).

---

## Cross-references

- **Schema documentation** — the catalog entry schema, dispatch context schema (5 input fields), and decision output schema (7 decision types) are documented in [`docs/schema.md`](schema.md).
- **Algorithm specification** — [`docs/design/2026-04-30-deterministic-first-router-design-v5.md`](design/2026-04-30-deterministic-first-router-design-v5.md)
- **v0.2 integration design rationale** — [`docs/design/2026-05-14-v0.2-integration-design.md`](design/2026-05-14-v0.2-integration-design.md)
- **Library API** — [`docs/api.md`](api.md)
