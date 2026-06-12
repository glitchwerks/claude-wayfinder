"""CLI entry point for the corpus eval harness (issue #340).

One-shot evaluation command for the #330 run::

    python -m scripts.corpus.eval \\
        --corpus PATH \\
        --labels PATH \\
        --catalog PATH \\
        [--systems lexical,extractors,encoder,composed]

Options
-------
    --corpus PATH       Corpus JSONL (phase A format, required).
    --labels PATH       Gold-labels JSONL (optional; metrics requiring
                        gold are skipped when absent).
    --catalog PATH      Dispatch-catalog JSON (required).
    --systems STR       Comma-separated list of systems to run.
                        Choices: lexical, extractors, encoder, composed.
                        Default: all (encoder + composed skipped when
                        model2vec is not installed).

Output
------
Metrics table to stdout.  Rows: one per system.  Columns: the six
metrics from §13.3.  When a metric is N/A (nan), displays ``n/a``.
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
)
from scripts.corpus.eval._reader import load_corpus, load_labels  # noqa: E402
from scripts.corpus.eval._systems import (  # noqa: E402
    SystemResult,
    run_extractors,
    run_lexical,
)


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
    rows: list[tuple[str, MetricsResult]],
    output=None,
) -> None:
    """Print a formatted metrics table to stdout or a file.

    Args:
        rows: List of (system_label, MetricsResult) pairs.
        output: Output file object; defaults to sys.stdout.
    """
    out = output or sys.stdout

    def p(*args: Any, **kwargs: Any) -> None:
        print(*args, **kwargs, file=out)

    p()
    p("=" * 80)
    p("CORPUS EVAL HARNESS — METRICS TABLE (issue #340, spec §13.3)")
    p("=" * 80)
    p()

    header = (
        f"{'System':<18}  "
        f"{'err_corr':>8}  "
        f"{'adj':>4} {'xpos':>4} {'xdom':>4}  "
        f"{'tierC%':>6}  "
        f"{'fdb%':>6}  "
        f"{'brak%':>6}  "
        f"{'cw%':>6}"
    )
    p(header)
    p("-" * 80)

    for label, m in rows:
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
            f"{_fmt(m.confident_wrong_rate):>6}"
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
    p("  n/a       = metric requires gold labels or no data")
    p()


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
            "Corpus eval harness: four systems × six metrics over "
            "corpus JSONL (issue #340, spec §13.3)."
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
            "Comma-separated systems to run.  Choices: lexical, extractors, "
            "encoder, composed.  Default: 'all' (encoder+composed skipped "
            "when model2vec absent)."
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
        entries = load_corpus(args.corpus)
    except Exception as exc:
        print(f"ERROR loading corpus: {exc}", file=sys.stderr)
        return 1

    print(f"  {len(entries)} entries loaded", file=sys.stderr)

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
        print("Running system 4: composed (domain × posture) ...", file=sys.stderr)
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

    if not results:
        print(
            "ERROR: no systems ran successfully — cannot produce metrics.",
            file=sys.stderr,
        )
        return 1

    # Compute metrics for each available system
    print("Computing metrics ...", file=sys.stderr)

    lexical_r = results.get("lexical", [])
    encoder_r = results.get("encoder")
    extractors_r = results.get("extractors", [])
    composed_r = results.get("composed")

    # Build rows: one per system
    rows: list[tuple[str, MetricsResult]] = []

    for system_label, sys_results in [
        ("lexical", lexical_r),
        ("extractors", extractors_r),
        ("encoder", encoder_r or []),
        ("composed", composed_r or []),
    ]:
        if not sys_results:
            continue
        # For each system, compute metrics using that system as the primary
        m = compute_all_metrics(
            lexical=sys_results,
            encoder=None,
            extractors=(
                sys_results
                if system_label == "extractors"
                else (extractors_r or sys_results)
            ),
            composed=None,
            labels=labels,
        )
        # Override error_correlation: compare this system vs lexical
        if system_label != "lexical" and lexical_r:
            from scripts.corpus.eval._metrics import metric_error_correlation

            corr = metric_error_correlation(lexical_r, sys_results, labels)
        else:
            corr = float("nan")

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
        rows.append((system_label, m))

    _print_metrics_table(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
