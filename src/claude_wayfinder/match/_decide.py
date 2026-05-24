"""Decision composition for the routing ladder (v5, 7-step surface, #210).

Implements ``decide()``, ``_rationale_for()``, ``_top_alternatives()``,
and ``_detect_mixed_content()`` plus the threshold constants that drive
each step.  The scoring helpers (``score``, ``feature_count``,
``group_satisfied``, ``_skills_for_agent``, ``matched_paths_for``)
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
    matched_paths_for,
)
from claude_wayfinder.match._types import (
    CatalogEntry,
    Features,
    LaneInfo,
    ScoredEntry,
)

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
_ADVISORY_MIN = 0.5

# Epsilon for the mixed_content score threshold (#210).
# Alternatives must score >= 1.0 - _MIXED_CONTENT_SCORE_EPSILON to qualify
# as top-tier candidates for lane partitioning.  Default 0.05 means any
# agent scoring >= 0.95 is considered "clamped at 1.0" for this purpose.
_MIXED_CONTENT_SCORE_EPSILON = 0.05


# ---------------------------------------------------------------------------
# Decision composition
# ---------------------------------------------------------------------------


def _detect_mixed_content(
    scored_agents: list[ScoredEntry],
    scored_skills: list[ScoredEntry],
    features: Features,
) -> dict[str, Any] | None:
    """Attempt to build a ``mixed_content`` decision from scored agents.

    Evaluates the four detection conditions from #210:

    1. ``scored_agents`` has >= 2 entries at score >=
       ``1.0 - _MIXED_CONTENT_SCORE_EPSILON`` (i.e. "clamped at 1.0").
    2. Every qualifying agent has non-zero path-glob contribution
       (i.e. ``matched_paths_for`` returns at least one path).
    3. The matched-path sets of all qualifying agents are pairwise
       disjoint — no single path appears in two agents' lanes.
    4. (Pre-condition enforced by caller) The decision would otherwise be
       ``advisory`` — i.e. the gap is < ``_DELEGATE_GAP``.

    When all conditions pass the function returns the ``mixed_content``
    decision dict.  On any failure it returns ``None`` so the caller
    falls through to ``advisory``.

    The ``unassigned_paths`` field contains input paths not claimed by
    any of the qualifying agents.

    Args:
        scored_agents: Agents sorted by score descending.
        scored_skills: Skills sorted by score descending (for skill
            attachment).
        features: Current feature set.

    Returns:
        A ``mixed_content`` decision dict, or ``None`` if the conditions
        are not met.
    """
    min_score = 1.0 - _MIXED_CONTENT_SCORE_EPSILON

    # Condition 1: at least two top-tier agents.
    top_tier = [se for se in scored_agents if se.score >= min_score]
    if len(top_tier) < 2:
        return None

    # Condition 2 + 3: each top-tier agent must have >= 1 path-glob match,
    # and the matched sets must be pairwise disjoint.
    lanes: list[LaneInfo] = []
    claimed_paths: set[str] = set()

    for se in top_tier:
        agent_paths = matched_paths_for(se.entry, features)
        # Condition 2: non-zero path-glob contribution.
        if not agent_paths:
            return None
        # Condition 3: no path already claimed by a prior lane.
        overlap = set(agent_paths) & claimed_paths
        if overlap:
            return None
        claimed_paths.update(agent_paths)
        skills = _skills_for_agent(se.entry, scored_skills, features)
        lanes.append(
            LaneInfo(
                agent=se.entry.name,
                score=round(se.score, 6),
                matched_paths=tuple(agent_paths),
                skills=tuple(skills),
            )
        )

    # Build unassigned_paths: input paths not claimed by any top-tier lane.
    all_input_paths = set(features.paths)
    unassigned = sorted(all_input_paths - claimed_paths)

    # Build alternatives: non-top-tier agents with score > 0.
    alternatives = _top_alternatives(
        [se for se in scored_agents if se.score < min_score], n=3
    )

    return {
        "decision": "mixed_content",
        "confidence": round(top_tier[0].score, 6),
        "rationale": (
            f"{len(lanes)} agents clamped at 1.0 on path-disjoint lanes; "
            "structural mixed-content task."
        ),
        "lanes": [
            {
                "agent": lane.agent,
                "score": lane.score,
                "matched_paths": list(lane.matched_paths),
                "skills": list(lane.skills),
            }
            for lane in lanes
        ],
        "unassigned_paths": unassigned,
        "alternatives": alternatives,
        "disposition_source": "scored",
    }


def decide(
    scored_agents: list[ScoredEntry],
    scored_skills: list[ScoredEntry],
    features: Features,
    catalog_entries: list[CatalogEntry],
) -> dict[str, Any]:
    """Compose the routing decision from scored agents and skills.

    Implements the decision ladder from v5 §3.1.3 / §3.1.4.
    ``general-purpose`` must be excluded from ``scored_agents`` before
    calling this function.

    Decision order (7-branch surface, v0.10.0 / #210):
    1. ``needs_more_detail`` — feature density < 2.
    2. ``delegate`` — best agent >= 0.85, gap >= 0.2.
    3. ``self_handle`` — skill >= 0.5.
    3.5. ``mixed_content`` — >= 2 agents clamped at 1.0 on disjoint path
         lanes (inserted between self_handle and advisory; only fires when
         the advisory pre-condition is met, i.e. gap < 0.2).
    4. ``advisory`` — best agent >= 0.5 (covers both tie scenarios with
       gap < 0.2 and marginal scenarios with gap >= 0.2 but score < 0.85).
       The tie-vs-marginal distinction is preserved in the rationale string.
    5. ``self_handle_unaided`` — fallback.

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
            "disposition_source": "scored",
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
            "disposition_source": "scored",
        }

    # Step 3: self_handle — at least one strong skill, no dominant agent.
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
            "disposition_source": "scored",
        }

    # Step 3.5: mixed_content — structural fork where >= 2 agents clamp at
    # 1.0 on path-disjoint lanes.  Placed after delegate (dominant-agent
    # check) and self_handle (skills check), and before advisory.  Only
    # fires when the gap < _DELEGATE_GAP (i.e. the advisory pre-condition
    # is met); delegate already handled the gap >= _DELEGATE_GAP case.
    if best_agent and gap < _DELEGATE_GAP:
        mixed = _detect_mixed_content(scored_agents, scored_skills, features)
        if mixed is not None:
            return mixed

    # Step 4: advisory — agent exists but not dominant.  Covers both the
    # former 'ambiguous' case (gap < 0.2, multiple agents close) and the
    # marginal case (gap >= 0.2 but score < 0.85).  The rationale string
    # distinguishes the two scenarios so consumers can detect a close cluster.
    if best_agent and best_agent.score >= _ADVISORY_MIN:
        name = best_agent.entry.name
        score = best_agent.score
        skills = _skills_for_agent(best_agent.entry, scored_skills, features)
        if gap < _DELEGATE_GAP:
            rationale = (
                f"Best agent '{name}' scores {score:.2f} "
                f"(gap={gap:.2f} from next); "
                "top pick recommended, alternatives close behind."
            )
        else:
            rationale = (
                f"Best agent '{name}' scores {score:.2f} "
                "but match is not conclusive."
            )
        return {
            "decision": "advisory",
            "agent": name,
            "skills": skills,
            "confidence": round(score, 6),
            "rationale": rationale,
            "alternatives": _top_alternatives(scored_agents[1:], n=3),
            "disposition_source": "scored",
        }

    # Step 5: self_handle_unaided — no useful signal.
    return {
        "decision": "self_handle_unaided",
        "confidence": 0.0,
        "rationale": (
            "No agent or skill scored above threshold; "
            "proceeding without delegation or skill activation."
        ),
        "alternatives": [],
        "disposition_source": "scored",
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
