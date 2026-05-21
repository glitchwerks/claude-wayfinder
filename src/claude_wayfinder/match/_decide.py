"""Decision composition for the 7-step routing ladder (v5).

Implements ``decide()``, ``_rationale_for()``, and ``_top_alternatives()``
plus the threshold constants that drive each step.  The scoring helpers
(``score``, ``feature_count``, ``group_satisfied``, ``_skills_for_agent``)
live in ``_match.py`` and are imported here.
"""

from __future__ import annotations

import fnmatch
from typing import Any

from claude_wayfinder.match._match import (
    _MAX_SKILLS,
    _SKILL_MIN,
    _skills_for_agent,
    feature_count,
    group_satisfied,
)
from claude_wayfinder.match._types import CatalogEntry, Features, ScoredEntry

# ---------------------------------------------------------------------------
# Constants (decision-ladder thresholds — scoring constants live in _match.py)
# ---------------------------------------------------------------------------

# Minimum number of populated input dimensions required before the
# matcher will attempt routing.  Below this threshold the matcher
# returns ``needs_more_detail`` (v5 §3.1.3).
_MIN_FEATURE_DENSITY = 2

# Score thresholds from the decision ladder (v5 §3.1.3 / §3.1.4).
_DELEGATE_THRESHOLD = 0.85
_DELEGATE_GAP = 0.2
_AMBIGUOUS_MIN = 0.5
_ADVISORY_MIN = 0.5


# ---------------------------------------------------------------------------
# Decision composition
# ---------------------------------------------------------------------------


def decide(
    scored_agents: list[ScoredEntry],
    scored_skills: list[ScoredEntry],
    features: Features,
    catalog_entries: list[CatalogEntry],
) -> dict[str, Any]:
    """Compose the routing decision from scored agents and skills.

    Implements the decision ladder from v5 §3.1.3 / §3.1.4 exactly.
    ``general-purpose`` must be excluded from ``scored_agents`` before
    calling this function.

    Decision order:
    1. ``needs_more_detail`` — feature density < 2.
    2. ``delegate`` — best agent >= 0.85, gap >= 0.2.
    3. ``ambiguous`` — best agent >= 0.5, gap < 0.2.
    4. ``self_handle`` — skill >= 0.5.
    5. ``advisory`` — best agent >= 0.5 (gap >= 0.2 implied by not
       hitting ambiguous above).
    6. ``self_handle_unaided`` — fallback.

    Args:
        scored_agents: Agents sorted by score descending, excluding
            ``general-purpose``.
        scored_skills: Skills sorted by score descending.
        features: Current feature set.
        catalog_entries: All catalog entries (used for alternatives).

    Returns:
        Decision dict matching the output JSON schema.
    """
    # Step 1: feature density guard.
    if feature_count(features) < _MIN_FEATURE_DENSITY:
        return {
            "decision": "needs_more_detail",
            "confidence": 0.0,
            "rationale": (
                "Feature density below threshold: provide more context "
                "(file paths, explicit tool mentions, or additional keywords)."
            ),
            "alternatives": [],
        }

    best_agent = scored_agents[0] if scored_agents else None
    best_skills = [se for se in scored_skills if se.score >= _SKILL_MIN][:_MAX_SKILLS]

    gap = 0.0
    if len(scored_agents) >= 2:
        gap = scored_agents[0].score - scored_agents[1].score
    elif best_agent:
        # Single agent: gap is effectively the agent's own score.
        gap = best_agent.score

    # Step 2: delegate — high-confidence single winner.
    if best_agent and best_agent.score >= _DELEGATE_THRESHOLD and gap >= _DELEGATE_GAP:
        skills = _skills_for_agent(best_agent.entry, scored_skills, features)
        return {
            "decision": "delegate",
            "agent": best_agent.entry.name,
            "skills": skills,
            "confidence": round(best_agent.score, 6),
            "rationale": _rationale_for(best_agent, features),
            "alternatives": _top_alternatives(scored_agents[1:], n=3),
        }

    # Step 3: ambiguous — two or more agents tie above 0.5.
    if best_agent and best_agent.score >= _AMBIGUOUS_MIN and gap < _DELEGATE_GAP:
        return {
            "decision": "ambiguous",
            "confidence": round(best_agent.score, 6),
            "rationale": (
                f"Multiple agents score similarly "
                f"(gap={gap:.2f}); user input needed to disambiguate."
            ),
            "alternatives": _top_alternatives(scored_agents, n=3),
        }

    # Step 4: self_handle — at least one strong skill, no dominant agent.
    if best_skills:
        return {
            "decision": "self_handle",
            "skills": [se.entry.name for se in best_skills],
            "confidence": round(best_skills[0].score, 6),
            "rationale": (
                "No dominant agent; routing to self with skills: "
                + ", ".join(se.entry.name for se in best_skills)
            ),
            "alternatives": [],
        }

    # Step 5: advisory — agent exists but not dominant.
    if best_agent and best_agent.score >= _ADVISORY_MIN:
        skills = _skills_for_agent(best_agent.entry, scored_skills, features)
        return {
            "decision": "advisory",
            "agent": best_agent.entry.name,
            "skills": skills,
            "confidence": round(best_agent.score, 6),
            "rationale": (
                f"Best agent '{best_agent.entry.name}' scores "
                f"{best_agent.score:.2f} but match is not conclusive."
            ),
            "alternatives": _top_alternatives(scored_agents[1:], n=2),
        }

    # Step 6: self_handle_unaided — no useful signal.
    return {
        "decision": "self_handle_unaided",
        "confidence": 0.0,
        "rationale": (
            "No agent or skill scored above threshold; "
            "proceeding without delegation or skill activation."
        ),
        "alternatives": [],
    }


# ---------------------------------------------------------------------------
# Helpers for output
# ---------------------------------------------------------------------------


def _rationale_for(se: ScoredEntry, features: Features) -> str:
    """Build a short human-readable rationale string.

    Format: ``matched <seg1>; <seg2>; ....``

    Segments (each only emitted when non-empty):
    - ``keywords: term1, term2``    — matched singleton keywords
    - ``globs: pat1, pat2``         — matched path globs
    - ``tools: tool1, tool2``       — matched tool mentions
    - ``groups: [name1+name2, ...]``— fired keyword groups (slot names
      joined by ``+``; falls back to ``group_<index>`` when a slot is
      unnamed)

    Args:
        se: The winning scored entry.
        features: Extracted feature set.

    Returns:
        A one-sentence rationale string.
    """
    matched_kw = [
        k.term for k in se.entry.triggers.keywords if k.term in features.keywords
    ]
    matched_globs = [
        g
        for g in se.entry.triggers.path_globs
        if any(fnmatch.fnmatch(p, g) for p in features.paths)
    ]
    parts: list[str] = []
    if matched_kw:
        parts.append(f"keywords: {', '.join(matched_kw[:3])}")
    if matched_globs:
        parts.append(f"globs: {', '.join(matched_globs[:2])}")
    if features.tool_mentions & se.entry.triggers.tool_mentions:
        matched_tools = sorted(
            features.tool_mentions & se.entry.triggers.tool_mentions
        )
        parts.append(f"tools: {', '.join(matched_tools[:2])}")

    # Fired keyword groups segment (AC #7).
    # Label each satisfied group by its slot names joined with '+', or
    # by zero-based index when any slot is unnamed.
    fired_group_labels: list[str] = []
    for idx, grp in enumerate(se.entry.triggers.keyword_groups):
        if group_satisfied(grp, features):
            if all(slot.name for slot in grp.slots):
                label = "+".join(slot.name for slot in grp.slots)  # type: ignore[arg-type]
            else:
                label = f"group_{idx}"
            fired_group_labels.append(label)
    if fired_group_labels:
        parts.append(f"groups: [{', '.join(fired_group_labels)}]")

    if not parts:
        return f"matched '{se.entry.name}' with score {se.score:.2f}."
    return f"matched {'; '.join(parts)}."


def _top_alternatives(scored: list[ScoredEntry], n: int = 3) -> list[dict[str, Any]]:
    """Return the top-N alternatives as compact dicts.

    Args:
        scored: Scored entries sorted by score descending.
        n: Maximum number to return.

    Returns:
        List of ``{"agent": name, "score": float}`` dicts.
    """
    return [
        {"agent": se.entry.name, "score": round(se.score, 6)}
        for se in scored[:n]
        if se.score > 0.0
    ]
