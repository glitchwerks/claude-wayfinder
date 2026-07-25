"""Change-detection snapshot pinning ``_cells.py`` against KC-3/KC-4
assumptions (#509).

``scripts/corpus/eval/_kc.py``'s ``compute_kc3`` (via its ``cell_exists``
eligibility gate) and ``compute_kc4`` (via ``_route_could_differ``, which
calls ``cell_map_lookup`` twice per row) both depend on the exact shape of
``claude_wayfinder.match._cells``'s domain/posture matrix. Nothing else
re-validates those formulas when ``_cells.py`` changes.

This test hardcodes the *current* return values of ``cell_map_lookup`` and
``_route_could_differ`` for the full posture set crossed with a
representative domain set, and asserts the live functions still match. The
expected tables below are captured by hand from the source, not derived at
test time from the same source -- the whole point is an independent pin
that would catch ``_cells.py`` drifting without a corresponding update
here.

This test is expected to PASS today (it pins current, correct behavior).
A failure means ``_cells.py`` changed in a way that shifts routing
behavior that KC-3/KC-4 depend on -- see the guidance embedded in each
assertion failure message.
"""

from __future__ import annotations

import pytest

from claude_wayfinder.match._cells import (
    _CELL_MAP,
    DOMAIN_AGENT_MAP,
    SELF_HANDLE_SENTINEL,
    cell_map_lookup,
)
from scripts.corpus.eval._kc import _route_could_differ

# ---------------------------------------------------------------------------
# Drift guidance embedded in every assertion failure message (#509).
# ---------------------------------------------------------------------------

_DRIFT_GUIDANCE = (
    "\n\nThis snapshot (tests/test_corpus_eval/test_cellmap_snapshot.py, "
    "issue #509) pins the claude_wayfinder.match._cells domain/posture "
    "matrix that scripts/corpus/eval/_kc.py's compute_kc3 (via its "
    "cell_exists eligibility gate) and compute_kc4 (via "
    "_route_could_differ, which calls cell_map_lookup twice per row) both "
    "depend on. A mismatch means _cells.py changed in a way that shifts "
    "routing behavior those two KC formulas assume. Before updating the "
    "hardcoded expectation here, re-review compute_kc3 and compute_kc4 in "
    "scripts/corpus/eval/_kc.py to confirm the KC-3/KC-4 formulas still "
    "hold under the new cell map -- only then update this table."
)

# ---------------------------------------------------------------------------
# Posture and domain enumeration, confirmed from _cells.py source (#509).
# ---------------------------------------------------------------------------

# Every posture appearing as the second element of a _CELL_MAP key. Nine
# postures total: build, diagnose, assess, critique, idea-critique, verify,
# plan, research, operate.
_EXPECTED_POSTURES: frozenset[str] = frozenset(
    {
        "build",
        "diagnose",
        "assess",
        "critique",
        "idea-critique",
        "verify",
        "plan",
        "research",
        "operate",
    }
)

# Every concrete (non-None) domain key in DOMAIN_AGENT_MAP.
_EXPECTED_CONCRETE_DOMAINS: frozenset[str] = frozenset(
    {"code", "docs_prose", "project_meta", "infra_deploy"}
)

# ---------------------------------------------------------------------------
# Task 1/2 -- cell_map_lookup(domain, posture) snapshot.
#
# Domains: None (is_any/unlabeled surrogate -- cell_map_lookup falls back
# to the ("any", posture) entry for None just as it does for the literal
# strings "is_any" and "any", since none of those three ever match a
# _CELL_MAP key's first element other than "any" itself, so they all
# resolve identically), plus each concrete domain key in DOMAIN_AGENT_MAP.
# Postures: the full set above.
#
# Hand-captured from claude_wayfinder/match/_cells.py's _CELL_MAP + its
# ("any", posture) fallback, 2026-07-25.
# ---------------------------------------------------------------------------

CELL_MAP_LOOKUP_SNAPSHOT: dict[tuple[str | None, str], str | None] = {
    # domain=None (is_any / unlabeled surrogate) -- always the ("any", *)
    # fallback row, since no _CELL_MAP key has None as its first element.
    (None, "build"): "code-writer",
    (None, "diagnose"): "investigator",
    (None, "assess"): "code-reviewer",
    (None, "critique"): "approach-critic",
    (None, "idea-critique"): "approach-critic",
    (None, "verify"): "auditor",
    (None, "plan"): "project-planner",
    (None, "research"): "researcher",
    (None, "operate"): "ops",
    # domain="code"
    ("code", "build"): "code-writer",
    ("code", "diagnose"): "debugger",
    ("code", "assess"): "code-reviewer",
    ("code", "critique"): "inquisitor",
    ("code", "idea-critique"): "approach-critic",  # ("any", *) fallback
    ("code", "verify"): "auditor",  # ("any", *) fallback
    ("code", "plan"): "project-planner",  # ("any", *) fallback
    ("code", "research"): "researcher",  # ("any", *) fallback
    ("code", "operate"): "ops",  # ("any", *) fallback
    # domain="docs_prose"
    ("docs_prose", "build"): "doc-writer",
    ("docs_prose", "diagnose"): "investigator",  # ("any", *) fallback
    ("docs_prose", "assess"): "code-reviewer",  # ("any", *) fallback
    ("docs_prose", "critique"): "approach-critic",  # ("any", *) fallback
    ("docs_prose", "idea-critique"): "approach-critic",  # fallback
    ("docs_prose", "verify"): "auditor",  # ("any", *) fallback
    ("docs_prose", "plan"): "project-planner",  # ("any", *) fallback
    ("docs_prose", "research"): "researcher",  # ("any", *) fallback
    ("docs_prose", "operate"): "ops",  # ("any", *) fallback
    # domain="project_meta"
    ("project_meta", "build"): SELF_HANDLE_SENTINEL,  # "__self_handle__"
    ("project_meta", "diagnose"): "investigator",  # ("any", *) fallback
    ("project_meta", "assess"): "project-reviewer",
    ("project_meta", "critique"): "approach-critic",  # fallback
    ("project_meta", "idea-critique"): "approach-critic",  # fallback
    ("project_meta", "verify"): "auditor",  # ("any", *) fallback
    ("project_meta", "plan"): "project-planner",
    ("project_meta", "research"): "researcher",  # ("any", *) fallback
    ("project_meta", "operate"): "ops",  # ("any", *) fallback
    # domain="infra_deploy"
    ("infra_deploy", "build"): "code-writer",  # ("any", *) fallback
    ("infra_deploy", "diagnose"): "investigator",
    ("infra_deploy", "assess"): "code-reviewer",  # ("any", *) fallback
    ("infra_deploy", "critique"): "approach-critic",  # fallback
    ("infra_deploy", "idea-critique"): "approach-critic",  # fallback
    ("infra_deploy", "verify"): "auditor",  # ("any", *) fallback
    ("infra_deploy", "plan"): "devops",
    ("infra_deploy", "research"): "researcher",  # ("any", *) fallback
    ("infra_deploy", "operate"): "ops",  # ("any", *) fallback
}

assert len(CELL_MAP_LOOKUP_SNAPSHOT) == 45, (
    "Expected 45 cell_map_lookup snapshot entries (5 domains x 9 "
    f"postures); got {len(CELL_MAP_LOOKUP_SNAPSHOT)}."
)

# ---------------------------------------------------------------------------
# Task 3 -- _route_could_differ(caller_domain, gold_domain, posture)
# snapshot, for the exact caller_domain values compute_kc4 checks
# (`caller_domain in {"is_any", "project_meta"}`) crossed against every
# concrete gold domain, for every posture.
#
# Hand-captured from cell_map_lookup(caller_domain, posture) !=
# cell_map_lookup(gold_domain, posture), 2026-07-25.
# ---------------------------------------------------------------------------

ROUTE_COULD_DIFFER_SNAPSHOT: dict[tuple[str, str, str], bool] = {
    # caller_domain="is_any" -- resolves via the ("any", *) fallback,
    # identically to the (None, posture) column of the table above.
    ("is_any", "code", "build"): False,
    ("is_any", "code", "diagnose"): True,
    ("is_any", "code", "assess"): False,
    ("is_any", "code", "critique"): True,
    ("is_any", "code", "idea-critique"): False,
    ("is_any", "code", "verify"): False,
    ("is_any", "code", "plan"): False,
    ("is_any", "code", "research"): False,
    ("is_any", "code", "operate"): False,
    ("is_any", "docs_prose", "build"): True,
    ("is_any", "docs_prose", "diagnose"): False,
    ("is_any", "docs_prose", "assess"): False,
    ("is_any", "docs_prose", "critique"): False,
    ("is_any", "docs_prose", "idea-critique"): False,
    ("is_any", "docs_prose", "verify"): False,
    ("is_any", "docs_prose", "plan"): False,
    ("is_any", "docs_prose", "research"): False,
    ("is_any", "docs_prose", "operate"): False,
    ("is_any", "project_meta", "build"): True,
    ("is_any", "project_meta", "diagnose"): False,
    ("is_any", "project_meta", "assess"): True,
    ("is_any", "project_meta", "critique"): False,
    ("is_any", "project_meta", "idea-critique"): False,
    ("is_any", "project_meta", "verify"): False,
    ("is_any", "project_meta", "plan"): False,
    ("is_any", "project_meta", "research"): False,
    ("is_any", "project_meta", "operate"): False,
    ("is_any", "infra_deploy", "build"): False,
    ("is_any", "infra_deploy", "diagnose"): False,
    ("is_any", "infra_deploy", "assess"): False,
    ("is_any", "infra_deploy", "critique"): False,
    ("is_any", "infra_deploy", "idea-critique"): False,
    ("is_any", "infra_deploy", "verify"): False,
    ("is_any", "infra_deploy", "plan"): True,
    ("is_any", "infra_deploy", "research"): False,
    ("is_any", "infra_deploy", "operate"): False,
    # caller_domain="project_meta"
    ("project_meta", "code", "build"): True,
    ("project_meta", "code", "diagnose"): True,
    ("project_meta", "code", "assess"): True,
    ("project_meta", "code", "critique"): True,
    ("project_meta", "code", "idea-critique"): False,
    ("project_meta", "code", "verify"): False,
    ("project_meta", "code", "plan"): False,
    ("project_meta", "code", "research"): False,
    ("project_meta", "code", "operate"): False,
    ("project_meta", "docs_prose", "build"): True,
    ("project_meta", "docs_prose", "diagnose"): False,
    ("project_meta", "docs_prose", "assess"): True,
    ("project_meta", "docs_prose", "critique"): False,
    ("project_meta", "docs_prose", "idea-critique"): False,
    ("project_meta", "docs_prose", "verify"): False,
    ("project_meta", "docs_prose", "plan"): False,
    ("project_meta", "docs_prose", "research"): False,
    ("project_meta", "docs_prose", "operate"): False,
    # caller_domain == gold_domain == "project_meta": never reached by
    # compute_kc4 (it excludes label.domain == caller_domain), but
    # _route_could_differ has no such guard, so it is pinned here too --
    # every posture is trivially False (same domain both sides).
    ("project_meta", "project_meta", "build"): False,
    ("project_meta", "project_meta", "diagnose"): False,
    ("project_meta", "project_meta", "assess"): False,
    ("project_meta", "project_meta", "critique"): False,
    ("project_meta", "project_meta", "idea-critique"): False,
    ("project_meta", "project_meta", "verify"): False,
    ("project_meta", "project_meta", "plan"): False,
    ("project_meta", "project_meta", "research"): False,
    ("project_meta", "project_meta", "operate"): False,
    ("project_meta", "infra_deploy", "build"): True,
    ("project_meta", "infra_deploy", "diagnose"): False,
    ("project_meta", "infra_deploy", "assess"): True,
    ("project_meta", "infra_deploy", "critique"): False,
    ("project_meta", "infra_deploy", "idea-critique"): False,
    ("project_meta", "infra_deploy", "verify"): False,
    ("project_meta", "infra_deploy", "plan"): True,
    ("project_meta", "infra_deploy", "research"): False,
    ("project_meta", "infra_deploy", "operate"): False,
}

assert len(ROUTE_COULD_DIFFER_SNAPSHOT) == 72, (
    "Expected 72 _route_could_differ snapshot entries (2 caller domains x "
    f"4 gold domains x 9 postures); got {len(ROUTE_COULD_DIFFER_SNAPSHOT)}."
)


class TestCellMapLookupSnapshot:
    """Pin ``cell_map_lookup`` for the full domain x posture matrix."""

    @pytest.mark.parametrize(
        "domain,posture,expected",
        [
            (domain, posture, expected)
            for (domain, posture), expected in CELL_MAP_LOOKUP_SNAPSHOT.items()
        ],
        ids=[
            f"{domain}/{posture}"
            for (domain, posture) in CELL_MAP_LOOKUP_SNAPSHOT
        ],
    )
    def test_cell_map_lookup_matches_snapshot(
        self,
        domain: str | None,
        posture: str,
        expected: str | None,
    ) -> None:
        """cell_map_lookup(domain, posture) must match the pinned value.

        compute_kc3 calls cell_map_lookup(domain_for_lookup, posture) to
        decide row eligibility (its cell_exists gate); a drift here
        silently changes which rows KC-3 considers eligible.
        """
        result = cell_map_lookup(domain, posture)
        assert result == expected, (
            f"cell_map_lookup({domain!r}, {posture!r}) = {result!r}, "
            f"expected {expected!r}." + _DRIFT_GUIDANCE
        )

    def test_posture_set_is_current(self) -> None:
        """The postures pinned above must match _CELL_MAP's actual set.

        A newly added or removed posture in _cells.py would otherwise go
        completely unnoticed by the parametrized snapshot above, since
        that test only iterates over the postures already known when
        this file was written.
        """
        actual_postures = {posture for (_, posture) in _CELL_MAP}
        assert actual_postures == _EXPECTED_POSTURES, (
            "_CELL_MAP's posture set has changed: expected "
            f"{sorted(_EXPECTED_POSTURES)!r}, found "
            f"{sorted(actual_postures)!r}." + _DRIFT_GUIDANCE
        )

    def test_concrete_domain_set_is_current(self) -> None:
        """The concrete domains pinned above must match DOMAIN_AGENT_MAP.

        A newly added or removed concrete domain key would otherwise go
        unnoticed by the parametrized snapshot above.
        """
        actual_domains = set(DOMAIN_AGENT_MAP) - {None}
        assert actual_domains == _EXPECTED_CONCRETE_DOMAINS, (
            "DOMAIN_AGENT_MAP's concrete domain set has changed: expected "
            f"{sorted(_EXPECTED_CONCRETE_DOMAINS)!r}, found "
            f"{sorted(actual_domains)!r}." + _DRIFT_GUIDANCE
        )


class TestRouteCouldDifferSnapshot:
    """Pin ``_route_could_differ`` for the caller/gold domain pairs KC-4
    actually evaluates, across every posture.
    """

    @pytest.mark.parametrize(
        "caller_domain,gold_domain,posture,expected",
        [
            (caller_domain, gold_domain, posture, expected)
            for (
                caller_domain,
                gold_domain,
                posture,
            ), expected in ROUTE_COULD_DIFFER_SNAPSHOT.items()
        ],
        ids=[
            f"{caller_domain}-vs-{gold_domain}/{posture}"
            for (
                caller_domain,
                gold_domain,
                posture,
            ) in ROUTE_COULD_DIFFER_SNAPSHOT
        ],
    )
    def test_route_could_differ_matches_snapshot(
        self,
        caller_domain: str,
        gold_domain: str,
        posture: str,
        expected: bool,
    ) -> None:
        """_route_could_differ(caller, gold, posture) must match the
        pinned value.

        compute_kc4 calls this to decide whether a mislabeled-domain row
        is exempt from the routing-neutrality check; a drift here
        silently changes KC-4's eligible-row set and its PASS/FAIL
        verdict.
        """
        result = _route_could_differ(caller_domain, gold_domain, posture)
        assert result == expected, (
            f"_route_could_differ({caller_domain!r}, {gold_domain!r}, "
            f"{posture!r}) = {result!r}, expected {expected!r}."
            + _DRIFT_GUIDANCE
        )
