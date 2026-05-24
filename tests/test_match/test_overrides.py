"""Tests for OverrideRule, OverrideMatch dataclasses, and load_overrides().

Verifies field shapes, immutability, and basic predicate storage for the
override-rule types defined in claude_wayfinder.match._types, plus full
loading/validation behaviour for load_overrides() in _overrides.
"""

import json
from pathlib import Path

import pytest

from claude_wayfinder.match._types import OverrideMatch, OverrideRule


def test_override_rule_required_fields() -> None:
    """OverrideRule stores all required fields and returns them correctly."""
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


def test_override_match_carries_rule_and_decision() -> None:
    """OverrideMatch stores the matched rule and which predicates matched."""
    rule = OverrideRule(
        id="r1",
        decision="self_handle_unaided",
        agent=None,
        skills=(),
        confidence=1.0,
        rationale="bypass",
        command_prefix="/skip",
        path_globs=(),
        tool_mentions=frozenset(),
    )
    m = OverrideMatch(rule=rule, matched_predicates=("command_prefix",))
    assert m.rule.id == "r1"
    assert "command_prefix" in m.matched_predicates


# ---------------------------------------------------------------------------
# load_overrides() tests — RED phase (import from _overrides not yet created)
# ---------------------------------------------------------------------------

from claude_wayfinder.match._overrides import (  # noqa: E402
    OverridesError,
    load_overrides,
)


def _write(tmp_path: Path, payload: dict) -> Path:
    """Write payload as JSON to overrides.json inside tmp_path."""
    p = tmp_path / "overrides.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def test_load_overrides_empty_rules(tmp_path: Path) -> None:
    """load_overrides returns an empty list when rules list is empty."""
    p = _write(tmp_path, {"version": 1, "rules": []})
    assert load_overrides(p) == []


def test_load_overrides_parses_one_rule(tmp_path: Path) -> None:
    """load_overrides parses a single valid rule into an OverrideRule."""
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


def test_load_overrides_missing_file_raises(tmp_path: Path) -> None:
    """load_overrides raises OverridesError when file does not exist."""
    with pytest.raises(OverridesError, match="not found"):
        load_overrides(tmp_path / "nope.json")


def test_load_overrides_malformed_json_raises(tmp_path: Path) -> None:
    """load_overrides raises OverridesError when file contains invalid JSON."""
    p = tmp_path / "broken.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(OverridesError, match="malformed"):
        load_overrides(p)


def test_load_overrides_invalid_decision_raises(tmp_path: Path) -> None:
    """load_overrides raises OverridesError when decision is not in VALID_DECISIONS."""
    p = _write(tmp_path, {
        "version": 1,
        "rules": [{
            "id": "bad",
            "decision": "not_a_real_decision",
            "agent": None,
            "skills": [],
            "confidence": 0.5,
            "rationale": "x",
            "predicates": {"command_prefix": "/x"},
        }],
    })
    with pytest.raises(OverridesError, match="invalid decision"):
        load_overrides(p)


# ---------------------------------------------------------------------------
# resolve_override() tests — RED phase
# ---------------------------------------------------------------------------

from claude_wayfinder.match._overrides import resolve_override  # noqa: E402
from claude_wayfinder.match._types import Features  # noqa: E402


def _rule(rid: str = "r", **predicates: object) -> OverrideRule:
    """Build a minimal OverrideRule for resolver tests."""
    return OverrideRule(
        id=rid,
        decision="delegate",
        agent="code-writer",
        skills=("python",),
        confidence=0.99,
        rationale="t",
        command_prefix=predicates.get("command_prefix"),  # type: ignore[arg-type]
        path_globs=tuple(predicates.get("path_globs", ())),  # type: ignore[arg-type]
        tool_mentions=frozenset(predicates.get("tool_mentions", ())),  # type: ignore[arg-type]
    )


def test_resolve_override_no_rules_returns_none() -> None:
    """resolve_override returns None when the rules list is empty."""
    assert resolve_override([], Features()) is None


def test_resolve_override_path_glob_match() -> None:
    """resolve_override returns an OverrideMatch when path_globs hit."""
    rule = _rule(path_globs=("**/*.py",))
    f = Features(paths=("src/foo.py",))
    m = resolve_override([rule], f)
    assert m is not None
    assert m.rule.id == "r"
    assert "path_globs" in m.matched_predicates


def test_resolve_override_command_prefix_match() -> None:
    """resolve_override returns an OverrideMatch when command_prefix hit."""
    rule = _rule(command_prefix="/deploy")
    f = Features(command_prefix="/deploy")
    m = resolve_override([rule], f)
    assert m is not None


def test_resolve_override_tool_mentions_match() -> None:
    """resolve_override returns an OverrideMatch when tool_mentions overlap."""
    rule = _rule(tool_mentions=("Bash",))
    f = Features(tool_mentions=frozenset({"Bash", "Read"}))
    m = resolve_override([rule], f)
    assert m is not None


def test_resolve_override_and_combined_predicates() -> None:
    """All active predicates must match (AND semantics); partial match fails."""
    rule = _rule(command_prefix="/x", path_globs=("*.md",))
    # command_prefix matches but no path matches -> no overall match
    f = Features(command_prefix="/x", paths=("src/foo.py",))
    assert resolve_override([rule], f) is None


def test_resolve_override_first_match_wins() -> None:
    """The first rule whose predicates all fire is returned; later rules skip."""
    r1 = _rule(rid="first", path_globs=("**/*.py",))
    r2 = _rule(rid="second", path_globs=("**/*.py",))
    f = Features(paths=("src/foo.py",))
    m = resolve_override([r1, r2], f)
    assert m is not None
    assert m.rule.id == "first"


def test_resolve_override_zero_predicates_never_matches() -> None:
    """Defense in depth: a rule with zero predicates never fires at runtime."""
    rule = _rule()  # no predicates set
    f = Features(paths=("any.py",))
    assert resolve_override([rule], f) is None
