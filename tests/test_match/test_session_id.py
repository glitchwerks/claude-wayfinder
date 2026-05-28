"""Regression tests for issue #294 — session_id propagation in matcher log.

These tests assert three distinct behaviors for the ``session_id`` field
in the ``matcher_decision`` log entry emitted by ``_write_log_entry``:

1. When the input JSON contains a ``session_id`` field, that value is
   written verbatim to the log entry (highest precedence).
2. When the input JSON omits ``session_id`` but the ``CLAUDE_SESSION_ID``
   env var is set, the env var value is written (fallback).
3. When neither is present, the log entry carries an empty string (today's
   behavior preserved; no regression).

All three cases exercise both the unit path (direct ``_write_log_entry``
call) and the subprocess path (full matcher run via ``_run``), so the
fix is verified end-to-end.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.test_match.conftest import (
    _LOG_TEST_CATALOG,
    _LOG_TEST_INPUT,
    _run,
)

# ---------------------------------------------------------------------------
# Unit tests — direct _write_log_entry calls
# ---------------------------------------------------------------------------


class TestWriteLogEntrySessionIdUnit:
    """Unit-level tests for session_id precedence in _write_log_entry."""

    def test_input_session_id_written_to_log(self, tmp_path: Path) -> None:
        """When input_dict contains session_id, that value is in the entry.

        Priority 1: caller-supplied value in the dispatch context JSON
        must win over env var and empty default.
        """
        from claude_wayfinder.match._catalog import _write_log_entry

        log_path = tmp_path / "log.jsonl"
        _write_log_entry(
            {"task_description": "test task", "session_id": "abc-123"},
            {"decision": "delegate"},
            "sha256:abc",
            log_path,
        )
        entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert entry["session_id"] == "abc-123"

    def test_env_var_session_id_used_when_input_omits_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When input omits session_id but env var is set, env var wins.

        Priority 2: CLAUDE_SESSION_ID env var provides the value when
        the dispatch context JSON does not include session_id.
        """
        from claude_wayfinder.match._catalog import _write_log_entry

        monkeypatch.setenv("CLAUDE_SESSION_ID", "env-session-xyz")
        log_path = tmp_path / "log.jsonl"
        _write_log_entry(
            {"task_description": "test task"},  # no session_id key
            {"decision": "self_handle"},
            "sha256:def",
            log_path,
        )
        entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert entry["session_id"] == "env-session-xyz"

    def test_empty_string_when_neither_input_nor_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When neither input nor env provides session_id, entry carries ''.

        Priority 3 / today's behavior: the field is present but empty
        when no session information is available.
        """
        from claude_wayfinder.match._catalog import _write_log_entry

        monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
        log_path = tmp_path / "log.jsonl"
        _write_log_entry(
            {"task_description": "test task"},  # no session_id key
            {"decision": "self_handle"},
            "sha256:ghi",
            log_path,
        )
        entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert entry["session_id"] == ""

    def test_input_session_id_beats_env_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Input session_id has higher precedence than CLAUDE_SESSION_ID env.

        Even when the env var is set, the caller-supplied value in
        input_dict must be written to the log entry.
        """
        from claude_wayfinder.match._catalog import _write_log_entry

        monkeypatch.setenv("CLAUDE_SESSION_ID", "env-session-should-not-win")
        log_path = tmp_path / "log.jsonl"
        _write_log_entry(
            {"task_description": "test task", "session_id": "input-wins"},
            {"decision": "delegate"},
            "sha256:jkl",
            log_path,
        )
        entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert entry["session_id"] == "input-wins"


# ---------------------------------------------------------------------------
# Subprocess (end-to-end) tests — full matcher run via _run()
# ---------------------------------------------------------------------------


class TestMatcherSessionIdEndToEnd:
    """End-to-end tests: session_id flows from stdin JSON into the log entry.

    These tests run the matcher as a subprocess (matching CI behavior) to
    confirm the fix survives the process boundary that caused the original
    bug: os.environ.get("CLAUDE_SESSION_ID", "") was always empty because
    the env var was never set in the child process.
    """

    def test_session_id_in_stdin_appears_in_log(self, tmp_path: Path) -> None:
        """session_id from the dispatch input JSON is written to the log.

        The matcher subprocess receives session_id in its stdin JSON and
        must write that value into the matcher_decision log entry.
        """
        log_path = tmp_path / "e2e.jsonl"
        stdin_with_session = {
            **_LOG_TEST_INPUT,
            "session_id": "e2e-session-456",
        }
        result = _run(
            stdin_with_session,
            _LOG_TEST_CATALOG,
            extra_env={"DISPATCH_LOG_PATH": str(log_path)},
            tmp_path=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        assert log_path.exists(), "Log file was not created"

        entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert entry["session_id"] == "e2e-session-456", (
            f"Expected 'e2e-session-456' in log entry session_id, "
            f"got: {entry['session_id']!r}"
        )

    def test_env_var_session_id_used_when_stdin_omits_it(
        self, tmp_path: Path
    ) -> None:
        """CLAUDE_SESSION_ID env var is used when stdin JSON omits session_id.

        The env var is propagated into the subprocess environment so the
        matcher can read it when no input-level session_id is present.
        """
        log_path = tmp_path / "e2e-env.jsonl"
        result = _run(
            _LOG_TEST_INPUT,  # no session_id in stdin
            _LOG_TEST_CATALOG,
            extra_env={
                "DISPATCH_LOG_PATH": str(log_path),
                "CLAUDE_SESSION_ID": "env-e2e-session",
            },
            tmp_path=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        assert log_path.exists(), "Log file was not created"

        entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert entry["session_id"] == "env-e2e-session", (
            f"Expected 'env-e2e-session' in log entry session_id, "
            f"got: {entry['session_id']!r}"
        )

    def test_empty_session_id_when_neither_present(self, tmp_path: Path) -> None:
        """Log entry carries '' session_id when no input or env value exists.

        Pre-fix baseline behavior: when nothing provides a session_id,
        the field is present but empty — not missing, not None.
        """
        log_path = tmp_path / "e2e-empty.jsonl"
        clean_env: dict[str, str] = {
            k: v
            for k, v in os.environ.items()
            if k != "CLAUDE_SESSION_ID"
        }
        clean_env["DISPATCH_LOG_PATH"] = str(log_path)

        result = _run(
            _LOG_TEST_INPUT,  # no session_id in stdin
            _LOG_TEST_CATALOG,
            extra_env=clean_env,
            tmp_path=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        assert log_path.exists(), "Log file was not created"

        entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert "session_id" in entry, "session_id key must always be present in log entry"
        assert entry["session_id"] == "", (
            f"Expected empty string session_id when nothing provides it; "
            f"got: {entry['session_id']!r}"
        )
