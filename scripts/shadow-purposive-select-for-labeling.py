"""Select likely KC-4 and KC-5 candidates from a raw shadow corpus."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_KC4_DOMAINS = {"is_any", "project_meta"}


def _candidate_kc(row: dict[str, Any]) -> str | None:
    """Return the purposive KC tag for a candidate row.

    Args:
        row: Raw shadow-corpus row.

    Returns:
        ``"KC-4"`` or ``"KC-5"`` when the caller-supplied domain makes the
        row a candidate, otherwise ``None``.
    """
    input_data = row.get("input")
    if not isinstance(input_data, dict):
        return None

    domain = input_data.get("domain")
    if not isinstance(domain, str):
        return None
    if domain in _KC4_DOMAINS:
        return "KC-4"
    if domain == "infra_deploy":
        return "KC-5"
    return None


def main(argv: list[str]) -> int:
    """Select likely KC-4 and KC-5 candidates from a JSONL corpus.

    Args:
        argv: Command-line arguments excluding the program name.

    Returns:
        Zero when the selected rows have been written successfully.

    Raises:
        FileExistsError: If the output path already exists.
        OSError: If either path cannot be opened or accessed.
        TypeError: If an input JSON value is not an object.
        ValueError: If a nonblank input line is not valid JSON.
    """
    parser = argparse.ArgumentParser(
        description="Select likely KC-4 and KC-5 labeling candidates.",
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
        help="Path for the new purposive-selection JSONL file.",
    )
    parser.add_argument(
        "--kc",
        choices=("4", "5", "both"),
        default="both",
        help="Candidate kind to select (default: both).",
    )
    args = parser.parse_args(argv)

    selected: list[dict[str, Any]] = []
    with args.input.open(encoding="utf-8") as source:
        for line_number, raw_line in enumerate(source, start=1):
            if not raw_line.strip():
                continue
            try:
                row = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"invalid JSON on line {line_number}: {exc}"
                ) from exc
            if not isinstance(row, dict):
                raise TypeError(
                    f"JSONL row on line {line_number} must be a JSON object"
                )

            candidate_kc = _candidate_kc(row)
            if candidate_kc is None:
                continue
            if args.kc != "both" and candidate_kc != f"KC-{args.kc}":
                continue
            selected.append({**row, "purposive_kc": candidate_kc})

    with args.output.open("x", encoding="utf-8", newline="\n") as output:
        for row in selected:
            output.write(json.dumps(row))
            output.write("\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
