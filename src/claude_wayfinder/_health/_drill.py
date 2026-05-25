"""Drill-down subcommand implementations for the router health tool.

Covers the three interactive subcommands added in #170:

  ``health drill``          — Day-by-day and per-session breakdown of a
                              single runtime metric (bypass, advisory-override,
                              recent-drift).
  ``health top``            — Top-N dispatched agents or invoked skills
                              within a time window.
  ``health catalog-status`` — Summarise plugin entry counts from the
                              dispatch catalog.

Also contains the ``_parse_window`` and ``_events_in_window`` helpers used
by the drill and top subcommands.

Depends on:
  _metrics.py — ``_catalog_path``, ``compute_plugin_entry_counts``
  _report.py  — ``load_jsonl``, ``load_catalog_entries``

Public names re-exported via ``_health/__init__.py``:
    _parse_window, _events_in_window, _event_kind,
    _cmd_drill, _cmd_top, _cmd_catalog_status
"""

from __future__ import annotations

import argparse
import collections
import datetime
import json
import os
from pathlib import Path
from typing import Any

from claude_wayfinder._health._metrics import (
    _catalog_path,
    compute_plugin_entry_counts,
)
from claude_wayfinder._health._report import (
    load_catalog_entries,
    load_jsonl,
)

# ---------------------------------------------------------------------------
# Default path helpers (Issue #262)
# ---------------------------------------------------------------------------
# These mirror the helpers in __init__.py.  Defined here rather than imported
# to avoid a circular import (since __init__.py imports from _drill.py).
# ---------------------------------------------------------------------------


def _default_drift_log() -> Path:
    """Return the default drift-log path, resolved at call time.

    Precedence (matches ``scripts/analyze-drift-causes.py``):
      1. ``ROUTER_DRIFT_PATH`` env var.
      2. ``~/.claude/state/router-drift.jsonl`` via ``Path.home()``.

    Returns:
        Path to the drift log file.
    """
    env = os.environ.get("ROUTER_DRIFT_PATH")
    if env:
        return Path(env)
    return Path.home() / ".claude" / "state" / "router-drift.jsonl"


def _default_dispatch_log() -> Path:
    """Return the default dispatch-log path, resolved at call time.

    Precedence:
      1. ``DISPATCH_LOG`` env var.
      2. ``~/.claude/state/dispatch-log.jsonl`` via ``Path.home()``.

    Returns:
        Path to the dispatch log file.
    """
    env = os.environ.get("DISPATCH_LOG")
    if env:
        return Path(env)
    return Path.home() / ".claude" / "state" / "dispatch-log.jsonl"


# ---------------------------------------------------------------------------
# Window parsing helper (#170)
# ---------------------------------------------------------------------------


def _parse_window(spec: str) -> datetime.timedelta:
    """Parse a window spec string like ``30d`` or ``48h`` into a timedelta.

    Supported unit suffixes:
      * ``d`` — days
      * ``h`` — hours

    Args:
        spec: A string of the form ``<integer><unit>``, e.g. ``"30d"``,
            ``"48h"``, ``"1d"``.

    Returns:
        A :class:`datetime.timedelta` representing the requested window.

    Raises:
        ValueError: If ``spec`` is empty, missing a numeric prefix, or
            uses an unsupported unit.
    """
    if not spec:
        raise ValueError(f"Window spec must not be empty: {spec!r}")
    unit = spec[-1]
    digits = spec[:-1]
    if not digits or not digits.isdigit():
        raise ValueError(
            f"Window spec {spec!r} must start with an integer followed by "
            f"a unit (d=days, h=hours). Got {spec!r}."
        )
    n = int(digits)
    if unit == "d":
        return datetime.timedelta(days=n)
    if unit == "h":
        return datetime.timedelta(hours=n)
    raise ValueError(
        f"Unknown window unit {spec!r}. Supported: Nd (days), Nh (hours)."
    )


# ---------------------------------------------------------------------------
# Drill-down subcommand helpers (#170)
# ---------------------------------------------------------------------------


def _events_in_window(
    events: list[dict[str, Any]],
    window: datetime.timedelta,
) -> list[dict[str, Any]]:
    """Return events whose ``ts`` field falls within *window* of now.

    Events that lack a parseable ``ts`` field are excluded silently.

    Args:
        events: List of raw event dicts (may be from drift or dispatch log).
        window: How far back from now to include.

    Returns:
        Filtered list of events within the window.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    cutoff = now - window
    result: list[dict[str, Any]] = []
    for e in events:
        ts_raw = e.get("ts")
        if not isinstance(ts_raw, str):
            continue
        try:
            ts = datetime.datetime.fromisoformat(
                ts_raw.replace("Z", "+00:00")
            )
        except ValueError:
            continue
        if ts >= cutoff:
            result.append(e)
    return result


def _event_kind(e: dict[str, Any]) -> str:
    """Return the canonical event kind using the two-shape discriminator.

    Drift events come in two shapes:
      * **Categorical**: ``{"type": "router_drift", "category": "bypass"}``
      * **Type-tagged**: ``{"type": "advisory_override"}`` (no ``category``)

    Use ``category`` when present; fall back to ``type`` otherwise.

    Args:
        e: A raw event dict.

    Returns:
        String event kind, e.g. ``"bypass"``, ``"advisory_override"``.
    """
    return e.get("category") or e.get("type") or ""


# ---------------------------------------------------------------------------
# ``health drill`` implementation
# ---------------------------------------------------------------------------


def _cmd_drill(argv: list[str]) -> int:
    """Implement ``claude-wayfinder health drill``.

    Args:
        argv: Arguments following ``health drill`` (sys.argv slice).

    Returns:
        Exit code: 0 on success, 2 on argparse error.
    """
    parser = argparse.ArgumentParser(
        prog="claude-wayfinder health drill",
        description=(
            "Drill into a single runtime metric from the drift log. "
            "Produces a day-by-day count and top-session breakdown."
        ),
    )
    parser.add_argument(
        "--metric",
        required=True,
        choices=["bypass", "advisory-override", "recent-drift"],
        help=(
            "Which metric to drill into: "
            "'bypass' (floor-hook bypass events by day + top sessions), "
            "'advisory-override' (overrides by session), "
            "'recent-drift' (last 5 drift events of any kind)."
        ),
    )
    parser.add_argument(
        "--drift-log",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Path to router-drift.jsonl.  Defaults to "
            "$ROUTER_DRIFT_PATH env var, then "
            "~/.claude/state/router-drift.jsonl."
        ),
    )
    parser.add_argument(
        "--dispatch-log",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Path to dispatch-log.jsonl (reserved for future cross-log "
            "correlation).  Defaults to $DISPATCH_LOG env var, then "
            "~/.claude/state/dispatch-log.jsonl."
        ),
    )
    parser.add_argument(
        "--window",
        default="30d",
        metavar="SPEC",
        help=(
            "Look-back window, e.g. '30d' (default) or '48h'. "
            "Supports Nd (days) and Nh (hours)."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        metavar="N",
        help="Max sessions / rows to show (default: 10).",
    )
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Emit machine-readable JSON instead of plain text.",
    )
    args = parser.parse_args(argv)

    try:
        window = _parse_window(args.window)
    except ValueError as exc:
        parser.error(str(exc))
        return 2  # unreachable but satisfies type checker

    # Resolve default paths (Issue #262): explicit flag > env var > home default.
    drift_log_path = (
        args.drift_log if args.drift_log is not None else _default_drift_log()
    )
    drift_events = load_jsonl(drift_log_path)

    windowed = _events_in_window(drift_events, window)

    metric = args.metric
    limit = args.limit

    if metric == "bypass":
        return _drill_bypass(windowed, window, limit, args.as_json)
    if metric == "advisory-override":
        return _drill_advisory_override(windowed, window, limit, args.as_json)
    # recent-drift
    return _drill_recent_drift(drift_events, limit, args.as_json)


def _drill_bypass(
    windowed: list[dict[str, Any]],
    window: datetime.timedelta,
    limit: int,
    as_json: bool,
) -> int:
    """Render bypass drill-down output.

    Args:
        windowed: Events already filtered to the look-back window.
        window: The look-back window (used for display label).
        limit: Max top sessions to show.
        as_json: Emit JSON when True.

    Returns:
        Exit code 0.
    """
    bypass_events = [e for e in windowed if _event_kind(e) == "bypass"]
    by_day: collections.Counter[str] = collections.Counter()
    by_session: collections.Counter[str] = collections.Counter()
    for e in bypass_events:
        ts_raw = e.get("ts", "")
        try:
            ts = datetime.datetime.fromisoformat(
                ts_raw.replace("Z", "+00:00")
            )
            by_day[ts.date().isoformat()] += 1
        except (ValueError, AttributeError):
            pass
        sid = e.get("session_id", "")
        if sid:
            by_session[sid] += 1

    total = len(bypass_events)
    window_label = (
        f"{int(window.total_seconds() // 86400)}d"
        if window.total_seconds() % 86400 == 0
        else f"{int(window.total_seconds() // 3600)}h"
    )

    if as_json:
        payload: dict[str, Any] = {
            "metric": "bypass",
            "window": window_label,
            "total": total,
            "by_day": dict(sorted(by_day.items())),
            "top_sessions": [
                {"session_id": s, "count": c}
                for s, c in by_session.most_common(limit)
            ],
        }
        print(json.dumps(payload))
        return 0

    print(f"=== Bypass drill-down ({window_label} window) ===")
    print(f"Total bypass events: {total}")
    if total == 0:
        print(f"No bypass events found in {window_label} window.")
        return 0
    print("")
    print("Bypass events by day (last 10 days shown):")
    for day, cnt in sorted(by_day.items())[-10:]:
        print(f"  {day}: {cnt}")
    print("")
    print(f"Top {min(limit, len(by_session))} bypassing sessions:")
    for sid, cnt in by_session.most_common(limit):
        print(f"  {sid[:8]}...: {cnt}")
    return 0


def _drill_advisory_override(
    windowed: list[dict[str, Any]],
    window: datetime.timedelta,
    limit: int,
    as_json: bool,
) -> int:
    """Render advisory-override drill-down output.

    Args:
        windowed: Events already filtered to the look-back window.
        window: The look-back window (used for display label).
        limit: Max top sessions to show.
        as_json: Emit JSON when True.

    Returns:
        Exit code 0.
    """
    override_events = [
        e for e in windowed if _event_kind(e) == "advisory_override"
    ]
    by_session: collections.Counter[str] = collections.Counter()
    for e in override_events:
        sid = e.get("session_id", "")
        if sid:
            by_session[sid] += 1

    total = len(override_events)
    window_label = (
        f"{int(window.total_seconds() // 86400)}d"
        if window.total_seconds() % 86400 == 0
        else f"{int(window.total_seconds() // 3600)}h"
    )

    if as_json:
        payload: dict[str, Any] = {
            "metric": "advisory-override",
            "window": window_label,
            "total": total,
            "top_sessions": [
                {"session_id": s, "count": c}
                for s, c in by_session.most_common(limit)
            ],
        }
        print(json.dumps(payload))
        return 0

    print(f"=== Advisory-override drill-down ({window_label} window) ===")
    print(f"Total advisory_override events: {total}")
    if total == 0:
        print(f"No advisory_override events in {window_label} window.")
        return 0
    print("")
    print(f"Top {min(limit, len(by_session))} overriding sessions:")
    for sid, cnt in by_session.most_common(limit):
        print(f"  {sid[:8]}...: {cnt}")
    return 0


def _drill_recent_drift(
    all_events: list[dict[str, Any]],
    limit: int,
    as_json: bool,
) -> int:
    """Render the N most recent drift events of any kind.

    Args:
        all_events: All drift events (unfiltered — shows the most recent).
        limit: How many events to show (default: 5 from the CLI default).
        as_json: Emit JSON when True.

    Returns:
        Exit code 0.
    """
    recent = all_events[-limit:] if len(all_events) > limit else all_events[:]

    if as_json:
        payload: dict[str, Any] = {
            "metric": "recent-drift",
            "count": len(recent),
            "events": [
                {
                    "ts": e.get("ts", ""),
                    "kind": _event_kind(e),
                    "session_id": e.get("session_id", "")[:8],
                }
                for e in recent
            ],
        }
        print(json.dumps(payload))
        return 0

    print("=== Recent drift events ===")
    if not recent:
        print("No drift events found.")
        return 0
    for e in recent:
        kind = _event_kind(e)
        sid = (e.get("session_id") or "")[:8]
        ts = e.get("ts", "")
        print(f"  {ts}  {kind:<30s}  {sid}...")
    return 0


# ---------------------------------------------------------------------------
# ``health top`` implementation
# ---------------------------------------------------------------------------


def _cmd_top(argv: list[str]) -> int:
    """Implement ``claude-wayfinder health top``.

    Args:
        argv: Arguments following ``health top``.

    Returns:
        Exit code: 0 on success, 2 on argparse error.
    """
    parser = argparse.ArgumentParser(
        prog="claude-wayfinder health top",
        description=(
            "Show the top-N most dispatched agents or most invoked skills "
            "within a time window."
        ),
    )
    parser.add_argument(
        "--kind",
        required=True,
        choices=["agents", "skills"],
        help="What to rank: 'agents' (agent_dispatch events) or "
             "'skills' (skill_invocation events).",
    )
    parser.add_argument(
        "--dispatch-log",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Path to dispatch-log.jsonl.  Defaults to "
            "$DISPATCH_LOG env var, then "
            "~/.claude/state/dispatch-log.jsonl."
        ),
    )
    parser.add_argument(
        "--window",
        default="30d",
        metavar="SPEC",
        help="Look-back window, e.g. '30d' (default) or '48h'.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=3,
        metavar="N",
        help="How many top entries to show (default: 3).",
    )
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Emit machine-readable JSON instead of plain text.",
    )
    args = parser.parse_args(argv)

    try:
        window = _parse_window(args.window)
    except ValueError as exc:
        parser.error(str(exc))
        return 2

    # Resolve default path (Issue #262): explicit flag > env var > home default.
    dispatch_log_path = (
        args.dispatch_log if args.dispatch_log is not None else _default_dispatch_log()
    )
    dispatch_events = load_jsonl(dispatch_log_path)

    windowed = _events_in_window(dispatch_events, window)

    kind = args.kind
    limit = args.limit
    window_label = (
        f"{int(window.total_seconds() // 86400)}d"
        if window.total_seconds() % 86400 == 0
        else f"{int(window.total_seconds() // 3600)}h"
    )

    if kind == "agents":
        event_type = "agent_dispatch"
        field = "agent"
        label = "Dispatched agents"
    else:
        event_type = "skill_invocation"
        field = "skill"
        label = "Invoked skills"

    relevant = [e for e in windowed if e.get("type") == event_type]
    counter: collections.Counter[str] = collections.Counter()
    for e in relevant:
        name = e.get(field, "")
        if name:
            counter[name] += 1

    total = sum(counter.values())
    top_entries = counter.most_common(limit)

    if args.as_json:
        payload: dict[str, Any] = {
            "kind": kind,
            "window": window_label,
            "total": total,
            "entries": [
                {
                    "name": name,
                    "count": cnt,
                    "pct": round(100.0 * cnt / total, 1) if total else 0.0,
                }
                for name, cnt in top_entries
            ],
        }
        print(json.dumps(payload))
        return 0

    print(f"=== Top {label} ({window_label} window) ===")
    if not top_entries:
        print(f"No {event_type} events found in {window_label} window.")
        return 0
    print(f"Total {event_type} events: {total}")
    print("")
    for name, cnt in top_entries:
        pct = 100.0 * cnt / total if total else 0.0
        print(f"  {name:<28s} {cnt:4d}  ({pct:.1f}%)")
    return 0


# ---------------------------------------------------------------------------
# ``health catalog-status`` implementation
# ---------------------------------------------------------------------------


def _cmd_catalog_status(argv: list[str]) -> int:
    """Implement ``claude-wayfinder health catalog-status``.

    Args:
        argv: Arguments following ``health catalog-status``.

    Returns:
        Exit code: 0 always (missing catalog is graceful, not an error).
    """
    parser = argparse.ArgumentParser(
        prog="claude-wayfinder health catalog-status",
        description=(
            "Summarise plugin entry counts from the dispatch catalog."
        ),
    )
    parser.add_argument(
        "--catalog-path",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Path to dispatch-catalog.json. Falls back to "
            "DISPATCH_CATALOG_PATH env var when omitted."
        ),
    )
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Emit machine-readable JSON instead of plain text.",
    )
    args = parser.parse_args(argv)

    catalog_path: Path | None = args.catalog_path
    if catalog_path is None:
        catalog_path = _catalog_path()

    catalog_absent = catalog_path is None or not catalog_path.exists()
    if catalog_absent:
        path_str = str(catalog_path) if catalog_path else "(not configured)"
        if args.as_json:
            print(
                json.dumps({
                    "skills": 0,
                    "agents": 0,
                    "routable": 0,
                    "catalog_present": False,
                    "catalog_path": path_str,
                })
            )
        else:
            print(
                f"Catalog absent at {path_str} — "
                "run `claude-wayfinder catalog build` to generate it."
            )
        return 0

    entries = load_catalog_entries(catalog_path=catalog_path)
    n_skills, m_agents, k_routable = compute_plugin_entry_counts(entries)
    total = len(entries)

    if args.as_json:
        print(
            json.dumps({
                "skills": n_skills,
                "agents": m_agents,
                "routable": k_routable,
                "catalog_present": True,
                "catalog_path": str(catalog_path),
                "total_entries": total,
            })
        )
        return 0

    print("=== Catalog status ===")
    print(f"Catalog:  {catalog_path}")
    print(f"Total entries:   {total}")
    print(
        f"Plugin skills:   {n_skills}"
    )
    print(
        f"Plugin agents:   {m_agents} "
        f"({k_routable} routable via override)"
    )
    return 0
