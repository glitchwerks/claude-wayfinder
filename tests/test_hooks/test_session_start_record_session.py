"""Tests for the fixed write-side behavior of session-start-record-session.py.

Issue #441: the hook currently calls ``os.getppid()`` which returns the
transient *node.exe* wrapper PID.  The fix must key the pidfile on the
nearest ancestor whose process name matches the CC binary
(``claude`` / ``claude.exe``, case-insensitive basename), falling back
to the immediate parent when no such ancestor exists.

## Seam required by Phase 2

The implementation must expose a module-level, monkeypatchable helper:

    def _iter_ancestors() -> Iterator[tuple[int, str, int]]:
        ...

It yields ``(pid, name, create_time_int)`` tuples for the ancestor chain
starting with the **immediate parent** (nearest-first), each tuple being:

* ``pid``             – integer process identifier
* ``name``            – basename of the executable (e.g. ``"node.exe"``)
* ``create_time_int`` – ``int(create_time)`` for that process

The hook's ``main()`` (or a helper it calls) must consume this iterator to
select the target PID/create_time pair instead of calling ``os.getppid()``
directly.  These tests monkeypatch ``_iter_ancestors`` so they can exercise
the selection logic in isolation.

## Filesystem isolation

All tests redirect ``HOME`` (and ``USERPROFILE``) to a temp directory via
the ``isolate_home`` autouse fixture so that the real ``~/.claude/state/``
is never touched.  Each test receives the fake home path as a fixture.
"""

from __future__ import annotations

import json
import sys
from io import StringIO
from pathlib import Path
from typing import Iterator
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _state_dir(home: Path) -> Path:
    """Return the wayfinder-sessions dir rooted at *home*."""
    return home / ".claude" / "state" / "wayfinder-sessions"


def _txt_files(state_dir: Path) -> list[Path]:
    """Return all ``.txt`` files in *state_dir* (may not exist yet)."""
    if not state_dir.exists():
        return []
    return list(state_dir.glob("*.txt"))


def _tmp_files(state_dir: Path) -> list[Path]:
    """Return all ``.tmp`` files in *state_dir*."""
    if not state_dir.exists():
        return []
    return list(state_dir.glob("*.tmp"))


def _load_hook():
    """Import (or reload) the hook module under test.

    The hook lives at ``hooks/session-start-record-session.py`` which is
    not on the default import path.  We import it by spec so tests can
    reference its internals (e.g. ``main``, ``_iter_ancestors``).
    """
    import importlib.util

    hook_path = (
        Path(__file__).parent.parent.parent / "hooks" / "session-start-record-session.py"
    )
    spec = importlib.util.spec_from_file_location("session_start_record_session", hook_path)
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

    The fixture returns the fake home directory so individual tests can
    compute the expected state directory path via ``_state_dir(home)``.

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
# Shared ancestor builder
# ---------------------------------------------------------------------------


def _make_ancestor_iter(
    chain: list[tuple[int, str, int]],
) -> Iterator[tuple[int, str, int]]:
    """Yield ancestor tuples from *chain* (nearest-first).

    Args:
        chain: List of ``(pid, name, create_time_int)`` tuples.

    Yields:
        Each tuple in order.
    """
    yield from chain


# ---------------------------------------------------------------------------
# Case 1 — nearest CC ancestor is chosen, not the immediate parent
# ---------------------------------------------------------------------------


class TestNearestCCAncestorChosen:
    """The hook writes a file keyed on the nearest claude-named ancestor."""

    def test_nearest_claude_ancestor_wins_over_immediate_parent(
        self,
        tmp_path: Path,
        isolate_home: Path,
    ) -> None:
        """Nearest ``claude.exe`` ancestor is chosen; immediate ``node.exe`` is skipped.

        Ancestor chain (nearest-first):
          (111, "node.exe",    900)   -- immediate parent, NOT a CC binary
          (222, "claude.exe",  850)   -- first CC ancestor, MUST be selected
          (333, "claude.exe",  800)   -- farther CC ancestor, must NOT be selected
          (444, "explorer.exe",700)   -- non-CC ancestor, must NOT be selected

        Expected: exactly one file written, named ``222-850.txt``,
        containing the session_id ``"abc-123"``.

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
        stdin_payload = json.dumps({"session_id": "abc-123"})

        hook = _load_hook()
        state = _state_dir(isolate_home)

        with (
            patch.object(sys, "stdin", StringIO(stdin_payload)),
            patch.object(hook, "_iter_ancestors", return_value=iter(chain)),
        ):
            hook.main()

        txt = _txt_files(state)
        assert len(txt) == 1, f"Expected exactly 1 .txt file; found {[f.name for f in txt]}"

        written = txt[0]
        assert written.name == "222-850.txt", (
            f"Expected '222-850.txt' (nearest CC ancestor); got '{written.name}'"
        )
        assert written.read_text(encoding="utf-8") == "abc-123"

        # Explicit negative: immediate parent file must NOT exist.
        assert not (state / "111-900.txt").exists(), (
            "Wrote pidfile for immediate parent (node.exe) — should have used claude.exe"
        )
        # Farther CC ancestor must NOT be selected.
        assert not (state / "333-800.txt").exists(), (
            "Wrote pidfile for farther claude.exe ancestor instead of nearest"
        )

    def test_pid_222_not_444_guards_against_shared_high_ancestor(
        self,
        tmp_path: Path,
        isolate_home: Path,
    ) -> None:
        """PID written is the per-session CC PID (222), not a shared high ancestor (444).

        This is the regression that motivates issue #441: if a shared ancestor
        such as ``explorer.exe`` (444) were used, two concurrent CC sessions
        would both write to the same pidfile and corrupt each other's attribution.

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
        stdin_payload = json.dumps({"session_id": "abc-123"})

        hook = _load_hook()
        state = _state_dir(isolate_home)

        with (
            patch.object(sys, "stdin", StringIO(stdin_payload)),
            patch.object(hook, "_iter_ancestors", return_value=iter(chain)),
        ):
            hook.main()

        txt = _txt_files(state)
        assert len(txt) == 1
        assert txt[0].name.startswith("222-"), (
            f"PID in filename must be 222 (per-session CC), not explorer/other; "
            f"got '{txt[0].name}'"
        )
        assert not (state / "444-700.txt").exists(), (
            "File was keyed on explorer.exe (444) — shared ancestor contamination"
        )


# ---------------------------------------------------------------------------
# Case 2 — fallback when no CC ancestor exists
# ---------------------------------------------------------------------------


class TestFallbackWhenNoCCAncestor:
    """When no ancestor name matches the CC binary, fall back to immediate parent."""

    def test_falls_back_to_immediate_parent_when_no_claude_ancestor(
        self,
        tmp_path: Path,
        isolate_home: Path,
    ) -> None:
        """No CC-named ancestor: immediate parent (111, 900) is used; no crash.

        Chain: ``[(111,"node.exe",900),(444,"explorer.exe",700)]``
        Expected: one file written, named ``111-900.txt``.

        Args:
            tmp_path: pytest per-test temp directory.
            isolate_home: fake home directory path from autouse fixture.
        """
        chain = [
            (111, "node.exe", 900),
            (444, "explorer.exe", 700),
        ]
        stdin_payload = json.dumps({"session_id": "fallback-session"})

        hook = _load_hook()
        state = _state_dir(isolate_home)

        with (
            patch.object(sys, "stdin", StringIO(stdin_payload)),
            patch.object(hook, "_iter_ancestors", return_value=iter(chain)),
        ):
            hook.main()

        txt = _txt_files(state)
        assert len(txt) == 1, f"Expected exactly 1 .txt file; found {[f.name for f in txt]}"
        assert txt[0].name == "111-900.txt", (
            f"Fallback should use immediate parent 111-900; got '{txt[0].name}'"
        )
        assert txt[0].read_text(encoding="utf-8") == "fallback-session"

    def test_fallback_never_crashes(
        self,
        tmp_path: Path,
        isolate_home: Path,
    ) -> None:
        """Fallback path completes without raising; exit code is 0.

        Args:
            tmp_path: pytest per-test temp directory.
            isolate_home: fake home directory path from autouse fixture.
        """
        chain = [
            (111, "node.exe", 900),
        ]
        stdin_payload = json.dumps({"session_id": "x"})

        hook = _load_hook()

        with (
            patch.object(sys, "stdin", StringIO(stdin_payload)),
            patch.object(hook, "_iter_ancestors", return_value=iter(chain)),
            pytest.raises(SystemExit) as exc_info,
        ):
            hook.main()

        assert exc_info.value.code == 0


# ---------------------------------------------------------------------------
# Case 3 — file content is exactly session_id; empty payload writes empty file
# ---------------------------------------------------------------------------


class TestFileContent:
    """The written file contains exactly the session_id string."""

    def test_file_contains_exact_session_id(
        self,
        tmp_path: Path,
        isolate_home: Path,
    ) -> None:
        """Content of the state file is exactly the session_id, nothing else.

        Args:
            tmp_path: pytest per-test temp directory.
            isolate_home: fake home directory path from autouse fixture.
        """
        chain = [(100, "claude.exe", 500)]
        session_id = "exact-session-content-check"
        stdin_payload = json.dumps({"session_id": session_id})

        hook = _load_hook()
        state = _state_dir(isolate_home)

        with (
            patch.object(sys, "stdin", StringIO(stdin_payload)),
            patch.object(hook, "_iter_ancestors", return_value=iter(chain)),
        ):
            hook.main()

        txt = _txt_files(state)
        assert len(txt) == 1
        content = txt[0].read_text(encoding="utf-8")
        assert content == session_id, (
            f"File content '{content!r}' != session_id '{session_id!r}'"
        )

    def test_empty_session_id_writes_empty_file(
        self,
        tmp_path: Path,
        isolate_home: Path,
    ) -> None:
        """Missing / empty session_id creates a file with empty content.

        Matches current behavior: ``str(payload.get("session_id") or "")``.

        Args:
            tmp_path: pytest per-test temp directory.
            isolate_home: fake home directory path from autouse fixture.
        """
        chain = [(100, "claude.exe", 500)]
        stdin_payload = json.dumps({})  # no session_id key

        hook = _load_hook()
        state = _state_dir(isolate_home)

        with (
            patch.object(sys, "stdin", StringIO(stdin_payload)),
            patch.object(hook, "_iter_ancestors", return_value=iter(chain)),
        ):
            hook.main()

        txt = _txt_files(state)
        assert len(txt) == 1, "A file must still be created even for empty session_id"
        assert txt[0].read_text(encoding="utf-8") == "", (
            "Empty session_id must produce an empty-content file"
        )


# ---------------------------------------------------------------------------
# Case 4 — atomic write: no .tmp residue after successful write
# ---------------------------------------------------------------------------


class TestAtomicWrite:
    """The atomic write must not leave .tmp files after a successful write."""

    def test_no_tmp_residue_after_successful_write(
        self,
        tmp_path: Path,
        isolate_home: Path,
    ) -> None:
        """After a clean write, the state dir contains only the .txt file.

        The hook uses write-then-rename atomicity: a ``.tmp`` file is created
        and then ``os.replace``-d to the target.  On success, no ``.tmp`` file
        must remain.

        Args:
            tmp_path: pytest per-test temp directory.
            isolate_home: fake home directory path from autouse fixture.
        """
        chain = [(200, "claude.exe", 600)]
        stdin_payload = json.dumps({"session_id": "atomic-session"})

        hook = _load_hook()
        state = _state_dir(isolate_home)

        with (
            patch.object(sys, "stdin", StringIO(stdin_payload)),
            patch.object(hook, "_iter_ancestors", return_value=iter(chain)),
        ):
            hook.main()

        assert _tmp_files(state) == [], (
            f"Stale .tmp files found after successful write: {_tmp_files(state)}"
        )
        assert len(_txt_files(state)) == 1, "Expected exactly one .txt file"


# ---------------------------------------------------------------------------
# Case 5 — error isolation: exit 0 even when _iter_ancestors raises
# ---------------------------------------------------------------------------


class TestErrorIsolation:
    """Hook exits 0 even when ancestor enumeration raises an exception."""

    def test_exit_0_when_iter_ancestors_raises(
        self,
        tmp_path: Path,
        isolate_home: Path,
    ) -> None:
        """Any exception from ``_iter_ancestors`` must not propagate; exit must be 0.

        The hook must never block a CC session.  If the ancestor enumeration
        raises (e.g. psutil unavailable, permission denied), the hook should
        log to stderr and exit 0.

        Args:
            tmp_path: pytest per-test temp directory.
            isolate_home: fake home directory path from autouse fixture.
        """
        stdin_payload = json.dumps({"session_id": "error-session"})

        hook = _load_hook()

        with (
            patch.object(sys, "stdin", StringIO(stdin_payload)),
            patch.object(
                hook,
                "_iter_ancestors",
                side_effect=RuntimeError("simulated psutil failure"),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            hook.main()

        assert exc_info.value.code == 0, (
            f"Hook must exit 0 on error; got exit code {exc_info.value.code}"
        )

    def test_exit_0_when_iter_ancestors_is_empty_iterator(
        self,
        tmp_path: Path,
        isolate_home: Path,
    ) -> None:
        """An empty ancestor iterator (no processes at all) must not crash; exit 0.

        Edge case: the process tree is empty (e.g. running as PID 1 with no
        parent).  The hook must survive gracefully.

        Args:
            tmp_path: pytest per-test temp directory.
            isolate_home: fake home directory path from autouse fixture.
        """
        stdin_payload = json.dumps({"session_id": "no-ancestors"})

        hook = _load_hook()

        with (
            patch.object(sys, "stdin", StringIO(stdin_payload)),
            patch.object(hook, "_iter_ancestors", return_value=iter([])),
            pytest.raises(SystemExit) as exc_info,
        ):
            hook.main()

        assert exc_info.value.code == 0
