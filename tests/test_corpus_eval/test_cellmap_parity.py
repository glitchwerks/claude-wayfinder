"""Parity guard: canonical _CELL_MAP must cover all entries the old local
literal in _systems.py carried before the #391 dedup.

The local _CELL_MAP was mapping-identical to the canonical one in
``claude_wayfinder.match._cells``; this test locks that invariant so any
future drift between the two is caught immediately.

Note: the expected cells below are verbatim from the pre-dedup local
literal in ``scripts/corpus/eval/_systems.py`` (removed in #391).
"""

from __future__ import annotations

import pytest

from claude_wayfinder.match._cells import (
    _CELL_MAP as CANONICAL_CELL_MAP,
)
from claude_wayfinder.match._cells import (
    cell_map_lookup,
)

# ---------------------------------------------------------------------------
# The 18 cells that were in the local _systems.py literal before #391.
# ---------------------------------------------------------------------------

_OLD_LOCAL_CELLS: dict[tuple[str, str], str] = {
    # build row
    ("code", "build"):           "code-writer",
    ("docs_prose", "build"):     "doc-writer",
    ("any", "build"):            "code-writer",
    # diagnose row
    ("code", "diagnose"):        "debugger",
    ("infra_deploy", "diagnose"): "investigator",
    ("any", "diagnose"):         "investigator",
    # assess row
    ("code", "assess"):          "code-reviewer",
    ("project_meta", "assess"):  "project-reviewer",
    ("any", "assess"):           "code-reviewer",
    # critique row
    ("code", "critique"):        "inquisitor",
    ("any", "critique"):         "approach-critic",
    # idea-critique row
    ("any", "idea-critique"):    "approach-critic",
    # verify row
    ("any", "verify"):           "auditor",
    # plan row
    ("project_meta", "plan"):    "project-planner",
    ("infra_deploy", "plan"):    "devops",
    ("any", "plan"):             "project-planner",
    # research row
    ("any", "research"):         "researcher",
    # operate row
    ("any", "operate"):          "ops",
}


class TestCellMapParity:
    """Assert the canonical _CELL_MAP covers every entry the old local literal
    carried — confirming the maps were identical before #391 removed the copy.
    """

    def test_canonical_contains_every_old_cell(self) -> None:
        """Every (domain, posture) -> agent from the old local literal must
        exist in the canonical _CELL_MAP with the same agent value.

        A failure here means the maps drifted before #391 landed, which is
        a drift finding — not a clean dedup.
        """
        mismatches: list[str] = []
        for (domain, posture), expected_agent in _OLD_LOCAL_CELLS.items():
            canonical_agent = CANONICAL_CELL_MAP.get((domain, posture))
            if canonical_agent != expected_agent:
                mismatches.append(
                    f"  ({domain!r}, {posture!r}): "
                    f"old={expected_agent!r}, canonical={canonical_agent!r}"
                )
        assert not mismatches, (
            "Cell map drift detected — the old local _systems.py literal "
            "does NOT match the canonical _cells._CELL_MAP:\n"
            + "\n".join(mismatches)
        )

    def test_cell_count_matches(self) -> None:
        """The canonical map must have the same number of entries as the old
        local literal (18 cells).

        A count difference indicates the canonical map added or removed cells
        relative to what _systems.py was using.
        """
        assert len(_OLD_LOCAL_CELLS) == len(CANONICAL_CELL_MAP), (
            f"Cell count mismatch: old local had {len(_OLD_LOCAL_CELLS)} cells, "
            f"canonical has {len(CANONICAL_CELL_MAP)} cells.  "
            f"This is a drift finding if it appears on main."
        )

    @pytest.mark.parametrize("domain,posture,expected", [
        ("code", "build", "code-writer"),
        ("docs_prose", "build", "doc-writer"),
        ("any", "build", "code-writer"),
        ("code", "diagnose", "debugger"),
        ("infra_deploy", "diagnose", "investigator"),
        ("any", "diagnose", "investigator"),
        ("code", "assess", "code-reviewer"),
        ("project_meta", "assess", "project-reviewer"),
        ("any", "assess", "code-reviewer"),
        ("code", "critique", "inquisitor"),
        ("any", "critique", "approach-critic"),
        ("any", "idea-critique", "approach-critic"),
        ("any", "verify", "auditor"),
        ("project_meta", "plan", "project-planner"),
        ("infra_deploy", "plan", "devops"),
        ("any", "plan", "project-planner"),
        ("any", "research", "researcher"),
        ("any", "operate", "ops"),
    ])
    def test_cell_map_lookup_matches_old_literal(
        self,
        domain: str,
        posture: str,
        expected: str,
    ) -> None:
        """cell_map_lookup(domain, posture) must return the same agent that
        the old local _systems.py literal held for each of the 18 cells.
        """
        result = cell_map_lookup(domain, posture)
        assert result == expected, (
            f"cell_map_lookup({domain!r}, {posture!r}) = {result!r}, "
            f"expected {expected!r} (from old local literal in _systems.py)"
        )
