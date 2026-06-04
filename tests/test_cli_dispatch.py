"""Tests for the ``python -m claude_wayfinder dispatch`` CLI subcommand.

Verifies the mode-detection contract for the dispatch skill:

- Demo mode (``--demo`` flag) — banner appears, demo runs; ignores env/catalog.
- Real-catalog mode (env set, valid catalog) — matcher decision JSON returned.
- Hard-error mode (env set, missing file) — ``[CATALOG ERROR]`` propagates,
  no demo fallback.
- Hard-error mode (env set, invalid JSON) — same.
- Canonical-default fallback — when neither ``--demo`` nor
  ``$DISPATCH_CATALOG_PATH`` is set, the canonical path is resolved.
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
    extra_args: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ``python -m claude_wayfinder dispatch`` and return the result.

    Args:
        env_overrides: Mapping of env var names to values (or ``None`` to
            unset a variable) to layer on top of the current environment.
        stdin_data: JSON string to pass on stdin.  Defaults to the minimal
            valid dispatch context.
        extra_args: Additional CLI arguments to append after ``dispatch``
            (e.g. ``["--demo"]``).

    Returns:
        A ``CompletedProcess`` with ``stdout`` and ``stderr`` captured as
        strings.
    """
    env = os.environ.copy()

    # Always remove DISPATCH_CATALOG_PATH from the base env so tests that
    # rely on canonical-fallback or demo mode are not polluted by the
    # caller's environment.
    env.pop("DISPATCH_CATALOG_PATH", None)
    # Also remove CLAUDE_HOME so canonical-path tests can redirect it cleanly.
    env.pop("CLAUDE_HOME", None)

    if env_overrides:
        for key, val in env_overrides.items():
            if val is None:
                env.pop(key, None)
            else:
                env[key] = val

    if stdin_data is None:
        stdin_data = json.dumps(_VALID_CONTEXT)

    cmd = [sys.executable, "-m", "claude_wayfinder", "dispatch"]
    if extra_args:
        cmd.extend(extra_args)

    return subprocess.run(
        cmd,
        input=stdin_data,
        capture_output=True,
        text=True,
        env=env,
    )


# ---------------------------------------------------------------------------
# Demo mode (--demo flag)
# ---------------------------------------------------------------------------


class TestDemoMode:
    """When ``--demo`` is passed, demo mode must activate regardless of env."""

    def test_demo_flag_exits_zero(self) -> None:
        """Dispatch with ``--demo`` exits 0."""
        result = _run_dispatch(extra_args=["--demo"])
        assert result.returncode == 0, (
            f"dispatch exited {result.returncode} in demo mode.\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )

    def test_demo_flag_banner_on_stdout(self) -> None:
        """``--demo`` emits the 'no catalog configured' banner on stdout."""
        result = _run_dispatch(extra_args=["--demo"])
        assert _DEMO_MODE_BANNER in result.stdout, (
            f"Expected banner '{_DEMO_MODE_BANNER}' not found in stdout.\n"
            f"stdout: {result.stdout}"
        )

    def test_demo_flag_no_catalog_error(self) -> None:
        """``--demo`` must NOT emit ``[CATALOG ERROR]``."""
        result = _run_dispatch(extra_args=["--demo"])
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

    def test_stdout_decision_includes_catalog_hash(self) -> None:
        """Stdout decision JSON must include ``catalog_hash`` as a real
        sha256 digest so the JS hook can write a complete attributed row
        (issue #311).

        The JS hook builds its log entry via
        ``decision.catalog_hash ?? null`` — if the Python matcher omits
        this field from stdout the hook writes ``null`` and the attributed
        row is incomplete.
        """
        import re

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
        assert "catalog_hash" in decision, (
            f"'catalog_hash' key missing from stdout decision JSON (issue #311).\n"
            f"output: {decision}"
        )
        catalog_hash = decision["catalog_hash"]
        assert re.match(r"^sha256:[0-9a-f]{64}$", catalog_hash), (
            f"catalog_hash must match sha256:<64 hex chars>, got: "
            f"{catalog_hash!r}"
        )

    def test_stdout_decision_includes_matcher_version(self) -> None:
        """Stdout decision JSON must include ``matcher_version`` as a
        non-empty string so the JS hook can write a complete attributed
        row (issue #311).

        The JS hook builds its log entry via
        ``decision.matcher_version ?? null`` — if the Python matcher
        omits this field from stdout the hook writes ``null``.
        """
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
        assert "matcher_version" in decision, (
            f"'matcher_version' key missing from stdout decision JSON "
            f"(issue #311).\noutput: {decision}"
        )
        matcher_version = decision["matcher_version"]
        assert isinstance(matcher_version, str) and matcher_version, (
            f"matcher_version must be a non-empty string, got: "
            f"{matcher_version!r}"
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


# ---------------------------------------------------------------------------
# Demo-mode override short-circuit
# ---------------------------------------------------------------------------


class TestDemoModeOverride:
    """When ``$DISPATCH_OVERRIDES_PATH`` is set and a rule matches a demo
    prompt, ``run_demo()`` must short-circuit scoring and emit the override
    decision with ``disposition_source: override`` in the formatted output."""

    @pytest.fixture
    def override_file(self, tmp_path: Path) -> Path:
        """Write an overrides file whose rule matches the first demo prompt.

        The first demo prompt has ``file_paths: ["src/auth.py"]``, so a
        ``path_globs: ["src/*.py"]`` predicate will fire on it.

        Args:
            tmp_path: pytest temporary directory.

        Returns:
            Path to the written overrides JSON file.
        """
        rules = {
            "rules": [
                {
                    "id": "demo-override-fires",
                    "decision": "self_handle_unaided",
                    "confidence": 0.99,
                    "rationale": "override rule matched src/*.py",
                    "predicates": {
                        "path_globs": ["src/*.py"],
                    },
                }
            ]
        }
        p = tmp_path / "overrides.json"
        p.write_text(json.dumps(rules), encoding="utf-8")
        return p

    def test_demo_override_emits_disposition_source(
        self, override_file: Path, tmp_path: Path
    ) -> None:
        """run_demo() with a matching override must show 'override' in output.

        The output is human-readable formatted text.  When an override rule
        fires, the formatted block for that prompt must contain the string
        ``disposition_source : override`` and ``override_id``.
        """
        import io

        from claude_wayfinder.cli import run_demo

        env_backup = os.environ.get("DISPATCH_OVERRIDES_PATH")
        try:
            os.environ["DISPATCH_OVERRIDES_PATH"] = str(override_file)
            buf = io.StringIO()
            exit_code = run_demo(out=buf)
        finally:
            if env_backup is None:
                os.environ.pop("DISPATCH_OVERRIDES_PATH", None)
            else:
                os.environ["DISPATCH_OVERRIDES_PATH"] = env_backup

        assert exit_code == 0, (
            f"run_demo() exited {exit_code} with override set"
        )
        output = buf.getvalue()
        assert "disposition_source : override" in output, (
            f"Expected 'disposition_source : override' in demo output.\n"
            f"output:\n{output}"
        )
        assert "override_id" in output, (
            f"Expected 'override_id' in demo output.\n"
            f"output:\n{output}"
        )
        assert "demo-override-fires" in output, (
            f"Expected rule id 'demo-override-fires' in demo output.\n"
            f"output:\n{output}"
        )


# ---------------------------------------------------------------------------
# Overrides-mtime staleness warning
# ---------------------------------------------------------------------------


class TestOverridesStalenessWarning:
    """When the overrides file is older than the catalog, a DISPATCH WARNING
    must appear on stderr.  Execution must still proceed normally."""

    def test_dispatch_warns_when_overrides_older_than_catalog(
        self, tmp_path: Path
    ) -> None:
        """Warning fires when overrides mtime is strictly older than catalog.

        The overrides file is written first so its mtime is earlier, then a
        brief sleep ensures the catalog's mtime is strictly newer.
        """
        overrides = tmp_path / "overrides.json"
        overrides.write_text(
            '{"version": 1, "rules": []}', encoding="utf-8"
        )
        time.sleep(0.05)
        catalog = tmp_path / "catalog.json"
        catalog.write_text(
            _DEMO_CATALOG_PATH.read_text(encoding="utf-8"),
            encoding="utf-8",
        )

        result = _run_dispatch(
            env_overrides={
                "DISPATCH_CATALOG_PATH": str(catalog),
                "DISPATCH_OVERRIDES_PATH": str(overrides),
            },
        )

        assert "[DISPATCH WARNING]" in result.stderr, (
            "Expected '[DISPATCH WARNING]' in stderr when overrides is older "
            "than catalog, but got:\n"
            f"stderr: {result.stderr!r}"
        )
        assert "stale" in result.stderr.lower() or "older" in result.stderr.lower(), (
            "Expected staleness message in stderr, but got:\n"
            f"stderr: {result.stderr!r}"
        )

    def test_dispatch_silent_when_overrides_newer_than_catalog(
        self, tmp_path: Path
    ) -> None:
        """No warning when overrides mtime is newer than (or equal to) catalog.

        The catalog is written first, then after a sleep the overrides file
        is written so its mtime is strictly newer.
        """
        catalog = tmp_path / "catalog.json"
        catalog.write_text(
            _DEMO_CATALOG_PATH.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        time.sleep(0.05)
        overrides = tmp_path / "overrides.json"
        overrides.write_text(
            '{"version": 1, "rules": []}', encoding="utf-8"
        )

        result = _run_dispatch(
            env_overrides={
                "DISPATCH_CATALOG_PATH": str(catalog),
                "DISPATCH_OVERRIDES_PATH": str(overrides),
            },
        )

        assert "[DISPATCH WARNING]" not in result.stderr, (
            "Unexpected '[DISPATCH WARNING]' in stderr when overrides is "
            "newer than catalog.\n"
            f"stderr: {result.stderr!r}"
        )


# ---------------------------------------------------------------------------
# --demo flag contract
# ---------------------------------------------------------------------------


class TestDemoFlagContract:
    """``--demo`` must win regardless of what env vars are set."""

    def test_demo_flag_overrides_catalog_env_var(self) -> None:
        """``--demo`` activates demo mode even when DISPATCH_CATALOG_PATH is set.

        With both ``--demo`` and a valid ``$DISPATCH_CATALOG_PATH``, demo mode
        must win: banner present, no decision JSON on stdout.
        """
        result = _run_dispatch(
            env_overrides={
                "DISPATCH_CATALOG_PATH": str(_DEMO_CATALOG_PATH),
            },
            extra_args=["--demo"],
        )
        assert result.returncode == 0, (
            f"dispatch exited {result.returncode} with --demo + valid catalog.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert _DEMO_MODE_BANNER in result.stdout, (
            "Expected demo banner when --demo is set alongside valid catalog.\n"
            f"stdout: {result.stdout}"
        )
        assert _CATALOG_ERROR_PREFIX not in result.stderr, (
            "Unexpected [CATALOG ERROR] when --demo is set.\n"
            f"stderr: {result.stderr}"
        )

    def test_demo_flag_in_dispatch_help(self) -> None:
        """``python -m claude_wayfinder dispatch --help`` must mention --demo."""
        import subprocess as _sp

        result = _sp.run(
            [sys.executable, "-m", "claude_wayfinder", "dispatch", "--help"],
            capture_output=True,
            text=True,
        )
        assert "--demo" in result.stdout, (
            "--demo flag not found in dispatch --help output.\n"
            f"stdout: {result.stdout}"
        )


# ---------------------------------------------------------------------------
# Canonical-default fallback (no --demo, no DISPATCH_CATALOG_PATH)
# ---------------------------------------------------------------------------


class TestCanonicalDefaultFallback:
    """When neither ``--demo`` nor ``$DISPATCH_CATALOG_PATH`` is set, dispatch
    must resolve the canonical default path and use it if it exists."""

    def test_canonical_path_exists_activates_real_catalog_mode(
        self, tmp_path: Path
    ) -> None:
        """When canonical default catalog exists, real-catalog mode activates.

        Sets ``$CLAUDE_HOME`` to a tmpdir and places a copy of the demo
        catalog at ``state/dispatch-catalog.json`` within it.  With no
        ``--demo`` and no ``$DISPATCH_CATALOG_PATH``, dispatch must find the
        canonical catalog and return decision JSON (not a demo banner).
        """
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        canonical = state_dir / "dispatch-catalog.json"
        canonical.write_text(
            _DEMO_CATALOG_PATH.read_text(encoding="utf-8"),
            encoding="utf-8",
        )

        result = _run_dispatch(
            env_overrides={"CLAUDE_HOME": str(tmp_path)},
        )
        assert result.returncode == 0, (
            "dispatch failed when canonical catalog exists at $CLAUDE_HOME.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert _DEMO_MODE_BANNER not in result.stdout, (
            "Demo banner appeared even though canonical catalog was present.\n"
            f"stdout: {result.stdout}"
        )
        assert _CATALOG_ERROR_PREFIX not in result.stderr, (
            "Unexpected [CATALOG ERROR] when canonical catalog is present.\n"
            f"stderr: {result.stderr}"
        )
        try:
            decision = result.stdout.strip()
            assert decision, "stdout was empty — expected decision JSON."
            parsed = json.loads(decision)
        except json.JSONDecodeError as exc:
            pytest.fail(
                f"stdout is not valid JSON after canonical-fallback: {exc}\n"
                f"stdout: {result.stdout!r}"
            )
        assert "decision" in parsed, (
            f"'decision' key missing from canonical-fallback output.\n"
            f"output: {parsed}"
        )

    def test_canonical_path_absent_emits_catalog_error(
        self, tmp_path: Path
    ) -> None:
        """When canonical catalog is absent, [CATALOG ERROR] is emitted.

        Sets ``$CLAUDE_HOME`` to an empty tmpdir so the canonical path does
        not exist.  With no ``--demo`` and no ``$DISPATCH_CATALOG_PATH``,
        dispatch must emit ``[CATALOG ERROR]`` and exit non-zero.
        """
        result = _run_dispatch(
            env_overrides={"CLAUDE_HOME": str(tmp_path)},
        )
        assert result.returncode != 0, (
            "dispatch should exit non-zero when canonical catalog is absent.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert _CATALOG_ERROR_PREFIX in result.stderr, (
            "Expected [CATALOG ERROR] when canonical catalog is absent.\n"
            f"stderr: {result.stderr}"
        )
        assert _DEMO_MODE_BANNER not in result.stdout, (
            "Demo banner appeared silently when canonical catalog is absent — "
            "violates the new contract (only --demo opts into demo mode).\n"
            f"stdout: {result.stdout}"
        )
