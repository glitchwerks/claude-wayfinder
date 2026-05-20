---
name: router-health
description: >
  Reports the deterministic-dispatch router's health — CI invariants, runtime
  telemetry, drift events, and catalog state. Wraps `claude-wayfinder health
  --report` and adds an Analysis section (drill-down on FAIL'ing or
  near-threshold metrics) plus an extended Notable Findings section (top
  dispatched agents/skills, catalog freshness).

  Trigger phrases: "/router-health", "router health", "dispatch health",
  "router status", "is the router healthy", "router metrics", "show router
  stats", "router report", "check the router", "router drift".
---

# Router Health Skill

You are reporting on the health of the deterministic-dispatch router. This is
a **read-only** skill — never mutate state, logs, or the catalog.

## What this skill does

1. Runs `python -m claude_wayfinder health --report`.
2. Prints the report verbatim.
3. Adds an **Analysis** section with drill-down on any FAIL'ing or
   near-threshold metric.
4. Adds an extended **Notable Findings** section with qualitative
   observations from `dispatch-log.jsonl` (top agents, top skills, catalog
   freshness) that complement the script's built-in Notable Findings.

If invoked with `--brief`, skip step 2 and produce only Analysis + extended
Notable Findings. Useful for mid-session quick checks where the user does
not need the full report.

## Path resolution

The script (`claude_wayfinder._health.main`) requires explicit paths — there
are no `~/.claude/...` defaults baked in. Resolve each path in this order:

| Argument                  | Env var override         | Default (when env var absent)                |
| ------------------------- | ------------------------ | -------------------------------------------- |
| `--drift-log`             | `$ROUTER_DRIFT_LOG`      | `$HOME/.claude/state/router-drift.jsonl`     |
| `--dispatch-log`          | `$DISPATCH_LOG`          | `$HOME/.claude/state/dispatch-log.jsonl`     |
| `--catalog-path`          | `$DISPATCH_CATALOG_PATH` | _(omit; catalog section will be empty)_      |
| `--skills-dir`            | `$ROUTER_SKILLS_DIR`     | `$HOME/.claude/skills`                       |
| `--agents-dir`            | `$ROUTER_AGENTS_DIR`     | `$HOME/.claude/agents`                       |
| `--plugin-overrides-dir`  | `$ROUTER_PLUGIN_OVERRIDES_DIR` | `$HOME/.claude/triggers`               |

Absent log files are treated as empty (telemetry sections render with zero
events). Missing skills/agents/overrides directories produce a FAIL on the
corresponding CI invariants, which is the correct signal.

## Step 1: Run the report

Use the plugin-installed `claude-wayfinder` package via `python -m`. This
assumes the plugin venv's `python` is on `$PATH` (the standard setup after
`/setup-wayfinder`). If the user's shell does not have the venv activated,
substitute the absolute interpreter path:
`${CLAUDE_PLUGIN_DATA}/venv/bin/python` (POSIX) or
`${CLAUDE_PLUGIN_DATA}/venv/Scripts/python.exe` (Windows).

Bash invocation:

```bash
python -m claude_wayfinder health --report \
  --drift-log    "${ROUTER_DRIFT_LOG:-$HOME/.claude/state/router-drift.jsonl}" \
  --dispatch-log "${DISPATCH_LOG:-$HOME/.claude/state/dispatch-log.jsonl}" \
  --skills-dir   "${ROUTER_SKILLS_DIR:-$HOME/.claude/skills}" \
  --agents-dir   "${ROUTER_AGENTS_DIR:-$HOME/.claude/agents}" \
  --plugin-overrides-dir "${ROUTER_PLUGIN_OVERRIDES_DIR:-$HOME/.claude/triggers}"
```

`--catalog-path` is omitted intentionally — the script falls back to
`$DISPATCH_CATALOG_PATH` when no flag is passed.

PowerShell invocation:

```powershell
python -m claude_wayfinder health --report `
  --drift-log    "$(if ($env:ROUTER_DRIFT_LOG) { $env:ROUTER_DRIFT_LOG } else { "$env:USERPROFILE\.claude\state\router-drift.jsonl" })" `
  --dispatch-log "$(if ($env:DISPATCH_LOG) { $env:DISPATCH_LOG } else { "$env:USERPROFILE\.claude\state\dispatch-log.jsonl" })" `
  --skills-dir   "$(if ($env:ROUTER_SKILLS_DIR) { $env:ROUTER_SKILLS_DIR } else { "$env:USERPROFILE\.claude\skills" })" `
  --agents-dir   "$(if ($env:ROUTER_AGENTS_DIR) { $env:ROUTER_AGENTS_DIR } else { "$env:USERPROFILE\.claude\agents" })" `
  --plugin-overrides-dir "$(if ($env:ROUTER_PLUGIN_OVERRIDES_DIR) { $env:ROUTER_PLUGIN_OVERRIDES_DIR } else { "$env:USERPROFILE\.claude\triggers" })"
```

If `--brief` was passed by the user, capture the output but do not print it
verbatim — only use it as input to Step 2.

## Step 2: Print the report verbatim

Unless `--brief` was passed, emit the captured markdown unchanged. Begin
your response with the report.

## Step 3: Analyze the report

The script already emits an `ACTION REQUIRED` block for FAIL'ing CI
invariants and a `THRESHOLD BREACH` block for FAIL'ing runtime telemetry.
The Analysis section adds **warning-zone** drill-down for metrics that pass
today but are within 80% of the FAIL threshold, plus drift-event
correlation.

Parse each row of the **CI Invariants** and **Runtime Telemetry** tables for
the `Status` column. For every row whose status is **FAIL** OR is within
**80% of the FAIL threshold**, produce a sub-bullet under the Analysis
section explaining the most likely driver.

### How to compute "warning zone"

| Metric                   | Healthy direction | Warning zone (>80% toward FAIL)        |
| ------------------------ | ----------------- | -------------------------------------- |
| Dispatch invocation rate | ≥ 80%             | between 80% and 84%                    |
| Bypass rate              | ≤ 10%             | between 8% and 10%                     |
| Advisory override rate   | ≤ 30%             | between 24% and 30%                    |
| Catalog availability     | = 100%            | any sub-100% reading is already a FAIL |
| Catalog stability        | identical bytes   | byte-difference is FAIL outright       |
| Schema validation        | exit 0, 0 fatal   | any fatal entry is FAIL outright       |
| Trigger firing accuracy  | 10/10 smoke tests | <10/10 is FAIL outright                |

### Drift event shape

Drift events come in two shapes — your queries must handle both:

- **Categorical**: `{"type": "router_drift", "category": "bypass" | "advisory_override" | ...}`
- **Type-tagged**: `{"type": "self_handle_unaided_invocation", "count": N}` (no `category` field)

Use `e.get("category") or e.get("type")` as the canonical event-kind
discriminator in all log queries. Treating `category` as required will
crash on type-tagged events.

### Drill-down playbook per FAIL'ing metric

For **Bypass rate** FAIL or warning, count bypass events grouped by ISO
date (last 30 days):

```bash
python -c "
import json, os, collections, datetime as dt
path = os.environ.get('ROUTER_DRIFT_LOG') or os.path.expanduser('~/.claude/state/router-drift.jsonl')
cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)
by_day = collections.Counter()
by_session = collections.Counter()
try:
    with open(path) as f:
        for line in f:
            e = json.loads(line)
            kind = e.get('category') or e.get('type')
            if kind != 'bypass': continue
            ts = dt.datetime.fromisoformat(e['ts'].replace('Z','+00:00'))
            if ts < cutoff: continue
            by_day[ts.date().isoformat()] += 1
            by_session[e['session_id']] += 1
except FileNotFoundError:
    print(f'No drift log at {path}'); raise SystemExit
print('Bypass events last 30d:')
for d, c in sorted(by_day.items())[-10:]: print(f'  {d}: {c}')
print('Top bypassing sessions:')
for s, c in by_session.most_common(5): print(f'  {s[:8]}...: {c}')
"
```

Report the daily distribution + top-bypassing sessions. If 5+ events come
from the same session, that session is the likely outlier — flag it.
Drift events do NOT carry `subagent_type`; to identify which agent was
bypassed, correlate by `session_id` with `dispatch-log.jsonl`.

For **Advisory override rate** FAIL or warning, filter by `category ==
"advisory_override"`, count by `session_id`, and report the top 3.
Correlate with `dispatch-log.jsonl` to see which agent the router chose
instead of the matcher's advice.

```bash
python -c "
import json, os, collections
path = os.environ.get('ROUTER_DRIFT_LOG') or os.path.expanduser('~/.claude/state/router-drift.jsonl')
cnt = collections.Counter()
with open(path) as f:
    for line in f:
        e = json.loads(line)
        kind = e.get('category') or e.get('type')
        if kind == 'advisory_override':
            cnt[e['session_id']] += 1
for s, c in cnt.most_common(5): print(f'{s[:8]}...: {c}')
"
```

For **Dispatch invocation rate** FAIL or warning, this is the rate at
which the router invoked the dispatch skill before delegating. Low rate
means the router is routing on LLM judgment instead of deterministic
matching. Sample 5 recent `agent_dispatch` events from
`dispatch-log.jsonl` and check whether each was preceded by a
`dispatch_invocation` for the same session within ~30 seconds.

For **Catalog availability** FAIL, any `catalog_degraded_session` event is
immediate action. Print the most recent ones in full (timestamp,
session_id, full event payload) and recommend re-running the catalog
generator (`/refresh-catalog` or `claude-wayfinder catalog build`) and
inspecting the build log.

For **Catalog stability** FAIL, the script ran the catalog builder twice
and got byte-different output — the generator is non-deterministic.
Re-run by hand and diff. Common causes: dict iteration order, timestamp
embedding, unsorted set serialization.

### Recent drift events list

After the per-metric drill-down, surface the **5 most recent drift
events** with timestamp, category, and session-id-prefix:

```bash
python -c "
import json, os
path = os.environ.get('ROUTER_DRIFT_LOG') or os.path.expanduser('~/.claude/state/router-drift.jsonl')
try:
    with open(path) as f:
        lines = f.readlines()[-5:]
except FileNotFoundError:
    print('No drift log — hook may not be firing.'); raise SystemExit
for line in lines:
    e = json.loads(line)
    kind = e.get('category') or e.get('type')
    print(f\"  {e['ts']}  {kind:30s}  {e['session_id'][:8]}...\")
"
```

If there are **zero** recent events, say so explicitly — that is a
positive signal worth surfacing.

## Step 4: Extended Notable Findings

The script's built-in `## Notable Findings` section reports plugin entry
counts. Extend it with:

### Top 3 dispatched agents (last 30 days)

```bash
python -c "
import json, os, collections, datetime as dt
path = os.environ.get('DISPATCH_LOG') or os.path.expanduser('~/.claude/state/dispatch-log.jsonl')
cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)
total = 0
cnt = collections.Counter()
try:
    with open(path) as f:
        for line in f:
            e = json.loads(line)
            if e.get('type') != 'agent_dispatch': continue
            ts = dt.datetime.fromisoformat(e['ts'].replace('Z','+00:00'))
            if ts < cutoff: continue
            cnt[e['agent']] += 1
            total += 1
except FileNotFoundError:
    print(f'No dispatch log at {path}'); raise SystemExit
for a, c in cnt.most_common(3):
    pct = 100.0 * c / total if total else 0.0
    print(f'  {a:24s} {c:4d}  ({pct:.1f}%)')
print(f'  total dispatches: {total}')
"
```

Report as a small table or bulleted list. Flag if any one agent dominates
above 60% — that may indicate the router is over-delegating to a single
specialist, or that the user's workload is genuinely concentrated.

### Top 3 most-invoked skills (last 30 days)

Same shape as agents but counting `skill_invocation` events grouped by
`skill`. The top skill being `dispatch` itself is expected; if any other
skill dominates dispatch's count, that is notable.

### `self_handle_unaided` rate (catalog coverage signal)

The dispatch matcher returns `self_handle_unaided` when the input has
sufficient density (≥ 2 feature dimensions) but no specialist scored. A
rising rate suggests catalog coverage gaps.

For now, since `dispatch-log.jsonl` does not directly tag
`self_handle_unaided`, report this as **best-effort** by counting
`agent_dispatch` events with `agent == "general-purpose"` and an empty
`skills_in_prompt` array — those are the closest live proxy. Caveat the
metric explicitly when reporting it.

### Catalog freshness

Check the modification timestamp on the catalog file (resolved via
`$DISPATCH_CATALOG_PATH`). If more than 14 days stale, flag it — the
catalog should be regenerated whenever `triggers.yml` files change.

```bash
python -c "
import os, datetime as dt
path = os.environ.get('DISPATCH_CATALOG_PATH') or os.path.expanduser('~/.claude/dispatch-catalog.json')
if os.path.exists(path):
    mtime = dt.datetime.fromtimestamp(os.path.getmtime(path), dt.timezone.utc)
    age = (dt.datetime.now(dt.timezone.utc) - mtime).days
    print(f'  {os.path.basename(path)}: {age}d old (mtime {mtime.isoformat()})')
else:
    print(f'  catalog absent at {path}')
"
```

## Output shape

The final response should look like this (markdown):

```markdown
<`claude-wayfinder health --report` output verbatim, including the H1 "# Router Health Report">

## Analysis

<bullet list of FAIL'ing or warning-zone metrics with drill-downs>

### Recent drift events

<5-row list, or "none in the last week">

## Extended Notable Findings

### Top dispatched agents (last 30 days)

<table or bullets>

### Top invoked skills (last 30 days)

<table or bullets>

### `self_handle_unaided` rate (catalog coverage)

<count + caveat>

### Catalog freshness

<one age line>
```

If `--brief`: omit the verbatim report (Step 2) — start the response with
`## Analysis`.

## Tone and discipline

- Be specific and quantitative. Cite exact counts, percentages, session-id
  prefixes, dates.
- Do not invent metrics that the script does not produce. Stick to the
  observed data.
- Do not recommend changes to thresholds — those are codified in
  `src/claude_wayfinder/_health.py` and changing them is a separate
  decision.
- Where a finding suggests action (regenerate catalog, investigate a
  bypass-heavy session), name the next step concretely with the command
  to run.
- If a log file is missing or empty, say so — never silently fall through
  with zero counts that look like "everything is healthy."

## Failure modes

- **Script exits non-zero**: surface stderr verbatim, recommend the user
  run the command directly to see the failure. Do not produce Analysis or
  Notable Findings sections — the source data is unreliable.
- **Log files missing**: if `dispatch-log.jsonl` or `router-drift.jsonl`
  do not exist, the corresponding hooks are not firing. Flag this as a
  dispatch-pipeline drift event in its own right and recommend checking
  the PreToolUse / Stop hooks in the consumer's hook registry.
- **Catalog file missing**: report this as a catalog-availability outage
  and recommend running `/refresh-catalog` or
  `claude-wayfinder catalog build`.
- **`python` not on PATH**: the user has not run `/setup-wayfinder` yet,
  or the plugin venv is not activated. Recommend `/setup-wayfinder` and
  re-running this skill in a fresh session.
