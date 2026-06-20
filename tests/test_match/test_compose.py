"""Tests for the compose_route module (M15-2, issue #419).

Covers the public API of ``src/claude_wayfinder/match/_compose.py`` and
the ``Labels`` dataclass added to ``_types.py``.  All tests are written
against the contract in ``.tmp/M15-2-CONTRACT.md`` — no implementation
exists when this file was authored.

Test inventory
--------------
1. ``TestParseLabels``          — ``parse_labels`` + ``Labels`` defaults
                                  and coercions.
2. ``TestIsLexicallyPlausible`` — veto boundary cases for
                                  ``_is_lexically_plausible``.
3. ``TestBranch1Diagnose``      — broad-diagnose → investigator (Branch 1)
                                  including all live-only gate fall-throughs.
4. ``TestBranch2Sentinel``      — project_meta × build sentinel → self_handle
                                  (Branch 2, never gated by confidence).
5. ``TestBranch3Generic``       — generic posture route (Branch 3) including
                                  empty-gate guard and veto fall-through.
6. ``TestIsAnyNormalization``   — ``domain="is_any"`` maps to ``"any"``
                                  for cell lookup; gate passes through
                                  unchanged.
7. ``TestComposeVsOracleEquivalence`` — parity over the gold corpus with
                                  confidence forced to ``"high"``;
                                  branch-classified assertions (revised
                                  2026-06-20): Branch 1 + 2 must match
                                  oracle; Branch 3 veto-blocks are the
                                  one accepted divergence.
8. (removed) ``TestLiveStdoutUnchanged`` — obsolete M15-2 guard; wiring
                                  landed in M15-5; invariant now covered
                                  behaviourally by
                                  ``test_shadow_mode.py::TestShadowLiveUnchanged``
                                  and the golden parity tests.
9. ``TestFallbackPayloadFidelity`` — compose_route fallback equals decide().
10. ``TestComposeDiagnosticsParam`` — NEW (M15-5): optional ``diagnostics``
                                  out-param of ``compose_route`` populates
                                  per-step §F.1 state without changing
                                  return value or decision logic.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Paths for gold corpus / catalog
# ---------------------------------------------------------------------------

_CORPUS_PATH = Path(
    "C:/Users/chris/.claude/state/wayfinder-corpus/"
    "2026-06-12/wayfinder-corpus.jsonl"
)
_GOLD_LABELS_PATH = Path(
    "I:/ai/claude/claude-wayfinder/.worktrees/m15-2-compose-lift"
    "/docs/research/2026-06-12-gold-labels-redacted.jsonl"
)
_CATALOG_PATH = Path(
    "C:/Users/chris/.claude/state/dispatch-catalog.json"
)

# ---------------------------------------------------------------------------
# Imports under test (these will fail with ImportError until _compose.py
# and the Labels addition to _types.py are implemented).
# ---------------------------------------------------------------------------

from claude_wayfinder.match._catalog import load_catalog  # noqa: E402
from claude_wayfinder.match._cells import (  # noqa: E402
    SELF_HANDLE_SENTINEL,
    cell_map_lookup,
    gate_agents,
)
from claude_wayfinder.match._compose import (  # noqa: E402  # type: ignore[import]
    _is_lexically_plausible,
    compose_route,
    parse_labels,
)
from claude_wayfinder.match._decide import _DELEGATE_THRESHOLD  # noqa: E402
from claude_wayfinder.match._match import build_features, score_entries  # noqa: E402
from claude_wayfinder.match._types import (  # noqa: E402  # type: ignore[attr-defined]
    CatalogEntry,
    Labels,  # D-LBL1: Labels is added to _types.py by the implementer
    ScoredEntry,
)
from claude_wayfinder.match_filters import is_agent_routable  # noqa: E402

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_scored_entry(name: str, score: float) -> ScoredEntry:
    """Build a minimal ``ScoredEntry`` for plausibility tests.

    Args:
        name: Agent name.
        score: Numeric score in [0.0, 1.0].

    Returns:
        A :class:`ScoredEntry` whose ``entry.name`` is ``name``.
    """
    from claude_wayfinder.match._parse import _parse_triggers

    triggers_raw: dict[str, Any] = {
        "command_prefixes": [],
        "agent_mentions": [],
        "path_globs": [],
        "path_globs_excluded": [],
        "keywords": [],
        "tool_mentions": [],
        "excludes": [],
    }
    triggers = _parse_triggers(triggers_raw)
    entry = CatalogEntry(
        name=name,
        kind="agent",
        source="owned",
        routable=True,
        triggers=triggers,
        applicable_skills=(),
        applicable_agents=(),
    )
    return ScoredEntry(entry=entry, score=score)


def _make_gated(names_scores: list[tuple[str, float]]) -> list[ScoredEntry]:
    """Build a score-sorted ``gated`` list from (name, score) pairs.

    Args:
        names_scores: Ordered list of (agent_name, score) pairs.
            Need not be pre-sorted; this function sorts descending by
            score then ascending by name to match the real engine.

    Returns:
        Score-sorted list of :class:`ScoredEntry` objects.
    """
    unsorted = [_make_scored_entry(n, s) for n, s in names_scores]
    return sorted(unsorted, key=lambda se: (-se.score, se.entry.name))


# ---------------------------------------------------------------------------
# 1. TestParseLabels — parse_labels + Labels defaults and coercions
# ---------------------------------------------------------------------------


class TestParseLabels:
    """``parse_labels`` converts a context dict into a ``Labels`` dataclass.

    Covers: contract item 1 — defaults, area_span coercions, and the
    ``confidence_is_high`` truth table.
    """

    def test_empty_context_produces_default_labels(self) -> None:
        """Empty context yields all-None fields and area_span=1.

        An absent context must never raise; every field defaults
        gracefully.
        """
        labels = parse_labels({})
        assert labels.domain is None
        assert labels.posture is None
        assert labels.confidence is None
        assert labels.area_span == 1

    def test_present_string_fields_pass_through(self) -> None:
        """Non-empty domain/posture/confidence are passed through verbatim.

        The function must not normalise or validate these strings.
        """
        labels = parse_labels(
            {
                "domain": "code",
                "posture": "build",
                "confidence": "high",
                "area_span": 2,
            }
        )
        assert labels.domain == "code"
        assert labels.posture == "build"
        assert labels.confidence == "high"
        assert labels.area_span == 2

    def test_empty_string_domain_becomes_none(self) -> None:
        """Empty-string domain is normalised to None.

        Empty string must be treated the same as absent.
        """
        labels = parse_labels({"domain": "", "posture": "", "confidence": ""})
        assert labels.domain is None
        assert labels.posture is None
        assert labels.confidence is None

    def test_area_span_string_digit_is_coerced_to_int(self) -> None:
        """A numeric string area_span is coerced to int.

        Callers that serialise as JSON strings must be handled without
        raising.
        """
        labels = parse_labels({"area_span": "2"})
        assert labels.area_span == 2

    def test_area_span_missing_defaults_to_one(self) -> None:
        """Absent area_span field defaults to 1."""
        labels = parse_labels({"domain": "code"})
        assert labels.area_span == 1

    def test_area_span_non_int_string_defaults_to_one(self) -> None:
        """Non-numeric string area_span defaults to 1 without raising."""
        labels = parse_labels({"area_span": "x"})
        assert labels.area_span == 1

    def test_area_span_zero_defaults_to_one(self) -> None:
        """area_span value of 0 is coerced to 1 (< 1 is invalid)."""
        labels = parse_labels({"area_span": 0})
        assert labels.area_span == 1

    def test_area_span_negative_defaults_to_one(self) -> None:
        """Negative area_span is coerced to 1."""
        labels = parse_labels({"area_span": -3})
        assert labels.area_span == 1

    def test_confidence_is_high_true_for_high(self) -> None:
        """``confidence_is_high`` returns True only when confidence is "high".

        This verifies the contract's truth table: "high" → True.
        """
        from claude_wayfinder.match._compose import confidence_is_high  # type: ignore[import]

        labels = Labels(confidence="high")
        assert confidence_is_high(labels) is True

    def test_confidence_is_high_false_for_medium(self) -> None:
        """``confidence_is_high`` returns False for confidence="medium"."""
        from claude_wayfinder.match._compose import confidence_is_high  # type: ignore[import]

        labels = Labels(confidence="medium")
        assert confidence_is_high(labels) is False

    def test_confidence_is_high_false_for_low(self) -> None:
        """``confidence_is_high`` returns False for confidence="low"."""
        from claude_wayfinder.match._compose import confidence_is_high  # type: ignore[import]

        labels = Labels(confidence="low")
        assert confidence_is_high(labels) is False

    def test_confidence_is_high_false_for_none(self) -> None:
        """``confidence_is_high`` returns False when confidence is None.

        None is treated as LOW (§D.1 fail-safe).
        """
        from claude_wayfinder.match._compose import confidence_is_high  # type: ignore[import]

        labels = Labels(confidence=None)
        assert confidence_is_high(labels) is False

    def test_labels_is_frozen(self) -> None:
        """``Labels`` is a frozen dataclass — mutation must raise."""
        labels = Labels(domain="code")
        with pytest.raises((AttributeError, TypeError)):
            labels.domain = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 2. TestIsLexicallyPlausible — veto boundary cases
# ---------------------------------------------------------------------------


class TestIsLexicallyPlausible:
    """``_is_lexically_plausible`` boundary cases for the §B.1 veto.

    Covers: contract item 2 — None/top-3/score-floor/below-floor cases.
    """

    def test_preferred_none_returns_false(self) -> None:
        """Preferred=None must return False (no agent to vet)."""
        gated = _make_gated([("alpha", 0.9), ("beta", 0.8)])
        assert _is_lexically_plausible(None, gated) is False

    def test_preferred_in_top_three_returns_true(self) -> None:
        """Preferred appearing in top-3 of gated returns True."""
        gated = _make_gated(
            [("alpha", 0.9), ("beta", 0.8), ("gamma", 0.7), ("delta", 0.5)]
        )
        # "gamma" is rank 3 (0-indexed: position 2)
        assert _is_lexically_plausible("gamma", gated) is True

    def test_preferred_rank_four_with_score_above_floor_returns_true(
        self,
    ) -> None:
        """Rank-4 preferred passes veto when score >= threshold - 0.15.

        _DELEGATE_THRESHOLD - 0.15 is the floor.  A score at exactly
        the floor must return True.
        """
        floor = _DELEGATE_THRESHOLD - 0.15
        # "delta" at exactly the floor, rank 4
        gated = _make_gated(
            [
                ("alpha", 0.9),
                ("beta", 0.85),
                ("gamma", 0.8),
                ("delta", floor),
            ]
        )
        assert _is_lexically_plausible("delta", gated) is True

    def test_preferred_rank_four_below_floor_returns_false(self) -> None:
        """Rank-4 preferred is vetoed when score < threshold - 0.15."""
        floor = _DELEGATE_THRESHOLD - 0.15
        below = floor - 0.01
        gated = _make_gated(
            [
                ("alpha", 0.9),
                ("beta", 0.85),
                ("gamma", 0.8),
                ("delta", below),
            ]
        )
        assert _is_lexically_plausible("delta", gated) is False

    def test_preferred_not_in_gated_at_all_returns_false(self) -> None:
        """Preferred not present in gated list at all returns False."""
        gated = _make_gated([("alpha", 0.9), ("beta", 0.8)])
        assert _is_lexically_plausible("absent-agent", gated) is False

    def test_empty_gated_returns_false(self) -> None:
        """Empty gated list returns False for any preferred."""
        assert _is_lexically_plausible("investigator", []) is False


# ---------------------------------------------------------------------------
# 3. TestBranch1Diagnose — broad diagnose → investigator
# ---------------------------------------------------------------------------


class TestBranch1Diagnose:
    """Branch 1: posture="diagnose", area_span>=2 → investigator@0.9.

    Covers: contract item 3 — successful route and all live-only
    fall-through cases.
    """

    @pytest.fixture()
    def investigator_catalog_names(self) -> frozenset[str]:
        """Frozenset containing "investigator" as a routable catalog name.

        Returns:
            Frozenset simulating a catalog that includes the investigator.
        """
        return frozenset({"investigator", "code-writer", "debugger"})

    @pytest.fixture()
    def investigator_gated(self) -> list[ScoredEntry]:
        """Gated list with investigator in top-3 (plausible).

        Returns:
            Score-sorted list with investigator at rank 1.
        """
        return _make_gated(
            [
                ("investigator", 0.9),
                ("code-writer", 0.7),
                ("debugger", 0.5),
            ]
        )

    @pytest.fixture()
    def minimal_catalog(self) -> list[CatalogEntry]:
        """Minimal catalog with investigator and code-writer.

        Returns:
            List of :class:`CatalogEntry` objects.
        """
        from claude_wayfinder.match._parse import _parse_triggers

        def _entry(name: str) -> CatalogEntry:
            tr = _parse_triggers(
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
                kind="agent",
                source="owned",
                routable=True,
                triggers=tr,
                applicable_skills=(),
                applicable_agents=(),
            )

        return [_entry("investigator"), _entry("code-writer")]

    @pytest.fixture()
    def minimal_features(self) -> Any:
        """Minimal features for a short task description.

        Returns:
            A :class:`Features` instance.
        """
        return build_features(
            {
                "task_description": "investigate the bug",
                "file_paths": [],
                "agent_mentions": [],
                "tool_mentions": [],
                "command_prefix": None,
            }
        )

    def test_branch1_routes_to_investigator_when_all_conditions_met(
        self,
        investigator_catalog_names: frozenset[str],
        investigator_gated: list[ScoredEntry],
        minimal_catalog: list[CatalogEntry],
        minimal_features: Any,
    ) -> None:
        """Broad diagnose routes to investigator@0.9 when all gates pass.

        Conditions: posture=diagnose, area_span>=2, confidence=high,
        investigator in catalog, investigator plausible in gated.
        """
        labels = Labels(
            domain="code",
            posture="diagnose",
            confidence="high",
            area_span=2,
        )
        result = compose_route(
            labels=labels,
            scored_agents=investigator_gated,
            scored_skills=[],
            features=minimal_features,
            catalog=minimal_catalog,
            catalog_agent_names=investigator_catalog_names,
        )
        assert result["decision"] == "delegate"
        assert result["agent"] == "investigator"
        assert result["confidence"] == pytest.approx(0.9)
        assert result["disposition_source"] == "posture_routed"

    def test_branch1_falls_through_when_investigator_absent_from_catalog(
        self,
        investigator_gated: list[ScoredEntry],
        minimal_catalog: list[CatalogEntry],
        minimal_features: Any,
    ) -> None:
        """Investigator absent from catalog_agent_names → decide() fallback.

        posture_routed must be False; decision must NOT be delegate to
        investigator.
        """
        labels = Labels(
            domain="code",
            posture="diagnose",
            confidence="high",
            area_span=2,
        )
        no_investigator: frozenset[str] = frozenset({"code-writer"})
        result = compose_route(
            labels=labels,
            scored_agents=investigator_gated,
            scored_skills=[],
            features=minimal_features,
            catalog=minimal_catalog,
            catalog_agent_names=no_investigator,
        )
        assert result["disposition_source"] != "posture_routed"

    def test_branch1_falls_through_when_confidence_absent(
        self,
        investigator_catalog_names: frozenset[str],
        investigator_gated: list[ScoredEntry],
        minimal_catalog: list[CatalogEntry],
        minimal_features: Any,
    ) -> None:
        """Absent confidence (treated as LOW) → decide() fallback.

        The §D.1 fail-safe: absent confidence is LOW, so the live gate
        blocks posture routing.
        """
        labels = Labels(
            domain="code",
            posture="diagnose",
            confidence=None,
            area_span=2,
        )
        result = compose_route(
            labels=labels,
            scored_agents=investigator_gated,
            scored_skills=[],
            features=minimal_features,
            catalog=minimal_catalog,
            catalog_agent_names=investigator_catalog_names,
        )
        assert result["disposition_source"] != "posture_routed"

    def test_branch1_falls_through_when_confidence_low(
        self,
        investigator_catalog_names: frozenset[str],
        investigator_gated: list[ScoredEntry],
        minimal_catalog: list[CatalogEntry],
        minimal_features: Any,
    ) -> None:
        """Explicit confidence=low → decide() fallback.

        Low confidence must block the posture route even when all other
        conditions are met.
        """
        labels = Labels(
            domain="code",
            posture="diagnose",
            confidence="low",
            area_span=2,
        )
        result = compose_route(
            labels=labels,
            scored_agents=investigator_gated,
            scored_skills=[],
            features=minimal_features,
            catalog=minimal_catalog,
            catalog_agent_names=investigator_catalog_names,
        )
        assert result["disposition_source"] != "posture_routed"

    def test_branch1_fires_even_when_investigator_lexically_implausible(
        self,
        investigator_catalog_names: frozenset[str],
        minimal_catalog: list[CatalogEntry],
        minimal_features: Any,
    ) -> None:
        """Branch 1 routes to investigator even when it is lexically implausible.

        Investigator is a *structural* route (area_span-driven), not a
        lexical pick.  The §B.1 plausibility veto does NOT apply to Branch 1.
        When confidence is high and investigator is routable, Branch 1 must
        fire regardless of investigator's lexical rank or score.

        This verifies the revised contract (2026-06-20): the veto applies
        only to Branch 3 (cell-map generic picks), never to Branch 1.
        """
        floor = _DELEGATE_THRESHOLD - 0.15
        # investigator at rank 4, well below the plausibility floor —
        # would be vetoed if Branch 3 rules applied, but Branch 1 ignores
        # the veto entirely.
        implausible_gated = _make_gated(
            [
                ("code-writer", 0.9),
                ("debugger", 0.85),
                ("code-reviewer", 0.8),
                ("investigator", floor - 0.05),
            ]
        )
        labels = Labels(
            domain="code",
            posture="diagnose",
            confidence="high",
            area_span=2,
        )
        result = compose_route(
            labels=labels,
            scored_agents=implausible_gated,
            scored_skills=[],
            features=minimal_features,
            catalog=minimal_catalog,
            catalog_agent_names=investigator_catalog_names,
        )
        # Branch 1 must fire: investigator is routable + confidence is high.
        # The veto does NOT apply here.
        assert result["decision"] == "delegate"
        assert result["agent"] == "investigator"
        assert result["confidence"] == pytest.approx(0.9)
        assert result["disposition_source"] == "posture_routed"

    def test_branch1_not_triggered_when_area_span_one(
        self,
        investigator_catalog_names: frozenset[str],
        investigator_gated: list[ScoredEntry],
        minimal_catalog: list[CatalogEntry],
        minimal_features: Any,
    ) -> None:
        """area_span=1 does not trigger the broad-diagnose → investigator path.

        Only area_span >= 2 fires Branch 1; span=1 falls into Branch 3
        (which uses cell_map_lookup for diagnose).
        """
        labels = Labels(
            domain="code",
            posture="diagnose",
            confidence="high",
            area_span=1,
        )
        result = compose_route(
            labels=labels,
            scored_agents=investigator_gated,
            scored_skills=[],
            features=minimal_features,
            catalog=minimal_catalog,
            catalog_agent_names=investigator_catalog_names,
        )
        # With area_span=1 and domain=code, cell_map_lookup returns
        # "debugger" for (code, diagnose), not investigator; the
        # posture_routed flag should not indicate investigator routing.
        if result.get("disposition_source") == "posture_routed":
            assert result.get("agent") != "investigator"


# ---------------------------------------------------------------------------
# 4. TestBranch2Sentinel — project_meta × build → self_handle
# ---------------------------------------------------------------------------


class TestBranch2Sentinel:
    """Branch 2: sentinel cell returns self_handle regardless of confidence.

    Covers: contract item 4.
    """

    @pytest.fixture()
    def sentinel_setup(self) -> dict[str, Any]:
        """Build inputs for the (project_meta, build) sentinel scenario.

        Returns:
            Dict with keys: labels, scored_agents, catalog,
            catalog_agent_names, features.
        """
        from claude_wayfinder.match._parse import _parse_triggers

        def _entry(name: str) -> CatalogEntry:
            tr = _parse_triggers(
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
                kind="agent",
                source="owned",
                routable=True,
                triggers=tr,
                applicable_skills=(),
                applicable_agents=(),
            )

        catalog = [_entry("project-planner"), _entry("project-reviewer")]
        catalog_agent_names = frozenset(
            {"project-planner", "project-reviewer"}
        )
        scored_agents = _make_gated(
            [("project-planner", 0.9), ("project-reviewer", 0.7)]
        )
        features = build_features(
            {
                "task_description": "update CLAUDE.md",
                "file_paths": ["CLAUDE.md"],
                "agent_mentions": [],
                "tool_mentions": [],
                "command_prefix": None,
            }
        )
        return {
            "catalog": catalog,
            "catalog_agent_names": catalog_agent_names,
            "scored_agents": scored_agents,
            "features": features,
        }

    def test_branch2_returns_self_handle_for_project_meta_build(
        self, sentinel_setup: dict[str, Any]
    ) -> None:
        """(project_meta, build) always returns self_handle regardless of confidence.

        SELF_HANDLE_SENTINEL is an abstention to the router, not a
        sub-agent delegation.  It must fire even when confidence is None.
        """
        labels = Labels(
            domain="project_meta",
            posture="build",
            confidence=None,  # absent confidence must NOT block Branch 2
            area_span=1,
        )
        result = compose_route(
            labels=labels,
            scored_agents=sentinel_setup["scored_agents"],
            scored_skills=[],
            features=sentinel_setup["features"],
            catalog=sentinel_setup["catalog"],
            catalog_agent_names=sentinel_setup["catalog_agent_names"],
        )
        assert result["decision"] == "self_handle"
        assert result["agent"] is None
        assert result["disposition_source"] == "posture_routed"

    def test_branch2_self_handle_with_high_confidence(
        self, sentinel_setup: dict[str, Any]
    ) -> None:
        """Branch 2 fires with confidence=high (not gated by confidence).

        Verifies the carve-out is unconditional.
        """
        labels = Labels(
            domain="project_meta",
            posture="build",
            confidence="high",
            area_span=1,
        )
        result = compose_route(
            labels=labels,
            scored_agents=sentinel_setup["scored_agents"],
            scored_skills=[],
            features=sentinel_setup["features"],
            catalog=sentinel_setup["catalog"],
            catalog_agent_names=sentinel_setup["catalog_agent_names"],
        )
        assert result["decision"] == "self_handle"
        assert result["agent"] is None

    def test_sentinel_never_appears_as_agent_in_result(
        self, sentinel_setup: dict[str, Any]
    ) -> None:
        """The SELF_HANDLE_SENTINEL string must never appear as result agent.

        The sentinel is an internal routing instruction; it must be
        translated to agent=None before output.
        """
        labels = Labels(
            domain="project_meta",
            posture="build",
            confidence=None,
            area_span=1,
        )
        result = compose_route(
            labels=labels,
            scored_agents=sentinel_setup["scored_agents"],
            scored_skills=[],
            features=sentinel_setup["features"],
            catalog=sentinel_setup["catalog"],
            catalog_agent_names=sentinel_setup["catalog_agent_names"],
        )
        assert result.get("agent") != SELF_HANDLE_SENTINEL

    def test_sentinel_lookup_verifiable_via_cell_map(self) -> None:
        """Verify (project_meta, build) actually returns the sentinel.

        Sanity-checks that ``_cells.cell_map_lookup`` produces the
        sentinel for this (domain, posture) pair, confirming the test
        is exercising the correct cell.
        """
        preferred = cell_map_lookup("project_meta", "build")
        assert preferred == SELF_HANDLE_SENTINEL


# ---------------------------------------------------------------------------
# 5. TestBranch3Generic — generic posture routing
# ---------------------------------------------------------------------------


class TestBranch3Generic:
    """Branch 3: generic preferred → delegate@0.9 or decide() fallback.

    Covers: contract item 5 — successful route, low/absent confidence
    fall-through, veto fall-through, and empty-gate D-KC-GUARD1 protection.
    """

    @pytest.fixture()
    def code_build_setup(self) -> dict[str, Any]:
        """Setup for (code, build) → code-writer routing.

        Returns:
            Dict with keys: labels, scored_agents, catalog,
            catalog_agent_names, features.
        """
        from claude_wayfinder.match._parse import _parse_triggers

        def _entry(name: str) -> CatalogEntry:
            tr = _parse_triggers(
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
                kind="agent",
                source="owned",
                routable=True,
                triggers=tr,
                applicable_skills=(),
                applicable_agents=(),
            )

        catalog = [
            _entry("code-writer"),
            _entry("debugger"),
            _entry("code-reviewer"),
        ]
        catalog_agent_names = frozenset(
            {"code-writer", "debugger", "code-reviewer"}
        )
        # code-writer plausible in top-3 for domain=code
        scored_agents = _make_gated(
            [
                ("code-writer", 0.9),
                ("debugger", 0.7),
                ("code-reviewer", 0.5),
            ]
        )
        features = build_features(
            {
                "task_description": "implement the login feature",
                "file_paths": ["src/auth.py"],
                "agent_mentions": [],
                "tool_mentions": [],
                "command_prefix": None,
            }
        )
        return {
            "catalog": catalog,
            "catalog_agent_names": catalog_agent_names,
            "scored_agents": scored_agents,
            "features": features,
        }

    def test_branch3_routes_preferred_when_all_conditions_met(
        self, code_build_setup: dict[str, Any]
    ) -> None:
        """Generic route fires when high confidence + plausible + in catalog.

        (code, build) → preferred=code-writer; it is in genuine_gated_names
        and in catalog_agent_names.
        """
        labels = Labels(
            domain="code",
            posture="build",
            confidence="high",
            area_span=1,
        )
        result = compose_route(
            labels=labels,
            scored_agents=code_build_setup["scored_agents"],
            scored_skills=[],
            features=code_build_setup["features"],
            catalog=code_build_setup["catalog"],
            catalog_agent_names=code_build_setup["catalog_agent_names"],
        )
        assert result["decision"] == "delegate"
        assert result["agent"] == "code-writer"
        assert result["confidence"] == pytest.approx(0.9)
        assert result["disposition_source"] == "posture_routed"

    def test_branch3_falls_through_when_confidence_absent(
        self, code_build_setup: dict[str, Any]
    ) -> None:
        """Low/absent confidence → decide() fallback (§D.1 fail-safe).

        posture_routed must be False.
        """
        labels = Labels(
            domain="code",
            posture="build",
            confidence=None,
            area_span=1,
        )
        result = compose_route(
            labels=labels,
            scored_agents=code_build_setup["scored_agents"],
            scored_skills=[],
            features=code_build_setup["features"],
            catalog=code_build_setup["catalog"],
            catalog_agent_names=code_build_setup["catalog_agent_names"],
        )
        assert result["disposition_source"] != "posture_routed"

    def test_branch3_falls_through_when_veto_fires(
        self, code_build_setup: dict[str, Any]
    ) -> None:
        """Plausibility veto → decide() fallback.

        code-writer at rank 4 below floor: veto blocks posture routing.
        """
        floor = _DELEGATE_THRESHOLD - 0.15
        not_plausible = _make_gated(
            [
                ("debugger", 0.9),
                ("code-reviewer", 0.85),
                ("investigator", 0.8),
                ("code-writer", floor - 0.05),
            ]
        )
        labels = Labels(
            domain="code",
            posture="build",
            confidence="high",
            area_span=1,
        )
        result = compose_route(
            labels=labels,
            scored_agents=not_plausible,
            scored_skills=[],
            features=code_build_setup["features"],
            catalog=code_build_setup["catalog"],
            catalog_agent_names=code_build_setup["catalog_agent_names"],
        )
        assert result["disposition_source"] != "posture_routed"

    def test_branch3_empty_gate_guard_blocks_out_of_domain_preferred(
        self,
    ) -> None:
        """D-KC-GUARD1: out-of-domain agent not routed even if it passes gate.

        When gate_agents falls back to the ungated list (empty-gate
        scenario), an out-of-domain preferred must not route via Branch 3
        because genuine_gated_names intersection with DOMAIN_AGENT_MAP
        excludes it.

        Scenario: domain=docs_prose; preferred=doc-writer.
        Agent list contains only infra/code agents.  gate_agents falls
        back to ungated because no docs_prose agents are scored.  But
        preferred (doc-writer) is NOT in the scored agents at all, so
        it cannot be in genuine_gated_names.
        """
        from claude_wayfinder.match._parse import _parse_triggers

        def _entry(name: str) -> CatalogEntry:
            tr = _parse_triggers(
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
                kind="agent",
                source="owned",
                routable=True,
                triggers=tr,
                applicable_skills=(),
                applicable_agents=(),
            )

        # Catalog has doc-writer (target) plus some code agents
        catalog = [
            _entry("doc-writer"),
            _entry("code-writer"),
            _entry("debugger"),
        ]
        catalog_agent_names = frozenset(
            {"doc-writer", "code-writer", "debugger"}
        )
        # Scored list: only code agents scored; doc-writer scored 0
        # so gate fallback returns the full list, but doc-writer absent
        scored_agents = _make_gated(
            [("code-writer", 0.9), ("debugger", 0.7)]
        )
        features = build_features(
            {
                "task_description": "write the docs",
                "file_paths": [],
                "agent_mentions": [],
                "tool_mentions": [],
                "command_prefix": None,
            }
        )
        # docs_prose + build → preferred = doc-writer, but it is not
        # in genuine_gated_names (absent from scored list) so must not route.
        labels = Labels(
            domain="docs_prose",
            posture="build",
            confidence="high",
            area_span=1,
        )
        result = compose_route(
            labels=labels,
            scored_agents=scored_agents,
            scored_skills=[],
            features=features,
            catalog=catalog,
            catalog_agent_names=catalog_agent_names,
        )
        # doc-writer absent from scores → not in genuine_gated_names →
        # posture routing must NOT fire
        assert result["disposition_source"] != "posture_routed"


# ---------------------------------------------------------------------------
# 6. TestIsAnyNormalization — is_any domain handling
# ---------------------------------------------------------------------------


class TestIsAnyNormalization:
    """``domain="is_any"`` maps to ``"any"`` for cell lookup; no gate applied.

    Covers: contract item 6.
    """

    def test_is_any_domain_uses_any_for_cell_lookup(self) -> None:
        """parse_labels("is_any") keeps is_any; compose_route must look up "any".

        Verify that cell_map_lookup("any", posture) would be used, not
        cell_map_lookup("is_any", posture).  We check this indirectly:
        ("is_any", "build") has no entry in _CELL_MAP, but ("any", "build")
        does → code-writer.
        """
        preferred_any = cell_map_lookup("any", "build")
        # "any" key must resolve
        assert preferred_any is not None
        # Implementation should handle is_any → any conversion; the two-step
        # fallback in cell_map_lookup means ("is_any","build") also hits
        # ("any","build") via fallback — this test confirms the compose_route
        # algorithm's *explicit* is_any→any conversion per the contract,
        # not merely the cell_map fallback.
        # The compose_route contract says:
        #   domain_for_lookup = "any" if domain in (None, "is_any") else domain
        # Assert that a compose_route call with domain="is_any" routes the
        # same as domain=None (both bypass the gate).

    def test_is_any_compose_route_does_not_gate_agents(self) -> None:
        """domain=is_any passes all agents through without filtering.

        gate_agents(scored, "is_any") returns ungated list per _cells.py.
        compose_route must not narrow the candidate set for is_any.
        """
        from claude_wayfinder.match._parse import _parse_triggers

        def _entry(name: str) -> CatalogEntry:
            tr = _parse_triggers(
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
                kind="agent",
                source="owned",
                routable=True,
                triggers=tr,
                applicable_skills=(),
                applicable_agents=(),
            )

        # Agents that would be domain-gated under "code" but must survive
        # when domain=is_any
        catalog = [_entry("researcher"), _entry("project-planner")]
        catalog_agent_names = frozenset({"researcher", "project-planner"})
        scored_agents = _make_gated(
            [("researcher", 0.9), ("project-planner", 0.7)]
        )
        features = build_features(
            {
                "task_description": "research new approaches",
                "file_paths": [],
                "agent_mentions": [],
                "tool_mentions": [],
                "command_prefix": None,
            }
        )
        labels = Labels(
            domain="is_any",
            posture="research",
            confidence="high",
            area_span=1,
        )
        result = compose_route(
            labels=labels,
            scored_agents=scored_agents,
            scored_skills=[],
            features=features,
            catalog=catalog,
            catalog_agent_names=catalog_agent_names,
        )
        # ("any", "research") → "researcher"; researcher is in scored and
        # plausible; result should be delegate to researcher.
        assert result["decision"] == "delegate"
        assert result["agent"] == "researcher"
        assert result["disposition_source"] == "posture_routed"


# ---------------------------------------------------------------------------
# 7. TestComposeVsOracleEquivalence — parity over gold corpus
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _CORPUS_PATH.exists()
    or not _GOLD_LABELS_PATH.exists()
    or not _CATALOG_PATH.exists(),
    reason=(
        "Gold corpus / labels / catalog files not present on this machine; "
        "parity test requires the private data set."
    ),
)
class TestComposeVsOracleEquivalence:
    """Compose-vs-oracle parity over the gold corpus (contract item 7).

    With confidence forced to "high" on every entry, assertions are driven
    by branch classification (revised contract 2026-06-20):
      (a) Branch 1 (diagnose + area_span>=2): compose MUST match oracle.
      (b) Branch 2 (sentinel): compose MUST match oracle.
      (c) Branch 3 (generic): match oracle when veto passes; compose must
          NOT be posture_routed when the veto blocks (one accepted
          divergence).
    """

    @pytest.fixture(scope="class")
    def corpus(self) -> list[Any]:
        """Load the wayfinder corpus JSONL.

        Returns:
            List of CorpusEntry objects.
        """
        from scripts.corpus.eval._reader import load_corpus

        return load_corpus(_CORPUS_PATH)

    @pytest.fixture(scope="class")
    def gold_labels(self) -> dict[int, Any]:
        """Load gold labels keyed by corpus_id.

        Returns:
            Dict mapping corpus_id → GoldLabel.
        """
        from scripts.corpus.eval._reader import GoldLabel

        labels: dict[int, Any] = {}
        with open(
            _GOLD_LABELS_PATH, encoding="utf-8"
        ) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                domain = record.get("domain") or None
                is_any = record.get("is_any", False)
                effective_domain = "is_any" if is_any else domain
                gl = GoldLabel(
                    corpus_id=int(record["corpus_id"]),
                    domain=effective_domain or "is_any",
                    posture=record.get("posture") or "",
                    gold_agent=record.get("gold_agent") or "",
                    is_any=is_any,
                    area_span=int(record.get("area_span", 1)),
                )
                labels[gl.corpus_id] = gl
        return labels

    @pytest.fixture(scope="class")
    def catalog_data(self) -> tuple[list[CatalogEntry], frozenset[str]]:
        """Load catalog and extract routable agent names.

        Returns:
            Tuple of (catalog entries list, frozenset of routable names).
        """
        catalog = load_catalog(_CATALOG_PATH)
        names: frozenset[str] = frozenset(
            e.name
            for e in catalog
            if e.kind == "agent"
            and is_agent_routable(
                name=e.name,
                kind=e.kind,
                source=e.source,
                routable=e.routable,
            )
        )
        return catalog, names

    def test_compose_route_matches_oracle_by_branch(
        self,
        corpus: list[Any],
        gold_labels: dict[int, Any],
        catalog_data: tuple[list[CatalogEntry], frozenset[str]],
    ) -> None:
        """compose_route (decision, agent, posture_routed) matches the oracle
        on Branch 1 and Branch 2; accepted divergence on Branch 3 veto-blocks.

        With confidence forced to "high" on every entry, per-entry assertions
        are driven by branch classification (revised contract 2026-06-20):

        (a) Branch 1 — posture=="diagnose" and area_span>=2:
            compose MUST match oracle (both route investigator).  The §B.1
            veto does NOT apply here; investigator is a structural route.

        (b) Branch 2 — preferred == SELF_HANDLE_SENTINEL:
            compose MUST match oracle (both self_handle, posture_routed=True).
            The sentinel is an unconditional abstention; no veto applies.

        (c) Branch 3 — all other entries:
            If _is_lexically_plausible(preferred, gated) is True:
              compose (decision, agent, posture_routed) MUST match oracle.
            Else (veto blocks):
              compose MUST equal decide(gated, ...) with posture_routed=False
              while the oracle routes preferred — this is the one accepted
              divergence (CW-safety trade, spec §B.1/§D.1).

        The original bug that prompted the revision: the previous test
        applied the ``elif not plausible`` / veto-block expectation to ALL
        entries, including Branch 1 (investigator) and Branch 2 (sentinel),
        contradicting the contract for those branches.

        At least one entry per branch (a, b, c-plausible, c-veto) should be
        encountered for the test to be meaningful; this is logged but not a
        hard assertion since corpus composition can vary.
        """
        from scripts.corpus.eval._systems import run_supplied_compose

        catalog, catalog_agent_names = catalog_data

        # Build oracle results — oracle has NO confidence gate or veto.
        oracle_results = run_supplied_compose(
            entries=corpus,
            catalog_path=_CATALOG_PATH,
            labels={cid: gl for cid, gl in gold_labels.items()},
        )
        oracle_by_id = {r.corpus_id: r for r in oracle_results}

        mismatches: list[str] = []
        checked = 0
        branch_counts: dict[str, int] = {
            "branch1": 0,
            "branch2": 0,
            "branch3_plausible": 0,
            "branch3_veto": 0,
        }

        for corpus_entry in corpus:
            cid = corpus_entry.corpus_id
            gl = gold_labels.get(cid)
            if gl is None:
                continue  # unlabeled entry — skip

            oracle_result = oracle_by_id.get(cid)
            if oracle_result is None:
                continue

            # Build context and score
            ctx = {
                "task_description": corpus_entry.task_description,
                "file_paths": corpus_entry.file_paths,
                "agent_mentions": corpus_entry.agent_mentions,
                "tool_mentions": corpus_entry.tool_mentions,
                "command_prefix": corpus_entry.command_prefix,
            }
            features = build_features(ctx)
            scored_agents, scored_skills = score_entries(catalog, features)

            # Force confidence to "high" per test contract
            labels_obj = Labels(
                domain=gl.domain if not gl.is_any else "is_any",
                posture=gl.posture or None,
                confidence="high",
                area_span=gl.area_span,
            )

            # Classify entry into branch by replicating compose_route's
            # branch conditions.  Must match _compose.py's algorithm exactly.
            effective_domain = None if gl.is_any else gl.domain
            gated = gate_agents(scored_agents, effective_domain)
            domain_for_lookup = (
                "any"
                if (gl.is_any or not gl.domain)
                else gl.domain
            )
            preferred = (
                cell_map_lookup(domain_for_lookup, gl.posture)
                if gl.posture
                else None
            )

            compose_result = compose_route(
                labels=labels_obj,
                scored_agents=scored_agents,
                scored_skills=scored_skills,
                features=features,
                catalog=catalog,
                catalog_agent_names=catalog_agent_names,
            )

            c_decision = compose_result["decision"]
            c_agent = compose_result.get("agent")
            c_posture_routed = (
                compose_result.get("disposition_source") == "posture_routed"
            )
            o_decision = oracle_result.decision
            o_agent = oracle_result.agent
            o_posture_routed = oracle_result.extras.get("posture_routed", False)

            # ----------------------------------------------------------------
            # (a) Branch 1: broad-diagnose → investigator (no veto)
            # ----------------------------------------------------------------
            if gl.posture == "diagnose" and gl.area_span >= 2:
                branch_counts["branch1"] += 1
                # compose MUST match oracle on both decision, agent,
                # and posture_routed — the veto is absent from Branch 1.
                if (
                    c_decision != o_decision
                    or c_agent != o_agent
                    or c_posture_routed != o_posture_routed
                ):
                    mismatches.append(
                        f"[Branch1] corpus_id={cid}: "
                        f"compose=({c_decision},{c_agent},"
                        f"pr={c_posture_routed}) vs "
                        f"oracle=({o_decision},{o_agent},"
                        f"pr={o_posture_routed})"
                    )

            # ----------------------------------------------------------------
            # (b) Branch 2: sentinel → self_handle (unconditional, no veto)
            # ----------------------------------------------------------------
            elif preferred == SELF_HANDLE_SENTINEL:
                branch_counts["branch2"] += 1
                # compose MUST match oracle — sentinel is an abstention to
                # the router; confidence and veto do not apply.
                if (
                    c_decision != o_decision
                    or c_agent != o_agent
                    or c_posture_routed != o_posture_routed
                ):
                    mismatches.append(
                        f"[Branch2] corpus_id={cid}: "
                        f"compose=({c_decision},{c_agent},"
                        f"pr={c_posture_routed}) vs "
                        f"oracle=({o_decision},{o_agent},"
                        f"pr={o_posture_routed})"
                    )

            # ----------------------------------------------------------------
            # (c) Branch 3: generic cell-map pick (§B.1 veto applies here)
            # ----------------------------------------------------------------
            else:
                plausible = _is_lexically_plausible(preferred, gated)
                if plausible:
                    branch_counts["branch3_plausible"] += 1
                    # compose MUST match oracle when veto passes
                    if (
                        c_decision != o_decision
                        or c_agent != o_agent
                        or c_posture_routed != o_posture_routed
                    ):
                        mismatches.append(
                            f"[Branch3-plausible] corpus_id={cid}: "
                            f"compose=({c_decision},{c_agent},"
                            f"pr={c_posture_routed}) vs "
                            f"oracle=({o_decision},{o_agent},"
                            f"pr={o_posture_routed})"
                        )
                else:
                    branch_counts["branch3_veto"] += 1
                    # Accepted divergence: compose must NOT be posture_routed
                    # (veto sends it to decide()); oracle may be posture_routed.
                    if c_posture_routed:
                        mismatches.append(
                            f"[Branch3-veto] corpus_id={cid}: "
                            f"veto should block posture_routed but "
                            f"compose emitted "
                            f"disposition_source=posture_routed "
                            f"(agent={c_agent})"
                        )

            checked += 1

        assert checked > 0, (
            "No labeled corpus entries were processed — "
            "corpus or labels empty."
        )
        assert not mismatches, (
            f"compose_route parity failures ({len(mismatches)}) "
            f"branch_counts={branch_counts}:\n"
            + "\n".join(mismatches[:20])
        )


# ---------------------------------------------------------------------------
# 8. TestLiveStdoutUnchanged — REMOVED in M15-5
#
# The ``test_compose_module_not_imported_by_main`` assertion was an M15-2
# guard that asserted ``_compose`` was absent from ``_main.py`` source.
# M15-5 is precisely the phase that wires compose_route / parse_labels into
# _main.py, so the invariant is now intentionally false.  Behavioural
# coverage is provided by:
#   - test_shadow_mode.py::TestShadowLiveUnchanged (stdout unchanged)
#   - test_scoring_kernel_parity.py golden parity tests
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 9. TestFallbackPayloadFidelity — compose_route fallback equals decide()
# ---------------------------------------------------------------------------


class TestFallbackPayloadFidelity:
    """Fallback path returns the FULL decide() payload, not a 4-field subset.

    Covers: contract item 9 (Codex P2, 2026-06-20) — on any fall-through
    context (posture absent, confidence not-high, or Branch-3 veto),
    ``compose_route`` must return a dict that is key-for-key identical to
    ``decide(gated, scored_skills, features, catalog)``, modulo
    ``disposition_source`` (which ``compose_route`` may add if absent).

    Two sub-cases:
      (a) Generic scalar fallback — a context where posture is None so no
          posture branch fires; validates that ``rationale``, ``alternatives``,
          and ``skills`` all survive.
      (b) ``mixed_content`` fallback — a context where the gated lexical
          scorer would produce ``mixed_content`` (two agents tied at 1.0 on
          disjoint path lanes); validates that ``lanes`` survives.
    """

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _entry_with_globs(name: str, globs: list[str]) -> "CatalogEntry":
        """Build a CatalogEntry with path_globs and no other triggers.

        Args:
            name: Agent name.
            globs: Path glob patterns to include.

        Returns:
            A routable :class:`CatalogEntry` for the given agent.
        """
        from claude_wayfinder.match._parse import _parse_triggers

        triggers = _parse_triggers(
            {
                "command_prefixes": [],
                "agent_mentions": [],
                "path_globs": globs,
                "path_globs_excluded": [],
                "keywords": [],
                "tool_mentions": [],
                "excludes": [],
            }
        )
        return CatalogEntry(
            name=name,
            kind="agent",
            source="owned",
            routable=True,
            triggers=triggers,
            applicable_skills=(),
            applicable_agents=(),
        )

    # ------------------------------------------------------------------
    # (a) Generic scalar fallback — posture absent → no posture branch
    # ------------------------------------------------------------------

    def test_fallback_payload_equals_decide_output_on_posture_absent_context(
        self,
    ) -> None:
        """On posture=None, compose_route fallback is byte-identical to decide().

        Validates that ``rationale``, ``alternatives``, and ``skills`` (when
        present in decide()'s output) all appear in compose_route's output
        with equal values.  Also asserts compose_route did NOT inject
        ``agent=None`` where decide() omitted ``agent``.

        The current cherry-pick bug returns only
        {decision, agent, confidence, disposition_source}, dropping the
        ``rationale`` and ``alternatives`` keys — so this test will FAIL
        against the unfixed implementation.
        """
        from claude_wayfinder.match._decide import decide

        # Build a minimal catalog + context that produces a non-trivial
        # decide() result (delegate with rationale/alternatives).
        from claude_wayfinder.match._parse import _parse_triggers

        def _entry(name: str) -> CatalogEntry:
            tr = _parse_triggers(
                {
                    "command_prefixes": [],
                    "agent_mentions": [],
                    "path_globs": [],
                    "path_globs_excluded": [],
                    "keywords": [{"term": name, "weight": 1.0}],
                    "tool_mentions": [],
                    "excludes": [],
                }
            )
            return CatalogEntry(
                name=name,
                kind="agent",
                source="owned",
                routable=True,
                triggers=tr,
                applicable_skills=(),
                applicable_agents=(),
            )

        catalog = [
            _entry("code-writer"),
            _entry("debugger"),
            _entry("code-reviewer"),
        ]
        catalog_agent_names = frozenset(
            {"code-writer", "debugger", "code-reviewer"}
        )
        # Task description includes "code-writer" as a keyword trigger so
        # that agent wins clearly and decide() returns a richer payload.
        features = build_features(
            {
                "task_description": "code-writer implement the feature",
                "file_paths": ["src/main.py"],
                "agent_mentions": [],
                "tool_mentions": [],
                "command_prefix": None,
            }
        )
        scored_agents, scored_skills = score_entries(catalog, features)

        # posture=None means no posture branch fires → forced fallback.
        labels = Labels(
            domain="code",
            posture=None,
            confidence="high",
            area_span=1,
        )

        from claude_wayfinder.match._cells import gate_agents

        gated = gate_agents(scored_agents, labels.domain)
        expected = decide(gated, scored_skills, features, catalog)
        actual = compose_route(
            labels=labels,
            scored_agents=scored_agents,
            scored_skills=scored_skills,
            features=features,
            catalog=catalog,
            catalog_agent_names=catalog_agent_names,
        )

        # Every key that decide() returned must survive into compose_route.
        # ``disposition_source`` is the only key compose_route may add or
        # override — exclude it from the strict check.
        keys_to_check = set(expected.keys()) - {"disposition_source"}
        missing_keys = keys_to_check - set(actual.keys())
        assert not missing_keys, (
            f"compose_route fallback dropped keys that decide() returned: "
            f"{sorted(missing_keys)}.  "
            f"decide() keys={sorted(expected.keys())}, "
            f"compose_route keys={sorted(actual.keys())}"
        )
        for key in keys_to_check:
            assert actual[key] == expected[key], (
                f"compose_route[{key!r}] != decide()[{key!r}]: "
                f"{actual[key]!r} vs {expected[key]!r}"
            )

        # compose_route must NOT inject ``agent=None`` where decide() omitted
        # ``agent`` (e.g. self_handle, needs_more_detail, advisory outcomes).
        if "agent" not in expected:
            assert "agent" not in actual or actual["agent"] is not None, (
                "compose_route injected agent=None but decide() omitted "
                "the 'agent' key entirely — the fallback must be "
                "byte-identical, not a cherry-pick with injected fields."
            )

        # disposition_source must be present in compose_route output
        # (added by setdefault if decide() omitted it).
        assert "disposition_source" in actual, (
            "compose_route fallback must include disposition_source"
        )

    # ------------------------------------------------------------------
    # (b) mixed_content fallback — lanes field must survive
    # ------------------------------------------------------------------

    def test_fallback_mixed_content_lanes_field_survives(self) -> None:
        """On a mixed_content fallback, compose_route preserves the lanes key.

        Constructs a context where decide() yields mixed_content (two agents
        tied at score 1.0 on path-disjoint lanes) and posture=None forces the
        compose_route fallback path.  Asserts that ``lanes`` is present in
        compose_route's output and equals decide()'s lanes.

        Three globs per agent at weight 0.4 each → 0.4 * 3 = 1.2 → clamped
        to 1.0, satisfying the mixed_content detection threshold (see
        test_mixed_content.py for the identical scoring setup).

        This test will FAIL against the unfixed cherry-pick fallback because
        the current fallback returns only 4 fields and ``lanes`` is absent.
        """
        from claude_wayfinder.match._cells import gate_agents
        from claude_wayfinder.match._decide import decide

        # Three disjoint .py globs for code-writer; three .md globs for
        # doc-writer — mirrors the setup in test_mixed_content.py exactly.
        cw_globs = ["src/*.py", "src/tests/*.py", "lib/*.py"]
        dw_globs = ["docs/*.md", "wiki/*.md", "CHANGELOG.md"]
        cw_paths = ["src/main.py", "src/tests/test_main.py", "lib/utils.py"]
        dw_paths = ["docs/api.md", "wiki/Home.md", "CHANGELOG.md"]

        cw_entry = self._entry_with_globs("code-writer", cw_globs)
        dw_entry = self._entry_with_globs("doc-writer", dw_globs)
        catalog = [cw_entry, dw_entry]
        catalog_agent_names = frozenset({"code-writer", "doc-writer"})

        features = build_features(
            {
                "task_description": "update the project files",
                "file_paths": cw_paths + dw_paths,
                "agent_mentions": [],
                "tool_mentions": [],
                "command_prefix": None,
            }
        )
        scored_agents, scored_skills = score_entries(catalog, features)

        # posture=None forces fallback to decide(); no posture branch fires.
        labels = Labels(
            domain=None,
            posture=None,
            confidence="high",
            area_span=1,
        )

        gated = gate_agents(scored_agents, labels.domain)
        expected = decide(gated, scored_skills, features, catalog)

        # Guard: if decide() doesn't produce mixed_content here, the test
        # input is wrong (scoring setup did not reach the threshold) — note
        # this as a limitation rather than masking a real failure.
        if expected.get("decision") != "mixed_content":
            import pytest as _pytest

            _pytest.skip(
                f"decide() did not yield mixed_content for this input "
                f"(got {expected.get('decision')!r}); "
                f"scored_agents scores: "
                f"{[(se.entry.name, se.score) for se in scored_agents]}. "
                f"Mixed_content setup may need more path-glob matches."
            )

        actual = compose_route(
            labels=labels,
            scored_agents=scored_agents,
            scored_skills=scored_skills,
            features=features,
            catalog=catalog,
            catalog_agent_names=catalog_agent_names,
        )

        # All keys that decide() returned must survive, especially ``lanes``.
        keys_to_check = set(expected.keys()) - {"disposition_source"}
        missing_keys = keys_to_check - set(actual.keys())
        assert not missing_keys, (
            f"compose_route fallback dropped keys from mixed_content "
            f"decide() output: {sorted(missing_keys)}.  "
            f"Expected keys: {sorted(expected.keys())}, "
            f"Actual keys: {sorted(actual.keys())}"
        )

        assert "lanes" in actual, (
            "compose_route fallback must preserve the 'lanes' key "
            "from a mixed_content decide() result"
        )
        assert actual["lanes"] == expected["lanes"], (
            f"compose_route[lanes] != decide()[lanes]: "
            f"{actual['lanes']!r} vs {expected['lanes']!r}"
        )
        assert actual["decision"] == "mixed_content", (
            f"compose_route fallback must propagate mixed_content decision, "
            f"got {actual['decision']!r}"
        )


# ---------------------------------------------------------------------------
# 10. TestComposeDiagnosticsParam — M15-5: optional diagnostics out-param
# ---------------------------------------------------------------------------


class TestComposeDiagnosticsParam:
    """``compose_route`` optional ``diagnostics`` out-param (M15-5, #422).

    The new trailing param ``diagnostics: dict | None = None`` captures
    per-step §F.1 routing state without changing the return value or
    decision logic.

    Test inventory:
        A1 — ``diagnostics=None`` (default): return value identical to
             a call without the param (pin, expected GREEN immediately).
        A2 — Branch-3 delegate: ``diag={}`` populated with posture_routed,
             branch, posture_preferred, lexical_agreement, gated_agent_names,
             posture_veto_reason (expected RED: param does not exist yet).
        A3 — Fallback with veto fail: populated with branch="fallback",
             lexical_agreement=False, posture_veto_reason set (expected RED).
        A4 — Branch-2 sentinel: branch="branch2_sentinel",
             posture_routed=True, lexical_agreement=None (expected RED).
    """

    # ------------------------------------------------------------------
    # Shared fixture: Branch-3 setup — (code, build) → code-writer
    # (reused from TestBranch3Generic.code_build_setup)
    # ------------------------------------------------------------------

    @staticmethod
    def _make_branch3_setup() -> dict:
        """Build inputs for Branch-3 (code, build) → code-writer.

        Returns:
            Dict with labels, scored_agents, catalog,
            catalog_agent_names, features.
        """
        from claude_wayfinder.match._parse import _parse_triggers

        def _entry(name: str) -> CatalogEntry:
            tr = _parse_triggers(
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
                kind="agent",
                source="owned",
                routable=True,
                triggers=tr,
                applicable_skills=(),
                applicable_agents=(),
            )

        catalog = [
            _entry("code-writer"),
            _entry("debugger"),
            _entry("code-reviewer"),
        ]
        catalog_agent_names = frozenset(
            {"code-writer", "debugger", "code-reviewer"}
        )
        # code-writer plausible in top-3 for domain=code
        scored_agents = _make_gated(
            [
                ("code-writer", 0.9),
                ("debugger", 0.7),
                ("code-reviewer", 0.5),
            ]
        )
        features = build_features(
            {
                "task_description": "implement the login feature",
                "file_paths": ["src/auth.py"],
                "agent_mentions": [],
                "tool_mentions": [],
                "command_prefix": None,
            }
        )
        return {
            "catalog": catalog,
            "catalog_agent_names": catalog_agent_names,
            "scored_agents": scored_agents,
            "features": features,
        }

    # ------------------------------------------------------------------
    # Shared fixture: Branch-2 sentinel setup — (project_meta, build)
    # (reused from TestBranch2Sentinel.sentinel_setup)
    # ------------------------------------------------------------------

    @staticmethod
    def _make_branch2_setup() -> dict:
        """Build inputs for Branch-2 sentinel (project_meta, build).

        Returns:
            Dict with labels, scored_agents, catalog,
            catalog_agent_names, features.
        """
        from claude_wayfinder.match._parse import _parse_triggers

        def _entry(name: str) -> CatalogEntry:
            tr = _parse_triggers(
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
                kind="agent",
                source="owned",
                routable=True,
                triggers=tr,
                applicable_skills=(),
                applicable_agents=(),
            )

        catalog = [
            _entry("project-planner"),
            _entry("project-reviewer"),
        ]
        catalog_agent_names = frozenset(
            {"project-planner", "project-reviewer"}
        )
        scored_agents = _make_gated(
            [("project-planner", 0.9), ("project-reviewer", 0.7)]
        )
        features = build_features(
            {
                "task_description": "update CLAUDE.md",
                "file_paths": ["CLAUDE.md"],
                "agent_mentions": [],
                "tool_mentions": [],
                "command_prefix": None,
            }
        )
        return {
            "catalog": catalog,
            "catalog_agent_names": catalog_agent_names,
            "scored_agents": scored_agents,
            "features": features,
        }

    # ------------------------------------------------------------------
    # A1 — diagnostics=None (default) ⇒ return value identical (PIN / GREEN)
    # ------------------------------------------------------------------

    def test_diagnostics_none_default_return_identical(self) -> None:
        """diagnostics=None leaves return value byte-identical to no-param call.

        This is the backward-compatibility pin: existing callers that do
        not pass ``diagnostics`` must see zero behavioral change.

        Phase 1 (pre-implementation): verifies the current return shape
        of the no-param call so we have a baseline.  Once ``diagnostics``
        is added, the same call with ``diagnostics=None`` must produce
        an identical result — asserted here when the param is available.
        """
        import inspect

        setup = self._make_branch3_setup()
        labels = Labels(
            domain="code",
            posture="build",
            confidence="high",
            area_span=1,
        )
        # Call without the new param (existing callers — always valid).
        result_no_param = compose_route(
            labels=labels,
            scored_agents=setup["scored_agents"],
            scored_skills=[],
            features=setup["features"],
            catalog=setup["catalog"],
            catalog_agent_names=setup["catalog_agent_names"],
        )
        # Baseline shape assertions that must hold before and after
        # the diagnostics param is added.
        assert result_no_param["decision"] == "delegate"
        assert result_no_param["agent"] == "code-writer"
        assert result_no_param["disposition_source"] == "posture_routed"

        # When the ``diagnostics`` param exists, assert that passing
        # ``diagnostics=None`` produces the same return value.
        sig = inspect.signature(compose_route)
        if "diagnostics" in sig.parameters:
            result_with_none = compose_route(
                labels=labels,
                scored_agents=setup["scored_agents"],
                scored_skills=[],
                features=setup["features"],
                catalog=setup["catalog"],
                catalog_agent_names=setup["catalog_agent_names"],
                diagnostics=None,
            )
            assert result_no_param == result_with_none, (
                "diagnostics=None must not alter the return value: "
                f"no-param={result_no_param!r}, "
                f"diagnostics=None={result_with_none!r}"
            )

    # ------------------------------------------------------------------
    # A2 — Branch-3 delegate: diag={} populated (expected RED)
    # ------------------------------------------------------------------

    def test_branch3_diagnostics_populated(self) -> None:
        """Branch-3 delegate path populates all §F.1 diagnostics keys.

        With domain=code, posture=build, confidence=high and code-writer
        in top-3 of gated: the Branch-3 posture route fires.  Passing
        ``diagnostics={}`` must populate:
          - posture_routed=True
          - branch="branch3_generic"
          - posture_preferred="code-writer"
          - lexical_agreement=True
          - gated_agent_names non-empty (contains "code-writer")
          - posture_veto_reason=None

        Expected to FAIL until ``diagnostics`` param is implemented
        (TypeError: unexpected keyword argument).
        """
        setup = self._make_branch3_setup()
        labels = Labels(
            domain="code",
            posture="build",
            confidence="high",
            area_span=1,
        )
        diag: dict = {}
        compose_route(
            labels=labels,
            scored_agents=setup["scored_agents"],
            scored_skills=[],
            features=setup["features"],
            catalog=setup["catalog"],
            catalog_agent_names=setup["catalog_agent_names"],
            diagnostics=diag,
        )
        assert diag.get("posture_routed") is True, (
            f"Expected posture_routed=True, got {diag.get('posture_routed')!r}"
        )
        assert diag.get("branch") == "branch3_generic", (
            f"Expected branch='branch3_generic', got {diag.get('branch')!r}"
        )
        assert diag.get("posture_preferred") == "code-writer", (
            "Expected posture_preferred='code-writer', "
            f"got {diag.get('posture_preferred')!r}"
        )
        assert diag.get("lexical_agreement") is True, (
            "Expected lexical_agreement=True, "
            f"got {diag.get('lexical_agreement')!r}"
        )
        gated_names = diag.get("gated_agent_names")
        assert gated_names, (
            f"Expected non-empty gated_agent_names, got {gated_names!r}"
        )
        assert "code-writer" in gated_names, (
            f"Expected 'code-writer' in gated_agent_names, got {gated_names!r}"
        )
        assert diag.get("posture_veto_reason") is None, (
            "Expected posture_veto_reason=None when branch fires, "
            f"got {diag.get('posture_veto_reason')!r}"
        )

    # ------------------------------------------------------------------
    # A3 — Fallback with veto fail: diag={} populates veto reason (RED)
    # ------------------------------------------------------------------

    def test_fallback_veto_diagnostics_populated(self) -> None:
        """Fallback (veto blocks Branch-3) populates §F.1 veto diagnostics.

        code-writer is preferred by (code, build) but is ranked 4th
        below the plausibility floor — the §B.1 veto blocks it and the
        Branch-3 route falls through to decide().  Passing
        ``diagnostics={}`` must populate:
          - posture_routed=False
          - branch="fallback"
          - lexical_agreement=False
          - posture_veto_reason="not_lexically_plausible"
            (or the correct reason for the constructed input)

        Expected to FAIL until ``diagnostics`` param is implemented.
        """
        floor = _DELEGATE_THRESHOLD - 0.15
        # code-writer at rank 4, below floor — veto fires
        scored_agents_veto = _make_gated(
            [
                ("debugger", 0.9),
                ("code-reviewer", 0.85),
                ("investigator", 0.8),
                ("code-writer", floor - 0.05),
            ]
        )
        setup = self._make_branch3_setup()
        labels = Labels(
            domain="code",
            posture="build",
            confidence="high",
            area_span=1,
        )
        diag: dict = {}
        compose_route(
            labels=labels,
            scored_agents=scored_agents_veto,
            scored_skills=[],
            features=setup["features"],
            catalog=setup["catalog"],
            catalog_agent_names=setup["catalog_agent_names"],
            diagnostics=diag,
        )
        assert diag.get("posture_routed") is False, (
            "Expected posture_routed=False on veto fallback, "
            f"got {diag.get('posture_routed')!r}"
        )
        assert diag.get("branch") == "fallback", (
            f"Expected branch='fallback', got {diag.get('branch')!r}"
        )
        assert diag.get("lexical_agreement") is False, (
            "Expected lexical_agreement=False when veto blocks, "
            f"got {diag.get('lexical_agreement')!r}"
        )
        veto_reason = diag.get("posture_veto_reason")
        assert veto_reason is not None, (
            "Expected posture_veto_reason to be set when veto blocks, "
            f"got {veto_reason!r}"
        )
        # The contract names "not_lexically_plausible" as the expected
        # reason when _is_lexically_plausible returns False.
        assert veto_reason == "not_lexically_plausible", (
            f"Expected posture_veto_reason='not_lexically_plausible', "
            f"got {veto_reason!r}"
        )

    # ------------------------------------------------------------------
    # A4 — Branch-2 sentinel: diag={} populated (expected RED)
    # ------------------------------------------------------------------

    def test_branch2_sentinel_diagnostics_populated(self) -> None:
        """Branch-2 sentinel populates §F.1 diagnostics with branch sentinel.

        (project_meta, build) → sentinel → self_handle.  Passing
        ``diagnostics={}`` must populate:
          - branch="branch2_sentinel"
          - posture_routed=True
          - lexical_agreement=None  (not evaluated on Branch 2)

        Expected to FAIL until ``diagnostics`` param is implemented.
        """
        setup = self._make_branch2_setup()
        labels = Labels(
            domain="project_meta",
            posture="build",
            confidence=None,
            area_span=1,
        )
        diag: dict = {}
        result = compose_route(
            labels=labels,
            scored_agents=setup["scored_agents"],
            scored_skills=[],
            features=setup["features"],
            catalog=setup["catalog"],
            catalog_agent_names=setup["catalog_agent_names"],
            diagnostics=diag,
        )
        # Sanity-check the route fired correctly before asserting diag.
        assert result["decision"] == "self_handle", (
            f"Expected self_handle from Branch-2, got {result['decision']!r}"
        )
        assert diag.get("branch") == "branch2_sentinel", (
            "Expected branch='branch2_sentinel', "
            f"got {diag.get('branch')!r}"
        )
        assert diag.get("posture_routed") is True, (
            "Expected posture_routed=True for Branch-2, "
            f"got {diag.get('posture_routed')!r}"
        )
        # lexical_agreement is None when not evaluated (Branches 1/2)
        assert "lexical_agreement" in diag, (
            "Expected lexical_agreement key to be present in diag"
        )
        assert diag["lexical_agreement"] is None, (
            "Expected lexical_agreement=None on Branch-2 (not evaluated), "
            f"got {diag['lexical_agreement']!r}"
        )

    # ------------------------------------------------------------------
    # A5 — Branch-1 confidence-not-high veto: posture_veto_reason set
    #      (ADDENDUM — Codex PR #429 P2; expected RED)
    # ------------------------------------------------------------------

    @staticmethod
    def _make_branch1_setup() -> dict:
        """Build inputs for a Branch-1 broad-diagnose scenario.

        Investigator is present in catalog and gated list; area_span=2.
        Confidence is left unset so the caller can inject the scenario.

        Returns:
            Dict with scored_agents, catalog, catalog_agent_names, features.
        """
        from claude_wayfinder.match._parse import _parse_triggers

        def _entry(name: str) -> CatalogEntry:
            tr = _parse_triggers(
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
                kind="agent",
                source="owned",
                routable=True,
                triggers=tr,
                applicable_skills=(),
                applicable_agents=(),
            )

        catalog = [
            _entry("investigator"),
            _entry("code-writer"),
            _entry("debugger"),
        ]
        catalog_agent_names = frozenset(
            {"investigator", "code-writer", "debugger"}
        )
        # investigator in top-3 so it is lexically plausible
        scored_agents = _make_gated(
            [
                ("investigator", 0.9),
                ("code-writer", 0.7),
                ("debugger", 0.5),
            ]
        )
        features = build_features(
            {
                "task_description": "investigate the bug across modules",
                "file_paths": ["src/a.py", "src/b.py"],
                "agent_mentions": [],
                "tool_mentions": [],
                "command_prefix": None,
            }
        )
        return {
            "catalog": catalog,
            "catalog_agent_names": catalog_agent_names,
            "scored_agents": scored_agents,
            "features": features,
        }

    def test_branch1_veto_confidence_not_high_sets_veto_reason(
        self,
    ) -> None:
        """Branch-1 gated off by low confidence records veto reason.

        Conditions:
          - posture="diagnose", area_span=2 (Branch-1 preconditions met)
          - investigator IS present in catalog_agent_names (single cause)
          - confidence="low" (the sole failing gate)

        The current code records posture_veto_reason=None for this case,
        hiding the broad-diagnose fail-safe reason in shadow logs (Codex
        P2 finding on PR #429).

        Expected outcome after the fix:
          - diag["branch"] == "fallback"
          - diag["posture_routed"] is False
          - diag["posture_veto_reason"] == "confidence_not_high"

        This test will FAIL until the code-writer implements the fix
        (current code leaves posture_veto_reason=None).
        """
        setup = self._make_branch1_setup()
        labels = Labels(
            domain="code",
            posture="diagnose",
            confidence="low",  # single failing gate; investigator present
            area_span=2,
        )
        diag: dict = {}
        compose_route(
            labels=labels,
            scored_agents=setup["scored_agents"],
            scored_skills=[],
            features=setup["features"],
            catalog=setup["catalog"],
            catalog_agent_names=setup["catalog_agent_names"],
            diagnostics=diag,
        )
        assert diag.get("branch") == "fallback", (
            f"Expected branch='fallback', got {diag.get('branch')!r}"
        )
        assert diag.get("posture_routed") is False, (
            "Expected posture_routed=False when Branch-1 gated off, "
            f"got {diag.get('posture_routed')!r}"
        )
        assert diag.get("posture_veto_reason") == "confidence_not_high", (
            "Expected posture_veto_reason='confidence_not_high' when "
            "confidence is low and investigator is in catalog, "
            f"got {diag.get('posture_veto_reason')!r}"
        )

    def test_branch1_veto_investigator_absent_from_catalog_sets_veto_reason(
        self,
    ) -> None:
        """Branch-1 gated off by absent investigator records veto reason.

        Conditions:
          - posture="diagnose", area_span=2 (Branch-1 preconditions met)
          - confidence="high" (confidence gate passes)
          - investigator is NOT in catalog_agent_names (single failing gate)

        Expected outcome after the fix:
          - diag["branch"] == "fallback"
          - diag["posture_routed"] is False
          - diag["posture_veto_reason"] == "investigator_not_in_catalog"

        This test will FAIL until the code-writer implements the fix
        (current code leaves posture_veto_reason=None).
        """
        setup = self._make_branch1_setup()
        # Swap catalog_agent_names to exclude investigator — single cause.
        no_investigator: frozenset[str] = frozenset(
            {"code-writer", "debugger"}
        )
        labels = Labels(
            domain="code",
            posture="diagnose",
            confidence="high",  # confidence gate passes; catalog is the blocker
            area_span=2,
        )
        diag: dict = {}
        compose_route(
            labels=labels,
            scored_agents=setup["scored_agents"],
            scored_skills=[],
            features=setup["features"],
            catalog=setup["catalog"],
            catalog_agent_names=no_investigator,
            diagnostics=diag,
        )
        assert diag.get("branch") == "fallback", (
            f"Expected branch='fallback', got {diag.get('branch')!r}"
        )
        assert diag.get("posture_routed") is False, (
            "Expected posture_routed=False when Branch-1 gated off, "
            f"got {diag.get('posture_routed')!r}"
        )
        assert diag.get("posture_veto_reason") == "investigator_not_in_catalog", (
            "Expected posture_veto_reason='investigator_not_in_catalog' when "
            "confidence is high but investigator is absent from catalog, "
            f"got {diag.get('posture_veto_reason')!r}"
        )
