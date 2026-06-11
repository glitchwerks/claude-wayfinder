"""Tests for spikes.domain_encoder._eval — per-prompt evaluation utilities.

All tests skip cleanly when model2vec is not installed (module-level
pytest.importorskip guard), matching the pattern in test_domain_encoder.py.

These tests are CI-path-safe: the module itself is guarded so that the
heavy _eval code path (which imports _classifier) is never triggered at
collection time in an environment without model2vec.
"""

from __future__ import annotations

import math
from typing import Any

import pytest

# Module-level skip guard: all tests in this file skip if model2vec is absent.
pytest.importorskip("model2vec")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _load_classifier_8m() -> Any:
    """Load 8M classifier; skip if model cannot be loaded from cache."""
    from spikes.domain_encoder._classifier import DomainClassifier

    try:
        clf = DomainClassifier.from_pretrained("minishlab/potion-base-8M")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Could not load potion-base-8M from cache: {exc}")
    return clf


@pytest.fixture(scope="module")
def classifier_8m() -> Any:
    """Module-scoped 8M classifier fixture."""
    return _load_classifier_8m()


# ---------------------------------------------------------------------------
# 1. PromptResult dataclass
# ---------------------------------------------------------------------------


def test_prompt_result_fields() -> None:
    """PromptResult must expose all required fields for the comparison table."""
    from spikes.domain_encoder._eval import PromptResult

    pr = PromptResult(
        prompt_id="P1",
        text="Fix the bug",
        gold_domain="code",
        is_any=False,
        predicted="code",
        entropy=1.5,
        top1_prob=0.35,
        top2_prob=0.20,
        margin=0.15,
        verdict="HIT",
    )
    assert pr.prompt_id == "P1"
    assert pr.text == "Fix the bug"
    assert pr.gold_domain == "code"
    assert pr.is_any is False
    assert pr.predicted == "code"
    assert abs(pr.entropy - 1.5) < 1e-9
    assert abs(pr.top1_prob - 0.35) < 1e-9
    assert abs(pr.top2_prob - 0.20) < 1e-9
    assert abs(pr.margin - 0.15) < 1e-9
    assert pr.verdict == "HIT"


def test_prompt_result_margin_computed_from_probs() -> None:
    """margin must equal top1_prob - top2_prob when constructed directly."""
    from spikes.domain_encoder._eval import PromptResult

    pr = PromptResult(
        prompt_id="P2",
        text="text",
        gold_domain="docs_prose",
        is_any=False,
        predicted="docs_prose",
        entropy=2.0,
        top1_prob=0.30,
        top2_prob=0.25,
        margin=0.05,
        verdict="HIT",
    )
    assert abs(pr.margin - (pr.top1_prob - pr.top2_prob)) < 1e-9


# ---------------------------------------------------------------------------
# 2. evaluate_prompt — single-prompt evaluation
# ---------------------------------------------------------------------------


def test_evaluate_prompt_returns_prompt_result(classifier_8m: Any) -> None:
    """evaluate_prompt must return a PromptResult for a deterministic-domain prompt."""
    from spikes.domain_encoder._eval import PromptResult, evaluate_prompt

    pr = evaluate_prompt(
        classifier=classifier_8m,
        prompt_id="P10",
        text="tests are failing after the rename, update them to match the new API.",
        gold_domain="code",
        is_any=False,
    )
    assert isinstance(pr, PromptResult)
    assert pr.prompt_id == "P10"
    assert pr.gold_domain == "code"
    assert pr.is_any is False


def test_evaluate_prompt_margin_equals_top1_minus_top2(classifier_8m: Any) -> None:
    """margin must equal top1_prob - top2_prob (from the distribution)."""
    from spikes.domain_encoder._eval import evaluate_prompt

    pr = evaluate_prompt(
        classifier=classifier_8m,
        prompt_id="P1",
        text="Make sure `db/schema.sql` is consistent with the migrations in `db/migrations/`.",
        gold_domain="data",
        is_any=False,
    )
    assert abs(pr.margin - (pr.top1_prob - pr.top2_prob)) < 1e-9
    assert pr.top1_prob >= pr.top2_prob


def test_evaluate_prompt_verdict_hit_on_correct_top1(classifier_8m: Any) -> None:
    """verdict must be 'HIT' when predicted top-1 matches gold_domain for is_any=False."""
    from spikes.domain_encoder._eval import evaluate_prompt

    pr = evaluate_prompt(
        classifier=classifier_8m,
        prompt_id="P10",
        text="tests are failing after the rename, update them to match the new API.",
        gold_domain="code",
        is_any=False,
    )
    # P10 is expected to hit — if it doesn't, the test will still pass structurally
    # but the verdict check ensures the logic is correct
    if pr.predicted == "code":
        assert pr.verdict == "HIT"
    else:
        assert pr.verdict == "MISS"


def test_evaluate_prompt_verdict_hit_for_domain_any_high_entropy(classifier_8m: Any) -> None:
    """verdict must be 'HIT (entropy>1.5)' for is_any=True with entropy > 1.5."""
    from spikes.domain_encoder._eval import evaluate_prompt

    # P6 is domain-any (is_any=True); 8M model produces near-uniform distributions
    pr = evaluate_prompt(
        classifier=classifier_8m,
        prompt_id="P6",
        text="What if we cached the catalog in memory instead of re-reading it each call?",
        gold_domain="project_meta",
        is_any=True,
    )
    assert pr.is_any is True
    if pr.entropy > 1.5:
        assert pr.verdict == "HIT (entropy>1.5)"
    else:
        assert pr.verdict == "MISS"


def test_evaluate_prompt_entropy_in_valid_range(classifier_8m: Any) -> None:
    """entropy must be in [0, log2(5)] for any prompt."""
    from spikes.domain_encoder._eval import evaluate_prompt

    pr = evaluate_prompt(
        classifier=classifier_8m,
        prompt_id="P8",
        text="Tear apart the error handling in `src/matcher/engine.py` — I think it's too clever.",
        gold_domain="code",
        is_any=False,
    )
    max_entropy = math.log2(5)
    assert 0.0 <= pr.entropy <= max_entropy + 1e-6


# ---------------------------------------------------------------------------
# 3. evaluate_all — batch evaluation over P1-P14
# ---------------------------------------------------------------------------


def test_evaluate_all_returns_14_results(classifier_8m: Any) -> None:
    """evaluate_all must return exactly 14 PromptResults for the P1-P14 gold set."""
    from spikes.domain_encoder._eval import SPIKE_GOLD_FOR_EVAL, evaluate_all

    results = evaluate_all(classifier_8m, SPIKE_GOLD_FOR_EVAL)
    assert len(results) == 14


def test_evaluate_all_prompt_ids_match_gold(classifier_8m: Any) -> None:
    """evaluate_all results must preserve P1-P14 prompt_id ordering."""
    from spikes.domain_encoder._eval import SPIKE_GOLD_FOR_EVAL, evaluate_all

    results = evaluate_all(classifier_8m, SPIKE_GOLD_FOR_EVAL)
    ids = [r.prompt_id for r in results]
    assert ids == [row[0] for row in SPIKE_GOLD_FOR_EVAL]


def test_evaluate_all_deterministic(classifier_8m: Any) -> None:
    """Two calls to evaluate_all must produce bit-identical results."""
    from spikes.domain_encoder._eval import SPIKE_GOLD_FOR_EVAL, evaluate_all

    r1 = evaluate_all(classifier_8m, SPIKE_GOLD_FOR_EVAL)
    r2 = evaluate_all(classifier_8m, SPIKE_GOLD_FOR_EVAL)
    for a, b in zip(r1, r2):
        assert a.prompt_id == b.prompt_id
        assert a.entropy == b.entropy
        assert a.margin == b.margin
        assert a.verdict == b.verdict


# ---------------------------------------------------------------------------
# 4. margin_gate_sweep — threshold sweep for margin-gate evaluation
# ---------------------------------------------------------------------------


def test_margin_gate_sweep_returns_dict(classifier_8m: Any) -> None:
    """margin_gate_sweep must return a dict mapping threshold → separation metrics."""
    from spikes.domain_encoder._eval import SPIKE_GOLD_FOR_EVAL, evaluate_all, margin_gate_sweep

    results = evaluate_all(classifier_8m, SPIKE_GOLD_FOR_EVAL)
    sweep = margin_gate_sweep(results)
    assert isinstance(sweep, dict)
    assert len(sweep) > 0


def test_margin_gate_sweep_keys_are_floats(classifier_8m: Any) -> None:
    """margin_gate_sweep keys must be float thresholds."""
    from spikes.domain_encoder._eval import SPIKE_GOLD_FOR_EVAL, evaluate_all, margin_gate_sweep

    results = evaluate_all(classifier_8m, SPIKE_GOLD_FOR_EVAL)
    sweep = margin_gate_sweep(results)
    for k in sweep:
        assert isinstance(k, float), f"Key {k!r} is not a float"


def test_margin_gate_sweep_values_have_required_fields(classifier_8m: Any) -> None:
    """Each sweep entry must include tp, fp, tn, fn, precision, recall, f1."""
    from spikes.domain_encoder._eval import SPIKE_GOLD_FOR_EVAL, evaluate_all, margin_gate_sweep

    results = evaluate_all(classifier_8m, SPIKE_GOLD_FOR_EVAL)
    sweep = margin_gate_sweep(results)
    for threshold, metrics in sweep.items():
        for field in ("tp", "fp", "tn", "fn", "precision", "recall", "f1"):
            assert field in metrics, (
                f"Missing field '{field}' at threshold {threshold}"
            )


def test_margin_gate_at_0_04_threshold_evaluated(classifier_8m: Any) -> None:
    """The standard 0.04 threshold from the 8M report must appear in the sweep."""
    from spikes.domain_encoder._eval import SPIKE_GOLD_FOR_EVAL, evaluate_all, margin_gate_sweep

    results = evaluate_all(classifier_8m, SPIKE_GOLD_FOR_EVAL)
    sweep = margin_gate_sweep(results)
    # 0.04 must be in the sweep (as a float key)
    keys = list(sweep.keys())
    assert any(abs(k - 0.04) < 1e-9 for k in keys), (
        f"0.04 threshold not found in sweep keys: {keys}"
    )


def test_margin_gate_best_threshold_returns_float(classifier_8m: Any) -> None:
    """best_margin_threshold must return a float."""
    from spikes.domain_encoder._eval import (
        SPIKE_GOLD_FOR_EVAL,
        best_margin_threshold,
        evaluate_all,
        margin_gate_sweep,
    )

    results = evaluate_all(classifier_8m, SPIKE_GOLD_FOR_EVAL)
    sweep = margin_gate_sweep(results)
    best = best_margin_threshold(sweep)
    assert isinstance(best, float)
