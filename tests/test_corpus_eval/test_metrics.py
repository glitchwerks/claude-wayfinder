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
    metric_routing_correctness,
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
        """Returns nan when results have no postures key (non-extractor sys).

        Fix 2: lexical/encoder rows have no 'postures' key in extras → the
        false-default-build metric is n/a (nan), not 0.0.  A 0.0 would
        erroneously suggest the system ran posture extractors and found zero
        default-build cases, which is misleading.  nan is the honest signal.
        """
        import math

        results = [_make_result(1, extras={})]
        labels = {1: _make_label(1, "ops")}
        rate = metric_false_default_build(results, labels)
        # No 'postures' key → metric is n/a (nan) for non-extractor systems
        assert math.isnan(rate), (
            f"metric_false_default_build must be nan when no result has "
            f"'postures' in extras (non-extractor row); got {rate}"
        )


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


# ---------------------------------------------------------------------------
# Fix 2: per-row metrics independence (CLI table rows compute own metrics)
# ---------------------------------------------------------------------------


class TestPerRowMetricsIndependence:
    """Each table row must compute metrics from ITS OWN sys_results.

    Fix 2 bug: the CLI loop passed ``extractors_r`` as the ``extractors``
    arg for every non-extractor row, so the lexical and encoder rows
    inherited the extractor system's tier_c_decisiveness, false_default_build,
    and braked_candidate_quality values.

    Correct behaviour:
    - lexical row: no ``postures``/``tier_c_fired`` in extras → tier_c,
      fdb, and brake metrics return 0.0/nan (no eligible results).
    - extractors row: has postures/tier_c_fired extras → computes for real.
    - composed row: has its own extras (runs extractors internally) → real.

    Two different extractor-like systems with different tier_c_fired
    distributions must NOT produce the same tier_c_decisiveness value
    when used as the primary row.
    """

    def test_lexical_row_tier_c_is_nan_not_inherited(self) -> None:
        """Lexical results (no tier_c_fired extras) → tier_c_decisiveness = nan.

        Fix 2: non-extractor rows lack the 'tier_c_fired' key in extras.
        The metric must return nan (displayed as n/a) rather than 0.0, which
        would falsely imply zero Tier-C events in an extractor-capable system.
        """
        import math

        # Lexical results have no 'tier_c_fired' key in extras
        lexical_r = [
            _make_result(1, extras={}),
            _make_result(2, extras={}),
        ]
        # tier_c_decisiveness computed from lexical_r directly: no eligible results → nan
        rate = metric_tier_c_decisiveness(lexical_r)
        assert math.isnan(rate), (
            f"Lexical row has no tier_c_fired extras → rate must be nan, got {rate}"
        )

    def test_extractor_row_tier_c_differs_from_lexical(self) -> None:
        """Extractor results with tier_c_fired=True → tier_c > 0; lexical → nan."""
        import math

        extractor_r = [
            _make_result(1, extras={"tier_c_fired": True, "postures": ["build"]}),
            _make_result(2, extras={"tier_c_fired": True, "postures": ["build"]}),
        ]
        lexical_r = [
            _make_result(1, extras={}),
            _make_result(2, extras={}),
        ]
        extractor_rate = metric_tier_c_decisiveness(extractor_r)
        lexical_rate = metric_tier_c_decisiveness(lexical_r)
        assert extractor_rate > 0.0, "Extractor row must have tier_c > 0 when all fired"
        assert math.isnan(lexical_rate), "Lexical row must be nan (no tier_c_fired key)"
        assert extractor_rate != lexical_rate or math.isnan(lexical_rate), (
            "Rows with different extras must NOT produce the same tier_c value"
        )

    def test_two_extractor_like_systems_with_different_tier_c_stay_different(
        self,
    ) -> None:
        """Two systems with different tier_c distributions stay independent."""
        sys_high = [
            _make_result(1, extras={"tier_c_fired": True, "postures": ["build"]}),
            _make_result(2, extras={"tier_c_fired": True, "postures": ["build"]}),
        ]
        sys_low = [
            _make_result(1, extras={"tier_c_fired": False, "postures": ["operate"]}),
            _make_result(2, extras={"tier_c_fired": False, "postures": ["operate"]}),
        ]
        rate_high = metric_tier_c_decisiveness(sys_high)
        rate_low = metric_tier_c_decisiveness(sys_low)
        assert rate_high == 1.0
        assert rate_low == 0.0
        assert rate_high != rate_low, (
            "Per-row independence: different tier_c distributions must differ"
        )

    def test_fdb_uses_own_postures_not_inherited(self) -> None:
        """False-default-build uses own postures key, not another row's extras.

        Fix 2: lexical row has no 'postures' key → fdb returns nan (n/a).
        Extractor row with empty postures → 1.0 (wrong default-build).
        They must not be equal (independence check).
        """
        import math

        # Lexical row: no 'postures' key in extras → nan (non-extractor row)
        lexical_r = [
            _make_result(1, "delegate", "code-writer", extras={}),
        ]
        labels = {1: _make_label(1, "investigator")}
        rate = metric_false_default_build(lexical_r, labels)
        assert math.isnan(rate), (
            f"Lexical row (no postures key) must return nan for fdb, got {rate}"
        )

        # Extractor row: postures=[] → default-build case, wrong → 1.0
        extractor_r = [
            _make_result(1, "delegate", "code-writer", extras={"postures": []}),
        ]
        rate_ext = metric_false_default_build(extractor_r, labels)
        assert rate_ext == 1.0, (
            f"Extractor row (empty postures) must return 1.0 for fdb, got {rate_ext}"
        )

        # The two rates must not be equal (independence check)
        assert math.isnan(rate) or rate != rate_ext, (
            "Lexical and extractor fdb rates must differ"
        )


# ---------------------------------------------------------------------------
# Fix 2 (review): fdb denominator conditioned on labeled rows only
# ---------------------------------------------------------------------------


class TestMetricFalseDefaultBuildLabeledOnly:
    """§10.4 fdb: partial-labels case — unlabeled default-build rows must be
    excluded from BOTH numerator AND denominator.

    Reviewer finding: current code counts unlabeled rows in the denominator
    but skips them in the numerator, artificially depressing the rate.
    """

    def test_partial_labels_excludes_unlabeled_from_denominator(self) -> None:
        """1 labeled wrong + 1 unlabeled → rate 1.0, not 0.5.

        Both rows are default-build (empty postures).  Only corpus_id=1 has
        a label, and it is wrong.  corpus_id=2 has no label — it must be
        excluded from the denominator.  Result: 1 wrong / 1 labeled = 1.0.
        """
        import math

        results = [
            _make_result(1, "delegate", "code-writer",
                         extras={"postures": []}),
            _make_result(2, "delegate", "code-writer",
                         extras={"postures": []}),
        ]
        labels = {
            1: _make_label(1, "investigator"),   # labeled, wrong
            # corpus_id=2 intentionally absent — unlabeled
        }
        rate = metric_false_default_build(results, labels)
        assert not math.isnan(rate), "Rate must not be nan when one labeled row exists"
        assert rate == 1.0, (
            f"1 labeled wrong / 1 labeled default-build = 1.0; got {rate}"
        )

    def test_all_unlabeled_default_build_returns_nan(self) -> None:
        """When ALL default-build rows lack labels, return nan (not 0.0).

        Denominator would be zero labeled rows → metric is undefined → nan.
        """
        import math

        results = [
            _make_result(1, "delegate", "code-writer",
                         extras={"postures": []}),
            _make_result(2, "delegate", "code-writer",
                         extras={"postures": []}),
        ]
        labels: dict[int, object] = {}  # no labels at all
        rate = metric_false_default_build(results, labels)
        assert math.isnan(rate), (
            f"All-unlabeled default-build must return nan; got {rate}"
        )


# ---------------------------------------------------------------------------
# Fix #397: self_handle normalization in metric_false_default_build
# ---------------------------------------------------------------------------


class TestFalseDefaultBuildSelfHandleNormalization:
    """FDB must NOT count a self_handle abstention as wrong when gold matches.

    The #397 abstain-sentinel emits decision="self_handle", agent=None for
    harness-carve-out rows (extras["postures"] is empty, so they are
    default-build candidates).  When gold_agent is also "self_handle" the
    system correctly abstained — it did NOT wrongly default-build.

    Bug: the current ``wrong`` sum uses ``r.agent != gold_agent``, which
    evaluates to ``None != "self_handle"`` → True, so a correct abstention
    is counted as a false default.

    Contract (mirrors the fix already applied to metric_routing_correctness):
      A row must NOT count as a wrong default when
        r.decision == "self_handle" AND gold_agent == "self_handle".
    """

    def test_correct_self_handle_default_build_is_not_wrong(
        self,
    ) -> None:
        """decision="self_handle", agent=None, gold="self_handle" → rate 0.0.

        A single default-build row that correctly abstains must not be
        counted as a false default, giving a rate of 0.0 not 1.0.
        """
        results = [
            _make_result(
                1,
                decision="self_handle",
                agent=None,
                extras={"postures": []},
            ),
        ]
        labels = {1: _make_label(1, gold_agent="self_handle")}
        rate = metric_false_default_build(results, labels)
        assert rate == 0.0, (
            f"decision='self_handle' + gold='self_handle' on a default-build "
            f"row must not count as wrong (rate must be 0.0); got {rate}"
        )

    def test_self_handle_default_build_is_wrong_when_gold_is_real_agent(
        self,
    ) -> None:
        """decision="self_handle", agent=None, gold="code-writer" → rate 1.0.

        The normalization is precise: self_handle is only excused when gold
        is also self_handle.  When gold names a real agent, the abstention
        IS a wrong default, and the rate must be 1.0.
        """
        results = [
            _make_result(
                1,
                decision="self_handle",
                agent=None,
                extras={"postures": []},
            ),
        ]
        labels = {1: _make_label(1, gold_agent="code-writer")}
        rate = metric_false_default_build(results, labels)
        assert rate == 1.0, (
            f"decision='self_handle' against gold='code-writer' on a "
            f"default-build row must still count as wrong (rate must be 1.0); "
            f"got {rate}"
        )

    def test_correct_real_agent_default_build_is_not_wrong(
        self,
    ) -> None:
        """agent="code-writer", gold="code-writer" → rate 0.0 (regression).

        Existing behaviour for real-agent default-builds that are correct
        must be preserved: when agent matches gold, the row is not wrong.
        """
        results = [
            _make_result(
                1,
                decision="delegate",
                agent="code-writer",
                extras={"postures": []},
            ),
        ]
        labels = {1: _make_label(1, gold_agent="code-writer")}
        rate = metric_false_default_build(results, labels)
        assert rate == 0.0, (
            f"Correct real-agent default-build must yield rate 0.0; "
            f"got {rate}"
        )

    def test_wrong_real_agent_default_build_is_counted(
        self,
    ) -> None:
        """agent="code-writer", gold="doc-writer" → rate 1.0 (regression).

        Existing behaviour for real-agent default-builds that are wrong must
        be preserved: agent != gold counts as a false default.
        """
        results = [
            _make_result(
                1,
                decision="delegate",
                agent="code-writer",
                extras={"postures": []},
            ),
        ]
        labels = {1: _make_label(1, gold_agent="doc-writer")}
        rate = metric_false_default_build(results, labels)
        assert rate == 1.0, (
            f"Wrong real-agent default-build must yield rate 1.0; "
            f"got {rate}"
        )

    def test_mixed_batch_self_handle_drops_from_numerator_only(
        self,
    ) -> None:
        """Mixed: 1 correct self_handle default-build + 1 wrong real-agent.

        Row layout:
          corpus_id=1: decision="self_handle", agent=None,
                       gold="self_handle" → correct (not wrong)
          corpus_id=2: decision="delegate",   agent="code-writer",
                       gold="doc-writer"   → wrong

        Expected: wrong=1, denominator=2 → rate = 0.5.

        This proves the self_handle row drops OUT of the numerator (correct
        abstention not counted as wrong) but stays IN the denominator
        (it is still a labeled default-build case).
        """
        results = [
            _make_result(
                1,
                decision="self_handle",
                agent=None,
                extras={"postures": []},
            ),
            _make_result(
                2,
                decision="delegate",
                agent="code-writer",
                extras={"postures": []},
            ),
        ]
        labels = {
            1: _make_label(1, gold_agent="self_handle"),
            2: _make_label(2, gold_agent="doc-writer"),
        }
        rate = metric_false_default_build(results, labels)
        assert rate == 0.5, (
            f"Mixed batch (1 correct self_handle + 1 wrong real-agent) "
            f"must yield rate 0.5 (1 wrong / 2 labeled); got {rate}"
        )


# ---------------------------------------------------------------------------
# Fix #397: self_handle normalization in metric_routing_correctness
# ---------------------------------------------------------------------------


class TestRoutingCorrectnessAbstainSentinel:
    """RC must count decision=="self_handle" + gold_agent=="self_handle" correct.

    The #397 abstain-sentinel makes the eval systems emit
    decision="self_handle" with agent=None for harness-carve-out rows.
    Gold encodes those rows as gold_agent="self_handle" (a string).
    Before the fix, None == "self_handle" is False, so every correct
    abstention scored as a miss.  These tests pin the corrected contract:

      RC is correct when EITHER:
        - r.agent == gold_agent   (existing behaviour), OR
        - r.decision == "self_handle" AND gold_agent == "self_handle"
    """

    def test_self_handle_decision_counts_as_correct_when_gold_matches(
        self,
    ) -> None:
        """decision="self_handle", agent=None, gold="self_handle" → RC 1.0.

        A single result with the self_handle sentinel against a gold label
        that also names self_handle must score as a correct prediction.
        """
        results = [
            _make_result(
                1,
                decision="self_handle",
                agent=None,
                confidence=1.0,
            )
        ]
        labels = {
            1: _make_label(1, gold_agent="self_handle"),
        }
        rc = metric_routing_correctness(results, labels)
        assert rc == 1.0, (
            f"decision='self_handle' + gold='self_handle' must score "
            f"correct (RC=1.0); got {rc}"
        )

    def test_self_handle_decision_is_miss_when_gold_is_real_agent(
        self,
    ) -> None:
        """decision="self_handle", agent=None, gold="code-writer" → RC 0.0.

        A self_handle abstention does NOT match a gold row that names a
        real agent — it is a miss.
        """
        results = [
            _make_result(
                1,
                decision="self_handle",
                agent=None,
                confidence=1.0,
            )
        ]
        labels = {
            1: _make_label(1, gold_agent="code-writer"),
        }
        rc = metric_routing_correctness(results, labels)
        assert rc == 0.0, (
            f"decision='self_handle' against gold='code-writer' must be "
            f"a miss (RC=0.0); got {rc}"
        )

    def test_real_agent_delegation_is_miss_when_gold_is_self_handle(
        self,
    ) -> None:
        """decision="delegate", agent="code-writer", gold="self_handle" → 0.0.

        A real-agent delegation does not satisfy a self_handle gold row.
        Only an explicit self_handle decision receives the normalization.
        """
        results = [
            _make_result(
                1,
                decision="delegate",
                agent="code-writer",
                confidence=0.9,
            )
        ]
        labels = {
            1: _make_label(1, gold_agent="self_handle"),
        }
        rc = metric_routing_correctness(results, labels)
        assert rc == 0.0, (
            f"delegate to real agent against gold='self_handle' must be "
            f"a miss (RC=0.0); got {rc}"
        )

    def test_real_agent_match_still_correct_unaffected_by_normalization(
        self,
    ) -> None:
        """Regression: existing real-agent == gold_agent path still works.

        decision="delegate", agent="code-writer", gold="code-writer" must
        score as correct — existing behaviour must not be disturbed by the
        self_handle normalization.
        """
        results = [
            _make_result(
                1,
                decision="delegate",
                agent="code-writer",
                confidence=0.9,
            )
        ]
        labels = {
            1: _make_label(1, gold_agent="code-writer"),
        }
        rc = metric_routing_correctness(results, labels)
        assert rc == 1.0, (
            f"Existing real-agent match must still score correct "
            f"(RC=1.0); got {rc}"
        )

    def test_mixed_batch_counts_self_handle_in_numerator_and_denominator(
        self,
    ) -> None:
        """Mixed batch: 1 correct self_handle + 1 correct real-agent + 1 miss.

        Batch of three rows:
          row 1: decision=self_handle, gold=self_handle → correct
          row 2: decision=delegate,   agent=code-writer, gold=code-writer → correct
          row 3: decision=delegate,   agent=ops,         gold=code-writer → miss

        Expected RC = round(2/3, 4) = 0.6667.

        This proves self_handle rows are counted in BOTH numerator (when
        correct) and denominator (always).
        """
        results = [
            _make_result(
                1,
                decision="self_handle",
                agent=None,
                confidence=1.0,
            ),
            _make_result(
                2,
                decision="delegate",
                agent="code-writer",
                confidence=0.9,
            ),
            _make_result(
                3,
                decision="delegate",
                agent="ops",
                confidence=0.9,
            ),
        ]
        labels = {
            1: _make_label(1, gold_agent="self_handle"),
            2: _make_label(2, gold_agent="code-writer"),
            3: _make_label(3, gold_agent="code-writer"),
        }
        rc = metric_routing_correctness(results, labels)
        assert rc == round(2 / 3, 4), (
            f"Mixed batch (1 self_handle correct, 1 real correct, 1 miss) "
            f"must yield RC={round(2 / 3, 4)}; got {rc}"
        )
