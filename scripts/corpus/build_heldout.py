"""Held-out sampler for two-axis labeler validation (issue #387).

Produces a reproducible, frozen held-out set of 150 dispatch-log contexts
drawn from production traffic, for use in independent measurement of the
two-axis labeler (domain × posture).

CRITICAL — MEASUREMENT ONLY
============================
The output ``docs/research/2026-06-17-heldout-contexts.jsonl`` is the
canonical frozen held-out set.  It MUST NEVER be used for rubric tuning,
prompt engineering, or any other form of labeler optimization.  Measurement
(scoring) only.  If you find yourself inspecting this file to improve the
labeler, stop — you are invalidating the held-out guarantee.

Reproducibility caveat
======================
The dispatch log (``~/.claude/state/dispatch-log.jsonl``) is live and
growing.  Re-running this script against a later snapshot will yield a
different candidate pool and, therefore, a different 150-entry sample even
with the same seed.  The *committed* JSONL is the authoritative frozen
held-out; this script records how it was produced.

Fidelity note
=============
The dispatch log ``input`` object does NOT carry ``command_prefix`` or
``agent_mentions`` in most records (those fields were added to the corpus
format later).  Both fields default to ``null`` / ``[]`` in the output,
making this a deliberately conservative held-out that lacks the
``operate``-posture signal carried by ``command_prefix``.  Measurement
results should be interpreted with this in mind.

Pipeline
========
1. Read ``~/.claude/state/dispatch-log.jsonl``; keep ``type == "matcher_decision"``.
2. Dedup by exact ``task_description`` (the log re-scores the same context
   many times; ~46 k entries collapse to ~1,574 unique).
3. Exclude:
   - TDs already in the training corpus.
   - Smoke-test TDs (``_SMOKE_DESCRIPTIONS``).
   - TDs shorter than 40 characters (trivially short / non-task strings).
   (Entries matching the synthetic placeholder "implement the new module"
   are already captured by the corpus or smoke sets.)
4. Sample exactly 150 with ``random.seed(42)`` over a ``sorted()`` list.
5. Write output JSONL with corpus IDs starting at 90001.
6. Print composition report to stdout.

Usage (from repo root or worktree root)::

    PYTHONPATH="<worktree-root>" python scripts/corpus/build_heldout.py

All paths are resolved relative to the script's containing worktree root,
except the dispatch log and training corpus which live under ``~/.claude/``.
"""

from __future__ import annotations

import json
import random
import statistics
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve()
_WT_ROOT = _HERE.parent.parent.parent  # <worktree-root>

for _p in (str(_WT_ROOT),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ---------------------------------------------------------------------------
# Data paths
# ---------------------------------------------------------------------------

_LOG_PATH = Path.home() / ".claude/state/dispatch-log.jsonl"
_CORPUS_PATH = Path.home() / (
    ".claude/state/wayfinder-corpus/2026-06-12/wayfinder-corpus.jsonl"
)
_OUTPUT_PATH = (
    _WT_ROOT / "docs/research/2026-06-17-heldout-contexts.jsonl"
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SAMPLE_SIZE = 150
_RANDOM_SEED = 42
_MIN_TD_LEN = 40
_FIRST_CORPUS_ID = 90001

# ---------------------------------------------------------------------------
# Imports from corpus module (after sys.path insert)
# ---------------------------------------------------------------------------

from scripts.corpus.phase0_failure_decomposition import (  # noqa: E402  # noqa: E402
    _SMOKE_DESCRIPTIONS,
)

from scripts.corpus.eval._reader import load_corpus  # noqa: E402

# ---------------------------------------------------------------------------
# Loader: dispatch log
# ---------------------------------------------------------------------------


def load_matcher_decisions(path: Path) -> list[dict[str, Any]]:
    """Load all ``matcher_decision`` records from the dispatch log.

    Skips blank lines and JSON-parse errors (logs can have partial writes).

    Args:
        path: Path to the dispatch log JSONL file.

    Returns:
        List of raw record dicts with ``type == "matcher_decision"``.

    Raises:
        FileNotFoundError: If the log file does not exist.
    """
    if not path.exists():
        raise FileNotFoundError(f"Dispatch log not found: {path}")
    records: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line:
                continue
            try:
                rec: dict[str, Any] = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("type") == "matcher_decision":
                records.append(rec)
    return records


# ---------------------------------------------------------------------------
# Dedup
# ---------------------------------------------------------------------------


def dedup_by_task_description(
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return one record per unique task_description (first occurrence).

    Args:
        records: Raw matcher_decision records.

    Returns:
        Deduplicated list (order: first-seen).
    """
    seen: dict[str, dict[str, Any]] = {}
    for rec in records:
        td: str = rec.get("input", {}).get("task_description", "")
        if td and td not in seen:
            seen[td] = rec
    return list(seen.values())


# ---------------------------------------------------------------------------
# Pool builder
# ---------------------------------------------------------------------------


def build_candidate_pool(
    records: list[dict[str, Any]],
    corpus_tds: frozenset[str],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Filter records to a clean candidate pool for sampling.

    Exclusion rules (applied in order):
    1. TD already in the training corpus (exact match).
    2. TD in ``_SMOKE_DESCRIPTIONS``.
    3. TD shorter than ``_MIN_TD_LEN`` characters.

    Args:
        records: Deduplicated matcher_decision records.
        corpus_tds: Frozen set of task_descriptions from the training corpus.

    Returns:
        Tuple of (pool_records, drop_counts) where drop_counts maps
        reason → count.
    """
    drop_counts: dict[str, int] = {
        "corpus": 0,
        "smoke": 0,
        "too_short": 0,
    }
    pool: list[dict[str, Any]] = []

    for rec in records:
        td: str = rec.get("input", {}).get("task_description", "")

        if td in corpus_tds:
            drop_counts["corpus"] += 1
            continue

        if td in _SMOKE_DESCRIPTIONS:
            drop_counts["smoke"] += 1
            continue

        if len(td) < _MIN_TD_LEN:
            drop_counts["too_short"] += 1
            continue

        pool.append(rec)

    return pool, drop_counts


# ---------------------------------------------------------------------------
# Sampler
# ---------------------------------------------------------------------------


def sample_pool(
    pool: list[dict[str, Any]],
    n: int = _SAMPLE_SIZE,
    seed: int = _RANDOM_SEED,
) -> list[dict[str, Any]]:
    """Sample up to ``n`` records from the pool with a fixed seed.

    Uses ``sorted()`` on ``task_description`` before sampling to guarantee
    a deterministic order regardless of dict-insertion order.

    Args:
        pool: Candidate records to sample from.
        n: Maximum number of records to return.
        seed: Random seed for reproducibility.

    Returns:
        List of up to ``n`` sampled records.
    """
    sorted_pool = sorted(pool, key=lambda r: r["input"]["task_description"])
    random.seed(seed)
    if len(sorted_pool) <= n:
        return sorted_pool
    return random.sample(sorted_pool, n)


# ---------------------------------------------------------------------------
# Output schema builder
# ---------------------------------------------------------------------------


def build_output_record(
    rec: dict[str, Any],
    corpus_id: int,
) -> dict[str, Any]:
    """Convert a raw log record into the training-corpus output schema.

    The schema mirrors the training corpus format:
    ``{"corpus_id": int, "input": {...}}``.

    ``command_prefix`` and ``agent_mentions`` default to ``null`` / ``[]``
    because the dispatch log does not populate them in most records.

    Args:
        rec: Raw matcher_decision log record.
        corpus_id: Fresh non-colliding corpus ID (90001–90150).

    Returns:
        Dict in training-corpus schema ready for JSON serialisation.
    """
    inp: dict[str, Any] = rec.get("input") or {}
    return {
        "corpus_id": corpus_id,
        "input": {
            "task_description": str(inp.get("task_description", "")),
            "file_paths": list(inp.get("file_paths") or []),
            "agent_mentions": [],
            "tool_mentions": list(inp.get("tool_mentions") or []),
            "command_prefix": None,
        },
    }


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------


def write_jsonl(
    records: list[dict[str, Any]],
    path: Path,
) -> int:
    """Write records as JSONL to ``path``, one object per line.

    Args:
        records: Dicts to serialise.
        path: Destination path; parent directories are created if missing.

    Returns:
        Number of lines written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False))
            fh.write("\n")
    return len(records)


# ---------------------------------------------------------------------------
# Composition reporter
# ---------------------------------------------------------------------------


def _has_github_tool(tool_mentions: list[str]) -> bool:
    """Return True if any tool mention looks like a GitHub or gh tool.

    Args:
        tool_mentions: List of tool-mention strings.

    Returns:
        True if any mention starts with ``mcp__github__`` or equals ``gh``.
    """
    return any(
        t.startswith("mcp__github__") or t == "gh"
        for t in tool_mentions
    )


def _has_file_with_exts(
    file_paths: list[str],
    exts: frozenset[str],
) -> bool:
    """Return True if any path in *file_paths* has a suffix in *exts*.

    Args:
        file_paths: List of file-path strings to inspect.
        exts: Frozenset of lowercase extensions to match (e.g.
            ``frozenset({".py", ".ts"})``).

    Returns:
        True if ``Path(p).suffix.lower() in exts`` for at least one
        element of *file_paths*.
    """
    return any(Path(p).suffix.lower() in exts for p in file_paths)


_CODE_EXTS: frozenset[str] = frozenset({
    ".py", ".ts", ".js", ".go", ".rs", ".java",
    ".cs", ".cpp", ".c", ".h", ".rb",
})
_DOC_EXTS: frozenset[str] = frozenset({".md", ".rst"})


def _has_code_path(file_paths: list[str]) -> bool:
    """Return True if any file path has a code-file extension.

    Args:
        file_paths: List of file-path strings.

    Returns:
        True if any path ends with a recognised code extension.
    """
    return _has_file_with_exts(file_paths, _CODE_EXTS)


def _has_docs_path(file_paths: list[str]) -> bool:
    """Return True if any file path has a documentation extension.

    Args:
        file_paths: List of file-path strings.

    Returns:
        True if any path ends with ``.md`` or ``.rst``.
    """
    return _has_file_with_exts(file_paths, _DOC_EXTS)


def print_composition_report(
    output_records: list[dict[str, Any]],
    drop_counts: dict[str, int],
    pool_size: int,
) -> None:
    """Print a composition report for the sampled held-out set.

    Reports: total written; task-length distribution; counts with
    file_paths, GitHub-tool mentions, code-extension paths, and
    docs-extension paths.

    Args:
        output_records: Output records in training-corpus schema.
        drop_counts: Mapping of exclusion reason → count.
        pool_size: Size of the candidate pool before sampling.
    """
    n = len(output_records)
    tds = [
        r["input"]["task_description"] for r in output_records
    ]
    td_lens = [len(td) for td in tds]

    with_file_paths = sum(
        1 for r in output_records if r["input"]["file_paths"]
    )
    with_github = sum(
        1 for r in output_records
        if _has_github_tool(r["input"]["tool_mentions"])
    )
    with_code_paths = sum(
        1 for r in output_records
        if _has_code_path(r["input"]["file_paths"])
    )
    with_docs_paths = sum(
        1 for r in output_records
        if _has_docs_path(r["input"]["file_paths"])
    )
    with_tool_mentions = sum(
        1 for r in output_records if r["input"]["tool_mentions"]
    )

    print("=" * 60)
    print("Held-out sampler — composition report")
    print("=" * 60)
    print("  Fidelity note: dispatch log 'input' has NO command_prefix")
    print("  or agent_mentions — those fields are empty in this held-out.")
    print("  This is a deliberately conservative sample (missing the")
    print("  'operate' posture signal from command_prefix).")
    print()
    print("Exclusion summary:")
    print(f"  Dropped (in training corpus): {drop_counts['corpus']}")
    print(f"  Dropped (smoke):              {drop_counts['smoke']}")
    print(f"  Dropped (too short <{_MIN_TD_LEN} chars): "
          f"{drop_counts['too_short']}")
    print(f"  Candidate pool after exclusions: {pool_size}")
    print()
    print("Sample composition:")
    print(f"  Total written:                {n}")
    print(f"  Corpus IDs:                   "
          f"{_FIRST_CORPUS_ID}–{_FIRST_CORPUS_ID + n - 1}")
    print(f"  Random seed:                  {_RANDOM_SEED}")
    print()
    print("Task-description length distribution:")
    if td_lens:
        print(f"  min:    {min(td_lens)}")
        print(f"  median: {statistics.median(td_lens):.0f}")
        print(f"  max:    {max(td_lens)}")
    print()
    print("Signal presence:")
    print(f"  With file_paths:              {with_file_paths}/{n}")
    print(f"  With any tool_mentions:       {with_tool_mentions}/{n}")
    print(f"  With GitHub-tool mentions:    {with_github}/{n}")
    print(f"  With code-ext paths:          {with_code_paths}/{n}")
    print(f"  With docs-ext paths:          {with_docs_paths}/{n}")
    print("=" * 60)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the held-out sampler and write the frozen output JSONL.

    Loads the dispatch log and training corpus, builds the candidate pool,
    samples 150 entries with a fixed seed, writes the output JSONL, and
    prints a composition report.

    Returns:
        None
    """
    print(f"Loading dispatch log: {_LOG_PATH}", file=sys.stderr)
    raw_records = load_matcher_decisions(_LOG_PATH)
    print(f"  {len(raw_records)} matcher_decision records", file=sys.stderr)

    print("Deduplicating by task_description …", file=sys.stderr)
    unique_records = dedup_by_task_description(raw_records)
    print(f"  {len(unique_records)} unique task_descriptions", file=sys.stderr)

    print(f"Loading training corpus: {_CORPUS_PATH}", file=sys.stderr)
    corpus_entries = load_corpus(_CORPUS_PATH)
    corpus_tds = frozenset(e.task_description for e in corpus_entries)
    print(f"  {len(corpus_tds)} unique training-corpus TDs", file=sys.stderr)

    print("Building candidate pool …", file=sys.stderr)
    pool, drop_counts = build_candidate_pool(unique_records, corpus_tds)
    print(
        f"  Pool: {len(pool)}  "
        f"(dropped corpus={drop_counts['corpus']}, "
        f"smoke={drop_counts['smoke']}, "
        f"too_short={drop_counts['too_short']})",
        file=sys.stderr,
    )

    if len(pool) < _SAMPLE_SIZE:
        print(
            f"  WARNING: pool ({len(pool)}) < requested sample "
            f"({_SAMPLE_SIZE}); taking all.",
            file=sys.stderr,
        )

    print(
        f"Sampling {min(_SAMPLE_SIZE, len(pool))} entries "
        f"(seed={_RANDOM_SEED}) …",
        file=sys.stderr,
    )
    sampled = sample_pool(pool)
    print(f"  Sampled: {len(sampled)}", file=sys.stderr)

    output_records = [
        build_output_record(rec, _FIRST_CORPUS_ID + i)
        for i, rec in enumerate(sampled)
    ]

    print(f"Writing output: {_OUTPUT_PATH}", file=sys.stderr)
    n_written = write_jsonl(output_records, _OUTPUT_PATH)
    print(f"  Wrote {n_written} lines.", file=sys.stderr)

    print_composition_report(output_records, drop_counts, len(pool))


if __name__ == "__main__":
    main()
