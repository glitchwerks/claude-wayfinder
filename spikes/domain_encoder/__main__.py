"""CLI entry point for the domain-encoder spike.

Usage::

    python -m spikes.domain_encoder "Fix the failing test in test_api.py"
    python -m spikes.domain_encoder --json "Deploy to Kubernetes"

Cross-process determinism check::

    python -m spikes.domain_encoder --json "some text" > run1.json
    python -m spikes.domain_encoder --json "some text" > run2.json
    diff run1.json run2.json   # must be empty

The --json flag outputs a JSON object with ``distribution``, ``top_label``,
and ``entropy`` so the caller can diff full-precision floats.
"""

from __future__ import annotations

import argparse
import json
import sys


def _main(argv: list[str] | None = None) -> int:
    """Entry point for the domain encoder CLI.

    Args:
        argv: Command-line arguments (defaults to sys.argv[1:]).

    Returns:
        Exit code (0 = success).
    """
    parser = argparse.ArgumentParser(
        prog="python -m spikes.domain_encoder",
        description="Classify a task description into the 5-way domain distribution.",
    )
    parser.add_argument(
        "text",
        nargs="?",
        help="Task description to classify.  Omit to read from stdin.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output full-precision JSON (for cross-process determinism checks).",
    )
    parser.add_argument(
        "--model",
        default=None,
        help=(
            "HuggingFace model id or local path "
            "(default: minishlab/potion-base-8M via DEFAULT_MODEL_NAME)."
        ),
    )
    parser.add_argument(
        "--revision",
        default=None,
        help=(
            "Exact git commit SHA to load from the HF cache "
            "(default: DEFAULT_MODEL_REVISION from _classifier.py; "
            "pass 'none' to disable pinning)."
        ),
    )
    args = parser.parse_args(argv)

    # Resolve input text
    if args.text:
        text = args.text
    else:
        text = sys.stdin.read().strip()
        if not text:
            parser.error("No text provided (either as argument or on stdin).")

    try:
        from spikes.domain_encoder._classifier import (
            DEFAULT_MODEL_NAME,
            DEFAULT_MODEL_REVISION,
            DomainClassifier,
        )
    except ImportError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    model_name = args.model if args.model is not None else DEFAULT_MODEL_NAME
    # --revision none disables pinning (mutable latest); omitting uses the default pin
    if args.revision is not None:
        revision = None if args.revision.lower() == "none" else args.revision
    else:
        revision = DEFAULT_MODEL_REVISION
    clf = DomainClassifier.from_pretrained(model_name, revision=revision)
    result = clf.classify(text)

    if args.json:
        output = {
            "text": text,
            "top_label": result.top_label,
            "entropy": result.entropy,
            "distribution": result.distribution,
        }
        # Ensure full float precision for determinism comparison
        print(json.dumps(output, sort_keys=True))
    else:
        print(f"top_label : {result.top_label}")
        print(f"entropy   : {result.entropy:.6f} bits (max {2.321928:.6f})")
        print("distribution:")
        for label, prob in sorted(result.distribution.items()):
            bar = "#" * int(prob * 40)
            print(f"  {label:<14} {prob:.6f}  {bar}")

    return 0


if __name__ == "__main__":
    sys.exit(_main())
