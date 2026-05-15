---
name: dispatch
description: >
  Mode-aware dispatch skill for the claude-wayfinder deterministic 7-decision
  matcher. Operates in one of two modes depending on whether
  $DISPATCH_CATALOG_PATH is set in the environment:

    - Demo mode (env var absent): runs the matcher against bundled demo
      fixtures and returns decision output for all 7 routing branches.
      Use this to evaluate the matcher before integrating into your router.

    - Real-catalog mode (env var set to a valid catalog): reads a dispatch
      context JSON from stdin, runs the matcher against your live catalog,
      and returns the decision JSON verbatim. Use this in your router agent.

  The mode is implicit — detected by the presence of $DISPATCH_CATALOG_PATH.
  The caller never passes a mode flag.
triggers:
  command_prefixes:
    - /dispatch
---

# Dispatch Skill

The `/dispatch` skill is a mode-aware wrapper around the
**claude-wayfinder** deterministic 7-decision matcher.  The active mode is
determined by the environment — not by any flag the caller supplies.

## Modes

### Demo mode (default — no catalog configured)

When `$DISPATCH_CATALOG_PATH` is **not** set, the skill runs the matcher
against bundled demo fixtures.  A `no catalog configured — running in demo
mode` banner is printed first so the output is clearly labelled.  This
mode is useful for evaluating the matcher before integrating it into a
live router.

### Real-catalog mode

When `$DISPATCH_CATALOG_PATH` is set and resolves to a readable, valid
catalog, the skill reads a dispatch context JSON from stdin, passes it
to the matcher, and returns the matcher's decision JSON verbatim.

**Hard-error guarantee:** If `$DISPATCH_CATALOG_PATH` is set but the path
is missing, unreadable, or contains invalid JSON, the skill emits a
`[CATALOG ERROR]` banner on stderr and exits non-zero.  It does **not**
fall back to demo mode — a broken catalog is surfaced immediately so the
consumer knows routing is degraded.

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

Python ≥ 3.11 must be on your `$PATH` and `claude-wayfinder` must be
installed:

```bash
# Confirm the package is available
python -m claude_wayfinder dispatch --help

# Install if needed
git clone https://github.com/glitchwerks/claude-wayfinder.git
cd claude-wayfinder
pip install -e ".[dev]"
```

## Running

```bash
# Demo mode (no catalog configured)
python -m claude_wayfinder dispatch

# Real-catalog mode
export DISPATCH_CATALOG_PATH=/path/to/dispatch-catalog.json
echo '{"task_description": "implement auth module", "file_paths": ["src/auth.py"], "agent_mentions": [], "tool_mentions": [], "command_prefix": null}' \
  | python -m claude_wayfinder dispatch
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
`python -m claude_wayfinder catalog build` to refresh.

## The 7 decision branches

| Branch              | When it fires                                                   |
|---------------------|-----------------------------------------------------------------|
| `delegate`          | One agent scores ≥ 0.85 with a gap ≥ 0.2 above the next.      |
| `self_handle`       | At least one skill scores ≥ 0.5; no dominant agent.            |
| `self_handle_unaided` | Nothing scores above threshold; proceed without delegation. |
| `advisory`          | Best agent ≥ 0.5 but not conclusive; suggested, not required.  |
| `ambiguous`         | Multiple agents score ≥ 0.5 with gap < 0.2; needs tiebreak.   |
| `ask_user`          | Reserved — not produced by the v0.1 matcher.                   |
| `needs_more_detail` | Feature density < 2; provide more context to route accurately. |
