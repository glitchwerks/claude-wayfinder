"""CLI orchestration for the dispatch matcher (v5).

Owns ``main()``: parses the ``--catalog-path`` flag, loads the catalog,
reads stdin JSON, delegates to ``build_features``, ``score_entries``,
``decide``, and ``_write_log_entry``, then prints the result JSON to stdout.

Two environment gates control Compose routing with opposite safe defaults.
``DISPATCH_SHADOW`` is the coarse, all-domain compute and hard-routing kill
switch: absent or malformed values fail open to ON, while an explicit falsey
value skips Compose entirely. ``DISPATCH_HARD_ROUTING_DOMAINS`` is the
surgical per-domain serving gate: absent or empty values resolve OFF, unknown
tokens are dropped, and parse failures fail closed to no hard routing.
Consequently, ``DISPATCH_SHADOW=0`` always serves the lexical ``decide()``
result regardless of the configured hard domains.
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
from claude_wayfinder.match._cells import DOMAIN_AGENT_MAP
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


def _parse_hard_routing_domains() -> frozenset[str]:
    """Resolve domains explicitly consented to Compose serving.

    Reads ``DISPATCH_HARD_ROUTING_DOMAINS`` as comma-separated domain
    tokens. Parsing is fail-closed: absent, empty, or whitespace-only
    values resolve to an empty set. Tokens are stripped, lowercased,
    deduplicated, and limited to keys in ``DOMAIN_AGENT_MAP`` plus the
    explicit ``"is_any"`` token. Unknown tokens are dropped with a
    warning, and any unexpected parse error also warns and resolves OFF.

    Returns:
        Recognized hard-routing domain tokens, or an empty frozenset when
        hard routing is disabled or parsing fails.
    """
    try:
        value = os.environ.get("DISPATCH_HARD_ROUTING_DOMAINS")
        if value is None or not value.strip():
            return frozenset()

        tokens = {
            token.strip().lower()
            for token in value.split(",")
            if token.strip()
        }
        recognized = (set(DOMAIN_AGENT_MAP) - {None}) | {"is_any"}
        unknown = tokens - recognized
        if unknown:
            print(
                "[dispatch] dropping unrecognized hard-routing domain(s): "
                + ", ".join(sorted(unknown)),
                file=sys.stderr,
            )
        return frozenset(tokens & recognized)
    except Exception as exc:
        print(
            f"[dispatch] failed to parse hard-routing domains; disabling: {exc}",
            file=sys.stderr,
        )
        return frozenset()


def _build_shadow_record(
    labels: Labels,
    live_result: dict[str, Any],
    shadow: dict[str, Any],
    diag: dict[str, Any],
    *,
    served: dict[str, Any],
    served_arm: str,
    hard_routing_domains: frozenset[str],
) -> dict[str, Any]:
    """Build the §F.1 shadow record from routing outputs and diagnostics.

    The historical ``live_*`` and ``shadow_*`` names identify algorithm
    arms: ``live_*`` is always the lexical ``decide()`` result and
    ``shadow_*`` is always the ``compose_route()`` result. They no longer
    indicate what stdout served; that meaning belongs to ``served_*`` and
    ``served_arm``. The misnomers are retained deliberately because renaming
    them would break already-joined corpora and require dual-read support in
    ``scripts/corpus/eval/_kc.py``, ``scripts/shadow-kc-report.py``, and
    ``scripts/shadow-summary.py``.

    Args:
        labels: Parsed routing labels from the dispatch context.
        live_result: The pure-lexical ``decide()`` algorithm result.
        shadow: The ``compose_route()`` result dict.
        diag: The populated §F.1 diagnostics dict from ``compose_route``.
        served: The exact decision dict selected for logging and stdout.
        served_arm: Algorithm selected by ``main()``; ``"lexical"`` or
            ``"compose"``.
        hard_routing_domains: Resolved domain set used for the serving
            decision, passed through without re-reading the environment.

    Returns:
        Flat dict matching shadow record schema version 2.
    """
    return {
        "shadow_schema_version": 2,
        "hard_routing_domains": sorted(hard_routing_domains),
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
        # Decision actually served by main()
        "served_arm": served_arm,
        "served_decision": served.get("decision"),
        "served_agent": served.get("agent"),
        "served_confidence": served.get("confidence"),
        "served_disposition_source": served.get("disposition_source"),
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
    served: dict[str, Any] = result

    # --- Shadow compute and scoped serving (gated by DISPATCH_SHADOW) ---
    # When the gate is OFF, shadow compute is skipped entirely (not
    # computed-then-discarded) — compose_route is never invoked and the
    # log entry omits the "shadow" key. See _shadow_enabled().
    shadow_record: dict[str, Any] | None = None
    hard_domains: frozenset[str] = _parse_hard_routing_domains()
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
            served_arm: str = "lexical"
            if labels.domain is not None and labels.domain in hard_domains:
                served = shadow
                served_arm = "compose"
            shadow_record = _build_shadow_record(
                labels,
                result,
                shadow,
                diag,
                served=served,
                served_arm=served_arm,
                hard_routing_domains=hard_domains,
            )
        except Exception as exc:   # shadow must never break live dispatch
            print(
                f"[dispatch] shadow compute failed: {exc}",
                file=sys.stderr,
            )
            served = result
            shadow_record = None

    # --- Log decision (non-fatal: log failure never blocks stdout output) ---
    _write_log_entry(
        context,
        served,
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
    served["catalog_hash"] = catalog_hash
    served["matcher_version"] = _get_matcher_version()
    print(json.dumps(served, sort_keys=True), flush=True)
