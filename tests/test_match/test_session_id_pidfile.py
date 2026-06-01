"""Regression tests for issue #296 — PID-keyed session_id auto-population.

These tests cover the tier-3 fallback in the ``_resolve_session_id``
function: walking the matcher process's ancestor chain and reading the
corresponding ``<pid>-<create_time>.txt`` file written by the
SessionStart hook.

## Issue #299 — Tier 3 is structurally broken in production

Tier 3 was designed so the ``session-start-record-session.py`` hook writes
a file keyed on ``os.getppid()`` (the node.exe that spawned it), and the
matcher walks its own ancestor chain to find that file.  This is broken:

- The JS hook spawns the Python script via ``spawnSync``, so the Python
  script's ``os.getppid()`` returns the **node.exe PID**, not ``claude.exe``.
- ``node.exe`` exits immediately after the script completes.
- The matcher's ancestor chain is ``python → bash → claude.exe``.  The dead
  ``node.exe`` is a sibling, never an ancestor, so the walk never finds the
  file.

## Fix — ``log-dispatch-decision.js`` PostToolUse hook (issue #299)

The production fix is the ``hooks/log-dispatch-decision.js`` PostToolUse
hook (wired in ``hooks/hooks.json``).  When a ``Skill(dispatch)`` call
completes, the hook reads ``session_id`` from the CC hook payload (where it
is guaranteed to be present per the hook contract: "Per session, stable")
and writes a ``matcher_decision`` log entry with the correct session_id.

This is deterministic and concurrent-safe:
- Each PostToolUse fires synchronously for its own Skill call with its own
  ``session_id``.
- Two concurrent CC sessions produce two separate PostToolUse processes with
  distinct session_ids.

## Tier 3 tests retained

The Tier 3 unit tests below are retained because the code still exists in
``_catalog.py`` (the four-tier chain is unchanged) and the tests exercise
valid code paths.  In production, Tier 3 will never match (the PID-keyed
file is never written to a PID in the matcher's ancestor chain), but the
fallthrough to Tier 4 (empty string) is benign — the PostToolUse hook
provides the attributed record separately.

The Tier 3 tests could be removed in a future cleanup once the PID-file
mechanism is fully retired.  For now they serve as regression coverage for
the orphan-prune and fallthrough logic.

Tier precedence verified here (all four tiers):
  1. Input JSON  ``session_id`` field (highest priority)
  2. ``CLAUDE_SESSION_ID`` env var
  3. PID-keyed state file  ``~/.claude/state/wayfinder-sessions/<pid>-<ct>.txt``
     (broken in production — see above; retained for code coverage)
  4. ``""`` (no info available)

All tests use ``tmp_path`` for the state directory and monkeypatch the
internal resolver so the real ``~/.claude/state/`` is never touched.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state_dir(tmp_path: Path) -> Path:
    """Create and return the wayfinder-sessions state directory."""
    state_dir = tmp_path / "wayfinder-sessions"
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir


def _write_session_file(
    state_dir: Path,
    pid: int,
    create_time_int: int,
    session_id: str,
) -> Path:
    """Write a session file for the given PID and create_time."""
    fname = f"{pid}-{create_time_int}.txt"
    p = state_dir / fname
    p.write_text(session_id, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Unit tests — _resolve_session_id directly
# ---------------------------------------------------------------------------


class TestResolveSessionIdPidfileTier3:
    """Unit tests for the PID-keyed file lookup (tier 3)."""

    def test_happy_path_reads_ancestor_session_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Tier 3: ancestor PID has a matching state file → session_id read.

        The matcher walks its process parents, finds a file named
        ``<pid>-<ct>.txt`` that matches one ancestor's PID and create_time,
        and returns its contents as the session_id.
        """
        from claude_wayfinder.match._catalog import _resolve_session_id

        state_dir = _make_state_dir(tmp_path)
        ancestor_pid = 9999
        ancestor_ct = 1700000000
        _write_session_file(state_dir, ancestor_pid, ancestor_ct, "hook-session-abc")

        # Build a fake parent process chain: current → parent (ancestor_pid)
        fake_parent = MagicMock()
        fake_parent.pid = ancestor_pid
        fake_parent.create_time.return_value = float(ancestor_ct)

        fake_proc = MagicMock()
        fake_proc.parents.return_value = [fake_parent]

        with patch(
            "claude_wayfinder.match._catalog._WAYFINDER_SESSION_DIR",
            state_dir,
        ):
            with patch("psutil.Process", return_value=fake_proc):
                monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
                # Reset the module-level cache so tier 3 runs
                _reset_session_id_cache()
                result = _resolve_session_id({})

        assert result == "hook-session-abc"

    def test_no_matching_file_returns_empty_string(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Tier 3: no state file for any ancestor → falls through to tier 4 ('').

        The state directory exists but is empty, so the walk finds nothing
        and the function returns the empty-string sentinel.
        """
        from claude_wayfinder.match._catalog import _resolve_session_id

        state_dir = _make_state_dir(tmp_path)

        fake_parent = MagicMock()
        fake_parent.pid = 12345
        fake_parent.create_time.return_value = 1700000001.0

        fake_proc = MagicMock()
        fake_proc.parents.return_value = [fake_parent]

        with patch(
            "claude_wayfinder.match._catalog._WAYFINDER_SESSION_DIR",
            state_dir,
        ):
            with patch("psutil.Process", return_value=fake_proc):
                monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
                _reset_session_id_cache()
                result = _resolve_session_id({})

        assert result == ""

    def test_stale_file_create_time_mismatch_ignored(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Tier 3: file PID matches but create_time differs → file is ignored.

        A stale file from a prior CC session at the same PID must not be
        read — the create_time mismatch is the guard against PID reuse.
        """
        from claude_wayfinder.match._catalog import _resolve_session_id

        state_dir = _make_state_dir(tmp_path)
        ancestor_pid = 8888
        stale_ct = 1600000000  # old session's create_time
        live_ct = 1700000000   # current ancestor's actual create_time
        _write_session_file(state_dir, ancestor_pid, stale_ct, "stale-session")

        fake_parent = MagicMock()
        fake_parent.pid = ancestor_pid
        # Ancestor's actual create_time differs from the file's name
        fake_parent.create_time.return_value = float(live_ct)

        fake_proc = MagicMock()
        fake_proc.parents.return_value = [fake_parent]

        with patch(
            "claude_wayfinder.match._catalog._WAYFINDER_SESSION_DIR",
            state_dir,
        ):
            with patch("psutil.Process", return_value=fake_proc):
                monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
                _reset_session_id_cache()
                result = _resolve_session_id({})

        assert result == ""

    def test_orphaned_file_deleted_on_walk(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Tier 3: file whose PID is not a live process is deleted on walk.

        When the walker encounters a state file and the PID in its name
        is no longer a running process, it prunes the file to keep the
        state directory tidy.
        """
        from claude_wayfinder.match._catalog import _resolve_session_id

        state_dir = _make_state_dir(tmp_path)
        orphan_pid = 77777
        orphan_ct = 1700000099
        orphan_file = _write_session_file(
            state_dir, orphan_pid, orphan_ct, "orphan-session"
        )
        assert orphan_file.exists()

        # Ancestor chain does NOT include the orphan PID
        fake_parent = MagicMock()
        fake_parent.pid = 11111
        fake_parent.create_time.return_value = 1700000000.0

        fake_proc = MagicMock()
        fake_proc.parents.return_value = [fake_parent]

        with patch(
            "claude_wayfinder.match._catalog._WAYFINDER_SESSION_DIR",
            state_dir,
        ):
            # psutil.pids() returns a list that does NOT include orphan_pid
            with patch("psutil.pids", return_value=[1, 2, 11111]):
                with patch("psutil.Process", return_value=fake_proc):
                    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
                    _reset_session_id_cache()
                    _resolve_session_id({})

        assert not orphan_file.exists(), (
            "Orphaned state file should be deleted during the walk"
        )

    def test_psutil_error_falls_through_to_empty_string(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Tier 3: any psutil exception → silently falls through to '' (tier 4).

        psutil may fail (import error, permission denied, NoSuchProcess) in
        restricted environments. The matcher must never crash a log write
        because of attribution lookup.
        """
        from claude_wayfinder.match._catalog import _resolve_session_id

        state_dir = _make_state_dir(tmp_path)

        with patch(
            "claude_wayfinder.match._catalog._WAYFINDER_SESSION_DIR",
            state_dir,
        ):
            with patch("psutil.Process", side_effect=Exception("psutil unavailable")):
                monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
                _reset_session_id_cache()
                result = _resolve_session_id({})

        assert result == ""

    def test_missing_state_dir_falls_through_to_empty_string(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Tier 3: state dir absent → silent fallthrough to '' (tier 4).

        The wayfinder-sessions directory may not exist on a fresh install
        before any SessionStart hook has fired. The matcher must not crash.
        """
        from claude_wayfinder.match._catalog import _resolve_session_id

        missing_dir = tmp_path / "does-not-exist"

        with patch(
            "claude_wayfinder.match._catalog._WAYFINDER_SESSION_DIR",
            missing_dir,
        ):
            monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
            _reset_session_id_cache()
            result = _resolve_session_id({})

        assert result == ""


# ---------------------------------------------------------------------------
# Precedence tests — all four tiers
# ---------------------------------------------------------------------------


class TestSessionIdPrecedenceFourTiers:
    """Cover all four tier positions explicitly for session_id resolution.

    Verifies that the precedence chain is:
      1. input JSON > 2. env var > 3. pidfile > 4. empty string
    """

    def _make_fake_process_with_session(
        self,
        state_dir: Path,
        pid: int,
        create_time_int: int,
        session_id: str,
    ) -> MagicMock:
        """Write a state file and return a mock psutil.Process for it."""
        _write_session_file(state_dir, pid, create_time_int, session_id)
        fake_parent = MagicMock()
        fake_parent.pid = pid
        fake_parent.create_time.return_value = float(create_time_int)
        fake_proc = MagicMock()
        fake_proc.parents.return_value = [fake_parent]
        return fake_proc

    def test_tier1_input_json_beats_all(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Tier 1 (input JSON) wins over env var, pidfile, and empty."""
        from claude_wayfinder.match._catalog import _resolve_session_id

        state_dir = _make_state_dir(tmp_path)
        fake_proc = self._make_fake_process_with_session(
            state_dir, 5001, 1700000001, "pidfile-session"
        )

        with patch(
            "claude_wayfinder.match._catalog._WAYFINDER_SESSION_DIR",
            state_dir,
        ):
            with patch("psutil.Process", return_value=fake_proc):
                monkeypatch.setenv("CLAUDE_SESSION_ID", "env-session")
                _reset_session_id_cache()
                result = _resolve_session_id({"session_id": "input-wins"})

        assert result == "input-wins"

    def test_tier2_env_var_beats_pidfile_and_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Tier 2 (env var) wins over pidfile and empty when input omits it."""
        from claude_wayfinder.match._catalog import _resolve_session_id

        state_dir = _make_state_dir(tmp_path)
        fake_proc = self._make_fake_process_with_session(
            state_dir, 5002, 1700000002, "pidfile-session"
        )

        with patch(
            "claude_wayfinder.match._catalog._WAYFINDER_SESSION_DIR",
            state_dir,
        ):
            with patch("psutil.Process", return_value=fake_proc):
                monkeypatch.setenv("CLAUDE_SESSION_ID", "env-wins")
                _reset_session_id_cache()
                result = _resolve_session_id({})  # no session_id key

        assert result == "env-wins"

    def test_tier3_pidfile_beats_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Tier 3 (pidfile) wins over empty string when tiers 1 and 2 absent."""
        from claude_wayfinder.match._catalog import _resolve_session_id

        state_dir = _make_state_dir(tmp_path)
        fake_proc = self._make_fake_process_with_session(
            state_dir, 5003, 1700000003, "pidfile-wins"
        )

        with patch(
            "claude_wayfinder.match._catalog._WAYFINDER_SESSION_DIR",
            state_dir,
        ):
            with patch("psutil.Process", return_value=fake_proc):
                monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
                _reset_session_id_cache()
                result = _resolve_session_id({})

        assert result == "pidfile-wins"

    def test_tier4_empty_string_when_all_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Tier 4: all lookups fail → empty string sentinel returned."""
        from claude_wayfinder.match._catalog import _resolve_session_id

        state_dir = _make_state_dir(tmp_path)
        # State dir is empty — no files to find

        fake_parent = MagicMock()
        fake_parent.pid = 9001
        fake_parent.create_time.return_value = 1700000009.0
        fake_proc = MagicMock()
        fake_proc.parents.return_value = [fake_parent]

        with patch(
            "claude_wayfinder.match._catalog._WAYFINDER_SESSION_DIR",
            state_dir,
        ):
            with patch("psutil.Process", return_value=fake_proc):
                monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
                _reset_session_id_cache()
                result = _resolve_session_id({})

        assert result == ""


# ---------------------------------------------------------------------------
# _write_log_entry integration — pidfile session_id appears in log
# ---------------------------------------------------------------------------


class TestWriteLogEntryWithPidfile:
    """Confirm _write_log_entry uses tier-3 session_id in the log entry."""

    def test_pidfile_session_id_written_to_log(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When only a pidfile provides session_id it appears in the log entry."""
        from claude_wayfinder.match._catalog import _write_log_entry

        state_dir = _make_state_dir(tmp_path)
        _write_session_file(state_dir, 6001, 1700000010, "log-pidfile-session")

        fake_parent = MagicMock()
        fake_parent.pid = 6001
        fake_parent.create_time.return_value = 1700000010.0
        fake_proc = MagicMock()
        fake_proc.parents.return_value = [fake_parent]

        log_path = tmp_path / "log.jsonl"

        with patch(
            "claude_wayfinder.match._catalog._WAYFINDER_SESSION_DIR",
            state_dir,
        ):
            with patch("psutil.Process", return_value=fake_proc):
                monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
                _reset_session_id_cache()
                _write_log_entry(
                    {"task_description": "test"},
                    {"decision": "delegate"},
                    "sha256:abc",
                    log_path,
                )

        entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert entry["session_id"] == "log-pidfile-session"


# ---------------------------------------------------------------------------
# Cache behaviour — resolve only once per process lifetime
# ---------------------------------------------------------------------------


class TestSessionIdCache:
    """The resolved session_id must be cached; psutil is called only once."""

    def test_pidfile_result_cached_across_calls(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Tier-3 resolution is cached; subsequent calls skip the psutil walk."""
        from claude_wayfinder.match._catalog import _resolve_session_id

        state_dir = _make_state_dir(tmp_path)
        _write_session_file(state_dir, 7001, 1700000020, "cached-session")

        fake_parent = MagicMock()
        fake_parent.pid = 7001
        fake_parent.create_time.return_value = 1700000020.0
        fake_proc = MagicMock()
        fake_proc.parents.return_value = [fake_parent]

        with patch(
            "claude_wayfinder.match._catalog._WAYFINDER_SESSION_DIR",
            state_dir,
        ):
            with patch("psutil.Process", return_value=fake_proc) as mock_proc_cls:
                monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
                _reset_session_id_cache()

                result1 = _resolve_session_id({})
                result2 = _resolve_session_id({})

        assert result1 == "cached-session"
        assert result2 == "cached-session"
        # psutil.Process should only be constructed once (cache hit on 2nd call)
        assert mock_proc_cls.call_count == 1


# ---------------------------------------------------------------------------
# Cache reset helper (test-internal only)
# ---------------------------------------------------------------------------


def _reset_session_id_cache() -> None:
    """Reset the module-level session_id cache in _catalog for test isolation.

    Each test must call this before calling ``_resolve_session_id`` to
    ensure the prior test's cached value does not bleed across test
    boundaries.
    """
    import claude_wayfinder.match._catalog as _cat

    _cat._SESSION_ID_CACHE = None  # type: ignore[attr-defined]
