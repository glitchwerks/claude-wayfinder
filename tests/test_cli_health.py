"""Tests for the ``python -m claude_wayfinder health`` CLI subcommand.

Verifies that the ``health`` subcommand is correctly wired into the top-level
CLI and delegates argument parsing + execution to ``_health.main()``.

Coverage:
  - ``health --ci``      returns an int (0 = all pass, 1 = invariant failure;
                         both are valid from a fresh checkout with no real dirs)
  - ``health --report``  returns 0 and prints output containing "Router Health"
  - ``health`` (no mode) exits 2 (argparse required-group error from _health)
  - ``health --help``    exits 0 (SystemExit caught via pytest.raises)
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from claude_wayfinder import cli

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
