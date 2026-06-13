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


# ---------------------------------------------------------------------------
# Fix 1: E6 flip semantics — diagnose must be REMOVED when E6 fires
# ---------------------------------------------------------------------------


class TestE6FlipSemantics:
    """§10.2 E6, §12.3: E6 is a MODIFIER that FLIPS diagnose → build.

    When E6 fires, the posture set must have 'diagnose' removed and 'build'
    present.  The resulting route is code × build → code-writer, not the
    diagnose-priority investigator/debugger path.

    P11 fixture: ``FAILED tests/test_api.py … Started after we renamed …
    Update the tests to match.``  E2 fires (test failure output), E6 fires
    (cause stated via 'after'), so diagnose must flip → build, routing to
    code-writer.
    """

    def test_e6_removes_diagnose_from_postures(self) -> None:
        """When E6 fires, _postures_from_extractor_results must NOT include diagnose."""
        from claude_wayfinder.posture._types import PostureContext
        from scripts.corpus.eval._systems import (
            _postures_from_extractor_results,
            _run_all_extractors,
        )

        # P11: test failure output (E2) + cause stated after (E6)
        ctx = PostureContext(
            task_description=(
                "Here's pytest: `FAILED tests/test_api.py::test_fetch -"
                " AttributeError: no attribute 'get_user'`. Started after we"
                " renamed get_user → fetch_user. Update the tests to match."
            ),
            file_paths=(),
            agent_mentions=frozenset(),
            tool_mentions=frozenset(),
            command_prefix=None,
        )
        results = _run_all_extractors(ctx)
        assert results["e6"].fired, "E6 must fire on P11 (cause stated via 'after')"
        postures = _postures_from_extractor_results(results)
        assert "diagnose" not in postures, (
            f"E6 flip must remove 'diagnose' from postures; got {postures!r}"
        )

    def test_e6_adds_build_to_postures(self) -> None:
        """When E6 fires, 'build' must be present in postures."""
        from claude_wayfinder.posture._types import PostureContext
        from scripts.corpus.eval._systems import (
            _postures_from_extractor_results,
            _run_all_extractors,
        )

        ctx = PostureContext(
            task_description=(
                "Here's pytest: `FAILED tests/test_api.py::test_fetch -"
                " AttributeError: no attribute 'get_user'`. Started after we"
                " renamed get_user → fetch_user. Update the tests to match."
            ),
            file_paths=(),
            agent_mentions=frozenset(),
            tool_mentions=frozenset(),
            command_prefix=None,
        )
        results = _run_all_extractors(ctx)
        postures = _postures_from_extractor_results(results)
        assert "build" in postures, (
            f"E6 flip must add 'build' to postures; got {postures!r}"
        )

    def test_p11_routes_code_writer_via_e6_flip(
        self,
        fixture_corpus_path: Path,
        fixture_catalog_path: Path,
    ) -> None:
        """P11 must route to code-writer after E6 flips diagnose → build."""
        from scripts.corpus.eval._reader import load_corpus
        from scripts.corpus.eval._systems import run_extractors

        entries = load_corpus(fixture_corpus_path)
        results = run_extractors(entries, fixture_catalog_path)
        p11 = next(r for r in results if r.corpus_id == 11)
        assert p11.agent == "code-writer", (
            f"P11 must route to code-writer (E6 flip), got {p11.agent!r}"
        )
        assert p11.decision == "delegate", (
            f"P11 must be delegate band, got {p11.decision!r}"
        )


# ---------------------------------------------------------------------------
# Fix 1 (review): E7 area-span must be gated on a diagnose host (E1 or E2)
# ---------------------------------------------------------------------------


class TestE7HostGating:
    """§10.2 E7: E7 is a MODIFIER inside diagnose — it must NOT activate
    diagnose itself.

    A prompt with only file_paths (no E1/E2 failure artifact) must NOT
    receive diagnose posture from E7.  E7's span count is recorded in
    extras["area_span"] regardless.
    """

    def test_file_paths_only_no_failure_does_not_get_diagnose(
        self,
        tmp_path: Path,
        fixture_catalog_path: Path,
    ) -> None:
        """Entry with file_paths but NO E1/E2 must NOT route to diagnose."""
        import json

        from scripts.corpus.eval._reader import load_corpus
        from scripts.corpus.eval._systems import run_extractors

        # Build an entry: only file_paths across two areas (src + infra),
        # no stacktrace, no test failure output.
        record = {
            "type": "matcher_decision",
            "session_id": "session-e7-gate-001",
            "input": {
                "task_description": (
                    "Update src/api/client.py and .github/workflows/deploy.yml"
                    " to use the new endpoint."
                ),
                "file_paths": [
                    "src/api/client.py",
                    ".github/workflows/deploy.yml",
                ],
                "agent_mentions": [],
                "tool_mentions": [],
                "command_prefix": None,
            },
            "output": {"decision": "advisory", "agent": None, "confidence": 0.5},
            "corpus_id": 1,
            "stratum": {
                "decision_band": "advisory",
                "td_length_band": "short",
                "file_paths_present": True,
            },
        }
        corpus_file = tmp_path / "e7-gate-corpus.jsonl"
        corpus_file.write_text(json.dumps(record) + "\n", encoding="utf-8")

        entries = load_corpus(corpus_file)
        results = run_extractors(entries, fixture_catalog_path)

        r = results[0]
        postures = r.extras.get("postures", [])
        assert "diagnose" not in postures, (
            f"E7 must NOT inject diagnose without E1/E2 host; "
            f"postures={postures!r}, agent={r.agent!r}"
        )

    def test_file_paths_only_no_failure_does_not_route_to_debugger_or_investigator(
        self,
        tmp_path: Path,
        fixture_catalog_path: Path,
    ) -> None:
        """file_paths-only prompt must NOT route to debugger or investigator."""
        import json

        from scripts.corpus.eval._reader import load_corpus
        from scripts.corpus.eval._systems import run_extractors

        record = {
            "type": "matcher_decision",
            "session_id": "session-e7-gate-002",
            "input": {
                "task_description": (
                    "Update src/api/client.py and .github/workflows/deploy.yml"
                    " to use the new endpoint."
                ),
                "file_paths": [
                    "src/api/client.py",
                    ".github/workflows/deploy.yml",
                ],
                "agent_mentions": [],
                "tool_mentions": [],
                "command_prefix": None,
            },
            "output": {"decision": "advisory", "agent": None, "confidence": 0.5},
            "corpus_id": 1,
            "stratum": {
                "decision_band": "advisory",
                "td_length_band": "short",
                "file_paths_present": True,
            },
        }
        corpus_file = tmp_path / "e7-gate-agent-corpus.jsonl"
        corpus_file.write_text(json.dumps(record) + "\n", encoding="utf-8")

        entries = load_corpus(corpus_file)
        results = run_extractors(entries, fixture_catalog_path)

        r = results[0]
        assert r.agent not in ("debugger", "investigator"), (
            f"file_paths-only prompt must not route to debugger/investigator; "
            f"got agent={r.agent!r}"
        )

    def test_area_span_in_extras_regardless_of_host_condition(
        self,
        tmp_path: Path,
        fixture_catalog_path: Path,
    ) -> None:
        """area_span count appears in extras even when E1/E2 did not fire."""
        import json

        from scripts.corpus.eval._reader import load_corpus
        from scripts.corpus.eval._systems import run_extractors

        record = {
            "type": "matcher_decision",
            "session_id": "session-e7-gate-003",
            "input": {
                "task_description": (
                    "Update src/api/client.py and .github/workflows/deploy.yml"
                    " to use the new endpoint."
                ),
                "file_paths": [
                    "src/api/client.py",
                    ".github/workflows/deploy.yml",
                ],
                "agent_mentions": [],
                "tool_mentions": [],
                "command_prefix": None,
            },
            "output": {"decision": "advisory", "agent": None, "confidence": 0.5},
            "corpus_id": 1,
            "stratum": {
                "decision_band": "advisory",
                "td_length_band": "short",
                "file_paths_present": True,
            },
        }
        corpus_file = tmp_path / "e7-gate-span-corpus.jsonl"
        corpus_file.write_text(json.dumps(record) + "\n", encoding="utf-8")

        entries = load_corpus(corpus_file)
        results = run_extractors(entries, fixture_catalog_path)

        r = results[0]
        # area_span must be present in extras (used by metric 4 + braked logic)
        assert "area_span" in r.extras, (
            "area_span must appear in extras regardless of E1/E2 host condition"
        )

    def test_e1_span_ge2_still_routes_investigator(
        self,
        tmp_path: Path,
        fixture_catalog_path: Path,
    ) -> None:
        """E1 (stacktrace) + file_paths spanning ≥2 areas → investigator.

        P14 fixture exercises this: stacktrace block + two-area file_paths.
        The host condition (E1 fired) is met, so E7 posture evidence counts
        and the span≥2 rule routes to investigator.
        """
        import json

        from scripts.corpus.eval._reader import load_corpus
        from scripts.corpus.eval._systems import run_extractors

        # Minimal E1 + span≥2: stacktrace in prose + file_paths in two areas
        record = {
            "type": "matcher_decision",
            "session_id": "session-e7-gate-004",
            "input": {
                "task_description": (
                    "Getting this in CI: `Traceback (most recent call last):\n"
                    "  File 'src/api/client.py', line 42, in fetch\n"
                    "    raise ConnectionError` — happens only in deploy."
                ),
                "file_paths": [
                    "src/api/client.py",
                    ".github/workflows/deploy.yml",
                ],
                "agent_mentions": [],
                "tool_mentions": [],
                "command_prefix": None,
            },
            "output": {"decision": "delegate", "agent": "investigator", "confidence": 0.9},
            "corpus_id": 1,
            "stratum": {
                "decision_band": "delegate",
                "td_length_band": "medium",
                "file_paths_present": True,
            },
        }
        corpus_file = tmp_path / "e7-e1-corpus.jsonl"
        corpus_file.write_text(json.dumps(record) + "\n", encoding="utf-8")

        entries = load_corpus(corpus_file)
        results = run_extractors(entries, fixture_catalog_path)

        r = results[0]
        assert r.agent == "investigator", (
            f"E1 + span≥2 → investigator; got agent={r.agent!r}, "
            f"postures={r.extras.get('postures')!r}"
        )


# ---------------------------------------------------------------------------
# Fix #351: _is_domain_any — margin-only gate, no entropy parameter
# ---------------------------------------------------------------------------


class TestIsDomainAnyGate:
    """Pure unit tests for the _is_domain_any helper.

    The helper must gate domain-any detection on the top-1/top-2 margin
    ONLY — the entropy parameter that caused every prompt to be forced
    domain-any (#351 root cause) must not exist.

    These tests are model-independent and carry no importorskip guard
    because the helper itself has no heavy dependencies.
    """

    def test_below_threshold_is_domain_any(self) -> None:
        """margin < _MARGIN_ANY_THRESHOLD (0.005) → domain-any."""
        from scripts.corpus.eval._systems import _is_domain_any

        assert _is_domain_any(0.005) is True

    def test_zero_margin_is_domain_any(self) -> None:
        """Degenerate zero margin → domain-any (widest possible ambiguity)."""
        from scripts.corpus.eval._systems import _is_domain_any

        assert _is_domain_any(0.0) is True

    def test_above_threshold_is_not_domain_any(self) -> None:
        """margin > _MARGIN_ANY_THRESHOLD (0.02) → domain-specific (routable)."""
        from scripts.corpus.eval._systems import _is_domain_any

        assert _is_domain_any(0.02) is False

    def test_clearly_above_threshold_is_not_domain_any(self) -> None:
        """Well-separated top-1/top-2 (0.05) → domain-specific, not any."""
        from scripts.corpus.eval._systems import _is_domain_any

        assert _is_domain_any(0.05) is False

    def test_exactly_at_threshold_is_not_domain_any(self) -> None:
        """Boundary: margin == 0.01 with strict < must NOT be domain-any.

        This test pins the operator to strict less-than so the implementer
        cannot silently flip < to <= without breaking a test.
        """
        from scripts.corpus.eval._systems import _is_domain_any

        # threshold is 0.01; strict < means exactly-equal is NOT domain-any
        assert _is_domain_any(0.01) is False

    def test_entropy_not_in_signature(self) -> None:
        """_is_domain_any must accept exactly one positional parameter.

        The entropy gate (_ENTROPY_ANY_THRESHOLD) is the root cause of
        bug #351: with max entropy ~2.31 always above threshold 1.5,
        every prompt was forced domain-any.  This test hard-pins the
        removal of entropy from the gate by asserting the helper's
        signature has one parameter only.
        """
        import inspect

        from scripts.corpus.eval._systems import _is_domain_any

        sig = inspect.signature(_is_domain_any)
        params = list(sig.parameters)
        assert len(params) == 1, (
            f"_is_domain_any must accept exactly one parameter (margin); "
            f"got {params!r}.  Entropy must not gate domain-any detection."
        )
        # Confirm it works with a single positional call — no entropy arg
        result = _is_domain_any(0.02)
        assert result is False


# ---------------------------------------------------------------------------
# Fix #351: encoder behavioral regression — must emit delegate decisions
# ---------------------------------------------------------------------------


class TestEncoderNotAlwaysDomainAny:
    """Integration regression guard for #351.

    Before the fix, entropy > 1.5 was always True (encoder entropy on
    5-class softmax is ~2.31, max 2.32), so 100% of prompts were forced
    domain-any → decision = "advisory".  This class asserts that at
    least one clearly domain-specific prompt produces decision="delegate"
    post-fix.

    Skipped when model2vec is absent (same pattern as TestRunEncoder).
    """

    def test_clear_code_prompt_produces_at_least_one_delegate(
        self,
        tmp_path: Path,
        fixture_catalog_path: Path,
    ) -> None:
        """Clearly domain-specific prompts must yield at least one delegate.

        Under the entropy-gate bug (entropy > 1.5 always True) every
        result was "advisory".  Post-fix, wide-margin domain-specific
        prompts must produce decision="delegate" — so this assertion
        fails before the fix and passes after.

        Prompts chosen from 8M spike §5.3 as consistently wide-margin:
          - A null-pointer crash in src/auth/login.py  (code domain)
          - Deploy Bicep template via azd              (infra_deploy domain)
        """
        import json

        pytest.importorskip("model2vec")
        from scripts.corpus.eval._reader import load_corpus
        from scripts.corpus.eval._systems import run_encoder

        # Two prompts the 8M spike showed have wide domain margins.
        # Null-pointer in a Python auth module → clear code domain.
        # Bicep deploy via azd → clear infra_deploy domain.
        records = [
            {
                "type": "matcher_decision",
                "session_id": "session-351-code-001",
                "input": {
                    "task_description": (
                        "Fix the null-pointer crash in src/auth/login.py"
                        " — the stack trace shows it fails when the user"
                        " object is None after token expiry."
                    ),
                    "file_paths": ["src/auth/login.py"],
                    "agent_mentions": [],
                    "tool_mentions": [],
                    "command_prefix": None,
                },
                "output": {
                    "decision": "delegate",
                    "agent": "code-writer",
                    "confidence": 0.9,
                },
                "corpus_id": 1,
                "stratum": {
                    "decision_band": "delegate",
                    "td_length_band": "short",
                    "file_paths_present": True,
                },
            },
            {
                "type": "matcher_decision",
                "session_id": "session-351-infra-001",
                "input": {
                    "task_description": (
                        "Deploy the Bicep template to the prod resource"
                        " group via azd — the pipeline is ready and the"
                        " template has been reviewed."
                    ),
                    "file_paths": [],
                    "agent_mentions": [],
                    "tool_mentions": [],
                    "command_prefix": None,
                },
                "output": {
                    "decision": "delegate",
                    "agent": "ops",
                    "confidence": 0.9,
                },
                "corpus_id": 2,
                "stratum": {
                    "decision_band": "delegate",
                    "td_length_band": "short",
                    "file_paths_present": False,
                },
            },
        ]
        corpus_file = tmp_path / "encoder-351-corpus.jsonl"
        lines = [json.dumps(r, ensure_ascii=False) for r in records]
        corpus_file.write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )

        entries = load_corpus(corpus_file)
        results = run_encoder(entries, fixture_catalog_path)

        delegate_count = sum(
            1 for r in results if r.decision == "delegate"
        )
        # Pre-fix: entropy > 1.5 always True → all advisory → 0 delegates.
        # Post-fix: wide-margin prompts route as delegate → count ≥ 1.
        assert delegate_count >= 1, (
            f"Expected at least one delegate decision for domain-specific "
            f"prompts, got {delegate_count}.  "
            f"Decisions: {[r.decision for r in results]!r}.  "
            f"This assertion catches the entropy-gate bug (#351) where "
            f"entropy > 1.5 always True forced every result advisory."
        )


# ---------------------------------------------------------------------------
# Fix #351 (run_composed): composed domain gate must not always be "any"
# ---------------------------------------------------------------------------


class TestComposedDomainGate:
    """Regression guard for the duplicate entropy-gate bug in run_composed.

    run_composed (~lines 865-878 of _systems.py) contains a copy of the
    broken gate from #351:

        is_any = entropy > 1.5 or margin < 0.04

    The encoder entropy on a 5-class softmax is always ~2.31, so
    ``entropy > 1.5`` is always True, forcing every composed result to
    ``extras["is_any"] = True``.  A delegate-count assertion does NOT
    catch this because run_composed still routes via posture even when
    domain is forced "any" (the "any" path yields advisory by design).
    The correct regression is on the domain axis itself: at least one
    clearly domain-specific prompt must survive the gate with
    ``is_any = False``.

    Skipped when model2vec is absent (same pattern as TestRunComposed).
    """

    def test_composed_domain_gate_not_always_any(
        self,
        tmp_path: Path,
        fixture_catalog_path: Path,
    ) -> None:
        """At least one domain-specific prompt must have extras["is_any"]=False.

        Prompts are the same wide-margin pair used in
        TestEncoderNotAlwaysDomainAny, chosen because the 8M spike (§5.3)
        confirmed they have wide margin on the encoder:
          - Fix the null-pointer crash in src/auth/login.py  (code domain)
          - Deploy the Bicep template via azd               (infra_deploy)

        Under the entropy-gate bug (``entropy > 1.5 or margin < 0.04``),
        entropy is always ~2.31 so the OR short-circuits and every result
        has ``extras["is_any"] = True``.  This assertion fails pre-fix and
        passes once run_composed adopts the margin-only gate
        ``is_any = margin < 0.01`` (matching the fixed run_encoder).
        """
        import json

        pytest.importorskip("model2vec")
        from scripts.corpus.eval._reader import load_corpus
        from scripts.corpus.eval._systems import run_composed

        # Same wide-margin prompts as TestEncoderNotAlwaysDomainAny so the
        # two regression guards stay consistent.
        records = [
            {
                "type": "matcher_decision",
                "session_id": "session-351-composed-code-001",
                "input": {
                    "task_description": (
                        "Fix the null-pointer crash in src/auth/login.py"
                        " — the stack trace shows it fails when the user"
                        " object is None after token expiry."
                    ),
                    "file_paths": ["src/auth/login.py"],
                    "agent_mentions": [],
                    "tool_mentions": [],
                    "command_prefix": None,
                },
                "output": {
                    "decision": "delegate",
                    "agent": "code-writer",
                    "confidence": 0.9,
                },
                "corpus_id": 1,
                "stratum": {
                    "decision_band": "delegate",
                    "td_length_band": "short",
                    "file_paths_present": True,
                },
            },
            {
                "type": "matcher_decision",
                "session_id": "session-351-composed-infra-001",
                "input": {
                    "task_description": (
                        "Deploy the Bicep template to the prod resource"
                        " group via azd — the pipeline is ready and the"
                        " template has been reviewed."
                    ),
                    "file_paths": [],
                    "agent_mentions": [],
                    "tool_mentions": [],
                    "command_prefix": None,
                },
                "output": {
                    "decision": "delegate",
                    "agent": "ops",
                    "confidence": 0.9,
                },
                "corpus_id": 2,
                "stratum": {
                    "decision_band": "delegate",
                    "td_length_band": "short",
                    "file_paths_present": False,
                },
            },
        ]
        corpus_file = tmp_path / "composed-351-corpus.jsonl"
        lines = [json.dumps(r, ensure_ascii=False) for r in records]
        corpus_file.write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )

        entries = load_corpus(corpus_file)
        results = run_composed(entries, fixture_catalog_path)

        # Under the entropy-gate bug every result has extras["is_any"]=True
        # because entropy (~2.31) always exceeds the 1.5 threshold.
        # Post-fix (margin-only gate), at least one wide-margin prompt
        # escapes the "any" classification and the domain axis engages.
        assert any(r.extras["is_any"] is False for r in results), (
            f"Expected at least one result with extras['is_any']=False for "
            f"clearly domain-specific prompts, but all results have "
            f"is_any=True.  "
            f"is_any values: {[r.extras.get('is_any') for r in results]!r}.  "
            f"This catches the duplicate entropy-gate bug in run_composed "
            f"(#351): 'entropy > 1.5 or margin < 0.04' forces is_any=True "
            f"on every prompt because encoder entropy is always ~2.31."
        )
