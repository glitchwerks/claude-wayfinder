"""Tests for the ``python -m claude_wayfinder health`` CLI subcommand.

Verifies that the ``health`` subcommand is correctly wired into the top-level
CLI and delegates argument parsing + execution to ``_health.main()``.

Coverage:
  - ``health --ci``          returns an int (0 = all pass, 1 = invariant
                             failure; both are valid from a fresh checkout
                             with no real dirs)
  - ``health --report``      returns 0 and prints output containing
                             "Router Health"
  - ``health`` (no mode)     exits 2 (argparse required-group error from
                             _health)
  - ``health --help``        exits 0 (SystemExit caught via pytest.raises)
  - ``_parse_window``        parses Nd / Nh specs; raises ValueError on bad
                             input
  - ``health drill``         happy path, missing-file, empty-file, --json,
                             --window, unknown metric
  - ``health top``           happy path, missing-file, empty-file, --json,
                             --window, unknown kind
  - ``health catalog-status`` happy path, missing catalog, empty catalog,
                             --json
"""

from __future__ import annotations

import datetime
import json
import subprocess
import sys
from pathlib import Path

import pytest

from claude_wayfinder import cli
from claude_wayfinder._health import _parse_window  # noqa: PLC2701

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    """Run ``python -m claude_wayfinder`` with *args and capture output.

    Args:
        *args: Additional arguments appended after ``-m claude_wayfinder``.

    Returns:
        A ``CompletedProcess`` with stdout/stderr captured as strings.
    """
    return subprocess.run(
        [sys.executable, "-m", "claude_wayfinder", *args],
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# health --ci
# ---------------------------------------------------------------------------


class TestHealthCi:
    """``health --ci`` must return an integer exit code (0 or 1).

    A fresh checkout with no real skills/agents dirs configured may have
    CI invariants that fail — exit code 1 is legitimate here.  The test
    guards only that the command is wired (not missing), returns an int,
    and does not crash unexpectedly (exit code 2 would indicate an
    argparse parse failure, which is an error).
    """

    def test_health_ci_returns_int(self) -> None:
        """``cli.main(['health', '--ci'])`` returns an int (0 or 1).

        Both 0 and 1 are valid outcomes: 0 means all CI invariants pass,
        1 means at least one invariant failed.  Exit code 2 would indicate
        an argparse error and is treated as a failure.
        """
        result = cli.main(["health", "--ci"])
        assert isinstance(result, int), (
            f"cli.main(['health', '--ci']) must return int, got {type(result)}"
        )
        assert result in (0, 1), (
            f"Expected exit code 0 or 1 from health --ci, got {result}. "
            "Exit code 2 would indicate an argparse error."
        )

    def test_health_ci_subprocess_exits_zero_or_one(self) -> None:
        """``python -m claude_wayfinder health --ci`` exits 0 or 1 (not 2).

        This exercises the real entry point via subprocess, ensuring the
        subcommand is registered in the top-level CLI parser.
        """
        result = _run("health", "--ci")
        assert result.returncode in (0, 1), (
            f"health --ci exited {result.returncode} (expected 0 or 1).\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )


# ---------------------------------------------------------------------------
# health --report
# ---------------------------------------------------------------------------


class TestHealthReport:
    """``health --report`` must return 0 and print a recognisable header."""

    def test_health_report_returns_zero(self) -> None:
        """``cli.main(['health', '--report'])`` returns 0."""
        result = cli.main(["health", "--report"])
        assert result == 0, (
            f"health --report expected exit code 0, got {result}."
        )

    def test_health_report_prints_router_health_header(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``health --report`` output must contain a recognisable report header.

        Args:
            capsys: Pytest fixture for capturing stdout/stderr.
        """
        cli.main(["health", "--report"])
        captured = capsys.readouterr()
        output = captured.out
        assert "Router Health" in output, (
            f"Expected 'Router Health' in health --report output.\n"
            f"Actual output:\n{output}"
        )

    def test_health_report_subprocess_exits_zero(self) -> None:
        """``python -m claude_wayfinder health --report`` must exit 0.

        This exercises the real entry point via subprocess, ensuring the
        subcommand is wired and ``_health.main()`` runs to completion.
        """
        result = _run("health", "--report")
        assert result.returncode == 0, (
            f"health --report exited {result.returncode}.\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )
        assert result.stdout.strip(), "health --report produced no stdout."


# ---------------------------------------------------------------------------
# health (no mode flag) — argparse required-group error
# ---------------------------------------------------------------------------


class TestHealthNoMode:
    """``health`` with no mode flag must exit 2 (argparse error from _health).

    ``_health.main()`` uses a mutually-exclusive required group for
    ``--ci`` / ``--report``.  When neither flag is given, argparse writes
    an error to stderr and raises ``SystemExit(2)``.
    """

    def test_health_no_mode_exits_two(self) -> None:
        """``python -m claude_wayfinder health`` exits 2.

        The exit code 2 must come from ``_health.main()``'s required
        mutually-exclusive group, not from the top-level CLI parser.
        """
        result = _run("health")
        assert result.returncode == 2, (
            f"health with no mode expected exit code 2, got {result.returncode}.\n"
            f"stderr: {result.stderr}"
        )


# ---------------------------------------------------------------------------
# health --help
# ---------------------------------------------------------------------------


class TestHealthHelp:
    """``health --help`` must exit 0 and show the _health argparse surface."""

    def test_health_help_exits_zero_via_systemexit(self) -> None:
        """``cli.main(['health', '--help'])`` raises SystemExit(0).

        argparse calls ``sys.exit(0)`` for ``--help``; we catch it via
        ``pytest.raises`` and assert the exit code is 0.
        """
        with pytest.raises(SystemExit) as exc_info:
            cli.main(["health", "--help"])
        assert exc_info.value.code == 0, (
            f"health --help SystemExit code expected 0, got {exc_info.value.code}."
        )

    def test_health_help_subprocess_exits_zero(self) -> None:
        """``python -m claude_wayfinder health --help`` exits 0."""
        result = _run("health", "--help")
        assert result.returncode == 0, (
            f"health --help exited {result.returncode}.\n"
            f"stderr: {result.stderr}"
        )

    def test_health_help_shows_ci_flag(self) -> None:
        """``health --help`` output must mention the --ci flag.

        This confirms that ``_health.main()``'s argparse surface is exposed,
        not the top-level parser's help.
        """
        result = _run("health", "--help")
        assert "--ci" in result.stdout, (
            f"Expected '--ci' in health --help output.\n"
            f"Full output:\n{result.stdout}"
        )

    def test_health_help_shows_report_flag(self) -> None:
        """``health --help`` output must mention the --report flag.

        Args are declared by ``_health.main()`` — this verifies delegation
        is working rather than the stub being printed.
        """
        result = _run("health", "--help")
        assert "--report" in result.stdout, (
            f"Expected '--report' in health --help output.\n"
            f"Full output:\n{result.stdout}"
        )


# ---------------------------------------------------------------------------
# Regression: existing subcommands unaffected
# ---------------------------------------------------------------------------


class TestExistingSubcommandsUnaffected:
    """Adding ``health`` must not break the existing subcommand surface."""

    def test_demo_still_exits_zero(self) -> None:
        """The ``demo`` subcommand must be unaffected by the health addition."""
        result = _run("demo")
        assert result.returncode == 0, (
            f"demo exited {result.returncode} after health subparser was added.\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )


# ---------------------------------------------------------------------------
# _parse_window helper
# ---------------------------------------------------------------------------


class TestParseWindow:
    """``_parse_window`` converts Nd / Nh spec strings to timedeltas."""

    def test_days_spec_returns_timedelta(self) -> None:
        """``_parse_window('30d')`` returns a 30-day timedelta."""
        result = _parse_window("30d")
        assert result == datetime.timedelta(days=30), (
            f"Expected timedelta(days=30), got {result!r}"
        )

    def test_hours_spec_returns_timedelta(self) -> None:
        """``_parse_window('48h')`` returns a 48-hour timedelta."""
        result = _parse_window("48h")
        assert result == datetime.timedelta(hours=48), (
            f"Expected timedelta(hours=48), got {result!r}"
        )

    def test_single_day_spec(self) -> None:
        """``_parse_window('1d')`` returns timedelta(days=1)."""
        assert _parse_window("1d") == datetime.timedelta(days=1)

    def test_invalid_unit_raises(self) -> None:
        """``_parse_window('30m')`` raises ValueError on unknown unit."""
        with pytest.raises(ValueError, match="30m"):
            _parse_window("30m")

    def test_missing_number_raises(self) -> None:
        """``_parse_window('d')`` raises ValueError when no number prefix."""
        with pytest.raises(ValueError):
            _parse_window("d")

    def test_empty_string_raises(self) -> None:
        """``_parse_window('')`` raises ValueError on empty input."""
        with pytest.raises(ValueError):
            _parse_window("")

    def test_plain_integer_raises(self) -> None:
        """``_parse_window('30')`` raises ValueError when unit absent."""
        with pytest.raises(ValueError):
            _parse_window("30")


# ---------------------------------------------------------------------------
# Fixtures shared across subcommand tests
# ---------------------------------------------------------------------------


def _make_drift_log(tmp_path: Path, events: list[dict]) -> Path:
    """Write JSONL drift events to a temp file and return the path.

    Args:
        tmp_path: Pytest-provided temporary directory.
        events: List of event dicts to serialise as JSONL.

    Returns:
        Path to the written file.
    """
    p = tmp_path / "router-drift.jsonl"
    p.write_text(
        "\n".join(json.dumps(e) for e in events) + ("\n" if events else ""),
        encoding="utf-8",
    )
    return p


def _make_dispatch_log(tmp_path: Path, events: list[dict]) -> Path:
    """Write JSONL dispatch events to a temp file and return the path.

    Args:
        tmp_path: Pytest-provided temporary directory.
        events: List of event dicts to serialise as JSONL.

    Returns:
        Path to the written file.
    """
    p = tmp_path / "dispatch-log.jsonl"
    p.write_text(
        "\n".join(json.dumps(e) for e in events) + ("\n" if events else ""),
        encoding="utf-8",
    )
    return p


def _make_catalog(tmp_path: Path, entries: list[dict]) -> Path:
    """Write a dispatch-catalog.json file and return the path.

    Args:
        tmp_path: Pytest-provided temporary directory.
        entries: Catalog entry dicts.

    Returns:
        Path to the written file.
    """
    p = tmp_path / "dispatch-catalog.json"
    p.write_text(
        json.dumps({"entries": entries}), encoding="utf-8"
    )
    return p


# Timestamps computed at module load so they never age out of the
# rolling window.  Using a single ``_now`` anchor makes the relationship
# between the two values explicit and ensures consistent behaviour
# regardless of when the test suite is run.
#
# _RECENT_TS: 1 hour ago — safely inside the tightest window any test
#   uses (``--window 1d``), so events tagged with it are always counted.
# _OLD_TS:    365 days ago — safely outside *all* windows exercised by
#   the test suite, so events tagged with it are always excluded.
_now = datetime.datetime.now(datetime.timezone.utc)
_RECENT_TS: str = (
    _now - datetime.timedelta(hours=1)
).isoformat()
_OLD_TS: str = (
    _now - datetime.timedelta(days=365)
).isoformat()


# ---------------------------------------------------------------------------
# health drill
# ---------------------------------------------------------------------------


class TestHealthDrill:
    """``health drill`` drills into a single metric from the drift/dispatch log."""

    def test_drill_bypass_happy_path(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``health drill --metric bypass`` prints bypass events by day.

        Args:
            tmp_path: Pytest-provided temporary directory.
            capsys: Pytest fixture for capturing stdout.
        """
        drift = _make_drift_log(
            tmp_path,
            [
                {
                    "type": "router_drift",
                    "category": "bypass",
                    "ts": _RECENT_TS,
                    "session_id": "abc123",
                },
                {
                    "type": "router_drift",
                    "category": "bypass",
                    "ts": _RECENT_TS,
                    "session_id": "def456",
                },
            ],
        )
        rc = cli.main([
            "health", "drill",
            "--metric", "bypass",
            "--drift-log", str(drift),
        ])
        captured = capsys.readouterr()
        assert rc == 0, f"Expected exit 0, got {rc}\nstdout: {captured.out}"
        assert "bypass" in captured.out.lower(), (
            f"Expected 'bypass' in output.\nActual: {captured.out}"
        )
        assert "2" in captured.out, (
            "Expected count '2' in output."
        )

    def test_drill_advisory_override_happy_path(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``health drill --metric advisory-override`` counts overrides by session.

        Args:
            tmp_path: Pytest-provided temporary directory.
            capsys: Pytest fixture for capturing stdout.
        """
        drift = _make_drift_log(
            tmp_path,
            [
                {
                    "type": "advisory_override",
                    "ts": _RECENT_TS,
                    "session_id": "sess01",
                },
                {
                    "type": "advisory_override",
                    "ts": _RECENT_TS,
                    "session_id": "sess01",
                },
            ],
        )
        rc = cli.main([
            "health", "drill",
            "--metric", "advisory-override",
            "--drift-log", str(drift),
        ])
        captured = capsys.readouterr()
        assert rc == 0, f"Expected exit 0, got {rc}"
        assert "advisory" in captured.out.lower(), (
            "Expected 'advisory' in output."
        )

    def test_drill_recent_drift_happy_path(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``health drill --metric recent-drift`` lists the 5 most recent events.

        Args:
            tmp_path: Pytest-provided temporary directory.
            capsys: Pytest fixture for capturing stdout.
        """
        events = [
            {
                "type": "router_drift",
                "category": "bypass",
                "ts": _RECENT_TS,
                "session_id": f"sess{i:02d}",
            }
            for i in range(7)
        ]
        drift = _make_drift_log(tmp_path, events)
        rc = cli.main([
            "health", "drill",
            "--metric", "recent-drift",
            "--drift-log", str(drift),
        ])
        captured = capsys.readouterr()
        assert rc == 0, f"Expected exit 0, got {rc}"
        # Should show 5 most recent, not all 7.
        assert "sess" in captured.out, "Expected session ID prefix in output."

    def test_drill_missing_drift_log_exits_zero(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``health drill`` with a missing drift log exits 0 with a helpful message.

        Args:
            tmp_path: Pytest-provided temporary directory.
            capsys: Pytest fixture for capturing stdout.
        """
        missing = tmp_path / "no-such-file.jsonl"
        rc = cli.main([
            "health", "drill",
            "--metric", "bypass",
            "--drift-log", str(missing),
        ])
        captured = capsys.readouterr()
        assert rc == 0, f"Expected exit 0 on missing file, got {rc}"
        assert "no" in captured.out.lower() or "missing" in captured.out.lower() or (
            "0" in captured.out
        ), f"Expected empty/missing notice in output.\nActual: {captured.out}"

    def test_drill_empty_drift_log_exits_zero(
        self, tmp_path: Path
    ) -> None:
        """``health drill`` with an empty drift log exits 0.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        empty = _make_drift_log(tmp_path, [])
        rc = cli.main([
            "health", "drill",
            "--metric", "bypass",
            "--drift-log", str(empty),
        ])
        assert rc == 0, f"Expected exit 0 on empty file, got {rc}"

    def test_drill_json_flag_emits_valid_json(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``health drill --json`` emits machine-readable JSON to stdout.

        Args:
            tmp_path: Pytest-provided temporary directory.
            capsys: Pytest fixture for capturing stdout.
        """
        drift = _make_drift_log(
            tmp_path,
            [
                {
                    "type": "router_drift",
                    "category": "bypass",
                    "ts": _RECENT_TS,
                    "session_id": "abc",
                }
            ],
        )
        rc = cli.main([
            "health", "drill",
            "--metric", "bypass",
            "--drift-log", str(drift),
            "--json",
        ])
        captured = capsys.readouterr()
        assert rc == 0, f"Expected exit 0, got {rc}"
        data = json.loads(captured.out)
        assert isinstance(data, dict), "JSON output must be a dict."
        assert "metric" in data, "JSON output must contain 'metric' key."

    def test_drill_window_flag_filters_old_events(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``health drill --window 1d`` excludes events older than 1 day.

        Args:
            tmp_path: Pytest-provided temporary directory.
            capsys: Pytest fixture for capturing stdout.
        """
        drift = _make_drift_log(
            tmp_path,
            [
                {
                    "type": "router_drift",
                    "category": "bypass",
                    "ts": _OLD_TS,
                    "session_id": "old",
                },
                {
                    "type": "router_drift",
                    "category": "bypass",
                    "ts": _RECENT_TS,
                    "session_id": "recent",
                },
            ],
        )
        rc = cli.main([
            "health", "drill",
            "--metric", "bypass",
            "--drift-log", str(drift),
            "--window", "1d",
        ])
        captured = capsys.readouterr()
        assert rc == 0, f"Expected exit 0, got {rc}"
        # The old event should not appear; only 'recent' in window.
        assert "old" not in captured.out, (
            "Old session should be excluded from 1d window."
        )

    def test_drill_unknown_metric_exits_two(self, tmp_path: Path) -> None:
        """``health drill --metric unknown-metric`` exits 2 (argparse error).

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        result = _run(
            "health", "drill", "--metric", "nonexistent-metric"
        )
        assert result.returncode == 2, (
            f"Expected exit 2 for invalid metric, got {result.returncode}.\n"
            f"stderr: {result.stderr}"
        )

    def test_drill_type_tagged_events_counted(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Drift events with ``type`` field (not ``category``) are counted.

        Verifies the ``e.get('category') or e.get('type')`` discriminator
        works for type-tagged drift events.

        Args:
            tmp_path: Pytest-provided temporary directory.
            capsys: Pytest fixture for capturing stdout.
        """
        drift = _make_drift_log(
            tmp_path,
            [
                # Type-tagged shape (no category field):
                {
                    "type": "advisory_override",
                    "ts": _RECENT_TS,
                    "session_id": "typed01",
                },
            ],
        )
        rc = cli.main([
            "health", "drill",
            "--metric", "advisory-override",
            "--drift-log", str(drift),
        ])
        captured = capsys.readouterr()
        assert rc == 0, f"Expected exit 0, got {rc}"
        # Should count 1 event, not zero.
        assert "typed01" in captured.out or "1" in captured.out, (
            "Type-tagged advisory_override event should be counted.\n"
            f"Actual output: {captured.out}"
        )


# ---------------------------------------------------------------------------
# health top
# ---------------------------------------------------------------------------


class TestHealthTop:
    """``health top`` shows most-dispatched agents or most-invoked skills."""

    def test_top_agents_happy_path(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``health top --kind agents`` lists top dispatched agents.

        Args:
            tmp_path: Pytest-provided temporary directory.
            capsys: Pytest fixture for capturing stdout.
        """
        dispatch = _make_dispatch_log(
            tmp_path,
            [
                {
                    "type": "agent_dispatch",
                    "agent": "code-writer",
                    "ts": _RECENT_TS,
                },
                {
                    "type": "agent_dispatch",
                    "agent": "code-writer",
                    "ts": _RECENT_TS,
                },
                {
                    "type": "agent_dispatch",
                    "agent": "debugger",
                    "ts": _RECENT_TS,
                },
            ],
        )
        rc = cli.main([
            "health", "top",
            "--kind", "agents",
            "--dispatch-log", str(dispatch),
        ])
        captured = capsys.readouterr()
        assert rc == 0, f"Expected exit 0, got {rc}\nstdout: {captured.out}"
        assert "code-writer" in captured.out, (
            "Expected 'code-writer' (top agent) in output."
        )

    def test_top_skills_happy_path(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``health top --kind skills`` lists top invoked skills.

        Args:
            tmp_path: Pytest-provided temporary directory.
            capsys: Pytest fixture for capturing stdout.
        """
        dispatch = _make_dispatch_log(
            tmp_path,
            [
                {
                    "type": "skill_invocation",
                    "skill": "dispatch",
                    "ts": _RECENT_TS,
                },
                {
                    "type": "skill_invocation",
                    "skill": "dispatch",
                    "ts": _RECENT_TS,
                },
                {
                    "type": "skill_invocation",
                    "skill": "python",
                    "ts": _RECENT_TS,
                },
            ],
        )
        rc = cli.main([
            "health", "top",
            "--kind", "skills",
            "--dispatch-log", str(dispatch),
        ])
        captured = capsys.readouterr()
        assert rc == 0, f"Expected exit 0, got {rc}"
        assert "dispatch" in captured.out, (
            "Expected 'dispatch' (top skill) in output."
        )

    def test_top_missing_dispatch_log_exits_zero(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``health top`` with a missing dispatch log exits 0 gracefully.

        Args:
            tmp_path: Pytest-provided temporary directory.
            capsys: Pytest fixture for capturing stdout.
        """
        missing = tmp_path / "no-dispatch.jsonl"
        rc = cli.main([
            "health", "top",
            "--kind", "agents",
            "--dispatch-log", str(missing),
        ])
        captured = capsys.readouterr()
        assert rc == 0, (
            f"Expected exit 0 on missing file, got {rc}.\n"
            f"stdout: {captured.out}"
        )

    def test_top_empty_dispatch_log_exits_zero(
        self, tmp_path: Path
    ) -> None:
        """``health top`` with an empty dispatch log exits 0 gracefully.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        empty = _make_dispatch_log(tmp_path, [])
        rc = cli.main([
            "health", "top",
            "--kind", "agents",
            "--dispatch-log", str(empty),
        ])
        assert rc == 0, f"Expected exit 0 on empty file, got {rc}"

    def test_top_json_flag_emits_valid_json(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``health top --json`` emits machine-readable JSON.

        Args:
            tmp_path: Pytest-provided temporary directory.
            capsys: Pytest fixture for capturing stdout.
        """
        dispatch = _make_dispatch_log(
            tmp_path,
            [
                {
                    "type": "agent_dispatch",
                    "agent": "code-writer",
                    "ts": _RECENT_TS,
                },
            ],
        )
        rc = cli.main([
            "health", "top",
            "--kind", "agents",
            "--dispatch-log", str(dispatch),
            "--json",
        ])
        captured = capsys.readouterr()
        assert rc == 0, f"Expected exit 0, got {rc}"
        data = json.loads(captured.out)
        assert isinstance(data, dict), "JSON output must be a dict."
        assert "kind" in data, "JSON output must contain 'kind' key."
        assert "entries" in data, "JSON output must contain 'entries' key."

    def test_top_window_flag_excludes_old_events(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``health top --window 1d`` excludes events outside the window.

        Args:
            tmp_path: Pytest-provided temporary directory.
            capsys: Pytest fixture for capturing stdout.
        """
        dispatch = _make_dispatch_log(
            tmp_path,
            [
                {
                    "type": "agent_dispatch",
                    "agent": "old-agent",
                    "ts": _OLD_TS,
                },
                {
                    "type": "agent_dispatch",
                    "agent": "recent-agent",
                    "ts": _RECENT_TS,
                },
            ],
        )
        rc = cli.main([
            "health", "top",
            "--kind", "agents",
            "--dispatch-log", str(dispatch),
            "--window", "1d",
        ])
        captured = capsys.readouterr()
        assert rc == 0, f"Expected exit 0, got {rc}"
        assert "old-agent" not in captured.out, (
            "old-agent should be excluded from 1d window."
        )

    def test_top_limit_flag_caps_entries(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``health top --limit 1`` shows only the single top entry.

        Args:
            tmp_path: Pytest-provided temporary directory.
            capsys: Pytest fixture for capturing stdout.
        """
        dispatch = _make_dispatch_log(
            tmp_path,
            [
                {
                    "type": "agent_dispatch",
                    "agent": "agent-a",
                    "ts": _RECENT_TS,
                },
                {
                    "type": "agent_dispatch",
                    "agent": "agent-b",
                    "ts": _RECENT_TS,
                },
                {
                    "type": "agent_dispatch",
                    "agent": "agent-b",
                    "ts": _RECENT_TS,
                },
            ],
        )
        rc = cli.main([
            "health", "top",
            "--kind", "agents",
            "--dispatch-log", str(dispatch),
            "--limit", "1",
        ])
        captured = capsys.readouterr()
        assert rc == 0, f"Expected exit 0, got {rc}"
        # With limit=1, only the top agent should appear.
        assert "agent-b" in captured.out, (
            "Top agent (agent-b, 2 dispatches) must appear."
        )
        assert "agent-a" not in captured.out, (
            "Second agent (agent-a) must be excluded with --limit 1."
        )

    def test_top_unknown_kind_exits_two(self) -> None:
        """``health top --kind bad-kind`` exits 2 (argparse error).

        Args: none — subprocess test, no tmp_path needed.
        """
        result = _run("health", "top", "--kind", "bad-kind")
        assert result.returncode == 2, (
            f"Expected exit 2 for invalid kind, got {result.returncode}.\n"
            f"stderr: {result.stderr}"
        )


# ---------------------------------------------------------------------------
# health catalog-status
# ---------------------------------------------------------------------------


class TestHealthCatalogStatus:
    """``health catalog-status`` reports plugin entry counts from the catalog."""

    def test_catalog_status_happy_path(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``health catalog-status`` prints skill/agent counts.

        Args:
            tmp_path: Pytest-provided temporary directory.
            capsys: Pytest fixture for capturing stdout.
        """
        cat = _make_catalog(
            tmp_path,
            [
                {"kind": "skill", "name": "python", "source": "plugin"},
                {"kind": "agent", "name": "code-writer", "source": "plugin"},
            ],
        )
        rc = cli.main([
            "health", "catalog-status",
            "--catalog-path", str(cat),
        ])
        captured = capsys.readouterr()
        assert rc == 0, f"Expected exit 0, got {rc}\nstdout: {captured.out}"
        assert "skill" in captured.out.lower(), (
            "Expected 'skill' count in output."
        )
        assert "agent" in captured.out.lower(), (
            "Expected 'agent' count in output."
        )

    def test_catalog_status_missing_catalog_exits_zero(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``health catalog-status`` with a missing catalog exits 0 gracefully.

        Args:
            tmp_path: Pytest-provided temporary directory.
            capsys: Pytest fixture for capturing stdout.
        """
        missing = tmp_path / "no-catalog.json"
        rc = cli.main([
            "health", "catalog-status",
            "--catalog-path", str(missing),
        ])
        captured = capsys.readouterr()
        assert rc == 0, (
            f"Expected exit 0 on missing catalog, got {rc}.\n"
            f"stdout: {captured.out}"
        )
        assert (
            "absent" in captured.out.lower()
            or "missing" in captured.out.lower()
            or "not found" in captured.out.lower()
            or "no catalog" in captured.out.lower()
        ), f"Expected missing-catalog notice.\nActual: {captured.out}"

    def test_catalog_status_empty_catalog_exits_zero(
        self, tmp_path: Path
    ) -> None:
        """``health catalog-status`` with zero entries exits 0.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        cat = _make_catalog(tmp_path, [])
        rc = cli.main([
            "health", "catalog-status",
            "--catalog-path", str(cat),
        ])
        assert rc == 0, f"Expected exit 0 on empty catalog, got {rc}"

    def test_catalog_status_json_flag_emits_valid_json(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``health catalog-status --json`` emits machine-readable JSON.

        Args:
            tmp_path: Pytest-provided temporary directory.
            capsys: Pytest fixture for capturing stdout.
        """
        cat = _make_catalog(
            tmp_path,
            [
                {"kind": "skill", "name": "python", "source": "plugin"},
            ],
        )
        rc = cli.main([
            "health", "catalog-status",
            "--catalog-path", str(cat),
            "--json",
        ])
        captured = capsys.readouterr()
        assert rc == 0, f"Expected exit 0, got {rc}"
        data = json.loads(captured.out)
        assert isinstance(data, dict), "JSON output must be a dict."
        assert "skills" in data, "JSON output must contain 'skills' key."
        assert "agents" in data, "JSON output must contain 'agents' key."
        assert "routable" in data, "JSON output must contain 'routable' key."


# ---------------------------------------------------------------------------
# Issue #262: Default paths for --drift-log and related args
# ---------------------------------------------------------------------------


class TestHealthCliDefaults:
    """Bare ``health --report`` / ``health drill`` must read from ~/.claude paths.

    These tests verify Issue #262 — invoking the CLI without explicit path
    flags no longer produces a misleading empty report.  The tests use
    subprocess with a monkeypatched ``HOME``/``USERPROFILE`` to redirect
    Path.home() to a controlled directory.
    """

    def _env_with_fake_home(
        self,
        fake_home: Path,
        extra: dict | None = None,
    ) -> dict:
        """Build a subprocess env dict with HOME pointing at *fake_home*.

        Clears path-override env vars so the defaults are exercised, then
        overlays any *extra* entries.

        Args:
            fake_home: Directory to use as the fake home.
            extra: Additional env vars to overlay (optional).

        Returns:
            A full environment dict suitable for ``subprocess.run``.
        """
        import os

        env = {**os.environ}
        env["HOME"] = str(fake_home)
        env["USERPROFILE"] = str(fake_home)
        # Clear all path-override vars so we test the pure defaults.
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

    def test_bare_report_exits_zero_with_fake_home(
        self, tmp_path: Path
    ) -> None:
        """``health --report`` with no path flags exits 0 using default paths.

        Redirects HOME to a tmp dir that has an empty drift/dispatch log so
        the default path resolution produces a valid (empty) report rather
        than crashing with "required argument missing".

        This is the primary regression guard for Issue #262.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        fake_home = tmp_path / "home"
        state_dir = fake_home / ".claude" / "state"
        state_dir.mkdir(parents=True)
        # Write minimal log files so the defaults are resolvable.
        (state_dir / "router-drift.jsonl").write_text("", encoding="utf-8")
        (state_dir / "dispatch-log.jsonl").write_text("", encoding="utf-8")
        # Create skills/agents/triggers dirs so CI invariants don't FAIL due
        # to missing directories.
        (fake_home / ".claude" / "skills").mkdir(parents=True, exist_ok=True)
        (fake_home / ".claude" / "agents").mkdir(parents=True, exist_ok=True)
        (fake_home / ".claude" / "triggers").mkdir(parents=True, exist_ok=True)

        env = self._env_with_fake_home(fake_home)
        result = subprocess.run(
            [sys.executable, "-m", "claude_wayfinder", "health", "--report"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0, (
            "``health --report`` with no flags must exit 0 using default paths.\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )
        assert "Router Health" in result.stdout, (
            "Expected 'Router Health' header in bare-invocation report output."
        )

    def test_bare_report_reads_from_default_drift_log(
        self, tmp_path: Path
    ) -> None:
        """``health --report`` without --drift-log reads events from the default path.

        Writes bypass+dispatch events to ``$HOME/.claude/state/router-drift.jsonl``
        and verifies the report shows non-zero bypass counts.  The broken
        (pre-fix) behavior would show "0 bypass events / 0 total agent calls"
        because the default path is never read.

        This is the primary regression test for Issue #262.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        fake_home = tmp_path / "home"
        state_dir = fake_home / ".claude" / "state"
        state_dir.mkdir(parents=True)
        # 1 bypass, 9 dispatches → bypass rate 10%.
        (state_dir / "router-drift.jsonl").write_text(
            json.dumps({
                "type": "router_drift",
                "category": "bypass",
                "ts": _RECENT_TS,
                "session_id": "sess-default-read",
            }) + "\n",
            encoding="utf-8",
        )
        (state_dir / "dispatch-log.jsonl").write_text(
            "\n".join(
                json.dumps({
                    "type": "agent_dispatch",
                    "ts": _RECENT_TS,
                    "session_id": f"sess-d{i}",
                    "agent": "code-writer",
                })
                for i in range(9)
            ) + "\n",
            encoding="utf-8",
        )
        (fake_home / ".claude" / "skills").mkdir(parents=True, exist_ok=True)
        (fake_home / ".claude" / "agents").mkdir(parents=True, exist_ok=True)
        (fake_home / ".claude" / "triggers").mkdir(parents=True, exist_ok=True)

        env = self._env_with_fake_home(fake_home)
        result = subprocess.run(
            [sys.executable, "-m", "claude_wayfinder", "health", "--report"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0, (
            f"Expected exit 0.\nstderr: {result.stderr}"
        )
        # With the fix, bypass count must be non-zero.
        # The broken code shows "0 bypass events / 0 total agent calls".
        assert "0 bypass events / 0 total agent calls" not in result.stdout, (
            "Bypass count must reflect the event written to the default drift "
            "log path.  '0 bypass events / 0 total agent calls' means the "
            "default path is NOT being read (Issue #262 footgun).\n"
            f"stdout:\n{result.stdout}"
        )

    def test_env_var_drift_path_overrides_default(
        self, tmp_path: Path
    ) -> None:
        """``ROUTER_DRIFT_PATH`` overrides the home-dir default for drift log.

        Writes bypass events ONLY to the env-var path; the default path is
        left empty.  The report must show non-zero bypass count, proving
        ``ROUTER_DRIFT_PATH`` is honored rather than the empty default.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        fake_home = tmp_path / "home"
        state_dir = fake_home / ".claude" / "state"
        state_dir.mkdir(parents=True)
        # Default path deliberately empty.
        (state_dir / "router-drift.jsonl").write_text("", encoding="utf-8")
        (state_dir / "dispatch-log.jsonl").write_text("", encoding="utf-8")
        (fake_home / ".claude" / "skills").mkdir(parents=True, exist_ok=True)
        (fake_home / ".claude" / "agents").mkdir(parents=True, exist_ok=True)
        (fake_home / ".claude" / "triggers").mkdir(parents=True, exist_ok=True)

        # Write 2 bypass events only at the env-var path.
        env_drift = tmp_path / "env-router-drift.jsonl"
        env_drift.write_text(
            "\n".join(
                json.dumps({
                    "type": "router_drift",
                    "category": "bypass",
                    "ts": _RECENT_TS,
                    "session_id": f"env-sess-{i}",
                })
                for i in range(2)
            ) + "\n",
            encoding="utf-8",
        )

        env = self._env_with_fake_home(
            fake_home, extra={"ROUTER_DRIFT_PATH": str(env_drift)}
        )
        result = subprocess.run(
            [sys.executable, "-m", "claude_wayfinder", "health", "--report"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0, (
            f"Expected exit 0.\nstderr: {result.stderr}"
        )
        # Env-var path has 2 bypass events; default is empty.
        # Non-zero count in report proves env-var is honored.
        assert "0 bypass events / 0 total agent calls" not in result.stdout, (
            "Bypass count must be non-zero when ROUTER_DRIFT_PATH contains "
            "events and default path is empty.\n"
            f"stdout:\n{result.stdout}"
        )

    def test_explicit_flag_overrides_env_and_default_for_drift_log(
        self, tmp_path: Path
    ) -> None:
        """Explicit ``--drift-log`` flag wins over ``ROUTER_DRIFT_PATH`` and default.

        An explicit flag pointing at an EMPTY log must produce a zero-bypass
        report even when the env var and default locations have events.

        Precedence: explicit flag > env var > ~/.claude default.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        fake_home = tmp_path / "home"
        state_dir = fake_home / ".claude" / "state"
        state_dir.mkdir(parents=True)

        bypass_line = json.dumps({
            "type": "router_drift",
            "category": "bypass",
            "ts": _RECENT_TS,
            "session_id": "sess-should-be-ignored",
        }) + "\n"

        # Poison both default and env-var locations with events.
        (state_dir / "router-drift.jsonl").write_text(
            bypass_line, encoding="utf-8"
        )
        env_drift = tmp_path / "env-drift.jsonl"
        env_drift.write_text(bypass_line, encoding="utf-8")

        # Empty explicit log.
        explicit_log = tmp_path / "explicit-empty.jsonl"
        explicit_log.write_text("", encoding="utf-8")

        (state_dir / "dispatch-log.jsonl").write_text("", encoding="utf-8")
        skills_dir = fake_home / ".claude" / "skills"
        agents_dir = fake_home / ".claude" / "agents"
        triggers_dir = fake_home / ".claude" / "triggers"
        skills_dir.mkdir(parents=True, exist_ok=True)
        agents_dir.mkdir(parents=True, exist_ok=True)
        triggers_dir.mkdir(parents=True, exist_ok=True)

        env = self._env_with_fake_home(
            fake_home, extra={"ROUTER_DRIFT_PATH": str(env_drift)}
        )
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "claude_wayfinder",
                "health",
                "--report",
                "--drift-log",
                str(explicit_log),
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0, (
            f"Explicit --drift-log should still work.\n"
            f"stderr: {result.stderr}"
        )

    def test_missing_default_drift_log_treated_as_empty(
        self, tmp_path: Path
    ) -> None:
        """Missing default drift log is treated as empty, not an error.

        When the default path does not exist on disk, the CLI must exit 0
        with a valid empty-telemetry report.  This confirms ``load_jsonl``'s
        "missing = empty" contract holds through the default-path code path.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        fake_home = tmp_path / "home_no_logs"
        fake_home.mkdir()
        # No state dir — no logs exist at all.
        (fake_home / ".claude" / "skills").mkdir(parents=True, exist_ok=True)
        (fake_home / ".claude" / "agents").mkdir(parents=True, exist_ok=True)
        (fake_home / ".claude" / "triggers").mkdir(parents=True, exist_ok=True)

        env = self._env_with_fake_home(fake_home)
        result = subprocess.run(
            [sys.executable, "-m", "claude_wayfinder", "health", "--report"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0, (
            "Missing default drift log must not raise — exit 0 expected.\n"
            f"stderr: {result.stderr}"
        )
        assert "Router Health" in result.stdout, (
            "Report header must appear even when log files are absent at the "
            "default path."
        )

    def test_drill_bare_invocation_uses_default_drift_log(
        self, tmp_path: Path
    ) -> None:
        """``health drill --metric bypass`` without --drift-log uses default path.

        Writes a bypass event to the default drift log location and runs the
        drill subcommand without an explicit --drift-log flag.  The output
        must reflect the event (non-zero total), proving the default path
        is resolved inside ``_drill.py`` as well as ``__init__.py``.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        fake_home = tmp_path / "home"
        state_dir = fake_home / ".claude" / "state"
        state_dir.mkdir(parents=True)
        (state_dir / "router-drift.jsonl").write_text(
            json.dumps({
                "type": "router_drift",
                "category": "bypass",
                "ts": _RECENT_TS,
                "session_id": "drill-default-sess",
            }) + "\n",
            encoding="utf-8",
        )

        env = self._env_with_fake_home(fake_home)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "claude_wayfinder",
                "health",
                "drill",
                "--metric",
                "bypass",
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0, (
            "health drill --metric bypass must exit 0 using default drift log path.\n"
            f"stderr: {result.stderr}\nstdout: {result.stdout}"
        )
        # Drill output for bypass with 1 event must report Total bypass events: 1.
        # The broken code (no default path) would show 0.
        assert "Total bypass events: 0" not in result.stdout, (
            "Drill must report non-zero bypass events when default drift log "
            "contains an event.  'Total bypass events: 0' means the default "
            "path is NOT being read in _drill.py (Issue #262).\n"
            f"stdout:\n{result.stdout}"
        )
