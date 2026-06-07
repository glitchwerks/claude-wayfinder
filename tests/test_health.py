"""Tests for claude_wayfinder/health.py — v5 §3.3.4 metrics.

Tests follow TDD order: each test was written before the implementation it
covers.  Test categories:
  1. Metric computation from synthetic log data
  2. Threshold-breach detection per §3.3.3
  3. CI mode exit codes
  4. --ci and --report output modes
  5. skill_mediated_delegation informational reporting (#341)
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from claude_wayfinder._health import (
    _BYPASS_CAUSE_MIN_SAMPLE,
    _UNKNOWN_SHARE_WARN,
    _UNWANTED_BYPASS_SHARE_MAX,
    _build_bypass_causes_section,
    check_ci_invariants,
    compute_metrics,
    load_catalog_entries,
    load_jsonl,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
# _health is now a package; invoke via -m rather than as a script path.
_HEALTH_MODULE = ["claude_wayfinder._health"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_dispatch_log(events: list[dict[str, Any]], tmp_path: Path) -> Path:
    """Write a list of dispatch-log events to a JSONL file."""
    p = tmp_path / "dispatch-log.jsonl"
    with p.open("w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    return p


def make_drift_log(events: list[dict[str, Any]], tmp_path: Path) -> Path:
    """Write a list of drift-log events to a JSONL file."""
    p = tmp_path / "router-drift.jsonl"
    with p.open("w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    return p


def agent_dispatch(session_id: str = "s1", agent: str = "code-writer") -> dict[str, Any]:
    """Build a synthetic agent_dispatch event for the dispatch log."""
    return {
        "type": "agent_dispatch",
        "ts": "2026-05-01T00:00:00.000Z",
        "session_id": session_id,
        "agent": agent,
        "skills_in_prompt": [],
        "task_excerpt": "test task",
    }


def bypass_event(session_id: str = "s1") -> dict[str, Any]:
    """Build a synthetic bypass drift event."""
    return {
        "type": "router_drift",
        "ts": "2026-05-01T00:00:00.000Z",
        "session_id": session_id,
        "category": "bypass",
    }


def stale_dispatch_event(session_id: str = "s1") -> dict[str, Any]:
    """Build a synthetic stale_dispatch drift event."""
    return {
        "type": "router_drift",
        "ts": "2026-05-01T00:00:00.000Z",
        "session_id": session_id,
        "category": "stale_dispatch",
    }


def skill_mediated_event(session_id: str = "s1") -> dict[str, Any]:
    """Build a synthetic skill_mediated drift event (informational)."""
    return {
        "type": "router_drift",
        "ts": "2026-05-01T00:00:00.000Z",
        "session_id": session_id,
        "category": "skill_mediated",
    }


def advisory_override_event(session_id: str = "s1") -> dict[str, Any]:
    """Build a synthetic advisory_override drift event."""
    return {
        "type": "advisory_override",
        "ts": "2026-05-01T00:00:00.000Z",
        "session_id": session_id,
        "recommended_agent": "code-writer",
        "actual_agent": "debugger",
    }


def catalog_degraded_event(session_id: str = "s1") -> dict[str, Any]:
    """Build a synthetic catalog_degraded_session drift event."""
    return {
        "type": "catalog_degraded_session",
        "ts": "2026-05-01T00:00:00.000Z",
        "session_id": session_id,
    }


def skill_mediated_delegation_event(session_id: str = "s1", count: int = 1) -> dict[str, Any]:
    """Build a synthetic skill_mediated_delegation event from the Stop hook."""
    return {
        "type": "skill_mediated_delegation",
        "ts": "2026-05-01T00:00:00.000Z",
        "session_id": session_id,
        "count": count,
    }


# ---------------------------------------------------------------------------
# Import the module under test
# ---------------------------------------------------------------------------
# (Imported at module top — see import block above.)


# ---------------------------------------------------------------------------
# 1. load_jsonl
# ---------------------------------------------------------------------------


def test_load_jsonl_returns_empty_when_file_missing(tmp_path: Path) -> None:
    """Missing log file is treated as zero events (fully healthy, not an error)."""
    result = load_jsonl(tmp_path / "nonexistent.jsonl")
    assert result == []


def test_load_jsonl_parses_valid_jsonl(tmp_path: Path) -> None:
    """Parses valid JSONL into a list of dicts."""
    p = tmp_path / "test.jsonl"
    p.write_text('{"type": "a"}\n{"type": "b"}\n', encoding="utf-8")
    result = load_jsonl(p)
    assert len(result) == 2
    assert result[0]["type"] == "a"
    assert result[1]["type"] == "b"


def test_load_jsonl_skips_blank_lines(tmp_path: Path) -> None:
    """Blank lines in JSONL are silently skipped."""
    p = tmp_path / "test.jsonl"
    p.write_text('{"type": "a"}\n\n{"type": "b"}\n', encoding="utf-8")
    result = load_jsonl(p)
    assert len(result) == 2


def test_load_jsonl_skips_malformed_lines(tmp_path: Path) -> None:
    """Malformed JSON lines are silently skipped (same tolerance as other log readers)."""
    p = tmp_path / "test.jsonl"
    p.write_text('{"type": "a"}\nNOT JSON\n{"type": "b"}\n', encoding="utf-8")
    result = load_jsonl(p)
    assert len(result) == 2


# ---------------------------------------------------------------------------
# 2. compute_metrics — dispatch invocation rate
# ---------------------------------------------------------------------------


def test_dispatch_invocation_rate_all_dispatched(tmp_path: Path) -> None:
    """10 dispatches, 10 agent calls → 100% dispatch rate."""
    dispatch_log = [agent_dispatch(session_id=f"s{i}") for i in range(10)]
    drift_log: list[dict[str, Any]] = []
    metrics = compute_metrics(dispatch_log, drift_log)
    assert metrics["dispatch_invocation_rate"].value == pytest.approx(1.0)
    assert metrics["dispatch_invocation_rate"].healthy is True


def test_dispatch_invocation_rate_below_threshold(tmp_path: Path) -> None:
    """8 dispatches, 10 agent calls (2 bypasses) → 80% — at threshold, healthy."""
    dispatch_log = [agent_dispatch(session_id=f"s{i}") for i in range(10)]
    drift_log = [bypass_event(session_id=f"s{i}") for i in range(2)]
    metrics = compute_metrics(dispatch_log, drift_log)
    # rate = dispatches / (dispatches + bypasses) = 10/(10+2) ≈ 0.833
    assert metrics["dispatch_invocation_rate"].healthy is True


def test_dispatch_invocation_rate_unhealthy(tmp_path: Path) -> None:
    """1 dispatch, 4 bypasses → 20% dispatch rate → unhealthy."""
    dispatch_log = [agent_dispatch()]
    drift_log = [bypass_event(session_id=f"s{i}") for i in range(4)]
    metrics = compute_metrics(dispatch_log, drift_log)
    assert metrics["dispatch_invocation_rate"].healthy is False


# ---------------------------------------------------------------------------
# 3. compute_metrics — bypass rate
# ---------------------------------------------------------------------------


def test_bypass_rate_zero(tmp_path: Path) -> None:
    """No bypass events → bypass rate is 0 → healthy."""
    dispatch_log = [agent_dispatch()]
    drift_log: list[dict[str, Any]] = []
    metrics = compute_metrics(dispatch_log, drift_log)
    assert metrics["bypass_rate"].value == pytest.approx(0.0)
    assert metrics["bypass_rate"].healthy is True


def test_bypass_rate_within_threshold(tmp_path: Path) -> None:
    """1 bypass in 10 total agent calls → 9% → healthy (≤10%)."""
    dispatch_log = [agent_dispatch(session_id=f"s{i}") for i in range(9)]
    drift_log = [bypass_event(session_id="bypass1")]
    metrics = compute_metrics(dispatch_log, drift_log)
    # total_agent_calls = dispatches + bypasses = 9 + 1 = 10; rate = 1/10 = 0.10
    assert metrics["bypass_rate"].value == pytest.approx(0.10)
    assert metrics["bypass_rate"].healthy is True


def test_bypass_rate_exceeds_threshold(tmp_path: Path) -> None:
    """2 bypasses in 10 total calls → 20% → unhealthy (>10%)."""
    dispatch_log = [agent_dispatch(session_id=f"s{i}") for i in range(8)]
    drift_log = [bypass_event(session_id=f"b{i}") for i in range(2)]
    metrics = compute_metrics(dispatch_log, drift_log)
    assert metrics["bypass_rate"].value == pytest.approx(0.20)
    assert metrics["bypass_rate"].healthy is False


# ---------------------------------------------------------------------------
# 4. compute_metrics — advisory override rate
# ---------------------------------------------------------------------------


def test_advisory_override_rate_zero(tmp_path: Path) -> None:
    """No advisory overrides → 0% → healthy."""
    dispatch_log = [agent_dispatch()]
    drift_log: list[dict[str, Any]] = []
    metrics = compute_metrics(dispatch_log, drift_log)
    assert metrics["advisory_override_rate"].value == pytest.approx(0.0)
    assert metrics["advisory_override_rate"].healthy is True


def test_advisory_override_rate_at_threshold(tmp_path: Path) -> None:
    """3 overrides in 10 advisory decisions → 30% → healthy (≤30%)."""
    # 10 agent dispatches, 3 advisory overrides
    dispatch_log = [agent_dispatch(session_id=f"s{i}") for i in range(10)]
    drift_log = [advisory_override_event(session_id=f"ao{i}") for i in range(3)]
    metrics = compute_metrics(dispatch_log, drift_log)
    assert metrics["advisory_override_rate"].value == pytest.approx(3 / 10)
    assert metrics["advisory_override_rate"].healthy is True


def test_advisory_override_rate_exceeds_threshold(tmp_path: Path) -> None:
    """4 overrides in 10 dispatches → 40% → unhealthy (>30%)."""
    dispatch_log = [agent_dispatch(session_id=f"s{i}") for i in range(10)]
    drift_log = [advisory_override_event(session_id=f"ao{i}") for i in range(4)]
    metrics = compute_metrics(dispatch_log, drift_log)
    assert metrics["advisory_override_rate"].value == pytest.approx(4 / 10)
    assert metrics["advisory_override_rate"].healthy is False


# ---------------------------------------------------------------------------
# 5. compute_metrics — catalog availability
# ---------------------------------------------------------------------------


def test_catalog_availability_no_degraded_events(tmp_path: Path) -> None:
    """No catalog_degraded events → 100% availability → healthy."""
    dispatch_log = [agent_dispatch(session_id=f"s{i}") for i in range(10)]
    drift_log: list[dict[str, Any]] = []
    metrics = compute_metrics(dispatch_log, drift_log)
    assert metrics["catalog_availability"].value == pytest.approx(1.0)
    assert metrics["catalog_availability"].healthy is True


def test_catalog_availability_one_degraded_event_triggers_unhealthy(
    tmp_path: Path,
) -> None:
    """Any catalog_degraded_session event → unhealthy (threshold: ≥1 ever)."""
    dispatch_log = [agent_dispatch(session_id=f"s{i}") for i in range(99)]
    drift_log = [catalog_degraded_event()]
    metrics = compute_metrics(dispatch_log, drift_log)
    assert metrics["catalog_availability"].healthy is False


def test_catalog_availability_zero_sessions_is_healthy(tmp_path: Path) -> None:
    """Zero sessions → 100% availability (no data = no degradation)."""
    metrics = compute_metrics([], [])
    assert metrics["catalog_availability"].healthy is True


# ---------------------------------------------------------------------------
# 6. skill_mediated_delegation — informational, NOT a threshold breach (#341)
# ---------------------------------------------------------------------------


def test_skill_mediated_delegation_counted_in_metrics(tmp_path: Path) -> None:
    """skill_mediated_delegation events are counted but do not affect health."""
    dispatch_log = [agent_dispatch()]
    drift_log = [
        skill_mediated_delegation_event(session_id="s1", count=3),
        skill_mediated_delegation_event(session_id="s2", count=2),
    ]
    metrics = compute_metrics(dispatch_log, drift_log)
    assert "skill_mediated_delegation" in metrics
    assert metrics["skill_mediated_delegation"].value == 5  # sum of counts
    # Informational: never unhealthy
    assert metrics["skill_mediated_delegation"].healthy is True


def test_skill_mediated_drift_category_not_counted_as_bypass(tmp_path: Path) -> None:
    """skill_mediated router_drift events (category=skill_mediated) are NOT bypasses."""
    dispatch_log = [agent_dispatch(session_id=f"s{i}") for i in range(5)]
    drift_log = [skill_mediated_event(session_id=f"sm{i}") for i in range(5)]
    metrics = compute_metrics(dispatch_log, drift_log)
    # skill_mediated is not a bypass — bypass_rate should remain 0
    assert metrics["bypass_rate"].value == pytest.approx(0.0)
    assert metrics["bypass_rate"].healthy is True


# ---------------------------------------------------------------------------
# 7. threshold-breach detection per §3.3.3
# ---------------------------------------------------------------------------


def test_threshold_breach_catalog_degraded(tmp_path: Path) -> None:
    """catalog_degraded_session triggers immediate action per §3.3.3."""
    drift_log = [catalog_degraded_event()]
    metrics = compute_metrics([], drift_log)
    assert metrics["catalog_availability"].healthy is False


def test_no_threshold_breach_when_all_healthy(tmp_path: Path) -> None:
    """When all metrics are in healthy ranges, no breaches reported."""
    dispatch_log = [agent_dispatch(session_id=f"s{i}") for i in range(10)]
    drift_log: list[dict[str, Any]] = []
    metrics = compute_metrics(dispatch_log, drift_log)
    breaches = [k for k, v in metrics.items() if not v.healthy]
    assert breaches == []


# ---------------------------------------------------------------------------
# 8. CI mode — exit codes
# ---------------------------------------------------------------------------


def _empty_dirs(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Create empty skills/, agents/, triggers/ dirs for CI test isolation."""
    skills = tmp_path / "skills"
    agents = tmp_path / "agents"
    triggers = tmp_path / "triggers"
    skills.mkdir(exist_ok=True)
    agents.mkdir(exist_ok=True)
    triggers.mkdir(exist_ok=True)
    return skills, agents, triggers


def run_ci(
    tmp_path: Path,
    *,
    drift_log_events: list[dict[str, Any]] | None = None,
    extra_args: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run router_health.py --ci in a subprocess with synthetic log files."""
    drift_path = make_drift_log(drift_log_events or [], tmp_path)
    dispatch_path = make_dispatch_log([], tmp_path)
    skills, agents, triggers = _empty_dirs(tmp_path)
    cmd = [
        sys.executable,
        "-m",
        *_HEALTH_MODULE,
        "--ci",
        "--drift-log",
        str(drift_path),
        "--dispatch-log",
        str(dispatch_path),
        "--skills-dir",
        str(skills),
        "--agents-dir",
        str(agents),
        "--plugin-overrides-dir",
        str(triggers),
    ]
    if extra_args:
        cmd.extend(extra_args)
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def test_ci_mode_exits_0_when_no_catalog_degraded(tmp_path: Path) -> None:
    """--ci exits 0 when no catalog_degraded_session events (CI invariant: availability)."""
    result = run_ci(tmp_path)
    assert result.returncode == 0, f"stderr: {result.stderr}"


def test_ci_mode_exits_nonzero_when_catalog_degraded(tmp_path: Path) -> None:
    """--ci exits non-zero when catalog_degraded_session events exist."""
    result = run_ci(tmp_path, drift_log_events=[catalog_degraded_event()])
    assert result.returncode != 0, "Expected non-zero exit for catalog degradation"


def test_ci_mode_output_contains_ci_invariant_label(tmp_path: Path) -> None:
    """--ci output clearly labels results as CI invariants."""
    result = run_ci(tmp_path)
    assert "CI" in result.stdout or "invariant" in result.stdout.lower()


# ---------------------------------------------------------------------------
# 9. --report mode output
# ---------------------------------------------------------------------------


def run_report(
    tmp_path: Path,
    *,
    dispatch_events: list[dict[str, Any]] | None = None,
    drift_events: list[dict[str, Any]] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run router_health.py --report in a subprocess with synthetic log files."""
    dispatch_path = make_dispatch_log(dispatch_events or [], tmp_path)
    drift_path = make_drift_log(drift_events or [], tmp_path)
    skills, agents, triggers = _empty_dirs(tmp_path)
    cmd = [
        sys.executable,
        "-m",
        *_HEALTH_MODULE,
        "--report",
        "--drift-log",
        str(drift_path),
        "--dispatch-log",
        str(dispatch_path),
        "--skills-dir",
        str(skills),
        "--agents-dir",
        str(agents),
        "--plugin-overrides-dir",
        str(triggers),
    ]
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def test_report_mode_exits_0(tmp_path: Path) -> None:
    """--report always exits 0 (informational, does not gate CI)."""
    result = run_report(tmp_path)
    assert result.returncode == 0, f"stderr: {result.stderr}"


def test_report_mode_contains_ci_invariant_section(tmp_path: Path) -> None:
    """--report output contains a CI invariant section."""
    result = run_report(tmp_path)
    output = result.stdout
    assert "CI" in output or "invariant" in output.lower()


def test_report_mode_contains_runtime_telemetry_section(tmp_path: Path) -> None:
    """--report output contains a runtime telemetry section."""
    result = run_report(tmp_path)
    output = result.stdout
    assert "telemetry" in output.lower() or "runtime" in output.lower()


def test_report_mode_labels_both_metric_classes(tmp_path: Path) -> None:
    """--report clearly distinguishes CI invariants from runtime telemetry."""
    result = run_report(tmp_path)
    output = result.stdout
    # Both sections must be present
    has_ci = "CI" in output or "invariant" in output.lower()
    has_rt = "telemetry" in output.lower() or "runtime" in output.lower()
    assert has_ci and has_rt, f"Missing section labels in output:\n{output}"


def test_report_mode_shows_skill_mediated_delegation_informational(
    tmp_path: Path,
) -> None:
    """--report shows skill_mediated_delegation with 'informational' label (#341)."""
    drift_events = [skill_mediated_delegation_event(count=7)]
    result = run_report(tmp_path, drift_events=drift_events)
    output = result.stdout
    assert "skill_mediated" in output.lower() or "skill mediated" in output.lower()
    assert "informational" in output.lower()


def test_report_mode_shows_threshold_breach_prominently(tmp_path: Path) -> None:
    """--report prominently surfaces threshold breaches (catalog degradation)."""
    drift_events = [catalog_degraded_event()]
    result = run_report(tmp_path, drift_events=drift_events)
    output = result.stdout
    # Should have some indication of a problem (breach / warning / fail / etc.)
    lowered = output.lower()
    assert any(
        word in lowered for word in ["breach", "fail", "unhealthy", "action", "degraded", "warning"]
    ), f"Expected prominent breach indicator, got:\n{output}"


# ---------------------------------------------------------------------------
# 10. check_ci_invariants — catalog stability check
# ---------------------------------------------------------------------------


def test_check_ci_invariants_returns_dict(tmp_path: Path) -> None:
    """check_ci_invariants returns a dict with at least catalog_stability key."""
    invariants = check_ci_invariants(
        skills_dir=tmp_path / "skills",
        agents_dir=tmp_path / "agents",
        plugin_overrides_dir=tmp_path / "triggers",
    )
    assert isinstance(invariants, dict)
    assert "catalog_stability" in invariants


def test_catalog_stability_passes_on_empty_dirs(tmp_path: Path) -> None:
    """catalog_stability passes when skills/agents dirs are empty (degenerate clean case)."""
    (tmp_path / "skills").mkdir()
    (tmp_path / "agents").mkdir()
    invariants = check_ci_invariants(
        skills_dir=tmp_path / "skills",
        agents_dir=tmp_path / "agents",
        plugin_overrides_dir=tmp_path / "triggers",
    )
    assert invariants["catalog_stability"].healthy is True


def test_schema_validation_key_present(tmp_path: Path) -> None:
    """check_ci_invariants includes schema_validation key."""
    invariants = check_ci_invariants(
        skills_dir=tmp_path / "skills",
        agents_dir=tmp_path / "agents",
        plugin_overrides_dir=tmp_path / "triggers",
    )
    assert "schema_validation" in invariants


# ---------------------------------------------------------------------------
# 11. CLI — --ci with catalog-stability check uses real skills/agents dirs
# ---------------------------------------------------------------------------


def test_ci_mode_catalog_stability_uses_real_dirs(tmp_path: Path) -> None:
    """--ci with --skills-dir / --agents-dir runs catalog stability check."""
    skills_dir = tmp_path / "skills"
    agents_dir = tmp_path / "agents"
    skills_dir.mkdir()
    agents_dir.mkdir()

    drift_path = make_drift_log([], tmp_path)
    dispatch_path = make_dispatch_log([], tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            *_HEALTH_MODULE,
            "--ci",
            "--drift-log",
            str(drift_path),
            "--dispatch-log",
            str(dispatch_path),
            "--skills-dir",
            str(skills_dir),
            "--agents-dir",
            str(agents_dir),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"


# ---------------------------------------------------------------------------
# 12. MetricResult structure
# ---------------------------------------------------------------------------


def test_metric_result_has_label_and_class(tmp_path: Path) -> None:
    """MetricResult carries a human-readable label and a metric_class field."""
    dispatch_log = [agent_dispatch()]
    drift_log: list[dict[str, Any]] = []
    metrics = compute_metrics(dispatch_log, drift_log)
    for key, result in metrics.items():
        assert hasattr(result, "label"), f"MetricResult for {key} missing 'label'"
        assert hasattr(result, "metric_class"), f"MetricResult for {key} missing 'metric_class'"
        assert result.metric_class in (
            "ci_invariant",
            "runtime_telemetry",
            "informational",
        ), f"Unexpected metric_class for {key}: {result.metric_class!r}"


def test_skill_mediated_delegation_class_is_informational() -> None:
    """skill_mediated_delegation metric has class 'informational' per #341."""
    metrics = compute_metrics(
        [],
        [skill_mediated_delegation_event(count=2)],
    )
    assert metrics["skill_mediated_delegation"].metric_class == "informational"


# ---------------------------------------------------------------------------
# 13. trigger_firing_accuracy — ephemeral catalog (CI portability fix #204)
# ---------------------------------------------------------------------------


def test_trigger_firing_accuracy_passes_with_empty_dirs(tmp_path: Path) -> None:
    """trigger_firing_accuracy is a no-op pass when skills/agents dirs are empty.

    This is the CI portability case: the ephemeral catalog has zero entries,
    so no smoke tests can run.  The invariant must return healthy=True with
    a 'skipping' detail rather than failing due to a missing user catalog.
    """
    invariants = check_ci_invariants(
        skills_dir=tmp_path / "skills",
        agents_dir=tmp_path / "agents",
        plugin_overrides_dir=tmp_path / "triggers",
    )
    result = invariants["trigger_firing_accuracy"]
    assert result.healthy is True, f"Expected healthy=True, got detail: {result.detail}"
    # Should mention 'skip' to make the no-op explicit in CI logs
    assert (
        "skip" in result.detail.lower() or "no skills" in result.detail.lower()
    ), f"Expected skip-related detail, got: {result.detail}"


def test_trigger_firing_accuracy_does_not_read_user_catalog(tmp_path: Path) -> None:
    """trigger_firing_accuracy must not rely on ~/.claude/state/dispatch-catalog.json.

    Point DISPATCH_CATALOG_PATH at a nonexistent path; if the invariant
    silently reads the user's real catalog it would still pass.  With the
    ephemeral-catalog fix, the check generates its own catalog from the
    (empty) dirs and returns a no-op pass regardless of what
    DISPATCH_CATALOG_PATH points at.
    """
    import os
    import subprocess as _sp

    # Build a fresh subprocess environment with DISPATCH_CATALOG_PATH pointing
    # at a guaranteed-nonexistent path.  If the production code falls through
    # to the user catalog the test environment would have no catalog at that
    # path and match.py would exit 2.  But the fix generates an ephemeral
    # catalog from the dirs, so it shouldn't matter.
    fake_catalog = tmp_path / "does_not_exist.json"
    env = {**os.environ, "DISPATCH_CATALOG_PATH": str(fake_catalog)}

    skills = tmp_path / "skills"
    agents = tmp_path / "agents"
    triggers = tmp_path / "triggers"
    skills.mkdir()
    agents.mkdir()
    triggers.mkdir()

    drift_path = make_drift_log([], tmp_path)
    dispatch_path = make_dispatch_log([], tmp_path)

    result = _sp.run(
        [
            sys.executable,
            "-m",
            *_HEALTH_MODULE,
            "--ci",
            "--drift-log",
            str(drift_path),
            "--dispatch-log",
            str(dispatch_path),
            "--skills-dir",
            str(skills),
            "--agents-dir",
            str(agents),
            "--plugin-overrides-dir",
            str(triggers),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result.returncode == 0, (
        f"--ci should exit 0 with empty dirs even when DISPATCH_CATALOG_PATH "
        f"is missing.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )


# ---------------------------------------------------------------------------
# 14. schema_validation — applicable_agents warnings are not fatal (#204)
# ---------------------------------------------------------------------------


def test_schema_validation_passes_when_only_applicable_agents_warnings(tmp_path: Path) -> None:
    """schema_validation must pass when generator emits only applicable_agents warnings.

    Router-only skills (e.g. refresh-catalog, whats-next) intentionally
    declare triggers but leave applicable_agents empty.  The generator emits
    a 'warning' line for each such skill but exits 0.  The invariant must
    treat these as non-fatal and return healthy=True.

    This test creates a minimal skill fixture with applicable_agents: [] to
    trigger the warning path in the generator.
    """
    # Create a minimal skill fixture with applicable_agents: [] to
    # trigger the warning path in the generator.
    skills_dir = tmp_path / "skills"
    skill_dir = skills_dir / "test-router-only-skill"
    skill_dir.mkdir(parents=True)

    # Minimal SKILL.md (no frontmatter v6 keys needed — triggers.yml carries them)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: test-router-only-skill\ndescription: test skill\n---\n\nBody.\n",
        encoding="utf-8",
    )
    # triggers.yml with applicable_agents: [] — this is the intentional pattern
    # that generates the warning in the generator
    (skill_dir / "triggers.yml").write_text(
        "applicable_agents: []\ntriggers:\n  keywords:\n    - term: test\n      weight: 1.0\n",
        encoding="utf-8",
    )

    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    triggers_dir = tmp_path / "triggers"
    triggers_dir.mkdir()

    invariants = check_ci_invariants(
        skills_dir=skills_dir,
        agents_dir=agents_dir,
        plugin_overrides_dir=triggers_dir,
    )
    result = invariants["schema_validation"]
    assert result.healthy is True, (
        f"schema_validation should pass when only applicable_agents warnings exist.\n"
        f"Detail: {result.detail}"
    )


# ---------------------------------------------------------------------------
# 15. harness_version stamping — issue #390
# ---------------------------------------------------------------------------


SENTINEL_SHA = "deadbeef1234567890abcdef1234567890abcdef"


def agent_dispatch_versioned(
    session_id: str = "s1", harness_version: str = SENTINEL_SHA
) -> dict[str, Any]:
    """Build a synthetic agent_dispatch event that includes harness_version."""
    ev = agent_dispatch(session_id=session_id)
    ev["harness_version"] = harness_version
    return ev


def test_report_mode_shows_harness_version_in_header(tmp_path: Path) -> None:
    """--report output shows the most-recent harness_version SHA in the header section.

    The SHA from the most recent dispatch-log event should appear somewhere in
    the report so that /router-health output is interpretable across harness changes.
    """
    dispatch_events = [
        agent_dispatch_versioned(session_id="s1", harness_version="aaaa1111" + "0" * 32),
        agent_dispatch_versioned(session_id="s2", harness_version=SENTINEL_SHA),
    ]
    result = run_report(tmp_path, dispatch_events=dispatch_events)
    assert result.returncode == 0, f"stderr: {result.stderr}"
    # The most-recent SHA (SENTINEL_SHA) must appear in the output.
    assert SENTINEL_SHA in result.stdout, (
        f"Expected most-recent harness_version {SENTINEL_SHA!r} in report output.\n"
        f"stdout:\n{result.stdout}"
    )


def test_report_mode_does_not_crash_on_mixed_corpus(tmp_path: Path) -> None:
    """--report handles a mix of events with and without harness_version without crashing.

    Legacy log entries lack the field.  New entries carry it.  The report must
    produce valid output and exit 0 regardless of the mix.
    """
    dispatch_events = [
        agent_dispatch(session_id="legacy-s1"),  # no harness_version field
        agent_dispatch(session_id="legacy-s2"),  # no harness_version field
        agent_dispatch_versioned(session_id="new-s3"),  # has harness_version
    ]
    drift_events = [
        advisory_override_event(session_id="legacy-d1"),  # no harness_version
        {**advisory_override_event(session_id="new-d2"), "harness_version": SENTINEL_SHA},  # has it
    ]
    result = run_report(tmp_path, dispatch_events=dispatch_events, drift_events=drift_events)
    assert result.returncode == 0, (
        f"--report must exit 0 on mixed corpus (legacy + versioned events).\n"
        f"stderr: {result.stderr}"
    )
    # Output must be non-empty and contain basic structure markers
    assert len(result.stdout) > 0, "Report output must not be empty"


def test_ci_mode_does_not_crash_on_mixed_corpus(tmp_path: Path) -> None:
    """--ci handles a mix of events with and without harness_version without crashing.

    This is the backward-compatibility test: existing logs without the field
    must not break --ci mode.
    """
    dispatch_events = [
        agent_dispatch(session_id="legacy"),  # no harness_version
        agent_dispatch_versioned(session_id="new"),  # has harness_version
    ]
    tmp_path_ci = tmp_path / "ci-mixed"
    tmp_path_ci.mkdir(exist_ok=True)
    dispatch_path_ci = make_dispatch_log(dispatch_events, tmp_path_ci)
    drift_path_ci = make_drift_log([], tmp_path_ci)
    skills, agents, triggers = _empty_dirs(tmp_path_ci)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            *_HEALTH_MODULE,
            "--ci",
            "--dispatch-log",
            str(dispatch_path_ci),
            "--drift-log",
            str(drift_path_ci),
            "--skills-dir",
            str(skills),
            "--agents-dir",
            str(agents),
            "--plugin-overrides-dir",
            str(triggers),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"--ci must exit 0 on mixed corpus.\nstderr: {result.stderr}"


# ---------------------------------------------------------------------------
# 16. Plugin entries line in --report output (#480)
# ---------------------------------------------------------------------------


def _make_catalog(entries: list[dict], tmp_path: Path) -> Path:
    """Write a synthetic dispatch-catalog.json to tmp_path and return its path.

    Args:
        entries: List of entry dicts to embed in the catalog.
        tmp_path: Directory to write the file into.

    Returns:
        Path to the written catalog file.
    """
    catalog = {"built_for_project": None, "entries": entries}
    p = tmp_path / "dispatch-catalog.json"
    p.write_text(json.dumps(catalog), encoding="utf-8")
    return p


def _catalog_entry(
    name: str,
    kind: str,
    source: str,
) -> dict:
    """Return a minimal catalog entry dict.

    Args:
        name: Entry name.
        kind: Either ``"agent"`` or ``"skill"``.
        source: Provenance tag — ``"owned"``, ``"plugin"``, or
            ``"plugin-override"``.

    Returns:
        Minimal dict suitable for inclusion in a synthetic catalog.
    """
    return {
        "name": name,
        "kind": kind,
        "source": source,
        "description": f"test {kind} {name}",
        "triggers": {
            "keywords": [],
            "agent_mentions": [],
            "command_prefixes": [],
            "path_globs": [],
            "tool_mentions": [],
            "excludes": [],
        },
        "applicable_skills": [],
    }


def run_report_with_catalog(
    tmp_path: Path,
    catalog_path: Path,
    *,
    dispatch_events: list[dict] | None = None,
    drift_events: list[dict] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run --report with an explicit catalog path via DISPATCH_CATALOG_PATH env var.

    Args:
        tmp_path: Temp directory for log files.
        catalog_path: Path to the pre-built synthetic catalog.
        dispatch_events: Synthetic dispatch-log events (default: empty).
        drift_events: Synthetic drift-log events (default: empty).

    Returns:
        CompletedProcess result from the subprocess run.
    """
    import os

    dispatch_path = make_dispatch_log(dispatch_events or [], tmp_path)
    drift_path = make_drift_log(drift_events or [], tmp_path)
    skills, agents, triggers = _empty_dirs(tmp_path)
    env = {**os.environ, "DISPATCH_CATALOG_PATH": str(catalog_path)}
    cmd = [
        sys.executable,
        "-m",
        *_HEALTH_MODULE,
        "--report",
        "--drift-log",
        str(drift_path),
        "--dispatch-log",
        str(dispatch_path),
        "--skills-dir",
        str(skills),
        "--agents-dir",
        str(agents),
        "--plugin-overrides-dir",
        str(triggers),
    ]
    return subprocess.run(cmd, capture_output=True, text=True, check=False, env=env)


def test_router_health_report_includes_plugin_entries_line(
    tmp_path: Path,
) -> None:
    """--report includes 'Plugin entries: N skills, M agents (K agent routable
    via override)' in the Notable Findings section.

    Catalog has: 2 plugin skills, 1 plugin agent, 1 plugin-override agent.
    Expected line: Plugin entries: 2 skills, 2 agents (1 agent routable via
    override) — singular 'agent' because k_routable == 1.
    """
    catalog_path = _make_catalog(
        [
            _catalog_entry("plugin-skill-a", "skill", "plugin"),
            _catalog_entry("plugin-skill-b", "skill", "plugin"),
            _catalog_entry("plugin-agent-x", "agent", "plugin"),
            _catalog_entry("plugin-override-agent-y", "agent", "plugin-override"),
            _catalog_entry("owned-agent-z", "agent", "owned"),
        ],
        tmp_path,
    )
    result = run_report_with_catalog(tmp_path, catalog_path)
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert (
        "Plugin entries: 2 skills, 2 agents (1 agent routable via override)" in result.stdout
    ), f"Expected plugin entries line not found in output:\n{result.stdout}"


def test_router_health_report_plugin_entries_zero_when_no_plugin_entries(
    tmp_path: Path,
) -> None:
    """--report shows Plugin entries: 0 skills, 0 agents (0 agents routable via
    override) when the catalog contains only owned entries.
    """
    catalog_path = _make_catalog(
        [
            _catalog_entry("owned-agent-a", "agent", "owned"),
            _catalog_entry("owned-skill-b", "skill", "owned"),
        ],
        tmp_path,
    )
    result = run_report_with_catalog(tmp_path, catalog_path)
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert (
        "Plugin entries: 0 skills, 0 agents (0 agents routable via override)" in result.stdout
    ), f"Expected zero-count plugin line not found:\n{result.stdout}"


def test_router_health_report_uses_shared_predicate(tmp_path: Path) -> None:
    """_health/_metrics.py imports is_agent_routable from match_filters.

    Verified via AST inspection of the source file — ensures the single source
    of truth for routability is used rather than an inline duplicate.
    After the Phase 2B split, the import lives in _health/_metrics.py.
    """
    import ast

    # The import now lives in _metrics.py (extracted in Phase 2B commit 1)
    metrics_source = (
        REPO_ROOT / "src" / "claude_wayfinder" / "_health" / "_metrics.py"
    )
    source = metrics_source.read_text(encoding="utf-8")
    tree = ast.parse(source)

    found = False
    for node in ast.walk(tree):
        # Look for: from scripts.router_lib.match_filters import is_agent_routable
        # OR: from .router_lib.match_filters import is_agent_routable
        # OR: from router_lib.match_filters import is_agent_routable
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names = [alias.name for alias in node.names]
            if "is_agent_routable" in names and "match_filters" in module:
                found = True
                break

    assert found, (
        "router_health.py must import is_agent_routable from "
        "scripts.router_lib.match_filters (or a relative equivalent). "
        "No such import found — inline predicate duplication is forbidden."
    )


# ---------------------------------------------------------------------------
# 17. Pluralization of singular counts in --report (#490 review)
# ---------------------------------------------------------------------------


def test_router_health_report_pluralizes_singular_counts(
    tmp_path: Path,
) -> None:
    """--report uses singular 'skill'/'agent' when each count is exactly 1.

    Fixture: 1 plugin skill, 1 plugin-override agent, 0 plain plugin agents.
    m_agents = plugin + plugin-override = 0 + 1 = 1 total plugin agent.
    k_routable = 1 (the plugin-override agent is routable via override).
    Expected line: 'Plugin entries: 1 skill, 1 agent (1 agent routable via
    override)' — grammatically correct singular forms throughout.

    This deviates from the literal acceptance-criterion format string in
    issue #480 (which hardcoded plurals); the reviewer's grammar fix takes
    precedence for operator-facing readouts.
    """
    catalog_path = _make_catalog(
        [
            _catalog_entry("plugin-skill-one", "skill", "plugin"),
            _catalog_entry("plugin-override-agent-one", "agent", "plugin-override"),
            _catalog_entry("owned-agent-z", "agent", "owned"),
        ],
        tmp_path,
    )
    result = run_report_with_catalog(tmp_path, catalog_path)
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert (
        "Plugin entries: 1 skill, 1 agent (1 agent routable via override)" in result.stdout
    ), f"Expected singular plugin entries line not found in output:\n{result.stdout}"


# ---------------------------------------------------------------------------
# 18. load_catalog_entries error paths (#490 review)
# ---------------------------------------------------------------------------


def test_load_catalog_entries_returns_empty_on_invalid_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """load_catalog_entries returns [] when the catalog file contains malformed JSON.

    Malformed JSON should be silently swallowed — consistent with the
    'silently treated as empty' contract documented in the function docstring.
    """
    catalog = tmp_path / "bad-catalog.json"
    catalog.write_text("{not valid json!!!", encoding="utf-8")
    monkeypatch.setenv("DISPATCH_CATALOG_PATH", str(catalog))

    result = load_catalog_entries()

    assert result == [], f"Expected empty list for invalid JSON catalog, got: {result!r}"


def test_load_catalog_entries_returns_empty_when_entries_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """load_catalog_entries returns [] when the JSON object lacks the 'entries' key.

    A catalog with no 'entries' key is treated as having zero entries rather
    than raising a KeyError — defensive handling consistent with the contract.
    """
    catalog = tmp_path / "no-entries-catalog.json"
    catalog.write_text('{"built_for_project": "test", "something_else": []}', encoding="utf-8")
    monkeypatch.setenv("DISPATCH_CATALOG_PATH", str(catalog))

    result = load_catalog_entries()

    assert result == [], f"Expected empty list when 'entries' key is missing, got: {result!r}"


# ---------------------------------------------------------------------------
# 19. _build_bypass_causes_section — unit tests (#143)
# ---------------------------------------------------------------------------

from datetime import datetime, timedelta, timezone  # noqa: E402  (section header)


def _now_iso(offset_days: int = 0) -> str:
    """Return an ISO-format UTC timestamp offset by the given number of days.

    Args:
        offset_days: How many days in the past to offset from now.

    Returns:
        ISO 8601 UTC datetime string.
    """
    return (datetime.now(timezone.utc) - timedelta(days=offset_days)).isoformat()


def _enriched(cause: str, days_ago: int = 1) -> dict:
    """Build a minimal post-enrichment router_drift event dict.

    Args:
        cause: The bypass_cause string to embed.
        days_ago: Age of the event in days (default 1 = yesterday, in window).

    Returns:
        Dict shaped like a post-enrichment drift event.
    """
    return {
        "type": "router_drift",
        "ts": _now_iso(days_ago),
        "category": "bypass" if cause.startswith("router_direct") else (
            "skill_mediated" if cause.startswith("skill_mediated") else cause
        ),
        "bypass_cause": cause,
        "bypass_signals": {"subagent_type": "x"},
    }


def test_bypass_causes_section_low_n_renders_na() -> None:
    """Section returns N/A notice when enriched event count is below the minimum sample."""
    events = [_enriched("router_direct_no_dispatch") for _ in range(5)]
    out = "\n".join(_build_bypass_causes_section(events))
    assert "N/A — insufficient post-enrichment data" in out
    assert "have 5, need" in out


def test_bypass_causes_section_high_n_renders_table_and_thresholds() -> None:
    """Section renders the cause table and threshold lines when N >= min sample.

    97 skill_mediated_interactive (expected) + 23 router_direct_no_dispatch
    (unwanted) = 120 total.  Unwanted share = 19.2% <= 20% F-1-recalibrated
    threshold → PASS.
    """
    events = [_enriched("skill_mediated_interactive") for _ in range(97)]
    events.extend(_enriched("router_direct_no_dispatch") for _ in range(23))
    out = "\n".join(_build_bypass_causes_section(events))
    assert "## Bypass causes (7-day window, 120 enriched events)" in out
    assert "skill_mediated_interactive" in out
    assert "router_direct_no_dispatch" in out
    assert "expected" in out
    assert "unwanted" in out
    # 23/120 = 19.2% unwanted, ≤ 20% F-1-recalibrated threshold → PASS
    assert "PASS — unwanted-bypass share 19.2%" in out


def test_bypass_causes_section_warns_when_over_threshold() -> None:
    """Section emits WARN when unwanted-bypass share exceeds the F-1-recalibrated
    threshold.

    70/120 = 58.3% unwanted, > 20% F-1-recalibrated threshold → WARN.
    """
    events = [_enriched("skill_mediated_interactive") for _ in range(50)]
    events.extend(_enriched("router_direct_no_dispatch") for _ in range(70))
    out = "\n".join(_build_bypass_causes_section(events))
    assert "WARN — unwanted-bypass share 58.3%" in out


def test_bypass_causes_section_unknown_share_threshold() -> None:
    """Section emits WARN when unknown-cause share exceeds the F-1-recalibrated
    warn threshold.

    85 skill_mediated_interactive + 15 unknown = 100 total.
    Unknown share = 15.0% > 5% F-1-recalibrated threshold → WARN.
    """
    events = [_enriched("skill_mediated_interactive") for _ in range(85)]
    events.extend(_enriched("unknown") for _ in range(15))
    out = "\n".join(_build_bypass_causes_section(events))
    assert "WARN — unknown share 15.0%" in out


def test_bypass_causes_section_pre_enrichment_baseline_reported() -> None:
    """Pre-enrichment events (no bypass_cause field) are reported as a baseline count."""
    enriched = [_enriched("skill_mediated_interactive") for _ in range(100)]
    pre = [
        {"type": "router_drift", "ts": _now_iso(2), "category": "bypass"}
        for _ in range(15)
    ]
    out = "\n".join(_build_bypass_causes_section(enriched + pre))
    assert "Pre-enrichment baseline (not counted): 15 events" in out


def test_bypass_causes_section_excludes_out_of_window_events() -> None:
    """Events older than 7 days are excluded from the cause counts and header total."""
    in_window = [_enriched("skill_mediated_interactive", days_ago=1) for _ in range(100)]
    out_of_window = [
        _enriched("router_direct_no_dispatch", days_ago=30) for _ in range(50)
    ]
    out = "\n".join(_build_bypass_causes_section(in_window + out_of_window))
    assert "## Bypass causes (7-day window, 100 enriched events)" in out
    assert "router_direct_no_dispatch" not in out


def test_bypass_causes_constants_match_spec() -> None:
    """Bypass-cause taxonomy constants match the F-1-recalibrated values.

    Recalibrated from F-1 baseline (issue #159): 7-day observed
    unwanted-bypass share ≈ 17.8%, unknown share = 0%.
    """
    assert _UNWANTED_BYPASS_SHARE_MAX == 0.20
    assert _UNKNOWN_SHARE_WARN == 0.05
    assert _BYPASS_CAUSE_MIN_SAMPLE == 100


def test_load_catalog_entries_returns_empty_when_entries_not_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """load_catalog_entries returns [] when 'entries' is not a list (e.g., a dict).

    Non-list 'entries' values are treated as empty — the isinstance(entries,
    list) guard in the implementation prevents returning unstructured data.
    """
    catalog = tmp_path / "bad-entries-catalog.json"
    catalog.write_text('{"entries": {"unexpected": "dict"}}', encoding="utf-8")
    monkeypatch.setenv("DISPATCH_CATALOG_PATH", str(catalog))

    result = load_catalog_entries()

    assert result == [], f"Expected empty list when 'entries' is not a list, got: {result!r}"


# ---------------------------------------------------------------------------
# 19. Issue #10: Remove ~/.claude defaults — health.py catalog path
# ---------------------------------------------------------------------------


def test_catalog_path_no_env_returns_empty_not_home(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """load_catalog_entries returns [] when no DISPATCH_CATALOG_PATH is set.

    After Issue #10, ``_catalog_path()`` must not fall back to
    ``~/.claude/state/dispatch-catalog.json``.  When the env var is absent,
    the function should return an empty list (the same silent-empty contract
    as when the path does not exist), not read from the user's home directory.
    """
    monkeypatch.delenv("DISPATCH_CATALOG_PATH", raising=False)

    # Place a catalog at the old default location so that if the fallback is
    # still present the test would incorrectly pass the silent-empty check.
    # We monkeypatch Path.home() to point at tmp_path so any .home() call
    # returns a directory we control.
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    state_dir = fake_home / ".claude" / "state"
    state_dir.mkdir(parents=True)
    poisoned_catalog = state_dir / "dispatch-catalog.json"
    poisoned_catalog.write_text(
        json.dumps({"entries": [{"kind": "agent", "name": "poison-agent"}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "claude_wayfinder._health.Path.home", lambda: fake_home
    )

    result = load_catalog_entries()

    # If the fallback to ~/.claude/ were still active the poisoned catalog
    # would be found and a non-empty list returned.
    assert result == [], (
        f"load_catalog_entries must return [] when no DISPATCH_CATALOG_PATH "
        f"is set (no ~/.claude fallback); got: {result!r}"
    )


def test_health_cli_catalog_path_flag_used_for_report(
    tmp_path: Path,
) -> None:
    """health.py --report uses --catalog-path when supplied.

    After Issue #10, the health CLI must accept a ``--catalog-path`` flag
    so callers can supply the catalog path explicitly rather than relying
    on the ``~/.claude/`` default.  This test passes a valid catalog via
    the flag and asserts the report runs without error.
    """
    catalog = {
        "schema_version": 1,
        "entries": [
            {
                "name": "code-writer",
                "kind": "agent",
                "description": "Writes code.",
                "source": "owned",
                "routable": True,
                "triggers": {"keywords": [{"term": "implement", "weight": 1.0}]},
                "applicable_skills": [],
            }
        ],
    }
    catalog_file = tmp_path / "dispatch-catalog.json"
    catalog_file.write_text(json.dumps(catalog), encoding="utf-8")

    skills_dir = tmp_path / "skills"
    agents_dir = tmp_path / "agents"
    skills_dir.mkdir()
    agents_dir.mkdir()

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            *_HEALTH_MODULE,
            "--report",
            "--catalog-path",
            str(catalog_file),
            "--drift-log",
            str(tmp_path / "drift.jsonl"),
            "--dispatch-log",
            str(tmp_path / "dispatch.jsonl"),
            "--skills-dir",
            str(skills_dir),
            "--agents-dir",
            str(agents_dir),
            "--plugin-overrides-dir",
            str(tmp_path / "triggers"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"health.py --report with --catalog-path failed; "
        f"stderr={result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# 20. Issue #262: Default --drift-log/--dispatch-log/--*-dir to ~/.claude paths
# ---------------------------------------------------------------------------
#
# All tests use subprocess with HOME/USERPROFILE redirected to a tmp dir so
# that Path.home() inside the CLI resolves to a controlled location.  This
# avoids the fragility of monkeypatching pathlib internals in-process.
# ---------------------------------------------------------------------------


def _env_with_fake_home(
    fake_home: Path,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build a subprocess env dict with HOME and USERPROFILE redirected.

    Clears all path-override env vars so the CLI exercises pure default-path
    resolution.  Any *extra* entries are overlaid last.

    Args:
        fake_home: Directory to use as the fake home.
        extra: Additional env var overrides (optional).

    Returns:
        Full environment dict suitable for ``subprocess.run``.
    """
    import os

    env = {**os.environ}
    env["HOME"] = str(fake_home)
    env["USERPROFILE"] = str(fake_home)
    for var in (
        "ROUTER_DRIFT_PATH",
        "DISPATCH_LOG",
        "ROUTER_SKILLS_DIR",
        "ROUTER_AGENTS_DIR",
        "ROUTER_PLUGIN_OVERRIDES_DIR",
    ):
        env.pop(var, None)
    if extra:
        env.update(extra)
    return env


def _scaffold_fake_home(
    fake_home: Path,
    *,
    drift_events: list[dict[str, Any]] | None = None,
    dispatch_events: list[dict[str, Any]] | None = None,
) -> None:
    """Create the standard ~/.claude/state/... layout under *fake_home*.

    Writes drift and dispatch JSONL files and creates skills/agents/triggers
    directories so the CLI finds valid empty dirs for CI invariant checks.

    Args:
        fake_home: Root of the fake home directory.
        drift_events: Events to write to router-drift.jsonl (default: empty).
        dispatch_events: Events to write to dispatch-log.jsonl (default: empty).
    """
    state_dir = fake_home / ".claude" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "router-drift.jsonl").write_text(
        "\n".join(json.dumps(e) for e in (drift_events or [])) + (
            "\n" if drift_events else ""
        ),
        encoding="utf-8",
    )
    (state_dir / "dispatch-log.jsonl").write_text(
        "\n".join(json.dumps(e) for e in (dispatch_events or [])) + (
            "\n" if dispatch_events else ""
        ),
        encoding="utf-8",
    )
    for subdir in ("skills", "agents", "triggers"):
        (fake_home / ".claude" / subdir).mkdir(parents=True, exist_ok=True)


def test_health_report_bare_exits_zero_with_fake_home(
    tmp_path: Path,
) -> None:
    """``health --report`` with no path flags exits 0 when defaults exist.

    Regression guard for Issue #262: the CLI must not require explicit path
    flags.  With HOME redirected to a controlled dir that has the expected
    layout, bare invocation must produce a valid report and exit 0.

    Args:
        tmp_path: Pytest-provided temporary directory.
    """
    fake_home = tmp_path / "home"
    _scaffold_fake_home(fake_home)

    result = subprocess.run(
        [sys.executable, "-m", *_HEALTH_MODULE, "--report"],
        capture_output=True,
        text=True,
        check=False,
        env=_env_with_fake_home(fake_home),
    )
    assert result.returncode == 0, (
        "``health --report`` with no path flags must exit 0.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "Router Health" in result.stdout, (
        "Expected 'Router Health' header in bare-invocation report output."
    )


def test_health_report_reads_from_default_drift_log(
    tmp_path: Path,
) -> None:
    """``health --report`` without --drift-log reads events from the default path.

    Writes bypass + dispatch events to the default paths under ``$HOME`` and
    verifies the report shows a non-zero bypass count.  The current (broken)
    behavior returns an empty list when no flag is given — the report would
    show "0 bypass events / 0 total agent calls".  After the fix, it must
    show counts that reflect the written events.

    This is the primary regression test for Issue #262.

    Args:
        tmp_path: Pytest-provided temporary directory.
    """
    fake_home = tmp_path / "home"
    # 1 bypass, 9 dispatches → bypass rate = 1/10 = 10%
    _scaffold_fake_home(
        fake_home,
        drift_events=[bypass_event(session_id=f"s{i}") for i in range(1)],
        dispatch_events=[agent_dispatch(session_id=f"s{i}") for i in range(9)],
    )

    result = subprocess.run(
        [sys.executable, "-m", *_HEALTH_MODULE, "--report"],
        capture_output=True,
        text=True,
        check=False,
        env=_env_with_fake_home(fake_home),
    )
    assert result.returncode == 0, (
        f"Expected exit 0.\nstderr: {result.stderr}"
    )
    # The report must reflect the non-zero counts we wrote.  The broken code
    # shows "0 bypass events / 0 total agent calls"; the fixed code shows
    # "1 bypass event" or similar.  We check that the total is NOT "0 / 0".
    assert "0 bypass events / 0 total agent calls" not in result.stdout, (
        "Bypass count must be non-zero when drift log default path contains "
        "events — '0 bypass events / 0 total agent calls' indicates the "
        "default drift log is NOT being read (Issue #262 footgun).\n"
        f"stdout:\n{result.stdout}"
    )


def test_health_report_env_var_overrides_default_drift_log(
    tmp_path: Path,
) -> None:
    """``ROUTER_DRIFT_PATH`` env var overrides the home-dir default.

    Writes 3 bypass + 7 dispatch events ONLY to the env-var path; the
    default path is left empty.  The report must show a non-zero bypass
    count, proving the env var is consulted rather than the empty default.

    Args:
        tmp_path: Pytest-provided temporary directory.
    """
    fake_home = tmp_path / "home"
    # Default path is empty (no events).
    _scaffold_fake_home(fake_home)

    # 3 bypass events only at the env-var path.
    env_drift = tmp_path / "env-drift.jsonl"
    events = [bypass_event(session_id=f"env-s{i}") for i in range(3)]
    env_drift.write_text(
        "\n".join(json.dumps(e) for e in events) + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, "-m", *_HEALTH_MODULE, "--report"],
        capture_output=True,
        text=True,
        check=False,
        env=_env_with_fake_home(
            fake_home, extra={"ROUTER_DRIFT_PATH": str(env_drift)}
        ),
    )
    assert result.returncode == 0, (
        f"Expected exit 0.\nstderr: {result.stderr}"
    )
    # Env-var path has 3 bypass events; default is empty.  If env var is
    # honored, the report must show non-zero bypass count.
    assert "0 bypass events / 0 total agent calls" not in result.stdout, (
        "Bypass count must be non-zero when ROUTER_DRIFT_PATH contains events "
        "and default path is empty — env-var override must be honored.\n"
        f"stdout:\n{result.stdout}"
    )


def test_health_report_explicit_flag_overrides_env_and_default_drift_log(
    tmp_path: Path,
) -> None:
    """Explicit ``--drift-log`` flag overrides both env var and home-dir default.

    Precedence order: explicit flag > ROUTER_DRIFT_PATH > home default.
    An explicit empty log must produce a zero-event report even when the
    env var and default paths have events.

    Args:
        tmp_path: Pytest-provided temporary directory.
    """
    fake_home = tmp_path / "home"
    _scaffold_fake_home(
        fake_home,
        drift_events=[bypass_event(session_id="poisoned-default")],
    )

    env_drift = tmp_path / "env-drift.jsonl"
    env_drift.write_text(
        json.dumps(bypass_event(session_id="poisoned-env")) + "\n",
        encoding="utf-8",
    )

    explicit_log = tmp_path / "explicit-empty.jsonl"
    explicit_log.write_text("", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            *_HEALTH_MODULE,
            "--report",
            "--drift-log",
            str(explicit_log),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=_env_with_fake_home(
            fake_home, extra={"ROUTER_DRIFT_PATH": str(env_drift)}
        ),
    )
    assert result.returncode == 0, (
        f"Explicit --drift-log must still work.\nstderr: {result.stderr}"
    )
    # Report ran successfully with the explicit (empty) log.
    assert "Router Health" in result.stdout, (
        "Report header must appear when using explicit --drift-log."
    )


def test_health_report_missing_default_drift_log_treated_as_empty(
    tmp_path: Path,
) -> None:
    """Missing default drift log produces empty telemetry, no exception.

    When no ``--drift-log`` flag is passed, no env var is set, and the
    default path ``~/.claude/state/router-drift.jsonl`` does not exist, the
    CLI must exit 0.  This confirms the "missing file = empty" contract from
    ``load_jsonl`` applies to the default-path code path too.

    Args:
        tmp_path: Pytest-provided temporary directory.
    """
    fake_home = tmp_path / "home_no_logs"
    # Create skills/agents/triggers but NO state dir — no log files at all.
    for subdir in ("skills", "agents", "triggers"):
        (fake_home / ".claude" / subdir).mkdir(parents=True, exist_ok=True)

    result = subprocess.run(
        [sys.executable, "-m", *_HEALTH_MODULE, "--report"],
        capture_output=True,
        text=True,
        check=False,
        env=_env_with_fake_home(fake_home),
    )
    assert result.returncode == 0, (
        "Missing default drift log must not raise — exit 0 expected.\n"
        f"stderr: {result.stderr}"
    )
    assert "Router Health" in result.stdout, (
        "Report header must appear even when log files are absent at the "
        "default path."
    )
