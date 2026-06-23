"""Tests for the cleanup-side behaviour of session-end-cleanup-session.py.

Issue #441 / PR #442 (P2): the SessionEnd hook currently builds its target
filename via ``os.getppid()`` (the immediate-parent PID), while SessionStart
was fixed to key on the **nearest claude-named ancestor**.  This mismatch
causes SessionEnd to miss the file written by SessionStart, leaving stale
session files in ``~/.claude/state/wayfinder-sessions/``.

## Expected Phase-2 fix (what these tests pin)

Phase 2 will:

1. Create ``hooks/_session_pidfile.py`` exporting ``_get_home``,
   ``_iter_ancestors``, and ``_select_target_pid``.
2. Rewrite ``session-end-cleanup-session.py`` to compute the target via
   ``_select_target_pid(_iter_ancestors())`` — the same selection logic as
   SessionStart.

These tests monkeypatch ``_iter_ancestors`` on the SessionEnd module's
namespace (where it will land after the ``from _session_pidfile import …``
import in Phase 2) — mirroring exactly the seam used in the existing frozen
session-start tests.

## Filesystem isolation

All tests redirect ``HOME`` (and ``USERPROFILE``) to a temp directory via
the ``isolate_home`` autouse fixture so that the real ``~/.claude/state/``
is never touched.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _state_dir(home: Path) -> Path:
    """Return the wayfinder-sessions dir rooted at *home*.

    Args:
        home: Fake home directory supplied by the ``isolate_home`` fixture.

    Returns:
        Path to ``<home>/.claude/state/wayfinder-sessions``.
    """
    return home / ".claude" / "state" / "wayfinder-sessions"


def _load_hook():
    """Import the SessionEnd hook module by file path.

    Imports ``hooks/session-end-cleanup-session.py`` via importlib spec so
    that tests can reference its internals and monkeypatch seams.

    Returns:
        The loaded module object.
    """
    import importlib.util

    hook_path = (
        Path(__file__).parent.parent.parent / "hooks" / "session-end-cleanup-session.py"
    )
    spec = importlib.util.spec_from_file_location(
        "session_end_cleanup_session", hook_path
    )
    assert spec is not None, f"Could not load hook at {hook_path}"
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


# ---------------------------------------------------------------------------
# Autouse: isolate HOME so tests never touch real ~/.claude/state
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect HOME / USERPROFILE to a temp directory for every test.

    Args:
        tmp_path: pytest-supplied per-test temporary directory.
        monkeypatch: pytest monkeypatch fixture.

    Returns:
        The fake home Path that was set as HOME / USERPROFILE.
    """
    home = tmp_path / "fakehome"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


# ---------------------------------------------------------------------------
# Case 1 — deletes the nearest-claude-keyed file
# ---------------------------------------------------------------------------


class TestDeletesNearestClaude:
    """SessionEnd must delete the file keyed on the nearest claude ancestor."""

    def test_deletes_nearest_claude_keyed_file(
        self,
        tmp_path: Path,
        isolate_home: Path,
    ) -> None:
        """The correct pidfile (nearest-CC-keyed) is deleted after SessionEnd.

        Ancestor chain (nearest-first):
          (111, "node.exe",    900)  -- immediate parent, NOT a CC binary
          (222, "claude.exe",  850)  -- nearest CC ancestor → key used by SessionStart
          (333, "claude.exe",  800)  -- farther CC ancestor
          (444, "explorer.exe",700)  -- non-CC ancestor

        Pre-condition: ``222-850.txt`` exists in the state dir (written by SessionStart).
        Expected: after SessionEnd runs, ``222-850.txt`` is gone.

        Args:
            tmp_path: pytest per-test temp directory.
            isolate_home: fake home directory path from autouse fixture.
        """
        chain = [
            (111, "node.exe", 900),
            (222, "claude.exe", 850),
            (333, "claude.exe", 800),
            (444, "explorer.exe", 700),
        ]

        state = _state_dir(isolate_home)
        state.mkdir(parents=True, exist_ok=True)
        target_file = state / "222-850.txt"
        target_file.write_text("some-session-id", encoding="utf-8")

        hook = _load_hook()

        with (
            patch.object(hook, "_iter_ancestors", return_value=iter(chain)),
            pytest.raises(SystemExit) as exc_info,
        ):
            hook.main()

        assert exc_info.value.code == 0
        assert not target_file.exists(), (
            "SessionEnd must delete the nearest-CC-keyed file (222-850.txt)"
        )


# ---------------------------------------------------------------------------
# Case 2 — does NOT delete by the old immediate-parent key (regression guard)
# ---------------------------------------------------------------------------


class TestDoesNotDeleteByOldImmediateParentKey:
    """SessionEnd must NOT delete a file keyed on the immediate-parent PID.

    This is the core regression from issue #441: the old code used
    ``os.getppid()`` which returned the immediate-parent (node.exe, PID 111)
    rather than the nearest CC ancestor (PID 222), so it deleted the wrong
    file (or none at all).
    """

    def test_deletes_correct_file_not_immediate_parent_file(
        self,
        tmp_path: Path,
        isolate_home: Path,
    ) -> None:
        """Correct file (222-850.txt) is deleted; old-key file (111-900.txt) survives.

        Pre-condition: BOTH files exist in the state dir.
        Expected:
          - ``222-850.txt`` is deleted (correct nearest-CC key).
          - ``111-900.txt`` is left untouched (old getppid() key must NOT be used).

        Args:
            tmp_path: pytest per-test temp directory.
            isolate_home: fake home directory path from autouse fixture.
        """
        chain = [
            (111, "node.exe", 900),
            (222, "claude.exe", 850),
            (333, "claude.exe", 800),
            (444, "explorer.exe", 700),
        ]

        state = _state_dir(isolate_home)
        state.mkdir(parents=True, exist_ok=True)
        correct_file = state / "222-850.txt"
        old_key_file = state / "111-900.txt"
        correct_file.write_text("session-abc", encoding="utf-8")
        old_key_file.write_text("session-abc", encoding="utf-8")

        hook = _load_hook()

        with (
            patch.object(hook, "_iter_ancestors", return_value=iter(chain)),
            pytest.raises(SystemExit) as exc_info,
        ):
            hook.main()

        assert exc_info.value.code == 0
        assert not correct_file.exists(), (
            "222-850.txt (nearest-CC key) must be deleted by SessionEnd"
        )
        assert old_key_file.exists(), (
            "111-900.txt (old getppid() key) must NOT be deleted — "
            "SessionEnd must not revert to the immediate-parent key"
        )


# ---------------------------------------------------------------------------
# Case 3 — missing target file is a silent no-op
# ---------------------------------------------------------------------------


class TestMissingFileIsNoOp:
    """SessionEnd must not error when the target pidfile does not exist."""

    def test_missing_file_is_silent_noop(
        self,
        tmp_path: Path,
        isolate_home: Path,
    ) -> None:
        """No exception and exit 0 when the expected pidfile is absent.

        Simulates the case where SessionStart never wrote a file (e.g. an
        earlier crash), or the file was already pruned by the matcher.

        Args:
            tmp_path: pytest per-test temp directory.
            isolate_home: fake home directory path from autouse fixture.
        """
        chain = [
            (111, "node.exe", 900),
            (222, "claude.exe", 850),
        ]

        # State dir exists but the file does not.
        state = _state_dir(isolate_home)
        state.mkdir(parents=True, exist_ok=True)

        hook = _load_hook()

        with (
            patch.object(hook, "_iter_ancestors", return_value=iter(chain)),
            pytest.raises(SystemExit) as exc_info,
        ):
            hook.main()

        assert exc_info.value.code == 0, (
            "SessionEnd must exit 0 even when the pidfile is missing"
        )


# ---------------------------------------------------------------------------
# Case 4 — fallback parity: no CC ancestor uses same key as SessionStart
# ---------------------------------------------------------------------------


class TestFallbackParity:
    """When no CC ancestor exists, SessionEnd must use the same fallback as SessionStart.

    SessionStart falls back to the immediate parent (first entry) when there
    is no CC-named ancestor but the chain has depth >= 2.  SessionEnd must
    select and delete the same key.
    """

    def test_fallback_deletes_immediate_parent_key_when_no_cc_ancestor(
        self,
        tmp_path: Path,
        isolate_home: Path,
    ) -> None:
        """No CC ancestor: SessionEnd deletes the immediate-parent-keyed file.

        Chain: ``[(111,"node.exe",900),(444,"explorer.exe",700)]``
        Pre-condition: ``111-900.txt`` exists (written by SessionStart fallback).
        Expected: ``111-900.txt`` is deleted.

        Args:
            tmp_path: pytest per-test temp directory.
            isolate_home: fake home directory path from autouse fixture.
        """
        chain = [
            (111, "node.exe", 900),
            (444, "explorer.exe", 700),
        ]

        state = _state_dir(isolate_home)
        state.mkdir(parents=True, exist_ok=True)
        fallback_file = state / "111-900.txt"
        fallback_file.write_text("fallback-session", encoding="utf-8")

        hook = _load_hook()

        with (
            patch.object(hook, "_iter_ancestors", return_value=iter(chain)),
            pytest.raises(SystemExit) as exc_info,
        ):
            hook.main()

        assert exc_info.value.code == 0
        assert not fallback_file.exists(), (
            "SessionEnd must delete the fallback-keyed file (111-900.txt) "
            "to match what SessionStart wrote in the fallback path"
        )


# ---------------------------------------------------------------------------
# Case 5 — error isolation: exit 0 when ancestor enumeration raises
# ---------------------------------------------------------------------------


class TestErrorIsolation:
    """SessionEnd must exit 0 even when ancestor enumeration fails."""

    def test_exit_0_when_iter_ancestors_raises(
        self,
        tmp_path: Path,
        isolate_home: Path,
    ) -> None:
        """Any exception from ``_iter_ancestors`` must not propagate; exit must be 0.

        SessionEnd must never block or crash Claude Code shutdown.

        Args:
            tmp_path: pytest per-test temp directory.
            isolate_home: fake home directory path from autouse fixture.
        """
        hook = _load_hook()

        with (
            patch.object(
                hook,
                "_iter_ancestors",
                side_effect=RuntimeError("simulated psutil failure"),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            hook.main()

        assert exc_info.value.code == 0, (
            f"SessionEnd must exit 0 on _iter_ancestors error; "
            f"got exit code {exc_info.value.code}"
        )
