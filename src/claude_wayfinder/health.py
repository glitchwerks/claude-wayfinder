"""Router health reporting tool — v5 §3.3.4 metrics.

Reports pre-ship CI invariants and runtime telemetry for the deterministic
dispatch system described in
docs/design/2026-04-30-deterministic-first-router-design-v5.md.

Two output modes:
  --ci      Pre-ship CI invariants only; exits non-zero on failure.
  --report  Full markdown report covering both CI invariants and runtime
            telemetry.

Log file inputs:
  ~/.claude/state/router-drift.jsonl   Drift events from hooks.
  ~/.claude/state/dispatch-log.jsonl   Agent dispatch events from the
                                       PreToolUse log-agent-dispatch hook.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import subprocess
import sys
import tempfile
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
    script = Path(__file__).parent / "build_catalog.py"
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
        Path(__file__).parents[2] / "tests" / "fixtures" / "trigger-smoke-tests.json"
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

    match_script = Path(__file__).parent / "match.py"
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


def format_report_output(
    invariants: dict[str, MetricResult],
    runtime_metrics: dict[str, MetricResult],
    dispatch_log: list[dict[str, Any]] | None = None,
    catalog_entries: list[dict[str, Any]] | None = None,
) -> str:
    """Format a full markdown health report covering both metric classes.

    Args:
        invariants:       CI invariant results from check_ci_invariants.
        runtime_metrics:  Runtime telemetry results from compute_metrics.
        dispatch_log:     Raw dispatch log events (used to surface
            harness_version in the report header).  Defaults to None
            (omits version line).
        catalog_entries:  Pre-loaded catalog entry list for Notable Findings
            computation.  When ``None``, entries are loaded from the live
            (or ``DISPATCH_CATALOG_PATH``-overridden) catalog file.

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
        )
        print(output, end="")
        return 0


if __name__ == "__main__":
    sys.exit(main())
