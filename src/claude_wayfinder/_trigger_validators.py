"""Shared trigger-validation primitives for build_catalog and audit_catalog.

This module provides pure-function helpers that both the catalog builder
(``build_catalog``) and the catalog auditor (``audit_catalog``) use to
validate keyword weights, keyword terms, and trigger dimensions.

**Design contract:** these primitives return booleans or clamped values only.
They do NOT return ``ValidationIssue`` or ``Finding`` objects — callers wrap
the return values in their own error models.  This separation keeps the module
error-model-neutral and usable by both callers without coupling.

Usage from ``build_catalog``::

    from claude_wayfinder._trigger_validators import (
        clamp_weight_to_ladder,
        has_whitespace,
        is_weight_in_ladder,
    )

    if not is_weight_in_ladder(w):
        clamped = clamp_weight_to_ladder(w)
        # append a ValidationIssue, set weight to clamped

Usage from ``audit_catalog``::

    from claude_wayfinder._trigger_validators import (
        count_trigger_dimensions,
        has_whitespace,
        is_weight_in_ladder,
    )

    if not is_weight_in_ladder(kw.weight):
        # append a Finding
"""

from __future__ import annotations

from typing import Any

# The canonical allowed weight values for keyword triggers.  Expressed as a
# frozenset so callers can use ``in`` membership tests (O(1)) and the set is
# immutable.  The tuple form ``ALLOWED_WEIGHTS`` in ``build_catalog`` is kept
# there for backward compatibility; this frozenset is the shared source of
# truth for validator logic.
WEIGHT_LADDER: frozenset[float] = frozenset({0.25, 0.5, 1.0})

# Ordered tuple used by ``clamp_weight_to_ladder`` for deterministic
# tie-breaking (higher value wins when equidistant).
_LADDER_ORDERED: tuple[float, ...] = (0.25, 0.5, 1.0)


def is_weight_in_ladder(weight: float) -> bool:
    """Return True if *weight* is exactly one of the allowed ladder values.

    The allowed ladder is ``{0.25, 0.5, 1.0}``.  Values that are
    numerically equal to a ladder member (e.g. ``int(1) == 1.0``) pass.

    Note: Boolean detection is the caller's responsibility.  ``True``
    evaluates to ``1.0`` and will return ``True``; callers that need to
    reject booleans should guard with ``isinstance(weight, bool)``
    before invoking this function.

    Args:
        weight: The weight value to check.

    Returns:
        ``True`` if *weight* is in ``{0.25, 0.5, 1.0}``, ``False``
        otherwise.
    """
    return weight in WEIGHT_LADDER


def clamp_weight_to_ladder(weight: float) -> float:
    """Return the ladder value nearest to *weight*.

    Ties (equidistant values) resolve to the *higher* ladder value, so
    ``0.375`` → ``0.5`` and ``0.75`` → ``1.0``.

    Args:
        weight: The raw weight value to clamp.

    Returns:
        The closest value in ``{0.25, 0.5, 1.0}``, as a ``float``.
        Ties break in favour of the larger value.
    """
    return float(min(_LADDER_ORDERED, key=lambda v: (abs(v - weight), -v)))


def has_whitespace(term: str) -> bool:
    """Return True if *term* contains any whitespace character.

    Uses ``str.isspace()`` on each character, which covers space, tab,
    newline, carriage return, form feed, and Unicode whitespace such as
    non-breaking space (U+00A0).

    Args:
        term: The string to test.

    Returns:
        ``True`` if any character in *term* is whitespace, ``False``
        otherwise (including the empty-string case).
    """
    return any(c.isspace() for c in term)


def count_trigger_dimensions(triggers: Any) -> int:
    """Count the number of populated positive dimensions on *triggers*.

    A dimension is "populated" when its corresponding list attribute is
    non-empty.  The five checked dimensions are:

    * ``command_prefixes``
    * ``agent_mentions``
    * ``path_globs``
    * ``keywords``
    * ``tool_mentions``

    This function accepts any object with those five list attributes, so
    it works with both the ``Triggers`` dataclass from ``match.py`` and
    any test double.

    Args:
        triggers: An object with the five trigger dimension attributes.

    Returns:
        An integer in ``[0, 5]`` representing how many dimensions are
        populated.
    """
    return sum(
        1
        for populated in (
            bool(triggers.command_prefixes),
            bool(triggers.agent_mentions),
            bool(triggers.path_globs),
            bool(triggers.keywords),
            bool(triggers.tool_mentions),
        )
        if populated
    )
