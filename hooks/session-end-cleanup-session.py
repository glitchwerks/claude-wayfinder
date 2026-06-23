"""SessionEnd hook: delete the nearest-CC-keyed session state file on clean exit.

Claude Code calls this hook when a session ends cleanly.  This hook
deletes the ``<pid>-<create_time_int>.txt`` file created by
``session-start-record-session.py`` to keep the state directory tidy.

The key is computed via the same ``_select_target_pid(_iter_ancestors())``
call used by SessionStart, so both hooks agree on which file to write and
which to delete.  (The old code used ``os.getppid()`` for the immediate-
parent PID, which caused a mismatch after the fix in PR #442 / issue #441.)

Non-clean exits (crashes, SIGKILL) leave stale files; the matcher prunes
them opportunistically during the ancestor walk when it detects that a
file's PID is no longer a live process.

Best-effort: missing file is silently ignored.  Any error is logged to
stderr but never blocks shutdown.  Exits 0 on any path.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the hooks directory is on sys.path so sibling modules
# (_session_pidfile) can be imported both at runtime (spawned as
# ``python hooks/session-end-cleanup-session.py``) and from tests
# (which load this file via importlib.util.spec_from_file_location).
_HOOKS_DIR = str(Path(__file__).parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from _session_pidfile import (  # noqa: E402
    _get_home,
    _iter_ancestors,
    _select_target_pid,
)


def main() -> None:
    """Delete the nearest-CC-keyed state file for the ending CC session."""
    try:
        target_pair = _select_target_pid(_iter_ancestors())
        if target_pair is None:
            # No usable ancestor — nothing to clean up.
            sys.exit(0)

        pid, create_time_int = target_pair
        state_dir: Path = _get_home() / ".claude" / "state" / "wayfinder-sessions"
        target: Path = state_dir / f"{pid}-{create_time_int}.txt"

        # Best-effort delete; missing file is a normal no-op.
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass

    except Exception as exc:
        # Log to stderr for diagnostics but never block shutdown.
        sys.stderr.write(f"[session-end-cleanup-session] error: {exc}\n")

    sys.exit(0)


if __name__ == "__main__":
    main()
