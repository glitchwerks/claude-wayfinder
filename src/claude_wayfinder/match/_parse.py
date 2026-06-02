"""Catalog trigger parsing helpers for the dispatch matcher.

Parses the raw JSON ``triggers`` dict from a catalog entry into the
typed ``Triggers`` / ``KeywordGroup`` / ``Slot`` dataclasses defined in
``_types.py``.  The matcher is intentionally lenient: malformed entries
are silently dropped so a corrupted catalog degrades gracefully rather
than crashing at dispatch time.  Fatal validation lives in
``build_catalog.py``.

Stemming (issue #304): keyword ``term`` values are run through Porter2
at parse time so that ``Keyword.term`` always holds a stem when
``no_stem`` is ``False``.  This makes the scoring check
``k.term in features.keywords`` a stem-vs-stem comparison with no
change to the scoring formula.  When ``no_stem=True`` the term is
preserved verbatim and matched against ``features.raw_keywords``.
"""

from __future__ import annotations

from typing import Any

from claude_wayfinder.match._stem import stem as _stem_word
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
        # Bare list form: slot-level no_stem not supported in this form;
        # all terms are stemmed.
        terms = tuple(
            _stem_word(str(t).lower()) for t in raw if isinstance(t, str)
        )
        if not terms:
            return None
        return Slot(terms=terms, name=None)
    if isinstance(raw, dict):
        raw_terms = raw.get("terms")
        if not isinstance(raw_terms, list):
            return None
        terms = tuple(
            _stem_word(str(t).lower()) for t in raw_terms if isinstance(t, str)
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
    # Build keywords with stem-deduplication.  Two distinct catalog terms
    # may collapse to the same Porter2 stem (e.g. "doc" and "docs" both
    # become "doc").  After stemming, only one Keyword per stem is kept
    # (last-wins, consistent with _validate_keywords dedup).  This prevents
    # double-counting in the scorer.  no_stem terms are keyed by their raw
    # term (not the stem) so they remain distinct.
    seen_stems: dict[str, Keyword] = {}
    for kw in raw.get("keywords", []):
        if isinstance(kw, dict) and "term" in kw and "weight" in kw:
            no_stem: bool = bool(kw.get("no_stem", False))
            # Keyword.__post_init__ applies Porter2 stemming automatically
            # (issue #304).  Pass the raw term; the dataclass handles
            # lowercasing and stemming.  When no_stem=True the dataclass
            # preserves the term verbatim (lowercased only).
            new_kw = Keyword(
                term=str(kw["term"]),
                weight=float(kw["weight"]),
                no_stem=no_stem,
            )
            # Dedup key: use the stored (stemmed) term so that "docs" and
            # "doc" (both → "doc") collapse.  For no_stem terms the stored
            # term IS the raw form, so they are never accidentally merged.
            seen_stems[new_kw.term] = new_kw
    keywords: list[Keyword] = list(seen_stems.values())

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
        path_globs_excluded=tuple(
            str(x) for x in raw.get("path_globs_excluded", [])
        ),
    )
