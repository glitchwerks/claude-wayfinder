"""Tests for scripts.corpus.eval._metrics.

Six metrics per spec §13.3, with hand-computed expected values
on tiny synthetic inputs.

RED — written before implementation.
"""

from __future__ import annotations

from typing import Any

from scripts.corpus.eval._metrics import (
    MetricsResult,
    compute_all_metrics,
    metric_braked_candidate_quality,
    metric_confident_wrong_rate,
    metric_error_correlation,
    metric_error_severity,
    metric_false_default_build,
    metric_tier_c_decisiveness,
)
from scripts.corpus.eval._reader import GoldLabel
from scripts.corpus.eval._systems import SystemResult

# ---------------------------------------------------------------------------
# Helpers to build minimal synthetic inputs
# ---------------------------------------------------------------------------


def _make_result(
    corpus_id: int,
    decision: str = "delegate",
    agent: str | None = "code-writer",
    confidence: float = 0.9,
    extras: dict[str, Any] | None = None,
) -> SystemResult:
    """Build a minimal SystemResult for testing."""
    return SystemResult(
        corpus_id=corpus_id,
        decision=decision,
        agent=agent,
        confidence=confidence,
        extras=extras or {},
    )


def _make_label(
    corpus_id: int,
    gold_agent: str = "code-writer",
    domain: str = "code",
    posture: str = "build",
    is_any: bool = False,
) -> GoldLabel:
    """Build a minimal GoldLabel for testing."""
    return GoldLabel(
        corpus_id=corpus_id,
        domain=domain,
        posture=posture,
        gold_agent=gold_agent,
        is_any=is_any,
    )


# ---------------------------------------------------------------------------
# Metric 6: confident_wrong_rate
# ---------------------------------------------------------------------------


class TestMetricConfidentWrong:
    """Tests for metric_confident_wrong_rate (§13.3 metric 6)."""

    def test_zero_when_all_correct(self) -> None:
        """Rate is 0.0 when all delegate decisions match gold."""
        results = [
            _make_result(1, "delegate", "code-writer", 0.9),
            _make_result(2, "delegate", "ops", 0.9),
        ]
        labels = {
            1: _make_label(1, "code-writer"),
            2: _make_label(2, "ops"),
        }
        rate = metric_confident_wrong_rate(results, labels)
        assert rate == 0.0

    def test_one_when_all_wrong(self) -> None:
        """Rate is 1.0 when all delegate decisions are wrong."""
        results = [
            _make_result(1, "delegate", "ops", 0.9),
            _make_result(2, "delegate", "code-writer", 0.9),
        ]
        labels = {
            1: _make_label(1, "code-writer"),
            2: _make_label(2, "ops"),
        }
        rate = metric_confident_wrong_rate(results, labels)
        assert rate == 1.0

    def test_half_when_one_of_two_wrong(self) -> None:
        """Rate is 0.5 when one of two delegate decisions is wrong."""
        results = [
            _make_result(1, "delegate", "code-writer", 0.9),
            _make_result(2, "delegate", "code-writer", 0.9),
        ]
        labels = {
            1: _make_label(1, "code-writer"),
            2: _make_label(2, "ops"),
        }
        rate = metric_confident_wrong_rate(results, labels)
        assert rate == 0.5

    def test_skips_non_delegate_decisions(self) -> None:
        """Only delegate decisions count; advisory/advisory are excluded."""
        results = [
            _make_result(1, "advisory", None, 0.5),
            _make_result(2, "delegate", "code-writer", 0.9),
        ]
        labels = {
            1: _make_label(1, "ops"),
            2: _make_label(2, "code-writer"),
        }
        rate = metric_confident_wrong_rate(results, labels)
        assert rate == 0.0

    def test_nan_when_no_delegate_decisions(self) -> None:
        """Returns float('nan') when no delegate decisions to evaluate."""
        results = [_make_result(1, "advisory", None, 0.5)]
        labels = {1: _make_label(1, "ops")}
        rate = metric_confident_wrong_rate(results, labels)
        import math

        assert math.isnan(rate)

    def test_skips_entries_without_labels(self) -> None:
        """Entries without gold labels are excluded from the rate."""
        results = [
            _make_result(1, "delegate", "code-writer", 0.9),
            _make_result(2, "delegate", "ops", 0.9),  # no label
        ]
        labels = {1: _make_label(1, "code-writer")}
        rate = metric_confident_wrong_rate(results, labels)
        assert rate == 0.0


# ---------------------------------------------------------------------------
# Metric 2: error_severity (R4 cell-distance)
# ---------------------------------------------------------------------------


class TestMetricErrorSeverity:
    """Tests for metric_error_severity (§13.3 metric 2, R4)."""

    def test_no_errors_returns_all_zeros(self) -> None:
        """When all predictions are correct, severity counts are all zero."""
        results = [_make_result(1, "delegate", "code-writer", 0.9)]
        labels = {1: _make_label(1, "code-writer", "code", "build")}
        severity = metric_error_severity(results, labels)
        assert severity["adjacent"] == 0
        assert severity["cross_posture"] == 0
        assert severity["cross_domain"] == 0

    def test_adjacent_posture_miss(self) -> None:
        """assess↔critique is classified as adjacent (§12.3 R4)."""
        # P9: code-reviewer (assess) predicted when inquisitor (critique) is gold
        results = [_make_result(9, "delegate", "code-reviewer", 0.9)]
        labels = {9: _make_label(9, "inquisitor", "code", "critique")}
        severity = metric_error_severity(results, labels)
        assert severity["adjacent"] == 1
        assert severity["cross_posture"] == 0
        assert severity["cross_domain"] == 0

    def test_cross_posture_miss(self) -> None:
        """diagnose vs build is a cross-posture miss."""
        results = [_make_result(1, "delegate", "code-writer", 0.9)]
        labels = {1: _make_label(1, "investigator", "code", "diagnose")}
        severity = metric_error_severity(results, labels)
        assert severity["cross_posture"] == 1
        assert severity["adjacent"] == 0

    def test_skips_non_delegate_decisions(self) -> None:
        """Advisory and other non-delegate decisions are not scored."""
        results = [_make_result(1, "advisory", None, 0.5)]
        labels = {1: _make_label(1, "investigator", "code", "diagnose")}
        severity = metric_error_severity(results, labels)
        assert sum(severity.values()) == 0


# ---------------------------------------------------------------------------
# Metric 3: tier_c_decisiveness
# ---------------------------------------------------------------------------


class TestMetricTierCDecisiveness:
    """Tests for metric_tier_c_decisiveness (§13.3 metric 3, §10.3 g4)."""

    def test_zero_when_no_tier_c_fired(self) -> None:
        """Rate is 0.0 when no extractor result had tier_c_fired=True."""
        results = [
            _make_result(1, extras={"tier_c_fired": False}),
            _make_result(2, extras={"tier_c_fired": False}),
        ]
        rate = metric_tier_c_decisiveness(results)
        assert rate == 0.0

    def test_one_when_all_tier_c_fired(self) -> None:
        """Rate is 1.0 when all results had tier_c_fired=True."""
        results = [
            _make_result(1, extras={"tier_c_fired": True}),
            _make_result(2, extras={"tier_c_fired": True}),
        ]
        rate = metric_tier_c_decisiveness(results)
        assert rate == 1.0

    def test_half_when_one_of_two(self) -> None:
        """Rate is 0.5 when one of two results had tier_c_fired=True."""
        results = [
            _make_result(1, extras={"tier_c_fired": True}),
            _make_result(2, extras={"tier_c_fired": False}),
        ]
        rate = metric_tier_c_decisiveness(results)
        assert rate == 0.5

    def test_skips_missing_tier_c_key(self) -> None:
        """Results without tier_c_fired key in extras are skipped."""
        results = [
            _make_result(1, extras={}),  # no key
            _make_result(2, extras={"tier_c_fired": True}),
        ]
        rate = metric_tier_c_decisiveness(results)
        # 1 of 1 extractor results (only r2 has the key) → 1.0
        assert rate == 1.0


# ---------------------------------------------------------------------------
# Metric 4: false_default_build
# ---------------------------------------------------------------------------


class TestMetricFalseDefaultBuild:
    """Tests for metric_false_default_build (§13.3 metric 4, §10.4)."""

    def test_zero_when_all_posture_fires(self) -> None:
        """Rate is 0.0 when all results have a non-empty postures list."""
        results = [
            _make_result(1, "delegate", "code-writer",
                         extras={"postures": ["build"]}),
            _make_result(2, "delegate", "ops",
                         extras={"postures": ["operate"]}),
        ]
        labels = {
            1: _make_label(1, "code-writer"),
            2: _make_label(2, "ops"),
        }
        rate = metric_false_default_build(results, labels)
        assert rate == 0.0

    def test_counts_default_build_wrong(self) -> None:
        """A result with empty postures that is wrong counts as false-default-build."""
        results = [
            _make_result(1, "delegate", "code-writer",
                         extras={"postures": []}),
        ]
        labels = {1: _make_label(1, "investigator")}
        rate = metric_false_default_build(results, labels)
        # 1 wrong default-build / 1 total default-build → 1.0
        assert rate == 1.0

    def test_zero_when_no_default_build_cases(self) -> None:
        """Returns 0.0 when no default-build cases (all extractors fired)."""
        results = [
            _make_result(1, extras={"postures": ["operate"]}),
        ]
        labels = {1: _make_label(1, "ops")}
        rate = metric_false_default_build(results, labels)
        assert rate == 0.0

    def test_nan_when_no_postures_key_at_all(self) -> None:
        """Returns 0.0 when results have no postures key (non-extractor sys)."""
        results = [_make_result(1, extras={})]
        labels = {1: _make_label(1, "ops")}
        rate = metric_false_default_build(results, labels)
        # No extractor-system results → 0.0 (no default-build events possible)
        assert rate == 0.0


# ---------------------------------------------------------------------------
# Metric 5: braked_candidate_quality
# ---------------------------------------------------------------------------


class TestMetricBrakedCandidateQuality:
    """Tests for metric_braked_candidate_quality (§13.3 metric 5)."""

    def test_one_when_gold_in_alternatives(self) -> None:
        """Rate is 1.0 when gold agent appears in advisory alternatives."""
        results = [
            _make_result(
                3,
                decision="advisory",
                agent="auditor",
                extras={
                    "postures": ["verify"],
                    "tier_c_fired": True,
                    "braked": True,
                    "alternatives": ["investigator", "auditor"],
                },
            )
        ]
        labels = {3: _make_label(3, "investigator")}
        rate = metric_braked_candidate_quality(results, labels)
        assert rate == 1.0

    def test_zero_when_gold_not_in_alternatives(self) -> None:
        """Rate is 0.0 when gold agent not in alternatives."""
        results = [
            _make_result(
                3,
                decision="advisory",
                agent="auditor",
                extras={
                    "postures": ["verify"],
                    "tier_c_fired": True,
                    "braked": True,
                    "alternatives": ["code-writer"],
                },
            )
        ]
        labels = {3: _make_label(3, "investigator")}
        rate = metric_braked_candidate_quality(results, labels)
        assert rate == 0.0

    def test_nan_when_no_braked_outcomes(self) -> None:
        """Returns nan when no braked outcomes exist."""
        results = [_make_result(1, "delegate", "code-writer")]
        labels = {1: _make_label(1, "code-writer")}
        rate = metric_braked_candidate_quality(results, labels)
        import math

        assert math.isnan(rate)


# ---------------------------------------------------------------------------
# Metric 1: error_correlation (§8.4, the decisive metric)
# ---------------------------------------------------------------------------


class TestMetricErrorCorrelation:
    """Tests for metric_error_correlation (§8.4, §13.3 metric 1)."""

    def test_requires_two_system_lists(self) -> None:
        """error_correlation takes two system result lists and gold labels."""
        sys_a = [_make_result(1, "delegate", "code-writer")]
        sys_b = [_make_result(1, "delegate", "code-writer")]
        labels = {1: _make_label(1, "code-writer")}
        corr = metric_error_correlation(sys_a, sys_b, labels)
        assert isinstance(corr, float)

    def test_zero_when_no_errors(self) -> None:
        """Correlation is 0.0 when neither system makes errors."""
        sys_a = [_make_result(1, "delegate", "code-writer")]
        sys_b = [_make_result(1, "delegate", "code-writer")]
        labels = {1: _make_label(1, "code-writer")}
        corr = metric_error_correlation(sys_a, sys_b, labels)
        assert corr == 0.0

    def test_high_when_both_always_wrong_together(self) -> None:
        """Correlation is high when both systems err on same entries."""
        sys_a = [
            _make_result(1, "delegate", "ops"),  # wrong
            _make_result(2, "delegate", "code-writer"),  # correct
        ]
        sys_b = [
            _make_result(1, "delegate", "ops"),  # wrong (same)
            _make_result(2, "delegate", "code-writer"),  # correct
        ]
        labels = {
            1: _make_label(1, "code-writer"),
            2: _make_label(2, "code-writer"),
        }
        corr = metric_error_correlation(sys_a, sys_b, labels)
        # Both wrong on same entry: high correlation
        assert corr > 0.0

    def test_nan_when_insufficient_delegate_overlap(self) -> None:
        """Returns nan when fewer than 2 common delegate entries."""
        sys_a = [_make_result(1, "advisory", None)]
        sys_b = [_make_result(1, "advisory", None)]
        labels = {1: _make_label(1, "code-writer")}
        import math

        corr = metric_error_correlation(sys_a, sys_b, labels)
        assert math.isnan(corr)


# ---------------------------------------------------------------------------
# compute_all_metrics — integration
# ---------------------------------------------------------------------------


class TestComputeAllMetrics:
    """Tests for compute_all_metrics() integration."""

    def test_returns_metrics_result(self) -> None:
        """compute_all_metrics returns a MetricsResult."""
        sys_a = [_make_result(1, "delegate", "code-writer")]
        sys_b = [_make_result(1, "delegate", "code-writer")]
        sys_c = [_make_result(1, "delegate", "code-writer",
                              extras={"postures": ["build"],
                                      "tier_c_fired": False})]
        sys_d = [_make_result(1, "delegate", "code-writer",
                              extras={"postures": ["build"],
                                      "tier_c_fired": False})]
        labels = {1: _make_label(1, "code-writer", "code", "build")}

        result = compute_all_metrics(
            lexical=sys_a,
            encoder=sys_b,
            extractors=sys_c,
            composed=sys_d,
            labels=labels,
        )
        assert isinstance(result, MetricsResult)

    def test_metrics_result_has_all_six_fields(self) -> None:
        """MetricsResult has all six metric fields."""
        r = MetricsResult(
            error_correlation=0.0,
            error_severity={"adjacent": 0, "cross_posture": 0, "cross_domain": 0},
            tier_c_decisiveness=0.0,
            false_default_build_rate=0.0,
            braked_candidate_quality=0.0,
            confident_wrong_rate=0.0,
        )
        assert hasattr(r, "error_correlation")
        assert hasattr(r, "error_severity")
        assert hasattr(r, "tier_c_decisiveness")
        assert hasattr(r, "false_default_build_rate")
        assert hasattr(r, "braked_candidate_quality")
        assert hasattr(r, "confident_wrong_rate")
