"""Test: braked flag must NOT leak into extras when sentinel converts the row.

Bug (#397): in run_composed(), the E12-brake block (~lines 1100-1110) sets
``braked = True`` while ``agent`` is still ``SELF_HANDLE_SENTINEL`` (non-None).
The sentinel branch (~lines 1114-1116) then sets ``decision="self_handle"`` and
``agent=None`` but does NOT clear ``braked``.  The extras block therefore emits
``extras["braked"] = True``, and ``metric_braked_candidate_quality`` wrongly
counts this self-handle row in its braked denominator.

Contract pinned here (RED):
    After the sentinel converts a row to decision="self_handle", the result's
    ``extras`` must NOT contain ``"braked"``, and the row must not be a braked
    outcome.

The fix is a single ``braked = False`` in the sentinel branch.

Approach:
    ``run_composed`` calls ``DomainClassifier.from_pretrained()`` at import time,
    which requires ``spikes.domain_encoder._classifier``.  That package is absent
    in CI and in this worktree venv.  Rather than ``importorskip`` (which never
    runs), we inject a lightweight fake module into ``sys.modules`` via
    ``monkeypatch.setitem`` so the import resolves to our stub and the function
    can execute end-to-end.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from scripts.corpus.eval._reader import CorpusEntry

# ---------------------------------------------------------------------------
# Fake DomainClassifier helpers
# ---------------------------------------------------------------------------


class _FakeClassifyResult:
    """Minimal classify-result object exposing every attribute run_composed reads.

    Attributes:
        top_label: Top predicted domain label (str).
        distribution: Label-to-probability dict (all labels, sums to ~1.0).
        entropy: Scalar entropy float (not used for routing, only logged).
    """

    def __init__(
        self,
        top_label: str,
        distribution: dict[str, float],
        entropy: float,
    ) -> None:
        """Initialise the fake classify result.

        Args:
            top_label: Predicted domain label.
            distribution: Label → probability mapping.
            entropy: Softmax entropy (diagnostic only in run_composed).
        """
        self.top_label = top_label
        self.distribution = distribution
        self.entropy = entropy


class _FakeDomainClassifier:
    """Stub DomainClassifier that always returns a fixed classify result.

    Attributes:
        _result: The pre-configured _FakeClassifyResult returned by classify().
    """

    def __init__(self, result: _FakeClassifyResult) -> None:
        """Initialise with a fixed result.

        Args:
            result: The _FakeClassifyResult to return on every classify() call.
        """
        self._result = result

    def classify(self, text: str) -> _FakeClassifyResult:
        """Return the fixed result regardless of text.

        Args:
            text: Task description text (ignored by the stub).

        Returns:
            The pre-configured _FakeClassifyResult.
        """
        return self._result


def _make_fake_domain_classifier_class(
    result: _FakeClassifyResult,
) -> type:
    """Return a class whose from_pretrained() yields a _FakeDomainClassifier.

    Args:
        result: The _FakeClassifyResult the classifier will return.

    Returns:
        A class with a ``from_pretrained`` classmethod that returns a
        _FakeDomainClassifier bound to ``result``.
    """

    class FakeDomainClassifierClass:
        """Stub class matching the DomainClassifier API surface."""

        @classmethod
        def from_pretrained(cls) -> _FakeDomainClassifier:
            """Return a stub classifier.

            Returns:
                _FakeDomainClassifier pre-configured with the given result.
            """
            return _FakeDomainClassifier(result)

    return FakeDomainClassifierClass


def _inject_fake_spikes_modules(
    monkeypatch: pytest.MonkeyPatch,
    classify_result: _FakeClassifyResult,
) -> None:
    """Register fake spikes module hierarchy into sys.modules.

    Builds ``spikes``, ``spikes.domain_encoder``, and
    ``spikes.domain_encoder._classifier`` as lightweight module objects so
    that the ``from spikes.domain_encoder._classifier import DomainClassifier``
    inside ``run_composed`` resolves to our stub without touching the real
    package (which is absent in CI).

    All three entries are patched via ``monkeypatch.setitem`` so they are
    automatically removed after the test.

    Args:
        monkeypatch: pytest monkeypatch fixture for cleanup on teardown.
        classify_result: The _FakeClassifyResult the stub classifier returns.

    Returns:
        None
    """
    fake_classifier_class = _make_fake_domain_classifier_class(classify_result)

    # Build the three-level fake module chain.
    spikes_mod = types.ModuleType("spikes")
    domain_encoder_mod = types.ModuleType("spikes.domain_encoder")
    classifier_mod = types.ModuleType("spikes.domain_encoder._classifier")
    classifier_mod.DomainClassifier = fake_classifier_class  # type: ignore[attr-defined]

    # Wire parent refs so attribute access (``spikes.domain_encoder``) works.
    spikes_mod.domain_encoder = domain_encoder_mod  # type: ignore[attr-defined]
    domain_encoder_mod._classifier = classifier_mod  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "spikes", spikes_mod)
    monkeypatch.setitem(sys.modules, "spikes.domain_encoder", domain_encoder_mod)
    monkeypatch.setitem(
        sys.modules, "spikes.domain_encoder._classifier", classifier_mod
    )


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

# Distribution for a clear ``project_meta`` prediction with margin > 0.01
# (so _is_domain_any returns False and domain stays "project_meta").
_PROJECT_META_DISTRIBUTION: dict[str, float] = {
    "project_meta": 0.70,
    "code": 0.25,
    "docs_prose": 0.02,
    "data": 0.02,
    "infra_deploy": 0.01,
}
# margin = 0.70 - 0.25 = 0.45 → well above the 0.01 any-threshold.

_CLASSIFY_RESULT_PROJECT_META = _FakeClassifyResult(
    top_label="project_meta",
    distribution=_PROJECT_META_DISTRIBUTION,
    entropy=1.23,
)


def _make_sentinel_brake_entry() -> CorpusEntry:
    """Build a CorpusEntry whose run_composed path hits the E12-brake + sentinel.

    The task description is crafted so that:
    - ``file_paths`` contains ``"docs/wayfinder/plan.md"``, which matches the
      ``"docs/**/*plan*.md"`` spec-plan glob → E4 (spec_plan_path) fires,
      emitting ``("build", "strong")`` evidence → postures=["build"].
    - The task description contains "failing" → E12 (prose_failure_mention)
      fires, setting e12_fired=True.
    - No stacktrace / test-failure / diagnose signals → E1, E2 do NOT fire.
    - The fake classifier returns domain="project_meta" (margin=0.45 > 0.01,
      NOT domain-any).
    - ``_route_from_postures(postures=["build"], domain="project_meta", ...)``
      calls ``cell_map_lookup("project_meta", "build")`` → SELF_HANDLE_SENTINEL.
      E12 brake in _route_from_postures brakes winning_posture="build" → returns
      (SELF_HANDLE_SENTINEL, 0.5).
    - Back in run_composed brake block:
        ``e12_fired=True and confidence==0.5 and agent is not None``  ← True
        ``winning_posture="build" not in ("diagnose","operate")``       ← True
        → ``braked = True``
    - Sentinel branch: agent==SELF_HANDLE_SENTINEL → decision="self_handle",
      agent=None.  ``braked`` is NOT cleared (that is the bug).
    - extras block: ``if braked`` → extras["braked"]=True leaks.

    Returns:
        A CorpusEntry configured to trigger the bug path.
    """
    task = (
        "Follow the implementation plan in docs/wayfinder/plan.md —"
        " the CI pipeline is failing right now."
    )
    raw: dict[str, Any] = {
        "type": "matcher_decision",
        "session_id": "session-sentinel-brake-001",
        "input": {
            "task_description": task,
            "file_paths": ["docs/wayfinder/plan.md"],
            "agent_mentions": [],
            "tool_mentions": [],
            "command_prefix": None,
        },
        "output": {"decision": "self_handle", "agent": None, "confidence": 0.5},
        "corpus_id": 999,
        "stratum": {
            "decision_band": "self_handle",
            "td_length_band": "short",
            "file_paths_present": True,
        },
    }
    return CorpusEntry(
        corpus_id=999,
        task_description=task,
        file_paths=["docs/wayfinder/plan.md"],
        agent_mentions=[],
        tool_mentions=[],
        command_prefix=None,
        stratum=raw["stratum"],
        raw=raw,
    )


# ---------------------------------------------------------------------------
# Catalog fixture
# ---------------------------------------------------------------------------

_CATALOG_ENTRIES_RAW: list[dict[str, Any]] = [
    {
        "name": "code-writer",
        "kind": "agent",
        "source": "owned",
        "routable": True,
        "applicable_agents": [],
        "applicable_skills": [],
        "triggers": {
            "command_prefixes": [],
            "agent_mentions": ["code-writer"],
            "path_globs": ["**/*.py"],
            "path_globs_excluded": [],
            "keywords": [{"term": "implement", "weight": 1.0}],
            "tool_mentions": [],
            "excludes": [],
        },
    },
    {
        "name": "project-planner",
        "kind": "agent",
        "source": "owned",
        "routable": True,
        "applicable_agents": [],
        "applicable_skills": [],
        "triggers": {
            "command_prefixes": [],
            "agent_mentions": ["project-planner"],
            "path_globs": [],
            "path_globs_excluded": [],
            "keywords": [{"term": "plan", "weight": 1.0}],
            "tool_mentions": [],
            "excludes": [],
        },
    },
]


@pytest.fixture()
def catalog_path(tmp_path: Path) -> Path:
    """Write a minimal catalog JSON file and return its path.

    Args:
        tmp_path: pytest-provided temporary directory.

    Returns:
        Path to the written catalog JSON file.
    """
    catalog = {"entries": _CATALOG_ENTRIES_RAW}
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(catalog), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRunComposedSentinelBrakeNotLeaked:
    """Verify that ``braked`` does not leak into extras after sentinel conversion.

    After the sentinel converts a row to ``decision="self_handle"``:
    - ``result.decision`` must be ``"self_handle"``.
    - ``result.agent`` must be ``None``.
    - ``"braked"`` must NOT appear in ``result.extras``.

    These tests are RED until the implementer adds ``braked = False``
    in the sentinel branch of ``run_composed``.
    """

    def test_sentinel_row_decision_is_self_handle(
        self,
        monkeypatch: pytest.MonkeyPatch,
        catalog_path: Path,
    ) -> None:
        """Sentinel conversion yields decision="self_handle" (pre-condition check).

        This test acts as the pre-condition guard: if it fails the test
        environment is misconfigured (wrong domain, no sentinel path hit).

        Args:
            monkeypatch: pytest monkeypatch fixture.
            catalog_path: Path to the minimal catalog JSON fixture.
        """
        _inject_fake_spikes_modules(monkeypatch, _CLASSIFY_RESULT_PROJECT_META)
        from scripts.corpus.eval._systems import run_composed

        entry = _make_sentinel_brake_entry()
        results = run_composed([entry], catalog_path)

        assert len(results) == 1, "Expected exactly one result"
        result = results[0]
        assert result.decision == "self_handle", (
            f"Expected decision='self_handle' (sentinel path), got {result.decision!r}. "
            "Check the fake classifier is returning 'project_meta' domain."
        )

    def test_sentinel_row_agent_is_none(
        self,
        monkeypatch: pytest.MonkeyPatch,
        catalog_path: Path,
    ) -> None:
        """Sentinel conversion yields agent=None (sentinel clears the agent field).

        Args:
            monkeypatch: pytest monkeypatch fixture.
            catalog_path: Path to the minimal catalog JSON fixture.
        """
        _inject_fake_spikes_modules(monkeypatch, _CLASSIFY_RESULT_PROJECT_META)
        from scripts.corpus.eval._systems import run_composed

        entry = _make_sentinel_brake_entry()
        results = run_composed([entry], catalog_path)

        assert len(results) == 1
        result = results[0]
        assert result.agent is None, (
            f"Expected agent=None after sentinel conversion, got {result.agent!r}"
        )

    def test_sentinel_row_braked_not_in_extras(
        self,
        monkeypatch: pytest.MonkeyPatch,
        catalog_path: Path,
    ) -> None:
        """Braked flag must NOT appear in extras after sentinel converts the row.

        This is the PRIMARY contract assertion.  Before the fix, ``braked=True``
        leaks into extras because the sentinel branch does not clear the
        ``braked`` local variable.  After the fix (``braked = False`` in the
        sentinel branch), this assertion passes.

        Args:
            monkeypatch: pytest monkeypatch fixture.
            catalog_path: Path to the minimal catalog JSON fixture.
        """
        _inject_fake_spikes_modules(monkeypatch, _CLASSIFY_RESULT_PROJECT_META)
        from scripts.corpus.eval._systems import run_composed

        entry = _make_sentinel_brake_entry()
        results = run_composed([entry], catalog_path)

        assert len(results) == 1
        result = results[0]
        assert "braked" not in result.extras, (
            f"Bug: 'braked' leaked into extras for a self_handle row. "
            f"extras={result.extras!r}. "
            "The sentinel branch must clear braked=False before extras are built."
        )

    def test_sentinel_row_does_not_count_as_braked_outcome(
        self,
        monkeypatch: pytest.MonkeyPatch,
        catalog_path: Path,
    ) -> None:
        """A self_handle row is not a braked outcome (extras["braked"] absent or False).

        Complements the primary assertion: confirms the row would NOT be counted
        by ``metric_braked_candidate_quality`` (which filters on
        ``extras.get("braked")``) after the fix.

        Args:
            monkeypatch: pytest monkeypatch fixture.
            catalog_path: Path to the minimal catalog JSON fixture.
        """
        _inject_fake_spikes_modules(monkeypatch, _CLASSIFY_RESULT_PROJECT_META)
        from scripts.corpus.eval._systems import run_composed

        entry = _make_sentinel_brake_entry()
        results = run_composed([entry], catalog_path)

        assert len(results) == 1
        result = results[0]
        is_braked = bool(result.extras.get("braked", False))
        assert not is_braked, (
            f"self_handle row must not be counted as a braked outcome; "
            f"extras.get('braked')={result.extras.get('braked')!r}"
        )
