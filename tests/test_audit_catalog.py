"""Tests for the ``python -m claude_wayfinder audit-catalog`` subcommand.

Covers:
  - The Finding dataclass and Severity enum.
  - The run_audit() entry point on an empty catalog.
  - Per-rule unit tests (added incrementally by later tasks).
  - End-to-end CLI smoke tests via subprocess.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

# ---------------------------------------------------------------------------
# Module-level imports under test
# ---------------------------------------------------------------------------
from claude_wayfinder.audit_catalog import (
    Finding,
    Severity,
    rule_weight_not_in_ladder,
    run_audit,
)
from claude_wayfinder.match import CatalogEntry, Keyword, Triggers

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _empty_triggers() -> Triggers:
    """Return a Triggers instance with all collections empty."""
    return Triggers(
        command_prefixes=frozenset(),
        agent_mentions=frozenset(),
        path_globs=tuple(),
        keywords=tuple(),
        tool_mentions=frozenset(),
        excludes=frozenset(),
    )


def _entry(name: str, **overrides) -> CatalogEntry:
    """Build a CatalogEntry with sensible defaults for testing.

    Args:
        name: Entry name (e.g. ``"code-writer"``).
        **overrides: Any ``CatalogEntry`` field to override.

    Returns:
        A ``CatalogEntry`` instance suitable for use in audit tests.
    """
    defaults: dict = {
        "name": name,
        "kind": "agent",
        "triggers": _empty_triggers(),
        "applicable_agents": tuple(),
        "applicable_skills": tuple(),
        "source": "owned",
        "routable": True,
    }
    defaults.update(overrides)
    return CatalogEntry(**defaults)


# ---------------------------------------------------------------------------
# Scaffold tests
# ---------------------------------------------------------------------------


class TestFindingDataclass:
    """The Finding type carries severity, rule id, entry name, and message."""

    def test_finding_has_required_fields(self) -> None:
        """Finding stores all four required fields and they are accessible."""
        f = Finding(
            severity=Severity.BLOCKING,
            rule="weight-not-in-ladder",
            entry="example",
            message="weight 0.7 not in {0.25, 0.5, 1.0}",
        )
        assert f.severity == Severity.BLOCKING
        assert f.rule == "weight-not-in-ladder"
        assert f.entry == "example"
        assert "0.7" in f.message


class TestSeverityOrdering:
    """Severity members have exit codes so BLOCKING > CONCERN > NIT."""

    def test_severity_ordering(self) -> None:
        """Each Severity member exposes an exit_code property aligned with spec."""
        assert Severity.BLOCKING.exit_code == 3
        assert Severity.CONCERN.exit_code == 2
        assert Severity.NIT.exit_code == 1


class TestRunAuditEmpty:
    """run_audit() on an empty catalog returns no findings."""

    def test_empty_catalog_no_findings(self) -> None:
        """An empty catalog list should produce zero findings."""
        findings = run_audit([])
        assert findings == []


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    """Invoke ``python -m claude_wayfinder`` with the given arguments.

    Args:
        *args: CLI arguments appended after the module name.

    Returns:
        A ``CompletedProcess`` instance with captured stdout/stderr.
    """
    return subprocess.run(
        [sys.executable, "-m", "claude_wayfinder", *args],
        capture_output=True,
        text=True,
    )


class TestAuditCatalogCliHelp:
    """audit-catalog --help exits 0 and surfaces the documented flags."""

    @pytest.fixture(scope="class")
    def help_output(self) -> str:
        """Run audit-catalog --help and return stdout.

        Returns:
            The help text emitted to stdout.
        """
        cp = _run_cli("audit-catalog", "--help")
        assert cp.returncode == 0, cp.stderr
        return cp.stdout

    def test_help_lists_json_flag(self, help_output: str) -> None:
        """--json flag appears in audit-catalog help text."""
        assert "--json" in help_output

    def test_help_lists_severity_flag(self, help_output: str) -> None:
        """--severity flag appears in audit-catalog help text."""
        assert "--severity" in help_output

    def test_help_lists_target_flag(self, help_output: str) -> None:
        """--target flag appears in audit-catalog help text."""
        assert "--target" in help_output

    def test_help_lists_catalog_flag(self, help_output: str) -> None:
        """--catalog flag appears in audit-catalog help text."""
        assert "--catalog" in help_output


# ---------------------------------------------------------------------------
# Task 7 — rule_weight_not_in_ladder (BLOCKING)
# ---------------------------------------------------------------------------


class TestWeightNotInLadder:
    """BLOCKING: keyword weight outside {0.25, 0.5, 1.0}."""

    def test_clean_catalog_no_finding(self) -> None:
        e = _entry(
            "ok",
            triggers=Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=tuple(),
                keywords=(Keyword("foo", 1.0), Keyword("bar", 0.5)),
                tool_mentions=frozenset(),
                excludes=frozenset(),
            ),
        )
        assert rule_weight_not_in_ladder([e]) == []

    def test_off_ladder_weight_flagged(self) -> None:
        e = _entry(
            "bad",
            triggers=Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=tuple(),
                keywords=(Keyword("foo", 0.7),),
                tool_mentions=frozenset(),
                excludes=frozenset(),
            ),
        )
        findings = rule_weight_not_in_ladder([e])
        assert len(findings) == 1
        assert findings[0].severity == Severity.BLOCKING
        assert findings[0].entry == "bad"
        assert "0.7" in findings[0].message
