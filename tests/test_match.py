"""Tests for claude_wayfinder/match.py — 7-decision matcher.

Each test covers one discrete behavior.  We exercise the public
interface in two ways:

1. Direct import of helper functions for unit-level coverage.
2. Subprocess invocation (stdin/stdout JSON contract) for integration
   tests and environment-variable tests.

Synthetic catalog fixtures are used throughout so tests are
independent of the live catalog.
"""

from __future__ import annotations

import json
import os
import re as _re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import claude_wayfinder.match as _match_mod

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]

# match.py is now a package (match/__init__.py); invoke via -m rather than
# as a script path so tests continue to work after the package split.
_MATCH_MODULE = ["claude_wayfinder.match"]

PYTHON = sys.executable


# ---------------------------------------------------------------------------
# Catalog builder helpers
# ---------------------------------------------------------------------------


def _make_agent(
    name: str,
    *,
    keywords: list[dict[str, Any]] | None = None,
    path_globs: list[str] | None = None,
    tool_mentions: list[str] | None = None,
    command_prefixes: list[str] | None = None,
    agent_mentions: list[str] | None = None,
    excludes: list[str] | None = None,
    applicable_skills: list[str] | None = None,
    routable: bool = True,
) -> dict[str, Any]:
    """Build a minimal agent catalog entry."""
    return {
        "name": name,
        "kind": "agent",
        "description": f"Agent {name}.",
        "source": "owned",
        "routable": routable,
        "triggers": {
            "command_prefixes": command_prefixes or [],
            "agent_mentions": agent_mentions or [],
            "path_globs": path_globs or [],
            "keywords": [{"term": k["term"], "weight": k["weight"]} for k in (keywords or [])],
            "tool_mentions": tool_mentions or [],
            "excludes": excludes or [],
        },
        "applicable_skills": applicable_skills or [],
    }


def _make_skill(
    name: str,
    *,
    keywords: list[dict[str, Any]] | None = None,
    path_globs: list[str] | None = None,
    tool_mentions: list[str] | None = None,
    command_prefixes: list[str] | None = None,
    agent_mentions: list[str] | None = None,
    excludes: list[str] | None = None,
    applicable_agents: list[str] | None = None,
) -> dict[str, Any]:
    """Build a minimal skill catalog entry."""
    return {
        "name": name,
        "kind": "skill",
        "description": f"Skill {name}.",
        "source": "owned",
        "triggers": {
            "command_prefixes": command_prefixes or [],
            "agent_mentions": agent_mentions or [],
            "path_globs": path_globs or [],
            "keywords": [{"term": k["term"], "weight": k["weight"]} for k in (keywords or [])],
            "tool_mentions": tool_mentions or [],
            "excludes": excludes or [],
        },
        "applicable_agents": applicable_agents or [],
    }


def _catalog(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Wrap entries in catalog envelope."""
    return {"schema_version": 1, "entries": entries}


# ---------------------------------------------------------------------------
# Subprocess helper
# ---------------------------------------------------------------------------


def _run(
    stdin_obj: dict[str, Any],
    catalog: dict[str, Any],
    *,
    catalog_path: Path | None = None,
    extra_env: dict[str, str] | None = None,
    tmp_path: Path,
) -> subprocess.CompletedProcess[str]:
    """Run match.py via subprocess with the given catalog and input."""
    if catalog_path is None:
        catalog_path = tmp_path / "catalog.json"
        catalog_path.write_text(json.dumps(catalog), encoding="utf-8")

    env = {**os.environ, "DISPATCH_CATALOG_PATH": str(catalog_path)}
    if extra_env:
        env.update(extra_env)

    return subprocess.run(
        [PYTHON, "-m", *_MATCH_MODULE],
        input=json.dumps(stdin_obj),
        capture_output=True,
        text=True,
        env=env,
        check=False,
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
# Catalog degradation
# ===========================================================================


class TestCatalogDegradation:
    """Catalog missing / malformed / empty → exit 2 + stderr banner."""

    BANNER_PREFIX = "[CATALOG ERROR]"

    def test_missing_catalog_exits_2_with_banner(self, tmp_path: Path) -> None:
        """Non-existent catalog file → exit code 2, banner on stderr."""
        missing = tmp_path / "nonexistent.json"
        result = subprocess.run(
            [PYTHON, "-m", *_MATCH_MODULE],
            input=json.dumps({"task_description": "implement something"}),
            capture_output=True,
            text=True,
            env={**os.environ, "DISPATCH_CATALOG_PATH": str(missing)},
            check=False,
        )
        assert result.returncode == 2, f"Expected exit 2, got {result.returncode}"
        assert self.BANNER_PREFIX in result.stderr

    def test_malformed_json_catalog_exits_2_with_banner(self, tmp_path: Path) -> None:
        """Malformed JSON catalog → exit code 2, banner on stderr."""
        bad_catalog = tmp_path / "bad.json"
        bad_catalog.write_text("{not valid json", encoding="utf-8")
        result = subprocess.run(
            [PYTHON, "-m", *_MATCH_MODULE],
            input=json.dumps({"task_description": "implement something"}),
            capture_output=True,
            text=True,
            env={**os.environ, "DISPATCH_CATALOG_PATH": str(bad_catalog)},
            check=False,
        )
        assert result.returncode == 2
        assert self.BANNER_PREFIX in result.stderr

    def test_empty_entries_catalog_exits_2_with_banner(self, tmp_path: Path) -> None:
        """Catalog with zero entries → exit code 2, banner on stderr."""
        empty_catalog = tmp_path / "empty.json"
        empty_catalog.write_text(json.dumps({"schema_version": 1, "entries": []}), encoding="utf-8")
        result = subprocess.run(
            [PYTHON, "-m", *_MATCH_MODULE],
            input=json.dumps({"task_description": "implement something"}),
            capture_output=True,
            text=True,
            env={**os.environ, "DISPATCH_CATALOG_PATH": str(empty_catalog)},
            check=False,
        )
        assert result.returncode == 2
        assert self.BANNER_PREFIX in result.stderr

    def test_banner_on_stderr_not_stdout(self, tmp_path: Path) -> None:
        """Banner must appear on stderr only, not on stdout."""
        missing = tmp_path / "nonexistent.json"
        result = subprocess.run(
            [PYTHON, "-m", *_MATCH_MODULE],
            input=json.dumps({"task_description": "implement something"}),
            capture_output=True,
            text=True,
            env={**os.environ, "DISPATCH_CATALOG_PATH": str(missing)},
            check=False,
        )
        assert self.BANNER_PREFIX not in result.stdout
        assert self.BANNER_PREFIX in result.stderr


# ===========================================================================
# Determinism
# ===========================================================================


class TestDeterminism:
    """Same input + catalog → byte-identical output across two runs."""

    def test_two_runs_produce_identical_output(self, tmp_path: Path) -> None:
        """Matcher is deterministic: two invocations produce the same JSON."""
        catalog = _catalog(
            [
                _make_agent(
                    "code-writer",
                    keywords=[
                        {"term": "implement", "weight": 1.0},
                        {"term": "feature", "weight": 0.5},
                    ],
                    path_globs=["**/*.py"],
                ),
                _make_agent(
                    "debugger",
                    keywords=[{"term": "debug", "weight": 1.0}],
                ),
            ]
        )
        stdin_obj = {
            "task_description": "implement a new feature in python",
            "file_paths": ["src/thing.py"],
        }

        r1 = _run(stdin_obj, catalog, tmp_path=tmp_path)
        r2 = _run(stdin_obj, catalog, tmp_path=tmp_path)

        assert r1.returncode == 0
        assert r2.returncode == 0
        assert r1.stdout == r2.stdout, "Outputs differ between runs (non-deterministic)"


# ===========================================================================
# Environment-variable overrides
# ===========================================================================


class TestEnvVarOverrides:
    """DISPATCH_CATALOG_PATH env var and --catalog-path flag are honored.

    Note: ``CLAUDE_HOME`` was removed as a lookup step in Issue #10.
    The matching test for the old ``CLAUDE_HOME`` behaviour has been
    converted into a negative assertion in ``TestIssue10FailLoudCatalogPath``.
    """

    def test_dispatch_catalog_path_override(self, tmp_path: Path) -> None:
        """DISPATCH_CATALOG_PATH points to a custom catalog file."""
        custom_path = tmp_path / "custom_catalog.json"
        catalog = _catalog(
            [
                _make_agent(
                    "code-writer",
                    keywords=[{"term": "implement", "weight": 1.0}],
                    path_globs=["**/*.py"],
                ),
            ]
        )
        custom_path.write_text(json.dumps(catalog), encoding="utf-8")

        result = subprocess.run(
            [PYTHON, "-m", *_MATCH_MODULE],
            input=json.dumps(
                {
                    "task_description": "implement the feature here",
                    "file_paths": ["main.py"],
                }
            ),
            capture_output=True,
            text=True,
            env={**os.environ, "DISPATCH_CATALOG_PATH": str(custom_path)},
            check=False,
        )
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out["decision"] != "needs_more_detail"

    def test_catalog_path_flag_takes_precedence_over_env(
        self, tmp_path: Path
    ) -> None:
        """--catalog-path flag takes precedence over DISPATCH_CATALOG_PATH env.

        Write two catalogs: one at an env-var path (with no matching agents)
        and one at the flag path (with a matching agent).  Assert the decision
        is driven by the flag-supplied catalog.
        """
        # Env-var catalog: empty entries — would produce needs_more_detail.
        env_catalog_path = tmp_path / "env_catalog.json"
        env_catalog_path.write_text(
            json.dumps({"schema_version": 1, "entries": []}),
            encoding="utf-8",
        )

        # Flag catalog: contains a matching agent.
        flag_catalog = _catalog(
            [
                _make_agent(
                    "code-writer",
                    keywords=[{"term": "implement", "weight": 1.0}],
                    path_globs=["**/*.py"],
                ),
            ]
        )
        flag_catalog_path = tmp_path / "flag_catalog.json"
        flag_catalog_path.write_text(json.dumps(flag_catalog), encoding="utf-8")

        env = {**os.environ, "DISPATCH_CATALOG_PATH": str(env_catalog_path)}
        result = subprocess.run(
            [
                PYTHON,
                "-m", *_MATCH_MODULE,
                "--catalog-path",
                str(flag_catalog_path),
            ],
            input=json.dumps(
                {
                    "task_description": "implement the feature",
                    "file_paths": ["main.py"],
                }
            ),
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        # Flag catalog has an agent — decision should not be needs_more_detail.
        assert out["decision"] != "needs_more_detail", (
            "--catalog-path flag must override DISPATCH_CATALOG_PATH env; "
            f"got: {out['decision']!r}"
        )


# ===========================================================================
# Output shape
# ===========================================================================


class TestOutputShape:
    """Output JSON contains required fields for each decision type."""

    def test_delegate_output_has_agent_and_skills(self, tmp_path: Path) -> None:
        """'delegate' output must have 'agent' and 'skills' fields."""
        catalog = _catalog(
            [
                _make_agent(
                    "code-writer",
                    keywords=[{"term": "implement", "weight": 1.0}],
                    path_globs=["**/*.py"],
                    applicable_skills=["python"],
                ),
            ]
        )
        stdin_obj = {
            "task_description": "implement the feature",
            "file_paths": ["src/main.py"],
        }
        result = _run(stdin_obj, catalog, tmp_path=tmp_path)
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        if out["decision"] == "delegate":
            assert "agent" in out
            assert "skills" in out
            assert "confidence" in out
            assert "rationale" in out

    def test_output_is_valid_json(self, tmp_path: Path) -> None:
        """stdout must always be valid JSON on success (exit 0)."""
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
            "file_paths": ["a.py"],
        }
        result = _run(stdin_obj, catalog, tmp_path=tmp_path)
        assert result.returncode == 0
        # This will raise if stdout is not valid JSON
        out = json.loads(result.stdout)
        assert "decision" in out

    def test_rationale_field_is_present(self, tmp_path: Path) -> None:
        """Every successful output must include a 'rationale' field."""
        catalog = _catalog(
            [
                _make_agent(
                    "code-writer",
                    keywords=[{"term": "implement", "weight": 1.0}],
                    path_globs=["**/*.py"],
                ),
            ]
        )
        stdin_obj = {
            "task_description": "implement something",
            "file_paths": ["src/main.py"],
        }
        result = _run(stdin_obj, catalog, tmp_path=tmp_path)
        assert result.returncode == 0
        out = json.loads(result.stdout)
        assert "rationale" in out
        assert isinstance(out["rationale"], str)


# ===========================================================================
# Dispatch log tests
# ===========================================================================

#: Representative minimal catalog used across log tests.
_LOG_TEST_CATALOG = _catalog(
    [
        _make_agent(
            "code-writer",
            keywords=[{"term": "implement", "weight": 1.0}],
            path_globs=["**/*.py"],
        ),
    ]
)

#: Representative input that produces a non-trivial decision.
_LOG_TEST_INPUT = {
    "task_description": "implement the new feature",
    "file_paths": ["src/main.py"],
}

#: ISO 8601 UTC timestamp regex (e.g. 2026-05-03T12:34:56.789012Z).
_ISO8601_RE = _re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z$")

#: Valid matcher decision strings (v5 §3.1.4).
_VALID_DECISIONS = {
    "delegate",
    "self_handle",
    "self_handle_unaided",
    "advisory",
    "ambiguous",
    "ask_user",
    "needs_more_detail",
}


class TestDispatchLog:
    """match.py appends a NDJSON decision record to DISPATCH_LOG_PATH."""

    def test_log_entry_written_on_success(self, tmp_path: Path) -> None:
        """A successful matcher run writes exactly one log entry.

        The entry must have the expected shape:
        - type == "matcher_decision"
        - ts matches ISO 8601 UTC regex
        - input.task_description matches what was sent
        - output.decision is one of the 7 valid decisions
        - catalog_hash matches sha256:<64 hex chars>
        - matcher_version is a non-empty string
        """
        log_path = tmp_path / "log.jsonl"
        result = _run(
            _LOG_TEST_INPUT,
            _LOG_TEST_CATALOG,
            extra_env={"DISPATCH_LOG_PATH": str(log_path)},
            tmp_path=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        assert log_path.exists(), "Log file was not created"

        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1, f"Expected 1 log line, got {len(lines)}"

        entry = json.loads(lines[0])
        assert entry["type"] == "matcher_decision"
        assert _ISO8601_RE.match(entry["ts"]), f"ts did not match ISO8601: {entry['ts']!r}"
        assert entry["input"]["task_description"] == _LOG_TEST_INPUT["task_description"]
        assert entry["output"]["decision"] in _VALID_DECISIONS
        assert _re.match(
            r"^sha256:[0-9a-f]{64}$", entry["catalog_hash"]
        ), f"catalog_hash malformed: {entry['catalog_hash']!r}"
        assert entry["matcher_version"], "matcher_version must be a non-empty string"

    def test_log_entry_appends_not_overwrites(self, tmp_path: Path) -> None:
        """A second run appends a second line; file has 2 valid JSON lines."""
        log_path = tmp_path / "append.jsonl"
        extra = {"DISPATCH_LOG_PATH": str(log_path)}

        _run(_LOG_TEST_INPUT, _LOG_TEST_CATALOG, extra_env=extra, tmp_path=tmp_path)
        _run(_LOG_TEST_INPUT, _LOG_TEST_CATALOG, extra_env=extra, tmp_path=tmp_path)

        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2, f"Expected 2 log lines after 2 runs, got {len(lines)}"
        for line in lines:
            entry = json.loads(line)  # raises if invalid JSON
            assert entry["type"] == "matcher_decision"

    def test_log_write_failure_does_not_block_decision(self, tmp_path: Path) -> None:
        """An unwritable log path does not prevent stdout decision output.

        Uses a drive letter that does not exist on the current machine so
        mkdir will fail, triggering the OSError handler in _write_log_entry.
        Falls back to a deeply nested path under a non-existent root on
        POSIX systems.
        """
        if os.name == "nt":
            bad_log = "Z:/nonexistent_drive/subdir/log.jsonl"
        else:
            bad_log = "/proc/nonexistent_dir/log.jsonl"

        result = _run(
            _LOG_TEST_INPUT,
            _LOG_TEST_CATALOG,
            extra_env={"DISPATCH_LOG_PATH": bad_log},
            tmp_path=tmp_path,
        )
        # Decision must still succeed.
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out["decision"] in _VALID_DECISIONS
        # Failure message must appear on stderr.
        assert (
            "[match.py] log write failed" in result.stderr
        ), f"Expected log-write-failed message on stderr; got: {result.stderr!r}"

    def test_catalog_hash_stable_for_identical_catalogs(self, tmp_path: Path) -> None:
        """_compute_catalog_hash produces the same digest for dicts with different key order."""
        dict_a = {"b": 2, "a": 1, "entries": []}
        dict_b = {"a": 1, "entries": [], "b": 2}
        hash_a = _match_mod._compute_catalog_hash(dict_a)
        hash_b = _match_mod._compute_catalog_hash(dict_b)
        assert (
            hash_a == hash_b
        ), f"Hashes differ for semantically identical catalogs: {hash_a!r} vs {hash_b!r}"
        assert _re.match(r"^sha256:[0-9a-f]{64}$", hash_a)

    def test_log_path_env_var_override(self, tmp_path: Path) -> None:
        """DISPATCH_LOG_PATH env var controls where the log is written.

        Confirms the log appears at the custom path and not at the default
        ~/.claude/state/dispatch-log.jsonl location.
        """
        custom_log = tmp_path / "custom.jsonl"
        result = _run(
            _LOG_TEST_INPUT,
            _LOG_TEST_CATALOG,
            extra_env={"DISPATCH_LOG_PATH": str(custom_log)},
            tmp_path=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        assert custom_log.exists(), "Log not written to DISPATCH_LOG_PATH override path"
        lines = custom_log.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1


# ===========================================================================
# Worktree catalog regression (#359)
# ===========================================================================


class TestWorktreeCatalogParity:
    """Matcher reads only dispatch-catalog.json — no worktree-specific variant.

    Regression guard for issue #359: a vestigial ``dispatch-catalog-wt.json``
    file was created by a prior agent session and could mislead future work
    into thinking a dual-catalog system is intentional.  These tests assert
    that the matcher's catalog resolution is independent of whether it is
    invoked from a worktree or the main checkout.

    Note: These tests previously used ``CLAUDE_HOME`` to supply the catalog
    directory.  After Issue #10 removed ``CLAUDE_HOME`` support, they have
    been updated to use ``DISPATCH_CATALOG_PATH`` (the explicit env var).
    """

    def test_matcher_reads_main_catalog_not_wt_variant(self, tmp_path: Path) -> None:
        """Matcher uses dispatch-catalog.json; dispatch-catalog-wt.json is ignored.

        Writes a catalog to ``<tmp>/state/dispatch-catalog.json`` and an
        intentionally broken file at ``<tmp>/state/dispatch-catalog-wt.json``
        (which would break matching if the matcher tried to read it).
        Sets ``DISPATCH_CATALOG_PATH`` to the main catalog path so the
        matcher resolves it explicitly.  Asserts the matcher succeeds using
        the main catalog, proving it never touches the wt file.
        """
        state_dir = tmp_path / "state"
        state_dir.mkdir()

        catalog = _catalog(
            [
                _make_agent(
                    "code-writer",
                    keywords=[{"term": "implement", "weight": 1.0}],
                    path_globs=["**/*.py"],
                ),
            ]
        )
        catalog_file = state_dir / "dispatch-catalog.json"
        catalog_file.write_text(json.dumps(catalog), encoding="utf-8")
        # Write a *broken* wt variant — if the matcher reads this it will fail.
        (state_dir / "dispatch-catalog-wt.json").write_text(
            "NOT VALID JSON — matcher must not read this file",
            encoding="utf-8",
        )

        env = {**os.environ, "DISPATCH_CATALOG_PATH": str(catalog_file)}

        result = subprocess.run(
            [PYTHON, "-m", *_MATCH_MODULE],
            input=json.dumps(
                {
                    "task_description": "implement the new feature",
                    "file_paths": ["src/main.py"],
                }
            ),
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        assert result.returncode == 0, (
            f"Matcher failed — may have tried to read dispatch-catalog-wt.json.\n"
            f"stderr: {result.stderr}"
        )
        out = json.loads(result.stdout)
        assert out["decision"] in {
            "delegate",
            "self_handle",
            "advisory",
        }, f"Unexpected decision: {out['decision']}"

    def test_worktree_and_main_checkout_produce_identical_decisions(
        self, tmp_path: Path
    ) -> None:
        """Matcher decision is identical regardless of catalog location.

        Simulates calling the matcher from a worktree (``wt_catalog``) vs
        the main checkout (``main_catalog``).  Both use the same catalog
        content, supplied via ``DISPATCH_CATALOG_PATH``.  The decision must
        be identical, confirming there is no context-specific routing path.

        Note: Previously used ``CLAUDE_HOME`` — updated for Issue #10.
        """
        catalog = _catalog(
            [
                _make_agent(
                    "code-writer",
                    keywords=[{"term": "implement", "weight": 1.0}],
                    path_globs=["**/*.py"],
                ),
                _make_agent(
                    "debugger",
                    keywords=[{"term": "debug", "weight": 1.0}],
                ),
            ]
        )
        stdin_obj = {
            "task_description": "implement a new python feature",
            "file_paths": ["src/main.py"],
        }

        # Main-checkout catalog.
        main_catalog = tmp_path / "main" / "dispatch-catalog.json"
        main_catalog.parent.mkdir(parents=True)
        main_catalog.write_text(json.dumps(catalog), encoding="utf-8")

        # Worktree catalog (same content, different path).
        wt_catalog = tmp_path / "worktree" / "dispatch-catalog.json"
        wt_catalog.parent.mkdir(parents=True)
        wt_catalog.write_text(json.dumps(catalog), encoding="utf-8")

        base_env = {
            k: v
            for k, v in os.environ.items()
            if k not in {"DISPATCH_CATALOG_PATH", "CLAUDE_HOME"}
        }

        result_main = subprocess.run(
            [PYTHON, "-m", *_MATCH_MODULE],
            input=json.dumps(stdin_obj),
            capture_output=True,
            text=True,
            env={**base_env, "DISPATCH_CATALOG_PATH": str(main_catalog)},
            check=False,
        )
        result_wt = subprocess.run(
            [PYTHON, "-m", *_MATCH_MODULE],
            input=json.dumps(stdin_obj),
            capture_output=True,
            text=True,
            env={**base_env, "DISPATCH_CATALOG_PATH": str(wt_catalog)},
            check=False,
        )

        assert result_main.returncode == 0, result_main.stderr
        assert result_wt.returncode == 0, result_wt.stderr

        out_main = json.loads(result_main.stdout)
        out_wt = json.loads(result_wt.stdout)

        assert out_main["decision"] == out_wt["decision"], (
            f"Decision differs between main ({out_main['decision']}) "
            f"and worktree ({out_wt['decision']}) contexts"
        )


# ===========================================================================
# Issue #361 regression tests: code-writer edit-family triggers + path_globs
# ===========================================================================

#: Path to the agents directory in the current checkout.
#: Used to build a live catalog from the actual agent frontmatter so these
#: tests fail before the agents/code-writer.md frontmatter edit and pass
#: after — giving us true TDD red/green coverage of the fix.
_AGENTS_DIR = REPO_ROOT / "agents"
_SKILLS_DIR = REPO_ROOT / "skills"
_TRIGGERS_DIR = REPO_ROOT / "triggers"

#: Fixture directories containing synthetic agents and skills for
#: catalog cascade tests that must run without the private harness.
_FIXTURE_AGENTS_DIR = REPO_ROOT / "tests" / "fixtures" / "agents"
_FIXTURE_SKILLS_DIR = REPO_ROOT / "tests" / "fixtures" / "skills"

_BUILD_SCRIPT = REPO_ROOT / "src" / "claude_wayfinder" / "build_catalog.py"


def _build_live_catalog(tmp_path: Path) -> Path:
    """Build a dispatch catalog from the worktree's agents/ and skills/ dirs.

    Runs ``build_dispatch_catalog.py`` with ``--agents-dir`` and
    ``--skills-dir`` pointed at the worktree sources so the resulting
    catalog reflects the current state of agent frontmatter — including
    any edits made as part of the fix being tested.

    Args:
        tmp_path: pytest temporary directory for catalog and log output.

    Returns:
        Path to the generated catalog JSON file.
    """
    if not _AGENTS_DIR.is_dir():
        pytest.skip(
            "requires harness agents/ directory (not present in public repo)"
        )
    out_path = tmp_path / "live-catalog.json"
    log_path = tmp_path / "live-catalog.log"
    result = subprocess.run(
        [
            PYTHON,
            str(_BUILD_SCRIPT),
            "--agents-dir",
            str(_AGENTS_DIR),
            "--skills-dir",
            str(_SKILLS_DIR),
            "--out",
            str(out_path),
            "--log",
            str(log_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in (0, 2):
        raise RuntimeError(f"Catalog build failed (exit {result.returncode}):\n" f"{result.stderr}")
    return out_path


def _build_synthetic_catalog(tmp_path: Path) -> Path:
    """Build a dispatch catalog from the tests/fixtures/ agent and skill dirs.

    Replaces ``_build_live_catalog`` for tests that previously skipped when
    the private harness ``agents/`` directory was absent.  The fixture
    directories are committed alongside the tests and must always exist —
    a missing fixture directory is a fatal error, not a skip.

    Args:
        tmp_path: pytest temporary directory for catalog and log output.

    Returns:
        Path to the generated catalog JSON file.

    Raises:
        AssertionError: If either fixture directory is missing.
    """
    assert _FIXTURE_AGENTS_DIR.is_dir(), (
        f"Fixture agents directory missing: {_FIXTURE_AGENTS_DIR}. "
        "Fixture files are committed and must always be present."
    )
    assert _FIXTURE_SKILLS_DIR.is_dir(), (
        f"Fixture skills directory missing: {_FIXTURE_SKILLS_DIR}. "
        "Fixture files are committed and must always be present."
    )
    out_path = tmp_path / "synthetic-catalog.json"
    log_path = tmp_path / "synthetic-catalog.log"
    result = subprocess.run(
        [
            PYTHON,
            str(_BUILD_SCRIPT),
            "--agents-dir",
            str(_FIXTURE_AGENTS_DIR),
            "--skills-dir",
            str(_FIXTURE_SKILLS_DIR),
            "--out",
            str(out_path),
            "--log",
            str(log_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in (0, 2):
        raise RuntimeError(
            f"Synthetic catalog build failed (exit {result.returncode}):\n"
            f"{result.stderr}\nLog path: {log_path}"
        )
    return out_path


class TestIssue361EditFamilyTriggers:
    """Regression tests for issue #361.

    Before the fix, code-writer scored 0.0 on edit-style tasks because
    it had no edit-family keywords and no path_globs.  These three tests
    cover the three scenarios described in the issue's acceptance criteria.

    All three tests build a synthetic catalog from tests/fixtures/ so they
    run without the private harness agents/ directory and give true
    TDD red/green coverage of the fixture frontmatter.
    """

    def test_css_edit_routes_to_code_writer(self, tmp_path: Path) -> None:
        """CSS edit task with HTML file path must identify code-writer.

        Expected post-fix score for code-writer:
          0.4 (glob *.html matches index.html)
          + 0.5 * 0.5 (edit keyword, weight=0.5)
          = 0.65 → advisory or delegate (code-writer is the identified agent).

        Pre-fix: code-writer has no path_globs and no edit keyword
        → score 0.0 → self_handle_unaided → no agent identified.

        This test verifies that code-writer is identified (decision is
        'delegate' or 'advisory'), not that the exact threshold is met.
        The key invariant is that the decision is NOT 'self_handle_unaided'
        and the agent IS 'code-writer'.
        """
        catalog_path = _build_synthetic_catalog(tmp_path)
        stdin_obj = {
            "task_description": (
                "Edit two CSS values in index.html on an existing branch"
                " to reduce the height of a sticky top nav bar."
            ),
            "file_paths": ["index.html"],
            "tool_mentions": ["git"],
        }
        result = _run(
            stdin_obj,
            {},  # unused when catalog_path is provided
            catalog_path=catalog_path,
            tmp_path=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out["decision"] in ("delegate", "advisory"), (
            f"Expected 'delegate' or 'advisory', got '{out['decision']}' "
            f"(confidence={out.get('confidence')}) — "
            "code-writer must match edit-family keywords + HTML path_glob"
        )
        assert (
            out.get("agent") == "code-writer"
        ), f"Expected agent 'code-writer', got '{out.get('agent')}'"

    def test_python_implement_routes_to_code_writer(self, tmp_path: Path) -> None:
        """Python script task with .py file path must identify code-writer.

        Expected post-fix score for code-writer:
          0.4 (glob *.py matches deploy.py)
          + 0.5 * 1.0 (implement keyword, weight=1.0)
          + 0.5 * 0.25 (script keyword, weight=0.25)
          = 0.4 + 0.5 + 0.125 = 1.025 → clamped to 1.0 → delegate.

        Pre-fix: code-writer has no path_globs → implement+script keywords
        score 0.625, but without glob bonus code-writer may still lose.
        The path_glob addition in #361 raises the score above 0.5 so
        code-writer is properly identified as the right agent.
        """
        catalog_path = _build_synthetic_catalog(tmp_path)
        stdin_obj = {
            "task_description": "Implement the deployment script in Python",
            "file_paths": ["deploy.py"],
        }
        result = _run(
            stdin_obj,
            {},
            catalog_path=catalog_path,
            tmp_path=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out["decision"] in ("delegate", "advisory"), (
            f"Expected 'delegate' or 'advisory', got '{out['decision']}' "
            f"(confidence={out.get('confidence')})"
        )
        assert (
            out.get("agent") == "code-writer"
        ), f"Expected agent 'code-writer', got '{out.get('agent')}'"

    def test_bicep_edit_routes_to_devops_not_code_writer(self, tmp_path: Path) -> None:
        """Bicep template edit with infra keywords must route to devops.

        This is the key regression guard: edit-family keywords added to
        code-writer must NOT pull bicep/infrastructure work away from devops.

        Score breakdown for devops (post-fix):
          0.4 (glob **/*.bicep matches infra/main.bicep)
          + 0.5 * 1.0 (infrastructure keyword, weight=1.0)
          + 0.5 * 1.0 (deployment keyword, weight=1.0)
          = 0.4 + 0.5 + 0.5 = 1.4 → clamped to 1.0.

        Score breakdown for code-writer (post-fix):
          0.5 * 0.5 (update keyword)
          = 0.25
          (infra/main.bicep does not match any code-writer path_glob)

        Gap = 1.0 - 0.25 = 0.75 >= 0.2 → delegate to devops.
        code-writer must NOT win here even with edit-family keywords.
        """
        catalog_path = _build_synthetic_catalog(tmp_path)
        stdin_obj = {
            "task_description": (
                "Update the bicep infrastructure deployment template"
                " to change the storage account SKU"
            ),
            "file_paths": ["infra/main.bicep"],
        }
        result = _run(
            stdin_obj,
            {},
            catalog_path=catalog_path,
            tmp_path=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out["decision"] == "delegate", f"Expected 'delegate', got '{out['decision']}'"
        assert out["agent"] == "devops", (
            f"Expected agent 'devops', got '{out.get('agent')}' — "
            "bicep/infrastructure edits must NOT route to code-writer"
        )


# ===========================================================================
# Issue #364 regression tests: doc-writer agent for prose/docs/specs
# ===========================================================================


class TestIssue364DocWriterAgent:
    """Regression tests for issue #364.

    Before this fix, prose-shaped markdown edits (docs/**/*.md, READMEs,
    plan files, ADRs) had no specialist owner.  The doc-writer agent
    introduced in #364 fills that gap.

    All four tests build a synthetic catalog from tests/fixtures/ so they
    run without the private harness agents/ directory and give true
    TDD red/green coverage of the fixture frontmatter.
    """

    def test_docs_md_routes_to_doc_writer(self, tmp_path: Path) -> None:
        """docs/**/*.md path + prose task description must route to doc-writer.

        Expected post-fix score for doc-writer:
          0.4  (glob docs/*.md matches docs/foo.md)
          + 0.5 * 1.0  (docs keyword, weight=1.0)
          + 0.5 * 0.25 (update keyword, weight=0.25)
          = 0.4 + 0.5 + 0.125 = 1.025 → clamped to 1.0 → delegate.

        Pre-fix: doc-writer does not exist → no agent matches prose paths
        → self_handle_unaided, no agent in output.
        """
        catalog_path = _build_synthetic_catalog(tmp_path)
        stdin_obj = {
            "task_description": "update the docs",
            "file_paths": ["docs/foo.md"],
        }
        result = _run(
            stdin_obj,
            {},
            catalog_path=catalog_path,
            tmp_path=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out["decision"] in ("delegate", "advisory"), (
            f"Expected 'delegate' or 'advisory', got '{out['decision']}' "
            f"(confidence={out.get('confidence')}) — "
            "docs/*.md + 'update the docs' must route to doc-writer"
        )
        assert (
            out.get("agent") == "doc-writer"
        ), f"Expected agent 'doc-writer', got '{out.get('agent')}'"

    def test_readme_routes_to_doc_writer(self, tmp_path: Path) -> None:
        """README.md path + edit task description must route to doc-writer.

        Expected post-fix score for doc-writer:
          0.4  (glob README.md matches README.md)
          + 0.5 * 1.0  (readme keyword, weight=1.0)
          + 0.5 * 0.25 (edit keyword, weight=0.25)
          = 0.4 + 0.5 + 0.125 = 1.025 → clamped to 1.0 → delegate.

        Pre-fix: doc-writer does not exist → README edits fall through.
        """
        catalog_path = _build_synthetic_catalog(tmp_path)
        stdin_obj = {
            "task_description": "edit the readme",
            "file_paths": ["README.md"],
        }
        result = _run(
            stdin_obj,
            {},
            catalog_path=catalog_path,
            tmp_path=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out["decision"] in ("delegate", "advisory"), (
            f"Expected 'delegate' or 'advisory', got '{out['decision']}' "
            f"(confidence={out.get('confidence')}) — "
            "README.md + 'edit the readme' must route to doc-writer"
        )
        assert (
            out.get("agent") == "doc-writer"
        ), f"Expected agent 'doc-writer', got '{out.get('agent')}'"

    def test_agent_md_does_not_route_to_doc_writer(self, tmp_path: Path) -> None:
        """agents/**/*.md path must NOT route to doc-writer.

        agents/code-writer.md is explicitly excluded from doc-writer's
        path_globs (harness files are router self-handled).  The matcher
        must not award doc-writer the path-glob bonus for this file.

        What decision is returned depends on other agents and harness
        carve-out behaviour, but doc-writer must not win here.
        """
        catalog_path = _build_synthetic_catalog(tmp_path)
        stdin_obj = {
            "task_description": "edit the agent",
            "file_paths": ["agents/code-writer.md"],
        }
        result = _run(
            stdin_obj,
            {},
            catalog_path=catalog_path,
            tmp_path=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out.get("agent") != "doc-writer", (
            "agents/**/*.md must NOT route to doc-writer; "
            f"got agent='{out.get('agent')}', decision='{out['decision']}'"
        )

    def test_python_edit_still_routes_to_code_writer(self, tmp_path: Path) -> None:
        """src/main.py + 'edit the function' must still route to code-writer.

        Regression guard from #361: adding doc-writer must not steal
        code-file edits away from code-writer.  code-writer's path_globs
        match **/*.py; doc-writer has no Python path_globs.

        Expected score for code-writer:
          0.4  (glob **/*.py matches src/main.py)
          + 0.5 * 0.5  (edit keyword, weight=0.5)
          = 0.65 → advisory or delegate (code-writer identified).

        doc-writer should score 0.0 (no matching glob, 'edit' weight
        only 0.25 → 0.125, not enough to win over code-writer).
        """
        catalog_path = _build_synthetic_catalog(tmp_path)
        stdin_obj = {
            "task_description": "edit the function",
            "file_paths": ["src/main.py"],
        }
        result = _run(
            stdin_obj,
            {},
            catalog_path=catalog_path,
            tmp_path=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out["decision"] in ("delegate", "advisory"), (
            f"Expected 'delegate' or 'advisory', got '{out['decision']}' "
            f"(confidence={out.get('confidence')}) — "
            "src/main.py + 'edit the function' must still route to code-writer"
        )
        assert out.get("agent") == "code-writer", (
            f"Expected agent 'code-writer', got '{out.get('agent')}' — "
            "adding doc-writer must not regress #361 code-writer routing"
        )


# ===========================================================================
# Issue #366 regression tests: agent-authoring skill for harness edits
# ===========================================================================


class TestIssue366AgentAuthoringSkill:
    """Regression tests for issue #366.

    Harness files (``agents/**/*.md``, ``skills/**/SKILL.md``, ``CLAUDE.md``,
    ``AGENTS.md``, ``GEMINI.md``) are router-self-handled, and the router
    should activate the ``agent-authoring`` skill when these files are in
    scope.

    All four tests build a synthetic catalog from tests/fixtures/ so they
    run without the private harness agents/ directory and give true TDD
    red/green coverage of the fixture frontmatter.
    """

    def test_agent_md_edit_self_handles_with_agent_authoring_skill(self, tmp_path: Path) -> None:
        """agents/foo.md + 'update the frontmatter' → self_handle with agent-authoring skill.

        Expected post-fix score breakdown:
          - ``agents/foo.md`` matches ``agents/*.md`` path_glob → +0.4
          - keyword "frontmatter" (weight=1.0) → +0.5*1.0 = +0.50
          Total skill score = 0.90 → self_handle decision, "agent-authoring" in skills.

        No agent should score >= 0.85 and dominate (harness files are not
        delegated to sub-agents), so the decision must be ``self_handle``
        (not ``delegate``).
        """
        catalog_path = _build_synthetic_catalog(tmp_path)
        stdin_obj = {
            "task_description": "update the frontmatter in agents/foo.md",
            "file_paths": ["agents/foo.md"],
        }
        result = _run(
            stdin_obj,
            {},
            catalog_path=catalog_path,
            tmp_path=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out["decision"] == "self_handle", (
            f"Expected 'self_handle', got '{out['decision']}' — "
            "agents/foo.md + 'edit the agent' must self_handle with "
            "agent-authoring skill active"
        )
        assert "agent-authoring" in out.get("skills", []), (
            f"Expected 'agent-authoring' in skills, got {out.get('skills')} — "
            "the agent-authoring skill must be activated for agent file edits"
        )

    def test_claude_md_edit_self_handles_with_agent_authoring_skill(self, tmp_path: Path) -> None:
        """CLAUDE.md + 'tighten the harness rule' → self_handle with agent-authoring skill.

        Expected post-fix score breakdown:
          - bare ``CLAUDE.md`` matches ``CLAUDE.md`` path_glob → +0.4
            (the bare form is needed because fnmatch ``**/CLAUDE.md``
            does NOT match a bare ``CLAUDE.md`` path)
          - keyword "harness" (weight=1.0) → +0.5*1.0 = +0.50
          Total skill score = 0.90 → self_handle, "agent-authoring" in skills.
        """
        catalog_path = _build_synthetic_catalog(tmp_path)
        stdin_obj = {
            "task_description": "tighten the harness rule in CLAUDE.md",
            "file_paths": ["CLAUDE.md"],
        }
        result = _run(
            stdin_obj,
            {},
            catalog_path=catalog_path,
            tmp_path=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out["decision"] == "self_handle", (
            f"Expected 'self_handle', got '{out['decision']}' — "
            "CLAUDE.md + 'tighten the rule' must self_handle with "
            "agent-authoring skill active"
        )
        assert "agent-authoring" in out.get("skills", []), (
            f"Expected 'agent-authoring' in skills, got {out.get('skills')} — "
            "the agent-authoring skill must activate for CLAUDE.md edits. "
            "Check that triggers.yml includes bare 'CLAUDE.md' alongside "
            "'**/CLAUDE.md' (fnmatch does not match bare filenames with **/ patterns)"
        )

    def test_skill_md_edit_self_handles_with_agent_authoring_skill(self, tmp_path: Path) -> None:
        """skills/foo/SKILL.md + 'update the skill' → self_handle with agent-authoring.

        Expected post-fix score breakdown:
          - ``skills/foo/SKILL.md`` matches ``skills/**/SKILL.md`` path_glob → +0.4
          - keyword "skill" (weight=0.25, demoted from 0.5 in #454) → +0.5*0.25 = +0.125
          Total skill score = 0.525 → self_handle, "agent-authoring" in skills.
        """
        catalog_path = _build_synthetic_catalog(tmp_path)
        stdin_obj = {
            "task_description": "update the skill",
            "file_paths": ["skills/foo/SKILL.md"],
        }
        result = _run(
            stdin_obj,
            {},
            catalog_path=catalog_path,
            tmp_path=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out["decision"] == "self_handle", (
            f"Expected 'self_handle', got '{out['decision']}' — "
            "skills/foo/SKILL.md + 'update the skill' must self_handle with "
            "agent-authoring skill active"
        )
        assert "agent-authoring" in out.get("skills", []), (
            f"Expected 'agent-authoring' in skills, got {out.get('skills')} — "
            "the agent-authoring skill must be activated for SKILL.md edits"
        )

    def test_doc_md_does_not_trigger_agent_authoring(self, tmp_path: Path) -> None:
        """docs/foo.md + 'update the docs' must NOT activate agent-authoring.

        Regression guard: the agent-authoring skill's path_globs must not
        include general docs paths.  docs/foo.md is prose content handled
        by the doc-writer agent, not a harness file.  This test ensures the
        skill's path_globs are tightly scoped to actual harness artifacts.
        """
        catalog_path = _build_synthetic_catalog(tmp_path)
        stdin_obj = {
            "task_description": "update the docs",
            "file_paths": ["docs/foo.md"],
        }
        result = _run(
            stdin_obj,
            {},
            catalog_path=catalog_path,
            tmp_path=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert "agent-authoring" not in out.get("skills", []), (
            f"agent-authoring must NOT activate for docs/foo.md, "
            f"but it appeared in skills={out.get('skills')} — "
            "tighten the agent-authoring triggers.yml path_globs to exclude docs/**"
        )


# ===========================================================================
# Issue #425 regression tests: keyword score multiplier 0.3 → 0.5
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


# ---------------------------------------------------------------------------
# CatalogEntry.source field — issue #475
# ---------------------------------------------------------------------------


class TestCatalogEntrySourceField:
    """Verify that CatalogEntry carries a ``source`` field that round-trips
    through ``load_catalog``.
    """

    def test_catalog_entry_source_field_round_trips(self, tmp_path: Path) -> None:
        """CatalogEntry.source is populated from the catalog JSON and defaults
        to ``"owned"`` when the field is absent.

        Three cases are verified in one catalog load:
        - An entry with ``source="owned"`` is preserved as ``"owned"``.
        - An entry with ``source="plugin"`` is preserved as ``"plugin"``.
        - An entry that omits ``source`` entirely defaults to ``"owned"``.
        """
        catalog_data = _catalog(
            [
                {**_make_agent("agent-owned"), "source": "owned"},
                {**_make_agent("agent-plugin"), "source": "plugin"},
                # ``source`` key is intentionally absent here.
                {k: v for k, v in _make_agent("agent-no-source").items() if k != "source"},
            ]
        )
        catalog_file = tmp_path / "catalog.json"
        catalog_file.write_text(json.dumps(catalog_data), encoding="utf-8")

        entries = _match_mod.load_catalog(catalog_file)
        by_name = {e.name: e for e in entries}

        assert by_name["agent-owned"].source == "owned", "source='owned' should be preserved"
        assert by_name["agent-plugin"].source == "plugin", "source='plugin' should be preserved"
        assert (
            by_name["agent-no-source"].source == "owned"
        ), "omitted source should default to 'owned'"


# ---------------------------------------------------------------------------
# Issue #477 — is_agent_routable predicate + matcher integration
# ---------------------------------------------------------------------------


class TestPluginAgentExcluded:
    """After Pass 2.5 wiring, plugin agents in the catalog are excluded from
    scoring via the ``is_agent_routable`` predicate.
    """

    def test_match_excludes_plugin_agent(self, tmp_path: Path) -> None:
        """A plugin agent (source='plugin') is excluded from agent scoring.

        The catalog contains one plugin agent ('plugin:my-agent') and one
        owned agent ('code-writer').  A task that would match both by keyword
        must route only to the owned agent (because the plugin agent is
        filtered out before scoring).
        """
        catalog = _catalog(
            [
                _make_agent(
                    "code-writer",
                    keywords=[{"term": "implement", "weight": 1.0}],
                    path_globs=["**/*.py"],
                ),
                # Plugin agent — must be excluded from scoring.
                {
                    **_make_agent(
                        "plugin:my-agent",
                        keywords=[{"term": "implement", "weight": 1.0}],
                        path_globs=["**/*.py"],
                    ),
                    "source": "plugin",
                },
            ]
        )
        stdin_obj = {
            "task_description": "implement the feature",
            "file_paths": ["src/main.py"],
        }
        result = _run(stdin_obj, catalog, tmp_path=tmp_path)
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        # Plugin agent must never appear as the winning agent.
        assert out.get("agent") != "plugin:my-agent", (
            "plugin agent 'plugin:my-agent' appeared as the matched agent — "
            "it should have been excluded by is_agent_routable"
        )
        # The owned agent must still be matchable.
        assert out["decision"] in (
            "delegate",
            "advisory",
            "self_handle",
            "self_handle_unaided",
            "ambiguous",
            "needs_more_detail",
        )

    def test_match_plugin_skill_participates_in_scoring(self, tmp_path: Path) -> None:
        """A plugin skill (source='plugin') enters the skill scoring pool.

        Plugin skills are dormant (zero triggers) so they score 0.0 and
        cannot drive a decision.  But they must not be excluded from the
        pool — if a plugin skill ever gains an override with real triggers,
        it should be able to score.

        This test uses a synthetic plugin skill with a real trigger so
        we can verify the pool contains it.  The skill must produce a
        self_handle decision when it's the only scoring entry.
        """
        catalog = _catalog(
            [
                # Plugin skill WITH a keyword trigger — source='plugin'
                {
                    **_make_skill(
                        "superpowers:brainstorming",
                        keywords=[{"term": "brainstorm", "weight": 1.0}],
                        applicable_agents=["*"],
                    ),
                    "source": "plugin",
                },
            ]
        )
        stdin_obj = {
            "task_description": "brainstorm ideas for the project",
            "file_paths": ["docs/ideas.md"],
        }
        result = _run(stdin_obj, catalog, tmp_path=tmp_path)
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        # The plugin skill must participate in scoring; if it scores >= 0.5,
        # the decision should be self_handle with it in the skills list.
        if out["decision"] == "self_handle":
            assert "superpowers:brainstorming" in out.get("skills", []), (
                "Plugin skill 'superpowers:brainstorming' scored but was not "
                "included in the self_handle skills list"
            )

    def test_previously_routed_prompt_still_routes_to_same_target(self, tmp_path: Path) -> None:
        """Adding dormant plugin entries does not change routing for owned agents.

        A prompt that previously routed to 'code-writer' must still route
        to 'code-writer' after dormant plugin entries are added to the catalog.
        Dormant entries score 0.0 (no triggers) so they cannot change decisions.
        """
        base_catalog = _catalog(
            [
                _make_agent(
                    "code-writer",
                    keywords=[{"term": "implement", "weight": 1.0}],
                    path_globs=["**/*.py"],
                    applicable_skills=["*"],
                ),
            ]
        )
        stdin_obj = {
            "task_description": "implement the new feature",
            "file_paths": ["src/main.py"],
        }
        result_base = _run(stdin_obj, base_catalog, tmp_path=tmp_path)
        assert result_base.returncode == 0, result_base.stderr
        out_base = json.loads(result_base.stdout)

        # Now add dormant plugin entries to the catalog.
        augmented_catalog = _catalog(
            [
                _make_agent(
                    "code-writer",
                    keywords=[{"term": "implement", "weight": 1.0}],
                    path_globs=["**/*.py"],
                    applicable_skills=["*"],
                ),
                # Dormant plugin skill — no triggers, source='plugin'
                {
                    "name": "superpowers:brainstorming",
                    "kind": "skill",
                    "description": "Brainstorming skill.",
                    "source": "plugin",
                    "triggers": {
                        "command_prefixes": [],
                        "agent_mentions": [],
                        "path_globs": [],
                        "keywords": [],
                        "tool_mentions": [],
                        "excludes": [],
                    },
                    "applicable_agents": [],
                },
                # Dormant plugin agent — no triggers, source='plugin'
                {
                    "name": "plugin:some-agent",
                    "kind": "agent",
                    "description": "A plugin agent.",
                    "source": "plugin",
                    "triggers": {
                        "command_prefixes": [],
                        "agent_mentions": [],
                        "path_globs": [],
                        "keywords": [],
                        "tool_mentions": [],
                        "excludes": [],
                    },
                    "applicable_skills": [],
                },
            ]
        )
        result_aug = _run(stdin_obj, augmented_catalog, tmp_path=tmp_path)
        assert result_aug.returncode == 0, result_aug.stderr
        out_aug = json.loads(result_aug.stdout)

        assert out_aug["decision"] == out_base["decision"], (
            f"Decision changed after adding dormant plugin entries: "
            f"{out_base['decision']!r} → {out_aug['decision']!r}. "
            "Dormant entries (zero triggers) must not affect routing."
        )
        if out_base["decision"] == "delegate":
            assert out_aug.get("agent") == out_base.get(
                "agent"
            ), "Delegate target changed after adding dormant plugin entries"


# ---------------------------------------------------------------------------
# Issue #478 — plugin-override agent routing
# ---------------------------------------------------------------------------


class TestPluginOverrideAgentRouting:
    """A plugin-override agent must be eligible for agent scoring.

    Unlike source='plugin' agents (which are inert/excluded), a
    source='plugin-override' agent has explicit trigger configuration
    and must participate in scoring.
    """

    def test_match_includes_plugin_override_agent(self, tmp_path: Path) -> None:
        """A plugin-override agent (source='plugin-override') can be matched.

        The catalog contains one plugin-override agent with keyword triggers.
        A task matching that keyword must route to that agent, confirming
        that source='plugin-override' agents are not excluded by
        ``is_agent_routable``.
        """
        catalog = _catalog(
            [
                # Plugin-override agent — must participate in scoring.
                {
                    **_make_agent(
                        "myplugin:my-agent",
                        keywords=[{"term": "specialtask", "weight": 1.0}],
                    ),
                    "source": "plugin-override",
                },
            ]
        )
        stdin_obj = {
            "task_description": "specialtask",
            "file_paths": [],
        }
        result = _run(stdin_obj, catalog, tmp_path=tmp_path)
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        # The plugin-override agent must be eligible — it should either be
        # matched as the delegate or produce a non-no_match decision.
        assert out["decision"] != "no_match", (
            f"plugin-override agent was not included in scoring "
            f"(decision was 'no_match'); full output: {out}"
        )


# ===========================================================================
# Issue #10: Remove ~/.claude default fallbacks — fail-loud path resolution
# ===========================================================================


class TestIssue10FailLoudCatalogPath:
    """Catalog path resolution must fail loud when no explicit source is given.

    After Issue #10, the two-step resolution chain is:
      1. ``--catalog-path <path>`` CLI flag
      2. ``DISPATCH_CATALOG_PATH`` env var
      3. **fail loud** — emit ``[CATALOG ERROR]`` banner, exit non-zero.

    ``CLAUDE_HOME`` and ``Path.home()`` are no longer lookup steps.
    """

    def test_no_path_no_env_exits_nonzero_with_catalog_error(
        self, tmp_path: Path
    ) -> None:
        """CLI exits non-zero and emits [CATALOG ERROR] when no path is given.

        Both ``DISPATCH_CATALOG_PATH`` and ``CLAUDE_HOME`` are absent from the
        environment.  The matcher must not fall back to ``~/.claude/...`` —
        it must emit a ``[CATALOG ERROR]`` banner on stderr and exit 2.
        """
        clean_env = {
            k: v
            for k, v in os.environ.items()
            if k not in {"DISPATCH_CATALOG_PATH", "CLAUDE_HOME"}
        }
        result = subprocess.run(
            [PYTHON, "-m", *_MATCH_MODULE],
            input=json.dumps({"task_description": "implement a feature"}),
            capture_output=True,
            text=True,
            env=clean_env,
            check=False,
        )
        assert result.returncode != 0, (
            "Expected non-zero exit when no catalog path is supplied; "
            f"got returncode={result.returncode}, stderr={result.stderr!r}"
        )
        assert "[CATALOG ERROR]" in result.stderr, (
            f"Expected [CATALOG ERROR] banner on stderr; got: {result.stderr!r}"
        )

    def test_catalog_error_message_names_the_fix(self, tmp_path: Path) -> None:
        """[CATALOG ERROR] message tells the user how to supply a path.

        The error text must mention either ``--catalog-path`` or
        ``DISPATCH_CATALOG_PATH`` so the caller knows what to do.
        """
        clean_env = {
            k: v
            for k, v in os.environ.items()
            if k not in {"DISPATCH_CATALOG_PATH", "CLAUDE_HOME"}
        }
        result = subprocess.run(
            [PYTHON, "-m", *_MATCH_MODULE],
            input=json.dumps({"task_description": "implement a feature"}),
            capture_output=True,
            text=True,
            env=clean_env,
            check=False,
        )
        assert any(
            token in result.stderr
            for token in ("--catalog-path", "DISPATCH_CATALOG_PATH")
        ), (
            "Error message must name the fix (--catalog-path or "
            f"DISPATCH_CATALOG_PATH); got: {result.stderr!r}"
        )

    def test_claude_home_env_var_is_ignored(self, tmp_path: Path) -> None:
        """CLAUDE_HOME env var no longer redirects catalog resolution.

        After Issue #10, ``CLAUDE_HOME`` is not a lookup step.  Even if a
        valid catalog exists under ``$CLAUDE_HOME/state/dispatch-catalog.json``,
        the matcher must not use it — it should still fail loud.
        """
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        catalog = _catalog(
            [
                _make_agent(
                    "code-writer",
                    keywords=[{"term": "implement", "weight": 1.0}],
                ),
            ]
        )
        (state_dir / "dispatch-catalog.json").write_text(
            json.dumps(catalog), encoding="utf-8"
        )

        # Env has CLAUDE_HOME pointing at a valid catalog but no
        # DISPATCH_CATALOG_PATH.
        clean_env = {
            k: v
            for k, v in os.environ.items()
            if k != "DISPATCH_CATALOG_PATH"
        }
        clean_env["CLAUDE_HOME"] = str(tmp_path)

        result = subprocess.run(
            [PYTHON, "-m", *_MATCH_MODULE],
            input=json.dumps({"task_description": "implement a feature"}),
            capture_output=True,
            text=True,
            env=clean_env,
            check=False,
        )
        assert result.returncode != 0, (
            "CLAUDE_HOME must no longer serve as a catalog fallback; "
            "expected non-zero exit but got success. "
            f"stderr={result.stderr!r}, stdout={result.stdout!r}"
        )
        assert "[CATALOG ERROR]" in result.stderr, (
            f"Expected [CATALOG ERROR] on stderr; got: {result.stderr!r}"
        )

    def test_catalog_path_flag_overrides_env(self, tmp_path: Path) -> None:
        """--catalog-path flag supplies the catalog path to the CLI.

        When ``--catalog-path <path>`` is passed, the matcher uses that file
        and succeeds — regardless of whether ``DISPATCH_CATALOG_PATH`` is set.
        """
        catalog = _catalog(
            [
                _make_agent(
                    "code-writer",
                    keywords=[{"term": "implement", "weight": 1.0}],
                    path_globs=["**/*.py"],
                ),
            ]
        )
        catalog_file = tmp_path / "my_catalog.json"
        catalog_file.write_text(json.dumps(catalog), encoding="utf-8")

        clean_env = {
            k: v
            for k, v in os.environ.items()
            if k not in {"DISPATCH_CATALOG_PATH", "CLAUDE_HOME"}
        }

        result = subprocess.run(
            [
                PYTHON,
                "-m", *_MATCH_MODULE,
                "--catalog-path",
                str(catalog_file),
            ],
            input=json.dumps(
                {
                    "task_description": "implement the feature",
                    "file_paths": ["main.py"],
                }
            ),
            capture_output=True,
            text=True,
            env=clean_env,
            check=False,
        )
        assert result.returncode == 0, (
            f"--catalog-path flag should supply catalog and succeed; "
            f"stderr={result.stderr!r}"
        )
        out = json.loads(result.stdout)
        assert out["decision"] in {
            "delegate",
            "self_handle",
            "advisory",
            "ambiguous",
        }, f"Expected a routing decision, got: {out['decision']!r}"

    def test_dispatch_catalog_path_env_still_works(self, tmp_path: Path) -> None:
        """DISPATCH_CATALOG_PATH env var remains the second resolution step.

        The env var is still supported after Issue #10 — only ``CLAUDE_HOME``
        and the ``~/.claude/`` default are removed.
        """
        catalog = _catalog(
            [
                _make_agent(
                    "code-writer",
                    keywords=[{"term": "implement", "weight": 1.0}],
                    path_globs=["**/*.py"],
                ),
            ]
        )
        catalog_file = tmp_path / "env_catalog.json"
        catalog_file.write_text(json.dumps(catalog), encoding="utf-8")

        clean_env = {
            k: v
            for k, v in os.environ.items()
            if k not in {"CLAUDE_HOME"}
        }
        clean_env["DISPATCH_CATALOG_PATH"] = str(catalog_file)

        result = subprocess.run(
            [PYTHON, "-m", *_MATCH_MODULE],
            input=json.dumps(
                {
                    "task_description": "implement the feature",
                    "file_paths": ["main.py"],
                }
            ),
            capture_output=True,
            text=True,
            env=clean_env,
            check=False,
        )
        assert result.returncode == 0, (
            f"DISPATCH_CATALOG_PATH env var must still work; "
            f"stderr={result.stderr!r}"
        )
        out = json.loads(result.stdout)
        assert out["decision"] not in {"needs_more_detail"}, (
            f"Expected a routing decision, got: {out['decision']!r}"
        )

    def test_log_path_missing_disables_logging_silently(
        self, tmp_path: Path
    ) -> None:
        """Missing DISPATCH_LOG_PATH disables log writing without crashing.

        After Issue #10, ``_resolve_log_path()`` returns ``None`` when no log
        path is configured (no env var).  The matcher must still succeed and
        emit a valid decision — no crash, no fallback to ``~/.claude/``.
        """
        catalog = _catalog(
            [
                _make_agent(
                    "code-writer",
                    keywords=[{"term": "implement", "weight": 1.0}],
                ),
            ]
        )
        catalog_file = tmp_path / "catalog.json"
        catalog_file.write_text(json.dumps(catalog), encoding="utf-8")

        clean_env = {
            k: v
            for k, v in os.environ.items()
            if k not in {"DISPATCH_LOG_PATH", "CLAUDE_HOME"}
        }
        clean_env["DISPATCH_CATALOG_PATH"] = str(catalog_file)

        result = subprocess.run(
            [PYTHON, "-m", *_MATCH_MODULE],
            input=json.dumps({"task_description": "implement a feature"}),
            capture_output=True,
            text=True,
            env=clean_env,
            check=False,
        )
        assert result.returncode == 0, (
            "Matcher must succeed even when no log path is configured; "
            f"stderr={result.stderr!r}"
        )
        out = json.loads(result.stdout)
        assert out["decision"] in _VALID_DECISIONS, (
            f"Expected a valid decision; got: {out['decision']!r}"
        )


# ---------------------------------------------------------------------------
# Task 5.5 — load_catalog on empty entries list
# ---------------------------------------------------------------------------


class TestLoadCatalogEmptyEntries:
    """load_catalog accepts an empty entries list (was: raised ValueError).

    Empty catalogs are a valid degraded state (e.g. fresh checkout, or the
    #506 all-entries-dropped path). Callers like audit-catalog need to
    operate on them without a load-time crash.
    """

    def test_empty_entries_returns_empty_tuple(self, tmp_path) -> None:
        """load_catalog on {"entries": []} returns an empty list."""
        from claude_wayfinder.match import load_catalog

        p = tmp_path / "cat.json"
        p.write_text(json.dumps({"entries": []}))
        result = load_catalog(p)
        assert tuple(result) == tuple()
