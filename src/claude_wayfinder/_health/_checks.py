"""CI invariant checks and subprocess-based catalog validation.

Contains the subprocess-based CI gate functions that invoke
``build_catalog.py`` and ``match.py`` in isolation.  All functions
depend only on ``_metrics.MetricResult`` from the sibling submodule —
no other intra-package imports.

Public names re-exported via ``_health/__init__.py``:
    check_ci_invariants
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from claude_wayfinder._health._metrics import MetricResult


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
    # build_catalog is now a package; invoke via -m rather than file path.
    cmd = [
        sys.executable,
        "-m",
        "claude_wayfinder.build_catalog",
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
    # __file__ is src/claude_wayfinder/_health/_checks.py
    # parents[3] = repo root
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
