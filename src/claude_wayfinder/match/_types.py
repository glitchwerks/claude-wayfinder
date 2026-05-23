"""Data-model types for the 7-decision dispatch matcher (v5, #210).

Defines the dataclasses and constants that represent the dispatch
catalog schema and the computed feature / score state.  All types are
immutable where possible (``frozen=True``) so they can be shared
safely across call-sites without defensive copying.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The seven valid routing decisions (v5 §3.1.4, updated v0.10.0 / #210).
# 'mixed_content' was added in v0.10.0 (#210): structural two-handed tasks
# where >= 2 agents clamp at 1.0 on path-disjoint lanes.
VALID_DECISIONS = frozenset(
    {
        "delegate",
        "self_handle",
        "self_handle_unaided",
        "advisory",
        "ask_user",
        "needs_more_detail",
        "mixed_content",
    }
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Keyword:
    """A single keyword trigger with its match weight.

    Attributes:
        term: Lowercase single-token trigger string.
        weight: Match weight in {0.25, 0.5, 1.0}.
    """

    term: str
    weight: float


@dataclass(frozen=True)
class Slot:
    """One slot in a keyword_group: a set of alternative terms (OR).

    Attributes:
        terms: Tuple of lowercase term strings. The slot is "filled"
            when at least one of these terms is in features.keywords.
        name: Optional human-readable label (e.g., "verbs", "nouns").
            Ignored by the matcher; surfaced in debug/rationale output.
    """

    terms: tuple[str, ...]
    name: str | None = None


@dataclass(frozen=True)
class KeywordGroup:
    """A conjunctive expression: AND-of-slots, each slot is OR-of-terms.

    Per spec § 3: group = AND-of-slots, slot = OR-of-terms. The group
    is "satisfied" when EVERY slot is filled. A satisfied group
    contributes ``_GROUP_MULTIPLIER * weight`` to the score and
    suppresses singleton contributions for any term named in any of
    its slots (replacement rule, spec D5).

    Attributes:
        slots: Tuple of Slots, length >= 2 (enforced at build time).
        weight: Float in {0.25, 0.5, 1.0} (validator enforces clamp).
    """

    slots: tuple[Slot, ...]
    weight: float


@dataclass(frozen=True)
class Triggers:
    """Parsed trigger block for one catalog entry.

    Attributes:
        command_prefixes: Slash commands that short-circuit to score 1.0.
        agent_mentions: Agent names whose explicit mention scores 1.0.
        path_globs: fnmatch-style globs matched against file paths.
        keywords: Weighted keyword terms matched against extracted tokens.
        keyword_groups: Conjunctive AND-group triggers. Each group is
            satisfied when every slot has >=1 term in
            features.keywords. See spec
            docs/superpowers/specs/2026-05-18-and-groups-design.md.
        tool_mentions: Tool names matched against features.tool_mentions.
        excludes: Terms that hard-zero the entry's score when present.
        path_globs_excluded: Path globs that, if any match the candidate
            file path, drop this entry from the scored pool. Exclusion
            wins over inclusion (``path_globs``). fnmatch semantics —
            include both bare and ``**/``-prefixed forms when matching
            root-level files (fnmatch does not expand ``**`` recursively
            across directory separators for bare filenames).
    """

    command_prefixes: frozenset[str]
    agent_mentions: frozenset[str]
    path_globs: tuple[str, ...]
    keywords: tuple[Keyword, ...]
    tool_mentions: frozenset[str]
    excludes: frozenset[str]
    keyword_groups: tuple[KeywordGroup, ...] = ()
    path_globs_excluded: tuple[str, ...] = ()


@dataclass(frozen=True)
class CatalogEntry:
    """One entry (agent or skill) from the dispatch catalog.

    Attributes:
        name: Unique entry name (e.g. ``"code-writer"``, ``"python"``).
        kind: Either ``"agent"`` or ``"skill"``.
        triggers: Parsed trigger configuration.
        applicable_agents: For skills: which agents may receive this skill.
        applicable_skills: For agents: which skills are applicable.
        source: Provenance of the entry — ``"owned"`` for first-party
            agents/skills and ``"plugin"`` for third-party plugins.
            Defaults to ``"owned"`` so existing catalog JSON without
            the field continues to load without modification.
        applicable_agents_intentional: Non-empty string documents why
            ``applicable_agents`` is deliberately empty on this skill
            (e.g. ``"router-only interactive skill"``).  When set, the
            ``empty-applicable-agents`` audit NIT is suppressed.
            Defaults to ``""`` so existing entries load without change.
    """

    name: str
    kind: str
    triggers: Triggers
    applicable_agents: tuple[str, ...]
    applicable_skills: tuple[str, ...]
    source: str = "owned"
    routable: bool = True
    applicable_agents_intentional: str = ""


@dataclass
class Features:
    """Extracted feature set from the dispatch context JSON.

    All string collections are lowercased and deduplicated.  The
    ``keywords`` set contains individual tokens split from the task
    description using whitespace and punctuation boundaries.

    Attributes:
        command_prefix: Single slash command string, or ``None``.
        agent_mentions: Explicit agent references in the prompt.
        keywords: Token set extracted from ``task_description``.
        paths: File/directory paths named in the task.
        extensions: File extensions (leading dot stripped, lowercased).
        tool_mentions: Explicit tool names mentioned.
    """

    command_prefix: str | None = None
    agent_mentions: frozenset[str] = field(default_factory=frozenset)
    keywords: frozenset[str] = field(default_factory=frozenset)
    paths: tuple[str, ...] = field(default_factory=tuple)
    extensions: frozenset[str] = field(default_factory=frozenset)
    tool_mentions: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class ScoredEntry:
    """A catalog entry paired with its computed score.

    Attributes:
        entry: The underlying catalog entry.
        score: Float in [0.0, 1.0] as computed by ``score()``.
    """

    entry: CatalogEntry
    score: float


@dataclass(frozen=True)
class LaneInfo:
    """Per-agent lane description in a ``mixed_content`` decision.

    Surfaces the matched paths and attached skills for one agent in a
    structural mixed-content task.  Two or more ``LaneInfo`` entries
    together fully describe the lane partition emitted by the matcher.

    Attributes:
        agent: Agent name (e.g. ``"code-writer"``).
        score: Final score for this agent, typically ``1.0``.
        matched_paths: Subset of input ``file_paths`` whose path globs
            claim this agent's lane.  Disjoint with every other lane's
            ``matched_paths``.
        skills: Skill names the matcher resolved for this agent (same
            list it would include in a ``delegate`` decision).
    """

    agent: str
    score: float
    matched_paths: tuple[str, ...]
    skills: tuple[str, ...]
