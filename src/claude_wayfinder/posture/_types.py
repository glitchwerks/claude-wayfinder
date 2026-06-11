"""Data-model types for the posture-evidence extractor library.

Defines the frozen dataclasses used to represent the dispatch context
inputs and the uniform extractor output contract from §10.3.

All types are immutable (``frozen=True``) so they can be shared safely
across call-sites without defensive copying.

This module is intentionally standalone — it has zero imports from
``claude_wayfinder.match`` so the posture library remains offline and
independently auditable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Input type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PostureContext:
    """Dispatch-context fields consumed by posture extractors.

    Mirrors the fields available to the matcher (per
    ``skills/dispatch/SKILL.md``) without importing from
    ``claude_wayfinder.match`` — the posture library must stand alone.

    Attributes:
        task_description: Required free-text task description from the
            dispatch context.
        file_paths: File/directory paths named in the context. Defaults
            to an empty tuple.
        agent_mentions: Explicit agent names referenced in the prompt.
            Defaults to an empty frozenset.
        tool_mentions: Explicit tool names mentioned in the context.
            Defaults to an empty frozenset.
        command_prefix: Single slash-command / CLI prefix present in the
            context, or ``None`` when absent.
    """

    task_description: str
    file_paths: tuple[str, ...] = field(default_factory=tuple)
    agent_mentions: frozenset[str] = field(default_factory=frozenset)
    tool_mentions: frozenset[str] = field(default_factory=frozenset)
    command_prefix: str | None = None


# ---------------------------------------------------------------------------
# Output type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExtractorResult:
    """Uniform output contract for every evidence extractor (§10.3).

    Each extractor emits exactly one ``ExtractorResult``.  When the
    extractor abstains (did not fire), ``fired`` is ``False`` and
    ``evidence`` is an empty list.  Abstain ≠ veto: a non-firing
    extractor adds no posture signal but does not block other extractors.

    Attributes:
        fired: ``True`` when the extractor matched, ``False`` when it
            abstained.  Some extractors (e.g. E7 ``area_span``) return
            an ``int`` count instead of a plain bool — callers may test
            ``bool(result.fired)`` for the fired/not-fired binary.
        tier: Determinism tier — ``"A"`` (structured field), ``"B"``
            (text-shape), or ``"C"`` (closed-marker lexical).  Carried
            through to telemetry so the §10.3 Tier-C decisiveness rate
            can be measured downstream.
        evidence: List of ``(posture, weight_class)`` pairs.  ``posture``
            is one of the §9.3 eight-way vocabulary.  ``weight_class``
            is one of ``"strong"`` / ``"weak"`` / ``"modifier"``.
            Empty list when ``fired`` is falsy.
    """

    fired: bool | int
    tier: str
    evidence: list[tuple[str, str]]
