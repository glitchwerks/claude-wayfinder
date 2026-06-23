"""Shared PID-file helpers used by both SessionStart and SessionEnd hooks.

Both hooks must key their state files on the **nearest ancestor** whose
process name matches the Claude Code binary (``claude`` / ``claude.exe``,
case-insensitive basename), so they agree on which file to write and
which to delete.  This module is the single source of that selection
logic; importing from here prevents the two hooks from drifting apart.

Exports
-------
_get_home          -- resolve HOME / USERPROFILE to a Path
_iter_ancestors    -- yield (pid, name, create_time_int) for each ancestor
_select_target_pid -- pick the nearest CC-named ancestor
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator


def _get_home() -> Path:
    """Return the user home directory from env, or Path.home().

    Checks ``HOME`` first, then ``USERPROFILE`` (Windows fallback).

    Returns:
        The resolved home directory as a Path.
    """
    home_str = os.environ.get("HOME") or os.environ.get("USERPROFILE")
    return Path(home_str) if home_str else Path.home()


def _iter_ancestors() -> Iterator[tuple[int, str, int]]:
    """Yield ``(pid, name, create_time_int)`` for each ancestor, nearest-first.

    Walks the process tree from the immediate parent upward.  Each tuple
    contains:

    * ``pid``             -- integer process identifier
    * ``name``            -- basename of the executable (e.g. ``"node.exe"``)
    * ``create_time_int`` -- ``int(create_time)`` for that process

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
