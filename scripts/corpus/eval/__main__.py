"""CLI entry point for the corpus eval harness (issue #340).

One-shot evaluation command for the #330 run::

    python -m scripts.corpus.eval \\
        --corpus PATH \\
        --labels PATH \\
        --catalog PATH \\
        [--systems lexical,extractors,encoder,composed]
        [--compose-labels oracle|PATH]
        [--cut full|no_smoke|no_mention]

Options
-------
    --corpus PATH        Corpus JSONL (phase A format, required).
    --labels PATH        Gold-labels JSONL (optional; metrics requiring
                         gold are skipped when absent).
    --catalog PATH       Dispatch-catalog JSON (required).
    --systems STR        Comma-separated list of systems to run.
                         Choices: lexical, extractors, encoder, composed,
                         compose.
                         Default: all = lexical, extractors, encoder,
                         composed (encoder + composed skipped when
                         model2vec is not installed; compose is NOT
                         included in 'all' — it requires --labels and
                         must be requested explicitly).
    --compose-labels STR Either the literal ``oracle`` (use the gold
                         --labels map for domain/posture) or a path to
                         a real-label JSONL.  Default: ``oracle``.
    --cut STR            Corpus cut to apply before running systems.
                         Choices: full, no_smoke, no_mention.
                         Default: ``full`` (no entries removed).

Output
------
Metrics table to stdout.  Rows: one per system.  Columns: the six
metrics from §13.3 plus RC (routing correctness).
When a metric is N/A (nan), displays ``n/a``.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from typing import Any

# Ensure scripts/ is on the path so imports resolve
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from scripts.corpus.eval._metrics import (  # noqa: E402
    MetricsResult,
    compute_all_metrics,
    metric_confident_wrong_rate,
    metric_routing_correctness,
)
from scripts.corpus.eval._reader import (  # noqa: E402
    CorpusEntry,
    GoldLabel,
    load_corpus,
    load_labels,
)
from scripts.corpus.eval._systems import (  # noqa: E402
    SystemResult,
    run_extractors,
    run_lexical,
    run_supplied_compose,
)

# ---------------------------------------------------------------------------
# Cut helpers
# ---------------------------------------------------------------------------

_SMOKE_DESCRIPTIONS: frozenset[str] = frozenset({
    "update the docs",
    "implement the new module",
})


def identify_smoke_ids(entries: list[CorpusEntry]) -> frozenset[int]:
    """Return corpus_ids of smoke-test entries.

    Smoke entries are identified by their exact ``task_description``
    matching the known smoke-test set.

    Args:
        entries: All loaded corpus entries.

    Returns:
        Frozenset of ``corpus_id`` values for smoke entries.
    """
    return frozenset(
        e.corpus_id
        for e in entries
        if e.task_description in _SMOKE_DESCRIPTIONS
    )


def identify_mention_ids(entries: list[CorpusEntry]) -> frozenset[int]:
    """Return corpus_ids of entries that have explicit agent mentions.

    Args:
        entries: All loaded corpus entries.

    Returns:
        Frozenset of ``corpus_id`` values for entries with non-empty
        ``agent_mentions``.
    """
    return frozenset(
        e.corpus_id for e in entries if e.agent_mentions
    )


def apply_cut(
    entries: list[CorpusEntry],
    labels: dict[int, GoldLabel],
    cut: str,
) -> tuple[list[CorpusEntry], dict[int, GoldLabel]]:
    """Apply the requested cut to entries and labels.

    Supported cut modes:

    - ``full``: no entries removed.
    - ``no_smoke``: remove smoke entries (by ``task_description``).
    - ``no_mention``: remove entries with explicit agent mentions.

    The labels dict is filtered to match the surviving entry set.

    Args:
        entries: All loaded corpus entries.
        labels: Gold label dict.
        cut: Cut mode string (``"full"``, ``"no_smoke"``,
            ``"no_mention"``).

    Returns:
        Tuple of ``(cut_entries, cut_labels)`` after the filter.
    """
    if cut == "full":
        return entries, labels

    if cut == "no_smoke":
        remove_ids = identify_smoke_ids(entries)
    elif cut == "no_mention":
        remove_ids = identify_mention_ids(entries)
    else:
        raise ValueError(f"Unknown cut: {cut!r}")

    cut_entries = [e for e in entries if e.corpus_id not in remove_ids]
    cut_labels = {
        cid: lbl for cid, lbl in labels.items() if cid not in remove_ids
    }
    return cut_entries, cut_labels


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _fmt(value: float) -> str:
    """Format a metric value for table display.

    Args:
        value: Float metric value (may be nan).

    Returns:
        Formatted string: ``n/a`` for nan, else 4 decimal places.
    """
    if math.isnan(value):
        return "   n/a"
    return f"{value:6.4f}"


def _print_metrics_table(
    rows: list[tuple[str, MetricsResult, float]],
    output: Any = None,
) -> None:
    """Print a formatted metrics table to stdout or a file.

    Args:
        rows: List of ``(system_label, MetricsResult, rc)`` triples
            where ``rc`` is the routing-correctness float (or nan).
        output: Output file object; defaults to ``sys.stdout``.
    """
    out = output or sys.stdout

    def p(*args: Any, **kwargs: Any) -> None:
        print(*args, **kwargs, file=out)

    p()
    p("=" * 90)
    p("CORPUS EVAL HARNESS — METRICS TABLE (issue #340, spec §13.3)")
    p("=" * 90)
    p()

    header = (
        f"{'System':<18}  "
        f"{'err_corr':>8}  "
        f"{'adj':>4} {'xpos':>4} {'xdom':>4}  "
        f"{'tierC%':>6}  "
        f"{'fdb%':>6}  "
        f"{'brak%':>6}  "
        f"{'cw%':>6}  "
        f"{'RC%':>6}"
    )
    p(header)
    p("-" * 90)

    for label, m, rc in rows:
        sev = m.error_severity
        row = (
            f"{label:<18}  "
            f"{_fmt(m.error_correlation):>8}  "
            f"{sev.get('adjacent', 0):>4} "
            f"{sev.get('cross_posture', 0):>4} "
            f"{sev.get('cross_domain', 0):>4}  "
            f"{_fmt(m.tier_c_decisiveness):>6}  "
            f"{_fmt(m.false_default_build_rate):>6}  "
            f"{_fmt(m.braked_candidate_quality):>6}  "
            f"{_fmt(m.confident_wrong_rate):>6}  "
            f"{_fmt(rc):>6}"
        )
        p(row)

    p()
    p("Columns:")
    p("  err_corr  Metric 1: error correlation (Phi; §8.4, decisive)")
    p("  adj       Metric 2: adjacent-posture errors (R4)")
    p("  xpos      Metric 2: cross-posture errors (R4)")
    p("  xdom      Metric 2: cross-domain errors (R4)")
    p("  tierC%    Metric 3: Tier-C decisiveness rate (§10.3 g4)")
    p("  fdb%      Metric 4: false-default-build rate (§10.4)")
    p("  brak%     Metric 5: braked-outcome candidate quality (P3)")
    p("  cw%       Metric 6: confident-wrong rate vs baseline")
    p("  RC%       Routing correctness: fraction of labeled entries")
    p("            where agent matches gold_agent")
    p("  n/a       = metric requires gold labels or no data")
    p()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Entry point for the corpus eval harness CLI.

    Args:
        argv: Argument list; defaults to ``sys.argv[1:]`` when None.

    Returns:
        Exit code: 0 on success, 1 on error.
    """
    parser = argparse.ArgumentParser(
        prog="python -m scripts.corpus.eval",
        description=(
            "Corpus eval harness: four baseline systems × six metrics + RC "
            "over corpus JSONL (issue #340 + #363, spec §13.3).  "
            "'compose' is a fifth opt-in system that requires --labels."
        ),
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        required=True,
        metavar="PATH",
        help="Corpus JSONL file (phase A format).",
    )
    parser.add_argument(
        "--labels",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Gold-labels JSONL file (optional; metrics requiring gold "
            "labels are n/a when absent)."
        ),
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        required=True,
        metavar="PATH",
        help="Dispatch-catalog JSON file.",
    )
    parser.add_argument(
        "--systems",
        type=str,
        default="all",
        metavar="LIST",
        help=(
            "Comma-separated systems to run.  "
            "Choices: lexical, extractors, encoder, composed, compose.  "
            "Default: 'all' = lexical, extractors, encoder, composed "
            "(encoder+composed skipped when model2vec absent).  "
            "'compose' is NOT included in 'all' — it must be requested "
            "explicitly and requires --labels."
        ),
    )
    parser.add_argument(
        "--compose-labels",
        type=str,
        default="oracle",
        metavar="oracle|PATH",
        dest="compose_labels",
        help=(
            "Source for domain/posture inputs to the compose system.  "
            "Use 'oracle' to take values from the gold --labels map, or "
            "supply a path to a real-label JSONL.  RC is always scored "
            "against the gold --labels map's gold_agent.  Default: oracle."
        ),
    )
    parser.add_argument(
        "--cut",
        type=str,
        default="full",
        choices=["full", "no_smoke", "no_mention"],
        metavar="{full,no_smoke,no_mention}",
        help=(
            "Corpus cut to apply before running systems.  "
            "full = no entries removed (default).  "
            "no_smoke = remove smoke-test entries.  "
            "no_mention = remove entries with explicit agent mentions."
        ),
    )

    args = parser.parse_args(argv)

    # Validate corpus path
    if not args.corpus.exists():
        print(
            f"ERROR: corpus file not found: {args.corpus}",
            file=sys.stderr,
        )
        return 1

    # Validate catalog path
    if not args.catalog.exists():
        print(
            f"ERROR: catalog file not found: {args.catalog}",
            file=sys.stderr,
        )
        return 1

    # Parse requested systems
    if args.systems.strip().lower() == "all":
        requested = {"lexical", "extractors", "encoder", "composed"}
    else:
        requested = {s.strip().lower() for s in args.systems.split(",")}

    # Load corpus and labels
    print(f"Loading corpus: {args.corpus}", file=sys.stderr)
    try:
        all_entries = load_corpus(args.corpus)
    except Exception as exc:
        print(f"ERROR loading corpus: {exc}", file=sys.stderr)
        return 1

    print(f"  {len(all_entries)} entries loaded", file=sys.stderr)

    # Gold labels (used for RC scoring and for oracle compose mode)
    labels = load_labels(args.labels)
    if args.labels:
        print(
            f"  {len(labels)} gold labels loaded from {args.labels}",
            file=sys.stderr,
        )
    else:
        print(
            "  No labels supplied — gold-dependent metrics will be n/a.",
            file=sys.stderr,
        )

    # Apply corpus cut
    entries, cut_labels = apply_cut(all_entries, labels, args.cut)
    if args.cut != "full":
        dropped = len(all_entries) - len(entries)
        print(
            f"  Cut '{args.cut}': {dropped} entries removed, "
            f"{len(entries)} remaining",
            file=sys.stderr,
        )

    # Run systems
    results: dict[str, list[SystemResult]] = {}

    if "lexical" in requested:
        print("Running system 1: lexical baseline ...", file=sys.stderr)
        try:
            results["lexical"] = run_lexical(entries, args.catalog)
        except Exception as exc:
            print(f"  ERROR in lexical: {exc}", file=sys.stderr)

    if "extractors" in requested:
        print("Running system 3: extractors-alone ...", file=sys.stderr)
        try:
            results["extractors"] = run_extractors(entries, args.catalog)
        except Exception as exc:
            print(f"  ERROR in extractors: {exc}", file=sys.stderr)

    if "encoder" in requested:
        print("Running system 2: encoder-alone ...", file=sys.stderr)
        try:
            from scripts.corpus.eval._systems import run_encoder  # noqa: F401

            results["encoder"] = run_encoder(entries, args.catalog)
        except ImportError:
            print(
                "  SKIP: model2vec not installed (use: pip install '.[spike]')",
                file=sys.stderr,
            )
        except Exception as exc:
            print(f"  ERROR in encoder: {exc}", file=sys.stderr)

    if "composed" in requested:
        print(
            "Running system 4: composed (domain × posture) ...",
            file=sys.stderr,
        )
        try:
            from scripts.corpus.eval._systems import run_composed  # noqa: F401

            results["composed"] = run_composed(entries, args.catalog)
        except ImportError:
            print(
                "  SKIP: model2vec not installed (use: pip install '.[spike]')",
                file=sys.stderr,
            )
        except Exception as exc:
            print(f"  ERROR in composed: {exc}", file=sys.stderr)

    if "compose" in requested:
        print(
            "Running system 5: supplied-compose (oracle two-axis) ...",
            file=sys.stderr,
        )
        # Resolve compose-labels source
        compose_label_map: dict[int, GoldLabel]
        if args.compose_labels == "oracle":
            if not labels:
                print(
                    "  SKIP compose: --compose-labels oracle requires --labels "
                    "to be supplied.",
                    file=sys.stderr,
                )
            else:
                compose_label_map = cut_labels
                try:
                    results["compose"] = run_supplied_compose(
                        entries, args.catalog, compose_label_map
                    )
                except Exception as exc:
                    print(
                        f"  ERROR in compose: {exc}", file=sys.stderr
                    )
        else:
            # Treat as a file path to a real-label JSONL
            compose_labels_path = Path(args.compose_labels)
            if not compose_labels_path.exists():
                print(
                    f"  ERROR: --compose-labels path not found: "
                    f"{compose_labels_path}",
                    file=sys.stderr,
                )
            else:
                try:
                    compose_label_map = load_labels(compose_labels_path)
                    # Apply same cut to compose labels
                    _, compose_label_map = apply_cut(
                        all_entries, compose_label_map, args.cut
                    )
                    results["compose"] = run_supplied_compose(
                        entries, args.catalog, compose_label_map
                    )
                except Exception as exc:
                    print(
                        f"  ERROR in compose: {exc}", file=sys.stderr
                    )

    if not results:
        print(
            "ERROR: no systems ran successfully — cannot produce metrics.",
            file=sys.stderr,
        )
        return 1

    # Compute metrics for each available system
    print("Computing metrics ...", file=sys.stderr)

    lexical_r = results.get("lexical", [])

    # Build rows: one per system (in display order)
    display_order = ["lexical", "extractors", "encoder", "composed", "compose"]
    rows: list[tuple[str, MetricsResult, float]] = []

    for system_label in display_order:
        sys_results = results.get(system_label)
        if not sys_results:
            continue

        m = compute_all_metrics(
            lexical=sys_results,
            encoder=None,
            extractors=sys_results,
            composed=None,
            labels=labels,
        )
        # Override error_correlation: compare this system vs lexical
        if system_label != "lexical" and lexical_r:
            from scripts.corpus.eval._metrics import metric_error_correlation

            corr = metric_error_correlation(lexical_r, sys_results, labels)
        else:
            corr = float("nan")

        # Routing correctness — always against gold labels
        rc = metric_routing_correctness(sys_results, labels)

        # Repack with corrected correlation
        m = MetricsResult(
            error_correlation=corr,
            error_severity=m.error_severity,
            tier_c_decisiveness=m.tier_c_decisiveness,
            false_default_build_rate=m.false_default_build_rate,
            braked_candidate_quality=m.braked_candidate_quality,
            confident_wrong_rate=metric_confident_wrong_rate(
                sys_results, labels
            ),
        )
        rows.append((system_label, m, rc))

    _print_metrics_table(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
