"""Organic-traffic filter and extraction for the dispatch-log telemetry.

Implements the ``python -m claude_wayfinder log-filter`` subcommand.

"Organic" entries are ``matcher_decision`` events that carry a non-empty
``session_id``, introduced by the v1.1.1 attribution fix.  Entries with an
empty ``session_id`` are fixture-contaminated or pre-fix and must be excluded.

Log schema (field name is ``type``, not ``event_type``)::

    {
        "type": "matcher_decision",
        "ts": "2026-05-29T12:00:00.000000Z",
        "session_id": "<non-empty string for organic entries>",
        "input":  {"task_description": "...", ...},
        "output": {"decision": "...", "confidence": 1.0, ...},
        "catalog_hash": "sha256:...",
        "matcher_version": "...",
        # newer organic entries also include:
        "override_id": null
    }

Public API
----------
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


def load_organic_decisions(path: Path) -> list[dict[str, Any]]:
    """Load a dispatch-log JSONL file and return organic matcher_decision entries.

    "Organic" is defined as::

        type == "matcher_decision"
        AND session_id is a non-empty string
        AND attribution_source != "python_matcher"

    The ``attribution_source == "python_matcher"`` exclusion (#440) prevents
    double-counting: both the JS hook and the Python writer produce
    ``matcher_decision`` entries.  Only the hook entry (carrying
    ``attribution_source="post_tool_use_hook"``, or no ``attribution_source``
    at all for pre-#440 historical entries) counts as the canonical organic
    record.  Entries that predate the ``attribution_source`` field (i.e. no
    such key present) are treated as organic for backward-compatibility.

    Entries with an empty or absent ``session_id`` are fixture-contaminated
    (pre-v1.1.1 attribution fix) and are excluded.  Non-``matcher_decision``
    event types (``agent_dispatch``, ``skill_invocation``, etc.) are always
    excluded.

    Missing files return an empty list (same convention as ``_health``).
    Malformed or non-dict JSON lines are silently skipped.

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
            if not isinstance(obj, dict):
                continue
            if obj.get("type") != _MATCHER_DECISION_TYPE:
                continue
            session_id = obj.get("session_id", "")
            if not session_id:
                continue
            # Exclude Python-side twin entries (#440): entries written by
            # _write_log_entry carry attribution_source="python_matcher".
            # Entries with no attribution_source field predate #440 and are
            # treated as organic (backward-compat with historical hook entries).
            if obj.get("attribution_source") == "python_matcher":
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
