"""Tests for scripts.corpus.eval.__main__ (CLI smoke tests).

Uses P1-P14 fixture corpus and catalog.

RED — written before implementation.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Catalog fixture (reuse from test_systems.py)
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
]


@pytest.fixture()
def fixture_catalog_path(tmp_path: Path) -> Path:
    """Write a minimal catalog JSON for CLI tests."""
    import json

    catalog = {"entries": _CATALOG_ENTRIES_RAW}
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(catalog), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# CLI smoke tests
# ---------------------------------------------------------------------------


class TestCLISmoke:
    """CLI smoke tests for scripts.corpus.eval.__main__."""

    def test_help_exits_zero(self) -> None:
        """--help exits with code 0."""
        result = subprocess.run(
            [sys.executable, "-m", "scripts.corpus.eval", "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0

    def test_runs_on_fixture_corpus_without_labels(
        self,
        fixture_corpus_path: Path,
        fixture_catalog_path: Path,
    ) -> None:
        """CLI runs on fixture corpus without labels and exits 0."""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.corpus.eval",
                "--corpus",
                str(fixture_corpus_path),
                "--catalog",
                str(fixture_catalog_path),
                "--systems",
                "lexical,extractors",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, (
            f"CLI failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "METRICS" in result.stdout.upper() or len(result.stdout) > 0

    def test_runs_with_labels(
        self,
        fixture_corpus_path: Path,
        fixture_labels_path: Path,
        fixture_catalog_path: Path,
    ) -> None:
        """CLI runs with labels and produces metric output."""
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
                str(fixture_catalog_path),
                "--systems",
                "lexical,extractors",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, (
            f"CLI failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        # Should contain metric names
        assert "confident_wrong" in result.stdout.lower() or (
            "metric" in result.stdout.lower()
        )

    def test_missing_corpus_exits_nonzero(
        self,
        fixture_catalog_path: Path,
        tmp_path: Path,
    ) -> None:
        """CLI exits non-zero when corpus file does not exist."""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.corpus.eval",
                "--corpus",
                str(tmp_path / "nonexistent.jsonl"),
                "--catalog",
                str(fixture_catalog_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode != 0

    def test_output_contains_system_labels(
        self,
        fixture_corpus_path: Path,
        fixture_labels_path: Path,
        fixture_catalog_path: Path,
    ) -> None:
        """Output table contains system labels (lexical, extractors)."""
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
                str(fixture_catalog_path),
                "--systems",
                "lexical,extractors",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, (
            f"CLI failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        out_lower = result.stdout.lower()
        assert "lexical" in out_lower
        assert "extractor" in out_lower
