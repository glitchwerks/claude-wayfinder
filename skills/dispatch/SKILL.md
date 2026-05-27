---
name: dispatch
description: >
  Mode-aware dispatch skill for the claude-wayfinder deterministic 7-decision
  matcher. Real-catalog mode is the default — the skill reads dispatch context
  JSON from stdin, runs the matcher against your live catalog, and returns
  the decision JSON verbatim. Pass --demo to opt into bundled fixtures
  instead. Catalog path resolution: --catalog-path flag > $DISPATCH_CATALOG_PATH
  env var > canonical default (~/.claude/state/dispatch-catalog.json). If
  neither --demo nor a resolvable catalog is present the skill emits a
  [CATALOG ERROR] and exits non-zero.
triggers:
  command_prefixes:
    - /dispatch
---

# Dispatch Skill

The `/dispatch` skill is a mode-aware wrapper around the
**claude-wayfinder** deterministic 7-decision matcher.

## Modes

### Real-catalog mode (default)

The skill reads a dispatch context JSON from stdin, runs the matcher
against your live catalog, and returns the decision JSON verbatim. This
is the default — no flag required. Catalog path resolution:

1. `--catalog-path <path>` CLI flag.
2. `$DISPATCH_CATALOG_PATH` env var.
3. **Canonical default** — `$CLAUDE_HOME/state/dispatch-catalog.json` or
   `~/.claude/state/dispatch-catalog.json` (see "Canonical catalog path"
   below).

### Demo mode (`--demo` flag)

Pass `--demo` to run the matcher against bundled demo fixtures instead
of the live catalog. Returns decision output for all 7 routing branches
so you can evaluate the matcher before integrating it into your router.
`--demo` wins over `--catalog-path` and `$DISPATCH_CATALOG_PATH` — if
both are present, demo mode runs and the catalog inputs are ignored.

**Hard-error guarantee:** Without `--demo`, if no catalog can be
resolved (no flag, no env var, no file at the canonical path), or if
the resolved file is missing/unreadable/malformed, the skill emits a
`[CATALOG ERROR]` banner on stderr and exits non-zero. It does **not**
silently fall back to demo mode — a broken or missing catalog is
surfaced immediately so the consumer knows routing is degraded. The
banner names the canonical default path and the repair hint inline.

## Dispatch context JSON (real-catalog mode)

The consumer's router agent must compose a 5-field JSON object and pass
it on stdin:

```json
{
  "task_description": "...",
  "file_paths": ["..."],
  "agent_mentions": ["..."],
  "tool_mentions": ["..."],
  "command_prefix": "..."
}
```

All fields except `task_description` are optional; omit or pass `null`
for fields that are not applicable.

## Output schema (both modes)

Real-catalog mode returns the matcher's decision JSON verbatim on stdout:

```json
{
  "decision":     "delegate",
  "agent":        "code-writer",
  "skills":       ["python"],
  "confidence":   0.92,
  "rationale":    "matched keywords: implement.",
  "alternatives": [{"agent": "devops", "score": 0.4}]
}
```

Demo mode produces human-readable text instead of JSON (one block per
decision branch).  The output schema is identical across the 7 branches
shown in demo mode.  Consumer routers should use real-catalog mode for
machine-readable output.

## Consumer router requirements

The consumer's router agent must include `Skill` in its `tools:` frontmatter
for `/dispatch` to be invocable.  Example of correct frontmatter:

```
tools: Glob, Grep, Read, Edit, Write, Bash, Skill, ToolSearch
```

See `docs/integration.md` for the end-to-end wiring guide.

## Prerequisites

`claude-wayfinder` must be installed in a Python ≥ 3.11 environment. After
running `/setup-wayfinder`, the plugin venv lives at
`${CLAUDE_PLUGIN_DATA}/venv/` — that is the canonical interpreter to use.

**Use the plugin venv's interpreter explicitly.** Do not rely on bare
`python` resolving to the right environment via `$PATH` — on Windows in
particular, a global Python (e.g. `C:\Python313\python.exe`) often takes
precedence over the venv and does NOT have `claude-wayfinder` installed,
producing `No module named claude_wayfinder` at runtime.

| Platform | Path |
|----------|------|
| POSIX    | `${CLAUDE_PLUGIN_DATA}/venv/bin/python` |
| Windows  | `${CLAUDE_PLUGIN_DATA}/venv/Scripts/python.exe` |

The skill's invocations below show the explicit path. Bare `python` is
fine as a shorthand **only** when the plugin venv is activated in the
calling shell, or its `bin/Scripts` dir is first on `$PATH`.

```bash
# Confirm the package is available
"${CLAUDE_PLUGIN_DATA}/venv/Scripts/python.exe" -m claude_wayfinder dispatch --help   # Windows
"${CLAUDE_PLUGIN_DATA}/venv/bin/python" -m claude_wayfinder dispatch --help           # POSIX
```

If `claude-wayfinder` is not installed yet, run `/setup-wayfinder` — that
skill materializes the venv at the canonical location and pins the
matching plugin version into it.

## Canonical catalog path

The live catalog is at **`~/.claude/state/dispatch-catalog.json`** (or
`$CLAUDE_HOME/state/dispatch-catalog.json` when `$CLAUDE_HOME` is set).
This is the default real-catalog mode resolves to when neither
`--catalog-path` nor `$DISPATCH_CATALOG_PATH` is supplied. Override only
for test fixtures or unusual deployments. The bundled hooks
(`refresh-catalog-on-stale.js`, `check-catalog-health.js`) use the same
default.

## Running

```bash
PY="${CLAUDE_PLUGIN_DATA}/venv/Scripts/python.exe"   # Windows
# PY="${CLAUDE_PLUGIN_DATA}/venv/bin/python"          # POSIX

# Real-catalog mode — default; resolves to the canonical catalog
echo '{"task_description": "implement auth module", "file_paths": ["src/auth.py"], "agent_mentions": [], "tool_mentions": [], "command_prefix": null}' \
  | "$PY" -m claude_wayfinder dispatch

# Demo mode — opt in with --demo
"$PY" -m claude_wayfinder dispatch --demo

# Explicit catalog override (e.g. test fixture)
export DISPATCH_CATALOG_PATH=/path/to/test-catalog.json   # POSIX
# $env:DISPATCH_CATALOG_PATH = "C:\path\to\test-catalog.json"  # PowerShell
echo '{...}' | "$PY" -m claude_wayfinder dispatch
```

## Stale-catalog warning

When `$DISPATCH_SKILLS_DIR` and/or `$DISPATCH_AGENTS_DIR` are set and any
source file within them has a modification time newer than the catalog
file, the skill emits a warning to stderr:

```
[DISPATCH WARNING] Catalog mtime is older than source files: ...
Consider running `claude-wayfinder catalog build` to refresh.
Proceeding with stale catalog.
```

Execution **proceeds** with the stale catalog — staleness is a
degraded-quality signal, not an error.  Run
`"$PY" -m claude_wayfinder catalog build` (using the same explicit
interpreter path resolved in the **Running** section above) to refresh.

## The 7 decision branches

| Branch              | When it fires                                                   |
|---------------------|-----------------------------------------------------------------|
| `needs_more_detail` | Feature density < 2; provide more context to route accurately. |
| `delegate`          | One agent scores ≥ 0.85 with a gap ≥ 0.2 above the next.      |
| `self_handle`       | At least one skill scores ≥ 0.5; no dominant agent.            |
| `mixed_content`     | Gap < 0.2; ≥ 2 agents clamped at 1.0 on path-disjoint lanes. Output includes `lanes[]` (agent, score, matched_paths, skills per lane) and `unassigned_paths[]`. |
| `advisory`          | Best agent ≥ 0.5. Covers both tie (gap < 0.2, rationale includes `gap=`) and marginal (gap ≥ 0.2 but score < 0.85) cases. Top agent named; alternatives populated. |
| `ask_user`          | Reserved — not produced by the v0.1 matcher.                   |
| `self_handle_unaided` | Nothing scores above threshold; proceed without delegation. |
