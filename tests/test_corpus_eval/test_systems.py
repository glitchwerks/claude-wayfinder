"""Tests for scripts.corpus.eval._systems.

Tests all four system runners.  Encoder-dependent paths use
pytest.importorskip to remain green in CI (.[dev] only).

RED — written before implementation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.corpus.eval._reader import load_corpus
from scripts.corpus.eval._systems import (
    SystemResult,
    run_extractors,
    run_lexical,
)

# ---------------------------------------------------------------------------
# Small fixture catalog (minimal agents for lexical scoring tests)
# ---------------------------------------------------------------------------

_CATALOG_ENTRIES_RAW = [
    {
        "name": "code-writer",
        "kind": "agent",
        "source": "owned",
        "routable": True,
        "applicable_agents": [],
        "applicable_skills": [],
        "triggers": {
            "command_prefixes": [],
            "agent_mentions": ["code-writer"],
            "path_globs": ["**/*.py"],
            "path_globs_excluded": [],
            "keywords": [
                {"term": "implement", "weight": 1.0},
                {"term": "update", "weight": 0.8},
                {"term": "fix", "weight": 0.8},
                {"term": "test", "weight": 0.5},
                {"term": "api", "weight": 0.5},
            ],
            "tool_mentions": [],
            "excludes": [],
        },
    },
    {
        "name": "ops",
        "kind": "agent",
        "source": "owned",
        "routable": True,
        "applicable_agents": [],
        "applicable_skills": [],
        "triggers": {
            "command_prefixes": ["gh", "git"],
            "agent_mentions": ["ops"],
            "path_globs": [],
            "path_globs_excluded": [],
            "keywords": [
                {"term": "run", "weight": 0.5},
                {"term": "status", "weight": 0.5},
            ],
            "tool_mentions": [],
            "excludes": [],
        },
    },
    {
        "name": "investigator",
        "kind": "agent",
        "source": "owned",
        "routable": True,
        "applicable_agents": [],
        "applicable_skills": [],
        "triggers": {
            "command_prefixes": [],
            "agent_mentions": ["investigator"],
            "path_globs": [],
            "path_globs_excluded": [],
            "keywords": [
                {"term": "debug", "weight": 1.0},
                {"term": "investigate", "weight": 1.0},
                {"term": "figure", "weight": 0.5},
                {"term": "error", "weight": 0.5},
                {"term": "fail", "weight": 0.5},
            ],
            "tool_mentions": [],
            "excludes": [],
        },
    },
    {
        "name": "researcher",
        "kind": "agent",
        "source": "owned",
        "routable": True,
        "applicable_agents": [],
        "applicable_skills": [],
        "triggers": {
            "command_prefixes": [],
            "agent_mentions": ["researcher"],
            "path_globs": [],
            "path_globs_excluded": [],
            "keywords": [
                {"term": "research", "weight": 1.0},
                {"term": "anyone", "weight": 0.5},
                {"term": "prior", "weight": 0.5},
            ],
            "tool_mentions": [],
            "excludes": [],
        },
    },
    {
        "name": "project-planner",
        "kind": "agent",
        "source": "owned",
        "routable": True,
        "applicable_agents": [],
        "applicable_skills": [],
        "triggers": {
            "command_prefixes": [],
            "agent_mentions": ["project-planner"],
            "path_globs": [],
            "path_globs_excluded": [],
            "keywords": [
                {"term": "phase", "weight": 1.0},
                {"term": "milestone", "weight": 1.0},
                {"term": "plan", "weight": 0.5},
            ],
            "tool_mentions": [],
            "excludes": [],
        },
    },
]


@pytest.fixture()
def fixture_catalog_path(tmp_path: Path) -> Path:
    """Write a minimal catalog JSON for lexical runner tests."""
    import json

    catalog = {"entries": _CATALOG_ENTRIES_RAW}
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(catalog), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# SystemResult contract
# ---------------------------------------------------------------------------


class TestSystemResult:
    """Tests for the SystemResult dataclass."""

    def test_system_result_has_required_fields(self) -> None:
        """SystemResult has corpus_id, decision, agent, confidence."""
        result = SystemResult(
            corpus_id=1,
            decision="delegate",
            agent="code-writer",
            confidence=0.9,
            extras={},
        )
        assert result.corpus_id == 1
        assert result.decision == "delegate"
        assert result.agent == "code-writer"
        assert result.confidence == 0.9

    def test_system_result_agent_can_be_none(self) -> None:
        """agent is None when decision has no target agent."""
        result = SystemResult(
            corpus_id=2,
            decision="advisory",
            agent=None,
            confidence=0.5,
            extras={},
        )
        assert result.agent is None


# ---------------------------------------------------------------------------
# run_lexical
# ---------------------------------------------------------------------------


class TestRunLexical:
    """Tests for run_lexical()."""

    def test_returns_list_of_system_results(
        self,
        fixture_corpus_path: Path,
        fixture_catalog_path: Path,
    ) -> None:
        """run_lexical returns one SystemResult per corpus entry."""
        entries = load_corpus(fixture_corpus_path)
        results = run_lexical(entries, fixture_catalog_path)
        assert isinstance(results, list)
        assert len(results) == 14

    def test_corpus_ids_preserved(
        self,
        fixture_corpus_path: Path,
        fixture_catalog_path: Path,
    ) -> None:
        """Each result has the same corpus_id as the input entry."""
        entries = load_corpus(fixture_corpus_path)
        results = run_lexical(entries, fixture_catalog_path)
        result_ids = [r.corpus_id for r in results]
        entry_ids = [e.corpus_id for e in entries]
        assert result_ids == entry_ids

    def test_p13_routes_ops_via_command_prefix(
        self,
        fixture_corpus_path: Path,
        fixture_catalog_path: Path,
    ) -> None:
        """P13 (gh command_prefix) routes to ops via lexical short-circuit."""
        entries = load_corpus(fixture_corpus_path)
        results = run_lexical(entries, fixture_catalog_path)
        p13 = next(r for r in results if r.corpus_id == 13)
        assert p13.agent == "ops"
        assert p13.decision == "delegate"

    def test_decision_is_valid_string(
        self,
        fixture_corpus_path: Path,
        fixture_catalog_path: Path,
    ) -> None:
        """Every decision is a non-empty string."""
        entries = load_corpus(fixture_corpus_path)
        results = run_lexical(entries, fixture_catalog_path)
        for r in results:
            assert isinstance(r.decision, str)
            assert len(r.decision) > 0

    def test_confidence_in_range(
        self,
        fixture_corpus_path: Path,
        fixture_catalog_path: Path,
    ) -> None:
        """Confidence is in [0.0, 1.0]."""
        entries = load_corpus(fixture_corpus_path)
        results = run_lexical(entries, fixture_catalog_path)
        for r in results:
            assert 0.0 <= r.confidence <= 1.0


# ---------------------------------------------------------------------------
# run_extractors
# ---------------------------------------------------------------------------


class TestRunExtractors:
    """Tests for run_extractors() (posture extractor cells)."""

    def test_returns_list_of_system_results(
        self,
        fixture_corpus_path: Path,
        fixture_catalog_path: Path,
    ) -> None:
        """run_extractors returns one SystemResult per corpus entry."""
        entries = load_corpus(fixture_corpus_path)
        results = run_extractors(entries, fixture_catalog_path)
        assert isinstance(results, list)
        assert len(results) == 14

    def test_corpus_ids_preserved(
        self,
        fixture_corpus_path: Path,
        fixture_catalog_path: Path,
    ) -> None:
        """Each result has the same corpus_id as the input entry."""
        entries = load_corpus(fixture_corpus_path)
        results = run_extractors(entries, fixture_catalog_path)
        result_ids = [r.corpus_id for r in results]
        entry_ids = [e.corpus_id for e in entries]
        assert result_ids == entry_ids

    def test_p13_routes_operate_via_command_prefix(
        self,
        fixture_corpus_path: Path,
        fixture_catalog_path: Path,
    ) -> None:
        """P13 (gh command_prefix) fires E8 → operate → ops."""
        entries = load_corpus(fixture_corpus_path)
        results = run_extractors(entries, fixture_catalog_path)
        p13 = next(r for r in results if r.corpus_id == 13)
        assert p13.agent == "ops"

    def test_p5_routes_plan_via_frame_markers(
        self,
        fixture_corpus_path: Path,
        fixture_catalog_path: Path,
    ) -> None:
        """P5 (phases + milestones) fires E9+E10 scope → plan → project-planner."""
        entries = load_corpus(fixture_corpus_path)
        results = run_extractors(entries, fixture_catalog_path)
        p5 = next(r for r in results if r.corpus_id == 5)
        assert p5.agent == "project-planner"

    def test_extras_contains_postures(
        self,
        fixture_corpus_path: Path,
        fixture_catalog_path: Path,
    ) -> None:
        """extras dict contains posture evidence information."""
        entries = load_corpus(fixture_corpus_path)
        results = run_extractors(entries, fixture_catalog_path)
        for r in results:
            assert "postures" in r.extras

    def test_extras_contains_tier_c_fired(
        self,
        fixture_corpus_path: Path,
        fixture_catalog_path: Path,
    ) -> None:
        """extras dict tracks whether Tier-C extractors fired (for telemetry)."""
        entries = load_corpus(fixture_corpus_path)
        results = run_extractors(entries, fixture_catalog_path)
        for r in results:
            assert "tier_c_fired" in r.extras


# ---------------------------------------------------------------------------
# run_encoder — importorskip guarded
# ---------------------------------------------------------------------------


class TestRunEncoder:
    """Tests for run_encoder() — skipped when model2vec is absent."""

    def test_importorskip_guard(self) -> None:
        """run_encoder is importable; model2vec absence is gracefully skipped."""
        pytest.importorskip("model2vec")
        from scripts.corpus.eval._systems import run_encoder  # noqa: F401

        assert callable(run_encoder)

    def test_encoder_returns_list_when_available(
        self,
        fixture_corpus_path: Path,
        fixture_catalog_path: Path,
    ) -> None:
        """run_encoder returns one result per entry when model is available."""
        pytest.importorskip("model2vec")
        from scripts.corpus.eval._systems import run_encoder

        entries = load_corpus(fixture_corpus_path)
        results = run_encoder(entries, fixture_catalog_path)
        assert isinstance(results, list)
        assert len(results) == 14


# ---------------------------------------------------------------------------
# run_composed — importorskip guarded
# ---------------------------------------------------------------------------


class TestRunComposed:
    """Tests for run_composed() — skipped when model2vec is absent."""

    def test_importorskip_guard(self) -> None:
        """run_composed is importable; model2vec absence gracefully skipped."""
        pytest.importorskip("model2vec")
        from scripts.corpus.eval._systems import run_composed  # noqa: F401

        assert callable(run_composed)

    def test_composed_returns_list_when_available(
        self,
        fixture_corpus_path: Path,
        fixture_catalog_path: Path,
    ) -> None:
        """run_composed returns one result per entry when model available."""
        pytest.importorskip("model2vec")
        from scripts.corpus.eval._systems import run_composed

        entries = load_corpus(fixture_corpus_path)
        results = run_composed(entries, fixture_catalog_path)
        assert isinstance(results, list)
        assert len(results) == 14
