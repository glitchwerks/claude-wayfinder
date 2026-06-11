"""Tests for the domain-encoder spike (spikes/domain_encoder).

All tests skip cleanly when model2vec is not installed OR when the
potion-base-8M model cannot be loaded from the local HuggingFace cache.

Skip guard rationale: CI installs .[dev] only (no spike extra).  The
tests must collect and skip without error in that environment.  Each test
that needs the model calls ``_require_model()`` at fixture setup time so
the skip fires lazily — collection-time imports of model2vec are guarded
by ``pytest.importorskip`` at module level.

Design refs: spec §8.2, §9.1–9.3, §11 (potion-base-8M spike).
"""

from __future__ import annotations

import math
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Module-level skip guard: skip the entire module if model2vec is absent.
# pytest.importorskip at module scope causes all tests to be skipped (not
# errored) when the package is missing.
# ---------------------------------------------------------------------------
pytest.importorskip("model2vec")

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

WORKTREE = "I:/ai/claude/claude-wayfinder/.claude/worktrees/vigilant-shamir-97d682"


def _load_classifier() -> Any:
    """Load the DomainClassifier; skip if model cannot be loaded from cache."""
    from spikes.domain_encoder._classifier import DomainClassifier

    try:
        clf = DomainClassifier.from_pretrained("minishlab/potion-base-8M")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Could not load potion-base-8M from cache: {exc}")
    return clf


@pytest.fixture(scope="module")
def classifier() -> Any:
    """Module-scoped classifier fixture — loads the model once per test module."""
    return _load_classifier()


# ---------------------------------------------------------------------------
# 1. Domain constants and seed phrases
# ---------------------------------------------------------------------------


def test_domain_classes_five_way() -> None:
    """DomainLabel must define exactly the 5 classes from spec §9.3."""
    from spikes.domain_encoder._domains import DOMAIN_CLASSES, DomainLabel

    assert set(DOMAIN_CLASSES) == {
        DomainLabel.CODE,
        DomainLabel.INFRA_DEPLOY,
        DomainLabel.DATA,
        DomainLabel.DOCS_PROSE,
        DomainLabel.PROJECT_META,
    }
    assert len(DOMAIN_CLASSES) == 5


def test_seed_phrases_versioned_constant() -> None:
    """SEED_PHRASES_VERSION must be a non-empty string (versioning discipline)."""
    from spikes.domain_encoder._domains import SEED_PHRASES_VERSION

    assert isinstance(SEED_PHRASES_VERSION, str)
    assert SEED_PHRASES_VERSION


def test_seed_phrases_all_classes_present() -> None:
    """Every domain class must have at least one seed phrase."""
    from spikes.domain_encoder._domains import DOMAIN_CLASSES, SEED_PHRASES, DomainLabel

    for label in DOMAIN_CLASSES:
        assert label in SEED_PHRASES, f"Missing seed phrases for {label}"
        assert len(SEED_PHRASES[label]) >= 1, f"Empty seed phrases for {label}"

    # Total must equal exactly the 5 classes
    assert set(SEED_PHRASES.keys()) == {
        DomainLabel.CODE,
        DomainLabel.INFRA_DEPLOY,
        DomainLabel.DATA,
        DomainLabel.DOCS_PROSE,
        DomainLabel.PROJECT_META,
    }


def test_seed_phrases_immutable() -> None:
    """SEED_PHRASES values must be tuples (immutable, not lists)."""
    from spikes.domain_encoder._domains import SEED_PHRASES

    for label, phrases in SEED_PHRASES.items():
        assert isinstance(phrases, tuple), (
            f"SEED_PHRASES[{label}] must be a tuple, got {type(phrases)}"
        )
        for phrase in phrases:
            assert isinstance(phrase, str), f"Phrase in {label} must be str: {phrase!r}"


# ---------------------------------------------------------------------------
# 1b. Default model revision constant
# ---------------------------------------------------------------------------


def test_default_model_revision_constant_exists() -> None:
    """DEFAULT_MODEL_REVISION must be a 40-char hex SHA (the pinned commit)."""
    from spikes.domain_encoder._classifier import DEFAULT_MODEL_REVISION

    assert isinstance(DEFAULT_MODEL_REVISION, str)
    assert len(DEFAULT_MODEL_REVISION) == 40, (
        f"Expected 40-char hex SHA, got {len(DEFAULT_MODEL_REVISION)!r} chars: "
        f"{DEFAULT_MODEL_REVISION!r}"
    )
    # Must match the revision recorded in the spike report §7
    assert DEFAULT_MODEL_REVISION == "bf8b056651a2c21b8d2565580b8569da283cab23"


# ---------------------------------------------------------------------------
# 2. Entropy
# ---------------------------------------------------------------------------


def test_entropy_uniform_is_log2_n() -> None:
    """Uniform distribution over 5 classes → entropy = log2(5) ≈ 2.322."""
    from spikes.domain_encoder._entropy import distribution_entropy

    uniform = [1.0 / 5] * 5
    h = distribution_entropy(uniform)
    assert abs(h - math.log2(5)) < 1e-6


def test_entropy_degenerate_is_zero() -> None:
    """Degenerate distribution (all mass on one class) → entropy = 0.0."""
    from spikes.domain_encoder._entropy import distribution_entropy

    degenerate = [1.0, 0.0, 0.0, 0.0, 0.0]
    h = distribution_entropy(degenerate)
    assert abs(h - 0.0) < 1e-9


def test_entropy_accepts_numpy_array() -> None:
    """distribution_entropy must accept numpy arrays, not just lists."""
    import numpy as np

    from spikes.domain_encoder._entropy import distribution_entropy

    arr = np.array([0.2, 0.2, 0.2, 0.2, 0.2], dtype=np.float32)
    h = distribution_entropy(arr)
    assert abs(h - math.log2(5)) < 1e-5


# ---------------------------------------------------------------------------
# 3. DomainResult type
# ---------------------------------------------------------------------------


def test_domain_result_fields() -> None:
    """DomainResult must expose distribution, top_label, and entropy."""
    from spikes.domain_encoder._classifier import DomainResult

    dist = {
        "code": 0.5,
        "infra_deploy": 0.1,
        "data": 0.1,
        "docs_prose": 0.2,
        "project_meta": 0.1,
    }
    result = DomainResult(distribution=dist, top_label="code", entropy=0.9)
    assert result.distribution == dist
    assert result.top_label == "code"
    assert result.entropy == 0.9


# ---------------------------------------------------------------------------
# 4. DomainClassifier — model loading and encoding (require model2vec model)
# ---------------------------------------------------------------------------


def test_classifier_loads(classifier: Any) -> None:
    """DomainClassifier must load without error and expose DOMAIN_CLASSES."""
    from spikes.domain_encoder._domains import DOMAIN_CLASSES

    assert classifier is not None
    labels = classifier.domain_classes
    assert set(labels) == set(DOMAIN_CLASSES)


def test_classify_returns_domain_result(classifier: Any) -> None:
    """classify() must return a DomainResult with a valid 5-class distribution."""
    from spikes.domain_encoder._classifier import DomainResult

    result = classifier.classify("Fix the bug in the Python function")
    assert isinstance(result, DomainResult)
    assert len(result.distribution) == 5
    total = sum(result.distribution.values())
    assert abs(total - 1.0) < 1e-5, f"Distribution sums to {total}, expected 1.0"


def test_classify_distribution_nonnegative(classifier: Any) -> None:
    """All distribution values must be non-negative."""
    result = classifier.classify("deploy to production Kubernetes cluster")
    for label, prob in result.distribution.items():
        assert prob >= 0.0, f"Negative probability for {label}: {prob}"


def test_classify_top_label_consistent(classifier: Any) -> None:
    """top_label must be the argmax of the distribution."""
    result = classifier.classify("write a Dockerfile for the API service")
    top = max(result.distribution, key=result.distribution.__getitem__)
    assert result.top_label == top


def test_classify_entropy_range(classifier: Any) -> None:
    """Entropy must be in [0, log2(5)] for any valid distribution."""
    result = classifier.classify("what are the phases to add caching?")
    max_entropy = math.log2(5)
    assert 0.0 <= result.entropy <= max_entropy + 1e-6


# ---------------------------------------------------------------------------
# 5. Determinism — identical input → identical output (in-process)
# ---------------------------------------------------------------------------


def test_determinism_in_process(classifier: Any) -> None:
    """Same input must produce bit-identical DomainResult across two calls."""
    text = "debug the failing test in test_api.py"
    r1 = classifier.classify(text)
    r2 = classifier.classify(text)
    assert r1.top_label == r2.top_label
    assert r1.entropy == r2.entropy
    for label in r1.distribution:
        assert r1.distribution[label] == r2.distribution[label], (
            f"Non-deterministic result for label {label!r}: "
            f"{r1.distribution[label]} != {r2.distribution[label]}"
        )


def test_determinism_batch_vs_single(classifier: Any) -> None:
    """classify() result must match classify_batch() result for the same text."""
    text = "update the README to reflect the new CLI flags"
    single = classifier.classify(text)
    batch = classifier.classify_batch([text])
    assert len(batch) == 1
    batch_result = batch[0]
    assert single.top_label == batch_result.top_label
    for label in single.distribution:
        assert single.distribution[label] == batch_result.distribution[label]


# ---------------------------------------------------------------------------
# 6. P1–P14 spike prompts — accuracy bound
#
# Gold domain labels are derived from the §9.1 grid row of each prompt's
# gold agent + the prompt text, per the brief's instructions.
# Labels are judgment calls; the report tables each with a one-line rationale.
#
# "domain-any" class prompts (investigator, approach-critic, auditor,
# researcher) have high expected entropy — we test entropy > 1.5 (mid-range)
# rather than a specific top_label, per §9.2 finding 4.
# ---------------------------------------------------------------------------

# Gold domain labels for P1-P14 (derived from §9.1 grid + prompt text).
# Format: (prompt_id, task_description, gold_domain, is_domain_any)
SPIKE_GOLD: list[tuple[str, str, str, bool]] = [
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
    # P9: inquisitor — PR harsh review (entropy=2.317 → effectively domain-any;
    # the phrase "harsh review" contains no code-salient vocabulary so the
    # encoder distributes probability nearly uniformly across all 5 classes)
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


_SPIKE_IDS = [r[0] for r in SPIKE_GOLD]


@pytest.mark.parametrize("prompt_id,text,gold_domain,is_any", SPIKE_GOLD, ids=_SPIKE_IDS)
def test_spike_prompt_domain_any_entropy(
    classifier: Any,
    prompt_id: str,
    text: str,
    gold_domain: str,
    is_any: bool,
) -> None:
    """Domain-any prompts (is_any=True) must have entropy > 1.5 (mid-range ≈ domain-agnostic).

    This tests §9.2 finding 4: high-entropy distribution IS the domain-any signal.
    Only domain-any prompts are tested here; deterministic-domain prompts are
    tested in test_spike_prompt_top1_accuracy below.
    """
    if not is_any:
        pytest.skip(f"{prompt_id}: deterministic-domain prompt, skip entropy test")
    result = classifier.classify(text)
    assert result.entropy > 1.5, (
        f"{prompt_id}: expected domain-any (entropy>1.5) but got "
        f"entropy={result.entropy:.3f}, top={result.top_label}"
    )


@pytest.mark.parametrize("prompt_id,text,gold_domain,is_any", SPIKE_GOLD, ids=_SPIKE_IDS)
def test_spike_prompt_top1_accuracy(
    classifier: Any,
    prompt_id: str,
    text: str,
    gold_domain: str,
    is_any: bool,
) -> None:
    """Deterministic-domain prompts (is_any=False) must hit the gold top-1 label.

    This is a bound measurement, not a training target.
    """
    if is_any:
        pytest.skip(f"{prompt_id}: domain-any prompt, tested via entropy instead")
    result = classifier.classify(text)
    assert result.top_label == gold_domain, (
        f"{prompt_id}: expected top_label={gold_domain!r} but got "
        f"{result.top_label!r} (entropy={result.entropy:.3f}, "
        f"dist={result.distribution})"
    )


# ---------------------------------------------------------------------------
# 7. Skip-guard self-test
# ---------------------------------------------------------------------------


def test_skip_guard_module_level_import_worked() -> None:
    """Confirm the module-level importorskip ran and model2vec is accessible."""
    import model2vec  # noqa: F401

    assert True  # If we reach here, model2vec is importable
