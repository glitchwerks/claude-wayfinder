---
title: "Dispatch overrides — deterministic pre-scoring rules"
date: 2026-05-24
issue: 213
status: implemented
touches:
  - src/claude_wayfinder/match/_main.py
  - src/claude_wayfinder/match/_decide.py
  - src/claude_wayfinder/match/_catalog.py
  - src/claude_wayfinder/match/_overrides.py
  - src/claude_wayfinder/match/_types.py
  - src/claude_wayfinder/match/__init__.py
  - src/claude_wayfinder/audit_catalog.py
  - src/claude_wayfinder/cli.py
  - src/claude_wayfinder/_dispatch.py
  - src/claude_wayfinder/fixtures/demo-catalog.json
  - src/claude_wayfinder/fixtures/demo-prompts.json
  - src/claude_wayfinder/fixtures/demo-overrides.json
  - tests/test_match/test_overrides.py
  - tests/test_match/test_decide.py
  - tests/test_match/test_integration.py
  - tests/test_audit_catalog.py
  - tests/test_cli_dispatch.py
  - docs/dispatch-overrides.md
  - README.md
skills_relevant:
  - python
  - dispatch-authoring
  - agent-authoring
---

# Dispatch overrides — deterministic pre-scoring rules

## Overview

Dispatch overrides let consumers declare verbatim routing decisions that fire
**before** the scoring + decision-ladder pipeline runs. When an override rule's
predicates match the dispatch context, the matcher returns the rule's frozen
`(decision, agent, skills, confidence, rationale)` directly and skips all
scoring. Overrides are a pure replacement mechanism: they do not mutate a
scored result, do not evaluate LLM-based predicates, and do not support regex.
Wayfinder ships the loader, resolver, and audit checks; the rule file is
consumer-private.

## Schema

Rules live in a single JSON file with a `version` field and a `rules` array.
Field types match the dispatch context and decision-dict contracts exactly.

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
      "rationale": "/deploy is always handled manually — skip matcher",
      "predicates": {
        "command_prefix": "/deploy"
      }
    },
    {
      "id": "py-files-to-code-writer",
      "decision": "delegate",
      "agent": "code-writer",
      "skills": ["python"],
      "confidence": 0.99,
      "rationale": "All Python edits go to code-writer unconditionally",
      "predicates": {
        "path_globs": ["**/*.py", "src/**/*.pyi"]
      }
    }
  ]
}
```

**Field reference:**

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | string | yes | Stable kebab-case identifier; must be unique within the file |
| `decision` | string | yes | One of `VALID_DECISIONS`; validated at load time |
| `agent` | string or null | yes | Agent name emitted verbatim; `null` when decision implies none |
| `skills` | array of string | yes | Skill names emitted verbatim into the decision output |
| `confidence` | float | yes | `[0.0, 1.0]`; values outside range are clamped with a stderr warning |
| `rationale` | string | yes | Emitted verbatim into the decision output |
| `predicates` | object | yes | At least one predicate key required; see next section |

## Predicate vocabulary v1

All predicates within a rule are AND-combined: every non-empty predicate must
match for the rule to fire. A rule with zero predicates is invalid; see
`override-zero-predicates` in the audit-rules table.

| Predicate | Expected type | Match semantics |
|---|---|---|
| `command_prefix` | string | Exact-string equality against `context.command_prefix`. Case-sensitive. |
| `path_globs` | array of string | [`fnmatch`](https://docs.python.org/3/library/fnmatch.html) glob list. Rule matches when **any** path in `context.file_paths` matches **any** glob. |
| `tool_mentions` | array of string | Exact-string set. Rule matches when the intersection with `context.tool_mentions` is non-empty. Tool names are case-sensitive (`"Bash"` not `"bash"` — see `override-tool-case-error`). |

Keywords and keyword groups are deferred to v2. See Out of scope below.

## Resolution order

Rules are evaluated top-to-bottom in file order. The first rule whose
predicates all match wins; no further rules are evaluated.

**File order is the priority list.** Put more-specific rules before
less-specific ones. The `override-unreachable` audit rule (NIT) catches the
most common copy/paste footgun: two rules with string-identical predicate
triples where the second can never fire. Glob subsumption (`**/*.py` subsumes
`src/**/*.py` semantically) is **not** checked — fnmatch subsumption is not
decidable by set comparison. The `override-unreachable` rule flags only
string-identical triples; more sophisticated subsumption detection is out of
scope for v1.

## Public/private boundary

Wayfinder ships the mechanism: the loader, resolver, audit checks, output
markers, and `fixtures/demo-overrides.json` (for tests and `python -m
claude_wayfinder demo`). Wayfinder does **not** ship a default consumer rule
file. The bundled `demo-overrides.json` is not loaded in production; it is
a test fixture only.

The only production discovery mechanism is `$DISPATCH_OVERRIDES_PATH`. When
unset, no overrides load and scored matching runs unchanged. This continues
the explicit-config posture established for `$DISPATCH_CATALOG_PATH` and
`$DISPATCH_LOG_PATH`. See issue [#54](https://github.com/glitchwerks/claude-wayfinder/issues/54)
for the public/private precedent.

When `$DISPATCH_OVERRIDES_PATH` is set:

- A single-line stderr note fires once per process: `[dispatch] overrides: N rules loaded from <path>`.
- When the file is missing or malformed, stderr emits `[OVERRIDES ERROR] <reason>; proceeding with scored matching.` and scored matching runs. Overrides are an enhancement, not a contract.

When `$DISPATCH_OVERRIDES_PATH` is **not** set: no note is emitted. Silence avoids adding new stderr output to pipelines that have never opted in.

## Telemetry

Every dispatch log entry (written by `_write_log_entry` in `_catalog.py`) gains
a top-level `override_id` field:

- **Override-fired decisions:** `override_id` is the matched rule's `id` string.
- **Scored decisions:** `override_id` is `null`.

The decision dict in `output` also carries `disposition_source` (`"override"` or
`"scored"`) and `override_id` for record-shape consistency. The top-level field
duplicates `output.override_id` intentionally — it is cheaper to query in NDJSON
sweeps (`override_id != null` to find all override-fired decisions without
parsing nested output dicts).

The JSON-parse-error early-return path in `_main.py` also writes a log entry
(`catalog_hash: ""` as sentinel — catalog had not loaded yet). This closes a
prior schema gap where parse-failure events were absent from the log entirely.

## Audit rules

All seven rules are registered alongside existing catalog rules and run via
`claude_wayfinder audit-catalog`. Override-aware rules require `--overrides-path`
to be passed to the CLI; without the flag, only catalog-only rules run.

| Rule ID | Severity | Description |
|---|---|---|
| `override-zero-predicates` | BLOCKING | Rule has no predicate set; would match every context unconditionally |
| `override-duplicate-id` | BLOCKING | Two rules share the same `id` |
| `override-load-error` | BLOCKING | Overrides file cannot be parsed (CLI-only; emitted when `--overrides-path` load fails) |
| `override-unknown-skill` | CONCERN | `skills:` names a skill not present in the loaded catalog |
| `override-unknown-agent` | CONCERN | `agent:` names an agent not present in the loaded catalog (skipped when `agent` is `null`) |
| `override-tool-case-error` | CONCERN | `tool_mentions:` contains a tool name that does not match canonical casing (e.g. `"bash"` vs `"Bash"`); reuses `_CANONICAL_TOOLS_LOWER` from `rule_tool_name_case_error` |
| `override-unreachable` | NIT | Two rules share string-identical `command_prefix`, `tool_mentions`, and `path_globs`; the later rule can never fire |

`override-unknown-skill` and `override-unknown-agent` are CONCERN rather than
BLOCKING because a consumer may intentionally name a skill or agent not yet
present in the live catalog (pre-declared routing for a resource under
authoring).

## Staleness

When `$DISPATCH_OVERRIDES_PATH` is set and `overrides.mtime < catalog.mtime`,
the dispatch skill emits to stderr:

```
[DISPATCH WARNING] overrides file is older than catalog — rules may reference stale agent/skill names
```

Non-fatal. The matcher proceeds with the loaded rules. There is no
`matcher_version_min` semver gate on individual rules — that was removed in Rev 1
as prose-only, never implemented, and deferred to v2 if needed.

## Out of scope (v1)

Quoted from issue #213:

- Regex-based predicates (v1 is fnmatch + exact-string only).
- LLM-evaluated predicates of any kind.
- Override rules that modify or augment a scored decision (overrides only replace, never mutate).
- A UI or editor for authoring rules.
- Shipping any consumer rule file inside `claude-wayfinder` — rule files are consumer-private (#54 precedent). The bundled `demo-overrides.json` is for tests/demos only.
- Cross-repo migration of existing `glitchwerks/claude-configs` decisions onto the new mechanism (AC #7) — handled as a follow-up PR in that repo.
- Issue #143 (richer telemetry beyond `override_id`).
- A `matcher_version_min` semver gate on individual rules.

## Related

- Issue [#213](https://github.com/glitchwerks/claude-wayfinder/issues/213) — implementation tracking and acceptance criteria.
- PR [#214](https://github.com/glitchwerks/claude-wayfinder/pull/214) — implementation; design decisions D1–D7, Rev 1 review resolution, and task breakdown were tracked in the plan file (deleted per #224 after shipment).
- Issue [#54](https://github.com/glitchwerks/claude-wayfinder/issues/54) — public/private split precedent.
- `glitchwerks/claude-configs` — consumer migration of existing prose-policy routing onto override rules (AC #7, separate follow-up PR).
