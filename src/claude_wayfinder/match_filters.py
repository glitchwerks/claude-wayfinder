"""Shared predicate for filtering catalog entries in the dispatch matcher.

This module contains ``is_agent_routable`` — the single source of truth
for which catalog entries may participate in agent scoring.  Both the
catalog generator (build_catalog.py) and the matcher (match.py) import
from here so the exclusion rules stay in sync.

Issue #477 acceptance criteria §"Shared predicate module" (Pass 2.5).

Design note (Finding #1, PR #487): the predicate accepts three named
scalar parameters rather than a ``dict[str, Any]`` to avoid allocating a
temporary dict per entry in the scoring loop (match.py:894-899).  Using a
``CatalogEntry`` directly would create a circular import because
``CatalogEntry`` is defined in ``match.py``, which already imports from
this module.  Three named keyword args give the same call-site clarity
and proper type information without the allocation cost or the circular
dependency.
"""

from __future__ import annotations

# The router agent is always excluded from the scored agents pool.
# Defined here as a constant so consumers do not embed magic strings.
_EXCLUDED_AGENT_NAME: str = "general-purpose"


def is_agent_routable(*, name: str, kind: str, source: str) -> bool:
    """Return True when the described entry may participate in agent scoring.

    An entry is **not** routable when either of the following holds:

    * ``name`` equals ``"general-purpose"`` — the router itself must
      never be selected as a delegation target.
    * ``kind`` is ``"agent"`` **and** ``source`` is ``"plugin"``
      — plugin agents land dormant (zero triggers) and are excluded from
      the scoring pool until they are explicitly given override triggers
      in a future pass.

    ``source="builtin"`` agents are **routable by default** — unlike
    plugin agents which require an explicit override to participate in
    routing.  Built-in agents (e.g. ``Explore``, ``Plan``) are authored
    via operator sidecars under ``~/.claude/triggers/builtin/`` and are
    intended to be active at dispatch time.

    Skills are never filtered by this predicate; the caller is expected
    to call ``is_agent_routable`` only for entries whose eligibility in
    the *agent* pool is being tested.  Skills with ``source="plugin"``
    are dormant (score 0.0) but remain in the skill pool so that a
    future plugin-override mechanism can activate them.

    Args:
        name: Entry name string (e.g. ``"code-writer"``).
        kind: Either ``"agent"`` or ``"skill"``.
        source: Provenance tag — ``"owned"``, ``"plugin"``,
            ``"plugin-override"``, ``"builtin"``, or ``"project"``.

    Returns:
        ``True`` when the entry may enter agent scoring; ``False`` when
        the exclusion rules apply.

    Examples:
        >>> is_agent_routable(name="general-purpose", kind="agent",
        ...                   source="owned")
        False
        >>> is_agent_routable(name="my-agent", kind="agent",
        ...                   source="plugin")
        False
        >>> is_agent_routable(name="code-writer", kind="agent",
        ...                   source="owned")
        True
        >>> is_agent_routable(name="Explore", kind="agent",
        ...                   source="builtin")
        True
        >>> is_agent_routable(name="superpowers:brainstorming",
        ...                   kind="skill", source="plugin")
        True
    """
    if name == _EXCLUDED_AGENT_NAME:
        return False
    if kind == "agent" and source == "plugin":
        return False
    return True
