"""Shared predicate for filtering catalog entries in the dispatch matcher.

This module contains ``is_agent_routable`` — the single source of truth
for which catalog entries may participate in agent scoring.  Both the
catalog generator (build_catalog.py) and the matcher (match.py) import
from here so the exclusion rules stay in sync.

The hardcoded ``_EXCLUDED_AGENT_NAME`` constant (``"general-purpose"``) was
replaced with a data-driven ``routable: bool`` parameter so that any agent
may be marked non-routable without requiring changes to this module.

The predicate accepts three named scalar parameters rather than a
``dict[str, Any]`` to avoid allocating a temporary dict per entry in the
scoring loop (match.py:894-899).  Using a ``CatalogEntry`` directly would
create a circular import because ``CatalogEntry`` is defined in ``match.py``,
which already imports from this module.  Three named keyword args give the
same call-site clarity and proper type information without the allocation
cost or the circular dependency.
"""

from __future__ import annotations


def is_agent_routable(
    *,
    name: str,
    kind: str,
    source: str,
    routable: bool = True,
) -> bool:
    """Return True when the described entry may participate in agent scoring.

    An entry is **not** routable when any of the following holds:

    * ``routable`` is ``False`` — the entry has been explicitly marked
      as non-routable in its catalog frontmatter (e.g. the router agent
      itself, which must never be selected as a delegation target).
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
        routable: Whether the entry declares itself as a valid routing
            target.  Defaults to ``True`` so callers that do not yet
            pass the field remain backward-compatible.  Set to ``False``
            for the router agent (or any other non-scoreable agent) so
            it is excluded from the scored pool at dispatch time.

    Returns:
        ``True`` when the entry may enter agent scoring; ``False`` when
        the exclusion rules apply.

    Examples:
        >>> is_agent_routable(name="router-agent", kind="agent",
        ...                   source="owned", routable=False)
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
    if not routable:
        return False
    if kind == "agent" and source == "plugin":
        return False
    return True
