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
- Encoder domain-any detection: margin-only gate at < 0.01 (issue #351
  data-driven sweep; F1=0.39 at margin<0.01, n=168, 16 gold-any).
  Entropy is computed but treated as DIAGNOSTIC only — the encoder's
  softmax entropy is ~2.31 on every prompt (near max-entropy for 5
  classes), so it carries no domain-any signal (8M spike §5.3).
- Extractor-posture → agent cell map: per §9.1 grid — each posture maps
  to a canonical agent; cells with domain split use the posture winner.
  When multiple postures fire, the first in priority order wins
  (priority: operate > diagnose > assess > verify > plan > research >
  idea-critique > build).
- Composed system: domain from encoder top-1 (or domain-any when
  margin < 0.01); posture from extractors; cell lookup from §9.1 grid.
  When the cell is ambiguous (two agents share it), domain breaks the
  tie. When domain is "any", posture alone routes.
- Encoder margin gate: < 0.01 → domain-any (issue #351 data-driven
  best-F1 threshold; supersedes the 0.04 value the 8M spike floated).
- Tier-C brake: when E12 fires and the winning posture is not diagnose,
  the result confidence is braked to advisory-band (0.5).
  Tier-A E8 (command_prefix) overrides all brakes: operate + E8 is
  always confident (per §12.1 P13 verdict).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import claude_wayfinder.match._decide as _decide_module
from claude_wayfinder.match._catalog import load_catalog
from claude_wayfinder.match._cells import (
    DOMAIN_AGENT_MAP,
    SELF_HANDLE_SENTINEL,
    cell_map_lookup,
    gate_agents,
)
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
from scripts.corpus.eval._reader import CorpusEntry, GoldLabel

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
# Canonical cell map lives in claude_wayfinder.match._cells._CELL_MAP.
# Lookup via cell_map_lookup(domain, posture) — imported above.

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
# Encoder gate constants
# ---------------------------------------------------------------------------

# Data-driven best-F1 on the organic gold ``is_any`` labels (issue #351
# sweep: F1=0.39 at margin<0.01, n=168, 16 gold-any).  Supersedes the 0.04
# value the 8M spike floated.  Strict less-than: exactly-at-threshold is NOT
# domain-any.
_MARGIN_ANY_THRESHOLD: float = 0.01

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_domain_any(margin: float) -> bool:
    """Domain-any when the top-1/top-2 margin is below threshold.

    Margin-only gate (issue #351).  Entropy does NOT gate — the encoder's
    softmax entropy is ~2.31 on every prompt (near maximum for a 5-class
    model), so entropy carries no domain-any signal (8M spike §5.3, rec
    #2).  This helper is intentionally pure and dependency-free so it can
    be unit-tested without model2vec.

    Args:
        margin: Difference between the top-1 and top-2 class probabilities
            from the domain classifier (``top1_prob - top2_prob``).  A
            small margin signals that the model is uncertain about the
            top-1 domain.

    Returns:
        ``True`` when ``margin < _MARGIN_ANY_THRESHOLD`` (i.e. the prompt
        is too ambiguous to route confidently to a single domain);
        ``False`` otherwise.
    """
    return margin < _MARGIN_ANY_THRESHOLD


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
    # E7 requires a non-None area_map; load coarse default when not provided.
    # host_condition is the same E1/E2 gate computed for E6 (both share the
    # diagnose host — §10/§11).
    if area_map is None:
        area_map = load_area_map(Path("."))
    e7 = extract_area_span(ctx, area_map=area_map, host_condition=host_condition)

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
            agent = cell_map_lookup(domain, p)
        # Sentinel is a routing instruction, not a real agent name; skip it.
        if agent and agent != SELF_HANDLE_SENTINEL and agent not in seen:
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
        agent = cell_map_lookup(domain, "build")
        return agent, 0.5

    # Diagnose + span≥2 → investigator regardless of domain
    if winning_posture == "diagnose" and area_span >= 2:
        agent = "investigator"
    else:
        # Look up cell (domain-specific first, then any)
        agent = cell_map_lookup(domain, winning_posture)

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
# Lever B: deterministic code-writer / doc-writer differentiator
# ---------------------------------------------------------------------------

# Keywords that strongly indicate prose/documentation tasks.
_DOC_KEYWORDS: frozenset[str] = frozenset({
    "readme", "changelog", "document", "documentation", "docs", "prose",
    "markdown", "md", "write up", "write-up", "adr", "spec", "design doc",
    "release note", "release notes", "api reference", "tutorial",
    "user guide", "docstring", "annotate", "annotation",
})

# Keywords that strongly indicate code tasks (code-writer over doc-writer).
_CODE_KEYWORDS: frozenset[str] = frozenset({
    "implement", "implementation", "function", "class", "module", "script",
    "import", "refactor", "debug", "test", "unittest", "pytest", "assert",
    "def ", "return ", "raise ", "exception", "algorithm", "stub", "mock",
    "compile", "lint", "type hint", "fix bug", "bug fix",
})

# File extensions that indicate prose tasks (path-level signal).
_DOC_EXTENSIONS: frozenset[str] = frozenset({
    "md", "rst", "txt", "adoc", "tex",
})

# File extensions that indicate code tasks.
_CODE_EXTENSIONS: frozenset[str] = frozenset({
    "py", "js", "ts", "tsx", "jsx", "go", "rs", "java", "cs", "cpp",
    "c", "h", "rb", "php", "sh", "bash", "zsh",
})


def _code_doc_boost(
    features: object,
    scored_agents: list,
    boost: float = 0.15,
) -> list:
    """Apply a deterministic code-writer / doc-writer boost from path/keyword.

    Inspects ``features.extensions``, ``features.paths``, and
    ``features.raw_keywords`` to determine whether the task leans toward
    code or prose.  The winning side receives a ``+boost`` lift (capped at
    1.0) on the relevant agent(s); the losing side is penalised by the
    same amount (floored at 0.0).  All other agents are untouched.

    This is purely deterministic and label-free — no LLM call, no model.

    Args:
        features: A ``Features`` namedtuple/dataclass with at minimum
            ``extensions`` (frozenset[str]), ``paths`` (tuple[str]),
            and ``raw_keywords`` (frozenset[str]) attributes.
        scored_agents: List of ``ScoredEntry`` (name + score) as returned
            by ``score_entries()``.
        boost: Score delta applied symmetrically (default 0.15).

    Returns:
        A NEW list of ``ScoredEntry`` objects with adjusted scores, sorted
        by score descending.  The original list is not mutated.
    """
    from dataclasses import replace

    # Gather extension evidence.
    exts = getattr(features, "extensions", frozenset())
    doc_ext_hit = bool(exts & _DOC_EXTENSIONS)
    code_ext_hit = bool(exts & _CODE_EXTENSIONS)

    # Gather raw-keyword evidence (unstemmed; covers exact terms).
    raw_kws = getattr(features, "raw_keywords", frozenset())
    task_lower = " ".join(sorted(raw_kws))  # comparable set-based string
    doc_kw_hit = bool(raw_kws & _DOC_KEYWORDS)
    code_kw_hit = bool(raw_kws & _CODE_KEYWORDS)
    # Also check path strings for doc/code indicators.
    paths_str = " ".join(getattr(features, "paths", ())).lower()
    if not doc_kw_hit and not doc_ext_hit:
        doc_kw_hit = any(k in paths_str for k in ("/docs/", "/doc/", ".md"))
    if not code_kw_hit and not code_ext_hit:
        code_kw_hit = any(
            k in paths_str for k in ("/src/", "/lib/", "/tests/", ".py")
        )

    # Score the signals: each hit = 1 vote.
    doc_votes = int(doc_ext_hit) + int(doc_kw_hit)
    code_votes = int(code_ext_hit) + int(code_kw_hit)

    if doc_votes == code_votes:
        # No net signal — return unchanged.
        return list(scored_agents)

    favor_doc = doc_votes > code_votes

    adjusted: list = []
    for se in scored_agents:
        name = se.entry.name
        delta = 0.0
        if name == "doc-writer":
            delta = boost if favor_doc else -boost
        elif name == "code-writer":
            delta = -boost if favor_doc else boost
        if delta == 0.0:
            adjusted.append(se)
        else:
            new_score = max(0.0, min(1.0, se.score + delta))
            # ScoredEntry is a dataclass — use replace() to avoid mutation.
            adjusted.append(replace(se, score=new_score))

    # Re-sort by score descending (stable sort preserves prior order on tie).
    adjusted.sort(key=lambda x: x.score, reverse=True)
    # Suppress unused variable from the intermediate string (needed for pyright)
    _ = task_lower
    return adjusted


# ---------------------------------------------------------------------------
# Calibrated lexical variant (offline spike only — #374)
# ---------------------------------------------------------------------------


def run_lexical_calibrated(
    entries: list[CorpusEntry],
    catalog_path: Path,
    *,
    delegate_gap: float = 0.2,
    delegate_threshold: float = 0.85,
    advisory_min: float = 0.5,
    code_doc_boost: float = 0.0,
) -> list[SystemResult]:
    """Calibrated lexical baseline with overridable decision thresholds.

    Runs the same pipeline as ``run_lexical`` (``build_features`` →
    ``score_entries`` → ``decide()``) but temporarily overrides the
    module-level threshold constants in ``claude_wayfinder.match._decide``
    for the duration of each call.  The live defaults in ``_decide.py``
    are **never changed on disk** — this is an offline-only spike
    variant for #374.

    Optionally applies the deterministic Lever-B code/doc differentiator
    before the ``decide()`` call (when ``code_doc_boost > 0``).

    The override mechanism uses a try/finally block to guarantee the
    original values are restored even if ``decide()`` raises.

    Args:
        entries: Corpus entries to evaluate.
        catalog_path: Path to the dispatch-catalog JSON file.
        delegate_gap: Override for ``_DELEGATE_GAP`` (default 0.2 is the
            live value; sweep candidate range 0.0–0.30).
        delegate_threshold: Override for ``_DELEGATE_THRESHOLD``
            (default 0.85 is the live value).
        advisory_min: Override for ``_ADVISORY_MIN`` (default 0.5 is
            the live value).
        code_doc_boost: When > 0, the Lever-B code/doc differentiator
            is applied before ``decide()``.  A value of 0.15 is
            recommended (source: #374 sweep).  0.0 disables Lever B.

    Returns:
        List of ``SystemResult``, one per entry, in input order.
    """
    catalog = load_catalog(Path(catalog_path))

    # Snapshot originals so the finally block can restore them.
    _orig_gap = _decide_module._DELEGATE_GAP
    _orig_threshold = _decide_module._DELEGATE_THRESHOLD
    _orig_advisory = _decide_module._ADVISORY_MIN

    results: list[SystemResult] = []
    try:
        # Apply threshold overrides to the live module namespace.
        _decide_module._DELEGATE_GAP = delegate_gap
        _decide_module._DELEGATE_THRESHOLD = delegate_threshold
        _decide_module._ADVISORY_MIN = advisory_min

        # Sanity check: verify the override takes effect by probing the
        # module attribute we just set before any decide() calls.
        assert _decide_module._DELEGATE_GAP == delegate_gap, (
            f"Override failed: expected _DELEGATE_GAP={delegate_gap!r}, "
            f"got {_decide_module._DELEGATE_GAP!r}"
        )

        for entry in entries:
            ctx = _entry_to_context(entry)
            features = build_features(ctx)
            scored_agents, scored_skills = score_entries(catalog, features)

            # Lever B: optional code/doc differentiator.
            if code_doc_boost > 0.0:
                scored_agents = _code_doc_boost(
                    features, scored_agents, boost=code_doc_boost
                )

            top_scores = {
                se.entry.name: round(se.score, 4)
                for se in scored_agents[:5]
            }
            decision_dict = decide(
                scored_agents, scored_skills, features, catalog
            )
            extras = {
                "scores": top_scores,
                "calibration": {
                    "delegate_gap": delegate_gap,
                    "delegate_threshold": delegate_threshold,
                    "advisory_min": advisory_min,
                    "code_doc_boost": code_doc_boost,
                },
            }
            results.append(
                _decide_to_system_result(
                    entry.corpus_id, decision_dict, extras
                )
            )
    finally:
        # Always restore originals — do not leave live code mutated.
        _decide_module._DELEGATE_GAP = _orig_gap
        _decide_module._DELEGATE_THRESHOLD = _orig_threshold
        _decide_module._ADVISORY_MIN = _orig_advisory

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

    The margin gate (``_is_domain_any``) marks a prompt as domain-any and
    routes to a posture-neutral advisory when domain evidence is too diffuse.

    Requires model2vec (``pip install '.[spike]'``).  Raises ImportError
    with a descriptive message if missing; callers use ``pytest.importorskip``
    or check availability before calling.

    v0 calibration decisions (updated for #351):
    - Margin gate for domain-any: margin < 0.01 (issue #351 data-driven
      best-F1 sweep; supersedes the 0.04 value the 8M spike floated).
    - Entropy is computed and stored in ``extras["entropy"]`` as DIAGNOSTIC
      metadata only — entropy does NOT gate domain-any.  The encoder's
      softmax entropy is ~2.31 on every prompt (near max for 5 classes) and
      carries no domain-any signal (8M spike §5.3, rec #2).
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

    results: list[SystemResult] = []
    for entry in entries:
        domain_result = clf.classify(entry.task_description)

        sorted_probs = sorted(domain_result.distribution.values(), reverse=True)
        top1 = sorted_probs[0]
        top2 = sorted_probs[1] if len(sorted_probs) > 1 else 0.0
        margin = top1 - top2
        # Entropy is kept as DIAGNOSTIC metadata only — it does not gate
        # domain-any detection (see _is_domain_any docstring and issue #351).
        entropy = domain_result.entropy

        # Domain-any detection: margin-only gate (issue #351).
        is_any = _is_domain_any(margin)
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
        agent = cell_map_lookup(domain, "build")

        # Sentinel is a routing instruction; translate before catalog check.
        if agent == SELF_HANDLE_SENTINEL:
            decision = "self_handle"
            agent = None
            confidence = round(float(top1), 4)
        elif agent and agent in catalog_agent_names:
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
    - Domain from encoder (margin-only gate (issue #351) as in system 2).
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

    results: list[SystemResult] = []
    for entry in entries:
        # Encoder domain
        domain_result = clf.classify(entry.task_description)
        sorted_probs = sorted(domain_result.distribution.values(), reverse=True)
        top1 = sorted_probs[0]
        top2 = sorted_probs[1] if len(sorted_probs) > 1 else 0.0
        margin = top1 - top2
        entropy = domain_result.entropy
        is_any = _is_domain_any(margin)
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

        # Sentinel check must fire FIRST: the sentinel is not a routable agent
        # and must never reach the "unknown agent → advisory" guard below.
        if agent == SELF_HANDLE_SENTINEL:
            decision = "self_handle"
            agent = None
            braked = False  # a self_handle abstention is not a braked advisory outcome
        elif agent and agent not in catalog_agent_names:
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


# ---------------------------------------------------------------------------
# System 5: Supplied-compose (oracle two-axis, issue #363)
# ---------------------------------------------------------------------------


def run_supplied_compose(
    entries: list[CorpusEntry],
    catalog_path: Path,
    labels: dict[int, GoldLabel],
) -> list[SystemResult]:
    """System 5: supplied-compose — domain × posture oracle variant.

    Mirrors ``run_oracle_compose`` from the validated probe
    (``.tmp/oracle_two_axis_probe.py`` lines 401-487).

    Algorithm
    ---------
    For each entry:

    1. Look up oracle domain/posture from ``labels`` (``None`` when
       the entry is unlabeled).
    2. Run lexical scoring via ``build_features`` + ``score_entries``.
    3. Apply the domain hard-gate via ``gate_agents`` (from
       ``claude_wayfinder.match._cells``).
    4. If ``oracle_posture`` is truthy, look up the preferred agent
       for ``(domain_for_lookup, oracle_posture)`` via
       ``cell_map_lookup``.  If that agent is in the gated candidate
       set AND is a routable catalog agent, delegate to it at
       confidence 0.9 (``posture_routed=True``).
    5. Otherwise fall back to ``decide()`` on the gated list
       (``posture_routed=False``).

    Uses the centralized ``gate_agents``/``cell_map_lookup``/
    ``DOMAIN_AGENT_MAP`` from ``_cells`` — does NOT replicate gating
    logic inline.

    Args:
        entries: Corpus entries to evaluate.
        catalog_path: Path to the dispatch-catalog JSON file.
        labels: Gold label dict (corpus_id → GoldLabel); entries
            absent from this dict are treated as unlabeled.

    Returns:
        List of ``SystemResult``, one per entry, in input order.
        Each result's ``extras`` contains:

        - ``"scores"``: top-5 gated agent name → score (rounded to
          4 decimal places).
        - ``"oracle_domain"``: domain string from labels, or ``None``.
        - ``"oracle_posture"``: posture string from labels, or ``None``.
        - ``"posture_routed"``: ``True`` when posture selected the
          agent; ``False`` on the fallback path.
    """
    catalog = load_catalog(catalog_path)
    catalog_agent_names: frozenset[str] = frozenset(
        e.name
        for e in catalog
        if e.kind == "agent" and is_agent_routable(
            name=e.name, kind=e.kind, source=e.source, routable=e.routable
        )
    )

    results: list[SystemResult] = []
    for entry in entries:
        label = labels.get(entry.corpus_id)
        oracle_domain: str | None = label.domain if label else None
        oracle_posture: str | None = label.posture if label else None
        domain_for_lookup: str = oracle_domain if oracle_domain else "any"

        # Step 1: lexical scoring + domain hard-gate
        ctx = _entry_to_context(entry)
        features = build_features(ctx)
        scored_agents, scored_skills = score_entries(catalog, features)

        # Apply domain gate via the centralized _cells helper
        gated_agents = gate_agents(scored_agents, oracle_domain)

        # Step 2: posture-based selection within gated candidates
        posture_routed: bool = False
        agent_out: str | None = None
        decision_out: str = "advisory"
        confidence_out: float = 0.5

        if oracle_posture:
            preferred = cell_map_lookup(domain_for_lookup, oracle_posture)
            area_span = label.area_span if label else 1
            if oracle_posture == "diagnose" and area_span >= 2:
                # #396/#411 (Codex P2): broad/cross-layer diagnose routes to
                # investigator regardless of domain — mirrors
                # _route_from_postures (run_composed). area_span is a gold
                # axis supplied via GoldLabel; production derives it from
                # text (E7). Routability guard: only fire when investigator
                # is a routable catalog agent; absent → posture_routed stays
                # False and control falls to the decide() fallback below.
                if "investigator" in catalog_agent_names:
                    agent_out = "investigator"
                    decision_out = "delegate"
                    confidence_out = 0.9
                    posture_routed = True
                # else: investigator absent — skip posture routing; decide() fires below
            # #397: sentinel short-circuits BEFORE the gate/catalog checks;
            # it is a routing instruction, not a routable agent.  It must
            # never reach genuine_gated_names or appear in extras["scores"].
            elif preferred == SELF_HANDLE_SENTINEL:
                decision_out = "self_handle"
                agent_out = None
                confidence_out = 0.9
                posture_routed = True
            else:
                gated_names = {se.entry.name for se in gated_agents}
                # Bug #366 guard: distinguish genuine gate survivors from the
                # empty-gate ungated fallback.  gate_agents() falls back to
                # the full ungated list when gating would produce an empty
                # result.  When that fallback fires, an out-of-domain
                # preferred agent may appear in gated_names even though the
                # domain gate excludes it.
                # Fix (Option B): for concretely-gated domains, only consider
                # an agent a genuine survivor when it is actually in the
                # domain's allowed set — not merely present in the (possibly
                # fallback) list.
                domain_allowed = DOMAIN_AGENT_MAP.get(oracle_domain)
                if domain_allowed is not None:
                    # Concrete gate: genuine survivors are scored AND allowed.
                    genuine_gated_names = gated_names & domain_allowed
                else:
                    # No gate (None key or unknown domain): all scored agents
                    # are genuine — no distinction needed.
                    genuine_gated_names = gated_names
                if (
                    preferred
                    and preferred in genuine_gated_names
                    and preferred in catalog_agent_names
                ):
                    agent_out = preferred
                    decision_out = "delegate"
                    confidence_out = 0.9
                    posture_routed = True

        if not posture_routed:
            decision_dict = decide(gated_agents, scored_skills, features, catalog)
            agent_out = decision_dict.get("agent")
            decision_out = str(decision_dict.get("decision", ""))
            confidence_out = float(decision_dict.get("confidence", 0.0))

        top_scores = {
            se.entry.name: round(se.score, 4) for se in gated_agents[:5]
        }
        results.append(SystemResult(
            corpus_id=entry.corpus_id,
            decision=decision_out,
            agent=agent_out,
            confidence=confidence_out,
            extras={
                "scores": top_scores,
                "oracle_domain": oracle_domain,
                "oracle_posture": oracle_posture,
                "posture_routed": posture_routed,
            },
        ))
    return results
