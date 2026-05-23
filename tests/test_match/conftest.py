"""Shared fixtures and helpers for the tests/test_match/ package.

All constants, catalog-builder helpers, subprocess runner, and path
references are centralised here so individual test modules can import
them without repetition.
"""

from __future__ import annotations

import json
import os
import re as _re
import subprocess
import sys
from pathlib import Path
from typing import Any

import claude_wayfinder.match as _match_mod

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]

# match.py is now a package (match/__init__.py); invoke via -m rather than
# as a script path so tests continue to work after the package split.
_MATCH_MODULE = ["claude_wayfinder.match"]

PYTHON = sys.executable

#: Path to the agents directory in the current checkout.
_AGENTS_DIR = REPO_ROOT / "agents"
_SKILLS_DIR = REPO_ROOT / "skills"
_TRIGGERS_DIR = REPO_ROOT / "triggers"

#: Fixture directories containing synthetic agents and skills for
#: catalog cascade tests that must run without the private harness.
_FIXTURE_AGENTS_DIR = REPO_ROOT / "tests" / "fixtures" / "agents"
_FIXTURE_SKILLS_DIR = REPO_ROOT / "tests" / "fixtures" / "skills"

# build_catalog is now a package; invoke via -m rather than as a file path.
_BUILD_MODULE = ["claude_wayfinder.build_catalog"]


# ---------------------------------------------------------------------------
# Catalog builder helpers
# ---------------------------------------------------------------------------


def _make_agent(
    name: str,
    *,
    keywords: list[dict[str, Any]] | None = None,
    path_globs: list[str] | None = None,
    path_globs_excluded: list[str] | None = None,
    tool_mentions: list[str] | None = None,
    command_prefixes: list[str] | None = None,
    agent_mentions: list[str] | None = None,
    excludes: list[str] | None = None,
    applicable_skills: list[str] | None = None,
    routable: bool = True,
) -> dict[str, Any]:
    """Build a minimal agent catalog entry."""
    return {
        "name": name,
        "kind": "agent",
        "description": f"Agent {name}.",
        "source": "owned",
        "routable": routable,
        "triggers": {
            "command_prefixes": command_prefixes or [],
            "agent_mentions": agent_mentions or [],
            "path_globs": path_globs or [],
            "path_globs_excluded": path_globs_excluded or [],
            "keywords": [{"term": k["term"], "weight": k["weight"]} for k in (keywords or [])],
            "tool_mentions": tool_mentions or [],
            "excludes": excludes or [],
        },
        "applicable_skills": applicable_skills or [],
    }


def _make_skill(
    name: str,
    *,
    keywords: list[dict[str, Any]] | None = None,
    path_globs: list[str] | None = None,
    path_globs_excluded: list[str] | None = None,
    tool_mentions: list[str] | None = None,
    command_prefixes: list[str] | None = None,
    agent_mentions: list[str] | None = None,
    excludes: list[str] | None = None,
    applicable_agents: list[str] | None = None,
) -> dict[str, Any]:
    """Build a minimal skill catalog entry."""
    return {
        "name": name,
        "kind": "skill",
        "description": f"Skill {name}.",
        "source": "owned",
        "triggers": {
            "command_prefixes": command_prefixes or [],
            "agent_mentions": agent_mentions or [],
            "path_globs": path_globs or [],
            "path_globs_excluded": path_globs_excluded or [],
            "keywords": [{"term": k["term"], "weight": k["weight"]} for k in (keywords or [])],
            "tool_mentions": tool_mentions or [],
            "excludes": excludes or [],
        },
        "applicable_agents": applicable_agents or [],
    }


def _catalog(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Wrap entries in catalog envelope."""
    return {"schema_version": 1, "entries": entries}


# ---------------------------------------------------------------------------
# Subprocess helper
# ---------------------------------------------------------------------------


def _run(
    stdin_obj: dict[str, Any],
    catalog: dict[str, Any],
    *,
    catalog_path: Path | None = None,
    extra_env: dict[str, str] | None = None,
    tmp_path: Path,
) -> subprocess.CompletedProcess[str]:
    """Run match.py via subprocess with the given catalog and input."""
    if catalog_path is None:
        catalog_path = tmp_path / "catalog.json"
        catalog_path.write_text(json.dumps(catalog), encoding="utf-8")

    env = {**os.environ, "DISPATCH_CATALOG_PATH": str(catalog_path)}
    if extra_env:
        env.update(extra_env)

    return subprocess.run(
        [PYTHON, "-m", *_MATCH_MODULE],
        input=json.dumps(stdin_obj),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


# ---------------------------------------------------------------------------
# Live and synthetic catalog builders (used by regression tests)
# ---------------------------------------------------------------------------


def _build_live_catalog(tmp_path: Path) -> Path:
    """Build a dispatch catalog from the worktree's agents/ and skills/ dirs.

    Runs ``build_dispatch_catalog.py`` with ``--agents-dir`` and
    ``--skills-dir`` pointed at the worktree sources so the resulting
    catalog reflects the current state of agent frontmatter — including
    any edits made as part of the fix being tested.

    Args:
        tmp_path: pytest temporary directory for catalog and log output.

    Returns:
        Path to the generated catalog JSON file.
    """
    if not _AGENTS_DIR.is_dir():
        import pytest as _pytest
        _pytest.skip(
            "requires harness agents/ directory (not present in public repo)"
        )
    out_path = tmp_path / "live-catalog.json"
    log_path = tmp_path / "live-catalog.log"
    result = subprocess.run(
        [
            PYTHON,
            "-m",
            *_BUILD_MODULE,
            "--agents-dir",
            str(_AGENTS_DIR),
            "--skills-dir",
            str(_SKILLS_DIR),
            "--out",
            str(out_path),
            "--log",
            str(log_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in (0, 2):
        raise RuntimeError(f"Catalog build failed (exit {result.returncode}):\n" f"{result.stderr}")
    return out_path


def _build_synthetic_catalog(tmp_path: Path) -> Path:
    """Build a dispatch catalog from the tests/fixtures/ agent and skill dirs.

    Replaces ``_build_live_catalog`` for tests that previously skipped when
    the private harness ``agents/`` directory was absent.  The fixture
    directories are committed alongside the tests and must always exist —
    a missing fixture directory is a fatal error, not a skip.

    Args:
        tmp_path: pytest temporary directory for catalog and log output.

    Returns:
        Path to the generated catalog JSON file.

    Raises:
        AssertionError: If either fixture directory is missing.
    """
    assert _FIXTURE_AGENTS_DIR.is_dir(), (
        f"Fixture agents directory missing: {_FIXTURE_AGENTS_DIR}. "
        "Fixture files are committed and must always be present."
    )
    assert _FIXTURE_SKILLS_DIR.is_dir(), (
        f"Fixture skills directory missing: {_FIXTURE_SKILLS_DIR}. "
        "Fixture files are committed and must always be present."
    )
    out_path = tmp_path / "synthetic-catalog.json"
    log_path = tmp_path / "synthetic-catalog.log"
    result = subprocess.run(
        [
            PYTHON,
            "-m",
            *_BUILD_MODULE,
            "--agents-dir",
            str(_FIXTURE_AGENTS_DIR),
            "--skills-dir",
            str(_FIXTURE_SKILLS_DIR),
            "--out",
            str(out_path),
            "--log",
            str(log_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in (0, 2):
        raise RuntimeError(
            f"Synthetic catalog build failed (exit {result.returncode}):\n"
            f"{result.stderr}\nLog path: {log_path}"
        )
    return out_path


# ---------------------------------------------------------------------------
# Dispatch-log test fixtures (module-level constants used in test_catalog.py)
# ---------------------------------------------------------------------------

#: Representative minimal catalog used across log tests.
_LOG_TEST_CATALOG = _catalog(
    [
        _make_agent(
            "code-writer",
            keywords=[{"term": "implement", "weight": 1.0}],
            path_globs=["**/*.py"],
        ),
    ]
)

#: Representative input that produces a non-trivial decision.
_LOG_TEST_INPUT = {
    "task_description": "implement the new feature",
    "file_paths": ["src/main.py"],
}

#: ISO 8601 UTC timestamp regex (e.g. 2026-05-03T12:34:56.789012Z).
_ISO8601_RE = _re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z$")

#: Valid matcher decision strings (v0.10.0, 7-branch surface).
#: 'ambiguous' was removed in v0.9.0 (#202).
#: 'mixed_content' was added in v0.10.0 (#210).
_VALID_DECISIONS = {
    "delegate",
    "self_handle",
    "self_handle_unaided",
    "advisory",
    "ask_user",
    "needs_more_detail",
    "mixed_content",
}

__all__ = [
    "PYTHON",
    "REPO_ROOT",
    "_AGENTS_DIR",
    "_BUILD_MODULE",
    "_FIXTURE_AGENTS_DIR",
    "_FIXTURE_SKILLS_DIR",
    "_ISO8601_RE",
    "_LOG_TEST_CATALOG",
    "_LOG_TEST_INPUT",
    "_MATCH_MODULE",
    "_SKILLS_DIR",
    "_TRIGGERS_DIR",
    "_VALID_DECISIONS",
    "_build_live_catalog",
    "_build_synthetic_catalog",
    "_catalog",
    "_make_agent",
    "_make_skill",
    "_match_mod",
    "_re",
    "_run",
]
