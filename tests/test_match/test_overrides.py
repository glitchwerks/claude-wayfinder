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
