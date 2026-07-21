"""Create a labeler-safe JSONL view of a shadow corpus.

The stripped view contains only the corpus identifier and raw input signal.
Caller labels, matcher decisions, and decision-adjacent metadata are excluded.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

_RETAINED_INPUT_KEYS = (
    "task_description",
    "file_paths",
    "agent_mentions",
    "tool_mentions",
    "command_prefix",
)


def strip_row(row: dict[str, object]) -> dict[str, object]:
    """Return a labeler-safe copy of one shadow-corpus row.

    Args:
        row: Raw shadow-corpus row containing a nested ``input`` mapping.

    Returns:
        A new row containing only ``corpus_id`` and the permitted raw-signal
        input fields. Missing permitted fields remain absent.

    Raises:
        TypeError: If ``row["input"]`` is not a dictionary.
    """
    input_data = row["input"]
    if not isinstance(input_data, dict):
        raise TypeError("row['input'] must be a dictionary")

    stripped_input = {
        key: copy.deepcopy(input_data[key])
        for key in _RETAINED_INPUT_KEYS
        if key in input_data
    }
    return {
        "corpus_id": copy.deepcopy(row["corpus_id"]),
        "input": stripped_input,
    }


def main(argv: list[str]) -> int:
    """Strip a JSONL shadow corpus into a new labeler-safe JSONL file.

    Args:
        argv: Command-line arguments excluding the program name.

    Returns:
        Zero when every input row has been written successfully.

    Raises:
        FileExistsError: If the output path already exists.
        json.JSONDecodeError: If a nonblank input line is not valid JSON.
        OSError: If either path cannot be opened or accessed.
        TypeError: If an input JSON value does not satisfy the row contract.
    """
    parser = argparse.ArgumentParser(
        description="Create a labeler-safe JSONL view of a shadow corpus.",
    )
    parser.add_argument(
        "--input",
        required=True,
        type=Path,
        help="Path to the source shadow-corpus JSONL file.",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Path for the new labeler-safe JSONL file.",
    )
    args = parser.parse_args(argv)

    with args.input.open(encoding="utf-8") as source:
        with args.output.open("x", encoding="utf-8", newline="\n") as output:
            for raw_line in source:
                if not raw_line.strip():
                    continue
                row = json.loads(raw_line)
                if not isinstance(row, dict):
                    raise TypeError("each JSONL row must be a JSON object")
                output.write(json.dumps(strip_row(row)))
                output.write("\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
