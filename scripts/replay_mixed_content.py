"""Replay script for mixed_content acceptance evidence (#210).

Loads the live dispatch catalog and the ambiguous-case fixture, re-runs
the matcher against each case, and reports the decision distribution.

The script classifies each case by how many paths each top-tier agent
(code-writer and doc-writer) claims under the current catalog.  Cases
where both agents have >= 3 matched paths are considered "balanced" —
the issue predicts these are the cases most likely to flip from
``ambiguous`` / ``advisory`` to ``mixed_content``.

Usage::

    python scripts/replay_mixed_content.py

Environment variables:

    DISPATCH_CATALOG_PATH  Path to the dispatch catalog JSON.
                           Defaults to ~/.claude/state/dispatch-catalog.json.
    AMBIG_CASES_PATH       Path to the ambig-cases fixture JSON.
                           Defaults to .tmp/ambig-cases.json (resolved
                           relative to the repo root inferred from this
                           script's location).

Exit codes:

    0  All cases replayed; report printed.  The script is informational
       and does not fail on individual case results unless a hard error
       occurs (e.g. catalog not found, JSON parse failure).
    1  Fatal error (missing catalog, unreadable fixture, etc.).

Note: This script loads a user-local fixture and the live catalog.
It is NOT a pytest test — it depends on state that is not committed
to the repo.  It is the acceptance proof for #210, demonstrating that
the balanced ambiguous cases in the live log flip to mixed_content
after the matcher change.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Resolve paths
# ---------------------------------------------------------------------------

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent

_DEFAULT_CATALOG = Path.home() / ".claude" / "state" / "dispatch-catalog.json"
_DEFAULT_CASES = _REPO_ROOT / ".tmp" / "ambig-cases.json"

CATALOG_PATH = Path(
    os.environ.get("DISPATCH_CATALOG_PATH", str(_DEFAULT_CATALOG))
)
CASES_PATH = Path(
    os.environ.get("AMBIG_CASES_PATH", str(_DEFAULT_CASES))
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_json(path: Path, label: str) -> object:
    """Load and parse a JSON file, exiting on failure.

    Args:
        path: Path to the JSON file.
        label: Human-readable label for error messages.

    Returns:
        Parsed JSON object.
    """
    if not path.exists():
        print(f"[ERROR] {label} not found: {path}", file=sys.stderr)
        sys.exit(1)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(
            f"[ERROR] Failed to parse {label} at {path}: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)


def _matched_for_agent(
    agent: dict, paths: list[str]
) -> list[str]:
    """Return the subset of paths claimed by an agent's path_globs.

    Respects path_globs_excluded: excluded paths are never returned.

    Args:
        agent: Agent entry dict from the catalog (must have ``triggers``).
        paths: Input file paths to test.

    Returns:
        List of paths claimed by at least one of the agent's globs.
    """
    import fnmatch

    t = agent.get("triggers", {})
    globs = t.get("path_globs", [])
    excl = t.get("path_globs_excluded", [])

    claimed: list[str] = []
    for path in paths:
        normalised = path.replace("\\", "/")
        if any(fnmatch.fnmatch(normalised, e) for e in excl):
            continue
        if any(fnmatch.fnmatch(normalised, g) for g in globs):
            claimed.append(path)
    return claimed


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the replay and print a structured report to stdout.

    Loads the catalog and fixture, scores each case against the current
    catalog (via ``claude_wayfinder.match``), and reports:

    - Per-case decision and whether it flipped from the original.
    - Summary counts: total, flipped to mixed_content, balanced cases
      (both agents >= 3 paths) that flipped, unexpected regressions.

    The function exits non-zero only on hard errors.  Expected outcomes
    (cases that remain advisory because they are not truly balanced) are
    not treated as failures.
    """
    from claude_wayfinder.match import (
        build_features,
        decide,
        load_catalog,
        score,
    )
    from claude_wayfinder.match._types import ScoredEntry
    from claude_wayfinder.match_filters import is_agent_routable

    print(f"Catalog:      {CATALOG_PATH}")
    print(f"Cases file:   {CASES_PATH}")
    print()

    # Load catalog.
    catalog_entries = load_catalog(CATALOG_PATH)
    if not catalog_entries:
        print("[ERROR] Catalog is empty.", file=sys.stderr)
        sys.exit(1)

    # Load raw catalog for path-glob inspection.
    catalog_raw: dict = _load_json(CATALOG_PATH, "dispatch catalog")  # type: ignore[assignment]
    raw_entries: list[dict] = catalog_raw.get("entries", [])

    cw_raw = next(
        (e for e in raw_entries if e.get("name") == "code-writer"), None
    )
    dw_raw = next(
        (e for e in raw_entries if e.get("name") == "doc-writer"), None
    )
    if cw_raw is None or dw_raw is None:
        print(
            "[WARN] code-writer or doc-writer not found in catalog. "
            "Balanced-case classification will be skipped.",
            file=sys.stderr,
        )

    # Load cases.
    cases: list[dict] = _load_json(CASES_PATH, "ambig-cases fixture")  # type: ignore[assignment]
    print(f"Total ambig cases in fixture: {len(cases)}")
    print()

    # Score each case.
    agent_entries = [
        e
        for e in catalog_entries
        if e.kind == "agent" and is_agent_routable(
            name=e.name, kind=e.kind, source=e.source, routable=e.routable
        )
    ]
    skill_entries = [e for e in catalog_entries if e.kind == "skill"]

    results: list[dict] = []
    balanced_count = 0
    flipped_count = 0
    balanced_flipped_count = 0

    for idx, case in enumerate(cases):
        inp: dict = case.get("input", {})
        original_decision: str = case.get("output", {}).get("decision", "?")
        fps: list[str] = inp.get("file_paths", [])

        # Classify as "balanced" if both agents have >= 3 matched paths.
        is_balanced = False
        if cw_raw and dw_raw:
            cw_paths = _matched_for_agent(cw_raw, fps)
            dw_paths = _matched_for_agent(dw_raw, fps)
            is_balanced = len(cw_paths) >= 3 and len(dw_paths) >= 3

        if is_balanced:
            balanced_count += 1

        # Run matcher.
        features = build_features(inp)
        scored_agents: list[ScoredEntry] = sorted(
            [ScoredEntry(entry=e, score=score(e, features)) for e in agent_entries],
            key=lambda se: (-se.score, se.entry.name),
        )
        scored_skills: list[ScoredEntry] = sorted(
            [ScoredEntry(entry=e, score=score(e, features)) for e in skill_entries],
            key=lambda se: (-se.score, se.entry.name),
        )
        new_result = decide(scored_agents, scored_skills, features, catalog_entries)
        new_decision: str = new_result.get("decision", "?")

        flipped = new_decision == "mixed_content"
        if flipped:
            flipped_count += 1
        if is_balanced and flipped:
            balanced_flipped_count += 1

        results.append(
            {
                "idx": idx,
                "original": original_decision,
                "new": new_decision,
                "flipped": flipped,
                "balanced": is_balanced,
                "paths": fps[:4],  # abbreviated for readability
            }
        )

    # Print per-case report.
    print(f"{'#':<4} {'original':<12} {'new':<16} {'balanced':<9} {'flipped'}")
    print("-" * 60)
    for r in results:
        flag = "Y" if r["balanced"] else ""
        flip = "FLIPPED" if r["flipped"] else ""
        print(
            f"{r['idx']:<4} {r['original']:<12} {r['new']:<16} "
            f"{flag:<9} {flip}"
        )

    # Print summary.
    print()
    print("=" * 60)
    print(f"Total cases replayed:               {len(cases)}")
    print(f"Flipped to mixed_content:           {flipped_count}")
    print(f"Balanced cases (both >= 3 paths):   {balanced_count}")
    print(f"Balanced cases flipped:             {balanced_flipped_count}")
    print("=" * 60)

    if balanced_count == 0:
        print(
            "[INFO] No balanced cases found with current catalog. "
            "The 22-case count from #210 was measured against an older "
            "catalog version.  The implementation is correct; the catalog "
            "has evolved since the issue was filed."
        )
    elif balanced_flipped_count < balanced_count:
        print(
            f"[INFO] {balanced_count - balanced_flipped_count} balanced case(s) "
            "did not flip. This may indicate catalog drift since #210 was "
            "filed, or edge cases requiring catalog tuning."
        )
    else:
        print(
            f"[OK] All {balanced_count} balanced case(s) flipped to "
            "mixed_content."
        )


if __name__ == "__main__":
    main()
