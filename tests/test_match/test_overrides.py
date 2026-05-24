"""Tests for OverrideRule and OverrideMatch dataclasses.

Verifies field shapes, immutability, and basic predicate storage for the
override-rule types defined in claude_wayfinder.match._types.
"""

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
