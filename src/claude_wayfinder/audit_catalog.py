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

import argparse
import enum
import json as _json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from claude_wayfinder.match import CatalogEntry, load_catalog

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


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


def add_audit_catalog_args(parser: argparse.ArgumentParser) -> None:
    """Register audit-catalog flags on ``parser``.

    Args:
        parser: The subcommand ``ArgumentParser`` to populate with flags.
    """
    parser.add_argument(
        "--catalog",
        type=Path,
        default=None,
        help=(
            "Path to the dispatch catalog JSON to audit. "
            "Defaults to $DISPATCH_CATALOG_PATH or "
            "~/.claude/state/dispatch-catalog.json."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of the text report.",
    )
    parser.add_argument(
        "--severity",
        choices=("blocking", "concern", "nit"),
        default=None,
        help=(
            "Filter findings to this severity level and worse. "
            "Default: show all findings."
        ),
    )
    parser.add_argument(
        "--target",
        default=None,
        help=(
            "Restrict findings to entries whose label contains this "
            "substring. Per-entry findings match against the entry name; "
            "catalog-wide findings (e.g. conflict-pair entries formatted "
            "as 'alpha <-> beta') match when either side of the pair "
            "label contains the substring -- so '--target alpha' "
            "surfaces pairs involving alpha. Default: no filter."
        ),
    )


def _resolve_catalog_path(arg: Path | None) -> Path:
    """Resolve the catalog path from the CLI arg, env var, or default.

    Resolution order:
    1. ``arg`` when not ``None``.
    2. ``$DISPATCH_CATALOG_PATH`` env var.
    3. ``~/.claude/state/dispatch-catalog.json``.

    Args:
        arg: Value passed via ``--catalog``, or ``None``.

    Returns:
        The resolved catalog ``Path``.
    """
    if arg is not None:
        return arg
    env = os.environ.get("DISPATCH_CATALOG_PATH")
    if env:
        return Path(env)
    return Path.home() / ".claude" / "state" / "dispatch-catalog.json"


def run_audit_cli(args: argparse.Namespace) -> int:
    """CLI entry point for ``audit-catalog``.

    Loads the catalog, runs all registered rules, and prints findings.
    Rendering, severity filtering, and exit-code mapping land in later
    tasks (Tasks 16-17).  For now, one line per finding is emitted and
    the command exits 0.

    Args:
        args: Parsed CLI arguments from :func:`add_audit_catalog_args`.

    Returns:
        Exit code: 0 on success, 1 on catalog load error.
    """
    catalog_path = _resolve_catalog_path(getattr(args, "catalog", None))
    try:
        entries = load_catalog(catalog_path)
    except (FileNotFoundError, _json.JSONDecodeError, ValueError) as exc:
        print(
            f"[AUDIT ERROR] Failed to load catalog: {exc}",
            file=sys.stderr,
        )
        return 1
    findings = run_audit(entries)
    # Rendering + severity filter + exit-code mapping land in Tasks 16-17.
    # For now: print one line per finding and exit 0.
    for f in findings:
        print(
            f"{f.severity.name:<8}  [{f.rule}]  {f.entry}: {f.message}"
        )
    return 0


# ---------------------------------------------------------------------------
# Rule: weight not in ladder (BLOCKING)
# ---------------------------------------------------------------------------

_LADDER: frozenset[float] = frozenset({0.25, 0.5, 1.0})


@register
def rule_weight_not_in_ladder(catalog: list[CatalogEntry]) -> list[Finding]:
    """Flag any keyword whose weight is not in {0.25, 0.5, 1.0}."""
    out: list[Finding] = []
    for e in catalog:
        for kw in e.triggers.keywords:
            if kw.weight not in _LADDER:
                out.append(
                    Finding(
                        severity=Severity.BLOCKING,
                        rule="weight-not-in-ladder",
                        entry=e.name,
                        message=(
                            f"keyword '{kw.term}' weight {kw.weight} "
                            f"not in {{0.25, 0.5, 1.0}}"
                        ),
                    )
                )
    return out
