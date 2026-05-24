"""Catalog-wide static analysis for the dispatch catalog.

Implements the ``python -m claude_wayfinder audit-catalog`` subcommand.

The module is structured as three layers:

1. ``Finding`` / ``Severity`` — the data model for one issue.
2. ``RULES`` — a registry of pure rule functions, each taking the parsed
   catalog and returning a list of Findings.  Rules are added one per
   subsequent commit in this feature branch.
3. ``OVERRIDE_RULES`` — a registry of override-aware rule functions,
   each taking the parsed catalog AND a list of OverrideRule objects.
   These are the BLOCKING-2 fix: the original ``RuleFn`` signature
   cannot receive override rules.
4. ``run_audit()`` — top-level entry that loads a catalog and applies
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

from claude_wayfinder._trigger_validators import (
    count_trigger_dimensions,
    has_whitespace,
    is_weight_in_ladder,
)
from claude_wayfinder.match import CatalogEntry, load_catalog
from claude_wayfinder.match._overrides import OverridesError, load_overrides
from claude_wayfinder.match._types import OverrideRule

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


# An override-aware rule function takes both the catalog AND the
# loaded override rules and returns 0+ findings.  This is the
# BLOCKING-2 fix: ``RuleFn`` cannot accept override input.
OverrideRuleFn = Callable[
    [list[CatalogEntry], list[OverrideRule]],
    list[Finding],
]

# Registry — populated via @register_override.
OVERRIDE_RULES: list[OverrideRuleFn] = []


def register_override(fn: OverrideRuleFn) -> OverrideRuleFn:
    """Register a rule that audits both catalog entries and override rules.

    Intended for use as a decorator on override-aware rule functions.
    Each registered function is called by ``run_audit()`` when override
    rules are supplied.

    Args:
        fn: A callable that accepts ``list[CatalogEntry]`` and
            ``list[OverrideRule]`` and returns ``list[Finding]``.

    Returns:
        The original function unchanged (decorator protocol).
    """
    OVERRIDE_RULES.append(fn)
    return fn


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run_audit(
    entries: Iterable[CatalogEntry],
    override_rules: list[OverrideRule] | None = None,
) -> list[Finding]:
    """Apply every registered rule to ``entries`` and return all findings.

    Iterates through ``RULES`` in registration order and concatenates
    each rule's output into a single flat list.  When ``override_rules``
    is supplied (non-None and non-empty), also applies every rule in
    ``OVERRIDE_RULES``.

    Backwards compatible: callers that pass only ``entries`` continue
    to work; the override branch is skipped entirely when
    ``override_rules`` is ``None``.

    Args:
        entries: Parsed catalog entries (typically from ``load_catalog``).
        override_rules: Parsed override rules, or ``None`` to skip
            override-aware audit rules.

    Returns:
        A flat list of findings, order-stable for a given catalog.
    """
    catalog = list(entries)
    findings: list[Finding] = []
    for rule in RULES:
        findings.extend(rule(catalog))
    if override_rules:
        for orule_fn in OVERRIDE_RULES:
            findings.extend(orule_fn(catalog, override_rules))
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
    parser.add_argument(
        "--overrides-path",
        type=Path,
        default=None,
        help=(
            "Optional path to a dispatch overrides JSON file to audit "
            "alongside the catalog."
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


_SEVERITY_FROM_FLAG: dict[str, Severity] = {
    "blocking": Severity.BLOCKING,
    "concern": Severity.CONCERN,
    "nit": Severity.NIT,
}


def _filter_by_severity(
    findings: list[Finding],
    threshold: Severity | None,
) -> list[Finding]:
    """Return only findings at or above ``threshold`` severity.

    Args:
        findings: The full finding list to filter.
        threshold: Minimum severity to retain, or ``None`` to keep all.

    Returns:
        Filtered list; order preserved.
    """
    if threshold is None:
        return findings
    return [f for f in findings if f.severity.value >= threshold.value]


def _filter_by_target(
    findings: list[Finding],
    target: str | None,
) -> list[Finding]:
    """Return only findings whose ``entry`` field contains ``target``.

    Args:
        findings: The full finding list to filter.
        target: Substring to match against each finding's ``entry``,
            or ``None`` to keep all.

    Returns:
        Filtered list; order preserved.
    """
    if target is None:
        return findings
    return [f for f in findings if target in f.entry]


def _exit_code_for(findings: list[Finding]) -> int:
    """Compute the CLI exit code for a finding set.

    Args:
        findings: The (already filtered) list of findings to score.

    Returns:
        0 when no findings; otherwise the maximum ``Severity.value``
        present (1 = NIT, 2 = CONCERN, 3 = BLOCKING).
    """
    if not findings:
        return 0
    return max(f.severity.value for f in findings)


def _emit_text(findings: list[Finding]) -> None:
    """Print a grouped human-readable text report to stdout.

    Findings are grouped by severity (BLOCKING first, then CONCERN, NIT)
    with a header and bullet per group.

    Args:
        findings: The (already filtered) list of findings to display.
    """
    if not findings:
        print("audit-catalog: no findings.")
        return
    for sev in (Severity.BLOCKING, Severity.CONCERN, Severity.NIT):
        bucket = [f for f in findings if f.severity == sev]
        if not bucket:
            continue
        print(f"\n## {sev.name} ({len(bucket)})\n")
        for f in bucket:
            print(f"- [{f.rule}] {f.entry}: {f.message}")


def _emit_json(findings: list[Finding]) -> None:
    """Print findings as a JSON array to stdout.

    Each element is an object with keys ``severity``, ``rule``,
    ``entry``, and ``message``.

    Args:
        findings: The (already filtered) list of findings to serialize.
    """
    payload = [
        {
            "severity": f.severity.name,
            "rule": f.rule,
            "entry": f.entry,
            "message": f.message,
        }
        for f in findings
    ]
    print(_json.dumps(payload, indent=2))


def run_audit_cli(args: argparse.Namespace) -> int:
    """CLI entry point for ``audit-catalog``.

    Loads the catalog, runs all registered rules, applies severity and
    target filters, renders output (text or JSON), and returns an exit
    code derived from the highest-severity finding in the filtered set.

    Exit codes: 0 = no findings, 1 = NIT, 2 = CONCERN, 3 = BLOCKING.

    Args:
        args: Parsed CLI arguments from :func:`add_audit_catalog_args`.

    Returns:
        Exit code derived from filtered findings (0-3), or 1 on load
        error.
    """
    # Windows default stdout encoding is cp1252, which crashes on the
    # `↔` (U+2194) character used in conflict-pair entry labels and
    # other non-ASCII glyphs that may appear in rule messages. Force
    # UTF-8 with replacement so the audit never crashes on rendering.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                # Stream may be a non-reconfigurable wrapper (e.g. in
                # test capture); fall through — the print() call below
                # may still succeed for ASCII-only findings.
                pass

    catalog_path = _resolve_catalog_path(getattr(args, "catalog", None))
    try:
        entries = load_catalog(catalog_path)
    except (FileNotFoundError, _json.JSONDecodeError, ValueError) as exc:
        print(
            f"[AUDIT ERROR] Failed to load catalog: {exc}",
            file=sys.stderr,
        )
        return 1

    load_error_findings: list[Finding] = []
    override_rules: list[OverrideRule] = []
    overrides_path = getattr(args, "overrides_path", None)
    if overrides_path is not None:
        try:
            override_rules = load_overrides(overrides_path)
        except OverridesError as exc:
            load_error_findings.append(
                Finding(
                    Severity.BLOCKING,
                    "override-load-error",
                    str(overrides_path),
                    str(exc),
                )
            )

    findings = load_error_findings + run_audit(entries, override_rules)

    sev_flag = getattr(args, "severity", None)
    threshold = _SEVERITY_FROM_FLAG.get(sev_flag) if sev_flag else None
    findings = _filter_by_severity(findings, threshold)
    findings = _filter_by_target(findings, getattr(args, "target", None))

    if getattr(args, "json", False):
        _emit_json(findings)
    else:
        _emit_text(findings)

    return _exit_code_for(findings)


# ---------------------------------------------------------------------------
# Rule: weight not in ladder (BLOCKING)
# ---------------------------------------------------------------------------

@register
def rule_weight_not_in_ladder(catalog: list[CatalogEntry]) -> list[Finding]:
    """Flag any keyword whose weight is not in {0.25, 0.5, 1.0}."""
    out: list[Finding] = []
    for e in catalog:
        for kw in e.triggers.keywords:
            if not is_weight_in_ladder(kw.weight):
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


@register
def rule_whitespace_in_term(catalog: list[CatalogEntry]) -> list[Finding]:
    """Flag keyword terms containing any whitespace character."""
    out: list[Finding] = []
    for e in catalog:
        for kw in e.triggers.keywords:
            if has_whitespace(kw.term):
                out.append(
                    Finding(
                        severity=Severity.BLOCKING,
                        rule="whitespace-in-term",
                        entry=e.name,
                        message=(
                            f"keyword term '{kw.term}' contains "
                            "whitespace; matcher only operates on single tokens"
                        ),
                    )
                )
    return out


@register
def rule_duplicate_keyword_terms(catalog: list[CatalogEntry]) -> list[Finding]:
    """Flag duplicate keyword terms within a single entry."""
    out: list[Finding] = []
    for e in catalog:
        seen: dict[str, int] = {}
        for kw in e.triggers.keywords:
            seen[kw.term] = seen.get(kw.term, 0) + 1
        for term, count in seen.items():
            if count > 1:
                out.append(
                    Finding(
                        severity=Severity.BLOCKING,
                        rule="duplicate-keyword-term",
                        entry=e.name,
                        message=(
                            f"keyword term '{term}' appears {count} times"
                        ),
                    )
                )
    return out


# ---------------------------------------------------------------------------
# Rule: path-glob footgun (CONCERN)
# ---------------------------------------------------------------------------

import re as _re  # noqa: E402

# Matches `*.<ext>` with a single alphanumeric extension component only.
# Does NOT match compound extensions like `*.tar.gz` or `*.min.js` —
# those are uncommon enough in dispatch globs that the false-negative
# is acceptable, and the rule body's "add a `**/*.<ext>` sibling"
# suggestion would be wrong for them anyway (the correct sibling for
# `*.tar.gz` is `**/*.tar.gz`, not `**/*.gz`). Future maintainers:
# extend this regex to compound extensions only if you also extend
# the sibling-suggestion logic in the rule body.
_BARE_EXT_RE = _re.compile(r"^\*\.[A-Za-z0-9]+$")


@register
def rule_path_glob_footgun(catalog: list[CatalogEntry]) -> list[Finding]:
    """Flag bare `*.<ext>` path-globs missing a `**/*.<ext>` sibling."""
    out: list[Finding] = []
    for e in catalog:
        globs = set(e.triggers.path_globs)
        for g in e.triggers.path_globs:
            if _BARE_EXT_RE.match(g):
                ext = g[2:]  # strip "*."
                if f"**/*.{ext}" not in globs:
                    out.append(
                        Finding(
                            severity=Severity.CONCERN,
                            rule="path-glob-footgun",
                            entry=e.name,
                            message=(
                                f"path_glob '{g}' matches only top-level "
                                f"files under fnmatch; use '**/*.{ext}' "
                                "for nested matching or add it as a sibling"
                            ),
                        )
                    )
    return out


# ---------------------------------------------------------------------------
# Rule: tool-name case error (CONCERN)
# ---------------------------------------------------------------------------

# Canonical case-correct names for tools the matcher recognises.
# Extend cautiously — adding a name here can flag previously-clean
# catalogs.
_CANONICAL_TOOLS: tuple[str, ...] = (
    "Agent",
    "Bash",
    "Edit",
    "Glob",
    "Grep",
    "Monitor",
    "NotebookEdit",
    "Read",
    "Skill",
    "TaskCreate",
    "ToolSearch",
    "WebFetch",
    "WebSearch",
    "Write",
)
_CANONICAL_TOOLS_LOWER: dict[str, str] = {
    t.lower(): t for t in _CANONICAL_TOOLS
}


@register
def rule_tool_name_case_error(catalog: list[CatalogEntry]) -> list[Finding]:
    """Flag tool_mentions matching a known tool but with wrong case."""
    out: list[Finding] = []
    for e in catalog:
        for tm in sorted(e.triggers.tool_mentions):
            canonical = _CANONICAL_TOOLS_LOWER.get(tm.lower())
            if canonical is not None and canonical != tm:
                out.append(
                    Finding(
                        severity=Severity.CONCERN,
                        rule="tool-name-case-error",
                        entry=e.name,
                        message=(
                            f"tool_mention '{tm}' is case-incorrect; "
                            f"matcher expects '{canonical}'"
                        ),
                    )
                )
    return out


# ---------------------------------------------------------------------------
# Rule: one-dimensional triggers (CONCERN)
# ---------------------------------------------------------------------------


@register
def rule_one_dimensional_triggers(
    catalog: list[CatalogEntry],
) -> list[Finding]:
    """Flag routable agents that populate only one trigger dimension."""
    out: list[Finding] = []
    for e in catalog:
        if e.kind != "agent" or not e.routable:
            continue
        dims = count_trigger_dimensions(e.triggers)
        if dims == 1:
            out.append(
                Finding(
                    severity=Severity.CONCERN,
                    rule="one-dimensional-triggers",
                    entry=e.name,
                    message=(
                        "routable agent populates only one trigger "
                        "dimension; matcher's feature-density floor "
                        "requires two — agent may be unreachable"
                    ),
                )
            )
    return out


# ---------------------------------------------------------------------------
# Rule: unreachable routable (CONCERN)
# ---------------------------------------------------------------------------


@register
def rule_unreachable_routable(catalog: list[CatalogEntry]) -> list[Finding]:
    """Flag routable agents with zero positive trigger dimensions."""
    out: list[Finding] = []
    for e in catalog:
        if e.kind != "agent" or not e.routable:
            continue
        if count_trigger_dimensions(e.triggers) == 0:
            out.append(
                Finding(
                    severity=Severity.CONCERN,
                    rule="unreachable-routable",
                    entry=e.name,
                    message=(
                        "routable agent has no positive triggers; "
                        "matcher will never produce delegate for it"
                    ),
                )
            )
    return out


# ---------------------------------------------------------------------------
# Rule: conflict pairs (CONCERN)
# ---------------------------------------------------------------------------

# Discriminator fields are those the matcher uses to break ties beyond
# the shared keyword overlap. A discriminator only reliably breaks a
# tie when one agent is *more specific* on that dimension and the other
# is *unscoped* — i.e., one agent's set is empty while the other's is
# not. Two non-empty disjoint sets do NOT break the tie across the
# typical prompt distribution: on prompts that fill neither agent's
# dimension (the common case for path_globs, since most prompts have
# no file paths), both agents score identically on keywords and the
# matcher produces an ambiguous decision.
_DISCRIMINATOR_FIELDS: tuple[str, ...] = (
    "command_prefixes",
    "tool_mentions",
    "path_globs",
)


def _discriminator_sets(t) -> dict[str, frozenset[str]]:
    """Per-field discriminator sets for a Triggers object."""
    return {
        "command_prefixes": frozenset(t.command_prefixes),
        "tool_mentions": frozenset(t.tool_mentions),
        "path_globs": frozenset(t.path_globs),
    }


def _has_breaking_discriminator(
    a_sets: dict[str, frozenset[str]],
    b_sets: dict[str, frozenset[str]],
) -> bool:
    """True iff some discriminator field is single-sided-asymmetric.

    Single-sided-asymmetric on field F means exactly one of (a[F], b[F])
    is empty and the other is non-empty. That is the only case where the
    discriminator reliably breaks the tie across the prompt distribution
    — the unscoped agent loses to the scoped agent when the input fills
    the field, and they tie on overlapping keywords when it doesn't, so
    the *scored-not-tied* subspace strictly favours one agent.

    Two non-empty disjoint sets (e.g. ``**/*.py`` vs ``**/*.ts``) do NOT
    qualify — on the common case of prompts with no file paths, neither
    agent's path_globs fire, both score identically on keywords, and the
    matcher emits ambiguous.
    """
    for field in _DISCRIMINATOR_FIELDS:
        a_empty = not a_sets[field]
        b_empty = not b_sets[field]
        if a_empty != b_empty:
            # Exactly one is empty → single-sided asymmetry.
            return True
    return False


@register
def rule_conflict_pairs(catalog: list[CatalogEntry]) -> list[Finding]:
    """Flag pairs of routable agents with heavy keyword overlap & no breaking discriminator."""
    routable_agents = [
        e for e in catalog if e.kind == "agent" and e.routable
    ]
    out: list[Finding] = []
    for i, a in enumerate(routable_agents):
        a_terms = {kw.term.lower() for kw in a.triggers.keywords}
        a_sets = _discriminator_sets(a.triggers)
        for b in routable_agents[i + 1:]:
            b_terms = {kw.term.lower() for kw in b.triggers.keywords}
            overlap = a_terms & b_terms
            if len(overlap) < 3:
                continue
            b_sets = _discriminator_sets(b.triggers)
            # Only single-sided-asymmetric discriminators reliably break
            # the tie. Disjoint non-empty sets (two specialists in
            # different domains) still tie on keyword-only prompts.
            if _has_breaking_discriminator(a_sets, b_sets):
                continue
            out.append(
                Finding(
                    severity=Severity.CONCERN,
                    rule="conflict-pair",
                    entry=f"{a.name} ↔ {b.name}",
                    message=(
                        f"agents '{a.name}' and '{b.name}' share "
                        f"{len(overlap)} keywords ({sorted(overlap)}) "
                        "with no discriminating path_globs/tool_mentions/"
                        "command_prefixes — matcher will produce ambiguous"
                    ),
                )
            )
    return out


# ---------------------------------------------------------------------------
# Rule: excludes overlap own keywords (CONCERN)
# ---------------------------------------------------------------------------


@register
def rule_excludes_overlap_own_keywords(
    catalog: list[CatalogEntry],
) -> list[Finding]:
    """Flag entries whose excludes set overlaps their own keyword terms."""
    out: list[Finding] = []
    for e in catalog:
        own_terms = {kw.term.lower() for kw in e.triggers.keywords}
        ex = {x.lower() for x in e.triggers.excludes}
        overlap = own_terms & ex
        if overlap:
            out.append(
                Finding(
                    severity=Severity.CONCERN,
                    rule="excludes-overlap-own-keywords",
                    entry=e.name,
                    message=(
                        f"excludes overlap own keywords {sorted(overlap)}"
                        " — entry self-zeros when those terms appear"
                    ),
                )
            )
    return out


# ---------------------------------------------------------------------------
# Rule: source-routable mismatch (CONCERN)
# ---------------------------------------------------------------------------


@register
def rule_source_routable_mismatch(
    catalog: list[CatalogEntry],
) -> list[Finding]:
    """Flag plugin-sourced agents marked routable=True."""
    out: list[Finding] = []
    for e in catalog:
        if e.kind == "agent" and e.source == "plugin" and e.routable:
            out.append(
                Finding(
                    severity=Severity.CONCERN,
                    rule="source-routable-mismatch",
                    entry=e.name,
                    message=(
                        "plugin-sourced agent marked routable=true; "
                        "plugin agents are advisory by default"
                    ),
                )
            )
    return out


# ---------------------------------------------------------------------------
# Rule: empty applicable_agents (NIT)
# ---------------------------------------------------------------------------


@register
def rule_empty_applicable_agents(
    catalog: list[CatalogEntry],
) -> list[Finding]:
    """Flag skills with empty applicable_agents.

    Suppressed when ``applicable_agents_intentional`` is a non-empty
    string documenting why the empty list is deliberate (e.g. for
    router-only interactive skills where ``["*"]`` would be wrong).
    """
    out: list[Finding] = []
    for e in catalog:
        if e.kind == "skill" and e.applicable_agents == tuple():
            if e.applicable_agents_intentional:
                continue
            out.append(
                Finding(
                    severity=Severity.NIT,
                    rule="empty-applicable-agents",
                    entry=e.name,
                    message=(
                        "skill has empty applicable_agents — set "
                        '["*"] for any-agent or list specific agents'
                    ),
                )
            )
    return out


# ---------------------------------------------------------------------------
# Rule: duplicate trigger set (NIT)
# ---------------------------------------------------------------------------


def _trigger_fingerprint(t) -> tuple:
    """Compute a hashable fingerprint of a Triggers object."""
    return (
        frozenset(t.command_prefixes),
        frozenset(t.agent_mentions),
        tuple(sorted(t.path_globs)),
        tuple(sorted((kw.term, kw.weight) for kw in t.keywords)),
        frozenset(t.tool_mentions),
        frozenset(t.excludes),
    )


@register
def rule_duplicate_trigger_set(
    catalog: list[CatalogEntry],
) -> list[Finding]:
    """Flag agent groups with identical trigger sets but different skills."""
    out: list[Finding] = []
    by_fp: dict[tuple, list[CatalogEntry]] = {}
    for e in catalog:
        if e.kind != "agent":
            continue
        by_fp.setdefault(_trigger_fingerprint(e.triggers), []).append(e)
    for fp, group in by_fp.items():
        if len(group) < 2:
            continue
        # Only flag if any pair has different applicable_skills.
        skill_sets = {tuple(sorted(e.applicable_skills)) for e in group}
        if len(skill_sets) > 1:
            names = sorted(e.name for e in group)
            out.append(
                Finding(
                    severity=Severity.NIT,
                    rule="duplicate-trigger-set",
                    entry=", ".join(names),
                    message=(
                        f"agents {names} share identical trigger sets "
                        "but differ in applicable_skills — likely a "
                        "copy-paste"
                    ),
                )
            )
    return out


# ---------------------------------------------------------------------------
# Override-aware rules (BLOCKING-2 fix)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Rule: override-zero-predicates (BLOCKING)
# ---------------------------------------------------------------------------


@register_override
def rule_override_zero_predicates(
    catalog: list[CatalogEntry],
    override_rules: list[OverrideRule],
) -> list[Finding]:
    """Flag OverrideRules with no predicate set.

    A rule with no ``command_prefix``, ``path_globs``, or
    ``tool_mentions`` would match every context — a silent traffic-killer.

    Args:
        catalog: Parsed catalog entries (unused but required by protocol).
        override_rules: Override rules to audit.

    Returns:
        One BLOCKING finding per zero-predicate rule.
    """
    out: list[Finding] = []
    for orule in override_rules:
        has_cp = orule.command_prefix is not None
        has_pg = bool(orule.path_globs)
        has_tm = bool(orule.tool_mentions)
        if not (has_cp or has_pg or has_tm):
            out.append(
                Finding(
                    severity=Severity.BLOCKING,
                    rule="override-zero-predicates",
                    entry=orule.id,
                    message=(
                        f"override rule '{orule.id}' has no predicates "
                        "(command_prefix, path_globs, tool_mentions all "
                        "empty) — would match every dispatch context"
                    ),
                )
            )
    return out


# ---------------------------------------------------------------------------
# Rule: override-unknown-skill (CONCERN)
# ---------------------------------------------------------------------------


@register_override
def rule_override_unknown_skill(
    catalog: list[CatalogEntry],
    override_rules: list[OverrideRule],
) -> list[Finding]:
    """Flag OverrideRules that name skills absent from the catalog.

    One Finding per (rule_id, unknown_skill) pair.

    Args:
        catalog: Parsed catalog entries.
        override_rules: Override rules to audit.

    Returns:
        CONCERN findings for each unknown skill reference.
    """
    known_skills: frozenset[str] = frozenset(
        e.name for e in catalog if e.kind == "skill"
    )
    out: list[Finding] = []
    for orule in override_rules:
        for skill in orule.skills:
            if skill not in known_skills:
                out.append(
                    Finding(
                        severity=Severity.CONCERN,
                        rule="override-unknown-skill",
                        entry=orule.id,
                        message=(
                            f"override rule '{orule.id}' references skill "
                            f"'{skill}' which is not in the catalog; "
                            "the override will emit an unresolvable skill"
                        ),
                    )
                )
    return out


# ---------------------------------------------------------------------------
# Rule: override-unknown-agent (CONCERN)
# ---------------------------------------------------------------------------


@register_override
def rule_override_unknown_agent(
    catalog: list[CatalogEntry],
    override_rules: list[OverrideRule],
) -> list[Finding]:
    """Flag OverrideRules that name an agent absent from the catalog.

    Skip rules where ``agent`` is ``None`` (valid for decisions such as
    ``self_handle_unaided`` that have no agent component).

    Args:
        catalog: Parsed catalog entries.
        override_rules: Override rules to audit.

    Returns:
        CONCERN findings for each unknown agent reference.
    """
    known_agents: frozenset[str] = frozenset(
        e.name for e in catalog if e.kind == "agent"
    )
    out: list[Finding] = []
    for orule in override_rules:
        if orule.agent is None:
            continue
        if orule.agent not in known_agents:
            out.append(
                Finding(
                    severity=Severity.CONCERN,
                    rule="override-unknown-agent",
                    entry=orule.id,
                    message=(
                        f"override rule '{orule.id}' references agent "
                        f"'{orule.agent}' which is not in the catalog"
                    ),
                )
            )
    return out


# ---------------------------------------------------------------------------
# Rule: override-unreachable (NIT)
# ---------------------------------------------------------------------------

def _override_predicate_key(
    orule: OverrideRule,
) -> tuple[str | None, tuple[str, ...], frozenset[str]]:
    """Return a hashable key for an override rule's predicate triple.

    Args:
        orule: The override rule to compute the key for.

    Returns:
        Tuple of (command_prefix, sorted path_globs, tool_mentions).
    """
    return (
        orule.command_prefix,
        tuple(sorted(orule.path_globs)),
        orule.tool_mentions,
    )


@register_override
def rule_override_unreachable(
    catalog: list[CatalogEntry],
    override_rules: list[OverrideRule],
) -> list[Finding]:
    """Flag override rules that are string-identical duplicates of earlier ones.

    Two rules with the same ``command_prefix``, ``path_globs``, and
    ``tool_mentions`` string values are duplicates.  The later rule can
    never fire because the earlier rule matches first (first-match-wins
    semantics).

    Only catches literal copy/paste — no glob-subsumption analysis.
    O(n²) but n is small (override files are tens of rules, not
    thousands).

    Args:
        catalog: Parsed catalog entries (unused but required by protocol).
        override_rules: Override rules to audit.

    Returns:
        One NIT finding per (earlier_id, later_id) shadowed pair.
    """
    out: list[Finding] = []
    for i, later in enumerate(override_rules):
        later_key = _override_predicate_key(later)
        for earlier in override_rules[:i]:
            if _override_predicate_key(earlier) == later_key:
                out.append(
                    Finding(
                        severity=Severity.NIT,
                        rule="override-unreachable",
                        entry=later.id,
                        message=(
                            f"override rule '{later.id}' has identical "
                            f"predicates to earlier rule '{earlier.id}' "
                            "and can never be reached (first-match-wins)"
                        ),
                    )
                )
                break  # Report once per later rule
    return out


# ---------------------------------------------------------------------------
# Rule: override-duplicate-id (BLOCKING)
# ---------------------------------------------------------------------------


@register_override
def rule_override_duplicate_id(
    catalog: list[CatalogEntry],
    override_rules: list[OverrideRule],
) -> list[Finding]:
    """Flag override rules that share an id with another rule.

    One Finding per duplicate id citing both positions.

    Args:
        catalog: Parsed catalog entries (unused but required by protocol).
        override_rules: Override rules to audit.

    Returns:
        One BLOCKING finding per duplicated id.
    """
    seen: dict[str, int] = {}
    out: list[Finding] = []
    reported: set[str] = set()
    for idx, orule in enumerate(override_rules):
        if orule.id in seen:
            if orule.id not in reported:
                reported.add(orule.id)
                out.append(
                    Finding(
                        severity=Severity.BLOCKING,
                        rule="override-duplicate-id",
                        entry=orule.id,
                        message=(
                            f"override rule id '{orule.id}' appears at "
                            f"positions {seen[orule.id]} and {idx} — "
                            "ids must be unique within the overrides file"
                        ),
                    )
                )
        else:
            seen[orule.id] = idx
    return out


# ---------------------------------------------------------------------------
# Rule: override-tool-case-error (CONCERN)
# ---------------------------------------------------------------------------


@register_override
def rule_override_tool_case_error(
    catalog: list[CatalogEntry],
    override_rules: list[OverrideRule],
) -> list[Finding]:
    """Flag miscased tool names in OverrideRule.tool_mentions.

    Reuses ``_CANONICAL_TOOLS_LOWER`` from the catalog rule.
    ``features.tool_mentions`` preserves caller-supplied casing (canonical),
    so a lowercase override predicate silently never matches.

    One Finding per (rule_id, miscased_tool) pair.

    Args:
        catalog: Parsed catalog entries (unused but required by protocol).
        override_rules: Override rules to audit.

    Returns:
        CONCERN findings for each miscased tool mention.
    """
    out: list[Finding] = []
    for orule in override_rules:
        for tm in sorted(orule.tool_mentions):
            canonical = _CANONICAL_TOOLS_LOWER.get(tm.lower())
            if canonical is not None and canonical != tm:
                out.append(
                    Finding(
                        severity=Severity.CONCERN,
                        rule="override-tool-case-error",
                        entry=orule.id,
                        message=(
                            f"override rule '{orule.id}' tool_mention "
                            f"'{tm}' is case-incorrect; "
                            f"matcher expects '{canonical}'"
                        ),
                    )
                )
    return out
