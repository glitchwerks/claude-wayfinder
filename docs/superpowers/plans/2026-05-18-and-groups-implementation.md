# AND-group conjunctive triggers — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `keyword_groups` in `match.py`, `build_catalog.py`, validators, fixtures, and `docs/design/trigger-schema.md` per the approved spec at `docs/superpowers/specs/2026-05-18-and-groups-design.md`.

**Architecture:** Add `KeywordGroup` and `Slot` dataclasses to `match.py`. Extend `Triggers`, `_parse_triggers`, and `score()` to evaluate groups with strict all-of semantics, replacement-rule suppression, and a distinct `_GROUP_MULTIPLIER = 1.0`. Extend `build_catalog.py` with `_validate_keyword_groups` following the existing `_validate_keywords` pattern. No new dependencies; stdlib only.

**Tech Stack:** Python 3.11+, pytest, ruff. Existing `~/.claude/plugins/cache/glitchwerks/claude-wayfinder/.../.venv` or the project-local `.venv/`.

**Tracking:** [glitchwerks/claude-wayfinder#135](https://github.com/glitchwerks/claude-wayfinder/issues/135). Spec lives in **PR #139** (draft); this implementation goes on a **separate branch** so the two PRs are independently reviewable and mergeable.

**Lifecycle note:** Per CLAUDE.md `# Document Files`, delete this plan file once #135 is closed (extract any durable rationale into commit messages or comments on the closed issue first).

---

## File map

### Files to modify

| Path | What changes |
| --- | --- |
| `src/claude_wayfinder/match.py` | Add `Slot`, `KeywordGroup` dataclasses; `_GROUP_MULTIPLIER` constant; `keyword_groups` field on `Triggers`; extend `_parse_triggers`; extend `score()` |
| `src/claude_wayfinder/build_catalog.py` | Add `_validate_keyword_groups`; add `"keyword_groups"` to `TRIGGER_FIELDS`; wire into `validate_entry` |
| `src/claude_wayfinder/_dispatch.py` | Update rationale composition to list groups that fired |
| `tests/test_match.py` | Update test helpers (`_make_agent`, `_make_skill`) to accept `keyword_groups` kwarg |
| `tests/test_build_catalog.py` | Add `_validate_keyword_groups` tests (one test class) |
| `docs/design/trigger-schema.md` | Add §§ for `keyword_groups` schema, matching rule, validation rule, example |

### Files to create

| Path | Purpose |
| --- | --- |
| `tests/test_match_keyword_groups.py` | Group scoring tests — all the worked examples from spec § 7 |
| `tests/test_and_groups_replay.py` | Fixture replay test (regression-locks the targeted behavior) |
| `src/claude_wayfinder/fixtures/and_groups/__init__.py` | Empty package marker |
| `src/claude_wayfinder/fixtures/and_groups/catalog.json` | Minimal catalog: code-writer + doc-writer (with group) |
| `src/claude_wayfinder/fixtures/and_groups/prompts.json` | The 5 prompts from spec § 7.1 with expected decisions |

---

## Task 0: Set up the implementation worktree

**Files:**
- Read: `docs/superpowers/specs/2026-05-18-and-groups-design.md`

**Why a separate branch:** spec PR #139 is independent from implementation. Stacking the implementation on `feature/and-groups-spec` lets you reference the spec/plan files locally during execution; after #139 merges, rebase onto `main`.

- [ ] **Step 0.1: Verify clean state of `main` and pull**

Run from anywhere (uses `-C` to avoid working-directory hops):

```bash
git -C I:/other/claude-wayfinder status --short
git -C I:/other/claude-wayfinder fetch origin
git -C I:/other/claude-wayfinder log --oneline origin/main..origin/feature/and-groups-spec
```

Expected: `status` clean (or only `.tmp/` untracked); `log` shows the spec commit (`docs(spec): AND-group conjunctive triggers design`).

- [ ] **Step 0.2: Create the implementation worktree off `feature/and-groups-spec`**

```bash
git -C I:/other/claude-wayfinder worktree add .worktrees/and-groups-impl -b feature/and-groups-impl feature/and-groups-spec
```

Expected: `Preparing worktree (new branch 'feature/and-groups-impl')`.

- [ ] **Step 0.3: Verify the worktree has the spec + plan files**

```bash
ls I:/other/claude-wayfinder/.worktrees/and-groups-impl/docs/superpowers/specs/2026-05-18-and-groups-design.md
ls I:/other/claude-wayfinder/.worktrees/and-groups-impl/docs/superpowers/plans/2026-05-18-and-groups-implementation.md
```

Both must exist. If they don't, the worktree wasn't created off `feature/and-groups-spec`.

- [ ] **Step 0.4: Confirm venv and dev install**

```bash
ls I:/other/claude-wayfinder/.worktrees/and-groups-impl/.venv 2>/dev/null || echo "no venv yet"
```

If absent, run from the worktree dir:

```bash
cd I:/other/claude-wayfinder/.worktrees/and-groups-impl
uv venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"
```

Verify pytest works:

```bash
.venv/Scripts/python.exe -m pytest tests/test_match.py -k test_score_capped_at_1_0 -v
```

Expected: 1 passed.

All subsequent tasks run from `I:/other/claude-wayfinder/.worktrees/and-groups-impl/` using `git -C` patterns or the relative `.venv/Scripts/python.exe` invocation.

---

## Task 1: Add `Slot`, `KeywordGroup` dataclasses and `_GROUP_MULTIPLIER` constant

**Files:**
- Modify: `src/claude_wayfinder/match.py:84` (constants), `src/claude_wayfinder/match.py:113-131` (dataclass block)
- Test: `tests/test_match_keyword_groups.py` (new file)

**Why:** Establish the data shape before any behavior change. Tests assert the dataclass surface and constant value.

- [ ] **Step 1.1: Write the failing test**

Create `tests/test_match_keyword_groups.py`:

```python
"""Tests for keyword_groups (AND-group conjunctive triggers).

Spec: docs/superpowers/specs/2026-05-18-and-groups-design.md
Tracking: glitchwerks/claude-wayfinder#135
"""

from __future__ import annotations

import pytest

from claude_wayfinder import match as _match_mod


class TestKeywordGroupTypes:
    """The dataclass surface and constants the spec mandates."""

    def test_group_multiplier_constant_is_1_0(self) -> None:
        """Spec D4: _GROUP_MULTIPLIER = 1.0 (distinct from singleton 0.5)."""
        assert _match_mod._GROUP_MULTIPLIER == 1.0

    def test_slot_dataclass_holds_terms_and_optional_name(self) -> None:
        """Slot stores a tuple of terms and an optional name."""
        slot = _match_mod.Slot(terms=("update", "edit"), name="verbs")
        assert slot.terms == ("update", "edit")
        assert slot.name == "verbs"

    def test_slot_name_defaults_to_none(self) -> None:
        """Slot name is optional."""
        slot = _match_mod.Slot(terms=("docs", "readme"))
        assert slot.name is None

    def test_keyword_group_holds_slots_and_weight(self) -> None:
        """KeywordGroup composes Slots with a weight."""
        group = _match_mod.KeywordGroup(
            slots=(
                _match_mod.Slot(terms=("update", "edit")),
                _match_mod.Slot(terms=("docs", "readme")),
            ),
            weight=1.0,
        )
        assert len(group.slots) == 2
        assert group.weight == 1.0
```

- [ ] **Step 1.2: Run test to verify it fails**

```bash
.venv/Scripts/python.exe -m pytest tests/test_match_keyword_groups.py::TestKeywordGroupTypes -v
```

Expected: 4 failures with `AttributeError: module 'claude_wayfinder.match' has no attribute '_GROUP_MULTIPLIER'` (and `Slot`, `KeywordGroup`).

- [ ] **Step 1.3: Add the constant**

In `src/claude_wayfinder/match.py`, locate the existing `_KEYWORD_MULTIPLIER = 0.5` declaration (around line 84). Add immediately after it:

```python
# Per-group score multiplier (spec D4 in
# docs/superpowers/specs/2026-05-18-and-groups-design.md).
# Distinct from _KEYWORD_MULTIPLIER (0.5) so a satisfied group can carry
# more signal than any single keyword: a weight-1.0 group contributes 1.0
# (solo-decides delegate), while a weight-0.5 group contributes 0.5
# (attachment-only).
_GROUP_MULTIPLIER = 1.0
```

- [ ] **Step 1.4: Add the dataclasses**

In `src/claude_wayfinder/match.py`, locate the `Keyword` dataclass (around line 100). Add immediately AFTER `Keyword` and BEFORE `Triggers`:

```python
@dataclass(frozen=True)
class Slot:
    """One slot in a keyword_group: a set of alternative terms (OR).

    Attributes:
        terms: Tuple of lowercase term strings. The slot is "filled"
            when at least one of these terms is in features.keywords.
        name: Optional human-readable label (e.g., "verbs", "nouns").
            Ignored by the matcher; surfaced in debug/rationale output.
    """

    terms: tuple[str, ...]
    name: str | None = None


@dataclass(frozen=True)
class KeywordGroup:
    """A conjunctive expression: AND-of-slots, each slot is OR-of-terms.

    Per spec § 3: group = AND-of-slots, slot = OR-of-terms. The group
    is "satisfied" when EVERY slot is filled. A satisfied group
    contributes `_GROUP_MULTIPLIER * weight` to the score and
    suppresses singleton contributions for any term named in any of
    its slots (replacement rule, spec D5).

    Attributes:
        slots: Tuple of Slots, length >= 2 (enforced at build time).
        weight: Float in {0.25, 0.5, 1.0} (validator enforces clamp).
    """

    slots: tuple[Slot, ...]
    weight: float
```

- [ ] **Step 1.5: Run test to verify it passes**

```bash
.venv/Scripts/python.exe -m pytest tests/test_match_keyword_groups.py::TestKeywordGroupTypes -v
```

Expected: 4 passed.

- [ ] **Step 1.6: Run full match-module tests to verify no regression**

```bash
.venv/Scripts/python.exe -m pytest tests/test_match.py -v
```

Expected: all existing tests pass (no regression — we added types only).

- [ ] **Step 1.7: Commit**

```bash
git -C I:/other/claude-wayfinder/.worktrees/and-groups-impl add src/claude_wayfinder/match.py tests/test_match_keyword_groups.py
git -C I:/other/claude-wayfinder/.worktrees/and-groups-impl commit -m "feat(match): add Slot, KeywordGroup dataclasses and _GROUP_MULTIPLIER

Types-only addition. No behavior change. Establishes the data
shape spec D1 and D4 require for the upcoming scoring work.

Refs: #135"
```

---

## Task 2: Extend `Triggers` with `keyword_groups`; update `_parse_triggers`

**Files:**
- Modify: `src/claude_wayfinder/match.py:113-131` (Triggers dataclass), `src/claude_wayfinder/match.py:388-420` (`_parse_triggers`)
- Test: `tests/test_match_keyword_groups.py`

**Why:** Parser must accept `keyword_groups` from raw catalog dicts. Backward compat: missing field defaults to empty tuple.

- [ ] **Step 2.1: Write the failing test**

Append to `tests/test_match_keyword_groups.py`:

```python
class TestTriggersParsing:
    """_parse_triggers correctly reads keyword_groups from raw dicts."""

    def test_triggers_defaults_keyword_groups_to_empty(self) -> None:
        """Catalog entries without keyword_groups parse cleanly."""
        triggers = _match_mod._parse_triggers({})
        assert triggers.keyword_groups == ()

    def test_parse_keyword_groups_dict_form(self) -> None:
        """The canonical dict form (terms + optional name) parses."""
        raw = {
            "keyword_groups": [
                {
                    "slots": [
                        {"name": "verbs", "terms": ["update", "edit"]},
                        {"name": "nouns", "terms": ["docs", "readme"]},
                    ],
                    "weight": 1.0,
                }
            ]
        }
        triggers = _match_mod._parse_triggers(raw)
        assert len(triggers.keyword_groups) == 1
        group = triggers.keyword_groups[0]
        assert group.weight == 1.0
        assert len(group.slots) == 2
        assert group.slots[0].name == "verbs"
        assert group.slots[0].terms == ("update", "edit")
        assert group.slots[1].name == "nouns"
        assert group.slots[1].terms == ("docs", "readme")

    def test_parse_keyword_groups_bare_list_form(self) -> None:
        """Authors may write slots as bare lists (no name)."""
        raw = {
            "keyword_groups": [
                {
                    "slots": [
                        ["github"],
                        ["issue", "pr", "workflow"],
                    ],
                    "weight": 1.0,
                }
            ]
        }
        triggers = _match_mod._parse_triggers(raw)
        group = triggers.keyword_groups[0]
        assert group.slots[0].name is None
        assert group.slots[0].terms == ("github",)
        assert group.slots[1].terms == ("issue", "pr", "workflow")

    def test_parse_keyword_groups_lowercases_terms(self) -> None:
        """Terms are lowercased to match feature extraction."""
        raw = {
            "keyword_groups": [
                {"slots": [["UPDATE"], ["DOCS"]], "weight": 1.0}
            ]
        }
        triggers = _match_mod._parse_triggers(raw)
        assert triggers.keyword_groups[0].slots[0].terms == ("update",)
        assert triggers.keyword_groups[0].slots[1].terms == ("docs",)
```

- [ ] **Step 2.2: Run tests to verify they fail**

```bash
.venv/Scripts/python.exe -m pytest tests/test_match_keyword_groups.py::TestTriggersParsing -v
```

Expected: 4 failures — `Triggers` has no `keyword_groups` attribute.

- [ ] **Step 2.3: Extend `Triggers` dataclass**

In `src/claude_wayfinder/match.py`, locate `Triggers` (around line 113-131). Add `keyword_groups` field with a default of empty tuple, AND update the docstring:

```python
@dataclass(frozen=True)
class Triggers:
    """Parsed trigger block for one catalog entry.

    Attributes:
        command_prefixes: Slash commands that short-circuit to score 1.0.
        agent_mentions: Agent names whose explicit mention scores 1.0.
        path_globs: fnmatch-style globs matched against file paths.
        keywords: Weighted keyword terms matched against extracted tokens.
        keyword_groups: Conjunctive AND-group triggers. Each group is
            satisfied when every slot has >=1 term in features.keywords.
            See spec docs/superpowers/specs/2026-05-18-and-groups-design.md.
        tool_mentions: Tool names matched against features.tool_mentions.
        excludes: Terms that hard-zero the entry's score when present.
    """

    command_prefixes: frozenset[str]
    agent_mentions: frozenset[str]
    path_globs: tuple[str, ...]
    keywords: tuple[Keyword, ...]
    tool_mentions: frozenset[str]
    excludes: frozenset[str]
    keyword_groups: tuple[KeywordGroup, ...] = ()
```

The new field has a default so existing callers (and existing test fixtures that construct `Triggers` directly with positional/kw args) continue to work.

- [ ] **Step 2.4: Extend `_parse_triggers`**

In `src/claude_wayfinder/match.py`, locate `_parse_triggers` (line 388). Add a helper for slots and the groups parser, then include the parsed value in the returned `Triggers`. Replace the function body (preserving the keyword parsing) with:

```python
def _parse_slot(raw: Any) -> Slot | None:
    """Parse one slot from a raw catalog value.

    Accepts two forms (matcher is lenient; builder normalizes to dict):
        - Bare list of strings: ['a', 'b']
        - Dict with terms (+ optional name): {'terms': ['a', 'b'], 'name': 'verbs'}

    Returns None for malformed input (group containing this slot will be
    silently dropped — fatal validation lives in build_catalog.py).
    """
    if isinstance(raw, list):
        terms = tuple(str(t).lower() for t in raw if isinstance(t, str))
        if not terms:
            return None
        return Slot(terms=terms, name=None)
    if isinstance(raw, dict):
        raw_terms = raw.get("terms")
        if not isinstance(raw_terms, list):
            return None
        terms = tuple(str(t).lower() for t in raw_terms if isinstance(t, str))
        if not terms:
            return None
        name_val = raw.get("name")
        name = str(name_val) if isinstance(name_val, str) else None
        return Slot(terms=terms, name=name)
    return None


def _parse_keyword_group(raw: Any) -> KeywordGroup | None:
    """Parse one keyword_group from a raw catalog value.

    Returns None when the group is malformed; build_catalog.py is
    responsible for emitting fatal/warning issues at catalog build time.
    The matcher silently drops malformed entries so a corrupted catalog
    degrades gracefully rather than crashing at dispatch time.
    """
    if not isinstance(raw, dict):
        return None
    raw_slots = raw.get("slots")
    if not isinstance(raw_slots, list) or len(raw_slots) < 2:
        return None
    slots: list[Slot] = []
    for raw_slot in raw_slots:
        slot = _parse_slot(raw_slot)
        if slot is None:
            return None
        slots.append(slot)
    weight = raw.get("weight")
    if not isinstance(weight, (int, float)) or isinstance(weight, bool):
        return None
    return KeywordGroup(slots=tuple(slots), weight=float(weight))


def _parse_triggers(raw: dict[str, Any]) -> Triggers:
    """Parse the raw ``triggers`` dict from a catalog entry.

    Missing fields default to empty collections per the schema.
    Unknown fields are silently ignored (forward compat).

    Args:
        raw: The ``triggers`` sub-object from a catalog entry.

    Returns:
        A ``Triggers`` instance with all fields populated.
    """
    keywords: list[Keyword] = []
    for kw in raw.get("keywords", []):
        if isinstance(kw, dict) and "term" in kw and "weight" in kw:
            keywords.append(
                Keyword(term=str(kw["term"]).lower(), weight=float(kw["weight"]))
            )

    keyword_groups: list[KeywordGroup] = []
    for raw_group in raw.get("keyword_groups", []):
        group = _parse_keyword_group(raw_group)
        if group is not None:
            keyword_groups.append(group)

    return Triggers(
        command_prefixes=frozenset(
            str(x).lower() for x in raw.get("command_prefixes", [])
        ),
        agent_mentions=frozenset(
            str(x).lower() for x in raw.get("agent_mentions", [])
        ),
        path_globs=tuple(str(x) for x in raw.get("path_globs", [])),
        keywords=tuple(keywords),
        tool_mentions=frozenset(
            str(x).lower() for x in raw.get("tool_mentions", [])
        ),
        excludes=frozenset(str(x).lower() for x in raw.get("excludes", [])),
        keyword_groups=tuple(keyword_groups),
    )
```

- [ ] **Step 2.5: Run tests to verify they pass**

```bash
.venv/Scripts/python.exe -m pytest tests/test_match_keyword_groups.py -v
```

Expected: all 8 tests pass (4 from Task 1 + 4 from Task 2).

- [ ] **Step 2.6: Run full match test module — no regression**

```bash
.venv/Scripts/python.exe -m pytest tests/test_match.py -v
```

Expected: no failures.

- [ ] **Step 2.7: Commit**

```bash
git -C I:/other/claude-wayfinder/.worktrees/and-groups-impl add src/claude_wayfinder/match.py tests/test_match_keyword_groups.py
git -C I:/other/claude-wayfinder/.worktrees/and-groups-impl commit -m "feat(match): parse keyword_groups from catalog dicts

Adds _parse_slot and _parse_keyword_group helpers; extends
_parse_triggers to populate Triggers.keyword_groups.

Matcher is lenient on malformed entries (silently drops);
build_catalog.py owns fatal validation in a later task.

Refs: #135"
```

---

## Task 3: Extend `score()` — group evaluation and singleton suppression

**Files:**
- Modify: `src/claude_wayfinder/match.py:550-603` (`score` function)
- Test: `tests/test_match_keyword_groups.py`

**Why:** This is the core behavior. Implements spec § 5 algorithm exactly.

- [ ] **Step 3.1: Write the failing tests** — the spec § 7.1 worked examples

Append to `tests/test_match_keyword_groups.py`:

```python
class TestScoreWithGroups:
    """Scoring with keyword_groups — spec § 7 worked examples."""

    def _doc_writer_entry(self) -> "_match_mod.CatalogEntry":
        """Doc-writer entry mirroring production singletons + new group."""
        return _match_mod.CatalogEntry(
            name="doc-writer",
            kind="agent",
            triggers=_match_mod.Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=(),
                keywords=(
                    _match_mod.Keyword("docs", 1.0),
                    _match_mod.Keyword("readme", 1.0),
                    _match_mod.Keyword("spec", 1.0),
                    _match_mod.Keyword("update", 0.25),
                    _match_mod.Keyword("edit", 0.25),
                ),
                tool_mentions=frozenset(),
                excludes=frozenset(),
                keyword_groups=(
                    _match_mod.KeywordGroup(
                        slots=(
                            _match_mod.Slot(terms=("update", "edit", "modify", "change")),
                            _match_mod.Slot(terms=("docs", "readme", "spec")),
                        ),
                        weight=1.0,
                    ),
                ),
            ),
            applicable_agents=(),
            applicable_skills=(),
        )

    def test_group_fires_and_suppresses_singletons(self) -> None:
        """Spec § 7.1 row 1: 'update the docs' → doc-writer 1.00.

        Group fires (update + docs both present), contributing
        _GROUP_MULTIPLIER * 1.0 = 1.0. Singletons 'update@0.25' and
        'docs@1.0' are suppressed by replacement rule (spec D5).
        Final score: 1.0 (no singleton residue).
        """
        entry = self._doc_writer_entry()
        features = _match_mod.build_features({"task_description": "update the docs"})
        assert _match_mod.score(entry, features) == pytest.approx(1.0, abs=1e-6)

    def test_group_does_not_fire_singletons_count_normally(self) -> None:
        """Spec § 7.1 row 3: 'the docs are great' → doc-writer 0.50.

        No verb in slot 1 ('the', 'docs', 'are', 'great' has none of
        {update, edit, modify, change}). Group does NOT fire; no
        suppression. Singleton 'docs@1.0' contributes 0.5.
        """
        entry = self._doc_writer_entry()
        features = _match_mod.build_features({"task_description": "the docs are great"})
        assert _match_mod.score(entry, features) == pytest.approx(0.5, abs=1e-6)

    def test_group_unfired_partial_singletons_still_contribute(self) -> None:
        """A prompt that hits only the verb slot, not the noun slot.

        'update the source code' contains 'update' but no doc-noun.
        Group does NOT fire. Singleton 'update@0.25' contributes
        0.5 * 0.25 = 0.125.
        """
        entry = self._doc_writer_entry()
        features = _match_mod.build_features({"task_description": "update the source code"})
        assert _match_mod.score(entry, features) == pytest.approx(0.125, abs=1e-6)

    def test_multiple_satisfied_groups_sum(self) -> None:
        """Spec § 7.3: two satisfied groups on one entry sum independently.

        Skill with two groups; prompt satisfies both.
        Group 1 weight 1.0 → 1.0; group 2 weight 0.5 → 0.5;
        sum = 1.5; min(1.5, 1.0) = 1.0.
        """
        entry = _match_mod.CatalogEntry(
            name="gh-pr-review-address",
            kind="skill",
            triggers=_match_mod.Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=(),
                keywords=(),
                tool_mentions=frozenset(),
                excludes=frozenset(),
                keyword_groups=(
                    _match_mod.KeywordGroup(
                        slots=(
                            _match_mod.Slot(terms=("address", "fix", "handle")),
                            _match_mod.Slot(terms=("review", "comments", "feedback")),
                        ),
                        weight=1.0,
                    ),
                    _match_mod.KeywordGroup(
                        slots=(
                            _match_mod.Slot(terms=("anything",)),
                            _match_mod.Slot(terms=("blocking", "merge")),
                        ),
                        weight=0.5,
                    ),
                ),
            ),
            applicable_agents=(),
            applicable_skills=(),
        )
        features = _match_mod.build_features(
            {"task_description": "address my review comments anything blocking merge"}
        )
        assert _match_mod.score(entry, features) == pytest.approx(1.0, abs=1e-6)

    def test_one_of_two_groups_satisfied(self) -> None:
        """Same entry as above, prompt satisfies only group 1.

        Score = _GROUP_MULTIPLIER * 1.0 (group 1) = 1.0; second group's
        verb slot is unsatisfied so it contributes 0. No singletons.
        """
        entry = _match_mod.CatalogEntry(
            name="gh-pr-review-address",
            kind="skill",
            triggers=_match_mod.Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=(),
                keywords=(),
                tool_mentions=frozenset(),
                excludes=frozenset(),
                keyword_groups=(
                    _match_mod.KeywordGroup(
                        slots=(
                            _match_mod.Slot(terms=("address", "fix", "handle")),
                            _match_mod.Slot(terms=("review", "comments", "feedback")),
                        ),
                        weight=1.0,
                    ),
                    _match_mod.KeywordGroup(
                        slots=(
                            _match_mod.Slot(terms=("anything",)),
                            _match_mod.Slot(terms=("blocking", "merge")),
                        ),
                        weight=0.5,
                    ),
                ),
            ),
            applicable_agents=(),
            applicable_skills=(),
        )
        features = _match_mod.build_features({"task_description": "address my review comments"})
        assert _match_mod.score(entry, features) == pytest.approx(1.0, abs=1e-6)

    def test_no_groups_means_unchanged_behavior(self) -> None:
        """Entry with no keyword_groups scores identically to v0.4.2.

        Regression-locks: doc-writer without groups, same singletons as
        production, scoring 'update the docs' = 0.5*0.25 (update@0.25) +
        0.5*1.0 (docs@1.0) = 0.625.
        """
        entry = _match_mod.CatalogEntry(
            name="doc-writer",
            kind="agent",
            triggers=_match_mod.Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=(),
                keywords=(
                    _match_mod.Keyword("docs", 1.0),
                    _match_mod.Keyword("update", 0.25),
                ),
                tool_mentions=frozenset(),
                excludes=frozenset(),
                keyword_groups=(),
            ),
            applicable_agents=(),
            applicable_skills=(),
        )
        features = _match_mod.build_features({"task_description": "update the docs"})
        assert _match_mod.score(entry, features) == pytest.approx(0.625, abs=1e-6)
```

- [ ] **Step 3.2: Run tests to verify they fail**

```bash
.venv/Scripts/python.exe -m pytest tests/test_match_keyword_groups.py::TestScoreWithGroups -v
```

Expected: `test_group_fires_and_suppresses_singletons` fails (gets 0.625 instead of 1.0 — current `score()` ignores groups). Most other tests fail similarly. `test_no_groups_means_unchanged_behavior` should pass already.

- [ ] **Step 3.3: Modify `score()` to evaluate groups**

In `src/claude_wayfinder/match.py`, locate the `score` function (line 550). Replace the body (preserving the short-circuits and docstring; adjust the docstring's formula comment to match the new algorithm). The full updated function:

```python
def score(entry: CatalogEntry, features: Features) -> float:
    """Compute the match score for one catalog entry against features.

    Implements the scoring formula from spec §5
    (docs/superpowers/specs/2026-05-18-and-groups-design.md)::

        if command_prefix matches → return 1.0
        if agent_mention matches → return 1.0
        if any exclude term in features.keywords → return 0.0
        s  = 0
        s += 0.4 * matched_glob_count
        s += 0.5 * count of matching tool_mentions
        # Group evaluation (collect suppressed terms):
        suppressed = set()
        for group in keyword_groups:
            if all slots filled:
                s += _GROUP_MULTIPLIER * group.weight
                suppressed |= union of slot.terms
        # Singletons (skip suppressed terms):
        s += sum(_KEYWORD_MULTIPLIER * k.weight
                 for k in keywords if k.term matched AND k.term not in suppressed)
        return min(s, 1.0)

    Args:
        entry: One catalog entry to score.
        features: The extracted feature set.

    Returns:
        Float score in [0.0, 1.0].
    """
    t = entry.triggers

    # Short-circuit: exact command prefix match.
    if features.command_prefix and features.command_prefix in t.command_prefixes:
        return 1.0

    # Short-circuit: explicit agent mention.
    if any(m in features.agent_mentions for m in t.agent_mentions):
        return 1.0

    # Hard zero: exclude term present in task keywords.
    if any(x in features.keywords for x in t.excludes):
        return 0.0

    s = 0.0
    # Path glob contributions: 0.4 per matched glob (each counted once).
    s += 0.4 * _matched_glob_count(entry, features)
    # Tool mention contributions: 0.5 per matched tool.
    s += 0.5 * len(
        [t_name for t_name in t.tool_mentions if t_name in features.tool_mentions]
    )

    # Keyword group evaluation (spec §5).
    # A group is satisfied when every slot has at least one term in
    # features.keywords. Satisfied groups contribute _GROUP_MULTIPLIER *
    # weight and suppress singletons for terms named in any of the
    # group's slots (replacement rule, spec D5).
    suppressed: set[str] = set()
    for group in t.keyword_groups:
        if all(
            any(term in features.keywords for term in slot.terms)
            for slot in group.slots
        ):
            s += _GROUP_MULTIPLIER * group.weight
            for slot in group.slots:
                suppressed.update(slot.terms)

    # Keyword contributions: _KEYWORD_MULTIPLIER * weight per matched
    # term, EXCEPT terms covered by a satisfied group (suppressed).
    s += sum(
        _KEYWORD_MULTIPLIER * k.weight
        for k in t.keywords
        if k.term in features.keywords and k.term not in suppressed
    )
    return min(s, 1.0)
```

- [ ] **Step 3.4: Run tests to verify they pass**

```bash
.venv/Scripts/python.exe -m pytest tests/test_match_keyword_groups.py -v
```

Expected: all tests pass (13 total: 4 + 4 + 6 minus one duplicate — actually counting: 4 type tests + 4 parsing tests + 6 scoring tests = 14).

- [ ] **Step 3.5: Run full match-module test — no regression**

```bash
.venv/Scripts/python.exe -m pytest tests/test_match.py -v
```

Expected: no failures. The existing `test_keyword_weight_contributes_multiplier_times_weight` still passes because it uses no `keyword_groups`.

- [ ] **Step 3.6: Commit**

```bash
git -C I:/other/claude-wayfinder/.worktrees/and-groups-impl add src/claude_wayfinder/match.py tests/test_match_keyword_groups.py
git -C I:/other/claude-wayfinder/.worktrees/and-groups-impl commit -m "feat(match): score() evaluates keyword_groups with replacement rule

Implements spec §5 algorithm: satisfied groups contribute
_GROUP_MULTIPLIER * weight and suppress singletons whose terms
appear in any slot of a satisfied group (D5 replacement rule).
Multiple satisfied groups sum independently; min(s, 1.0) clamp
absorbs overflow.

Refs: #135"
```

---

## Task 4: Update dispatch rationale to list fired groups

**Files:**
- Modify: `src/claude_wayfinder/_dispatch.py` (rationale composition)
- Test: extend `tests/test_match_keyword_groups.py`

**Why:** Acceptance criterion #7 — `matcher_decision.output.rationale` should list fired groups for post-launch verification.

- [ ] **Step 4.1: Locate the rationale composition site**

```bash
grep -n "rationale" I:/other/claude-wayfinder/.worktrees/and-groups-impl/src/claude_wayfinder/_dispatch.py
```

Read the surrounding code (use `Read` on `src/claude_wayfinder/_dispatch.py` for ~30 lines around each match). Understand:
1. Where the score-to-rationale mapping is built.
2. What format the existing rationale strings use (e.g., `"matched keywords: implement; globs: **/*.py"`).
3. Whether per-entry rationale is built once and reused or per-decision-branch.

**If** rationale composition is centralised in a helper (likely something like `_build_rationale(entry, features)`), this task amounts to:
- Compute the list of fired groups inside the helper.
- Append a `"groups_fired: [G1, G2]"` segment when non-empty (use the group's slot names if all named, else slot indices).

**If** rationale is built ad-hoc at each branch (delegate/self_handle/etc), extract a helper first.

- [ ] **Step 4.2: Write the failing test**

Append to `tests/test_match_keyword_groups.py`:

```python
class TestRationaleListsFiredGroups:
    """matcher_decision.output.rationale surfaces fired groups (AC #7)."""

    def test_rationale_includes_fired_group_when_satisfied(self, tmp_path) -> None:
        """A satisfied group appears in the decision rationale string."""
        from claude_wayfinder import _dispatch as _disp_mod

        catalog = {
            "schema_version": 1,
            "entries": [
                {
                    "name": "doc-writer",
                    "kind": "agent",
                    "description": "Doc writer.",
                    "source": "owned",
                    "routable": True,
                    "triggers": {
                        "command_prefixes": [],
                        "agent_mentions": [],
                        "path_globs": [],
                        "keywords": [],
                        "tool_mentions": [],
                        "excludes": [],
                        "keyword_groups": [
                            {
                                "slots": [
                                    {"name": "verbs", "terms": ["update", "edit"]},
                                    {"name": "nouns", "terms": ["docs", "readme"]},
                                ],
                                "weight": 1.0,
                            }
                        ],
                    },
                    "applicable_skills": [],
                }
            ],
        }
        import json
        catalog_path = tmp_path / "catalog.json"
        catalog_path.write_text(json.dumps(catalog))

        result = _disp_mod.dispatch(
            catalog_path=catalog_path,
            context={"task_description": "update the docs"},
        )
        # The decision's rationale should mention the group having fired.
        assert "group" in result["rationale"].lower()
        # Slot names (when present) appear in the rationale for traceability.
        # If the rationale uses a structured token like "verbs+nouns" or
        # "[verbs, nouns]", either is acceptable — adjust assertion to
        # the chosen format during implementation.
        assert "verbs" in result["rationale"] or "verbs+nouns" in result["rationale"]
```

(The exact rationale-string format is your call — keep it short and machine-greppable. The test above is intentionally lenient about exact phrasing; tighten it once you've chosen the format. **Pick a stable format** before merging: a future analysis pipeline will read these.)

- [ ] **Step 4.3: Run test to verify it fails**

```bash
.venv/Scripts/python.exe -m pytest tests/test_match_keyword_groups.py::TestRationaleListsFiredGroups -v
```

Expected: rationale string doesn't mention groups.

- [ ] **Step 4.4: Implement rationale update**

In `_dispatch.py` (or wherever the rationale is composed):

1. Pass the `entry` and `features` to the rationale builder so it can re-evaluate which groups fired.
2. Compute fired groups (the same `all(any(...))` predicate as in `score()`; consider extracting a `_group_satisfied(group, features)` helper in `match.py` to avoid duplication).
3. Append a segment to the rationale:
   - If all slots have names: `groups_fired: [verbs+nouns]`
   - Otherwise: `groups_fired: [group_0]` (index)

Concrete suggested helper to add to `match.py`:

```python
def group_satisfied(group: KeywordGroup, features: Features) -> bool:
    """Return True iff every slot has at least one term in features.keywords.

    Public API: used by both score() and dispatch rationale composition.
    """
    return all(
        any(term in features.keywords for term in slot.terms)
        for slot in group.slots
    )
```

Then in `score()`, replace the inline predicate with `group_satisfied(group, features)`. Refactor first, run tests, then proceed with rationale.

- [ ] **Step 4.5: Run all tests**

```bash
.venv/Scripts/python.exe -m pytest tests/ -v
```

Expected: all pass.

- [ ] **Step 4.6: Commit**

```bash
git -C I:/other/claude-wayfinder/.worktrees/and-groups-impl add src/claude_wayfinder/match.py src/claude_wayfinder/_dispatch.py tests/test_match_keyword_groups.py
git -C I:/other/claude-wayfinder/.worktrees/and-groups-impl commit -m "feat(dispatch): rationale lists fired keyword_groups

Extracts group_satisfied() helper for shared use between score()
and rationale composition. Decision rationale now lists groups
that fired during scoring (AC #7).

Refs: #135"
```

---

## Task 5: Build-time validator — `_validate_keyword_groups`

**Files:**
- Modify: `src/claude_wayfinder/build_catalog.py` (add `_validate_keyword_groups`; add `"keyword_groups"` to `TRIGGER_FIELDS`; wire into `validate_entry`)
- Test: `tests/test_build_catalog.py` (add a new `TestValidateKeywordGroups` class)

**Why:** Sidecar and frontmatter authors must get validator feedback at build time per spec § 6.

- [ ] **Step 5.1: Write the failing tests**

Read the existing `tests/test_build_catalog.py` to find the test-helper pattern used for validator tests. Then append a new class:

```python
class TestValidateKeywordGroups:
    """Validator rules for keyword_groups (spec § 6)."""

    def _validate(self, raw_groups, name="doc-writer"):
        """Run only the group validator over a minimal fm dict.

        Returns (sanitized_groups, issues) by calling validate_entry
        with a frontmatter containing only the group config and
        inspecting the result.
        """
        from claude_wayfinder import build_catalog as bc

        fm = {
            "name": name,
            "description": "Doc writer.",
            "triggers": {"keyword_groups": raw_groups},
        }
        result = bc.validate_entry(fm, kind="agent", source_stem=name)
        sanitized = (
            result.entry["triggers"].get("keyword_groups", [])
            if result.entry
            else []
        )
        return sanitized, result.issues

    def test_minimal_valid_group_passes(self):
        groups = [
            {
                "slots": [
                    {"name": "verbs", "terms": ["update", "edit"]},
                    {"name": "nouns", "terms": ["docs", "readme"]},
                ],
                "weight": 1.0,
            }
        ]
        sanitized, issues = self._validate(groups)
        fatals = [i for i in issues if i.severity == "fatal"]
        assert not fatals, [i.message for i in fatals]
        assert len(sanitized) == 1

    def test_group_with_fewer_than_2_slots_is_fatal(self):
        groups = [{"slots": [["docs"]], "weight": 1.0}]
        _, issues = self._validate(groups)
        fatals = [i.message for i in issues if i.severity == "fatal"]
        assert any("2 slots" in m or ">= 2" in m or "at least 2" in m for m in fatals)

    def test_group_with_more_than_8_slots_is_fatal(self):
        slots = [[f"v{i}"] for i in range(9)]
        groups = [{"slots": slots, "weight": 1.0}]
        _, issues = self._validate(groups)
        fatals = [i.message for i in issues if i.severity == "fatal"]
        assert any("8" in m for m in fatals)

    def test_group_with_4_or_more_slots_warns(self):
        slots = [["a"], ["b"], ["c"], ["d"]]
        groups = [{"slots": slots, "weight": 1.0}]
        _, issues = self._validate(groups)
        warns = [i.message for i in issues if i.severity == "warning"]
        # The exact phrasing may vary; check for slot-count signal.
        assert any("4 slots" in m or "many slots" in m or "rarely" in m for m in warns)

    def test_slot_with_zero_terms_is_fatal(self):
        groups = [{"slots": [{"terms": []}, ["docs"]], "weight": 1.0}]
        _, issues = self._validate(groups)
        fatals = [i.message for i in issues if i.severity == "fatal"]
        assert any("terms" in m for m in fatals)

    def test_slot_with_one_term_warns(self):
        groups = [{"slots": [["github"], ["issue", "pr"]], "weight": 1.0}]
        sanitized, issues = self._validate(groups)
        warns = [i.message for i in issues if i.severity == "warning"]
        assert any("single-term" in m.lower() or "1 term" in m.lower() for m in warns)
        # The group is still emitted — single-term slot is allowed.
        assert len(sanitized) == 1

    def test_intra_group_term_overlap_is_fatal(self):
        groups = [
            {
                "slots": [["update", "fix"], ["fix", "repair"]],
                "weight": 1.0,
            }
        ]
        _, issues = self._validate(groups)
        fatals = [i.message for i in issues if i.severity == "fatal"]
        assert any("'fix'" in m or '"fix"' in m for m in fatals)

    def test_weight_outside_allowed_set_is_fatal(self):
        groups = [{"slots": [["a"], ["b"]], "weight": 0.7}]
        _, issues = self._validate(groups)
        fatals = [i.message for i in issues if i.severity == "fatal"]
        assert any("weight" in m.lower() for m in fatals)

    def test_terms_are_lowercased(self):
        groups = [
            {"slots": [["UPDATE", "Edit"], ["DOCS"]], "weight": 1.0}
        ]
        sanitized, _ = self._validate(groups)
        slot1_terms = sanitized[0]["slots"][0]["terms"]
        assert all(t == t.lower() for t in slot1_terms)

    def test_slot_name_with_whitespace_warns(self):
        groups = [
            {
                "slots": [
                    {"name": "verb words", "terms": ["update"]},
                    {"name": "nouns", "terms": ["docs"]},
                ],
                "weight": 1.0,
            }
        ]
        _, issues = self._validate(groups)
        warns = [i.message for i in issues if i.severity == "warning"]
        assert any("name" in m and ("whitespace" in m or "identifier" in m) for m in warns)
```

- [ ] **Step 5.2: Run tests to verify they fail**

```bash
.venv/Scripts/python.exe -m pytest tests/test_build_catalog.py::TestValidateKeywordGroups -v
```

Expected: most fail because the validator doesn't yet inspect `keyword_groups`.

- [ ] **Step 5.3: Implement `_validate_keyword_groups`**

In `src/claude_wayfinder/build_catalog.py`, add the helper. Place it near `_validate_keywords` (around line 321). The function:

```python
def _validate_keyword_groups(
    name: str,
    raw: Any,
) -> tuple[list[dict[str, Any]], list[ValidationIssue]]:
    """Validate triggers.keyword_groups; return (sanitized, issues).

    Spec § 6 validator rules:
        - slots: must be a list of length 2..8 (fatal outside)
        - slots length 4..8: warning ("real prompts rarely contain N roles")
        - each slot: 'terms' list with >= 1 string (1 term: warning)
        - intra-group term overlap: fatal
        - weight: must be in ALLOWED_WEIGHTS (fatal otherwise)
        - slot.name with whitespace: warning

    Sanitized output is the canonical dict form:
        [{'slots': [{'name': str|None, 'terms': [...]}], 'weight': float}, ...]
    """
    issues: list[ValidationIssue] = []

    if raw is None:
        return [], issues  # field is optional

    if not isinstance(raw, list):
        issues.append(
            ValidationIssue(
                "fatal",
                name,
                "'triggers.keyword_groups' must be a list — field dropped",
            )
        )
        return [], issues

    sanitized: list[dict[str, Any]] = []

    for g_idx, raw_group in enumerate(raw):
        if not isinstance(raw_group, dict):
            issues.append(
                ValidationIssue(
                    "fatal",
                    name,
                    f"keyword_groups[{g_idx}] is not a mapping — group dropped",
                )
            )
            continue

        raw_slots = raw_group.get("slots")
        if not isinstance(raw_slots, list):
            issues.append(
                ValidationIssue(
                    "fatal",
                    name,
                    f"keyword_groups[{g_idx}].slots must be a list — group dropped",
                )
            )
            continue
        if len(raw_slots) < 2:
            issues.append(
                ValidationIssue(
                    "fatal",
                    name,
                    f"keyword_groups[{g_idx}] needs >= 2 slots; use 'keywords:' "
                    "for single-term triggers — group dropped",
                )
            )
            continue
        if len(raw_slots) > 8:
            issues.append(
                ValidationIssue(
                    "fatal",
                    name,
                    f"keyword_groups[{g_idx}] has {len(raw_slots)} slots; "
                    "max is 8 — group dropped",
                )
            )
            continue
        if len(raw_slots) >= 4:
            issues.append(
                ValidationIssue(
                    "warning",
                    name,
                    f"keyword_groups[{g_idx}] has {len(raw_slots)} slots — "
                    "real prompts rarely contain that many distinct role tokens; "
                    "verify against real user phrasing",
                )
            )

        # Validate each slot.
        slot_results: list[dict[str, Any]] = []
        slot_fatal = False
        all_terms_per_slot: list[set[str]] = []

        for s_idx, raw_slot in enumerate(raw_slots):
            slot_name: str | None = None
            if isinstance(raw_slot, list):
                raw_terms: list = raw_slot
            elif isinstance(raw_slot, dict):
                raw_terms = raw_slot.get("terms")  # type: ignore[assignment]
                name_val = raw_slot.get("name")
                if isinstance(name_val, str):
                    slot_name = name_val
                    if any(c.isspace() for c in name_val) or not name_val.replace("_", "").isalnum():
                        issues.append(
                            ValidationIssue(
                                "warning",
                                name,
                                f"keyword_groups[{g_idx}].slots[{s_idx}].name "
                                f"'{name_val}' should be a short identifier "
                                "(alphanumeric + underscore)",
                            )
                        )
            else:
                issues.append(
                    ValidationIssue(
                        "fatal",
                        name,
                        f"keyword_groups[{g_idx}].slots[{s_idx}] is neither a "
                        "list nor a mapping — group dropped",
                    )
                )
                slot_fatal = True
                break

            if not isinstance(raw_terms, list) or not raw_terms:
                issues.append(
                    ValidationIssue(
                        "fatal",
                        name,
                        f"keyword_groups[{g_idx}].slots[{s_idx}].terms must "
                        "be a non-empty list — group dropped",
                    )
                )
                slot_fatal = True
                break

            terms: list[str] = []
            for t in raw_terms:
                if not isinstance(t, str) or not t:
                    issues.append(
                        ValidationIssue(
                            "fatal",
                            name,
                            f"keyword_groups[{g_idx}].slots[{s_idx}].terms "
                            "contains a non-string or empty entry — group dropped",
                        )
                    )
                    slot_fatal = True
                    break
                terms.append(t.lower())
            if slot_fatal:
                break

            if len(terms) == 1:
                issues.append(
                    ValidationIssue(
                        "warning",
                        name,
                        f"keyword_groups[{g_idx}].slots[{s_idx}] is a "
                        f"single-term slot ('{terms[0]}') — consider merging into "
                        "an adjacent slot or using 'keywords:' if the term is "
                        "a standalone signal",
                    )
                )

            slot_results.append({"name": slot_name, "terms": terms})
            all_terms_per_slot.append(set(terms))

        if slot_fatal:
            continue

        # Intra-group term overlap check.
        seen: dict[str, int] = {}
        overlap_fatal = False
        for s_idx, term_set in enumerate(all_terms_per_slot):
            for term in term_set:
                if term in seen:
                    issues.append(
                        ValidationIssue(
                            "fatal",
                            name,
                            f"keyword_groups[{g_idx}]: term '{term}' appears "
                            f"in slots[{seen[term]}] AND slots[{s_idx}] — a "
                            "term cannot fill two roles in one expression; "
                            "group dropped",
                        )
                    )
                    overlap_fatal = True
                    break
                seen[term] = s_idx
            if overlap_fatal:
                break
        if overlap_fatal:
            continue

        # Weight validation.
        raw_weight = raw_group.get("weight")
        if isinstance(raw_weight, bool) or not isinstance(raw_weight, (int, float)):
            issues.append(
                ValidationIssue(
                    "fatal",
                    name,
                    f"keyword_groups[{g_idx}].weight must be numeric — "
                    "group dropped",
                )
            )
            continue
        weight_f = float(raw_weight)
        if weight_f not in ALLOWED_WEIGHTS:
            issues.append(
                ValidationIssue(
                    "fatal",
                    name,
                    f"keyword_groups[{g_idx}].weight {weight_f} not in "
                    f"{ALLOWED_WEIGHTS} — group dropped",
                )
            )
            continue

        sanitized.append({"slots": slot_results, "weight": weight_f})

    return sanitized, issues
```

- [ ] **Step 5.4: Add `"keyword_groups"` to `TRIGGER_FIELDS`**

In `src/claude_wayfinder/build_catalog.py` (around line 47), update:

```python
TRIGGER_FIELDS: tuple[str, ...] = (
    "command_prefixes",
    "agent_mentions",
    "path_globs",
    "keywords",
    "tool_mentions",
    "excludes",
    "keyword_groups",
)
```

- [ ] **Step 5.5: Wire `_validate_keyword_groups` into `validate_entry`**

In `validate_entry` (line 445), find the section that calls per-field validators and adds them to `sanitized_triggers`. After the existing `_validate_keywords` call, add:

```python
# --- Validate keyword_groups ---
raw_groups = triggers_raw.get("keyword_groups")
sanitized_groups, group_issues = _validate_keyword_groups(name, raw_groups)
issues.extend(group_issues)
if sanitized_groups:
    sanitized_triggers["keyword_groups"] = sanitized_groups
```

(Locate the exact insertion point by reading `validate_entry`'s body — the existing pattern for `_validate_keywords` shows where to put it.)

- [ ] **Step 5.6: Run tests**

```bash
.venv/Scripts/python.exe -m pytest tests/test_build_catalog.py::TestValidateKeywordGroups -v
.venv/Scripts/python.exe -m pytest tests/test_build_catalog.py -v
```

Expected: new tests pass; existing tests unchanged.

- [ ] **Step 5.7: Commit**

```bash
git -C I:/other/claude-wayfinder/.worktrees/and-groups-impl add src/claude_wayfinder/build_catalog.py tests/test_build_catalog.py
git -C I:/other/claude-wayfinder/.worktrees/and-groups-impl commit -m "feat(build_catalog): validate keyword_groups per spec § 6

Adds _validate_keyword_groups with the full rule table:
- slots: 2..8 (fatal outside, warn at 4..8)
- per-slot terms: >= 1 (warn at exactly 1)
- intra-group term overlap: fatal
- weight: must be in ALLOWED_WEIGHTS
- slot name non-identifier: warn

Wires the validator into validate_entry alongside existing checks.

Refs: #135"
```

---

## Task 6: Regression fixtures + replay test

**Files:**
- Create: `src/claude_wayfinder/fixtures/and_groups/__init__.py` (empty)
- Create: `src/claude_wayfinder/fixtures/and_groups/catalog.json`
- Create: `src/claude_wayfinder/fixtures/and_groups/prompts.json`
- Create: `tests/test_and_groups_replay.py`

**Why:** AC #5 — a regression fixture that locks the targeted behavior. Future refactors must keep these decisions correct.

- [ ] **Step 6.1: Create the catalog fixture**

`src/claude_wayfinder/fixtures/and_groups/catalog.json` — minimal catalog with only code-writer and doc-writer, doc-writer carrying the new group:

```json
{
  "schema_version": 1,
  "catalog_hash": "and_groups_fixture_v1",
  "entries": [
    {
      "name": "code-writer",
      "kind": "agent",
      "description": "Code writer fixture.",
      "source": "owned",
      "routable": true,
      "triggers": {
        "command_prefixes": [],
        "agent_mentions": ["code-writer"],
        "path_globs": ["**/*.py", "*.py"],
        "keywords": [
          {"term": "implement", "weight": 1.0},
          {"term": "write", "weight": 1.0},
          {"term": "update", "weight": 0.5},
          {"term": "edit", "weight": 0.5}
        ],
        "tool_mentions": [],
        "excludes": [],
        "keyword_groups": []
      },
      "applicable_skills": []
    },
    {
      "name": "doc-writer",
      "kind": "agent",
      "description": "Doc writer fixture.",
      "source": "owned",
      "routable": true,
      "triggers": {
        "command_prefixes": [],
        "agent_mentions": ["doc-writer"],
        "path_globs": ["docs/**/*.md", "**/README.md"],
        "keywords": [
          {"term": "docs", "weight": 1.0},
          {"term": "readme", "weight": 1.0},
          {"term": "spec", "weight": 1.0},
          {"term": "update", "weight": 0.25},
          {"term": "edit", "weight": 0.25}
        ],
        "tool_mentions": [],
        "excludes": [],
        "keyword_groups": [
          {
            "slots": [
              {"name": "verbs", "terms": ["update", "edit", "modify", "change"]},
              {"name": "nouns", "terms": ["docs", "readme", "spec"]}
            ],
            "weight": 1.0
          }
        ]
      },
      "applicable_skills": []
    }
  ]
}
```

- [ ] **Step 6.2: Create the prompts fixture**

`src/claude_wayfinder/fixtures/and_groups/prompts.json`:

```json
[
  {
    "prompt": "update the docs",
    "expected_decision": "delegate",
    "expected_agent": "doc-writer",
    "rationale": "spec § 7.1 row 1: verb-noun group fires; doc-writer 1.00, code-writer 0.25"
  },
  {
    "prompt": "edit the readme",
    "expected_decision": "delegate",
    "expected_agent": "doc-writer",
    "rationale": "spec § 7.1 row 2: verb-noun group fires; doc-writer 1.00, code-writer 0.25"
  },
  {
    "prompt": "the docs are great",
    "expected_decision": "self_handle_unaided",
    "expected_agent": null,
    "rationale": "spec § 7.1 row 3: no verb in slot 1, group does not fire; doc-writer 0.50 (attachment-only, no skill matched)"
  },
  {
    "prompt": "implement a new feature in auth.py",
    "expected_decision": "delegate",
    "expected_agent": "code-writer",
    "rationale": "Code-writer keywords + .py glob; group does not fire on doc-writer (no noun)"
  },
  {
    "prompt": "modify the spec document",
    "expected_decision": "delegate",
    "expected_agent": "doc-writer",
    "rationale": "Verb 'modify' (slot 1) + 'spec' (slot 2) → group fires; doc-writer 1.00"
  }
]
```

- [ ] **Step 6.3: Create the empty package marker**

```bash
echo "" > I:/other/claude-wayfinder/.worktrees/and-groups-impl/src/claude_wayfinder/fixtures/and_groups/__init__.py
```

Or, more portably, use the `Write` tool to create an empty file at that path.

- [ ] **Step 6.4: Write the replay test**

`tests/test_and_groups_replay.py`:

```python
"""Replay regression test for keyword_groups fixture.

Validates that spec § 7.1 worked examples produce the expected
dispatch decisions when run against the and_groups catalog fixture.

Spec: docs/superpowers/specs/2026-05-18-and-groups-design.md
Tracking: glitchwerks/claude-wayfinder#135
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_wayfinder import _dispatch as _disp_mod

_FIXTURE_DIR = (
    Path(__file__).parent.parent
    / "src"
    / "claude_wayfinder"
    / "fixtures"
    / "and_groups"
)


def _load_prompts() -> list[dict]:
    return json.loads((_FIXTURE_DIR / "prompts.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("prompt_entry", _load_prompts())
def test_and_groups_replay_case(prompt_entry: dict) -> None:
    """Each fixture prompt produces the expected decision and agent."""
    catalog_path = _FIXTURE_DIR / "catalog.json"
    result = _disp_mod.dispatch(
        catalog_path=catalog_path,
        context={"task_description": prompt_entry["prompt"]},
    )
    assert result["decision"] == prompt_entry["expected_decision"], (
        f"Prompt: {prompt_entry['prompt']!r}\n"
        f"Expected decision: {prompt_entry['expected_decision']}\n"
        f"Got: {result['decision']}\n"
        f"Rationale: {result.get('rationale')}\n"
        f"Note: {prompt_entry['rationale']}"
    )
    if prompt_entry.get("expected_agent"):
        assert result.get("agent") == prompt_entry["expected_agent"], (
            f"Prompt: {prompt_entry['prompt']!r}\n"
            f"Expected agent: {prompt_entry['expected_agent']}\n"
            f"Got: {result.get('agent')}"
        )
```

(If `_dispatch.dispatch()` doesn't have exactly that signature, adjust by reading `src/claude_wayfinder/_dispatch.py` — the same call shape used in Task 4's rationale test.)

- [ ] **Step 6.5: Run the replay test**

```bash
.venv/Scripts/python.exe -m pytest tests/test_and_groups_replay.py -v
```

Expected: all 5 parameterized cases pass.

If any fail, investigate whether the issue is:
- `_dispatch.dispatch()` signature mismatch → fix the test call shape
- A real scoring bug → revisit Task 3
- A fixture math error → recompute scores by hand against spec § 7.1

- [ ] **Step 6.6: Commit**

```bash
git -C I:/other/claude-wayfinder/.worktrees/and-groups-impl add src/claude_wayfinder/fixtures/and_groups/ tests/test_and_groups_replay.py
git -C I:/other/claude-wayfinder/.worktrees/and-groups-impl commit -m "test: regression replay for keyword_groups fixture

Adds catalog + 5 prompts from spec § 7.1 worked examples and a
parameterized replay test. Locks the targeted behavior so future
refactors must keep these decisions correct (AC #5).

Refs: #135"
```

---

## Task 7: Update `docs/design/trigger-schema.md`

**Files:**
- Modify: `docs/design/trigger-schema.md`

**Why:** AC #6 — schema reference, matching rule, validation rule, example.

- [ ] **Step 7.1: Read the existing structure**

```bash
grep -nE "^##? " I:/other/claude-wayfinder/.worktrees/and-groups-impl/docs/design/trigger-schema.md
```

Existing structure (per earlier survey):
- § 1 Purpose and non-goals
- § 2 Schema reference
- § 3 The `features` JSON contract
- § 4 Matching rules
- § 5 `applicable_agents` / `applicable_skills`
- § 6 Validation rules
- § 7 EXCLUDE_DEAD_ZONE detection
- § 8 Backward compatibility
- § 9 Examples
- § 10 Authoring guide
- § 11 Forward work pointers

You'll add subsections to §§ 2, 4, 6, 9.

- [ ] **Step 7.2: Add the schema-reference subsection to § 2**

After the existing `keywords:` block in § 2, add a new subsection. Format match the surrounding YAML+commentary style. The full block to add (adapt heading numbering to whatever the existing § 2 uses):

````markdown
### 2.x `keyword_groups` (added 2026-05-18 per #135)

```yaml
triggers:
  keyword_groups:
    - slots:
        - {name: verbs, terms: [update, edit, modify, change]}
        - {name: nouns, terms: [docs, readme, spec]}
      weight: 1.0
    - slots:
        - [github]
        - [issue, pr, workflow]
      weight: 1.0
```

A `keyword_group` expresses a **conjunctive trigger** — *"all of slot A AND all of slot B"* — that fires only when every slot has at least one matching term in `features.keywords`.

- `slots`: list of 2–8 slot objects. Each slot is either:
  - A bare list of alternative term strings (`[a, b, c]`), or
  - A dict `{terms: [a, b, c], name: "verbs"}` with an optional human-readable `name`.
- `weight`: float in `{0.25, 0.5, 1.0}`. A satisfied group contributes `_GROUP_MULTIPLIER × weight` to the entry's score (currently `_GROUP_MULTIPLIER = 1.0`).
- See § 4.x for matching rules; § 6.x for validation; § 9.x for a worked example.

Authoritative design: `docs/superpowers/specs/2026-05-18-and-groups-design.md`.
````

- [ ] **Step 7.3: Add matching-rule subsection to § 4**

After existing matching rules, add:

````markdown
### 4.x `keyword_groups` matching

A group is **satisfied** when *every* slot contains at least one term in `features.keywords`. Strict all-of; no partial credit.

When satisfied:

1. The group contributes `_GROUP_MULTIPLIER × group.weight` to the entry's pre-clamp score.
2. **Replacement rule:** every singleton keyword on the same entry whose `term` appears in any slot of the satisfied group has its contribution **suppressed** (set to zero) for this scoring pass.

When unsatisfied: zero contribution, no suppression. Singletons score normally.

Multiple satisfied groups on the same entry **sum independently** — each adds its weight. The final `min(s, 1.0)` clamp absorbs any overflow.

Authoritative pseudocode: `docs/superpowers/specs/2026-05-18-and-groups-design.md` § 5.
````

- [ ] **Step 7.4: Add validation subsection to § 6**

````markdown
### 6.x `keyword_groups` validation

| Condition | Severity |
| --- | --- |
| `slots:` missing or has 0 or 1 entries | **fatal** — use `keywords:` for single-term triggers |
| `slots:` has more than 8 entries | **fatal** — max is 8 |
| `slots:` has 4–8 entries | warning — real prompts rarely contain that many roles |
| Slot has 0 terms or missing `terms:` | **fatal** |
| Slot has exactly 1 term | warning — consider merging or using `keywords:` |
| Same term in ≥ 2 slots of the SAME group | **fatal** — a term cannot fill two roles |
| `weight` outside `{0.25, 0.5, 1.0}` | **fatal** |
| `name:` on slot contains whitespace or non-identifier chars | warning |

Cross-group term overlap on the same entry is allowed.
````

- [ ] **Step 7.5: Add example to § 9**

````markdown
### 9.x `keyword_groups` example — doc-writer

```yaml
# Adds a verb-noun conjunctive trigger to doc-writer.
# Existing singletons (docs, readme, spec, update, edit) preserved for
# weak attachment signal when no verb is present.
triggers:
  keywords:
    - {term: docs,   weight: 1.0}
    - {term: readme, weight: 1.0}
    - {term: spec,   weight: 1.0}
    - {term: update, weight: 0.25}
    - {term: edit,   weight: 0.25}
  keyword_groups:
    - slots:
        - {name: verbs, terms: [update, edit, modify, change]}
        - {name: nouns, terms: [docs, readme, spec]}
      weight: 1.0
```

Behavior on representative prompts:

| Prompt | doc-writer score | Reason |
| --- | --- | --- |
| `update the docs` | 1.00 | Group fires; both singletons suppressed (replacement rule) |
| `edit the readme` | 1.00 | Group fires |
| `the docs are great` | 0.50 | Group does NOT fire (no verb); singleton `docs@1.0` contributes 0.5 |
| `modify the spec document` | 1.00 | Group fires |
````

- [ ] **Step 7.6: Verify the file renders correctly**

Read the modified file end-to-end and check section numbering is consistent.

- [ ] **Step 7.7: Commit**

```bash
git -C I:/other/claude-wayfinder/.worktrees/and-groups-impl add docs/design/trigger-schema.md
git -C I:/other/claude-wayfinder/.worktrees/and-groups-impl commit -m "docs(schema): document keyword_groups in trigger-schema.md

Adds subsections to §§ 2 (schema reference), 4 (matching rule),
6 (validation rules), and 9 (worked example). Points to the
authoritative design spec for full rationale.

Refs: #135"
```

---

## Task 8: Full-suite verification + lint

**Files:** none (verification only)

- [ ] **Step 8.1: Run the entire test suite**

```bash
.venv/Scripts/python.exe -m pytest tests/ -v
```

Expected: ALL tests pass (existing + new). If any existing test fails, investigate — the only legitimate failure shapes are:

- A test that constructs `Triggers` directly via positional args (rare; field added at end with default, so positional construction still works).
- A test that asserts on full rationale string match (rare; Task 4 may have appended `groups_fired: [...]` content).

Both should be straightforward to update.

- [ ] **Step 8.2: Run ruff (matches CI)**

```bash
.venv/Scripts/python.exe -m ruff check src/ tests/
```

Expected: no errors. Fix any reported issues with `.venv/Scripts/python.exe -m ruff check --fix src/ tests/` and review the diff before re-running.

- [ ] **Step 8.3: Run any other CI checks the workflow runs**

```bash
ls .github/workflows/
```

Read each workflow file; run any additional commands (mypy, black --check, pre-commit) locally. The rule per CLAUDE.md `# Sub-agent verification mirrors CI` (paraphrased: run every CI gate locally before declaring done).

- [ ] **Step 8.4: Run the dispatch CLI smoke test**

```bash
echo '{"task_description": "update the docs"}' | .venv/Scripts/python.exe -m claude_wayfinder dispatch --catalog-path src/claude_wayfinder/fixtures/and_groups/catalog.json
```

Expected: JSON output with `"decision": "delegate"`, `"agent": "doc-writer"`. End-to-end manual sanity check.

- [ ] **Step 8.5: Commit any fixes**

If lint or test fixes were made:

```bash
git -C I:/other/claude-wayfinder/.worktrees/and-groups-impl add -u
git -C I:/other/claude-wayfinder/.worktrees/and-groups-impl commit -m "chore: lint + CI fixes from full-suite verification

Refs: #135"
```

If nothing changed, skip this commit.

---

## Task 9: Open the implementation PR

- [ ] **Step 9.1: Push the branch**

```bash
git -C I:/other/claude-wayfinder/.worktrees/and-groups-impl push -u origin feature/and-groups-impl
```

- [ ] **Step 9.2: Open the PR via `mcp__github__create_pull_request`**

PR title: `feat(matcher): implement keyword_groups conjunctive triggers (#135)`

PR base: depends on whether spec PR #139 has merged yet:

- **If #139 still open:** base = `feature/and-groups-spec`. Mention in the body that the PR is stacked on #139 and will be re-targeted to `main` after #139 merges.
- **If #139 merged:** base = `main`. Rebase the implementation branch onto `main` first:
  ```bash
  git -C I:/other/claude-wayfinder/.worktrees/and-groups-impl fetch origin
  git -C I:/other/claude-wayfinder/.worktrees/and-groups-impl rebase origin/main
  git -C I:/other/claude-wayfinder/.worktrees/and-groups-impl push --force-with-lease
  ```

PR body:

```markdown
## Summary

Implements [#135](https://github.com/glitchwerks/claude-wayfinder/issues/135) — `keyword_groups` (AND-of-slots conjunctive triggers) per the approved spec.

- **Spec:** `docs/superpowers/specs/2026-05-18-and-groups-design.md` (landed in #139)
- **Plan:** `docs/superpowers/plans/2026-05-18-and-groups-implementation.md` (deleted in the final commit per CLAUDE.md plan-file lifecycle)

## Changes

- `src/claude_wayfinder/match.py` — adds `Slot`, `KeywordGroup`, `_GROUP_MULTIPLIER`; extends `Triggers` and `_parse_triggers`; extends `score()` with strict-AND group satisfaction and replacement-rule suppression.
- `src/claude_wayfinder/build_catalog.py` — adds `_validate_keyword_groups` with full rule table from spec § 6; wires into `validate_entry`.
- `src/claude_wayfinder/_dispatch.py` — rationale lists fired groups (AC #7).
- `src/claude_wayfinder/fixtures/and_groups/` — regression fixture (catalog + 5 prompts from spec § 7.1).
- `tests/test_match_keyword_groups.py` — new test module (types, parsing, scoring).
- `tests/test_build_catalog.py` — extended with `TestValidateKeywordGroups`.
- `tests/test_and_groups_replay.py` — new replay test.
- `docs/design/trigger-schema.md` — adds subsections to §§ 2, 4, 6, 9.

## Test plan

- [ ] `pytest tests/` — all pass locally
- [ ] `ruff check src/ tests/` — clean
- [ ] CI green on this PR
- [ ] Manual smoke: `echo '{"task_description":"update the docs"}' | python -m claude_wayfinder dispatch --catalog-path src/claude_wayfinder/fixtures/and_groups/catalog.json` returns `delegate doc-writer`

## Backward compatibility

- Catalogs without `keyword_groups:` parse and score identically to v0.4.x.
- Existing `_KEYWORD_MULTIPLIER` unchanged at 0.5.
- No new dependencies.

Closes #135

---
🤖 _Generated by Claude Code on behalf of @cbeaulieu-gt_
```

(Per CLAUDE.md `# Pull Requests`: the `Closes #135` is in the PR body in plain text — not in commit messages alone — so squash-merge will fire the close.)

- [ ] **Step 9.3: Verify CI**

```bash
scripts/wait-for-pr-checks.sh <PR_NUMBER>
```

(Or the equivalent — see CLAUDE.md `# Background Polling` for the canonical helper.) Address any failures before requesting review.

- [ ] **Step 9.4: Delete the plan file (final commit)**

Per CLAUDE.md `# Document Files § Lifecycle`: when the implementing PR is open and the plan has served its purpose, delete the plan file. Extract any durable rationale into the PR body first (already done in step 9.2 above).

```bash
git -C I:/other/claude-wayfinder/.worktrees/and-groups-impl rm docs/superpowers/plans/2026-05-18-and-groups-implementation.md
git -C I:/other/claude-wayfinder/.worktrees/and-groups-impl commit -m "chore: delete implementation plan now that PR is open

Plan file's checklist is complete; PR body captures the change
summary. Per CLAUDE.md plan-file lifecycle (extract durable info,
then delete).

Refs: #135"
git -C I:/other/claude-wayfinder/.worktrees/and-groups-impl push
```

---

## Self-review

After writing this plan, I checked it against the spec:

**Spec coverage:**

| Spec section | Plan task |
| --- | --- |
| § 2 D1 (sibling field) | Task 2 (extend Triggers) |
| § 2 D2 (flat two-layer grammar) | Implicit in dataclass shape (Slot/KeywordGroup); enforced by Task 5 validator |
| § 2 D3 (strict all-of) | Task 3 (`all(any(...))` in score()) |
| § 2 D4 (`_GROUP_MULTIPLIER = 1.0`) | Task 1 (constant); Task 3 (use it) |
| § 2 D5 (replacement rule) | Task 3 (suppression logic) |
| § 2 D6 (multi-group SUM) | Task 3 (loop accumulates); test in Task 3 step 3.1 |
| § 2 D7 (positional slots + optional name) | Task 1 (Slot dataclass); Task 2 (parser) |
| § 2 D8 (N ∈ [2, 8]; warn at ≥4) | Task 5 (validator) |
| § 2 D9 (intra-group overlap = error) | Task 5 (validator) |
| § 2 D10 (per-slot terms ≥ 1, warn at 1) | Task 5 (validator) |
| § 5 algorithm | Task 3 (score()) |
| § 6 validator table | Task 5 (all 8 rules covered) |
| § 7 worked examples | Task 3 tests; Task 6 fixture |
| § 8 backward compat | Task 2 (default empty tuple); Task 3 test `test_no_groups_means_unchanged_behavior` |
| § 9 AC#1 (matcher honors groups) | Task 3 |
| § 9 AC#2 (builder picks up) | Task 5 |
| § 9 AC#3 (validator emits per § 6) | Task 5 |
| § 9 AC#4 (regression locked) | Task 8 step 8.1 (full suite) |
| § 9 AC#5 (forward test fixture) | Task 6 |
| § 9 AC#6 (trigger-schema.md updated) | Task 7 |
| § 9 AC#7 (rationale lists groups) | Task 4 |
| § 9 AC#8 (stdlib-only) | No deps added |

**Placeholder scan:** None.

**Type consistency:** `Slot`/`KeywordGroup` names used consistently. `group_satisfied()` helper extracted in Task 4 and used in both `score()` and rationale.

---

## Execution handoff

Plan complete and committed (next step) to `docs/superpowers/plans/2026-05-18-and-groups-implementation.md`. Two execution options:

**1. Subagent-Driven (recommended)** — Router dispatches a fresh subagent per task, two-stage review between tasks, fast iteration. Sub-skill: `superpowers:subagent-driven-development`.

**2. Inline Execution** — Execute tasks in the current session using `superpowers:executing-plans`, batch execution with checkpoints for review.

Tell the router which approach you want.
