"""Keyword extraction, scoring, and feature density for the dispatcher.

Core matching logic: tokenises the task description, computes feature
vectors, evaluates keyword groups and singletons, and computes a float
score in [0.0, 1.0] for each catalog entry.

Stemming (issue #304): ``extract_keywords`` applies Porter2 stemming to
every token via :func:`claude_wayfinder.match._stem.stem`.  Catalog terms
are stored as their Porter2 stems at catalog-build time (in the
``stemmed_terms`` field).  The in-memory :class:`Keyword` object's
``term`` field already holds the stem when the catalog is loaded via
:func:`_parse._parse_triggers`.  The scoring membership check
``k.term in features.keywords`` therefore compares stem→stem and
morphological variants route identically without any change to the scoring
formula or decision thresholds.

Terms with ``no_stem=True`` bypass stemming on the catalog side (their
``Keyword.term`` is verbatim).  They are matched against
``features.raw_keywords`` (the unstemmed token set) rather than
``features.keywords``.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any

from claude_wayfinder.match._stem import stem as _stem_word
from claude_wayfinder.match._types import (
    CatalogEntry,
    Features,
    KeywordGroup,
    ScoredEntry,
)
from claude_wayfinder.match_filters import is_agent_routable

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Punctuation to strip when tokenising the task description.
# We preserve hyphens inside words (e.g. "git-rebase") but strip
# leading/trailing punctuation.  Simple approach: replace any char
# that is not alphanumeric or hyphen with a space, then split.
_TOKEN_RE = re.compile(r"[^a-z0-9\-]+")

# Per-keyword score multiplier (v5 §3.1.2).
# A weight-1.0 keyword contributes exactly this value; lower weights
# scale proportionally.  Must be >= _SKILL_MIN so a single primary
# keyword alone can clear the attachment threshold.  Raised from 0.3
# to 0.5 to fix single-keyword skills never attaching.
_KEYWORD_MULTIPLIER = 0.5

# Per-group score multiplier (spec D4 in
# docs/superpowers/specs/2026-05-18-and-groups-design.md).
# Distinct from _KEYWORD_MULTIPLIER (0.5) so a satisfied group can carry
# more signal than any single keyword: a weight-1.0 group contributes 1.0
# (solo-decides delegate), while a weight-0.5 group contributes 0.5
# (attachment-only).
_GROUP_MULTIPLIER = 1.0

# Score threshold for skill attachment.
_SKILL_MIN = 0.5

# Maximum skills returned with a decision (v5 §3.1.3).
_MAX_SKILLS = 3


# ---------------------------------------------------------------------------
# Keyword extraction
# ---------------------------------------------------------------------------

def _raw_tokens(text: str) -> frozenset[str]:
    """Extract raw lowercase tokens from *text* (no stemming applied).

    Shared internal helper used by both :func:`extract_keywords` (which
    then stems the result) and :func:`build_features` (which preserves
    the raw form in ``Features.raw_keywords`` for ``no_stem`` matching).

    Args:
        text: Raw task description string.

    Returns:
        Frozenset of lowercase token strings without stemming.
    """
    lowered = text.lower()
    spaced = _TOKEN_RE.sub(" ", lowered)
    return frozenset(t for t in spaced.split() if t)


def extract_keywords(text: str) -> frozenset[str]:
    """Extract Porter2-stemmed tokens from a task description.

    Algorithm (issue #304 — stemming integration):

    1. Lowercase the entire string.
    2. Replace all non-alphanumeric, non-hyphen characters with spaces.
    3. Split on whitespace.
    4. Drop empty strings.
    5. Apply Porter2 stemming to each token via
       :func:`claude_wayfinder.match._stem.stem`.
    6. Deduplicate into a frozenset.

    Hyphens inside tokens are preserved so ``"git-rebase"`` stays as
    one token and can match a trigger term ``"git-rebase"``.

    Catalog keywords are also stored as their Porter2 stems at build
    time (``stemmed_terms`` field).  The in-memory ``Keyword.term``
    holds the stem, so scoring is a simple set-membership check between
    two stem-sets — no change to the scoring formula.

    Args:
        text: Raw task description string.

    Returns:
        Frozenset of Porter2-stemmed lowercase token strings.
    """
    return frozenset(_stem_word(t) for t in _raw_tokens(text))


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------


def build_features(context: dict[str, Any]) -> Features:
    """Build a ``Features`` object from the dispatch context JSON.

    Normalises all string values to lowercase and deduplicates.
    File extensions are derived from ``file_paths`` (leading dot stripped).

    Two keyword sets are populated:

    - ``keywords``: Porter2-stemmed tokens (primary matching surface).
    - ``raw_keywords``: Unstemmed tokens used only for catalog keywords
      with ``no_stem=True`` (acronyms, product names, etc.).

    Args:
        context: Parsed dispatch context dict (from stdin).

    Returns:
        A fully-populated ``Features`` instance.
    """
    task = str(context.get("task_description", ""))
    raw_kws = _raw_tokens(task)
    stemmed_kws = frozenset(_stem_word(t) for t in raw_kws)

    raw_paths: list[str] = [str(p) for p in context.get("file_paths", [])]
    paths = tuple(raw_paths)

    # Derive extensions from file paths: strip leading dot, lowercase.
    extensions: set[str] = set()
    for p in raw_paths:
        suffix = Path(p).suffix
        if suffix:
            extensions.add(suffix.lstrip(".").lower())

    raw_agents: list[str] = [
        str(a).lower() for a in context.get("agent_mentions", [])
    ]
    raw_tools: list[str] = [
        str(t).lower() for t in context.get("tool_mentions", [])
    ]

    cmd_prefix_raw = context.get("command_prefix")
    command_prefix = str(cmd_prefix_raw).lower() if cmd_prefix_raw else None

    return Features(
        command_prefix=command_prefix,
        agent_mentions=frozenset(raw_agents),
        keywords=stemmed_kws,
        raw_keywords=raw_kws,
        paths=paths,
        extensions=frozenset(extensions),
        tool_mentions=frozenset(raw_tools),
    )


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _matched_glob_count(entry: CatalogEntry, features: Features) -> int:
    """Count distinct globs from the entry that match any feature path.

    Each glob is counted at most once even if it matches multiple paths
    (per v5 §3.1.2 / docs/design/trigger-schema.md §4).

    Matching uses ``fnmatch.fnmatch`` per docs/design/trigger-schema.md §2d.
    Note that ``fnmatch`` does not treat ``**`` as a recursive wildcard;
    ``**/*.py`` matches ``src/main.py`` (because fnmatch expands ``*``
    greedily within a segment) but the exact expansion depends on the
    path separator.  Authors should follow the catalog generator's
    conventions when writing globs.

    Args:
        entry: The catalog entry whose ``path_globs`` are tested.
        features: The extracted feature set.

    Returns:
        Integer count of globs that matched at least one path.
    """
    count = 0
    for glob in entry.triggers.path_globs:
        for path in features.paths:
            # Normalise path separators to forward slash for
            # consistent cross-platform fnmatch behaviour.
            normalised = path.replace("\\", "/")
            if fnmatch.fnmatch(normalised, glob):
                count += 1
                break
    return count


def matched_paths_for(
    entry: CatalogEntry, features: Features
) -> list[str]:
    """Return the subset of feature paths claimed by an entry's path globs.

    A path is claimed when at least one of the entry's ``path_globs``
    matches it (after normalising separators to ``/``).  The function
    respects ``path_globs_excluded``: paths excluded by the entry's
    exclusion globs are never returned, even if an inclusion glob also
    matches them.

    This is the path-level counterpart to ``_matched_glob_count``, which
    counts matched *globs*; this function returns matched *paths*.  The
    distinction matters for lane partitioning in ``mixed_content``
    detection: two agents may share globs (e.g. ``**/*.md``) but still
    partition cleanly across disjoint input paths.

    Args:
        entry: The catalog entry whose globs are tested.
        features: The extracted feature set.

    Returns:
        List of input paths (original form, not normalised) that at least
        one of the entry's ``path_globs`` matches and no
        ``path_globs_excluded`` pattern matches.
    """
    t = entry.triggers
    claimed: list[str] = []
    for path in features.paths:
        normalised = path.replace("\\", "/")
        # Exclusion wins: skip if any exclusion glob matches.
        if any(fnmatch.fnmatch(normalised, excl) for excl in t.path_globs_excluded):
            continue
        # Claim if any inclusion glob matches.
        if any(fnmatch.fnmatch(normalised, g) for g in t.path_globs):
            claimed.append(path)
    return claimed


def group_satisfied(group: KeywordGroup, features: Features) -> bool:
    """Return True iff every slot has at least one term in features.keywords.

    Public helper shared by ``score()`` and rationale composition so
    both use the identical predicate with no duplication.

    Args:
        group: The ``KeywordGroup`` to evaluate.
        features: Current feature set.

    Returns:
        ``True`` when all slots are satisfied, ``False`` otherwise.
    """
    return all(
        any(term in features.keywords for term in slot.terms)
        for slot in group.slots
    )


def score(entry: CatalogEntry, features: Features) -> float:
    """Compute the match score for one catalog entry against features.

    Implements the scoring formula from spec §5
    (docs/superpowers/specs/2026-05-18-and-groups-design.md)::

        if command_prefix matches → return 1.0
        if agent_mention matches → return 1.0
        if any exclude term in features.keywords → return 0.0
        s  = 0
        s += 0.4 * matched_glob_count
        s += 0.5 * count of matching tool_mentions
        # Group evaluation (collect suppressed terms):
        suppressed = set()
        for group in keyword_groups:
            if all slots filled:
                s += _GROUP_MULTIPLIER * group.weight
                suppressed |= union of slot.terms
        # Singletons (skip suppressed terms):
        s += sum(_KEYWORD_MULTIPLIER * k.weight
                 for k in keywords if k.term matched AND k.term not in suppressed)
        return min(s, 1.0)

    Args:
        entry: One catalog entry to score.
        features: The extracted feature set.

    Returns:
        Float score in [0.0, 1.0].
    """
    t = entry.triggers

    # Short-circuit: exact command prefix match.
    if features.command_prefix and features.command_prefix in t.command_prefixes:
        return 1.0

    # Short-circuit: explicit agent mention.
    if any(m in features.agent_mentions for m in t.agent_mentions):
        return 1.0

    # Hard zero: exclude term present in task keywords (stemmed or raw).
    # Excludes are checked against both stemmed and raw keyword sets so
    # that exclusions work regardless of whether the term was stemmed.
    if any(
        x in features.keywords or x in features.raw_keywords for x in t.excludes
    ):
        return 0.0

    s = 0.0
    # Path glob contributions: 0.4 per matched glob (each counted once).
    # Per-path-subtractive semantics (#287): paths matching
    # path_globs_excluded contribute 0 to path score; other paths are
    # unaffected.  Build a filtered Features view that excludes those
    # paths before delegating to _matched_glob_count.
    if t.path_globs_excluded and features.paths:
        included_paths = tuple(
            p for p in features.paths
            if not any(
                fnmatch.fnmatch(p.replace("\\", "/"), excl)
                for excl in t.path_globs_excluded
            )
        )
        filtered = Features(
            command_prefix=features.command_prefix,
            agent_mentions=features.agent_mentions,
            keywords=features.keywords,
            paths=included_paths,
            extensions=features.extensions,
            tool_mentions=features.tool_mentions,
        )
        s += 0.4 * _matched_glob_count(entry, filtered)
    else:
        s += 0.4 * _matched_glob_count(entry, features)
    # Tool mention contributions: 0.5 per matched tool.
    s += 0.5 * len(
        [t_name for t_name in t.tool_mentions if t_name in features.tool_mentions]
    )

    # Keyword group evaluation (spec §5).
    # A group is satisfied when every slot has at least one term in
    # features.keywords. Satisfied groups contribute _GROUP_MULTIPLIER *
    # weight and suppress singletons for terms named in any of the
    # group's slots (replacement rule, spec D5).
    suppressed: set[str] = set()
    for group in t.keyword_groups:
        if group_satisfied(group, features):
            s += _GROUP_MULTIPLIER * group.weight
            for slot in group.slots:
                suppressed.update(slot.terms)

    # Keyword contributions: _KEYWORD_MULTIPLIER * weight per matched
    # term, EXCEPT terms covered by a satisfied group (suppressed).
    #
    # Stemming split (issue #304):
    # - Normal keywords (no_stem=False): k.term holds the Porter2 stem;
    #   matched against features.keywords (also stems).
    # - no_stem keywords (no_stem=True): k.term holds the verbatim term;
    #   matched against features.raw_keywords (unstemmed tokens).
    s += sum(
        _KEYWORD_MULTIPLIER * k.weight
        for k in t.keywords
        if k.term not in suppressed
        and (
            (not k.no_stem and k.term in features.keywords)
            or (k.no_stem and k.term in features.raw_keywords)
        )
    )
    return min(s, 1.0)


# ---------------------------------------------------------------------------
# Feature density
# ---------------------------------------------------------------------------


def feature_count(features: Features) -> int:
    """Count the number of populated input dimensions.

    Dimensions:
    - ``command_prefix`` is set (1 point)
    - ``agent_mentions`` is non-empty (1 point)
    - At least one keyword matched against any catalog entry's keywords
      (1 point — but computed lazily here as "keywords set non-empty")
    - ``paths`` is non-empty (1 point)
    - ``extensions`` is non-empty (1 point)
    - ``tool_mentions`` is non-empty (1 point)

    Per v5 §3.1.3 the check is ``< 2`` → ``needs_more_detail``.  This
    counts raw populated dimensions from the input, not matched ones.

    Args:
        features: Extracted feature set.

    Returns:
        Integer count of populated input dimensions.
    """
    n = 0
    if features.command_prefix:
        n += 1
    if features.agent_mentions:
        n += 1
    if features.keywords:
        n += 1
    if features.paths:
        n += 1
    if features.extensions:
        n += 1
    if features.tool_mentions:
        n += 1
    return n


# ---------------------------------------------------------------------------
# Skills resolution
# ---------------------------------------------------------------------------


def _skills_for_agent(
    agent_entry: CatalogEntry,
    scored_skills: list[ScoredEntry],
    features: Features,
) -> list[str]:
    """Return skill names applicable to an agent, sorted by score desc.

    Filters ``scored_skills`` to those where:
    1. ``applicable_agents`` contains the agent name OR ``"*"``.
    2. Score >= ``_SKILL_MIN``.

    Args:
        agent_entry: The winning agent entry.
        scored_skills: All scored skill entries (sorted by score desc).
        features: Current feature set (unused but kept for future use).

    Returns:
        List of skill names (up to ``_MAX_SKILLS``), highest score first.
    """
    applicable: list[str] = []
    for se in scored_skills:
        if se.score < _SKILL_MIN:
            continue
        aa = se.entry.applicable_agents
        if "*" in aa or agent_entry.name in aa:
            applicable.append(se.entry.name)
        if len(applicable) >= _MAX_SKILLS:
            break
    return applicable


# ---------------------------------------------------------------------------
# Scored-entry lists (used by main orchestration)
# ---------------------------------------------------------------------------


def score_entries(
    entries: list[CatalogEntry],
    features: Features,
) -> tuple[list[ScoredEntry], list[ScoredEntry]]:
    """Score all catalog entries and return sorted agent/skill lists.

    Args:
        entries: All catalog entries from the loaded catalog.
        features: The extracted feature set.

    Returns:
        Tuple of ``(scored_agents, scored_skills)`` where each is a list
        of ``ScoredEntry`` objects sorted by score descending (ties broken
        by name alphabetically).  Agents are filtered through
        ``is_agent_routable``.
    """
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
    return scored_agents, scored_skills
