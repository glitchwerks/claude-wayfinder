"""Override rule loading and resolution for the dispatch matcher (#213).

Override rules pre-declare a verbatim decision tuple that the matcher
returns when a rule's predicates match the dispatch context.  Resolution
runs BEFORE scoring; a matched rule short-circuits the entire scoring +
decision-ladder pipeline.

Public surface:
    - ``load_overrides(path)`` — parse a JSON rules file into a typed
      OverrideRule list.
    - ``resolve_override(rules, features)`` — first-match-wins predicate
      evaluation returning an OverrideMatch or None.
    - ``OverridesError`` — raised on missing/malformed/invalid override
      files.
"""

from __future__ import annotations

import fnmatch
import json
import sys
from pathlib import Path

from claude_wayfinder.match._types import (
    VALID_DECISIONS,
    Features,
    OverrideMatch,
    OverrideRule,
)


class OverridesError(Exception):
    """Raised when an overrides file cannot be loaded or is invalid."""


def load_overrides(path: Path) -> list[OverrideRule]:
    """Parse a JSON overrides file into a list of OverrideRule.

    Args:
        path: Resolved path to the overrides JSON file.

    Returns:
        Rule list in file order.  Order is significant: ``resolve_override``
        uses first-match-wins semantics.

    Raises:
        OverridesError: If the file is missing, malformed JSON, or any
            rule fails decision-value validation.
    """
    if not path.exists():
        raise OverridesError(f"overrides file not found at {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise OverridesError(
            f"malformed JSON in overrides file: {exc}"
        ) from exc

    raw_rules: list[dict] = payload.get("rules", [])
    rules: list[OverrideRule] = []
    for idx, raw in enumerate(raw_rules):
        rule_id = str(raw.get("id", f"rule-{idx}"))
        decision = str(raw.get("decision", ""))
        if decision not in VALID_DECISIONS:
            raise OverridesError(
                f"rule {rule_id!r}: invalid decision {decision!r} "
                f"(must be one of {sorted(VALID_DECISIONS)})"
            )
        predicates: dict = raw.get("predicates", {}) or {}
        raw_conf = float(raw.get("confidence", 1.0))
        clamped_conf = max(0.0, min(1.0, raw_conf))
        if clamped_conf != raw_conf:
            print(
                f"[OVERRIDES WARNING] rule {rule_id!r}: confidence "
                f"{raw_conf} outside [0.0, 1.0], clamped to {clamped_conf}",
                file=sys.stderr,
            )
        rules.append(
            OverrideRule(
                id=rule_id,
                decision=decision,
                agent=raw.get("agent"),
                skills=tuple(raw.get("skills", [])),
                confidence=clamped_conf,
                rationale=str(raw.get("rationale", "")),
                command_prefix=predicates.get("command_prefix"),
                path_globs=tuple(predicates.get("path_globs", [])),
                tool_mentions=frozenset(
                    predicates.get("tool_mentions", [])
                ),
            )
        )
    return rules


def _rule_matches(rule: OverrideRule, features: Features) -> tuple[str, ...]:
    """Return predicate names that fired, or () when the rule does not match.

    AND semantics apply: every predicate set on the rule must fire.
    A rule with zero predicates returns () as a defense-in-depth guard
    so it never fires at runtime even if audit-catalog missed it.

    Args:
        rule: The override rule whose predicates are evaluated.
        features: Extracted dispatch context features.

    Returns:
        Tuple of matched predicate names (e.g. ``("path_globs",)``), or
        an empty tuple when any required predicate misses.
    """
    fired: list[str] = []

    has_cp = rule.command_prefix is not None
    has_pg = bool(rule.path_globs)
    has_tm = bool(rule.tool_mentions)

    if not (has_cp or has_pg or has_tm):
        return ()

    if has_cp:
        if features.command_prefix != rule.command_prefix:
            return ()
        fired.append("command_prefix")

    if has_pg:
        path_hit = any(
            fnmatch.fnmatch(p, g)
            for p in features.paths
            for g in rule.path_globs
        )
        if not path_hit:
            return ()
        fired.append("path_globs")

    if has_tm:
        if not (rule.tool_mentions & features.tool_mentions):
            return ()
        fired.append("tool_mentions")

    return tuple(fired)


def resolve_override(
    rules: list[OverrideRule],
    features: Features,
) -> OverrideMatch | None:
    """Return the first rule whose predicates all match, or None.

    First-match-wins by file order (rules are evaluated in the order they
    appear in the overrides JSON).  A matched rule short-circuits the
    scoring pipeline.

    Args:
        rules: Loaded override rules in file order.
        features: Extracted dispatch context features.

    Returns:
        OverrideMatch on the first hit; None when no rule matches.
    """
    for rule in rules:
        fired = _rule_matches(rule, features)
        if fired:
            return OverrideMatch(rule=rule, matched_predicates=fired)
    return None
