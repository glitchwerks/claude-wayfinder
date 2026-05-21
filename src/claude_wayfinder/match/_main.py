"""CLI orchestration for the dispatch matcher (v5).

Owns ``main()``: parses the ``--catalog-path`` flag, loads the catalog,
reads stdin JSON, delegates to ``build_features``, ``score_entries``,
``decide``, and ``_write_log_entry``, then prints the result JSON to stdout.
"""

from __future__ import annotations

import argparse
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
from claude_wayfinder.match._decide import decide
from claude_wayfinder.match._match import build_features, score
from claude_wayfinder.match._types import ScoredEntry
from claude_wayfinder.match_filters import is_agent_routable


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
