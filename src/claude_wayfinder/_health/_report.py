"""Report formatting and I/O helpers for the router health tool.

Covers:
  - JSONL file loading (``load_jsonl``)
  - Catalog entry loading (``load_catalog_entries``)
  - Status-string helper (``_status_str``)
  - CI banner formatting (``format_ci_output``)
  - Harness-version surfacing (``most_recent_harness_version``)
  - Bypass-cause section builder (``_build_bypass_causes_section``)
  - Full markdown report generation (``format_report_output``)

Depends on ``_metrics.py`` for MetricResult, constants, and
compute_plugin_entry_counts.  No subprocess invocations — all output
is derived from pre-loaded event lists.

Public names re-exported via ``_health/__init__.py``:
    load_jsonl, load_catalog_entries, format_ci_output, format_report_output,
    most_recent_harness_version
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from claude_wayfinder._health._metrics import (
    _BYPASS_CAUSE_DISPOSITION,
    _BYPASS_CAUSE_MIN_SAMPLE,
    _UNKNOWN_SHARE_WARN,
    _UNWANTED_BYPASS_SHARE_MAX,
    MetricResult,
    _catalog_path,
    compute_plugin_entry_counts,
)

# ---------------------------------------------------------------------------
# Status constants
# ---------------------------------------------------------------------------

_STATUS_PASS = "PASS"
_STATUS_FAIL = "FAIL"
_STATUS_INFO = "INFO"


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    """Load a JSONL file, returning a list of parsed objects.

    Missing files are treated as zero events (fully healthy, not an error).
    Malformed lines are silently skipped — consistent with other log readers
    in this codebase.

    Args:
        path: Path to the JSONL file.

    Returns:
        List of parsed JSON objects.  Empty list if the file is absent.
    """
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
            if isinstance(obj, dict):
                entries.append(obj)
        except json.JSONDecodeError:
            pass
    return entries


def load_catalog_entries(
    catalog_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Load catalog entries from the given path or ``DISPATCH_CATALOG_PATH``.

    Missing or malformed catalogs are silently treated as empty — consistent
    with the ``load_jsonl`` strategy used for log files.

    Resolution order:

    1. ``catalog_path`` argument when provided.
    2. ``DISPATCH_CATALOG_PATH`` env var.
    3. Return ``[]`` — no ``~/.claude/`` fallback (Issue #10).

    Args:
        catalog_path: Explicit path to the catalog file.  When ``None``,
            ``DISPATCH_CATALOG_PATH`` is consulted; if that is also absent,
            ``[]`` is returned immediately.

    Returns:
        List of raw entry dicts from the ``"entries"`` key of the catalog
        file.  Empty list when no path is configured, the file is absent,
        or the file is unparseable.
    """
    path: Path | None = catalog_path if catalog_path is not None else _catalog_path()
    if path is None:
        return []
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = data.get("entries", [])
        if isinstance(entries, list):
            return entries
    except (json.JSONDecodeError, OSError):
        pass
    return []


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


def _status_str(result: MetricResult) -> str:
    """Return the display status string for a MetricResult.

    Args:
        result: The metric result to label.

    Returns:
        ``"INFO"`` for informational metrics; ``"PASS"`` or ``"FAIL"``
        based on ``result.healthy`` for all other classes.
    """
    if result.metric_class == "informational":
        return _STATUS_INFO
    return _STATUS_PASS if result.healthy else _STATUS_FAIL


def format_ci_output(invariants: dict[str, MetricResult]) -> str:
    """Format CI invariant results as a plain-text report.

    Args:
        invariants: Dict of CI invariant MetricResults.

    Returns:
        Formatted string for stdout.
    """
    lines = ["=== Router Health: CI Invariants ===", ""]
    lines.append("Pre-ship CI invariants (v5 §3.3.4):")
    lines.append("")

    for key, result in invariants.items():
        status = _status_str(result)
        lines.append(f"  [{status}] {result.label}")
        lines.append(f"         Threshold: {result.threshold}")
        if result.detail:
            lines.append(f"         Detail:    {result.detail}")
        lines.append("")

    all_pass = all(r.healthy for r in invariants.values())
    if all_pass:
        lines.append("Result: All CI invariants PASSED")
    else:
        failed = [r.label for r in invariants.values() if not r.healthy]
        lines.append(f"Result: FAILED — {len(failed)} invariant(s) failing: {', '.join(failed)}")

    return "\n".join(lines) + "\n"


def most_recent_harness_version(dispatch_log: list[dict[str, Any]]) -> str | None:
    """Return the ``harness_version`` from the most recent versioned dispatch-log entry.

    Legacy entries lack the field — those are silently skipped.  Returns None
    when no versioned entry exists (e.g. all entries are legacy unversioned).

    Args:
        dispatch_log: Events from dispatch-log.jsonl.

    Returns:
        40-char hex SHA string, or None if no versioned entry found.
    """
    for event in reversed(dispatch_log):
        version = event.get("harness_version")
        if version and version != "unknown":
            return str(version)
    return None


def _build_bypass_causes_section(
    drift_events: list[dict[str, Any]],
) -> list[str]:
    """Build the 'Bypass causes (7-day window)' markdown section.

    Reads enriched drift events (with bypass_signals + bypass_cause
    fields), counts by cause within a 7-day window, and returns
    markdown lines. When the enriched-event count is below
    _BYPASS_CAUSE_MIN_SAMPLE, returns a low-N notice instead of
    distribution + thresholds.

    Args:
        drift_events: Pre-loaded drift events. Mix of pre- and
            post-enrichment is fine; pre-enrichment events are skipped
            from cause counts but reported as a baseline.

    Returns:
        List of markdown lines (no trailing newline per line).
    """
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=7)

    def _in_window(ev: dict[str, Any]) -> bool:
        ts = ev.get("ts")
        if not isinstance(ts, str):
            return False
        try:
            t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return False
        return t >= since

    drift_in_window = [
        e
        for e in drift_events
        if e.get("type") == "router_drift" and _in_window(e)
    ]
    enriched = [
        e for e in drift_in_window if isinstance(e.get("bypass_cause"), str)
    ]
    pre_enrichment = len(drift_in_window) - len(enriched)

    lines: list[str] = []
    lines.append(
        f"## Bypass causes (7-day window, {len(enriched)} enriched events)"
    )
    lines.append("")

    if len(enriched) < _BYPASS_CAUSE_MIN_SAMPLE:
        lines.append(
            f"N/A — insufficient post-enrichment data (have {len(enriched)},"
            f" need {_BYPASS_CAUSE_MIN_SAMPLE}). Pre-enrichment baseline:"
            f" {pre_enrichment} events."
        )
        lines.append("")
        return lines

    # Count by cause
    counts: dict[str, int] = {}
    for e in enriched:
        cause = e.get("bypass_cause", "unknown")
        if not isinstance(cause, str):
            cause = "unknown"
        counts[cause] = counts.get(cause, 0) + 1

    total = sum(counts.values())
    lines.append(
        "| Cause                                   |  Count |  Share"
        " | Disposition |"
    )
    lines.append(
        "| --------------------------------------- | -----: | -----:"
        " | ----------- |"
    )
    for cause, cnt in sorted(counts.items(), key=lambda kv: -kv[1]):
        share = cnt / total
        disp = _BYPASS_CAUSE_DISPOSITION.get(cause, "review")
        lines.append(
            f"| {cause:<39} | {cnt:>6} | {share * 100:>5.1f}%"
            f" | {disp:<11} |"
        )
    lines.append("")

    # Threshold evaluation
    unwanted = sum(
        c
        for cause, c in counts.items()
        if _BYPASS_CAUSE_DISPOSITION.get(cause) == "unwanted"
    )
    unwanted_share = unwanted / total
    unknown_share = counts.get("unknown", 0) / total

    unwanted_status = (
        "PASS" if unwanted_share <= _UNWANTED_BYPASS_SHARE_MAX else "WARN"
    )
    unknown_status = (
        "PASS" if unknown_share <= _UNKNOWN_SHARE_WARN else "WARN"
    )

    lines.append(
        f"{unwanted_status} — unwanted-bypass share"
        f" {unwanted_share * 100:.1f}%"
        f" (threshold: ≤{_UNWANTED_BYPASS_SHARE_MAX * 100:.0f}% bootstrap)"
    )
    lines.append(
        f"{unknown_status} — unknown share {unknown_share * 100:.1f}%"
        f" (threshold: ≤{_UNKNOWN_SHARE_WARN * 100:.0f}% bootstrap)"
    )
    if pre_enrichment > 0:
        lines.append(
            f"Pre-enrichment baseline (not counted): {pre_enrichment} events"
        )
    lines.append("")

    return lines


def format_report_output(
    invariants: dict[str, MetricResult],
    runtime_metrics: dict[str, MetricResult],
    dispatch_log: list[dict[str, Any]] | None = None,
    catalog_entries: list[dict[str, Any]] | None = None,
    drift_events: list[dict[str, Any]] | None = None,
) -> str:
    """Format a full markdown health report covering both metric classes.

    Args:
        invariants:       CI invariant results from check_ci_invariants.
        runtime_metrics:  Runtime telemetry results from compute_metrics.
        dispatch_log:     Raw dispatch log events (used to surface
            harness_version in the report header).  Defaults to None
            (omits version line).
        catalog_entries:  Pre-loaded catalog entry list for Notable
            Findings computation.  When ``None``, entries are loaded
            from the live (or ``DISPATCH_CATALOG_PATH``-overridden)
            catalog file.
        drift_events:     Pre-loaded drift events for the bypass-causes
            section.  When ``None``, the section is omitted entirely
            (opt-in; callers that don't pass this arg see no change).

    Returns:
        Markdown-formatted string.
    """
    lines: list[str] = []
    lines.append("# Router Health Report")
    lines.append("")
    lines.append(
        "Metrics split per v5 §3.3.4: **CI invariants** (pre-ship, must pass) vs "
        "**runtime telemetry** (post-ship, informs iteration)."
    )
    lines.append("")

    # Surface the version so /router-health output is interpretable across
    # tool changes.  Absent on legacy log entries without this field.
    harness_version = most_recent_harness_version(dispatch_log or [])
    if harness_version:
        lines.append(f"**Harness version (most recent):** `{harness_version}`")
    else:
        lines.append("**Harness version:** _(unversioned — legacy log entries)_")
    lines.append("")

    # --- Section 1: CI Invariants ---
    lines.append("## CI Invariants")
    lines.append("")
    lines.append(
        "Pre-ship checks. These must pass before releasing changes to skill/agent frontmatter."
    )
    lines.append("")
    lines.append("| Status | Metric | Threshold | Detail |")
    lines.append("|--------|--------|-----------|--------|")
    for key, result in invariants.items():
        status = _status_str(result)
        lines.append(f"| {status} | {result.label} | {result.threshold} | {result.detail} |")
    lines.append("")

    ci_failing = [r for r in invariants.values() if not r.healthy]
    if ci_failing:
        lines.append("> **ACTION REQUIRED** — the following CI invariants are failing:")
        for r in ci_failing:
            lines.append(f">   - **{r.label}**: {r.detail}")
        lines.append("")

    # --- Section 2: Runtime Telemetry ---
    lines.append("## Runtime Telemetry")
    lines.append("")
    lines.append(
        "Computed from drift log and dispatch log. "
        "Informs routing quality iteration — not a CI gate."
    )
    lines.append("")
    lines.append("| Status | Metric | Value | Threshold | Detail |")
    lines.append("|--------|--------|-------|-----------|--------|")

    # Separate informational from non-informational runtime metrics
    telemetry_metrics = {
        k: v for k, v in runtime_metrics.items() if v.metric_class == "runtime_telemetry"
    }
    info_metrics = {k: v for k, v in runtime_metrics.items() if v.metric_class == "informational"}

    for key, result in telemetry_metrics.items():
        status = _status_str(result)
        value_str = f"{result.value:.1%}" if result.value <= 1.0 else f"{result.value:.0f}"
        lines.append(
            f"| {status} | {result.label} | {value_str} | {result.threshold} | {result.detail} |"
        )
    lines.append("")

    # Threshold breach summary
    rt_failing = [r for r in telemetry_metrics.values() if not r.healthy]
    if rt_failing:
        lines.append(
            "> **THRESHOLD BREACH** — the following runtime metrics are outside healthy ranges:"
        )
        for r in rt_failing:
            lines.append(f">   - **{r.label}**: {r.detail} (threshold: {r.threshold})")
        lines.append("")
    else:
        lines.append("> All runtime telemetry metrics are within healthy ranges.")
        lines.append("")

    # --- Section 2b: Bypass causes (v2 telemetry enrichment) ---
    if drift_events is not None:
        lines.extend(_build_bypass_causes_section(drift_events))

    # --- Section 3: Informational ---
    if info_metrics:
        lines.append("## Informational Metrics")
        lines.append("")
        lines.append(
            "These events are **informational only** — they are not drift threshold "
            "breaches and do not affect CI or health status."
        )
        lines.append("")
        lines.append("| Metric | Value | Detail |")
        lines.append("|--------|-------|--------|")
        for key, result in info_metrics.items():
            value_str = f"{result.value:.0f}"
            lines.append(f"| {result.label} (informational) | {value_str} | {result.detail} |")
        lines.append("")
        lines.append(
            "> `skill_mediated_delegation` events are counted per session and in "
            "total. They are **not** treated as drift threshold breaches — this is "
            "the correct skill-first dispatch pattern (v5 §3.2.1)."
        )
        lines.append("")

    # --- Section 4: Notable Findings ---
    lines.append("## Notable Findings")
    lines.append("")
    entries = catalog_entries if catalog_entries is not None else load_catalog_entries()
    n_skills, m_agents, k_routable = compute_plugin_entry_counts(entries)
    skill_word = "skill" if n_skills == 1 else "skills"
    agent_word = "agent" if m_agents == 1 else "agents"
    routable_word = "agent" if k_routable == 1 else "agents"
    lines.append(
        f"Plugin entries: {n_skills} {skill_word}, {m_agents} {agent_word} "
        f"({k_routable} {routable_word} routable via override)"
    )
    lines.append("")

    # --- Section 5: Drift action thresholds reference ---
    lines.append("## Drift Action Thresholds (v5 §3.3.3)")
    lines.append("")
    lines.append(
        "For reference: thresholds at which the user should investigate "
        "each drift type."
    )
    lines.append("")
    lines.append("| Drift type | Action threshold |")
    lines.append("|------------|-----------------|")
    lines.append("| `bypass` | ≥ 5 events with same subagent_type in 7 days |")
    lines.append(
        "| `stale_dispatch` | ≥ 3 events in 7 days "
        "(advisory-only until STALENESS_BOUND calibrated) |"
    )
    lines.append(
        "| `advisory_override` | ≥ 3 events with same router-vs-catalog choice in 7 days |"
    )
    lines.append("| `self_handle_unaided_invocation` | ≥ 10 events in 7 days |")
    lines.append("| `needs_more_detail_repeat` | ≥ 3 events in 7 days |")
    lines.append("| `catalog_degraded_session` | ≥ 1 ever → immediate action |")
    lines.append("")

    return "\n".join(lines) + "\n"
