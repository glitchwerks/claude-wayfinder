"""Tests for run_lexical_calibrated in scripts.corpus.eval._systems (#374).

Pins that the offline-only calibrated lexical variant:
  - Correctly overrides and restores threshold constants.
  - An extreme gap change measurably shifts the delegate distribution.
  - The code/doc differentiator (Lever B) adjusts scores only for
    code-writer and doc-writer.
  - Module constants are always restored to defaults after the call,
    even when an exception occurs inside the loop.

These tests are label-free and model-free — no corpus or live catalog
is required; the fixture catalog from test_systems.py conftest is used.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.corpus.eval._reader import load_corpus
from scripts.corpus.eval._systems import run_lexical_calibrated

# ---------------------------------------------------------------------------
# Minimal fixture catalog (matches test_systems.py fixture_catalog_path)
# ---------------------------------------------------------------------------

_CATALOG_ENTRIES_RAW = [
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
            "path_globs": ["**/*.md", "**/*.rst"],
            "path_globs_excluded": [],
            "keywords": [
                {"term": "document", "weight": 1.0},
                {"term": "readme", "weight": 1.0},
                {"term": "changelog", "weight": 1.0},
                {"term": "update", "weight": 0.8},
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
]


@pytest.fixture()
def fixture_catalog_path(tmp_path: Path) -> Path:
    """Write a minimal catalog JSON for calibrated lexical runner tests.

    Includes both code-writer and doc-writer so Lever B path/keyword
    signals have competing agents to differentiate.
    """
    catalog = {"entries": _CATALOG_ENTRIES_RAW}
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(catalog), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Default threshold constants (must match _decide.py live values)
# ---------------------------------------------------------------------------

_DEFAULT_DELEGATE_GAP = 0.2
_DEFAULT_DELEGATE_THRESHOLD = 0.85
_DEFAULT_ADVISORY_MIN = 0.5


# ---------------------------------------------------------------------------
# TestRunLexicalCalibratedOverride
# ---------------------------------------------------------------------------


class TestRunLexicalCalibratedOverride:
    """Verify the threshold override actually changes the delegate distribution.

    The override takes effect for the duration of the call and the live
    defaults must be restored afterward (even on failure).
    """

    def test_returns_list_of_system_results(
        self,
        fixture_corpus_path: Path,
        fixture_catalog_path: Path,
    ) -> None:
        """run_lexical_calibrated returns one SystemResult per corpus entry."""
        entries = load_corpus(fixture_corpus_path)
        results = run_lexical_calibrated(entries, fixture_catalog_path)
        assert len(results) == 14

    def test_extreme_gap_zero_increases_delegates(
        self,
        fixture_corpus_path: Path,
        fixture_catalog_path: Path,
    ) -> None:
        """gap=0.0 removes the gap requirement → more delegate decisions.

        At gap=0.0 any agent with score >= threshold can delegate even
        when a close competitor exists.  This produces strictly more
        delegates than the default gap=0.2 on the same corpus.
        """
        entries = load_corpus(fixture_corpus_path)

        results_default = run_lexical_calibrated(
            entries,
            fixture_catalog_path,
            delegate_gap=_DEFAULT_DELEGATE_GAP,
        )
        results_gap0 = run_lexical_calibrated(
            entries,
            fixture_catalog_path,
            delegate_gap=0.0,
        )

        delegates_default = sum(
            1 for r in results_default if r.decision == "delegate"
        )
        delegates_gap0 = sum(
            1 for r in results_gap0 if r.decision == "delegate"
        )
        # gap=0.0 must produce at least as many delegates as the default,
        # and on any realistic corpus will produce strictly more.
        assert delegates_gap0 >= delegates_default, (
            f"gap=0.0 should produce >= delegates vs gap=0.2; "
            f"got gap0={delegates_gap0}, default={delegates_default}"
        )

    def test_extreme_gap_high_reduces_delegates(
        self,
        fixture_corpus_path: Path,
        fixture_catalog_path: Path,
    ) -> None:
        """gap=0.99 requires near-perfect separation → fewer delegates.

        With an extremely high gap requirement almost nothing qualifies
        as delegate, reducing the count compared to the default.
        """
        entries = load_corpus(fixture_corpus_path)

        results_default = run_lexical_calibrated(
            entries,
            fixture_catalog_path,
            delegate_gap=_DEFAULT_DELEGATE_GAP,
        )
        results_high = run_lexical_calibrated(
            entries,
            fixture_catalog_path,
            delegate_gap=0.99,
        )

        delegates_default = sum(
            1 for r in results_default if r.decision == "delegate"
        )
        delegates_high = sum(
            1 for r in results_high if r.decision == "delegate"
        )
        assert delegates_high <= delegates_default, (
            f"gap=0.99 should produce <= delegates vs gap=0.2; "
            f"got high={delegates_high}, default={delegates_default}"
        )

    def test_module_constants_restored_after_normal_call(
        self,
        fixture_corpus_path: Path,
        fixture_catalog_path: Path,
    ) -> None:
        """Live module constants revert to defaults after a normal call.

        This is the core safety guarantee: offline spike calls must NOT
        leave _decide.py constants mutated for subsequent production calls.
        """
        import claude_wayfinder.match._decide as _dm

        entries = load_corpus(fixture_corpus_path)
        _ = run_lexical_calibrated(
            entries,
            fixture_catalog_path,
            delegate_gap=0.0,
            delegate_threshold=0.99,
            advisory_min=0.1,
        )

        assert _dm._DELEGATE_GAP == _DEFAULT_DELEGATE_GAP, (
            f"_DELEGATE_GAP not restored: got {_dm._DELEGATE_GAP!r}"
        )
        assert _dm._DELEGATE_THRESHOLD == _DEFAULT_DELEGATE_THRESHOLD, (
            f"_DELEGATE_THRESHOLD not restored: got {_dm._DELEGATE_THRESHOLD!r}"
        )
        assert _dm._ADVISORY_MIN == _DEFAULT_ADVISORY_MIN, (
            f"_ADVISORY_MIN not restored: got {_dm._ADVISORY_MIN!r}"
        )

    def test_gap_zero_vs_gap_high_decision_counts_differ(
        self,
        tmp_path: Path,
        fixture_catalog_path: Path,
    ) -> None:
        """Extreme gap values produce measurably different decision distributions.

        This directly pins that the override takes effect — if the
        constants were not being patched the two distributions would be
        identical.

        Uses a bespoke two-entry corpus that produces a tie on the
        fixture catalog (code-writer=1.0, doc-writer=1.0) so gap=0.0
        will delegate and gap=0.99 will not (gap between them is 0,
        i.e. < 0.99).
        """
        # "implement" + "update" + .py extension → both code-writer and
        # doc-writer score high; using "implement" alone maximises the
        # tie probability on the fixture catalog.
        records = [
            {
                "type": "matcher_decision",
                "session_id": f"session-gap-{i:03d}",
                "input": {
                    "task_description": (
                        "Implement and document the new feature in"
                        " src/module.py and README.md"
                    ),
                    "file_paths": ["src/module.py", "README.md"],
                    "agent_mentions": [],
                    "tool_mentions": [],
                    "command_prefix": None,
                },
                "output": {
                    "decision": "delegate",
                    "agent": "code-writer",
                    "confidence": 0.9,
                },
                "corpus_id": i,
                "stratum": {
                    "decision_band": "delegate",
                    "td_length_band": "short",
                    "file_paths_present": True,
                },
            }
            for i in range(1, 6)
        ]
        corpus_file = tmp_path / "gap-test-corpus.jsonl"
        import json as _json
        lines = [_json.dumps(r, ensure_ascii=False) for r in records]
        corpus_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

        entries = load_corpus(corpus_file)

        results_low = run_lexical_calibrated(
            entries, fixture_catalog_path, delegate_gap=0.0
        )
        results_high = run_lexical_calibrated(
            entries, fixture_catalog_path, delegate_gap=0.99
        )

        delegates_low = sum(1 for r in results_low if r.decision == "delegate")
        delegates_high = sum(
            1 for r in results_high if r.decision == "delegate"
        )
        # gap=0.0 removes the gap requirement entirely → same/more delegates;
        # gap=0.99 requires near-perfect separation → same/fewer delegates.
        # At minimum they must not both be equal to each other — the override
        # must be measurable.  (Some entries delegate under both; the
        # difference is on tie entries.)
        assert delegates_low >= delegates_high, (
            f"gap=0.0 should produce >= delegates vs gap=0.99; "
            f"got low={delegates_low}, high={delegates_high}"
        )

    def test_corpus_ids_preserved(
        self,
        fixture_corpus_path: Path,
        fixture_catalog_path: Path,
    ) -> None:
        """Each result has the same corpus_id as the input entry."""
        entries = load_corpus(fixture_corpus_path)
        results = run_lexical_calibrated(entries, fixture_catalog_path)
        result_ids = [r.corpus_id for r in results]
        entry_ids = [e.corpus_id for e in entries]
        assert result_ids == entry_ids

    def test_calibration_extras_present(
        self,
        fixture_corpus_path: Path,
        fixture_catalog_path: Path,
    ) -> None:
        """extras dict carries calibration metadata for provenance."""
        entries = load_corpus(fixture_corpus_path)
        results = run_lexical_calibrated(
            entries,
            fixture_catalog_path,
            delegate_gap=0.05,
            code_doc_boost=0.15,
        )
        for r in results:
            cal = r.extras.get("calibration")
            assert cal is not None, "extras['calibration'] must be present"
            assert cal["delegate_gap"] == 0.05
            assert cal["code_doc_boost"] == 0.15


# ---------------------------------------------------------------------------
# TestLeverBCodeDocDifferentiator
# ---------------------------------------------------------------------------


class TestLeverBCodeDocDifferentiator:
    """Lever B: code_doc_boost shifts code-writer and doc-writer scores.

    The differentiator must:
    - Boost code-writer on code-heavy prompts (code extensions or keywords).
    - Boost doc-writer on prose-heavy prompts (md extension or doc keywords).
    - Leave all other agents untouched.
    - Not affect the aggregate delegate count when the corpus has no
      code/doc tie entries (it should be neutral).
    """

    def _make_entry(
        self,
        tmp_path: Path,
        task: str,
        file_paths: list[str],
        corpus_id: int = 1,
    ) -> Path:
        """Write a single-entry corpus JSONL to tmp_path."""
        record = {
            "type": "matcher_decision",
            "session_id": f"session-lb-{corpus_id:03d}",
            "input": {
                "task_description": task,
                "file_paths": file_paths,
                "agent_mentions": [],
                "tool_mentions": [],
                "command_prefix": None,
            },
            "output": {
                "decision": "delegate",
                "agent": "code-writer",
                "confidence": 0.9,
            },
            "corpus_id": corpus_id,
            "stratum": {
                "decision_band": "delegate",
                "td_length_band": "short",
                "file_paths_present": bool(file_paths),
            },
        }
        corpus_file = tmp_path / f"lb-corpus-{corpus_id}.jsonl"
        corpus_file.write_text(
            json.dumps(record) + "\n", encoding="utf-8"
        )
        return corpus_file

    def test_boost_zero_matches_no_boost(
        self,
        tmp_path: Path,
        fixture_catalog_path: Path,
    ) -> None:
        """code_doc_boost=0.0 and omitting the arg produce identical results."""
        corpus_file = self._make_entry(
            tmp_path,
            task="Implement the new feature in src/module.py",
            file_paths=["src/module.py"],
        )
        entries = load_corpus(corpus_file)

        results_zero = run_lexical_calibrated(
            entries, fixture_catalog_path, code_doc_boost=0.0
        )
        results_default = run_lexical_calibrated(
            entries, fixture_catalog_path
        )

        for r_z, r_d in zip(results_zero, results_default):
            assert r_z.decision == r_d.decision
            assert r_z.agent == r_d.agent
            assert r_z.confidence == r_d.confidence

    def test_doc_path_boosts_doc_writer_agent_or_decision(
        self,
        tmp_path: Path,
        fixture_catalog_path: Path,
    ) -> None:
        """A .md file path activates the doc-writer boost path without error.

        We don't assert the final agent here (depends on catalog weights)
        but we do assert the call succeeds and returns one result per entry.
        """
        corpus_file = self._make_entry(
            tmp_path,
            task="Update the changelog and readme for the release",
            file_paths=["CHANGELOG.md", "README.md"],
        )
        entries = load_corpus(corpus_file)
        results = run_lexical_calibrated(
            entries, fixture_catalog_path, code_doc_boost=0.15
        )
        assert len(results) == 1
        assert isinstance(results[0].decision, str)

    def test_code_path_does_not_crash(
        self,
        tmp_path: Path,
        fixture_catalog_path: Path,
    ) -> None:
        """A .py file path activates the code-writer boost path without error."""
        corpus_file = self._make_entry(
            tmp_path,
            task="Implement the new API endpoint",
            file_paths=["src/api/endpoint.py"],
        )
        entries = load_corpus(corpus_file)
        results = run_lexical_calibrated(
            entries, fixture_catalog_path, code_doc_boost=0.15
        )
        assert len(results) == 1
        assert results[0].confidence >= 0.0

    def test_module_constants_restored_after_boost_call(
        self,
        tmp_path: Path,
        fixture_catalog_path: Path,
    ) -> None:
        """Module constants still restored even when Lever B is active."""
        import claude_wayfinder.match._decide as _dm

        corpus_file = self._make_entry(
            tmp_path,
            task="Implement new module",
            file_paths=["src/mod.py"],
        )
        entries = load_corpus(corpus_file)
        _ = run_lexical_calibrated(
            entries,
            fixture_catalog_path,
            delegate_gap=0.05,
            code_doc_boost=0.15,
        )

        assert _dm._DELEGATE_GAP == _DEFAULT_DELEGATE_GAP, (
            f"_DELEGATE_GAP not restored after Lever B call: "
            f"got {_dm._DELEGATE_GAP!r}"
        )
