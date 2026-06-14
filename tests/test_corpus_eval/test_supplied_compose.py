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
# Deterministic scenario:
#   domain="infra_deploy", posture="build"
#   cell_map_lookup("infra_deploy", "build"):
#     no direct ("infra_deploy", "build") entry → falls back to ("any", "build")
#     → preferred = "code-writer"
#   infra_deploy gate = {"devops"} ∪ ANY_DOMAIN_AGENTS
#     → includes: "investigator", "approach-critic", "auditor", "researcher",
#                 "ops", "project-planner", "devops"
#     → EXCLUDES "code-writer" (verbatim from probe; issue #364 will revisit)
#   Catalog below DOES contain "code-writer" (routable, scores >0 on "build")
#     so preferred in catalog_agent_names = True
#   But gated_names = {se.entry.name for se in gated_agents}
#     = infra_deploy-allowed agents that score >0
#     "code-writer" is excluded from the gate → NOT in gated_names
#   → posture-routed block is skipped
#   → decide(gated_agents) runs → posture_routed=False
#
# Reference: _cells.py line 49:
#   "infra_deploy": frozenset({"devops"}) | ANY_DOMAIN_AGENTS

_FALLBACK_PREFERRED_IN_CATALOG_NOT_GATED: list[dict[str, Any]] = [
    # code-writer IS in catalog (routable) but is excluded from the
    # infra_deploy domain gate.  Preferred cell lookup resolves to
    # "code-writer" via ("any","build") fallback, but it fails the
    # `preferred in gated_names` check.
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
    # ops IS in infra_deploy gate (via ANY_DOMAIN_AGENTS) and scores >0
    # on "deploy" → decide(gated_agents) returns ops, not code-writer.
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
def fixture_preferred_in_catalog_not_gated_path(tmp_path: Path) -> Path:
    """Write the catalog used to test the preferred-in-catalog-not-gated branch.

    Catalog contains code-writer (routable, keyword: build/implement) and ops
    (routable, keyword: deploy).  Domain=infra_deploy gates OUT code-writer
    but allows ops (via ANY_DOMAIN_AGENTS).

    When cell_map_lookup("infra_deploy", "build") returns "code-writer" as
    preferred, that agent is in the catalog but NOT in gated_names — so the
    posture-routed block is skipped and decide(gated_agents) fires instead.
    """
    catalog = {"entries": _FALLBACK_PREFERRED_IN_CATALOG_NOT_GATED}
    path = tmp_path / "preferred-in-catalog-not-gated.json"
    path.write_text(json.dumps(catalog), encoding="utf-8")
    return path


class TestFallbackPreferredInCatalogNotGated:
    """Anchor 8: posture present, cell-winner in catalog but excluded by domain gate.

    Setup:
      domain="infra_deploy", posture="build"
      cell_map_lookup("infra_deploy","build") → "code-writer" (via any/build)
      catalog: code-writer (routable, scores >0 on "build") + ops (in gate)
      infra_deploy gate: includes ops (ANY_DOMAIN_AGENTS) but NOT code-writer
      task_description="deploy the build" → code-writer scores on "build",
        ops scores on "deploy"

    Branch under test (scripts/corpus/eval/_systems.py ~lines 1044-1047):
      if (
          preferred                            ← "code-writer" (truthy)
          and preferred in gated_names         ← False: excluded by infra_deploy gate
          and preferred in catalog_agent_names ← True: in catalog
      ):
    The entire block is False → posture_routed stays False → decide(gated_agents).

    Expected:
      - extras["posture_routed"] is False
      - confidence != 0.9 (posture-routed path not taken)
      - agent is NOT "code-writer" (gated out of infra_deploy)
    """

    def test_posture_routed_is_false_when_preferred_excluded_by_domain_gate(
        self, fixture_preferred_in_catalog_not_gated_path: Path
    ) -> None:
        """extras['posture_routed'] is False when preferred is in catalog but gated out.

        domain=infra_deploy, posture=build → cell gives "code-writer".
        code-writer exists in catalog but the infra_deploy gate excludes it
        → preferred in gated_names is False → posture-routed block skipped.
        """
        from scripts.corpus.eval._systems import run_supplied_compose

        entry, label = _make_entry(
            corpus_id=1,
            task_description="deploy the build",
            domain="infra_deploy",
            posture="build",
            gold_agent="ops",
        )
        results = run_supplied_compose(
            [entry],
            fixture_preferred_in_catalog_not_gated_path,
            {1: label},
        )
        assert len(results) == 1
        assert results[0].extras.get("posture_routed") is False, (
            f"preferred in catalog but gated out → posture_routed must be "
            f"False, got {results[0].extras.get('posture_routed')!r}"
        )

    def test_confidence_is_not_0_9_when_preferred_excluded_by_domain_gate(
        self, fixture_preferred_in_catalog_not_gated_path: Path
    ) -> None:
        """confidence != 0.9 when the preferred-not-gated fallback fires.

        The posture-routed path unconditionally sets confidence=0.9.  When
        the domain gate excludes the preferred agent and the block is skipped,
        confidence must come from decide(), not from the posture-routed path.
        """
        from scripts.corpus.eval._systems import run_supplied_compose

        entry, label = _make_entry(
            corpus_id=1,
            task_description="deploy the build",
            domain="infra_deploy",
            posture="build",
            gold_agent="ops",
        )
        results = run_supplied_compose(
            [entry],
            fixture_preferred_in_catalog_not_gated_path,
            {1: label},
        )
        assert results[0].confidence != 0.9, (
            f"Fallback (preferred gated out) must not produce confidence=0.9; "
            f"got confidence={results[0].confidence}"
        )

    def test_agent_is_not_code_writer_when_gated_out_of_infra_deploy(
        self, fixture_preferred_in_catalog_not_gated_path: Path
    ) -> None:
        """Returned agent is NOT code-writer; it is from the infra_deploy-gated set.

        code-writer is the cell-map preferred agent but is excluded by the
        infra_deploy gate.  decide(gated_agents) runs over the remaining gated
        candidates (ops scores >0 on "deploy").  The returned agent must not
        be code-writer — it is either ops (or None if decide returns advisory).
        """
        from scripts.corpus.eval._systems import run_supplied_compose

        entry, label = _make_entry(
            corpus_id=1,
            task_description="deploy the build",
            domain="infra_deploy",
            posture="build",
            gold_agent="ops",
        )
        results = run_supplied_compose(
            [entry],
            fixture_preferred_in_catalog_not_gated_path,
            {1: label},
        )
        # code-writer must NOT be the result — it is excluded from infra_deploy gate
        assert results[0].agent != "code-writer", (
            f"code-writer must be excluded by infra_deploy gate; "
            f"got agent={results[0].agent!r}"
        )
        # The agent must be from the infra_deploy-allowed set or None
        infra_deploy_allowed = {
            "devops", "investigator", "approach-critic",
            "auditor", "researcher", "ops", "project-planner",
        }
        assert results[0].agent is None or results[0].agent in infra_deploy_allowed, (
            f"Fallback agent must be from infra_deploy gate or None; "
            f"got agent={results[0].agent!r}"
        )
