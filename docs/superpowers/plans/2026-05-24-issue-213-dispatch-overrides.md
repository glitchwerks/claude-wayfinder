---
title: "Issue #213 — Dispatch Overrides (deterministic decision rules)"
issue: 213
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
  - docs/superpowers/specs/2026-05-24-dispatch-overrides.md
  - README.md
skills_relevant:
  - python
  - dispatch-authoring
  - agent-authoring
---

# Dispatch Overrides Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a deterministic override mechanism in the wayfinder matcher: when an override rule matches the dispatch context, the matcher returns the rule's pre-declared `(decision, agent, skills, confidence, rationale)` verbatim — bypassing scoring + the 7-branch decision ladder — and tags the output with `disposition_source: "override"` (vs `"scored"`).

**Architecture:** A new `_overrides.py` module owns rule loading + resolution. Override resolution runs in `_main.py` **between catalog load and scoring**: if a rule matches, return its frozen decision; otherwise scoring proceeds unchanged. Rules live in a JSON file discovered via `$DISPATCH_OVERRIDES_PATH` (or beside the catalog). The audit-catalog CLI gains override-validity checks. Wayfinder ships **mechanism only**; rule files are consumer-private (per #54 precedent).

**Tech Stack:** Python 3.11+, stdlib `json` / `fnmatch` (no new dependencies), pytest.

---

## Review Response (Rev 1)

Findings from `.tmp/2026-05-24-project-review-issue-213-dispatch-overrides.md` are mapped to plan changes here so the next reviewer can trace resolution.

| Finding | Status | Where in plan |
|---|---|---|
| BLOCKING-1 — `cli.py:run_demo()` bypass | Addressed | `touches:` adds `src/claude_wayfinder/cli.py`; new **Task 4b** wires `resolve_override()` into `run_demo()`; Task 8 Step 3 explicitly tests against `_main.py`, with `run_demo()` coverage added |
| BLOCKING-2 — `audit_catalog.py` RuleFn signature | Addressed | Task 6 Step 0 (new) defines `OverrideRuleFn`, `OVERRIDE_RULES` registry, `@register_override` decorator, and extends `run_audit()` signature concretely |
| BLOCKING-3 — `_main.py` JSON-parse-error log gap | Addressed | Task 5 Step 4 (new) adds `_write_log_entry` call on the JSON-parse-error path (option (a) — closes existing gap) |
| CONCERN-1 — Unconditional stderr note | Addressed | Task 4 Step 7 code block gated on `overrides_path is not None`; D5 prose updated |
| CONCERN-2 — D7 staleness + `matcher_version_min` ghost field | Addressed | D7 rewritten: `matcher_version_min` removed (v2-out-of-scope), only catalog-mtime comparison retained |
| CONCERN-3 — `override-unreachable` O(n²) + glob subsumption undecidable | Addressed | Task 6 rule 4 restricted to `tool_mentions` + `command_prefix` exact-set semantics, downgraded to NIT-level (flags string-identical `path_globs` only); glob-subsumption explicitly documented as not checked |
| CONCERN-4 — `tool_mentions` case sensitivity | Addressed | Task 6 adds 7th rule `override-tool-case-error` (CONCERN), reusing `_CANONICAL_TOOLS_LOWER` |
| CONCERN-5 — `claude-configs` follow-up untracked | Addressed | Pre-Merge Verification adds: file `claude-configs` issue for AC #7 and record number in PR body |
| NIT-1 — `_resolve_overrides_path()` placement | Defended | Kept in `_catalog.py` for consistency with `_resolve_catalog_path()` / `_resolve_log_path()` co-location; cohesion preference only |
| NIT-2 — Private-symbol cross-module import | Defended | Consistent with existing `_main.py` imports from `_catalog`; latent smell flagged for future cleanup but not in scope |
| NIT-3 — `OverrideRule.confidence` no range validation | Addressed | Task 2 loader adds clamp `max(0.0, min(1.0, val))` with stderr warning when value falls outside `[0.0, 1.0]` |

---

## Source-of-Truth References

- Issue: `gh issue view 213 --repo glitchwerks/claude-wayfinder`
- Decision ladder: `src/claude_wayfinder/match/_decide.py:153-281`
- Matcher entrypoint: `src/claude_wayfinder/match/_main.py:30-163`
- Catalog loader: `src/claude_wayfinder/match/_catalog.py:198-241`
- Log writer (telemetry): `src/claude_wayfinder/match/_catalog.py:155-195`
- Audit-catalog registry: `src/claude_wayfinder/audit_catalog.py:80-100`
- Dispatch mode-switch: `src/claude_wayfinder/_dispatch.py:1-54`
- Public API surface: `src/claude_wayfinder/match/__init__.py`
- Related (downstream consumer): `glitchwerks/claude-configs` (AC #7, separate PR)
- Related (precedent for public/private split): #54

## Out-of-Scope (quoted from issue #213)

The following are explicitly OUT of scope for this PR and must not be addressed:

- Regex-based predicates (v1 is fnmatch + exact-string only).
- LLM-evaluated predicates of any kind.
- Override rules that modify or augment a scored decision (overrides only **replace**, never **mutate**).
- A UI / editor for authoring rules.
- Shipping any consumer rule file inside `claude-wayfinder` — rule files are consumer-private (#54 precedent). The bundled `demo-overrides.json` is for tests/demos only and is not a default-loaded rules file.
- Cross-repo migration of existing `glitchwerks/claude-configs` decisions onto the new mechanism (AC #7) — handled as a follow-up PR in that repo.
- Issue #143 (richer telemetry beyond `override_id`).

## Up-Front Design Decisions

These resolve the open questions called out in the issue and in the planning brief. Each is appealable at project-reviewer pass.

### D1 — Rule file format and discovery

**Decision:** JSON, single file, discovered via `$DISPATCH_OVERRIDES_PATH` (absolute path). If unset, no overrides are loaded (empty rule list — pure-scored behavior).

**Rationale:** JSON matches the catalog format, avoids adding PyYAML to wayfinder's dependency footprint, and is trivially diff-friendly. Single file keeps resolution-order semantics simple (D3). Env-var-only discovery (no auto-discovery beside catalog) mirrors the explicit-config posture already established for `$DISPATCH_CATALOG_PATH` and `$DISPATCH_LOG_PATH` in `_catalog.py:42-101`.

### D2 — Predicate vocabulary v1

**Decision:** Three predicates, all optional, AND-combined within a rule:

- `command_prefix` — exact string match against `context.command_prefix` (e.g. `"/deploy"`).
- `path_globs` — fnmatch list; rule matches when **any** path in `context.file_paths` matches **any** glob.
- `tool_mentions` — exact-string set; rule matches when the intersection with `context.tool_mentions` is non-empty.

A rule with zero predicates is a `BLOCKING` audit failure (would match every context).

**Rationale:** These three are the highest-leverage signals already extracted in `Features` (`_types.py:148-170`) and exercised by existing predicate code paths — re-using `fnmatch` + set intersection avoids new evaluator code. `keywords` and `keyword_groups` are intentionally deferred to v2: keyword tokenization is intent-sensitive and would replicate too much of the scorer for marginal gain over `tool_mentions`.

### D3 — Resolution order on overlap

**Decision:** **First-match wins** by file order. The loader preserves rule order; resolution iterates top-to-bottom and returns on the first predicate-satisfying rule.

**Rationale:** Deterministic, trivial to reason about, and the file becomes a human-readable priority list. The audit-catalog overlap check (Task 6) flags rules whose predicate-set is a strict subset of an earlier rule's (i.e. unreachable rules) as `CONCERN`, which catches the footgun without forcing runtime to compute specificity.

### D4 — Skill reference semantics

**Decision:** Override rules **name skills directly** in a `skills:` array. The matcher emits those skill names verbatim into the decision output; it does **not** resolve them through the skill catalog or re-evaluate their applicability.

**Rationale:** Indirection through "reference an existing skill's triggers" couples override-file syntax to catalog churn (renaming a skill silently changes override behavior) and forces a second lookup pass at resolution time. Direct naming is simpler, makes the override file self-contained, and matches the contract the decision dict already exposes to callers (a plain list of skill names). Audit-catalog (Task 6) flags skill names not present in the live catalog as `CONCERN` (not `BLOCKING` — a consumer may intentionally name a skill not yet in catalog).

### D5 — Public/private boundary

**Decision:** Wayfinder ships:

- The mechanism (loader, resolver, audit checks, output marker).
- `fixtures/demo-overrides.json` — a 2-3-rule fixture used by tests and the demo-mode dispatch path.

Wayfinder does **not** ship a default consumer rule file. When `$DISPATCH_OVERRIDES_PATH` is unset, no overrides load. When set but the file is missing/malformed, the matcher emits `[OVERRIDES ERROR]` to stderr and proceeds with scored behavior (degraded-but-functional — overrides are an enhancement, not a contract).

When `$DISPATCH_OVERRIDES_PATH` is set, a single-line stderr note is emitted exactly once per process: `[dispatch] overrides: N rules loaded from <path>`. **When the env var is unset, no note is emitted** — absence is the default state, and silence avoids adding new stderr output to pipelines that have never opted in to overrides. (Rev 1: previously unconditional; gated per CONCERN-1.)

### D6 — Telemetry

**Decision:** `_write_log_entry` (`_catalog.py:155`) gains an `override_id` field at the **top level** of the log entry (sibling of `output`, `catalog_hash`, `matcher_version`). Value is the matched rule's `id` string, or `null` when no override fired. The decision dict in `output` also carries the new `disposition_source` field (`"override"` | `"scored"`); the top-level `override_id` is redundant with `output.override_id` but materially cheaper to query in NDJSON sweeps.

**Rationale:** The schema change is one field; landing it with the mechanism avoids a follow-up PR. Issue #143 (richer telemetry) remains out of scope — this is the minimum surface the audit-line marker (AC #3) needs to be debuggable post-hoc.

### D7 — Staleness

**Decision:** The dispatch skill's existing mtime check (`_dispatch.py`) is extended to compare the overrides-file mtime against `$DISPATCH_CATALOG_PATH`'s mtime. A newer overrides file is fine; when **overrides.mtime < catalog.mtime** (overrides may reference agents/skills the catalog rebuild renamed or removed), emit `[DISPATCH WARNING] overrides file is older than catalog — rules may reference stale agent/skill names` to stderr but proceed. We do not gate execution on overrides staleness.

**Rationale:** The pre-Explore brief flagged "stale overrides bypass the matcher silently" as a risk. A loud-but-non-fatal warning is the cheapest mitigation; gating would invert the degraded-but-functional contract from D5.

**Out of scope for v1:** A `matcher_version_min` semver gate on individual rules. (Rev 1: removed per CONCERN-2 — was prose-only, never reached the schema/dataclass/loader. Reintroduce in v2 if needed.)

---

## File Structure

**New files:**

- `src/claude_wayfinder/match/_overrides.py` — loader, predicate evaluator, resolver. Pure functions; no I/O beyond `Path.read_text`.
- `src/claude_wayfinder/fixtures/demo-overrides.json` — 2-3 rules exercising each predicate.
- `tests/test_match/test_overrides.py` — unit tests for loader, evaluator, resolver, audit checks.
- `docs/superpowers/specs/2026-05-24-dispatch-overrides.md` — short reviewer-facing spec (frontmatter + schema reference).

**Modified files:**

- `src/claude_wayfinder/match/_main.py` — call override resolver before scoring; short-circuit return on match.
- `src/claude_wayfinder/match/_types.py` — add `OverrideRule`, `OverrideMatch` dataclasses; widen decision-dict contract docstring.
- `src/claude_wayfinder/match/_catalog.py` — extend `_write_log_entry` with `override_id`; add `_resolve_overrides_path()` helper.
- `src/claude_wayfinder/match/_decide.py` — every decision-returning branch tags `disposition_source: "scored"` for symmetry.
- `src/claude_wayfinder/match/__init__.py` — re-export `load_overrides`, `resolve_override` for tests and audit-catalog.
- `src/claude_wayfinder/audit_catalog.py` — register `override-*` rules; extend CLI to accept `--overrides-path`.
- `src/claude_wayfinder/_dispatch.py` — overrides-path mtime warning; per-process stderr line.
- `src/claude_wayfinder/fixtures/demo-prompts.json` — add one prompt that the demo override rule short-circuits.
- `README.md` — one-paragraph "Dispatch overrides" section pointing at the spec.

---

## Phasing

Each phase is independently committable and reviewable. Phases 1-3 are the user-visible mechanism; 4-7 are audit / docs / demo polish.

| Phase | Goal | Definition of Done |
|-------|------|--------------------|
| 1 | Schema + loader | `load_overrides(path)` returns typed rule list; unit tests pass |
| 2 | Resolver | `resolve_override(rules, features) -> OverrideMatch \| None` works |
| 3 | Wire into matcher | `_main.py` short-circuits on match; `disposition_source` tagged on every decision |
| 4 | Audit-catalog rules | 4 new `override-*` rules registered; CLI accepts `--overrides-path` |
| 5 | Telemetry + dispatch-skill staleness | `override_id` in log entries; mtime warning in `_dispatch.py` |
| 6 | Demo fixtures + integration test | end-to-end pipeline test passes with override loaded |
| 7 | Docs (spec + README) | Spec committed; README updated |

---

## Task Breakdown

### Task 1 — Define `OverrideRule` and `OverrideMatch` dataclasses

**Files:**
- Modify: `src/claude_wayfinder/match/_types.py`

- [ ] **Step 1: Write failing test for OverrideRule field shape**

`tests/test_match/test_overrides.py`:

```python
from claude_wayfinder.match._types import OverrideRule, OverrideMatch

def test_override_rule_required_fields():
    rule = OverrideRule(
        id="test-rule",
        decision="delegate",
        agent="code-writer",
        skills=("python",),
        confidence=0.99,
        rationale="test override",
        command_prefix=None,
        path_globs=("**/*.py",),
        tool_mentions=frozenset(),
    )
    assert rule.id == "test-rule"
    assert rule.decision == "delegate"
    assert rule.skills == ("python",)
    assert rule.path_globs == ("**/*.py",)

def test_override_match_carries_rule_and_decision():
    rule = OverrideRule(
        id="r1", decision="self_handle_unaided", agent=None, skills=(),
        confidence=1.0, rationale="bypass", command_prefix="/skip",
        path_globs=(), tool_mentions=frozenset(),
    )
    m = OverrideMatch(rule=rule, matched_predicates=("command_prefix",))
    assert m.rule.id == "r1"
    assert "command_prefix" in m.matched_predicates
```

- [ ] **Step 2: Run test to verify it fails**

`./.venv/Scripts/python.exe -m pytest tests/test_match/test_overrides.py -v`
Expected: FAIL — `ImportError: cannot import name 'OverrideRule'`.

- [ ] **Step 3: Add dataclasses to `_types.py`**

Append after the existing `LaneInfo` block:

```python
@dataclass(frozen=True)
class OverrideRule:
    """A deterministic override rule. See docs/superpowers/specs/2026-05-24-dispatch-overrides.md.

    A rule matches when ALL of its non-empty predicates are satisfied.
    A rule with zero predicates is invalid (caught by audit-catalog).

    Attributes:
        id: Stable rule identifier (kebab-case, unique within the file).
        decision: One of VALID_DECISIONS; the verbatim decision to emit.
        agent: Agent name when decision implies one; None otherwise.
        skills: Skill names emitted verbatim into the decision output.
        confidence: Float in [0.0, 1.0] surfaced as the decision confidence.
        rationale: Human-readable string surfaced as the decision rationale.
        command_prefix: Exact-string match for context.command_prefix, or None.
        path_globs: fnmatch globs; rule matches when ANY path matches ANY glob.
        tool_mentions: Rule matches when intersection with context tools is non-empty.
    """
    id: str
    decision: str
    agent: str | None
    skills: tuple[str, ...]
    confidence: float
    rationale: str
    command_prefix: str | None
    path_globs: tuple[str, ...]
    tool_mentions: frozenset[str]


@dataclass(frozen=True)
class OverrideMatch:
    """Result of a successful override resolution.

    Attributes:
        rule: The matched OverrideRule.
        matched_predicates: Names of predicates that contributed to the match.
    """
    rule: OverrideRule
    matched_predicates: tuple[str, ...]
```

- [ ] **Step 4: Verify test passes**

`./.venv/Scripts/python.exe -m pytest tests/test_match/test_overrides.py -v`
Expected: 2 PASSED.

- [ ] **Step 5: Commit**

```powershell
git add src/claude_wayfinder/match/_types.py tests/test_match/test_overrides.py
git commit -m "feat(#213): add OverrideRule and OverrideMatch dataclasses"
```

---

### Task 2 — Implement `load_overrides()`

**Files:**
- Create: `src/claude_wayfinder/match/_overrides.py`
- Modify: `tests/test_match/test_overrides.py`

- [ ] **Step 1: Write failing test for loader**

Append to `tests/test_match/test_overrides.py`:

```python
import json
import pytest
from pathlib import Path
from claude_wayfinder.match._overrides import (
    load_overrides,
    OverridesError,
)

def _write(tmp_path: Path, payload: dict) -> Path:
    p = tmp_path / "overrides.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p

def test_load_overrides_empty_rules(tmp_path):
    p = _write(tmp_path, {"version": 1, "rules": []})
    assert load_overrides(p) == []

def test_load_overrides_parses_one_rule(tmp_path):
    p = _write(tmp_path, {
        "version": 1,
        "rules": [{
            "id": "py-files-to-code-writer",
            "decision": "delegate",
            "agent": "code-writer",
            "skills": ["python"],
            "confidence": 0.99,
            "rationale": "all py files go to code-writer",
            "predicates": {"path_globs": ["**/*.py"]},
        }],
    })
    rules = load_overrides(p)
    assert len(rules) == 1
    assert rules[0].id == "py-files-to-code-writer"
    assert rules[0].path_globs == ("**/*.py",)
    assert rules[0].tool_mentions == frozenset()
    assert rules[0].command_prefix is None

def test_load_overrides_missing_file_raises(tmp_path):
    with pytest.raises(OverridesError, match="not found"):
        load_overrides(tmp_path / "nope.json")

def test_load_overrides_malformed_json_raises(tmp_path):
    p = tmp_path / "broken.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(OverridesError, match="malformed"):
        load_overrides(p)

def test_load_overrides_invalid_decision_raises(tmp_path):
    p = _write(tmp_path, {
        "version": 1,
        "rules": [{
            "id": "bad", "decision": "not_a_real_decision",
            "agent": None, "skills": [], "confidence": 0.5,
            "rationale": "x", "predicates": {"command_prefix": "/x"},
        }],
    })
    with pytest.raises(OverridesError, match="invalid decision"):
        load_overrides(p)
```

- [ ] **Step 2: Run tests to verify they fail**

`./.venv/Scripts/python.exe -m pytest tests/test_match/test_overrides.py -v`
Expected: 5 import errors.

- [ ] **Step 3: Create `_overrides.py` loader**

```python
"""Override rule loading and resolution for the dispatch matcher (issue #213).

Override rules pre-declare a verbatim decision tuple that the matcher
returns when a rule's predicates match the dispatch context.  Resolution
runs BEFORE scoring; a matched rule short-circuits the entire scoring +
decision-ladder pipeline.

Public surface:
- ``load_overrides(path)`` — parse a JSON rules file into typed OverrideRule list.
- ``resolve_override(rules, features)`` — return the first matching rule, or None.
- ``OverridesError`` — raised on missing/malformed/invalid override files.
"""

from __future__ import annotations

import fnmatch
import json
from pathlib import Path
from typing import Any

from claude_wayfinder.match._types import (
    Features,
    OverrideMatch,
    OverrideRule,
    VALID_DECISIONS,
)


class OverridesError(Exception):
    """Raised when an overrides file cannot be loaded or is invalid."""


def load_overrides(path: Path) -> list[OverrideRule]:
    """Parse a JSON overrides file into a list of OverrideRule.

    Args:
        path: Resolved path to the overrides JSON file.

    Returns:
        Rule list in file order.  Order is significant: resolve_override
        uses first-match-wins semantics.

    Raises:
        OverridesError: If the file is missing, malformed, or any rule
            fails schema validation.
    """
    if not path.exists():
        raise OverridesError(f"overrides file not found at {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise OverridesError(f"malformed JSON in overrides file: {exc}") from exc

    raw_rules = payload.get("rules", [])
    rules: list[OverrideRule] = []
    import sys  # local import; loader is rarely called twice
    for idx, raw in enumerate(raw_rules):
        rule_id = str(raw.get("id", f"rule-{idx}"))
        decision = str(raw.get("decision", ""))
        if decision not in VALID_DECISIONS:
            raise OverridesError(
                f"rule {rule_id!r}: invalid decision {decision!r} "
                f"(must be one of {sorted(VALID_DECISIONS)})"
            )
        predicates = raw.get("predicates", {}) or {}
        raw_conf = float(raw.get("confidence", 1.0))
        clamped_conf = max(0.0, min(1.0, raw_conf))
        if clamped_conf != raw_conf:
            print(
                f"[OVERRIDES WARNING] rule {rule_id!r}: confidence "
                f"{raw_conf} outside [0.0, 1.0], clamped to {clamped_conf}",
                file=sys.stderr,
            )
        rules.append(
            OverrideRule(
                id=rule_id,
                decision=decision,
                agent=raw.get("agent"),
                skills=tuple(raw.get("skills", [])),
                confidence=clamped_conf,
                rationale=str(raw.get("rationale", "")),
                command_prefix=predicates.get("command_prefix"),
                path_globs=tuple(predicates.get("path_globs", [])),
                tool_mentions=frozenset(predicates.get("tool_mentions", [])),
            )
        )
    return rules
```

- [ ] **Step 4: Verify tests pass**

`./.venv/Scripts/python.exe -m pytest tests/test_match/test_overrides.py -v`
Expected: 7 PASSED (2 from Task 1 + 5 new).

- [ ] **Step 5: Commit**

```powershell
git add src/claude_wayfinder/match/_overrides.py tests/test_match/test_overrides.py
git commit -m "feat(#213): add load_overrides() with schema validation"
```

---

### Task 3 — Implement `resolve_override()`

**Files:**
- Modify: `src/claude_wayfinder/match/_overrides.py`
- Modify: `tests/test_match/test_overrides.py`

- [ ] **Step 1: Write failing tests for resolver**

Append to test file:

```python
from claude_wayfinder.match._overrides import resolve_override
from claude_wayfinder.match._types import Features

def _rule(rid="r", **predicates) -> OverrideRule:
    return OverrideRule(
        id=rid, decision="delegate", agent="code-writer",
        skills=("python",), confidence=0.99, rationale="t",
        command_prefix=predicates.get("command_prefix"),
        path_globs=tuple(predicates.get("path_globs", ())),
        tool_mentions=frozenset(predicates.get("tool_mentions", ())),
    )

def test_resolve_override_no_rules_returns_none():
    assert resolve_override([], Features()) is None

def test_resolve_override_path_glob_match():
    rule = _rule(path_globs=("**/*.py",))
    f = Features(paths=("src/foo.py",))
    m = resolve_override([rule], f)
    assert m is not None
    assert m.rule.id == "r"
    assert "path_globs" in m.matched_predicates

def test_resolve_override_command_prefix_match():
    rule = _rule(command_prefix="/deploy")
    f = Features(command_prefix="/deploy")
    m = resolve_override([rule], f)
    assert m is not None

def test_resolve_override_tool_mentions_match():
    rule = _rule(tool_mentions=("Bash",))
    f = Features(tool_mentions=frozenset({"Bash", "Read"}))
    m = resolve_override([rule], f)
    assert m is not None

def test_resolve_override_and_combined_predicates():
    rule = _rule(command_prefix="/x", path_globs=("*.md",))
    # command_prefix matches but no path matches -> no match
    f = Features(command_prefix="/x", paths=("src/foo.py",))
    assert resolve_override([rule], f) is None

def test_resolve_override_first_match_wins():
    r1 = _rule(rid="first", path_globs=("**/*.py",))
    r2 = _rule(rid="second", path_globs=("**/*.py",))
    f = Features(paths=("src/foo.py",))
    m = resolve_override([r1, r2], f)
    assert m.rule.id == "first"

def test_resolve_override_zero_predicates_never_matches():
    # Defense in depth: even if audit-catalog missed it, runtime won't fire.
    rule = _rule()  # no predicates set
    f = Features(paths=("any.py",))
    assert resolve_override([rule], f) is None
```

- [ ] **Step 2: Run tests to verify they fail**

`./.venv/Scripts/python.exe -m pytest tests/test_match/test_overrides.py -v`
Expected: 7 new failures with `ImportError: cannot import name 'resolve_override'`.

- [ ] **Step 3: Add `resolve_override()` to `_overrides.py`**

Append:

```python
def _rule_matches(rule: OverrideRule, features: Features) -> tuple[str, ...]:
    """Return the names of predicates that match, or () on no match.

    Defense-in-depth: a rule with zero predicates returns () so it never
    fires at runtime even if audit-catalog missed it.
    """
    fired: list[str] = []

    has_cp = rule.command_prefix is not None
    has_pg = bool(rule.path_globs)
    has_tm = bool(rule.tool_mentions)

    if not (has_cp or has_pg or has_tm):
        return ()

    if has_cp:
        if features.command_prefix != rule.command_prefix:
            return ()
        fired.append("command_prefix")

    if has_pg:
        path_hit = any(
            fnmatch.fnmatch(p, g) for p in features.paths for g in rule.path_globs
        )
        if not path_hit:
            return ()
        fired.append("path_globs")

    if has_tm:
        if not (rule.tool_mentions & features.tool_mentions):
            return ()
        fired.append("tool_mentions")

    return tuple(fired)


def resolve_override(
    rules: list[OverrideRule],
    features: Features,
) -> OverrideMatch | None:
    """Return the first rule whose predicates all match, or None.

    First-match-wins by file order (see D3 in the implementation plan).

    Args:
        rules: Loaded override rules in file order.
        features: Extracted dispatch context features.

    Returns:
        OverrideMatch on first hit; None when no rule matches.
    """
    for rule in rules:
        fired = _rule_matches(rule, features)
        if fired:
            return OverrideMatch(rule=rule, matched_predicates=fired)
    return None
```

- [ ] **Step 4: Verify tests pass**

`./.venv/Scripts/python.exe -m pytest tests/test_match/test_overrides.py -v`
Expected: 14 PASSED total.

- [ ] **Step 5: Commit**

```powershell
git add src/claude_wayfinder/match/_overrides.py tests/test_match/test_overrides.py
git commit -m "feat(#213): add resolve_override() with first-match-wins semantics"
```

---

### Task 4 — Wire overrides into `_main.py` + tag `disposition_source` on every decision

**Files:**
- Modify: `src/claude_wayfinder/match/_catalog.py` — add `_resolve_overrides_path()`.
- Modify: `src/claude_wayfinder/match/_main.py` — call resolver pre-scoring.
- Modify: `src/claude_wayfinder/match/_decide.py` — tag `disposition_source: "scored"` on every return.
- Modify: `src/claude_wayfinder/match/__init__.py` — export new public names.
- Create: tests in `tests/test_match/test_decide.py` and `tests/test_match/test_integration.py`.

- [ ] **Step 1: Write failing tests for disposition_source field**

Append to `tests/test_match/test_decide.py` (create if missing):

```python
def test_decide_tags_disposition_source_scored_on_delegate(
    sample_scored_agents_high, sample_scored_skills, sample_features, sample_catalog
):
    result = decide(sample_scored_agents_high, sample_scored_skills,
                    sample_features, sample_catalog)
    assert result["disposition_source"] == "scored"

def test_decide_tags_disposition_source_scored_on_needs_more_detail():
    from claude_wayfinder.match._decide import decide
    from claude_wayfinder.match._types import Features
    result = decide([], [], Features(), [])
    assert result["decision"] == "needs_more_detail"
    assert result["disposition_source"] == "scored"
```

(Use whatever fixtures already exist in the file — if absent, build minimal scored entries inline.)

- [ ] **Step 2: Verify tests fail**

`./.venv/Scripts/python.exe -m pytest tests/test_match/test_decide.py -v`
Expected: KeyError or assertion failure on `disposition_source`.

- [ ] **Step 3: Add `disposition_source: "scored"` to every dict returned by `decide()` and `_detect_mixed_content()`**

In `_decide.py`, every `return { ... }` adds `"disposition_source": "scored"`. The returns are at lines ~132 (mixed_content), ~189 (needs_more_detail), ~212 (delegate), ~223 (self_handle), ~263 (advisory), ~273 (self_handle_unaided).

- [ ] **Step 4: Verify decide() tests pass**

`./.venv/Scripts/python.exe -m pytest tests/test_match/test_decide.py -v`
Expected: PASS.

- [ ] **Step 5: Write failing test for override short-circuit in main()**

Append to `tests/test_match/test_integration.py`:

```python
def test_main_short_circuits_on_override(tmp_path, monkeypatch, capsys):
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps({
        "entries": [{
            "name": "code-writer", "kind": "agent",
            "triggers": {"keywords": [{"term": "implement", "weight": 1.0}]},
            "applicable_agents": [], "applicable_skills": [],
            "source": "owned", "routable": True,
        }]
    }), encoding="utf-8")

    overrides_path = tmp_path / "overrides.json"
    overrides_path.write_text(json.dumps({
        "version": 1,
        "rules": [{
            "id": "always-skip", "decision": "self_handle_unaided",
            "agent": None, "skills": [], "confidence": 1.0,
            "rationale": "test override fires",
            "predicates": {"command_prefix": "/skip"},
        }]
    }), encoding="utf-8")

    monkeypatch.setenv("DISPATCH_CATALOG_PATH", str(catalog_path))
    monkeypatch.setenv("DISPATCH_OVERRIDES_PATH", str(overrides_path))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({
        "task_description": "implement feature x",
        "command_prefix": "/skip",
    })))

    from claude_wayfinder.match._main import main
    main([])
    out = json.loads(capsys.readouterr().out)
    assert out["decision"] == "self_handle_unaided"
    assert out["disposition_source"] == "override"
    assert out["override_id"] == "always-skip"
    assert out["rationale"] == "test override fires"
```

- [ ] **Step 6: Add `_resolve_overrides_path()` to `_catalog.py`**

After `_resolve_log_path()`:

```python
def _resolve_overrides_path() -> Path | None:
    """Return the overrides file path from env, or None when disabled.

    Resolution: ``$DISPATCH_OVERRIDES_PATH`` env var only. No auto-discovery.
    """
    val = os.environ.get("DISPATCH_OVERRIDES_PATH")
    return Path(val).expanduser() if val else None
```

- [ ] **Step 7: Wire the resolver into `_main.py`**

After `entries = load_catalog(catalog_path)` and `catalog_hash = _compute_catalog_hash(...)`, and after `features = build_features(context)`:

```python
# --- Load + resolve overrides (issue #213) ---
overrides_path = _resolve_overrides_path()
override_rules: list[OverrideRule] = []
if overrides_path is not None:
    try:
        override_rules = load_overrides(overrides_path)
    except OverridesError as exc:
        print(f"[OVERRIDES ERROR] {exc}; proceeding with scored matching.",
              file=sys.stderr)
    # Stderr note only when consumer has opted in to overrides (Rev 1, CONCERN-1).
    print(
        f"[dispatch] overrides: {len(override_rules)} rules loaded from {overrides_path}",
        file=sys.stderr,
    )

# --- Short-circuit on override match ---
override_match = resolve_override(override_rules, features)
if override_match is not None:
    rule = override_match.rule
    result: dict[str, Any] = {
        "decision": rule.decision,
        "confidence": rule.confidence,
        "rationale": rule.rationale,
        "alternatives": [],
        "disposition_source": "override",
        "override_id": rule.id,
    }
    if rule.agent is not None:
        result["agent"] = rule.agent
    if rule.skills:
        result["skills"] = list(rule.skills)
    _write_log_entry(context, result, catalog_hash, _resolve_log_path(),
                     override_id=rule.id)
    print(json.dumps(result, sort_keys=True), flush=True)
    return
```

Add to imports:

```python
from claude_wayfinder.match._catalog import _resolve_overrides_path
from claude_wayfinder.match._overrides import (
    OverrideRule, OverridesError, load_overrides, resolve_override,
)
```

For the scored path, add `"override_id": None` to the scored-path call to `_write_log_entry` (signature change in Task 7).

- [ ] **Step 8: Re-export public names from `__init__.py`**

Add `load_overrides`, `resolve_override`, `OverridesError`, `OverrideRule`, `OverrideMatch` to `src/claude_wayfinder/match/__init__.py` `__all__`.

- [ ] **Step 9: Verify integration test passes**

`./.venv/Scripts/python.exe -m pytest tests/test_match/test_integration.py::test_main_short_circuits_on_override -v`
Expected: PASS.

- [ ] **Step 10: Commit**

```powershell
git add src/claude_wayfinder/match tests/test_match
git commit -m "feat(#213): short-circuit on override match; tag disposition_source"
```

---

### Task 4b — Wire overrides into `cli.py:run_demo()` (Rev 1, BLOCKING-1)

**Files:**
- Modify: `src/claude_wayfinder/cli.py`
- Modify: `tests/test_cli_dispatch.py` (or appropriate demo-mode test file)

**Context:** `run_demo()` in `cli.py:132-229` calls `build_features` → `_score_catalog` → `decide()` directly, bypassing `_main.py:main()`. Without this task the override resolver never fires in demo mode and Task 8's demo override fixtures are a false green.

- [ ] **Step 1: Write failing test that demo mode honors $DISPATCH_OVERRIDES_PATH**

In `tests/test_cli_dispatch.py`:

```python
def test_run_demo_short_circuits_on_override(tmp_path, monkeypatch, capsys):
    overrides_path = tmp_path / "overrides.json"
    overrides_path.write_text(json.dumps({
        "version": 1,
        "rules": [{
            "id": "demo-override-fires",
            "decision": "self_handle_unaided",
            "agent": None, "skills": [], "confidence": 1.0,
            "rationale": "demo override",
            "predicates": {"command_prefix": "/deploy"},
        }],
    }), encoding="utf-8")
    monkeypatch.setenv("DISPATCH_OVERRIDES_PATH", str(overrides_path))
    # Invoke run_demo with a prompt that has command_prefix=/deploy
    # Assert one of the decision dicts has disposition_source=="override"
    # and override_id=="demo-override-fires".
```

- [ ] **Step 2: Verify failure**

`./.venv/Scripts/python.exe -m pytest tests/test_cli_dispatch.py -k run_demo_short_circuits -v`
Expected: assertion failure — no `disposition_source: "override"` in output.

- [ ] **Step 3: Wire `resolve_override()` into `run_demo()`**

At the top of `run_demo()`, after fixtures are loaded but before the prompt loop:

```python
from claude_wayfinder.match._catalog import _resolve_overrides_path
from claude_wayfinder.match._overrides import (
    OverridesError, load_overrides, resolve_override,
)

overrides_path = _resolve_overrides_path()
override_rules = []
if overrides_path is not None:
    try:
        override_rules = load_overrides(overrides_path)
    except OverridesError as exc:
        print(f"[OVERRIDES ERROR] {exc}; demo proceeding with scored matching.",
              file=sys.stderr)
```

Inside the prompt loop, after `features = build_features(context)` and BEFORE `_score_catalog` / `decide`:

```python
override_match = resolve_override(override_rules, features)
if override_match is not None:
    rule = override_match.rule
    decision = {
        "decision": rule.decision,
        "confidence": rule.confidence,
        "rationale": rule.rationale,
        "alternatives": [],
        "disposition_source": "override",
        "override_id": rule.id,
    }
    if rule.agent is not None:
        decision["agent"] = rule.agent
    if rule.skills:
        decision["skills"] = list(rule.skills)
    # emit decision through the same channel as scored decisions
    _emit_demo_decision(decision, prompt)  # use whatever the existing helper is
    continue
```

For the scored path, also tag `disposition_source: "scored"` and `override_id: None` for symmetry with `_main.py` output.

- [ ] **Step 4: Verify test passes**

`./.venv/Scripts/python.exe -m pytest tests/test_cli_dispatch.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/claude_wayfinder/cli.py tests/test_cli_dispatch.py
git commit -m "feat(#213): wire override resolver into demo-mode dispatch (cli.py)"
```

---

### Task 5 — `override_id` in `_write_log_entry`

**Files:**
- Modify: `src/claude_wayfinder/match/_catalog.py`
- Modify: existing tests that exercise log entries.

- [ ] **Step 1: Write failing test for override_id field**

In `tests/test_match/test_catalog.py`:

```python
def test_write_log_entry_records_override_id(tmp_path):
    log_path = tmp_path / "log.jsonl"
    _write_log_entry({}, {"decision": "delegate"}, "sha256:abc",
                     log_path, override_id="my-rule")
    entry = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert entry["override_id"] == "my-rule"

def test_write_log_entry_override_id_null_default(tmp_path):
    log_path = tmp_path / "log.jsonl"
    _write_log_entry({}, {"decision": "delegate"}, "sha256:abc", log_path)
    entry = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert entry["override_id"] is None
```

- [ ] **Step 2: Verify failure**

`./.venv/Scripts/python.exe -m pytest tests/test_match/test_catalog.py -v -k override_id`
Expected: TypeError on unexpected keyword arg.

- [ ] **Step 3: Extend `_write_log_entry` signature**

```python
def _write_log_entry(
    input_dict: dict[str, Any],
    output_dict: dict[str, Any],
    catalog_hash: str,
    log_path: Path | None,
    override_id: str | None = None,
) -> None:
```

And in the `entry` dict insert `"override_id": override_id,` before the `try:` block.

- [ ] **Step 4: Update the scored-path call in `_main.py` to pass `override_id=None` explicitly** (default is None, but explicit improves grep-ability).

- [ ] **Step 4b: Close JSON-parse-error log gap (Rev 1, BLOCKING-3)**

`_main.py` lines 116-128 emit a `needs_more_detail` dict to stdout on JSON parse error but never call `_write_log_entry`. With `override_id` now a top-level field consumers sweep on (`override_id is None` to find scored decisions), this silent gap becomes a schema hole.

Add a `_write_log_entry({}, result, catalog_hash="", _resolve_log_path(), override_id=None)` call on the parse-error path. Use `catalog_hash=""` since catalog hasn't loaded yet — consumers can distinguish parse-failure entries by the empty hash.

Add unit test in `tests/test_match/test_integration.py`:

```python
def test_main_logs_json_parse_error_entry(tmp_path, monkeypatch, capsys):
    log_path = tmp_path / "log.jsonl"
    monkeypatch.setenv("DISPATCH_LOG_PATH", str(log_path))
    monkeypatch.setattr("sys.stdin", io.StringIO("{not json"))
    from claude_wayfinder.match._main import main
    main([])
    entry = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert entry["override_id"] is None
    assert entry["output"]["decision"] == "needs_more_detail"
```

- [ ] **Step 5: Verify tests pass**

`./.venv/Scripts/python.exe -m pytest tests/test_match/test_catalog.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/claude_wayfinder/match/_catalog.py src/claude_wayfinder/match/_main.py tests/test_match/test_catalog.py
git commit -m "feat(#213): record override_id in dispatch log entries"
```

---

### Task 6 — Audit-catalog rules for overrides

**Files:**
- Modify: `src/claude_wayfinder/audit_catalog.py`
- Modify: `tests/test_audit_catalog.py`

Rules to add (registered via a NEW `@register_override` decorator — see Step 0):

1. `override-zero-predicates` — BLOCKING — rule has no predicate set (would match every context).
2. `override-unknown-skill` — CONCERN — `skills:` names a skill not present in the loaded catalog.
3. `override-unknown-agent` — CONCERN — `agent:` names an agent not present in the loaded catalog (skip when `agent is None`).
4. `override-unreachable` — NIT — two rules share **string-identical** `command_prefix` AND `tool_mentions` AND `path_globs` (the common copy/paste footgun). **Glob-subsumption is NOT checked** (Rev 1, CONCERN-3: fnmatch subsumption is not decidable by set comparison — `**/*.py` subsumes `src/**/*.py` semantically but not under set equality). For `command_prefix` + `tool_mentions` alone, exact-set semantics hold and the check is O(n²) but n is small (rule files are tens, not thousands).
5. `override-invalid-decision` — already caught at load-time by `OverridesError`; audit surfaces the parse error as BLOCKING `override-load-error` when running through the CLI.
6. `override-duplicate-id` — BLOCKING — two rules share the same `id`.
7. `override-tool-case-error` — CONCERN (Rev 1, CONCERN-4) — `tool_mentions:` contains a tool name that does not match canonical casing (e.g. `"bash"` vs `"Bash"`). Reuses `_CANONICAL_TOOLS_LOWER` from existing `rule_tool_name_case_error` (`audit_catalog.py:497`). Without this check, an override rule with lowercase `"bash"` silently never matches because `features.tool_mentions` preserves caller casing (`"Bash"`).

- [ ] **Step 0: Extend the rule-registry contract to support override-aware rules (Rev 1, BLOCKING-2)**

Current `RuleFn = Callable[[list[CatalogEntry]], list[Finding]]` (`audit_catalog.py:81`) cannot receive override rules. Add a parallel registry:

```python
# audit_catalog.py — add alongside existing RuleFn / RULES

OverrideRuleFn = Callable[
    [list[CatalogEntry], list[OverrideRule]],
    list[Finding],
]
OVERRIDE_RULES: list[OverrideRuleFn] = []

def register_override(fn: OverrideRuleFn) -> OverrideRuleFn:
    """Register a rule that needs both catalog entries and override rules."""
    OVERRIDE_RULES.append(fn)
    return fn
```

Extend `run_audit()`:

```python
def run_audit(
    entries: list[CatalogEntry],
    override_rules: list[OverrideRule] | None = None,
) -> list[Finding]:
    findings: list[Finding] = []
    for rule in RULES:
        findings.extend(rule(entries))
    if override_rules:
        for orule in OVERRIDE_RULES:
            findings.extend(orule(entries, override_rules))
    return findings
```

Existing catalog-only rules continue to use `@register` unchanged; new override-aware rules use `@register_override`. No breakage to existing rule contract.

- [ ] **Step 1: Extend audit-catalog CLI to accept `--overrides-path`**

In `audit_catalog.py` arg parser, add:

```python
parser.add_argument(
    "--overrides-path", type=Path, default=None,
    help="Optional path to a dispatch overrides JSON file to audit alongside the catalog.",
)
```

And in `run_audit_cli()`, after `entries = load_catalog(...)`:

```python
override_rules: list[OverrideRule] = []
if args.overrides_path is not None:
    try:
        override_rules = load_overrides(args.overrides_path)
    except OverridesError as exc:
        findings.append(Finding(
            Severity.BLOCKING, "override-load-error", "",
            f"{args.overrides_path}: {exc}",
        ))
```

Then call `findings.extend(run_audit(entries, override_rules))` so override-aware rules see both inputs.

- [ ] **Step 2: Implement each rule with TDD**

For each of the 7 rules listed above, write a failing unit test in `tests/test_audit_catalog.py` (one test per rule, asserting the Finding shape), then implement the rule function with the `@register_override` decorator defined in Step 0.

- [ ] **Step 3: Run the audit-catalog test suite**

`./.venv/Scripts/python.exe -m pytest tests/test_audit_catalog.py -v`
Expected: all PASS, including the 7 new tests.

- [ ] **Step 4: Commit**

```powershell
git add src/claude_wayfinder/audit_catalog.py tests/test_audit_catalog.py
git commit -m "feat(#213): add override-* audit-catalog rules"
```

---

### Task 7 — Dispatch-skill staleness warning

**Files:**
- Modify: `src/claude_wayfinder/_dispatch.py`
- Modify: `tests/test_cli_dispatch.py` (or `tests/test_dispatch_runtimewarning.py`)

- [ ] **Step 1: Write failing test for stale-overrides warning**

```python
def test_dispatch_warns_when_overrides_older_than_catalog(tmp_path, monkeypatch, capsys):
    # Create catalog newer than overrides
    overrides = tmp_path / "overrides.json"
    overrides.write_text('{"version": 1, "rules": []}', encoding="utf-8")
    import time; time.sleep(0.01)
    catalog = tmp_path / "catalog.json"
    catalog.write_text('{"entries": []}', encoding="utf-8")

    monkeypatch.setenv("DISPATCH_CATALOG_PATH", str(catalog))
    monkeypatch.setenv("DISPATCH_OVERRIDES_PATH", str(overrides))
    # Invoke dispatch and assert warning in stderr.
    # (use existing dispatch invocation helpers)
```

- [ ] **Step 2: Implement the mtime comparison in `_dispatch.py`**

Add to existing staleness section: if `$DISPATCH_OVERRIDES_PATH` is set, compare `overrides_path.stat().st_mtime` against `catalog_path.stat().st_mtime`. When `overrides.mtime < catalog.mtime`, emit `[DISPATCH WARNING] overrides file is older than catalog — rules may reference stale agent/skill names` to stderr. Non-fatal. (Rev 1, CONCERN-2: only catalog-mtime comparison; no `matcher_version_min` gate — that was removed from D7.)

- [ ] **Step 3: Verify test passes; commit**

```powershell
git add src/claude_wayfinder/_dispatch.py tests/test_dispatch_runtimewarning.py
git commit -m "feat(#213): warn when overrides file is older than catalog"
```

---

### Task 8 — Demo fixtures + end-to-end test

**Files:**
- Create: `src/claude_wayfinder/fixtures/demo-overrides.json`
- Modify: `src/claude_wayfinder/fixtures/demo-prompts.json`
- Modify: `tests/test_match/test_integration.py` or `tests/test_cli_demo.py`

- [ ] **Step 1: Author `demo-overrides.json`**

Three rules covering each predicate:

```json
{
  "version": 1,
  "rules": [
    {
      "id": "demo-deploy-command",
      "decision": "self_handle_unaided",
      "agent": null,
      "skills": [],
      "confidence": 1.0,
      "rationale": "demo: /deploy short-circuits to manual handling",
      "predicates": {"command_prefix": "/deploy"}
    },
    {
      "id": "demo-docs-to-doc-writer",
      "decision": "delegate",
      "agent": "doc-writer",
      "skills": [],
      "confidence": 1.0,
      "rationale": "demo: any docs/ path routes to doc-writer",
      "predicates": {"path_globs": ["docs/**/*.md"]}
    },
    {
      "id": "demo-write-tool",
      "decision": "advisory",
      "agent": "code-writer",
      "skills": ["python"],
      "confidence": 0.9,
      "rationale": "demo: Write tool mention pre-tags code-writer",
      "predicates": {"tool_mentions": ["Write"]}
    }
  ]
}
```

- [ ] **Step 2: Add a demo prompt that triggers the deploy override**

In `demo-prompts.json`, add one entry with `command_prefix: "/deploy"` and an expected `disposition_source: "override"` outcome.

- [ ] **Step 3: Write E2E test that runs the full pipeline with the demo fixtures**

In `tests/test_match/test_integration.py`, load the bundled `demo-catalog.json` AND `demo-overrides.json`, send each demo prompt through **`_main.py:main()`** (NOT `run_demo()` — that path is covered separately by Task 4b's test, and conflating them would let either path silently regress). Assert that prompts which should hit overrides emit `disposition_source: "override"` and the matching `override_id`. (Rev 1, BLOCKING-1: explicit `_main.py` target prevents the false-green identified in review.)

- [ ] **Step 4: Verify all tests pass**

`./.venv/Scripts/python.exe -m pytest -v`
Expected: full suite green.

- [ ] **Step 5: Commit**

```powershell
git add src/claude_wayfinder/fixtures tests/test_match/test_integration.py
git commit -m "feat(#213): demo override fixtures + end-to-end pipeline test"
```

---

### Task 9 — Reviewer-facing spec + README

**Files:**
- Create: `docs/superpowers/specs/2026-05-24-dispatch-overrides.md`
- Modify: `README.md`

- [ ] **Step 1: Write the spec**

Frontmatter (`touches:` mirrors this plan; `skills_relevant:` same), plus sections: Schema, Predicate vocabulary, Resolution order, Public/private boundary, Telemetry, Audit rules, Out-of-scope. Cross-reference issue #213, this plan, and `glitchwerks/claude-configs` for the consumer migration.

- [ ] **Step 2: Add README section**

One paragraph + a code-block showing the env-var setup and a 2-rule example. Link to the spec.

- [ ] **Step 3: Commit**

```powershell
git add docs/superpowers/specs/2026-05-24-dispatch-overrides.md README.md
git commit -m "docs(#213): spec + README for dispatch overrides"
```

---

## Pre-Merge Verification

- [ ] `./.venv/Scripts/python.exe -m pytest -v` — full suite green.
- [ ] `./.venv/Scripts/python.exe -m claude_wayfinder audit-catalog --catalog-path src/claude_wayfinder/fixtures/demo-catalog.json --overrides-path src/claude_wayfinder/fixtures/demo-overrides.json` — exits 0 with no findings on the demo fixtures.
- [ ] Manual smoke: invoke `dispatch` skill with a context matching a demo override; confirm `disposition_source: "override"` in stdout and `override_id` in the log file.
- [ ] AC checklist in issue #213 — every box ticked off in PR body.
- [ ] PR body contains `Closes #213` (per `CLAUDE.md` Pull Requests rule).
- [ ] **File a `glitchwerks/claude-configs` follow-up issue for AC #7** (consumer migration of prose-policy routing onto the new override mechanism) and record its number in this PR body. Per CLAUDE.md (Issues = single source of truth), this must happen *before* merge so the deferred work is tracked rather than memory-resident. (Rev 1, CONCERN-5.)

## Follow-Up (separate PR, separate repo)

Open a follow-up PR in `glitchwerks/claude-configs` once this PR merges and a new wayfinder release ships:

1. Convert existing prose-policy routing decisions (the ones the consumer maintains) into `dispatch-overrides.json`.
2. Wire `$DISPATCH_OVERRIDES_PATH` into the consumer's session-start env setup.
3. Verify with `audit-catalog --overrides-path …` that no `BLOCKING` rules fire.

This is AC #7 and is intentionally not solved here.

---

## Risks & Mitigations

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|------------|--------|------------|
| 1 | Schema lock-in: v1 predicate vocabulary may not extend cleanly to v2 (`keywords`, `keyword_groups`, `excludes`). | Medium | High — schema break would force a consumer-rule-file migration. | Predicates live under a nested `predicates:` object (Task 2 schema). Adding new predicate keys is a strict superset; the `version: 1` field reserves the right to gate breaking changes behind a major bump. |
| 2 | Stale overrides bypass the matcher silently — a forgotten rule continues to fire long after its design rationale disappears. | High | Medium — wrong routing without warning. | (a) `[dispatch] overrides: N rules loaded` line on every invocation (D5). (b) `override_id` in NDJSON log enables retroactive sweeps. (c) Dispatch-skill mtime warning when overrides is older than catalog (Task 7). |
| 3 | Debuggability: when an override unexpectedly fires, the operator doesn't know which rule did it. | Medium | Medium — surprise routing decision with no audit trail. | `disposition_source: "override"` + `override_id` in decision output (Task 4) + `override_id` in log entry (Task 5) + matched-predicates list returned by `resolve_override` (available for future surface). |

---

## Self-Review

- **Spec coverage:** AC #1 (schema) → Task 2; AC #2 (resolution + short-circuit) → Tasks 3-4; AC #3 (`disposition_source` marker) → Task 4; AC #4 (audit-catalog rules) → Task 6; AC #5 (demo fixtures) → Task 8; AC #6 (docs) → Task 9; AC #7 (consumer migration) → explicitly deferred as follow-up PR. Technical notes — determinism, predicate vocabulary v1, conflict handling, telemetry (`override_id`), skill interaction — all addressed in D1-D7 and traced into tasks.
- **Placeholder scan:** All code blocks show exact content. No "TBD" / "similar to" / "add error handling" placeholders.
- **Type consistency:** `OverrideRule` field names, `OverridesError` class name, `resolve_override()` signature, and `_write_log_entry(..., override_id=…)` kwarg are consistent across Tasks 1-7.

---

## Execution Handoff

Plan complete. Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks, fast iteration. Uses `superpowers:subagent-driven-development`.
2. **Inline Execution** — execute in this session with checkpoints. Uses `superpowers:executing-plans`.

Which approach?
