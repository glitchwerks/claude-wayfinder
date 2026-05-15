"""Tests for the ``python -m claude_wayfinder catalog build`` subcommand.

Covers two behaviors:
  (a) ``catalog build --help`` exits 0 and lists all expected flags.
  (b) End-to-end smoke — ``catalog build`` on a fixture skills-dir and
      agents-dir produces a valid ``dispatch-catalog.json``.

The tests exercise the real entry point via subprocess so that the full
argparse / delegation chain is exercised, not mocked internals.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_TESTS_DIR = Path(__file__).resolve().parent
_FIXTURES_DIR = _TESTS_DIR / "fixtures"
_SKILLS_DIR = _FIXTURES_DIR / "skills"
_AGENTS_DIR = _FIXTURES_DIR / "agents"

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
# (a) --help surface
# ---------------------------------------------------------------------------

# All flags that must appear in ``catalog build --help``.
_EXPECTED_FLAGS = [
    "--skills-dir",
    "--agents-dir",
    "--out",
    "--log",
    "--plugin-overrides-dir",
    "--plugins-dir",
    "--builtin-agents-dir",
    "--corpus",
    "--project-root",
]


class TestCatalogBuildHelp:
    """``catalog build --help`` must exit 0 and surface all expected flags."""

    @pytest.fixture(scope="class")
    def help_output(self) -> str:
        """Run ``catalog build --help`` once and return stdout for the class.

        Returns:
            The captured stdout of the help invocation.
        """
        result = _run("catalog", "build", "--help")
        assert result.returncode == 0, (
            f"catalog build --help exited {result.returncode}.\n"
            f"stderr: {result.stderr}"
        )
        return result.stdout

    def test_help_exits_zero(self) -> None:
        """``catalog build --help`` must exit with code 0."""
        result = _run("catalog", "build", "--help")
        assert result.returncode == 0, (
            f"catalog build --help exited {result.returncode}.\n"
            f"stderr: {result.stderr}"
        )

    @pytest.mark.parametrize("flag", _EXPECTED_FLAGS)
    def test_flag_in_help(self, flag: str, help_output: str) -> None:
        """Every expected flag must appear in the help text.

        Args:
            flag: The flag name to look for (e.g. ``--skills-dir``).
            help_output: Captured stdout from the help invocation.
        """
        assert flag in help_output, (
            f"Expected flag '{flag}' not found in catalog build --help output.\n"
            f"Full output:\n{help_output}"
        )


# ---------------------------------------------------------------------------
# (b) End-to-end smoke test
# ---------------------------------------------------------------------------


class TestCatalogBuildSmoke:
    """``catalog build`` on fixture dirs must produce a valid catalog."""

    def test_catalog_build_produces_json(self, tmp_path: Path) -> None:
        """Running catalog build against fixtures creates dispatch-catalog.json.

        Args:
            tmp_path: Pytest-provided temporary directory for output files.
        """
        out_path = tmp_path / "dispatch-catalog.json"
        log_path = tmp_path / "catalog-build.log"

        result = _run(
            "catalog",
            "build",
            "--skills-dir",
            str(_SKILLS_DIR),
            "--agents-dir",
            str(_AGENTS_DIR),
            "--out",
            str(out_path),
            "--log",
            str(log_path),
        )

        # Exit 0 (clean build) or 2 (degraded but completed) are both
        # acceptable here; either means build ran to completion.
        assert result.returncode in (0, 2), (
            f"catalog build exited {result.returncode} (expected 0 or 2).\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )
        assert out_path.exists(), (
            f"dispatch-catalog.json was not created at {out_path}.\n"
            f"stderr: {result.stderr}"
        )

    def test_catalog_output_is_valid_json(self, tmp_path: Path) -> None:
        """The produced dispatch-catalog.json must be valid JSON.

        Args:
            tmp_path: Pytest-provided temporary directory for output files.
        """
        out_path = tmp_path / "dispatch-catalog.json"
        log_path = tmp_path / "catalog-build.log"

        _run(
            "catalog",
            "build",
            "--skills-dir",
            str(_SKILLS_DIR),
            "--agents-dir",
            str(_AGENTS_DIR),
            "--out",
            str(out_path),
            "--log",
            str(log_path),
        )

        assert out_path.exists(), "Output file was not created."
        catalog = json.loads(out_path.read_text(encoding="utf-8"))
        assert isinstance(catalog, dict), "Catalog top level must be a JSON object."

    def test_catalog_has_entries_key(self, tmp_path: Path) -> None:
        """The produced catalog must contain a top-level ``entries`` key.

        Args:
            tmp_path: Pytest-provided temporary directory for output files.
        """
        out_path = tmp_path / "dispatch-catalog.json"
        log_path = tmp_path / "catalog-build.log"

        _run(
            "catalog",
            "build",
            "--skills-dir",
            str(_SKILLS_DIR),
            "--agents-dir",
            str(_AGENTS_DIR),
            "--out",
            str(out_path),
            "--log",
            str(log_path),
        )

        catalog = json.loads(out_path.read_text(encoding="utf-8"))
        assert "entries" in catalog, (
            f"Catalog missing 'entries' key. Keys found: {list(catalog.keys())}"
        )

    def test_demo_subcommand_still_works_after_catalog_added(self) -> None:
        """The existing ``demo`` subcommand must be unaffected by the new subparser."""
        result = _run("demo")
        assert result.returncode == 0, (
            f"demo exited {result.returncode} after catalog subparser was added.\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )
        assert result.stdout.strip(), "demo produced no stdout output."
