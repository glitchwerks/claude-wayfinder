"""claude-wayfinder — typed, auditable dispatch matcher for Claude Code.

Provides a deterministic 7-decision routing kernel that scores an incoming
dispatch context against a compiled catalog of agents and skills, then
returns one of seven routing decisions with scored alternatives and a
human-readable rationale.  See ``docs/api.md`` for the full API reference, ``docs/schema.md`` §4 for
the algorithm specification, and ``docs/design.md`` for the design rationale.
"""

from __future__ import annotations

from claude_wayfinder.match import (
    VALID_DECISIONS,
    CatalogEntry,
    Features,
    Keyword,
    KeywordGroup,
    ScoredEntry,
    Slot,
    Triggers,
    build_features,
    decide,
    load_catalog,
    score,
)

__version__ = "0.1.0"

# ``build_catalog.build_catalog`` is public but cannot be re-exported here
# because the name ``build_catalog`` at the package level refers to the
# submodule (``claude_wayfinder.build_catalog``).  Exposing the function
# at the same name would shadow the submodule and break
# ``import claude_wayfinder.build_catalog as bdc`` patterns.
# Public access path: ``from claude_wayfinder.build_catalog import build_catalog``.
__all__ = [
    # Functions (from match module)
    "load_catalog",
    "build_features",
    "score",
    "decide",
    # Dataclasses
    "CatalogEntry",
    "Features",
    "Keyword",
    "KeywordGroup",
    "ScoredEntry",
    "Slot",
    "Triggers",
    # Constants
    "VALID_DECISIONS",
]
