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
from typing import Iterator


def _get_home() -> Path:
    """Return the user home directory from env, or Path.home().

    Returns:
        The resolved home directory as a Path.
    """
    home_str = os.environ.get("HOME") or os.environ.get("USERPROFILE")
    return Path(home_str) if home_str else Path.home()


def _iter_ancestors() -> Iterator[tuple[int, str, int]]:
    """Yield ``(pid, name, create_time_int)`` for each ancestor, nearest-first.

    Walks the process tree from the immediate parent upward.  Each tuple
    contains:

    * ``pid``             – integer process identifier
    * ``name``            – basename of the executable (e.g. ``"node.exe"``)
    * ``create_time_int`` – ``int(create_time)`` for that process

    Stops when no further parent is accessible (e.g. PID 0 or 1 on
    POSIX, or when psutil raises NoSuchProcess / AccessDenied).

    Yields:
        Tuples of (pid, name, create_time_int) from nearest to farthest.
    """
    import psutil  # noqa: PLC0415

    try:
        proc = psutil.Process().parent()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return

    while proc is not None:
        try:
            yield (proc.pid, proc.name(), int(proc.create_time()))
            proc = proc.parent()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            break


def _select_target_pid(
    ancestors: Iterator[tuple[int, str, int]],
) -> tuple[int, int] | None:
    """Walk *ancestors* and return (pid, create_time_int) to key the pidfile.

    Selects the **nearest** ancestor whose name (lowercased basename,
    stripped of ``.exe``) equals ``"claude"``.  If no CC-named ancestor
    is found but at least two ancestors are visible, falls back to the
    immediate parent (first yielded entry).  Returns ``None`` when the
    chain is empty or contains only a single non-CC entry (too shallow
    to reliably attribute a CC session).

    Args:
        ancestors: Iterator of (pid, name, create_time_int) tuples,
            nearest-first.

    Returns:
        A ``(pid, create_time_int)`` pair, or ``None`` when no usable
        ancestor is found.
    """
    entries: list[tuple[int, int]] = []
    for pid, name, create_time_int in ancestors:
        # Case-insensitive basename match: accept "claude" or "claude.exe".
        bare = name.lower()
        if bare.endswith(".exe"):
            bare = bare[:-4]
        if bare == "claude":
            return (pid, create_time_int)
        entries.append((pid, create_time_int))
    # Fallback: use immediate parent only when the chain has depth >= 2
    # (i.e. we can see at least the parent and one more ancestor above it).
    if len(entries) >= 2:
        return entries[0]
    return None


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
