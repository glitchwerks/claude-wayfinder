"""Override rule loading for the dispatch matcher (issue #213).

Override rules pre-declare a verbatim decision tuple that the matcher
returns when a rule's predicates match the dispatch context.  Resolution
runs BEFORE scoring; a matched rule short-circuits the entire scoring +
decision-ladder pipeline.

Public surface:
    - ``load_overrides(path)`` — parse a JSON rules file into a typed
      OverrideRule list.
    - ``OverridesError`` — raised on missing/malformed/invalid override
      files.

Note:
    ``resolve_override`` and fnmatch-based predicate matching are added
    in Task 3 of issue #213.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from claude_wayfinder.match._types import (
    VALID_DECISIONS,
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
