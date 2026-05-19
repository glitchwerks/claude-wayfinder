---
title: Dispatch telemetry enrichment design
date: 2026-05-18
status: draft
tracking: glitchwerks/claude-wayfinder#143
touches:
  - src/claude_wayfinder/_dispatch.py
  - src/claude_wayfinder/match.py
  - src/claude_wayfinder/cli.py
  - src/claude_wayfinder/build_catalog.py
  - agents/general-purpose.md             # audit-line format: add decision_id
  - hooks/lib/router-drift-scanner.js     # Stop-hook enrichment for advisory_override
  - hooks/check-agent-dispatch-pairing.js # PreToolUse: embed raw_input in bypass/skill_mediated
  - docs/design/telemetry-schema.md       # new file
skills_relevant:
  - python
  - superpowers:test-driven-development
related:
  - glitchwerks/claude-wayfinder#135  # AND-groups (bumps feature_schema_version)
  - glitchwerks/claude-wayfinder#145  # advisory_override detection fix (precondition)
review_history:
  - 2026-05-18 project-reviewer  # 2 BLOCKING, 6 CONCERN, 3 NIT — addressed in v2
---

# Dispatch telemetry enrichment

## Goal

Make claude-wayfinder dispatch decisions and drift events analytically
usable across matcher releases. Three concrete enrichments:

1. **Always-populated `top_agents` / `top_skills`** in matcher output, so
   alternatives are never null/missing.
2. **Structured `score_components`** per scored entry showing per-trigger-class
   contribution, so "why did this case go this way?" is answerable from a
   single row.
3. **Input-context retention on drift events**, so `advisory_override`,
   `bypass`, and `skill_mediated` rows carry the prompt context that
   produced them — enabling drift-rate attribution to feature combinations
   across releases.

## Motivation

Today, drift telemetry tells us *that* drift happened (categories: `bypass`,
`skill_mediated`, `advisory_override`) but not *what was in play when it
happened*. The matcher itself produces a rich decision object but that
object is never persisted — only its downstream effects on tool calls. This
makes the high-value question — **"did a matcher change reduce drift for
feature combination X?"** — answerable only by re-running the matcher
against archived transcripts, which is slow and brittle.

This design adds a new persisted event (`matcher_decision`) and enriches
the three existing drift event types with `raw_input` (always) and
`features` (where cheap). The data is shaped to survive ordinary matcher
evolution (weight tuning, threshold tweaks, catalog adds) without losing
analytical comparability across releases.

## The three-field model

Every persisted record (matcher_decision and enriched drift) carries up to
three layers, each with a different stability contract and consumer. Drift
events emitted in PreToolUse contexts carry only the cheapest layer
(`raw_input`) at write time; richer layers are derivable on demand.

| Field              | Contents                                                       | Stability        | Consumer                                |
| ------------------ | -------------------------------------------------------------- | ---------------- | --------------------------------------- |
| `raw_input`        | matcher's input verbatim (or transcript-reconstructed equivalent) | **highest**   | replay-from-source, disaster recovery, derive features on demand |
| `features`         | raw extractor output, presence-only                            | **semi-stable**  | cross-version drift attribution (goal 3)|
| `score_components` | weighted, multiplied, summed contributions per scored entry    | **volatile**     | per-case live debugging                 |

```
┌──────────────────────────────────────────────────────────────────────┐
│  matcher_decision row (Stop-time-equivalent: matcher ran)            │
│                                                                      │
│  raw_input         ░░░░░░░░░░  HIGHEST  → replay substrate           │
│  features          ░░░░░░░     SEMI     → cross-version analysis     │
│  score_components  ░░          VOLATILE → live debugging only        │
└──────────────────────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────────────────────┐
│  advisory_override drift row (Stop-time, matcher ran)                │
│                                                                      │
│  raw_input  + features  + decision_id (cross-ref to score_components)│
└──────────────────────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────────────────────┐
│  bypass / skill_mediated drift row (PreToolUse-time, matcher did NOT)│
│                                                                      │
│  raw_input  ONLY  (features derivable on demand via offline pass)    │
└──────────────────────────────────────────────────────────────────────┘
```

Reasoning:

- **score_components ages by design** because every weight tune changes the
  numbers. It is *not* the unit of long-term analysis.
- **features are the unit of long-term analysis**, partitioned by
  `feature_schema_version` (the extractor surface, bumped rarely).
- **raw_input is the disaster-recovery substrate**: when feature shape
  changes incompatibly, raw_input + a chosen historical catalog snapshot
  lets you re-derive anything.

### Why features are flat, not per-entry

Catalog entries (agents, skills) version independently — each has its own
`content_hash` and `rev`. Per-entry feature storage ("which entries' keyword
list contained `refactor` at scoring time") would couple every record to
multiple entry content_hashes. Even within a single `feature_schema_version`
window, agent edits would partition the data finely enough to lose
statistical power.

Flat storage ("did keyword `refactor` appear in this prompt") is coupled at
one level (term presence in input) rather than two. Per-entry attribution —
when needed — is a *replay-time* derivation from `raw_input` against a
pinned catalog snapshot, not a stored field.

### Why `features` is catalog-agnostic raw extractor output

The `build_features()` function at `src/claude_wayfinder/match.py:468-510`
takes only the dispatch context (a `raw_input` equivalent) and returns
extracted tokens, paths, mentions, etc. — **without consulting the
catalog**. That is the function the offline `features` CLI exposes; it must
remain catalog-agnostic so the same extractor produces identical output for
the Python matcher in-process and the CLI in batch mode.

Any field that requires catalog knowledge to compute (e.g., "which catalog
glob fired") belongs in `score_components`, not `features`. The matcher's
internal scoring already produces that information; the spec preserves it
in `score_components` per entry.

## Section 1 — New event: `matcher_decision`

### Location

New JSONL file: `~/.claude/state/matcher-decisions.jsonl`. Distinct from
`dispatch-log.jsonl` (hook-emitted, Node) and `router-drift.jsonl`
(scanner- and PreToolUse-emitted, Node). One file per emitter source.

### Schema

```json
{
  "type": "matcher_decision",
  "ts": "2026-05-18T22:15:30.123456Z",
  "session_id": "9337dfe9-7576-4753-a0ed-ebeebd76a02c",
  "decision_id": "9337dfe9-2026-05-18T22:15:30.123456Z-a8f3c1d2",

  "matcher_version": "0.4.2",
  "catalog_hash": "b1e9f23c...",
  "feature_schema_version": 1,

  "raw_input": {
    "task_description": "edit the auth docs",
    "file_paths": ["src/auth.py", "README.md"],
    "tool_mentions": [],
    "agent_mentions": [],
    "command_prefix": null
  },

  "features": {
    "keywords":        ["edit", "auth", "docs"],
    "paths":           ["src/auth.py", "README.md"],
    "extensions":      ["py", "md"],
    "agent_mentions":  [],
    "tool_mentions":   [],
    "command_prefix":  null
  },

  "decision":   "ambiguous",
  "confidence": 0.9,
  "rationale":  "ambiguous: code-writer 0.9, doc-writer 0.9 (gap 0.0 < 0.2 threshold)",

  "score_components": [
    {
      "entry": "code-writer",
      "kind": "agent",
      "contributions": {
        "keywords":    {"score": 0.5, "hit": ["edit"]},
        "path_globs":  {"score": 0.4, "hit": ["**/*.py"]}
      },
      "total": 0.9
    },
    {
      "entry": "doc-writer",
      "kind": "agent",
      "contributions": {
        "keywords":    {"score": 0.5, "hit": ["edit", "docs"]},
        "path_globs":  {"score": 0.4, "hit": ["**/*.md"]}
      },
      "total": 0.9
    }
  ],

  "top_agents": [
    {"name": "code-writer", "score": 0.9},
    {"name": "doc-writer",  "score": 0.9}
  ],
  "top_skills": []
}
```

### Field contracts

- **`ts`** = ISO 8601 UTC with microsecond precision, matching the existing
  `_write_log_entry()` convention (`%Y-%m-%dT%H:%M:%S.%fZ`, 6-digit fractional
  seconds).
- **`decision_id`** = `{session_id}-{ts}-{sha8(canonical_json(raw_input))}`.
  Stable join key. `canonical_json` = sorted keys, no whitespace, UTF-8
  (Python `json.dumps(obj, sort_keys=True, separators=(',', ':'))`). The
  sha8 disambiguates dispatches that share a timestamp millisecond. **The
  matcher is also responsible for emitting this id in the dispatch audit
  line** (see Section 2c) so the scanner can read it directly rather than
  reconstruct it.
- **`catalog_hash`** = whole-catalog SHA-256 hex, exactly the value the
  existing `_compute_catalog_hash()` at `match.py:294-317` already returns.
  The same field name (`catalog_hash`) used by existing `_write_log_entry()`
  output. Reused, not re-invented.
- **`feature_schema_version`** = integer; bumped only when the
  `build_features()` extractor output surface changes (new field, rename,
  semantic shift in an existing field). Starts at 1; bumps to 2 when
  AND-groups (#135) extends the extractor.
- **`features`** = exactly what `build_features()` returns: raw extractor
  output, no catalog coupling. Field names mirror the internal
  `Features` dataclass to make the identity contract trivial.
- **`score_components`** = sparse: only entries that scored > 0; each entry
  lists only non-zero contribution buckets. Each bucket records both
  the numeric `score` and the catalog-coupled `hit` list (which specific
  catalog values fired), since this layer is the one that loses meaning
  on weight tuning anyway — embedding catalog-coupled detail here costs
  nothing extra.
- **`top_agents` / `top_skills`** = always present as arrays; `[]` when
  none. Capped at 5 each, ordered by score descending.

### Emission point

The Python matcher's `dispatch()` wrapper writes one row per call,
**unconditionally** (no selective emission — selective sampling would bias
aggregate analysis). Writes follow the existing `_write_log_entry()`
pattern: `mkdir(parents=True, exist_ok=True)` + `f.write(json.dumps(entry)
+ "\n")`, no fsync.

### Volume

At current dispatch volume (~50/day across all sessions), expected growth
is roughly 50 × 2 KB = 100 KB/day, ~36 MB/year. No rotation policy needed
in v1. Lookup cost implications are documented in Section 2b.

## Section 2 — Enriched drift events

### Background: two emission paths

The existing `router-drift.jsonl` is written by **two different hooks with
different lifecycles**. This is the central architectural distinction the
enrichment design must respect.

| Emitter                                  | Hook lifecycle | Drift categories produced               | Available context                                           |
| ---------------------------------------- | -------------- | --------------------------------------- | ----------------------------------------------------------- |
| `hooks/check-agent-dispatch-pairing.js`  | **PreToolUse** (synchronous, before Agent call) | `bypass`, `skill_mediated`              | `conversation_history`, the tool call's `subagent_type` and prompt |
| `hooks/lib/router-drift-scanner.js`      | **Stop** (end of session)                 | `advisory_override`, `self_handle_unaided_invocation`, `needs_more_detail_repeat`, `skill_mediated_delegation`, `catalog_degraded_session` | full transcript, `matcher-decisions.jsonl` on disk |

(The `skill_mediated_delegation` event from the scanner is distinct from
the synchronous `skill_mediated` event emitted by the PreToolUse hook —
the former is a transcript-derived pattern detection, the latter is a
single-call drift annotation.)

**Operational implication.** The PreToolUse hook must not block on
expensive operations — it runs in the synchronous path of every Agent
call. Spawning Python from there (~100ms cold start) would add latency to
every dispatched call, even ones that never trigger drift detection. The
Stop hook has no such constraint.

This forces a per-emitter enrichment strategy:

| Drift category    | Emitter      | Enrichment strategy                                                                                               |
| ----------------- | ------------ | ----------------------------------------------------------------------------------------------------------------- |
| `advisory_override` | scanner (Stop) | At Stop time, read `decision_id` from the dispatch audit line; look up the paired `matcher_decision` row; copy `raw_input` + `features` into the drift row. No Python spawn (data is already in `matcher-decisions.jsonl`). |
| `bypass`          | PreToolUse  | At emission time, embed `raw_input` directly. Features are NOT computed at write time. `feature_schema_version` is omitted from the row (no features present). |
| `skill_mediated`  | PreToolUse  | Same as `bypass`.                                                                                                 |

### Section 2a — `advisory_override` schema (Stop-hook enriched)

```jsonc
{
  // existing fields kept verbatim:
  "type": "advisory_override",
  "ts": "...", "session_id": "...", "harness_version": "...",
  "recommended_agent": "...", "actual_agent": "...",
  "recommended_agent_rev": 3, "recommended_agent_content_hash": "...",
  "actual_agent_rev": 3,      "actual_agent_content_hash": "...",

  // NEW (added by router-drift-scanner.js after reading matcher-decisions.jsonl):
  "decision_id":            "9337dfe9-2026-05-18T22:15:30.123456Z-a8f3c1d2",
  "catalog_hash":           "b1e9f23c...",
  "feature_schema_version": 1,
  "raw_input":              { /* same shape as matcher_decision.raw_input */ },
  "features":               { /* same shape as matcher_decision.features  */ }
}
```

### Section 2b — `bypass` / `skill_mediated` schema (PreToolUse-emitted)

```jsonc
{
  // existing fields kept verbatim:
  "type": "bypass",
  "ts": "...", "session_id": "...", "harness_version": "...",
  "actual_agent": "...",

  // NEW (embedded at PreToolUse time, no Python spawn):
  "catalog_hash":           "b1e9f23c...",     // read from catalog file
  "raw_input":              { /* extracted from PreToolUse hook input */ }
  // NO decision_id — matcher never ran
  // NO features — derivable on demand via offline analysis (see Section 3)
  // NO feature_schema_version — no features present
}
```

The PreToolUse hook constructs `raw_input` from the data it already has:

| `raw_input` field   | PreToolUse-time source                                            |
| ------------------- | ----------------------------------------------------------------- |
| `task_description`  | The Agent tool call's `prompt` parameter (the brief the router wrote) |
| `file_paths`        | extracted from the prompt via the same regex/heuristic the matcher uses (if any), or empty |
| `tool_mentions`     | extracted from the prompt, same heuristic                         |
| `agent_mentions`    | extracted from the prompt, same heuristic                         |
| `command_prefix`    | first whitespace-delimited token of the prompt, if it begins with `/` |

The extraction heuristics are simple enough to reproduce in JS without
introducing a meaningful divergence risk. They are NOT the catalog-coupled
scoring logic — they are the same low-level tokenization the Python
extractor uses, and any drift between the two would surface as `features`
disagreement when the offline analysis runs.

### Section 2c — Cross-reference: `decision_id` in the audit line

To support the Stop-hook lookup for `advisory_override`, the router agent's
dispatch audit line gains a `decision_id` field:

```
🎯 Dispatch → advisory [code-writer] (confidence: 0.62, id: 9337dfe9-2026-05-18T22:15:30.123456Z-a8f3c1d2)
   Rationale: ...
```

The scanner already parses these audit lines to detect `advisory_override`.
Adding `id: <decision_id>` to the line makes the join O(1) per event with
no hash reconstruction — the scanner reads the id from the transcript and
looks up the matching row in `matcher-decisions.jsonl`. The audit-line
format change lives in `agents/general-purpose.md`.

### Section 2d — Lookup cost and version-skew handling

The scanner's lookup of `matcher_decision` rows by `decision_id` is a
linear scan of `matcher-decisions.jsonl` per advisory_override event.

At current scale (~36 MB/year, expected ~30s of scan time per year-long
file at typical disk speeds), this is acceptable for v1. The cost is
documented as a **rotation prerequisite**: when rotation is introduced, an
index file (`~/.claude/state/matcher-decisions.idx`) keyed on `decision_id`
should be added in the same change.

**Version-skew handling**: the scanner reads `feature_schema_version` from
each candidate `matcher_decision` row. If the row's version exceeds the
scanner's supported max, the scanner emits the drift event WITHOUT
features (degrade gracefully) and logs a `feature_schema_version_unsupported`
warning. Drift events are never spliced with features of an unsupported
schema version.

## Section 3 — Extractor strategy

### Single source of truth

The Python matcher's `build_features()` function (`match.py:468-510`) is
the canonical implementation. The Node hooks **do not re-implement feature
extraction**; they consume features either via direct copy from
`matcher-decisions.jsonl` (advisory_override, at Stop time) or via the
offline batch CLI (analysis tools, on demand).

### CLI subcommand: `features --batch` (offline use)

New: `python -m claude_wayfinder features --batch`

```
stdin:  JSONL of raw_input objects (one per line)
stdout: JSONL of {features, feature_schema_version} objects (one per line, same order)
exit:   0 on success; non-zero with diagnostic on stderr on any extraction error
```

The batch mode reads to EOF, processes all inputs, writes all outputs, then
exits. **This CLI is intended for offline analysis** — for example, an
analysis script that wants to derive features for the population of
`bypass` / `skill_mediated` events stored with `raw_input` only.

The batch CLI does NOT spawn from any hot path: not from PreToolUse, not
from the scanner during its routine run. If a future change wants to
enrich `bypass` / `skill_mediated` events with features at scanner time,
that decision is in scope of a follow-up issue, not this one.

### Hook flows summary

```
hooks/check-agent-dispatch-pairing.js (PreToolUse, modified):
  - existing behavior: detect bypass / skill_mediated, write event
  - NEW: embed raw_input (from hook input) into the event before writing
  - NEW: embed catalog_hash (read from catalog file at startup, cached)
  - NO Python spawn, NO feature extraction

hooks/lib/router-drift-scanner.js (Stop, modified):
  - existing behavior: scan transcript, detect drift via existing detectors
  - NEW: for each advisory_override event detected,
         parse decision_id from the dispatch audit line in the transcript
         look up matcher_decision row (linear scan of matcher-decisions.jsonl)
         if found, copy raw_input + features + feature_schema_version + catalog_hash
         if feature_schema_version > scanner-supported, omit features (log warning)
  - NO Python spawn
  - existing detectors for self_handle_unaided / needs_more_detail_repeat / skill_mediated_delegation
    are unchanged (those are pattern-detection events; raw_input enrichment is not in scope for v1)

src/claude_wayfinder/cli.py (modified):
  - NEW: features --batch subparser, follows existing argparse pattern
  - calls build_features() per line of stdin input
```

### Alternatives considered and rejected

**A: Re-implement extractor in Node** — would eliminate batch CLI dependency
for any future scanner-time enrichment. Rejected because the extractor
surface is part of the matcher contract that `feature_schema_version`
controls; two implementations to keep in sync defeats the
single-source-of-truth principle.

**B: PreToolUse Python spawn for bypass/skill_mediated features** —
considered earlier in design. Rejected because it adds ~100ms latency to
every Agent call (Python cold start is the dominant cost). The synchronous
PreToolUse hook should remain fast.

**C: Stop-time enrichment of PreToolUse-emitted bypass/skill_mediated** —
would require the scanner to re-open already-written drift events and
rewrite them in place, breaking append-only semantics. Rejected. If
features become required for these categories, the right answer is an
offline analysis pass that produces a derived dataset, not in-place edits
to the raw log.

## Section 4 — Versioning policy

| Field                    | When bumped                                          | Lives on                                                                            |
| ------------------------ | ---------------------------------------------------- | ----------------------------------------------------------------------------------- |
| `feature_schema_version` | `build_features()` output surface changes (new field, rename, semantic shift) | every features-bearing record (matcher_decision, advisory_override drift) |
| `matcher_version`        | every published `claude-wayfinder` release           | matcher_decision                                                                    |
| `catalog_hash`           | any catalog entry changes                            | every record (matcher_decision + all drift categories)                              |

### Query conventions

- Queries that touch `features` MUST filter or partition by
  `feature_schema_version`. Mixing v1 and v2 features in a single
  aggregation is undefined behavior.
- Queries that touch `score_components` MUST filter by `matcher_version`,
  or accept that scores will drift release-to-release.
- Queries that touch `raw_input` only are version-independent; this is the
  long-tail substrate.

### Bump policy

`feature_schema_version` is exposed as a module-level constant
(`FEATURE_SCHEMA_VERSION`) next to `build_features()`. CI test asserts
that any change to `build_features()` (or the `Features` dataclass) either
bumps the constant or adds a `# feature_schema_version unchanged: <reason>`
comment.

## Section 5 — Storage and migration

### Storage

Both files are JSONL append-forever in v1. No rotation. Expected growth:

- `matcher-decisions.jsonl`: ~100 KB/day at current volume; ~36 MB/year.
- `router-drift.jsonl`: ~3-5 KB/day enriched (currently ~50 KB total); a
  few MB/year.

### Migration

Existing rows in `router-drift.jsonl` (today: 491 bypass + 462
skill_mediated + 1 advisory_override) stay as-is. They lack `raw_input`,
`features`, `feature_schema_version`, `catalog_hash`, and `decision_id`.

No backfill is attempted — transcripts may have been cleared, and even
when present, post-hoc extraction against today's catalog would attribute
pre-schema rows to a catalog state they never saw.

Analysis tooling treats absence of new fields as "legacy row" and excludes
those rows from feature-conditioned queries.

### Forward-compatibility

New `matcher_decision` events appear on plugin upgrade. Pre-upgrade
sessions emit zero `matcher_decision` events; post-upgrade sessions emit
one per dispatch. Consumers that filter by `matcher_version` handle the
boundary naturally.

## Section 6 — Non-goals

- **No deletion or rotation of telemetry in v1.** Storage policy is
  append-forever. When rotation is added later, the rule MUST be: never
  prune `matcher-decisions.jsonl` rows whose `decision_id` is still
  referenced by an unpruned `router-drift.jsonl` row. The same change
  should introduce a `decision_id` index file to amortize lookup cost.
- **No real-time analysis stream.** Telemetry is for offline analysis;
  no querying API or dashboard is in scope.
- **No PII redaction in v1.** `raw_input.task_description` is stored
  verbatim. If this becomes a concern, a redaction pre-processor can be
  added in a follow-up; for now, the file is local-only under
  `~/.claude/state/` and not transmitted.
- **No backfill of legacy drift rows.** See Migration above.
- **No replacement of `dispatch-log.jsonl`.** Its event types
  (`agent_dispatch`, `skill_invocation`) are orthogonal to matcher
  decisions; both files coexist.
- **No features on `bypass` / `skill_mediated` rows at write time.** Features
  for these categories are derivable on demand from `raw_input` via the
  offline batch CLI. PreToolUse-time Python spawn is rejected per Section 3.
- **No automatic features for `self_handle_unaided_invocation`,
  `needs_more_detail_repeat`, or `skill_mediated_delegation` events.**
  These are scanner-detected pattern events whose primary signal is
  structural; raw_input enrichment is a separate follow-up if it becomes
  useful.

## Section 7 — Open questions

None blocking. Items to revisit post-implementation:

1. **Rotation policy + lookup index** — when does append-forever become a
   problem, and what does the index file's shape look like? Filed as
   follow-up.
2. **PII / redaction** — if any user opts in to sharing telemetry
   externally, raw_input.task_description redaction becomes load-bearing.
3. **Cross-session correlation** — should `decision_id` include a global
   counter so two machines emitting in the same millisecond don't collide?
   Currently relies on session_id + sha8 of raw_input — single-machine
   collision probability is negligible, cross-machine merge is out of scope.
4. **Optional enrichment of `bypass` / `skill_mediated` with features** —
   if a future analyst wants this consistently in the log (not on demand),
   the right place is an offline post-processing pass that produces a
   derived enriched file. Out of scope for v1.

## Acceptance criteria

1. `matcher-decisions.jsonl` exists in `~/.claude/state/` after the first
   post-upgrade dispatch.
2. Every row in `matcher-decisions.jsonl` has all required fields per
   Section 1 schema; CI test asserts shape on a fixture corpus.
3. A scanner unit test fixture includes a paired
   (`advisory_override` event, `matcher_decision` row) and verifies that
   the enriched `advisory_override` row carries the correct `decision_id`
   and that `raw_input` + `features` match the paired matcher_decision
   row's values.
4. Every new `bypass` and `skill_mediated` row in `router-drift.jsonl` has
   `raw_input` and `catalog_hash` populated, and does NOT have a
   `features` field (or has `features: null`).
5. `python -m claude_wayfinder features --batch` exists. CI test asserts
   round-trip identity: feeding the `raw_input` from a `matcher_decision`
   row through the batch CLI produces a `features` object equal to the
   one in that same row.
6. Legacy rows (no new fields) coexist in `router-drift.jsonl` without
   breaking any consumer.
7. `feature_schema_version` is set to 1 in all v1 records that have a
   `features` field. AND-groups (#135) will bump it to 2 as a follow-up PR.
8. Aggregate analysis demo: a script in `scripts/analysis/` computes
   "advisory_override rate by `recommended_agent`" from the enriched
   `router-drift.jsonl`, filtered by `feature_schema_version`.
9. The dispatch audit-line format documented in `agents/general-purpose.md`
   includes a `decision_id` field; an audit-line parser test in
   `hooks/tests/` confirms the scanner extracts it correctly from a
   representative transcript fixture.
10. Scanner version-skew test: a fixture `matcher_decision` row with
    `feature_schema_version: 99` produces an enriched advisory_override
    drift row WITHOUT a `features` field, and the scanner emits a
    `feature_schema_version_unsupported` warning.

## Implementation order (high-level — detailed plan to follow)

1. Define `FEATURE_SCHEMA_VERSION` constant near `build_features()` in
   `match.py`; ensure `build_features()` output dataclass is the source of
   truth for the `features` field shape.
2. Add `top_agents`/`top_skills` always-populated to matcher decision dict
   (`decide()` in `match.py`).
3. Extend `score()` (or `decide()`) to retain per-class score contributions
   and `hit` lists in a `score_components` structure.
4. Add `decision_id` derivation to the matcher's `dispatch()` wrapper in
   `_dispatch.py`. Embed it in the dispatch-output dict.
5. Wire matcher's `dispatch()` to emit `matcher_decision` rows to
   `~/.claude/state/matcher-decisions.jsonl` using the existing
   `_write_log_entry()`-style pattern.
6. Add `features --batch` subcommand to `cli.py`.
7. Update `agents/general-purpose.md` to include `decision_id` in the
   dispatch audit line; add a parser unit test in `hooks/tests/`.
8. Modify `hooks/check-agent-dispatch-pairing.js` (PreToolUse) to embed
   `raw_input` + `catalog_hash` into `bypass` / `skill_mediated` events.
9. Modify `hooks/lib/router-drift-scanner.js` (Stop) to:
   a. Parse `decision_id` from the audit line for advisory_override events.
   b. Look up the matcher_decision row by decision_id.
   c. Copy raw_input + features + feature_schema_version + catalog_hash
      into the drift row.
   d. Skip features (with warning) on version-skew.
10. Add demo analysis script + `docs/design/telemetry-schema.md`.
