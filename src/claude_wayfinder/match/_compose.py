"""Shippable single-context compose_route for two-axis routing (M15-2, #419).

Lifts the per-entry routing logic of the offline oracle
``scripts/corpus/eval/_systems.run_supplied_compose`` (lines 1216–1311)
into a standalone, unit-testable function with two live-only additions:

1. The §D.1 **confidence-high gate**: posture routing only fires when the
   caller explicitly asserts ``confidence="high"``; absent or non-high
   confidence falls through to ``decide()``.
2. The §B.1 **plausibility veto** (``_is_lexically_plausible``): applied
   in Branch 3 only — blocks a cell-preferred agent from routing when
   lexical scores indicate it is implausible.  NOT applied to Branch 1
   (investigator is a structural route, rarely lexically top-k even when
   correct) or Branch 2 (sentinel abstention, never delegation).

Branch 2 (sentinel → ``self_handle``) is intentionally NOT gated by
confidence or the veto — it encodes the ``project_meta × build``
router carve-out, which must hold regardless of caller confidence.

Public API
----------
- ``parse_labels`` — build a ``Labels`` from a context dict.
- ``confidence_is_high`` — predicate on ``Labels.confidence``.
- ``_is_lexically_plausible`` — §B.1 plausibility veto helper.
- ``compose_route`` — the main routing function.

Hard prohibitions (from the contract)
--------------------------------------
- Do NOT import or call ``_route_from_postures``,
  ``_run_all_extractors``, ``_postures_from_extractor_results``, or
  ``POSTURE_PRIORITY`` (the killed-#357 extractor route).
- Do NOT import ``_DELEGATE_THRESHOLD`` as a literal — always import
  it from ``._decide``.
- Do NOT modify ``_CELL_MAP`` or ``DOMAIN_AGENT_MAP`` in ``_cells.py``.
- Do NOT wire this module into ``_main.py`` in M15-2 (wiring is M15-7).
"""

from __future__ import annotations

from typing import Any, Mapping

from claude_wayfinder.match._cells import (
    DOMAIN_AGENT_MAP,
    SELF_HANDLE_SENTINEL,
    cell_map_lookup,
    gate_agents,
)
from claude_wayfinder.match._decide import (
    _DELEGATE_THRESHOLD,
    decide,
)
from claude_wayfinder.match._types import (
    CatalogEntry,
    Features,
    Labels,
    ScoredEntry,
)

# ---------------------------------------------------------------------------
# Label helpers
# ---------------------------------------------------------------------------


def parse_labels(context: Mapping[str, Any]) -> Labels:
    """Build a ``Labels`` instance from a raw context mapping.

    Reads ``domain``, ``posture``, ``confidence``, and ``area_span``
    from *context*.  Empty strings are normalised to ``None`` for the
    string fields.  ``area_span`` is coerced to ``int``; any value
    that is missing, non-numeric, or ``< 1`` is silently replaced
    with ``1`` (never raises).

    Args:
        context: Arbitrary mapping (e.g. the JSON context dict).
            Missing keys default gracefully; no ``KeyError`` is raised.

    Returns:
        A frozen :class:`Labels` instance.
    """
    def _str_or_none(key: str) -> str | None:
        val = context.get(key)
        if val is None:
            return None
        s = str(val)
        return s if s else None

    raw_span = context.get("area_span")
    area_span: int = 1
    if raw_span is not None:
        try:
            coerced = int(raw_span)
            area_span = coerced if coerced >= 1 else 1
        except (ValueError, TypeError):
            area_span = 1

    return Labels(
        domain=_str_or_none("domain"),
        posture=_str_or_none("posture"),
        confidence=_str_or_none("confidence"),
        area_span=area_span,
    )


def confidence_is_high(labels: Labels) -> bool:
    """Return ``True`` iff ``labels.confidence`` is exactly ``"high"``.

    Implements the §D.1 fail-safe: absent, ``None``, ``"medium"``, and
    ``"low"`` all resolve to ``False``; only ``"high"`` resolves to
    ``True``.

    Args:
        labels: The routing label set.

    Returns:
        ``True`` when ``labels.confidence == "high"``, else ``False``.
    """
    return labels.confidence == "high"


# ---------------------------------------------------------------------------
# Plausibility veto
# ---------------------------------------------------------------------------


def _is_lexically_plausible(
    preferred: str | None,
    gated: list[ScoredEntry],
) -> bool:
    """§B.1 matcher-side plausibility veto.

    Blocks a posture-preferred agent from routing when the lexical
    scores indicate it is implausible in the current context.  Returns
    ``True`` (plausible, allow routing) under either condition:

    1. *preferred* appears in the **top-3** entries of *gated* (by
       score; *gated* is assumed score-sorted descending).
    2. *preferred* has a score ``>= _DELEGATE_THRESHOLD - 0.15``
       anywhere in *gated* (regardless of rank).

    This is a **veto, not a selector** — it only ever BLOCKS a posture
    route into the lexical fallback; it does not select a winner.

    Args:
        preferred: Name of the posture-preferred agent, or ``None``.
        gated: Score-sorted (descending) list of gated
            :class:`ScoredEntry` objects.

    Returns:
        ``True`` when *preferred* is lexically plausible, ``False``
        otherwise (including when *preferred* is ``None`` or absent
        from *gated*).
    """
    if preferred is None:
        return False

    # Build name→score lookup for the full gated list.
    score_by_name: dict[str, float] = {
        se.entry.name: se.score for se in gated
    }

    if preferred not in score_by_name:
        return False

    # Condition 1: preferred in top-3 by rank.
    top3_names = {se.entry.name for se in gated[:3]}
    if preferred in top3_names:
        return True

    # Condition 2: preferred's score meets the floor threshold.
    floor = _DELEGATE_THRESHOLD - 0.15
    return score_by_name[preferred] >= floor


# ---------------------------------------------------------------------------
# ops GitHub tool-shape discriminator (#448, supersedes #445)
# ---------------------------------------------------------------------------

_WRITE_TOOL_PREFIXES: tuple[str, ...] = (
    "create",
    "add_",
    "update",
    "merge_",
    "delete",
    "push",
    "fork",
)
_READ_TOOL_PREFIXES: tuple[str, ...] = ("get_", "list_", "search_")

_GITHUB_TOOL_PREFIX = "mcp__github__"


def _tool_basename(tool_mention: str) -> str:
    """Compute the tool "basename" used by the ops tool-shape guard.

    Lowercases *tool_mention*; when it contains the
    ``mcp__github__`` prefix, returns the substring after that
    prefix, otherwise returns the (lowercased) token unchanged.

    Args:
        tool_mention: A single raw or already-lowercased tool-mention
            token.

    Returns:
        The basename used for the WRITE/READ prefix checks.
    """
    lowered = tool_mention.lower()
    if _GITHUB_TOOL_PREFIX in lowered:
        return lowered.split(_GITHUB_TOOL_PREFIX, 1)[1]
    return lowered


def _github_tool_signal(features: Features) -> str | None:
    """Classify the ops GitHub tool-shape signal (#448).

    Applies precedence **write > read > none** over
    ``features.tool_mentions`` basenames (see :func:`_tool_basename`),
    plus two read-only fallbacks (a bare ``"gh"`` tool mention and an
    ``"ops"`` agent mention) that a basename check alone would miss.

    Args:
        features: Extracted feature set for the current context.
            ``tool_mentions`` and ``agent_mentions`` are lowercased
            ``frozenset[str]`` values.

    Returns:
        ``"write"`` when any tool_mention basename starts with a
        write-shaped prefix (``create``, ``add_``, ``update``,
        ``merge_``, ``delete``, ``push``, ``fork``); ``"read"`` when
        no write signal fired but a read-shaped signal is present
        (a ``get_``/``list_``/``search_`` basename, a bare ``"gh"``
        tool mention, a raw ``mcp__github__``-prefixed tool mention,
        or an ``"ops"`` agent mention); ``None`` otherwise.
    """
    basenames = [_tool_basename(tm) for tm in features.tool_mentions]

    if any(b.startswith(_WRITE_TOOL_PREFIXES) for b in basenames):
        return "write"

    if (
        any(b.startswith(_READ_TOOL_PREFIXES) for b in basenames)
        or "gh" in features.tool_mentions
        or any(
            tm.startswith(_GITHUB_TOOL_PREFIX)
            for tm in features.tool_mentions
        )
        or "ops" in features.agent_mentions
    ):
        return "read"

    return None


# ---------------------------------------------------------------------------
# test-authoring discriminator (#452, mirrors #448's ops tool-shape guard)
# ---------------------------------------------------------------------------

_TEST_AUTHORING_QUALIFIER_STEMS: frozenset[str] = frozenset(
    {"first", "red", "pytest", "vitest", "write"}
)


def _test_authoring_signal(features: Features) -> bool:
    """Detect a test-authoring (test-first / TDD-red) signal (#452).

    ``Features`` exposes no raw task-description string -- only the
    Porter2-stemmed ``keywords`` and unstemmed ``raw_keywords``
    frozensets -- so this is a token-presence heuristic, not a phrase
    match. Fires when the stemmed token set contains the hyphenated
    ``"test-first"`` token (it survives stemming as a single token), OR
    contains both ``"test"`` (the stem of "test"/"tests") and at least
    one test-authoring qualifier stem: ``"first"``, ``"red"``,
    ``"pytest"``, ``"vitest"``, or ``"write"``. Requiring a qualifier
    alongside the bare ``"test"`` stem avoids firing on plain implementation
    tasks that mention tests only incidentally -- and deliberately excludes
    ``"fail"``/"failing", since a build task can legitimately mention a
    failing test without being a test-authoring task.

    Args:
        features: Extracted feature set for the current context.

    Returns:
        ``True`` when a test-authoring signal is present in
        ``features.keywords``, else ``False``.
    """
    tokens = features.keywords
    if "test-first" in tokens:
        return True
    return "test" in tokens and bool(tokens & _TEST_AUTHORING_QUALIFIER_STEMS)


# ---------------------------------------------------------------------------
# compose_route
# ---------------------------------------------------------------------------


def compose_route(
    labels: Labels,
    scored_agents: list[ScoredEntry],
    scored_skills: list[ScoredEntry],
    features: Features,
    catalog: list[CatalogEntry],
    catalog_agent_names: frozenset[str],
    diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compose a routing decision from two-axis labels + lexical scores.

    Ports the per-entry algorithm from
    ``scripts/corpus/eval/_systems.run_supplied_compose`` (lines
    1216–1311) into a single-context function, adding the §D.1
    confidence-high gate and §B.1 plausibility veto.

    Algorithm (three-branch surface)
    ---------------------------------
    **Branch 1** — broad-diagnose → investigator (#396/#411):
        Fires when ``posture="diagnose"``, ``area_span >= 2``,
        ``confidence_is_high``, and investigator is in
        *catalog_agent_names*.  The §B.1 plausibility veto is NOT
        applied — investigator is a structural route (area_span-driven),
        rarely lexically top-k even for correct broad-diagnose inputs.
        Veto-ing it would suppress correct routes and diverge from the
        validated oracle.  (Revised 2026-06-20.)

    **Branch 2** — sentinel → self_handle (#397):
        Fires when ``cell_map_lookup`` returns ``SELF_HANDLE_SENTINEL``
        (currently ``project_meta × build``).  NOT gated by confidence
        or the veto — this is an abstention to the router, not a
        sub-agent delegation.

    **Branch 3** — generic preferred → delegate@0.9 (§B.3):
        Fires when a preferred agent is returned by ``cell_map_lookup``,
        that agent is in ``genuine_gated_names`` (the D-KC-GUARD1
        intersection that prevents empty-gate false positives from
        routing out-of-domain agents), is in *catalog_agent_names*,
        ``confidence_is_high`` passes, and ``_is_lexically_plausible``
        passes.

        **ops GitHub tool-shape guard (#448, supersedes #445):** when
        the preferred agent is ``"ops"``, delegation is additionally
        gated by :func:`_github_tool_signal`, a tool-shape
        discriminator applied with precedence **write > read > none**:

        1. **write** — any ``features.tool_mentions`` basename starts
           with a write-shaped prefix (``create``, ``add_``,
           ``update``, ``merge_``, ``delete``, ``push``, ``fork``) →
           veto to ``self_handle`` (``agent=None``),
           ``posture_veto_reason="ops_write_tool"``.
        2. **read** — else, any basename starts with ``get_``,
           ``list_``, or ``search_``; or ``"gh"`` is a raw tool
           mention; or a raw tool mention keeps the
           ``mcp__github__`` prefix; or ``"ops"`` is an agent
           mention → proceed with the ordinary Branch-3 gates below.
        3. **none** — else, veto to ``self_handle``
           (``posture_veto_reason="ops_no_github_signal"``,
           unchanged from #445).

        ``ops`` is read-only GitHub-only, but codebase-read tasks
        resolve to the same ``(any, operate)`` cell; the write/none
        vetoes mirror the Branch-2 sentinel abstention shape.  Scoped
        strictly to ``preferred == "ops"`` — no other Branch-3 route
        is affected (byte-for-byte unchanged when ``preferred !=
        "ops"``).

        **test-authoring discriminator (#452):** when the preferred
        agent is ``"code-writer"`` AND :func:`_test_authoring_signal`
        detects a test-first / TDD-red signal in ``features.keywords``,
        the route is redirected to ``"test-implementer"`` instead of
        ``"code-writer"``: ``decision="delegate"``, ``confidence=0.9``,
        ``diagnostics["branch"] == "branch3_testfirst"``.  Gated by the
        same predicates the generic route below uses — applied to
        ``"test-implementer"`` rather than the cell-preferred
        ``"code-writer"``, since it is ``"test-implementer"`` that
        ultimately delegates: it must be in ``genuine_gated_names`` and
        ``catalog_agent_names``, ``confidence_is_high`` must pass, and
        it must clear the §B.1 plausibility veto. Scoped strictly to
        ``preferred == "code-writer"`` with the signal present — no
        signal, or any other preferred agent, falls through unchanged
        to the generic route (``branch3_generic``).

    **Fallback** — ``decide()`` on the gated list: fires when no branch
        routes.

    Args:
        labels: Two-axis routing labels (domain, posture, confidence,
            area_span).
        scored_agents: Lexically scored agents (score-sorted descending).
        scored_skills: Lexically scored skills (score-sorted descending).
        features: Extracted feature set for the current context.
        catalog: Full catalog entry list (passed to ``decide()``).
        catalog_agent_names: Frozenset of routable agent names from the
            catalog.
        diagnostics: Optional out-param dict (M15-5, §F.1).  When not
            ``None``, populated in-place with per-step routing state:
            ``gated_agent_names``, ``posture_preferred``,
            ``posture_routed``, ``branch``, ``lexical_agreement``,
            ``posture_veto_reason``.  Does not affect return value or
            decision logic.  Default ``None`` leaves existing callers
            and frozen tests unaffected.

    Returns:
        Decision dict with at minimum the keys ``decision`` (str),
        ``agent`` (str | None), ``confidence`` (float), and
        ``disposition_source`` (``"posture_routed"`` | forwarded from
        ``decide()``).
    """
    # --- Pre-compute gated list and preferred agent ---
    gated = gate_agents(scored_agents, labels.domain)

    # is_any / None → look up under "any"; concrete domain → look up verbatim.
    domain_for_lookup: str = (
        "any"
        if labels.domain in (None, "is_any")
        else labels.domain
    )

    preferred: str | None = (
        cell_map_lookup(domain_for_lookup, labels.posture)
        if labels.posture
        else None
    )

    # --- Routing state ---
    posture_routed: bool = False
    agent_out: str | None = None
    decision_out: str = "advisory"
    confidence_out: float = 0.5

    # §F.1 diagnostic locals — populated only when diagnostics is not None.
    _branch: str = "fallback"
    _lexical_agreement: bool | None = None
    _posture_veto_reason: str | None = None

    if labels.posture:
        # ------------------------------------------------------------------
        # BRANCH 1: broad-diagnose → investigator (#396/#411)
        # Structural route — §B.1 plausibility veto does NOT apply here.
        # Investigator is rarely lexically top-k even for correct
        # broad-diagnose inputs, so veto-ing it would suppress correct
        # routes and diverge from the validated oracle.  Gate ONLY on
        # the §D.1 confidence fail-safe and the routability guard.
        # (Revised 2026-06-20 per contract §D-KC-GUARD1 / Branch-gating.)
        # ------------------------------------------------------------------
        if labels.posture == "diagnose" and labels.area_span >= 2:
            if (
                "investigator" in catalog_agent_names
                and confidence_is_high(labels)            # §D.1 live gate
            ):
                agent_out = "investigator"
                decision_out = "delegate"
                confidence_out = 0.9
                posture_routed = True
                _branch = "branch1_investigator"
            # else: investigator absent or confidence not high → decide()
            elif diagnostics is not None:
                # §F.1 Branch-1 veto diagnostics (Codex P2 / #429):
                # confidence_not_high takes precedence when both gates fail.
                if not confidence_is_high(labels):
                    _posture_veto_reason = "confidence_not_high"
                else:
                    _posture_veto_reason = "investigator_not_in_catalog"

        # ------------------------------------------------------------------
        # BRANCH 2: sentinel → self_handle (#397)
        # NOT gated by confidence or plausibility veto — this is an
        # abstention to the router, not a sub-agent delegation.
        # ------------------------------------------------------------------
        elif preferred == SELF_HANDLE_SENTINEL:
            decision_out = "self_handle"
            agent_out = None
            confidence_out = 0.9
            posture_routed = True
            _branch = "branch2_sentinel"

        # ------------------------------------------------------------------
        # BRANCH 3: generic preferred → delegate@0.9 (§B.3)
        # ------------------------------------------------------------------
        else:
            gated_names: frozenset[str] = frozenset(
                se.entry.name for se in gated
            )
            # D-KC-GUARD1 (#366): prevent empty-gate fallback from
            # routing an out-of-domain preferred agent.  For concretely-
            # gated domains (non-None DOMAIN_AGENT_MAP entry), only agents
            # that are BOTH scored AND in the domain's allowed set are
            # considered genuine survivors.
            domain_allowed = DOMAIN_AGENT_MAP.get(labels.domain)
            genuine_gated_names: frozenset[str] = (
                gated_names & domain_allowed
                if domain_allowed is not None
                else gated_names
            )
            # ops GitHub tool-shape guard (#448, supersedes #445): ops
            # is read-only GitHub-only, but codebase-read tasks
            # resolve to the same (any, operate) cell.  Apply the
            # write > read > none tool-shape discriminator before
            # letting ops delegate.  Scoped strictly to preferred ==
            # "ops" — no other Branch-3 route is affected.
            ops_tool_signal: str | None = (
                _github_tool_signal(features) if preferred == "ops" else None
            )
            if preferred == "ops" and ops_tool_signal == "write":
                decision_out = "self_handle"
                agent_out = None
                confidence_out = 0.9
                posture_routed = True
                _branch = "branch3_ops_veto"
                _posture_veto_reason = "ops_write_tool"
            elif preferred == "ops" and ops_tool_signal is None:
                decision_out = "self_handle"
                agent_out = None
                confidence_out = 0.9
                posture_routed = True
                _branch = "branch3_ops_veto"
                _posture_veto_reason = "ops_no_github_signal"
            elif (
                preferred == "code-writer"
                and _test_authoring_signal(features)
                and "test-implementer" in genuine_gated_names
                and "test-implementer" in catalog_agent_names
                and confidence_is_high(labels)           # §D.1 live gate
                and _is_lexically_plausible(               # §B.1 veto,
                    "test-implementer", gated               # on the redirect
                )                                          # target
            ):
                agent_out = "test-implementer"
                decision_out = "delegate"
                confidence_out = 0.9
                posture_routed = True
                _branch = "branch3_testfirst"
                _lexical_agreement = True   # §B.1 passed — record it
            elif (
                preferred
                and preferred in genuine_gated_names
                and preferred in catalog_agent_names
                and confidence_is_high(labels)           # §D.1 live gate
                and _is_lexically_plausible(preferred, gated)  # §B.1 veto
            ):
                agent_out = preferred
                decision_out = "delegate"
                confidence_out = 0.9
                posture_routed = True
                _branch = "branch3_generic"
                _lexical_agreement = True   # §B.1 passed — record it
            elif diagnostics is not None and preferred:
                # Branch-3 did not fire — diagnose the blocking condition.
                # Evaluate each gate in order to surface the first veto.
                _branch = "fallback"
                if preferred not in genuine_gated_names:
                    _posture_veto_reason = "not_in_genuine_gated"
                elif preferred not in catalog_agent_names:
                    _posture_veto_reason = "not_in_catalog"
                elif not confidence_is_high(labels):
                    _posture_veto_reason = "confidence_not_high"
                else:
                    # Must be _is_lexically_plausible that failed.
                    _posture_veto_reason = "not_lexically_plausible"
                # Evaluate lexical_agreement explicitly for the diagnostic.
                _lexical_agreement = _is_lexically_plausible(
                    preferred, gated
                )

    # --- Fallback: decide() on the gated list ---
    if not posture_routed:
        d = dict(decide(gated, scored_skills, features, catalog))
        d.setdefault("disposition_source", "scored")
        # Populate diagnostics before returning (fallback path).
        if diagnostics is not None:
            diagnostics["gated_agent_names"] = sorted(
                se.entry.name for se in gated
            )
            diagnostics["posture_preferred"] = preferred
            diagnostics["posture_routed"] = False
            diagnostics["branch"] = _branch
            diagnostics["lexical_agreement"] = _lexical_agreement
            diagnostics["posture_veto_reason"] = _posture_veto_reason
        return d

    # Populate diagnostics for posture-routed paths.
    if diagnostics is not None:
        diagnostics["gated_agent_names"] = sorted(
            se.entry.name for se in gated
        )
        diagnostics["posture_preferred"] = preferred
        diagnostics["posture_routed"] = True
        diagnostics["branch"] = _branch
        diagnostics["lexical_agreement"] = _lexical_agreement
        diagnostics["posture_veto_reason"] = _posture_veto_reason

    return {
        "decision": decision_out,
        "agent": agent_out,
        "confidence": confidence_out,
        "disposition_source": "posture_routed",
    }
