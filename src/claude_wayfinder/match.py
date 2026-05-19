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
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NoReturn

from claude_wayfinder.match_filters import is_agent_routable

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The seven valid routing decisions (v5 §3.1.4).
VALID_DECISIONS = frozenset(
    {
        "delegate",
        "self_handle",
        "self_handle_unaided",
        "advisory",
        "ambiguous",
        "ask_user",
        "needs_more_detail",
    }
)

# Minimum number of populated input dimensions required before the
# matcher will attempt routing.  Below this threshold the matcher
# returns ``needs_more_detail`` (v5 §3.1.3).
_MIN_FEATURE_DENSITY = 2

# Score thresholds from the decision ladder (v5 §3.1.3 / §3.1.4).
_DELEGATE_THRESHOLD = 0.85
_DELEGATE_GAP = 0.2
_AMBIGUOUS_MIN = 0.5
_SKILL_MIN = 0.5
_ADVISORY_MIN = 0.5

# Maximum skills returned with a decision (v5 §3.1.3).
_MAX_SKILLS = 3

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

# Catalog error banner prefix (v5 §3.1.6).
_CATALOG_ERROR_PREFIX = "[CATALOG ERROR]"

# Punctuation to strip when tokenising the task description.
# We preserve hyphens inside words (e.g. "git-rebase") but strip
# leading/trailing punctuation.  Simple approach: replace any char
# that is not alphanumeric or hyphen with a space, then split.
_TOKEN_RE = re.compile(r"[^a-z0-9\-]+")


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
    """

    command_prefixes: frozenset[str]
    agent_mentions: frozenset[str]
    path_globs: tuple[str, ...]
    keywords: tuple[Keyword, ...]
    tool_mentions: frozenset[str]
    excludes: frozenset[str]
    keyword_groups: tuple[KeywordGroup, ...] = ()


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
    """

    name: str
    kind: str
    triggers: Triggers
    applicable_agents: tuple[str, ...]
    applicable_skills: tuple[str, ...]
    source: str = "owned"
    routable: bool = True


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


# ---------------------------------------------------------------------------
# Keyword extraction
# ---------------------------------------------------------------------------

def extract_keywords(text: str) -> frozenset[str]:
    """Extract lowercase single tokens from a task description.

    The algorithm is intentionally simple (v5 §11.1 defers stemming /
    lemmatization to post-launch tuning):

    1. Lowercase the entire string.
    2. Replace all non-alphanumeric, non-hyphen characters with spaces.
    3. Split on whitespace.
    4. Drop empty strings.
    5. Deduplicate into a frozenset.

    Hyphens inside tokens are preserved so ``"git-rebase"`` stays as
    one token and can match a trigger term ``"git-rebase"``.

    Args:
        text: Raw task description string.

    Returns:
        Frozenset of lowercase token strings.
    """
    lowered = text.lower()
    spaced = _TOKEN_RE.sub(" ", lowered)
    return frozenset(t for t in spaced.split() if t)


# ---------------------------------------------------------------------------
# Catalog loading
# ---------------------------------------------------------------------------


def _resolve_catalog_path(
    explicit_path: str | Path | None = None,
) -> Path:
    """Return the catalog file path from the explicit arg or env var.

    Resolution order (first match wins):

    1. ``explicit_path`` argument — supplied via ``--catalog-path`` CLI
       flag.
    2. ``DISPATCH_CATALOG_PATH`` env var.
    3. **Fail loud** — emits a ``[CATALOG ERROR]`` banner on stderr and
       exits with code 2.

    The previous three-step lookup (env var, home-env middle step,
    platform default) has been reduced to two steps — env var or explicit
    arg, else fail loud (Issue #10).  Callers that previously relied on
    the default must now supply an explicit path or set
    ``DISPATCH_CATALOG_PATH``.

    Args:
        explicit_path: Path supplied by the caller (e.g. ``--catalog-path``
            CLI flag).  ``None`` falls through to the env var.

    Returns:
        Resolved ``Path`` to the catalog file.  The file may not exist;
        callers are responsible for checking.

    Raises:
        SystemExit: With code 2 when no path source is available.
    """
    if explicit_path is not None:
        return Path(explicit_path)
    env_val = os.environ.get("DISPATCH_CATALOG_PATH")
    if env_val:
        return Path(env_val)
    _emit_catalog_error(
        "no catalog path specified — pass --catalog-path <path> "
        "or set DISPATCH_CATALOG_PATH"
    )


def _resolve_log_path() -> Path | None:
    """Return the dispatch log file path from env, or None to disable logging.

    Resolution order:

    1. ``DISPATCH_LOG_PATH`` env var — absolute override.
    2. ``None`` — logging is silently disabled (no ``~/.claude/`` fallback).

    The previous ``~/.claude/state/dispatch-log.jsonl`` platform default
    has been removed (Issue #10).  When ``DISPATCH_LOG_PATH`` is absent,
    log writing is skipped without error.

    Returns:
        ``Path`` to the log file, or ``None`` when logging is disabled.
    """
    explicit = os.environ.get("DISPATCH_LOG_PATH")
    if explicit:
        return Path(explicit).expanduser()
    return None


def _compute_catalog_hash(catalog_data: dict[str, Any] | str | bytes) -> str:
    """Return a stable SHA-256 digest of the catalog content.

    Normalises the catalog before hashing so that whitespace and
    key-order variations produce the same digest.  If ``catalog_data``
    is already a ``str`` or ``bytes`` it is re-parsed as JSON first to
    guarantee normalisation.

    Args:
        catalog_data: The catalog content as a parsed dict, a JSON
            string, or UTF-8-encoded JSON bytes.

    Returns:
        Hash string in the form ``"sha256:<64-hex-digits>"``.
    """
    if isinstance(catalog_data, (str, bytes)):
        if isinstance(catalog_data, bytes):
            catalog_data = catalog_data.decode("utf-8")
        catalog_data = json.loads(catalog_data)
    normalised = json.dumps(
        catalog_data, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    hexdigest = hashlib.sha256(normalised).hexdigest()
    return f"sha256:{hexdigest}"


def _get_matcher_version() -> str:
    """Return a stable identifier for the current matcher revision.

    Attempts to read the short git SHA from the repository that contains
    this file.  Falls back to the string ``"unknown"`` on any failure
    (missing git binary, not a git repo, subprocess timeout, etc.).

    Returns:
        Short git SHA string, or ``"unknown"`` if unavailable.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).parent,
            capture_output=True,
            text=True,
            timeout=1,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:  # any failure is acceptable here
        pass
    return "unknown"


def _write_log_entry(
    input_dict: dict[str, Any],
    output_dict: dict[str, Any],
    catalog_hash: str,
    log_path: Path | None,
) -> None:
    """Append one decision record to the dispatch log file.

    The entry is written as newline-delimited JSON (NDJSON).  If the
    parent directory does not exist it is created.  All I/O errors are
    caught and emitted to stderr; this function never raises.

    When ``log_path`` is ``None`` (logging disabled — no
    ``DISPATCH_LOG_PATH`` env var was set), the function returns
    immediately without writing or emitting any message.

    Args:
        input_dict: The parsed dispatch context (stdin JSON).
        output_dict: The matcher decision (stdout JSON).
        catalog_hash: SHA-256 digest of the catalog used, from
            ``_compute_catalog_hash``.
        log_path: Path to the ``.jsonl`` log file, or ``None`` to
            silently skip log writing.
    """
    if log_path is None:
        return
    entry: dict[str, Any] = {
        "type": "matcher_decision",
        "ts": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z",
        "session_id": os.environ.get("CLAUDE_SESSION_ID", ""),
        "input": input_dict,
        "output": output_dict,
        "catalog_hash": catalog_hash,
        "matcher_version": _get_matcher_version(),
    }
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except (OSError, ValueError) as err:
        print(f"[match.py] log write failed: {err}", file=sys.stderr)


def _parse_slot(raw: Any) -> Slot | None:
    """Parse one slot from a raw catalog value.

    Accepts two forms (matcher is lenient; builder normalizes to dict):

    - Bare list of strings: ``['a', 'b']``
    - Dict with terms (+ optional name):
      ``{'terms': ['a', 'b'], 'name': 'verbs'}``

    Returns ``None`` for malformed input (group containing this slot
    will be silently dropped — fatal validation lives in
    build_catalog.py).

    Args:
        raw: Unvalidated catalog value for a single slot entry.

    Returns:
        A ``Slot`` instance, or ``None`` if the input is malformed.
    """
    if isinstance(raw, list):
        terms = tuple(
            str(t).lower() for t in raw if isinstance(t, str)
        )
        if not terms:
            return None
        return Slot(terms=terms, name=None)
    if isinstance(raw, dict):
        raw_terms = raw.get("terms")
        if not isinstance(raw_terms, list):
            return None
        terms = tuple(
            str(t).lower() for t in raw_terms if isinstance(t, str)
        )
        if not terms:
            return None
        name_val = raw.get("name")
        name = str(name_val) if isinstance(name_val, str) else None
        return Slot(terms=terms, name=name)
    return None


def _parse_keyword_group(raw: Any) -> KeywordGroup | None:
    """Parse one keyword_group from a raw catalog value.

    Returns ``None`` when the group is malformed; build_catalog.py is
    responsible for emitting fatal/warning issues at catalog build
    time.  The matcher silently drops malformed entries so a corrupted
    catalog degrades gracefully rather than crashing at dispatch time.

    Args:
        raw: Unvalidated catalog value for a single keyword_group.

    Returns:
        A ``KeywordGroup`` instance, or ``None`` if the input is
        malformed.
    """
    if not isinstance(raw, dict):
        return None
    raw_slots = raw.get("slots")
    if not isinstance(raw_slots, list) or len(raw_slots) < 2:
        return None
    slots: list[Slot] = []
    for raw_slot in raw_slots:
        slot = _parse_slot(raw_slot)
        if slot is None:
            return None
        slots.append(slot)
    weight = raw.get("weight")
    if not isinstance(weight, (int, float)) or isinstance(weight, bool):
        return None
    return KeywordGroup(slots=tuple(slots), weight=float(weight))


def _parse_triggers(raw: dict[str, Any]) -> Triggers:
    """Parse the raw ``triggers`` dict from a catalog entry.

    Missing fields default to empty collections per the schema.
    Unknown fields are silently ignored (forward compat).

    Args:
        raw: The ``triggers`` sub-object from a catalog entry.

    Returns:
        A ``Triggers`` instance with all fields populated.
    """
    keywords: list[Keyword] = []
    for kw in raw.get("keywords", []):
        if isinstance(kw, dict) and "term" in kw and "weight" in kw:
            keywords.append(
                Keyword(
                    term=str(kw["term"]).lower(),
                    weight=float(kw["weight"]),
                )
            )

    keyword_groups: list[KeywordGroup] = []
    for raw_group in raw.get("keyword_groups", []):
        group = _parse_keyword_group(raw_group)
        if group is not None:
            keyword_groups.append(group)

    return Triggers(
        command_prefixes=frozenset(
            str(x).lower() for x in raw.get("command_prefixes", [])
        ),
        agent_mentions=frozenset(
            str(x).lower() for x in raw.get("agent_mentions", [])
        ),
        path_globs=tuple(str(x) for x in raw.get("path_globs", [])),
        keywords=tuple(keywords),
        tool_mentions=frozenset(
            str(x).lower() for x in raw.get("tool_mentions", [])
        ),
        excludes=frozenset(
            str(x).lower() for x in raw.get("excludes", [])
        ),
        keyword_groups=tuple(keyword_groups),
    )


def load_catalog(path: Path) -> list[CatalogEntry]:
    """Load and parse the dispatch catalog JSON file.

    Args:
        path: Resolved path to ``dispatch-catalog.json``.

    Returns:
        List of ``CatalogEntry`` objects.

    Raises:
        FileNotFoundError: If the catalog file does not exist.
        json.JSONDecodeError: If the file contains malformed JSON.
        ValueError: If the catalog has zero entries.
    """
    raw_text = path.read_text(encoding="utf-8")
    catalog = json.loads(raw_text)
    raw_entries: list[dict[str, Any]] = catalog.get("entries", [])
    if not raw_entries:
        raise ValueError("Catalog contains zero entries.")

    entries: list[CatalogEntry] = []
    for raw in raw_entries:
        triggers_raw = raw.get("triggers", {})
        triggers = _parse_triggers(
            triggers_raw if isinstance(triggers_raw, dict) else {}
        )
        entries.append(
            CatalogEntry(
                name=str(raw.get("name", "")),
                kind=str(raw.get("kind", "")),
                triggers=triggers,
                applicable_agents=tuple(raw.get("applicable_agents", [])),
                applicable_skills=tuple(raw.get("applicable_skills", [])),
                source=str(raw.get("source", "owned")),
                routable=bool(raw.get("routable", True)),
            )
        )
    return entries


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------


def build_features(context: dict[str, Any]) -> Features:
    """Build a ``Features`` object from the dispatch context JSON.

    Normalises all string values to lowercase and deduplicates.
    File extensions are derived from ``file_paths`` (leading dot stripped).

    Args:
        context: Parsed dispatch context dict (from stdin).

    Returns:
        A fully-populated ``Features`` instance.
    """
    task = str(context.get("task_description", ""))
    keywords = extract_keywords(task)

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
        keywords=keywords,
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

    Matching uses ``fnmatch.fnmatch`` per docs/design/trigger-schema.md §2d.  Note
    that ``fnmatch`` does not treat ``**`` as a recursive wildcard;
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


def score(entry: CatalogEntry, features: Features) -> float:
    """Compute the match score for one catalog entry against features.

    Implements the scoring formula from v5 §3.1.2 exactly::

        if command_prefix matches → return 1.0
        if agent_mention matches → return 1.0
        if any exclude term in features.keywords → return 0.0
        s = 0
        s += 0.4 * matched_glob_count(entry, features)
        s += sum(0.5 * k.weight for matching keywords)
        s += 0.5 * count of matching tool_mentions
        return min(s, 1.0)

    Note: ``file_extensions`` is removed from the schema.
    The original v5 formula included ``0.4 * file_extensions``; this
    implementation omits it and uses path_globs exclusively, consistent
    with docs/design/trigger-schema.md §4.

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

    # Hard zero: exclude term present in task keywords.
    if any(x in features.keywords for x in t.excludes):
        return 0.0

    s = 0.0
    # Path glob contributions: 0.4 per matched glob (each counted once).
    s += 0.4 * _matched_glob_count(entry, features)
    # Keyword contributions: _KEYWORD_MULTIPLIER * weight per matched term.
    s += sum(
        _KEYWORD_MULTIPLIER * k.weight
        for k in t.keywords
        if k.term in features.keywords
    )
    # Tool mention contributions: 0.5 per matched tool.
    s += 0.5 * len(
        [t_name for t_name in t.tool_mentions if t_name in features.tool_mentions]
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


def _emit_catalog_error(details: str) -> NoReturn:
    """Write the catalog-degraded banner to stderr and exit 2.

    Args:
        details: Human-readable description of the degradation.
    """
    banner = (
        f"{_CATALOG_ERROR_PREFIX} Dispatch catalog is degraded: {details}. "
        "Until restored, routing falls back to LLM judgment per the "
        "legacy prose-policy."
    )
    print(banner, file=sys.stderr)
    sys.exit(2)


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
    except ValueError as exc:
        _emit_catalog_error(str(exc))

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
