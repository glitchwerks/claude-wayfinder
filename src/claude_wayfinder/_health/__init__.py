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
  (remaining submodules extracted in subsequent commits)
"""

from __future__ import annotations

import argparse
import collections
import datetime
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

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
# CI invariants
# ---------------------------------------------------------------------------


def check_ci_invariants(
    *,
    skills_dir: Path | None,
    agents_dir: Path | None,
    plugin_overrides_dir: Path | None = None,
) -> dict[str, MetricResult]:
    """Run pre-ship CI invariants.

    Three invariants per v5 §3.3.4:
      1. catalog_stability   — generate catalog twice, compare byte-for-byte.
      2. schema_validation   — generator exits 0 with no fatal-severity entries.
      3. trigger_firing_accuracy — smoke tests from fixtures/trigger-smoke-tests.json.

    When ``skills_dir`` or ``agents_dir`` is ``None``, all three invariants
    are marked unhealthy with a descriptive message (paths not configured).

    Args:
        skills_dir:           Path to the skills tree, or ``None``.
        agents_dir:           Path to the agents directory, or ``None``.
        plugin_overrides_dir: Path to the triggers override directory.

    Returns:
        Dict mapping invariant key to MetricResult.
    """
    if skills_dir is None or agents_dir is None:
        msg = (
            "CI invariant checks require --skills-dir and --agents-dir. "
            "Pass explicit paths or set them as arguments."
        )
        not_configured = MetricResult(
            label="Not configured",
            metric_class="ci_invariant",
            value=0,
            healthy=False,
            threshold="Paths required",
            detail=msg,
        )
        return {
            "catalog_stability": not_configured,
            "schema_validation": not_configured,
            "trigger_firing_accuracy": not_configured,
        }
    results: dict[str, MetricResult] = {}
    catalog_stability = _check_catalog_stability(
        skills_dir=skills_dir,
        agents_dir=agents_dir,
        plugin_overrides_dir=plugin_overrides_dir,
    )
    results["catalog_stability"] = catalog_stability
    results["schema_validation"] = _check_schema_validation(
        skills_dir=skills_dir,
        agents_dir=agents_dir,
        plugin_overrides_dir=plugin_overrides_dir,
    )
    # Trigger-firing accuracy uses an ephemeral catalog generated from the
    # provided skills/agents dirs so the check is self-contained and portable
    # to CI runners that have no ~/.claude/state/dispatch-catalog.json.
    results["trigger_firing_accuracy"] = _check_trigger_firing_accuracy(
        skills_dir=skills_dir,
        agents_dir=agents_dir,
        plugin_overrides_dir=plugin_overrides_dir,
    )
    return results


def _run_generator(
    *,
    skills_dir: Path,
    agents_dir: Path,
    plugin_overrides_dir: Path | None,
    out_path: Path,
    log_path: Path,
    plugins_dir: Path | None = None,
    builtin_agents_dir: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run build_dispatch_catalog.py and return the completed process.

    Args:
        skills_dir:           Path to the skills tree.
        agents_dir:           Path to the agents directory.
        plugin_overrides_dir: Path to the triggers override directory.
        out_path:             Catalog output path.
        log_path:             Log output path.
        plugins_dir:          Path to the installed-plugins manifest
            directory (``plugins/installed_plugins.json``).  When
            ``None``, ``--plugins-dir`` is explicitly set to a path
            that does not exist so the generator does not pick up the
            caller's real ``~/.claude/plugins/`` directory — which
            is important for CI-isolated builds that operate on empty
            fixture dirs.
        builtin_agents_dir:   Path to the builtin-agent sidecar ``.yml``
            directory (``~/.claude/triggers/builtin/`` by default in the
            generator).  When ``None``, ``--builtin-agents-dir`` is set
            to a nonexistent path so the generator does not pick up
            ``Explore.yml`` / ``Plan.yml`` from the caller's real
            ``~/.claude/triggers/builtin/`` — which would add entries
            to what should be an empty CI-isolated catalog.
    """
    script = Path(__file__).parent.parent / "build_catalog.py"
    cmd = [
        sys.executable,
        str(script),
        "--skills-dir",
        str(skills_dir),
        "--agents-dir",
        str(agents_dir),
        "--out",
        str(out_path),
        "--log",
        str(log_path),
    ]
    if plugin_overrides_dir is not None:
        cmd += ["--plugin-overrides-dir", str(plugin_overrides_dir)]
    # Pass an explicit --plugins-dir so the generator never falls back to
    # the real ~/.claude/plugins/ when called in isolation (CI or tests).
    # A non-existent path produces an info-level "manifest not found" entry
    # and zero plugin entries, which is the correct no-op for isolated runs.
    effective_plugins_dir = (
        plugins_dir if plugins_dir is not None else out_path.parent / "_no_plugins"
    )
    cmd += ["--plugins-dir", str(effective_plugins_dir)]
    # Pass an explicit --builtin-agents-dir for the same isolation reason:
    # without it the generator defaults to ~/.claude/triggers/builtin/ and
    # picks up Explore.yml / Plan.yml, producing a non-empty catalog even
    # when skills_dir and agents_dir are empty.
    effective_builtin_dir = (
        builtin_agents_dir
        if builtin_agents_dir is not None
        else out_path.parent / "_no_builtins"
    )
    cmd += ["--builtin-agents-dir", str(effective_builtin_dir)]
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def _check_catalog_stability(
    *,
    skills_dir: Path,
    agents_dir: Path,
    plugin_overrides_dir: Path | None,
) -> MetricResult:
    """Run the catalog generator twice and compare output byte-for-byte.

    Real failure modes caught: non-deterministic hash ordering, filesystem
    iteration order differences, YAML parser quirks.  Per v5 §3.3.4.

    Returns:
        MetricResult with healthy=True if both runs produce identical output.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        out_a = tmp / "catalog-A.json"
        out_b = tmp / "catalog-B.json"
        log_a = tmp / "log-A.txt"
        log_b = tmp / "log-B.txt"

        proc_a = _run_generator(
            skills_dir=skills_dir,
            agents_dir=agents_dir,
            plugin_overrides_dir=plugin_overrides_dir,
            out_path=out_a,
            log_path=log_a,
        )
        if proc_a.returncode not in (0, 2):
            return MetricResult(
                label="Catalog stability",
                metric_class="ci_invariant",
                value=0.0,
                healthy=False,
                threshold="Byte-for-byte identical on two runs",
                detail=(
                    f"First generator run failed (exit {proc_a.returncode}): "
                    f"{proc_a.stderr.strip()}"
                ),
            )

        proc_b = _run_generator(
            skills_dir=skills_dir,
            agents_dir=agents_dir,
            plugin_overrides_dir=plugin_overrides_dir,
            out_path=out_b,
            log_path=log_b,
        )
        if proc_b.returncode not in (0, 2):
            return MetricResult(
                label="Catalog stability",
                metric_class="ci_invariant",
                value=0.0,
                healthy=False,
                threshold="Byte-for-byte identical on two runs",
                detail=(
                    f"Second generator run failed (exit {proc_b.returncode}): "
                    f"{proc_b.stderr.strip()}"
                ),
            )

        # Compare outputs byte-for-byte
        content_a = out_a.read_bytes() if out_a.exists() else b""
        content_b = out_b.read_bytes() if out_b.exists() else b""

        if content_a == content_b:
            return MetricResult(
                label="Catalog stability",
                metric_class="ci_invariant",
                value=1.0,
                healthy=True,
                threshold="Byte-for-byte identical on two runs",
                detail=f"Both runs produced {len(content_a)} bytes — identical",
            )
        else:
            return MetricResult(
                label="Catalog stability",
                metric_class="ci_invariant",
                value=0.0,
                healthy=False,
                threshold="Byte-for-byte identical on two runs",
                detail="Outputs differ between runs — catalog generation is non-deterministic",
            )


def _check_schema_validation(
    *,
    skills_dir: Path,
    agents_dir: Path,
    plugin_overrides_dir: Path | None,
) -> MetricResult:
    """Run the catalog generator once and check for fatal-severity log entries.

    Pass condition: generator exits 0 with no 'fatal' lines in the log.
    Warning-severity lines (e.g. ``applicable_agents is empty`` for router-only
    skills) are intentional and non-fatal — they are counted and reported in
    the detail but do not cause this invariant to fail.
    Exit 2 = degraded catalog = fail.

    Returns:
        MetricResult with healthy=True if exit 0 and no fatal log lines.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        out_path = tmp / "catalog.json"
        log_path = tmp / "catalog-generation.log"

        proc = _run_generator(
            skills_dir=skills_dir,
            agents_dir=agents_dir,
            plugin_overrides_dir=plugin_overrides_dir,
            out_path=out_path,
            log_path=log_path,
        )

        if proc.returncode == 2:
            # Exit 2 = degraded catalog.  When no skills/agents exist at all
            # (e.g. CI running against an empty fixture directory), treat this
            # as a schema-validation skip rather than a hard failure — there is
            # nothing to validate.  We detect the "nothing to scan" case by
            # checking whether the catalog was written with zero entries.
            catalog_is_empty = False
            if out_path.exists():
                try:
                    cat = json.loads(out_path.read_text(encoding="utf-8"))
                    catalog_is_empty = len(cat.get("entries", [])) == 0
                except (json.JSONDecodeError, OSError):
                    pass

            if catalog_is_empty:
                return MetricResult(
                    label="Schema validation",
                    metric_class="ci_invariant",
                    value=1.0,
                    healthy=True,
                    threshold="Generator exits 0 with no fatal-severity entries",
                    detail="No skills/agents found — nothing to validate (empty catalog)",
                )

            return MetricResult(
                label="Schema validation",
                metric_class="ci_invariant",
                value=0.0,
                healthy=False,
                threshold="Generator exits 0 with no fatal-severity entries",
                detail=f"Catalog degraded (exit 2): {proc.stderr.strip()}",
            )

        if proc.returncode != 0:
            return MetricResult(
                label="Schema validation",
                metric_class="ci_invariant",
                value=0.0,
                healthy=False,
                threshold="Generator exits 0 with no fatal-severity entries",
                detail=f"Generator error (exit {proc.returncode}): {proc.stderr.strip()}",
            )

        # Scan the log for fatal and warning severity lines.
        # Log line format: "<timestamp> <severity> <entry_name> <message>"
        # Only fatal-severity lines are a CI failure — they indicate an entry
        # was excluded from the catalog.  Warning-severity lines (e.g.
        # "applicable_agents is empty" for router-only skills) are intentional
        # and are surfaced in the detail message only.
        fatal_lines: list[str] = []
        warning_lines: list[str] = []
        if log_path.exists():
            for line in log_path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                parts = stripped.split(" ", 3)  # ts severity entry_name message
                if len(parts) >= 2:
                    severity = parts[1].lower()
                    if severity == "fatal":
                        fatal_lines.append(stripped)
                    elif severity == "warning":
                        warning_lines.append(stripped)

        if fatal_lines:
            preview = "; ".join(fatal_lines[:3])
            detail = f"{len(fatal_lines)} fatal entry error(s): {preview}"
            if warning_lines:
                detail += f" ({len(warning_lines)} warning(s) suppressed — non-fatal)"
            return MetricResult(
                label="Schema validation",
                metric_class="ci_invariant",
                value=0.0,
                healthy=False,
                threshold="Generator exits 0 with no fatal-severity entries",
                detail=detail,
            )

        if warning_lines:
            # Warnings are non-fatal (e.g. router-only skills with empty
            # applicable_agents).  Report count but pass.
            return MetricResult(
                label="Schema validation",
                metric_class="ci_invariant",
                value=1.0,
                healthy=True,
                threshold="Generator exits 0 with no fatal-severity entries",
                detail=f"Exit 0, {len(warning_lines)} non-fatal warning(s) — OK",
            )

        return MetricResult(
            label="Schema validation",
            metric_class="ci_invariant",
            value=1.0,
            healthy=True,
            threshold="Generator exits 0 with no fatal-severity entries",
            detail="Exit 0, no per-entry errors",
        )


def _check_trigger_firing_accuracy(
    *,
    skills_dir: Path,
    agents_dir: Path,
    plugin_overrides_dir: Path | None = None,
) -> MetricResult:
    """Run smoke tests from fixtures/trigger-smoke-tests.json via match.py.

    For each fixture entry with an ``expected_decision`` or
    ``expected_decision_not`` field, run match.py and verify the result.

    An ephemeral catalog is generated from ``skills_dir`` / ``agents_dir`` and
    passed to match.py via the ``DISPATCH_CATALOG_PATH`` environment variable so
    the check is self-contained and does not depend on
    ``~/.claude/state/dispatch-catalog.json`` being present (which it is not on
    CI runners).

    When the ephemeral catalog has zero entries (e.g. empty dirs in test
    isolation), the smoke tests are skipped — there is no catalog to route
    against so the invariant is a no-op pass.

    Args:
        skills_dir:           Path to the skills tree (used to build ephemeral catalog).
        agents_dir:           Path to the agents directory (used to build ephemeral catalog).
        plugin_overrides_dir: Path to the triggers override directory.

    Returns:
        MetricResult with healthy=True if all smoke tests pass.
    """
    fixtures_path = (
        Path(__file__).parents[3] / "tests" / "fixtures" / "trigger-smoke-tests.json"
    )
    if not fixtures_path.exists():
        return MetricResult(
            label="Trigger-rule firing accuracy",
            metric_class="ci_invariant",
            value=1.0,
            healthy=True,
            threshold="Smoke test inputs produce expected match decisions",
            detail="No smoke test fixture file found — skipping",
        )

    try:
        fixtures: list[dict[str, Any]] = json.loads(fixtures_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return MetricResult(
            label="Trigger-rule firing accuracy",
            metric_class="ci_invariant",
            value=0.0,
            healthy=False,
            threshold="Smoke test inputs produce expected match decisions",
            detail=f"Failed to load fixture file: {exc}",
        )

    match_script = Path(__file__).parent.parent / "match.py"
    if not match_script.exists():
        return MetricResult(
            label="Trigger-rule firing accuracy",
            metric_class="ci_invariant",
            value=1.0,
            healthy=True,
            threshold="Smoke test inputs produce expected match decisions",
            detail="match.py not found — skipping trigger-firing check",
        )

    # Generate an ephemeral catalog from the provided skills/agents dirs.
    # This makes the check self-contained — no dependency on the user's
    # ~/.claude/state/dispatch-catalog.json which does not exist on CI runners.
    with tempfile.TemporaryDirectory() as _tmpdir:
        tmp = Path(_tmpdir)
        ephemeral_catalog = tmp / "ephemeral-catalog.json"
        ephemeral_log = tmp / "ephemeral-catalog.log"

        gen_proc = _run_generator(
            skills_dir=skills_dir,
            agents_dir=agents_dir,
            plugin_overrides_dir=plugin_overrides_dir,
            out_path=ephemeral_catalog,
            log_path=ephemeral_log,
        )

        # Exit codes: 0 = fully healthy, 2 = degraded (partial catalog written).
        # Any other code = generator failed entirely.
        if gen_proc.returncode not in (0, 2):
            return MetricResult(
                label="Trigger-rule firing accuracy",
                metric_class="ci_invariant",
                value=0.0,
                healthy=False,
                threshold="Smoke test inputs produce expected match decisions",
                detail=(
                    f"Ephemeral catalog generation failed (exit {gen_proc.returncode}): "
                    f"{gen_proc.stderr.strip()}"
                ),
            )

        # Check catalog entry count: if zero, skip — nothing to route against.
        catalog_entry_count = 0
        if ephemeral_catalog.exists():
            try:
                cat = json.loads(ephemeral_catalog.read_text(encoding="utf-8"))
                catalog_entry_count = len(cat.get("entries", []))
            except (json.JSONDecodeError, OSError):
                pass

        if catalog_entry_count == 0:
            return MetricResult(
                label="Trigger-rule firing accuracy",
                metric_class="ci_invariant",
                value=1.0,
                healthy=True,
                threshold="Smoke test inputs produce expected match decisions",
                detail="No skills/agents to check; skipping trigger-firing accuracy",
            )

        # Build the environment for match.py subprocess calls, overriding
        # DISPATCH_CATALOG_PATH to point at our ephemeral catalog.
        import os as _os

        match_env = {**_os.environ, "DISPATCH_CATALOG_PATH": str(ephemeral_catalog)}

        passed = 0
        failed = 0
        failures: list[str] = []

        for fixture in fixtures:
            desc = fixture.get("description", "(no description)")
            task_desc = fixture.get("task_description", "")
            file_paths = fixture.get("file_paths", [])
            expected = fixture.get("expected_decision")
            expected_not = fixture.get("expected_decision_not")

            if expected is None and expected_not is None:
                # No assertion — skip
                continue

            context = {"task_description": task_desc, "file_paths": file_paths}
            try:
                result = subprocess.run(
                    [sys.executable, str(match_script)],
                    input=json.dumps(context),
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=30,
                    env=match_env,
                )
            except subprocess.TimeoutExpired:
                failed += 1
                failures.append(f"{desc}: timed out")
                continue

            if result.returncode != 0:
                failed += 1
                failures.append(
                    f"{desc}: match.py error (exit {result.returncode}): "
                    f"{result.stderr.strip()[:100]}"
                )
                continue

            try:
                output = json.loads(result.stdout.strip())
                decision = output.get("decision", "")
            except (json.JSONDecodeError, AttributeError):
                # Non-JSON output — unexpected error, count as failure
                failed += 1
                failures.append(f"{desc}: non-JSON output from match.py")
                continue

            if expected is not None and decision != expected:
                failed += 1
                failures.append(f"{desc}: expected {expected!r}, got {decision!r}")
            elif expected_not is not None and decision == expected_not:
                failed += 1
                failures.append(
                    f"{desc}: expected decision != {expected_not!r}, but got {decision!r}"
                )
            else:
                passed += 1

        total = passed + failed
        if total == 0:
            return MetricResult(
                label="Trigger-rule firing accuracy",
                metric_class="ci_invariant",
                value=1.0,
                healthy=True,
                threshold="Smoke test inputs produce expected match decisions",
                detail="No assertable fixtures found",
            )

        rate = passed / total
        if failed == 0:
            return MetricResult(
                label="Trigger-rule firing accuracy",
                metric_class="ci_invariant",
                value=rate,
                healthy=True,
                threshold="Smoke test inputs produce expected match decisions",
                detail=f"{passed}/{total} smoke tests passed",
            )
        else:
            failure_summary = "; ".join(failures[:3])
            if len(failures) > 3:
                failure_summary += f" (and {len(failures) - 3} more)"
            return MetricResult(
                label="Trigger-rule firing accuracy",
                metric_class="ci_invariant",
                value=rate,
                healthy=False,
                threshold="Smoke test inputs produce expected match decisions",
                detail=f"{failed}/{total} smoke tests failed: {failure_summary}",
            )


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

_STATUS_PASS = "PASS"
_STATUS_FAIL = "FAIL"
_STATUS_INFO = "INFO"


def _status_str(result: MetricResult) -> str:
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
    from datetime import datetime, timedelta, timezone

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
        "For reference: thresholds at which the user should investigate " "each drift type."
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
        help="Path to router-drift.jsonl.",
    )
    parser.add_argument(
        "--dispatch-log",
        type=Path,
        default=None,
        metavar="PATH",
        help="Path to dispatch-log.jsonl (reserved for future cross-log correlation).",
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

    drift_events: list[dict[str, Any]] = []
    if args.drift_log is not None:
        drift_events = load_jsonl(args.drift_log)

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
        help="Path to dispatch-log.jsonl.",
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

    dispatch_events: list[dict[str, Any]] = []
    if args.dispatch_log is not None:
        dispatch_events = load_jsonl(args.dispatch_log)

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


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    All directory and file paths previously defaulting to ``~/.claude/...``
    now require explicit values (Issue #10).  The ``~/.claude/`` default
    has been removed; callers must pass ``--catalog-path``, ``--drift-log``,
    ``--dispatch-log``, ``--skills-dir``, ``--agents-dir``, and
    ``--plugin-overrides-dir`` explicitly, or set ``DISPATCH_CATALOG_PATH``
    for the catalog.

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
            "Path to router-drift.jsonl.  Telemetry section is empty "
            "when omitted and the file does not exist."
        ),
    )
    parser.add_argument(
        "--dispatch-log",
        type=Path,
        default=None,
        help=(
            "Path to dispatch-log.jsonl.  Telemetry section is empty "
            "when omitted and the file does not exist."
        ),
    )
    parser.add_argument(
        "--skills-dir",
        type=Path,
        default=None,
        help="Skills directory for CI invariant checks.",
    )
    parser.add_argument(
        "--agents-dir",
        type=Path,
        default=None,
        help="Agents directory for CI invariant checks.",
    )
    parser.add_argument(
        "--plugin-overrides-dir",
        type=Path,
        default=None,
        help="Plugin overrides directory for CI invariant checks.",
    )

    args = parser.parse_args(argv)

    # Log files: treat absent paths the same as empty files.
    _empty: list[dict[str, Any]] = []
    dispatch_log = (
        load_jsonl(args.dispatch_log) if args.dispatch_log is not None else _empty
    )
    drift_log = (
        load_jsonl(args.drift_log) if args.drift_log is not None else _empty
    )

    invariants = check_ci_invariants(
        skills_dir=args.skills_dir,
        agents_dir=args.agents_dir,
        plugin_overrides_dir=args.plugin_overrides_dir,
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
