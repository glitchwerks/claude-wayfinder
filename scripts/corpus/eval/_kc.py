"""Compute the KC-1 through KC-5 shadow-routing verdicts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from claude_wayfinder.match._cells import cell_map_lookup
from scripts.corpus.eval._metrics import (
    metric_confident_wrong_rate,
    metric_routing_correctness,
)
from scripts.corpus.eval._reader import GoldLabel
from scripts.corpus.eval._systems import SystemResult

# Fixed historical lexical-CW anchor from
# docs/superpowers/specs/2026-06-14-two-axis-labeling-design.md:586. Its
# denominator is wrong delegates / all delegates, matching
# metric_confident_wrong_rate's documented "Fraction of delegate decisions
# that were wrong" convention in scripts/corpus/eval/_metrics.py.
KC2_LEXICAL_CW_ANCHOR: float = 0.2558
assert KC2_LEXICAL_CW_ANCHOR == 0.2558, "KC-2 historical anchor must stay pinned"

# F_indep_lo (0.7391) minus the specified 0.05 tolerance. This value is
# pinned instead of recomputed so the threshold has stable float semantics.
_KC1_SHADOW_RC_FLOOR: float = 0.6891
_KC1_LEXICAL_MARGIN: float = 0.20
_KC3_DECISIVENESS_FLOOR: float = 0.55
_KC5_INFRA_RC_FLOOR: float = 0.600
_KC5_MIN_SLICE_N: int = 20

KCStatus = Literal["PASS", "FAIL", "INSUFFICIENT_DATA"]
CorpusRow = dict[str, Any]


@dataclass(frozen=True)
class KCVerdict:
    """Record one knowledge-criterion verdict and its supporting metrics.

    Attributes:
        kc: Criterion identifier, such as ``"KC-1"``.
        status: One of ``"PASS"``, ``"FAIL"``, or
            ``"INSUFFICIENT_DATA"``.
        metrics: Criterion-specific measurements used for the verdict.
    """

    kc: str
    status: KCStatus
    metrics: dict[str, Any]


def _route_could_differ(
    caller_domain: str, gold_domain: str, posture: str | None
) -> bool:
    """Return whether correcting the caller's domain could change the route.

    Compares the preferred agent under the caller's (mislabeled) domain
    against the preferred agent under the gold domain, for this row's
    actual posture -- not whether the posture is invariant across every
    domain in the abstract. A coincidental pair collision (e.g. ``is_any``
    and ``code`` both resolving to ``code-writer`` under ``build``) is
    correctly excluded even for postures with genuine per-domain variance
    elsewhere in the map.

    Args:
        caller_domain: The caller-supplied (mislabeled) domain, one of
            ``"is_any"`` or ``"project_meta"``.
        gold_domain: The corrected domain from the gold label.
        posture: Caller-supplied posture label, or ``None`` when omitted.

    Returns:
        ``True`` when the two domains resolve to different preferred
        agents under this posture. A missing posture preserves the
        original KC-4 behavior of never being exempted.
    """
    if posture is None:
        return True
    return cell_map_lookup(caller_domain, posture) != cell_map_lookup(
        gold_domain, posture
    )


def _system_results(
    corpus_rows: list[CorpusRow],
    arm: Literal["shadow", "live"],
) -> list[SystemResult]:
    """Adapt shadow-corpus rows to the validated metric result type.

    Args:
        corpus_rows: Shadow-joined corpus rows to adapt.
        arm: Row-field prefix selecting the shadow or live routing arm.

    Returns:
        System results containing the fields consumed by the RC/CW kernels.
    """
    results: list[SystemResult] = []
    for row in corpus_rows:
        shadow = row["shadow"]
        results.append(
            SystemResult(
                corpus_id=row["corpus_id"],
                decision=shadow[f"{arm}_decision"],
                agent=shadow[f"{arm}_agent"],
                confidence=shadow[f"{arm}_confidence"],
                extras={},
            )
        )
    return results


def compute_kc1(
    corpus_rows: list[CorpusRow],
    gold: dict[int, GoldLabel],
) -> KCVerdict:
    """Compute KC-1 whole-sample routing correctness.

    Args:
        corpus_rows: Shadow-joined corpus rows.
        gold: Gold labels keyed by corpus ID.

    Returns:
        KC-1 verdict with shadow and lexical routing correctness.
    """
    shadow_rc = metric_routing_correctness(
        _system_results(corpus_rows, "shadow"), gold
    )
    lexical_rc = metric_routing_correctness(
        _system_results(corpus_rows, "live"), gold
    )
    passed = (
        shadow_rc >= _KC1_SHADOW_RC_FLOOR
        and shadow_rc >= lexical_rc + _KC1_LEXICAL_MARGIN
    )
    return KCVerdict(
        kc="KC-1",
        status="PASS" if passed else "FAIL",
        metrics={"shadow_rc": shadow_rc, "lexical_rc": lexical_rc},
    )


def compute_kc2(
    corpus_rows: list[CorpusRow],
    gold: dict[int, GoldLabel],
) -> KCVerdict:
    """Compute the KC-2 confident-wrong hard block.

    Args:
        corpus_rows: Shadow-joined corpus rows.
        gold: Gold labels keyed by corpus ID.

    Returns:
        KC-2 verdict with both arm rates and the fixed lexical anchor.
    """
    shadow_cw = metric_confident_wrong_rate(
        _system_results(corpus_rows, "shadow"), gold
    )
    lexical_cw = metric_confident_wrong_rate(
        _system_results(corpus_rows, "live"), gold
    )
    status: KCStatus = (
        "PASS" if shadow_cw <= KC2_LEXICAL_CW_ANCHOR else "FAIL"
    )
    return KCVerdict(
        kc="KC-2",
        status=status,
        metrics={
            "shadow_cw": shadow_cw,
            "lexical_cw": lexical_cw,
            "anchor": KC2_LEXICAL_CW_ANCHOR,
        },
    )


def compute_kc3(
    corpus_rows: list[CorpusRow],
    gold: dict[int, GoldLabel],
) -> KCVerdict:
    """Compute KC-3 decisiveness on gated, mapped, high-confidence rows.

    Args:
        corpus_rows: Shadow-joined corpus rows.
        gold: Gold labels keyed by corpus ID. KC-3 does not inspect their
            values, but accepts the common KC computation interface.

    Returns:
        KC-3 verdict with eligible count, numerator, and decisiveness rate.
    """
    del gold
    eligible: list[CorpusRow] = []
    for row in corpus_rows:
        caller_input = row["input"]
        domain = caller_input.get("domain")
        posture = caller_input.get("posture")
        confidence = caller_input.get("confidence")
        domain_for_lookup = (
            domain if domain not in (None, "is_any") else "any"
        )
        is_gated = domain not in (None, "is_any")
        cell_exists = (
            posture is not None
            and cell_map_lookup(domain_for_lookup, posture) is not None
        )
        if is_gated and cell_exists and confidence == "high":
            eligible.append(row)

    eligible_n = len(eligible)
    numerator = sum(
        1
        for row in eligible
        if row["shadow"]["posture_routed"] is True
        or (
            row["shadow"]["posture_routed"] is False
            and row["shadow"]["shadow_decision"] == "delegate"
            and bool(row["shadow"]["gated_agent_names"])
        )
    )
    rate = numerator / eligible_n if eligible_n else 0.0
    if eligible_n == 0:
        status: KCStatus = "INSUFFICIENT_DATA"
    else:
        status = "PASS" if rate >= _KC3_DECISIVENESS_FLOOR else "FAIL"

    return KCVerdict(
        kc="KC-3",
        status=status,
        metrics={
            "eligible_n": eligible_n,
            "numerator": numerator,
            "rate": rate,
        },
    )


def compute_kc4(
    corpus_rows: list[CorpusRow],
    gold: dict[int, GoldLabel],
) -> KCVerdict:
    """Compute KC-4 routing neutrality for caller-domain mislabels.

    Args:
        corpus_rows: Shadow-joined corpus rows.
        gold: Gold labels keyed by corpus ID.

    Returns:
        KC-4 verdict with eligible-row and route-change counts.
    """
    eligible: list[CorpusRow] = []
    for row in corpus_rows:
        corpus_id = row["corpus_id"]
        label = gold.get(corpus_id)
        caller_domain = row["input"].get("domain")
        posture = row["input"].get("posture")
        if (
            label is not None
            and caller_domain in {"is_any", "project_meta"}
            and label.domain != caller_domain
            and _route_could_differ(caller_domain, label.domain, posture)
        ):
            eligible.append(row)

    eligible_n = len(eligible)
    violations = sum(
        row["shadow"]["posture_routed"] is True for row in eligible
    )
    if eligible_n == 0:
        status: KCStatus = "INSUFFICIENT_DATA"
    else:
        status = "PASS" if violations == 0 else "FAIL"

    return KCVerdict(
        kc="KC-4",
        status=status,
        metrics={"eligible_n": eligible_n, "violations": violations},
    )


def compute_kc5(
    corpus_rows: list[CorpusRow],
    gold: dict[int, GoldLabel],
) -> KCVerdict:
    """Compute KC-5 routing correctness on the gold infra-deploy slice.

    Args:
        corpus_rows: Shadow-joined corpus rows.
        gold: Gold labels keyed by corpus ID.

    Returns:
        KC-5 verdict with slice size and shadow routing correctness.
    """
    slice_rows = [
        row
        for row in corpus_rows
        if row["corpus_id"] in gold
        and gold[row["corpus_id"]].domain == "infra_deploy"
    ]
    slice_gold = {
        row["corpus_id"]: gold[row["corpus_id"]] for row in slice_rows
    }
    slice_n = len(slice_rows)
    shadow_rc = metric_routing_correctness(
        _system_results(slice_rows, "shadow"), slice_gold
    )
    if slice_n < _KC5_MIN_SLICE_N:
        status: KCStatus = "INSUFFICIENT_DATA"
    else:
        status = "PASS" if shadow_rc >= _KC5_INFRA_RC_FLOOR else "FAIL"

    return KCVerdict(
        kc="KC-5",
        status=status,
        metrics={"slice_n": slice_n, "shadow_rc": shadow_rc},
    )
