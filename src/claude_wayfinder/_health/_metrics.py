"""Metrics computation for the router health tool — v5 §3.3.4.

Defines the ``MetricResult`` dataclass, threshold constants, plugin-entry
counting, and the core telemetry-computation function ``compute_metrics``.

No intra-package dependencies — this submodule is the foundation layer that
all other ``_health`` submodules import from.

Public names re-exported via ``_health/__init__.py``:
    MetricResult, compute_metrics, compute_plugin_entry_counts
"""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from typing import Any, Literal

from claude_wayfinder.match_filters import is_agent_routable

# ---------------------------------------------------------------------------
# Constants — healthy thresholds (v5 §3.3.4 / §3.3.3)
# ---------------------------------------------------------------------------

# Runtime telemetry thresholds
_DISPATCH_INVOCATION_RATE_MIN = 0.80  # ≥ 80 % → healthy
_BYPASS_RATE_MAX = 0.10  # ≤ 10 % → healthy
_ADVISORY_OVERRIDE_RATE_MAX = 0.30  # ≤ 30 % → healthy
# catalog_availability: any catalog_degraded_session event = immediate action

# Bypass-cause taxonomy thresholds — F-1-recalibrated (issue #159 baseline:
# 7-day unwanted-bypass share ≈ 17.8%, unknown share = 0%).
_UNWANTED_BYPASS_SHARE_MAX = 0.20  # F-1 baseline ~17.8%; margin above observed
_UNKNOWN_SHARE_WARN = 0.05  # F-1 baseline 0%; tight warn for new unknowns
_BYPASS_CAUSE_MIN_SAMPLE = 100  # low-N guard: section renders N/A below this

# Cause → disposition mapping (mirrors scripts/analyze-drift-causes.py)
_BYPASS_CAUSE_DISPOSITION: dict[str, str] = {
    "skill_mediated_interactive": "expected",
    "skill_mediated_other": "review",
    "router_direct_after_consumed_dispatch": "unwanted",
    "router_direct_no_dispatch": "unwanted",
    "stale_dispatch": "review",
    "unknown": "review",
}

MetricClass = Literal["ci_invariant", "runtime_telemetry", "informational"]


# ---------------------------------------------------------------------------
# MetricResult
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class MetricResult:
    """One evaluated health metric.

    Attributes:
        label:        Human-readable metric name for display.
        metric_class: Classification bucket — "ci_invariant",
                      "runtime_telemetry", or "informational".
        value:        Numeric metric value (rate 0.0-1.0, or count).
        healthy:      True when within the healthy range; informational
                      metrics are always True.
        threshold:    Human-readable threshold string (e.g. "≥ 80%").
        detail:       Optional extra context for the report.
    """

    label: str
    metric_class: MetricClass
    value: float
    healthy: bool
    threshold: str = ""
    detail: str = ""


# ---------------------------------------------------------------------------
# Plugin entry counts
# ---------------------------------------------------------------------------

_PLUGIN_SOURCES: frozenset[str] = frozenset({"plugin", "plugin-override"})


def _catalog_path() -> Path | None:
    """Return the dispatch-catalog.json path from ``DISPATCH_CATALOG_PATH``.

    Returns ``None`` when the environment variable is absent — callers must
    treat ``None`` as "no catalog available" and return empty results rather
    than falling back to ``~/.claude/``.

    The ``~/.claude/state/dispatch-catalog.json`` default and the old
    ``_DEFAULT_CATALOG_PATH`` constant have been removed (Issue #10).

    Returns:
        Path to the catalog file, or ``None`` when the env var is unset.
    """
    env_override = os.environ.get("DISPATCH_CATALOG_PATH", "")
    if env_override:
        return Path(env_override)
    return None


def compute_plugin_entry_counts(
    catalog: list[dict[str, Any]],
) -> tuple[int, int, int]:
    """Count plugin-sourced entries in a loaded catalog entry list.

    Counts are sourced from the caller-provided entry list so the function
    is pure and testable without touching the filesystem.

    Args:
        catalog: List of entry dicts from the ``"entries"`` key of a
            dispatch-catalog.json file.

    Returns:
        A 3-tuple ``(n_skills, m_agents, k_routable)`` where:

        * ``n_skills``  — entries with ``kind=="skill"`` and
          ``source in {"plugin", "plugin-override"}``.
        * ``m_agents``  — entries with ``kind=="agent"`` and
          ``source in {"plugin", "plugin-override"}``.
        * ``k_routable``— subset of ``m_agents`` where
          ``is_agent_routable(name=..., kind=..., source=...)`` returns
          ``True`` (only ``source=="plugin-override"`` agents qualify).
    """
    n_skills = 0
    m_agents = 0
    k_routable = 0
    for entry in catalog:
        kind = entry.get("kind", "")
        source = entry.get("source", "")
        name = entry.get("name", "")
        if source not in _PLUGIN_SOURCES:
            continue
        if kind == "skill":
            n_skills += 1
        elif kind == "agent":
            m_agents += 1
            if is_agent_routable(name=name, kind=kind, source=source):
                k_routable += 1
    return n_skills, m_agents, k_routable


# ---------------------------------------------------------------------------
# Metric computation
# ---------------------------------------------------------------------------


def compute_metrics(
    dispatch_log: list[dict[str, Any]],
    drift_log: list[dict[str, Any]],
) -> dict[str, MetricResult]:
    """Compute all runtime telemetry metrics from log data.

    Args:
        dispatch_log: Events from dispatch-log.jsonl (agent_dispatch events).
        drift_log:    Events from router-drift.jsonl (all drift event types).

    Returns:
        Dict mapping metric key to MetricResult.
    """
    # --- Count raw events ---
    agent_dispatches = sum(1 for e in dispatch_log if e.get("type") == "agent_dispatch")

    # Floor-hook drift events (router_drift with category field)
    bypass_count = sum(
        1 for e in drift_log if e.get("type") == "router_drift" and e.get("category") == "bypass"
    )
    # skill_mediated is informational (floor-hook emits it as category=skill_mediated)
    skill_mediated_floor_count = sum(
        1
        for e in drift_log
        if e.get("type") == "router_drift" and e.get("category") == "skill_mediated"
    )

    # Stop-hook drift event counts
    advisory_override_count = sum(1 for e in drift_log if e.get("type") == "advisory_override")
    catalog_degraded_count = sum(
        1 for e in drift_log if e.get("type") == "catalog_degraded_session"
    )
    # skill_mediated_delegation from Stop hook: sum the per-session counts
    skill_mediated_delegation_total = sum(
        e.get("count", 0) for e in drift_log if e.get("type") == "skill_mediated_delegation"
    )
    # Also add floor-hook skill_mediated events to total skill-mediated count
    total_skill_mediated = skill_mediated_delegation_total + skill_mediated_floor_count

    # --- Dispatch invocation rate ---
    # Total potential agent calls = dispatches + bypasses
    # (skill_mediated is NOT counted as a bypass; it's the correct skill-first path)
    total_agent_calls = agent_dispatches + bypass_count
    if total_agent_calls == 0:
        dispatch_rate = 1.0
    else:
        dispatch_rate = agent_dispatches / total_agent_calls

    # --- Bypass rate ---
    bypass_rate = bypass_count / total_agent_calls if total_agent_calls > 0 else 0.0

    # --- Advisory override rate ---
    # Denominator: total agent dispatches (proxy for advisory decisions seen)
    advisory_override_rate = (
        advisory_override_count / agent_dispatches if agent_dispatches > 0 else 0.0
    )

    # --- Catalog availability ---
    # Any catalog_degraded_session event is immediate action (v5 §3.3.3)
    catalog_healthy = catalog_degraded_count == 0
    # Approximate availability as fraction of sessions without degradation
    total_sessions = len(
        {e.get("session_id") for e in dispatch_log + drift_log if e.get("session_id")}
    )
    if total_sessions == 0:
        catalog_avail = 1.0
    else:
        degraded_sessions = len(
            {e.get("session_id") for e in drift_log if e.get("type") == "catalog_degraded_session"}
        )
        catalog_avail = (total_sessions - degraded_sessions) / total_sessions

    return {
        "dispatch_invocation_rate": MetricResult(
            label="Dispatch invocation rate",
            metric_class="runtime_telemetry",
            value=dispatch_rate,
            healthy=dispatch_rate >= _DISPATCH_INVOCATION_RATE_MIN,
            threshold=f"≥ {_DISPATCH_INVOCATION_RATE_MIN:.0%}",
            detail=f"{agent_dispatches} dispatches / {total_agent_calls} total agent calls",
        ),
        "bypass_rate": MetricResult(
            label="Bypass rate",
            metric_class="runtime_telemetry",
            value=bypass_rate,
            healthy=bypass_rate <= _BYPASS_RATE_MAX,
            threshold=f"≤ {_BYPASS_RATE_MAX:.0%}",
            detail=f"{bypass_count} bypass events / {total_agent_calls} total agent calls",
        ),
        "advisory_override_rate": MetricResult(
            label="Advisory override rate",
            metric_class="runtime_telemetry",
            value=advisory_override_rate,
            healthy=advisory_override_rate <= _ADVISORY_OVERRIDE_RATE_MAX,
            threshold=f"≤ {_ADVISORY_OVERRIDE_RATE_MAX:.0%}",
            detail=f"{advisory_override_count} overrides / {agent_dispatches} dispatches",
        ),
        "catalog_availability": MetricResult(
            label="Catalog availability",
            metric_class="runtime_telemetry",
            value=catalog_avail,
            healthy=catalog_healthy,
            threshold="≥ 99% (any degraded_session event = immediate action)",
            detail=f"{catalog_degraded_count} catalog_degraded_session events",
        ),
        "skill_mediated_delegation": MetricResult(
            label="Skill-mediated delegation",
            metric_class="informational",
            value=float(total_skill_mediated),
            healthy=True,  # Informational — never a breach
            threshold="N/A (informational)",
            detail=(
                f"{total_skill_mediated} total skill-mediated delegations "
                f"({skill_mediated_delegation_total} from Stop hook, "
                f"{skill_mediated_floor_count} from floor hook)"
            ),
        ),
    }
