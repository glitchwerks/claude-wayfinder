"""Draw a deterministic stratified sample from a raw shadow corpus."""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from scripts.corpus.builder import _assign_stratum, _cell_key

CellKey = tuple[str, str, bool]


def _stable_row_key(row: dict[str, Any]) -> str:
    """Return a content-based key for deterministic row ordering.

    Args:
        row: Raw shadow-corpus row.

    Returns:
        A canonical JSON representation of the row.
    """
    return json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sample_counts(
    cells: dict[CellKey, list[dict[str, Any]]],
    n: int,
    floor: int,
) -> dict[CellKey, int]:
    """Allocate a proportional sample count to each populated cell.

    Args:
        cells: Populated strata cells and their rows.
        n: Requested total sample size.
        floor: Minimum requested count per populated cell.

    Returns:
        The number of rows to draw from each cell.
    """
    minimums = {
        key: min(max(floor, 0), len(cell_rows))
        for key, cell_rows in cells.items()
    }
    total_rows = sum(len(cell_rows) for cell_rows in cells.values())
    target = min(total_rows, max(max(n, 0), sum(minimums.values())))
    counts = dict(minimums)
    remaining = target - sum(counts.values())
    capacities = {
        key: len(cells[key]) - counts[key]
        for key in cells
    }
    total_capacity = sum(capacities.values())

    if remaining == 0 or total_capacity == 0:
        return counts

    shares = {
        key: remaining * capacities[key] / total_capacity
        for key in cells
    }
    for key in cells:
        addition = min(capacities[key], int(shares[key]))
        counts[key] += addition

    remainder = target - sum(counts.values())
    remainder_order = sorted(
        cells,
        key=lambda key: (-(shares[key] - int(shares[key])), key),
    )
    for key in remainder_order:
        if remainder == 0:
            break
        if counts[key] < len(cells[key]):
            counts[key] += 1
            remainder -= 1

    return counts


def draw_stratified_sample(
    rows: list[dict[str, Any]],
    n: int,
    seed: int,
    floor: int = 2,
) -> list[dict[str, Any]]:
    """Draw a deterministic proportional sample with per-cell minimums.

    Args:
        rows: Raw shadow-corpus rows.
        n: Requested total sample size.
        seed: Seed for this draw's local random number generator.
        floor: Minimum requested count per populated stratum cell.

    Returns:
        Unmodified rows selected from the input corpus.
    """
    grouped: dict[CellKey, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[_cell_key(_assign_stratum(row))].append(row)

    cells = {
        key: sorted(grouped[key], key=_stable_row_key)
        for key in sorted(grouped)
    }
    counts = _sample_counts(cells, n, floor)
    rng = random.Random(seed)

    selected: list[dict[str, Any]] = []
    for key, cell_rows in cells.items():
        selected.extend(rng.sample(cell_rows, counts[key]))
    return selected


def main(argv: list[str]) -> int:
    """Draw a stratified sample from a raw shadow-corpus JSONL file.

    Args:
        argv: Command-line arguments excluding the program name.

    Returns:
        Zero when the sample has been written successfully.

    Raises:
        FileExistsError: If the output path already exists.
        json.JSONDecodeError: If a nonblank input line is not valid JSON.
        OSError: If either path cannot be opened or accessed.
        TypeError: If an input JSON value is not an object.
    """
    parser = argparse.ArgumentParser(
        description="Draw a stratified sample from a raw shadow corpus.",
    )
    parser.add_argument(
        "--input",
        required=True,
        type=Path,
        help="Path to the source raw shadow-corpus JSONL file.",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Path for the new sampled JSONL file.",
    )
    parser.add_argument(
        "--n",
        type=int,
        default=120,
        help="Requested total sample size (default: 120).",
    )
    parser.add_argument(
        "--seed",
        required=True,
        type=int,
        help="Random seed for the deterministic draw.",
    )
    parser.add_argument(
        "--floor",
        type=int,
        default=2,
        help="Minimum rows per populated cell (default: 2).",
    )
    args = parser.parse_args(argv)

    rows: list[dict[str, Any]] = []
    with args.input.open(encoding="utf-8") as source:
        with args.output.open("x", encoding="utf-8", newline="\n") as output:
            for raw_line in source:
                if not raw_line.strip():
                    continue
                row = json.loads(raw_line)
                if not isinstance(row, dict):
                    raise TypeError("each JSONL row must be a JSON object")
                rows.append(row)

            selected = draw_stratified_sample(
                rows,
                n=args.n,
                seed=args.seed,
                floor=args.floor,
            )
            for row in selected:
                output.write(json.dumps(row))
                output.write("\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
