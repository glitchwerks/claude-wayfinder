"""Tests for the ``python -m claude_wayfinder catalog build`` subcommand.

Covers three behaviors:
  (a) ``catalog build --help`` exits 0 and lists all expected flags.
  (b) End-to-end smoke — ``catalog build`` on a fixture skills-dir and
      agents-dir produces a valid ``dispatch-catalog.json``.
  (c) Default arg resolution — ``catalog build`` with no args resolves the
      four required paths from ``CLAUDE_HOME`` (or ``~/.claude`` when unset).

The tests exercise the real entry point via subprocess so that the full
argparse / delegation chain is exercised, not mocked internals.  The default-
resolution tests (c) call the internal helper directly to avoid needing a real
filesystem tree at ``~/.claude``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_TESTS_DIR = Path(__file__).resolve().parent
_FIXTURES_DIR = _TESTS_DIR / "fixtures"
_SKILLS_DIR = _FIXTURES_DIR / "skills"
_AGENTS_DIR = _FIXTURES_DIR / "agents"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    """Run ``python -m claude_wayfinder`` with *args and capture output.

    Args:
        *args: Additional arguments appended after ``-m claude_wayfinder``.

    Returns:
        A ``CompletedProcess`` with stdout/stderr captured as strings.
    """
    return subprocess.run(
        [sys.executable, "-m", "claude_wayfinder", *args],
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# (a) --help surface
# ---------------------------------------------------------------------------

# All flags that must appear in ``catalog build --help``.
_EXPECTED_FLAGS = [
    "--skills-dir",
    "--agents-dir",
    "--out",
    "--log",
    "--plugin-overrides-dir",
    "--plugins-dir",
    "--builtin-agents-dir",
    "--corpus",
    "--project-root",
]


class TestCatalogBuildHelp:
    """``catalog build --help`` must exit 0 and surface all expected flags."""

    @pytest.fixture(scope="class")
    def help_output(self) -> str:
        """Run ``catalog build --help`` once and return stdout for the class.

        Returns:
            The captured stdout of the help invocation.
        """
        result = _run("catalog", "build", "--help")
        assert result.returncode == 0, (
            f"catalog build --help exited {result.returncode}.\n"
            f"stderr: {result.stderr}"
        )
        return result.stdout

    def test_help_exits_zero(self) -> None:
        """``catalog build --help`` must exit with code 0."""
        result = _run("catalog", "build", "--help")
        assert result.returncode == 0, (
            f"catalog build --help exited {result.returncode}.\n"
            f"stderr: {result.stderr}"
        )

    @pytest.mark.parametrize("flag", _EXPECTED_FLAGS)
    def test_flag_in_help(self, flag: str, help_output: str) -> None:
        """Every expected flag must appear in the help text.

        Args:
            flag: The flag name to look for (e.g. ``--skills-dir``).
            help_output: Captured stdout from the help invocation.
        """
        assert flag in help_output, (
            f"Expected flag '{flag}' not found in catalog build --help output.\n"
            f"Full output:\n{help_output}"
        )


# ---------------------------------------------------------------------------
# (b) End-to-end smoke test
# ---------------------------------------------------------------------------


class TestCatalogBuildSmoke:
    """``catalog build`` on fixture dirs must produce a valid catalog."""

    def test_catalog_build_produces_json(self, tmp_path: Path) -> None:
        """Running catalog build against fixtures creates dispatch-catalog.json.

        Args:
            tmp_path: Pytest-provided temporary directory for output files.
        """
        out_path = tmp_path / "dispatch-catalog.json"
        log_path = tmp_path / "catalog-build.log"

        result = _run(
            "catalog",
            "build",
            "--skills-dir",
            str(_SKILLS_DIR),
            "--agents-dir",
            str(_AGENTS_DIR),
            "--out",
            str(out_path),
            "--log",
            str(log_path),
        )

        # Exit 0 (clean build) or 2 (degraded but completed) are both
        # acceptable here; either means build ran to completion.
        assert result.returncode in (0, 2), (
            f"catalog build exited {result.returncode} (expected 0 or 2).\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )
        assert out_path.exists(), (
            f"dispatch-catalog.json was not created at {out_path}.\n"
            f"stderr: {result.stderr}"
        )

    def test_catalog_output_is_valid_json(self, tmp_path: Path) -> None:
        """The produced dispatch-catalog.json must be valid JSON.

        Args:
            tmp_path: Pytest-provided temporary directory for output files.
        """
        out_path = tmp_path / "dispatch-catalog.json"
        log_path = tmp_path / "catalog-build.log"

        _run(
            "catalog",
            "build",
            "--skills-dir",
            str(_SKILLS_DIR),
            "--agents-dir",
            str(_AGENTS_DIR),
            "--out",
            str(out_path),
            "--log",
            str(log_path),
        )

        assert out_path.exists(), "Output file was not created."
        catalog = json.loads(out_path.read_text(encoding="utf-8"))
        assert isinstance(catalog, dict), "Catalog top level must be a JSON object."

    def test_catalog_has_entries_key(self, tmp_path: Path) -> None:
        """The produced catalog must contain a top-level ``entries`` key.

        Args:
            tmp_path: Pytest-provided temporary directory for output files.
        """
        out_path = tmp_path / "dispatch-catalog.json"
        log_path = tmp_path / "catalog-build.log"

        _run(
            "catalog",
            "build",
            "--skills-dir",
            str(_SKILLS_DIR),
            "--agents-dir",
            str(_AGENTS_DIR),
            "--out",
            str(out_path),
            "--log",
            str(log_path),
        )

        catalog = json.loads(out_path.read_text(encoding="utf-8"))
        assert "entries" in catalog, (
            f"Catalog missing 'entries' key. Keys found: {list(catalog.keys())}"
        )

    def test_demo_subcommand_still_works_after_catalog_added(self) -> None:
        """The existing ``demo`` subcommand must be unaffected by the new subparser."""
        result = _run("demo")
        assert result.returncode == 0, (
            f"demo exited {result.returncode} after catalog subparser was added.\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )
        assert result.stdout.strip(), "demo produced no stdout output."


# ---------------------------------------------------------------------------
# (c) Default arg resolution — issue #87
# ---------------------------------------------------------------------------


class TestCatalogBuildDefaults:
    """``catalog build`` with no args must resolve four paths from CLAUDE_HOME.

    These tests call the internal ``_resolve_catalog_build_defaults`` helper
    in ``build_catalog`` directly, rather than using subprocess, so they can
    mock ``Path.home()`` and ``os.environ`` without needing a real
    ``~/.claude`` tree on disk.

    Three sub-scenarios:
      1. No args given, ``CLAUDE_HOME`` unset → default to ``Path.home() / ".claude"``.
      2. No args given, ``CLAUDE_HOME`` set → default to ``Path($CLAUDE_HOME)``.
      3. Explicit ``--skills-dir foo`` overrides only that arg; the rest default.
    """

    def test_no_args_defaults_to_home_dot_claude(self, tmp_path: Path) -> None:
        """With no args and ``CLAUDE_HOME`` unset, all four paths default to
        ``~/.claude/<subpath>``.

        Args:
            tmp_path: Pytest-provided temporary directory (unused but required
                for consistency with sibling tests that write output files).
        """
        from claude_wayfinder.build_catalog import _resolve_catalog_build_defaults

        fake_home = tmp_path / "fake_home"
        with patch("claude_wayfinder.build_catalog._discover.Path.home", return_value=fake_home):
            env_without_claude_home = {
                k: v for k, v in os.environ.items() if k != "CLAUDE_HOME"
            }
            with patch.dict(os.environ, env_without_claude_home, clear=True):
                defaults = _resolve_catalog_build_defaults(
                    skills_dir=None,
                    agents_dir=None,
                    out=None,
                    log=None,
                )

        expected_base = fake_home / ".claude"
        assert defaults["skills_dir"] == expected_base / "skills", (
            f"Expected skills_dir={expected_base / 'skills'}, got {defaults['skills_dir']}"
        )
        assert defaults["agents_dir"] == expected_base / "agents", (
            f"Expected agents_dir={expected_base / 'agents'}, got {defaults['agents_dir']}"
        )
        assert defaults["out"] == expected_base / "state" / "dispatch-catalog.json", (
            f"Expected out={expected_base / 'state' / 'dispatch-catalog.json'}, "
            f"got {defaults['out']}"
        )
        assert defaults["log"] == expected_base / "state" / "catalog-generation.log", (
            f"Expected log={expected_base / 'state' / 'catalog-generation.log'}, "
            f"got {defaults['log']}"
        )

    def test_no_args_with_claude_home_env_set(self, tmp_path: Path) -> None:
        """With ``CLAUDE_HOME`` set, all four paths default to ``$CLAUDE_HOME/<subpath>``.

        Args:
            tmp_path: Pytest-provided temporary directory used as a fake
                ``CLAUDE_HOME``.
        """
        from claude_wayfinder.build_catalog import _resolve_catalog_build_defaults

        fake_claude_home = tmp_path / "custom_claude"
        with patch.dict(os.environ, {"CLAUDE_HOME": str(fake_claude_home)}):
            defaults = _resolve_catalog_build_defaults(
                skills_dir=None,
                agents_dir=None,
                out=None,
                log=None,
            )

        assert defaults["skills_dir"] == fake_claude_home / "skills", (
            f"Expected skills_dir={fake_claude_home / 'skills'}, "
            f"got {defaults['skills_dir']}"
        )
        assert defaults["agents_dir"] == fake_claude_home / "agents", (
            f"Expected agents_dir={fake_claude_home / 'agents'}, "
            f"got {defaults['agents_dir']}"
        )
        assert defaults["out"] == fake_claude_home / "state" / "dispatch-catalog.json", (
            f"Expected out={fake_claude_home / 'state' / 'dispatch-catalog.json'}, "
            f"got {defaults['out']}"
        )
        assert defaults["log"] == fake_claude_home / "state" / "catalog-generation.log", (
            f"Expected log={fake_claude_home / 'state' / 'catalog-generation.log'}, "
            f"got {defaults['log']}"
        )

    def test_explicit_skills_dir_overrides_default(self, tmp_path: Path) -> None:
        """An explicit ``--skills-dir`` value is preserved; other three args default.

        Args:
            tmp_path: Pytest-provided temporary directory; its ``skills``
                subdirectory is used as the explicit skills_dir.
        """
        from claude_wayfinder.build_catalog import _resolve_catalog_build_defaults

        explicit_skills = tmp_path / "my_skills"
        fake_claude_home = tmp_path / "custom_claude"
        with patch.dict(os.environ, {"CLAUDE_HOME": str(fake_claude_home)}):
            defaults = _resolve_catalog_build_defaults(
                skills_dir=explicit_skills,
                agents_dir=None,
                out=None,
                log=None,
            )

        assert defaults["skills_dir"] == explicit_skills, (
            f"Explicit skills_dir must not be overridden. Got {defaults['skills_dir']}"
        )
        # The other three should still default.
        assert defaults["agents_dir"] == fake_claude_home / "agents"
        assert defaults["out"] == fake_claude_home / "state" / "dispatch-catalog.json"
        assert defaults["log"] == fake_claude_home / "state" / "catalog-generation.log"

    def test_bare_invocation_does_not_emit_argparse_required_error(
        self, tmp_path: Path
    ) -> None:
        """``catalog build`` with no flags must not emit an argparse "required" error.

        Exit code 2 from argparse signals missing required arguments and
        produces ``error: the following arguments are required`` in stderr.
        After issue #87 is fixed, the four formerly-required args must be
        optional so bare invocation gets past argparse without that message.

        Note: the build itself may still exit non-zero or emit other warnings
        (e.g. "no router agent declared") when the fake ``CLAUDE_HOME`` tree
        is nearly empty — that is acceptable.  The *only* failure mode we
        guard against here is argparse rejecting the invocation as if the
        args were still required.

        Args:
            tmp_path: Pytest-provided temp directory used as a fake
                ``CLAUDE_HOME``.
        """
        # Run with a fake CLAUDE_HOME that exists but has empty source dirs —
        # this avoids touching the real ~/.claude while letting the CLI get
        # past argparse into the actual build stage.
        fake_home = tmp_path / "fake_claude"
        fake_home.mkdir()
        (fake_home / "skills").mkdir()
        (fake_home / "agents").mkdir()
        (fake_home / "state").mkdir()

        env = {**os.environ, "CLAUDE_HOME": str(fake_home)}
        result = subprocess.run(
            [sys.executable, "-m", "claude_wayfinder", "catalog", "build"],
            capture_output=True,
            text=True,
            env=env,
        )

        argparse_required_msg = "the following arguments are required"
        assert argparse_required_msg not in result.stderr, (
            "argparse still treats the args as required after issue #87 fix.\n"
            f"stderr: {result.stderr}"
        )


# ---------------------------------------------------------------------------
# (d) Default arg resolution for plugin-discovery flags — issue #124
# ---------------------------------------------------------------------------


class TestCatalogBuildPluginDiscoveryDefaults:
    """``catalog build`` must resolve three plugin-discovery paths from CLAUDE_HOME.

    Mirrors ``TestCatalogBuildDefaults`` (issue #87) but covers the three
    flags added in issue #124:
      - ``--plugin-overrides-dir`` → ``${CLAUDE_HOME}/triggers``
      - ``--plugins-dir``          → ``${CLAUDE_HOME}/plugins``
      - ``--builtin-agents-dir``   → three-level cascade (Issue #286):
          1. Explicit arg wins.
          2. ``${CLAUDE_HOME}/triggers/builtin`` when it exists on disk.
          3. Bundled in-package fixtures fallback.

    All three previously defaulted to ``None``, silently disabling Pass 2.5
    (plugin discovery), Pass 2.6 (builtin agents), and trigger-override
    resolution when the hook invoked bare ``catalog build``.
    """

    def test_plugin_dirs_default_when_claude_home_unset(
        self, tmp_path: Path
    ) -> None:
        """With CLAUDE_HOME unset and no user builtin dir, bundled path is returned.

        The user builtin dir (fake_home/.claude/triggers/builtin) does not
        exist on disk, so the resolver falls back to the in-package fixtures.
        Only plugin_overrides_dir and plugins_dir default under ~/.claude.

        Args:
            tmp_path: Pytest-provided temporary directory used as a fake
                ``HOME`` so the test is hermetic.
        """
        import claude_wayfinder.fixtures as _fixtures_pkg
        from claude_wayfinder.build_catalog import _resolve_catalog_build_defaults

        fake_home = tmp_path / "fake_home"
        with patch("claude_wayfinder.build_catalog._discover.Path.home", return_value=fake_home):
            env_without_claude_home = {
                k: v for k, v in os.environ.items() if k != "CLAUDE_HOME"
            }
            with patch.dict(os.environ, env_without_claude_home, clear=True):
                defaults = _resolve_catalog_build_defaults(
                    skills_dir=None,
                    agents_dir=None,
                    out=None,
                    log=None,
                    plugin_overrides_dir=None,
                    plugins_dir=None,
                    builtin_agents_dir=None,
                )

        expected_base = fake_home / ".claude"
        assert defaults["plugin_overrides_dir"] == expected_base / "triggers", (
            f"Expected plugin_overrides_dir={expected_base / 'triggers'}, "
            f"got {defaults.get('plugin_overrides_dir')}"
        )
        assert defaults["plugins_dir"] == expected_base / "plugins", (
            f"Expected plugins_dir={expected_base / 'plugins'}, "
            f"got {defaults.get('plugins_dir')}"
        )
        # builtin_agents_dir: user dir does not exist → bundled fallback
        bundled_dir = Path(_fixtures_pkg.__file__).parent / "builtin"
        assert defaults["builtin_agents_dir"] == bundled_dir, (
            f"Expected bundled fallback {bundled_dir}, "
            f"got {defaults.get('builtin_agents_dir')}. "
            "Issue #286: resolver must fall back to bundled fixtures when "
            "user directory is absent."
        )

    def test_plugin_dirs_default_when_claude_home_set(self, tmp_path: Path) -> None:
        """With CLAUDE_HOME set but no user builtin dir, bundled path is returned.

        The CLAUDE_HOME-derived builtin dir does not exist on disk, so the
        resolver falls back to the in-package fixtures.

        Args:
            tmp_path: Pytest-provided temporary directory used as a fake
                ``CLAUDE_HOME``.
        """
        import claude_wayfinder.fixtures as _fixtures_pkg
        from claude_wayfinder.build_catalog import _resolve_catalog_build_defaults

        fake_claude_home = tmp_path / "custom_claude"
        with patch.dict(os.environ, {"CLAUDE_HOME": str(fake_claude_home)}):
            defaults = _resolve_catalog_build_defaults(
                skills_dir=None,
                agents_dir=None,
                out=None,
                log=None,
                plugin_overrides_dir=None,
                plugins_dir=None,
                builtin_agents_dir=None,
            )

        assert defaults["plugin_overrides_dir"] == fake_claude_home / "triggers", (
            f"Expected plugin_overrides_dir={fake_claude_home / 'triggers'}, "
            f"got {defaults.get('plugin_overrides_dir')}"
        )
        assert defaults["plugins_dir"] == fake_claude_home / "plugins", (
            f"Expected plugins_dir={fake_claude_home / 'plugins'}, "
            f"got {defaults.get('plugins_dir')}"
        )
        # builtin_agents_dir: user dir (fake_claude_home/triggers/builtin)
        # does not exist on disk → bundled fallback
        bundled_dir = Path(_fixtures_pkg.__file__).parent / "builtin"
        assert defaults["builtin_agents_dir"] == bundled_dir, (
            f"Expected bundled fallback {bundled_dir}, "
            f"got {defaults.get('builtin_agents_dir')}. "
            "Issue #286: resolver must fall back to bundled fixtures when "
            "user directory is absent."
        )

    def test_plugin_dirs_default_when_user_builtin_dir_exists(
        self, tmp_path: Path
    ) -> None:
        """When CLAUDE_HOME builtin dir exists on disk, it is preferred over bundled.

        Creates the user-side builtin directory, confirms the resolver
        returns it rather than the bundled fallback.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        from claude_wayfinder.build_catalog import _resolve_catalog_build_defaults

        fake_claude_home = tmp_path / "custom_claude"
        user_builtin = fake_claude_home / "triggers" / "builtin"
        user_builtin.mkdir(parents=True)

        with patch.dict(os.environ, {"CLAUDE_HOME": str(fake_claude_home)}):
            defaults = _resolve_catalog_build_defaults(
                skills_dir=None,
                agents_dir=None,
                out=None,
                log=None,
                plugin_overrides_dir=None,
                plugins_dir=None,
                builtin_agents_dir=None,
            )

        assert defaults["builtin_agents_dir"] == user_builtin, (
            f"Expected user dir {user_builtin}, "
            f"got {defaults.get('builtin_agents_dir')}. "
            "User-side builtin dir must take precedence over bundled fallback."
        )

    def test_explicit_plugin_overrides_dir_wins_over_default(
        self, tmp_path: Path
    ) -> None:
        """Explicit ``--plugin-overrides-dir`` is preserved; others still default.

        Args:
            tmp_path: Pytest-provided temporary directory; its
                ``my_triggers`` subdirectory is used as the explicit value.
        """
        import claude_wayfinder.fixtures as _fixtures_pkg
        from claude_wayfinder.build_catalog import _resolve_catalog_build_defaults

        explicit_overrides = tmp_path / "my_triggers"
        fake_claude_home = tmp_path / "custom_claude"
        with patch.dict(os.environ, {"CLAUDE_HOME": str(fake_claude_home)}):
            defaults = _resolve_catalog_build_defaults(
                skills_dir=None,
                agents_dir=None,
                out=None,
                log=None,
                plugin_overrides_dir=explicit_overrides,
                plugins_dir=None,
                builtin_agents_dir=None,
            )

        assert defaults["plugin_overrides_dir"] == explicit_overrides, (
            "Explicit plugin_overrides_dir must not be overridden. "
            f"Got {defaults.get('plugin_overrides_dir')}"
        )
        # The other two plugin-discovery dirs should still default.
        assert defaults["plugins_dir"] == fake_claude_home / "plugins"
        # builtin_agents_dir: user dir absent → bundled fallback
        bundled_dir = Path(_fixtures_pkg.__file__).parent / "builtin"
        assert defaults["builtin_agents_dir"] == bundled_dir, (
            f"Expected bundled fallback {bundled_dir}, "
            f"got {defaults.get('builtin_agents_dir')}"
        )

    def test_explicit_plugins_dir_wins_over_default(self, tmp_path: Path) -> None:
        """Explicit ``--plugins-dir`` is preserved; others still default.

        Args:
            tmp_path: Pytest-provided temporary directory; its
                ``my_plugins`` subdirectory is used as the explicit value.
        """
        import claude_wayfinder.fixtures as _fixtures_pkg
        from claude_wayfinder.build_catalog import _resolve_catalog_build_defaults

        explicit_plugins = tmp_path / "my_plugins"
        fake_claude_home = tmp_path / "custom_claude"
        with patch.dict(os.environ, {"CLAUDE_HOME": str(fake_claude_home)}):
            defaults = _resolve_catalog_build_defaults(
                skills_dir=None,
                agents_dir=None,
                out=None,
                log=None,
                plugin_overrides_dir=None,
                plugins_dir=explicit_plugins,
                builtin_agents_dir=None,
            )

        assert defaults["plugins_dir"] == explicit_plugins, (
            "Explicit plugins_dir must not be overridden. "
            f"Got {defaults.get('plugins_dir')}"
        )
        assert defaults["plugin_overrides_dir"] == fake_claude_home / "triggers"
        # builtin_agents_dir: user dir absent → bundled fallback
        bundled_dir = Path(_fixtures_pkg.__file__).parent / "builtin"
        assert defaults["builtin_agents_dir"] == bundled_dir, (
            f"Expected bundled fallback {bundled_dir}, "
            f"got {defaults.get('builtin_agents_dir')}"
        )

    def test_explicit_builtin_agents_dir_wins_over_default(
        self, tmp_path: Path
    ) -> None:
        """Explicit ``--builtin-agents-dir`` is preserved; others still default.

        Args:
            tmp_path: Pytest-provided temporary directory; its
                ``my_builtin`` subdirectory is used as the explicit value.
        """
        from claude_wayfinder.build_catalog import _resolve_catalog_build_defaults

        explicit_builtin = tmp_path / "my_builtin"
        fake_claude_home = tmp_path / "custom_claude"
        with patch.dict(os.environ, {"CLAUDE_HOME": str(fake_claude_home)}):
            defaults = _resolve_catalog_build_defaults(
                skills_dir=None,
                agents_dir=None,
                out=None,
                log=None,
                plugin_overrides_dir=None,
                plugins_dir=None,
                builtin_agents_dir=explicit_builtin,
            )

        assert defaults["builtin_agents_dir"] == explicit_builtin, (
            "Explicit builtin_agents_dir must not be overridden. "
            f"Got {defaults.get('builtin_agents_dir')}"
        )
        assert defaults["plugin_overrides_dir"] == fake_claude_home / "triggers"
        assert defaults["plugins_dir"] == fake_claude_home / "plugins"
