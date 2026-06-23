"""SessionStart hook: record session_id in a PID-keyed state file.

Claude Code calls this hook at the start of every CC session, passing a
JSON payload on stdin that includes ``session_id``.  This hook captures
the ``session_id`` and writes it to:

    ~/.claude/state/wayfinder-sessions/<pid>-<create_time_int>.txt

where ``<pid>`` is the PID of the **nearest ancestor** whose process
name matches the Claude Code binary (``claude`` / ``claude.exe``,
case-insensitive basename match), and ``<create_time_int>`` is the
integer seconds of that process's start time (from psutil).  Using both
PID and create_time in the filename makes the key unique across PID
reuse (OS-recycled PID guard).

If no CC-named ancestor is found in the chain, the immediate parent PID
is used as a fallback so that today's behaviour is preserved.

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

# Ensure the hooks directory is on sys.path so sibling modules
# (_session_pidfile) can be imported both at runtime (spawned as
# ``python hooks/session-start-record-session.py``) and from tests
# (which load this file via importlib.util.spec_from_file_location).
_HOOKS_DIR = str(Path(__file__).parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

# Import shared helpers from the sibling module.  The bare names
# (_get_home, _iter_ancestors, _select_target_pid) are bound into
# this module's namespace so that test monkeypatching of
# ``<module>._iter_ancestors`` intercepts calls in main() correctly.
from _session_pidfile import (  # noqa: E402
    _get_home,
    _iter_ancestors,
    _select_target_pid,
)


def main() -> None:
    """Read stdin JSON, extract session_id, write PID-keyed state file."""
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        session_id: str = str(payload.get("session_id") or "")

        target_pair = _select_target_pid(_iter_ancestors())
        if target_pair is None:
            # No ancestors at all — nothing to key on; exit cleanly.
            sys.exit(0)

        target_pid, create_time_int = target_pair

        state_dir: Path = _get_home() / ".claude" / "state" / "wayfinder-sessions"
        state_dir.mkdir(parents=True, exist_ok=True)

        target: Path = state_dir / f"{target_pid}-{create_time_int}.txt"

        # Atomic write: write to a temp file in the same directory, then rename.
        fd, tmp_path = tempfile.mkstemp(
            dir=state_dir, prefix=f"{target_pid}-", suffix=".tmp"
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
