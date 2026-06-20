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

The consumer's router agent must compose a JSON object and pass it on
stdin. `task_description` is the only required field:

```json
{
  "task_description": "...",
  "file_paths": ["..."],
  "agent_mentions": ["..."],
  "tool_mentions": ["..."],
  "command_prefix": "...",
  "session_id": "...",
  "domain": "code",
  "posture": "build",
  "confidence": "high",
  "area_span": 1
}
```

All fields except `task_description` are optional; omit or pass `null`
for fields that are not applicable. The four two-axis labels (`domain`,
`posture`, `confidence`, `area_span`) are documented under **Two-axis
labels** below.

`session_id` (optional string, added in fix #294, auto-populated in
#296) — the Claude Code session identifier for the calling session.
When present, this value is written verbatim into the `matcher_decision`
log entry, enabling per-session attribution in the dispatch log.

**Auto-population (issue #296):** when `session_id` is absent from the
input JSON and the `CLAUDE_SESSION_ID` env var is not set, the matcher
automatically walks its ancestor process chain looking for a PID-keyed
state file written by the `session-start-record-session` hook. The hook
fires at SessionStart, captures the CC process's PID and start time
(`psutil.Process(ppid).create_time()`), and writes
`~/.claude/state/wayfinder-sessions/<ppid>-<create_time_int>.txt`. The
matcher finds its own CC ancestor's file and reads the session_id from
it. The result is cached for the matcher process's lifetime.

**Concurrent-session safety:** each CC session gets a unique file keyed
by both PID and integer create_time. Two concurrent CC sessions write
two separate files; the matcher's process-tree walk reaches only its own
CC ancestor's PID, so there is no cross-contamination. Do **not**
simplify this to a single shared file — a shared file is broken under
concurrent sessions (each SessionStart overwrites the prior session's
ID). The per-file-per-session design is load-bearing.

## Two-axis labels (Matcher v3)

Four optional **caller-supplied labels** the matcher consumes for two-axis
(domain × posture) routing. The matcher runs no encoder — it routes on exactly
what the caller labels — so these are the only signals it cannot derive
lexically. They describe the **task**, not a target agent: the matcher resolves
domain/posture to an agent internally (via its own routing policy), so the caller
never names an agent here.

**Caller: classify each task into these four labels and include them in the
context JSON you compose** — apply the rubric below and obey the `confidence`
fail-safe (never label `high` on a guess). Label only what the task _is_.

All four are **optional and additive** — the matcher ignores absent or unknown
fields, so emitting them never changes current behavior. While the rollout flag
is off they feed shadow-mode telemetry only (the v3 route is computed and logged
beside the live lexical decision); `high`-confidence labels begin steering live
routes only after the flag flip.

| Field | Type | Default when absent |
| --- | --- | --- |
| `domain` | enum / `null` | `is_any` (no domain gate) |
| `posture` | enum / `null` | no posture route |
| `confidence` | `high` \| `medium` \| `low` / `null` | **`low`** (fail-safe) |
| `area_span` | integer ≥ 1 | `1` |

### `domain` — what kind of artifact/area the task touches

| Value | Task is about |
| --- | --- |
| `code` | source code: features, fixes, refactors, tests (`.py` / `.ts` / …) |
| `docs_prose` | documentation, READMEs, ADRs, plans, specs, changelogs |
| `project_meta` | harness self-edits — `agents/**`, `skills/**/SKILL.md`, `CLAUDE.md`, `AGENTS.md`, `GEMINI.md`, project governance |
| `infra_deploy` | infrastructure, deployment, IaC (bicep/terraform), CI/CD, topology |
| `is_any` / `null` | cross-cutting, or no single domain dominates (no domain gate) |

### `posture` — what action the task performs

| Value | Intent |
| --- | --- |
| `build` | create / implement / add new behavior |
| `diagnose` | find the root cause of a failure / bug |
| `assess` | review / evaluate existing code quality |
| `critique` | adversarial critique (architecture or idea soundness) |
| `verify` | conformance / consistency check vs. a stated source of truth |
| `plan` | scope / design / requirements |
| `research` | prior-art discovery before planning |
| `operate` | read-only operations (e.g. status / read queries) |

### `confidence` — fail-safe; never label `high` on a guess

How sure the caller is of the `domain` + `posture` pair. The fail-safe governs
**delegate** posture-routes: the matcher hard-routes to a preferred agent **only
on `high`**; `medium` / `low` / absent fall through to the lexical scorer. A wrong
`high` is the one label that can mis-steer a live delegate, so when unsure, omit
`confidence` (⇒ treated as `low`) rather than inventing a level.

**Exception:** a harness self-edit (`domain: project_meta`, `posture: build`)
abstains to the router (`self_handle`) **regardless of confidence** — that
abstention is not a delegate, so the `high`-gate does not apply.

### `area_span` — default 1

Number of distinct layers/areas the task genuinely spans (e.g. code + infra +
data = 3). Defaults to `1`. Emit `≥ 2` **only** for genuinely multi-layer work — a
single-file bug is `1`; an outage spanning service + database + config is `≥ 2`.
Values are coerced to `int`; anything missing, non-numeric, or `< 1` becomes `1`.

## Output schema (both modes)

Real-catalog mode returns the matcher's decision JSON verbatim on stdout:

```json
{
  "decision":     "delegate",
  "agent":        "Explore",
  "skills":       ["python"],
  "confidence":   0.92,
  "rationale":    "matched keywords: implement.",
  "alternatives": [{"agent": "Plan", "score": 0.4}]
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
