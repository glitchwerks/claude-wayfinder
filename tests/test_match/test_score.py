"""Tests for the scoring engine implemented in match/_match.py.

Covers:
- Per-entry scoring rules (command_prefix, agent_mention, excludes,
  path_glob, keyword_weight, tool_mention, score_cap)
- path_globs_excluded: per-path-subtractive exclusion (issue #24, #287)
- General-purpose / routable=False exclusion from agent pool
- Feature density gate (needs_more_detail threshold)
- Issue #425 keyword-multiplier regression (multiplier raised 0.3→0.5)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_match.conftest import (
    _build_synthetic_catalog,
    _catalog,
    _make_agent,
    _make_skill,
    _match_mod,
    _run,
)

# ===========================================================================
# Scoring rules
# ===========================================================================


class TestScoringRules:
    """Per-entry scoring matches spec §3.1.2 exactly."""

    def test_command_prefix_short_circuits_to_1_0(self, tmp_path: Path) -> None:
        """command_prefix exact match → score 1.0 → delegate."""
        catalog = _catalog(
            [
                _make_agent(
                    "code-writer",
                    command_prefixes=["/implement"],
                ),
            ]
        )
        stdin_obj = {
            "task_description": "run /implement on the task",
            "command_prefix": "/implement",
        }
        result = _run(stdin_obj, catalog, tmp_path=tmp_path)
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        # score=1.0, single agent → delegate
        assert out["decision"] == "delegate"
        assert out["confidence"] == pytest.approx(1.0)

    def test_agent_mention_short_circuits_to_1_0(self, tmp_path: Path) -> None:
        """Explicit agent mention → score 1.0."""
        catalog = _catalog(
            [
                _make_agent(
                    "debugger",
                    agent_mentions=["debugger"],
                ),
            ]
        )
        stdin_obj = {
            "task_description": "debug this crash",
            "agent_mentions": ["debugger"],
        }
        result = _run(stdin_obj, catalog, tmp_path=tmp_path)
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out["decision"] == "delegate"
        assert out["agent"] == "debugger"

    def test_excludes_hard_zeros_an_agent(self, tmp_path: Path) -> None:
        """Keyword in excludes → score forced to 0.0."""
        catalog = _catalog(
            [
                _make_agent(
                    "azure-agent",
                    keywords=[{"term": "azure", "weight": 1.0}],
                    excludes=["aws"],
                ),
                _make_agent(
                    "code-writer",
                    keywords=[{"term": "implement", "weight": 1.0}],
                ),
            ]
        )
        # "aws" triggers the exclude on azure-agent → score 0
        stdin_obj = {
            "task_description": "implement an aws function",
            "file_paths": ["main.py"],
        }
        result = _run(stdin_obj, catalog, tmp_path=tmp_path)
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        # azure-agent is zeroed; code-writer may match "implement"
        assert out.get("agent") != "azure-agent"

    def test_path_glob_contributes_0_4(self, tmp_path: Path) -> None:
        """Each matched path glob adds 0.4 per distinct glob (capped at 1.0).

        We use direct function import to test the raw score formula
        without the decision-layer threshold interfering.
        """
        mod = _match_mod

        entry = mod.CatalogEntry(
            name="bicep-agent",
            kind="agent",
            triggers=mod.Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=("**/*.bicep",),
                keywords=(),
                tool_mentions=frozenset(),
                excludes=frozenset(),
            ),
            applicable_agents=(),
            applicable_skills=(),
        )
        features = mod.build_features(
            {
                "task_description": "update the azure deployment",
                "file_paths": ["infra/main.bicep", "infra/db.bicep"],
            }
        )
        # Single glob matches two paths but is counted at most once → +0.4
        raw_score = mod.score(entry, features)
        assert raw_score == pytest.approx(0.4, abs=1e-6)

    def test_keyword_weight_contributes_multiplier_times_weight(self, tmp_path: Path) -> None:
        """Each matched keyword adds _KEYWORD_MULTIPLIER * weight.

        We use direct function import to test the raw scoring formula.
        Expected (post-#425 fix, multiplier=0.5):
          0.5 * 1.0 + 0.5 * 0.5 = 0.75

        Updated from the original 0.3 assertion (which expected 0.45) when
        the keyword multiplier was raised from 0.3 to 0.5 in issue #425.
        """
        mod = _match_mod

        entry = mod.CatalogEntry(
            name="code-writer",
            kind="agent",
            triggers=mod.Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=(),
                keywords=(
                    mod.Keyword("implement", 1.0),
                    mod.Keyword("feature", 0.5),
                ),
                tool_mentions=frozenset(),
                excludes=frozenset(),
            ),
            applicable_agents=(),
            applicable_skills=(),
        )
        features = mod.build_features(
            {
                "task_description": "implement a new feature",
            }
        )
        raw_score = mod.score(entry, features)
        assert raw_score == pytest.approx(0.75, abs=1e-6)

    def test_tool_mention_contributes_0_5(self, tmp_path: Path) -> None:
        """Matched tool mention adds 0.5."""
        catalog = _catalog(
            [
                _make_agent(
                    "ops",
                    tool_mentions=["gh"],
                ),
            ]
        )
        stdin_obj = {
            "task_description": "run the gh command to list prs",
            "tool_mentions": ["gh"],
        }
        result = _run(stdin_obj, catalog, tmp_path=tmp_path)
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out["confidence"] == pytest.approx(0.5, abs=1e-6)

    def test_score_capped_at_1_0(self, tmp_path: Path) -> None:
        """Combined signal exceeding 1.0 is clamped to exactly 1.0."""
        catalog = _catalog(
            [
                _make_agent(
                    "power-agent",
                    keywords=[
                        {"term": "implement", "weight": 1.0},
                        {"term": "feature", "weight": 1.0},
                        {"term": "write", "weight": 1.0},
                    ],
                    path_globs=["**/*.py"],
                    tool_mentions=["git"],
                ),
            ]
        )
        stdin_obj = {
            "task_description": "implement a feature write it now",
            "file_paths": ["main.py"],
            "tool_mentions": ["git"],
        }
        result = _run(stdin_obj, catalog, tmp_path=tmp_path)
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out["confidence"] <= 1.0


# ===========================================================================
# general-purpose exclusion
# ===========================================================================


class TestGeneralPurposeExclusion:
    """The router agent (routable=False) is never in the scored agents pool."""

    def test_general_purpose_excluded_from_agent_pool(self, tmp_path: Path) -> None:
        """The router agent never wins as best_agent, even with keyword matches.

        Exclusion is driven by ``routable=False`` in the catalog entry,
        not by the name ``"general-purpose"`` (issue #19).
        """
        catalog = _catalog(
            [
                _make_agent(
                    "general-purpose",
                    keywords=[{"term": "anything", "weight": 1.0}],
                    routable=False,
                ),
                _make_agent(
                    "code-writer",
                    keywords=[{"term": "implement", "weight": 1.0}],
                ),
            ]
        )
        stdin_obj = {
            "task_description": "implement anything new now with code",
            "file_paths": ["main.py"],
        }
        result = _run(stdin_obj, catalog, tmp_path=tmp_path)
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out.get("agent") != "general-purpose"
        if "alternatives" in out:
            alt_names = [a["agent"] for a in out["alternatives"]]
            assert "general-purpose" not in alt_names


# ===========================================================================
# Feature density
# ===========================================================================


class TestFeatureDensity:
    """Feature density controls needs_more_detail gating."""

    def test_one_dimension_returns_needs_more_detail(self, tmp_path: Path) -> None:
        """Only keyword matches, no paths/tools/mentions → needs_more_detail."""
        catalog = _catalog(
            [
                _make_agent(
                    "code-writer",
                    keywords=[{"term": "implement", "weight": 1.0}],
                ),
            ]
        )
        # One keyword in features.keywords, but no other signal dimensions
        stdin_obj = {
            "task_description": "implement",
        }
        result = _run(stdin_obj, catalog, tmp_path=tmp_path)
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out["decision"] == "needs_more_detail"

    def test_keyword_plus_path_passes_density_check(self, tmp_path: Path) -> None:
        """Keyword match + path provided → density >= 2 → not needs_more_detail."""
        catalog = _catalog(
            [
                _make_agent(
                    "code-writer",
                    keywords=[{"term": "implement", "weight": 1.0}],
                ),
            ]
        )
        stdin_obj = {
            "task_description": "implement the module",
            "file_paths": ["src/module.py"],
        }
        result = _run(stdin_obj, catalog, tmp_path=tmp_path)
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out["decision"] != "needs_more_detail"


# ===========================================================================
# Issue #425 keyword multiplier regression
# ===========================================================================


class TestIssue425KeywordMultiplier:
    """Regression tests for issue #425.

    Before the fix, the keyword score multiplier was 0.3, meaning a single
    weight-1.0 keyword contributed only 0.3 — below the _SKILL_MIN threshold
    of 0.5.  Skills that matched only one primary keyword could never attach.

    After the fix (multiplier raised to 0.5), a single weight-1.0 keyword
    contributes exactly 0.5, hitting the threshold precisely.

    Tests use synthetic catalog fixtures so they are independent of catalog
    drift.
    """

    def test_single_weight1_keyword_score_reaches_threshold(self) -> None:
        """A skill with one weight-1.0 keyword hitting the task scores >= 0.5.

        Regression guard for #425: with the old 0.3 multiplier the score was
        0.3 (below _SKILL_MIN=0.5).  With the corrected 0.5 multiplier the
        score is exactly 0.5.

        Uses direct module import and the raw ``score()`` function to isolate
        the formula without decision-layer threshold logic.
        """
        mod = _match_mod

        entry = mod.CatalogEntry(
            name="synthetic-skill",
            kind="skill",
            triggers=mod.Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=(),
                keywords=(mod.Keyword("refactor", 1.0),),
                tool_mentions=frozenset(),
                excludes=frozenset(),
            ),
            applicable_agents=(),
            applicable_skills=(),
        )
        features = mod.build_features(
            {
                "task_description": "refactor the authentication module",
            }
        )
        raw_score = mod.score(entry, features)
        assert raw_score >= 0.5, (
            f"Single weight-1.0 keyword scored {raw_score:.4f} — "
            "expected >= 0.5 after multiplier fix (#425). "
            "Check that keyword multiplier is 0.5, not 0.3."
        )

    def test_single_weight1_keyword_skill_attaches_via_self_handle(self, tmp_path: Path) -> None:
        """A skill with one weight-1.0 keyword triggers self_handle when hit.

        End-to-end subprocess test: confirms the decision layer respects the
        updated threshold, not just the raw scoring function.  The task hits
        exactly one keyword ('refactor', weight=1.0) and no globs or tools.
        Decision must be 'self_handle' with the synthetic skill in the output.
        """
        catalog = _catalog(
            [
                _make_skill(
                    "synthetic-skill",
                    keywords=[{"term": "refactor", "weight": 1.0}],
                    applicable_agents=["*"],
                ),
            ]
        )
        stdin_obj = {
            "task_description": "refactor the authentication module",
            "file_paths": ["src/auth.py"],  # path provides density dimension
        }
        result = _run(stdin_obj, catalog, tmp_path=tmp_path)
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out["decision"] == "self_handle", (
            f"Expected 'self_handle', got '{out['decision']}' — "
            "single weight-1.0 keyword must now clear the 0.5 threshold (#425)"
        )
        assert "synthetic-skill" in out.get("skills", []), (
            f"Expected 'synthetic-skill' in skills={out.get('skills')} — "
            "skill must attach when its only keyword scores >= 0.5"
        )

    def test_real_world_refactoring_discipline_attaches(self, tmp_path: Path) -> None:
        """refactoring-discipline attaches for the exact #402 reproduction case.

        Task: 'refactor the auth module to extract credential validation'
        File: src/auth.py
        Tools: none

        With old multiplier (0.3):
          0.3 * 1.0 (refactor) + 0.3 * 0.25 (extract) = 0.375 — does NOT attach.

        With new multiplier (0.5):
          0.5 * 1.0 (refactor) + 0.5 * 0.25 (extract) = 0.625 — attaches.

        This test builds from the synthetic fixture catalog so it validates the
        fixture refactoring-discipline triggers.yml against the fixed scorer.
        """
        catalog_path = _build_synthetic_catalog(tmp_path)
        stdin_obj = {
            "task_description": ("refactor the auth module to extract credential validation"),
            "file_paths": ["src/auth.py"],
        }
        result = _run(
            stdin_obj,
            {},
            catalog_path=catalog_path,
            tmp_path=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert "refactoring-discipline" in out.get("skills", []), (
            f"Expected 'refactoring-discipline' in skills={out.get('skills')} "
            f"(decision={out['decision']!r}, confidence={out.get('confidence')}) — "
            "this is the exact #402 unblock check. "
            "Ensure keyword multiplier is 0.5 in score() and "
            "refactoring-discipline/triggers.yml has 'refactor' weight=1.0 "
            "and 'extract' weight=0.25."
        )


# ===========================================================================
# Issue #24 / #287: path_globs_excluded — per-path-subtractive exclusion
# ===========================================================================


class TestPathGlobsExcluded:
    """path_globs_excluded uses per-path-subtractive semantics (#287).

    An excluded path contributes 0 to the agent's path score.  Other
    paths in the same input are unaffected — they still contribute
    normally.  Issue #24 (original field), #287 (semantic change).
    """

    def test_excluded_path_contributes_zero(self) -> None:
        """A path matching path_globs_excluded contributes 0 to score.

        An agent with ``path_globs: ['**/*.py']`` and
        ``path_globs_excluded: ['agents/**/*.py', 'agents/*.py']`` must
        score 0.0 when the only input path is ``agents/foo.py`` —
        excluded path contributes nothing, non-excluded paths are absent.

        Uses the raw ``score()`` function to isolate matcher logic from
        the decision layer.
        """
        mod = _match_mod

        entry = mod.CatalogEntry(
            name="test-agent",
            kind="agent",
            triggers=mod.Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=("**/*.py",),
                keywords=(),
                tool_mentions=frozenset(),
                excludes=frozenset(),
                path_globs_excluded=("agents/**/*.py", "agents/*.py"),
            ),
            applicable_agents=(),
            applicable_skills=(),
        )
        features = mod.build_features(
            {
                "task_description": "update the agent",
                "file_paths": ["agents/foo.py"],
            }
        )
        raw_score = mod.score(entry, features)
        assert raw_score == 0.0, (
            f"score() returned {raw_score!r} for an excluded-only input — "
            "excluded path must contribute 0; with no other paths score "
            "should be 0.0 (#287)"
        )

    def test_non_excluded_path_is_scored_normally(self) -> None:
        """A path that does NOT match path_globs_excluded is scored normally.

        Same entry as above but candidate path is ``src/foo.py``,
        which does not match ``agents/**/*.py`` or ``agents/*.py``.
        The path_globs ``**/*.py`` should match, giving +0.4.
        """
        mod = _match_mod

        entry = mod.CatalogEntry(
            name="test-agent",
            kind="agent",
            triggers=mod.Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=("**/*.py",),
                keywords=(),
                tool_mentions=frozenset(),
                excludes=frozenset(),
                path_globs_excluded=("agents/**/*.py", "agents/*.py"),
            ),
            applicable_agents=(),
            applicable_skills=(),
        )
        features = mod.build_features(
            {
                "task_description": "update the module",
                "file_paths": ["src/foo.py"],
            }
        )
        raw_score = mod.score(entry, features)
        assert raw_score == pytest.approx(0.4, abs=1e-6), (
            f"score() returned {raw_score!r} for a non-excluded path — "
            "expected 0.4 (one path_globs match) when path_globs_excluded "
            "does NOT match the candidate path (#24)"
        )

    def test_excluded_only_path_yields_zero_score(self) -> None:
        """An entry whose only input path is excluded scores 0.0.

        An entry with ``path_globs: ['**/*']`` (match everything) and
        ``path_globs_excluded: ['secret.py']`` must score 0.0 when the
        only candidate is ``secret.py`` — the excluded path contributes
        nothing, leaving the total at 0.0.

        This is the per-path-subtractive equivalent of the old
        "exclusion wins over inclusion" hard-exclude test (#287).
        """
        mod = _match_mod

        entry = mod.CatalogEntry(
            name="broad-agent",
            kind="agent",
            triggers=mod.Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=("**/*",),
                keywords=(),
                tool_mentions=frozenset(),
                excludes=frozenset(),
                path_globs_excluded=("secret.py",),
            ),
            applicable_agents=(),
            applicable_skills=(),
        )
        features = mod.build_features(
            {
                "task_description": "handle secret file",
                "file_paths": ["secret.py"],
            }
        )
        raw_score = mod.score(entry, features)
        assert raw_score == 0.0, (
            f"score() returned {raw_score!r} for an excluded-only path — "
            "excluded path contributes 0; with no other paths total "
            "must be 0.0 (#287)"
        )

    def test_empty_path_globs_excluded_has_no_effect(self) -> None:
        """Default empty path_globs_excluded does not affect scoring.

        An entry with no path_globs_excluded (default ``()``) must
        score normally — 0.4 for one matched path glob.
        """
        mod = _match_mod

        entry = mod.CatalogEntry(
            name="normal-agent",
            kind="agent",
            triggers=mod.Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=("**/*.py",),
                keywords=(),
                tool_mentions=frozenset(),
                excludes=frozenset(),
                # path_globs_excluded not specified — should default to ()
            ),
            applicable_agents=(),
            applicable_skills=(),
        )
        features = mod.build_features(
            {
                "task_description": "update the module",
                "file_paths": ["src/foo.py"],
            }
        )
        raw_score = mod.score(entry, features)
        assert raw_score == pytest.approx(0.4, abs=1e-6), (
            f"score() returned {raw_score!r} — "
            "empty path_globs_excluded must not affect scoring (#24)"
        )


# ===========================================================================
# Issue #287: per-path-subtractive semantics for path_globs_excluded
# ===========================================================================


class TestPathGlobsExcludedPerPathSubtractive:
    """path_globs_excluded is per-path-subtractive, not a hard-exclude (#287).

    An excluded path contributes 0 to this agent's path score.
    Other paths in the same input are unaffected — positive contributions
    from non-excluded paths remain intact.

    The old hard-exclude zeroed the whole agent when ANY path matched an
    excluded glob.  The new semantic only zeroes the contribution of the
    matched path; other paths continue to score normally.
    """

    def test_mixed_paths_excluded_path_does_not_zero_agent(self) -> None:
        """Excluded path contributes 0; other paths still raise the score.

        An agent with ``path_globs: ['**/*.py']`` and
        ``path_globs_excluded: ['agents/*.py']`` presented with two
        paths — one excluded (``agents/foo.py``) and one not
        (``src/main.py``) — must score 0.4 (the non-excluded path
        contributes one matched glob) rather than 0.0 (old hard-exclude).

        This directly tests the per-path-subtractive semantic: the
        excluded path's contribution is suppressed, but the other path's
        contribution is unaffected (#287).
        """
        mod = _match_mod

        entry = mod.CatalogEntry(
            name="test-agent",
            kind="agent",
            triggers=mod.Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=("**/*.py",),
                keywords=(),
                tool_mentions=frozenset(),
                excludes=frozenset(),
                path_globs_excluded=("agents/**/*.py", "agents/*.py"),
            ),
            applicable_agents=(),
            applicable_skills=(),
        )
        features = mod.build_features(
            {
                "task_description": "update the module",
                "file_paths": ["agents/foo.py", "src/main.py"],
            }
        )
        raw_score = mod.score(entry, features)
        # agents/foo.py is excluded → contributes 0.
        # src/main.py matches **/*.py → contributes 0.4 (one glob).
        # Total: 0.4, not 0.0 (old hard-exclude would have given 0.0).
        assert raw_score == pytest.approx(0.4, abs=1e-6), (
            f"score() returned {raw_score!r} — expected 0.4 from the "
            "non-excluded path; excluded path must not zero the agent "
            "under per-path-subtractive semantics (#287)"
        )

    def test_all_excluded_paths_yields_zero_score(self) -> None:
        """When all paths are excluded, path contribution is 0.0.

        An agent whose every input path matches an excluded glob gets
        zero path contribution.  Combined with no keyword/tool matches
        this yields 0.0 overall — the same observable result as the old
        hard-exclude, but for a structurally different reason (#287).
        """
        mod = _match_mod

        entry = mod.CatalogEntry(
            name="test-agent",
            kind="agent",
            triggers=mod.Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=("**/*.py",),
                keywords=(),
                tool_mentions=frozenset(),
                excludes=frozenset(),
                path_globs_excluded=("agents/**/*.py", "agents/*.py"),
            ),
            applicable_agents=(),
            applicable_skills=(),
        )
        features = mod.build_features(
            {
                "task_description": "update the agent",
                "file_paths": ["agents/foo.py", "agents/bar.py"],
            }
        )
        raw_score = mod.score(entry, features)
        assert raw_score == pytest.approx(0.0, abs=1e-6), (
            f"score() returned {raw_score!r} — all paths excluded must "
            "yield 0.0 path contribution (#287)"
        )

    def test_issue_287_reproducer_code_writer_wins_with_mixed_paths(
        self, tmp_path: Path
    ) -> None:
        """Issue #287 exact reproducer: code-writer must win via delegate.

        Catalog has ``code-writer`` with broad ``**/*.py`` globs and
        ``path_globs_excluded: ['docs/skills/**/*.md', 'docs/skills/*.md']``.
        Input has 5 paths: 3 code paths (.py, .yml) and 2 doc paths
        (one of which matches the excluded glob).

        With OLD hard-exclude semantics, presence of
        ``docs/skills/link-lint.md`` zeroed code-writer's path score → it
        dropped to ``self_handle_unaided``.

        With NEW per-path-subtractive semantics, only
        ``docs/skills/link-lint.md`` contributes 0; the remaining 4 paths
        (3 code + 1 non-skills doc) each contribute normally → code-writer
        scores high enough for ``delegate``.
        """
        catalog = _catalog(
            [
                _make_agent(
                    "code-writer",
                    keywords=[
                        {"term": "implement", "weight": 1.0},
                        {"term": "issue", "weight": 0.25},
                        {"term": "integration", "weight": 0.5},
                    ],
                    path_globs=[
                        "**/*.py",
                        "*.py",
                        "**/*.yml",
                        "*.yml",
                        "**/*.md",
                        "*.md",
                    ],
                    path_globs_excluded=[
                        "docs/skills/**/*.md",
                        "docs/skills/*.md",
                    ],
                ),
            ]
        )
        stdin_obj = {
            "task_description": (
                "Implement issue #211 cross-skill link lint integration"
            ),
            "file_paths": [
                "scripts/lint_skill_links.py",
                "scripts/tests/test_lint_skill_links.py",
                "src/lint/core.py",
                "docs/skills/link-lint.md",
                "hooks/pre-commit-link-lint.yml",
            ],
            "agent_mentions": [],
            "tool_mentions": [],
            "command_prefix": None,
        }
        result = _run(stdin_obj, catalog, tmp_path=tmp_path)
        assert result.returncode == 0, (
            f"matcher exited {result.returncode}:\n{result.stderr}"
        )
        output = json.loads(result.stdout)
        assert output["decision"] == "delegate", (
            f"expected decision='delegate' but got {output['decision']!r}; "
            "code-writer must win despite docs/skills/link-lint.md being "
            "excluded — excluded paths are per-path-subtractive (#287)\n"
            f"full output: {output}"
        )
        assert output.get("agent") == "code-writer", (
            f"expected agent='code-writer' but got {output.get('agent')!r}; "
            f"full output: {output}"
        )
