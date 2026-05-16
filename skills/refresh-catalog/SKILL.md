---
name: refresh-catalog
description: >
  Manually regenerate the dispatch catalog from skill sidecars and agent
  frontmatter. Trigger this skill whenever the user types /refresh-catalog,
  says "regenerate catalog", "refresh dispatch catalog", "rebuild catalog",
  "update the catalog", or any similar request to force a fresh dispatch
  catalog build outside of the automatic SessionStart or mtime-check paths.
---

# /refresh-catalog

Force a fresh regeneration of the dispatch catalog and report the results.

## When to use this skill

The bundled `refresh-catalog-on-stale.js` hook (see [Bundled hooks](../../docs/integration.md#bundled-hooks)) automatically rebuilds the catalog when a source file (skill sidecar or agent frontmatter) is newer than the catalog itself. This skill exists for cases the mtime heuristic misses or when you want to force a rebuild for debugging:

- A sidecar edit did not bump a watched path's mtime.
- You edited the catalog directly and want to overwrite with a clean rebuild.
- You are diagnosing a `[CATALOG ERROR]` or `[CATALOG STALE]` banner.

## Step 1: Record the catalog mtime before regeneration

Read the mtime of the catalog before running the generator. The catalog path is resolved from `$DISPATCH_CATALOG_PATH` if set, otherwise the documented default `~/.claude/dispatch-catalog.json`. If the file does not exist, note "catalog absent before run".

```bash
python -c "
import os, datetime
p = os.environ.get('DISPATCH_CATALOG_PATH') or os.path.expanduser('~/.claude/dispatch-catalog.json')
if os.path.exists(p):
    t = os.path.getmtime(p)
    print(datetime.datetime.fromtimestamp(t).isoformat(timespec='seconds'))
else:
    print('absent')
"
```

## Step 2: Run the catalog generator

Invoke `claude-wayfinder catalog build` with the consumer's source directories. The default layout follows Claude Code's standard `~/.claude/` tree; adjust the flags if your project uses non-standard paths.

```bash
claude-wayfinder catalog build \
  --skills-dir ~/.claude/skills \
  --agents-dir ~/.claude/agents \
  --plugin-overrides-dir ~/.claude/triggers \
  --plugins-dir ~/.claude/plugins \
  --builtin-agents-dir ~/.claude/triggers/builtin \
  --out "${DISPATCH_CATALOG_PATH:-$HOME/.claude/dispatch-catalog.json}" \
  --log ~/.claude/dispatch-catalog-build.log
```

Capture both stdout and stderr. Record the exit code.

- **Exit 0** — clean generation.
- **Exit 1** — fatal error; the catalog may not have been written.
- **Exit 2** — degraded catalog; a partial file was written but one or more entries were excluded due to validation issues.

If the generator exits non-zero, surface the error visibly:

> **Catalog generation FAILED (exit {N})**
>
> ```
> {captured stderr}
> ```
>
> Check `~/.claude/dispatch-catalog-build.log` for per-entry details.

## Step 3: Record the catalog mtime after regeneration

Read the mtime again using the same command as Step 1.

## Step 4: Parse the new catalog

Read the catalog file. The top-level structure is:

```json
{ "entries": [ { "kind": "skill" | "agent", ... }, ... ] }
```

Count entries by `kind`:

- **skills** — entries where `kind == "skill"`
- **agents** — entries where `kind == "agent"`

## Step 5: Check the generation log for warnings

Read `~/.claude/dispatch-catalog-build.log` and extract any lines containing `warning` or `fatal` (case-insensitive). Show only the lines from the most recent run (lines after the last generator-invocation timestamp that precedes the run).

If there are no warning/fatal lines, report "No warnings or fatal issues."

## Step 6: Report

Print a structured summary:

```
Catalog regenerated successfully.   ← or "DEGRADED (exit 2)" / "FAILED (exit 1)"

  Skills:  <N>
  Agents:  <M>
  Total:   <N+M>

  Catalog mtime before: <ISO timestamp or "absent">
  Catalog mtime after:  <ISO timestamp>

  Exclusion warnings (from dispatch-catalog-build.log):
  <warning/fatal lines, or "None">
```

If exit code was 2, prepend the summary with a visible banner:

> **WARNING: Catalog is degraded — some entries were excluded due to validation
> errors. Review the warnings above and fix the affected `triggers.yml` or
> agent frontmatter files.**
