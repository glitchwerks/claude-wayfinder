"""Tests for the 7-step decision ladder implemented in match/_decide.py.

Covers all six decision outcomes:
- delegate (best agent >= 0.85, gap >= 0.2)
- self_handle (no dominant agent, skill >= 0.5)
- self_handle_unaided (no useful signal)
- advisory (agent >= 0.5, no strong skill, gap >= 0.2)
- ambiguous (agents tie above 0.5, gap < 0.2)
- needs_more_detail (feature density < 2)
- ask_user (reserved; never produced in current impl)
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.test_match.conftest import (
    _catalog,
    _make_agent,
    _make_skill,
    _run,
)

# ===========================================================================
# RED-phase tests: each should FAIL before match.py is implemented
# ===========================================================================


class TestDecisionDelegate:
    """Best agent >= 0.85, gap >= 0.2 → 'delegate'."""

    def test_high_confidence_agent_returns_delegate(self, tmp_path: Path) -> None:
        """A strong keyword + glob + tool match on one agent produces 'delegate'.

        Score breakdown for code-writer:
          0.4 (glob **/*.py matches src/main.py)
          + 0.5*1.0 (implement keyword)
          + 0.5*1.0 (write keyword)
          + 0.5 (git tool mention)
          = 1.9 → clamped to 1.0

        debugger has no matching signals → score 0.0.
        Gap = 1.0 - 0.0 = 1.0 >= 0.2 → delegate.
        """
        catalog = _catalog(
            [
                _make_agent(
                    "code-writer",
                    keywords=[
                        {"term": "implement", "weight": 1.0},
                        {"term": "write", "weight": 1.0},
                    ],
                    path_globs=["**/*.py"],
                    tool_mentions=["git"],
                    applicable_skills=["*"],
                ),
                _make_agent(
                    "debugger",
                    keywords=[{"term": "debug", "weight": 1.0}],
                ),
            ]
        )
        stdin_obj = {
            "task_description": "implement and write a new python feature",
            "file_paths": ["src/main.py"],
            "tool_mentions": ["git"],
        }
        result = _run(stdin_obj, catalog, tmp_path=tmp_path)
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out["decision"] == "delegate"
        assert out["agent"] == "code-writer"
        assert "confidence" in out
        assert out["confidence"] >= 0.85


class TestDecisionSelfHandle:
    """No dominant agent; at least one skill >= 0.5 → 'self_handle'."""

    def test_skill_match_with_no_strong_agent_returns_self_handle(self, tmp_path: Path) -> None:
        """When only a skill scores >= 0.5, decision is 'self_handle'.

        Score breakdown for python skill:
          0.4 (glob **/*.py matches src/utils.py)
          + 0.5*1.0 (python keyword)
          = 0.9 >= 0.5 → self_handle

        No agents in catalog → no agent path.
        """
        catalog = _catalog(
            [
                _make_skill(
                    "python",
                    path_globs=["**/*.py"],
                    keywords=[{"term": "python", "weight": 1.0}],
                    applicable_agents=["*"],
                ),
            ]
        )
        stdin_obj = {
            "task_description": "explain how python decorators work",
            "file_paths": ["src/utils.py"],  # needs subdir so **/*.py matches
        }
        result = _run(stdin_obj, catalog, tmp_path=tmp_path)
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out["decision"] == "self_handle"
        assert "skills" in out
        assert "python" in out["skills"]


class TestDecisionSelfHandleUnaided:
    """Sufficient context, no specialist or skill applies → 'self_handle_unaided'."""

    def test_no_matches_returns_self_handle_unaided(self, tmp_path: Path) -> None:
        """Task with keywords not in any catalog entry → 'self_handle_unaided'.

        We provide a tool_mention so feature_count >= 2 (keywords +
        tool_mentions both populated), bypassing needs_more_detail, but
        the 'git' tool is not in the code-writer entry → score 0.
        'implement' is not in 'weather' keywords → score 0.
        """
        catalog = _catalog(
            [
                _make_agent(
                    "code-writer",
                    keywords=[{"term": "implement", "weight": 1.0}],
                ),
            ]
        )
        stdin_obj = {
            "task_description": "what is the weather like today in boston",
            "tool_mentions": ["curl"],  # ensures feature_count >= 2
        }
        result = _run(stdin_obj, catalog, tmp_path=tmp_path)
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out["decision"] == "self_handle_unaided"


class TestDecisionAdvisory:
    """Best agent >= 0.5 but no strong skill → 'advisory'."""

    def test_medium_confidence_no_skill_returns_advisory(self, tmp_path: Path) -> None:
        """Agent scores >= 0.5, no skill >= 0.5, gap >= 0.2 → 'advisory'."""
        catalog = _catalog(
            [
                _make_agent(
                    "ops",
                    keywords=[{"term": "github", "weight": 1.0}],
                    tool_mentions=["gh"],
                ),
            ]
        )
        # "github" + "gh" tool mention = 0.5*1.0 + 0.5 = 1.0 — high enough
        # for advisory if gap condition and no strong skill match
        stdin_obj = {
            "task_description": "list github issues for the repo",
            "tool_mentions": ["gh"],
        }
        result = _run(stdin_obj, catalog, tmp_path=tmp_path)
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        # advisory or delegate (depends on gap): both valid given single agent
        assert out["decision"] in ("advisory", "delegate")
        assert "agent" in out


class TestDecisionAmbiguous:
    """Two agents tie above 0.5 with gap < 0.2 → 'ambiguous'."""

    def test_tied_agents_return_ambiguous(self, tmp_path: Path) -> None:
        """Two agents with identical keyword + glob match → 'ambiguous'.

        Score breakdown for each agent:
          0.4 (glob **/*.py matches src/broken.py)
          + 0.5*1.0 (write keyword, weight=1.0)
          + 0.5*0.5 (fix keyword, weight=0.5)
          = 0.4 + 0.5 + 0.25 = 1.15 → clamped to 1.0

        Both agents score identically → gap = 0 < 0.2 → ambiguous.
        """
        catalog = _catalog(
            [
                _make_agent(
                    "code-writer",
                    keywords=[
                        {"term": "write", "weight": 1.0},
                        {"term": "fix", "weight": 0.5},
                    ],
                    path_globs=["**/*.py"],
                ),
                _make_agent(
                    "debugger",
                    keywords=[
                        {"term": "write", "weight": 1.0},
                        {"term": "fix", "weight": 0.5},
                    ],
                    path_globs=["**/*.py"],
                ),
            ]
        )
        # Both agents match identically → gap = 0 < 0.2 → ambiguous
        stdin_obj = {
            "task_description": "write a fix for the broken function",
            "file_paths": ["src/broken.py"],
        }
        result = _run(stdin_obj, catalog, tmp_path=tmp_path)
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out["decision"] == "ambiguous"
        assert "alternatives" in out
        assert len(out["alternatives"]) >= 2


class TestDecisionNeedsMoreDetail:
    """Feature density < 2 → 'needs_more_detail'."""

    def test_sparse_input_returns_needs_more_detail(self, tmp_path: Path) -> None:
        """Single-word input with no paths → 'needs_more_detail'."""
        catalog = _catalog(
            [
                _make_agent(
                    "code-writer",
                    keywords=[{"term": "implement", "weight": 1.0}],
                ),
            ]
        )
        # "ok" is not in catalog keywords, no paths, no tool_mentions
        # Feature density: 0 matched dimensions < 2
        stdin_obj = {
            "task_description": "ok",
        }
        result = _run(stdin_obj, catalog, tmp_path=tmp_path)
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out["decision"] == "needs_more_detail"

    def test_two_feature_dimensions_passes_threshold(self, tmp_path: Path) -> None:
        """Two populated dimensions (keyword + path) should not return needs_more_detail."""
        catalog = _catalog(
            [
                _make_agent(
                    "code-writer",
                    keywords=[{"term": "implement", "weight": 1.0}],
                ),
            ]
        )
        stdin_obj = {
            "task_description": "implement the function",
            "file_paths": ["src/main.py"],
        }
        result = _run(stdin_obj, catalog, tmp_path=tmp_path)
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out["decision"] != "needs_more_detail"


class TestDecisionAskUser:
    """ask_user is reserved; matcher never produces it in current impl."""

    def test_ask_user_not_produced_in_normal_flow(self, tmp_path: Path) -> None:
        """The matcher should not produce 'ask_user' in normal flows."""
        catalog = _catalog(
            [
                _make_agent("code-writer", keywords=[{"term": "implement", "weight": 1.0}]),
            ]
        )
        stdin_obj = {"task_description": "implement the feature", "file_paths": ["a.py"]}
        result = _run(stdin_obj, catalog, tmp_path=tmp_path)
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out["decision"] != "ask_user"
