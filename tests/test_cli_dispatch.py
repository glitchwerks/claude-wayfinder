"""Tests for the ``python -m claude_wayfinder dispatch`` CLI subcommand.

Verifies the mode-detection contract for the dispatch skill:

- Demo mode (``$DISPATCH_CATALOG_PATH`` unset) — banner appears, demo runs.
- Real-catalog mode (env set, valid catalog) — matcher decision JSON returned.
- Hard-error mode (env set, missing file) — ``[CATALOG ERROR]`` propagates,
  no demo fallback.
- Hard-error mode (env set, invalid JSON) — same.
- Stale-mtime warning fires when catalog mtime is older than source files.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Constants / Helpers
# ---------------------------------------------------------------------------

_TESTS_DIR = Path(__file__).resolve().parent
_FIXTURES_DIR = _TESTS_DIR / "fixtures"

_DEMO_CATALOG_PATH = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "claude_wayfinder"
    / "fixtures"
    / "demo-catalog.json"
)

_CATALOG_ERROR_PREFIX = "[CATALOG ERROR]"
_DEMO_MODE_BANNER = "no catalog configured — running in demo mode"

#: Minimal valid dispatch context JSON (5-field shape from design § 2.2).
_VALID_CONTEXT: dict[str, Any] = {
    "task_description": "implement the authentication module",
    "file_paths": ["src/auth.py"],
    "agent_mentions": [],
    "tool_mentions": [],
    "command_prefix": None,
}


def _run_dispatch(
    env_overrides: dict[str, str | None] | None = None,
    stdin_data: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ``python -m claude_wayfinder dispatch`` and return the result.

    Args:
        env_overrides: Mapping of env var names to values (or ``None`` to
            unset a variable) to layer on top of the current environment.
        stdin_data: JSON string to pass on stdin.  Defaults to the minimal
            valid dispatch context.

    Returns:
        A ``CompletedProcess`` with ``stdout`` and ``stderr`` captured as
        strings.
    """
    env = os.environ.copy()

    # Always remove DISPATCH_CATALOG_PATH from the base env so tests that
    # want demo mode are not polluted by the caller's environment.
    env.pop("DISPATCH_CATALOG_PATH", None)

    if env_overrides:
        for key, val in env_overrides.items():
            if val is None:
                env.pop(key, None)
            else:
                env[key] = val

    if stdin_data is None:
        stdin_data = json.dumps(_VALID_CONTEXT)

    return subprocess.run(
        [sys.executable, "-m", "claude_wayfinder", "dispatch"],
        input=stdin_data,
        capture_output=True,
        text=True,
        env=env,
    )


# ---------------------------------------------------------------------------
# Demo mode (DISPATCH_CATALOG_PATH unset)
# ---------------------------------------------------------------------------


class TestDemoMode:
    """When ``$DISPATCH_CATALOG_PATH`` is absent, demo mode must activate."""

    def test_demo_mode_exits_zero(self) -> None:
        """Dispatch with no env var set exits 0 (demo mode)."""
        result = _run_dispatch()
        assert result.returncode == 0, (
            f"dispatch exited {result.returncode} in demo mode.\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )

    def test_demo_mode_banner_on_stdout(self) -> None:
        """Demo mode emits the 'no catalog configured' banner on stdout."""
        result = _run_dispatch()
        assert _DEMO_MODE_BANNER in result.stdout, (
            f"Expected banner '{_DEMO_MODE_BANNER}' not found in stdout.\n"
            f"stdout: {result.stdout}"
        )

    def test_demo_mode_no_catalog_error(self) -> None:
        """Demo mode must NOT emit ``[CATALOG ERROR]``."""
        result = _run_dispatch()
        assert _CATALOG_ERROR_PREFIX not in result.stderr, (
            f"Unexpected [CATALOG ERROR] in stderr during demo mode.\n"
            f"stderr: {result.stderr}"
        )


# ---------------------------------------------------------------------------
# Real-catalog mode (env set, valid catalog)
# ---------------------------------------------------------------------------


class TestRealCatalogMode:
    """When ``$DISPATCH_CATALOG_PATH`` points to a valid catalog, the matcher
    must run and return decision JSON."""

    def test_real_catalog_mode_exits_zero(self) -> None:
        """Dispatch with a valid catalog env path exits 0."""
        result = _run_dispatch(
            env_overrides={"DISPATCH_CATALOG_PATH": str(_DEMO_CATALOG_PATH)},
        )
        assert result.returncode == 0, (
            f"dispatch exited {result.returncode} with valid catalog.\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )

    def test_real_catalog_mode_returns_decision_json(self) -> None:
        """Real-catalog mode output must be parseable decision JSON."""
        result = _run_dispatch(
            env_overrides={"DISPATCH_CATALOG_PATH": str(_DEMO_CATALOG_PATH)},
        )
        assert result.returncode == 0, (
            f"dispatch failed; cannot inspect decision JSON.\n"
            f"stderr: {result.stderr}"
        )
        try:
            decision = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            pytest.fail(
                f"stdout is not valid JSON: {exc}\n"
                f"stdout: {result.stdout!r}"
            )
        assert "decision" in decision, (
            f"'decision' key missing from output.\noutput: {decision}"
        )
        assert "confidence" in decision, (
            f"'confidence' key missing from output.\noutput: {decision}"
        )

    def test_real_catalog_mode_no_demo_banner(self) -> None:
        """Real-catalog mode must NOT emit the demo-mode banner."""
        result = _run_dispatch(
            env_overrides={"DISPATCH_CATALOG_PATH": str(_DEMO_CATALOG_PATH)},
        )
        assert _DEMO_MODE_BANNER not in result.stdout, (
            f"Demo banner appeared unexpectedly in real-catalog mode.\n"
            f"stdout: {result.stdout}"
        )


# ---------------------------------------------------------------------------
# Hard-error mode (env set, missing file)
# ---------------------------------------------------------------------------


class TestHardErrorMissingFile:
    """When ``$DISPATCH_CATALOG_PATH`` points to a nonexistent file,
    ``[CATALOG ERROR]`` must propagate and demo mode must NOT activate."""

    def test_missing_catalog_exits_nonzero(self, tmp_path: Path) -> None:
        """Exit code must be nonzero when the catalog file is missing."""
        missing = tmp_path / "nonexistent-catalog.json"
        result = _run_dispatch(
            env_overrides={"DISPATCH_CATALOG_PATH": str(missing)},
        )
        assert result.returncode != 0, (
            f"dispatch exited 0 with missing catalog (expected error).\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )

    def test_missing_catalog_emits_catalog_error(
        self, tmp_path: Path
    ) -> None:
        """``[CATALOG ERROR]`` must appear on stderr for a missing file."""
        missing = tmp_path / "nonexistent-catalog.json"
        result = _run_dispatch(
            env_overrides={"DISPATCH_CATALOG_PATH": str(missing)},
        )
        assert _CATALOG_ERROR_PREFIX in result.stderr, (
            f"Expected '{_CATALOG_ERROR_PREFIX}' in stderr but got:\n"
            f"stderr: {result.stderr}"
        )

    def test_missing_catalog_no_demo_fallback(self, tmp_path: Path) -> None:
        """A missing catalog must NOT fall back to demo mode silently."""
        missing = tmp_path / "nonexistent-catalog.json"
        result = _run_dispatch(
            env_overrides={"DISPATCH_CATALOG_PATH": str(missing)},
        )
        assert _DEMO_MODE_BANNER not in result.stdout, (
            "Demo mode activated silently after missing-file catalog error — "
            "this violates the hard-error contract.\n"
            f"stdout: {result.stdout}"
        )


# ---------------------------------------------------------------------------
# Hard-error mode (env set, invalid JSON)
# ---------------------------------------------------------------------------


class TestHardErrorInvalidJson:
    """When ``$DISPATCH_CATALOG_PATH`` points to a file with invalid JSON,
    ``[CATALOG ERROR]`` must propagate and demo mode must NOT activate."""

    @pytest.fixture
    def invalid_catalog(self, tmp_path: Path) -> Path:
        """Write a file containing invalid JSON and return its path.

        Args:
            tmp_path: pytest temporary directory.

        Returns:
            Path to the invalid catalog file.
        """
        p = tmp_path / "bad-catalog.json"
        p.write_text("{not valid json!!!}", encoding="utf-8")
        return p

    def test_invalid_json_exits_nonzero(
        self, invalid_catalog: Path
    ) -> None:
        """Exit code must be nonzero when catalog contains invalid JSON."""
        result = _run_dispatch(
            env_overrides={"DISPATCH_CATALOG_PATH": str(invalid_catalog)},
        )
        assert result.returncode != 0, (
            f"dispatch exited 0 with invalid JSON catalog (expected error).\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )

    def test_invalid_json_emits_catalog_error(
        self, invalid_catalog: Path
    ) -> None:
        """``[CATALOG ERROR]`` must appear on stderr for invalid JSON."""
        result = _run_dispatch(
            env_overrides={"DISPATCH_CATALOG_PATH": str(invalid_catalog)},
        )
        assert _CATALOG_ERROR_PREFIX in result.stderr, (
            f"Expected '{_CATALOG_ERROR_PREFIX}' in stderr but got:\n"
            f"stderr: {result.stderr}"
        )

    def test_invalid_json_no_demo_fallback(
        self, invalid_catalog: Path
    ) -> None:
        """Invalid JSON catalog must NOT fall back to demo mode silently."""
        result = _run_dispatch(
            env_overrides={"DISPATCH_CATALOG_PATH": str(invalid_catalog)},
        )
        assert _DEMO_MODE_BANNER not in result.stdout, (
            "Demo mode activated silently after invalid-JSON catalog error — "
            "this violates the hard-error contract.\n"
            f"stdout: {result.stdout}"
        )


# ---------------------------------------------------------------------------
# Stale-mtime warning
# ---------------------------------------------------------------------------


class TestStaleMtimeWarning:
    """When catalog mtime is older than any source skill/agent file, a
    staleness warning must be emitted on stderr, but execution must proceed."""

    @pytest.fixture
    def stale_catalog_setup(self, tmp_path: Path) -> dict[str, Path]:
        """Create a directory with a catalog and a newer source file.

        The catalog file is written first, then a source SKILL.md is written
        with ``time.sleep(0.05)`` to ensure a strictly newer mtime.

        Args:
            tmp_path: pytest temporary directory.

        Returns:
            Dict with keys ``"catalog"`` (Path), ``"skills_dir"`` (Path),
            and ``"agents_dir"`` (Path).
        """
        # Write a minimal valid catalog (use the demo catalog content).
        catalog_path = tmp_path / "dispatch-catalog.json"
        catalog_path.write_text(
            _DEMO_CATALOG_PATH.read_text(encoding="utf-8"),
            encoding="utf-8",
        )

        # Ensure the source file has a strictly newer mtime.
        time.sleep(0.05)

        skills_dir = tmp_path / "skills"
        skill_dir = skills_dir / "python"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: python\n---\n# Python Skill\n",
            encoding="utf-8",
        )

        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()

        return {
            "catalog": catalog_path,
            "skills_dir": skills_dir,
            "agents_dir": agents_dir,
        }

    def test_stale_warning_on_stderr(
        self, stale_catalog_setup: dict[str, Path]
    ) -> None:
        """A stale catalog must emit a warning to stderr."""
        catalog = stale_catalog_setup["catalog"]
        skills_dir = stale_catalog_setup["skills_dir"]
        agents_dir = stale_catalog_setup["agents_dir"]

        result = _run_dispatch(
            env_overrides={
                "DISPATCH_CATALOG_PATH": str(catalog),
                "DISPATCH_SKILLS_DIR": str(skills_dir),
                "DISPATCH_AGENTS_DIR": str(agents_dir),
            },
        )
        assert "stale" in result.stderr.lower() or "older" in result.stderr.lower(), (
            f"Expected stale-mtime warning in stderr but got:\n"
            f"stderr: {result.stderr}"
        )

    def test_stale_warning_does_not_abort(
        self, stale_catalog_setup: dict[str, Path]
    ) -> None:
        """Stale catalog warning must not prevent the dispatch from running."""
        catalog = stale_catalog_setup["catalog"]
        skills_dir = stale_catalog_setup["skills_dir"]
        agents_dir = stale_catalog_setup["agents_dir"]

        result = _run_dispatch(
            env_overrides={
                "DISPATCH_CATALOG_PATH": str(catalog),
                "DISPATCH_SKILLS_DIR": str(skills_dir),
                "DISPATCH_AGENTS_DIR": str(agents_dir),
            },
        )
        assert result.returncode == 0, (
            f"dispatch aborted on stale catalog (expected proceed).\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )
        # Output must still be valid decision JSON when catalog is stale.
        try:
            decision = json.loads(result.stdout)
        except json.JSONDecodeError:
            pytest.fail(
                f"stdout is not valid JSON after stale-catalog warning.\n"
                f"stdout: {result.stdout!r}"
            )
        assert "decision" in decision, (
            f"'decision' key missing from stale-catalog output.\n"
            f"output: {decision}"
        )
