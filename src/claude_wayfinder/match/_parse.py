"""Catalog trigger parsing helpers for the dispatch matcher.

Parses the raw JSON ``triggers`` dict from a catalog entry into the
typed ``Triggers`` / ``KeywordGroup`` / ``Slot`` dataclasses defined in
``_types.py``.  The matcher is intentionally lenient: malformed entries
are silently dropped so a corrupted catalog degrades gracefully rather
than crashing at dispatch time.  Fatal validation lives in
``build_catalog.py``.
"""

from __future__ import annotations

from typing import Any

from claude_wayfinder.match._types import (
    Keyword,
    KeywordGroup,
    Slot,
    Triggers,
)


def _parse_slot(raw: Any) -> Slot | None:
    """Parse one slot from a raw catalog value.

    Accepts two forms (matcher is lenient; builder normalizes to dict):

    - Bare list of strings: ``['a', 'b']``
    - Dict with terms (+ optional name):
      ``{'terms': ['a', 'b'], 'name': 'verbs'}``

    Returns ``None`` for malformed input (group containing this slot
    will be silently dropped — fatal validation lives in
    build_catalog.py).

    Args:
        raw: Unvalidated catalog value for a single slot entry.

    Returns:
        A ``Slot`` instance, or ``None`` if the input is malformed.
    """
    if isinstance(raw, list):
        terms = tuple(
            str(t).lower() for t in raw if isinstance(t, str)
        )
        if not terms:
            return None
        return Slot(terms=terms, name=None)
    if isinstance(raw, dict):
        raw_terms = raw.get("terms")
        if not isinstance(raw_terms, list):
            return None
        terms = tuple(
            str(t).lower() for t in raw_terms if isinstance(t, str)
        )
        if not terms:
            return None
        name_val = raw.get("name")
        name = str(name_val) if isinstance(name_val, str) else None
        return Slot(terms=terms, name=name)
    return None


def _parse_keyword_group(raw: Any) -> KeywordGroup | None:
    """Parse one keyword_group from a raw catalog value.

    Returns ``None`` when the group is malformed; build_catalog.py is
    responsible for emitting fatal/warning issues at catalog build
    time.  The matcher silently drops malformed entries so a corrupted
    catalog degrades gracefully rather than crashing at dispatch time.

    Args:
        raw: Unvalidated catalog value for a single keyword_group.

    Returns:
        A ``KeywordGroup`` instance, or ``None`` if the input is
        malformed.
    """
    if not isinstance(raw, dict):
        return None
    raw_slots = raw.get("slots")
    if not isinstance(raw_slots, list) or len(raw_slots) < 2:
        return None
    slots: list[Slot] = []
    for raw_slot in raw_slots:
        slot = _parse_slot(raw_slot)
        if slot is None:
            return None
        slots.append(slot)
    weight = raw.get("weight")
    if not isinstance(weight, (int, float)) or isinstance(weight, bool):
        return None
    return KeywordGroup(slots=tuple(slots), weight=float(weight))


def _parse_triggers(raw: dict[str, Any]) -> Triggers:
    """Parse the raw ``triggers`` dict from a catalog entry.

    Missing fields default to empty collections per the schema.
    Unknown fields are silently ignored (forward compat).

    Args:
        raw: The ``triggers`` sub-object from a catalog entry.

    Returns:
        A ``Triggers`` instance with all fields populated.
    """
    keywords: list[Keyword] = []
    for kw in raw.get("keywords", []):
        if isinstance(kw, dict) and "term" in kw and "weight" in kw:
            keywords.append(
                Keyword(
                    term=str(kw["term"]).lower(),
                    weight=float(kw["weight"]),
                )
            )

    keyword_groups: list[KeywordGroup] = []
    for raw_group in raw.get("keyword_groups", []):
        group = _parse_keyword_group(raw_group)
        if group is not None:
            keyword_groups.append(group)

    return Triggers(
        command_prefixes=frozenset(
            str(x).lower() for x in raw.get("command_prefixes", [])
        ),
        agent_mentions=frozenset(
            str(x).lower() for x in raw.get("agent_mentions", [])
        ),
        path_globs=tuple(str(x) for x in raw.get("path_globs", [])),
        keywords=tuple(keywords),
        tool_mentions=frozenset(
            str(x).lower() for x in raw.get("tool_mentions", [])
        ),
        excludes=frozenset(
            str(x).lower() for x in raw.get("excludes", [])
        ),
        keyword_groups=tuple(keyword_groups),
    )
