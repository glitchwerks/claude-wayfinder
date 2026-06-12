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


# ---------------------------------------------------------------------------
# _route_from_postures — default-build must route via cell map
# ---------------------------------------------------------------------------


class TestRouteFromPosturesDefaultBuild:
    """§10.4: when no posture extractor fires but domain is concrete,
    default-build MUST route via _CELL_MAP[(domain, 'build')]."""

    def test_default_build_with_code_domain_returns_code_writer(
        self,
    ) -> None:
        """No-posture + code domain → code-writer via cell map (not None)."""
        from scripts.corpus.eval._systems import _route_from_postures

        agent, confidence = _route_from_postures(
            postures=[],
            area_span=0,
            e8_fired=False,
            e12_fired=False,
            domain="code",
        )
        assert agent == "code-writer", (
            f"Expected 'code-writer' via _CELL_MAP[('code','build')], got {agent!r}"
        )
        # Confidence should be advisory per §10.4 (contributes posture, not
        # confidence — advisory band is fine)
        assert confidence == 0.5

    def test_default_build_with_docs_prose_domain_returns_doc_writer(
        self,
    ) -> None:
        """No-posture + docs_prose domain → doc-writer via cell map."""
        from scripts.corpus.eval._systems import _route_from_postures

        agent, confidence = _route_from_postures(
            postures=[],
            area_span=0,
            e8_fired=False,
            e12_fired=False,
            domain="docs_prose",
        )
        assert agent == "doc-writer"
        assert confidence == 0.5

    def test_default_build_with_any_domain_returns_code_writer(
        self,
    ) -> None:
        """No-posture + 'any' domain → code-writer via ('any','build') fallback."""
        from scripts.corpus.eval._systems import _route_from_postures

        agent, confidence = _route_from_postures(
            postures=[],
            area_span=0,
            e8_fired=False,
            e12_fired=False,
            domain="any",
        )
        assert agent == "code-writer"
        assert confidence == 0.5

    def test_default_build_agent_is_not_none(self) -> None:
        """The default-build path MUST return a concrete agent, never None."""
        from scripts.corpus.eval._systems import _route_from_postures

        agent, _ = _route_from_postures(
            postures=[],
            area_span=0,
            e8_fired=False,
            e12_fired=False,
            domain="code",
        )
        assert agent is not None, (
            "§10.4 default-build must yield an agent from _CELL_MAP, not None"
        )


# ---------------------------------------------------------------------------
# Fix 1: E11 agent-mention pass-through (spec §10.2)
# ---------------------------------------------------------------------------


class TestE11PassThrough:
    """§10.2: explicit agent mention → near-dispositive pass-through.

    E11 evidence has form ``("as-named:<agent>", "strong")`` which does
    not match any posture name.  The runner must detect this and route
    directly to the named agent at confident band (0.9).
    """

    def test_e11_only_entry_routes_to_named_agent(
        self,
        tmp_path: Path,
        fixture_catalog_path: Path,
    ) -> None:
        """Entry with only agent_mentions fires E11 → routes to named agent."""
        import json

        from scripts.corpus.eval._reader import load_corpus
        from scripts.corpus.eval._systems import run_extractors

        record = {
            "type": "matcher_decision",
            "session_id": "session-e11-001",
            "input": {
                "task_description": "Can you have the researcher look into this?",
                "file_paths": [],
                "agent_mentions": ["researcher"],
                "tool_mentions": [],
                "command_prefix": None,
            },
            "output": {"decision": "delegate", "agent": "researcher", "confidence": 0.9},
            "corpus_id": 1,
            "stratum": {
                "decision_band": "delegate",
                "td_length_band": "short",
                "file_paths_present": False,
            },
        }
        corpus_file = tmp_path / "e11-corpus.jsonl"
        corpus_file.write_text(json.dumps(record) + "\n", encoding="utf-8")

        entries = load_corpus(corpus_file)
        results = run_extractors(entries, fixture_catalog_path)

        assert len(results) == 1
        r = results[0]
        assert r.agent == "researcher", (
            f"E11 pass-through should route to 'researcher', got {r.agent!r}"
        )
        # Near-dispositive: confident band (0.9), not advisory (0.5)
        assert r.confidence == 0.9, (
            f"E11 pass-through must be confident (0.9), got {r.confidence}"
        )

    def test_e11_wins_over_other_postures(
        self,
        tmp_path: Path,
        fixture_catalog_path: Path,
    ) -> None:
        """E11 is near-dispositive: agent_mentions overrides other posture signals."""
        import json

        from scripts.corpus.eval._reader import load_corpus
        from scripts.corpus.eval._systems import run_extractors

        # Has a build-posture signal AND an agent mention — E11 should win
        record = {
            "type": "matcher_decision",
            "session_id": "session-e11-002",
            "input": {
                "task_description": (
                    "Implement this feature — I want the researcher agent on it."
                ),
                "file_paths": ["src/feature.py"],
                "agent_mentions": ["researcher"],
                "tool_mentions": [],
                "command_prefix": None,
            },
            "output": {"decision": "delegate", "agent": "researcher", "confidence": 0.9},
            "corpus_id": 1,
            "stratum": {
                "decision_band": "delegate",
                "td_length_band": "short",
                "file_paths_present": True,
            },
        }
        corpus_file = tmp_path / "e11-wins-corpus.jsonl"
        corpus_file.write_text(json.dumps(record) + "\n", encoding="utf-8")

        entries = load_corpus(corpus_file)
        results = run_extractors(entries, fixture_catalog_path)

        r = results[0]
        assert r.agent == "researcher", (
            f"E11 near-dispositive must override build posture; got {r.agent!r}"
        )
        assert r.confidence == 0.9

    def test_e11_agent_not_in_catalog_falls_to_advisory(
        self,
        tmp_path: Path,
        fixture_catalog_path: Path,
    ) -> None:
        """E11 with unknown agent name stays advisory (not in catalog)."""
        import json

        from scripts.corpus.eval._reader import load_corpus
        from scripts.corpus.eval._systems import run_extractors

        record = {
            "type": "matcher_decision",
            "session_id": "session-e11-003",
            "input": {
                "task_description": "Can the unknown-agent handle this?",
                "file_paths": [],
                "agent_mentions": ["unknown-agent-xyz"],
                "tool_mentions": [],
                "command_prefix": None,
            },
            "output": {"decision": "advisory", "agent": None, "confidence": 0.5},
            "corpus_id": 1,
            "stratum": {
                "decision_band": "advisory",
                "td_length_band": "short",
                "file_paths_present": False,
            },
        }
        corpus_file = tmp_path / "e11-unknown-corpus.jsonl"
        corpus_file.write_text(json.dumps(record) + "\n", encoding="utf-8")

        entries = load_corpus(corpus_file)
        results = run_extractors(entries, fixture_catalog_path)

        r = results[0]
        # Agent not in catalog → advisory
        assert r.decision == "advisory", (
            f"Unknown E11 agent should produce advisory, got {r.decision!r}"
        )


# ---------------------------------------------------------------------------
# Fix 2: braked-outcome recording (extras["braked"] + extras["alternatives"])
# ---------------------------------------------------------------------------


class TestBrakedOutcomeRecording:
    """E12 brake must set extras['braked']=True and extras['alternatives'].

    Without these flags, metric_braked_candidate_quality always returns n/a.
    """

    def test_braked_entry_sets_braked_flag(
        self,
        tmp_path: Path,
        fixture_catalog_path: Path,
    ) -> None:
        """E4 build posture + E12 prose-failure term → E12 brakes → braked=True.

        Fires E4 (spec path in prose → build posture) and E12 (broken →
        prose_failure_mention).  E12 brakes the confident build → advisory,
        so extras['braked'] must be set to True.
        """
        import json

        from scripts.corpus.eval._reader import load_corpus
        from scripts.corpus.eval._systems import run_extractors

        # E4: prose path matching docs/superpowers/specs/** → build posture.
        # E12: "broken" → prose_failure_mention fires → brakes build.
        record = {
            "type": "matcher_decision",
            "session_id": "session-brake-001",
            "input": {
                "task_description": (
                    "The spec is broken — check"
                    " docs/superpowers/specs/feature-spec.md and make"
                    " sure the build passes."
                ),
                "file_paths": [],
                "agent_mentions": [],
                "tool_mentions": [],
                "command_prefix": None,
            },
            "output": {"decision": "advisory", "agent": None, "confidence": 0.5},
            "corpus_id": 1,
            "stratum": {
                "decision_band": "advisory",
                "td_length_band": "medium",
                "file_paths_present": False,
            },
        }
        corpus_file = tmp_path / "brake-corpus.jsonl"
        corpus_file.write_text(json.dumps(record) + "\n", encoding="utf-8")

        entries = load_corpus(corpus_file)
        results = run_extractors(entries, fixture_catalog_path)

        r = results[0]
        assert r.extras.get("braked") is True, (
            f"E12-braked result must have extras['braked']=True; "
            f"extras={r.extras!r}"
        )

    def test_braked_entry_sets_alternatives(
        self,
        tmp_path: Path,
        fixture_catalog_path: Path,
    ) -> None:
        """Braked entry must have extras['alternatives'] as a non-empty list."""
        import json

        from scripts.corpus.eval._reader import load_corpus
        from scripts.corpus.eval._systems import run_extractors

        record = {
            "type": "matcher_decision",
            "session_id": "session-brake-002",
            "input": {
                "task_description": (
                    "The spec is broken — check"
                    " docs/superpowers/specs/feature-spec.md and make"
                    " sure the build passes."
                ),
                "file_paths": [],
                "agent_mentions": [],
                "tool_mentions": [],
                "command_prefix": None,
            },
            "output": {"decision": "advisory", "agent": None, "confidence": 0.5},
            "corpus_id": 1,
            "stratum": {
                "decision_band": "advisory",
                "td_length_band": "medium",
                "file_paths_present": False,
            },
        }
        corpus_file = tmp_path / "brake-alts-corpus.jsonl"
        corpus_file.write_text(json.dumps(record) + "\n", encoding="utf-8")

        entries = load_corpus(corpus_file)
        results = run_extractors(entries, fixture_catalog_path)

        r = results[0]
        alternatives = r.extras.get("alternatives")
        assert isinstance(alternatives, list), (
            f"extras['alternatives'] must be a list; got {type(alternatives)}"
        )
        assert len(alternatives) > 0, (
            "extras['alternatives'] must be non-empty for a braked result"
        )

    def test_non_braked_entry_has_no_braked_flag(
        self,
        tmp_path: Path,
        fixture_catalog_path: Path,
    ) -> None:
        """Non-braked entry must NOT have extras['braked']=True."""
        import json

        from scripts.corpus.eval._reader import load_corpus
        from scripts.corpus.eval._systems import run_extractors

        # P13: operate (E8 Tier-A dominant) → no brake
        record = {
            "type": "matcher_decision",
            "session_id": "session-brake-003",
            "input": {
                "task_description": "Run `gh pr checks 214` and summarize what's red.",
                "file_paths": [],
                "agent_mentions": [],
                "tool_mentions": [],
                "command_prefix": "gh",
            },
            "output": {"decision": "delegate", "agent": "ops", "confidence": 0.9},
            "corpus_id": 1,
            "stratum": {
                "decision_band": "delegate",
                "td_length_band": "short",
                "file_paths_present": False,
            },
        }
        corpus_file = tmp_path / "no-brake-corpus.jsonl"
        corpus_file.write_text(json.dumps(record) + "\n", encoding="utf-8")

        entries = load_corpus(corpus_file)
        results = run_extractors(entries, fixture_catalog_path)

        r = results[0]
        assert not r.extras.get("braked", False), (
            "Non-braked result must not have extras['braked']=True"
        )

    def test_metric_braked_quality_computes_on_braked_fixture(
        self,
        tmp_path: Path,
        fixture_catalog_path: Path,
    ) -> None:
        """metric_braked_candidate_quality must return non-nan with a braked case."""
        import json
        import math

        from scripts.corpus.eval._metrics import metric_braked_candidate_quality
        from scripts.corpus.eval._reader import GoldLabel, load_corpus
        from scripts.corpus.eval._systems import run_extractors

        # Same E4+E12 braked prompt as the other brake tests
        record = {
            "type": "matcher_decision",
            "session_id": "session-brake-004",
            "input": {
                "task_description": (
                    "The spec is broken — check"
                    " docs/superpowers/specs/feature-spec.md and make"
                    " sure the build passes."
                ),
                "file_paths": [],
                "agent_mentions": [],
                "tool_mentions": [],
                "command_prefix": None,
            },
            "output": {"decision": "advisory", "agent": None, "confidence": 0.5},
            "corpus_id": 1,
            "stratum": {
                "decision_band": "advisory",
                "td_length_band": "medium",
                "file_paths_present": False,
            },
        }
        corpus_file = tmp_path / "brake-metric-corpus.jsonl"
        corpus_file.write_text(json.dumps(record) + "\n", encoding="utf-8")

        entries = load_corpus(corpus_file)
        results = run_extractors(entries, fixture_catalog_path)

        # Gold: code-writer is the braked winner (E4 build → code-writer)
        labels = {
            1: GoldLabel(
                corpus_id=1,
                domain="code",
                posture="build",
                gold_agent="code-writer",
                is_any=False,
            )
        }
        quality = metric_braked_candidate_quality(results, labels)
        assert not math.isnan(quality), (
            f"metric_braked_candidate_quality must not be nan when braked "
            f"cases exist; got {quality}"
        )
