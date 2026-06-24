"""Tests for scripts/corpus/builder._load_organic_entries — attribution filter.

Pins the new organic contract from issue #440: only entries stamped with
attribution_source='post_tool_use_hook' count as organic.  Entries with
attribution_source='python_matcher' or NO attribution_source field are
excluded (double-count sources).

All tests are RED until Phase 2 updates _load_organic_entries.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HOOK_ATTRIBUTION = "post_tool_use_hook"
_PYTHON_ATTRIBUTION = "python_matcher"
_ORGANIC_SESSION = "real-session-abc"


def _md(
    session_id: str = _ORGANIC_SESSION,
    **overrides: Any,
) -> dict[str, Any]:
    """Build a minimal matcher_decision entry."""
    entry: dict[str, Any] = {
        "type": "matcher_decision",
        "ts": "2026-06-01T10:00:00.000000Z",
        "session_id": session_id,
        "input": {"task_description": "fix the bug"},
        "output": {
            "decision": "delegate",
            "agent": "code-writer",
            "confidence": 1.0,
            "rationale": "matched keywords",
            "alternatives": [],
        },
        "catalog_hash": "sha256:abc123",
        "matcher_version": "abc1234",
    }
    entry.update(overrides)
    return entry


def _hook_entry(session_id: str = _ORGANIC_SESSION) -> dict[str, Any]:
    """Build a hook-stamped matcher_decision entry (canonical organic)."""
    return _md(session_id=session_id, attribution_source=_HOOK_ATTRIBUTION)


def _python_entry(session_id: str = _ORGANIC_SESSION) -> dict[str, Any]:
    """Build a python_matcher-stamped entry (non-organic twin)."""
    return _md(session_id=session_id, attribution_source=_PYTHON_ATTRIBUTION)


def _no_attribution_entry(session_id: str = _ORGANIC_SESSION) -> dict[str, Any]:
    """Build an entry with no attribution_source key (pre-#440, non-organic)."""
    entry = _md(session_id=session_id)
    entry.pop("attribution_source", None)
    return entry


def _empty_session_entry(session_id: str = "") -> dict[str, Any]:
    """Build a hook entry with an empty session_id (fixture, excluded)."""
    return _md(session_id=session_id, attribution_source=_HOOK_ATTRIBUTION)


def _write_jsonl(tmp_path: Path, entries: list[Any]) -> Path:
    """Write entries as JSONL and return the path."""
    p = tmp_path / "dispatch-log.jsonl"
    with p.open("w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry) + "\n")
    return p


# ---------------------------------------------------------------------------
# _load_organic_entries — attribution-aware organic filter
# ---------------------------------------------------------------------------


class TestLoadOrganicEntriesAttributionFilter:
    """_load_organic_entries must apply the strict attribution_source check.

    New rule after #440: only attribution_source='post_tool_use_hook'
    entries qualify as organic.  python_matcher twins and no-attribution
    entries are excluded.

    All tests RED until Phase 2 updates _load_organic_entries.
    """

    def test_four_entry_mix_returns_only_hook_entry(
        self, tmp_path: Path
    ) -> None:
        """Four-entry JSONL: only the hook entry is returned.

        Input:
          line 1 — hook entry       (organic, included)
          line 2 — python_matcher   (twin, excluded)
          line 3 — no-attribution   (excluded)
          line 4 — empty session_id (excluded)

        Expected: one (line_no, obj) tuple for the hook entry.
        RED: current _load_organic_entries does not filter on attribution_source.
        """
        from scripts.corpus.builder import _load_organic_entries

        hook = _hook_entry()
        python_twin = _python_entry()
        no_attr = _no_attribution_entry()
        empty_sess = _empty_session_entry()

        log = _write_jsonl(tmp_path, [hook, python_twin, no_attr, empty_sess])
        result = _load_organic_entries(log)

        assert len(result) == 1, (
            f"Expected 1 organic entry (hook only), got {len(result)}"
        )
        _line_no, obj = result[0]
        assert obj["attribution_source"] == _HOOK_ATTRIBUTION
        assert obj["session_id"] == _ORGANIC_SESSION

    def test_hook_entry_line_number_is_one(self, tmp_path: Path) -> None:
        """Hook entry on line 1 returns line_no=1."""
        from scripts.corpus.builder import _load_organic_entries

        log = _write_jsonl(tmp_path, [_hook_entry()])
        result = _load_organic_entries(log)

        assert len(result) == 1
        line_no, _ = result[0]
        assert line_no == 1

    def test_python_matcher_entry_not_returned(self, tmp_path: Path) -> None:
        """A python_matcher entry alone returns an empty list."""
        from scripts.corpus.builder import _load_organic_entries

        log = _write_jsonl(tmp_path, [_python_entry()])
        result = _load_organic_entries(log)

        assert result == [], (
            "python_matcher entry must not appear in organic results"
        )

    def test_no_attribution_entry_not_returned(self, tmp_path: Path) -> None:
        """An entry with no attribution_source key returns an empty list."""
        from scripts.corpus.builder import _load_organic_entries

        log = _write_jsonl(tmp_path, [_no_attribution_entry()])
        result = _load_organic_entries(log)

        assert result == [], (
            "No-attribution entry must not appear in organic results"
        )

    def test_empty_session_id_entry_not_returned(self, tmp_path: Path) -> None:
        """Hook entry with empty session_id is excluded (fixture gate)."""
        from scripts.corpus.builder import _load_organic_entries

        log = _write_jsonl(tmp_path, [_empty_session_entry()])
        result = _load_organic_entries(log)

        assert result == []
