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


# ---------------------------------------------------------------------------
# Fix 2: per-row metrics — lexical row must show n/a for extractor-only cols
# ---------------------------------------------------------------------------


class TestPerRowMetricsIsolation:
    """Fix 2: each row computes its own metrics, not the extractor row's metrics.

    Bug: the CLI loop passed ``extractors_r`` as the ``extractors`` arg for
    every non-extractor row, so the lexical row's tierC%, fdb%, and brak%
    columns inherited the extractor system's values.

    Correct behaviour: the lexical row has no ``tier_c_fired`` or ``postures``
    keys in extras → those columns must show ``n/a`` (nan), not a borrowed
    value from the extractor system.
    """

    def test_lexical_row_shows_na_for_tierc_column(
        self,
        fixture_corpus_path: Path,
        fixture_labels_path: Path,
        fixture_catalog_path: Path,
    ) -> None:
        """The lexical row in the output table shows 'n/a' for tierC% column.

        The extractor row will show a real (non-n/a) value for tierC%.
        They must differ if the bug is fixed.
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
        # Parse the table rows for lexical and extractors lines
        lines = result.stdout.splitlines()
        lexical_line = next(
            (row for row in lines if row.startswith("lexical")), None
        )
        extractor_line = next(
            (row for row in lines if row.startswith("extractors")), None
        )
        assert lexical_line is not None, (
            f"No 'lexical' row in output:\n{result.stdout}"
        )
        assert extractor_line is not None, (
            f"No 'extractors' row in output:\n{result.stdout}"
        )
        # Column layout (space-separated, from header):
        #   System  err_corr  adj xpos xdom  tierC%  fdb%  brak%  cw%
        # Parse each line by splitting on whitespace.
        # Fields in order: [system, err_corr, adj, xpos, xdom, tierC, fdb, brak, cw]
        lex_parts = lexical_line.split()
        ext_parts = extractor_line.split()
        # tierC% is the 6th field (index 5) in the row (0-indexed after system label)
        # System(0) err_corr(1) adj(2) xpos(3) xdom(4) tierC(5) fdb(6) brak(7) cw(8)
        assert len(lex_parts) >= 6 and len(ext_parts) >= 6, (
            f"Row has too few fields; lex={lex_parts!r} ext={ext_parts!r}"
        )
        lex_tierc = lex_parts[5]
        ext_tierc = ext_parts[5]
        # The lexical row must show 'n/a' for the tierC% column (no extractor extras)
        assert lex_tierc == "n/a", (
            f"Lexical row tierC% must be 'n/a' (no extractor extras), "
            f"got {lex_tierc!r}.\nLexical row: {lexical_line!r}\n"
            f"Extractor row: {extractor_line!r}"
        )
        # The extractor row must show a real value (not n/a) for tierC%
        assert lex_tierc != ext_tierc, (
            f"Lexical and extractor rows must have different tierC% values "
            f"(rows must not inherit each other's extractor metrics).\n"
            f"Lexical: {lexical_line!r}\nExtractor: {extractor_line!r}"
        )
