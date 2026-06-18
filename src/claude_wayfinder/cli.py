"""CLI entry point for the ``claude_wayfinder`` package.

Exposes the ``demo`` sub-command which iterates the bundled demo prompts
against the bundled demo catalog and prints human-readable output for each
of the 7 decision branches.

Usage::

    python -m claude_wayfinder demo

No external dependencies, no network access, no user state required.
The bundled fixtures are loaded from the package's ``fixtures/`` directory.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import claude_wayfinder._health as _health_mod
import claude_wayfinder.audit_catalog as _audit_mod
import claude_wayfinder.build_catalog as _build_catalog_mod
import claude_wayfinder.log_filter as _log_filter_mod
from claude_wayfinder._dispatch import run_batch_dispatch, run_dispatch
from claude_wayfinder.match import (
    build_features,
    decide,
    load_catalog,
    score_entries,
)
from claude_wayfinder.match._catalog import _resolve_overrides_path
from claude_wayfinder.match._overrides import (
    OverridesError,
    load_overrides,
    resolve_override,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Path to the bundled demo fixtures directory (shipped with the package).
_FIXTURES_DIR: Path = Path(__file__).parent / "fixtures"
_DEMO_CATALOG: Path = _FIXTURES_DIR / "demo-catalog.json"
_DEMO_PROMPTS: Path = _FIXTURES_DIR / "demo-prompts.json"

# Decision branch label reserved but not produced by the v0.1 matcher.
_ASK_USER_BRANCH = "ask_user"

# Human-readable banner displayed once at the start of the demo.
_BANNER = """
=============================================================
  claude-wayfinder demo — 7-decision dispatch matcher (v5)
=============================================================
Bundled catalog:  {catalog}
Bundled prompts:  {prompts}
-------------------------------------------------------------
""".strip()


# ---------------------------------------------------------------------------
# Core demo logic
# ---------------------------------------------------------------------------


def _score_catalog(
    entries: list[Any],
    features: Any,
) -> tuple[list[Any], list[Any]]:
    """Score all catalog entries against features and return sorted pools.

    Args:
        entries: List of ``CatalogEntry`` objects from the catalog.
        features: ``Features`` extracted from the dispatch context.

    Returns:
        A tuple of ``(scored_agents, scored_skills)`` each sorted by score
        descending then name ascending.
    """
    return score_entries(entries, features)


def _format_decision(result: dict[str, Any]) -> str:
    """Format a decision dict as a concise human-readable line.

    Args:
        result: Decision dict returned by ``decide()``.

    Returns:
        Multi-line string summarising the decision.
    """
    lines: list[str] = []
    decision = result.get("decision", "?")
    confidence = result.get("confidence", 0.0)
    rationale = result.get("rationale", "")
    lines.append(f"  decision    : {decision}")
    lines.append(f"  confidence  : {confidence:.4f}")
    if "agent" in result:
        lines.append(f"  agent       : {result['agent']}")
    if result.get("skills"):
        lines.append(f"  skills      : {', '.join(result['skills'])}")
    lines.append(f"  rationale   : {rationale}")
    alts = result.get("alternatives", [])
    if alts:
        alt_str = ", ".join(
            f"{a['agent']}({a['score']:.2f})" for a in alts
        )
        lines.append(f"  alternatives: {alt_str}")
    if "disposition_source" in result:
        lines.append(
            f"  disposition_source : {result['disposition_source']}"
        )
    if "override_id" in result:
        lines.append(f"  override_id : {result['override_id']}")
    return "\n".join(lines)


def run_demo(out: Any = None) -> int:
    """Run the demo against the bundled fixtures and write output.

    Iterates ``demo-prompts.json``, runs the matcher for each non-reserved
    prompt, and prints one formatted block per decision branch.  The
    ``ask_user`` branch is reserved in v0.1 and is shown with a note
    rather than a live matcher call.

    Args:
        out: File-like object to write output to.  Defaults to
            ``sys.stdout``.

    Returns:
        Exit code: 0 on success, non-zero on error.
    """
    if out is None:
        out = sys.stdout

    # --- Load catalog ---
    try:
        entries = load_catalog(_DEMO_CATALOG)
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        print(
            f"[DEMO ERROR] Failed to load bundled catalog: {exc}",
            file=sys.stderr,
        )
        return 1

    # --- Load prompts ---
    try:
        prompts: list[dict[str, Any]] = json.loads(
            _DEMO_PROMPTS.read_text(encoding="utf-8")
        )
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(
            f"[DEMO ERROR] Failed to load bundled prompts: {exc}",
            file=sys.stderr,
        )
        return 1

    # --- Banner ---
    print(
        _BANNER.format(
            catalog=_DEMO_CATALOG.name,
            prompts=_DEMO_PROMPTS.name,
        ),
        file=out,
    )
    print("", file=out)

    # --- Load overrides (if $DISPATCH_OVERRIDES_PATH is set) ---
    overrides_path = _resolve_overrides_path()
    override_rules: list[Any] = []
    if overrides_path is not None:
        try:
            override_rules = load_overrides(overrides_path)
        except OverridesError as exc:
            print(
                f"[OVERRIDES ERROR] {exc}; demo proceeding with "
                "scored matching.",
                file=sys.stderr,
            )

    # --- Iterate prompts ---
    for idx, prompt in enumerate(prompts, start=1):
        branch = prompt.get("_branch", f"prompt-{idx}")
        note = prompt.get("_note", "")

        print(f"[{idx}/7] Branch: {branch}", file=out)
        if note:
            print(f"  note        : {note}", file=out)

        # ask_user is reserved — the matcher never produces it in v0.1.
        if branch == _ASK_USER_BRANCH:
            print(
                "  decision    : ask_user",
                file=out,
            )
            print(
                "  rationale   : Reserved — not produced by the v0.1 "
                "matcher. ask_user is part of the 7-decision contract "
                "and reserved for future clarification flows.",
                file=out,
            )
            print("", file=out)
            continue

        # Build context (drop private _-prefixed keys).
        context: dict[str, Any] = {
            k: v for k, v in prompt.items() if not k.startswith("_")
        }
        # Handle null task_description gracefully (should not occur for
        # non-reserved prompts, but guard defensively).
        if context.get("task_description") is None:
            context["task_description"] = ""

        features = build_features(context)

        # Override short-circuit: if a rule matches, emit its decision and
        # skip scoring entirely for this prompt.
        override_match = resolve_override(override_rules, features)
        if override_match is not None:
            rule = override_match.rule
            result: dict[str, Any] = {
                "decision": rule.decision,
                "confidence": rule.confidence,
                "rationale": rule.rationale,
                "alternatives": [],
                "disposition_source": "override",
                "override_id": rule.id,
            }
            if rule.agent is not None:
                result["agent"] = rule.agent
            if rule.skills:
                result["skills"] = list(rule.skills)
            task_desc = context.get("task_description", "")
            paths = context.get("file_paths", [])
            print(f"  input       : {task_desc!r}", file=out)
            if paths:
                print(f"  file_paths  : {paths}", file=out)
            print(_format_decision(result), file=out)
            print("", file=out)
            continue

        scored_agents, scored_skills = _score_catalog(entries, features)
        result = decide(scored_agents, scored_skills, features, entries)

        task_desc = context.get("task_description", "")
        paths = context.get("file_paths", [])
        print(f"  input       : {task_desc!r}", file=out)
        if paths:
            print(f"  file_paths  : {paths}", file=out)
        print(_format_decision(result), file=out)
        print("", file=out)

    print("-------------------------------------------------------------", file=out)
    print("Demo complete. All 7 decision branches shown.", file=out)
    return 0


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the top-level argument parser.

    Returns:
        A configured ``ArgumentParser`` for ``python -m claude_wayfinder``.
    """
    parser = argparse.ArgumentParser(
        prog="python -m claude_wayfinder",
        description=(
            "claude-wayfinder — deterministic 7-decision dispatch matcher."
        ),
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser(
        "demo",
        help=(
            "Run the bundled demo: 7 example prompts, one per decision "
            "branch, with the matcher's output for each."
        ),
    )

    dispatch_parser = sub.add_parser(
        "dispatch",
        help=(
            "Mode-aware dispatch: real-catalog mode by default when a "
            "catalog exists at the canonical path "
            "($CLAUDE_HOME/state/dispatch-catalog.json or "
            "~/.claude/state/dispatch-catalog.json) or when "
            "$DISPATCH_CATALOG_PATH is set; use --demo to opt into bundled "
            "fixtures.  Reads dispatch context JSON from stdin; writes "
            "decision JSON to stdout."
        ),
    )
    dispatch_parser.add_argument(
        "--demo",
        action="store_true",
        default=False,
        help=(
            "Demo mode: run the bundled fixture prompts and ignore any "
            "catalog configuration.  Overrides $DISPATCH_CATALOG_PATH and "
            "the canonical-default lookup.  Useful for testing the dispatch "
            "pipeline without a real catalog."
        ),
    )
    dispatch_parser.add_argument(
        "--batch",
        action="store_true",
        default=False,
        help=(
            "Batch mode: read one dispatch context JSON object per line from "
            "stdin (NDJSON) and write one decision JSON object per line to "
            "stdout (NDJSON).  Blank lines are skipped; malformed lines "
            "produce an error record without aborting the batch.  The catalog "
            "is loaded once per invocation.  Each output line includes an "
            "'input_index' field (0-based) so async consumers can correlate "
            "decisions with inputs.  Example: "
            "echo '{\"task_description\": \"...\"}' | "
            "python -m claude_wayfinder dispatch --batch"
        ),
    )

    # --- catalog subcommand ---
    catalog_parser = sub.add_parser(
        "catalog",
        help="Catalog management subcommands.",
    )
    catalog_sub = catalog_parser.add_subparsers(dest="catalog_command")

    build_parser = catalog_sub.add_parser(
        "build",
        help=(
            "Build the dispatch catalog from skill SKILL.md sidecars and "
            "agent frontmatter files."
        ),
    )
    _build_catalog_mod.add_catalog_build_args(build_parser)

    # --- audit-catalog subcommand ---
    audit_parser = sub.add_parser(
        "audit-catalog",
        help=(
            "Catalog-wide static analysis: conflict pairs, structural "
            "validation, matcher-aware semantic checks."
        ),
    )
    _audit_mod.add_audit_catalog_args(audit_parser)

    # --- health subcommand ---
    # ``_health.main()`` owns its own argparse surface (--ci, --report, and
    # several path flags).  We register a stub subparser with add_help=False
    # so the command appears in top-level --help, then pass all remaining
    # argv through to ``_health.main()`` unchanged.
    sub.add_parser(
        "health",
        add_help=False,
        help="Router health report (CI invariants + runtime telemetry).",
    )

    # --- log-filter subcommand ---
    log_filter_parser = sub.add_parser(
        "log-filter",
        help=(
            "Filter the dispatch-log to organic matcher_decision entries "
            "(non-empty session_id, post-v1.1.1 attribution fix).  "
            "Prints the organic entry count by default; use --emit-jsonl "
            "to stream the filtered entries to stdout."
        ),
    )
    _log_filter_mod.add_log_filter_args(log_filter_parser)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse CLI arguments and dispatch to the requested sub-command.

    Args:
        argv: Argument list.  Defaults to ``sys.argv[1:]`` when ``None``.

    Returns:
        Exit code: 0 on success, non-zero on error.
    """
    # Short-circuit for the ``health`` subcommand before running the top-level
    # parser.  ``_health.main()`` owns its own argparse surface and must
    # receive all flags that follow "health" unmodified.  Letting the top-level
    # parser see those flags would cause it to reject them as unknown options.
    effective_argv: list[str] = sys.argv[1:] if argv is None else list(argv)
    if effective_argv and effective_argv[0] == "health":
        return _health_mod.main(effective_argv[1:])

    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "demo":
        return run_demo()

    if args.command == "dispatch":
        demo_flag = getattr(args, "demo", False)
        if getattr(args, "batch", False):
            return run_batch_dispatch(demo=demo_flag)
        return run_dispatch(demo=demo_flag)

    if args.command == "catalog":
        if getattr(args, "catalog_command", None) == "build":
            return _build_catalog_mod.run_catalog_build(args)
        # ``catalog`` with no sub-subcommand — print catalog help.
        parser.parse_args(["catalog", "--help"])
        return 1

    if args.command == "audit-catalog":
        return _audit_mod.run_audit_cli(args)

    if args.command == "log-filter":
        return _log_filter_mod.run_log_filter_cli(args)

    # No sub-command given — print help and exit non-zero.
    parser.print_help()
    return 1
