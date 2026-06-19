"""Six metrics for the corpus eval harness (issue #340).

All metrics per spec §13.3.  Metrics that require gold labels are
skipped (return float('nan')) when labels are missing or insufficient.

Metrics
-------
1. Error correlation (§8.4, the decisive one):
   P(domain wrong ∧ posture wrong, same direction) vs independence
   product, conditioned on gold.  Measures whether system A and
   system B err on the same entries (lexical+encoder pair for #330).

2. Error-severity distribution by cell distance (R4):
   Classifies delegate-band misses as adjacent-posture, cross-posture,
   or cross-domain using the §9.1 grid.

3. Tier-C decisiveness rate (§10.3 guardrail 4):
   How often a C select/brake changed the final cell/band.
   Above threshold = §8.1 re-entry = failing result.

4. False-default-build rate (§10.4):
   Rate at which the build default fires and produces a wrong route.

5. Braked-outcome candidate quality (P3 residual):
   gold ∈ alternatives when band = advisory-via-brake.

6. Confident-wrong rate vs baseline:
   Delegate-band misses as a fraction of all delegate decisions.

v0 calibration decisions:
- Adjacent postures (low-harm): assess ↔ critique (§12.3 R4).
  Any other same-domain posture miss → cross-posture.
  Any cross-domain miss → cross-domain.
- Agent → (domain, posture) cell map for severity uses the §9.1 grid.
- Error correlation: Phi coefficient over error indicator vectors,
  conditioned on gold labels being present and decision = delegate.
  Falls back to nan with < 2 shared delegate entries.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from scripts.corpus.eval._reader import GoldLabel
from scripts.corpus.eval._systems import SystemResult

# ---------------------------------------------------------------------------
# Agent → (domain, posture) cell map for severity scoring
# Source: §9.1 grid from the spec.
# ---------------------------------------------------------------------------

_AGENT_CELL: dict[str, tuple[str, str]] = {
    "code-writer": ("code", "build"),
    "doc-writer": ("docs_prose", "build"),
    "debugger": ("code", "diagnose"),
    "investigator": ("any", "diagnose"),
    "code-reviewer": ("code", "assess"),
    "project-reviewer": ("project_meta", "assess"),
    "inquisitor": ("code", "critique"),
    "approach-critic": ("any", "idea-critique"),
    "auditor": ("any", "verify"),
    "researcher": ("any", "research"),
    "project-planner": ("project_meta", "plan"),
    "devops": ("infra_deploy", "plan"),
    "ops": ("any", "operate"),
}

# Adjacent posture pairs (low-harm, §12.3 R4).
# Only assess↔critique is named as low-harm in the spec (P9 finding).
# idea-critique is a sub-type of critique (both approach-critic territory),
# so assess↔idea-critique is also adjacent.
_ADJACENT_PAIRS: frozenset[frozenset[str]] = frozenset({
    frozenset({"assess", "critique"}),
    frozenset({"assess", "idea-critique"}),
    frozenset({"critique", "idea-critique"}),
})


# ---------------------------------------------------------------------------
# MetricsResult
# ---------------------------------------------------------------------------


@dataclass
class MetricsResult:
    """Container for all six eval metrics.

    Attributes:
        error_correlation: Phi coefficient of error co-occurrence between
            two systems, conditioned on gold.  ``float('nan')`` when
            insufficient data.
        error_severity: Dict with counts for each severity class:
            ``adjacent``, ``cross_posture``, ``cross_domain``.
        tier_c_decisiveness: Fraction of extractor results where Tier-C
            fired.  Above ~0.3 is a failing signal (§10.3 g4).
        false_default_build_rate: Rate at which default-build produced
            a wrong routing.  ``float('nan')`` when no default-build cases.
        braked_candidate_quality: Fraction of braked advisory outcomes
            where gold appears in the candidate list.  ``float('nan')``
            when no braked outcomes.
        confident_wrong_rate: Fraction of delegate decisions that were
            wrong.  ``float('nan')`` when no delegate decisions.
    """

    error_correlation: float
    error_severity: dict[str, int]
    tier_c_decisiveness: float
    false_default_build_rate: float
    braked_candidate_quality: float
    confident_wrong_rate: float


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _prediction_matches_gold(result: SystemResult, gold_agent: str) -> bool:
    """Return True when result's prediction matches gold.

    Covers both the normal real-agent path and the self_handle abstain
    normalization: ``decision == "self_handle"`` with
    ``gold_agent == "self_handle"`` is a correct abstention.
    ``decision == "self_handle_unaided"`` is NOT credited.

    Args:
        result: A SystemResult from any eval system.
        gold_agent: The expected agent name from the GoldLabel.

    Returns:
        True when the result correctly predicts gold, including the
        self_handle normalization.
    """
    if result.agent == gold_agent:
        return True
    return result.decision == "self_handle" and gold_agent == "self_handle"


def _is_error(result: SystemResult, label: GoldLabel) -> bool:
    """Return True when a delegate decision has the wrong agent.

    Args:
        result: A SystemResult.
        label: The corresponding GoldLabel.

    Returns:
        True when decision is ``"delegate"`` and agent ≠ gold_agent.
    """
    return result.decision == "delegate" and result.agent != label.gold_agent


def _severity_class(
    predicted_agent: str | None,
    gold_agent: str,
) -> str:
    """Classify an error by cell distance (§12.3 R4).

    Args:
        predicted_agent: The agent name returned by the system.
        gold_agent: The expected agent name.

    Returns:
        One of ``"adjacent"``, ``"cross_posture"``, ``"cross_domain"``.
    """
    if predicted_agent is None or predicted_agent == gold_agent:
        return "correct"

    pred_cell = _AGENT_CELL.get(predicted_agent)
    gold_cell = _AGENT_CELL.get(gold_agent)

    if pred_cell is None or gold_cell is None:
        # Unknown agent → treat as cross-domain
        return "cross_domain"

    pred_domain, pred_posture = pred_cell
    gold_domain, gold_posture = gold_cell

    # Same posture, different domain (or same both) — shouldn't reach here
    # since we already filtered agent == gold_agent above, but just in case
    if pred_posture == gold_posture and (
        pred_domain == gold_domain or pred_domain == "any" or gold_domain == "any"
    ):
        return "correct"

    # Adjacent posture pairs (low-harm per R4)
    if frozenset({pred_posture, gold_posture}) in _ADJACENT_PAIRS:
        return "adjacent"

    # Cross-domain: different concrete domains (neither is "any")
    # "any" means the agent is domain-agnostic, so no domain clash
    concrete_pred = pred_domain not in ("any",)
    concrete_gold = gold_domain not in ("any",)
    if concrete_pred and concrete_gold and pred_domain != gold_domain:
        return "cross_domain"

    # Different postures, domains compatible
    return "cross_posture"


def _build_index(
    results: list[SystemResult],
) -> dict[int, SystemResult]:
    """Index SystemResults by corpus_id.

    Args:
        results: List of SystemResult objects.

    Returns:
        Dict mapping corpus_id → SystemResult.
    """
    return {r.corpus_id: r for r in results}


# ---------------------------------------------------------------------------
# Metric 1: Error correlation (§8.4)
# ---------------------------------------------------------------------------


def metric_error_correlation(
    sys_a: list[SystemResult],
    sys_b: list[SystemResult],
    labels: dict[int, GoldLabel],
) -> float:
    """Compute error co-occurrence (Phi coefficient) between two systems.

    Conditioned on gold labels.  Only entries with both a label AND a
    delegate decision in at least one system are included.

    The Phi coefficient measures whether the two systems err on the same
    corpus entries (high Phi → correlated errors → the additive combination
    is unsafe per §8.4).

    v0 calibration: uses all delegate entries in the intersection of both
    systems.  Returns 0.0 when both systems have zero errors.  Returns
    float('nan') when fewer than 2 shared labelled entries.

    Args:
        sys_a: System A results (first signal, e.g. lexical or encoder).
        sys_b: System B results (second signal, e.g. extractors or composed).
        labels: Gold label dict from ``load_labels()``.

    Returns:
        Phi coefficient in [-1.0, 1.0], or ``float('nan')`` when
        insufficient data.
    """
    idx_a = _build_index(sys_a)
    idx_b = _build_index(sys_b)

    # Restrict to entries present in BOTH systems AND with labels
    common_ids = set(idx_a.keys()) & set(idx_b.keys()) & set(labels.keys())

    # Further restrict to entries where at least one system emitted delegate
    eval_ids = [
        cid for cid in common_ids
        if (
            idx_a[cid].decision == "delegate"
            or idx_b[cid].decision == "delegate"
        )
    ]

    if len(eval_ids) < 2:
        # With 0 entries: no data → nan; with 1 entry: check for errors
        if len(eval_ids) == 0:
            return float("nan")
        # 1 entry: if neither system has an error → correlation is 0.0
        label = labels[eval_ids[0]]
        ra = idx_a[eval_ids[0]]
        rb = idx_b[eval_ids[0]]
        if not _is_error(ra, label) and not _is_error(rb, label):
            return 0.0
        return float("nan")

    # Build binary error indicator vectors (1 = error, 0 = correct/non-delegate)
    vec_a: list[int] = []
    vec_b: list[int] = []
    for cid in sorted(eval_ids):
        label = labels[cid]
        ra = idx_a[cid]
        rb = idx_b[cid]
        err_a = 1 if _is_error(ra, label) else 0
        err_b = 1 if _is_error(rb, label) else 0
        vec_a.append(err_a)
        vec_b.append(err_b)

    # Phi coefficient: (n11*n00 - n10*n01) / sqrt(...)
    n11 = sum(a and b for a, b in zip(vec_a, vec_b))
    n10 = sum(a and not b for a, b in zip(vec_a, vec_b))
    n01 = sum(not a and b for a, b in zip(vec_a, vec_b))
    n00 = sum(not a and not b for a, b in zip(vec_a, vec_b))

    denom_sq = (n11 + n10) * (n11 + n01) * (n00 + n10) * (n00 + n01)
    if denom_sq <= 0:
        return 0.0

    phi = (n11 * n00 - n10 * n01) / math.sqrt(denom_sq)
    return round(phi, 4)


# ---------------------------------------------------------------------------
# Metric 2: Error severity distribution (R4)
# ---------------------------------------------------------------------------


def metric_error_severity(
    results: list[SystemResult],
    labels: dict[int, GoldLabel],
) -> dict[str, int]:
    """Classify delegate-band errors by cell-distance severity (§12.3 R4).

    Counts misses into three buckets:
    - adjacent: posture is a low-harm adjacent pair (assess↔critique etc.)
    - cross_posture: different posture, same or any domain
    - cross_domain: different non-any domain

    Args:
        results: System results to evaluate.
        labels: Gold label dict from ``load_labels()``.

    Returns:
        Dict with keys ``"adjacent"``, ``"cross_posture"``, ``"cross_domain"``
        and their respective error counts.
    """
    counts: dict[str, int] = {
        "adjacent": 0,
        "cross_posture": 0,
        "cross_domain": 0,
    }
    for result in results:
        if result.decision != "delegate":
            continue
        label = labels.get(result.corpus_id)
        if label is None:
            continue
        if result.agent == label.gold_agent:
            continue

        cls = _severity_class(result.agent, label.gold_agent)
        if cls in counts:
            counts[cls] += 1

    return counts


# ---------------------------------------------------------------------------
# Metric 3: Tier-C decisiveness rate (§10.3 g4)
# ---------------------------------------------------------------------------


def metric_tier_c_decisiveness(results: list[SystemResult]) -> float:
    """Compute the Tier-C decisiveness rate (§10.3 guardrail 4).

    Measures how often Tier-C extractor evidence (E10, E12) changed the
    routing outcome.  A high rate means the §8.1 correlation risk
    re-entered through the side door.

    Only includes results that have ``"tier_c_fired"`` in their extras.

    Args:
        results: System results (typically from run_extractors or
            run_composed).

    Returns:
        Fraction of eligible results where Tier-C fired.  Returns 0.0
        when no eligible results.
    """
    eligible = [r for r in results if "tier_c_fired" in r.extras]
    if not eligible:
        # No result has a 'tier_c_fired' key — this system is not an extractor
        # system (e.g. lexical or encoder row).  Return nan so the table shows
        # n/a, which is honest: the metric is undefined for non-extractor rows.
        return float("nan")
    fired_count = sum(1 for r in eligible if r.extras["tier_c_fired"])
    return round(fired_count / len(eligible), 4)


# ---------------------------------------------------------------------------
# Metric 4: False-default-build rate (§10.4)
# ---------------------------------------------------------------------------


def metric_false_default_build(
    results: list[SystemResult],
    labels: dict[int, GoldLabel],
) -> float:
    """Compute the false-default-build rate (§10.4).

    A result counts as a default-build case when ``postures`` in extras is
    empty (no extractor fired → build is the unmarked default).
    The rate is: (wrong default-build cases) / (total default-build cases).

    Wrong is determined using the self_handle normalization: a result with
    ``decision == "self_handle"`` against ``gold_agent == "self_handle"``
    is a correct abstention and does NOT count as wrong.  A self_handle
    result against a real-agent gold still counts as wrong.

    Returns ``float('nan')`` when no default-build cases exist.

    Args:
        results: System results (typically from run_extractors or
            run_composed).
        labels: Gold label dict from ``load_labels()``.

    Returns:
        Rate in [0.0, 1.0] or ``float('nan')`` when no default-build cases.
    """
    # Only count as "default-build" when postures key is present AND empty
    # (the extractor ran but no posture fired → unmarked default §10.4)
    extractor_results = [r for r in results if "postures" in r.extras]
    if not extractor_results:
        # No result has a 'postures' key — this is a non-extractor system
        # (lexical or encoder row).  Return nan: the metric is undefined for
        # systems that did not run posture extractors.
        return float("nan")

    default_build = [r for r in extractor_results if not r.extras["postures"]]
    # When no default-build cases exist (all posture extractors fired), the
    # rate is 0.0 by definition — there are no cases to be false.
    if not default_build:
        return 0.0

    # Denominator: only labeled default-build rows.  Unlabeled rows cannot
    # contribute to numerator or denominator — counting them in the
    # denominator while skipping them in the numerator artificially depresses
    # the rate (partial-labels artifact, reviewer fix §10.4).
    labeled_default_build = [
        r for r in default_build if labels.get(r.corpus_id) is not None
    ]
    if not labeled_default_build:
        return float("nan")

    # A row is wrong only when it does NOT match gold.  The self_handle
    # normalization (via _prediction_matches_gold) ensures that
    # decision="self_handle" against gold="self_handle" is NOT counted as
    # wrong — it is a correct abstention.  A self_handle row against a real-
    # agent gold remains wrong, and real-agent comparisons are unchanged.
    wrong = sum(
        1 for r in labeled_default_build
        if not _prediction_matches_gold(r, labels[r.corpus_id].gold_agent)
    )
    return round(wrong / len(labeled_default_build), 4)


# ---------------------------------------------------------------------------
# Metric 5: Braked-outcome candidate quality (P3 residual)
# ---------------------------------------------------------------------------


def metric_braked_candidate_quality(
    results: list[SystemResult],
    labels: dict[int, GoldLabel],
) -> float:
    """Compute braked-outcome candidate quality (§13.3 metric 5).

    Measures the fraction of braked advisory outcomes where the gold
    agent appears in the candidate alternatives list.

    A result is a "braked outcome" when ``extras["braked"] is True``
    (set by the extractor runner when E12 braked the decision).

    Returns ``float('nan')`` when no braked outcomes exist.

    Args:
        results: System results (typically from run_extractors or
            run_composed).
        labels: Gold label dict from ``load_labels()``.

    Returns:
        Rate in [0.0, 1.0] or ``float('nan')`` when no braked outcomes.
    """
    braked = [r for r in results if r.extras.get("braked", False)]
    if not braked:
        return float("nan")

    gold_in_candidates = 0
    evaluated = 0
    for r in braked:
        label = labels.get(r.corpus_id)
        if label is None:
            continue
        evaluated += 1
        alternatives = r.extras.get("alternatives", [])
        if label.gold_agent in alternatives:
            gold_in_candidates += 1

    if evaluated == 0:
        return float("nan")

    return round(gold_in_candidates / evaluated, 4)


# ---------------------------------------------------------------------------
# Metric 6: Confident-wrong rate (§13.3 metric 6)
# ---------------------------------------------------------------------------


def metric_confident_wrong_rate(
    results: list[SystemResult],
    labels: dict[int, GoldLabel],
) -> float:
    """Compute confident-wrong rate (§13.3 metric 6).

    Fraction of delegate decisions that were wrong.  Only entries with
    gold labels AND decision == ``"delegate"`` are counted.

    Returns ``float('nan')`` when no delegate decisions with labels.

    Args:
        results: System results to evaluate.
        labels: Gold label dict from ``load_labels()``.

    Returns:
        Rate in [0.0, 1.0] or ``float('nan')`` when no eligible entries.
    """
    delegates = [
        r for r in results
        if r.decision == "delegate" and r.corpus_id in labels
    ]
    if not delegates:
        return float("nan")

    wrong = sum(
        1 for r in delegates
        if r.agent != labels[r.corpus_id].gold_agent
    )
    return round(wrong / len(delegates), 4)


# ---------------------------------------------------------------------------
# Metric 7: Routing correctness (RC) — standalone, not in MetricsResult
# ---------------------------------------------------------------------------


def metric_routing_correctness(
    results: list[SystemResult],
    labels: dict[int, GoldLabel],
) -> float:
    """Compute routing correctness (RC) across all labeled results.

    RC is the fraction of labeled corpus entries where the system's
    prediction matches gold.  A result counts as correct when EITHER:

    - ``r.agent == gold_agent`` (existing path — real-agent match), OR
    - ``r.decision == "self_handle"`` AND ``gold_agent == "self_handle"``
      (self_handle normalization — abstain sentinel matches gold abstain).

    The self_handle normalization credits deliberate abstentions when gold
    also calls for abstention.  Note: ``decision == "self_handle_unaided"``
    is NOT credited as matching ``gold_agent == "self_handle"`` — only an
    explicit ``decision == "self_handle"`` receives this treatment.

    A non-delegate result with a matching real agent still counts toward
    RC (decision is irrelevant for the real-agent path).

    Returns ``float('nan')`` when no result has a matching label.

    Args:
        results: System results to evaluate.
        labels: Gold label dict from ``load_labels()``.

    Returns:
        RC in [0.0, 1.0] rounded to 4 decimal places, or
        ``float('nan')`` when no labeled results exist.
    """
    labeled = [r for r in results if r.corpus_id in labels]
    if not labeled:
        return float("nan")

    correct = sum(
        1 for r in labeled
        if _prediction_matches_gold(r, labels[r.corpus_id].gold_agent)
    )
    return round(correct / len(labeled), 4)


# ---------------------------------------------------------------------------
# compute_all_metrics — integration
# ---------------------------------------------------------------------------


def compute_all_metrics(
    lexical: list[SystemResult],
    encoder: list[SystemResult] | None,
    extractors: list[SystemResult],
    composed: list[SystemResult] | None,
    labels: dict[int, GoldLabel],
) -> MetricsResult:
    """Compute all six metrics for all four systems.

    For metrics that compare two systems (metric 1 — error correlation),
    the primary comparison is lexical vs extractors (systems 1 vs 3), as
    these are the two signals intended to be decorrelated.  When encoder
    or composed results are available, additional pairwise correlations
    are also computed (stored in the MetricsResult for the CLI table).

    Metric 3 (Tier-C decisiveness) and 4 (false-default-build) and
    5 (braked candidate quality) are computed over the extractors or
    composed system results (the systems that run the posture extractors).

    Metric 6 (confident-wrong) is computed for each available system.

    Args:
        lexical: System 1 (lexical baseline) results.
        encoder: System 2 (encoder) results, or ``None`` if unavailable.
        extractors: System 3 (extractors-alone) results.
        composed: System 4 (composed) results, or ``None`` if unavailable.
        labels: Gold label dict from ``load_labels()``.

    Returns:
        MetricsResult with all six metrics populated.
    """
    # Metric 1: error correlation (lexical vs extractors — primary pair)
    primary_b = extractors
    error_corr = metric_error_correlation(lexical, primary_b, labels)

    # Metric 2: severity over composed (or extractors if composed unavailable)
    primary_system = composed if composed is not None else extractors
    severity = metric_error_severity(primary_system, labels)

    # Metric 3: Tier-C decisiveness over extractors
    tier_c = metric_tier_c_decisiveness(extractors)

    # Metric 4: false-default-build over extractors
    false_build = metric_false_default_build(extractors, labels)

    # Metric 5: braked candidate quality over extractors
    braked_quality = metric_braked_candidate_quality(extractors, labels)

    # Metric 6: confident-wrong over lexical baseline (primary comparison)
    conf_wrong = metric_confident_wrong_rate(lexical, labels)

    return MetricsResult(
        error_correlation=error_corr,
        error_severity=severity,
        tier_c_decisiveness=tier_c,
        false_default_build_rate=false_build,
        braked_candidate_quality=braked_quality,
        confident_wrong_rate=conf_wrong,
    )
