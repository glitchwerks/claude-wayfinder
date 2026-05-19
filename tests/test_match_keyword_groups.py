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


class TestTriggersParsing:
    """_parse_triggers correctly reads keyword_groups from raw dicts."""

    def test_triggers_defaults_keyword_groups_to_empty(self) -> None:
        """Catalog entries without keyword_groups parse cleanly."""
        triggers = _match_mod._parse_triggers({})
        assert triggers.keyword_groups == ()

    def test_parse_keyword_groups_dict_form(self) -> None:
        """The canonical dict form (terms + optional name) parses."""
        raw = {
            "keyword_groups": [
                {
                    "slots": [
                        {"name": "verbs", "terms": ["update", "edit"]},
                        {"name": "nouns", "terms": ["docs", "readme"]},
                    ],
                    "weight": 1.0,
                }
            ]
        }
        triggers = _match_mod._parse_triggers(raw)
        assert len(triggers.keyword_groups) == 1
        group = triggers.keyword_groups[0]
        assert group.weight == 1.0
        assert len(group.slots) == 2
        assert group.slots[0].name == "verbs"
        assert group.slots[0].terms == ("update", "edit")
        assert group.slots[1].name == "nouns"
        assert group.slots[1].terms == ("docs", "readme")

    def test_parse_keyword_groups_bare_list_form(self) -> None:
        """Authors may write slots as bare lists (no name)."""
        raw = {
            "keyword_groups": [
                {
                    "slots": [
                        ["github"],
                        ["issue", "pr", "workflow"],
                    ],
                    "weight": 1.0,
                }
            ]
        }
        triggers = _match_mod._parse_triggers(raw)
        group = triggers.keyword_groups[0]
        assert group.slots[0].name is None
        assert group.slots[0].terms == ("github",)
        assert group.slots[1].terms == ("issue", "pr", "workflow")

    def test_parse_keyword_groups_lowercases_terms(self) -> None:
        """Terms are lowercased to match feature extraction."""
        raw = {
            "keyword_groups": [
                {"slots": [["UPDATE"], ["DOCS"]], "weight": 1.0}
            ]
        }
        triggers = _match_mod._parse_triggers(raw)
        assert triggers.keyword_groups[0].slots[0].terms == ("update",)
        assert triggers.keyword_groups[0].slots[1].terms == ("docs",)
