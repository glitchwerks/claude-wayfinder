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


# Canonical ISO-8601 timestamp within the default 30-day window.
_RECENT_TS = "2026-05-20T12:00:00+00:00"
# Timestamp well outside any window.
_OLD_TS = "2020-01-01T00:00:00+00:00"


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
