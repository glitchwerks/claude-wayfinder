"""Deterministic 7-decision dispatch matcher for the router (v5, #210).

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
      | python -m claude_wayfinder.match --catalog-path /path/to/dispatch-catalog.json

See ``docs/schema.md`` §4 for the scoring and decision algorithm this
module implements, and ``docs/design.md`` for the design rationale.

Public surface
--------------
The names below are importable directly from ``claude_wayfinder.match``
and form the stable public API used by ``_dispatch.py`` and tests.

Dataclasses:
    CatalogEntry, Features, Keyword, KeywordGroup, LaneInfo, ScoredEntry,
    Slot, Triggers

Functions:
    build_features, decide, group_satisfied, load_catalog, matched_paths_for,
    score, score_entries

Constants:
    VALID_DECISIONS, _MIXED_CONTENT_SCORE_EPSILON

Entry point:
    main — invoked by ``_dispatch.py`` via dynamic import.

Private symbols re-exported for test backward compatibility
(these will be cleaned up in the test reorganization commit):
    _GROUP_MULTIPLIER, _parse_triggers, _rationale_for, _top_alternatives,
    extract_keywords
"""

from __future__ import annotations

from claude_wayfinder.match._catalog import (
    _compute_catalog_hash as _compute_catalog_hash,  # re-export for test compat
)
from claude_wayfinder.match._catalog import (
    load_catalog as load_catalog,
)
from claude_wayfinder.match._decide import (
    _MIXED_CONTENT_SCORE_EPSILON as _MIXED_CONTENT_SCORE_EPSILON,  # re-export
)
from claude_wayfinder.match._decide import (
    _rationale_for as _rationale_for,  # re-export for test compat
)
from claude_wayfinder.match._decide import (
    _top_alternatives as _top_alternatives,  # re-export for test compat
)
from claude_wayfinder.match._decide import (
    decide as decide,
)
from claude_wayfinder.match._main import main as main
from claude_wayfinder.match._match import (
    _GROUP_MULTIPLIER as _GROUP_MULTIPLIER,  # re-export for test compat
)
from claude_wayfinder.match._match import (
    build_features as build_features,
)
from claude_wayfinder.match._match import (
    extract_keywords as extract_keywords,  # re-export
)
from claude_wayfinder.match._match import (
    group_satisfied as group_satisfied,
)
from claude_wayfinder.match._match import (
    matched_paths_for as matched_paths_for,
)
from claude_wayfinder.match._match import (
    score as score,
)
from claude_wayfinder.match._match import (
    score_entries as score_entries,
)
from claude_wayfinder.match._overrides import (
    OverridesError as OverridesError,
)
from claude_wayfinder.match._overrides import (
    load_overrides as load_overrides,
)
from claude_wayfinder.match._overrides import (
    resolve_override as resolve_override,
)
from claude_wayfinder.match._parse import _parse_triggers as _parse_triggers  # re-export
from claude_wayfinder.match._types import (
    VALID_DECISIONS as VALID_DECISIONS,
)
from claude_wayfinder.match._types import (
    CatalogEntry as CatalogEntry,
)
from claude_wayfinder.match._types import (
    Features as Features,
)
from claude_wayfinder.match._types import (
    Keyword as Keyword,
)
from claude_wayfinder.match._types import (
    KeywordGroup as KeywordGroup,
)
from claude_wayfinder.match._types import (
    LaneInfo as LaneInfo,
)
from claude_wayfinder.match._types import (
    OverrideMatch as OverrideMatch,
)
from claude_wayfinder.match._types import (
    OverrideRule as OverrideRule,
)
from claude_wayfinder.match._types import (
    ScoredEntry as ScoredEntry,
)
from claude_wayfinder.match._types import (
    Slot as Slot,
)
from claude_wayfinder.match._types import (
    Triggers as Triggers,
)

__all__ = [
    "VALID_DECISIONS",
    "CatalogEntry",
    "Features",
    "Keyword",
    "KeywordGroup",
    "LaneInfo",
    "OverrideMatch",
    "OverrideRule",
    "OverridesError",
    "ScoredEntry",
    "Slot",
    "Triggers",
    "build_features",
    "decide",
    "group_satisfied",
    "load_catalog",
    "load_overrides",
    "main",
    "matched_paths_for",
    "resolve_override",
    "score",
    "score_entries",
]
