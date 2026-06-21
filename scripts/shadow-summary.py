"""Summarize Matcher v3 shadow-mode results from the dispatch-log JSONL.

Reads a dispatch-log JSONL file (one JSON object per line), extracts entries
that carry a ``"shadow"`` dict (added in M15-5), and prints an accumulation
summary: total entries, shadow-covered entries, live/shadow agreement count,
and branch distribution.

Usage::

    python scripts/shadow-summary.py [log_path] [--json]

Resolution order when ``log_path`` is omitted:
  1. ``$DISPATCH_LOG_PATH`` environment variable
  2. ``~/.claude/state/dispatch-log.jsonl``
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path


def summarize(path: Path) -> dict[str, object]:
    """Count shadow-mode metrics in a dispatch-log JSONL file.

    Reads the file line by line, silently skipping blank lines and any
    line that fails ``json.loads``.  For each parsed entry the function
    looks for a nested ``"shadow"`` dict and accumulates:

    * total entries parsed
    * entries with a shadow record
    * agreement count (``shadow["agreement"]`` truthy)
    * branch distribution (``shadow["branch"]`` values)

    Args:
        path: Absolute or relative path to the dispatch-log JSONL file.
            The caller is responsible for confirming the file exists before
            calling this function.

    Returns:
        A dict with keys:
            ``entries`` (int): total parsed lines.
            ``shadow`` (int): lines carrying a shadow dict.
            ``agreement`` (int): shadow records where agreement is truthy.
            ``branches`` (dict[str, int]): branch-value frequency map.
    """
    total = 0
    shadow_count = 0
    agreement_count = 0
    branch_dist: Counter[str] = Counter()

    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            total += 1
            shadow = entry.get("shadow")
            if not isinstance(shadow, dict):
                continue

            shadow_count += 1
            if shadow.get("agreement"):
                agreement_count += 1
            branch = shadow.get("branch")
            if branch is not None:
                branch_dist[str(branch)] += 1

    return {
        "entries": total,
        "shadow": shadow_count,
        "agreement": agreement_count,
        "branches": dict(branch_dist),
    }


def _resolve_log_path(arg: str | None) -> Path:
    """Resolve the dispatch-log path from CLI arg or fallback chain.

    Resolution order:
      1. Explicit ``log_path`` positional argument (if provided).
      2. ``$DISPATCH_LOG_PATH`` environment variable.
      3. ``~/.claude/state/dispatch-log.jsonl``.

    Args:
        arg: The value of the positional ``log_path`` CLI argument, or
            ``None`` when omitted.

    Returns:
        Resolved ``Path`` object (not yet verified to exist).
    """
    if arg:
        return Path(arg)
    env = os.environ.get("DISPATCH_LOG_PATH")
    if env:
        return Path(env)
    return Path("~/.claude/state/dispatch-log.jsonl").expanduser()


def main() -> None:
    """Entry point for the shadow-summary CLI.

    Parses arguments, resolves the log path, runs :func:`summarize`, and
    prints either a human-readable line or a JSON object depending on the
    ``--json`` flag.  Exits non-zero if the log file does not exist.
    """
    parser = argparse.ArgumentParser(
        description="Summarize Matcher v3 shadow-mode results from dispatch-log JSONL.",
    )
    parser.add_argument(
        "log_path",
        nargs="?",
        default=None,
        help=(
            "Path to the dispatch-log JSONL file.  When omitted, falls back"
            " to $DISPATCH_LOG_PATH, then ~/.claude/state/dispatch-log.jsonl."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit a JSON object instead of the default human-readable line.",
    )
    args = parser.parse_args()

    log_path = _resolve_log_path(args.log_path)

    if not log_path.exists():
        print(
            f"shadow-summary: log file not found: {log_path}",
            file=sys.stderr,
        )
        sys.exit(1)

    result = summarize(log_path)

    if args.as_json:
        print(json.dumps(result))
    else:
        s = result["shadow"]
        a = result["agreement"]
        print(
            f"entries={result['entries']}"
            f" shadow={s}"
            f" agreement={a}/{s}"
            f" branches={result['branches']}"
        )


if __name__ == "__main__":
    main()
