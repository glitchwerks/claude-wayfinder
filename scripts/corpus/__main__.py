"""CLI entry point for corpus phase A: profiling + stratified construction.

Usage
-----
    python -m scripts.corpus [options]

Options
-------
    --log-path PATH      Override dispatch-log path (default: DISPATCH_LOG env
                         or ~/.claude/state/dispatch-log.jsonl).
    --output-dir DIR     Local directory for corpus artifact
                         (default: ~/.claude/state/wayfinder-corpus/2026-06-12/).
    --sample-floor N     Per-cell sample floor (default: 30).
    --profile-only       Run profiling only; do not build corpus.
    --shadow-only        Include only shadow-attributed entries.
    --join-shadow-from-twins
                         Join shadow data from the nearest preceding
                         python_matcher twin row in the same session.
    --exclude-gold-labels-file PATH
                         Exclude corpus IDs listed in a gold-labels JSONL file.
    --manifest-out PATH  Write manifest JSON to this path (repo-safe;
                         default: docs/research/2026-06-12-corpus-manifest.json).

Outputs
-------
    1. Profile summary to stdout.
    2. Corpus artifact (JSONL, local only) at output_dir/wayfinder-corpus.jsonl.
    3. Manifest JSON (aggregate stats + sha256, no raw text) at manifest_out.

Privacy
-------
    The corpus artifact contains raw task_description text and MUST NOT be
    committed to the repository.  The manifest is commit-safe.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Ensure scripts/ is on the path so profiler and builder can be imported
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# Ensure src/ is on the path so the claude_wayfinder package resolves
# even without an editable install (corpus.builder/profiler import it).
_SRC_DIR = Path(__file__).resolve().parents[2] / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from corpus.builder import build_corpus, build_manifest, write_corpus_artifact  # noqa: E402
from corpus.profiler import NEAR_EMPTY_THRESHOLD, field_profile  # noqa: E402


def _default_log_path() -> Path:
    """Resolve the canonical dispatch-log path."""
    env = os.environ.get("DISPATCH_LOG")
    if env:
        return Path(env)
    return Path.home() / ".claude" / "state" / "dispatch-log.jsonl"


def _default_output_dir() -> Path:
    """Resolve the default local corpus artifact directory."""
    return Path.home() / ".claude" / "state" / "wayfinder-corpus" / "2026-06-12"


def _load_excluded_corpus_ids(path: Path) -> set[int]:
    """Load corpus IDs from a gold-labels JSONL file.

    Args:
        path: Path to the gold-labels JSONL file.

    Returns:
        The set of integer corpus IDs found in valid rows.
    """
    corpus_ids: set[int] = set()
    with path.open(encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            corpus_id = row.get("corpus_id")
            if type(corpus_id) is int:
                corpus_ids.add(corpus_id)
    return corpus_ids


def _print_profile_report(profile: dict, output_file=None) -> None:
    """Print a human-readable profile report to stdout (or a file)."""
    out = output_file or sys.stdout

    def p(*args, **kwargs):
        print(*args, **kwargs, file=out)

    p("=" * 72)
    p("CORPUS PHASE A — DISPATCH LOG PROFILE")
    p("=" * 72)
    p()
    p(f"Total matcher_decision entries: {profile['total_matcher_decision']:,}")
    p(f"  Organic (session_id non-empty): {profile['organic_count']:,}")
    p(f"  Fixture / pre-fix (empty sid):  {profile['fixture_count']:,}")
    p(f"  Empty task_description (organic): {profile['empty_task_description_count']:,}")
    p()

    p("--- Decision distribution (organic) ---")
    dd = profile.get("decision_distribution", {})
    total_organic = profile["organic_count"] or 1
    for decision, count in sorted(dd.items(), key=lambda x: -x[1]):
        pct = count / total_organic * 100
        p(f"  {decision:<25} {count:>4}  ({pct:5.1f}%)")
    p()

    p("--- task_description length bands (organic) ---")
    bands = profile.get("td_length_bands", {})
    for band in ["empty", "short", "medium", "long", "very_long"]:
        count = bands.get(band, 0)
        pct = count / total_organic * 100
        p(f"  {band:<12} {count:>4}  ({pct:5.1f}%)")
    p()

    p("--- input field presence (organic) ---")
    inp = profile.get("input_field_presence", {})
    for field, info in sorted(inp.items(), key=lambda x: -x[1]["rate"]):
        pct = info["rate"] * 100
        pop_rate = info.get("nonempty_count", 0) / total_organic
        flag = " *** FLAGGED" if pop_rate < NEAR_EMPTY_THRESHOLD else ""
        p(
            f"  input.{field:<25} {info['count']:>4}/{total_organic}"
            f"  ({pct:5.1f}% present, {pop_rate * 100:5.1f}% populated){flag}"
        )
    p()

    p("--- output field presence (organic) ---")
    outp = profile.get("output_field_presence", {})
    for field, info in sorted(outp.items(), key=lambda x: -x[1]["rate"]):
        pct = info["rate"] * 100
        pop_rate = info.get("nonempty_count", 0) / total_organic
        flag = " *** FLAGGED" if pop_rate < NEAR_EMPTY_THRESHOLD else ""
        p(
            f"  output.{field:<24} {info['count']:>4}/{total_organic}"
            f"  ({pct:5.1f}% present, {pop_rate * 100:5.1f}% populated){flag}"
        )
    p()

    p("--- Flagged fields (0% or near-empty populated in organic) ---")
    flagged = profile.get("flagged_fields", [])
    if flagged:
        for item in flagged:
            pop_pct = item["populated_rate"] * 100
            p(f"  {item['field']:<40} {pop_pct:5.1f}% populated  — {item['reason']}")
    else:
        p("  (none)")
    p()


def _print_corpus_report(result: dict, sample_floor: int, output_file=None) -> None:
    """Print corpus construction summary."""
    out = output_file or sys.stdout

    def p(*args, **kwargs):
        print(*args, **kwargs, file=out)

    p("=" * 72)
    p("CORPUS CONSTRUCTION SUMMARY")
    p("=" * 72)
    p()
    p(f"Total organic entries:          {result['total_organic']:>4}")
    p(f"  Excluded (empty td):          {result['total_filtered']:>4}")
    p(f"  Eligible for corpus:          {result['total_organic'] - result['total_filtered']:>4}")
    p(f"  In corpus (after cell cap):   {result['total_in_corpus']:>4}")
    p(f"  Per-cell floor target:        {sample_floor:>4}")
    p()

    p("--- Per-cell corpus counts ---")
    cell_counts = result.get("per_cell_counts", {})
    if cell_counts:
        for cell, count in sorted(cell_counts.items()):
            p(f"  {cell:<50} {count:>4}")
    else:
        p("  (empty corpus)")
    p()

    p("--- Shortfall table (cells below floor) ---")
    shortfalls = result.get("shortfall_table", [])
    if shortfalls:
        p(f"  {'Cell':<50} {'Organic':>7}  {'Floor':>5}  {'Shortfall':>9}")
        p("  " + "-" * 72)
        for item in shortfalls:
            cell = item["cell"]
            cnt = item["count"]
            flr = item["floor"]
            sf = item["shortfall"]
            p(f"  {cell:<50} {cnt:>7}  {flr:>5}  {sf:>9}")
    else:
        p("  (all cells meet floor)")
    p()


def main(argv: list[str] | None = None) -> int:
    """Entry point for corpus phase A CLI.

    Args:
        argv: Argument list; defaults to sys.argv[1:] when None.

    Returns:
        Exit code: 0 on success.
    """
    parser = argparse.ArgumentParser(
        description="Corpus phase A: dispatch-log profiling + stratified construction."
    )
    parser.add_argument(
        "--log-path",
        type=str,
        default=None,
        help="Override dispatch-log path.",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Local directory for corpus artifact (never committed).",
    )
    parser.add_argument(
        "--sample-floor",
        type=int,
        default=30,
        help="Per-cell sample floor (default: 30).",
    )
    parser.add_argument(
        "--profile-only",
        action="store_true",
        default=False,
        help="Run profiling only; skip corpus construction.",
    )
    parser.add_argument(
        "--shadow-only",
        action="store_true",
        default=False,
        help="Include only shadow-attributed entries.",
    )
    parser.add_argument(
        "--join-shadow-from-twins",
        action="store_true",
        default=False,
        help=(
            "Join shadow data from the nearest preceding python_matcher twin row "
            "in the same session onto the organic row before filtering."
        ),
    )
    parser.add_argument(
        "--exclude-gold-labels-file",
        type=str,
        default=None,
        help="Path to a gold-labels JSONL file whose corpus IDs are excluded.",
    )
    parser.add_argument(
        "--manifest-out",
        type=str,
        default=None,
        help="Path to write the commit-safe manifest JSON.",
    )
    args = parser.parse_args(argv)

    log_path = Path(args.log_path) if args.log_path else _default_log_path()
    output_dir = Path(args.output_dir) if args.output_dir else _default_output_dir()
    sample_floor = args.sample_floor

    # Step 1: Profile
    profile = field_profile(log_path)
    _print_profile_report(profile)

    # Profiling stop-gate (#294 lesson): if organic count is zero, stop.
    if profile["organic_count"] == 0:
        print(
            "STOP-GATE: 0 organic entries found.  "
            "The dispatch log has no session-attributed entries.  "
            "Fix the collection pipeline before proceeding.",
            file=sys.stderr,
        )
        return 2

    if args.profile_only:
        return 0

    # Step 2: Build corpus
    exclude_corpus_ids = (
        _load_excluded_corpus_ids(Path(args.exclude_gold_labels_file))
        if args.exclude_gold_labels_file
        else None
    )
    result = build_corpus(
        log_path,
        output_dir=None,
        sample_floor=sample_floor,
        shadow_only=args.shadow_only,
        join_shadow_from_twins=args.join_shadow_from_twins,
        exclude_corpus_ids=exclude_corpus_ids,
    )
    _print_corpus_report(result, sample_floor)

    # Step 3: Write artifact locally
    artifact_path = write_corpus_artifact(result, output_dir)
    print(f"Corpus artifact written: {artifact_path}")
    print(f"  Entries: {result['total_in_corpus']}")

    # Step 4: Build and write manifest
    manifest = build_manifest(result, artifact_path)

    manifest_out = (
        Path(args.manifest_out)
        if args.manifest_out
        else Path(__file__).resolve().parents[2]
        / "docs"
        / "research"
        / "2026-06-12-corpus-manifest.json"
    )
    manifest_out.parent.mkdir(parents=True, exist_ok=True)
    manifest_out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Manifest written: {manifest_out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
