"""Analyze bypass causes from router-drift.jsonl.

Reads ~/.claude/state/router-drift.jsonl (overridable via
ROUTER_DRIFT_PATH env), groups events by bypass_cause, and prints a
distribution report.

See docs/superpowers/specs/2026-05-19-telemetry-bypass-taxonomy-design.md
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Disposition mapping for each cause — used in human-readable output.
# Synced with spec §Cause enum.
DISPOSITION: dict[str, str] = {
    "skill_mediated_interactive": "expected",
    "skill_mediated_other": "review",
    "router_direct_after_consumed_dispatch": "unwanted",
    "router_direct_no_dispatch": "unwanted",
    "stale_dispatch": "review",
    "unknown": "review",
}

# Cause set the spec defines — events with a cause outside this set are
# treated as "unknown" for distribution purposes but counted separately
# for visibility.
KNOWN_CAUSES: frozenset[str] = frozenset(DISPOSITION.keys())


def default_drift_path() -> Path:
    """Return the configured drift-log path.

    Returns:
        Path to the drift log file, sourced from ROUTER_DRIFT_PATH env
        var if set, otherwise the default location under the user's
        home directory at ~/.claude/state/router-drift.jsonl.
    """
    env = os.environ.get("ROUTER_DRIFT_PATH")
    if env:
        return Path(env)
    home = Path(os.environ.get("HOME") or os.environ.get("USERPROFILE") or Path.home())
    return home / ".claude" / "state" / "router-drift.jsonl"


@dataclasses.dataclass
class Event:
    """One parsed drift event.

    Attributes:
        ts: Parsed UTC timestamp, or None if missing/unparseable.
        category: The drift category string (e.g. "bypass",
            "skill_mediated", "stale_dispatch").
        cause: The bypass_cause string written by the hook, or None
            for pre-enrichment events.
        signals: The bypass_signals dict from the event, or None.
        subagent_type: Convenience accessor into signals, or None.
        raw: The original parsed JSON object.
    """

    ts: datetime | None
    category: str
    cause: str | None
    signals: dict | None
    subagent_type: str | None
    raw: dict


def parse_event(line: str) -> Event | None:
    """Parse one JSONL line into an Event, or None if malformed.

    Args:
        line: A single line of JSONL text.

    Returns:
        An Event instance if the line is a valid router_drift event,
        None if the line is empty, not valid JSON, not a dict, or has
        a type field other than "router_drift".
    """
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    if obj.get("type") != "router_drift":
        return None
    ts_str = obj.get("ts")
    ts: datetime | None = None
    if isinstance(ts_str, str):
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except ValueError:
            ts = None
    signals = (
        obj.get("bypass_signals")
        if isinstance(obj.get("bypass_signals"), dict)
        else None
    )
    return Event(
        ts=ts,
        category=str(obj.get("category", "")),
        cause=(
            obj.get("bypass_cause")
            if isinstance(obj.get("bypass_cause"), str)
            else None
        ),
        signals=signals,
        subagent_type=(signals or {}).get("subagent_type") if signals else None,
        raw=obj,
    )


def load_events(path: Path) -> list[Event]:
    """Load and parse all drift events from a JSONL file.

    Args:
        path: Path to the router-drift.jsonl file.

    Returns:
        List of parsed Event objects. Returns an empty list if the
        file does not exist or contains no valid router_drift lines.
    """
    if not path.exists():
        return []
    out: list[Event] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        ev = parse_event(line)
        if ev is not None:
            out.append(ev)
    return out


def filter_window(
    events: list[Event],
    days: int | None,
    since: datetime | None,
) -> list[Event]:
    """Filter events to a time window. since takes precedence over days.

    Args:
        events: Full list of parsed events.
        days: Rolling window in days, measured back from now. Used
            only when since is None.
        since: Absolute lower-bound timestamp. When provided, days
            is ignored.

    Returns:
        Subset of events whose ts falls within the window. Events
        with ts=None are always excluded when filtering is active.
        When both days and since are None, all events are returned.
    """
    if since is None and days is None:
        return events
    if since is None:
        since = datetime.now(timezone.utc) - timedelta(days=days or 7)
    return [e for e in events if e.ts is not None and e.ts >= since]


def re_derive_cause(ev: Event) -> str | None:
    """Re-derive cause from signals for the --disagreements check.

    Mirrors deriveCause() in hooks/lib/bypass-taxonomy.js. Returns None
    if the event has no signals (pre-enrichment).

    Args:
        ev: A parsed drift event.

    Returns:
        The cause string that would be assigned by the current hook
        logic, or None if signals are absent and re-derivation is
        not possible.
    """
    s = ev.signals
    if s is None:
        return None
    cat = ev.category
    if cat == "stale_dispatch":
        return "stale_dispatch"
    if cat == "skill_mediated":
        return (
            "skill_mediated_interactive"
            if s.get("last_skill_call_is_interactive")
            else "skill_mediated_other"
        )
    if cat == "bypass":
        if not s.get("dispatch_skill_called_recently"):
            return "router_direct_no_dispatch"
        c = s.get("count_agent_since_dispatch")
        if isinstance(c, int) and c >= 1:
            return "router_direct_after_consumed_dispatch"
        return "unknown"
    return "unknown"


def render_report(
    events: list[Event],
    *,
    days: int,
    show_disagreements: bool,
    by_agent: bool,
    as_json: bool,
) -> str:
    """Render the report as text or JSON.

    Args:
        events: Windowed list of drift events to summarize.
        days: Window size used in the report header (informational).
        show_disagreements: When True, list individual events where
            re-derived cause differs from stored bypass_cause.
        by_agent: When True, append a cause × subagent_type cross-tab.
        as_json: When True, return machine-readable JSON instead of
            human-readable text.

    Returns:
        Formatted report string (text or JSON).
    """
    enriched = [e for e in events if e.cause is not None]
    pre_enrichment = len(events) - len(enriched)

    counter = Counter(e.cause for e in enriched)
    total = sum(counter.values())

    if as_json:
        payload = {
            "window_days": days,
            "total_events": len(events),
            "enriched_events": len(enriched),
            "pre_enrichment_baseline": pre_enrichment,
            "distribution": [
                {
                    "cause": cause,
                    "count": cnt,
                    "share": cnt / total if total else 0,
                    "disposition": DISPOSITION.get(cause, "review"),
                }
                for cause, cnt in counter.most_common()
            ],
        }
        return json.dumps(payload, indent=2)

    lines: list[str] = []
    lines.append(
        f"Bypass cause distribution (last {days} days,"
        f" {len(enriched)} enriched events;"
        f" {pre_enrichment} pre-enrichment baseline):"
    )
    lines.append("")
    for cause, cnt in counter.most_common():
        share = cnt / total if total else 0
        disp = DISPOSITION.get(cause, "review")
        marker = {"expected": "✓", "unwanted": "⚠", "review": "?"}.get(
            disp, " "
        )
        lines.append(
            f"  {cause:<40} {cnt:>5}  {share * 100:>5.1f}%   {marker} {disp}"
        )
    lines.append("")

    # Disagreement check
    disagreements = []
    for e in enriched:
        derived = re_derive_cause(e)
        if derived is not None and derived != e.cause:
            disagreements.append((e, derived))
    lines.append(
        f"Disagreement check: {len(disagreements)} events"
        f" ({(len(disagreements) / total * 100) if total else 0:.1f}%)"
        f" where re-derived cause from signals ≠ stored bypass_cause."
    )
    if show_disagreements and disagreements:
        lines.append("")
        lines.append("Disagreements:")
        for e, derived in disagreements:
            lines.append(
                f"  ts={e.ts.isoformat() if e.ts else '?'}"
                f" stored={e.cause} derived={derived}"
                f" agent={e.subagent_type}"
            )

    if by_agent:
        lines.append("")
        lines.append("By agent × cause:")
        cross: dict[tuple[str, str], int] = {}
        for e in enriched:
            key = (e.subagent_type or "?", e.cause or "?")
            cross[key] = cross.get(key, 0) + 1
        for (agent, cause), cnt in sorted(cross.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {agent:<20} {cause:<40} {cnt:>5}")

    return "\n".join(lines)


def _force_utf8_stdout() -> None:
    """Reconfigure stdout/stderr to UTF-8 so report markers (✓, ⚠, ≠, ×)
    render on Windows consoles that default to cp1252. No-op on POSIX or
    when streams aren't reconfigurable (e.g., piped to a non-TTY).
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (OSError, ValueError):
                pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdout()
    """Entry point for the analyze-drift-causes CLI.

    Args:
        argv: Argument list; defaults to sys.argv[1:] when None.

    Returns:
        Exit code: 0 on success.
    """
    parser = argparse.ArgumentParser(
        description=__doc__.strip().splitlines()[0]
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Window in days (default: 7).",
    )
    parser.add_argument(
        "--since",
        type=str,
        default=None,
        help="ISO timestamp overriding --days.",
    )
    parser.add_argument(
        "--disagreements",
        action="store_true",
        help="List events where re-derived cause != stored bypass_cause.",
    )
    parser.add_argument(
        "--by-agent",
        action="store_true",
        help="Cross-tab cause x subagent_type.",
    )
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Machine-readable output.",
    )
    parser.add_argument(
        "--drift-path",
        type=str,
        default=None,
        help=(
            "Override the drift-log path"
            " (default: $ROUTER_DRIFT_PATH or"
            " ~/.claude/state/router-drift.jsonl)."
        ),
    )
    args = parser.parse_args(argv)

    drift_path = Path(args.drift_path) if args.drift_path else default_drift_path()
    events = load_events(drift_path)

    since: datetime | None = None
    if args.since:
        since = datetime.fromisoformat(args.since.replace("Z", "+00:00"))

    windowed = filter_window(events, args.days, since)
    print(
        render_report(
            windowed,
            days=args.days,
            show_disagreements=args.disagreements,
            by_agent=args.by_agent,
            as_json=args.as_json,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
