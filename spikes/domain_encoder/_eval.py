"""Per-prompt evaluation utilities for the domain-encoder spike.

Provides deterministic per-prompt classification with entropy AND top-1 margin,
margin-gate sweep, and a batch evaluator over the P1-P14 gold set.

Designed to be reusable for #330 corpus evaluation.  All functions are pure
(no side effects) and deterministic: given the same classifier and gold set,
the output is bit-identical.

Usage::

    from spikes.domain_encoder._eval import SPIKE_GOLD_FOR_EVAL, evaluate_all
    from spikes.domain_encoder._classifier import DomainClassifier

    clf = DomainClassifier.from_pretrained("minishlab/potion-base-8M")
    results = evaluate_all(clf, SPIKE_GOLD_FOR_EVAL)
    for r in results:
        print(r.prompt_id, r.verdict, f"margin={r.margin:.4f}", f"entropy={r.entropy:.4f}")
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Gold-label dataset — P1–P14
#
# Imported by both this module and test_domain_encoder.py so there is a
# single canonical source of the gold labels.
#
# Format: (prompt_id, text, gold_domain, is_any)
# ---------------------------------------------------------------------------

SPIKE_GOLD_FOR_EVAL: list[tuple[str, str, str, bool]] = [
    # P1: auditor — verify conformance of db schema vs migrations (data artifacts)
    (
        "P1",
        "Make sure `db/schema.sql` is consistent with the migrations in `db/migrations/`.",
        "data",
        False,
    ),
    # P2: auditor — README vs live build (docs/prose domain; conformance question)
    (
        "P2",
        "Does the README still reflect how the build actually works?",
        "docs_prose",
        False,
    ),
    # P3: investigator — config mismatch + crash (cross-domain → domain-any)
    (
        "P3",
        (
            "The app crashes on startup and the config doesn't match what"
            " the docs say — figure out which is right."
        ),
        "code",
        True,
    ),
    # P4: researcher — caching idea / prior art (project/meta domain)
    (
        "P4",
        (
            "I have an idea for caching dispatch results between sessions"
            " — has anyone built something like this?"
        ),
        "project_meta",
        True,
    ),
    # P5: project-planner — caching feature phases/milestones (project/meta domain)
    (
        "P5",
        (
            "We should add result caching to the matcher."
            " Lay out the phases and milestones to get there."
        ),
        "project_meta",
        False,
    ),
    # P6: approach-critic — caching idea critique (project/meta → domain-any)
    (
        "P6",
        "What if we cached the catalog in memory instead of re-reading it each call?",
        "project_meta",
        True,
    ),
    # P7: approach-critic — challenge a specific storage approach (project/meta → domain-any)
    (
        "P7",
        (
            "Poke holes in this approach before I build it:"
            " store gold labels in issue bodies instead of a file."
        ),
        "project_meta",
        True,
    ),
    # P8: inquisitor — code critique (code domain)
    (
        "P8",
        "Tear apart the error handling in `src/matcher/engine.py` — I think it's too clever.",
        "code",
        False,
    ),
    # P9: inquisitor — PR harsh review (near-uniform dist → domain-any by entropy)
    (
        "P9",
        "Give PR #214 a really harsh review — don't go easy on it.",
        "code",
        True,
    ),
    # P10: code-writer — test rename (code domain)
    (
        "P10",
        "tests are failing after the rename, update them to match the new API.",
        "code",
        False,
    ),
    # P11: code-writer — test fix with pasted output (code domain)
    (
        "P11",
        (
            "Here's pytest: `FAILED tests/test_api.py::test_fetch -"
            " AttributeError: no attribute 'get_user'`."
            " Started after we renamed get_user → fetch_user."
            " Update the tests to match."
        ),
        "code",
        False,
    ),
    # P12: investigator — deploy failure + DNS + cross-layer (infra/deploy domain)
    (
        "P12",
        (
            "The deploy fails every time — logs show"
            " `Error: ECONNREFUSED api.internal:443`."
            " We changed the DNS config last week because the old provider was slow."
            " Figure out why it fails."
        ),
        "infra_deploy",
        False,
    ),
    # P13: ops — gh pr checks command (infra/deploy domain; VCS-operate)
    (
        "P13",
        "Run `gh pr checks 214` and summarize what's red.",
        "infra_deploy",
        True,
    ),
    # P14: investigator — CI Traceback + cross-layer (infra/deploy domain)
    (
        "P14",
        (
            "Getting this in CI: `Traceback (most recent call last)..."
            " ConnectionError` — happens only in the deploy workflow, never locally."
        ),
        "infra_deploy",
        False,
    ),
]


# ---------------------------------------------------------------------------
# PromptResult dataclass
# ---------------------------------------------------------------------------


@dataclass
class PromptResult:
    """Classification result for a single prompt with all measurement fields.

    Attributes:
        prompt_id: Prompt identifier (e.g. ``"P1"``).
        text: The prompt text.
        gold_domain: Expected domain label string.
        is_any: True if this prompt is domain-any (tested via entropy, not top-1).
        predicted: Predicted top-1 domain label string.
        entropy: Shannon entropy of the distribution in bits.
        top1_prob: Probability of the top-1 class.
        top2_prob: Probability of the second-ranked class.
        margin: top1_prob - top2_prob.  Larger margin → more confident prediction.
        verdict: Classification verdict string.
            One of ``"HIT"``, ``"MISS"``, ``"HIT (entropy>1.5)"``.
    """

    prompt_id: str
    text: str
    gold_domain: str
    is_any: bool
    predicted: str
    entropy: float
    top1_prob: float
    top2_prob: float
    margin: float
    verdict: str


# ---------------------------------------------------------------------------
# evaluate_prompt — single prompt
# ---------------------------------------------------------------------------


def evaluate_prompt(
    classifier: Any,
    prompt_id: str,
    text: str,
    gold_domain: str,
    is_any: bool,
) -> PromptResult:
    """Classify one prompt and compute all evaluation fields.

    Args:
        classifier: A ``DomainClassifier`` instance with a ``classify()`` method.
        prompt_id: Prompt identifier (e.g. ``"P1"``).
        text: Task description to classify.
        gold_domain: Expected gold domain label string.
        is_any: True if the prompt is domain-any (entropy-tested).

    Returns:
        PromptResult with distribution stats and verdict.
    """
    result = classifier.classify(text)
    dist = result.distribution

    # Sort by probability descending to get top-1 and top-2
    sorted_probs = sorted(dist.values(), reverse=True)
    top1_prob = sorted_probs[0]
    top2_prob = sorted_probs[1] if len(sorted_probs) > 1 else 0.0
    margin = top1_prob - top2_prob

    predicted = result.top_label
    entropy = result.entropy

    # Determine verdict
    if is_any:
        verdict = "HIT (entropy>1.5)" if entropy > 1.5 else "MISS"
    else:
        verdict = "HIT" if predicted == gold_domain else "MISS"

    return PromptResult(
        prompt_id=prompt_id,
        text=text,
        gold_domain=gold_domain,
        is_any=is_any,
        predicted=predicted,
        entropy=entropy,
        top1_prob=top1_prob,
        top2_prob=top2_prob,
        margin=margin,
        verdict=verdict,
    )


# ---------------------------------------------------------------------------
# evaluate_all — batch over a gold-label dataset
# ---------------------------------------------------------------------------


def evaluate_all(
    classifier: Any,
    gold_set: list[tuple[str, str, str, bool]],
) -> list[PromptResult]:
    """Evaluate a classifier over a gold-label dataset.

    Args:
        classifier: A ``DomainClassifier`` instance.
        gold_set: List of ``(prompt_id, text, gold_domain, is_any)`` tuples.

    Returns:
        List of ``PromptResult`` in the same order as ``gold_set``.
    """
    return [
        evaluate_prompt(
            classifier=classifier,
            prompt_id=prompt_id,
            text=text,
            gold_domain=gold_domain,
            is_any=is_any,
        )
        for prompt_id, text, gold_domain, is_any in gold_set
    ]


# ---------------------------------------------------------------------------
# margin_gate_sweep — threshold sweep for domain-any detection via margin
# ---------------------------------------------------------------------------


def margin_gate_sweep(
    results: list[PromptResult],
    thresholds: list[float] | None = None,
) -> dict[float, dict[str, float | int]]:
    """Sweep margin thresholds and compute separation metrics at each threshold.

    For each threshold T, a prompt is predicted "domain-any" if margin < T.
    The ground truth is ``is_any``.  Metrics: tp, fp, tn, fn, precision,
    recall, f1.

    Args:
        results: List of PromptResult from evaluate_all.
        thresholds: List of threshold values to sweep.
            Defaults to a grid of 0.00 to 0.20 in steps of 0.01, always
            including the standard 0.04 value from the 8M report.

    Returns:
        Dict mapping float threshold → dict with keys:
        ``tp``, ``fp``, ``tn``, ``fn``, ``precision``, ``recall``, ``f1``.
    """
    if thresholds is None:
        # Grid 0.00 to 0.20 step 0.01, include 0.04 explicitly
        thresholds = [round(i * 0.01, 2) for i in range(21)]
        if 0.04 not in thresholds:
            thresholds.append(0.04)
        thresholds = sorted(thresholds)

    sweep: dict[float, dict[str, float | int]] = {}

    for threshold in thresholds:
        tp = fp = tn = fn = 0
        for r in results:
            predicted_any = r.margin < threshold
            actual_any = r.is_any
            if predicted_any and actual_any:
                tp += 1
            elif predicted_any and not actual_any:
                fp += 1
            elif not predicted_any and not actual_any:
                tn += 1
            else:  # not predicted_any and actual_any
                fn += 1

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = (
            2 * precision * recall / (precision + recall)
            if (precision + recall) > 0
            else 0.0
        )

        sweep[float(threshold)] = {
            "tp": tp,
            "fp": fp,
            "tn": tn,
            "fn": fn,
            "precision": precision,
            "recall": recall,
            "f1": f1,
        }

    return sweep


# ---------------------------------------------------------------------------
# best_margin_threshold — find the threshold with the highest F1
# ---------------------------------------------------------------------------


def best_margin_threshold(sweep: dict[float, dict[str, float | int]]) -> float:
    """Return the threshold with the highest F1 score in a margin_gate_sweep result.

    In case of ties, returns the smallest (most conservative) threshold.

    Args:
        sweep: Output of ``margin_gate_sweep``.

    Returns:
        Float threshold value with highest F1.
    """
    if not sweep:
        return 0.0

    best_t = min(sweep.keys())  # tie-break: smallest threshold
    best_f1 = float(sweep[best_t]["f1"])

    for t, metrics in sorted(sweep.items()):
        f1 = float(metrics["f1"])
        if f1 > best_f1:
            best_f1 = f1
            best_t = t

    return float(best_t)
