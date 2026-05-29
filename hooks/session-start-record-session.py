"""SessionStart hook: record session_id in a PID-keyed state file.

Claude Code calls this hook at the start of every CC session, passing a
JSON payload on stdin that includes ``session_id``.  This hook captures
the ``session_id`` and writes it to:

    ~/.claude/state/wayfinder-sessions/<ppid>-<create_time_int>.txt

where ``<ppid>`` is the CC process's PID (this hook's parent PID) and
``<create_time_int>`` is the integer seconds of that process's start
time (from psutil).  Using both PID and create_time in the filename
makes the key unique across PID reuse (OS recycled PID guard).

The matcher (``_catalog.py``) walks its ancestor chain to find this file
and attribute log entries to the correct concurrent CC session.

Write is atomic: a temp file next to the target is written then renamed
so no reader ever sees a partial file.

On any error this script exits 0 (never block a CC session).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


def _get_home() -> Path:
    """Return the user home directory from env, or Path.home()."""
    home_str = os.environ.get("HOME") or os.environ.get("USERPROFILE")
    return Path(home_str) if home_str else Path.home()


def main() -> None:
    """Read stdin JSON, extract session_id, write PID-keyed state file."""
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        session_id: str = str(payload.get("session_id") or "")

        ppid: int = os.getppid()

        import psutil  # noqa: PLC0415

        try:
            create_time_int: int = int(psutil.Process(ppid).create_time())
        except Exception:
            # If we cannot get create_time, use 0 as a safe fallback.
            # The matcher will still match on PID; the guard is weakened
            # but we do not block the session.
            create_time_int = 0

        state_dir: Path = _get_home() / ".claude" / "state" / "wayfinder-sessions"
        state_dir.mkdir(parents=True, exist_ok=True)

        target: Path = state_dir / f"{ppid}-{create_time_int}.txt"

        # Atomic write: write to a temp file in the same directory, then rename.
        fd, tmp_path = tempfile.mkstemp(
            dir=state_dir, prefix=f"{ppid}-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(session_id)
            os.replace(tmp_path, target)
        except Exception:
            # Clean up the temp file on failure; do not raise.
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    except Exception as exc:
        # Log to stderr for diagnostics but never block the session.
        sys.stderr.write(f"[session-start-record-session] error: {exc}\n")

    sys.exit(0)


if __name__ == "__main__":
    main()
