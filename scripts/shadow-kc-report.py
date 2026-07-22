"""Generate the shadow-routing knowledge-criteria go/no-go report."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

# Direct script execution does not automatically put the repository root on
# sys.path. Add it before importing the scripts namespace.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_SRC_DIR = _REPO_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from claude_wayfinder.match._cells import cell_map_lookup  # noqa: E402
from scripts.corpus.eval._kc import (  # noqa: E402
    KCVerdict,
    compute_kc1,
    compute_kc2,
    compute_kc3,
    compute_kc4,
    compute_kc5,
)
from scripts.corpus.eval._metrics import (  # noqa: E402
    metric_confident_wrong_rate,
    metric_routing_correctness,
)
from scripts.corpus.eval._reader import (  # noqa: E402
    GoldLabel,
    load_corpus,
    load_labels,
)
from scripts.corpus.eval._systems import SystemResult  # noqa: E402

CorpusRow = dict[str, Any]
Arm = Literal["shadow", "live"]

_DEPENDENCY_MODULES = (
    "src/claude_wayfinder/match/_compose.py",
    "src/claude_wayfinder/match/_cells.py",
)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Arguments excluding the program name, or None for sys.argv.

    Returns:
        Parsed command-line namespace.
    """
    parser = argparse.ArgumentParser(
        description="Generate the shadow-routing KC go/no-go report.",
    )
    parser.add_argument(
        "--corpus",
        required=True,
        type=Path,
        metavar="PATH",
        help="Shadow-corpus JSONL file.",
    )
    parser.add_argument(
        "--labels",
        required=True,
        type=Path,
        metavar="PATH",
        help="Gold-labels JSONL file.",
    )
    parser.add_argument(
        "--json",
        default=None,
        type=Path,
        metavar="PATH",
        help="Optional path for the machine-readable JSON report.",
    )
    parser.add_argument(
        "--repo-root",
        default=Path.cwd(),
        type=Path,
        metavar="PATH",
        help="Git repository root used for provenance checks (default: cwd).",
    )
    return parser.parse_args(argv)


def _run_git(repo_root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Run a captured git command in the selected repository.

    Args:
        repo_root: Repository working directory.
        *arguments: Git subcommand and arguments.

    Returns:
        Completed git process without automatic return-code checking.

    Raises:
        OSError: If the process cannot be started or the cwd is invalid.
    """
    return subprocess.run(
        ["git", *arguments],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def _guard_error(message: str) -> bool:
    """Print a provenance warning and return the failed-guard sentinel.

    Args:
        message: Human-readable failure detail.

    Returns:
        False, indicating that the guard failed.
    """
    print(f"ERROR: matcher_version provenance guard: {message}", file=sys.stderr)
    return False


def _provenance_guard(rows: list[CorpusRow], repo_root: Path) -> bool:
    """Validate corpus matcher provenance against committed and dirty state.

    Args:
        rows: Raw shadow-corpus records.
        repo_root: Git repository containing matcher dependency modules.

    Returns:
        True only when one resolvable matcher version produced every row and
        both dependency modules are unchanged and clean.
    """
    try:
        versions = {row.get("matcher_version") for row in rows}
        if len(versions) != 1:
            return _guard_error(
                "corpus rows do not contain one consistent matcher_version"
            )

        matcher_version = next(iter(versions))
        if not isinstance(matcher_version, str) or matcher_version == "unknown":
            return _guard_error(
                f"matcher_version {matcher_version!r} is not a resolvable commit"
            )

        resolved = _run_git(
            repo_root,
            "rev-parse",
            "--verify",
            f"{matcher_version}^{{commit}}",
        )
        if resolved.returncode != 0:
            detail = resolved.stderr.strip() or "git could not resolve the commit"
            return _guard_error(
                f"matcher_version {matcher_version!r} is unverifiable: {detail}"
            )

        for module_path in _DEPENDENCY_MODULES:
            comparison = _run_git(
                repo_root,
                "diff",
                "--quiet",
                matcher_version,
                "HEAD",
                "--",
                module_path,
            )
            if comparison.returncode == 1:
                return _guard_error(
                    f"{module_path} changed after matcher_version {matcher_version}"
                )
            if comparison.returncode != 0:
                detail = comparison.stderr.strip() or "git diff failed"
                return _guard_error(f"cannot compare {module_path}: {detail}")

            status = _run_git(
                repo_root,
                "status",
                "--porcelain",
                "--",
                module_path,
            )
            if status.returncode != 0:
                detail = status.stderr.strip() or "git status failed"
                return _guard_error(f"cannot inspect {module_path}: {detail}")
            if status.stdout.strip():
                return _guard_error(f"{module_path} has uncommitted changes")
    except Exception as exc:  # Fail closed for all git/process edge cases.
        return _guard_error(f"git verification failed safely: {exc}")

    return True


def _eligible_rows(rows: list[CorpusRow]) -> list[CorpusRow]:
    """Return the exact gated, mapped, high-confidence KC-3 partition.

    This filter mirrors ``compute_kc3`` in ``scripts/corpus/eval/_kc.py``,
    which remains the source of truth for KC-3 eligibility.

    Args:
        rows: Raw shadow-corpus records.

    Returns:
        Rows eligible for KC-3 and the gated-subset RC/CW cut.
    """
    eligible: list[CorpusRow] = []
    for row in rows:
        caller_input = row["input"]
        domain = caller_input["domain"]
        posture = caller_input["posture"]
        confidence = caller_input["confidence"]
        domain_for_lookup = domain if domain not in (None, "is_any") else "any"
        is_gated = domain not in (None, "is_any")
        cell_exists = (
            posture is not None
            and cell_map_lookup(domain_for_lookup, posture) is not None
        )
        if is_gated and cell_exists and confidence == "high":
            eligible.append(row)
    return eligible


def _system_results(rows: list[CorpusRow], arm: Arm) -> list[SystemResult]:
    """Adapt raw corpus records for the validated RC/CW metric kernels.

    Args:
        rows: Raw shadow-corpus records.
        arm: Either the logged shadow or live routing arm.

    Returns:
        Metric-compatible system results.
    """
    return [
        SystemResult(
            corpus_id=row["corpus_id"],
            decision=row["shadow"][f"{arm}_decision"],
            agent=row["shadow"][f"{arm}_agent"],
            confidence=row["shadow"][f"{arm}_confidence"],
            extras={},
        )
        for row in rows
    ]


def _cut_metrics(
    rows: list[CorpusRow],
    gold: dict[int, GoldLabel],
) -> dict[str, float | int]:
    """Compute shadow RC/CW for one report cut.

    Args:
        rows: Raw corpus rows in the selected cut.
        gold: Full gold-label map.

    Returns:
        Cut size, routing correctness, and confident-wrong rate.
    """
    results = _system_results(rows, "shadow")
    return {
        "n": len(rows),
        "shadow_rc": metric_routing_correctness(results, gold),
        "shadow_cw": metric_confident_wrong_rate(results, gold),
    }


def _recommendation(verdicts: list[KCVerdict]) -> str:
    """Build the overall go/no-go recommendation.

    Args:
        verdicts: KC-1 through KC-5 verdicts.

    Returns:
        Non-empty recommendation with insufficient-data criteria named.
    """
    by_kc = {verdict.kc: verdict for verdict in verdicts}
    failed = [verdict.kc for verdict in verdicts if verdict.status == "FAIL"]
    insufficient = [
        verdict.kc
        for verdict in verdicts
        if verdict.status == "INSUFFICIENT_DATA"
    ]

    if by_kc["KC-2"].status != "PASS" or failed:
        recommendation = "NO-GO"
        if failed:
            recommendation += f": failed criteria: {', '.join(failed)}."
        else:
            recommendation += ": KC-2 hard block lacks a passing verdict."
    else:
        recommendation = "GO: all criteria with sufficient data passed."

    if insufficient:
        recommendation += f" Insufficient data: {', '.join(insufficient)}."
    return recommendation


def _render_report(
    verdicts: list[KCVerdict],
    rows: list[CorpusRow],
    gold: dict[int, GoldLabel],
    recommendation: str,
) -> str:
    """Render the human-readable Markdown report.

    Args:
        verdicts: KC-1 through KC-5 verdicts.
        rows: Full raw corpus row set.
        gold: Gold labels keyed by corpus ID.
        recommendation: Overall recommendation text.

    Returns:
        Complete Markdown report text.
    """
    lines = ["# Shadow KC Report", ""]
    for verdict in verdicts:
        lines.extend(
            [
                f"## {verdict.kc}",
                f"{verdict.kc}: {verdict.status} — "
                f"metrics: {json.dumps(verdict.metrics, sort_keys=True)}",
                "",
            ]
        )

    whole_metrics = _cut_metrics(rows, gold)
    gated_metrics = _cut_metrics(_eligible_rows(rows), gold)
    lines.extend(
        [
            "## Whole-sample cut",
            f"RC/CW: {json.dumps(whole_metrics, sort_keys=True)}",
            "",
            "## Gated-eligible subset cut",
            f"RC/CW: {json.dumps(gated_metrics, sort_keys=True)}",
            "",
        ]
    )

    matched = 0
    mismatched = 0
    for row in rows:
        label = gold.get(row["corpus_id"])
        if label is None:
            continue
        if row["input"]["domain"] == label.domain:
            matched += 1
        else:
            mismatched += 1
    lines.extend(
        [
            "## Caller-label match breakdown",
            f"Matched gold: {matched}; caller-label mismatch/disagreement: "
            f"{mismatched}.",
            "",
            "## Go/no-go recommendation",
            recommendation,
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Load inputs, enforce provenance, and emit the shadow KC report.

    Args:
        argv: Command-line arguments excluding the program name. When None,
            argparse reads ``sys.argv[1:]``.

    Returns:
        Zero after a completed report; non-zero for loading, provenance, or
        output errors.
    """
    args = _parse_args(argv)

    try:
        entries = load_corpus(args.corpus)
        gold = load_labels(args.labels)
    except Exception as exc:
        print(f"ERROR loading report inputs: {exc}", file=sys.stderr)
        return 1

    rows = [entry.raw for entry in entries]
    if not _provenance_guard(rows, args.repo_root):
        return 1

    try:
        verdicts = [
            compute_kc1(rows, gold),
            compute_kc2(rows, gold),
            compute_kc3(rows, gold),
            compute_kc4(rows, gold),
            compute_kc5(rows, gold),
        ]
        recommendation = _recommendation(verdicts)
        report = _render_report(verdicts, rows, gold, recommendation)
        print(report)

        if args.json is not None:
            payload = {
                "criteria": [asdict(verdict) for verdict in verdicts],
                "overall_recommendation": recommendation,
            }
            args.json.write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
    except Exception as exc:
        print(f"ERROR generating report: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
