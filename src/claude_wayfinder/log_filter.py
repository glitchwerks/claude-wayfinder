"""Organic-traffic filter and extraction for the dispatch-log telemetry.

Implements the ``python -m claude_wayfinder log-filter`` subcommand.

"Organic" entries are ``matcher_decision`` events written by the JS
PostToolUse hook.  The hook stamps ``attribution_source="post_tool_use_hook"``
UNCONDITIONALLY on every entry it writes.  The Python writer stamps
``"python_matcher"``.  Therefore:

* An entry with ``attribution_source="post_tool_use_hook"`` AND a non-empty
  ``session_id`` is canonical organic.
* An entry with ``attribution_source="python_matcher"`` is the Python-side
  twin produced alongside the hook entry — excluded to prevent double-counting.
* An entry with NO ``attribution_source`` key did NOT come from the hook
  (pre-#440 Python row, or test fixture) — it is excluded, NOT treated as
  a historical organic entry.  This is the key fix from Codex review P2
  on PR #443: absence of the field means non-hook origin.

Log schema (field name is ``type``, not ``event_type``)::

    {
        "type": "matcher_decision",
        "ts": "2026-05-29T12:00:00.000000Z",
        "session_id": "<non-empty string for organic entries>",
        "attribution_source": "post_tool_use_hook",   # required for organic
        "input":  {"task_description": "...", ...},
        "output": {"decision": "...", "confidence": 1.0, ...},
        "catalog_hash": "sha256:...",
        "matcher_version": "...",
        "override_id": null
    }

Public API
----------
- ``is_organic_entry(obj)``        — canonical organic predicate (single source
                                     of truth used by all callers)
- ``load_organic_decisions(path)`` — load JSONL → filter → return list of dicts
- ``default_log_path()``           — resolve the canonical log path
- ``add_log_filter_args(parser)``  — register CLI args on a subparser
- ``run_log_filter_cli(args)``     — execute the CLI subcommand

The CLI shim in ``cli.py`` calls ``add_log_filter_args`` and ``run_log_filter_cli``.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: The ``type`` field value that identifies a matcher decision event.
_MATCHER_DECISION_TYPE: str = "matcher_decision"

#: The ``attribution_source`` value stamped by the JS PostToolUse hook.
#: The hook stamps this field UNCONDITIONALLY on every entry it writes, so
#: its presence is the canonical signal that an entry is hook-written (organic).
_HOOK_ATTRIBUTION_SOURCE: str = "post_tool_use_hook"


# ---------------------------------------------------------------------------
# Default path resolution
# ---------------------------------------------------------------------------


def default_log_path() -> Path:
    """Return the default dispatch-log path, resolved at call time.

    Precedence:
      1. ``DISPATCH_LOG`` env var (matches the convention in ``_health``).
      2. ``~/.claude/state/dispatch-log.jsonl`` via ``Path.home()``.

    Resolved at call time (not import time) so that test monkeypatching of
    environment variables and ``Path.home()`` takes effect.

    Returns:
        Path to the dispatch-log file.
    """
    env = os.environ.get("DISPATCH_LOG")
    if env:
        return Path(env)
    return Path.home() / ".claude" / "state" / "dispatch-log.jsonl"


# ---------------------------------------------------------------------------
# Core filter logic
# ---------------------------------------------------------------------------


def is_organic_entry(obj: object) -> bool:
    """Return True iff *obj* is a canonical organic matcher_decision entry.

    An entry is organic when ALL four conditions hold:

    1. ``obj`` is a ``dict``.
    2. ``obj["type"] == "matcher_decision"``.
    3. ``obj["session_id"]`` is a non-empty string.
    4. ``obj["attribution_source"] == "post_tool_use_hook"``.

    Rationale for condition 4 (#440 / Codex P2 on PR #443):
    The JS PostToolUse hook stamps ``attribution_source="post_tool_use_hook"``
    UNCONDITIONALLY on every entry it writes.  The Python writer stamps
    ``"python_matcher"``.  Therefore an entry with NO ``attribution_source``
    key was NOT written by the hook (it is a Python-side or pre-#440 row)
    and must be EXCLUDED — not treated as organic for backward-compatibility.
    This flips the prior assumption and is the correct strict rule.

    This predicate is the single source of truth for organic classification.
    All callers (``load_organic_decisions``, ``builder._load_organic_entries``,
    ``profiler.field_profile``) must delegate to this function.

    Args:
        obj: Any Python object (typically parsed from a JSONL line).

    Returns:
        ``True`` iff *obj* is a hook-written organic matcher_decision entry.
    """
    if not isinstance(obj, dict):
        return False
    if obj.get("type") != _MATCHER_DECISION_TYPE:
        return False
    session_id = obj.get("session_id", "")
    if not session_id:
        return False
    # Strict check: absence of attribution_source means non-hook origin.
    # The hook stamps the field unconditionally, so missing == not organic.
    if obj.get("attribution_source") != _HOOK_ATTRIBUTION_SOURCE:
        return False
    return True


def load_organic_decisions(path: Path) -> list[dict[str, Any]]:
    """Load a dispatch-log JSONL file and return organic matcher_decision entries.

    "Organic" is defined by :func:`is_organic_entry` — all four conditions
    must hold::

        isinstance(obj, dict)
        AND type == "matcher_decision"
        AND session_id is a non-empty string
        AND attribution_source == "post_tool_use_hook"

    The strict ``attribution_source`` check (#440 / Codex P2 on PR #443)
    prevents double-counting.  Both the JS hook and the Python writer produce
    ``matcher_decision`` entries with non-empty ``session_id`` values.  Only
    the hook entry (``attribution_source="post_tool_use_hook"``) is organic.
    The Python twin (``"python_matcher"``) and rows with no
    ``attribution_source`` key at all are excluded — the hook stamps the field
    unconditionally, so absence means non-hook origin.

    Non-``matcher_decision`` event types (``agent_dispatch``,
    ``skill_invocation``, etc.) are always excluded.  Missing files return
    ``[]``.  Malformed or non-dict JSON lines are silently skipped.

    Args:
        path: Path to the JSONL dispatch-log file.

    Returns:
        List of parsed JSON dicts, one per organic ``matcher_decision`` entry,
        in file order.  Each dict is the full original record — no fields are
        stripped.
    """
    if not path.exists():
        return []

    results: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as fh:
        for raw_line in fh:
            stripped = raw_line.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if not is_organic_entry(obj):
                continue
            results.append(obj)

    return results


def load_shadow_decisions(path: Path) -> list[dict[str, Any]]:
    """Load a dispatch-log JSONL file and return shadow matcher_decision entries.

    A "shadow" entry qualifies when all of::

        type == "matcher_decision"
        AND session_id is a non-empty string
        AND "shadow" key is present with a truthy value

    Entries without a ``"shadow"`` key, with an empty/missing ``session_id``,
    or with a non-``matcher_decision`` type are excluded.  Missing files
    return ``[]``.  Malformed or non-dict JSON lines are silently skipped.

    This mirrors ``load_organic_decisions`` structure with a different
    predicate — the shadow sub-record added by ``_write_log_entry`` when
    ``shadow_data`` is supplied (#423 clean shadow set).

    Args:
        path: Path to the JSONL dispatch-log file.

    Returns:
        List of parsed JSON dicts, one per shadow ``matcher_decision`` entry,
        in file order.  Each dict is the full original record — no fields are
        stripped.
    """
    if not path.exists():
        return []

    results: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as fh:
        for raw_line in fh:
            stripped = raw_line.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            if obj.get("type") != _MATCHER_DECISION_TYPE:
                continue
            session_id = obj.get("session_id", "")
            if not session_id:
                continue
            if not obj.get("shadow"):
                continue
            results.append(obj)

    return results


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------


def add_log_filter_args(parser: argparse.ArgumentParser) -> None:
    """Register ``log-filter`` subcommand arguments on *parser*.

    Args:
        parser: The subparser instance for the ``log-filter`` subcommand.
    """
    parser.add_argument(
        "--log-path",
        metavar="PATH",
        default=None,
        help=(
            "Path to the dispatch-log JSONL file.  Defaults to "
            "$DISPATCH_LOG if set, otherwise "
            "~/.claude/state/dispatch-log.jsonl."
        ),
    )
    parser.add_argument(
        "--emit-jsonl",
        action="store_true",
        default=False,
        help=(
            "Emit the filtered organic entries as JSONL to stdout "
            "(one JSON object per line).  Without this flag, only the "
            "count is printed."
        ),
    )


def run_log_filter_cli(args: argparse.Namespace) -> int:
    """Execute the ``log-filter`` subcommand.

    Loads the dispatch log, filters to organic entries, then either prints
    the count (default) or emits all entries as JSONL (``--emit-jsonl``).

    Args:
        args: Parsed argument namespace from ``add_log_filter_args``.

    Returns:
        Exit code: 0 on success.
    """
    log_path: Path = (
        Path(args.log_path) if args.log_path is not None else default_log_path()
    )

    entries = load_organic_decisions(log_path)

    if getattr(args, "emit_jsonl", False):
        for entry in entries:
            print(json.dumps(entry, ensure_ascii=False))
    else:
        print(f"organic matcher_decision entries: {len(entries)}")

    return 0
