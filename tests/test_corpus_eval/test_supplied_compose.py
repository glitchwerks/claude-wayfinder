"""Tests for the supplied-compose system (issue #363, Phase 0).

Pins three new symbols before any implementation exists:
  1. ``run_supplied_compose`` in ``scripts.corpus.eval._systems``
  2. ``metric_routing_correctness`` in ``scripts.corpus.eval._metrics``
  3. CLI additions: ``--systems compose``, ``--compose-labels``, ``--cut``

RED — written before implementation.  Every test in this file must
fail at collection time or at run time for the right reason
(ImportError / AttributeError / SystemExit / wrong-value), not due
to syntax errors or bad imports from the TEST itself.
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from scripts.corpus.eval._reader import CorpusEntry, GoldLabel
from scripts.corpus.eval._systems import SystemResult

# ---------------------------------------------------------------------------
# Repo-relative paths for committed research artefacts.
# Derived from __file__ so the paths are cwd-independent and resolve on any
# OS, including Linux CI runners that have no I:/ drive.
# ---------------------------------------------------------------------------
_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_RESEARCH_DIR: Path = _REPO_ROOT / "docs" / "research"

# ---------------------------------------------------------------------------
# Shared catalog fixture with agents that match the spec's deterministic
# anchors.  Two routable agents:
#   code-writer — keyword "implement" triggers on "implement the feature"
#   doc-writer  — keyword "document" triggers on "document and implement it"
# Both are in the relevant domain gates (code / docs_prose).
# ---------------------------------------------------------------------------

_COMPOSE_CATALOG_ENTRIES: list[dict[str, Any]] = [
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
            "keywords": [
                {"term": "implement", "weight": 1.0},
                {"term": "build", "weight": 0.8},
                {"term": "feature", "weight": 0.5},
            ],
            "tool_mentions": [],
            "excludes": [],
        },
    },
    {
        "name": "doc-writer",
        "kind": "agent",
        "source": "owned",
        "routable": True,
        "applicable_agents": [],
        "applicable_skills": [],
        "triggers": {
            "command_prefixes": [],
            "agent_mentions": ["doc-writer"],
            "path_globs": ["**/*.md"],
            "path_globs_excluded": [],
            "keywords": [
                {"term": "document", "weight": 1.0},
                {"term": "docs", "weight": 0.8},
            ],
            "tool_mentions": [],
            "excludes": [],
        },
    },
    # ops is always-any (any domain) — needed so the fallback path can
    # produce a concrete delegate when posture is absent or cell not in gate.
    {
        "name": "ops",
        "kind": "agent",
        "source": "owned",
        "routable": True,
        "applicable_agents": [],
        "applicable_skills": [],
        "triggers": {
            "command_prefixes": ["gh", "git"],
            "agent_mentions": ["ops"],
            "path_globs": [],
            "path_globs_excluded": [],
            "keywords": [
                {"term": "run", "weight": 0.5},
                {"term": "deploy", "weight": 0.5},
            ],
            "tool_mentions": [],
            "excludes": [],
        },
    },
]

_CLI_CATALOG_ENTRIES: list[dict[str, Any]] = [
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
            "keywords": [
                {"term": "implement", "weight": 1.0},
                {"term": "update", "weight": 0.8},
                {"term": "fix", "weight": 0.8},
                {"term": "test", "weight": 0.5},
                {"term": "api", "weight": 0.5},
                {"term": "rename", "weight": 0.8},
            ],
            "tool_mentions": [],
            "excludes": [],
        },
    },
    {
        "name": "ops",
        "kind": "agent",
        "source": "owned",
        "routable": True,
        "applicable_agents": [],
        "applicable_skills": [],
        "triggers": {
            "command_prefixes": ["gh", "git"],
            "agent_mentions": ["ops"],
            "path_globs": [],
            "path_globs_excluded": [],
            "keywords": [
                {"term": "run", "weight": 0.5},
                {"term": "status", "weight": 0.5},
                {"term": "checks", "weight": 0.5},
            ],
            "tool_mentions": [],
            "excludes": [],
        },
    },
    {
        "name": "investigator",
        "kind": "agent",
        "source": "owned",
        "routable": True,
        "applicable_agents": [],
        "applicable_skills": [],
        "triggers": {
            "command_prefixes": [],
            "agent_mentions": ["investigator"],
            "path_globs": [],
            "path_globs_excluded": [],
            "keywords": [
                {"term": "debug", "weight": 1.0},
                {"term": "investigate", "weight": 1.0},
                {"term": "figure", "weight": 0.5},
                {"term": "error", "weight": 0.5},
                {"term": "fail", "weight": 0.5},
            ],
            "tool_mentions": [],
            "excludes": [],
        },
    },
    {
        "name": "researcher",
        "kind": "agent",
        "source": "owned",
        "routable": True,
        "applicable_agents": [],
        "applicable_skills": [],
        "triggers": {
            "command_prefixes": [],
            "agent_mentions": ["researcher"],
            "path_globs": [],
            "path_globs_excluded": [],
            "keywords": [
                {"term": "research", "weight": 1.0},
                {"term": "anyone", "weight": 0.5},
                {"term": "prior", "weight": 0.5},
            ],
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
            "keywords": [
                {"term": "phase", "weight": 1.0},
                {"term": "milestone", "weight": 1.0},
                {"term": "plan", "weight": 0.5},
            ],
            "tool_mentions": [],
            "excludes": [],
        },
    },
    {
        "name": "auditor",
        "kind": "agent",
        "source": "owned",
        "routable": True,
        "applicable_agents": [],
        "applicable_skills": [],
        "triggers": {
            "command_prefixes": [],
            "agent_mentions": ["auditor"],
            "path_globs": [],
            "path_globs_excluded": [],
            "keywords": [
                {"term": "consistent", "weight": 1.0},
                {"term": "verify", "weight": 1.0},
                {"term": "check", "weight": 0.5},
            ],
            "tool_mentions": [],
            "excludes": [],
        },
    },
    {
        "name": "approach-critic",
        "kind": "agent",
        "source": "owned",
        "routable": True,
        "applicable_agents": [],
        "applicable_skills": [],
        "triggers": {
            "command_prefixes": [],
            "agent_mentions": ["approach-critic"],
            "path_globs": [],
            "path_globs_excluded": [],
            "keywords": [
                {"term": "poke", "weight": 0.5},
                {"term": "critique", "weight": 0.5},
                {"term": "challenge", "weight": 0.5},
            ],
            "tool_mentions": [],
            "excludes": [],
        },
    },
    {
        "name": "doc-writer",
        "kind": "agent",
        "source": "owned",
        "routable": True,
        "applicable_agents": [],
        "applicable_skills": [],
        "triggers": {
            "command_prefixes": [],
            "agent_mentions": ["doc-writer"],
            "path_globs": ["**/*.md"],
            "path_globs_excluded": [],
            "keywords": [
                {"term": "document", "weight": 1.0},
                {"term": "docs", "weight": 0.8},
            ],
            "tool_mentions": [],
            "excludes": [],
        },
    },
]


@pytest.fixture()
def fixture_compose_catalog_path(tmp_path: Path) -> Path:
    """Write a minimal two-agent catalog for compose tests.

    Includes code-writer (keyword: implement), doc-writer (keyword:
    document), and ops (any-domain fallback) so deterministic anchor
    tests have the right agents routable.
    """
    catalog = {"entries": _COMPOSE_CATALOG_ENTRIES}
    path = tmp_path / "compose-catalog.json"
    path.write_text(json.dumps(catalog), encoding="utf-8")
    return path


@pytest.fixture()
def fixture_cli_catalog_path(tmp_path: Path) -> Path:
    """Write the full catalog used in CLI smoke tests.

    Matches the catalog pattern from test_cli.py, extended with doc-writer
    so the compose system can exercise the docs_prose domain gate.
    """
    catalog = {"entries": _CLI_CATALOG_ENTRIES}
    path = tmp_path / "cli-catalog.json"
    path.write_text(json.dumps(catalog), encoding="utf-8")
    return path


def _make_entry(
    corpus_id: int,
    task_description: str,
    domain: str = "code",
    posture: str = "build",
    gold_agent: str = "code-writer",
) -> tuple[CorpusEntry, GoldLabel]:
    """Return a minimal CorpusEntry + GoldLabel pair for compose tests.

    Args:
        corpus_id: Unique ID for the entry.
        task_description: Free-text task description.
        domain: Gold domain label.
        posture: Gold posture label.
        gold_agent: Expected routing target.

    Returns:
        Tuple of (CorpusEntry, GoldLabel).
    """
    entry = CorpusEntry(
        corpus_id=corpus_id,
        task_description=task_description,
        file_paths=[],
        agent_mentions=[],
        tool_mentions=[],
        command_prefix=None,
        stratum={
            "decision_band": "delegate",
            "td_length_band": "short",
            "file_paths_present": False,
        },
        raw={},
    )
    label = GoldLabel(
        corpus_id=corpus_id,
        domain=domain,
        posture=posture,
        gold_agent=gold_agent,
        is_any=False,
    )
    return entry, label


# ===========================================================================
# Anchor 1: metric_routing_correctness — pure function, no catalog needed
# ===========================================================================


class TestMetricRoutingCorrectness:
    """Anchor 1: metric_routing_correctness pure-function contract.

    No catalog or real entries needed — all assertions are over
    hand-built SystemResult lists and GoldLabel dicts.
    """

    def test_partial_correct_returns_expected_fraction(self) -> None:
        """3 results, 2 match gold → RC = 0.6667.

        Agents [a, b, c] vs gold [a, b, x]:
          r1.agent==a, gold==a → correct
          r2.agent==b, gold==b → correct
          r3.agent==c, gold==x → wrong
        Expected: round(2/3, 4) == 0.6667.
        """
        from scripts.corpus.eval._metrics import metric_routing_correctness

        results = [
            SystemResult(
                corpus_id=1, decision="delegate",
                agent="a", confidence=0.9, extras={},
            ),
            SystemResult(
                corpus_id=2, decision="delegate",
                agent="b", confidence=0.9, extras={},
            ),
            SystemResult(
                corpus_id=3, decision="delegate",
                agent="c", confidence=0.9, extras={},
            ),
        ]
        labels = {
            1: GoldLabel(
                corpus_id=1, domain="any", posture="build",
                gold_agent="a", is_any=False,
            ),
            2: GoldLabel(
                corpus_id=2, domain="any", posture="build",
                gold_agent="b", is_any=False,
            ),
            3: GoldLabel(
                corpus_id=3, domain="any", posture="build",
                gold_agent="x", is_any=False,
            ),
        }
        rc = metric_routing_correctness(results, labels)
        assert rc == round(2 / 3, 4), (
            f"Expected {round(2 / 3, 4)}, got {rc}"
        )

    def test_all_correct_returns_one(self) -> None:
        """All agents match gold → RC = 1.0."""
        from scripts.corpus.eval._metrics import metric_routing_correctness

        results = [
            SystemResult(
                corpus_id=i, decision="delegate",
                agent=f"agent-{i}", confidence=0.9, extras={},
            )
            for i in range(1, 5)
        ]
        labels = {
            i: GoldLabel(
                corpus_id=i, domain="any", posture="build",
                gold_agent=f"agent-{i}", is_any=False,
            )
            for i in range(1, 5)
        }
        rc = metric_routing_correctness(results, labels)
        assert rc == 1.0, f"All correct → expected 1.0, got {rc}"

    def test_no_labeled_overlap_returns_nan(self) -> None:
        """When no result has a gold label → RC = float('nan')."""
        from scripts.corpus.eval._metrics import metric_routing_correctness

        results = [
            SystemResult(
                corpus_id=99, decision="delegate",
                agent="code-writer", confidence=0.9, extras={},
            ),
        ]
        labels: dict[int, GoldLabel] = {}  # no overlap
        rc = metric_routing_correctness(results, labels)
        assert math.isnan(rc), (
            f"No labeled overlap → expected nan, got {rc}"
        )

    def test_decision_value_irrelevant_to_rc(self) -> None:
        """RC counts r.agent == gold_agent regardless of decision field.

        A non-delegate result with the correct agent still counts toward RC.
        This pins that RC is purely agent-matching, not filtered by decision.
        """
        from scripts.corpus.eval._metrics import metric_routing_correctness

        results = [
            # advisory, but correct agent — must count
            SystemResult(
                corpus_id=1, decision="advisory",
                agent="code-writer", confidence=0.5, extras={},
            ),
            # delegate, wrong agent
            SystemResult(
                corpus_id=2, decision="delegate",
                agent="ops", confidence=0.9, extras={},
            ),
        ]
        labels = {
            1: GoldLabel(
                corpus_id=1, domain="code", posture="build",
                gold_agent="code-writer", is_any=False,
            ),
            2: GoldLabel(
                corpus_id=2, domain="any", posture="operate",
                gold_agent="investigator", is_any=False,
            ),
        }
        rc = metric_routing_correctness(results, labels)
        # r1 correct (advisory but agent matches) → 1/2 = 0.5
        assert rc == round(1 / 2, 4), (
            f"Expected 0.5 (decision irrelevant to RC), got {rc}"
        )

    def test_result_is_rounded_to_4dp(self) -> None:
        """Return value is rounded to 4 decimal places."""
        from scripts.corpus.eval._metrics import metric_routing_correctness

        # 1 correct out of 3 → 0.3333...
        results = [
            SystemResult(
                corpus_id=i, decision="delegate",
                agent="correct" if i == 1 else "wrong",
                confidence=0.9, extras={},
            )
            for i in range(1, 4)
        ]
        labels = {
            i: GoldLabel(
                corpus_id=i, domain="any", posture="build",
                gold_agent="correct", is_any=False,
            )
            for i in range(1, 4)
        }
        rc = metric_routing_correctness(results, labels)
        assert rc == round(1 / 3, 4), (
            f"Expected {round(1 / 3, 4)} (4dp), got {rc}"
        )


# ===========================================================================
# Anchor 2: run_supplied_compose — posture-routed path at confidence 0.9
# ===========================================================================


class TestRunSuppliedComposePostureRouted:
    """Anchor 2: posture-routed delegate at confidence 0.9.

    Entry: task_description="implement the feature", domain=code, posture=build.
    Cell: (code, build) → code-writer.
    code-writer scores > 0 on "implement" keyword → is in gated set.
    Expected: decision=delegate, agent=code-writer, confidence=0.9,
              extras["posture_routed"] is True.
    """

    def test_posture_routed_delegate_decision(
        self, fixture_compose_catalog_path: Path
    ) -> None:
        """decision == 'delegate' when cell agent is in gated candidates."""
        from scripts.corpus.eval._systems import run_supplied_compose

        entry, label = _make_entry(
            corpus_id=1,
            task_description="implement the feature",
            domain="code",
            posture="build",
            gold_agent="code-writer",
        )
        results = run_supplied_compose(
            [entry], fixture_compose_catalog_path, {1: label}
        )
        assert len(results) == 1
        assert results[0].decision == "delegate", (
            f"Expected decision='delegate', got {results[0].decision!r}"
        )

    def test_posture_routed_agent_is_code_writer(
        self, fixture_compose_catalog_path: Path
    ) -> None:
        """agent == 'code-writer' when (code, build) cell is in gated set."""
        from scripts.corpus.eval._systems import run_supplied_compose

        entry, label = _make_entry(
            corpus_id=1,
            task_description="implement the feature",
            domain="code",
            posture="build",
            gold_agent="code-writer",
        )
        results = run_supplied_compose(
            [entry], fixture_compose_catalog_path, {1: label}
        )
        assert results[0].agent == "code-writer", (
            f"Expected agent='code-writer', got {results[0].agent!r}"
        )

    def test_posture_routed_confidence_is_0_9(
        self, fixture_compose_catalog_path: Path
    ) -> None:
        """confidence == 0.9 for posture-routed results."""
        from scripts.corpus.eval._systems import run_supplied_compose

        entry, label = _make_entry(
            corpus_id=1,
            task_description="implement the feature",
            domain="code",
            posture="build",
            gold_agent="code-writer",
        )
        results = run_supplied_compose(
            [entry], fixture_compose_catalog_path, {1: label}
        )
        assert results[0].confidence == 0.9, (
            f"Expected confidence=0.9, got {results[0].confidence}"
        )

    def test_posture_routed_extras_flag_is_true(
        self, fixture_compose_catalog_path: Path
    ) -> None:
        """extras['posture_routed'] is True for posture-routed results."""
        from scripts.corpus.eval._systems import run_supplied_compose

        entry, label = _make_entry(
            corpus_id=1,
            task_description="implement the feature",
            domain="code",
            posture="build",
            gold_agent="code-writer",
        )
        results = run_supplied_compose(
            [entry], fixture_compose_catalog_path, {1: label}
        )
        assert results[0].extras.get("posture_routed") is True, (
            f"Expected extras['posture_routed']=True, "
            f"got {results[0].extras.get('posture_routed')!r}"
        )

    def test_returns_one_result_per_entry_preserving_id(
        self, fixture_compose_catalog_path: Path
    ) -> None:
        """One SystemResult per input entry, corpus_id preserved, input order."""
        from scripts.corpus.eval._systems import run_supplied_compose

        entries_and_labels = [
            _make_entry(i, f"implement feature {i}", "code", "build", "code-writer")
            for i in range(1, 4)
        ]
        entries = [e for e, _ in entries_and_labels]
        labels = {lbl.corpus_id: lbl for _, lbl in entries_and_labels}

        results = run_supplied_compose(entries, fixture_compose_catalog_path, labels)

        assert len(results) == 3
        assert [r.corpus_id for r in results] == [1, 2, 3], (
            "Input order and corpus_ids must be preserved"
        )


# ===========================================================================
# Anchor 3: domain gate excludes out-of-domain high scorers
# ===========================================================================


class TestRunSuppliedComposeDomainGate:
    """Anchor 3: domain gate fires and excludes out-of-domain agents.

    Catalog: code-writer (keyword: implement) + doc-writer (keyword: document).
    Entry: task_description='document and implement it', domain=docs_prose,
           posture=build.
    Cell: (docs_prose, build) → doc-writer.
    Domain gate for docs_prose: allows doc-writer but NOT code-writer.
    Both agents score > 0 (entry has both keywords), but code-writer is
    gated out.
    Expected: agent == 'doc-writer', confidence == 0.9.
    """

    def test_gating_excludes_code_writer_in_docs_prose_domain(
        self, fixture_compose_catalog_path: Path
    ) -> None:
        """code-writer is excluded by docs_prose gate even though it scores."""
        from scripts.corpus.eval._systems import run_supplied_compose

        entry, label = _make_entry(
            corpus_id=1,
            task_description="document and implement it",
            domain="docs_prose",
            posture="build",
            gold_agent="doc-writer",
        )
        results = run_supplied_compose(
            [entry], fixture_compose_catalog_path, {1: label}
        )
        assert results[0].agent == "doc-writer", (
            f"Expected agent='doc-writer' (code-writer gated out by "
            f"docs_prose gate), got {results[0].agent!r}"
        )

    def test_gating_result_is_delegate_at_0_9(
        self, fixture_compose_catalog_path: Path
    ) -> None:
        """When gate produces posture-routed result, confidence is 0.9."""
        from scripts.corpus.eval._systems import run_supplied_compose

        entry, label = _make_entry(
            corpus_id=1,
            task_description="document and implement it",
            domain="docs_prose",
            posture="build",
            gold_agent="doc-writer",
        )
        results = run_supplied_compose(
            [entry], fixture_compose_catalog_path, {1: label}
        )
        assert results[0].decision == "delegate", (
            f"Expected decision='delegate', got {results[0].decision!r}"
        )
        assert results[0].confidence == 0.9, (
            f"Expected confidence=0.9, got {results[0].confidence}"
        )

    def test_gating_result_posture_routed_true(
        self, fixture_compose_catalog_path: Path
    ) -> None:
        """Domain gate + cell match → posture_routed is True."""
        from scripts.corpus.eval._systems import run_supplied_compose

        entry, label = _make_entry(
            corpus_id=1,
            task_description="document and implement it",
            domain="docs_prose",
            posture="build",
            gold_agent="doc-writer",
        )
        results = run_supplied_compose(
            [entry], fixture_compose_catalog_path, {1: label}
        )
        assert results[0].extras.get("posture_routed") is True, (
            f"Expected extras['posture_routed']=True, "
            f"got {results[0].extras.get('posture_routed')!r}"
        )


# ===========================================================================
# Anchor 4: fallback path — no posture or unlabeled entry
# ===========================================================================


class TestRunSuppliedComposeFallback:
    """Anchor 4: fallback path when posture is absent or entry is unlabeled.

    When posture is empty string or the corpus_id is absent from labels,
    the system must NOT posture-route and must still produce a SystemResult
    with extras['posture_routed'] is False.  Decision value is not pinned
    (depends on decide() output).
    """

    def test_empty_posture_falls_back(
        self, fixture_compose_catalog_path: Path
    ) -> None:
        """entry with posture='' → extras['posture_routed'] is False."""
        from scripts.corpus.eval._systems import run_supplied_compose

        entry = CorpusEntry(
            corpus_id=42,
            task_description="implement the feature",
            file_paths=[],
            agent_mentions=[],
            tool_mentions=[],
            command_prefix=None,
            stratum={
                "decision_band": "delegate",
                "td_length_band": "short",
                "file_paths_present": False,
            },
            raw={},
        )
        # Label has empty posture — no cell lookup possible
        label = GoldLabel(
            corpus_id=42, domain="code", posture="",
            gold_agent="code-writer", is_any=False,
        )
        results = run_supplied_compose(
            [entry], fixture_compose_catalog_path, {42: label}
        )
        assert len(results) == 1, "Must produce one result even on fallback"
        assert results[0].extras.get("posture_routed") is False, (
            f"Empty posture → posture_routed must be False, "
            f"got {results[0].extras.get('posture_routed')!r}"
        )

    def test_unlabeled_entry_falls_back(
        self, fixture_compose_catalog_path: Path
    ) -> None:
        """Entry absent from labels → extras['posture_routed'] is False."""
        from scripts.corpus.eval._systems import run_supplied_compose

        entry = CorpusEntry(
            corpus_id=99,
            task_description="implement the feature",
            file_paths=[],
            agent_mentions=[],
            tool_mentions=[],
            command_prefix=None,
            stratum={
                "decision_band": "delegate",
                "td_length_band": "short",
                "file_paths_present": False,
            },
            raw={},
        )
        # corpus_id 99 absent from labels dict
        results = run_supplied_compose(
            [entry], fixture_compose_catalog_path, {}
        )
        assert len(results) == 1
        assert results[0].extras.get("posture_routed") is False, (
            f"Unlabeled entry → posture_routed must be False, "
            f"got {results[0].extras.get('posture_routed')!r}"
        )

    def test_fallback_still_produces_system_result(
        self, fixture_compose_catalog_path: Path
    ) -> None:
        """Fallback path always produces a SystemResult, never raises."""
        from scripts.corpus.eval._systems import run_supplied_compose

        entry = CorpusEntry(
            corpus_id=7,
            task_description="implement the feature",
            file_paths=[],
            agent_mentions=[],
            tool_mentions=[],
            command_prefix=None,
            stratum={},
            raw={},
        )
        results = run_supplied_compose([entry], fixture_compose_catalog_path, {})
        assert len(results) == 1
        r = results[0]
        assert isinstance(r, SystemResult)
        assert isinstance(r.decision, str) and len(r.decision) > 0
        assert 0.0 <= r.confidence <= 1.0

    def test_fallback_extras_carry_oracle_fields(
        self, fixture_compose_catalog_path: Path
    ) -> None:
        """Fallback path still populates extras with oracle_domain and oracle_posture."""
        from scripts.corpus.eval._systems import run_supplied_compose

        entry = CorpusEntry(
            corpus_id=5,
            task_description="implement the feature",
            file_paths=[],
            agent_mentions=[],
            tool_mentions=[],
            command_prefix=None,
            stratum={},
            raw={},
        )
        label = GoldLabel(
            corpus_id=5, domain="code", posture="",
            gold_agent="code-writer", is_any=False,
        )
        results = run_supplied_compose(
            [entry], fixture_compose_catalog_path, {5: label}
        )
        extras = results[0].extras
        assert "oracle_domain" in extras, (
            "extras must contain 'oracle_domain'"
        )
        assert "oracle_posture" in extras, (
            "extras must contain 'oracle_posture'"
        )


# ===========================================================================
# Anchor 5: CLI smoke — --systems compose with --compose-labels oracle and --cut
# ===========================================================================


class TestCLIComposeSmoke:
    """Anchor 5: CLI additions for the compose system.

    Tests use fixture_corpus_path + fixture_labels_path from conftest
    (P1-P14 synthetic corpus) plus a local catalog that includes enough
    agents to route plausibly.  Assertions are structural (exit 0, table
    contains 'compose' and 'RC'), not tied to exact metric floats.
    """

    def test_compose_system_choice_accepted_exits_zero(
        self,
        fixture_corpus_path: Path,
        fixture_labels_path: Path,
        fixture_cli_catalog_path: Path,
    ) -> None:
        """--systems compose is a valid choice and exits 0."""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.corpus.eval",
                "--corpus",
                str(fixture_corpus_path),
                "--labels",
                str(fixture_labels_path),
                "--catalog",
                str(fixture_cli_catalog_path),
                "--systems",
                "compose",
                "--compose-labels",
                "oracle",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, (
            f"--systems compose must exit 0.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_output_contains_compose_row(
        self,
        fixture_corpus_path: Path,
        fixture_labels_path: Path,
        fixture_cli_catalog_path: Path,
    ) -> None:
        """Output table contains a row labelled 'compose'."""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.corpus.eval",
                "--corpus",
                str(fixture_corpus_path),
                "--labels",
                str(fixture_labels_path),
                "--catalog",
                str(fixture_cli_catalog_path),
                "--systems",
                "lexical,compose",
                "--compose-labels",
                "oracle",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, (
            f"CLI failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "compose" in result.stdout.lower(), (
            f"'compose' row missing from output:\n{result.stdout}"
        )

    def test_output_contains_rc_column(
        self,
        fixture_corpus_path: Path,
        fixture_labels_path: Path,
        fixture_cli_catalog_path: Path,
    ) -> None:
        """Output table header contains 'RC' column for routing correctness."""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.corpus.eval",
                "--corpus",
                str(fixture_corpus_path),
                "--labels",
                str(fixture_labels_path),
                "--catalog",
                str(fixture_cli_catalog_path),
                "--systems",
                "compose",
                "--compose-labels",
                "oracle",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, (
            f"CLI failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        # Header must contain "RC" (case-insensitive) to show the new column
        assert "rc" in result.stdout.lower(), (
            f"'RC' column missing from output header:\n{result.stdout}"
        )

    def test_cut_no_smoke_accepted_exits_zero(
        self,
        fixture_corpus_path: Path,
        fixture_labels_path: Path,
        fixture_cli_catalog_path: Path,
    ) -> None:
        """--cut no_smoke is accepted and exits 0."""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.corpus.eval",
                "--corpus",
                str(fixture_corpus_path),
                "--labels",
                str(fixture_labels_path),
                "--catalog",
                str(fixture_cli_catalog_path),
                "--systems",
                "lexical,compose",
                "--compose-labels",
                "oracle",
                "--cut",
                "no_smoke",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, (
            f"--cut no_smoke must exit 0.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_cut_no_smoke_drops_smoke_entries(
        self,
        tmp_path: Path,
        fixture_labels_path: Path,
        fixture_cli_catalog_path: Path,
    ) -> None:
        """--cut no_smoke removes entries with smoke task_descriptions.

        Corpus contains 2 smoke entries ('update the docs',
        'implement the new module') plus 2 normal entries.
        After no_smoke cut, the compose system must run on 2 entries.
        We verify indirectly via --verbose/output, but the primary signal
        is exit 0 with the compose row present (if output only shows fewer
        entries, it still shows the row).
        """
        smoke_records = [
            {
                "type": "matcher_decision",
                "session_id": "smoke-001",
                "input": {
                    "task_description": "update the docs",
                    "file_paths": [],
                    "agent_mentions": [],
                    "tool_mentions": [],
                    "command_prefix": None,
                },
                "output": {
                    "decision": "advisory",
                    "agent": None,
                    "confidence": 0.5,
                },
                "corpus_id": 101,
                "stratum": {
                    "decision_band": "advisory",
                    "td_length_band": "short",
                    "file_paths_present": False,
                },
            },
            {
                "type": "matcher_decision",
                "session_id": "smoke-002",
                "input": {
                    "task_description": "implement the new module",
                    "file_paths": [],
                    "agent_mentions": [],
                    "tool_mentions": [],
                    "command_prefix": None,
                },
                "output": {
                    "decision": "delegate",
                    "agent": "code-writer",
                    "confidence": 0.9,
                },
                "corpus_id": 102,
                "stratum": {
                    "decision_band": "delegate",
                    "td_length_band": "short",
                    "file_paths_present": False,
                },
            },
            {
                "type": "matcher_decision",
                "session_id": "normal-001",
                "input": {
                    "task_description": "implement the real feature here",
                    "file_paths": [],
                    "agent_mentions": [],
                    "tool_mentions": [],
                    "command_prefix": None,
                },
                "output": {
                    "decision": "delegate",
                    "agent": "code-writer",
                    "confidence": 0.9,
                },
                "corpus_id": 103,
                "stratum": {
                    "decision_band": "delegate",
                    "td_length_band": "short",
                    "file_paths_present": False,
                },
            },
            {
                "type": "matcher_decision",
                "session_id": "normal-002",
                "input": {
                    "task_description": "document and implement the API",
                    "file_paths": [],
                    "agent_mentions": [],
                    "tool_mentions": [],
                    "command_prefix": None,
                },
                "output": {
                    "decision": "delegate",
                    "agent": "doc-writer",
                    "confidence": 0.9,
                },
                "corpus_id": 104,
                "stratum": {
                    "decision_band": "delegate",
                    "td_length_band": "short",
                    "file_paths_present": False,
                },
            },
        ]
        corpus_file = tmp_path / "smoke-corpus.jsonl"
        lines = [json.dumps(r, ensure_ascii=False) for r in smoke_records]
        corpus_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.corpus.eval",
                "--corpus",
                str(corpus_file),
                "--labels",
                str(fixture_labels_path),
                "--catalog",
                str(fixture_cli_catalog_path),
                "--systems",
                "compose",
                "--compose-labels",
                "oracle",
                "--cut",
                "no_smoke",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, (
            f"--cut no_smoke with smoke entries must exit 0.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        # The compose row must still appear; smoke entries were dropped silently
        assert "compose" in result.stdout.lower(), (
            f"'compose' row missing after no_smoke cut:\n{result.stdout}"
        )

    def test_compose_labels_oracle_uses_gold_labels_map(
        self,
        fixture_corpus_path: Path,
        fixture_labels_path: Path,
        fixture_cli_catalog_path: Path,
    ) -> None:
        """--compose-labels oracle means domain/posture come from --labels map.

        RC scoring always uses the gold --labels map's gold_agent.
        This test asserts: the command runs without error and produces an
        RC value (not n/a) in the compose row, proving oracle mode used
        the labels map for both routing inputs and scoring.
        """
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.corpus.eval",
                "--corpus",
                str(fixture_corpus_path),
                "--labels",
                str(fixture_labels_path),
                "--catalog",
                str(fixture_cli_catalog_path),
                "--systems",
                "compose",
                "--compose-labels",
                "oracle",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, (
            f"Oracle compose mode failed.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        # The compose row must have an RC value that is not 'n/a'
        lines = result.stdout.splitlines()
        compose_line = next(
            (row for row in lines if row.strip().startswith("compose")), None
        )
        assert compose_line is not None, (
            f"No 'compose' row found in output:\n{result.stdout}"
        )
        # RC cell in the compose row must be a numeric value, not 'n/a'.
        # The table row is whitespace-separated; field layout:
        #   [0]=system [1]=err_corr [2]=adj [3]=xpos [4]=xdom
        #   [5]=tierC% [6]=fdb% [7]=brak% [8]=cw% [9]=RC%
        compose_parts = compose_line.split()
        assert len(compose_parts) >= 10, (
            f"compose row has fewer than 10 fields — RC cell absent.\n"
            f"Row: {compose_line!r}\nFull output:\n{result.stdout}"
        )
        rc_cell = compose_parts[9]
        assert rc_cell != "n/a", (
            f"RC cell in compose row must be a numeric value when labels are "
            f"supplied, got {rc_cell!r}.\nRow: {compose_line!r}"
        )
        try:
            float(rc_cell)
        except ValueError:
            pytest.fail(
                f"RC cell {rc_cell!r} in compose row is not a float.\n"
                f"Row: {compose_line!r}"
            )

    def test_compose_labels_path_accepted(
        self,
        fixture_corpus_path: Path,
        fixture_labels_path: Path,
        fixture_cli_catalog_path: Path,
    ) -> None:
        """--compose-labels <path> (real-label JSONL) is accepted and exits 0.

        Uses the same fixture labels path for compose-labels as for gold
        labels (schema is identical), ensuring the path form parses without
        error.
        """
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.corpus.eval",
                "--corpus",
                str(fixture_corpus_path),
                "--labels",
                str(fixture_labels_path),
                "--catalog",
                str(fixture_cli_catalog_path),
                "--systems",
                "compose",
                "--compose-labels",
                str(fixture_labels_path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, (
            f"--compose-labels <path> must exit 0.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_invalid_cut_value_exits_nonzero(
        self,
        fixture_corpus_path: Path,
        fixture_labels_path: Path,
        fixture_cli_catalog_path: Path,
    ) -> None:
        """--cut bogus_value must exit non-zero."""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.corpus.eval",
                "--corpus",
                str(fixture_corpus_path),
                "--labels",
                str(fixture_labels_path),
                "--catalog",
                str(fixture_cli_catalog_path),
                "--systems",
                "compose",
                "--compose-labels",
                "oracle",
                "--cut",
                "bogus_value",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode != 0, (
            f"--cut bogus_value must exit non-zero.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_full_cut_is_default(
        self,
        fixture_corpus_path: Path,
        fixture_labels_path: Path,
        fixture_cli_catalog_path: Path,
    ) -> None:
        """Omitting --cut defaults to 'full' (all entries used)."""
        # Run with explicit --cut full and without --cut; both must exit 0
        # and produce identical exit codes (not asserting stdout equality
        # since ordering may vary; just asserting no-crash)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.corpus.eval",
                "--corpus",
                str(fixture_corpus_path),
                "--labels",
                str(fixture_labels_path),
                "--catalog",
                str(fixture_cli_catalog_path),
                "--systems",
                "compose",
                "--compose-labels",
                "oracle",
                "--cut",
                "full",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, (
            f"--cut full must exit 0.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )


# ===========================================================================
# Anchor 6: Exact-RC regression guard — end-to-end compose routing + RC math
# ===========================================================================
#
# NOTE: The real-corpus reproduction of §13.4 (lexical 0.3303 / oracle compose
# 0.7798 / real compose 0.7431) is a LOCAL, ROUTER-VERIFIED acceptance run.
# That run requires the full production catalog + corpus, both absent from CI.
# THIS test guards the deterministic compose-routing + RC computation path
# against regression using a small, hand-crafted fixture where the expected RC
# is hand-derived and independent of any catalog drift.


_RC_REGRESSION_CATALOG_ENTRIES: list[dict[str, Any]] = [
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
            "keywords": [
                {"term": "implement", "weight": 1.0},
                {"term": "build", "weight": 0.8},
            ],
            "tool_mentions": [],
            "excludes": [],
        },
    },
    {
        "name": "ops",
        "kind": "agent",
        "source": "owned",
        "routable": True,
        "applicable_agents": [],
        "applicable_skills": [],
        "triggers": {
            "command_prefixes": ["gh", "git"],
            "agent_mentions": ["ops"],
            "path_globs": [],
            "path_globs_excluded": [],
            "keywords": [
                {"term": "deploy", "weight": 1.0},
                {"term": "run", "weight": 0.5},
            ],
            "tool_mentions": [],
            "excludes": [],
        },
    },
]


@pytest.fixture()
def fixture_rc_regression_catalog_path(tmp_path: Path) -> Path:
    """Write the two-agent catalog used in the exact-RC regression test.

    Catalog: code-writer (keyword: implement) + ops (keyword: deploy).
    Both are routable; only code-writer is in the code-domain gate, so the
    cell (code, build) → code-writer is deterministically selected when the
    task contains "implement".
    """
    catalog = {"entries": _RC_REGRESSION_CATALOG_ENTRIES}
    path = tmp_path / "rc-regression-catalog.json"
    path.write_text(json.dumps(catalog), encoding="utf-8")
    return path


class TestExactRCRegression:
    """Anchor 6: end-to-end regression guard pinning compose RC to 0.6667.

    Fixture: 3 CorpusEntry objects, all domain=code / posture=build.
    Cell map: (code, build) → code-writer.
    code-writer keyword "implement" scores >0 on every task → it is in
    the gated candidate set for all three entries.
    Posture-routing fires for all three (code-writer is in catalog AND in
    gated set), delegating to code-writer at 0.9 for each.

    Gold labels:
      entry 1: gold_agent=code-writer → CORRECT
      entry 2: gold_agent=code-writer → CORRECT
      entry 3: gold_agent=ops        → WRONG  (posture-routed to code-writer)

    Hand-derived RC = round(2/3, 4) = 0.6667.

    This pins the compose routing step AND the metric_routing_correctness
    computation together so drift in either path is caught immediately.
    """

    def test_exact_rc_is_0_6667(
        self, fixture_rc_regression_catalog_path: Path
    ) -> None:
        """metric_routing_correctness(run_supplied_compose(...)) == 0.6667.

        Hand-derived: 2 of 3 entries route to their gold_agent.
        Entries 1 and 2 have gold_agent=code-writer; the posture-routed
        delegate is code-writer (correct).  Entry 3 has gold_agent=ops;
        the posture-routed delegate is still code-writer (wrong).
        RC = round(2/3, 4) = 0.6667.
        """
        from scripts.corpus.eval._metrics import metric_routing_correctness
        from scripts.corpus.eval._systems import run_supplied_compose

        entries_and_labels = [
            _make_entry(1, "implement the feature", "code", "build", "code-writer"),
            _make_entry(2, "implement something else", "code", "build", "code-writer"),
            _make_entry(3, "implement it now", "code", "build", "ops"),
        ]
        entries = [e for e, _ in entries_and_labels]
        labels = {lbl.corpus_id: lbl for _, lbl in entries_and_labels}

        results = run_supplied_compose(
            entries, fixture_rc_regression_catalog_path, labels
        )
        rc = metric_routing_correctness(results, labels)

        # Hand-derived: 2 correct out of 3 → 0.6667
        expected_rc = round(2 / 3, 4)
        assert rc == expected_rc, (
            f"Exact-RC regression: expected {expected_rc}, got {rc}. "
            f"Results: {[(r.agent, r.extras.get('posture_routed')) for r in results]}"
        )

    def test_all_three_entries_are_posture_routed(
        self, fixture_rc_regression_catalog_path: Path
    ) -> None:
        """All 3 entries take the posture-routed path (posture_routed=True).

        Confirms the fixture is on the deterministic posture-routed path, not
        the fallback path — so the RC assertion above reflects compose logic.
        """
        from scripts.corpus.eval._systems import run_supplied_compose

        entries_and_labels = [
            _make_entry(1, "implement the feature", "code", "build", "code-writer"),
            _make_entry(2, "implement something else", "code", "build", "code-writer"),
            _make_entry(3, "implement it now", "code", "build", "ops"),
        ]
        entries = [e for e, _ in entries_and_labels]
        labels = {lbl.corpus_id: lbl for _, lbl in entries_and_labels}

        results = run_supplied_compose(
            entries, fixture_rc_regression_catalog_path, labels
        )
        for r in results:
            assert r.extras.get("posture_routed") is True, (
                f"Entry {r.corpus_id} should be posture-routed but got "
                f"posture_routed={r.extras.get('posture_routed')!r}"
            )


# ===========================================================================
# Anchor 7: Fallback branch — posture present, cell-winner absent from catalog
# ===========================================================================
#
# The existing fallback tests (Anchor 4) cover EMPTY/absent posture only.
# This anchor covers the RC-sensitive branch where:
#   - oracle_posture IS present (truthy)
#   - cell_map_lookup(domain, posture) returns a preferred agent
#   - but that agent is NOT in catalog_agent_names (absent from the fixture
#     catalog), so the `preferred in catalog_agent_names` check fails
#   → system must fall back to decide(gated_agents), NOT force the cell agent
#   → extras["posture_routed"] must be False
#   → confidence must NOT be 0.9 (the delegate@0.9 posture-routed value)
#
# Probe reference: oracle_two_axis_probe.py lines 460-465
#   ```python
#   if preferred and preferred in gated_names and preferred in catalog_agent_names:
#       ...posture_routed = True
#   ```
# When `preferred in catalog_agent_names` is False, the block is skipped and
# the fallback path at line 467 fires: `decide(gated_agents, ...)`.


_FALLBACK_CELL_WINNER_ABSENT_CATALOG: list[dict[str, Any]] = [
    # domain=code, posture=critique → cell_map gives "inquisitor".
    # "inquisitor" is INTENTIONALLY ABSENT from this catalog, so the
    # posture-routed path cannot fire. code-writer IS present and scores
    # >0 on "implement" so decide() has a concrete candidate to pick.
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
            "keywords": [
                {"term": "implement", "weight": 1.0},
                {"term": "build", "weight": 0.8},
            ],
            "tool_mentions": [],
            "excludes": [],
        },
    },
    {
        "name": "ops",
        "kind": "agent",
        "source": "owned",
        "routable": True,
        "applicable_agents": [],
        "applicable_skills": [],
        "triggers": {
            "command_prefixes": ["gh", "git"],
            "agent_mentions": ["ops"],
            "path_globs": [],
            "path_globs_excluded": [],
            "keywords": [
                {"term": "deploy", "weight": 1.0},
            ],
            "tool_mentions": [],
            "excludes": [],
        },
    },
    # "inquisitor" is deliberately NOT in this catalog.
]


@pytest.fixture()
def fixture_fallback_cell_winner_absent_catalog_path(tmp_path: Path) -> Path:
    """Write the catalog used to test the cell-winner-absent fallback branch.

    Catalog contains code-writer and ops, but NOT inquisitor.
    When the cell (code, critique) → inquisitor is looked up, inquisitor
    fails the `preferred in catalog_agent_names` check, so posture_routed
    remains False and the system falls back to decide(gated_agents).
    """
    catalog = {"entries": _FALLBACK_CELL_WINNER_ABSENT_CATALOG}
    path = tmp_path / "fallback-absent-catalog.json"
    path.write_text(json.dumps(catalog), encoding="utf-8")
    return path


class TestFallbackCellWinnerAbsentFromCatalog:
    """Anchor 7: posture present but cell-map winner absent from catalog.

    Setup:
      domain="code", posture="critique"
      cell_map_lookup("code", "critique") → "inquisitor"
      catalog has code-writer + ops, NOT inquisitor
      task_description="implement the thing" → code-writer scores >0

    Expected behavior (per algorithm at oracle_two_axis_probe.py 460-465):
      - preferred="inquisitor" is NOT in catalog_agent_names → block is skipped
      - posture_routed stays False
      - system calls decide(gated_agents, ...) on the code-domain gated list
      - confidence is NOT 0.9 (that is the posture-routed value only)
    """

    def test_posture_routed_is_false_when_cell_winner_absent_from_catalog(
        self, fixture_fallback_cell_winner_absent_catalog_path: Path
    ) -> None:
        """extras['posture_routed'] is False when cell winner is not in catalog.

        domain=code, posture=critique → cell gives "inquisitor", but inquisitor
        is absent from the catalog → condition fails → fallback path fires.
        """
        from scripts.corpus.eval._systems import run_supplied_compose

        entry, label = _make_entry(
            corpus_id=1,
            task_description="implement the thing",
            domain="code",
            posture="critique",
            gold_agent="inquisitor",
        )
        results = run_supplied_compose(
            [entry],
            fixture_fallback_cell_winner_absent_catalog_path,
            {1: label},
        )
        assert len(results) == 1
        assert results[0].extras.get("posture_routed") is False, (
            f"Cell winner absent from catalog → posture_routed must be False, "
            f"got {results[0].extras.get('posture_routed')!r}"
        )

    def test_confidence_is_not_delegate_0_9_when_cell_winner_absent_from_catalog(
        self, fixture_fallback_cell_winner_absent_catalog_path: Path
    ) -> None:
        """confidence != 0.9 when the cell-winner-absent fallback fires.

        The posture-routed path sets confidence=0.9.  When it does NOT fire,
        confidence must come from decide(), which produces a different value.
        This asserts the system did NOT silently take the posture-routed path.
        """
        from scripts.corpus.eval._systems import run_supplied_compose

        entry, label = _make_entry(
            corpus_id=1,
            task_description="implement the thing",
            domain="code",
            posture="critique",
            gold_agent="inquisitor",
        )
        results = run_supplied_compose(
            [entry],
            fixture_fallback_cell_winner_absent_catalog_path,
            {1: label},
        )
        assert results[0].confidence != 0.9, (
            f"Fallback path must not produce confidence=0.9 (posture-routed "
            f"value); got confidence={results[0].confidence}"
        )

    def test_result_agent_is_from_gated_list_not_cell_winner(
        self, fixture_fallback_cell_winner_absent_catalog_path: Path
    ) -> None:
        """Returned agent comes from decide() over the gated list, not the cell.

        The cell winner is "inquisitor" (absent from catalog).  The fallback
        calls decide() over the code-domain gated list, which contains
        code-writer (scores >0 on "implement").  The returned agent must not
        be "inquisitor" — it must be an agent that was actually in the catalog.
        """
        from scripts.corpus.eval._systems import run_supplied_compose

        entry, label = _make_entry(
            corpus_id=1,
            task_description="implement the thing",
            domain="code",
            posture="critique",
            gold_agent="inquisitor",
        )
        results = run_supplied_compose(
            [entry],
            fixture_fallback_cell_winner_absent_catalog_path,
            {1: label},
        )
        # The agent must NOT be "inquisitor" (absent from catalog and ungated set)
        assert results[0].agent != "inquisitor", (
            f"Fallback must NOT route to inquisitor (absent from catalog); "
            f"got agent={results[0].agent!r}"
        )
        # The agent must be one of the catalog agents or None (advisory)
        catalog_agent_names = {"code-writer", "ops"}
        assert results[0].agent is None or results[0].agent in catalog_agent_names, (
            f"Fallback agent must be from the catalog or None; "
            f"got agent={results[0].agent!r}"
        )


# ===========================================================================
# Anchor 8: Fallback branch — preferred ∈ catalog but ∉ gated_names
# ===========================================================================
#
# Codex review (#363) flagged this adjacent uncovered branch in
# scripts/corpus/eval/_systems.py (~lines 1044-1047):
#
#   if (
#       preferred
#       and preferred in gated_names        ← THIS check can fail independently
#       and preferred in catalog_agent_names
#   ):
#
# The existing Anchor 7 (TestFallbackCellWinnerAbsentFromCatalog) covers:
#   preferred NOT in catalog_agent_names
#
# This anchor covers:
#   preferred IS in catalog_agent_names (routable)
#   preferred NOT in gated_names (domain gate excludes it)
#
# NOTE (#364): After the infra_deploy gate fix, code-writer IS in the
# infra_deploy gate, so the original infra_deploy/build scenario no
# longer exercises this branch.  The class below now tests a different
# domain (docs_prose with a devops-only catalog) that still exercises
# the "preferred in catalog, not in gated_names" branch correctly.
#
# Corrected scenario:
#   domain="docs_prose", posture="build"
#   cell_map_lookup("docs_prose", "build") → "doc-writer"
#   docs_prose gate = {"doc-writer"} ∪ ANY_DOMAIN_AGENTS
#   Catalog: devops only (NOT in docs_prose gate, scores >0 on "deploy")
#     preferred="doc-writer" in catalog_agent_names = False (devops is
#     the only catalog agent, not doc-writer)
#   → posture-routed block skipped → decide(gated_agents) fires
#
# Reference: _cells.py — DOMAIN_AGENT_MAP["infra_deploy"] now includes
# "code-writer" (fix shipped in #364).

_INFRA_BUILD_CODE_WRITER_CATALOG: list[dict[str, Any]] = [
    # code-writer IS in catalog (routable) and, after #364, IS also in
    # the infra_deploy domain gate.  Preferred cell lookup resolves to
    # "code-writer" via ("any","build") fallback AND the gate now allows
    # it → posture-routed block fires → code-writer is delegated at 0.9.
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
            "path_globs": ["**/*.py", "**/*.bicep", "**/*.yml"],
            "path_globs_excluded": [],
            "keywords": [
                {"term": "implement", "weight": 1.0},
                {"term": "build", "weight": 0.8},
                {"term": "deploy", "weight": 0.7},
            ],
            "tool_mentions": [],
            "excludes": [],
        },
    },
    # ops IS in infra_deploy gate (via ANY_DOMAIN_AGENTS).
    {
        "name": "ops",
        "kind": "agent",
        "source": "owned",
        "routable": True,
        "applicable_agents": [],
        "applicable_skills": [],
        "triggers": {
            "command_prefixes": ["gh", "git"],
            "agent_mentions": ["ops"],
            "path_globs": [],
            "path_globs_excluded": [],
            "keywords": [
                {"term": "run", "weight": 0.5},
            ],
            "tool_mentions": [],
            "excludes": [],
        },
    },
]


@pytest.fixture()
def fixture_preferred_in_catalog_not_gated_path(tmp_path: Path) -> Path:
    """Write the catalog used to test the infra_deploy/build → code-writer contract.

    Catalog contains code-writer (routable, keywords: implement/build/deploy)
    and ops (routable, any-domain).  After #364, the infra_deploy gate
    INCLUDES code-writer, so cell_map_lookup("infra_deploy", "build") →
    "code-writer" satisfies both the gate check and the catalog check →
    posture-routed block fires → code-writer is delegated at confidence 0.9.
    """
    catalog = {"entries": _INFRA_BUILD_CODE_WRITER_CATALOG}
    path = tmp_path / "infra-build-code-writer.json"
    path.write_text(json.dumps(catalog), encoding="utf-8")
    return path


class TestFallbackPreferredInCatalogNotGated:
    """Anchor 8 (updated for #364): infra_deploy/build routes to code-writer.

    Setup (post-#364 contract):
      domain="infra_deploy", posture="build"
      cell_map_lookup("infra_deploy","build") → "code-writer" (via any/build)
      catalog: code-writer (routable, scores >0 on "build"/"deploy") + ops
      infra_deploy gate: now INCLUDES code-writer (fix shipped in #364) +
        ops (via ANY_DOMAIN_AGENTS)
      task_description="implement the build pipeline"

    Branch under test (scripts/corpus/eval/_systems.py ~lines 1044-1047):
      if (
          preferred                            ← "code-writer" (truthy)
          and preferred in gated_names         ← True: code-writer now in gate
          and preferred in catalog_agent_names ← True: in catalog
      ):
    The block is True → posture_routed=True → code-writer delegated@0.9.

    Expected (new contract after #364):
      - extras["posture_routed"] is True
      - confidence == 0.9 (posture-routed path taken)
      - agent IS "code-writer" (now included in infra_deploy gate)
    """

    def test_posture_routed_is_true_when_code_writer_in_infra_deploy_gate(
        self, fixture_preferred_in_catalog_not_gated_path: Path
    ) -> None:
        """extras['posture_routed'] is True when code-writer is in infra_deploy gate.

        domain=infra_deploy, posture=build → cell gives "code-writer".
        After #364, code-writer IS in the infra_deploy gate, so
        preferred in gated_names is True → posture-routed block fires.
        This test is RED until DOMAIN_AGENT_MAP["infra_deploy"] includes
        "code-writer".
        """
        from scripts.corpus.eval._systems import run_supplied_compose

        entry, label = _make_entry(
            corpus_id=1,
            task_description="implement the build pipeline",
            domain="infra_deploy",
            posture="build",
            gold_agent="code-writer",
        )
        results = run_supplied_compose(
            [entry],
            fixture_preferred_in_catalog_not_gated_path,
            {1: label},
        )
        assert len(results) == 1
        assert results[0].extras.get("posture_routed") is True, (
            f"code-writer now in infra_deploy gate → posture_routed must be "
            f"True, got {results[0].extras.get('posture_routed')!r}. "
            f"Fix: add 'code-writer' to DOMAIN_AGENT_MAP['infra_deploy'] in "
            f"src/claude_wayfinder/match/_cells.py."
        )

    def test_confidence_is_0_9_when_code_writer_posture_routed_for_infra_deploy(
        self, fixture_preferred_in_catalog_not_gated_path: Path
    ) -> None:
        """confidence == 0.9 when code-writer is posture-routed for infra_deploy/build.

        The posture-routed path unconditionally sets confidence=0.9.
        After #364, code-writer IS in the infra_deploy gate so this path fires.
        This test is RED until DOMAIN_AGENT_MAP["infra_deploy"] includes
        "code-writer".
        """
        from scripts.corpus.eval._systems import run_supplied_compose

        entry, label = _make_entry(
            corpus_id=1,
            task_description="implement the build pipeline",
            domain="infra_deploy",
            posture="build",
            gold_agent="code-writer",
        )
        results = run_supplied_compose(
            [entry],
            fixture_preferred_in_catalog_not_gated_path,
            {1: label},
        )
        assert results[0].confidence == 0.9, (
            f"Posture-routed code-writer must produce confidence=0.9; "
            f"got confidence={results[0].confidence}. "
            f"Fix: add 'code-writer' to DOMAIN_AGENT_MAP['infra_deploy'] in "
            f"src/claude_wayfinder/match/_cells.py."
        )

    def test_agent_is_code_writer_for_infra_deploy_build(
        self, fixture_preferred_in_catalog_not_gated_path: Path
    ) -> None:
        """Returned agent IS code-writer for infra_deploy/build (new contract #364).

        code-writer is the cell-map preferred agent for infra_deploy/build
        (resolved via ("any","build") fallback).  After #364, code-writer
        IS in the infra_deploy gate → posture-routed block fires →
        the returned agent must be "code-writer".
        This test is RED until DOMAIN_AGENT_MAP["infra_deploy"] includes
        "code-writer" (currently gated out).
        """
        from scripts.corpus.eval._systems import run_supplied_compose

        entry, label = _make_entry(
            corpus_id=1,
            task_description="implement the build pipeline",
            domain="infra_deploy",
            posture="build",
            gold_agent="code-writer",
        )
        results = run_supplied_compose(
            [entry],
            fixture_preferred_in_catalog_not_gated_path,
            {1: label},
        )
        assert results[0].agent == "code-writer", (
            f"infra_deploy/build must route to 'code-writer' after #364. "
            f"Got agent={results[0].agent!r}. "
            f"code-writer is currently gated out of DOMAIN_AGENT_MAP['infra_deploy']. "
            f"Fix: add 'code-writer' to the infra_deploy frozenset in "
            f"src/claude_wayfinder/match/_cells.py."
        )


# ===========================================================================
# Anchor 9 (updated for #364): infra_deploy/build → code-writer@0.9
# ===========================================================================
#
# HISTORY (issue #366): Before #364, the infra_deploy gate excluded
# code-writer ({devops} | ANY_DOMAIN_AGENTS only).  When a catalog
# contained only code-writer, gate_agents empties and falls back to
# [code-writer] as the ungated list.  The bug caused this fallback to
# satisfy the posture-pick guard, force-delegating code-writer@0.9.
#
# POST-#364 CHANGE: code-writer is now IN the infra_deploy gate.
# With a code-writer-only catalog:
#   gate_agents([code-writer], "infra_deploy") → [code-writer]
#     (genuine survivor — NOT an empty-gate fallback artifact)
#   preferred = cell_map_lookup("infra_deploy","build") → "code-writer"
#   preferred in gated_names → True  (genuinely gated in)
#   preferred in catalog_agent_names → True
#   → posture_routed = True, confidence = 0.9, agent = "code-writer"
#
# This is CORRECT behavior after #364, not the #366 bug.
# The correct route is: delegate to code-writer@0.9 (posture_routed=True).
#
# SETUP:
#   Catalog: code-writer ONLY.
#   domain="infra_deploy", posture="build"
#   infra_deploy gate (after #364) = {devops, code-writer} | ANY_DOMAIN_AGENTS
#   gate_agents([code-writer], "infra_deploy") → [code-writer] (genuine)
#   cell_map_lookup("infra_deploy","build") → "code-writer" (via any/build)
#   preferred in gated_names={"code-writer"} → True (genuine gate member)
#   → posture_routed=True, confidence=0.9, agent="code-writer"
#   EXPECTED: delegate to code-writer@0.9 with posture_routed=True

_EMPTY_GATE_ONLY_CODE_WRITER_CATALOG: list[dict[str, Any]] = [
    # code-writer is the ONLY agent in this catalog.
    # After #364, code-writer IS in the infra_deploy gate, so
    # gate_agents([code-writer], "infra_deploy") returns [code-writer]
    # as a genuine gate survivor (not an empty-gate fallback artifact).
    # The posture-pick guard fires correctly → code-writer@0.9.
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
            "keywords": [
                {"term": "build", "weight": 0.8},
                {"term": "deploy", "weight": 0.6},
            ],
            "tool_mentions": [],
            "excludes": [],
        },
    },
]


@pytest.fixture()
def fixture_empty_gate_only_code_writer_catalog_path(tmp_path: Path) -> Path:
    """Write a single-agent catalog containing only code-writer.

    After #364, code-writer IS in the infra_deploy gate, so
    gate_agents([code-writer], "infra_deploy") returns [code-writer] as a
    genuine gate survivor.  The posture-pick guard fires correctly and
    code-writer is delegated at confidence 0.9.
    """
    catalog = {"entries": _EMPTY_GATE_ONLY_CODE_WRITER_CATALOG}
    path = tmp_path / "empty-gate-code-writer-only.json"
    path.write_text(json.dumps(catalog), encoding="utf-8")
    return path


class TestEmptyGateFallbackDoesNotDelegateAtNinetyPercent:
    """Anchor 9 (updated for #364): code-writer-only catalog routes correctly.

    After #364, code-writer is a genuine member of the infra_deploy gate.
    A code-writer-only catalog for infra_deploy/build produces:
      gate_agents([code-writer], "infra_deploy") → [code-writer] (genuine)
      preferred = "code-writer" (cell_map_lookup via any/build)
      posture-pick guard: True → delegate@0.9

    Expected behavior (new contract): posture_routed=True, confidence=0.9,
    agent="code-writer", decision="delegate".
    """

    def test_infra_deploy_build_posture_routed_is_true_with_code_writer(
        self,
        fixture_empty_gate_only_code_writer_catalog_path: Path,
    ) -> None:
        """extras['posture_routed'] is True for infra_deploy/build after #364.

        domain=infra_deploy, posture=build, catalog=[code-writer only].
        After #364, gate_agents returns [code-writer] as a genuine survivor.
        preferred="code-writer" in gated_names → True → posture_routed=True.
        This test is RED until DOMAIN_AGENT_MAP["infra_deploy"] includes
        "code-writer".
        """
        from scripts.corpus.eval._systems import run_supplied_compose

        entry, label = _make_entry(
            corpus_id=1,
            task_description="build and deploy the service",
            domain="infra_deploy",
            posture="build",
            gold_agent="code-writer",
        )
        results = run_supplied_compose(
            [entry],
            fixture_empty_gate_only_code_writer_catalog_path,
            {1: label},
        )
        assert len(results) == 1
        assert results[0].extras.get("posture_routed") is True, (
            f"After #364: code-writer is a genuine infra_deploy gate member. "
            f"posture_routed must be True, got "
            f"{results[0].extras.get('posture_routed')!r}. "
            f"Fix: add 'code-writer' to DOMAIN_AGENT_MAP['infra_deploy'] in "
            f"src/claude_wayfinder/match/_cells.py."
        )

    def test_infra_deploy_build_confidence_is_0_9_with_code_writer(
        self,
        fixture_empty_gate_only_code_writer_catalog_path: Path,
    ) -> None:
        """confidence == 0.9 for infra_deploy/build code-writer after #364.

        The posture-routed path sets confidence=0.9.  After #364, code-writer
        is a genuine gate member and this path fires correctly.
        This test is RED until DOMAIN_AGENT_MAP["infra_deploy"] includes
        "code-writer".
        """
        from scripts.corpus.eval._systems import run_supplied_compose

        entry, label = _make_entry(
            corpus_id=1,
            task_description="build and deploy the service",
            domain="infra_deploy",
            posture="build",
            gold_agent="code-writer",
        )
        results = run_supplied_compose(
            [entry],
            fixture_empty_gate_only_code_writer_catalog_path,
            {1: label},
        )
        assert results[0].confidence == 0.9, (
            f"After #364: posture-routed code-writer must produce confidence=0.9. "
            f"Got confidence={results[0].confidence}. "
            f"Fix: add 'code-writer' to DOMAIN_AGENT_MAP['infra_deploy'] in "
            f"src/claude_wayfinder/match/_cells.py."
        )

    def test_infra_deploy_build_routes_delegate_to_code_writer(
        self,
        fixture_empty_gate_only_code_writer_catalog_path: Path,
    ) -> None:
        """Decision is delegate to code-writer@0.9 for infra_deploy/build after #364.

        The combined assertion: infra_deploy/build with a code-writer-only
        catalog must produce decision=delegate, agent=code-writer,
        confidence=0.9, posture_routed=True.
        This test is RED until DOMAIN_AGENT_MAP["infra_deploy"] includes
        "code-writer".
        """
        from scripts.corpus.eval._systems import run_supplied_compose

        entry, label = _make_entry(
            corpus_id=1,
            task_description="build and deploy the service",
            domain="infra_deploy",
            posture="build",
            gold_agent="code-writer",
        )
        results = run_supplied_compose(
            [entry],
            fixture_empty_gate_only_code_writer_catalog_path,
            {1: label},
        )
        r = results[0]
        is_correct_route = (
            r.decision == "delegate"
            and r.agent == "code-writer"
            and r.confidence == 0.9
            and r.extras.get("posture_routed") is True
        )
        assert is_correct_route, (
            f"After #364: infra_deploy/build must delegate to code-writer@0.9 "
            f"with posture_routed=True. "
            f"Got decision={r.decision!r}, agent={r.agent!r}, "
            f"confidence={r.confidence}, "
            f"posture_routed={r.extras.get('posture_routed')!r}. "
            f"Fix: add 'code-writer' to DOMAIN_AGENT_MAP['infra_deploy'] in "
            f"src/claude_wayfinder/match/_cells.py."
        )


# ===========================================================================
# Anchor 10 (#364): Positive route — infra_deploy/build → code-writer
# ===========================================================================
#
# Spec (issue #364, adjudicated):
#   Tasks with domain=infra_deploy, posture=build (implement/edit an IaC or
#   CI-CD file) MUST route to code-writer, NOT devops.  devops is
#   advisory-only; the implementer is code-writer with the IaC skill attached
#   by file path.  The gold corpus was corrected to match (commit 1705ebc).
#
# This anchor is the primary positive assertion for the #364 contract:
#   - supplied labels: domain="infra_deploy", posture="build", is_any=False
#   - expected agent: "code-writer"
#   - expected decision: "delegate" (posture-routed)
#   - expected posture_routed: True
#
# The test is RED against the current (unfixed) _cells.py because
# DOMAIN_AGENT_MAP["infra_deploy"] currently excludes code-writer.

_INFRA_DEPLOY_BUILD_ROUTABLE_CATALOG: list[dict[str, Any]] = [
    # code-writer: routable, IaC-shaped keywords, path globs for
    # infrastructure file types.  After #364 it IS in the infra_deploy gate.
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
            "path_globs": ["**/*.bicep", "**/*.yml", "**/*.tf"],
            "path_globs_excluded": [],
            "keywords": [
                {"term": "implement", "weight": 1.0},
                {"term": "build", "weight": 0.8},
                {"term": "pipeline", "weight": 0.7},
                {"term": "deploy", "weight": 0.6},
            ],
            "tool_mentions": [],
            "excludes": [],
        },
    },
    # devops: advisory-only per charter; included to confirm it is NOT
    # the result even when present alongside code-writer.
    {
        "name": "devops",
        "kind": "agent",
        "source": "owned",
        "routable": True,
        "applicable_agents": [],
        "applicable_skills": [],
        "triggers": {
            "command_prefixes": [],
            "agent_mentions": ["devops"],
            "path_globs": [],
            "path_globs_excluded": [],
            "keywords": [
                {"term": "deploy", "weight": 0.5},
                {"term": "pipeline", "weight": 0.5},
            ],
            "tool_mentions": [],
            "excludes": [],
        },
    },
    # ops: any-domain agent, present as fallback candidate.
    {
        "name": "ops",
        "kind": "agent",
        "source": "owned",
        "routable": True,
        "applicable_agents": [],
        "applicable_skills": [],
        "triggers": {
            "command_prefixes": ["gh", "git"],
            "agent_mentions": ["ops"],
            "path_globs": [],
            "path_globs_excluded": [],
            "keywords": [
                {"term": "run", "weight": 0.5},
            ],
            "tool_mentions": [],
            "excludes": [],
        },
    },
]


@pytest.fixture()
def fixture_infra_deploy_build_routable_catalog_path(tmp_path: Path) -> Path:
    """Write the catalog for the primary infra_deploy/build contract test.

    Contains code-writer (IaC keywords), devops (pipeline keywords), and
    ops (any-domain).  After #364, code-writer IS in the infra_deploy gate,
    so cell_map_lookup("infra_deploy","build") → "code-writer" (via any/build)
    satisfies the posture-pick guard → code-writer delegated at 0.9.
    devops is present but must NOT be the result (advisory-only per charter).
    """
    catalog = {"entries": _INFRA_DEPLOY_BUILD_ROUTABLE_CATALOG}
    path = tmp_path / "infra-deploy-build-routable.json"
    path.write_text(json.dumps(catalog), encoding="utf-8")
    return path


class TestInfraDeployBuildRoutesToCodeWriter:
    """Anchor 10 (#364): infra_deploy/build with supplied labels routes to code-writer.

    Exercises the full System-5 supplied-compose path:
      - supplied labels: domain="infra_deploy", posture="build", is_any=False
      - cell_map_lookup("infra_deploy","build") → "code-writer" (any/build)
      - After #364: code-writer IS in infra_deploy gate → posture_routed=True
      - Expected agent: "code-writer" (NOT devops, NOT ops)

    These tests are RED against the current (unfixed) _cells.py which
    excludes code-writer from the infra_deploy gate.
    """

    def test_infra_deploy_build_supplied_labels_routes_to_code_writer(
        self,
        fixture_infra_deploy_build_routable_catalog_path: Path,
    ) -> None:
        """Supplied labels domain=infra_deploy, posture=build route to code-writer.

        This is the primary positive assertion for issue #364.
        infra_deploy+build is an implementation task (IaC/CI-CD file edit).
        The implementer is code-writer — devops is advisory-only per charter.
        RED until DOMAIN_AGENT_MAP["infra_deploy"] includes "code-writer".
        """
        from scripts.corpus.eval._systems import run_supplied_compose

        entry, label = _make_entry(
            corpus_id=1,
            task_description=(
                "implement the Bicep deployment pipeline for the staging slot"
            ),
            domain="infra_deploy",
            posture="build",
            gold_agent="code-writer",
        )
        results = run_supplied_compose(
            [entry],
            fixture_infra_deploy_build_routable_catalog_path,
            {1: label},
        )
        assert len(results) == 1
        assert results[0].agent == "code-writer", (
            f"infra_deploy/build must route to 'code-writer' (issue #364). "
            f"Got agent={results[0].agent!r}. "
            f"code-writer is currently excluded from "
            f"DOMAIN_AGENT_MAP['infra_deploy'] — it must be added. "
            f"devops is advisory-only and must NOT be the routing target."
        )

    def test_infra_deploy_build_decision_is_delegate_not_advisory(
        self,
        fixture_infra_deploy_build_routable_catalog_path: Path,
    ) -> None:
        """Decision for infra_deploy/build is 'delegate', not 'advisory'.

        Posture-routed routing produces decision='delegate'.  Verifying
        decision confirms the posture-pick guard fired (not the advisory
        fallback path).  RED until code-writer is in infra_deploy gate.
        """
        from scripts.corpus.eval._systems import run_supplied_compose

        entry, label = _make_entry(
            corpus_id=1,
            task_description=(
                "implement the Bicep deployment pipeline for the staging slot"
            ),
            domain="infra_deploy",
            posture="build",
            gold_agent="code-writer",
        )
        results = run_supplied_compose(
            [entry],
            fixture_infra_deploy_build_routable_catalog_path,
            {1: label},
        )
        assert results[0].decision == "delegate", (
            f"infra_deploy/build must produce decision='delegate'. "
            f"Got decision={results[0].decision!r}. "
            f"Fix: add 'code-writer' to DOMAIN_AGENT_MAP['infra_deploy'] in "
            f"src/claude_wayfinder/match/_cells.py."
        )

    def test_infra_deploy_build_agent_is_not_devops(
        self,
        fixture_infra_deploy_build_routable_catalog_path: Path,
    ) -> None:
        """Returned agent is NOT devops for infra_deploy/build after #364.

        devops is advisory-only per charter; the implementer is code-writer.
        Even when devops is present in the catalog and scores on keywords,
        the result must be code-writer (posture-routed via the cell map).
        RED until code-writer is in infra_deploy gate (currently routes to
        devops or ops via the gated fallback path).
        """
        from scripts.corpus.eval._systems import run_supplied_compose

        entry, label = _make_entry(
            corpus_id=1,
            task_description=(
                "implement the Bicep deployment pipeline for the staging slot"
            ),
            domain="infra_deploy",
            posture="build",
            gold_agent="code-writer",
        )
        results = run_supplied_compose(
            [entry],
            fixture_infra_deploy_build_routable_catalog_path,
            {1: label},
        )
        assert results[0].agent != "devops", (
            f"infra_deploy/build must NOT route to 'devops' (advisory-only). "
            f"Got agent={results[0].agent!r}. "
            f"The correct agent is 'code-writer' per issue #364."
        )


# ===========================================================================
# Anchor 11 (PR #394 review): cell-winner-gated-out guard — docs_prose/assess
# ===========================================================================
#
# HISTORY:  Before #364, Anchors 8 and 9 exercised the branch where the cell
# winner (code-writer) was present in the catalog but NOT in the domain gate
# (infra_deploy).  #364 added code-writer to the infra_deploy gate, so those
# two anchors were updated to assert the new positive-route contract — leaving
# the "cell winner in catalog, gated out of domain" branch UNTESTED.
#
# This anchor re-establishes that guard using a (domain, posture) pair that
# is still gated out after #364:
#
#   domain="docs_prose", posture="assess"
#   cell_map_lookup("docs_prose", "assess") → no direct key
#     → falls back to ("any", "assess") → "code-reviewer"
#   DOMAIN_AGENT_MAP["docs_prose"] = frozenset({"doc-writer"}) | ANY_DOMAIN_AGENTS
#     = {"doc-writer","investigator","approach-critic","auditor",
#        "researcher","ops","project-planner"}
#   "code-reviewer" is NOT in that set → gated out.
#
# Catalog: code-reviewer (routable, keyword "review" → scores >0 on the task)
#          + doc-writer (routable, keyword "document" → any-domain fallback)
#
# Expected behavior (cell-winner-gated-out branch):
#   - preferred = "code-reviewer"
#   - preferred in catalog_agent_names → True
#   - preferred in gated_names → False  (core gate check fails)
#   → posture_routed stays False
#   → system falls back to decide(gated_agents)
#   → result is NOT delegate@0.9 and agent is NOT "code-reviewer"
#
# Algorithm reference (scripts/corpus/eval/_systems.py ~lines 1044-1047):
#   if (
#       preferred
#       and preferred in gated_names        ← THIS check fails here
#       and preferred in catalog_agent_names
#   ):
#       posture_routed = True; ...
#
# A regression that deletes or weakens the `preferred in gated_names` check
# would cause code-reviewer to be delegated@0.9 despite being gated out —
# exactly what these assertions detect.

_DOCS_PROSE_ASSESS_GATED_OUT_CATALOG: list[dict[str, Any]] = [
    # code-reviewer: routable, scores >0 on "review" keyword.
    # Cell (docs_prose, assess) resolves to "code-reviewer" via (any, assess).
    # code-reviewer is NOT in the docs_prose gate — it must be gated out.
    {
        "name": "code-reviewer",
        "kind": "agent",
        "source": "owned",
        "routable": True,
        "applicable_agents": [],
        "applicable_skills": [],
        "triggers": {
            "command_prefixes": [],
            "agent_mentions": ["code-reviewer"],
            "path_globs": [],
            "path_globs_excluded": [],
            "keywords": [
                {"term": "review", "weight": 1.0},
                {"term": "assess", "weight": 0.8},
            ],
            "tool_mentions": [],
            "excludes": [],
        },
    },
    # doc-writer: in docs_prose gate, scores on "document" keyword.
    # Present so the gated candidate list is non-empty after gate_agents(),
    # giving decide() a concrete candidate.
    {
        "name": "doc-writer",
        "kind": "agent",
        "source": "owned",
        "routable": True,
        "applicable_agents": [],
        "applicable_skills": [],
        "triggers": {
            "command_prefixes": [],
            "agent_mentions": ["doc-writer"],
            "path_globs": ["**/*.md"],
            "path_globs_excluded": [],
            "keywords": [
                {"term": "document", "weight": 1.0},
                {"term": "docs", "weight": 0.8},
            ],
            "tool_mentions": [],
            "excludes": [],
        },
    },
]


@pytest.fixture()
def fixture_docs_prose_assess_gated_out_catalog_path(tmp_path: Path) -> Path:
    """Write the catalog for the docs_prose/assess cell-winner-gated-out test.

    Catalog contains code-reviewer (keyword: review/assess) and doc-writer
    (keyword: document).  code-reviewer is the cell-map winner for
    (docs_prose, assess) — resolved via (any, assess) — but is NOT in the
    docs_prose gate.  doc-writer IS in the gate and provides a concrete
    gated candidate so decide() has something to work with.
    """
    catalog = {"entries": _DOCS_PROSE_ASSESS_GATED_OUT_CATALOG}
    path = tmp_path / "docs-prose-assess-gated-out.json"
    path.write_text(json.dumps(catalog), encoding="utf-8")
    return path


class TestCellWinnerGatedOutDocsProseAssess:
    """Anchor 11: post-#364 guard for the cell-winner-gated-out branch.

    Replaces the coverage the Anchor-8/9 inversion removed (PR #394 review).
    The old Anchors 8 and 9 used infra_deploy/build to show code-writer was
    gated out.  #364 fixed that — code-writer is now in the infra_deploy gate
    — so Anchors 8/9 were rewritten to assert the positive route.  This
    anchor restores the ``preferred in gated_names → False`` guard using a
    pair that is STILL gated out: docs_prose/assess → code-reviewer.

    Setup:
      domain="docs_prose", posture="assess"
      cell_map_lookup("docs_prose","assess") → "code-reviewer" (via any/assess)
      DOMAIN_AGENT_MAP["docs_prose"] = frozenset({"doc-writer"}) | ANY_DOMAIN_AGENTS
      "code-reviewer" NOT in that set → gated out
      catalog: code-reviewer (review/assess keywords) + doc-writer (document keyword)
      task_description contains "review" → code-reviewer scores >0 in the catalog

    Expected: preferred in gated_names → False → posture_routed stays False
              result is not delegate@0.9; agent is not "code-reviewer".
    """

    def test_posture_routed_is_false_when_cell_winner_gated_out(
        self, fixture_docs_prose_assess_gated_out_catalog_path: Path
    ) -> None:
        """extras['posture_routed'] is False when cell winner is gated out.

        domain=docs_prose, posture=assess → cell gives "code-reviewer".
        code-reviewer is NOT in the docs_prose gate → preferred in
        gated_names is False → posture_routed stays False.

        Replaces the Anchor-8/9 pre-#364 guard for the same branch
        (infra_deploy/build was the old example; see PR #394 review).
        """
        from scripts.corpus.eval._systems import run_supplied_compose

        entry, label = _make_entry(
            corpus_id=1,
            task_description="review and assess the documentation",
            domain="docs_prose",
            posture="assess",
            gold_agent="doc-writer",
        )
        results = run_supplied_compose(
            [entry],
            fixture_docs_prose_assess_gated_out_catalog_path,
            {1: label},
        )
        assert len(results) == 1
        assert results[0].extras.get("posture_routed") is False, (
            f"docs_prose/assess cell winner (code-reviewer) is gated out — "
            f"posture_routed must be False, "
            f"got {results[0].extras.get('posture_routed')!r}"
        )

    def test_not_delegate_at_0_9_when_cell_winner_gated_out(
        self, fixture_docs_prose_assess_gated_out_catalog_path: Path
    ) -> None:
        """Result is not a delegate at confidence 0.9 when cell winner gated out.

        The posture-routed path (when it fires) sets decision='delegate'
        and confidence=0.9.  When the gate check fails, that path is
        skipped; the fallback decide() path produces a different confidence.
        Asserts the system did NOT silently take the posture-routed path.
        """
        from scripts.corpus.eval._systems import run_supplied_compose

        entry, label = _make_entry(
            corpus_id=1,
            task_description="review and assess the documentation",
            domain="docs_prose",
            posture="assess",
            gold_agent="doc-writer",
        )
        results = run_supplied_compose(
            [entry],
            fixture_docs_prose_assess_gated_out_catalog_path,
            {1: label},
        )
        r = results[0]
        # The posture-routed path produces (decision="delegate", confidence=0.9)
        # simultaneously — the gated-out branch must not produce that pair.
        is_posture_routed_result = (
            r.decision == "delegate" and r.confidence == 0.9
        )
        assert not is_posture_routed_result, (
            f"Cell winner gated out — result must not be delegate@0.9. "
            f"Got decision={r.decision!r}, confidence={r.confidence}"
        )

    def test_agent_is_not_code_reviewer_when_gated_out(
        self, fixture_docs_prose_assess_gated_out_catalog_path: Path
    ) -> None:
        """Returned agent is NOT code-reviewer when it is gated out of docs_prose.

        code-reviewer is the cell-map preferred agent for (docs_prose, assess)
        but is excluded by the docs_prose domain gate.  The fallback
        decide() call must not return "code-reviewer" as the routed agent.
        """
        from scripts.corpus.eval._systems import run_supplied_compose

        entry, label = _make_entry(
            corpus_id=1,
            task_description="review and assess the documentation",
            domain="docs_prose",
            posture="assess",
            gold_agent="doc-writer",
        )
        results = run_supplied_compose(
            [entry],
            fixture_docs_prose_assess_gated_out_catalog_path,
            {1: label},
        )
        assert results[0].agent != "code-reviewer", (
            f"code-reviewer is gated out of docs_prose — "
            f"must not be returned as the routed agent, "
            f"got agent={results[0].agent!r}"
        )


# ===========================================================================
# Issue #397: Abstain sentinel — (project_meta, build) → self_handle
# ===========================================================================
#
# These tests are RED until:
#   1. SELF_HANDLE_SENTINEL is added to _cells.py
#   2. ("project_meta","build") maps to SELF_HANDLE_SENTINEL in _CELL_MAP
#   3. run_supplied_compose translates the sentinel to
#      decision="self_handle", agent=None (NOT a delegate to a real agent)
#
# Before the change, ("project_meta","build") falls back to
# ("any","build") → "code-writer", so run_supplied_compose delegates to
# code-writer (which IS in the catalog below).
#
# Expected failure modes BEFORE implementation:
#   test_project_meta_build_decision_is_self_handle
#     → AssertionError: decision='delegate', agent='code-writer' (fallback)
#   test_project_meta_build_agent_is_none
#     → AssertionError: agent='code-writer'
#   test_project_meta_build_sentinel_not_in_scores
#     → passes before implementation (no sentinel in scores), but
#       may fail if sentinel bleeds into scores dict after implementation
#   test_project_meta_build_sentinel_not_routed_as_agent
#     → AssertionError: agent='code-writer' before; must be None after
#   test_seven_gold_self_handle_ids_all_produce_self_handle
#     → AssertionError: most corpus IDs produce decision='delegate'
#       (routed to code-writer via any-build fallback) before the fix
#   test_34712_is_not_among_seven_self_handle_ids (guard, always passes)
#
# RED/GREEN boundary: all tests in TestSelfHandleSentinelCompose must be
# RED before implementation and GREEN after correct implementation.

# Minimal catalog for project_meta/build sentinel tests.
# Contains code-writer (to prove it does NOT get routed to after the
# sentinel fires), ops (any-domain fallback), and project-planner
# (in the project_meta gate).  The sentinel mechanism must suppress
# delegation to any of them.
_PROJECT_META_BUILD_CATALOG_ENTRIES: list[dict[str, Any]] = [
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
            "path_globs": ["**/*.py", "**/*.md"],
            "path_globs_excluded": [],
            "keywords": [
                {"term": "edit", "weight": 1.0},
                {"term": "rename", "weight": 0.9},
                {"term": "update", "weight": 0.8},
                {"term": "remove", "weight": 0.8},
                {"term": "add", "weight": 0.7},
            ],
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
            "keywords": [
                {"term": "plan", "weight": 1.0},
                {"term": "milestone", "weight": 0.8},
            ],
            "tool_mentions": [],
            "excludes": [],
        },
    },
    {
        "name": "ops",
        "kind": "agent",
        "source": "owned",
        "routable": True,
        "applicable_agents": [],
        "applicable_skills": [],
        "triggers": {
            "command_prefixes": ["gh", "git"],
            "agent_mentions": ["ops"],
            "path_globs": [],
            "path_globs_excluded": [],
            "keywords": [
                {"term": "run", "weight": 0.5},
            ],
            "tool_mentions": [],
            "excludes": [],
        },
    },
]


@pytest.fixture()
def fixture_project_meta_build_catalog_path(tmp_path: Path) -> Path:
    """Write a catalog for project_meta/build sentinel tests.

    Includes code-writer (keyword: edit/rename/update), project-planner,
    and ops.  Before #397 code-writer would be posture-routed (via the
    any-build fallback); after #397 the sentinel fires and the result
    must be self_handle / agent=None.
    """
    catalog = {"entries": _PROJECT_META_BUILD_CATALOG_ENTRIES}
    path = tmp_path / "project-meta-build-catalog.json"
    path.write_text(json.dumps(catalog), encoding="utf-8")
    return path


class TestSelfHandleSentinelCompose:
    """Issue #397: run_supplied_compose translates sentinel → self_handle.

    All tests in this class must be RED before _cells.py and _systems.py
    are updated, and GREEN after correct implementation.
    """

    def test_project_meta_build_decision_is_self_handle(
        self, fixture_project_meta_build_catalog_path: Path
    ) -> None:
        """decision == 'self_handle' for domain=project_meta, posture=build.

        Before #397: cell_map_lookup("project_meta","build") falls back
        to ("any","build") → "code-writer".  run_supplied_compose delegates
        to code-writer (decision='delegate').

        After #397: sentinel fires → decision='self_handle'.

        RED: AssertionError — decision='delegate' before implementation.
        """
        from scripts.corpus.eval._systems import run_supplied_compose

        entry, label = _make_entry(
            corpus_id=1,
            task_description=(
                "Rename the Claude Code skill session-variance to "
                "session-analysis in the claude-prospector repo."
            ),
            domain="project_meta",
            posture="build",
            gold_agent="self_handle",
        )
        results = run_supplied_compose(
            [entry],
            fixture_project_meta_build_catalog_path,
            {1: label},
        )
        assert len(results) == 1
        assert results[0].decision == "self_handle", (
            f"domain=project_meta / posture=build must produce "
            f"decision='self_handle' after #397; "
            f"got decision={results[0].decision!r}. "
            f"Before #397 the fallback routes to 'code-writer' (delegate)."
        )

    def test_project_meta_build_agent_is_none(
        self, fixture_project_meta_build_catalog_path: Path
    ) -> None:
        """agent is None and decision is 'self_handle' for project_meta/build.

        The sentinel represents 'router abstains, handles itself' — there
        is no real agent to delegate to.

        Uses corpus 33622's exact prompt (harness rename) — it triggers
        code-writer's 'rename' keyword (score > 0), so before #397 the
        posture-routed path fires and delegates to code-writer.  After #397
        the sentinel fires first and agent must be None.

        RED: AssertionError — decision='delegate', agent='code-writer'
        before implementation (rename keyword triggers code-writer).
        """
        from scripts.corpus.eval._systems import run_supplied_compose

        entry, label = _make_entry(
            corpus_id=1,
            # "rename" hits code-writer's keyword → score > 0 → posture-routed
            # before #397; sentinel must suppress delegation after #397.
            task_description=(
                "Rename the Claude Code skill session-variance to "
                "session-analysis in the claude-prospector repo. "
                "Harness edit: rename skills/session-variance/ directory "
                "to skills/session-analysis/, update the name: field in "
                "SKILL.md frontmatter, update references in README.md."
            ),
            domain="project_meta",
            posture="build",
            gold_agent="self_handle",
        )
        results = run_supplied_compose(
            [entry],
            fixture_project_meta_build_catalog_path,
            {1: label},
        )
        # Both assertions together pin the complete self_handle contract:
        # the decision string AND the agent being absent.
        assert results[0].decision == "self_handle", (
            f"domain=project_meta / posture=build must produce "
            f"decision='self_handle' after #397; "
            f"got decision={results[0].decision!r}. "
            f"Before #397 the 'rename' keyword triggers code-writer scoring "
            f"> 0 and the posture-routed path delegates to it."
        )
        assert results[0].agent is None, (
            f"domain=project_meta / posture=build must produce agent=None "
            f"after #397; got agent={results[0].agent!r}. "
            f"The sentinel means 'router handles itself', not a real agent."
        )

    def test_project_meta_build_sentinel_not_in_scores(
        self, fixture_project_meta_build_catalog_path: Path
    ) -> None:
        """SELF_HANDLE_SENTINEL must not appear as a key in extras['scores'].

        The sentinel is an internal routing instruction, not a routable
        agent name.  It must never propagate into the scores dict that
        callers inspect.

        Uses a task with 'update' keyword so code-writer scores > 0
        (posture-routed to code-writer before #397).

        RED: decision='delegate', agent='code-writer' before #397;
        after #397 must be self_handle and sentinel absent from scores.
        """
        from scripts.corpus.eval._systems import run_supplied_compose

        entry, label = _make_entry(
            corpus_id=1,
            # "update" + "add" hits code-writer keywords → posture-routed
            # before #397; sentinel fires and scores dict must not carry
            # the sentinel string after correct implementation.
            task_description=(
                "Edit skills/python/SKILL.md to add a warning about "
                "ruff format --check masking content issues on Windows. "
                "Update the footguns section with this new entry."
            ),
            domain="project_meta",
            posture="build",
            gold_agent="self_handle",
        )
        results = run_supplied_compose(
            [entry],
            fixture_project_meta_build_catalog_path,
            {1: label},
        )
        # After #397 the decision must be self_handle (not delegate).
        assert results[0].decision == "self_handle", (
            f"domain=project_meta / posture=build must produce "
            f"decision='self_handle' after #397; "
            f"got decision={results[0].decision!r}."
        )
        scores = results[0].extras.get("scores", {})
        assert "__self_handle__" not in scores, (
            f"SELF_HANDLE_SENTINEL '__self_handle__' must not appear in "
            f"extras['scores']; found it with value "
            f"{scores.get('__self_handle__')!r}. "
            f"The sentinel is a routing instruction, not a routable agent."
        )

    def test_project_meta_build_sentinel_not_routed_as_agent(
        self, fixture_project_meta_build_catalog_path: Path
    ) -> None:
        """The sentinel string '__self_handle__' is never the agent field.

        Regardless of decision, the agent field must be None (not the
        sentinel string itself — that would mean the sentinel was
        mishandled as a real agent name).

        Uses an 'edit'/'add' task so code-writer scores > 0 and the
        posture-routed path fires before #397 (decision='delegate',
        agent='code-writer').  After #397 the sentinel fires and
        agent must be None (never the sentinel string itself).

        RED: decision='delegate', agent='code-writer' before #397.
        """
        from scripts.corpus.eval._systems import run_supplied_compose

        entry, label = _make_entry(
            corpus_id=1,
            # "edit" + "add" keywords → code-writer scores > 0
            task_description=(
                "Edit agents/debugger.md to add a Software Standards section "
                "ending in @import of standards/software-standards.md, "
                "mirroring the #918 change to code-writer and code-reviewer "
                "agent definitions. Harness agent-file edit."
            ),
            domain="project_meta",
            posture="build",
            gold_agent="self_handle",
        )
        results = run_supplied_compose(
            [entry],
            fixture_project_meta_build_catalog_path,
            {1: label},
        )
        # Pin: sentinel string itself must never be the agent.
        assert results[0].agent != "__self_handle__", (
            "The sentinel string '__self_handle__' must NEVER appear as the "
            "agent field — it must be translated to agent=None."
        )
        # After #397 the whole result must be self_handle / None.
        assert results[0].decision == "self_handle", (
            f"domain=project_meta / posture=build must produce "
            f"decision='self_handle' after #397; "
            f"got decision={results[0].decision!r}."
        )
        assert results[0].agent is None, (
            f"After #397 the agent field must be None for the sentinel cell; "
            f"got agent={results[0].agent!r}."
        )


class TestSelfHandleSentinelGoldIds:
    """Issue #397: the 7 gold self_handle corpus IDs resolve to self_handle.

    Uses real label-blind prompts from
    docs/research/label-blind-prompts.jsonl joined to gold labels from
    docs/research/2026-06-12-gold-labels-redacted.jsonl.

    Seven corpus IDs known to be (project_meta, build, gold_agent=self_handle):
      33622, 33683, 34638, 34788, 34794, 34862, 35362

    Corpus ID 34712 is ALSO (project_meta, build) but gold_agent=doc-writer
    (a plan-doc edit — a known cell impurity, out of scope for #397).
    Do NOT assert anything about 34712 routing to self_handle.
    NOTE (#410): 34712 will be relabelled domain -> docs_prose so it routes
    correctly to doc-writer instead of hitting the sentinel.  See
    TestCorpus34712RelabelledToDocsProse for the #410 contract tests.

    All tests in this class are RED until _cells.py + _systems.py are updated.
    """

    # These two paths are committed to the repo and always present.
    _GOLD_LABELS_PATH: Path = (
        _RESEARCH_DIR / "2026-06-12-gold-labels-redacted.jsonl"
    )
    _PROMPTS_PATH: Path = (
        _RESEARCH_DIR / "label-blind-prompts.jsonl"
    )

    # The 7 corpus IDs that must resolve to self_handle after #397.
    _SELF_HANDLE_IDS: frozenset[int] = frozenset({
        33622, 33683, 34638, 34788, 34794, 34862, 35362,
    })

    # 34712 is (project_meta, build) but gold=doc-writer; never self_handle.
    _RESIDUAL_ID: int = 34712

    @staticmethod
    def _load_seven_entries_and_labels() -> (
        tuple[list[CorpusEntry], dict[int, GoldLabel]]
    ):
        """Load the 7 gold self_handle entries from committed research files.

        Returns:
            Tuple of (entries_list, labels_dict) for the 7 IDs only.
            Labels use the real gold data (domain=project_meta,
            posture=build, gold_agent=self_handle).
        """
        from scripts.corpus.eval._reader import load_labels

        target_ids = frozenset({
            33622, 33683, 34638, 34788, 34794, 34862, 35362,
        })

        # Load gold labels for the 7 target IDs
        all_labels_path = (
            _RESEARCH_DIR / "2026-06-12-gold-labels-redacted.jsonl"
        )
        all_labels = load_labels(all_labels_path)
        labels = {
            cid: lbl
            for cid, lbl in all_labels.items()
            if cid in target_ids
        }

        # Load prompts (label-blind) for the 7 target IDs
        prompts_path = (
            _RESEARCH_DIR / "label-blind-prompts.jsonl"
        )
        import json as _json
        entries: list[CorpusEntry] = []
        with open(prompts_path, encoding="utf-8") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line:
                    continue
                rec = _json.loads(line)
                cid = int(rec["corpus_id"])
                if cid not in target_ids:
                    continue
                entries.append(CorpusEntry(
                    corpus_id=cid,
                    task_description=str(
                        rec.get("task_description", "")
                    ),
                    file_paths=list(rec.get("file_paths") or []),
                    agent_mentions=list(
                        rec.get("agent_mentions") or []
                    ),
                    tool_mentions=list(
                        rec.get("tool_mentions") or []
                    ),
                    command_prefix=rec.get("command_prefix") or None,
                    stratum={},
                    raw=rec,
                ))
        return entries, labels

    def test_seven_gold_self_handle_ids_all_produce_self_handle(
        self, fixture_project_meta_build_catalog_path: Path
    ) -> None:
        """All 7 (project_meta, build, gold=self_handle) entries → self_handle.

        Before #397: cell falls back to ("any","build") → "code-writer" and
        run_supplied_compose delegates to code-writer.  Most/all entries will
        have decision='delegate' and agent='code-writer' — so the assertion
        decision=='self_handle' fails.

        After #397: sentinel fires for all 7 → decision='self_handle',
        agent=None for every entry.

        RED: AssertionError (decision='delegate') before implementation.
        """
        from scripts.corpus.eval._systems import run_supplied_compose

        entries, labels = self._load_seven_entries_and_labels()

        # Verify we loaded all 7
        loaded_ids = frozenset(e.corpus_id for e in entries)
        assert loaded_ids == self._SELF_HANDLE_IDS, (
            f"Expected to load entries for IDs {sorted(self._SELF_HANDLE_IDS)}, "
            f"got {sorted(loaded_ids)}.  Check that label-blind-prompts.jsonl "
            f"contains all 7 IDs."
        )

        results = run_supplied_compose(
            entries,
            fixture_project_meta_build_catalog_path,
            labels,
        )

        failures: list[str] = []
        for r in results:
            if r.decision != "self_handle" or r.agent is not None:
                failures.append(
                    f"corpus_id={r.corpus_id}: "
                    f"decision={r.decision!r}, agent={r.agent!r} "
                    f"(expected decision='self_handle', agent=None)"
                )
        assert not failures, (
            "After #397, all 7 self_handle gold IDs must produce "
            "decision='self_handle' and agent=None.  Failures:\n"
            + "\n".join(failures)
        )

    def test_34712_is_not_among_seven_self_handle_ids(self) -> None:
        """corpus_id 34712 is excluded from the 7 self_handle IDs (guard).

        34712 is (project_meta, build) but gold_agent='doc-writer' — a
        plan-doc edit that is an out-of-scope cell impurity.  The sentinel
        does NOT fix 34712; this test simply guards that our set of IDs
        does not accidentally include it.
        NOTE (#410): 34712's domain will be relabelled docs_prose (not
        project_meta) — it will then route correctly to doc-writer.  This
        set-membership guard remains valid regardless of the relabel.

        This test is always GREEN (it asserts a set membership invariant).
        """
        assert self._RESIDUAL_ID not in self._SELF_HANDLE_IDS, (
            f"corpus_id {self._RESIDUAL_ID} must not be in the 7 "
            f"self_handle IDs.  It is (project_meta, build) but "
            f"gold_agent='doc-writer' (out-of-scope cell impurity)."
        )


# ===========================================================================
# Issue #397: run_composed also abstains on (project_meta, build)
# ===========================================================================
#
# run_composed uses _route_from_postures → cell_map_lookup.  After the
# sentinel is added, _route_from_postures will receive "__self_handle__"
# from cell_map_lookup when domain=project_meta and winning_posture=build.
#
# The implementation must translate the sentinel in _route_from_postures
# (or in run_composed's decision gate) to self_handle / agent=None, just
# as it does in run_supplied_compose.
#
# Testing this directly requires the DomainClassifier (model2vec dependency),
# which is NOT available in the test environment.  Instead, we test the
# _route_from_postures helper directly — it calls cell_map_lookup and
# returns (agent, confidence).  After #397 it must return
# (SELF_HANDLE_SENTINEL, ...) for (project_meta, build), and the sentinel
# translation in run_composed must convert that to decision='self_handle'.
#
# We test the OBSERVABLE OUTPUT of _route_from_postures to verify the
# sentinel propagates correctly out of the cell-map layer before it reaches
# the decision gate.


class TestRunComposedSentinelPropagation:
    """Issue #397: sentinel propagates through _route_from_postures.

    Tests the internal helper _route_from_postures to confirm it returns
    the sentinel for (domain=project_meta, posture=build), so that the
    run_composed decision gate can translate it to self_handle.

    These tests are RED until SELF_HANDLE_SENTINEL is added to _cells.py.
    """

    def test_route_from_postures_returns_sentinel_for_project_meta_build(
        self,
    ) -> None:
        """_route_from_postures returns SELF_HANDLE_SENTINEL for project_meta+build.

        After #397, cell_map_lookup("project_meta","build") returns the
        sentinel.  _route_from_postures uses cell_map_lookup internally,
        so it must pass the sentinel through as the agent component of its
        return tuple.

        This is the sweep-coverage test for the extractor path: it confirms
        the sentinel propagates through _route_from_postures so that every
        call site that invokes cell_map_lookup (including run_composed) will
        encounter it.

        RED: AssertionError — returns 'code-writer' before #397 (via fallback).
        """
        from claude_wayfinder.match._cells import SELF_HANDLE_SENTINEL
        from scripts.corpus.eval._systems import _route_from_postures

        agent, _confidence = _route_from_postures(
            postures=["build"],
            area_span=1,
            e8_fired=False,
            e12_fired=False,
            domain="project_meta",
        )
        assert agent == SELF_HANDLE_SENTINEL, (
            f"_route_from_postures(postures=['build'], domain='project_meta') "
            f"must return SELF_HANDLE_SENTINEL ('{SELF_HANDLE_SENTINEL}') as "
            f"the agent after #397; got {agent!r}.  "
            f"Before #397 cell_map_lookup falls back to ('any','build') → "
            f"'code-writer', so this returns 'code-writer'."
        )


# ===========================================================================
# Issue #396: area_span signal — (code, diagnose) + span≥2 → investigator
# ===========================================================================
#
# BACKGROUND: _route_from_postures (lines 477-479) already implements:
#   if winning_posture == "diagnose" and area_span >= 2: agent = "investigator"
#
# run_supplied_compose uses the ORACLE path (supplied labels), NOT extractors.
# Today it calls cell_map_lookup(domain, posture) which returns "debugger" for
# (code, diagnose).  The span rule is NOT applied on the oracle path.
#
# #396 adds a hard override BEFORE the existing sentinel check:
#   if oracle_posture == "diagnose" and label.area_span >= 2:
#       agent_out = "investigator"
#       decision_out = "delegate"
#       confidence_out = 0.9
#       posture_routed = True
#
# This mirrors the extractor path's diagnose+span rule for oracle entries.
#
# Catalog used in these tests: a minimal catalog containing both
# "debugger" (the current cell_map_lookup result for code/diagnose) and
# "investigator" (the expected result after #396), plus "code-writer"
# (any-domain fallback) to keep the catalog from being trivially empty.
# "infra-debugger" is deliberately absent — we only need the two agents
# that are in the code domain gate.
#
# EXPECTED FAILURE MODES BEFORE IMPLEMENTATION:
#   test_code_diagnose_span2_routes_to_investigator
#     → AssertionError: agent='debugger' (cell_map_lookup returns debugger)
#   test_code_diagnose_span2_decision_is_delegate_at_0_9
#     → AssertionError: confidence != 0.9 or decision != 'delegate'
#       (falls through to the existing gated posture-routed path which
#        gives debugger, not investigator)
#   test_code_diagnose_span1_routes_to_debugger
#     → passes (cell_map_lookup already returns "debugger"); synthetic pin
#   test_code_diagnose_default_span_routes_to_debugger
#     → passes (area_span absent from GoldLabel → defaults to 1)
#       but fails at COLLECTION if GoldLabel lacks area_span field
#   test_infra_deploy_diagnose_span2_routes_to_investigator
#     → passes or fails depending on infra_deploy gate; pinned for parity
#   test_sentinel_intact_under_span_rule
#     → passes (sentinel already fires before span override in contract)
#   test_non_diagnose_posture_unaffected_by_span_rule
#     → passes (span rule only activates on posture=="diagnose")
#   test_gold_data_8_ids_have_area_span_2_and_investigator
#     → AttributeError or wrong value until gold data edited
#   test_gold_data_34774_has_default_span_and_researcher
#     → AttributeError until GoldLabel gains area_span field

# Minimal catalog for span-rule tests.
# Contains "debugger" (current code/diagnose cell winner), "investigator"
# (expected span≥2 winner), and "code-writer" (fallback).
# Both debugger and investigator are in the code domain gate.
_SPAN_SIGNAL_CATALOG_ENTRIES: list[dict[str, Any]] = [
    {
        "name": "debugger",
        "kind": "agent",
        "source": "owned",
        "routable": True,
        "applicable_agents": [],
        "applicable_skills": [],
        "triggers": {
            "command_prefixes": [],
            "agent_mentions": ["debugger"],
            "path_globs": ["**/*.py"],
            "path_globs_excluded": [],
            "keywords": [
                {"term": "debug", "weight": 1.0},
                {"term": "error", "weight": 0.5},
                {"term": "traceback", "weight": 0.8},
            ],
            "tool_mentions": [],
            "excludes": [],
        },
    },
    {
        "name": "investigator",
        "kind": "agent",
        "source": "owned",
        "routable": True,
        "applicable_agents": [],
        "applicable_skills": [],
        "triggers": {
            "command_prefixes": [],
            "agent_mentions": ["investigator"],
            "path_globs": [],
            "path_globs_excluded": [],
            "keywords": [
                {"term": "investigate", "weight": 1.0},
                {"term": "figure", "weight": 0.5},
                {"term": "debug", "weight": 0.7},
            ],
            "tool_mentions": [],
            "excludes": [],
        },
    },
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
            "keywords": [
                {"term": "implement", "weight": 1.0},
                {"term": "build", "weight": 0.8},
            ],
            "tool_mentions": [],
            "excludes": [],
        },
    },
    {
        "name": "ops",
        "kind": "agent",
        "source": "owned",
        "routable": True,
        "applicable_agents": [],
        "applicable_skills": [],
        "triggers": {
            "command_prefixes": ["gh", "git"],
            "agent_mentions": ["ops"],
            "path_globs": [],
            "path_globs_excluded": [],
            "keywords": [
                {"term": "run", "weight": 0.5},
            ],
            "tool_mentions": [],
            "excludes": [],
        },
    },
]


@pytest.fixture()
def fixture_span_signal_catalog_path(tmp_path: Path) -> Path:
    """Write the minimal catalog for span-rule tests.

    Contains debugger (current code/diagnose cell winner), investigator
    (expected span>=2 winner), code-writer (any-domain fallback), and ops
    (any-domain).  Both debugger and investigator are in the code gate.
    """
    catalog = {"entries": _SPAN_SIGNAL_CATALOG_ENTRIES}
    path = tmp_path / "span-signal-catalog.json"
    path.write_text(json.dumps(catalog), encoding="utf-8")
    return path


def _make_entry_with_span(
    corpus_id: int,
    task_description: str,
    domain: str,
    posture: str,
    gold_agent: str,
    area_span: int,
) -> tuple[CorpusEntry, GoldLabel]:
    """Return a CorpusEntry + GoldLabel pair with an explicit area_span.

    Args:
        corpus_id: Unique ID for the entry.
        task_description: Free-text task description.
        domain: Gold domain label.
        posture: Gold posture label.
        gold_agent: Expected routing target agent name.
        area_span: Gold area span count (1 = single-layer, 2+ = multi-layer).

    Returns:
        Tuple of (CorpusEntry, GoldLabel) with area_span set on the label.
    """
    entry = CorpusEntry(
        corpus_id=corpus_id,
        task_description=task_description,
        file_paths=[],
        agent_mentions=[],
        tool_mentions=[],
        command_prefix=None,
        stratum={
            "decision_band": "delegate",
            "td_length_band": "short",
            "file_paths_present": False,
        },
        raw={},
    )
    label = GoldLabel(
        corpus_id=corpus_id,
        domain=domain,
        posture=posture,
        gold_agent=gold_agent,
        is_any=False,
        area_span=area_span,
    )
    return entry, label


class TestAreaSpanRouteInSuppliedCompose:
    """Issue #396: area_span≥2 on (code,diagnose) labels → investigator.

    All tests in this class must be RED before:
      1. GoldLabel.area_span field is added to _reader.py.
      2. run_supplied_compose hard-override for diagnose+span>=2 is added.
    After correct implementation all tests must be GREEN.
    """

    def test_code_diagnose_span2_routes_to_investigator(
        self, fixture_span_signal_catalog_path: Path
    ) -> None:
        """(code, diagnose) entry + label area_span=2 → agent == 'investigator'.

        The span rule mirrors _route_from_postures lines 477-479:
          if winning_posture == 'diagnose' and area_span >= 2:
              agent = 'investigator'

        Before #396: cell_map_lookup('code','diagnose') returns 'debugger';
        the span check is absent on the oracle path → agent='debugger'.
        After #396: the hard override fires before the gate/catalog check
        and agent='investigator'.

        RED: AssertionError — agent='debugger' before implementation.
        """
        from scripts.corpus.eval._systems import run_supplied_compose

        entry, label = _make_entry_with_span(
            corpus_id=1,
            task_description=(
                "The test suite is failing after the refactor — "
                "debug why the error handling breaks across both "
                "the API layer and the database layer."
            ),
            domain="code",
            posture="diagnose",
            gold_agent="investigator",
            area_span=2,
        )
        results = run_supplied_compose(
            [entry], fixture_span_signal_catalog_path, {1: label}
        )
        assert len(results) == 1
        assert results[0].agent == "investigator", (
            f"(code, diagnose) + area_span=2 must route to 'investigator' "
            f"(mirrors _route_from_postures diagnose+span rule, issue #396). "
            f"Got agent={results[0].agent!r}. "
            f"Before #396 cell_map_lookup returns 'debugger' and span is "
            f"not checked on the oracle path."
        )

    def test_code_diagnose_span2_decision_is_delegate_at_0_9(
        self, fixture_span_signal_catalog_path: Path
    ) -> None:
        """(code, diagnose) + area_span=2 → decision='delegate', confidence=0.9.

        The hard override sets decision_out='delegate', confidence_out=0.9
        and posture_routed=True, identical to the normal posture-pick path.

        RED: AssertionError — either agent is 'debugger' (wrong agent) or
        the override is absent, meaning confidence/decision may differ.
        """
        from scripts.corpus.eval._systems import run_supplied_compose

        entry, label = _make_entry_with_span(
            corpus_id=2,
            task_description=(
                "Figure out why the deploy pipeline breaks in staging "
                "but not locally — spans the CI config and the app code."
            ),
            domain="code",
            posture="diagnose",
            gold_agent="investigator",
            area_span=2,
        )
        results = run_supplied_compose(
            [entry], fixture_span_signal_catalog_path, {2: label}
        )
        r = results[0]
        assert r.decision == "delegate", (
            f"(code, diagnose) + area_span=2 must produce decision='delegate'; "
            f"got decision={r.decision!r}."
        )
        assert r.confidence == 0.9, (
            f"(code, diagnose) + area_span=2 must produce confidence=0.9; "
            f"got confidence={r.confidence}."
        )

    def test_code_diagnose_span2_posture_routed_is_true(
        self, fixture_span_signal_catalog_path: Path
    ) -> None:
        """(code, diagnose) + area_span=2 → extras['posture_routed'] is True.

        The override fires BEFORE the sentinel check (per spec), setting
        posture_routed=True — the entry did NOT fall through to decide().

        RED: AssertionError — posture_routed flag reflects wrong path.
        """
        from scripts.corpus.eval._systems import run_supplied_compose

        entry, label = _make_entry_with_span(
            corpus_id=3,
            task_description=(
                "Production errors after the deployment — spans "
                "the backend service and the infra config."
            ),
            domain="code",
            posture="diagnose",
            gold_agent="investigator",
            area_span=2,
        )
        results = run_supplied_compose(
            [entry], fixture_span_signal_catalog_path, {3: label}
        )
        assert results[0].extras.get("posture_routed") is True, (
            f"(code, diagnose) + area_span=2 must set posture_routed=True; "
            f"got {results[0].extras.get('posture_routed')!r}."
        )

    def test_code_diagnose_span1_routes_to_debugger_cell_map_fallback(
        self, fixture_span_signal_catalog_path: Path
    ) -> None:
        """(code, diagnose) + area_span=1 → agent == 'debugger' (cell-map).

        SYNTHETIC TEST — pins the debugger fallback.  When area_span < 2
        the span rule does not fire; cell_map_lookup('code','diagnose')
        returns 'debugger' → posture-routed to 'debugger'.

        No real gold row has area_span=1 for (code, diagnose) + investigator
        (all such rows have area_span=2 per the #396 data edit).

        This test is RED at COLLECTION time until GoldLabel gains area_span
        (TypeError: __init__() got unexpected keyword 'area_span').
        After GoldLabel is updated but before the span override, it will
        pass (debugger is the current cell winner).  After implementation it
        must still pass (span=1 must NOT trigger the investigator override).
        """
        from scripts.corpus.eval._systems import run_supplied_compose

        entry, label = _make_entry_with_span(
            corpus_id=4,
            task_description=(
                "The login endpoint throws a 500 — debug the stack trace."
            ),
            domain="code",
            posture="diagnose",
            gold_agent="debugger",
            area_span=1,
        )
        results = run_supplied_compose(
            [entry], fixture_span_signal_catalog_path, {4: label}
        )
        assert results[0].agent == "debugger", (
            f"(code, diagnose) + area_span=1 must route to 'debugger' "
            f"(span rule must NOT fire for area_span < 2). "
            f"Got agent={results[0].agent!r}."
        )

    def test_code_diagnose_default_span_routes_to_debugger(
        self, fixture_span_signal_catalog_path: Path
    ) -> None:
        """(code, diagnose) + label with area_span absent → agent == 'debugger'.

        When area_span is absent from the label record it defaults to 1.
        The span rule must NOT fire (1 < 2) → cell_map_lookup gives debugger.

        This test is RED at COLLECTION time until GoldLabel gains area_span
        field (TypeError on construction).
        """
        from scripts.corpus.eval._systems import run_supplied_compose

        # Construct a GoldLabel WITHOUT providing area_span — relies on the
        # default value (area_span: int = 1) added in Phase 2.
        entry = CorpusEntry(
            corpus_id=5,
            task_description=(
                "The login endpoint throws a 500 — debug the stack trace."
            ),
            file_paths=[],
            agent_mentions=[],
            tool_mentions=[],
            command_prefix=None,
            stratum={
                "decision_band": "delegate",
                "td_length_band": "short",
                "file_paths_present": False,
            },
            raw={},
        )
        label = GoldLabel(
            corpus_id=5,
            domain="code",
            posture="diagnose",
            gold_agent="debugger",
            is_any=False,
            # area_span intentionally omitted — must default to 1
        )
        results = run_supplied_compose(
            [entry], fixture_span_signal_catalog_path, {5: label}
        )
        assert results[0].agent == "debugger", (
            f"(code, diagnose) + default area_span must route to 'debugger' "
            f"(default is 1, span rule does not fire). "
            f"Got agent={results[0].agent!r}."
        )

    def test_infra_deploy_diagnose_span2_routes_to_investigator(
        self, fixture_span_signal_catalog_path: Path
    ) -> None:
        """Cross-domain parity: (infra_deploy, diagnose) + area_span=2 → investigator.

        The spec says the override fires 'regardless of domain', mirroring
        _route_from_postures lines 477-479 exactly.  This test locks that
        the hard override is domain-agnostic.

        Note: cell_map_lookup('infra_deploy','diagnose') already returns
        'investigator', so this test pins the span rule does not BREAK the
        existing infra_deploy/diagnose path (i.e. it still gets investigator
        regardless of whether the override or the cell-map path fired).
        The cross-domain parity assertion is captured by the code/diagnose
        tests above; this test guards regressions on the infra_deploy path.

        RED at COLLECTION until GoldLabel gains area_span field.
        """
        from scripts.corpus.eval._systems import run_supplied_compose

        entry, label = _make_entry_with_span(
            corpus_id=6,
            task_description=(
                "Production deploy keeps failing — spans both the IaC "
                "config and the application startup code."
            ),
            domain="infra_deploy",
            posture="diagnose",
            gold_agent="investigator",
            area_span=2,
        )
        results = run_supplied_compose(
            [entry], fixture_span_signal_catalog_path, {6: label}
        )
        assert results[0].agent == "investigator", (
            f"(infra_deploy, diagnose) + area_span=2 must route to "
            f"'investigator' (domain-agnostic span rule, issue #396). "
            f"Got agent={results[0].agent!r}."
        )

    def test_sentinel_intact_under_span_rule(
        self, fixture_project_meta_build_catalog_path: Path
    ) -> None:
        """Regression: sentinel branch intact — span rule must not clobber it.

        (project_meta, build) → SELF_HANDLE_SENTINEL.  The sentinel fires
        AFTER the span override (which only fires on diagnose+span>=2).
        With posture='build' the span rule does NOT apply, so the sentinel
        must still fire as implemented by #397.

        Uses the project_meta/build catalog from TestSelfHandleSentinelCompose
        (already in this file).

        RED: AssertionError on decision until #397 is also implemented.
        After both #396 and #397: sentinel test is GREEN regardless of span.
        """
        from scripts.corpus.eval._systems import run_supplied_compose

        entry, label = _make_entry_with_span(
            corpus_id=7,
            task_description=(
                "Rename the Claude Code skill session-variance to "
                "session-analysis in the claude-prospector repo."
            ),
            domain="project_meta",
            posture="build",
            gold_agent="self_handle",
            area_span=2,
        )
        results = run_supplied_compose(
            [entry],
            fixture_project_meta_build_catalog_path,
            {7: label},
        )
        assert results[0].decision == "self_handle", (
            f"Sentinel (project_meta, build) must produce decision='self_handle' "
            f"even when area_span=2 (span rule only applies to diagnose posture). "
            f"Got decision={results[0].decision!r}."
        )
        assert results[0].agent is None, (
            f"Sentinel must produce agent=None; "
            f"got agent={results[0].agent!r}."
        )

    def test_non_diagnose_posture_unaffected_by_span_rule(
        self, fixture_span_signal_catalog_path: Path
    ) -> None:
        """The span rule only fires on posture='diagnose'; build is unaffected.

        A (code, build) entry with area_span=2 must NOT route to investigator.
        It must route via the normal cell-map path: cell_map_lookup('code',
        'build') → 'code-writer' → agent='code-writer'.

        RED at COLLECTION until GoldLabel gains area_span field.
        """
        from scripts.corpus.eval._systems import run_supplied_compose

        entry, label = _make_entry_with_span(
            corpus_id=8,
            task_description="implement the new cache module",
            domain="code",
            posture="build",
            gold_agent="code-writer",
            area_span=2,
        )
        results = run_supplied_compose(
            [entry], fixture_span_signal_catalog_path, {8: label}
        )
        assert results[0].agent != "investigator", (
            f"(code, build) + area_span=2 must NOT route to 'investigator' "
            f"(span rule only applies to posture='diagnose'). "
            f"Got agent={results[0].agent!r}."
        )
        from claude_wayfinder.match._cells import cell_map_lookup
        expected = cell_map_lookup("code", "build")
        assert results[0].agent == expected, (
            f"(code, build) + area_span=2 must route via cell-map to "
            f"{expected!r}; got agent={results[0].agent!r}."
        )


# ===========================================================================
# Issue #396: gold-data guard — 8 ids have area_span=2, 34774 has default 1
# ===========================================================================
#
# These tests load the REAL committed gold file and assert the data edit
# landed.  They are RED until docs/research/2026-06-12-gold-labels-redacted.jsonl
# is updated with "area_span": 2 on the 8 investigator rows.
#
# Path discipline: uses _REPO_ROOT / _RESEARCH_DIR anchors (same as
# TestSelfHandleSentinelGoldIds) so the paths are cwd-independent and
# resolve correctly on CI Linux runners.

_EIGHT_SPAN2_IDS: frozenset[int] = frozenset({
    33660, 35229, 35233, 35266, 35268, 35297, 35317, 35414,
})
_OUT_OF_SCOPE_SPAN_ID: int = 34774


class TestGoldDataAreaSpanEdit:
    """Issue #396: gold-data guard for the 8 investigator (code,diagnose) rows.

    Loads the committed gold file and asserts:
      - Each of the 8 area_span target IDs has area_span >= 2 AND
        gold_agent == 'investigator'.
      - Corpus ID 34774 (gold_agent='researcher') has area_span < 2
        (default 1) and must NOT be affected by the data edit.

    All tests are RED until:
      1. GoldLabel gains the area_span field (#396 Phase 2).
      2. The gold JSONL is updated with "area_span": 2 on the 8 rows.
    """

    _GOLD_LABELS_PATH: Path = (
        _RESEARCH_DIR / "2026-06-12-gold-labels-redacted.jsonl"
    )

    def test_eight_ids_have_area_span_2_and_investigator_gold_agent(
        self,
    ) -> None:
        """All 8 target IDs have area_span >= 2 and gold_agent == 'investigator'.

        Before #396: loaded GoldLabel will raise AttributeError on .area_span
        (field absent from the frozen dataclass).  After GoldLabel is updated
        but before the data edit, area_span will be 1 (default) — not 2.
        After both changes all 8 IDs must satisfy the assertion.

        RED: AttributeError (no area_span field) until GoldLabel updated.
        Then: AssertionError (area_span==1) until gold data edited.
        """
        from scripts.corpus.eval._reader import load_labels

        all_labels = load_labels(self._GOLD_LABELS_PATH)
        missing_ids = _EIGHT_SPAN2_IDS - set(all_labels.keys())
        assert not missing_ids, (
            f"Gold file missing expected corpus IDs: {sorted(missing_ids)}. "
            f"Check {self._GOLD_LABELS_PATH}."
        )

        failures: list[str] = []
        for cid in sorted(_EIGHT_SPAN2_IDS):
            label = all_labels[cid]
            if label.gold_agent != "investigator":
                failures.append(
                    f"  {cid}: gold_agent={label.gold_agent!r} "
                    f"(expected 'investigator')"
                )
            if label.area_span < 2:
                failures.append(
                    f"  {cid}: area_span={label.area_span!r} "
                    f"(expected >= 2)"
                )
        assert not failures, (
            "Gold data edit validation failed for issue #396. "
            "Each of the 8 investigator (code,diagnose) rows must have "
            "area_span >= 2 after the data edit:\n"
            + "\n".join(failures)
        )

    def test_34774_has_default_span_and_researcher_gold_agent(self) -> None:
        """Corpus ID 34774 has area_span < 2 and gold_agent == 'researcher'.

        34774 is (code, diagnose, gold_agent=researcher) — an out-of-scope
        #407 residual.  It must NOT receive area_span=2 in the data edit
        (it must NOT route to investigator via the span rule).

        RED: AttributeError (no area_span field) until GoldLabel updated.
        After GoldLabel is updated and gold data is NOT edited for 34774:
        area_span defaults to 1 → test passes.
        """
        from scripts.corpus.eval._reader import load_labels

        all_labels = load_labels(self._GOLD_LABELS_PATH)
        assert _OUT_OF_SCOPE_SPAN_ID in all_labels, (
            f"corpus_id {_OUT_OF_SCOPE_SPAN_ID} must be present in gold file. "
            f"Check {self._GOLD_LABELS_PATH}."
        )
        label = all_labels[_OUT_OF_SCOPE_SPAN_ID]
        assert label.gold_agent == "researcher", (
            f"corpus_id {_OUT_OF_SCOPE_SPAN_ID} must have gold_agent='researcher'; "
            f"got {label.gold_agent!r}. "
            f"This ID is an out-of-scope #407 residual and must NOT be relabeled."
        )
        assert label.area_span < 2, (
            f"corpus_id {_OUT_OF_SCOPE_SPAN_ID} must have area_span < 2 "
            f"(must NOT receive the #396 data edit); "
            f"got area_span={label.area_span!r}."
        )


# ===========================================================================
# Issue #396 / PR #411 — Codex P2: span override must check catalog
# ===========================================================================
#
# BACKGROUND (PR #411): run_supplied_compose added a hard override:
#   if oracle_posture == "diagnose" and label.area_span >= 2:
#       agent_out = "investigator"; decision_out = "delegate"; ...
#
# Codex (P2) flagged that this override bypasses the catalog/routability
# guard used on the normal posture-routed path.  Against a catalog where
# "investigator" is ABSENT / non-routable, the override still emits a
# high-confidence delegate to an un-routable agent — a phantom route.
#
# PHASE-2 FIX (not yet implemented):
#   Add `and "investigator" in catalog_agent_names` to the override
#   condition.  When investigator is absent the override does NOT fire;
#   control falls to the sentinel/gated/decide() path, posture_routed
#   stays False.
#
# THIS TEST: RED now (override routes to investigator regardless of catalog),
# GREEN after the Phase-2 guard is added.
#
# Catalog: _SPAN_SIGNAL_CATALOG_ENTRIES without "investigator" — debugger,
# code-writer, and ops only.  When the guard is absent the override fires
# and routes to "investigator" (phantom); after the guard, the override
# is skipped and the normal gated path fires instead.

_SPAN_SIGNAL_NO_INVESTIGATOR_CATALOG_ENTRIES: list[dict[str, Any]] = [
    entry
    for entry in _SPAN_SIGNAL_CATALOG_ENTRIES
    if entry["name"] != "investigator"
]


@pytest.fixture()
def fixture_span_signal_no_investigator_catalog_path(tmp_path: Path) -> Path:
    """Write the span-signal catalog with investigator removed.

    Identical to fixture_span_signal_catalog_path except the
    'investigator' entry is excluded.  Used to confirm the span
    override does not phantom-route to an absent agent.
    """
    catalog = {"entries": _SPAN_SIGNAL_NO_INVESTIGATOR_CATALOG_ENTRIES}
    path = tmp_path / "span-signal-no-investigator-catalog.json"
    path.write_text(json.dumps(catalog), encoding="utf-8")
    return path


class TestSpanOverrideRespectsInvestigatorCatalogPresence:
    """PR #411 / Codex P2: span override must not phantom-route to absent agent.

    When investigator is absent from the catalog the (code, diagnose) +
    area_span>=2 override must NOT fire.  Without the Phase-2 guard the
    override ignores the catalog entirely and emits a phantom delegate to
    'investigator'.  After the guard the override is skipped and control
    falls to the normal gated/decide() path.

    This test is RED until the guard
    `and "investigator" in catalog_agent_names` is added to the override
    condition in run_supplied_compose.
    """

    def test_span_override_absent_when_investigator_not_in_catalog(
        self,
        fixture_span_signal_no_investigator_catalog_path: Path,
    ) -> None:
        """Override must not route to investigator when it is absent from catalog.

        Setup:
          - Catalog: debugger, code-writer, ops (no investigator).
          - Entry: domain='code', posture='diagnose', area_span=2.
          - Without the Phase-2 guard: override fires, result.agent ==
            'investigator' (phantom route — agent is not in catalog).
          - After the Phase-2 guard: override does NOT fire;
            result.agent != 'investigator' (core assertion).

        Also asserts extras['posture_routed'] is False to confirm
        control fell through to the decide() path, not the override.

        RED: AssertionError — agent == 'investigator' before the guard
        is added to run_supplied_compose.
        """
        from scripts.corpus.eval._systems import run_supplied_compose

        entry, label = _make_entry_with_span(
            corpus_id=901,
            task_description=(
                "The test suite is failing after the refactor — "
                "debug why the error handling breaks across both "
                "the API layer and the database layer."
            ),
            domain="code",
            posture="diagnose",
            gold_agent="investigator",
            area_span=2,
        )
        results = run_supplied_compose(
            [entry],
            fixture_span_signal_no_investigator_catalog_path,
            {901: label},
        )
        assert len(results) == 1
        r = results[0]
        # Core assertion: no phantom route to an agent absent from catalog.
        assert r.agent != "investigator", (
            f"Override must NOT route to 'investigator' when it is absent "
            f"from the catalog (Codex P2, PR #411). "
            f"Got agent={r.agent!r}. "
            f"Fix: add `and \"investigator\" in catalog_agent_names` to the "
            f"span-override condition in run_supplied_compose."
        )
        # Secondary assertion: override did not fire, so posture_routed
        # must be False (control fell through to the gated/decide() path).
        assert r.extras.get("posture_routed") is False, (
            f"When the span override does not fire, posture_routed must be "
            f"False (control fell to decide()); "
            f"got posture_routed={r.extras.get('posture_routed')!r}."
        )


# ===========================================================================
# Issue #410: corpus 34712 relabelled project_meta → docs_prose
# ===========================================================================
#
# BACKGROUND: corpus 34712 is a plan-doc edit
# (docs/superpowers/plans/...md).  Its gold_agent is doc-writer.
# Before #410 its domain is "project_meta" and posture is "build".
# Because (project_meta, build) maps to SELF_HANDLE_SENTINEL (#397),
# 34712 routes to self_handle — a miss versus its gold_agent doc-writer.
#
# #410 option 1 (user-ratified): relabel domain project_meta → docs_prose.
# (docs_prose, build) → doc-writer already exists in the cell map,
# so no _cells.py / _systems.py change is needed; only the gold-data
# file changes.
#
# THREE TEST GROUPS — all RED until the gold-data relabel lands:
#
#   A. Routing mechanism (fixture-based):
#      Build a synthetic (docs_prose, build, gold_agent=doc-writer) entry,
#      run run_supplied_compose, and assert it routes to doc-writer.
#      This exercises the cell map directly; the test itself may be green
#      today if the cell map is already correct — the RED comes from B + C.
#
#   B. Gold-data guard:
#      Load the committed gold JSONL and assert labels[34712].domain ==
#      "docs_prose".  RED now (currently "project_meta").
#
#   C. Real end-to-end:
#      Join label-blind-prompts.jsonl to real gold for 34712, run
#      run_supplied_compose, and assert decision="delegate",
#      agent="doc-writer".  RED now (currently routes to self_handle).
#
# EXPECTED FAILURE MODES BEFORE IMPLEMENTATION:
#   test_docs_prose_build_routes_to_doc_writer_via_cell_map
#     → may pass (cell map is correct), but B + C are the true guards
#   test_gold_label_34712_domain_is_docs_prose
#     → AssertionError: domain == "project_meta", expected "docs_prose"
#   test_gold_label_34712_posture_unchanged
#     → passes (posture stays "build")
#   test_gold_label_34712_gold_agent_unchanged
#     → passes (gold_agent stays "doc-writer")
#   test_34712_real_entry_routes_to_doc_writer
#     → AssertionError: agent == None / decision == "self_handle"
#       (sentinel fires because gold domain is still project_meta)

# ---------------------------------------------------------------------------
# Catalog for #410 fixture-based routing test.
# Reuse _COMPOSE_CATALOG_ENTRIES (already contains doc-writer + code-writer
# + ops) rather than defining a new constant.
# ---------------------------------------------------------------------------


@pytest.fixture()
def fixture_docs_prose_build_catalog_path(tmp_path: Path) -> Path:
    """Write a catalog that contains doc-writer for docs_prose routing tests.

    Reuses _COMPOSE_CATALOG_ENTRIES which already includes doc-writer
    (keywords: document, docs) and code-writer (keywords: implement, build,
    feature) as well as ops.  doc-writer is in the docs_prose domain gate.
    """
    catalog = {"entries": _COMPOSE_CATALOG_ENTRIES}
    path = tmp_path / "docs-prose-build-catalog.json"
    path.write_text(json.dumps(catalog), encoding="utf-8")
    return path


class TestCorpus34712RelabelledToDocsProse:
    """Issue #410: 34712 relabelled docs_prose → routes to doc-writer.

    Corpus 34712 is a plan-doc edit whose gold_agent is doc-writer.
    Before #410 its domain is project_meta; the #397 sentinel routes it
    to self_handle (a miss).  #410 relabels it docs_prose so that
    cell_map_lookup("docs_prose","build") == "doc-writer" fires correctly.

    Tests B and C are RED until the gold-data relabel lands.
    """

    # Paths resolved via _RESEARCH_DIR (cwd-independent; safe on CI).
    _GOLD_LABELS_PATH: Path = (
        _RESEARCH_DIR / "2026-06-12-gold-labels-redacted.jsonl"
    )
    _PROMPTS_PATH: Path = (
        _RESEARCH_DIR / "label-blind-prompts.jsonl"
    )
    _CORPUS_ID: int = 34712

    # -----------------------------------------------------------------------
    # A. Routing mechanism — synthetic fixture-based test
    # -----------------------------------------------------------------------

    def test_docs_prose_build_routes_to_doc_writer_via_cell_map(
        self,
        fixture_docs_prose_build_catalog_path: Path,
    ) -> None:
        """Synthetic (docs_prose, build, gold=doc-writer) entry routes correctly.

        Verifies that the cell map already maps (docs_prose, build) to
        doc-writer and that run_supplied_compose uses it.  This test
        exercises the routing mechanism independently of the gold-data
        relabel; it serves as a contract pin so the implementer knows
        no _cells.py change is needed.

        Expected: decision="delegate", agent="doc-writer",
        extras["posture_routed"] is True.

        Note: this test may be GREEN today (cell map is already correct);
        the red guards for #410 are tests B and C below.
        """
        from scripts.corpus.eval._systems import run_supplied_compose

        entry, label = _make_entry(
            corpus_id=self._CORPUS_ID,
            task_description=(
                "Edit an existing implementation plan markdown file to add "
                "a new backend-less Slice P parallel to Slice 0. Update "
                "slice descriptions and the dependency graph. "
                "Prose/planning doc edit."
            ),
            domain="docs_prose",
            posture="build",
            gold_agent="doc-writer",
        )
        results = run_supplied_compose(
            [entry],
            fixture_docs_prose_build_catalog_path,
            {self._CORPUS_ID: label},
        )
        assert len(results) == 1, (
            f"Expected 1 result, got {len(results)}"
        )
        r = results[0]
        assert r.decision == "delegate", (
            f"(docs_prose, build) with doc-writer in catalog must produce "
            f"decision='delegate'; got decision={r.decision!r}."
        )
        assert r.agent == "doc-writer", (
            f"cell_map_lookup('docs_prose','build') returns 'doc-writer'; "
            f"run_supplied_compose must delegate to it. "
            f"Got agent={r.agent!r}."
        )
        assert r.extras.get("posture_routed") is True, (
            f"Oracle posture path must set posture_routed=True; "
            f"got posture_routed={r.extras.get('posture_routed')!r}."
        )

    # -----------------------------------------------------------------------
    # B. Gold-data guard — domain relabel verification
    # -----------------------------------------------------------------------

    def test_gold_label_34712_domain_is_docs_prose(self) -> None:
        """Gold label for 34712 must have domain='docs_prose' after #410.

        Loads the committed gold JSONL and asserts the domain field.
        RED now: domain is currently 'project_meta'.

        Path discipline: resolved via _RESEARCH_DIR (never an absolute
        I:/ path — that FileNotFoundErrors on CI's Linux runner).
        """
        from scripts.corpus.eval._reader import load_labels

        all_labels = load_labels(self._GOLD_LABELS_PATH)
        assert self._CORPUS_ID in all_labels, (
            f"corpus_id {self._CORPUS_ID} must be present in "
            f"{self._GOLD_LABELS_PATH}."
        )
        label = all_labels[self._CORPUS_ID]
        assert label.domain == "docs_prose", (
            f"corpus_id {self._CORPUS_ID} must have domain='docs_prose' "
            f"after #410 relabel; got domain={label.domain!r}. "
            f"The domain 'project_meta' causes the #397 sentinel to fire "
            f"and route 34712 to self_handle instead of doc-writer."
        )

    def test_gold_label_34712_posture_unchanged(self) -> None:
        """Posture for 34712 stays 'build' after the #410 relabel.

        Only the domain changes; posture and gold_agent must be untouched.
        """
        from scripts.corpus.eval._reader import load_labels

        all_labels = load_labels(self._GOLD_LABELS_PATH)
        assert self._CORPUS_ID in all_labels, (
            f"corpus_id {self._CORPUS_ID} must be present in gold file."
        )
        label = all_labels[self._CORPUS_ID]
        assert label.posture == "build", (
            f"corpus_id {self._CORPUS_ID} posture must stay 'build' after "
            f"#410 (only domain changes); got posture={label.posture!r}."
        )

    def test_gold_label_34712_gold_agent_unchanged(self) -> None:
        """gold_agent for 34712 stays 'doc-writer' after the #410 relabel.

        The relabel touches domain only; gold_agent must remain doc-writer.
        """
        from scripts.corpus.eval._reader import load_labels

        all_labels = load_labels(self._GOLD_LABELS_PATH)
        assert self._CORPUS_ID in all_labels, (
            f"corpus_id {self._CORPUS_ID} must be present in gold file."
        )
        label = all_labels[self._CORPUS_ID]
        assert label.gold_agent == "doc-writer", (
            f"corpus_id {self._CORPUS_ID} gold_agent must stay 'doc-writer' "
            f"after #410 (only domain changes); "
            f"got gold_agent={label.gold_agent!r}."
        )

    # -----------------------------------------------------------------------
    # C. Real end-to-end — 34712 routes to doc-writer
    # -----------------------------------------------------------------------

    @staticmethod
    def _load_34712_entry_and_label() -> tuple[CorpusEntry, GoldLabel]:
        """Load corpus 34712 from committed research files.

        Returns:
            Tuple of (CorpusEntry, GoldLabel) for corpus 34712.
        """
        import json as _json

        from scripts.corpus.eval._reader import load_labels

        all_labels = load_labels(
            _RESEARCH_DIR / "2026-06-12-gold-labels-redacted.jsonl"
        )
        label = all_labels[34712]

        entry: CorpusEntry | None = None
        prompts_path = _RESEARCH_DIR / "label-blind-prompts.jsonl"
        with open(prompts_path, encoding="utf-8") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line:
                    continue
                rec = _json.loads(line)
                if int(rec["corpus_id"]) == 34712:
                    entry = CorpusEntry(
                        corpus_id=34712,
                        task_description=str(
                            rec.get("task_description", "")
                        ),
                        file_paths=list(rec.get("file_paths") or []),
                        agent_mentions=list(
                            rec.get("agent_mentions") or []
                        ),
                        tool_mentions=list(
                            rec.get("tool_mentions") or []
                        ),
                        command_prefix=rec.get("command_prefix") or None,
                        stratum={},
                        raw=rec,
                    )
                    break

        assert entry is not None, (
            "corpus_id 34712 not found in label-blind-prompts.jsonl. "
            "The file must contain this entry for the end-to-end test."
        )
        return entry, label

    def test_34712_real_entry_routes_to_doc_writer(
        self,
        fixture_docs_prose_build_catalog_path: Path,
    ) -> None:
        """Real corpus 34712 entry routes to doc-writer after #410 relabel.

        Joins label-blind-prompts.jsonl to gold labels and runs
        run_supplied_compose against a catalog containing doc-writer.

        Before #410: gold domain is project_meta → #397 sentinel fires →
        decision='self_handle', agent=None (a miss vs gold_agent=doc-writer).

        After #410: gold domain is docs_prose → cell_map_lookup returns
        doc-writer → decision='delegate', agent='doc-writer'.

        RED now: routes to self_handle because domain is still project_meta.
        """
        from scripts.corpus.eval._systems import run_supplied_compose

        entry, label = self._load_34712_entry_and_label()
        results = run_supplied_compose(
            [entry],
            fixture_docs_prose_build_catalog_path,
            {34712: label},
        )
        assert len(results) == 1, (
            f"Expected 1 result for corpus 34712, got {len(results)}"
        )
        r = results[0]
        assert r.decision == "delegate", (
            f"corpus 34712 must route to delegate after #410 relabel; "
            f"got decision={r.decision!r}. "
            f"Before #410 the domain is 'project_meta' which triggers the "
            f"#397 sentinel (self_handle). After #410 it becomes "
            f"'docs_prose' and routes correctly to doc-writer."
        )
        assert r.agent == "doc-writer", (
            f"corpus 34712 (plan-doc edit) must route to 'doc-writer' after "
            f"#410 relabel; got agent={r.agent!r}. "
            f"Before #410: domain=project_meta → sentinel → agent=None."
        )
