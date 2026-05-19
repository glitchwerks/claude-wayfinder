"""Catalog-wide static analysis for the dispatch catalog.

Implements the ``python -m claude_wayfinder audit-catalog`` subcommand.

The module is structured as three layers:

1. ``Finding`` / ``Severity`` — the data model for one issue.
2. ``RULES`` — a registry of pure rule functions, each taking the parsed
   catalog and returning a list of Findings.  Rules are added one per
   subsequent commit in this feature branch.
3. ``run_audit()`` — top-level entry that loads a catalog and applies
   every registered rule.

The CLI shim in ``cli.py`` calls into ``run_audit_cli()`` defined here.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Callable, Iterable

from claude_wayfinder.match import CatalogEntry

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


class Severity(enum.Enum):
    """Audit finding severity.

    Each member's value is the exit code the CLI should return when the
    highest-severity finding is at that level (0 reserved for "no
    findings").  Higher numeric value = more severe.
    """

    NIT = 1
    CONCERN = 2
    BLOCKING = 3

    @property
    def exit_code(self) -> int:
        """Return the CLI exit code corresponding to this severity level.

        Returns:
            Integer exit code — NIT=1, CONCERN=2, BLOCKING=3.
        """
        return self.value


@dataclass(frozen=True)
class Finding:
    """One audit finding.

    Attributes:
        severity: BLOCKING / CONCERN / NIT.
        rule: Stable rule identifier (kebab-case).
        entry: Catalog entry name the finding applies to, or "" for
            catalog-wide findings.
        message: Human-readable description.
    """

    severity: Severity
    rule: str
    entry: str
    message: str


# A rule function takes the full catalog and returns 0+ findings.
RuleFn = Callable[[list[CatalogEntry]], list[Finding]]

# Registry — populated by later tasks via @register.
RULES: list[RuleFn] = []


def register(fn: RuleFn) -> RuleFn:
    """Add a rule function to the global RULES registry.

    Intended for use as a decorator on rule functions defined in this
    module or imported sub-modules.  Each registered rule is called by
    ``run_audit()`` in registration order.

    Args:
        fn: A callable that accepts ``list[CatalogEntry]`` and returns
            ``list[Finding]``.

    Returns:
        The original function unchanged (decorator protocol).
    """
    RULES.append(fn)
    return fn


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run_audit(entries: Iterable[CatalogEntry]) -> list[Finding]:
    """Apply every registered rule to ``entries`` and return all findings.

    Iterates through ``RULES`` in registration order and concatenates
    each rule's output into a single flat list.  With no rules registered
    (the initial scaffold state), always returns an empty list.

    Args:
        entries: Parsed catalog entries (typically from ``load_catalog``).

    Returns:
        A flat list of findings, order-stable for a given catalog.
    """
    catalog = list(entries)
    findings: list[Finding] = []
    for rule in RULES:
        findings.extend(rule(catalog))
    return findings
