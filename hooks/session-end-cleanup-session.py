"""SessionEnd hook: delete the PID-keyed session state file on clean exit.

Claude Code calls this hook when a session ends cleanly.  This hook
deletes the ``<ppid>-<create_time_int>.txt`` file created by
``session-start-record-session.py`` to keep the state directory tidy.

Non-clean exits (crashes, SIGKILL) leave stale files, which the matcher
prunes opportunistically during the ancestor walk when it detects that a
file's PID is no longer a live process.

Best-effort: missing file is silently ignored.  Any other error is
logged to stderr but never blocks shutdown.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _get_home() -> Path:
    """Return the user home directory from env, or Path.home()."""
    home_str = os.environ.get("HOME") or os.environ.get("USERPROFILE")
    return Path(home_str) if home_str else Path.home()


def main() -> None:
    """Delete the PID-keyed state file for the ending CC session."""
    try:
        ppid: int = os.getppid()

        import psutil  # noqa: PLC0415

        try:
            create_time_int: int = int(psutil.Process(ppid).create_time())
        except Exception:
            create_time_int = 0

        state_dir: Path = _get_home() / ".claude" / "state" / "wayfinder-sessions"
        target: Path = state_dir / f"{ppid}-{create_time_int}.txt"

        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass  # best-effort: non-existent or permission error is fine

    except Exception as exc:
        sys.stderr.write(f"[session-end-cleanup-session] error: {exc}\n")

    sys.exit(0)


if __name__ == "__main__":
    main()
