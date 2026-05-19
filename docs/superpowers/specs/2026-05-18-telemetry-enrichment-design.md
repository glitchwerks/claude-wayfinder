---
title: Dispatch telemetry enrichment design
date: 2026-05-18
status: draft
tracking: glitchwerks/claude-wayfinder#143
touches:
  - src/claude_wayfinder/_dispatch.py
  - src/claude_wayfinder/match.py
  - src/claude_wayfinder/__init__.py
  - src/claude_wayfinder/build_catalog.py
  - hooks/lib/router-drift-scanner.js
  - docs/design/telemetry-schema.md  # new file
skills_relevant:
  - python
  - superpowers:test-driven-development
related:
  - glitchwerks/claude-wayfinder#135  # AND-groups (bumps feature_schema_version)
  - glitchwerks/claude-wayfinder#145  # advisory_override detection fix (precondition)
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
   `bypass`, and `skill_mediated` rows carry the prompt features that
   produced them — enabling drift-rate attribution to feature combinations
   across releases.

## Motivation

Today, drift telemetry tells us *that* drift happened (categories: `bypass`,
`skill_mediated`, `advisory_override`) but not *what features were in play
when it happened*. The matcher itself produces a rich decision object but
that object is never persisted — only its downstream effects on tool calls.
This makes the high-value question — **"did a matcher change reduce drift
for feature combination X?"** — answerable only by re-running the matcher
against archived transcripts, which is slow and brittle.

This design adds a new persisted event (`matcher_decision`) and enriches the
three existing drift event types with input + features. The data is shaped
to survive ordinary matcher evolution (weight tuning, threshold tweaks,
catalog adds) without losing analytical comparability across releases.

## The three-field model

Every persisted record (matcher_decision and enriched drift) carries three
layers, each with a different stability contract and consumer.

| Field              | Contents                                                       | Stability        | Consumer                                |
| ------------------ | -------------------------------------------------------------- | ---------------- | --------------------------------------- |
| `raw_input`        | matcher's input verbatim                                       | **highest**      | replay-from-source, disaster recovery   |
| `features`         | extractor output, presence-only                                | **semi-stable**  | cross-version drift attribution (goal 3)|
| `score_components` | weighted, multiplied, summed contributions per scored entry    | **volatile**     | per-case live debugging                 |

```
┌──────────────────────────────────────────────────────────────────────┐
│  matcher_decision row (one per dispatch invocation)                  │
│                                                                      │
│  raw_input         ░░░░░░░░░░  HIGHEST  → replay substrate           │
│  features          ░░░░░░░     SEMI     → cross-version analysis     │
│  score_components  ░░          VOLATILE → live debugging only        │
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

Flat storage ("did keyword `refactor` fire on this prompt across any catalog
entry") is coupled at one level (term presence in catalog) rather than two.
Per-entry attribution — when needed — is a *replay-time* derivation from
`raw_input` against a pinned catalog snapshot, not a stored field.

## Section 1 — New event: `matcher_decision`

### Location

New JSONL file: `~/.claude/state/matcher-decisions.jsonl`. Distinct from
`dispatch-log.jsonl` (hook-emitted, Node) and `router-drift.jsonl`
(scanner-emitted, Node). One file per emitter source.

### Schema

```json
{
  "type": "matcher_decision",
  "ts": "2026-05-18T22:15:30.123Z",
  "session_id": "9337dfe9-7576-4753-a0ed-ebeebd76a02c",
  "decision_id": "9337dfe9-2026-05-18T22:15:30.123Z-a8f3c1d2",

  "matcher_version": "0.4.2",
  "catalog_content_hash": "b1e9f23c...",
  "feature_schema_version": 1,

  "raw_input": {
    "task_description": "edit the auth docs",
    "file_paths": ["src/auth.py", "README.md"],
    "tool_mentions": [],
    "agent_mentions": [],
    "command_prefix": null
  },

  "features": {
    "keywords_hit":      ["edit", "auth", "docs"],
    "path_globs_hit":    ["**/*.py", "**/*.md"],
    "tools_hit":         [],
    "agents_hit":        [],
    "command_hit":       null
  },

  "decision":   "ambiguous",
  "confidence": 0.9,
  "rationale":  "ambiguous: code-writer 0.9, doc-writer 0.9 (gap 0.0 < 0.2 threshold)",

  "score_components": [
    {
      "entry": "code-writer",
      "kind": "agent",
      "contributions": {"keywords": 0.5, "path_globs": 0.4},
      "total": 0.9
    },
    {
      "entry": "doc-writer",
      "kind": "agent",
      "contributions": {"keywords": 0.5, "path_globs": 0.4},
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

- **`decision_id`** = `{session_id}-{ts}-{sha8(canonical_json(raw_input))}`.
  Stable join key. `canonical_json` = sorted keys, no whitespace, UTF-8
  (Python `json.dumps(obj, sort_keys=True, separators=(',', ':'))`). The
  sha8 disambiguates dispatches that share a timestamp millisecond. The
  Python emitter and any consumer that wants to derive the id from
  raw_input MUST use the same canonicalization.
- **`catalog_content_hash`** = roll-up hash of the catalog state at scoring
  time. Cheap to compute (catalog already tracks per-entry hashes).
- **`feature_schema_version`** = integer; bumped only when the extractor
  surface changes (new trigger class, tokenization rule, rename). Starts
  at 1; bumps to 2 when AND-groups (#135) lands, etc.
- **`score_components`** = sparse: only entries that scored > 0; each entry
  lists only non-zero contribution buckets.
- **`top_agents` / `top_skills`** = always present as arrays; `[]` when
  none. Capped at 5 each, ordered by score descending.
- **`groups_hit`** = always present as an array; `[]` when AND-groups not
  yet shipped or no group fired.

### Emission point

The Python matcher's `dispatch()` wrapper writes one row per call,
**unconditionally** (no selective emission — selective sampling would bias
aggregate analysis). Writes are append-only fsync-skipped JSONL.

### Volume

At current dispatch volume (~50/day across all sessions), expected growth
is roughly 50 × 2 KB = 100 KB/day, ~36 MB/year. No rotation policy needed
in v1.

## Section 2 — Enriched drift events

### Location

Existing file: `~/.claude/state/router-drift.jsonl`. Existing fields are
kept verbatim. New fields are added optionally — legacy rows lacking them
are valid.

### Schema additions

```jsonc
// advisory_override (matcher ran)
{
  // existing fields kept verbatim:
  "type": "advisory_override",
  "ts": "...", "session_id": "...", "harness_version": "...",
  "recommended_agent": "...", "actual_agent": "...",
  "recommended_agent_rev": 3, "recommended_agent_content_hash": "...",
  "actual_agent_rev": 3,      "actual_agent_content_hash": "...",

  // NEW:
  "decision_id":           "9337dfe9-2026-05-18T22:15:30.123Z-a8f3c1d2",
  "catalog_content_hash":  "b1e9f23c...",
  "feature_schema_version": 1,
  "raw_input":             { /* same shape as matcher_decision.raw_input */ },
  "features":              { /* same shape as matcher_decision.features  */ }
}

// bypass / skill_mediated (matcher did NOT run)
{
  // existing fields kept verbatim:
  "type": "bypass",
  "ts": "...", "session_id": "...", "harness_version": "...",
  "actual_agent": "...",

  // NEW:
  "catalog_content_hash":  "b1e9f23c...",
  "feature_schema_version": 1,
  "raw_input":             { /* extracted from transcript by Node scanner */ },
  "features":              { /* produced by batch shell-out to Python     */ }
  // NO decision_id — matcher never ran, no matcher_decision row exists
}
```

### Where the features come from

| Category            | Matcher ran? | Source of `features`                                        |
| ------------------- | ------------ | ----------------------------------------------------------- |
| `advisory_override` | yes          | copied from paired `matcher_decision` row via `decision_id` |
| `bypass`            | no           | Node scanner extracts `raw_input` from transcript; batch shell-out to Python extractor produces `features` |
| `skill_mediated`    | no           | same as `bypass`                                            |

### Cross-reference convention

For `advisory_override`, consumers may join drift events to matcher_decision
rows by `decision_id`. Both files always live in `~/.claude/state/`.
Drift-event consumers that want score-level detail (not just feature-level)
read the paired matcher_decision row.

## Section 3 — Extractor strategy

### Single source of truth

The Python matcher's feature extractor is the canonical implementation. The
Node drift scanner does NOT re-implement extraction — it shells out to
Python in batch.

### CLI subcommand

New: `python -m claude_wayfinder features --batch`

```
stdin:  JSONL of raw_input objects (one per line)
stdout: JSONL of {features, feature_schema_version} objects (one per line, same order)
exit:   0 on success; non-zero with diagnostic on stderr on any extraction error
```

The batch mode reads to EOF, processes all inputs, writes all outputs, then
exits. No streaming or partial-failure recovery — extraction is fast enough
that the whole-batch model is simpler and reliable.

### Node scanner flow

```
hooks/lib/router-drift-scanner.js (modified):

1. parseTranscript → events stream
2. for each event, detect drift category
3. for events lacking features (bypass, skill_mediated):
     - extract raw_input from transcript context
     - push {drift_event_ref, raw_input} into needs_features buffer
4. for events with decision_id (advisory_override):
     - look up matcher_decision row by decision_id (file read)
     - copy raw_input + features into drift event
5. ONE batch spawn per session:
     - python -m claude_wayfinder features --batch < needs_features.jsonl
     - read stdout, splice features back into matching drift events
6. write enriched drift events to router-drift.jsonl
```

### Cost analysis

One Python process spawn per session that contains at least one bypass or
skill_mediated drift event. The Node scanner checks the `needs_features`
buffer length before spawning — empty buffer skips the spawn entirely.
Sessions with N drift events incur 1 spawn (not N). Spawn cost (~100ms
cold Python startup) is amortized across all drift events in the session.

### Alternative considered and rejected

Porting the extractor to Node (re-implementing in JS, with a parity test
in CI) would eliminate spawn cost but introduces a second implementation
to keep in sync. AND-groups (#135) and future trigger-class additions
would require parallel updates to two codebases. Rejected because the
spawn cost is small and divergence risk is real.

## Section 4 — Versioning policy

| Field                    | When bumped                                          | Lives on            |
| ------------------------ | ---------------------------------------------------- | ------------------- |
| `feature_schema_version` | extractor surface changes (new trigger class, tokenization rule, rename, etc.) | every features-bearing record |
| `matcher_version`        | every published `claude-wayfinder` release           | matcher_decision    |
| `catalog_content_hash`   | any catalog entry changes                            | every record        |

### Query conventions

- Queries that touch `features` MUST filter or partition by
  `feature_schema_version`. Mixing v1 and v2 features in a single
  aggregation is undefined behavior.
- Queries that touch `score_components` MUST filter by `matcher_version`,
  or accept that scores will drift release-to-release.
- Queries that touch `raw_input` only are version-independent; this is the
  long-tail substrate.

### Bump policy

`feature_schema_version` bumps are version-controlled in the source: the
extractor function exposes a constant that increments on any change to its
output surface. CI asserts that any PR touching the extractor either bumps
the constant or includes a comment justifying why the existing version is
still valid.

## Section 5 — Storage and migration

### Storage

Both files are JSONL append-forever in v1. No rotation. Expected growth:

- `matcher-decisions.jsonl`: ~100 KB/day at current volume; ~36 MB/year.
- `router-drift.jsonl`: ~3-5 KB/day enriched (currently ~50 KB total); a
  few MB/year.

Rotation policy is out of scope for v1. If introduced later, the rule MUST
be: never prune `matcher-decisions.jsonl` rows whose `decision_id` is still
referenced by an unpruned `router-drift.jsonl` row.

### Migration

Existing rows in `router-drift.jsonl` (today: 491 bypass + 462 skill_mediated
+ 1 advisory_override) stay as-is. They lack:

- `decision_id` (matcher never ran for bypass / skill_mediated; the one
  advisory_override predates this design)
- `raw_input`, `features`, `feature_schema_version`, `catalog_content_hash`

No backfill is attempted — transcripts may have been cleared, and even when
present, post-hoc extraction against today's catalog would attribute pre-
schema rows to a catalog state they never saw.

Analysis tooling treats absence of new fields as "legacy row" and excludes
those rows from feature-conditioned queries.

### Forward-compatibility

New `matcher_decision` events appear on plugin upgrade. Pre-upgrade sessions
emit zero `matcher_decision` events; post-upgrade sessions emit one per
dispatch. Consumers that filter by `matcher_version` handle the boundary
naturally.

## Section 6 — Non-goals

- **No deletion or rotation of telemetry in v1.** Storage policy is
  append-forever. A separate issue may revisit if growth becomes a problem.
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

## Section 7 — Open questions

None blocking. Items to revisit post-implementation:

1. **Rotation policy** — when does append-forever become a problem? Filed
   as follow-up if growth exceeds expectations.
2. **PII / redaction** — if any user opts in to sharing telemetry
   externally, raw_input.task_description redaction becomes load-bearing.
3. **Cross-session correlation** — should `decision_id` include a global
   counter so two machines emitting in the same millisecond don't collide?
   Currently relies on session_id + sha8 of raw_input — single-machine
   collision probability is negligible, cross-machine merge is out of scope.

## Acceptance criteria

1. `matcher-decisions.jsonl` exists in `~/.claude/state/` after the first
   post-upgrade dispatch.
2. Every row in `matcher-decisions.jsonl` has all required fields per
   Section 1 schema; CI test asserts shape on a fixture corpus.
3. Every new `advisory_override` row in `router-drift.jsonl` has a
   `decision_id` that resolves to a row in `matcher-decisions.jsonl`.
4. Every new `bypass` and `skill_mediated` row in `router-drift.jsonl` has
   `raw_input`, `features`, `feature_schema_version`, and
   `catalog_content_hash` populated.
5. `python -m claude_wayfinder features --batch` exists and round-trips
   correctly: given the same raw_inputs as the matcher saw, it produces
   features identical to what the matcher's internal extractor produced
   for the same dispatch.
6. Legacy rows (no new fields) coexist in `router-drift.jsonl` without
   breaking any consumer.
7. `feature_schema_version` is set to 1 in all v1 records. AND-groups
   (#135) will bump it to 2 as a follow-up PR.
8. Aggregate analysis demo: a script in `scripts/analysis/` computes
   "advisory_override rate by `recommended_agent`" from the enriched
   `router-drift.jsonl`, filtered by `feature_schema_version`.

## Implementation order (high-level — detailed plan to follow)

1. Add `decision_id` derivation + `top_agents`/`top_skills` always-populated
   to matcher output (#143-task-1).
2. Add the `python -m claude_wayfinder features --batch` CLI subcommand
   (#143-task-2).
3. Wire matcher to emit `matcher_decision` rows to
   `~/.claude/state/matcher-decisions.jsonl` (#143-task-3).
4. Modify `hooks/lib/router-drift-scanner.js` to:
   a. Extract `raw_input` from transcript for bypass / skill_mediated
   b. Batch-shell-out to Python for feature extraction
   c. Copy features from paired matcher_decision row for advisory_override
   d. Emit enriched drift rows (#143-task-4).
5. Add demo analysis script + documentation (#143-task-5).
6. Update `docs/design/` with telemetry schema doc (#143-task-6).
