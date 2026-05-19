"""Tests for keyword_groups (AND-group conjunctive triggers).

Spec: docs/superpowers/specs/2026-05-18-and-groups-design.md
Tracking: glitchwerks/claude-wayfinder#135
"""

from __future__ import annotations

from claude_wayfinder import match as _match_mod


class TestKeywordGroupTypes:
    """The dataclass surface and constants the spec mandates."""

    def test_group_multiplier_constant_is_1_0(self) -> None:
        """Spec D4: _GROUP_MULTIPLIER = 1.0 (distinct from singleton 0.5)."""
        assert _match_mod._GROUP_MULTIPLIER == 1.0

    def test_slot_dataclass_holds_terms_and_optional_name(self) -> None:
        """Slot stores a tuple of terms and an optional name."""
        slot = _match_mod.Slot(terms=("update", "edit"), name="verbs")
        assert slot.terms == ("update", "edit")
        assert slot.name == "verbs"

    def test_slot_name_defaults_to_none(self) -> None:
        """Slot name is optional."""
        slot = _match_mod.Slot(terms=("docs", "readme"))
        assert slot.name is None

    def test_keyword_group_holds_slots_and_weight(self) -> None:
        """KeywordGroup composes Slots with a weight."""
        group = _match_mod.KeywordGroup(
            slots=(
                _match_mod.Slot(terms=("update", "edit")),
                _match_mod.Slot(terms=("docs", "readme")),
            ),
            weight=1.0,
        )
        assert len(group.slots) == 2
        assert group.weight == 1.0
