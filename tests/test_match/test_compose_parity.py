"""Return-shape parity tests for posture-routed compose decisions."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from claude_wayfinder.match._compose import compose_route
from claude_wayfinder.match._decide import decide
from claude_wayfinder.match._match import build_features
from claude_wayfinder.match._parse import _parse_triggers
from claude_wayfinder.match._types import (
    CatalogEntry,
    Features,
    Labels,
    ScoredEntry,
)


def _entry(
    name: str,
    *,
    kind: str = "agent",
    applicable_agents: tuple[str, ...] = (),
) -> CatalogEntry:
    """Build a minimal catalog entry for routing tests.

    Args:
        name: Catalog entry name.
        kind: Entry kind, either ``"agent"`` or ``"skill"``.
        applicable_agents: Agent names to which a skill applies.

    Returns:
        A routable catalog entry with no lexical triggers.
    """
    triggers = _parse_triggers(
        {
            "command_prefixes": [],
            "agent_mentions": [],
            "path_globs": [],
            "path_globs_excluded": [],
            "keywords": [],
            "tool_mentions": [],
            "excludes": [],
        }
    )
    return CatalogEntry(
        name=name,
        kind=kind,
        source="owned",
        routable=True,
        triggers=triggers,
        applicable_skills=(),
        applicable_agents=applicable_agents,
    )


def _make_gated(
    names_scores: list[tuple[str, float]],
) -> list[ScoredEntry]:
    """Build a score-sorted scored-agent list.

    Args:
        names_scores: Agent-name and score pairs.

    Returns:
        Scored entries sorted by descending score, then name.
    """
    scored = [
        ScoredEntry(entry=_entry(name), score=score)
        for name, score in names_scores
    ]
    return sorted(scored, key=lambda se: (-se.score, se.entry.name))


def _features(
    task_description: str = "implement the requested change",
    *,
    tool_mentions: list[str] | None = None,
) -> Features:
    """Build a feature-dense context for routing tests.

    Args:
        task_description: Text from which keyword features are extracted.
        tool_mentions: Optional explicit tool mentions.

    Returns:
        Extracted matcher features.
    """
    return build_features(
        {
            "task_description": task_description,
            "file_paths": ["src/example.py"],
            "agent_mentions": [],
            "tool_mentions": tool_mentions or [],
            "command_prefix": None,
        }
    )


def _skill_scores() -> list[ScoredEntry]:
    """Build a strong universally applicable skill score.

    Returns:
        A single scored ``python`` skill applicable to every agent.
    """
    return [
        ScoredEntry(
            entry=_entry(
                "python",
                kind="skill",
                applicable_agents=("*",),
            ),
            score=0.8,
        )
    ]


def _compose_fixture(
    labels: Labels,
    names_scores: list[tuple[str, float]],
    *,
    features: Features | None = None,
) -> dict[str, Any]:
    """Call ``compose_route`` with a compact synthetic catalog.

    Args:
        labels: Two-axis labels selecting the posture branch.
        names_scores: Scored agent names and scores.
        features: Optional feature set for branch-specific signals.

    Returns:
        The composed routing decision.
    """
    scored_agents = _make_gated(names_scores)
    catalog = [se.entry for se in scored_agents]
    return compose_route(
        labels=labels,
        scored_agents=scored_agents,
        scored_skills=_skill_scores(),
        features=features or _features(),
        catalog=catalog,
        catalog_agent_names=frozenset(se.entry.name for se in scored_agents),
    )


def _branch1() -> dict[str, Any]:
    """Return a Branch-1 investigator decision."""
    return _compose_fixture(
        Labels(
            domain="docs_prose",
            posture="diagnose",
            confidence="high",
            area_span=2,
        ),
        [("investigator", 0.9), ("doc-writer", 0.6)],
    )


def _branch2() -> dict[str, Any]:
    """Return a Branch-2 sentinel self-handle decision."""
    return _compose_fixture(
        Labels(
            domain="project_meta",
            posture="build",
            confidence=None,
        ),
        [("project-planner", 0.9), ("project-reviewer", 0.6)],
    )


def _branch3_generic() -> dict[str, Any]:
    """Return a Branch-3 generic code-writer decision."""
    return _compose_fixture(
        Labels(domain="code", posture="build", confidence="high"),
        [("code-writer", 0.9), ("debugger", 0.6)],
    )


def _branch3_testfirst() -> dict[str, Any]:
    """Return a Branch-3 test-first redirect decision."""
    return _compose_fixture(
        Labels(domain="code", posture="build", confidence="high"),
        [
            ("code-writer", 0.9),
            ("test-implementer", 0.85),
            ("debugger", 0.6),
        ],
        features=_features("Write failing pytest tests first"),
    )


def _branch3_ops_write() -> dict[str, Any]:
    """Return a Branch-3 ops write-veto decision."""
    return _compose_fixture(
        Labels(domain="is_any", posture="operate", confidence="high"),
        [("ops", 0.9), ("code-writer", 0.6)],
        features=_features(tool_mentions=["mcp__github__create_issue"]),
    )


def _branch3_ops_no_signal() -> dict[str, Any]:
    """Return a Branch-3 ops no-signal veto decision."""
    return _compose_fixture(
        Labels(domain="is_any", posture="operate", confidence="high"),
        [("ops", 0.9), ("code-writer", 0.6)],
    )


def _decide_result(decision: str) -> dict[str, Any]:
    """Produce a natural ``decide()`` result of the requested type.

    Args:
        decision: Expected decision type, ``delegate`` or ``self_handle``.

    Returns:
        An unrelated decision fixture with the requested decision type.

    Raises:
        ValueError: If *decision* is not supported by this helper.
    """
    features = _features("implement parser behavior")
    skill_scores = _skill_scores()
    if decision == "delegate":
        scored_agents = _make_gated(
            [("unrelated-winner", 0.9), ("unrelated-runner-up", 0.5)]
        )
    elif decision == "self_handle":
        scored_agents = _make_gated(
            [("unrelated-winner", 0.7), ("unrelated-runner-up", 0.65)]
        )
    else:
        raise ValueError(f"Unsupported decision fixture: {decision}")
    return decide(
        scored_agents,
        skill_scores,
        features,
        [se.entry for se in scored_agents],
    )


@pytest.mark.parametrize(
    ("scenario", "expected_decision"),
    [
        (_branch1, "delegate"),
        (_branch3_generic, "delegate"),
        (_branch3_testfirst, "delegate"),
        (_branch2, "self_handle"),
        (_branch3_ops_write, "self_handle"),
        (_branch3_ops_no_signal, "self_handle"),
    ],
    ids=[
        "branch1-investigator",
        "branch3-generic",
        "branch3-testfirst",
        "branch2-sentinel",
        "branch3-ops-write-veto",
        "branch3-ops-no-signal-veto",
    ],
)
def test_posture_routes_match_decide_return_shape(
    scenario: Callable[[], dict[str, Any]],
    expected_decision: str,
) -> None:
    """Posture decisions carry the corresponding ``decide()`` fields.

    Args:
        scenario: Factory for one posture-routed decision.
        expected_decision: Decision type expected from both fixtures.
    """
    result = scenario()
    decide_result = _decide_result(expected_decision)

    assert result["decision"] == expected_decision
    assert decide_result["decision"] == expected_decision
    if expected_decision == "delegate":
        assert set(result) == set(decide_result)
    else:
        assert set(decide_result) <= set(result)
    assert result["disposition_source"] == "posture_routed"
    if scenario is _branch3_testfirst:
        assert result["agent"] == "test-implementer"
    assert "skills" in result
    assert "rationale" in result
    assert "alternatives" in result
    assert result["skills"] == ["python"]
    if scenario is _branch1:
        assert result["alternatives"] == [
            {"agent": "doc-writer", "score": 0.6}
        ]
        assert result["rationale"] == (
            "posture route: diagnose × area_span>=2 → investigator"
        )
    elif scenario is _branch2:
        assert result["rationale"] == (
            "posture route: project_meta × build → router self-handles"
        )


def test_branch1_missing_scored_agent_has_empty_skills() -> None:
    """Branch 1 tolerates a catalog-known investigator never scored."""
    scored_agents = _make_gated([("doc-writer", 0.9)])
    catalog = [_entry("investigator"), _entry("doc-writer")]

    result = compose_route(
        labels=Labels(
            domain="docs_prose",
            posture="diagnose",
            confidence="high",
            area_span=2,
        ),
        scored_agents=scored_agents,
        scored_skills=_skill_scores(),
        features=_features("investigate documentation behavior"),
        catalog=catalog,
        catalog_agent_names=frozenset({"investigator", "doc-writer"}),
    )

    assert result["decision"] == "delegate"
    assert result["agent"] == "investigator"
    assert result["skills"] == []
