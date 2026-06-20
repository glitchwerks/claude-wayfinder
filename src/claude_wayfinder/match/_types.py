"""Data-model types for the 7-decision dispatch matcher (v5, #210).

Defines the dataclasses and constants that represent the dispatch
catalog schema and the computed feature / score state.  All types are
immutable where possible (``frozen=True``) so they can be shared
safely across call-sites without defensive copying.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Lazy import of the stemmer to avoid a circular-import at module load.
# The actual function is imported inside Keyword.__post_init__ so that the
# types module does not unconditionally depend on snowballstemmer at import
# time (useful for tests that mock or inspect the types module in isolation).
# The stem function is pulled once and cached as a module-level variable on
# first use.
_stem_fn = None


def _get_stem_fn():  # type: ignore[return]
    """Return the stem function, importing it on first call."""
    global _stem_fn
    if _stem_fn is None:
        from claude_wayfinder.match._stem import stem
        _stem_fn = stem
    return _stem_fn

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
        term: Lowercase single-token trigger string.  When ``no_stem``
            is ``False`` (the default), this field holds the Porter2 stem
            of the original catalog term so morphological variants of the
            same word route identically.  When ``no_stem`` is ``True``,
            this field holds the original term verbatim (no stemming
            applied); the matcher then checks it against
            ``features.raw_keywords`` rather than ``features.keywords``.
        weight: Match weight in {0.25, 0.5, 1.0}.
        no_stem: When ``True`` this term is opted out of stemming.
            Defaults to ``False`` for back-compat — existing catalogs
            without this field behave identically to before.
    """

    term: str
    weight: float
    no_stem: bool = False

    def __post_init__(self) -> None:
        """Apply Porter2 stemming to ``term`` unless ``no_stem`` is True.

        Stemming is applied at construction time so that ``Keyword.term``
        always holds the form that will be checked against
        ``features.keywords`` (stems) in the scoring engine.  When
        ``no_stem=True`` the term is lowercased but NOT stemmed; the scorer
        then checks it against ``features.raw_keywords`` instead.

        This ``__post_init__`` runs regardless of how a ``Keyword`` is
        constructed — through ``_parse_triggers``, directly in tests, or
        via ``audit_catalog`` — ensuring consistent stem normalization
        across all code paths.
        """
        # Normalise case always.
        lowered = self.term.lower()
        # Apply stemming only for normal (non-opted-out) terms.
        stored = lowered if self.no_stem else _get_stem_fn()(lowered)
        # frozen=True means we must use object.__setattr__ to mutate.
        object.__setattr__(self, "term", stored)


@dataclass(frozen=True)
class Slot:
    """One slot in a keyword_group: a set of alternative terms (OR).

    Attributes:
        terms: Tuple of Porter2-stemmed lowercase term strings.  The
            slot is "filled" when at least one of these stems is in
            ``features.keywords`` (also stems).  Stemming is applied
            automatically in ``__post_init__`` so that direct
            construction and parse-time construction behave identically.
        name: Optional human-readable label (e.g., "verbs", "nouns").
            Ignored by the matcher; surfaced in debug/rationale output.
    """

    terms: tuple[str, ...]
    name: str | None = None

    def __post_init__(self) -> None:
        """Stem all terms in the slot using Porter2.

        Applied at construction time so ``Slot.terms`` always contains
        stems, regardless of whether the slot was created by
        ``_parse_slot`` or directly in tests.
        """
        stemmed = tuple(_get_stem_fn()(t.lower()) for t in self.terms)
        object.__setattr__(self, "terms", stemmed)


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
    ``keywords`` set contains Porter2-stemmed individual tokens split
    from the task description.  The ``raw_keywords`` set contains the
    same tokens WITHOUT stemming applied; it is used to match catalog
    terms that have ``no_stem: true`` so those terms preserve their
    exact-match semantics.

    Attributes:
        command_prefix: Single slash command string, or ``None``.
        agent_mentions: Explicit agent references in the prompt.
        keywords: Stemmed token set extracted from ``task_description``.
            This is the primary matching surface for normal (stemmed)
            catalog keywords.
        raw_keywords: Unstemmed token set from ``task_description``.
            Used exclusively to match catalog keywords with
            ``no_stem=True`` so that acronyms and product names are
            matched verbatim.
        paths: File/directory paths named in the task.
        extensions: File extensions (leading dot stripped, lowercased).
        tool_mentions: Explicit tool names mentioned.
    """

    command_prefix: str | None = None
    agent_mentions: frozenset[str] = field(default_factory=frozenset)
    keywords: frozenset[str] = field(default_factory=frozenset)
    raw_keywords: frozenset[str] = field(default_factory=frozenset)
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


@dataclass(frozen=True)
class OverrideRule:
    """A deterministic override rule.

    See docs/dispatch-overrides.md.

    A rule matches when ALL of its non-empty predicates are satisfied.
    A rule with zero predicates is invalid (caught by audit-catalog).

    Attributes:
        id: Stable rule identifier (kebab-case, unique within the file).
        decision: One of VALID_DECISIONS; the verbatim decision to emit.
        agent: Agent name when decision implies one; None otherwise.
        skills: Skill names emitted verbatim into the decision output.
        confidence: Float in [0.0, 1.0] surfaced as the decision
            confidence.
        rationale: Human-readable string surfaced as the decision
            rationale.
        command_prefix: Exact-string match for context.command_prefix,
            or None.
        path_globs: fnmatch globs; rule matches when ANY path matches
            ANY glob.
        tool_mentions: Rule matches when intersection with context tools
            is non-empty.
    """

    id: str
    decision: str
    agent: str | None
    skills: tuple[str, ...]
    confidence: float
    rationale: str
    command_prefix: str | None
    path_globs: tuple[str, ...]
    tool_mentions: frozenset[str]


@dataclass(frozen=True)
class OverrideMatch:
    """Result of a successful override resolution.

    Attributes:
        rule: The matched OverrideRule.
        matched_predicates: Names of predicates that contributed to the
            match.
    """

    rule: OverrideRule
    matched_predicates: tuple[str, ...]


@dataclass(frozen=True)
class Labels:
    """Caller-supplied two-axis routing labels (domain × posture).

    Added in M15-2 (#419) as the shippable label carrier for
    ``compose_route``.  Separate from ``Features`` per design decision
    D-LBL1: labels are caller-supplied structured annotations, not
    lexically-extracted signals.

    Attributes:
        domain: Domain label (e.g. ``"code"``, ``"infra_deploy"``,
            ``"docs_prose"``, ``"project_meta"``, ``"is_any"``), or
            ``None`` when unknown.
        posture: Posture label (e.g. ``"build"``, ``"diagnose"``,
            ``"assess"``, ``"critique"``, ``"verify"``, ``"plan"``,
            ``"research"``, ``"operate"``), or ``None`` when unknown.
        confidence: Caller-asserted labeling confidence — one of
            ``"high"``, ``"medium"``, ``"low"``, or ``None``.  Absent
            / ``None`` is treated as LOW by ``compose_route`` (§D.1
            fail-safe): the posture route is blocked until the caller
            asserts ``"high"``.
        area_span: Number of distinct technical layers the task spans.
            Defaults to ``1``; values ``< 1`` are coerced to ``1`` by
            ``parse_labels``.  A value ``>= 2`` triggers the
            broad-diagnose → investigator branch (Branch 1, #396/#411).
    """

    domain: str | None = None
    posture: str | None = None
    confidence: str | None = None
    area_span: int = 1
