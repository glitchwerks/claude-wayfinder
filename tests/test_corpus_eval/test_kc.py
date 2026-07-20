"""Tests for scripts.corpus.eval._kc — spec-exact KC-1..KC-5 go/no-go logic.

Issue #423 / #484 ("M15-6b"). These assert the *contract* the not-yet-written
``_kc.py`` module must satisfy, from the plan spec §4.2
(docs/superpowers/plans/2026-07-19-m15-6-shadow-kc-report.md) — the normative
KC-1..KC-5 formulas — NOT from any implementation (none exists yet).

RED — written before implementation.

Public API designed here (the implementer builds to match):

    KCVerdict(kc: str, status: str, metrics: dict[str, Any])
        status is one of "PASS" | "FAIL" | "INSUFFICIENT_DATA".

    compute_kc1(corpus_rows, gold) -> KCVerdict
        metrics: {"shadow_rc", "lexical_rc"}  ("n" sample size optional)
        PASS iff shadow_rc >= 0.6891  AND  shadow_rc >= lexical_rc + 0.20
        (whole-sample; F_indep_lo 0.7391 - 0.05 = 0.6891).
    compute_kc2(corpus_rows, gold) -> KCVerdict           # HARD BLOCK
        metrics: {"shadow_cw", "lexical_cw", "anchor"}
        PASS iff shadow_cw <= 0.2558 (KC2_LEXICAL_CW_ANCHOR).
    compute_kc3(corpus_rows, gold) -> KCVerdict
        metrics: {"eligible_n", "numerator", "rate"}
        PASS iff numerator/eligible_n >= 0.55; INSUFFICIENT_DATA if eligible_n == 0.
    compute_kc4(corpus_rows, gold) -> KCVerdict
        metrics: {"eligible_n", "violations"}
        PASS iff violations == 0; INSUFFICIENT_DATA if eligible_n == 0.
    compute_kc5(corpus_rows, gold) -> KCVerdict
        metrics: {"slice_n", "shadow_rc"}
        PASS iff infra_deploy-slice shadow_rc >= 0.600; INSUFFICIENT_DATA if slice_n == 0.

    KC2_LEXICAL_CW_ANCHOR: float == 0.2558 (spec §F.3, ...:586). Its denominator
    (wrong-delegates / all-delegates) must match metric_confident_wrong_rate's;
    the KC-2 rate fixtures pin that equivalence behaviorally.

Both caller labels (input.*) and the mirrored shadow.* copies are set to the
same values so the implementer may read caller domain/posture/confidence from
either location.
"""

from __future__ import annotations

from typing import Any

from scripts.corpus.eval._kc import (
    KC2_LEXICAL_CW_ANCHOR,
    KCVerdict,
    compute_kc1,
    compute_kc2,
    compute_kc3,
    compute_kc4,
    compute_kc5,
)
from scripts.corpus.eval._reader import GoldLabel

# ---------------------------------------------------------------------------
# Synthetic corpus-row + gold builders (match the documented shadow schema)
# ---------------------------------------------------------------------------


def _row(
    corpus_id: int,
    *,
    # caller labels (mirrored into input.* AND shadow.*)
    domain: str | None = "code",
    posture: str | None = "build",
    confidence: str | None = "high",
    area_span: int = 1,
    # shadow arm (Compose decision)
    shadow_decision: str = "delegate",
    shadow_agent: str | None = "code-writer",
    shadow_confidence: float = 0.9,
    # lexical arm (live decide() decision)
    live_decision: str = "delegate",
    live_agent: str | None = "code-writer",
    live_confidence: float = 0.9,
    # KC-3 / KC-4 routing signals
    posture_routed: bool | None = False,
    gated_agent_names: list[str] | None = None,
    shadow_disposition_source: str = "decide",
) -> dict[str, Any]:
    """Build one corpus row matching the shadow-joined schema from the briefing."""
    return {
        "corpus_id": corpus_id,
        "input": {
            "task_description": "synthetic",
            "file_paths": [],
            "agent_mentions": [],
            "tool_mentions": [],
            "command_prefix": None,
            "domain": domain,
            "posture": posture,
            "confidence": confidence,
            "area_span": area_span,
        },
        "output": {},
        "matcher_version": "6d5f416",
        "shadow": {
            "domain": domain,
            "posture": posture,
            "confidence": confidence,
            "area_span": area_span,
            "live_decision": live_decision,
            "live_agent": live_agent,
            "live_confidence": live_confidence,
            "live_disposition_source": "decide",
            "shadow_decision": shadow_decision,
            "shadow_agent": shadow_agent,
            "shadow_confidence": shadow_confidence,
            "shadow_disposition_source": shadow_disposition_source,
            "gated_agent_names": gated_agent_names,
            "posture_preferred": None,
            "posture_routed": posture_routed,
            "branch": None,
            "lexical_agreement": None,
            "posture_veto_reason": None,
            "agreement": shadow_agent == live_agent,
        },
    }


def _gold(
    corpus_id: int,
    gold_agent: str = "code-writer",
    domain: str = "code",
    posture: str = "build",
    is_any: bool = False,
    area_span: int = 1,
) -> GoldLabel:
    """Build one GoldLabel for the gold map."""
    return GoldLabel(
        corpus_id=corpus_id,
        domain=domain,
        posture=posture,
        gold_agent=gold_agent,
        is_any=is_any,
        area_span=area_span,
    )


def _gold_map(*labels: GoldLabel) -> dict[int, GoldLabel]:
    """Build a corpus_id -> GoldLabel dict."""
    return {label.corpus_id: label for label in labels}


_VALID_STATUSES = {"PASS", "FAIL", "INSUFFICIENT_DATA"}


# ---------------------------------------------------------------------------
# Structural contract
# ---------------------------------------------------------------------------


class TestKCVerdictShape:
    """Every compute_kcN returns a KCVerdict with a valid status."""

    def test_verdict_has_kc_status_metrics(self) -> None:
        rows = [_row(1)]
        gold = _gold_map(_gold(1))
        verdict = compute_kc1(rows, gold)
        assert isinstance(verdict, KCVerdict)
        assert hasattr(verdict, "kc")
        assert hasattr(verdict, "status")
        assert hasattr(verdict, "metrics")
        assert verdict.status in _VALID_STATUSES


# ---------------------------------------------------------------------------
# KC-1 (RC, two clauses) — whole sample
# ---------------------------------------------------------------------------


class TestKC1:
    """KC-1: PASS iff shadow_rc >= 0.6891 AND shadow_rc >= lexical_rc + 0.20."""

    def test_pass_when_both_clauses_hold(self) -> None:
        """shadow_rc 1.0, lexical_rc 0.6 -> clause(i) and clause(ii) both hold."""
        rows = [
            _row(1, shadow_agent="code-writer", live_agent="code-writer"),
            _row(2, shadow_agent="code-writer", live_agent="code-writer"),
            _row(3, shadow_agent="code-writer", live_agent="code-writer"),
            _row(4, shadow_agent="code-writer", live_agent="ops"),
            _row(5, shadow_agent="code-writer", live_agent="ops"),
        ]
        gold = _gold_map(*[_gold(i, "code-writer") for i in range(1, 6)])
        verdict = compute_kc1(rows, gold)
        assert verdict.metrics["shadow_rc"] == 1.0
        assert verdict.metrics["lexical_rc"] == 0.6
        assert verdict.status == "PASS"

    def test_fail_when_only_floor_clause_i_fails(self) -> None:
        """shadow_rc 0.5 (< 0.6891) but margin holds -> FAIL on clause(i) alone.

        shadow_rc 0.5, lexical_rc 0.0: clause(ii) 0.5 >= 0.0 + 0.20 holds, so the
        FAIL is driven purely by clause(i) 0.5 >= 0.6891 failing.
        """
        rows = [
            _row(1, shadow_agent="code-writer", live_agent="ops"),
            _row(2, shadow_agent="code-writer", live_agent="ops"),
            _row(3, shadow_agent="ops", live_agent="ops"),
            _row(4, shadow_agent="ops", live_agent="ops"),
        ]
        gold = _gold_map(*[_gold(i, "code-writer") for i in range(1, 5)])
        verdict = compute_kc1(rows, gold)
        assert verdict.metrics["shadow_rc"] == 0.5
        assert verdict.metrics["lexical_rc"] == 0.0
        assert verdict.status == "FAIL"

    def test_fail_when_only_margin_clause_ii_fails(self) -> None:
        """shadow_rc 0.8, lexical_rc 0.8 -> FAIL on clause(ii) alone.

        clause(i) 0.8 >= 0.6891 holds; clause(ii) 0.8 >= 0.8 + 0.20 = 1.0 fails.
        """
        rows = [
            _row(1, shadow_agent="code-writer", live_agent="code-writer"),
            _row(2, shadow_agent="code-writer", live_agent="code-writer"),
            _row(3, shadow_agent="code-writer", live_agent="code-writer"),
            _row(4, shadow_agent="code-writer", live_agent="code-writer"),
            _row(5, shadow_agent="ops", live_agent="ops"),
        ]
        gold = _gold_map(*[_gold(i, "code-writer") for i in range(1, 6)])
        verdict = compute_kc1(rows, gold)
        assert verdict.metrics["shadow_rc"] == 0.8
        assert verdict.metrics["lexical_rc"] == 0.8
        assert verdict.status == "FAIL"

    def test_self_handle_shadow_decision_credits_shadow_rc(self) -> None:
        """decision='self_handle' + gold='self_handle' counts correct for shadow RC.

        Proves the adapter passes the self_handle normalization through to the
        reused metric_routing_correctness kernel; the single correct-abstain row
        yields shadow_rc 1.0.
        """
        rows = [
            _row(
                1,
                shadow_decision="self_handle",
                shadow_agent=None,
                live_decision="self_handle",
                live_agent=None,
            )
        ]
        gold = _gold_map(_gold(1, gold_agent="self_handle"))
        verdict = compute_kc1(rows, gold)
        assert verdict.metrics["shadow_rc"] == 1.0


# ---------------------------------------------------------------------------
# KC-2 (CW) — HARD BLOCK
# ---------------------------------------------------------------------------


class TestKC2:
    """KC-2: PASS iff shadow_cw <= 0.2558."""

    def test_anchor_constant_is_exactly_0_2558(self) -> None:
        """The module-level lexical-CW anchor constant is exactly 0.2558."""
        assert KC2_LEXICAL_CW_ANCHOR == 0.2558

    def test_pass_when_cw_at_or_below_anchor(self) -> None:
        """shadow_cw 0.2 (1 wrong of 5 delegates) <= 0.2558 -> PASS."""
        rows = [
            _row(1, shadow_agent="code-writer"),
            _row(2, shadow_agent="code-writer"),
            _row(3, shadow_agent="code-writer"),
            _row(4, shadow_agent="code-writer"),
            _row(5, shadow_agent="ops"),  # wrong delegate
        ]
        gold = _gold_map(*[_gold(i, "code-writer") for i in range(1, 6)])
        verdict = compute_kc2(rows, gold)
        assert verdict.metrics["shadow_cw"] == 0.2
        assert verdict.status == "PASS"

    def test_fail_when_cw_above_anchor(self) -> None:
        """shadow_cw 0.5 (2 wrong of 4 delegates) > 0.2558 -> FAIL."""
        rows = [
            _row(1, shadow_agent="code-writer"),
            _row(2, shadow_agent="code-writer"),
            _row(3, shadow_agent="ops"),  # wrong
            _row(4, shadow_agent="ops"),  # wrong
        ]
        gold = _gold_map(*[_gold(i, "code-writer") for i in range(1, 5)])
        verdict = compute_kc2(rows, gold)
        assert verdict.metrics["shadow_cw"] == 0.5
        assert verdict.status == "FAIL"

    def test_reports_in_situ_lexical_cw(self) -> None:
        """The in-situ lexical CW is reported alongside shadow CW for transparency.

        Lexical arm: 2 wrong of 4 delegates -> lexical_cw 0.5. It is present in
        the verdict (reported), not gated on.
        """
        rows = [
            _row(1, shadow_agent="code-writer", live_agent="code-writer"),
            _row(2, shadow_agent="code-writer", live_agent="code-writer"),
            _row(3, shadow_agent="code-writer", live_agent="ops"),  # lexical wrong
            _row(4, shadow_agent="code-writer", live_agent="ops"),  # lexical wrong
        ]
        gold = _gold_map(*[_gold(i, "code-writer") for i in range(1, 5)])
        verdict = compute_kc2(rows, gold)
        assert "lexical_cw" in verdict.metrics
        assert verdict.metrics["lexical_cw"] == 0.5
        assert verdict.metrics["anchor"] == 0.2558


# ---------------------------------------------------------------------------
# KC-3 (decisiveness on the eligible set)
# ---------------------------------------------------------------------------


class TestKC3:
    """KC-3: numerator/eligible_n >= 0.55 over the gated x cell x high-conf set."""

    def test_pass_when_ratio_at_or_above_threshold(self) -> None:
        """3 of 4 eligible rows route decisively -> 0.75 >= 0.55 -> PASS."""
        rows = [
            _row(1, posture_routed=True),  # posture-routed clause
            _row(2, posture_routed=True),  # posture-routed clause
            _row(
                3,
                posture_routed=False,
                shadow_decision="delegate",
                gated_agent_names=["code-writer"],
            ),  # gated-delegate clause
            _row(
                4,
                posture_routed=False,
                shadow_decision="delegate",
                gated_agent_names=[],
            ),  # ungated-delegate -> excluded from numerator
        ]
        gold = _gold_map(*[_gold(i) for i in range(1, 5)])
        verdict = compute_kc3(rows, gold)
        assert verdict.metrics["eligible_n"] == 4
        assert verdict.metrics["numerator"] == 3
        assert verdict.status == "PASS"

    def test_fail_when_ratio_below_threshold(self) -> None:
        """1 of 4 eligible rows route decisively -> 0.25 < 0.55 -> FAIL."""
        rows = [
            _row(1, posture_routed=True),  # counts
            _row(
                2,
                posture_routed=False,
                shadow_decision="delegate",
                gated_agent_names=[],
            ),  # ungated-delegate -> excluded
            _row(
                3,
                posture_routed=False,
                shadow_decision="delegate",
                gated_agent_names=[],
            ),  # excluded
            _row(
                4,
                posture_routed=False,
                shadow_decision="delegate",
                gated_agent_names=[],
            ),  # excluded
        ]
        gold = _gold_map(*[_gold(i) for i in range(1, 5)])
        verdict = compute_kc3(rows, gold)
        assert verdict.metrics["eligible_n"] == 4
        assert verdict.metrics["numerator"] == 1
        assert verdict.status == "FAIL"

    def test_insufficient_data_when_eligible_set_empty(self) -> None:
        """Zero eligible rows -> INSUFFICIENT_DATA, never a vacuous PASS.

        All rows are ungated (caller domain 'is_any'), so none enter the
        eligible set even though each would otherwise route decisively.
        """
        rows = [
            _row(1, domain="is_any", posture_routed=True),
            _row(2, domain="is_any", posture_routed=True),
        ]
        gold = _gold_map(_gold(1, domain="code"), _gold(2, domain="code"))
        verdict = compute_kc3(rows, gold)
        assert verdict.metrics["eligible_n"] == 0
        assert verdict.status == "INSUFFICIENT_DATA"

    def test_three_way_numerator_classification(self) -> None:
        """posture-routed and gated-delegate count; ungated-delegate does not.

        Three rows in one eligible set:
          A: posture_routed=True                       -> counts
          B: posture_routed=False, delegate, gated=[x] -> counts
          C: posture_routed=False, delegate, gated=[]  -> excluded
        Exactly A and B (numerator 2) must be counted.
        """
        rows = [
            _row(1, posture_routed=True),
            _row(
                2,
                posture_routed=False,
                shadow_decision="delegate",
                gated_agent_names=["code-writer"],
            ),
            _row(
                3,
                posture_routed=False,
                shadow_decision="delegate",
                gated_agent_names=[],
            ),
        ]
        gold = _gold_map(*[_gold(i) for i in range(1, 4)])
        verdict = compute_kc3(rows, gold)
        assert verdict.metrics["eligible_n"] == 3
        assert verdict.metrics["numerator"] == 2

    def test_ungated_delegate_none_gated_names_excluded(self) -> None:
        """gated_agent_names=None (not just []) is still an ungated-delegate.

        Guards against a `gated_agent_names is not None` truthiness slip: a None
        value must be treated identically to [] and excluded from the numerator.
        """
        rows = [
            _row(1, posture_routed=True),  # counts
            _row(
                2,
                posture_routed=False,
                shadow_decision="delegate",
                gated_agent_names=None,
            ),  # ungated-delegate (None) -> excluded
        ]
        gold = _gold_map(_gold(1), _gold(2))
        verdict = compute_kc3(rows, gold)
        assert verdict.metrics["eligible_n"] == 2
        assert verdict.metrics["numerator"] == 1

    def test_disposition_source_string_does_not_classify_numerator(self) -> None:
        """shadow_disposition_source=='posture_routed' string must NOT count.

        The exact bug the spec warns against: classification must key off the
        posture_routed BOOL, not the shadow_disposition_source STRING. Here the
        bool is False and the row is an ungated delegate, so it is excluded even
        though the disposition-source string reads 'posture_routed'.
        """
        rows = [
            _row(
                1,
                posture_routed=False,
                shadow_disposition_source="posture_routed",
                shadow_decision="delegate",
                gated_agent_names=[],
            ),
        ]
        gold = _gold_map(_gold(1))
        verdict = compute_kc3(rows, gold)
        assert verdict.metrics["eligible_n"] == 1
        assert verdict.metrics["numerator"] == 0
        assert verdict.status == "FAIL"

    def test_eligibility_boundary_excludes_non_qualifying_rows(self) -> None:
        """Only the gated x cell-exists x high-confidence row is eligible.

        Four rows, three excluded for distinct reasons:
          1: eligible baseline (domain=code, posture=build, confidence=high)
          2: ungated       (domain='is_any')          -> excluded
          3: no cell        (posture=None -> cell_map_lookup None) -> excluded
          4: not high-conf  (confidence='medium')     -> excluded
        Only row 1 survives -> eligible_n == 1.
        """
        rows = [
            _row(1, domain="code", posture="build", confidence="high", posture_routed=True),
            _row(2, domain="is_any", posture="build", confidence="high", posture_routed=True),
            _row(3, domain="code", posture=None, confidence="high", posture_routed=True),
            _row(4, domain="code", posture="build", confidence="medium", posture_routed=True),
        ]
        gold = _gold_map(*[_gold(i) for i in range(1, 5)])
        verdict = compute_kc3(rows, gold)
        assert verdict.metrics["eligible_n"] == 1


# ---------------------------------------------------------------------------
# KC-4 (routing-neutrality, structural method)
# ---------------------------------------------------------------------------


class TestKC4:
    """KC-4: among mislabel rows (caller in {is_any, project_meta}, gold differs),
    0 posture-routed route changes -> PASS."""

    def test_pass_when_no_route_changes(self) -> None:
        """Eligible mislabel rows with posture_routed=False -> violations 0 -> PASS.

        Row 3 (domain='code') is NOT in {is_any, project_meta} even though it is
        posture_routed=True, so it is excluded and does not count as a violation.
        """
        rows = [
            _row(1, domain="is_any", posture_routed=False),
            _row(2, domain="project_meta", posture_routed=False),
            _row(3, domain="code", posture_routed=True),  # not eligible -> no violation
        ]
        gold = _gold_map(
            _gold(1, domain="code"),
            _gold(2, domain="code"),
            _gold(3, domain="data"),
        )
        verdict = compute_kc4(rows, gold)
        assert verdict.metrics["eligible_n"] == 2
        assert verdict.metrics["violations"] == 0
        assert verdict.status == "PASS"

    def test_fail_when_eligible_row_posture_routed(self) -> None:
        """An eligible mislabel row that posture_routed -> a violation -> FAIL."""
        rows = [
            _row(1, domain="project_meta", posture_routed=True),
        ]
        gold = _gold_map(_gold(1, domain="code"))
        verdict = compute_kc4(rows, gold)
        assert verdict.metrics["eligible_n"] == 1
        assert verdict.metrics["violations"] == 1
        assert verdict.status == "FAIL"

    def test_insufficient_data_when_eligible_set_empty(self) -> None:
        """No mislabel rows -> INSUFFICIENT_DATA, never a vacuous PASS.

        Row 1: caller project_meta but gold also project_meta (no mislabel).
        Row 2: caller 'code' (not in {is_any, project_meta}).
        The eligible set is empty; KC-4 must NOT read as PASS on no evidence.
        """
        rows = [
            _row(1, domain="project_meta", posture_routed=True),
            _row(2, domain="code", posture_routed=True),
        ]
        gold = _gold_map(
            _gold(1, domain="project_meta"),
            _gold(2, domain="code"),
        )
        verdict = compute_kc4(rows, gold)
        assert verdict.metrics["eligible_n"] == 0
        assert verdict.status == "INSUFFICIENT_DATA"


# ---------------------------------------------------------------------------
# KC-5 (infra_deploy no regression)
# ---------------------------------------------------------------------------


class TestKC5:
    """KC-5: shadow RC on the gold.domain=='infra_deploy' slice >= 0.600."""

    def test_pass_when_slice_rc_above_floor(self) -> None:
        """infra slice shadow_rc 0.8 (4 of 5) >= 0.600 -> PASS.

        A non-infra row (corpus_id 6) the shadow gets wrong must NOT affect the
        slice RC — it is outside the infra_deploy slice.
        """
        rows = [
            _row(1, shadow_agent="devops"),
            _row(2, shadow_agent="devops"),
            _row(3, shadow_agent="devops"),
            _row(4, shadow_agent="devops"),
            _row(5, shadow_agent="ops"),  # wrong within slice
            _row(6, shadow_agent="ops"),  # non-infra, wrong, must be ignored
        ]
        gold = _gold_map(
            *[_gold(i, gold_agent="devops", domain="infra_deploy") for i in range(1, 6)],
            _gold(6, gold_agent="code-writer", domain="code"),
        )
        verdict = compute_kc5(rows, gold)
        assert verdict.metrics["slice_n"] == 5
        assert verdict.metrics["shadow_rc"] == 0.8
        assert verdict.status == "PASS"

    def test_pass_at_exactly_point_six_boundary(self) -> None:
        """infra slice shadow_rc exactly 0.60 (3 of 5) -> PASS (pins >= not >).

        The go/no-go floor is `>= 0.600`; 3/5 rounds to a clean 0.6, so a `>`
        implementation would wrongly FAIL this fixture.
        """
        rows = [
            _row(1, shadow_agent="devops"),
            _row(2, shadow_agent="devops"),
            _row(3, shadow_agent="devops"),
            _row(4, shadow_agent="ops"),  # wrong
            _row(5, shadow_agent="ops"),  # wrong
        ]
        gold = _gold_map(
            *[_gold(i, gold_agent="devops", domain="infra_deploy") for i in range(1, 6)]
        )
        verdict = compute_kc5(rows, gold)
        assert verdict.metrics["slice_n"] == 5
        assert verdict.metrics["shadow_rc"] == 0.6
        assert verdict.status == "PASS"

    def test_fail_when_slice_rc_below_floor(self) -> None:
        """infra slice shadow_rc 0.4 (2 of 5) < 0.600 -> FAIL."""
        rows = [
            _row(1, shadow_agent="devops"),
            _row(2, shadow_agent="devops"),
            _row(3, shadow_agent="ops"),  # wrong
            _row(4, shadow_agent="ops"),  # wrong
            _row(5, shadow_agent="ops"),  # wrong
        ]
        gold = _gold_map(
            *[_gold(i, gold_agent="devops", domain="infra_deploy") for i in range(1, 6)]
        )
        verdict = compute_kc5(rows, gold)
        assert verdict.metrics["slice_n"] == 5
        assert verdict.metrics["shadow_rc"] == 0.4
        assert verdict.status == "FAIL"

    def test_insufficient_data_when_slice_empty(self) -> None:
        """No infra_deploy rows -> INSUFFICIENT_DATA, never a vacuous PASS/FAIL."""
        rows = [
            _row(1, shadow_agent="code-writer"),
            _row(2, shadow_agent="code-writer"),
        ]
        gold = _gold_map(
            _gold(1, gold_agent="code-writer", domain="code"),
            _gold(2, gold_agent="code-writer", domain="code"),
        )
        verdict = compute_kc5(rows, gold)
        assert verdict.metrics["slice_n"] == 0
        assert verdict.status == "INSUFFICIENT_DATA"
