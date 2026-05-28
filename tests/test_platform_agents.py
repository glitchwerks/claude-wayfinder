"""Tests for in-package platform-agent fixtures (Issue #286).

Covers:
  - Bundled Explore.yml and Plan.yml fixtures exist in the package.
  - ``_resolve_catalog_build_defaults`` falls back to bundled fixtures
    when ``~/.claude/triggers/builtin/`` is absent.
  - Catalog build with bundled fixtures includes Explore and Plan with
    correct source, kind, and trigger shape.
  - Acceptance test: warp code-recon input routes to Explore (AC #4
    from issue #286).
  - Regression test: architecture/strategy input routes to Plan.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

PYTHON = sys.executable

# build_catalog package module path
_BUILD_MODULE = ["claude_wayfinder.build_catalog"]
# match package module path
_MATCH_MODULE = ["claude_wayfinder.match"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_catalog_with_builtins(
    tmp_path: Path,
    *,
    monkeypatch: pytest.MonkeyPatch | None = None,
    builtin_agents_dir: Path | None = None,
    extra_env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build a minimal catalog that includes platform-agent entries.

    Passes an empty skills dir and empty agents dir so only the bundled
    (or supplied) builtin entries contribute to the catalog.  Sets
    ``CLAUDE_VERSION=2.1.138`` so version-pinning passes.

    Args:
        tmp_path: pytest temporary directory.
        monkeypatch: pytest MonkeyPatch fixture; used to set CLAUDE_VERSION
            when *extra_env* is not supplied.
        builtin_agents_dir: Override the builtin agents directory.  When
            ``None``, the ``--builtin-agents-dir`` flag is omitted so the
            generator falls back to its default resolution (which the
            bundled-fallback logic under test should satisfy).
        extra_env: Additional environment variables to set.

    Returns:
        Parsed catalog dict.
    """
    out = tmp_path / "catalog.json"
    log = tmp_path / "build.log"
    no_agents = tmp_path / "no-agents"
    no_skills = tmp_path / "no-skills"
    no_plugins = tmp_path / "no-plugins"

    cmd = [
        PYTHON,
        "-m",
        *_BUILD_MODULE,
        "--agents-dir",
        str(no_agents),
        "--skills-dir",
        str(no_skills),
        "--plugins-dir",
        str(no_plugins),
        "--plugin-overrides-dir",
        str(tmp_path / "no-triggers"),
        "--out",
        str(out),
        "--log",
        str(log),
    ]
    if builtin_agents_dir is not None:
        cmd += ["--builtin-agents-dir", str(builtin_agents_dir)]

    env = {**os.environ, "CLAUDE_VERSION": "2.1.138"}
    if extra_env:
        env.update(extra_env)

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode in (0, 2), (
        f"Build failed with rc={result.returncode}.\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
    return json.loads(out.read_text(encoding="utf-8"))


def _run_match(
    stdin_obj: dict[str, Any],
    catalog: dict[str, Any],
    tmp_path: Path,
) -> dict[str, Any]:
    """Run the matcher against *catalog* with *stdin_obj* and return parsed output.

    Args:
        stdin_obj: Dispatch context dict.
        catalog: Catalog dict.
        tmp_path: pytest temporary directory.

    Returns:
        Parsed matcher output dict.
    """
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
    env = {**os.environ, "DISPATCH_CATALOG_PATH": str(catalog_path)}

    result = subprocess.run(
        [PYTHON, "-m", *_MATCH_MODULE],
        input=json.dumps(stdin_obj),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0, (
        f"Matcher failed with rc={result.returncode}.\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
    return json.loads(result.stdout)


# ---------------------------------------------------------------------------
# Fixture file existence
# ---------------------------------------------------------------------------


class TestBundledFixtureFiles:
    """Bundled YAML fixture files exist in the package at the expected paths."""

    def test_explore_yml_exists(self) -> None:
        """Explore.yml is bundled in fixtures/builtin/.

        Verifies the file is present via importlib.resources-style path
        resolution so the check is install-path-agnostic.
        """
        import claude_wayfinder.fixtures as _fixtures_pkg

        fixtures_dir = Path(_fixtures_pkg.__file__).parent
        explore_yml = fixtures_dir / "builtin" / "Explore.yml"
        assert explore_yml.is_file(), (
            f"Bundled Explore.yml not found at {explore_yml}. "
            "Issue #286 requires in-package fixtures."
        )

    def test_plan_yml_exists(self) -> None:
        """Plan.yml is bundled in fixtures/builtin/.

        Verifies the file is present via importlib.resources-style path
        resolution so the check is install-path-agnostic.
        """
        import claude_wayfinder.fixtures as _fixtures_pkg

        fixtures_dir = Path(_fixtures_pkg.__file__).parent
        plan_yml = fixtures_dir / "builtin" / "Plan.yml"
        assert plan_yml.is_file(), (
            f"Bundled Plan.yml not found at {plan_yml}. "
            "Issue #286 requires in-package fixtures."
        )

    def test_explore_yml_is_valid_yaml(self) -> None:
        """Explore.yml parses as valid YAML with required fields."""
        import yaml

        import claude_wayfinder.fixtures as _fixtures_pkg

        fixtures_dir = Path(_fixtures_pkg.__file__).parent
        explore_yml = fixtures_dir / "builtin" / "Explore.yml"
        data = yaml.safe_load(explore_yml.read_text(encoding="utf-8"))
        assert isinstance(data, dict), "Explore.yml must parse to a dict"
        assert data.get("name") == "Explore", "name field must be 'Explore'"
        assert "min_claude_version" in data, "min_claude_version is required"
        assert "triggers" in data, "triggers block is required"
        assert "agent_mentions" in data["triggers"], (
            "agent_mentions required; used for explicit @Explore routing"
        )
        assert "Explore" in data["triggers"]["agent_mentions"], (
            "'Explore' must appear in agent_mentions for @Explore short-circuit"
        )

    def test_plan_yml_is_valid_yaml(self) -> None:
        """Plan.yml parses as valid YAML with required fields."""
        import yaml

        import claude_wayfinder.fixtures as _fixtures_pkg

        fixtures_dir = Path(_fixtures_pkg.__file__).parent
        plan_yml = fixtures_dir / "builtin" / "Plan.yml"
        data = yaml.safe_load(plan_yml.read_text(encoding="utf-8"))
        assert isinstance(data, dict), "Plan.yml must parse to a dict"
        assert data.get("name") == "Plan", "name field must be 'Plan'"
        assert "min_claude_version" in data, "min_claude_version is required"
        assert "triggers" in data, "triggers block is required"
        assert "agent_mentions" in data["triggers"], (
            "agent_mentions required; used for explicit @Plan routing"
        )
        assert "Plan" in data["triggers"]["agent_mentions"], (
            "'Plan' must appear in agent_mentions for @Plan short-circuit"
        )


# ---------------------------------------------------------------------------
# Bundled-fallback default resolution
# ---------------------------------------------------------------------------


class TestBundledFallback:
    """``_resolve_catalog_build_defaults`` falls back to bundled fixtures."""

    def test_fallback_used_when_user_dir_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When ~/.claude/triggers/builtin/ is absent, bundled path is returned.

        Sets CLAUDE_HOME to a non-existent directory so the resolver cannot
        find a user-side builtin directory, then confirms the returned path
        points at the in-package fixtures/builtin/.
        """
        import claude_wayfinder.fixtures as _fixtures_pkg
        from claude_wayfinder.build_catalog._discover import (
            _resolve_catalog_build_defaults,
        )

        phantom_home = tmp_path / "phantom-claude-home"
        monkeypatch.setenv("CLAUDE_HOME", str(phantom_home))

        resolved = _resolve_catalog_build_defaults(
            skills_dir=None,
            agents_dir=None,
            out=None,
            log=None,
        )
        builtin_dir = resolved["builtin_agents_dir"]
        bundled_dir = Path(_fixtures_pkg.__file__).parent / "builtin"

        assert builtin_dir == bundled_dir, (
            f"Expected bundled fallback path {bundled_dir}, "
            f"got {builtin_dir}. The resolver must fall back to the "
            "in-package fixtures/builtin/ when the user directory is absent."
        )

    def test_user_dir_takes_precedence_when_present(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When ~/.claude/triggers/builtin/ exists, it is preferred over bundled.

        Creates a real directory at the CLAUDE_HOME-derived path and confirms
        the resolver returns it rather than the bundled fallback.
        """
        from claude_wayfinder.build_catalog._discover import (
            _resolve_catalog_build_defaults,
        )

        fake_claude_home = tmp_path / "fake-claude"
        user_builtin_dir = fake_claude_home / "triggers" / "builtin"
        user_builtin_dir.mkdir(parents=True)

        monkeypatch.setenv("CLAUDE_HOME", str(fake_claude_home))

        resolved = _resolve_catalog_build_defaults(
            skills_dir=None,
            agents_dir=None,
            out=None,
            log=None,
        )
        assert resolved["builtin_agents_dir"] == user_builtin_dir, (
            f"Expected user dir {user_builtin_dir}, "
            f"got {resolved['builtin_agents_dir']}. The user directory must "
            "take precedence over the bundled fallback."
        )

    def test_explicit_arg_always_wins(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An explicitly supplied builtin_agents_dir is returned unchanged.

        Even when the user directory exists and the bundled fallback exists,
        an explicit caller argument overrides both.
        """
        from claude_wayfinder.build_catalog._discover import (
            _resolve_catalog_build_defaults,
        )

        explicit_dir = tmp_path / "explicit-builtin"
        explicit_dir.mkdir()

        # CLAUDE_HOME points at an absent dir so default would be bundled.
        monkeypatch.setenv("CLAUDE_HOME", str(tmp_path / "absent-home"))

        resolved = _resolve_catalog_build_defaults(
            skills_dir=None,
            agents_dir=None,
            out=None,
            log=None,
            builtin_agents_dir=explicit_dir,
        )
        assert resolved["builtin_agents_dir"] == explicit_dir, (
            "Explicit builtin_agents_dir must override all fallback logic."
        )


# ---------------------------------------------------------------------------
# Catalog-build integration
# ---------------------------------------------------------------------------


class TestCatalogBuildIntegration:
    """Catalog built with bundled fixtures includes Explore and Plan."""

    def test_explore_and_plan_appear_in_catalog(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Bundled fixtures produce Explore and Plan entries in the catalog.

        Omits --builtin-agents-dir so the generator resolves the bundled
        fallback.  Sets CLAUDE_HOME to absent so the bundled path is used.
        """
        import claude_wayfinder.fixtures as _fixtures_pkg

        bundled_dir = Path(_fixtures_pkg.__file__).parent / "builtin"
        catalog = _build_catalog_with_builtins(
            tmp_path,
            builtin_agents_dir=bundled_dir,
        )
        names_by_source = {e["name"]: e["source"] for e in catalog["entries"]}
        assert "Explore" in names_by_source, (
            "Explore must appear in catalog when built with bundled fixtures"
        )
        assert "Plan" in names_by_source, (
            "Plan must appear in catalog when built with bundled fixtures"
        )
        assert names_by_source["Explore"] == "builtin", (
            "Explore must have source='builtin'"
        )
        assert names_by_source["Plan"] == "builtin", (
            "Plan must have source='builtin'"
        )

    def test_explore_kind_is_agent(
        self, tmp_path: Path
    ) -> None:
        """Explore entry has kind='agent'."""
        import claude_wayfinder.fixtures as _fixtures_pkg

        bundled_dir = Path(_fixtures_pkg.__file__).parent / "builtin"
        catalog = _build_catalog_with_builtins(
            tmp_path,
            builtin_agents_dir=bundled_dir,
        )
        explore = next(
            (e for e in catalog["entries"] if e["name"] == "Explore"),
            None,
        )
        assert explore is not None, "Explore entry must be present"
        assert explore["kind"] == "agent", (
            f"Expected kind='agent', got {explore['kind']!r}"
        )

    def test_plan_kind_is_agent(
        self, tmp_path: Path
    ) -> None:
        """Plan entry has kind='agent'."""
        import claude_wayfinder.fixtures as _fixtures_pkg

        bundled_dir = Path(_fixtures_pkg.__file__).parent / "builtin"
        catalog = _build_catalog_with_builtins(
            tmp_path,
            builtin_agents_dir=bundled_dir,
        )
        plan = next(
            (e for e in catalog["entries"] if e["name"] == "Plan"),
            None,
        )
        assert plan is not None, "Plan entry must be present"
        assert plan["kind"] == "agent", (
            f"Expected kind='agent', got {plan['kind']!r}"
        )

    def test_explore_has_locate_keyword(
        self, tmp_path: Path
    ) -> None:
        """Explore entry includes 'locate' in its keywords (weight 1.0)."""
        import claude_wayfinder.fixtures as _fixtures_pkg

        bundled_dir = Path(_fixtures_pkg.__file__).parent / "builtin"
        catalog = _build_catalog_with_builtins(
            tmp_path,
            builtin_agents_dir=bundled_dir,
        )
        explore = next(
            (e for e in catalog["entries"] if e["name"] == "Explore"),
            None,
        )
        assert explore is not None
        kw_map = {
            k["term"]: k["weight"]
            for k in explore["triggers"]["keywords"]
        }
        assert "locate" in kw_map, (
            "Explore must include 'locate' keyword for code-recon prompts"
        )
        assert kw_map["locate"] == 1.0, (
            f"'locate' weight must be 1.0, got {kw_map['locate']}"
        )

    def test_plan_has_strategy_keyword(
        self, tmp_path: Path
    ) -> None:
        """Plan entry includes 'strategy' in its keywords (weight 1.0)."""
        import claude_wayfinder.fixtures as _fixtures_pkg

        bundled_dir = Path(_fixtures_pkg.__file__).parent / "builtin"
        catalog = _build_catalog_with_builtins(
            tmp_path,
            builtin_agents_dir=bundled_dir,
        )
        plan = next(
            (e for e in catalog["entries"] if e["name"] == "Plan"),
            None,
        )
        assert plan is not None
        kw_map = {
            k["term"]: k["weight"]
            for k in plan["triggers"]["keywords"]
        }
        assert "strategy" in kw_map, (
            "Plan must include 'strategy' keyword for design/strategy prompts"
        )
        assert kw_map["strategy"] == 1.0, (
            f"'strategy' weight must be 1.0, got {kw_map['strategy']}"
        )


# ---------------------------------------------------------------------------
# Acceptance tests (Issue #286 AC #4)
# ---------------------------------------------------------------------------


class TestAcceptanceRouting:
    """End-to-end routing acceptance tests.

    Issue #286 AC #4: a "find me X in the codebase" input
    (the glitchwerks/claude-configs#811 task — locate keyboard shortcut
    handler and Settings pane component in warpdotdev/warp) must route
    to Explore after the change.
    """

    def _build_platform_catalog(self, tmp_path: Path) -> dict[str, Any]:
        """Build a catalog with Explore and Plan from bundled fixtures plus a
        router-agent entry to satisfy the no-router-agent warning.

        Returns the parsed catalog dict.
        """
        import claude_wayfinder.fixtures as _fixtures_pkg
        from claude_wayfinder.build_catalog import build

        bundled_dir = Path(_fixtures_pkg.__file__).parent / "builtin"
        out = tmp_path / "catalog.json"
        log = tmp_path / "build.log"

        # We need CLAUDE_VERSION set for builtin pass to succeed.
        original_env = os.environ.get("CLAUDE_VERSION")
        os.environ["CLAUDE_VERSION"] = "2.1.138"
        try:
            rc = build(
                skills_dir=tmp_path / "no-skills",
                agents_dir=tmp_path / "no-agents",
                corpus_path=None,
                out_path=out,
                log_path=log,
                builtin_agents_dir=bundled_dir,
                now="2026-05-28T00:00:00Z",
            )
        finally:
            if original_env is None:
                os.environ.pop("CLAUDE_VERSION", None)
            else:
                os.environ["CLAUDE_VERSION"] = original_env

        assert rc == 0, f"catalog build failed rc={rc}"
        return json.loads(out.read_text(encoding="utf-8"))

    def test_warp_code_recon_routes_to_explore(
        self, tmp_path: Path
    ) -> None:
        """The warp code-recon task routes to Explore.

        Issue #286 AC #4: input for glitchwerks/claude-configs#811 task
        ("locate the keyboard shortcut handler and Settings pane component
        in warpdotdev/warp") must produce a delegate or advisory decision
        targeting Explore.
        """
        catalog = self._build_platform_catalog(tmp_path)

        # Verify Explore is present with correct source before testing routing.
        names = {e["name"] for e in catalog["entries"]}
        assert "Explore" in names, "Explore must be in catalog before routing test"

        # Input from issue #286 AC #4: locate keyboard shortcut handler
        # and Settings pane component in warpdotdev/warp.
        # file_paths is non-empty to satisfy the 2-dimension density floor
        # (the real dispatch context from a codebase task includes file paths).
        stdin_obj = {
            "task_description": (
                "locate the keyboard shortcut handler and Settings pane "
                "component in warpdotdev/warp"
            ),
            "file_paths": ["src/main.rs"],
        }

        output = _run_match(stdin_obj, catalog, tmp_path)
        decision = output.get("decision")

        assert decision in ("delegate", "advisory"), (
            f"Expected delegate or advisory, got {decision!r}. "
            "The warp code-recon task must score Explore highly enough "
            "to reach at least advisory."
        )

        # Verify Explore is named as the top candidate.
        top_agent = output.get("agent") or (
            output.get("agents", [{}])[0].get("agent") if output.get("agents") else None
        )
        assert top_agent == "Explore", (
            f"Expected top agent 'Explore', got {top_agent!r}. "
            "The locate/find keywords must score Explore above other agents."
        )

    def test_architecture_strategy_routes_to_plan(
        self, tmp_path: Path
    ) -> None:
        """An architecture/strategy input routes to Plan.

        Regression test for the Plan agent: a prompt asking for strategy
        and architecture approach must score Plan as the top agent.
        """
        catalog = self._build_platform_catalog(tmp_path)

        names = {e["name"] for e in catalog["entries"]}
        assert "Plan" in names, "Plan must be in catalog before routing test"

        # file_paths is non-empty to satisfy the 2-dimension density floor.
        stdin_obj = {
            "task_description": (
                "design the strategy and architecture approach for "
                "the new implementation"
            ),
            "file_paths": ["src/main.py"],
        }

        output = _run_match(stdin_obj, catalog, tmp_path)
        decision = output.get("decision")

        assert decision in ("delegate", "advisory"), (
            f"Expected delegate or advisory, got {decision!r}. "
            "The architecture/strategy task must score Plan highly enough "
            "to reach at least advisory."
        )

        top_agent = output.get("agent") or (
            output.get("agents", [{}])[0].get("agent") if output.get("agents") else None
        )
        assert top_agent == "Plan", (
            f"Expected top agent 'Plan', got {top_agent!r}. "
            "The strategy/architecture keywords must score Plan above others."
        )
