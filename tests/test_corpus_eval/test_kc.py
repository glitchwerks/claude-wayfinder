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

import pytest

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


class TestKC3MissingOptionalKeys:
    """Issue #493: `input.confidence` may be an ABSENT key, not just null.

    ``skills/dispatch/SKILL.md`` documents ``confidence`` as one of the four
    optional caller-supplied labels: "omit or pass null for fields that are
    not applicable." Real shadow-corpus rows exercise the "omit" form, and a
    diagnostic run against real telemetry (issue #493 body) confirmed
    ``compute_kc3`` crashes with an uncaught ``KeyError`` on such rows via
    direct ``caller_input["confidence"]`` indexing. An absent key and an
    explicit null must be treated identically -- both mean "no confidence
    label" per the contract.
    """

    def test_missing_confidence_key_equals_null_confidence(self) -> None:
        """A row with no "confidence" key must verdict-match confidence=None.

        Both rows are otherwise identical (posture_routed=True, so each
        would count toward the numerator if eligible). Neither is eligible
        under KC-3's `confidence == "high"` clause, so both verdicts must
        agree: eligible_n excludes them and the KC computation must not
        raise. Today, evaluating `row_absent` raises KeyError before this
        assertion is ever reached.
        """
        row_null = _row(1, confidence=None, posture_routed=True)
        row_absent = _row(2, confidence=None, posture_routed=True)
        del row_absent["input"]["confidence"]
        gold = _gold_map(_gold(1), _gold(2))

        verdict_null = compute_kc3([row_null], gold)
        verdict_absent = compute_kc3([row_absent], gold)

        assert verdict_absent.metrics == verdict_null.metrics
        assert verdict_absent.status == verdict_null.status

    def test_missing_posture_key_equals_null_posture(self) -> None:
        """A row with no "posture" key must verdict-match posture=None.

        Both rows are otherwise identical (posture_routed=True, so each
        would count toward the numerator if eligible). Neither is eligible
        under KC-3's cell-lookup clause (posture=None -> no cell), so both
        verdicts must agree: eligible_n excludes them and the KC computation
        must not raise. Today, evaluating `row_absent` raises KeyError
        before this assertion is ever reached.
        """
        row_null = _row(1, posture=None, posture_routed=True)
        row_absent = _row(2, posture=None, posture_routed=True)
        del row_absent["input"]["posture"]
        gold = _gold_map(_gold(1), _gold(2))

        verdict_null = compute_kc3([row_null], gold)
        verdict_absent = compute_kc3([row_absent], gold)

        assert verdict_absent.metrics == verdict_null.metrics
        assert verdict_absent.status == verdict_null.status


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


class TestKC4MissingOptionalKeys:
    """Issue #493: `input.domain` may be an ABSENT key, not just null.

    Same contract gap as ``TestKC3MissingOptionalKeys``, applied to
    ``domain``. A diagnostic run against real telemetry (issue #493 body)
    confirmed ``compute_kc4`` crashes with an uncaught ``KeyError`` via
    direct ``caller_input["domain"]`` indexing on rows that omit the key
    entirely.
    """

    def test_missing_domain_key_equals_null_domain(self) -> None:
        """A row with no "domain" key must verdict-match domain=None.

        `domain=None` means "no domain gate" per the dispatch contract, so
        neither row falls in KC-4's `{is_any, project_meta}` eligible set
        and both verdicts must agree. Today, evaluating `row_absent` raises
        KeyError before this assertion is ever reached.
        """
        row_null = _row(1, domain=None, posture_routed=True)
        row_absent = _row(2, domain=None, posture_routed=True)
        del row_absent["input"]["domain"]
        gold = _gold_map(
            _gold(1, domain="code"),
            _gold(2, domain="code"),
        )

        verdict_null = compute_kc4([row_null], gold)
        verdict_absent = compute_kc4([row_absent], gold)

        assert verdict_absent.metrics == verdict_null.metrics
        assert verdict_absent.status == verdict_null.status


class TestKC4DomainInvariantPostureExemption:
    """Issue #503: mislabel rows only count as eligible when the preferred
    agent could ACTUALLY differ under the corrected (gold) domain versus
    the caller's domain, i.e. when
    ``cell_map_lookup(gold_domain, posture) != cell_map_lookup(caller_domain,
    posture)``.

    For postures whose only ``_CELL_MAP`` entry is the domain-agnostic
    ``("any", posture)`` fallback -- ``operate`` -> ``ops``, ``research`` ->
    ``researcher``, ``verify`` -> ``auditor``, and ``idea-critique`` ->
    ``approach-critic`` -- the preferred agent is the same for every domain
    (confirmed by calling ``cell_map_lookup`` directly across a spread of
    domains, since these fixtures must not rely on which domains happen to
    share a cell), so a mislabeled ``is_any``/``project_meta`` row can never
    actually change the route for these postures. The prior
    ``posture_routed``-only proxy counted such rows as violation-eligible
    regardless, which is a false positive that must be excluded from both
    ``eligible_n`` and ``violations``.

    ``build`` is used as the domain-*variant* control: ``project_meta`` is
    the one caller domain whose ``build`` cell (the self-handle sentinel)
    differs from every other domain's (``code-writer``) -- ``is_any``
    resolves to ``code-writer`` too, so it is NOT a valid variant fixture
    for ``build`` and must not be used as one.
    """

    @pytest.mark.parametrize("posture", ["operate", "research", "verify", "idea-critique"])
    def test_domain_invariant_posture_mislabel_excluded_from_eligible_set(
        self, posture: str
    ) -> None:
        """A mislabeled row for a domain-invariant posture is never eligible.

        The preferred agent for these postures cannot change regardless of
        domain (only an ``("any", posture)`` cell exists), so a mislabeled
        ``is_any`` row -- even one flagged ``posture_routed=True`` -- must
        not inflate ``eligible_n`` nor count toward ``violations``. With
        zero eligible rows the verdict must be INSUFFICIENT_DATA, never a
        FAIL driven by a proxy that could not have actually fired.
        """
        rows = [_row(1, domain="is_any", posture=posture, posture_routed=True)]
        gold = _gold_map(_gold(1, domain="code", posture=posture))
        verdict = compute_kc4(rows, gold)
        assert verdict.metrics["eligible_n"] == 0
        assert verdict.metrics["violations"] == 0
        assert verdict.status == "INSUFFICIENT_DATA"

    def test_domain_variant_build_posture_mislabel_remains_eligible(self) -> None:
        """A mislabel where the preferred agent genuinely differs stays eligible.

        ``build`` has a domain-specific cell for ``project_meta`` (the
        self-handle sentinel) that differs from ``code``'s (``code-writer``),
        so correcting the caller's ``project_meta`` mislabel to gold ``code``
        really could change the preferred agent. This row must remain
        eligible and, since it did not posture-route, count as compliant
        (PASS) -- the narrowed eligible-set filter must not sweep away
        genuinely eligible rows along with the domain-invariant ones.
        """
        rows = [
            _row(1, domain="project_meta", posture="build", posture_routed=False),
        ]
        gold = _gold_map(_gold(1, domain="code", posture="build"))
        verdict = compute_kc4(rows, gold)
        assert verdict.metrics["eligible_n"] == 1
        assert verdict.metrics["violations"] == 0
        assert verdict.status == "PASS"

    def test_domain_variant_build_posture_violation_still_flagged(self) -> None:
        """A genuinely-eligible mislabel row that DOES posture-route still FAILs.

        Companion to the PASS case above: with the real difference between
        ``project_meta`` (self-handle) and ``code`` (``code-writer``) for
        ``build``, a ``posture_routed=True`` row must still count as a
        violation after the fix -- narrowing the eligible set must not
        accidentally suppress real violations too.
        """
        rows = [
            _row(1, domain="project_meta", posture="build", posture_routed=True),
        ]
        gold = _gold_map(_gold(1, domain="code", posture="build"))
        verdict = compute_kc4(rows, gold)
        assert verdict.metrics["eligible_n"] == 1
        assert verdict.metrics["violations"] == 1
        assert verdict.status == "FAIL"

    def test_realistic_mixed_scenario_eligible_n_drops_when_invariant_excluded(
        self,
    ) -> None:
        """Issue #503 shape: is_any/project_meta + operate mislabels alongside
        one genuine build-posture mislabel.

        Four ``operate``-posture mislabel rows (domain-invariant: every
        domain resolves to ``ops``) sit alongside one ``build``-posture
        mislabel row with caller domain ``project_meta`` (domain-variant:
        ``project_meta``'s self-handle resolution differs from ``code``'s
        ``code-writer``). Only the build row may count -- the four operate
        rows must be excluded from both ``eligible_n`` and ``violations``
        regardless of their ``posture_routed`` flag, so ``eligible_n`` drops
        from 5 to 1.
        """
        rows = [
            _row(1, domain="is_any", posture="operate", posture_routed=True),
            _row(2, domain="is_any", posture="operate", posture_routed=False),
            _row(3, domain="project_meta", posture="operate", posture_routed=True),
            _row(4, domain="project_meta", posture="operate", posture_routed=False),
            _row(5, domain="project_meta", posture="build", posture_routed=True),
        ]
        gold = _gold_map(
            _gold(1, domain="code", posture="operate"),
            _gold(2, domain="code", posture="operate"),
            _gold(3, domain="code", posture="operate"),
            _gold(4, domain="code", posture="operate"),
            _gold(5, domain="code", posture="build"),
        )
        verdict = compute_kc4(rows, gold)
        assert verdict.metrics["eligible_n"] == 1
        assert verdict.metrics["violations"] == 1
        assert verdict.status == "FAIL"


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
