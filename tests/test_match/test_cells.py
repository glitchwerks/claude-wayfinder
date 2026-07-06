"""Characterisation tests for the two-axis routing policy in match/_cells.py.

Covers cell_map_lookup (domain x posture → preferred agent) and
gate_agents (filter a scored list to the agents permitted in a domain).

Provenance: _cells.py is lifted verbatim from a validated probe.
Deferred-fix locks (see issue #364):
- "infra_deploy" gate now INCLUDES "code-writer" (fix shipped in #364)
- ("code","diagnose") maps to "debugger", not "investigator"
- ("infra_deploy","research") resolves via ("any","research") to
  "researcher", not "investigator" — gold-correct is investigator
  but the fix is deferred to a future issue

Issue #397: SELF_HANDLE_SENTINEL tests (added below) are deliberately
RED until the sentinel is added to _cells.py.
"""

from __future__ import annotations

from claude_wayfinder.match._cells import (
    ANY_DOMAIN_AGENTS,
    DOMAIN_AGENT_MAP,
    cell_map_lookup,
    gate_agents,
)
from claude_wayfinder.match._types import CatalogEntry, ScoredEntry, Triggers

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EMPTY_TRIGGERS = Triggers(
    command_prefixes=frozenset(),
    agent_mentions=frozenset(),
    path_globs=(),
    keywords=(),
    tool_mentions=frozenset(),
    excludes=(),
)


def _make_scored(name: str, score: float = 0.5) -> ScoredEntry:
    """Build a minimal ScoredEntry for gate_agents tests.

    Args:
        name: Agent name string.
        score: Score to assign; default 0.5.

    Returns:
        A ScoredEntry wrapping a minimal CatalogEntry.
    """
    entry = CatalogEntry(
        name=name,
        kind="agent",
        triggers=_EMPTY_TRIGGERS,
        applicable_agents=(),
        applicable_skills=(),
    )
    return ScoredEntry(entry=entry, score=score)


# ===========================================================================
# cell_map_lookup
# ===========================================================================


class TestCellMapLookup:
    """cell_map_lookup returns the preferred agent for a (domain, posture) pair.

    Verbatim values are locked as characterisation tests.  Deferred
    corrections are noted inline and tracked in issue #364.
    """

    # -----------------------------------------------------------------------
    # Direct hits — explicit (domain, posture) cells
    # -----------------------------------------------------------------------

    def test_code_build_returns_code_writer(self) -> None:
        """("code","build") → "code-writer" (direct cell hit)."""
        assert cell_map_lookup("code", "build") == "code-writer"

    def test_docs_prose_build_returns_doc_writer(self) -> None:
        """("docs_prose","build") → "doc-writer" (direct cell hit)."""
        assert cell_map_lookup("docs_prose", "build") == "doc-writer"

    def test_any_build_returns_code_writer(self) -> None:
        """("any","build") → "code-writer" (explicit any-domain cell)."""
        assert cell_map_lookup("any", "build") == "code-writer"

    def test_code_diagnose_returns_debugger_not_investigator(self) -> None:
        """("code","diagnose") → "debugger".

        Deliberate verbatim quirk (deferred fix #364): the gold-correct
        agent for code diagnose may differ, but the probe shipped this
        and we lock it here.
        """
        assert cell_map_lookup("code", "diagnose") == "debugger"

    def test_project_meta_plan_returns_project_planner(self) -> None:
        """("project_meta","plan") → "project-planner" (direct cell)."""
        assert cell_map_lookup("project_meta", "plan") == "project-planner"

    def test_infra_deploy_plan_returns_devops(self) -> None:
        """("infra_deploy","plan") → "devops" (direct cell)."""
        assert cell_map_lookup("infra_deploy", "plan") == "devops"

    # -----------------------------------------------------------------------
    # Fallback — (domain, posture) miss → ("any", posture) hit
    # -----------------------------------------------------------------------

    def test_project_meta_operate_falls_back_to_ops(self) -> None:
        """("project_meta","operate") falls back to ("any","operate") → "ops".

        There is no project_meta-operate cell, so the lookup falls
        through to the any-domain cell.
        """
        assert cell_map_lookup("project_meta", "operate") == "ops"

    def test_infra_deploy_research_falls_back_to_researcher(self) -> None:
        """("infra_deploy","research") → "researcher" via any-fallback.

        Deferred-fix lock (#364): the gold-correct value is
        "investigator" but the probe resolves this via ("any","research")
        → "researcher".  Lock the verbatim value until #364 is shipped.
        """
        assert cell_map_lookup("infra_deploy", "research") == "researcher"

    # -----------------------------------------------------------------------
    # Miss — neither (domain, posture) nor ("any", posture) exists
    # -----------------------------------------------------------------------

    def test_unknown_posture_returns_none(self) -> None:
        """("code","nonexistent_posture") → None when no cell matches."""
        assert cell_map_lookup("code", "nonexistent_posture") is None


# ===========================================================================
# DOMAIN_AGENT_MAP composition
# ===========================================================================


class TestDomainAgentMap:
    """DOMAIN_AGENT_MAP entries are composed as specified."""

    def test_any_domain_agents_contains_expected_six_names(self) -> None:
        """ANY_DOMAIN_AGENTS contains exactly the 6 cross-domain agents."""
        expected = frozenset({
            "investigator",
            "approach-critic",
            "auditor",
            "researcher",
            "ops",
            "project-planner",
        })
        assert ANY_DOMAIN_AGENTS == expected

    def test_code_domain_includes_code_specific_agents(self) -> None:
        """code domain set contains all four code-specific agents."""
        code_set = DOMAIN_AGENT_MAP["code"]
        assert code_set is not None
        assert "code-writer" in code_set
        assert "debugger" in code_set
        assert "code-reviewer" in code_set
        assert "inquisitor" in code_set

    def test_code_domain_includes_all_any_domain_agents(self) -> None:
        """code domain set is a superset of ANY_DOMAIN_AGENTS."""
        code_set = DOMAIN_AGENT_MAP["code"]
        assert code_set is not None
        assert ANY_DOMAIN_AGENTS.issubset(code_set)

    def test_code_domain_excludes_doc_writer_and_devops(self) -> None:
        """code domain set does NOT contain doc-writer or devops."""
        code_set = DOMAIN_AGENT_MAP["code"]
        assert code_set is not None
        assert "doc-writer" not in code_set
        assert "devops" not in code_set

    def test_infra_deploy_includes_devops_and_any_agents(self) -> None:
        """infra_deploy set contains devops and all ANY_DOMAIN_AGENTS."""
        infra_set = DOMAIN_AGENT_MAP["infra_deploy"]
        assert infra_set is not None
        assert "devops" in infra_set
        assert ANY_DOMAIN_AGENTS.issubset(infra_set)

    def test_infra_deploy_includes_code_writer(self) -> None:
        """infra_deploy set includes code-writer (fix shipped in #364).

        domain=infra_deploy, posture=build tasks are implementation
        work (IaC / CI-CD files).  The implementer is code-writer with
        the IaC skill attached — devops is advisory-only per charter.
        This test is RED until DOMAIN_AGENT_MAP["infra_deploy"] is
        updated to include "code-writer".
        """
        infra_set = DOMAIN_AGENT_MAP["infra_deploy"]
        assert infra_set is not None
        assert "code-writer" in infra_set, (
            "DOMAIN_AGENT_MAP['infra_deploy'] must include 'code-writer'. "
            "infra_deploy/build tasks route to the code-writer implementer "
            "(IaC skill attached); devops is advisory-only per charter. "
            "Fix: add 'code-writer' to the infra_deploy frozenset in "
            "src/claude_wayfinder/match/_cells.py."
        )

    def test_none_key_maps_to_none(self) -> None:
        """DOMAIN_AGENT_MAP[None] is None (is_any / unlabeled sentinel)."""
        assert DOMAIN_AGENT_MAP[None] is None

    def test_code_domain_includes_test_implementer(self) -> None:
        """code domain set includes test-implementer (#452).

        test-implementer authors tests in the test-first split and is
        NOT a member of ANY_DOMAIN_AGENTS, so it must be added
        explicitly to the code domain's allowed set -- otherwise
        gate_agents() drops it for every code-domain task before
        compose_route's Branch-3 test-first discriminator ever sees it.
        RED until DOMAIN_AGENT_MAP['code'] is updated to include
        'test-implementer'.
        """
        code_set = DOMAIN_AGENT_MAP["code"]
        assert code_set is not None
        assert "test-implementer" in code_set, (
            "DOMAIN_AGENT_MAP['code'] must include 'test-implementer' "
            "(#452) so it survives gate_agents() for code-domain tasks. "
            "Fix: add 'test-implementer' to the code frozenset in "
            "src/claude_wayfinder/match/_cells.py."
        )

    def test_code_domain_test_implementer_addition_preserves_existing_members(
        self,
    ) -> None:
        """Adding test-implementer must not remove any existing code member.

        Sanity check: code-writer, debugger, code-reviewer, and
        inquisitor must all remain present alongside test-implementer.
        """
        code_set = DOMAIN_AGENT_MAP["code"]
        assert code_set is not None
        for expected in (
            "code-writer",
            "debugger",
            "code-reviewer",
            "inquisitor",
        ):
            assert expected in code_set, (
                f"{expected!r} must remain in DOMAIN_AGENT_MAP['code'] "
                "after adding 'test-implementer'."
            )


# ===========================================================================
# gate_agents
# ===========================================================================


class TestGateAgents:
    """gate_agents filters a scored list by the allowed set for a domain."""

    def test_code_domain_keeps_in_domain_and_any_agents(self) -> None:
        """code domain keeps code-writer (in-domain) and ops (any-domain).

        doc-writer and devops are out-of-domain and must be dropped.
        Order of survivors is preserved.
        """
        scored = [
            _make_scored("code-writer", 0.9),
            _make_scored("doc-writer", 0.8),
            _make_scored("devops", 0.7),
            _make_scored("ops", 0.6),
        ]
        result = gate_agents(scored, "code")
        names = [se.entry.name for se in result]
        assert names == ["code-writer", "ops"]

    def test_code_domain_preserves_score_objects(self) -> None:
        """gate_agents returns the original ScoredEntry objects unchanged."""
        cw = _make_scored("code-writer", 0.9)
        ops = _make_scored("ops", 0.6)
        scored = [cw, _make_scored("doc-writer", 0.8), ops]
        result = gate_agents(scored, "code")
        assert result[0] is cw
        assert result[1] is ops

    def test_none_domain_returns_list_unchanged(self) -> None:
        """domain=None → no gate applied, original list returned unchanged."""
        scored = [
            _make_scored("doc-writer", 0.8),
            _make_scored("devops", 0.5),
        ]
        result = gate_agents(scored, None)
        assert result == scored

    def test_is_any_domain_returns_list_unchanged(self) -> None:
        """domain="is_any" → no gate applied, original list returned."""
        scored = [
            _make_scored("code-writer", 1.0),
            _make_scored("doc-writer", 0.7),
        ]
        result = gate_agents(scored, "is_any")
        assert result == scored

    def test_unknown_domain_returns_list_unchanged(self) -> None:
        """An unknown domain string → no gate (safe no-gate fallback)."""
        scored = [
            _make_scored("some-agent", 0.6),
            _make_scored("another-agent", 0.4),
        ]
        result = gate_agents(scored, "bogus_unknown")
        assert result == scored

    def test_empty_after_gate_falls_back_to_ungated(self) -> None:
        """All agents gated out → return original list (fallback to ungated).

        Mirrors the probe behaviour: if gating produces an empty list,
        gate_agents returns the full original list so the caller always
        has candidates to choose from.
        """
        # doc-writer is not in infra_deploy; gating would empty the list.
        scored = [_make_scored("doc-writer", 0.8)]
        result = gate_agents(scored, "infra_deploy")
        assert result == scored

    def test_gate_preserves_order_of_survivors(self) -> None:
        """Survivors appear in the same relative order as the input list."""
        scored = [
            _make_scored("investigator", 0.9),   # any-domain ✓
            _make_scored("code-writer", 0.85),    # code-domain ✓
            _make_scored("doc-writer", 0.7),      # dropped
            _make_scored("researcher", 0.6),      # any-domain ✓
        ]
        result = gate_agents(scored, "code")
        names = [se.entry.name for se in result]
        assert names == ["investigator", "code-writer", "researcher"]


# ===========================================================================
# Issue #397: SELF_HANDLE_SENTINEL — abstain sentinel for (project_meta, build)
# ===========================================================================
#
# These tests are RED until the sentinel constant and cell-map entry are added
# to _cells.py.  They will fail with ImportError (SELF_HANDLE_SENTINEL does
# not exist) or AssertionError (cell_map_lookup returns "code-writer" via the
# ("any","build") fallback, not the sentinel).
#
# Expected failure modes BEFORE implementation:
#   test_sentinel_constant_is_importable        → ImportError
#   test_sentinel_constant_value                → ImportError
#   test_project_meta_build_returns_sentinel    → AssertionError
#   test_cross_domain_code_build_unaffected     → ImportError (same module)
#   test_cross_domain_any_build_unaffected      → ImportError (same module)
#   test_cross_domain_docs_prose_build_unaffect → ImportError (same module)
#   test_sentinel_not_a_real_agent_name         → ImportError


class TestSelfHandleSentinel:
    """Issue #397: SELF_HANDLE_SENTINEL constant and cell-map entry contract.

    All tests in this class must remain RED until _cells.py is updated.
    """

    def test_sentinel_constant_is_importable(self) -> None:
        """SELF_HANDLE_SENTINEL can be imported from _cells.

        RED: ImportError until the constant is added to _cells.py.
        """
        from claude_wayfinder.match._cells import (  # noqa: F401
            SELF_HANDLE_SENTINEL,
        )

    def test_sentinel_constant_value(self) -> None:
        """SELF_HANDLE_SENTINEL == "__self_handle__" (exact string, exact name).

        Both phases (test author + code implementer) agree on this exact
        value so the compose path can check against it unambiguously.

        RED: ImportError until constant is added; wrong value after incorrect
        implementation.
        """
        from claude_wayfinder.match._cells import SELF_HANDLE_SENTINEL

        assert SELF_HANDLE_SENTINEL == "__self_handle__", (
            f"Sentinel must be '__self_handle__'; got {SELF_HANDLE_SENTINEL!r}"
        )

    def test_project_meta_build_returns_sentinel(self) -> None:
        """cell_map_lookup('project_meta', 'build') == SELF_HANDLE_SENTINEL.

        Before #397: this cell is absent from _CELL_MAP, so cell_map_lookup
        falls back to ("any","build") → "code-writer".

        After #397: the explicit ("project_meta","build") cell must exist and
        return the sentinel, short-circuiting the fallback.

        RED: returns "code-writer" (via fallback) until the cell is added.
        """
        from claude_wayfinder.match._cells import SELF_HANDLE_SENTINEL

        result = cell_map_lookup("project_meta", "build")
        assert result == SELF_HANDLE_SENTINEL, (
            f"cell_map_lookup('project_meta','build') must return "
            f"SELF_HANDLE_SENTINEL ('{SELF_HANDLE_SENTINEL}'); "
            f"got {result!r}.  Before #397, the cell is absent so the fallback "
            f"('any','build') returns 'code-writer'."
        )


class TestSelfHandleSentinelNoRegression:
    """Issue #397: sentinel is confined to (project_meta, build) only.

    These tests assert that unrelated cells are NOT broken by the change.
    They will fail with ImportError before the sentinel is added (same
    module import), but after implementation they must all be GREEN.
    """

    def test_code_build_still_returns_code_writer(self) -> None:
        """('code','build') still → 'code-writer' after #397.

        The sentinel must not bleed into other (domain, build) cells.
        RED before implementation: ImportError from the SELF_HANDLE_SENTINEL
        import in the same test class; GREEN after correct implementation.
        """
        from claude_wayfinder.match._cells import SELF_HANDLE_SENTINEL

        result = cell_map_lookup("code", "build")
        assert result == "code-writer", (
            f"('code','build') must still return 'code-writer'; got {result!r}"
        )
        assert result != SELF_HANDLE_SENTINEL, (
            "Sentinel must NOT bleed into ('code','build') cell."
        )

    def test_any_build_still_returns_code_writer(self) -> None:
        """('any','build') still → 'code-writer' after #397.

        The fallback cell is unchanged; only the project_meta-specific
        cell is given the sentinel.
        RED before implementation: ImportError.
        """
        from claude_wayfinder.match._cells import SELF_HANDLE_SENTINEL

        result = cell_map_lookup("any", "build")
        assert result == "code-writer", (
            f"('any','build') fallback must still return 'code-writer'; "
            f"got {result!r}"
        )
        assert result != SELF_HANDLE_SENTINEL, (
            "Sentinel must NOT bleed into ('any','build') cell."
        )

    def test_docs_prose_build_still_returns_doc_writer(self) -> None:
        """('docs_prose','build') still → 'doc-writer' after #397.

        RED before implementation: ImportError.
        """
        from claude_wayfinder.match._cells import SELF_HANDLE_SENTINEL

        result = cell_map_lookup("docs_prose", "build")
        assert result == "doc-writer", (
            f"('docs_prose','build') must still return 'doc-writer'; "
            f"got {result!r}"
        )
        assert result != SELF_HANDLE_SENTINEL, (
            "Sentinel must NOT bleed into ('docs_prose','build') cell."
        )

    def test_sentinel_is_not_a_valid_agent_name_in_any_domain(self) -> None:
        """SELF_HANDLE_SENTINEL is not in DOMAIN_AGENT_MAP for any domain.

        The sentinel is a routing instruction, not a real agent.  It must
        never appear as a member of any domain's allowed-agent frozenset,
        so the domain gate can never 'pass' it as if it were routable.

        RED before implementation: ImportError.
        """
        from claude_wayfinder.match._cells import SELF_HANDLE_SENTINEL

        for domain, agent_set in DOMAIN_AGENT_MAP.items():
            if agent_set is not None:
                assert SELF_HANDLE_SENTINEL not in agent_set, (
                    f"SELF_HANDLE_SENTINEL must not appear in "
                    f"DOMAIN_AGENT_MAP[{domain!r}] — it is a routing "
                    f"instruction, not a real agent."
                )
