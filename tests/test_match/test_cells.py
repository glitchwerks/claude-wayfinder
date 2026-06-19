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
