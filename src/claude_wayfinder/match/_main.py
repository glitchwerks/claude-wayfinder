"""CLI orchestration for the dispatch matcher (v5).

Owns ``main()``: parses the ``--catalog-path`` flag, loads the catalog,
reads stdin JSON, delegates to ``build_features``, ``score_entries``,
``decide``, and ``_write_log_entry``, then prints the result JSON to stdout.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from claude_wayfinder.match._catalog import (
    _compute_catalog_hash,
    _emit_catalog_error,
    _get_matcher_version,
    _resolve_catalog_path,
    _resolve_log_path,
    _resolve_overrides_path,
    _write_log_entry,
    load_catalog,
)
from claude_wayfinder.match._compose import compose_route, parse_labels
from claude_wayfinder.match._decide import decide
from claude_wayfinder.match._match import build_features, score_entries
from claude_wayfinder.match._overrides import (
    OverrideRule,
    OverridesError,
    load_overrides,
    resolve_override,
)
from claude_wayfinder.match._types import Labels

#: Exact (case-insensitive) DISPATCH_SHADOW values that disable shadow
#: compute.  Anything else — absent, truthy, or malformed — fails open
#: to ON; see ``_shadow_enabled``.
_SHADOW_FALSEY_VALUES = frozenset({"0", "false", "no"})


def _shadow_enabled() -> bool:
    """Determine whether shadow-route compute should run this call.

    Reads the ``DISPATCH_SHADOW`` environment variable. The gate is
    fail-open: an absent, truthy, or unrecognized/malformed value all
    resolve to ON. Only an exact case-insensitive match of
    ``{"0", "false", "no"}`` resolves to OFF, matching this module's
    other never-break-live-dispatch conventions.

    Returns:
        True if shadow compute should run, False to skip it entirely.
    """
    value = os.environ.get("DISPATCH_SHADOW")
    if value is None:
        return True
    return value.lower() not in _SHADOW_FALSEY_VALUES


def _build_shadow_record(
    labels: Labels,
    live_result: dict[str, Any],
    shadow: dict[str, Any],
    diag: dict[str, Any],
) -> dict[str, Any]:
    """Build the §F.1 shadow record from routing outputs and diagnostics.

    Combines live decision fields, Compose shadow decision fields, label
    context, and per-step §F.1 intermediate state into a single flat
    record suitable for storage under the ``"shadow"`` key of a log entry.

    Args:
        labels: Parsed routing labels from the dispatch context.
        live_result: The live ``decide()`` result dict (stdout decision).
        shadow: The ``compose_route()`` result dict.
        diag: The populated §F.1 diagnostics dict from ``compose_route``.

    Returns:
        Flat dict matching the §F.1 shadow record schema.
    """
    return {
        # Label context
        "domain": labels.domain,
        "posture": labels.posture,
        "confidence": labels.confidence,
        "area_span": labels.area_span,
        # Live (decide) decision mirror
        "live_decision": live_result.get("decision"),
        "live_agent": live_result.get("agent"),
        "live_confidence": live_result.get("confidence"),
        "live_disposition_source": live_result.get("disposition_source"),
        # Shadow (compose_route) decision
        "shadow_decision": shadow.get("decision"),
        "shadow_agent": shadow.get("agent"),
        "shadow_confidence": shadow.get("confidence"),
        "shadow_disposition_source": shadow.get("disposition_source"),
        # §F.1 intermediate state from diagnostics
        "gated_agent_names": diag.get("gated_agent_names"),
        "posture_preferred": diag.get("posture_preferred"),
        "posture_routed": diag.get("posture_routed"),
        "branch": diag.get("branch"),
        "lexical_agreement": diag.get("lexical_agreement"),
        "posture_veto_reason": diag.get("posture_veto_reason"),
        # Agreement flag
        "agreement": live_result.get("agent") == shadow.get("agent"),
    }


def main(argv: list[str] | None = None) -> None:
    """Entry point: read JSON from stdin, write decision JSON to stdout.

    The catalog path is resolved via ``_resolve_catalog_path()``.  If no
    path is available (no ``--catalog-path`` flag and no
    ``DISPATCH_CATALOG_PATH`` env var), emits a ``[CATALOG ERROR]`` banner
    on stderr and exits with code 2.  If the catalog is degraded (missing,
    malformed, or empty), the same banner is emitted.

    Arg resolution order for catalog:

    1. ``--catalog-path <path>`` CLI flag.
    2. ``DISPATCH_CATALOG_PATH`` env var.
    3. Fail loud with ``[CATALOG ERROR]``.

    Log path resolution order:

    1. ``DISPATCH_LOG_PATH`` env var.
    2. Logging silently disabled (no ``~/.claude/`` fallback).

    Args:
        argv: Argument list.  Defaults to ``sys.argv[1:]`` when ``None``.

    Input JSON shape (stdin)::

        {
            "task_description": "...",     # required
            "file_paths":       ["..."],   # optional
            "agent_mentions":   ["..."],   # optional
            "tool_mentions":    ["..."],   # optional
            "command_prefix":   null,      # optional
            "session_id":       "..."      # optional — fix #294; written
                                           # verbatim into matcher_decision
                                           # log entry when present
        }

    Output JSON shape (stdout)::

        {
            "decision":     "delegate" | "self_handle" | ...,
            "agent":        "code-writer",   # when decision implies one
            "skills":       ["python"],      # for delegate/self_handle/advisory
            "confidence":   0.92,
            "rationale":    "matched keywords: implement.",
            "alternatives": [{"agent": "...", "score": 0.x}, ...]
        }
    """
    # --- Parse CLI args ---
    parser = argparse.ArgumentParser(
        description="Deterministic 7-decision dispatch matcher (v5).",
        add_help=True,
    )
    parser.add_argument(
        "--catalog-path",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Path to the dispatch-catalog.json file.  "
            "Resolution order: --catalog-path > DISPATCH_CATALOG_PATH env "
            "var > error.  The old ~/.claude/state/ default has been removed."
        ),
    )
    args = parser.parse_args(argv)

    # --- Load catalog ---
    catalog_path = _resolve_catalog_path(args.catalog_path)

    if not catalog_path.exists():
        _emit_catalog_error(f"file not found at {catalog_path}")

    catalog_raw_text: str = ""
    try:
        catalog_raw_text = catalog_path.read_text(encoding="utf-8")
        entries = load_catalog(catalog_path)
    except json.JSONDecodeError as exc:
        _emit_catalog_error(f"malformed JSON ({exc})")
    if not entries:
        # load_catalog returns [] for empty catalogs rather than raising
        # (audit-catalog needs to load them without crashing).  The dispatch
        # runtime treats zero entries as a degraded state and errors out.
        _emit_catalog_error("Catalog contains zero entries.")

    catalog_hash = _compute_catalog_hash(catalog_raw_text)

    # --- Parse stdin ---
    raw_input = sys.stdin.read()
    try:
        context: dict[str, Any] = json.loads(raw_input)
    except json.JSONDecodeError as exc:
        result = {
            "decision": "needs_more_detail",
            "confidence": 0.0,
            "disposition_source": "scored",
            "rationale": f"Could not parse input JSON: {exc}",
            "alternatives": [],
        }
        # Log the parse failure before returning.  catalog_hash="" is the
        # sentinel for "catalog not loaded; parse failed pre-catalog" so
        # that NDJSON consumers can distinguish these entries by hash shape.
        _write_log_entry(
            {},
            result,
            "",
            _resolve_log_path(),
            override_id=None,
        )
        print(json.dumps(result, sort_keys=True), flush=True)
        return

    # --- Extract features ---
    features = build_features(context)

    # --- Load + resolve overrides (issue #213) ---
    overrides_path = _resolve_overrides_path()
    override_rules: list[OverrideRule] = []
    if overrides_path is not None:
        try:
            override_rules = load_overrides(overrides_path)
        except OverridesError as exc:
            print(
                f"[OVERRIDES ERROR] {exc}; proceeding with scored matching.",
                file=sys.stderr,
            )
        # Stderr note only when consumer has opted in to overrides
        # (Rev 1 CONCERN-1: gated on env var being set).
        print(
            f"[dispatch] overrides: {len(override_rules)} rules loaded"
            f" from {overrides_path}",
            file=sys.stderr,
        )

    # --- Short-circuit on override match ---
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
        _write_log_entry(
            context,
            result,
            catalog_hash,
            _resolve_log_path(),
            override_id=rule.id,
        )
        # Include catalog_hash and matcher_version in stdout so the JS
        # hook (log-dispatch-decision.js) can write a fully-attributed
        # log row without null fields (issue #311).
        result["catalog_hash"] = catalog_hash
        result["matcher_version"] = _get_matcher_version()
        print(json.dumps(result, sort_keys=True), flush=True)
        return

    # --- Score all entries ---
    # score_entries filters agents via is_agent_routable (excludes router and
    # plugin agents), scores every entry, and sorts each pool by
    # (-score, name).  See match._match.score_entries for details.
    scored_agents, scored_skills = score_entries(entries, features)

    # --- Compose decision ---
    result = decide(scored_agents, scored_skills, features, entries)

    # --- Shadow compute (telemetry-only; gated by DISPATCH_SHADOW) ---
    # When the gate is OFF, shadow compute is skipped entirely (not
    # computed-then-discarded) — compose_route is never invoked and the
    # log entry omits the "shadow" key. See _shadow_enabled().
    shadow_record: dict[str, Any] | None = None
    if _shadow_enabled():
        try:
            catalog_agent_names: frozenset[str] = frozenset(
                se.entry.name for se in scored_agents
            )
            labels: Labels = parse_labels(context)
            diag: dict[str, Any] = {}
            shadow = compose_route(
                labels,
                scored_agents,
                scored_skills,
                features,
                entries,
                catalog_agent_names,
                diagnostics=diag,
            )
            shadow_record = _build_shadow_record(labels, result, shadow, diag)
        except Exception as exc:   # shadow must never break live dispatch
            print(
                f"[dispatch] shadow compute failed: {exc}",
                file=sys.stderr,
            )
            shadow_record = None

    # --- Log decision (non-fatal: log failure never blocks stdout output) ---
    _write_log_entry(
        context,
        result,
        catalog_hash,
        _resolve_log_path(),
        override_id=None,
        shadow_data=shadow_record,
    )

    # --- Emit JSON (enriched with catalog_hash / matcher_version) ---
    # These fields are added AFTER the log write so the log entry shape
    # is unchanged; the fields are present in stdout for the JS hook
    # (log-dispatch-decision.js) to write a complete attributed row
    # (issue #311).
    result["catalog_hash"] = catalog_hash
    result["matcher_version"] = _get_matcher_version()
    print(json.dumps(result, sort_keys=True), flush=True)
