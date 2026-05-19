---
tracking_issue: 156
touches:
  - skills/frontmatter/
  - src/claude_wayfinder/audit_catalog.py
  - src/claude_wayfinder/cli.py
  - tests/test_audit_catalog.py
  - docs/frontmatter-guide.md
  - docs/integration.md
  - README.md
skills_relevant:
  - python
  - superpowers:test-driven-development
---

# Frontmatter Knowledge Skill + Audit-Catalog CLI — Implementation Plan

**Tracking issue:** [#156](https://github.com/glitchwerks/claude-wayfinder/issues/156)
**Spec:** issue #156 body (verbatim acceptance criteria reproduced inline below)
**Target branch:** `main` (direct — no spec-PR antecedent)
**Implementation branch:** `feature/frontmatter-and-audit-catalog`
**Worktree:** `I:/other/claude-wayfinder/.worktrees/frontmatter-and-audit-catalog`
**Plan author date:** 2026-05-19

---

## 0. Overview

This plan delivers two paired surfaces from issue #156:

1. **Knowledge skill** at `skills/frontmatter/SKILL.md` (slug `claude-wayfinder:frontmatter`) — prose-only matcher-aware authoring guidance, loaded by any agent (`applicable_agents: ["*"]`) on authoring/troubleshooting keywords or the `/frontmatter` slash command.
2. **CLI subcommand** `python -m claude_wayfinder audit-catalog` — deterministic catalog-wide static analysis with `--json`, `--severity`, `--target` flags and a four-tier exit-code contract.

Supporting deliverables: `docs/frontmatter-guide.md` (new), `README.md` and `docs/integration.md` updates, full unit and smoke-test coverage.

### Execution model

Tasks are TDD-disciplined wherever code is involved. Each code task follows: **write failing test → implement → run test green → commit**. Prose tasks (skill body, guide doc) commit on completion without a test cycle.

This plan is intended to be executed via `superpowers:subagent-driven-development`. Each task is self-contained and committable independently.

### Task ordering rationale

- **Tasks 0–1**: setup (worktree, branch).
- **Tasks 2–4**: knowledge-skill authoring (prose). Doc-writer work; independent of the CLI, so it can be parallelized in principle but is sequenced here for a clean commit history.
- **Tasks 5–8**: CLI scaffolding + finding model + first BLOCKING rule (structural). Establishes the `audit_catalog.py` module and the `Finding` type that all subsequent rule tasks plug into.
- **Tasks 9–14**: CONCERN rules, one per task (TDD per rule).
- **Task 15**: NIT rules + exit-code wiring.
- **Tasks 16–17**: `--json`, `--severity`, `--target` flags.
- **Task 18**: smoke run against the live wayfinder catalog (AC line: "Runs cleanly on wayfinder's own catalog").
- **Tasks 19–20**: docs and README/integration updates.
- **Task 21**: PR open, targeting `main` directly.

### Citations and reasonable-call

Where issue #156 provides explicit content (the 10 SKILL.md sections, the rule list, the severity-to-exit-code table, the trigger keyword set), those sections are reproduced inline as task content. Implementers should not need to re-read the issue; this plan is self-contained.

Where the spec is silent (e.g., specific argparse flag layout for `audit-catalog`, internal module structure of `audit_catalog.py`), reasonable engineering calls are made and noted. The author has not asked the user to disambiguate these — per the directive, make the call and continue.

---

## Task 0 — Create the implementation worktree

**Goal:** isolated worktree off `main` for all subsequent work.

**Pre-flight:**

- Confirm `main` is up to date locally:
  ```bash
  git -C I:/other/claude-wayfinder fetch origin
  git -C I:/other/claude-wayfinder pull --ff-only origin main
  ```
- Confirm `.worktrees/` is in `.gitignore` (it is, from earlier worktree-using features in this repo).

**Action:**

```bash
git -C I:/other/claude-wayfinder worktree add \
  .worktrees/frontmatter-and-audit-catalog \
  -b feature/frontmatter-and-audit-catalog main
```

**Verification:**

```bash
git -C I:/other/claude-wayfinder worktree list
```

The new entry `.worktrees/frontmatter-and-audit-catalog [feature/frontmatter-and-audit-catalog]` must appear.

**No commit** — worktree creation is not a content change.

**All subsequent tasks operate inside:** `I:/other/claude-wayfinder/.worktrees/frontmatter-and-audit-catalog`. Use `git -C <worktree-path>` to avoid the no-chained-cd rule.

---

## Task 1 — Confirm catalog freshness for smoke testing later

**Goal:** ensure the live user catalog at `C:/Users/chris/.claude/state/dispatch-catalog.json` reflects current `main` state for Task 18's smoke run. The Explore note flagged the catalog as built 2026-05-19 14:09 with 69 entries; `#135` (keyword_groups) has since merged, so a rebuild is required.

**Action:**

```bash
"I:/other/claude-wayfinder/.worktrees/frontmatter-and-audit-catalog/.venv/Scripts/python.exe" \
  -m claude_wayfinder catalog build
```

(If the worktree `.venv` does not yet exist, create it first per the Python rules: `uv venv .venv` then `uv pip install -e ".[dev]"` from the worktree root.)

**Verification:** the build prints a success line and the catalog file's mtime advances. No commit.

---

## Task 2 — Author `skills/frontmatter/SKILL.md` (sections 1–5)

**Goal:** create the knowledge skill file with the first five of the ten sections specified by issue #156. Splitting the SKILL body across two tasks keeps each commit reviewable (~200–300 lines per half).

**File:** `skills/frontmatter/SKILL.md`

**Frontmatter block** (verbatim):

```yaml
---
name: frontmatter
description: >
  Matcher-aware authoring and troubleshooting knowledge for agent and
  skill trigger frontmatter. Loaded by any agent (router, code-writer,
  doc-writer, project-planner, etc.) when the user wants to write,
  improve, troubleshoot, or understand dispatch frontmatter. Trigger
  this skill whenever the user types /frontmatter, asks "how do I
  write triggers", "how do I make frontmatter for my agent", "what's
  a good keyword weight", "set up triggers", or says "my agent isn't
  being dispatched", "this skill never matches", "my frontmatter
  isn't working", "dispatch isn't picking up", or similar authoring
  or troubleshooting requests around dispatch frontmatter. Covers
  the matcher's seven-decision ladder, scoring math, weight ladder
  {0.25, 0.5, 1.0}, fnmatch path-glob footguns, conflict-pair
  detection, and the audit-catalog CLI pointer.
---
```

**Body — sections 1 through 5** (the implementer writes prose; the content outline below is mandatory but the wording is the implementer's):

### Section 1 — What the matcher consumes

Cover:

- Source precedence: sidecar (`skills/<name>/triggers.yml` or `<plugin>/triggers/agents/<name>.yml` per #142) wins over inline frontmatter when both exist.
- v6 sidecar pattern: colocated `<name>.triggers.yml` next to `SKILL.md` (per #150 — "owned + project agent sidecar overrides"), `triggers/<plugin>/agents/<name>.yml` for plugin-shipped agents (per #142).
- Schema fields: `command_prefixes`, `agent_mentions`, `path_globs`, `keywords` (list of `{term, weight}`), `tool_mentions`, `excludes`, plus `applicable_agents` / `applicable_skills` and `routable`.
- Where each field type is consumed by the matcher.
- Link to `docs/schema.md` for the canonical field reference and `docs/design/trigger-schema.md` for the design discussion.

### Section 2 — The seven-decision ladder

Enumerate each branch with one-sentence trigger conditions:

1. `delegate` — strongest agent score above delegation floor.
2. `self_handle` — top score within self-handle band; skills attach.
3. `self_handle_unaided` — no signal at all; router proceeds without skill attachment.
4. `advisory` — non-routable advisory agent's signal attaches to a self-handle.
5. `ambiguous` — top two routable agents are statistically tied (conflict pair).
6. `ask_user` — reserved in v0.1; not produced by the matcher today.
7. `needs_more_detail` — feature density below the two-dimension floor.

Then draw the **input-side / entry-side distinction explicitly** — these are two separate concerns the skill body must not conflate:

- **Input-side density floor (this section's `needs_more_detail` branch).** The matcher emits `needs_more_detail` when the *user prompt's* extracted `Features` populate fewer than two dimensions (paths, keywords, tools, command prefixes, agent mentions). This is about how thin the *input* is, not how thin any catalog entry's triggers are. A two-word prompt with no file paths and no recognised keywords trips this branch regardless of how rich the catalog is.

- **Entry-side weak-scoring (a calibration footgun, covered in Section 5).** An entry whose triggers populate only one dimension — for instance keywords-only with no `path_globs`, `tool_mentions`, or `command_prefixes` — will score weakly *whenever* the input lacks that dimension, which is most of the time. This is not an unreachability theorem; the entry *can* score if the input happens to fill its one dimension. But it is a calibration smell: any prompt that doesn't mention one of the entry's specific terms scores it at zero, and the score ceiling on prompts that do match is limited (`+0.5 × weight` per keyword hit, clamped at `1.0`).

The Section 5 footgun on "one-dimensional triggers" elaborates the entry-side case. This section's floor is about inputs.

### Section 3 — Scoring math

State the formulas explicitly:

- `command_prefixes` match → score `1.0` (short-circuit).
- `agent_mentions` match → score `1.0` (short-circuit).
- `excludes` match in `features.keywords` → score `0.0` (short-circuit).
- Per `path_globs` match: `+0.4`.
- Per `keywords` match: `+0.5 × weight` (verified at `src/claude_wayfinder/match.py:84` — `_KEYWORD_MULTIPLIER = 0.5`; raised from 0.3 to fix single-keyword skills never attaching).
- Per `tool_mentions` match: `+0.5`.
- Final score is clamped to `1.0`.

Provide a small worked example: one matching path-glob (`**/*.py`) plus one matching weight-`1.0` keyword (`python`) → `0.4 + (0.5 × 1.0) = 0.9`.

**Clamping footgun (call this out explicitly in the skill body).** Because the final score is hard-clamped at `1.0`, stacking additional high-weight keywords past the clamp adds nothing. Example: an entry with one path-glob hit (`+0.4`) and two weight-`1.0` keyword hits (`+0.5` + `+0.5`) sums to `1.4`, which clamps to `1.0` — exactly the same score as one path-glob + one weight-`1.0` keyword. The second high-weight keyword is dead weight in any input that already crossed the ceiling. Practical guidance: once an entry can plausibly score `≥ 1.0` on its highest-signal inputs, prefer broadening *coverage* (more distinct terms at `0.25` / `0.5`) over stacking duplicate `1.0` weights — the latter only inflates the per-input score, which the clamp throws away.

### Section 4 — Trigger field rules

- Weight ladder is exactly `{0.25, 0.5, 1.0}`. Other numeric weights are clamped to the nearest ladder value with a validator warning (see `_clamp_weight` in `build_catalog.py`).
- `keywords` is a list of `{term, weight}` mappings. Bare strings are rejected.
- `path_globs` uses Python `fnmatch` semantics, not gitignore semantics.
- `tool_mentions` is case-sensitive: `Bash`, not `bash`.
- `excludes` matches against `features.keywords` only, not against paths or tools.
- `command_prefixes` should start with `/`.

### Section 5 — Footguns

- **fnmatch `*.py` does not match nested files** — use `**/*.py` if you mean "any .py anywhere under the tree". This is the most common path-glob mistake.
- **Tool names are case-sensitive** — `Bash` not `bash`; `WebFetch` not `webfetch`. Wrong case silently fails to match.
- **`applicable_skills: []` mutes the agent's skill attachment entirely** — only set this if you genuinely want no skill auto-attached.
- **One-dimensional triggers are a calibration footgun (entry-side).** A routable agent with only `keywords` (no `path_globs` / `tool_mentions` / `command_prefixes`) will score zero on any prompt that doesn't happen to mention one of its specific terms — and even on matching prompts the score is bounded by `+0.5 × weight` per hit, clamped at `1.0`. This is *not* the same as the input-side `needs_more_detail` floor in Section 2 (which is about thin user prompts); this is about the entry being weakly reachable in practice across the prompt distribution the matcher actually sees. Pair the keywords with at least one `path_globs`, `tool_mentions`, or `command_prefixes` entry to give the matcher a second dimension to score on.
- **Conflict pairs** — two entries with ≥3 overlapping case-insensitive keywords and no discriminating `path_globs` / `tool_mentions` / `command_prefixes` will produce `ambiguous` decisions whenever both score similarly. Heavy keyword overlap is a design smell; introduce a discriminator (a path-glob unique to one, a tool-mention unique to the other) or rename one of the entries to a more specific scope.

**Verification:**

```bash
"$VENV_PY" -c "import yaml, pathlib; \
  meta = yaml.safe_load(pathlib.Path('skills/frontmatter/SKILL.md').read_text().split('---')[1]); \
  assert meta['name'] == 'frontmatter'; \
  assert 'description' in meta; \
  print('SKILL.md frontmatter parses')"
```

(`$VENV_PY` here is `./.venv/Scripts/python.exe` per the project's Python rules. Subsequent tasks use the same shorthand.)

**Commit:**

```
docs(skill): scaffold claude-wayfinder:frontmatter SKILL.md sections 1-5

Adds the knowledge-skill body covering schema, decision ladder, scoring
math, field rules, and footguns. Part 1 of 2 — sections 6-10 land in the
next commit. Refs #156.
```

---

## Task 3 — `skills/frontmatter/SKILL.md` (sections 6–10) + cross-references

**Goal:** complete the SKILL.md body with the workflow and reference sections.

**File:** continue editing `skills/frontmatter/SKILL.md`.

### Section 6 — Authoring workflow

Step-by-step for "user is writing new frontmatter":

1. Read the agent/skill body in full.
2. Identify prominent terms; weight by centrality:
   - Skill/agent **name** or its core verb → `1.0`.
   - Recurring concept terms → `0.5`.
   - Supporting / contextual terms → `0.25`.
3. Add `path_globs` for any file patterns the body implies (use `**/*.ext` for nested).
4. Add `tool_mentions` for any tool names the body explicitly directs the user toward.
5. Decide `applicable_skills` (for agents) based on which skill-task language the body uses.
6. Prefer the **v6 sidecar location** (`skills/<name>/triggers.yml` for owned skills; `triggers/<plugin>/agents/<name>.yml` for plugin agents) over inline frontmatter — sidecars are easier to diff and review.

### Section 7 — Tuning workflow

Step-by-step for "user is improving existing frontmatter":

1. Read the body and the current triggers side-by-side.
2. Find **stale keywords** — terms in `triggers` that no longer appear in the body.
3. Find **missing keywords** — recurring terms in the body that are not in `triggers`.
4. Check **weight alignment** — does each term's weight reflect its centrality? Demote terms that crept up to `1.0` without justification.
5. Check **conflict-pair risk** — eyeball the catalog (or run `audit-catalog`, see Section 9) for entries with similar keyword sets.
6. Check **structural violations** — bare strings, non-ladder weights, whitespace in terms.

### Section 8 — Troubleshooting workflow

Symptom → cause table for "my agent isn't being dispatched / this skill never matches":

| Symptom | Likely cause |
| ------- | ------------ |
| Routable agent scores 0 on prompts that should match | Unreachable routable: triggers empty or all-zero |
| Score never crosses the delegation floor | One-dimensional triggers — add a `path_globs` or `tool_mentions` |
| Agent matches everything | Keyword set too generic; conflict-pair risk against other entries |
| Skill never attaches to expected agent | `applicable_agents` excludes that agent, or `applicable_skills: []` on the agent mutes attachment |
| Weight you set isn't what the matcher uses | Non-ladder weight got clamped (check `catalog-generation.log`) |
| Specific term never matches | `excludes` self-zero — the term is in the entry's own `excludes` |
| Tool-mention never matches | Case mismatch (`bash` vs `Bash`) |

### Section 9 — When to run the CLI

Pointer block:

> The matcher-aware checks the LLM can't do consistently across all ~70 catalog entries (conflict-pair detection, unreachable-routable scans, structural validation across the whole catalog) live in `python -m claude_wayfinder audit-catalog`. Run it whenever you add or substantially edit a routable agent, before opening a PR that ships new frontmatter, or as a periodic catalog sanity check. See `docs/frontmatter-guide.md` for the rule reference and exit-code contract.

### Section 10 — References

Bulleted list:

- `docs/schema.md` — canonical trigger field reference.
- `docs/design/trigger-schema.md` — design rationale for the schema.
- `docs/frontmatter-guide.md` — extended worked-examples companion to this skill.
- `agent-authoring` skill (in `~/.claude/skills/agent-authoring/`) — broader harness authoring discipline; this skill is its matcher-specific counterpart.

**Verification:**

```bash
grep -c "^## " skills/frontmatter/SKILL.md  # must be >= 10
```

**Commit:**

```
docs(skill): complete frontmatter SKILL.md sections 6-10

Adds authoring, tuning, troubleshooting workflows, the audit-catalog
CLI pointer, and the references section. Skill body is now complete.
Refs #156.
```

---

## Task 4 — `skills/frontmatter/triggers.yml`

**Goal:** sidecar with the trigger configuration. Per the issue: `applicable_agents: ["*"]`, command prefix `/frontmatter`, authoring + troubleshooting + conceptual keywords.

**File:** `skills/frontmatter/triggers.yml`

```yaml
triggers:
  command_prefixes:
    - "/frontmatter"
  keywords:
    - { term: "frontmatter", weight: 1.0 }
    - { term: "triggers", weight: 1.0 }
    - { term: "keywords", weight: 0.5 }
    - { term: "weight", weight: 0.5 }
    - { term: "dispatch", weight: 0.5 }
    - { term: "fnmatch", weight: 1.0 }
    - { term: "path_globs", weight: 1.0 }
    - { term: "applicable_skills", weight: 1.0 }
    - { term: "conflict", weight: 0.5 }
    - { term: "matcher", weight: 0.25 }
    - { term: "routable", weight: 0.25 }
applicable_agents:
  - "*"
```

**Reasonable call:** the issue lists many phrases ("how do I write triggers", "my agent isn't being dispatched") — these are surfaced via the `description:` field in `SKILL.md` (handled by the dispatch matcher's description-keyword extraction), not as explicit `keywords` entries. The keyword list above covers the single-token signal; the phrase signal flows through the description field already authored in Task 2.

**Verification:**

```bash
"$VENV_PY" -c "import yaml; \
  d = yaml.safe_load(open('skills/frontmatter/triggers.yml')); \
  assert d['applicable_agents'] == ['*']; \
  assert '/frontmatter' in d['triggers']['command_prefixes']; \
  assert all(k['weight'] in {0.25, 0.5, 1.0} for k in d['triggers']['keywords']); \
  print('triggers.yml OK')"
```

Then rebuild the catalog and confirm the entry appears:

```bash
"$VENV_PY" -m claude_wayfinder catalog build
"$VENV_PY" -c "import json, os; \
  cat = json.load(open(os.path.expanduser('~/.claude/state/dispatch-catalog.json'))); \
  names = [e['name'] for e in cat['entries']]; \
  assert 'frontmatter' in names, f'frontmatter missing from catalog; got: {names}'; \
  print('catalog contains frontmatter')"
```

**Commit:**

```
feat(skill): wire frontmatter sidecar triggers

Adds skills/frontmatter/triggers.yml with /frontmatter prefix,
matcher-domain keywords, and applicable_agents: ["*"]. Refs #156.
```

---

## Task 5 — TDD: `audit_catalog` module scaffold + `Finding` dataclass

**Goal:** create the `audit_catalog.py` module with the `Finding` dataclass, severity enum, and a `run_audit()` entry point that returns an empty result. This is the minimum scaffold that all subsequent rule tasks plug into.

### Step 5a — write the failing test

**File:** `tests/test_audit_catalog.py` (new)

```python
"""Tests for the ``python -m claude_wayfinder audit-catalog`` subcommand.

Covers:
  - The Finding dataclass and Severity enum.
  - The run_audit() entry point on an empty catalog.
  - Per-rule unit tests (added incrementally by later tasks).
  - End-to-end CLI smoke tests via subprocess.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Module-level imports under test
# ---------------------------------------------------------------------------

from claude_wayfinder.audit_catalog import (
    Finding,
    Severity,
    run_audit,
)
from claude_wayfinder.match import CatalogEntry, Triggers, Keyword


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _empty_triggers() -> Triggers:
    return Triggers(
        command_prefixes=frozenset(),
        agent_mentions=frozenset(),
        path_globs=tuple(),
        keywords=tuple(),
        tool_mentions=frozenset(),
        excludes=frozenset(),
    )


def _entry(name: str, **overrides) -> CatalogEntry:
    """Build a CatalogEntry with sensible defaults for testing."""
    defaults: dict = {
        "name": name,
        "kind": "agent",
        "triggers": _empty_triggers(),
        "applicable_agents": tuple(),
        "applicable_skills": tuple(),
        "source": "owned",
        "routable": True,
    }
    defaults.update(overrides)
    return CatalogEntry(**defaults)


# ---------------------------------------------------------------------------
# Scaffold tests
# ---------------------------------------------------------------------------


class TestFindingDataclass:
    """The Finding type carries severity, rule id, entry name, and message."""

    def test_finding_has_required_fields(self) -> None:
        f = Finding(
            severity=Severity.BLOCKING,
            rule="weight-not-in-ladder",
            entry="example",
            message="weight 0.7 not in {0.25, 0.5, 1.0}",
        )
        assert f.severity == Severity.BLOCKING
        assert f.rule == "weight-not-in-ladder"
        assert f.entry == "example"
        assert "0.7" in f.message


class TestSeverityOrdering:
    """Severity members compare so that BLOCKING > CONCERN > NIT."""

    def test_severity_ordering(self) -> None:
        assert Severity.BLOCKING.exit_code == 3
        assert Severity.CONCERN.exit_code == 2
        assert Severity.NIT.exit_code == 1


class TestRunAuditEmpty:
    """run_audit() on an empty catalog returns no findings."""

    def test_empty_catalog_no_findings(self) -> None:
        findings = run_audit([])
        assert findings == []
```

Run it:

```bash
"$VENV_PY" -m pytest tests/test_audit_catalog.py -x
```

Expect: `ModuleNotFoundError: No module named 'claude_wayfinder.audit_catalog'`.

### Step 5b — implement the scaffold

**File:** `src/claude_wayfinder/audit_catalog.py` (new)

```python
"""Catalog-wide static analysis for the dispatch catalog.

Implements the ``python -m claude_wayfinder audit-catalog`` subcommand.

The module is structured as three layers:

1. ``Finding`` / ``Severity`` — the data model for one issue.
2. ``RULES`` — a registry of pure rule functions, each taking the parsed
   catalog and returning a list of Findings.  Rules are added one per
   subsequent commit in this feature branch.
3. ``run_audit()`` — top-level entry that loads a catalog and applies
   every registered rule.

The CLI shim in ``cli.py`` calls into ``run_audit_cli()`` defined here.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Callable, Iterable

from claude_wayfinder.match import CatalogEntry


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


class Severity(enum.Enum):
    """Audit finding severity.

    Each member's value is the exit code the CLI should return when the
    highest-severity finding is at that level (0 reserved for "no
    findings").  Higher numeric value = more severe.
    """

    NIT = 1
    CONCERN = 2
    BLOCKING = 3

    @property
    def exit_code(self) -> int:
        return self.value


@dataclass(frozen=True)
class Finding:
    """One audit finding.

    Attributes:
        severity: BLOCKING / CONCERN / NIT.
        rule: Stable rule identifier (kebab-case).
        entry: Catalog entry name the finding applies to, or "" for
            catalog-wide findings.
        message: Human-readable description.
    """

    severity: Severity
    rule: str
    entry: str
    message: str


# A rule function takes the full catalog and returns 0+ findings.
RuleFn = Callable[[list[CatalogEntry]], list[Finding]]

# Registry — populated by later tasks via @register.
RULES: list[RuleFn] = []


def register(fn: RuleFn) -> RuleFn:
    """Decorator: add a rule function to the global registry."""
    RULES.append(fn)
    return fn


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run_audit(entries: Iterable[CatalogEntry]) -> list[Finding]:
    """Apply every registered rule to ``entries`` and return all findings.

    Args:
        entries: Parsed catalog entries (typically from ``load_catalog``).

    Returns:
        A flat list of findings, order-stable for a given catalog.
    """
    catalog = list(entries)
    findings: list[Finding] = []
    for rule in RULES:
        findings.extend(rule(catalog))
    return findings
```

### Step 5c — run the test green

```bash
"$VENV_PY" -m pytest tests/test_audit_catalog.py -x
```

All three test classes must pass.

**Commit:**

```
feat(audit): scaffold audit_catalog module with Finding + Severity

Adds src/claude_wayfinder/audit_catalog.py with the Finding dataclass,
Severity enum (NIT=1, CONCERN=2, BLOCKING=3 — exit-code aligned), the
RULES registry, and run_audit() entry point. No rules registered yet —
those land in subsequent commits. Refs #156.
```

---

## Task 5.5 — Prerequisite: allow `load_catalog` on empty entries list

**Goal:** the audit CLI's `test_clean_catalog_exits_zero` (Task 16) and several `tmp_path`-based fixtures in Tasks 16–17 build empty / minimal catalogs (`{"entries": []}`) and pass them through `load_catalog()`. The current implementation at `src/claude_wayfinder/match.py:440-441` raises `ValueError("Catalog contains zero entries.")` on this input, which would break those tests and — more importantly — make the audit CLI unable to operate on the legitimate degraded state of an empty catalog (the same state #506 introduced the catalog-error path for).

The right fix is to relax `load_catalog`: an empty entries list is a valid audit target, not a load error. Returning `tuple()` (or `[]`) lets the audit CLI report "no findings, exit 0" instead of crashing with `[AUDIT ERROR]`.

**Why option (a), not option (b):** the reviewer suggested two routes — relax the loader, or adjust the test fixtures. (a) is the more correct fix because the brittleness lives in the loader, not the tests. An empty catalog is a real-world state operators encounter (fresh checkout before first `catalog build`, or the #506-style degraded catalog with all entries dropped); the audit CLI should give a useful answer on it, not refuse to load.

### Step 5.5a — failing test

Append to `tests/test_match.py` (existing file):

```python
class TestLoadCatalogEmptyEntries:
    """load_catalog accepts an empty entries list (was: raised ValueError).

    Empty catalogs are a valid degraded state (e.g. fresh checkout, or the
    #506 all-entries-dropped path). Callers like audit-catalog need to
    operate on them without a load-time crash.
    """

    def test_empty_entries_returns_empty_tuple(self, tmp_path) -> None:
        from claude_wayfinder.match import load_catalog

        p = tmp_path / "cat.json"
        p.write_text(json.dumps({"entries": []}))
        result = load_catalog(p)
        assert tuple(result) == tuple()
```

Run; expect `ValueError: Catalog contains zero entries.`

### Step 5.5b — implement

Edit `src/claude_wayfinder/match.py` around line 440:

```python
    raw_text = path.read_text(encoding="utf-8")
    catalog = json.loads(raw_text)
    raw_entries: list[dict[str, Any]] = catalog.get("entries", [])
    # Empty entries list is a valid degraded state (#506 catalog-error
    # path, fresh-checkout pre-build). Callers like audit-catalog need
    # to operate on it without crashing. Return an empty list rather
    # than raise.
    entries: list[CatalogEntry] = []
    for raw in raw_entries:
        # ... existing loop body unchanged ...
```

Update the docstring's `Raises:` block — remove the `ValueError: If the catalog has zero entries.` line.

**Sweep for downstream assumptions.** Before committing, grep for callers that depend on the old ValueError behaviour:

```bash
"$VENV_PY" -m pytest tests/ -x  # full suite
grep -rn "Catalog contains zero entries" src/ tests/
```

If any test or caller explicitly catches that ValueError, update them. (The audit CLI's `run_audit_cli` already catches `ValueError`, so its broad except clause stays correct — the empty-catalog path simply never reaches it now.)

### Step 5.5c — green; commit

```
fix(match): allow load_catalog on empty entries list

Empty catalogs are a valid degraded state (fresh checkout pre-build,
#506 all-entries-dropped path). load_catalog now returns an empty list
instead of raising ValueError. Required for audit-catalog (#156) to
operate on empty/minimal test fixtures and on real degraded catalogs.

Refs #156.
```

---

## Task 6 — Wire `audit-catalog` into the CLI

**Goal:** register the `audit-catalog` top-level subcommand in `cli.py` so `python -m claude_wayfinder audit-catalog --help` works. The subcommand currently just loads a catalog and prints "no findings"; flag and rendering work lands in Tasks 16–17.

**Reasonable call:** the issue says "`python -m claude_wayfinder audit-catalog`" — top-level, not nested under `catalog`. This matches the issue text verbatim. (Implementers may wonder why it's not `catalog audit`; the answer is: the issue chose the flatter name, and there's no functional reason to override.)

### Step 6a — failing test

Append to `tests/test_audit_catalog.py`:

```python
# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "claude_wayfinder", *args],
        capture_output=True,
        text=True,
    )


class TestAuditCatalogCliHelp:
    """audit-catalog --help exits 0 and surfaces the documented flags."""

    @pytest.fixture(scope="class")
    def help_output(self) -> str:
        cp = _run_cli("audit-catalog", "--help")
        assert cp.returncode == 0, cp.stderr
        return cp.stdout

    def test_help_lists_json_flag(self, help_output: str) -> None:
        assert "--json" in help_output

    def test_help_lists_severity_flag(self, help_output: str) -> None:
        assert "--severity" in help_output

    def test_help_lists_target_flag(self, help_output: str) -> None:
        assert "--target" in help_output

    def test_help_lists_catalog_flag(self, help_output: str) -> None:
        # Path to the catalog under audit; defaults to the resolved
        # DISPATCH_CATALOG_PATH or ~/.claude/state/dispatch-catalog.json.
        assert "--catalog" in help_output
```

Run; expect failure (subcommand not registered yet).

### Step 6b — implement

Edit `src/claude_wayfinder/cli.py`:

1. Add `from claude_wayfinder import audit_catalog as _audit_mod` near the existing module imports.
2. In `_build_parser()`, after the existing `catalog` block, add:

```python
    # --- audit-catalog subcommand ---
    audit_parser = sub.add_parser(
        "audit-catalog",
        help=(
            "Catalog-wide static analysis: conflict pairs, structural "
            "validation, matcher-aware semantic checks."
        ),
    )
    _audit_mod.add_audit_catalog_args(audit_parser)
```

3. In `main()`, after the existing `if args.command == "catalog":` block, add:

```python
    if args.command == "audit-catalog":
        return _audit_mod.run_audit_cli(args)
```

Edit `src/claude_wayfinder/audit_catalog.py` — add the CLI surface:

```python
# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------

import argparse
import json as _json
import os
from pathlib import Path

from claude_wayfinder.match import load_catalog


def add_audit_catalog_args(parser: argparse.ArgumentParser) -> None:
    """Register audit-catalog flags on ``parser``."""
    parser.add_argument(
        "--catalog",
        type=Path,
        default=None,
        help=(
            "Path to the dispatch catalog JSON to audit. "
            "Defaults to $DISPATCH_CATALOG_PATH or "
            "~/.claude/state/dispatch-catalog.json."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of the text report.",
    )
    parser.add_argument(
        "--severity",
        choices=("blocking", "concern", "nit"),
        default=None,
        help=(
            "Filter findings to this severity level and worse. "
            "Default: show all findings."
        ),
    )
    parser.add_argument(
        "--target",
        default=None,
        help=(
            "Restrict findings to entries whose label contains this "
            "substring. Per-entry findings match against the entry name; "
            "catalog-wide findings (e.g. conflict-pair entries formatted "
            "as 'alpha ↔ beta') match when either side of the pair "
            "label contains the substring — so '--target alpha' surfaces "
            "pairs involving alpha. Default: no filter."
        ),
    )


def _resolve_catalog_path(arg: Path | None) -> Path:
    if arg is not None:
        return arg
    env = os.environ.get("DISPATCH_CATALOG_PATH")
    if env:
        return Path(env)
    return Path.home() / ".claude" / "state" / "dispatch-catalog.json"


def run_audit_cli(args: argparse.Namespace) -> int:
    """CLI entry point for ``audit-catalog``."""
    catalog_path = _resolve_catalog_path(getattr(args, "catalog", None))
    try:
        entries = load_catalog(catalog_path)
    except (FileNotFoundError, _json.JSONDecodeError, ValueError) as exc:
        print(f"[AUDIT ERROR] Failed to load catalog: {exc}", file=__import__("sys").stderr)
        return 1
    findings = run_audit(entries)
    # Rendering + severity filter + exit-code mapping land in Tasks 16-17.
    # For now: print one line per finding and exit 0.
    for f in findings:
        print(f"{f.severity.name:<8}  [{f.rule}]  {f.entry}: {f.message}")
    return 0
```

### Step 6c — green

```bash
"$VENV_PY" -m pytest tests/test_audit_catalog.py -x
```

**Commit:**

```
feat(audit): wire audit-catalog subcommand into cli.py

Adds `python -m claude_wayfinder audit-catalog` with --catalog, --json,
--severity, --target flags. Rendering/filtering/exit-code mapping are
stubbed; rules land in subsequent commits. Refs #156.
```

---

## Task 7 — Rule: weight not in ladder (BLOCKING)

**Goal:** first BLOCKING rule — entry has a keyword whose weight is not in `{0.25, 0.5, 1.0}`.

**Reasonable call:** the existing `_validate_keywords` in `build_catalog.py` already clamps non-ladder weights with a warning at *build* time, so a well-formed catalog never reaches this rule with a non-ladder weight. The rule still exists for catalogs hand-edited or generated by an external tool — defense in depth.

### Step 7a — failing test

Append to `tests/test_audit_catalog.py`:

```python
from claude_wayfinder.audit_catalog import rule_weight_not_in_ladder


class TestWeightNotInLadder:
    """BLOCKING: keyword weight outside {0.25, 0.5, 1.0}."""

    def test_clean_catalog_no_finding(self) -> None:
        e = _entry(
            "ok",
            triggers=Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=tuple(),
                keywords=(Keyword("foo", 1.0), Keyword("bar", 0.5)),
                tool_mentions=frozenset(),
                excludes=frozenset(),
            ),
        )
        assert rule_weight_not_in_ladder([e]) == []

    def test_off_ladder_weight_flagged(self) -> None:
        e = _entry(
            "bad",
            triggers=Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=tuple(),
                keywords=(Keyword("foo", 0.7),),
                tool_mentions=frozenset(),
                excludes=frozenset(),
            ),
        )
        findings = rule_weight_not_in_ladder([e])
        assert len(findings) == 1
        assert findings[0].severity == Severity.BLOCKING
        assert findings[0].entry == "bad"
        assert "0.7" in findings[0].message
```

### Step 7b — implement

Append to `src/claude_wayfinder/audit_catalog.py`:

```python
# ---------------------------------------------------------------------------
# Rule: weight not in ladder (BLOCKING)
# ---------------------------------------------------------------------------

_LADDER: frozenset[float] = frozenset({0.25, 0.5, 1.0})


@register
def rule_weight_not_in_ladder(catalog: list[CatalogEntry]) -> list[Finding]:
    """Flag any keyword whose weight is not in {0.25, 0.5, 1.0}."""
    out: list[Finding] = []
    for e in catalog:
        for kw in e.triggers.keywords:
            if kw.weight not in _LADDER:
                out.append(
                    Finding(
                        severity=Severity.BLOCKING,
                        rule="weight-not-in-ladder",
                        entry=e.name,
                        message=(
                            f"keyword '{kw.term}' weight {kw.weight} "
                            f"not in {{0.25, 0.5, 1.0}}"
                        ),
                    )
                )
    return out
```

### Step 7c — green

```bash
"$VENV_PY" -m pytest tests/test_audit_catalog.py::TestWeightNotInLadder -x
```

**Commit:**

```
feat(audit): rule weight-not-in-ladder (BLOCKING)

Flags any keyword whose weight is outside {0.25, 0.5, 1.0}. Refs #156.
```

---

## Task 8 — Rule: whitespace in keyword term (BLOCKING)

Mirror of Task 7's structure. Issue lists this under BLOCKING: "Whitespace in keyword terms".

### Step 8a — failing test

```python
from claude_wayfinder.audit_catalog import rule_whitespace_in_term


class TestWhitespaceInTerm:
    def test_clean_no_finding(self) -> None:
        e = _entry(
            "ok",
            triggers=Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=tuple(),
                keywords=(Keyword("clean-token", 1.0),),
                tool_mentions=frozenset(),
                excludes=frozenset(),
            ),
        )
        assert rule_whitespace_in_term([e]) == []

    def test_whitespace_flagged(self) -> None:
        e = _entry(
            "bad",
            triggers=Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=tuple(),
                keywords=(Keyword("two words", 1.0),),
                tool_mentions=frozenset(),
                excludes=frozenset(),
            ),
        )
        findings = rule_whitespace_in_term([e])
        assert len(findings) == 1
        assert findings[0].severity == Severity.BLOCKING
        assert "two words" in findings[0].message
```

### Step 8b — implement

```python
@register
def rule_whitespace_in_term(catalog: list[CatalogEntry]) -> list[Finding]:
    """Flag keyword terms containing any whitespace character."""
    out: list[Finding] = []
    for e in catalog:
        for kw in e.triggers.keywords:
            if any(c.isspace() for c in kw.term):
                out.append(
                    Finding(
                        severity=Severity.BLOCKING,
                        rule="whitespace-in-term",
                        entry=e.name,
                        message=(
                            f"keyword term '{kw.term}' contains "
                            "whitespace; matcher only operates on single tokens"
                        ),
                    )
                )
    return out
```

### Step 8c — green; commit

```
feat(audit): rule whitespace-in-term (BLOCKING)

Flags keyword terms containing whitespace — matcher operates on single
tokens only. Refs #156.
```

---

## Task 9 — Rule: duplicate keyword terms within an entry (BLOCKING)

The matcher's catalog loader already last-wins-deduplicates, so this rule only catches hand-edited catalogs that bypass the loader. Still BLOCKING because it indicates the source-of-truth file has duplicates that the user probably didn't intend.

### Step 9a — failing test

```python
from claude_wayfinder.audit_catalog import rule_duplicate_keyword_terms


class TestDuplicateKeywordTerms:
    def test_clean(self) -> None:
        e = _entry(
            "ok",
            triggers=Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=tuple(),
                keywords=(Keyword("a", 1.0), Keyword("b", 0.5)),
                tool_mentions=frozenset(),
                excludes=frozenset(),
            ),
        )
        assert rule_duplicate_keyword_terms([e]) == []

    def test_duplicate_flagged(self) -> None:
        # Note: the in-memory CatalogEntry can hold duplicates only if
        # the loader was bypassed; we construct one directly here.
        e = _entry(
            "dup",
            triggers=Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=tuple(),
                keywords=(Keyword("a", 1.0), Keyword("a", 0.5)),
                tool_mentions=frozenset(),
                excludes=frozenset(),
            ),
        )
        findings = rule_duplicate_keyword_terms([e])
        assert len(findings) == 1
        assert findings[0].severity == Severity.BLOCKING
        assert "'a'" in findings[0].message
```

### Step 9b — implement

```python
@register
def rule_duplicate_keyword_terms(catalog: list[CatalogEntry]) -> list[Finding]:
    """Flag duplicate keyword terms within a single entry."""
    out: list[Finding] = []
    for e in catalog:
        seen: dict[str, int] = {}
        for kw in e.triggers.keywords:
            seen[kw.term] = seen.get(kw.term, 0) + 1
        for term, count in seen.items():
            if count > 1:
                out.append(
                    Finding(
                        severity=Severity.BLOCKING,
                        rule="duplicate-keyword-term",
                        entry=e.name,
                        message=(
                            f"keyword term '{term}' appears {count} times"
                        ),
                    )
                )
    return out
```

### Step 9c — green; commit

```
feat(audit): rule duplicate-keyword-term (BLOCKING)

Flags entries that contain the same keyword term more than once. Refs #156.
```

---

## Task 10 — Rule: fnmatch path-glob footgun (CONCERN)

"`*.py` vs `**/*.py`" — a top-level `*.ext` glob does not match nested files under fnmatch. Heuristic: any `path_globs` entry that is exactly `*.<ext>` (no `**`) is suspicious unless it is the only glob and the entry is clearly file-name-scoped.

**Reasonable call:** flag every bare `*.<ext>` pattern that does not also have a matching `**/*.<ext>` sibling in the same entry. The user can opt-in to keep both. False positives are acceptable at CONCERN level.

### Step 10a — failing test

```python
from claude_wayfinder.audit_catalog import rule_path_glob_footgun


class TestPathGlobFootgun:
    def test_double_star_ok(self) -> None:
        e = _entry(
            "ok",
            triggers=Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=("**/*.py",),
                keywords=tuple(),
                tool_mentions=frozenset(),
                excludes=frozenset(),
            ),
        )
        assert rule_path_glob_footgun([e]) == []

    def test_bare_star_ext_flagged(self) -> None:
        e = _entry(
            "footgun",
            triggers=Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=("*.py",),
                keywords=tuple(),
                tool_mentions=frozenset(),
                excludes=frozenset(),
            ),
        )
        findings = rule_path_glob_footgun([e])
        assert len(findings) == 1
        assert findings[0].severity == Severity.CONCERN
        assert "*.py" in findings[0].message

    def test_bare_with_double_star_sibling_ok(self) -> None:
        # If both `*.py` and `**/*.py` are present, the author opted in.
        e = _entry(
            "both",
            triggers=Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=("*.py", "**/*.py"),
                keywords=tuple(),
                tool_mentions=frozenset(),
                excludes=frozenset(),
            ),
        )
        assert rule_path_glob_footgun([e]) == []
```

### Step 10b — implement

```python
import re as _re

# Matches `*.<ext>` with a single alphanumeric extension component only.
# Does NOT match compound extensions like `*.tar.gz` or `*.min.js` —
# those are uncommon enough in dispatch globs that the false-negative
# is acceptable, and the rule body's "add a `**/*.<ext>` sibling"
# suggestion would be wrong for them anyway (the correct sibling for
# `*.tar.gz` is `**/*.tar.gz`, not `**/*.gz`). Future maintainers:
# extend this regex to compound extensions only if you also extend
# the sibling-suggestion logic in the rule body.
_BARE_EXT_RE = _re.compile(r"^\*\.[A-Za-z0-9]+$")


@register
def rule_path_glob_footgun(catalog: list[CatalogEntry]) -> list[Finding]:
    """Flag bare `*.<ext>` path-globs missing a `**/*.<ext>` sibling."""
    out: list[Finding] = []
    for e in catalog:
        globs = set(e.triggers.path_globs)
        for g in e.triggers.path_globs:
            if _BARE_EXT_RE.match(g):
                ext = g[2:]  # strip "*."
                if f"**/*.{ext}" not in globs:
                    out.append(
                        Finding(
                            severity=Severity.CONCERN,
                            rule="path-glob-footgun",
                            entry=e.name,
                            message=(
                                f"path_glob '{g}' matches only top-level "
                                f"files under fnmatch; use '**/*.{ext}' "
                                "for nested matching or add it as a sibling"
                            ),
                        )
                    )
    return out
```

### Step 10c — green; commit

```
feat(audit): rule path-glob-footgun (CONCERN)

Flags bare `*.<ext>` path_globs that lack a `**/*.<ext>` sibling —
the fnmatch semantics are surprising. Refs #156.
```

---

## Task 11 — Rule: tool-name case error (CONCERN)

Tool names are case-sensitive. Maintain a canonical case map of known Claude Code tool names: `Agent`, `Bash`, `Edit`, `Glob`, `Grep`, `Monitor`, `NotebookEdit`, `Read`, `Skill`, `TaskCreate`, `ToolSearch`, `WebFetch`, `WebSearch`, `Write`. Any `tool_mentions` entry whose lowercased form matches a canonical name but whose actual casing differs is a CONCERN. Keep this prose list in sync with `_CANONICAL_TOOLS` in the implementation — drift between the two is a maintenance hazard.

### Step 11a — failing test

```python
from claude_wayfinder.audit_catalog import rule_tool_name_case_error


class TestToolNameCaseError:
    def test_correct_case_ok(self) -> None:
        e = _entry(
            "ok",
            triggers=Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=tuple(),
                keywords=tuple(),
                tool_mentions=frozenset({"Bash"}),
                excludes=frozenset(),
            ),
        )
        assert rule_tool_name_case_error([e]) == []

    def test_wrong_case_flagged(self) -> None:
        e = _entry(
            "bad",
            triggers=Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=tuple(),
                keywords=tuple(),
                tool_mentions=frozenset({"bash"}),
                excludes=frozenset(),
            ),
        )
        findings = rule_tool_name_case_error([e])
        assert len(findings) == 1
        assert findings[0].severity == Severity.CONCERN
        assert "bash" in findings[0].message
        assert "Bash" in findings[0].message

    def test_unknown_tool_not_flagged(self) -> None:
        # Unknown tool names are passed through — only known tools with
        # wrong case are flagged.
        e = _entry(
            "unknown",
            triggers=Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=tuple(),
                keywords=tuple(),
                tool_mentions=frozenset({"CustomToolXYZ"}),
                excludes=frozenset(),
            ),
        )
        assert rule_tool_name_case_error([e]) == []
```

### Step 11b — implement

```python
# Canonical case-correct names for tools the matcher recognises.
# Extend cautiously — adding a name here can flag previously-clean
# catalogs.
_CANONICAL_TOOLS: tuple[str, ...] = (
    "Agent",
    "Bash",
    "Edit",
    "Glob",
    "Grep",
    "Monitor",
    "NotebookEdit",
    "Read",
    "Skill",
    "TaskCreate",
    "ToolSearch",
    "WebFetch",
    "WebSearch",
    "Write",
)
_CANONICAL_TOOLS_LOWER: dict[str, str] = {t.lower(): t for t in _CANONICAL_TOOLS}


@register
def rule_tool_name_case_error(catalog: list[CatalogEntry]) -> list[Finding]:
    """Flag tool_mentions matching a known tool but with wrong case."""
    out: list[Finding] = []
    for e in catalog:
        for tm in sorted(e.triggers.tool_mentions):
            canonical = _CANONICAL_TOOLS_LOWER.get(tm.lower())
            if canonical is not None and canonical != tm:
                out.append(
                    Finding(
                        severity=Severity.CONCERN,
                        rule="tool-name-case-error",
                        entry=e.name,
                        message=(
                            f"tool_mention '{tm}' is case-incorrect; "
                            f"matcher expects '{canonical}'"
                        ),
                    )
                )
    return out
```

### Step 11c — green; commit

```
feat(audit): rule tool-name-case-error (CONCERN)

Flags tool_mentions that match a canonical tool name (case-insensitive)
but have wrong casing. Tool-mention matching is case-sensitive. Refs #156.
```

---

## Task 12 — Rule: one-dimensional triggers (CONCERN)

A routable entry whose triggers populate only one input dimension cannot reach `delegate` — the matcher's feature-density floor requires two dimensions. Dimensions: `command_prefixes`, `agent_mentions`, `path_globs`, `keywords`, `tool_mentions`. (`excludes` is not a positive dimension.)

### Step 12a — failing test

```python
from claude_wayfinder.audit_catalog import rule_one_dimensional_triggers


class TestOneDimensionalTriggers:
    def test_two_dimensions_ok(self) -> None:
        e = _entry(
            "ok",
            triggers=Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=("**/*.py",),
                keywords=(Keyword("python", 1.0),),
                tool_mentions=frozenset(),
                excludes=frozenset(),
            ),
        )
        assert rule_one_dimensional_triggers([e]) == []

    def test_only_keywords_flagged(self) -> None:
        e = _entry(
            "thin",
            triggers=Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=tuple(),
                keywords=(Keyword("python", 1.0),),
                tool_mentions=frozenset(),
                excludes=frozenset(),
            ),
        )
        findings = rule_one_dimensional_triggers([e])
        assert len(findings) == 1
        assert findings[0].severity == Severity.CONCERN
        assert "one dimension" in findings[0].message.lower() or "dimension" in findings[0].message.lower()

    def test_non_routable_not_flagged(self) -> None:
        # Skills and non-routable agents are not subject to the floor.
        e = _entry(
            "skill-thin",
            kind="skill",
            routable=False,
            triggers=Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=tuple(),
                keywords=(Keyword("python", 1.0),),
                tool_mentions=frozenset(),
                excludes=frozenset(),
            ),
        )
        assert rule_one_dimensional_triggers([e]) == []
```

### Step 12b — implement

```python
def _trigger_dimensions(t) -> int:
    """Count the number of populated positive dimensions on a Triggers."""
    return sum(
        1
        for present in (
            bool(t.command_prefixes),
            bool(t.agent_mentions),
            bool(t.path_globs),
            bool(t.keywords),
            bool(t.tool_mentions),
        )
        if present
    )


@register
def rule_one_dimensional_triggers(catalog: list[CatalogEntry]) -> list[Finding]:
    """Flag routable agents that populate only one trigger dimension."""
    out: list[Finding] = []
    for e in catalog:
        if e.kind != "agent" or not e.routable:
            continue
        dims = _trigger_dimensions(e.triggers)
        if dims == 1:
            out.append(
                Finding(
                    severity=Severity.CONCERN,
                    rule="one-dimensional-triggers",
                    entry=e.name,
                    message=(
                        "routable agent populates only one trigger "
                        "dimension; matcher's feature-density floor "
                        "requires two — agent may be unreachable"
                    ),
                )
            )
    return out
```

### Step 12c — green; commit

```
feat(audit): rule one-dimensional-triggers (CONCERN)

Flags routable agents whose triggers populate fewer than two positive
dimensions — they cannot satisfy the matcher's feature-density floor.
Refs #156.
```

---

## Task 13 — Rule: unreachable routable agent (CONCERN)

Routable agent with **zero** positive dimensions. Stricter than Task 12.

### Step 13a — failing test

```python
from claude_wayfinder.audit_catalog import rule_unreachable_routable


class TestUnreachableRoutable:
    def test_empty_routable_flagged(self) -> None:
        e = _entry(
            "ghost",
            kind="agent",
            routable=True,
            triggers=Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=tuple(),
                keywords=tuple(),
                tool_mentions=frozenset(),
                excludes=frozenset(),
            ),
        )
        findings = rule_unreachable_routable([e])
        assert len(findings) == 1
        assert findings[0].severity == Severity.CONCERN
        assert findings[0].entry == "ghost"

    def test_one_dim_not_flagged_here(self) -> None:
        # The 1-dim case is handled by rule_one_dimensional_triggers.
        e = _entry(
            "thin",
            triggers=Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=tuple(),
                keywords=(Keyword("x", 0.25),),
                tool_mentions=frozenset(),
                excludes=frozenset(),
            ),
        )
        assert rule_unreachable_routable([e]) == []

    def test_non_routable_skipped(self) -> None:
        e = _entry(
            "advisory",
            kind="agent",
            routable=False,
            triggers=Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=tuple(),
                keywords=tuple(),
                tool_mentions=frozenset(),
                excludes=frozenset(),
            ),
        )
        assert rule_unreachable_routable([e]) == []
```

### Step 13b — implement

```python
@register
def rule_unreachable_routable(catalog: list[CatalogEntry]) -> list[Finding]:
    """Flag routable agents with zero positive trigger dimensions."""
    out: list[Finding] = []
    for e in catalog:
        if e.kind != "agent" or not e.routable:
            continue
        if _trigger_dimensions(e.triggers) == 0:
            out.append(
                Finding(
                    severity=Severity.CONCERN,
                    rule="unreachable-routable",
                    entry=e.name,
                    message=(
                        "routable agent has no positive triggers; "
                        "matcher will never produce delegate for it"
                    ),
                )
            )
    return out
```

### Step 13c — green; commit

```
feat(audit): rule unreachable-routable (CONCERN)

Flags routable agents with zero positive trigger dimensions. Refs #156.
```

---

## Task 14 — Rule: conflict pairs (CONCERN)

The headline matcher-aware rule. Two routable agents with ≥3 case-insensitive overlapping `keywords` terms and **no discriminating** `path_globs` / `tool_mentions` / `command_prefixes` (i.e., the sets of those three fields on the two entries are either both empty or identical) form a conflict pair.

### Step 14a — failing test

```python
from claude_wayfinder.audit_catalog import rule_conflict_pairs


class TestConflictPairs:
    def _e(self, name: str, terms: list[str], **overrides) -> CatalogEntry:
        return _entry(
            name,
            triggers=Triggers(
                command_prefixes=frozenset(overrides.get("cp", set())),
                agent_mentions=frozenset(),
                path_globs=tuple(overrides.get("pg", ())),
                keywords=tuple(Keyword(t, 1.0) for t in terms),
                tool_mentions=frozenset(overrides.get("tm", set())),
                excludes=frozenset(),
            ),
            **{k: v for k, v in overrides.items() if k not in {"cp", "pg", "tm"}},
        )

    def test_no_overlap_clean(self) -> None:
        a = self._e("a", ["one", "two", "three"])
        b = self._e("b", ["four", "five", "six"])
        assert rule_conflict_pairs([a, b]) == []

    def test_two_overlap_clean(self) -> None:
        a = self._e("a", ["one", "two", "three"])
        b = self._e("b", ["one", "two", "nine"])
        assert rule_conflict_pairs([a, b]) == []

    def test_three_overlap_no_discriminator_flagged(self) -> None:
        a = self._e("a", ["one", "two", "three", "four"])
        b = self._e("b", ["one", "two", "three", "nine"])
        findings = rule_conflict_pairs([a, b])
        assert len(findings) == 1
        assert findings[0].severity == Severity.CONCERN
        assert findings[0].rule == "conflict-pair"
        assert "a" in findings[0].message and "b" in findings[0].message

    def test_three_overlap_with_asymmetric_discriminator_clean(self) -> None:
        # b has a unique path_glob (a has none) — asymmetric discriminator.
        # The matcher can break the tie on any input that fills path_globs,
        # so this is not a conflict.
        a = self._e("a", ["one", "two", "three"])
        b = self._e("b", ["one", "two", "three"], pg=["**/*.py"])
        assert rule_conflict_pairs([a, b]) == []

    def test_three_overlap_disjoint_globs_flagged(self) -> None:
        # Both agents have non-empty path_globs but the sets are disjoint
        # (a covers .py, b covers .ts). The OLD signature-equality check
        # cleared this pair because the sigs differ. The CORRECT check
        # asks whether the discriminator is *single-sided-asymmetric* —
        # one side empty, one side non-empty. Here both sides are non-
        # empty, so neither agent is the "unscoped fallback" the matcher
        # can demote on path-bearing prompts. On the typical no-path
        # prompt, both score identically on the keyword overlap and the
        # matcher emits ambiguous. That is the failure mode this rule
        # must catch.
        a = self._e("a", ["one", "two", "three"], pg=["**/*.py"])
        b = self._e("b", ["one", "two", "three"], pg=["**/*.ts"])
        findings = rule_conflict_pairs([a, b])
        assert len(findings) == 1
        assert findings[0].rule == "conflict-pair"

    def test_case_insensitive_overlap(self) -> None:
        a = self._e("a", ["One", "Two", "Three"])
        b = self._e("b", ["one", "two", "three"])
        findings = rule_conflict_pairs([a, b])
        assert len(findings) == 1

    def test_non_routable_skipped(self) -> None:
        a = self._e("a", ["one", "two", "three"], routable=False)
        b = self._e("b", ["one", "two", "three"])
        assert rule_conflict_pairs([a, b]) == []
```

### Step 14b — implement

```python
# Discriminator fields are those the matcher uses to break ties beyond
# the shared keyword overlap. A discriminator only reliably breaks a
# tie when one agent is *more specific* on that dimension and the other
# is *unscoped* — i.e., one agent's set is empty while the other's is
# not. Two non-empty disjoint sets do NOT break the tie across the
# typical prompt distribution: on prompts that fill neither agent's
# dimension (the common case for path_globs, since most prompts have
# no file paths), both agents score identically on keywords and the
# matcher produces an ambiguous decision.
_DISCRIMINATOR_FIELDS: tuple[str, ...] = (
    "command_prefixes",
    "tool_mentions",
    "path_globs",
)


def _discriminator_sets(t) -> dict[str, frozenset[str]]:
    """Per-field discriminator sets for a Triggers object."""
    return {
        "command_prefixes": frozenset(t.command_prefixes),
        "tool_mentions": frozenset(t.tool_mentions),
        "path_globs": frozenset(t.path_globs),
    }


def _has_breaking_discriminator(
    a_sets: dict[str, frozenset[str]],
    b_sets: dict[str, frozenset[str]],
) -> bool:
    """True iff some discriminator field is single-sided-asymmetric.

    Single-sided-asymmetric on field F means exactly one of (a[F], b[F])
    is empty and the other is non-empty. That is the only case where the
    discriminator reliably breaks the tie across the prompt distribution
    — the unscoped agent loses to the scoped agent when the input fills
    the field, and they tie on overlapping keywords when it doesn't, so
    the *scored-not-tied* subspace strictly favours one agent.

    Two non-empty disjoint sets (e.g. ``**/*.py`` vs ``**/*.ts``) do NOT
    qualify — on the common case of prompts with no file paths, neither
    agent's path_globs fire, both score identically on keywords, and the
    matcher emits ambiguous.
    """
    for field in _DISCRIMINATOR_FIELDS:
        a_empty = not a_sets[field]
        b_empty = not b_sets[field]
        if a_empty != b_empty:
            # Exactly one is empty → single-sided asymmetry.
            return True
    return False


@register
def rule_conflict_pairs(catalog: list[CatalogEntry]) -> list[Finding]:
    """Flag pairs of routable agents with heavy keyword overlap & no breaking discriminator."""
    routable_agents = [
        e for e in catalog if e.kind == "agent" and e.routable
    ]
    out: list[Finding] = []
    for i, a in enumerate(routable_agents):
        a_terms = {kw.term.lower() for kw in a.triggers.keywords}
        a_sets = _discriminator_sets(a.triggers)
        for b in routable_agents[i + 1:]:
            b_terms = {kw.term.lower() for kw in b.triggers.keywords}
            overlap = a_terms & b_terms
            if len(overlap) < 3:
                continue
            b_sets = _discriminator_sets(b.triggers)
            # Only single-sided-asymmetric discriminators reliably break
            # the tie. Disjoint non-empty sets (two specialists in
            # different domains) still tie on keyword-only prompts.
            if _has_breaking_discriminator(a_sets, b_sets):
                continue
            out.append(
                Finding(
                    severity=Severity.CONCERN,
                    rule="conflict-pair",
                    entry=f"{a.name} ↔ {b.name}",
                    message=(
                        f"agents '{a.name}' and '{b.name}' share "
                        f"{len(overlap)} keywords ({sorted(overlap)}) "
                        "with no discriminating path_globs/tool_mentions/"
                        "command_prefixes — matcher will produce ambiguous"
                    ),
                )
            )
    return out
```

### Step 14c — green; commit

```
feat(audit): rule conflict-pair (CONCERN)

Flags pairs of routable agents with >=3 overlapping case-insensitive
keywords where no discriminator field (command_prefixes / tool_mentions
/ path_globs) is single-sided-asymmetric. Single-sided-asymmetric means
exactly one side's set is empty — the only configuration where the
discriminator reliably breaks the tie across the prompt distribution.
Disjoint non-empty sets (e.g. `**/*.py` vs `**/*.ts`) do NOT qualify,
because on the common no-path prompt both agents score identically on
keywords. Refs #156.
```

---

## Task 15 — NIT rules + remaining CONCERN rules

This task bundles the smaller rules to keep the plan length manageable. Each rule still gets its own test class.

### Rules to implement in this commit

1. **`rule_excludes_overlap_own_keywords` (CONCERN)** — an entry whose `excludes` set intersects its own `keywords` terms (case-insensitive) self-zeros every time those terms appear.
2. **`rule_source_routable_mismatch` (CONCERN)** — `source == "plugin"` with `routable == True` is the spec-flagged mismatch.
3. **`rule_empty_applicable_agents` (NIT)** — skill with `applicable_agents == ()` (treat empty as "any agent" only if explicitly `["*"]`; bare empty is a NIT because the build pipeline can't tell intent).
4. **`rule_duplicate_trigger_set` (NIT)** — two agents with identical full trigger configurations but different `applicable_skills` — likely a copy-paste.

### Tests

One `Test...` class per rule, following the pattern in Tasks 7–14. Two of the four rules have non-obvious edges and require the explicit cases below; the other two (`rule_source_routable_mismatch`, `rule_empty_applicable_agents`) are mechanical and can follow the standard "clean → no finding / dirty → one finding" two-case pattern.

**`TestExcludesOverlapOwnKeywords` (non-obvious edges):**

```python
from claude_wayfinder.audit_catalog import rule_excludes_overlap_own_keywords


class TestExcludesOverlapOwnKeywords:
    def test_no_excludes_clean(self) -> None:
        # Empty excludes — nothing to overlap.
        e = _entry(
            "ok",
            triggers=Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=tuple(),
                keywords=(Keyword("python", 1.0),),
                tool_mentions=frozenset(),
                excludes=frozenset(),
            ),
        )
        assert rule_excludes_overlap_own_keywords([e]) == []

    def test_disjoint_excludes_clean(self) -> None:
        # Excludes present but disjoint from own keywords — the common
        # legitimate case (excludes are meant to dampen other agents'
        # matches, not self-zero).
        e = _entry(
            "ok",
            triggers=Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=tuple(),
                keywords=(Keyword("python", 1.0),),
                tool_mentions=frozenset(),
                excludes=frozenset({"javascript"}),
            ),
        )
        assert rule_excludes_overlap_own_keywords([e]) == []

    def test_self_zero_overlap_flagged(self) -> None:
        e = _entry(
            "selfzero",
            triggers=Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=tuple(),
                keywords=(Keyword("python", 1.0),),
                tool_mentions=frozenset(),
                excludes=frozenset({"python"}),
            ),
        )
        findings = rule_excludes_overlap_own_keywords([e])
        assert len(findings) == 1
        assert findings[0].severity == Severity.CONCERN
        assert "python" in findings[0].message

    def test_case_insensitive_overlap_flagged(self) -> None:
        # The matcher lowercases both sides — "Python" in keywords with
        # "python" in excludes still self-zeros.
        e = _entry(
            "case",
            triggers=Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=tuple(),
                keywords=(Keyword("Python", 1.0),),
                tool_mentions=frozenset(),
                excludes=frozenset({"python"}),
            ),
        )
        assert len(rule_excludes_overlap_own_keywords([e])) == 1
```

**`TestDuplicateTriggerSet` (non-obvious edges):**

```python
from claude_wayfinder.audit_catalog import rule_duplicate_trigger_set


class TestDuplicateTriggerSet:
    def _shared_triggers(self) -> Triggers:
        return Triggers(
            command_prefixes=frozenset(),
            agent_mentions=frozenset(),
            path_globs=("**/*.py",),
            keywords=(Keyword("python", 1.0),),
            tool_mentions=frozenset(),
            excludes=frozenset(),
        )

    def test_single_entry_no_finding(self) -> None:
        e = _entry("solo", triggers=self._shared_triggers())
        assert rule_duplicate_trigger_set([e]) == []

    def test_identical_triggers_identical_skills_no_finding(self) -> None:
        # Same triggers AND same applicable_skills — not a copy-paste
        # smell, just two pointers at the same dispatch shape.
        t = self._shared_triggers()
        a = _entry("a", triggers=t, applicable_skills=("python",))
        b = _entry("b", triggers=t, applicable_skills=("python",))
        # Rule fires only when applicable_skills DIFFER (per Task 15 impl).
        assert rule_duplicate_trigger_set([a, b]) == []

    def test_identical_triggers_different_skills_flagged(self) -> None:
        t = self._shared_triggers()
        a = _entry("a", triggers=t, applicable_skills=("python",))
        b = _entry(
            "b", triggers=t, applicable_skills=("python", "testing"),
        )
        findings = rule_duplicate_trigger_set([a, b])
        assert len(findings) == 1
        assert findings[0].severity == Severity.NIT
        assert "a" in findings[0].entry and "b" in findings[0].entry

    def test_both_empty_skills_no_finding(self) -> None:
        # Edge case: two agents with identical triggers and BOTH have
        # empty applicable_skills. The rule fires on *differing* skill
        # sets; empty == empty does not differ.
        t = self._shared_triggers()
        a = _entry("a", triggers=t, applicable_skills=())
        b = _entry("b", triggers=t, applicable_skills=())
        assert rule_duplicate_trigger_set([a, b]) == []

    def test_skills_only_skill_kind_not_flagged(self) -> None:
        # The rule scopes to kind == "agent". Skills with duplicate
        # triggers aren't a copy-paste smell in the same way.
        t = self._shared_triggers()
        a = _entry("a", kind="skill", triggers=t, applicable_skills=("x",))
        b = _entry("b", kind="skill", triggers=t, applicable_skills=("y",))
        assert rule_duplicate_trigger_set([a, b]) == []
```

For `rule_source_routable_mismatch` and `rule_empty_applicable_agents`, use the standard pattern: one "clean → []" case, one "dirty → 1 finding with expected severity/entry" case. Both rules are single-predicate checks with no edges worth elaborating beyond the rule's literal definition.

### Implementation

```python
@register
def rule_excludes_overlap_own_keywords(
    catalog: list[CatalogEntry],
) -> list[Finding]:
    out: list[Finding] = []
    for e in catalog:
        own_terms = {kw.term.lower() for kw in e.triggers.keywords}
        ex = {x.lower() for x in e.triggers.excludes}
        overlap = own_terms & ex
        if overlap:
            out.append(
                Finding(
                    severity=Severity.CONCERN,
                    rule="excludes-overlap-own-keywords",
                    entry=e.name,
                    message=(
                        f"excludes overlap own keywords {sorted(overlap)}"
                        " — entry self-zeros when those terms appear"
                    ),
                )
            )
    return out


@register
def rule_source_routable_mismatch(catalog: list[CatalogEntry]) -> list[Finding]:
    out: list[Finding] = []
    for e in catalog:
        if e.kind == "agent" and e.source == "plugin" and e.routable:
            out.append(
                Finding(
                    severity=Severity.CONCERN,
                    rule="source-routable-mismatch",
                    entry=e.name,
                    message=(
                        "plugin-sourced agent marked routable=true; "
                        "plugin agents are advisory by default"
                    ),
                )
            )
    return out


@register
def rule_empty_applicable_agents(catalog: list[CatalogEntry]) -> list[Finding]:
    out: list[Finding] = []
    for e in catalog:
        if e.kind == "skill" and e.applicable_agents == tuple():
            out.append(
                Finding(
                    severity=Severity.NIT,
                    rule="empty-applicable-agents",
                    entry=e.name,
                    message=(
                        "skill has empty applicable_agents — set "
                        '["*"] for any-agent or list specific agents'
                    ),
                )
            )
    return out


def _trigger_fingerprint(t) -> tuple:
    return (
        frozenset(t.command_prefixes),
        frozenset(t.agent_mentions),
        tuple(sorted(t.path_globs)),
        tuple(sorted((kw.term, kw.weight) for kw in t.keywords)),
        frozenset(t.tool_mentions),
        frozenset(t.excludes),
    )


@register
def rule_duplicate_trigger_set(catalog: list[CatalogEntry]) -> list[Finding]:
    out: list[Finding] = []
    by_fp: dict[tuple, list[CatalogEntry]] = {}
    for e in catalog:
        if e.kind != "agent":
            continue
        by_fp.setdefault(_trigger_fingerprint(e.triggers), []).append(e)
    for fp, group in by_fp.items():
        if len(group) < 2:
            continue
        # Only flag if any pair has different applicable_skills.
        skill_sets = {tuple(sorted(e.applicable_skills)) for e in group}
        if len(skill_sets) > 1:
            names = sorted(e.name for e in group)
            out.append(
                Finding(
                    severity=Severity.NIT,
                    rule="duplicate-trigger-set",
                    entry=", ".join(names),
                    message=(
                        f"agents {names} share identical trigger sets "
                        "but differ in applicable_skills — likely a "
                        "copy-paste"
                    ),
                )
            )
    return out
```

**Verification:**

```bash
"$VENV_PY" -m pytest tests/test_audit_catalog.py -x
```

**Commit:**

```
feat(audit): rules excludes-overlap, source-routable-mismatch,
empty-applicable-agents, duplicate-trigger-set

Completes the rule set per #156: two more CONCERN rules and two NIT
rules. Refs #156.
```

---

## Task 16 — `--severity` filter + exit-code mapping

**Goal:** `audit-catalog` exit codes match the issue's contract:
- `0` = no findings.
- `1` = NIT findings only.
- `2` = CONCERN (no BLOCKING).
- `3` = BLOCKING present.

`--severity blocking` shows only BLOCKING; `--severity concern` shows CONCERN+BLOCKING; `--severity nit` shows all. The exit code is computed from the **filtered** finding set (so `--severity blocking` against a CONCERN-only catalog exits 0).

### Step 16a — failing tests

```python
class TestExitCodes:
    """Exit code is the max severity present in the filtered finding set."""

    def test_clean_catalog_exits_zero(self, tmp_path: Path) -> None:
        cat = {"entries": []}
        p = tmp_path / "cat.json"
        p.write_text(json.dumps(cat))
        cp = _run_cli("audit-catalog", "--catalog", str(p))
        assert cp.returncode == 0

    def test_blocking_exits_three(self, tmp_path: Path) -> None:
        # Build a catalog with one off-ladder weight.
        cat = {
            "entries": [
                {
                    "name": "bad",
                    "kind": "agent",
                    "routable": True,
                    "source": "owned",
                    "applicable_skills": [],
                    "triggers": {
                        "command_prefixes": [],
                        "agent_mentions": [],
                        "path_globs": ["**/*.py"],
                        "keywords": [{"term": "x", "weight": 0.7}],
                        "tool_mentions": [],
                        "excludes": [],
                    },
                }
            ]
        }
        p = tmp_path / "cat.json"
        p.write_text(json.dumps(cat))
        cp = _run_cli("audit-catalog", "--catalog", str(p))
        assert cp.returncode == 3, cp.stdout + cp.stderr

    def test_severity_filter_changes_exit(self, tmp_path: Path) -> None:
        # Catalog with only a NIT finding (skill, empty applicable_agents).
        cat = {
            "entries": [
                {
                    "name": "s",
                    "kind": "skill",
                    "routable": False,
                    "source": "owned",
                    "applicable_agents": [],
                    "triggers": {
                        "command_prefixes": ["/x"],
                        "agent_mentions": [],
                        "path_globs": [],
                        "keywords": [{"term": "x", "weight": 1.0}],
                        "tool_mentions": [],
                        "excludes": [],
                    },
                }
            ]
        }
        p = tmp_path / "cat.json"
        p.write_text(json.dumps(cat))
        # No filter — NIT present, exits 1.
        cp = _run_cli("audit-catalog", "--catalog", str(p))
        assert cp.returncode == 1
        # Filter to BLOCKING-only — no findings remain, exits 0.
        cp = _run_cli("audit-catalog", "--catalog", str(p), "--severity", "blocking")
        assert cp.returncode == 0
```

(Adjust the fixture-JSON shape above to whatever `load_catalog()` accepts — if the loader expects a different schema, update accordingly. The test fixture catalog format is whatever `match.load_catalog()` reads.)

### Step 16b — implement

Update `run_audit_cli()` in `audit_catalog.py`:

```python
_SEVERITY_FROM_FLAG: dict[str, Severity] = {
    "blocking": Severity.BLOCKING,
    "concern": Severity.CONCERN,
    "nit": Severity.NIT,
}


def _filter_by_severity(
    findings: list[Finding],
    threshold: Severity | None,
) -> list[Finding]:
    if threshold is None:
        return findings
    return [f for f in findings if f.severity.value >= threshold.value]


def _filter_by_target(
    findings: list[Finding],
    target: str | None,
) -> list[Finding]:
    if target is None:
        return findings
    # Keep catalog-wide findings (e.g. conflict-pair with " ↔ " in entry)
    # only if `target` appears in the entry label.
    return [f for f in findings if target in f.entry]


def _exit_code_for(findings: list[Finding]) -> int:
    if not findings:
        return 0
    return max(f.severity.value for f in findings)


def run_audit_cli(args: argparse.Namespace) -> int:
    import sys
    catalog_path = _resolve_catalog_path(getattr(args, "catalog", None))
    try:
        entries = load_catalog(catalog_path)
    except (FileNotFoundError, _json.JSONDecodeError, ValueError) as exc:
        print(f"[AUDIT ERROR] Failed to load catalog: {exc}", file=sys.stderr)
        return 1

    findings = run_audit(entries)

    threshold = _SEVERITY_FROM_FLAG.get(getattr(args, "severity", None) or "")
    findings = _filter_by_severity(findings, threshold)
    findings = _filter_by_target(findings, getattr(args, "target", None))

    if getattr(args, "json", False):
        _emit_json(findings)
    else:
        _emit_text(findings)

    return _exit_code_for(findings)


def _emit_text(findings: list[Finding]) -> None:
    if not findings:
        print("audit-catalog: no findings.")
        return
    # Group by severity, BLOCKING first.
    for sev in (Severity.BLOCKING, Severity.CONCERN, Severity.NIT):
        bucket = [f for f in findings if f.severity == sev]
        if not bucket:
            continue
        print(f"\n## {sev.name} ({len(bucket)})\n")
        for f in bucket:
            print(f"- [{f.rule}] {f.entry}: {f.message}")


def _emit_json(findings: list[Finding]) -> None:
    payload = [
        {
            "severity": f.severity.name,
            "rule": f.rule,
            "entry": f.entry,
            "message": f.message,
        }
        for f in findings
    ]
    print(_json.dumps(payload, indent=2))
```

### Step 16c — green; commit

```
feat(audit): --severity filter + exit-code contract

Exit codes: 0/1/2/3 = none/NIT/CONCERN/BLOCKING, computed from the
filtered finding set. --severity={blocking|concern|nit} narrows the
report and the exit code together. Refs #156.
```

---

## Task 17 — `--json` and `--target` end-to-end tests

`--json` and `--target` are already implemented in Task 16. This task adds the end-to-end tests that lock the surface in.

### Tests

```python
class TestJsonOutput:
    def test_json_emits_valid_array(self, tmp_path: Path) -> None:
        cat = {"entries": []}
        p = tmp_path / "cat.json"
        p.write_text(json.dumps(cat))
        cp = _run_cli("audit-catalog", "--catalog", str(p), "--json")
        assert cp.returncode == 0
        # Empty findings → empty array.
        assert json.loads(cp.stdout) == []


class TestTargetFilter:
    def test_target_restricts_per_entry_findings(self, tmp_path: Path) -> None:
        # Build a catalog where two entries have BLOCKING findings.
        cat = {
            "entries": [
                {
                    "name": "alpha",
                    "kind": "agent",
                    "routable": True,
                    "source": "owned",
                    "applicable_skills": [],
                    "triggers": {
                        "command_prefixes": [],
                        "agent_mentions": [],
                        "path_globs": ["**/*.py"],
                        "keywords": [{"term": "x", "weight": 0.7}],
                        "tool_mentions": [],
                        "excludes": [],
                    },
                },
                {
                    "name": "beta",
                    "kind": "agent",
                    "routable": True,
                    "source": "owned",
                    "applicable_skills": [],
                    "triggers": {
                        "command_prefixes": [],
                        "agent_mentions": [],
                        "path_globs": ["**/*.py"],
                        "keywords": [{"term": "y", "weight": 0.5}],
                        "tool_mentions": [],
                        "excludes": [],
                    },
                },
            ]
        }
        p = tmp_path / "cat.json"
        p.write_text(json.dumps(cat))
        cp = _run_cli(
            "audit-catalog", "--catalog", str(p), "--json", "--target", "alpha"
        )
        payload = json.loads(cp.stdout)
        names = {f["entry"] for f in payload}
        assert "alpha" in names
        assert "beta" not in names
```

**Verification:** `pytest tests/test_audit_catalog.py -x` clean.

**Commit:**

```
test(audit): end-to-end tests for --json and --target flags

Locks in the machine-readable output and entry-filtering surfaces.
Refs #156.
```

---

## Task 18 — Smoke run against the live wayfinder catalog

**Goal:** acceptance criterion "Runs cleanly on wayfinder's own catalog produces only legitimate findings (not noise)".

**Action:**

```bash
# Rebuild catalog first to pick up the new frontmatter skill from Task 4.
"$VENV_PY" -m claude_wayfinder catalog build

# Smoke run, full output.
"$VENV_PY" -m claude_wayfinder audit-catalog > .tmp/audit-smoke.txt
echo "exit=$?"

# Same in JSON.
"$VENV_PY" -m claude_wayfinder audit-catalog --json > .tmp/audit-smoke.json
echo "exit=$?"
```

**Acceptance:**

- BLOCKING count = 0 (any BLOCKING is a real bug to fix before merging — could be in the catalog or the rule).
- CONCERN/NIT findings are reviewed manually. Any finding that is **wrong** (false positive) is a rule bug → open a follow-up issue rather than tweak the rule under this PR's scope. Any finding that is **legitimate** (genuine matcher footgun in an owned agent/skill) is fixed in the same PR.

Document the smoke run results in the PR body (next task), including the exit code and a summary table of findings by severity.

**No commit** unless catalog or owned-frontmatter fixes were necessary. If fixes were necessary:

```
fix(catalog): address audit-catalog findings against live catalog

<summary of what was wrong and what changed>

Refs #156.
```

---

## Task 19 — `docs/frontmatter-guide.md`

**Goal:** user-facing companion to the skill, deeper than SKILL.md with worked examples. The skill body intentionally stays concise and points here.

**File:** `docs/frontmatter-guide.md`

**Structure:**

1. **Purpose** — short opener: "This guide is the long-form companion to the `claude-wayfinder:frontmatter` skill. The skill is what the agent loads at runtime; this doc is what the human reads when they want examples."
2. **Schema reference** — the field list with one-paragraph descriptions per field. Link to `docs/schema.md` for the canonical definitions and to `docs/design/trigger-schema.md` for design rationale (do not duplicate them).
3. **Worked example: authoring from scratch** — pick a real owned skill in the repo (e.g. `refresh-catalog`) and walk through how its current `triggers.yml` was derived from the body prose. Include the body excerpts and the resulting keyword choices.
4. **Worked example: tuning an existing entry** — pick another owned skill and show a hypothetical "before/after" tune that drops a stale keyword and demotes an overweighted term.
5. **Worked example: troubleshooting an unreachable agent** — narrative: "my agent isn't being dispatched"; walk through the symptom-cause table in SKILL.md Section 8 with a concrete fictional entry.
6. **The audit-catalog CLI** — full rule reference. For each rule (the ~10 implemented in Tasks 7–15), one heading with: rule id, severity, what it checks, why it matters, fix recipe.
7. **Exit code contract** — reproduce the 0/1/2/3 table with one-line use cases (CI gate on BLOCKING, dev-loop gate on CONCERN).
8. **Cross-references** — links back to SKILL.md, `docs/schema.md`, `docs/design/trigger-schema.md`, `agent-authoring` skill.

**Verification:** `grep -c "^## " docs/frontmatter-guide.md` ≥ 7.

**Commit:**

```
docs: add frontmatter-guide.md companion

Long-form user-facing companion to the claude-wayfinder:frontmatter
skill. Covers schema, three worked examples (authoring, tuning,
troubleshooting), the audit-catalog rule reference, and the exit-code
contract. Refs #156.
```

---

## Task 20 — Update `README.md` and `docs/integration.md`

### README.md

Add the new skill to the user-facing surface list and the new CLI to the operator-facing surface list. Search for the existing surface-list section and append:

- Under "Skills shipped with claude-wayfinder" (or equivalent heading): add a bullet for `claude-wayfinder:frontmatter — matcher-aware authoring and troubleshooting knowledge for trigger frontmatter`.
- Under "CLI surfaces" / "Subcommands": add `audit-catalog — catalog-wide static analysis (conflict pairs, structural checks, matcher-aware semantic rules). See docs/frontmatter-guide.md.`

If those exact headings don't exist, find the analogous section and add the entries there. Match the existing tone and bullet style.

### docs/integration.md

Add an "Auditing the dispatch catalog" section near the end (after the bundled-hooks section). Recommend running `audit-catalog` before every release and as a pre-commit check on changes to skill sidecars or agent frontmatter. Include the exit-code contract one more time (the doc is the operator's primary reference).

**Verification:**

```bash
grep -l "audit-catalog" README.md docs/integration.md
```

Must list both files.

**Commit:**

```
docs: surface audit-catalog in README and integration guide

Adds the new skill and CLI subcommand to the README's surface lists and
adds an "Auditing the dispatch catalog" section to docs/integration.md
with the exit-code contract and recommended usage. Refs #156.
```

---

## Task 21 — Open the PR

**Goal:** open a PR from `feature/frontmatter-and-audit-catalog` directly to `main`.

### Pre-flight

```bash
git -C .worktrees/frontmatter-and-audit-catalog status   # clean
git -C .worktrees/frontmatter-and-audit-catalog log --oneline main..HEAD
```

Expect ~12–14 commits matching the per-task list above.

Run the full test suite from the worktree:

```bash
"$VENV_PY" -m pytest -q
```

All tests green.

### Push

```bash
git -C .worktrees/frontmatter-and-audit-catalog push -u origin feature/frontmatter-and-audit-catalog
```

### Open PR

Use `mcp__github__create_pull_request` with:

- **Title:** `feat: claude-wayfinder:frontmatter skill + audit-catalog CLI (#156)`
- **Base:** `main`
- **Head:** `feature/frontmatter-and-audit-catalog`
- **Body:**
  - Summary paragraph.
  - Bullet list of acceptance criteria with checkmarks.
  - Smoke-run results from Task 18 (exit code + finding counts by severity).
  - Closing keyword: `Closes #156` (plain text, no backticks).
  - Claude attribution footer per CLAUDE.md § GitHub Comments:
    > 🤖 _Generated by Claude Code on behalf of @cbeaulieu-gt_

**Closing-keyword caveat reminder** (per CLAUDE.md § Pull Requests): the keyword must be in the **PR body**, not just in a commit message. Squash-merge synthesizes the merge commit from the PR title + body, so commit-message-only `Closes #N` does not auto-close.

### Post-open verification

```bash
# Confirm the PR is open and closing-linked to #156.
gh -R glitchwerks/claude-wayfinder pr view <PR#> --json closingIssuesReferences,state
```

Expect `state: OPEN` and `closingIssuesReferences` containing `#156`.

**No commit.** The PR open is the terminal action.

---

## Appendix A — Definition of Done (mirrors issue #156 ACs)

### Knowledge skill

- [x] `skills/frontmatter/SKILL.md` exists with the 10 sections (Tasks 2–3).
- [x] `skills/frontmatter/triggers.yml` configured per spec (Task 4).
- [x] `applicable_agents: ["*"]` (Task 4).
- [ ] Loaded successfully via `/frontmatter` slash command (manual verification after PR merge).
- [ ] Loaded automatically by description-keyword phrases (manual verification — pick the four phrases listed in the AC and confirm dispatch).
- [x] Skill cross-references `docs/schema.md`, `docs/design/trigger-schema.md`, and `agent-authoring` skill (Task 3 Section 10).

### CLI subcommand

- [x] `python -m claude_wayfinder audit-catalog` exists and runs (Task 6).
- [x] All BLOCKING and CONCERN checks implemented (Tasks 7–15).
- [x] `--json`, `--severity`, `--target` flags work (Tasks 16–17).
- [x] Exit codes 0/1/2/3 (Task 16).
- [x] Smoke-run against the live catalog produces no false-positive noise (Task 18).
- [x] Unit tests cover each rule (Tasks 7–15).

### Documentation

- [x] `docs/frontmatter-guide.md` (Task 19).
- [x] `README.md` updated (Task 20).
- [x] `docs/integration.md` updated (Task 20).

---

## Appendix B — Out-of-scope (do NOT bundle into this PR)

Per issue #156 § Future work:

- Dedicated `frontmatter-author` agent.
- Worked-examples library in `docs/frontmatter-guide.md` beyond the three examples in Task 19.
- Wiring `audit-catalog` into `release.yml` as a CI gate.

If any of these come up during implementation, open a follow-up issue rather than expanding this PR.

---

## Appendix C — File-by-file summary

| File | Action | Task(s) |
| ---- | ------ | ------- |
| `skills/frontmatter/SKILL.md` | new | 2, 3 |
| `skills/frontmatter/triggers.yml` | new | 4 |
| `src/claude_wayfinder/audit_catalog.py` | new | 5, 6, 7–17 |
| `src/claude_wayfinder/cli.py` | edit | 6 |
| `tests/test_audit_catalog.py` | new | 5–17 |
| `docs/frontmatter-guide.md` | new | 19 |
| `README.md` | edit | 20 |
| `docs/integration.md` | edit | 20 |

Total new files: 5. Edited files: 3.

---

## Changelog

### Rev 2 — 2026-05-19 (post project-reviewer revision)

Project-reviewer pass produced 3 BLOCKING, 3 CONCERN, and 3 NIT findings. The user opted to skip the inquisitor pass; this revision is the final gate before SDD execution. All 9 findings addressed below.

**BLOCKING fixes:**

1. **Scoring math corrected in SKILL.md § 3 (Task 3).** Per-keyword contribution is `+0.5 × weight`, not `+0.3 × weight`. Verified against `src/claude_wayfinder/match.py:84` (`_KEYWORD_MULTIPLIER = 0.5`). Worked example rewritten: one glob (`+0.4`) + one weight-`1.0` keyword (`+0.5`) = `0.9`. Added explicit **clamping footgun** subsection: stacking weight-`1.0` keywords past the `1.0` ceiling is dead weight; prefer broadening keyword coverage at lower weights over inflating per-input scores the clamp throws away.
2. **Added Task 5.5: relax `load_catalog` on empty entries list.** Took option (a) from the reviewer's two-route fix — the loader at `match.py:440-441` raises `ValueError("Catalog contains zero entries.")` on empty input, which would break the audit CLI's degraded-state path and the `tmp_path` test fixtures in Tasks 16–17. The fix returns an empty list instead of raising. Verified the ValueError currently exists by reading `match.py:425-460`. New task is fully scoped with failing-test, implementation, and full-suite re-run.
3. **Conflict-pair rule rewritten in Task 14.** The old `if a_sig != b_sig: continue` cleared any signature mismatch — which silently passed disjoint non-empty discriminators (e.g. `**/*.py` vs `**/*.ts`) that don't actually break the tie on no-path prompts. The corrected algorithm uses `_has_breaking_discriminator`: clears the pair only when at least one discriminator field is **single-sided-asymmetric** (one side empty, one side non-empty). Disjoint non-empty sets stay flagged. Added a new test case `test_three_overlap_disjoint_globs_flagged` exercising the failure mode. Renamed the existing test `test_three_overlap_with_discriminator_clean` → `test_three_overlap_with_asymmetric_discriminator_clean` for precision. Commit message rewritten to describe the new algorithm.

**CONCERN fixes:**

4. **`--target` help text and conflict-pair filtering reconciled.** Took the substring-match approach (more useful — `--target alpha` should surface pairs involving alpha). Updated argparse help to describe substring matching against the entry label, including the `alpha ↔ beta` pair-label case. Implementation in `_filter_by_target` (Task 16) already does substring match — kept as-is; this was a docs-vs-code drift, not a behavior change.
5. **SKILL.md § 2 input-side vs entry-side distinction made explicit (Task 2).** Rewrote the feature-density paragraph as two labelled cases: **input-side density floor** (matcher emits `needs_more_detail` when the *prompt* is thin — about user inputs, not entries) and **entry-side weak-scoring** (a calibration footgun where an entry with only one trigger dimension scores zero on most of the prompt distribution). Also rewrote § 5's "one-dimensional triggers" footgun to explicitly reference the entry-side case and distinguish it from § 2's input-side floor.
6. **Task 15 explicit test cases for the two non-obvious rules.** Replaced the vague "implementer fills these in" line. Added full `TestExcludesOverlapOwnKeywords` (no-excludes, disjoint-excludes, self-zero, case-insensitive) and `TestDuplicateTriggerSet` (single, identical-triggers-identical-skills, identical-triggers-different-skills, both-empty-skills edge, kind-scoping) test classes. Left `rule_source_routable_mismatch` and `rule_empty_applicable_agents` on the standard two-case pattern with an explicit note saying so.

**NIT fixes:**

7. **`_BARE_EXT_RE` regex comment added (Task 10).** Documented that the regex matches single-component extensions only — `*.tar.gz` and `*.min.js` are intentional false-negatives. The sibling-suggestion logic in the rule body would be wrong for compound extensions anyway, so the limitation is consistent.
8. **`_CANONICAL_TOOLS` extended with `Agent`, `Monitor`, `TaskCreate` (Task 11).** Also reconciled the prose tool list in Task 11's preamble with the code tuple — sorted both alphabetically and added a "keep these in sync" note for future maintainers.
9. **Task 17 `TestTargetFilter` fixture weight `0.8` → `0.5` (clean ladder value).** The `beta` entry's weight no longer trips `rule_weight_not_in_ladder` BLOCKING — kept the fixture finding-clean so the target-filter test exercises only the filtering logic, not collateral BLOCKING findings from off-ladder weights.

**Findings declined:** None. All 9 findings addressed.

**Verification performed during this revision:**

- Read `src/claude_wayfinder/match.py:75-95` to verify `_KEYWORD_MULTIPLIER = 0.5` (BLOCKING #1).
- Read `src/claude_wayfinder/match.py:425-460` to verify `load_catalog` raises `ValueError("Catalog contains zero entries.")` on empty input (BLOCKING #2).
- Re-read existing Task 14 rule body before rewriting (BLOCKING #3).

### Rev 1 — 2026-05-19 (initial plan)

Initial 22-task plan emitted for project-reviewer pass. See git history for the unrevised version.
