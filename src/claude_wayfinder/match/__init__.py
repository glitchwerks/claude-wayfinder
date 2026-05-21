"""Deterministic 7-decision dispatch matcher for the router (v5).

Reads a JSON dispatch context from stdin and writes a JSON routing
decision to stdout.  The catalog path must be supplied via one of:

  1. ``--catalog-path <path>`` CLI flag.
  2. ``DISPATCH_CATALOG_PATH`` env var.

If neither is present the matcher exits non-zero with a
``[CATALOG ERROR]`` banner on stderr naming the fix.  The old
``~/.claude/`` default and the middle env-var step have been
removed (Issue #10).

Every successful invocation appends a decision record to the path given
by ``DISPATCH_LOG_PATH``.  When ``DISPATCH_LOG_PATH`` is absent logging
is silently disabled — no fallback to ``~/.claude/``.  Log-write
failures are non-fatal: a message is written to stderr but the
matcher's stdout decision is always emitted.

Usage::

    echo '{"task_description": "implement the new feature",
           "file_paths": ["src/main.py"]}' \\
      | python match.py --catalog-path /path/to/dispatch-catalog.json

See ``docs/schema.md`` §4 for the scoring and decision algorithm this
module implements, and ``docs/design.md`` for the design rationale.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import sys
from pathlib import Path
from typing import Any

from claude_wayfinder.match._catalog import (
    _compute_catalog_hash,
    _emit_catalog_error,
    _resolve_catalog_path,
    _resolve_log_path,
    _write_log_entry,
    load_catalog,
)
from claude_wayfinder.match._match import (
    _GROUP_MULTIPLIER as _GROUP_MULTIPLIER,  # re-export for test compat
)
from claude_wayfinder.match._match import (
    _MAX_SKILLS,
    _SKILL_MIN,
    _skills_for_agent,
    build_features,
    feature_count,
    group_satisfied,
    score,
)
from claude_wayfinder.match._match import (
    extract_keywords as extract_keywords,  # re-export
)
from claude_wayfinder.match._parse import _parse_triggers as _parse_triggers  # re-export
from claude_wayfinder.match._types import (
    VALID_DECISIONS,
    CatalogEntry,
    Features,
    Keyword,
    KeywordGroup,
    ScoredEntry,
    Slot,
    Triggers,
)
from claude_wayfinder.match_filters import is_agent_routable

# Re-export types so ``from claude_wayfinder.match import CatalogEntry``
# etc. continue to work after the package split.
__all__ = [
    "VALID_DECISIONS",
    "CatalogEntry",
    "Features",
    "Keyword",
    "KeywordGroup",
    "ScoredEntry",
    "Slot",
    "Triggers",
    "build_features",
    "decide",
    "group_satisfied",
    "load_catalog",
    "main",
    "score",
]

# ---------------------------------------------------------------------------
# Constants (decision ladder thresholds — scoring constants live in _match.py)
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

# Keyword/feature extraction, scoring, feature density, and skills resolution
# are implemented in _match.py and imported at the top of this module.


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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    """Entry point: read JSON from stdin, write decision JSON to stdout.

    The catalog path is resolved via ``_resolve_catalog_path()``.  If no
    path is available (no ``--catalog-path`` flag and no
    ``DISPATCH_CATALOG_PATH`` env var), emits a ``[CATALOG ERROR]`` banner
    on stderr and exits with code 2.  If the catalog is degraded (missing,
    malformed, or empty), the same banner is emitted.

    Arg resolution order for catalog:

    1. ``--catalog-path <path>`` CLI flag.
    2. ``DISPATCH_CATALOG_PATH`` env var.
    3. Fail loud with ``[CATALOG ERROR]``.

    Log path resolution order:

    1. ``DISPATCH_LOG_PATH`` env var.
    2. Logging silently disabled (no ``~/.claude/`` fallback).

    Args:
        argv: Argument list.  Defaults to ``sys.argv[1:]`` when ``None``.

    Input JSON shape (stdin)::

        {
            "task_description": "...",     # required
            "file_paths":       ["..."],   # optional
            "agent_mentions":   ["..."],   # optional
            "tool_mentions":    ["..."],   # optional
            "command_prefix":   null       # optional
        }

    Output JSON shape (stdout)::

        {
            "decision":     "delegate" | "self_handle" | ...,
            "agent":        "code-writer",   # when decision implies one
            "skills":       ["python"],      # for delegate/self_handle/advisory
            "confidence":   0.92,
            "rationale":    "matched keywords: implement.",
            "alternatives": [{"agent": "...", "score": 0.x}, ...]
        }
    """
    # --- Parse CLI args ---
    parser = argparse.ArgumentParser(
        description="Deterministic 7-decision dispatch matcher (v5).",
        add_help=True,
    )
    parser.add_argument(
        "--catalog-path",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Path to the dispatch-catalog.json file.  "
            "Resolution order: --catalog-path > DISPATCH_CATALOG_PATH env "
            "var > error.  The old ~/.claude/state/ default has been removed."
        ),
    )
    args = parser.parse_args(argv)

    # --- Load catalog ---
    catalog_path = _resolve_catalog_path(args.catalog_path)

    if not catalog_path.exists():
        _emit_catalog_error(f"file not found at {catalog_path}")

    catalog_raw_text: str = ""
    try:
        catalog_raw_text = catalog_path.read_text(encoding="utf-8")
        entries = load_catalog(catalog_path)
    except json.JSONDecodeError as exc:
        _emit_catalog_error(f"malformed JSON ({exc})")
    if not entries:
        # load_catalog returns [] for empty catalogs rather than raising
        # (audit-catalog needs to load them without crashing).  The dispatch
        # runtime treats zero entries as a degraded state and errors out.
        _emit_catalog_error("Catalog contains zero entries.")

    catalog_hash = _compute_catalog_hash(catalog_raw_text)

    # --- Parse stdin ---
    raw_input = sys.stdin.read()
    try:
        context: dict[str, Any] = json.loads(raw_input)
    except json.JSONDecodeError as exc:
        print(
            json.dumps(
                {
                    "decision": "needs_more_detail",
                    "confidence": 0.0,
                    "rationale": f"Could not parse input JSON: {exc}",
                    "alternatives": [],
                }
            ),
            flush=True,
        )
        return

    # --- Extract features ---
    features = build_features(context)

    # --- Score all entries ---
    # is_agent_routable excludes the router agent (routable=False) and
    # plugin agents (source='plugin') from the scored pool.
    # The routable flag is set in agent frontmatter; issue #19 replaced
    # the hardcoded name check with this data-driven field.
    agent_entries = [
        e
        for e in entries
        if e.kind == "agent" and is_agent_routable(
            name=e.name, kind=e.kind, source=e.source, routable=e.routable
        )
    ]
    skill_entries = [e for e in entries if e.kind == "skill"]

    scored_agents: list[ScoredEntry] = sorted(
        [ScoredEntry(entry=e, score=score(e, features)) for e in agent_entries],
        key=lambda se: (-se.score, se.entry.name),
    )
    scored_skills: list[ScoredEntry] = sorted(
        [ScoredEntry(entry=e, score=score(e, features)) for e in skill_entries],
        key=lambda se: (-se.score, se.entry.name),
    )

    # --- Compose decision ---
    result = decide(scored_agents, scored_skills, features, entries)

    # --- Log decision (non-fatal: log failure never blocks stdout output) ---
    _write_log_entry(context, result, catalog_hash, _resolve_log_path())

    # --- Emit JSON ---
    print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
