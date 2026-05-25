"""Router health reporting tool — v5 §3.3.4 metrics.

Reports pre-ship CI invariants and runtime telemetry for the deterministic
dispatch system. Observability design and action thresholds: docs/schema.md §5.
Design rationale: docs/design.md.

Two output modes:
  --ci      Pre-ship CI invariants only; exits non-zero on failure.
  --report  Full markdown report covering both CI invariants and runtime
            telemetry.

Three drill-down subcommands (new in #170):
  drill         Drill into a single runtime metric (bypass, advisory-override,
                recent-drift) with day-by-day and per-session breakdown.
  top           Show top-N dispatched agents or most-invoked skills.
  catalog-status  Summarise plugin entry counts from the dispatch catalog.

Log file inputs:
  ~/.claude/state/router-drift.jsonl   Drift events from hooks.
  ~/.claude/state/dispatch-log.jsonl   Agent dispatch events from the
                                       PreToolUse log-agent-dispatch hook.

Internal submodules (private — do not import directly):
  _metrics  — MetricResult, compute_metrics, threshold constants
  _checks   — check_ci_invariants and CI sub-checks
  _report   — I/O helpers and report formatting
  _drill    — window helpers and drill/top/catalog-status subcommands
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from claude_wayfinder._health._checks import (
    check_ci_invariants as check_ci_invariants,
)

# ---------------------------------------------------------------------------
# Import from extracted submodule: _drill
# ---------------------------------------------------------------------------
from claude_wayfinder._health._drill import (
    _cmd_catalog_status as _cmd_catalog_status,
)
from claude_wayfinder._health._drill import (
    _cmd_drill as _cmd_drill,
)
from claude_wayfinder._health._drill import (
    _cmd_top as _cmd_top,
)
from claude_wayfinder._health._drill import (
    _drill_advisory_override as _drill_advisory_override,
)
from claude_wayfinder._health._drill import (
    _drill_bypass as _drill_bypass,
)
from claude_wayfinder._health._drill import (
    _drill_recent_drift as _drill_recent_drift,
)
from claude_wayfinder._health._drill import (
    _event_kind as _event_kind,
)
from claude_wayfinder._health._drill import (
    _events_in_window as _events_in_window,
)
from claude_wayfinder._health._drill import (
    _parse_window as _parse_window,
)
from claude_wayfinder._health._metrics import (
    _ADVISORY_OVERRIDE_RATE_MAX as _ADVISORY_OVERRIDE_RATE_MAX,
)
from claude_wayfinder._health._metrics import (
    _BYPASS_CAUSE_DISPOSITION as _BYPASS_CAUSE_DISPOSITION,
)
from claude_wayfinder._health._metrics import (
    _BYPASS_CAUSE_MIN_SAMPLE as _BYPASS_CAUSE_MIN_SAMPLE,
)
from claude_wayfinder._health._metrics import (
    _BYPASS_RATE_MAX as _BYPASS_RATE_MAX,
)
from claude_wayfinder._health._metrics import (
    _DISPATCH_INVOCATION_RATE_MIN as _DISPATCH_INVOCATION_RATE_MIN,
)
from claude_wayfinder._health._metrics import (
    _PLUGIN_SOURCES as _PLUGIN_SOURCES,
)
from claude_wayfinder._health._metrics import (
    _UNKNOWN_SHARE_WARN as _UNKNOWN_SHARE_WARN,
)
from claude_wayfinder._health._metrics import (
    _UNWANTED_BYPASS_SHARE_MAX as _UNWANTED_BYPASS_SHARE_MAX,
)

# ---------------------------------------------------------------------------
# Import from extracted submodule: _metrics
# ---------------------------------------------------------------------------
from claude_wayfinder._health._metrics import (
    MetricClass as MetricClass,
)
from claude_wayfinder._health._metrics import (
    MetricResult as MetricResult,
)
from claude_wayfinder._health._metrics import (
    _catalog_path as _catalog_path,
)
from claude_wayfinder._health._metrics import (
    compute_metrics as compute_metrics,
)
from claude_wayfinder._health._metrics import (
    compute_plugin_entry_counts as compute_plugin_entry_counts,
)

# ---------------------------------------------------------------------------
# Import from extracted submodule: _report
# ---------------------------------------------------------------------------
from claude_wayfinder._health._report import (
    _STATUS_FAIL as _STATUS_FAIL,
)
from claude_wayfinder._health._report import (
    _STATUS_INFO as _STATUS_INFO,
)
from claude_wayfinder._health._report import (
    _STATUS_PASS as _STATUS_PASS,
)
from claude_wayfinder._health._report import (
    _build_bypass_causes_section as _build_bypass_causes_section,
)
from claude_wayfinder._health._report import (
    _status_str as _status_str,
)
from claude_wayfinder._health._report import (
    format_ci_output as format_ci_output,
)
from claude_wayfinder._health._report import (
    format_report_output as format_report_output,
)
from claude_wayfinder._health._report import (
    load_catalog_entries as load_catalog_entries,
)
from claude_wayfinder._health._report import (
    load_jsonl as load_jsonl,
)
from claude_wayfinder._health._report import (
    most_recent_harness_version as most_recent_harness_version,
)

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _default_drift_log() -> Path:
    """Return the default drift-log path, resolved at call time.

    Precedence (matches ``scripts/analyze-drift-causes.py``):
      1. ``ROUTER_DRIFT_PATH`` env var.
      2. ``~/.claude/state/router-drift.jsonl`` via ``Path.home()``.

    Resolved at call time (not import time) so that test monkeypatching of
    environment variables and ``Path.home()`` takes effect.

    Returns:
        Path to the drift log file.
    """
    import os

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
    import os

    env = os.environ.get("DISPATCH_LOG")
    if env:
        return Path(env)
    return Path.home() / ".claude" / "state" / "dispatch-log.jsonl"


def _default_skills_dir() -> Path:
    """Return the default skills directory path, resolved at call time.

    Precedence:
      1. ``ROUTER_SKILLS_DIR`` env var.
      2. ``~/.claude/skills`` via ``Path.home()``.

    Returns:
        Path to the skills directory.
    """
    import os

    env = os.environ.get("ROUTER_SKILLS_DIR")
    if env:
        return Path(env)
    return Path.home() / ".claude" / "skills"


def _default_agents_dir() -> Path:
    """Return the default agents directory path, resolved at call time.

    Precedence:
      1. ``ROUTER_AGENTS_DIR`` env var.
      2. ``~/.claude/agents`` via ``Path.home()``.

    Returns:
        Path to the agents directory.
    """
    import os

    env = os.environ.get("ROUTER_AGENTS_DIR")
    if env:
        return Path(env)
    return Path.home() / ".claude" / "agents"


def _default_plugin_overrides_dir() -> Path:
    """Return the default plugin-overrides directory path, resolved at call time.

    Precedence:
      1. ``ROUTER_PLUGIN_OVERRIDES_DIR`` env var.
      2. ``~/.claude/triggers`` via ``Path.home()``.

    Returns:
        Path to the plugin-overrides directory.
    """
    import os

    env = os.environ.get("ROUTER_PLUGIN_OVERRIDES_DIR")
    if env:
        return Path(env)
    return Path.home() / ".claude" / "triggers"


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Each path argument now defaults to an env-var override or the standard
    ``~/.claude/...`` location when the flag is omitted (Issue #262).
    Defaults are resolved at call time via helper functions so that test
    monkeypatching of ``HOME``/``USERPROFILE`` takes effect.

    Explicit flags always win over env vars, which win over home-dir defaults.

    Args:
        argv: Argument list.  Defaults to sys.argv[1:].

    Returns:
        Exit code.  --ci returns non-zero on invariant failure.
        --report always returns 0.
    """
    # Ensure stdout can handle Unicode (e.g. ≥, ≤, §) on Windows where the
    # default console encoding may be cp1252.  reconfigure() is available in
    # Python 3.7+.  This is safe to call before any output.
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    # Route new drill-down subcommands (#170) before the existing argparse
    # so the --ci / --report mutually-exclusive group is completely unaffected.
    _subcommands: dict[str, Any] = {
        "drill": _cmd_drill,
        "top": _cmd_top,
        "catalog-status": _cmd_catalog_status,
    }
    if argv and argv[0] in _subcommands:
        return _subcommands[argv[0]](argv[1:])

    parser = argparse.ArgumentParser(
        description=(
            "Router health reporting tool — v5 §3.3.4 metrics.\n"
            "Reports pre-ship CI invariants and runtime telemetry."
        )
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--ci",
        action="store_true",
        help="Pre-ship CI invariants only; exits non-zero on failure.",
    )
    mode.add_argument(
        "--report",
        action="store_true",
        help="Full markdown report with both CI invariants and runtime telemetry.",
    )

    parser.add_argument(
        "--catalog-path",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Path to dispatch-catalog.json.  Falls back to "
            "DISPATCH_CATALOG_PATH env var when omitted; catalog section "
            "of the report is empty when neither is set."
        ),
    )
    parser.add_argument(
        "--drift-log",
        type=Path,
        default=None,
        help=(
            "Path to router-drift.jsonl.  Defaults to "
            "$ROUTER_DRIFT_PATH env var, then "
            "~/.claude/state/router-drift.jsonl.  "
            "Missing files are treated as empty."
        ),
    )
    parser.add_argument(
        "--dispatch-log",
        type=Path,
        default=None,
        help=(
            "Path to dispatch-log.jsonl.  Defaults to "
            "$DISPATCH_LOG env var, then "
            "~/.claude/state/dispatch-log.jsonl.  "
            "Missing files are treated as empty."
        ),
    )
    parser.add_argument(
        "--skills-dir",
        type=Path,
        default=None,
        help=(
            "Skills directory for CI invariant checks.  Defaults to "
            "$ROUTER_SKILLS_DIR env var, then ~/.claude/skills."
        ),
    )
    parser.add_argument(
        "--agents-dir",
        type=Path,
        default=None,
        help=(
            "Agents directory for CI invariant checks.  Defaults to "
            "$ROUTER_AGENTS_DIR env var, then ~/.claude/agents."
        ),
    )
    parser.add_argument(
        "--plugin-overrides-dir",
        type=Path,
        default=None,
        help=(
            "Plugin overrides directory for CI invariant checks.  Defaults to "
            "$ROUTER_PLUGIN_OVERRIDES_DIR env var, then ~/.claude/triggers."
        ),
    )

    args = parser.parse_args(argv)

    # Resolve paths: explicit flag > env var > home-dir default (Issue #262).
    # load_jsonl treats missing files as empty — no exception raised.
    drift_log_path = args.drift_log if args.drift_log is not None else _default_drift_log()
    dispatch_log_path = (
        args.dispatch_log if args.dispatch_log is not None else _default_dispatch_log()
    )
    skills_dir = (
        args.skills_dir if args.skills_dir is not None else _default_skills_dir()
    )
    agents_dir = (
        args.agents_dir if args.agents_dir is not None else _default_agents_dir()
    )
    plugin_overrides_dir = (
        args.plugin_overrides_dir
        if args.plugin_overrides_dir is not None
        else _default_plugin_overrides_dir()
    )

    dispatch_log = load_jsonl(dispatch_log_path)
    drift_log = load_jsonl(drift_log_path)

    invariants = check_ci_invariants(
        skills_dir=skills_dir,
        agents_dir=agents_dir,
        plugin_overrides_dir=plugin_overrides_dir,
    )

    if args.ci:
        # Also check: any catalog_degraded_session event = CI failure
        runtime_metrics = compute_metrics(dispatch_log, drift_log)
        if not runtime_metrics["catalog_availability"].healthy:
            invariants["catalog_availability_runtime"] = MetricResult(
                label="Catalog availability (runtime)",
                metric_class="ci_invariant",
                value=runtime_metrics["catalog_availability"].value,
                healthy=False,
                threshold="No catalog_degraded_session events",
                detail=runtime_metrics["catalog_availability"].detail,
            )

        output = format_ci_output(invariants)
        print(output, end="")

        all_pass = all(r.healthy for r in invariants.values())
        return 0 if all_pass else 1

    else:  # --report
        runtime_metrics = compute_metrics(dispatch_log, drift_log)
        # catalog_path arg: explicit flag > DISPATCH_CATALOG_PATH env var > None
        catalog_entries = load_catalog_entries(catalog_path=args.catalog_path)
        output = format_report_output(
            invariants,
            runtime_metrics,
            dispatch_log=dispatch_log,
            catalog_entries=catalog_entries,
            drift_events=drift_log,
        )
        print(output, end="")
        return 0


if __name__ == "__main__":
    sys.exit(main())
