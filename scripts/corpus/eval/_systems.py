"""Four system runners over corpus-format input (issue #340).

Systems
-------
1. Lexical baseline  — current matcher scoring, invoked offline.
2. Encoder-alone     — 8M pinned + margin gate (importorskip if missing).
3. Extractors-alone  — posture cells (E1-E12 + R1-R3).
4. Composed          — domain × posture cells (importorskip if missing).

Each runner accepts a list of ``CorpusEntry`` objects and a catalog path,
and returns a list of ``SystemResult`` objects in the same order.

v0 calibration decisions (flagged for #330 calibration run):
- Encoder domain-any detection: entropy threshold > 1.5 bits (per spike
  report §7; matches SPIKE_GOLD_FOR_EVAL verdicts).
- Extractor-posture → agent cell map: per §9.1 grid — each posture maps
  to a canonical agent; cells with domain split use the posture winner.
  When multiple postures fire, the first in priority order wins
  (priority: operate > diagnose > assess > verify > plan > research >
  idea-critique > build).
- Composed system: domain from encoder top-1 (or domain-any when
  entropy > 1.5); posture from extractors; cell lookup from §9.1 grid.
  When the cell is ambiguous (two agents share it), domain breaks the
  tie. When domain is "any", posture alone routes.
- Encoder margin gate: < 0.04 → domain-any (per §7 pinned report best
  threshold; consistent with spike gold set).
- Tier-C brake: when E12 fires and the winning posture is not diagnose,
  the result confidence is braked to advisory-band (0.5).
  Tier-A E8 (command_prefix) overrides all brakes: operate + E8 is
  always confident (per §12.1 P13 verdict).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from claude_wayfinder.match._catalog import load_catalog
from claude_wayfinder.match._decide import decide
from claude_wayfinder.match._match import build_features, score_entries
from claude_wayfinder.match_filters import is_agent_routable
from claude_wayfinder.posture import (
    ExtractorResult,
    PostureContext,
    extract_agent_mentions,
    extract_area_span,
    extract_artifact_absence,
    extract_cause_stated,
    extract_command_prefix,
    extract_frame_markers,
    extract_prose_failure_mention,
    extract_source_of_truth_pair,
    extract_spec_plan_path,
    extract_stacktrace_block,
    extract_test_failure_output,
    extract_vcs_artifact_ref,
)
from claude_wayfinder.posture._areas import load_area_map
from scripts.corpus.eval._reader import CorpusEntry

# ---------------------------------------------------------------------------
# §9.1 cell map: posture → preferred agent(s)
# Priority order for posture winner when multiple fire.
# When domain is available, domain breaks ties where noted.
# ---------------------------------------------------------------------------

# Priority order for postures (operate is strongest single signal per §10.2)
_POSTURE_PRIORITY: list[str] = [
    "operate",
    "diagnose",
    "assess",
    "verify",
    "plan",
    "research",
    "idea-critique",
    "build",
]

# §9.1 grid: (domain, posture) → agent.
# "any" domain rows apply when domain is domain-any or missing.
# Ties within a posture are resolved by domain.
_CELL_MAP: dict[tuple[str, str], str] = {
    # build row
    ("code", "build"): "code-writer",
    ("docs_prose", "build"): "doc-writer",
    ("any", "build"): "code-writer",  # default build = code-writer
    # diagnose row — domain splits debugger vs investigator
    # scope/span distinguishes; here span≥2 → investigator captured in extractor
    # The extractor result carries the span count in extras; we use posture name
    # "diagnose" with span info to route.
    ("code", "diagnose"): "debugger",
    ("infra_deploy", "diagnose"): "investigator",
    ("any", "diagnose"): "investigator",  # cross-layer default
    # assess row
    ("code", "assess"): "code-reviewer",
    ("project_meta", "assess"): "project-reviewer",
    ("any", "assess"): "code-reviewer",
    # critique row
    ("code", "critique"): "inquisitor",
    ("any", "critique"): "approach-critic",
    # idea-critique row
    ("any", "idea-critique"): "approach-critic",
    # verify row
    ("any", "verify"): "auditor",
    # plan row
    ("project_meta", "plan"): "project-planner",
    ("infra_deploy", "plan"): "devops",
    ("any", "plan"): "project-planner",
    # research row
    ("any", "research"): "researcher",
    # operate row
    ("any", "operate"): "ops",
}

# ---------------------------------------------------------------------------
# SystemResult
# ---------------------------------------------------------------------------


@dataclass
class SystemResult:
    """Routing result for one corpus entry from one system runner.

    Attributes:
        corpus_id: Matches the input CorpusEntry.corpus_id.
        decision: Routing decision string (e.g. ``"delegate"``).
        agent: Target agent name when decision implies one, else ``None``.
        confidence: Confidence score in [0.0, 1.0].
        extras: Runner-specific metadata dict.  Keys vary by system:
            - lexical: ``{"scores": {agent: score, ...}}``
            - extractors: ``{"postures": [...], "tier_c_fired": bool,
              "area_span": int}``
            - encoder: ``{"domain": str, "entropy": float, "is_any": bool}``
            - composed: all of the above combined.
    """

    corpus_id: int
    decision: str
    agent: str | None
    confidence: float
    extras: dict[str, Any]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _entry_to_context(entry: CorpusEntry) -> dict[str, Any]:
    """Convert a CorpusEntry to a dispatch-context dict for build_features.

    Args:
        entry: A CorpusEntry from the corpus reader.

    Returns:
        Context dict compatible with ``build_features()``.
    """
    return {
        "task_description": entry.task_description,
        "file_paths": list(entry.file_paths),
        "agent_mentions": list(entry.agent_mentions),
        "tool_mentions": list(entry.tool_mentions),
        "command_prefix": entry.command_prefix,
    }


def _decide_to_system_result(
    corpus_id: int,
    decision_dict: dict[str, Any],
    extras: dict[str, Any] | None = None,
) -> SystemResult:
    """Convert a decide() output dict into a SystemResult.

    Args:
        corpus_id: The corpus entry ID.
        decision_dict: Output of ``decide()``.
        extras: Additional metadata for the result.

    Returns:
        SystemResult with fields populated from the decision dict.
    """
    return SystemResult(
        corpus_id=corpus_id,
        decision=str(decision_dict.get("decision", "")),
        agent=decision_dict.get("agent"),
        confidence=float(decision_dict.get("confidence", 0.0)),
        extras=extras or {},
    )


def _run_all_extractors(
    ctx: PostureContext,
    area_map: dict[str, list[str]] | None = None,
) -> dict[str, ExtractorResult]:  # noqa: C901
    """Run all E1-E12 extractors on a PostureContext and return results dict.

    Args:
        ctx: Dispatch context for extraction.
        area_map: Optional area-glob map for E7. Defaults to coarse globs.

    Returns:
        Dict mapping extractor name to ExtractorResult.
    """
    e1 = extract_stacktrace_block(ctx)
    e2 = extract_test_failure_output(ctx)
    e3 = extract_vcs_artifact_ref(ctx)
    e4 = extract_spec_plan_path(ctx)
    e12 = extract_prose_failure_mention(ctx)

    # E5 has no e12_result param — it is standalone per its own signature
    e5 = extract_source_of_truth_pair(ctx)

    # E6: conditional on E1/E2 firing
    host_condition = bool(e1.fired) or bool(e2.fired)
    e6 = extract_cause_stated(ctx, host_condition=host_condition)

    # E7: area span — pass area_map or use None (extractor handles default)
    # E7 requires a non-None area_map; load coarse default when not provided
    if area_map is None:
        area_map = load_area_map(Path("."))
    e7 = extract_area_span(ctx, area_map=area_map)

    # E8: command prefix
    e8 = extract_command_prefix(ctx)

    # E9: artifact absence — suppressed by E12 (R2) via prose_failure_result
    artifact_results = [e1, e2, e3, e4, e5, e8]
    e9 = extract_artifact_absence(
        ctx,
        artifact_extractor_results=artifact_results,
        prose_failure_result=e12,
    )

    # E10: frame markers — only inside E9 gate
    e9_gate_open = bool(e9.fired)
    e10 = extract_frame_markers(ctx, e9_gate_open=e9_gate_open)

    # E11: agent mentions
    e11 = extract_agent_mentions(ctx)

    return {
        "e1": e1,
        "e2": e2,
        "e3": e3,
        "e4": e4,
        "e5": e5,
        "e6": e6,
        "e7": e7,
        "e8": e8,
        "e9": e9,
        "e10": e10,
        "e11": e11,
        "e12": e12,
    }


def _postures_from_extractor_results(
    results: dict[str, ExtractorResult],
) -> list[str]:
    """Collect all fired posture evidence values from extractor results.

    Applies §12.3 R1-R3 refinements:
    - E6 FLIPS diagnose→build when it fires: "diagnose" is removed from
      the posture set and "build" is added.  The "modifier" weight class
      on E6's evidence signals this flip role; a modifier must not be
      treated as an additive posture alongside the source it modifies.
    - E7 host-gate (§10.2): E7 is a modifier INSIDE an active diagnose
      context.  Its posture evidence only counts when E1 or E2 also fired
      (the diagnose host condition).  The span count is recorded in
      extras["area_span"] regardless; only the posture contribution is
      gated.  Without this gate, plain file-path-bearing build/verify
      prompts misroute to investigator/debugger because diagnose outranks
      build in the priority order.
    - E12 brakes non-diagnose confident postures (tracked separately).
    - Priority ordering is applied by the caller.

    Args:
        results: Dict of extractor name → ExtractorResult.

    Returns:
        List of unique posture strings from all fired extractors.
        Order reflects the evidence (not priority — priority is applied
        by the routing function).
    """
    postures: list[str] = []
    seen: set[str] = set()
    # E7 host condition: E1 (stacktrace) or E2 (test failure) must have fired
    # for E7's posture evidence to count (§10.2 — E7 refines diagnose, does
    # not activate it).
    e1_fired = bool(results.get("e1") and results["e1"].fired)
    e2_fired = bool(results.get("e2") and results["e2"].fired)
    e7_host_condition = e1_fired or e2_fired
    for name in ["e1", "e2", "e3", "e4", "e5", "e6", "e7", "e8", "e9",
                 "e10", "e11"]:
        result = results.get(name)
        if result is None or not result.fired:
            continue
        # Gate E7 posture evidence on host condition
        if name == "e7" and not e7_host_condition:
            continue
        for posture, weight in result.evidence:
            if weight == "modifier":
                # §12.3 R1 — E6 flip: modifier evidence removes the source
                # posture it modifies ("diagnose") and replaces it with the
                # target posture ("build").  Do not treat modifier as additive.
                if "diagnose" in seen:
                    postures.remove("diagnose")
                    seen.discard("diagnose")
                if posture not in seen:
                    postures.append(posture)
                    seen.add(posture)
            else:
                if posture not in seen:
                    postures.append(posture)
                    seen.add(posture)
    return postures


def _area_span_count(results: dict[str, ExtractorResult]) -> int:
    """Extract the area span count from E7 result.

    Args:
        results: Dict of extractor name → ExtractorResult.

    Returns:
        Integer area span count from E7, or 0 if E7 did not fire.
    """
    e7 = results.get("e7")
    if e7 is None or not e7.fired:
        return 0
    return int(e7.fired)


def _e11_agents_from_results(
    results: dict[str, ExtractorResult],
) -> list[str]:
    """Extract explicit agent names from E11 evidence.

    E11 emits evidence of the form ``("as-named:<agent>", "strong")``
    for each agent mentioned.  This helper decodes those entries into
    bare agent name strings for use in pass-through routing.

    Args:
        results: Dict of extractor name → ExtractorResult.

    Returns:
        Sorted list of agent names mentioned via E11, or ``[]`` when
        E11 did not fire.
    """
    e11 = results.get("e11")
    if e11 is None or not e11.fired:
        return []
    agents: list[str] = []
    for posture_key, _ in e11.evidence:
        if posture_key.startswith("as-named:"):
            agent_name = posture_key[len("as-named:"):]
            agents.append(agent_name)
    return agents


def _candidate_agents_from_postures(
    postures: list[str],
    area_span: int,
    domain: str,
) -> list[str]:
    """Build a candidate-agent list from all activated posture evidence.

    Used to populate ``extras["alternatives"]`` for braked outcomes
    (metric 5).  Returns one agent per activated posture in priority
    order, de-duplicated.

    Args:
        postures: Fired posture strings (from ``_postures_from_extractor_results``).
        area_span: E7 area span count.
        domain: Coarse domain string.

    Returns:
        List of candidate agent names in priority order, without duplicates.
    """
    candidates: list[str] = []
    seen: set[str] = set()
    for p in _POSTURE_PRIORITY:
        if p not in postures:
            continue
        # Apply diagnose + span rule
        if p == "diagnose" and area_span >= 2:
            agent = "investigator"
        else:
            agent = _CELL_MAP.get(
                (domain, p),
                _CELL_MAP.get(("any", p)),
            )
        if agent and agent not in seen:
            candidates.append(agent)
            seen.add(agent)
    return candidates


def _tier_c_fired(results: dict[str, ExtractorResult]) -> bool:
    """Return True if any Tier-C extractor fired.

    Tier-C extractors: E10, E11 (agent_mentions is A but tracked as
    potential C influence in composed routing), E12.
    Per spec §10.3 guardrail 4: track e10 and e12 as Tier-C.

    Args:
        results: Dict of extractor name → ExtractorResult.

    Returns:
        True when E10 or E12 fired.
    """
    e10 = results.get("e10")
    e12 = results.get("e12")
    return bool((e10 and e10.fired) or (e12 and e12.fired))


def _route_from_postures(
    postures: list[str],
    area_span: int,
    e8_fired: bool,
    e12_fired: bool,
    domain: str = "any",
) -> tuple[str | None, float]:
    """Map posture evidence to an agent + confidence.

    Applies the §9.1 grid with §12.3 R1/R2 braking.

    v0 calibration (flagged for #330):
    - Priority order: operate > diagnose > assess > verify > plan >
      research > idea-critique > build.
    - Diagnose + span≥2 → investigator (overrides code domain).
    - E12 brake: when E12 fired and winning posture is NOT diagnose and
      NOT operate, confidence is braked to 0.5 (advisory).
    - E8 (operate/command-prefix) is Tier-A dominant: ignores E12 brake.
    - No postures and not default-build → abstain (advisory, agent None).
    - Default-build: when no posture fires but domain signal exists,
      assume build (§10.4). Confidence is advisory (0.5).

    Args:
        postures: List of posture strings from extractor evidence.
        area_span: E7 area span count (int).
        e8_fired: True when E8 (command_prefix) extractor fired.
        e12_fired: True when E12 (prose_failure_mention) fired.
        domain: Coarse domain string (5-way, or ``"any"``).

    Returns:
        Tuple of (agent_name_or_None, confidence_float).
    """
    # Select winning posture by priority
    winning_posture: str | None = None
    for p in _POSTURE_PRIORITY:
        if p in postures:
            winning_posture = p
            break

    if winning_posture is None:
        # Default-build (§10.4): no posture extractor fired but domain signal
        # exists → treat as build posture and route via the cell map so that
        # composed delegation and the false-default-build metric can count it.
        # Confidence remains advisory (0.5) per §10.4 (contributes posture,
        # not confidence).
        agent = _CELL_MAP.get(
            (domain, "build"),
            _CELL_MAP.get(("any", "build")),
        )
        return agent, 0.5

    # Diagnose + span≥2 → investigator regardless of domain
    if winning_posture == "diagnose" and area_span >= 2:
        agent = "investigator"
    else:
        # Look up cell (domain-specific first, then any)
        agent = _CELL_MAP.get(
            (domain, winning_posture),
            _CELL_MAP.get(("any", winning_posture)),
        )

    if agent is None:
        return None, 0.5

    # Confidence: E8 (operate) is always confident (Tier-A dominant)
    if e8_fired and winning_posture == "operate":
        return agent, 0.9

    # E12 brake: non-diagnose confident → advisory
    if e12_fired and winning_posture not in ("diagnose", "operate"):
        return agent, 0.5

    return agent, 0.9


# ---------------------------------------------------------------------------
# System 1: Lexical baseline
# ---------------------------------------------------------------------------


def run_lexical(
    entries: list[CorpusEntry],
    catalog_path: Path,
) -> list[SystemResult]:
    """System 1: lexical baseline — current matcher scoring, offline.

    Invokes ``build_features`` + ``score_entries`` + ``decide`` exactly
    as the matcher CLI does, but from a fixed catalog path (not live state).

    v0 calibration: none; uses existing calibrated thresholds verbatim.

    Args:
        entries: Corpus entries to evaluate.
        catalog_path: Path to the dispatch-catalog JSON file.

    Returns:
        List of SystemResult, one per entry, in input order.
    """
    catalog = load_catalog(Path(catalog_path))
    results: list[SystemResult] = []
    for entry in entries:
        ctx = _entry_to_context(entry)
        features = build_features(ctx)
        scored_agents, scored_skills = score_entries(catalog, features)

        # Collect top scores for extras
        top_scores = {
            se.entry.name: round(se.score, 4)
            for se in scored_agents[:5]
        }

        decision_dict = decide(scored_agents, scored_skills, features, catalog)
        extras = {"scores": top_scores}
        results.append(
            _decide_to_system_result(entry.corpus_id, decision_dict, extras)
        )
    return results


# ---------------------------------------------------------------------------
# System 3: Extractors-alone (posture cells)
# ---------------------------------------------------------------------------


def run_extractors(
    entries: list[CorpusEntry],
    catalog_path: Path,
) -> list[SystemResult]:
    """System 3: extractors-alone — posture cells E1-E12 + R1-R3.

    Runs all posture extractors and maps the winning posture cell to an
    agent using the §9.1 grid.  Domain axis is ``"any"`` (no encoder).

    v0 calibration decisions (flagged for #330):
    - Domain defaults to ``"any"`` (encoder not used in this system).
    - Diagnose + span≥2 always routes to investigator.
    - Priority order: operate > diagnose > assess > verify > plan >
      research > idea-critique > build.
    - E12 brake: non-diagnose, non-operate confident → advisory (0.5).
    - E8 (operate) is Tier-A dominant; overrides E12 brake.

    Args:
        entries: Corpus entries to evaluate.
        catalog_path: Path to the dispatch-catalog JSON file.

    Returns:
        List of SystemResult, one per entry, in input order.
    """
    # Catalog is loaded to validate that the system can resolve agents;
    # agent names from the cell map are matched against catalog names.
    catalog = load_catalog(Path(catalog_path))
    catalog_agent_names = {
        e.name for e in catalog if e.kind == "agent" and is_agent_routable(
            name=e.name, kind=e.kind, source=e.source, routable=e.routable
        )
    }

    results: list[SystemResult] = []
    for entry in entries:
        ctx = PostureContext(
            task_description=entry.task_description,
            file_paths=tuple(entry.file_paths),
            agent_mentions=frozenset(entry.agent_mentions),
            tool_mentions=frozenset(entry.tool_mentions),
            command_prefix=entry.command_prefix,
        )

        extractor_results = _run_all_extractors(ctx)
        postures = _postures_from_extractor_results(extractor_results)
        span = _area_span_count(extractor_results)
        e8_fired = bool(extractor_results["e8"].fired)
        e12_fired = bool(extractor_results["e12"].fired)
        tier_c = _tier_c_fired(extractor_results)

        # §10.2 E11 near-dispositive pass-through: explicit agent mention
        # overrides posture-priority selection.  Route directly to the named
        # agent at confident band; subject to catalog routability check.
        e11_agents = _e11_agents_from_results(extractor_results)

        braked = False
        if e11_agents:
            # Use the first named agent (sorted in _e11_agents_from_results)
            agent = e11_agents[0]
            confidence = 0.9
        else:
            agent, confidence = _route_from_postures(
                postures=postures,
                area_span=span,
                e8_fired=e8_fired,
                e12_fired=e12_fired,
                domain="any",
            )
            # Record brake: E12 fires + a posture extractor fired + the
            # winning posture is not diagnose/operate → E12 braked the
            # confident outcome down to advisory (0.5).  Excludes the
            # default-build case (postures empty, winning_posture=None)
            # which is an abstain, not a brake.
            if e12_fired and confidence == 0.5 and agent is not None:
                winning_posture_set = set(postures) & set(_POSTURE_PRIORITY)
                winning_posture = next(
                    (p for p in _POSTURE_PRIORITY if p in winning_posture_set),
                    None,
                )
                if (
                    winning_posture is not None
                    and winning_posture not in ("diagnose", "operate")
                ):
                    braked = True

        # Validate agent against catalog (may be absent from small fixture)
        if agent and agent not in catalog_agent_names:
            # Agent not in catalog — treat as advisory
            decision = "advisory"
        elif agent and confidence >= 0.85:
            decision = "delegate"
        elif agent and confidence >= 0.5:
            decision = "advisory"
        else:
            decision = "advisory"

        extras: dict[str, Any] = {
            "postures": postures,
            "tier_c_fired": tier_c,
            "area_span": span,
        }
        if braked:
            extras["braked"] = True
            extras["alternatives"] = _candidate_agents_from_postures(
                postures=postures,
                area_span=span,
                domain="any",
            )
        results.append(
            SystemResult(
                corpus_id=entry.corpus_id,
                decision=decision,
                agent=agent,
                confidence=confidence,
                extras=extras,
            )
        )
    return results


# ---------------------------------------------------------------------------
# System 2: Encoder-alone (importorskip)
# ---------------------------------------------------------------------------


def run_encoder(
    entries: list[CorpusEntry],
    catalog_path: Path,
) -> list[SystemResult]:
    """System 2: encoder-alone — 8M pinned domain classifier + margin gate.

    Uses the DomainClassifier from spikes.domain_encoder to produce a 5-way
    domain distribution per prompt.  Maps top-1 domain to an agent via the
    §9.1 grid, using posture="build" as the unmarked default (§10.4).

    The margin gate (< 0.04) marks a prompt as domain-any and routes to a
    posture-neutral advisory when domain evidence is too diffuse.

    Requires model2vec (``pip install '.[spike]'``).  Raises ImportError
    with a descriptive message if missing; callers use ``pytest.importorskip``
    or check availability before calling.

    v0 calibration decisions (flagged for #330):
    - Entropy threshold for domain-any: > 1.5 bits (per spike §7).
    - Margin gate for domain-any: < 0.04 (per spike §7 best threshold).
    - Encoder alone uses posture="build" default (no extractor context).
    - When domain is "any", decision is always advisory (encoder cannot
      route without posture).

    Args:
        entries: Corpus entries to evaluate.
        catalog_path: Path to the dispatch-catalog JSON file.

    Returns:
        List of SystemResult, one per entry, in input order.

    Raises:
        ImportError: When model2vec is not installed.
    """
    try:
        from spikes.domain_encoder._classifier import DomainClassifier
    except ImportError as exc:
        raise ImportError(
            "run_encoder requires model2vec. Install with: "
            "pip install '.[spike]'"
        ) from exc

    clf = DomainClassifier.from_pretrained()
    catalog = load_catalog(Path(catalog_path))
    catalog_agent_names = {
        e.name for e in catalog if e.kind == "agent" and is_agent_routable(
            name=e.name, kind=e.kind, source=e.source, routable=e.routable
        )
    }

    _ENTROPY_ANY_THRESHOLD = 1.5
    _MARGIN_ANY_THRESHOLD = 0.04

    results: list[SystemResult] = []
    for entry in entries:
        domain_result = clf.classify(entry.task_description)

        sorted_probs = sorted(domain_result.distribution.values(), reverse=True)
        top1 = sorted_probs[0]
        top2 = sorted_probs[1] if len(sorted_probs) > 1 else 0.0
        margin = top1 - top2
        entropy = domain_result.entropy

        # Domain-any detection: high entropy OR low margin
        is_any = entropy > _ENTROPY_ANY_THRESHOLD or margin < _MARGIN_ANY_THRESHOLD
        domain = "any" if is_any else domain_result.top_label

        extras = {
            "domain": domain_result.top_label,
            "entropy": round(entropy, 4),
            "margin": round(margin, 4),
            "is_any": is_any,
        }

        if is_any:
            # Cannot route without posture signal; advisory
            results.append(SystemResult(
                corpus_id=entry.corpus_id,
                decision="advisory",
                agent=None,
                confidence=0.5,
                extras=extras,
            ))
            continue

        # Route via domain + build default (posture not computed here)
        agent = _CELL_MAP.get(
            (domain, "build"),
            _CELL_MAP.get(("any", "build")),
        )

        if agent and agent in catalog_agent_names:
            decision = "delegate"
            confidence = round(float(top1), 4)
        else:
            decision = "advisory"
            agent = None
            confidence = round(float(top1), 4)

        results.append(SystemResult(
            corpus_id=entry.corpus_id,
            decision=decision,
            agent=agent,
            confidence=confidence,
            extras=extras,
        ))
    return results


# ---------------------------------------------------------------------------
# System 4: Composed (domain × posture)
# ---------------------------------------------------------------------------


def run_composed(
    entries: list[CorpusEntry],
    catalog_path: Path,
) -> list[SystemResult]:
    """System 4: composed domain × posture cells.

    Combines encoder domain with extractor posture per §9.1 grid.
    Honors R1 (Tier-C select/brake only) and §10.4 (build default).

    v0 calibration decisions (flagged for #330):
    - Domain from encoder (entropy + margin gate as in system 2).
    - Posture from extractors (E1-E12 + R1-R3 as in system 3).
    - Cell lookup: exact (domain, posture) first, then ("any", posture).
    - Diagnose + span≥2 → investigator regardless of domain.
    - E12 brake applies to composed result as well (non-diagnose,
      non-operate confident → advisory).
    - E8 (operate) Tier-A dominant: overrides E12 brake.
    - When domain is "any": posture alone routes (same as system 3).

    Args:
        entries: Corpus entries to evaluate.
        catalog_path: Path to the dispatch-catalog JSON file.

    Returns:
        List of SystemResult, one per entry, in input order.

    Raises:
        ImportError: When model2vec is not installed.
    """
    try:
        from spikes.domain_encoder._classifier import DomainClassifier
    except ImportError as exc:
        raise ImportError(
            "run_composed requires model2vec. Install with: "
            "pip install '.[spike]'"
        ) from exc

    clf = DomainClassifier.from_pretrained()
    catalog = load_catalog(Path(catalog_path))
    catalog_agent_names = {
        e.name for e in catalog if e.kind == "agent" and is_agent_routable(
            name=e.name, kind=e.kind, source=e.source, routable=e.routable
        )
    }

    _ENTROPY_ANY_THRESHOLD = 1.5
    _MARGIN_ANY_THRESHOLD = 0.04

    results: list[SystemResult] = []
    for entry in entries:
        # Encoder domain
        domain_result = clf.classify(entry.task_description)
        sorted_probs = sorted(domain_result.distribution.values(), reverse=True)
        top1 = sorted_probs[0]
        top2 = sorted_probs[1] if len(sorted_probs) > 1 else 0.0
        margin = top1 - top2
        entropy = domain_result.entropy
        is_any = entropy > _ENTROPY_ANY_THRESHOLD or margin < _MARGIN_ANY_THRESHOLD
        domain = "any" if is_any else domain_result.top_label

        # Extractor posture
        ctx = PostureContext(
            task_description=entry.task_description,
            file_paths=tuple(entry.file_paths),
            agent_mentions=frozenset(entry.agent_mentions),
            tool_mentions=frozenset(entry.tool_mentions),
            command_prefix=entry.command_prefix,
        )
        extractor_results = _run_all_extractors(ctx)
        postures = _postures_from_extractor_results(extractor_results)
        span = _area_span_count(extractor_results)
        e8_fired = bool(extractor_results["e8"].fired)
        e12_fired = bool(extractor_results["e12"].fired)
        tier_c = _tier_c_fired(extractor_results)

        # §10.2 E11 near-dispositive pass-through: explicit agent mention
        # overrides posture-priority selection.
        e11_agents = _e11_agents_from_results(extractor_results)

        braked = False
        if e11_agents:
            agent = e11_agents[0]
            confidence = 0.9
        else:
            agent, confidence = _route_from_postures(
                postures=postures,
                area_span=span,
                e8_fired=e8_fired,
                e12_fired=e12_fired,
                domain=domain,
            )
            # Record brake: E12 fires + a posture extractor fired + the
            # winning posture is not diagnose/operate.
            if e12_fired and confidence == 0.5 and agent is not None:
                winning_posture_set = set(postures) & set(_POSTURE_PRIORITY)
                winning_posture = next(
                    (p for p in _POSTURE_PRIORITY if p in winning_posture_set),
                    None,
                )
                if (
                    winning_posture is not None
                    and winning_posture not in ("diagnose", "operate")
                ):
                    braked = True

        if agent and agent not in catalog_agent_names:
            decision = "advisory"
        elif agent and confidence >= 0.85:
            decision = "delegate"
        elif agent and confidence >= 0.5:
            decision = "advisory"
        else:
            decision = "advisory"

        extras: dict[str, Any] = {
            "domain": domain_result.top_label,
            "entropy": round(entropy, 4),
            "margin": round(margin, 4),
            "is_any": is_any,
            "postures": postures,
            "tier_c_fired": tier_c,
            "area_span": span,
        }
        if braked:
            extras["braked"] = True
            extras["alternatives"] = _candidate_agents_from_postures(
                postures=postures,
                area_span=span,
                domain=domain,
            )
        results.append(SystemResult(
            corpus_id=entry.corpus_id,
            decision=decision,
            agent=agent,
            confidence=confidence,
            extras=extras,
        ))
    return results
