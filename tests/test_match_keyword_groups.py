"""Tests for keyword_groups (AND-group conjunctive triggers).

Spec: docs/superpowers/specs/2026-05-18-and-groups-design.md
Tracking: glitchwerks/claude-wayfinder#135
"""

from __future__ import annotations

import pytest

from claude_wayfinder import match as _match_mod


class TestKeywordGroupTypes:
    """The dataclass surface and constants the spec mandates."""

    def test_group_multiplier_constant_is_1_0(self) -> None:
        """Spec D4: _GROUP_MULTIPLIER = 1.0 (distinct from singleton 0.5)."""
        assert _match_mod._GROUP_MULTIPLIER == 1.0

    def test_slot_dataclass_holds_terms_and_optional_name(self) -> None:
        """Slot stores stemmed terms and an optional name.

        After stemming integration (issue #304), Slot.__post_init__ applies
        Porter2 stemming: 'update' -> 'updat', 'edit' -> 'edit'.
        """
        slot = _match_mod.Slot(terms=("update", "edit"), name="verbs")
        # "update" -> "updat", "edit" -> "edit"
        assert slot.terms == ("updat", "edit")
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
        """The canonical dict form (terms + optional name) parses.

        After stemming integration (issue #304), slot terms are stored as
        their Porter2 stems: 'update' -> 'updat', 'edit' -> 'edit',
        'docs' -> 'doc', 'readme' -> 'readm'.
        """
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
        # Stems: "update" -> "updat", "edit" -> "edit"
        assert group.slots[0].terms == ("updat", "edit")
        assert group.slots[1].name == "nouns"
        # Stems: "docs" -> "doc", "readme" -> "readm"
        assert group.slots[1].terms == ("doc", "readm")

    def test_parse_keyword_groups_bare_list_form(self) -> None:
        """Authors may write slots as bare lists (no name).

        After stemming integration (issue #304), slot terms are stored as
        their Porter2 stems: 'github' -> 'github', 'issue' -> 'issu',
        'pr' -> 'pr', 'workflow' -> 'workflow'.
        """
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
        # "github" -> "github" (unchanged)
        assert group.slots[0].terms == ("github",)
        # "issue" -> "issu", "pr" -> "pr", "workflow" -> "workflow"
        assert group.slots[1].terms == ("issu", "pr", "workflow")

    def test_parse_keyword_groups_lowercases_terms(self) -> None:
        """Terms are lowercased and stemmed to match feature extraction.

        After stemming integration (issue #304), case normalisation is
        followed by Porter2 stemming: 'UPDATE' -> 'updat', 'DOCS' -> 'doc'.
        """
        raw = {
            "keyword_groups": [
                {"slots": [["UPDATE"], ["DOCS"]], "weight": 1.0}
            ]
        }
        triggers = _match_mod._parse_triggers(raw)
        # "UPDATE" lowercased -> "update" -> stem "updat"
        assert triggers.keyword_groups[0].slots[0].terms == ("updat",)
        # "DOCS" lowercased -> "docs" -> stem "doc"
        assert triggers.keyword_groups[0].slots[1].terms == ("doc",)


class TestScoreWithGroups:
    """Scoring with keyword_groups — spec § 7 worked examples."""

    def _doc_writer_entry(self) -> "_match_mod.CatalogEntry":
        """Doc-writer entry mirroring production singletons + new group."""
        return _match_mod.CatalogEntry(
            name="doc-writer",
            kind="agent",
            triggers=_match_mod.Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=(),
                keywords=(
                    _match_mod.Keyword("docs", 1.0),
                    _match_mod.Keyword("readme", 1.0),
                    _match_mod.Keyword("spec", 1.0),
                    _match_mod.Keyword("update", 0.25),
                    _match_mod.Keyword("edit", 0.25),
                ),
                tool_mentions=frozenset(),
                excludes=frozenset(),
                keyword_groups=(
                    _match_mod.KeywordGroup(
                        slots=(
                            _match_mod.Slot(terms=("update", "edit", "modify", "change")),
                            _match_mod.Slot(terms=("docs", "readme", "spec")),
                        ),
                        weight=1.0,
                    ),
                ),
            ),
            applicable_agents=(),
            applicable_skills=(),
        )

    def test_group_fires_and_suppresses_singletons(self) -> None:
        """Spec § 7.1 row 1: 'update the docs' → doc-writer 1.00.

        Group fires (update + docs both present), contributing
        _GROUP_MULTIPLIER * 1.0 = 1.0. Singletons 'update@0.25' and
        'docs@1.0' are suppressed by replacement rule (spec D5).
        Final score: 1.0 (no singleton residue).
        """
        entry = self._doc_writer_entry()
        features = _match_mod.build_features({"task_description": "update the docs"})
        assert _match_mod.score(entry, features) == pytest.approx(1.0, abs=1e-6)

    def test_group_does_not_fire_singletons_count_normally(self) -> None:
        """Spec § 7.1 row 3: 'the docs are great' → doc-writer 0.50.

        No verb in slot 1 ('the', 'docs', 'are', 'great' has none of
        {update, edit, modify, change}). Group does NOT fire; no
        suppression. Singleton 'docs@1.0' contributes 0.5.
        """
        entry = self._doc_writer_entry()
        features = _match_mod.build_features({"task_description": "the docs are great"})
        assert _match_mod.score(entry, features) == pytest.approx(0.5, abs=1e-6)

    def test_group_unfired_partial_singletons_still_contribute(self) -> None:
        """A prompt that hits only the verb slot, not the noun slot.

        'update the source code' contains 'update' but no doc-noun.
        Group does NOT fire. Singleton 'update@0.25' contributes
        0.5 * 0.25 = 0.125.
        """
        entry = self._doc_writer_entry()
        features = _match_mod.build_features({"task_description": "update the source code"})
        assert _match_mod.score(entry, features) == pytest.approx(0.125, abs=1e-6)

    def test_multiple_satisfied_groups_sum(self) -> None:
        """Spec § 7.3: two satisfied groups on one entry sum independently.

        Skill with two groups; prompt satisfies both.
        Group 1 weight 1.0 → 1.0; group 2 weight 0.5 → 0.5;
        sum = 1.5; min(1.5, 1.0) = 1.0.
        """
        entry = _match_mod.CatalogEntry(
            name="gh-pr-review-address",
            kind="skill",
            triggers=_match_mod.Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=(),
                keywords=(),
                tool_mentions=frozenset(),
                excludes=frozenset(),
                keyword_groups=(
                    _match_mod.KeywordGroup(
                        slots=(
                            _match_mod.Slot(terms=("address", "fix", "handle")),
                            _match_mod.Slot(terms=("review", "comments", "feedback")),
                        ),
                        weight=1.0,
                    ),
                    _match_mod.KeywordGroup(
                        slots=(
                            _match_mod.Slot(terms=("anything",)),
                            _match_mod.Slot(terms=("blocking", "merge")),
                        ),
                        weight=0.5,
                    ),
                ),
            ),
            applicable_agents=(),
            applicable_skills=(),
        )
        features = _match_mod.build_features(
            {"task_description": "address my review comments anything blocking merge"}
        )
        assert _match_mod.score(entry, features) == pytest.approx(1.0, abs=1e-6)

    def test_one_of_two_groups_satisfied(self) -> None:
        """Same entry as above, prompt satisfies only group 1.

        Score = _GROUP_MULTIPLIER * 1.0 (group 1) = 1.0; second group's
        verb slot is unsatisfied so it contributes 0. No singletons.
        """
        entry = _match_mod.CatalogEntry(
            name="gh-pr-review-address",
            kind="skill",
            triggers=_match_mod.Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=(),
                keywords=(),
                tool_mentions=frozenset(),
                excludes=frozenset(),
                keyword_groups=(
                    _match_mod.KeywordGroup(
                        slots=(
                            _match_mod.Slot(terms=("address", "fix", "handle")),
                            _match_mod.Slot(terms=("review", "comments", "feedback")),
                        ),
                        weight=1.0,
                    ),
                    _match_mod.KeywordGroup(
                        slots=(
                            _match_mod.Slot(terms=("anything",)),
                            _match_mod.Slot(terms=("blocking", "merge")),
                        ),
                        weight=0.5,
                    ),
                ),
            ),
            applicable_agents=(),
            applicable_skills=(),
        )
        features = _match_mod.build_features({"task_description": "address my review comments"})
        assert _match_mod.score(entry, features) == pytest.approx(1.0, abs=1e-6)

    def test_no_groups_means_unchanged_behavior(self) -> None:
        """Entry with no keyword_groups scores identically to v0.4.2.

        Regression-locks: doc-writer without groups, same singletons as
        production, scoring 'update the docs' = 0.5*0.25 (update@0.25) +
        0.5*1.0 (docs@1.0) = 0.625.
        """
        entry = _match_mod.CatalogEntry(
            name="doc-writer",
            kind="agent",
            triggers=_match_mod.Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=(),
                keywords=(
                    _match_mod.Keyword("docs", 1.0),
                    _match_mod.Keyword("update", 0.25),
                ),
                tool_mentions=frozenset(),
                excludes=frozenset(),
                keyword_groups=(),
            ),
            applicable_agents=(),
            applicable_skills=(),
        )
        features = _match_mod.build_features({"task_description": "update the docs"})
        assert _match_mod.score(entry, features) == pytest.approx(0.625, abs=1e-6)


class TestDispatchWrapper:
    """The dispatch() in-process wrapper exposed for tests."""

    def test_dispatch_wrapper_returns_decision_dict(self, tmp_path) -> None:
        """dispatch(catalog_path=..., context={...}) returns the decision JSON as a dict."""
        from claude_wayfinder import _dispatch as _disp_mod

        catalog = {
            "schema_version": 1,
            "entries": [
                {
                    "name": "code-writer",
                    "kind": "agent",
                    "description": "Code writer.",
                    "source": "owned",
                    "routable": True,
                    "triggers": {
                        "command_prefixes": [],
                        "agent_mentions": ["code-writer"],
                        "path_globs": [],
                        "keywords": [{"term": "implement", "weight": 1.0}],
                        "tool_mentions": [],
                        "excludes": [],
                    },
                    "applicable_skills": [],
                }
            ],
        }
        import json as _json
        catalog_path = tmp_path / "catalog.json"
        catalog_path.write_text(_json.dumps(catalog))

        result = _disp_mod.dispatch(
            catalog_path=catalog_path,
            context={"task_description": "implement the new module"},
        )
        assert isinstance(result, dict)
        assert "decision" in result
        assert "confidence" in result
        assert "rationale" in result


class TestRationaleListsFiredGroups:
    """matcher_decision.output.rationale surfaces fired groups (AC #7)."""

    def test_rationale_includes_fired_group_when_satisfied(
        self, tmp_path
    ) -> None:
        """A satisfied group appears in the decision rationale string."""
        from claude_wayfinder import _dispatch as _disp_mod

        catalog = {
            "schema_version": 1,
            "entries": [
                {
                    "name": "doc-writer",
                    "kind": "agent",
                    "description": "Doc writer.",
                    "source": "owned",
                    "routable": True,
                    "triggers": {
                        "command_prefixes": [],
                        "agent_mentions": [],
                        "path_globs": [],
                        "keywords": [],
                        "tool_mentions": [],
                        "excludes": [],
                        "keyword_groups": [
                            {
                                "slots": [
                                    {
                                        "name": "verbs",
                                        "terms": ["update", "edit"],
                                    },
                                    {
                                        "name": "nouns",
                                        "terms": ["docs", "readme"],
                                    },
                                ],
                                "weight": 1.0,
                            }
                        ],
                    },
                    "applicable_skills": [],
                }
            ],
        }
        import json

        catalog_path = tmp_path / "catalog.json"
        catalog_path.write_text(json.dumps(catalog))

        result = _disp_mod.dispatch(
            catalog_path=catalog_path,
            # file_paths provides a second feature dimension so feature_count
            # reaches 2 and avoids the needs_more_detail short-circuit.
            context={
                "task_description": "update the docs",
                "file_paths": ["README.md"],
            },
        )
        # The decision's rationale should mention the group having fired.
        assert "group" in result["rationale"].lower()
        # Slot names (when present) appear joined by '+' — e.g. "verbs+nouns".
        assert "verbs+nouns" in result["rationale"]
