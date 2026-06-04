"""Tests for claude_wayfinder.log_filter — organic-traffic extraction.

Covers:
  1. Organic-entry selection (type='matcher_decision' + non-empty session_id)
  2. Empty session_id exclusion
  3. Non-'matcher_decision' type exclusion
  4. Malformed-line skipping
  5. Empty-file / missing-file handling
  6. Default path resolution (env var + Path.home() fallback)
  7. CLI subcommand: count output
  8. CLI subcommand: --emit-jsonl output
  9. CLI subcommand: --log-path override
  10. Structured-record field completeness

Tests MUST use inline/fixture JSONL written to tmp_path — never read the
live ~/.claude/state/dispatch-log.jsonl (it is mutable and machine-specific).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ORGANIC_SESSION = "abc-123-real-session"
_FIXTURE_SESSION = ""  # empty → fixture-contaminated / pre-fix
_OTHER_SESSION = "cached-session"


def _md(session_id: str = _ORGANIC_SESSION, **overrides: Any) -> dict[str, Any]:
    """Build a minimal matcher_decision entry."""
    entry: dict[str, Any] = {
        "type": "matcher_decision",
        "ts": "2026-05-29T12:00:00.000000Z",
        "session_id": session_id,
        "input": {"task_description": "fix the bug"},
        "output": {
            "decision": "delegate",
            "agent": "code-writer",
            "confidence": 1.0,
            "rationale": "matched keywords: fix",
            "alternatives": [],
        },
        "catalog_hash": "sha256:abc123",
        "matcher_version": "abc1234",
    }
    entry.update(overrides)
    return entry


def _other_event(event_type: str = "agent_dispatch") -> dict[str, Any]:
    """Build a non-matcher_decision log entry."""
    return {
        "type": event_type,
        "ts": "2026-05-29T12:00:00.000000Z",
        "session_id": _ORGANIC_SESSION,
        "agent": "code-writer",
    }


def _write_jsonl(tmp_path: Path, entries: list[Any]) -> Path:
    """Write entries to a JSONL file in tmp_path and return its path."""
    p = tmp_path / "dispatch-log.jsonl"
    with p.open("w", encoding="utf-8") as f:
        for entry in entries:
            if isinstance(entry, str):
                f.write(entry + "\n")
            else:
                f.write(json.dumps(entry) + "\n")
    return p


# ---------------------------------------------------------------------------
# 1. Organic-entry selection
# ---------------------------------------------------------------------------


def test_organic_entry_selected(tmp_path: Path) -> None:
    """A matcher_decision entry with non-empty session_id is returned."""
    from claude_wayfinder.log_filter import load_organic_decisions

    log = _write_jsonl(tmp_path, [_md(session_id=_ORGANIC_SESSION)])
    results = load_organic_decisions(log)

    assert len(results) == 1
    assert results[0]["session_id"] == _ORGANIC_SESSION
    assert results[0]["type"] == "matcher_decision"


def test_multiple_organic_entries_all_returned(tmp_path: Path) -> None:
    """All organic matcher_decision entries are returned, in order."""
    from claude_wayfinder.log_filter import load_organic_decisions

    entries = [
        _md(session_id="s1"),
        _md(session_id="s2"),
        _md(session_id="s3"),
    ]
    log = _write_jsonl(tmp_path, entries)
    results = load_organic_decisions(log)

    assert len(results) == 3
    assert [r["session_id"] for r in results] == ["s1", "s2", "s3"]


# ---------------------------------------------------------------------------
# 2. Empty session_id exclusion
# ---------------------------------------------------------------------------


def test_empty_session_id_excluded(tmp_path: Path) -> None:
    """A matcher_decision with empty session_id is excluded."""
    from claude_wayfinder.log_filter import load_organic_decisions

    log = _write_jsonl(tmp_path, [_md(session_id=_FIXTURE_SESSION)])
    results = load_organic_decisions(log)

    assert results == []


def test_missing_session_id_field_excluded(tmp_path: Path) -> None:
    """A matcher_decision with no session_id key is excluded."""
    from claude_wayfinder.log_filter import load_organic_decisions

    entry = {
        "type": "matcher_decision",
        "ts": "2026-05-29T12:00:00.000000Z",
        "input": {"task_description": "do something"},
        "output": {"decision": "delegate", "confidence": 1.0},
    }
    log = _write_jsonl(tmp_path, [entry])
    results = load_organic_decisions(log)

    assert results == []


def test_mixed_entries_only_organic_returned(tmp_path: Path) -> None:
    """Mix of organic and fixture entries: only organic ones returned."""
    from claude_wayfinder.log_filter import load_organic_decisions

    log = _write_jsonl(
        tmp_path,
        [
            _md(session_id=_FIXTURE_SESSION),
            _md(session_id=_ORGANIC_SESSION),
            _md(session_id=_FIXTURE_SESSION),
            _md(session_id="another-real"),
        ],
    )
    results = load_organic_decisions(log)

    assert len(results) == 2
    assert results[0]["session_id"] == _ORGANIC_SESSION
    assert results[1]["session_id"] == "another-real"


# ---------------------------------------------------------------------------
# 3. Non-matcher_decision type exclusion
# ---------------------------------------------------------------------------


def test_agent_dispatch_excluded(tmp_path: Path) -> None:
    """agent_dispatch events are not matcher_decision and must be excluded."""
    from claude_wayfinder.log_filter import load_organic_decisions

    log = _write_jsonl(tmp_path, [_other_event("agent_dispatch")])
    results = load_organic_decisions(log)

    assert results == []


def test_skill_invocation_excluded(tmp_path: Path) -> None:
    """skill_invocation events must be excluded."""
    from claude_wayfinder.log_filter import load_organic_decisions

    log = _write_jsonl(tmp_path, [_other_event("skill_invocation")])
    results = load_organic_decisions(log)

    assert results == []


def test_matcher_session_id_event_excluded(tmp_path: Path) -> None:
    """matcher_session_id events are not matcher_decision and must be excluded."""
    from claude_wayfinder.log_filter import load_organic_decisions

    log = _write_jsonl(tmp_path, [_other_event("matcher_session_id")])
    results = load_organic_decisions(log)

    assert results == []


def test_non_dict_json_line_excluded(tmp_path: Path) -> None:
    """A valid JSON line that is not a dict (e.g. a list) is skipped."""
    from claude_wayfinder.log_filter import load_organic_decisions

    log = _write_jsonl(tmp_path, ['["not", "a", "dict"]'])
    results = load_organic_decisions(log)

    assert results == []


# ---------------------------------------------------------------------------
# 4. Malformed-line skipping
# ---------------------------------------------------------------------------


def test_malformed_line_skipped(tmp_path: Path) -> None:
    """A malformed (non-JSON) line is silently skipped."""
    from claude_wayfinder.log_filter import load_organic_decisions

    entries = [
        "this is not json",
        _md(session_id=_ORGANIC_SESSION),
    ]
    log = _write_jsonl(tmp_path, entries)
    results = load_organic_decisions(log)

    assert len(results) == 1


def test_partial_json_line_skipped(tmp_path: Path) -> None:
    """A truncated/partial JSON line is silently skipped."""
    from claude_wayfinder.log_filter import load_organic_decisions

    entries = [
        '{"type": "matcher_decision", "session_id"',  # truncated
        _md(session_id=_ORGANIC_SESSION),
    ]
    log = _write_jsonl(tmp_path, entries)
    results = load_organic_decisions(log)

    assert len(results) == 1


def test_multiple_malformed_lines_all_skipped(tmp_path: Path) -> None:
    """Multiple malformed lines are all skipped; valid organic entries kept."""
    from claude_wayfinder.log_filter import load_organic_decisions

    entries = [
        "garbage line 1",
        "{broken json",
        _md(session_id="s1"),
        "garbage line 3",
        _md(session_id="s2"),
    ]
    log = _write_jsonl(tmp_path, entries)
    results = load_organic_decisions(log)

    assert len(results) == 2


# ---------------------------------------------------------------------------
# 5. Empty-file / missing-file handling
# ---------------------------------------------------------------------------


def test_empty_file_returns_empty_list(tmp_path: Path) -> None:
    """An empty JSONL file returns an empty list without error."""
    from claude_wayfinder.log_filter import load_organic_decisions

    log = tmp_path / "dispatch-log.jsonl"
    log.write_text("", encoding="utf-8")
    results = load_organic_decisions(log)

    assert results == []


def test_missing_file_returns_empty_list(tmp_path: Path) -> None:
    """A path that does not exist returns an empty list without error."""
    from claude_wayfinder.log_filter import load_organic_decisions

    log = tmp_path / "nonexistent.jsonl"
    results = load_organic_decisions(log)

    assert results == []


def test_blank_lines_in_file_are_skipped(tmp_path: Path) -> None:
    """Blank lines in the JSONL file do not cause errors."""
    from claude_wayfinder.log_filter import load_organic_decisions

    p = tmp_path / "dispatch-log.jsonl"
    with p.open("w", encoding="utf-8") as f:
        f.write("\n")
        f.write(json.dumps(_md(session_id=_ORGANIC_SESSION)) + "\n")
        f.write("\n\n")
        f.write(json.dumps(_md(session_id="s2")) + "\n")
    results = load_organic_decisions(p)

    assert len(results) == 2


# ---------------------------------------------------------------------------
# 6. Default path resolution
# ---------------------------------------------------------------------------


def test_default_log_path_uses_env_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DISPATCH_LOG env var overrides the default ~/.claude/state path."""
    from claude_wayfinder.log_filter import default_log_path

    log = _write_jsonl(tmp_path, [_md(session_id=_ORGANIC_SESSION)])
    monkeypatch.setenv("DISPATCH_LOG", str(log))

    path = default_log_path()

    assert path == log


def test_default_log_path_falls_back_to_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When DISPATCH_LOG is unset, default path is ~/.claude/state/dispatch-log.jsonl."""
    from claude_wayfinder.log_filter import default_log_path

    monkeypatch.delenv("DISPATCH_LOG", raising=False)

    path = default_log_path()

    assert path == Path.home() / ".claude" / "state" / "dispatch-log.jsonl"


# ---------------------------------------------------------------------------
# 7. Structured-record field completeness
# ---------------------------------------------------------------------------


def test_returned_record_preserves_all_fields(tmp_path: Path) -> None:
    """The returned record is the full original dict, not a partial extraction."""
    from claude_wayfinder.log_filter import load_organic_decisions

    entry = _md(
        session_id=_ORGANIC_SESSION,
        override_id=None,
    )
    log = _write_jsonl(tmp_path, [entry])
    results = load_organic_decisions(log)

    assert len(results) == 1
    # All original keys must survive.
    for key in entry:
        assert key in results[0], f"key {key!r} missing from returned record"


# ---------------------------------------------------------------------------
# 8. CLI: count output
# ---------------------------------------------------------------------------

_PYTHON = sys.executable


def test_cli_log_filter_count_output(tmp_path: Path) -> None:
    """``log-filter`` subcommand prints organic count to stdout."""
    log = _write_jsonl(
        tmp_path,
        [
            _md(session_id=_ORGANIC_SESSION),
            _md(session_id=_FIXTURE_SESSION),
            _md(session_id="real-2"),
        ],
    )
    result = subprocess.run(
        [_PYTHON, "-m", "claude_wayfinder", "log-filter", "--log-path", str(log)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 0
    assert "2" in result.stdout


def test_cli_log_filter_missing_file_exits_zero(tmp_path: Path) -> None:
    """``log-filter`` on a missing file exits 0 and prints count of 0."""
    log = tmp_path / "nonexistent.jsonl"
    result = subprocess.run(
        [_PYTHON, "-m", "claude_wayfinder", "log-filter", "--log-path", str(log)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 0
    assert "0" in result.stdout


# ---------------------------------------------------------------------------
# 9. CLI: --emit-jsonl output
# ---------------------------------------------------------------------------


def test_cli_emit_jsonl_outputs_organic_entries(tmp_path: Path) -> None:
    """``--emit-jsonl`` writes one organic entry per line to stdout."""
    log = _write_jsonl(
        tmp_path,
        [
            _md(session_id=_FIXTURE_SESSION),
            _md(session_id=_ORGANIC_SESSION),
            _md(session_id="real-2"),
        ],
    )
    result = subprocess.run(
        [
            _PYTHON,
            "-m",
            "claude_wayfinder",
            "log-filter",
            "--log-path",
            str(log),
            "--emit-jsonl",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 0
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    assert len(lines) == 2
    parsed = [json.loads(ln) for ln in lines]
    assert parsed[0]["session_id"] == _ORGANIC_SESSION
    assert parsed[1]["session_id"] == "real-2"


def test_cli_emit_jsonl_no_entries_produces_no_lines(tmp_path: Path) -> None:
    """``--emit-jsonl`` with zero organic entries produces no output lines."""
    log = _write_jsonl(tmp_path, [_md(session_id=_FIXTURE_SESSION)])
    result = subprocess.run(
        [
            _PYTHON,
            "-m",
            "claude_wayfinder",
            "log-filter",
            "--log-path",
            str(log),
            "--emit-jsonl",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 0
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    assert lines == []


# ---------------------------------------------------------------------------
# 10. Existing CLI subcommands are unaffected
# ---------------------------------------------------------------------------


def test_help_still_works_after_log_filter_added() -> None:
    """``python -m claude_wayfinder --help`` exits 0 after adding log-filter."""
    result = subprocess.run(
        [_PYTHON, "-m", "claude_wayfinder", "--help"],
        capture_output=True,
    )
    # Decode with errors=replace to tolerate non-UTF-8 bytes in help strings
    # (the existing description uses a cp1252 em-dash \x97).
    stdout = result.stdout.decode("utf-8", errors="replace")

    assert result.returncode == 0
    assert "log-filter" in stdout


def test_dispatch_help_unaffected() -> None:
    """``python -m claude_wayfinder dispatch --help`` still exits 0."""
    result = subprocess.run(
        [_PYTHON, "-m", "claude_wayfinder", "dispatch", "--help"],
        capture_output=True,
    )

    assert result.returncode == 0
