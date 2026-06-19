"""Tests for scripts.corpus.eval._reader.

RED tests — written before implementation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Import under test (will fail RED until implemented)
# ---------------------------------------------------------------------------
from scripts.corpus.eval._reader import (
    load_corpus,
    load_labels,
)

# ---------------------------------------------------------------------------
# load_corpus — basic contract
# ---------------------------------------------------------------------------


class TestLoadCorpus:
    """Tests for load_corpus()."""

    def test_returns_list_of_corpus_entries(
        self, fixture_corpus_path: Path
    ) -> None:
        """load_corpus returns a list of CorpusEntry objects."""
        entries = load_corpus(fixture_corpus_path)
        assert isinstance(entries, list)
        assert len(entries) == 14

    def test_corpus_entry_has_corpus_id(
        self, fixture_corpus_path: Path
    ) -> None:
        """Each CorpusEntry has a corpus_id field."""
        entries = load_corpus(fixture_corpus_path)
        assert entries[0].corpus_id == 1
        assert entries[13].corpus_id == 14

    def test_corpus_entry_has_task_description(
        self, fixture_corpus_path: Path
    ) -> None:
        """Each CorpusEntry has a non-empty task_description."""
        entries = load_corpus(fixture_corpus_path)
        for entry in entries:
            assert isinstance(entry.task_description, str)
            assert len(entry.task_description) > 0

    def test_corpus_entry_has_file_paths_as_list(
        self, fixture_corpus_path: Path
    ) -> None:
        """file_paths is a list (possibly empty)."""
        entries = load_corpus(fixture_corpus_path)
        for entry in entries:
            assert isinstance(entry.file_paths, list)

    def test_corpus_entry_has_stratum(
        self, fixture_corpus_path: Path
    ) -> None:
        """Each CorpusEntry has a stratum dict with required fields."""
        entries = load_corpus(fixture_corpus_path)
        for entry in entries:
            assert isinstance(entry.stratum, dict)
            assert "decision_band" in entry.stratum
            assert "td_length_band" in entry.stratum
            assert "file_paths_present" in entry.stratum

    def test_corpus_entry_has_command_prefix_possibly_none(
        self, fixture_corpus_path: Path
    ) -> None:
        """command_prefix is either a string or None."""
        entries = load_corpus(fixture_corpus_path)
        p13 = next(e for e in entries if e.corpus_id == 13)
        assert p13.command_prefix == "gh"
        p1 = next(e for e in entries if e.corpus_id == 1)
        assert p1.command_prefix is None

    def test_skips_blank_lines(self, tmp_path: Path) -> None:
        """load_corpus ignores blank lines in the JSONL file."""
        import json

        corpus_file = tmp_path / "corpus.jsonl"
        # Use the real nested shape emitted by the builder
        record = {
            "type": "matcher_decision",
            "session_id": "session-blank-test",
            "input": {
                "task_description": "hello",
                "file_paths": [],
                "agent_mentions": [],
                "tool_mentions": [],
                "command_prefix": None,
            },
            "output": {"decision": "delegate"},
            "corpus_id": 1,
            "stratum": {
                "decision_band": "delegate",
                "td_length_band": "short",
                "file_paths_present": False,
            },
        }
        corpus_file.write_text(
            json.dumps(record) + "\n\n" + json.dumps(record) + "\n",
            encoding="utf-8",
        )
        entries = load_corpus(corpus_file)
        assert len(entries) == 2

    def test_file_not_found_raises(self, tmp_path: Path) -> None:
        """load_corpus raises FileNotFoundError for missing files."""
        with pytest.raises(FileNotFoundError):
            load_corpus(tmp_path / "nonexistent.jsonl")

    def test_preserves_agent_mentions(
        self, fixture_corpus_path: Path
    ) -> None:
        """agent_mentions field is present and is a list."""
        entries = load_corpus(fixture_corpus_path)
        for entry in entries:
            assert isinstance(entry.agent_mentions, list)

    def test_preserves_tool_mentions(
        self, fixture_corpus_path: Path
    ) -> None:
        """tool_mentions field is present and is a list."""
        entries = load_corpus(fixture_corpus_path)
        for entry in entries:
            assert isinstance(entry.tool_mentions, list)


# ---------------------------------------------------------------------------
# load_labels — basic contract
# ---------------------------------------------------------------------------


class TestLoadLabels:
    """Tests for load_labels()."""

    def test_returns_dict_keyed_by_corpus_id(
        self, fixture_labels_path: Path
    ) -> None:
        """load_labels returns a dict mapping corpus_id -> GoldLabel."""
        labels = load_labels(fixture_labels_path)
        assert isinstance(labels, dict)
        assert len(labels) == 14

    def test_gold_label_has_required_fields(
        self, fixture_labels_path: Path
    ) -> None:
        """Each GoldLabel has domain, posture, gold_agent, is_any."""
        labels = load_labels(fixture_labels_path)
        for label in labels.values():
            assert hasattr(label, "corpus_id")
            assert hasattr(label, "domain")
            assert hasattr(label, "posture")
            assert hasattr(label, "gold_agent")
            assert hasattr(label, "is_any")

    def test_gold_label_values_p1(
        self, fixture_labels_path: Path
    ) -> None:
        """P1 gold label has expected values."""
        labels = load_labels(fixture_labels_path)
        label = labels[1]
        assert label.domain == "data"
        assert label.posture == "verify"
        assert label.gold_agent == "auditor"
        assert label.is_any is False

    def test_label_file_not_found_raises(self, tmp_path: Path) -> None:
        """load_labels raises FileNotFoundError for missing file."""
        with pytest.raises(FileNotFoundError):
            load_labels(tmp_path / "nonexistent.jsonl")

    def test_none_when_path_is_none(self) -> None:
        """load_labels(None) returns an empty dict (labels optional)."""
        labels = load_labels(None)
        assert labels == {}


# ---------------------------------------------------------------------------
# GoldLabel.area_span — issue #396 (RED until Phase 2 implementation)
# ---------------------------------------------------------------------------


class TestGoldLabelAreaSpan:
    """GoldLabel gains a trailing ``area_span: int = 1`` field (#396).

    All tests in this class must be RED until:
      1. ``GoldLabel`` dataclass gains ``area_span: int = 1`` field.
      2. ``_parse_label_record`` reads ``area_span=int(record.get(..., 1))``.
    """

    def test_area_span_2_parsed_from_label_record(
        self, tmp_path: Path
    ) -> None:
        """A record with 'area_span': 2 → parsed GoldLabel.area_span == 2.

        Writes a minimal gold-label JSONL containing ``area_span: 2`` and
        confirms that ``load_labels`` surfaces the parsed value via
        ``GoldLabel.area_span``.
        """
        import json

        label_record = {
            "corpus_id": 33660,
            "domain": "code",
            "posture": "diagnose",
            "gold_agent": "investigator",
            "is_any": False,
            "area_span": 2,
        }
        labels_file = tmp_path / "labels.jsonl"
        labels_file.write_text(
            json.dumps(label_record) + "\n",
            encoding="utf-8",
        )
        labels = load_labels(labels_file)
        assert 33660 in labels, (
            "corpus_id 33660 must be present in loaded labels"
        )
        label = labels[33660]
        assert label.area_span == 2, (
            f"Expected GoldLabel.area_span == 2 for record with "
            f"'area_span': 2; got {label.area_span!r}. "
            f"GoldLabel likely lacks the area_span field (issue #396)."
        )

    def test_area_span_defaults_to_1_when_absent(
        self, tmp_path: Path
    ) -> None:
        """A record with NO 'area_span' key → GoldLabel.area_span == 1.

        The field must default to 1 (single-layer) when the key is absent
        from the raw record, preserving backward compatibility with all
        existing gold-label rows that predate issue #396.
        """
        import json

        label_record = {
            "corpus_id": 34774,
            "domain": "code",
            "posture": "diagnose",
            "gold_agent": "researcher",
            "is_any": False,
            # area_span intentionally absent
        }
        labels_file = tmp_path / "labels.jsonl"
        labels_file.write_text(
            json.dumps(label_record) + "\n",
            encoding="utf-8",
        )
        labels = load_labels(labels_file)
        assert 34774 in labels, (
            "corpus_id 34774 must be present in loaded labels"
        )
        label = labels[34774]
        assert label.area_span == 1, (
            f"Expected GoldLabel.area_span == 1 (default) for record "
            f"with no 'area_span' key; got {label.area_span!r}. "
            f"_parse_label_record must default to int(record.get"
            f"('area_span', 1)) (issue #396)."
        )


# ---------------------------------------------------------------------------
# Contract test: reader must parse the real builder-emitted shape
# ---------------------------------------------------------------------------


class TestBuilderShapeContract:
    """Reader must parse the phase-A corpus shape produced by builder.py.

    The builder emits original log entry fields (including ``input`` nested
    dict) PLUS ``corpus_id`` and ``stratum`` at the top level.  Dispatch-
    context fields (task_description, file_paths, agent_mentions,
    tool_mentions, command_prefix) live inside ``input``, NOT at top level.
    """

    # Minimal record hand-constructed from builder augmentation logic:
    # - original log entry has top-level: type, session_id, input{}, output{}
    # - builder adds: corpus_id (line number), stratum dict
    _BUILDER_EMITTED_RECORD = {
        "type": "matcher_decision",
        "session_id": "session-contract-001",
        "input": {
            "task_description": "Implement the new cache module.",
            "file_paths": ["src/cache.py", "tests/test_cache.py"],
            "agent_mentions": ["code-writer"],
            "tool_mentions": ["Read"],
            "command_prefix": None,
        },
        "output": {
            "decision": "delegate",
            "agent": "code-writer",
            "confidence": 0.9,
        },
        "corpus_id": 42,
        "stratum": {
            "decision_band": "delegate",
            "td_length_band": "short",
            "file_paths_present": True,
        },
    }

    def test_parses_task_description_from_input(
        self, tmp_path: Path
    ) -> None:
        """task_description must be read from record['input'], not top level."""
        import json

        corpus_file = tmp_path / "contract.jsonl"
        corpus_file.write_text(
            json.dumps(self._BUILDER_EMITTED_RECORD) + "\n",
            encoding="utf-8",
        )
        entries = load_corpus(corpus_file)
        assert len(entries) == 1
        entry = entries[0]
        assert entry.task_description == "Implement the new cache module."

    def test_parses_file_paths_from_input(self, tmp_path: Path) -> None:
        """file_paths must be read from record['input']['file_paths']."""
        import json

        corpus_file = tmp_path / "contract.jsonl"
        corpus_file.write_text(
            json.dumps(self._BUILDER_EMITTED_RECORD) + "\n",
            encoding="utf-8",
        )
        entries = load_corpus(corpus_file)
        entry = entries[0]
        assert entry.file_paths == ["src/cache.py", "tests/test_cache.py"]

    def test_parses_agent_mentions_from_input(self, tmp_path: Path) -> None:
        """agent_mentions must be read from record['input']."""
        import json

        corpus_file = tmp_path / "contract.jsonl"
        corpus_file.write_text(
            json.dumps(self._BUILDER_EMITTED_RECORD) + "\n",
            encoding="utf-8",
        )
        entries = load_corpus(corpus_file)
        entry = entries[0]
        assert entry.agent_mentions == ["code-writer"]

    def test_parses_tool_mentions_from_input(self, tmp_path: Path) -> None:
        """tool_mentions must be read from record['input']."""
        import json

        corpus_file = tmp_path / "contract.jsonl"
        corpus_file.write_text(
            json.dumps(self._BUILDER_EMITTED_RECORD) + "\n",
            encoding="utf-8",
        )
        entries = load_corpus(corpus_file)
        entry = entries[0]
        assert entry.tool_mentions == ["Read"]

    def test_parses_command_prefix_from_input(self, tmp_path: Path) -> None:
        """command_prefix must be read from record['input']."""
        import json

        # A record where input has a command_prefix set
        record_with_prefix = {
            **self._BUILDER_EMITTED_RECORD,
            "input": {
                **self._BUILDER_EMITTED_RECORD["input"],
                "command_prefix": "gh",
            },
        }
        corpus_file = tmp_path / "contract.jsonl"
        corpus_file.write_text(
            json.dumps(record_with_prefix) + "\n",
            encoding="utf-8",
        )
        entries = load_corpus(corpus_file)
        entry = entries[0]
        assert entry.command_prefix == "gh"

    def test_corpus_id_and_stratum_stay_top_level(
        self, tmp_path: Path
    ) -> None:
        """corpus_id and stratum remain top-level fields (not in input)."""
        import json

        corpus_file = tmp_path / "contract.jsonl"
        corpus_file.write_text(
            json.dumps(self._BUILDER_EMITTED_RECORD) + "\n",
            encoding="utf-8",
        )
        entries = load_corpus(corpus_file)
        entry = entries[0]
        assert entry.corpus_id == 42
        assert entry.stratum == {
            "decision_band": "delegate",
            "td_length_band": "short",
            "file_paths_present": True,
        }

    def test_missing_input_dict_yields_empty_defaults(
        self, tmp_path: Path
    ) -> None:
        """When 'input' key is absent, all context fields default to empty."""
        import json

        record_no_input = {
            "type": "matcher_decision",
            "session_id": "session-no-input",
            "corpus_id": 99,
            "stratum": {
                "decision_band": "advisory",
                "td_length_band": "empty",
                "file_paths_present": False,
            },
        }
        corpus_file = tmp_path / "contract.jsonl"
        corpus_file.write_text(
            json.dumps(record_no_input) + "\n", encoding="utf-8"
        )
        entries = load_corpus(corpus_file)
        entry = entries[0]
        assert entry.task_description == ""
        assert entry.file_paths == []
        assert entry.agent_mentions == []
        assert entry.tool_mentions == []
        assert entry.command_prefix is None
