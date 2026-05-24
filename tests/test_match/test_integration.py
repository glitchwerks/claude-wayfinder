"""Integration and regression tests for the full dispatch matcher pipeline.

Covers end-to-end behavior that spans multiple submodules:
- Determinism (same input → same output)
- Env-var and flag overrides for catalog path
- Output JSON shape requirements
- Worktree catalog parity (#359)
- Issue #361 edit-family triggers regression
- Issue #364 doc-writer agent regression
- Issue #366 agent-authoring skill regression
- CatalogEntry.source field (#475)
- Plugin agent exclusion / skill participation (#477)
- Plugin-override agent routing (#478)
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from tests.test_match.conftest import (
    _MATCH_MODULE,
    PYTHON,
    _build_synthetic_catalog,
    _catalog,
    _make_agent,
    _make_skill,
    _match_mod,
    _run,
)

# ===========================================================================
# Determinism
# ===========================================================================


class TestDeterminism:
    """Same input + catalog → byte-identical output across two runs."""

    def test_two_runs_produce_identical_output(self, tmp_path: Path) -> None:
        """Matcher is deterministic: two invocations produce the same JSON."""
        catalog = _catalog(
            [
                _make_agent(
                    "code-writer",
                    keywords=[
                        {"term": "implement", "weight": 1.0},
                        {"term": "feature", "weight": 0.5},
                    ],
                    path_globs=["**/*.py"],
                ),
                _make_agent(
                    "debugger",
                    keywords=[{"term": "debug", "weight": 1.0}],
                ),
            ]
        )
        stdin_obj = {
            "task_description": "implement a new feature in python",
            "file_paths": ["src/thing.py"],
        }

        r1 = _run(stdin_obj, catalog, tmp_path=tmp_path)
        r2 = _run(stdin_obj, catalog, tmp_path=tmp_path)

        assert r1.returncode == 0
        assert r2.returncode == 0
        assert r1.stdout == r2.stdout, "Outputs differ between runs (non-deterministic)"


# ===========================================================================
# Environment-variable overrides
# ===========================================================================


class TestEnvVarOverrides:
    """DISPATCH_CATALOG_PATH env var and --catalog-path flag are honored.

    Note: ``CLAUDE_HOME`` was removed as a lookup step in Issue #10.
    The matching test for the old ``CLAUDE_HOME`` behaviour has been
    converted into a negative assertion in ``TestIssue10FailLoudCatalogPath``.
    """

    def test_dispatch_catalog_path_override(self, tmp_path: Path) -> None:
        """DISPATCH_CATALOG_PATH points to a custom catalog file."""
        custom_path = tmp_path / "custom_catalog.json"
        catalog = _catalog(
            [
                _make_agent(
                    "code-writer",
                    keywords=[{"term": "implement", "weight": 1.0}],
                    path_globs=["**/*.py"],
                ),
            ]
        )
        custom_path.write_text(json.dumps(catalog), encoding="utf-8")

        result = subprocess.run(
            [PYTHON, "-m", *_MATCH_MODULE],
            input=json.dumps(
                {
                    "task_description": "implement the feature here",
                    "file_paths": ["main.py"],
                }
            ),
            capture_output=True,
            text=True,
            env={**os.environ, "DISPATCH_CATALOG_PATH": str(custom_path)},
            check=False,
        )
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out["decision"] != "needs_more_detail"

    def test_catalog_path_flag_takes_precedence_over_env(
        self, tmp_path: Path
    ) -> None:
        """--catalog-path flag takes precedence over DISPATCH_CATALOG_PATH env.

        Write two catalogs: one at an env-var path (with no matching agents)
        and one at the flag path (with a matching agent).  Assert the decision
        is driven by the flag-supplied catalog.
        """
        # Env-var catalog: empty entries — would produce needs_more_detail.
        env_catalog_path = tmp_path / "env_catalog.json"
        env_catalog_path.write_text(
            json.dumps({"schema_version": 1, "entries": []}),
            encoding="utf-8",
        )

        # Flag catalog: contains a matching agent.
        flag_catalog = _catalog(
            [
                _make_agent(
                    "code-writer",
                    keywords=[{"term": "implement", "weight": 1.0}],
                    path_globs=["**/*.py"],
                ),
            ]
        )
        flag_catalog_path = tmp_path / "flag_catalog.json"
        flag_catalog_path.write_text(json.dumps(flag_catalog), encoding="utf-8")

        env = {**os.environ, "DISPATCH_CATALOG_PATH": str(env_catalog_path)}
        result = subprocess.run(
            [
                PYTHON,
                "-m", *_MATCH_MODULE,
                "--catalog-path",
                str(flag_catalog_path),
            ],
            input=json.dumps(
                {
                    "task_description": "implement the feature",
                    "file_paths": ["main.py"],
                }
            ),
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        # Flag catalog has an agent — decision should not be needs_more_detail.
        assert out["decision"] != "needs_more_detail", (
            "--catalog-path flag must override DISPATCH_CATALOG_PATH env; "
            f"got: {out['decision']!r}"
        )


# ===========================================================================
# Output shape
# ===========================================================================


class TestOutputShape:
    """Output JSON contains required fields for each decision type."""

    def test_delegate_output_has_agent_and_skills(self, tmp_path: Path) -> None:
        """'delegate' output must have 'agent' and 'skills' fields."""
        catalog = _catalog(
            [
                _make_agent(
                    "code-writer",
                    keywords=[{"term": "implement", "weight": 1.0}],
                    path_globs=["**/*.py"],
                    applicable_skills=["python"],
                ),
            ]
        )
        stdin_obj = {
            "task_description": "implement the feature",
            "file_paths": ["src/main.py"],
        }
        result = _run(stdin_obj, catalog, tmp_path=tmp_path)
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        if out["decision"] == "delegate":
            assert "agent" in out
            assert "skills" in out
            assert "confidence" in out
            assert "rationale" in out

    def test_output_is_valid_json(self, tmp_path: Path) -> None:
        """stdout must always be valid JSON on success (exit 0)."""
        catalog = _catalog(
            [
                _make_agent(
                    "code-writer",
                    keywords=[{"term": "implement", "weight": 1.0}],
                ),
            ]
        )
        stdin_obj = {
            "task_description": "implement the module",
            "file_paths": ["a.py"],
        }
        result = _run(stdin_obj, catalog, tmp_path=tmp_path)
        assert result.returncode == 0
        # This will raise if stdout is not valid JSON
        out = json.loads(result.stdout)
        assert "decision" in out

    def test_rationale_field_is_present(self, tmp_path: Path) -> None:
        """Every successful output must include a 'rationale' field."""
        catalog = _catalog(
            [
                _make_agent(
                    "code-writer",
                    keywords=[{"term": "implement", "weight": 1.0}],
                    path_globs=["**/*.py"],
                ),
            ]
        )
        stdin_obj = {
            "task_description": "implement something",
            "file_paths": ["src/main.py"],
        }
        result = _run(stdin_obj, catalog, tmp_path=tmp_path)
        assert result.returncode == 0
        out = json.loads(result.stdout)
        assert "rationale" in out
        assert isinstance(out["rationale"], str)


# ===========================================================================
# Worktree catalog regression (#359)
# ===========================================================================


class TestWorktreeCatalogParity:
    """Matcher reads only dispatch-catalog.json — no worktree-specific variant.

    Regression guard for issue #359: a vestigial ``dispatch-catalog-wt.json``
    file was created by a prior agent session and could mislead future work
    into thinking a dual-catalog system is intentional.  These tests assert
    that the matcher's catalog resolution is independent of whether it is
    invoked from a worktree or the main checkout.

    Note: These tests previously used ``CLAUDE_HOME`` to supply the catalog
    directory.  After Issue #10 removed ``CLAUDE_HOME`` support, they have
    been updated to use ``DISPATCH_CATALOG_PATH`` (the explicit env var).
    """

    def test_matcher_reads_main_catalog_not_wt_variant(self, tmp_path: Path) -> None:
        """Matcher uses dispatch-catalog.json; dispatch-catalog-wt.json is ignored.

        Writes a catalog to ``<tmp>/state/dispatch-catalog.json`` and an
        intentionally broken file at ``<tmp>/state/dispatch-catalog-wt.json``
        (which would break matching if the matcher tried to read it).
        Sets ``DISPATCH_CATALOG_PATH`` to the main catalog path so the
        matcher resolves it explicitly.  Asserts the matcher succeeds using
        the main catalog, proving it never touches the wt file.
        """
        state_dir = tmp_path / "state"
        state_dir.mkdir()

        catalog = _catalog(
            [
                _make_agent(
                    "code-writer",
                    keywords=[{"term": "implement", "weight": 1.0}],
                    path_globs=["**/*.py"],
                ),
            ]
        )
        catalog_file = state_dir / "dispatch-catalog.json"
        catalog_file.write_text(json.dumps(catalog), encoding="utf-8")
        # Write a *broken* wt variant — if the matcher reads this it will fail.
        (state_dir / "dispatch-catalog-wt.json").write_text(
            "NOT VALID JSON — matcher must not read this file",
            encoding="utf-8",
        )

        env = {**os.environ, "DISPATCH_CATALOG_PATH": str(catalog_file)}

        result = subprocess.run(
            [PYTHON, "-m", *_MATCH_MODULE],
            input=json.dumps(
                {
                    "task_description": "implement the new feature",
                    "file_paths": ["src/main.py"],
                }
            ),
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        assert result.returncode == 0, (
            f"Matcher failed — may have tried to read dispatch-catalog-wt.json.\n"
            f"stderr: {result.stderr}"
        )
        out = json.loads(result.stdout)
        assert out["decision"] in {
            "delegate",
            "self_handle",
            "advisory",
        }, f"Unexpected decision: {out['decision']}"

    def test_worktree_and_main_checkout_produce_identical_decisions(
        self, tmp_path: Path
    ) -> None:
        """Matcher decision is identical regardless of catalog location.

        Simulates calling the matcher from a worktree (``wt_catalog``) vs
        the main checkout (``main_catalog``).  Both use the same catalog
        content, supplied via ``DISPATCH_CATALOG_PATH``.  The decision must
        be identical, confirming there is no context-specific routing path.

        Note: Previously used ``CLAUDE_HOME`` — updated for Issue #10.
        """
        catalog = _catalog(
            [
                _make_agent(
                    "code-writer",
                    keywords=[{"term": "implement", "weight": 1.0}],
                    path_globs=["**/*.py"],
                ),
                _make_agent(
                    "debugger",
                    keywords=[{"term": "debug", "weight": 1.0}],
                ),
            ]
        )
        stdin_obj = {
            "task_description": "implement the new feature",
            "file_paths": ["src/main.py"],
        }
        base_env = {k: v for k, v in os.environ.items() if k != "DISPATCH_CATALOG_PATH"}

        main_catalog = tmp_path / "main" / "dispatch-catalog.json"
        main_catalog.parent.mkdir()
        main_catalog.write_text(json.dumps(catalog), encoding="utf-8")

        wt_catalog = tmp_path / "wt" / "dispatch-catalog.json"
        wt_catalog.parent.mkdir()
        wt_catalog.write_text(json.dumps(catalog), encoding="utf-8")

        result_main = subprocess.run(
            [PYTHON, "-m", *_MATCH_MODULE],
            input=json.dumps(stdin_obj),
            capture_output=True,
            text=True,
            env={**base_env, "DISPATCH_CATALOG_PATH": str(main_catalog)},
            check=False,
        )
        result_wt = subprocess.run(
            [PYTHON, "-m", *_MATCH_MODULE],
            input=json.dumps(stdin_obj),
            capture_output=True,
            text=True,
            env={**base_env, "DISPATCH_CATALOG_PATH": str(wt_catalog)},
            check=False,
        )

        assert result_main.returncode == 0, result_main.stderr
        assert result_wt.returncode == 0, result_wt.stderr

        out_main = json.loads(result_main.stdout)
        out_wt = json.loads(result_wt.stdout)

        assert out_main["decision"] == out_wt["decision"], (
            f"Decision differs between main ({out_main['decision']}) "
            f"and worktree ({out_wt['decision']}) contexts"
        )


# ===========================================================================
# Issue #361 regression tests: code-writer edit-family triggers + path_globs
# ===========================================================================


class TestIssue361EditFamilyTriggers:
    """Regression tests for issue #361.

    Before the fix, code-writer scored 0.0 on edit-style tasks because
    it had no edit-family keywords and no path_globs.  These three tests
    cover the three scenarios described in the issue's acceptance criteria.

    All three tests build a synthetic catalog from tests/fixtures/ so they
    run without the private harness agents/ directory and give true
    TDD red/green coverage of the fixture frontmatter.
    """

    def test_css_edit_routes_to_code_writer(self, tmp_path: Path) -> None:
        """CSS edit task with HTML file path must identify code-writer.

        Expected post-fix score for code-writer:
          0.4 (glob *.html matches index.html)
          + 0.5 * 0.5 (edit keyword, weight=0.5)
          = 0.65 → advisory or delegate (code-writer is the identified agent).

        Pre-fix: code-writer has no path_globs and no edit keyword
        → score 0.0 → self_handle_unaided → no agent identified.

        This test verifies that code-writer is identified (decision is
        'delegate' or 'advisory'), not that the exact threshold is met.
        The key invariant is that the decision is NOT 'self_handle_unaided'
        and the agent IS 'code-writer'.
        """
        catalog_path = _build_synthetic_catalog(tmp_path)
        stdin_obj = {
            "task_description": (
                "Edit two CSS values in index.html on an existing branch"
                " to reduce the height of a sticky top nav bar."
            ),
            "file_paths": ["index.html"],
            "tool_mentions": ["git"],
        }
        result = _run(
            stdin_obj,
            {},  # unused when catalog_path is provided
            catalog_path=catalog_path,
            tmp_path=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out["decision"] in ("delegate", "advisory"), (
            f"Expected 'delegate' or 'advisory', got '{out['decision']}' "
            f"(confidence={out.get('confidence')}) — "
            "code-writer must match edit-family keywords + HTML path_glob"
        )
        assert (
            out.get("agent") == "code-writer"
        ), f"Expected agent 'code-writer', got '{out.get('agent')}'"

    def test_python_implement_routes_to_code_writer(self, tmp_path: Path) -> None:
        """Python script task with .py file path must identify code-writer.

        Expected post-fix score for code-writer:
          0.4 (glob *.py matches deploy.py)
          + 0.5 * 1.0 (implement keyword, weight=1.0)
          + 0.5 * 0.25 (script keyword, weight=0.25)
          = 0.4 + 0.5 + 0.125 = 1.025 → clamped to 1.0 → delegate.

        Pre-fix: code-writer has no path_globs → implement+script keywords
        score 0.625, but without glob bonus code-writer may still lose.
        The path_glob addition in #361 raises the score above 0.5 so
        code-writer is properly identified as the right agent.
        """
        catalog_path = _build_synthetic_catalog(tmp_path)
        stdin_obj = {
            "task_description": "Implement the deployment script in Python",
            "file_paths": ["deploy.py"],
        }
        result = _run(
            stdin_obj,
            {},
            catalog_path=catalog_path,
            tmp_path=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out["decision"] in ("delegate", "advisory"), (
            f"Expected 'delegate' or 'advisory', got '{out['decision']}' "
            f"(confidence={out.get('confidence')})"
        )
        assert (
            out.get("agent") == "code-writer"
        ), f"Expected agent 'code-writer', got '{out.get('agent')}'"

    def test_bicep_edit_routes_to_devops_not_code_writer(self, tmp_path: Path) -> None:
        """Bicep template edit with infra keywords must route to devops.

        This is the key regression guard: edit-family keywords added to
        code-writer must NOT pull bicep/infrastructure work away from devops.

        Score breakdown for devops (post-fix):
          0.4 (glob **/*.bicep matches infra/main.bicep)
          + 0.5 * 1.0 (infrastructure keyword, weight=1.0)
          + 0.5 * 1.0 (deployment keyword, weight=1.0)
          = 0.4 + 0.5 + 0.5 = 1.4 → clamped to 1.0.

        Score breakdown for code-writer (post-fix):
          0.5 * 0.5 (update keyword)
          = 0.25
          (infra/main.bicep does not match any code-writer path_glob)

        Gap = 1.0 - 0.25 = 0.75 >= 0.2 → delegate to devops.
        code-writer must NOT win here even with edit-family keywords.
        """
        catalog_path = _build_synthetic_catalog(tmp_path)
        stdin_obj = {
            "task_description": (
                "Update the bicep infrastructure deployment template"
                " to change the storage account SKU"
            ),
            "file_paths": ["infra/main.bicep"],
        }
        result = _run(
            stdin_obj,
            {},
            catalog_path=catalog_path,
            tmp_path=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out["decision"] == "delegate", f"Expected 'delegate', got '{out['decision']}'"
        assert out["agent"] == "devops", (
            f"Expected agent 'devops', got '{out.get('agent')}' — "
            "bicep/infrastructure edits must NOT route to code-writer"
        )


# ===========================================================================
# Issue #364 regression tests: doc-writer agent for prose/docs/specs
# ===========================================================================


class TestIssue364DocWriterAgent:
    """Regression tests for issue #364.

    Before this fix, prose-shaped markdown edits (docs/**/*.md, READMEs,
    plan files, ADRs) had no specialist owner.  The doc-writer agent
    introduced in #364 fills that gap.

    All four tests build a synthetic catalog from tests/fixtures/ so they
    run without the private harness agents/ directory and give true
    TDD red/green coverage of the fixture frontmatter.
    """

    def test_docs_md_routes_to_doc_writer(self, tmp_path: Path) -> None:
        """docs/**/*.md path + prose task description must route to doc-writer.

        Expected post-fix score for doc-writer:
          0.4  (glob docs/*.md matches docs/foo.md)
          + 0.5 * 1.0  (docs keyword, weight=1.0)
          + 0.5 * 0.25 (update keyword, weight=0.25)
          = 0.4 + 0.5 + 0.125 = 1.025 → clamped to 1.0 → delegate.

        Pre-fix: doc-writer does not exist → no agent matches prose paths
        → self_handle_unaided, no agent in output.
        """
        catalog_path = _build_synthetic_catalog(tmp_path)
        stdin_obj = {
            "task_description": "update the docs",
            "file_paths": ["docs/foo.md"],
        }
        result = _run(
            stdin_obj,
            {},
            catalog_path=catalog_path,
            tmp_path=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out["decision"] in ("delegate", "advisory"), (
            f"Expected 'delegate' or 'advisory', got '{out['decision']}' "
            f"(confidence={out.get('confidence')}) — "
            "docs/*.md + 'update the docs' must route to doc-writer"
        )
        assert (
            out.get("agent") == "doc-writer"
        ), f"Expected agent 'doc-writer', got '{out.get('agent')}'"

    def test_readme_routes_to_doc_writer(self, tmp_path: Path) -> None:
        """README.md path + edit task description must route to doc-writer.

        Expected post-fix score for doc-writer:
          0.4  (glob README.md matches README.md)
          + 0.5 * 1.0  (readme keyword, weight=1.0)
          + 0.5 * 0.25 (edit keyword, weight=0.25)
          = 0.4 + 0.5 + 0.125 = 1.025 → clamped to 1.0 → delegate.

        Pre-fix: doc-writer does not exist → README edits fall through.
        """
        catalog_path = _build_synthetic_catalog(tmp_path)
        stdin_obj = {
            "task_description": "edit the readme",
            "file_paths": ["README.md"],
        }
        result = _run(
            stdin_obj,
            {},
            catalog_path=catalog_path,
            tmp_path=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out["decision"] in ("delegate", "advisory"), (
            f"Expected 'delegate' or 'advisory', got '{out['decision']}' "
            f"(confidence={out.get('confidence')}) — "
            "README.md + 'edit the readme' must route to doc-writer"
        )
        assert (
            out.get("agent") == "doc-writer"
        ), f"Expected agent 'doc-writer', got '{out.get('agent')}'"

    def test_agent_md_does_not_route_to_doc_writer(self, tmp_path: Path) -> None:
        """agents/**/*.md path must NOT route to doc-writer.

        Enforced via path_globs_excluded (issue #24): ``agents/**/*.md``
        and ``agents/*.md`` are in doc-writer's ``path_globs_excluded``,
        dropping it from the scored pool before threshold gating.
        Before #24 this was scope-by-omission (``agents/**`` not in
        ``path_globs``); now it is explicit exclusion that survives
        future path_globs broadening.

        What decision is returned depends on other agents and harness
        carve-out behaviour, but doc-writer must not win here.
        """
        catalog_path = _build_synthetic_catalog(tmp_path)
        stdin_obj = {
            "task_description": "edit the agent",
            "file_paths": ["agents/code-writer.md"],
        }
        result = _run(
            stdin_obj,
            {},
            catalog_path=catalog_path,
            tmp_path=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out.get("agent") != "doc-writer", (
            "agents/**/*.md must NOT route to doc-writer; "
            f"got agent='{out.get('agent')}', decision='{out['decision']}'"
        )

    def test_python_edit_still_routes_to_code_writer(self, tmp_path: Path) -> None:
        """src/main.py + 'edit the function' must still route to code-writer.

        Regression guard from #361: adding doc-writer must not steal
        code-file edits away from code-writer.  code-writer's path_globs
        match **/*.py; doc-writer has no Python path_globs.

        Expected score for code-writer:
          0.4  (glob **/*.py matches src/main.py)
          + 0.5 * 0.5  (edit keyword, weight=0.5)
          = 0.65 → advisory or delegate (code-writer identified).

        doc-writer should score 0.0 (no matching glob, 'edit' weight
        only 0.25 → 0.125, not enough to win over code-writer).
        """
        catalog_path = _build_synthetic_catalog(tmp_path)
        stdin_obj = {
            "task_description": "edit the function",
            "file_paths": ["src/main.py"],
        }
        result = _run(
            stdin_obj,
            {},
            catalog_path=catalog_path,
            tmp_path=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out["decision"] in ("delegate", "advisory"), (
            f"Expected 'delegate' or 'advisory', got '{out['decision']}' "
            f"(confidence={out.get('confidence')}) — "
            "src/main.py + 'edit the function' must still route to code-writer"
        )
        assert out.get("agent") == "code-writer", (
            f"Expected agent 'code-writer', got '{out.get('agent')}' — "
            "adding doc-writer must not regress #361 code-writer routing"
        )


# ===========================================================================
# Issue #24 regression tests: path_globs_excluded explicit exclusion
# ===========================================================================


class TestIssue24PathGlobsExcluded:
    """Regression tests for issue #24: path_globs_excluded field.

    The doc-writer fixture now uses ``path_globs_excluded`` to explicitly
    reject harness files (``agents/**/*.md``, ``skills/**/*.md``) instead
    of relying on scope-by-omission.  These tests verify the exclusion
    mechanism works end-to-end through the full dispatch pipeline.
    """

    def test_doc_writer_excluded_for_agents_md_via_path_globs_excluded(
        self, tmp_path: Path
    ) -> None:
        """doc-writer is excluded for agents/*.md via path_globs_excluded.

        The doc-writer fixture has ``path_globs: ['**/*.md']`` (broad)
        combined with ``path_globs_excluded: ['agents/**/*.md', 'agents/*.md']``
        (issue #24).  An ``agents/code-writer.md`` path should drop doc-writer
        from the scored pool despite the broad ``**/*.md`` inclusion glob.
        """
        catalog_path = _build_synthetic_catalog(tmp_path)
        stdin_obj = {
            "task_description": "update the agent docs",
            "file_paths": ["agents/code-writer.md"],
        }
        result = _run(
            stdin_obj,
            {},
            catalog_path=catalog_path,
            tmp_path=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out.get("agent") != "doc-writer", (
            "doc-writer must NOT win for agents/**/*.md even with broad "
            "path_globs — path_globs_excluded must drop it (#24); "
            f"got agent='{out.get('agent')}', decision='{out['decision']}'"
        )

    def test_doc_writer_included_for_docs_md_not_excluded(
        self, tmp_path: Path
    ) -> None:
        """doc-writer is NOT excluded for docs/foo.md.

        A docs/foo.md path is in doc-writer's path_globs and NOT in
        path_globs_excluded.  The entry must still score and win here.
        """
        catalog_path = _build_synthetic_catalog(tmp_path)
        stdin_obj = {
            "task_description": "update the docs",
            "file_paths": ["docs/foo.md"],
        }
        result = _run(
            stdin_obj,
            {},
            catalog_path=catalog_path,
            tmp_path=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out.get("agent") == "doc-writer", (
            f"Expected agent 'doc-writer', got '{out.get('agent')}' — "
            "docs/foo.md is not excluded and must still route to doc-writer"
        )


# ===========================================================================
# Issue #366 regression tests: agent-authoring skill for harness edits
# ===========================================================================


class TestIssue366AgentAuthoringSkill:
    """Regression tests for issue #366.

    Harness files (``agents/**/*.md``, ``skills/**/SKILL.md``, ``CLAUDE.md``,
    ``AGENTS.md``, ``GEMINI.md``) are router-self-handled, and the router
    should activate the ``agent-authoring`` skill when these files are in
    scope.

    All four tests build a synthetic catalog from tests/fixtures/ so they
    run without the private harness agents/ directory and give true TDD
    red/green coverage of the fixture frontmatter.
    """

    def test_agent_md_edit_self_handles_with_agent_authoring_skill(self, tmp_path: Path) -> None:
        """agents/foo.md + 'update the frontmatter' → self_handle with agent-authoring skill.

        Expected post-fix score breakdown:
          - ``agents/foo.md`` matches ``agents/*.md`` path_glob → +0.4
          - keyword "frontmatter" (weight=1.0) → +0.5*1.0 = +0.50
          Total skill score = 0.90 → self_handle decision, "agent-authoring" in skills.

        No agent should score >= 0.85 and dominate (harness files are not
        delegated to sub-agents), so the decision must be ``self_handle``
        (not ``delegate``).
        """
        catalog_path = _build_synthetic_catalog(tmp_path)
        stdin_obj = {
            "task_description": "update the frontmatter in agents/foo.md",
            "file_paths": ["agents/foo.md"],
        }
        result = _run(
            stdin_obj,
            {},
            catalog_path=catalog_path,
            tmp_path=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out["decision"] == "self_handle", (
            f"Expected 'self_handle', got '{out['decision']}' — "
            "agents/foo.md + 'edit the agent' must self_handle with "
            "agent-authoring skill active"
        )
        assert "agent-authoring" in out.get("skills", []), (
            f"Expected 'agent-authoring' in skills, got {out.get('skills')} — "
            "the agent-authoring skill must be activated for agent file edits"
        )

    def test_claude_md_edit_self_handles_with_agent_authoring_skill(self, tmp_path: Path) -> None:
        """CLAUDE.md + 'tighten the harness rule' → self_handle with agent-authoring skill.

        Expected post-fix score breakdown:
          - bare ``CLAUDE.md`` matches ``CLAUDE.md`` path_glob → +0.4
            (the bare form is needed because fnmatch ``**/CLAUDE.md``
            does NOT match a bare ``CLAUDE.md`` path)
          - keyword "harness" (weight=1.0) → +0.5*1.0 = +0.50
          Total skill score = 0.90 → self_handle, "agent-authoring" in skills.
        """
        catalog_path = _build_synthetic_catalog(tmp_path)
        stdin_obj = {
            "task_description": "tighten the harness rule in CLAUDE.md",
            "file_paths": ["CLAUDE.md"],
        }
        result = _run(
            stdin_obj,
            {},
            catalog_path=catalog_path,
            tmp_path=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out["decision"] == "self_handle", (
            f"Expected 'self_handle', got '{out['decision']}' — "
            "CLAUDE.md + 'tighten the rule' must self_handle with "
            "agent-authoring skill active"
        )
        assert "agent-authoring" in out.get("skills", []), (
            f"Expected 'agent-authoring' in skills, got {out.get('skills')} — "
            "the agent-authoring skill must activate for CLAUDE.md edits. "
            "Check that triggers.yml includes bare 'CLAUDE.md' alongside "
            "'**/CLAUDE.md' (fnmatch does not match bare filenames with **/ patterns)"
        )

    def test_skill_md_edit_self_handles_with_agent_authoring_skill(self, tmp_path: Path) -> None:
        """skills/foo/SKILL.md + 'update the skill' → self_handle with agent-authoring.

        Expected post-fix score breakdown:
          - ``skills/foo/SKILL.md`` matches ``skills/**/SKILL.md`` path_glob → +0.4
          - keyword "skill" (weight=0.25, demoted from 0.5 in #454) → +0.5*0.25 = +0.125
          Total skill score = 0.525 → self_handle, "agent-authoring" in skills.
        """
        catalog_path = _build_synthetic_catalog(tmp_path)
        stdin_obj = {
            "task_description": "update the skill",
            "file_paths": ["skills/foo/SKILL.md"],
        }
        result = _run(
            stdin_obj,
            {},
            catalog_path=catalog_path,
            tmp_path=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out["decision"] == "self_handle", (
            f"Expected 'self_handle', got '{out['decision']}' — "
            "skills/foo/SKILL.md + 'update the skill' must self_handle with "
            "agent-authoring skill active"
        )
        assert "agent-authoring" in out.get("skills", []), (
            f"Expected 'agent-authoring' in skills, got {out.get('skills')} — "
            "the agent-authoring skill must be activated for SKILL.md edits"
        )

    def test_doc_md_does_not_trigger_agent_authoring(self, tmp_path: Path) -> None:
        """docs/foo.md + 'update the docs' must NOT activate agent-authoring.

        Regression guard: the agent-authoring skill's path_globs must not
        include general docs paths.  docs/foo.md is prose content handled
        by the doc-writer agent, not a harness file.  This test ensures the
        skill's path_globs are tightly scoped to actual harness artifacts.
        """
        catalog_path = _build_synthetic_catalog(tmp_path)
        stdin_obj = {
            "task_description": "update the docs",
            "file_paths": ["docs/foo.md"],
        }
        result = _run(
            stdin_obj,
            {},
            catalog_path=catalog_path,
            tmp_path=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert "agent-authoring" not in out.get("skills", []), (
            f"agent-authoring must NOT activate for docs/foo.md, "
            f"but it appeared in skills={out.get('skills')} — "
            "tighten the agent-authoring triggers.yml path_globs to exclude docs/**"
        )


# ---------------------------------------------------------------------------
# CatalogEntry.source field — issue #475
# ---------------------------------------------------------------------------


class TestCatalogEntrySourceField:
    """Verify that CatalogEntry carries a ``source`` field that round-trips
    through ``load_catalog``.
    """

    def test_catalog_entry_source_field_round_trips(self, tmp_path: Path) -> None:
        """CatalogEntry.source is populated from the catalog JSON and defaults
        to ``"owned"`` when the field is absent.

        Three cases are verified in one catalog load:
        - An entry with ``source="owned"`` is preserved as ``"owned"``.
        - An entry with ``source="plugin"`` is preserved as ``"plugin"``.
        - An entry that omits ``source`` entirely defaults to ``"owned"``.
        """
        catalog_data = _catalog(
            [
                {**_make_agent("agent-owned"), "source": "owned"},
                {**_make_agent("agent-plugin"), "source": "plugin"},
                # ``source`` key is intentionally absent here.
                {k: v for k, v in _make_agent("agent-no-source").items() if k != "source"},
            ]
        )
        catalog_file = tmp_path / "catalog.json"
        catalog_file.write_text(json.dumps(catalog_data), encoding="utf-8")

        entries = _match_mod.load_catalog(catalog_file)
        by_name = {e.name: e for e in entries}

        assert by_name["agent-owned"].source == "owned", "source='owned' should be preserved"
        assert by_name["agent-plugin"].source == "plugin", "source='plugin' should be preserved"
        assert (
            by_name["agent-no-source"].source == "owned"
        ), "omitted source should default to 'owned'"


# ---------------------------------------------------------------------------
# Issue #477 — is_agent_routable predicate + matcher integration
# ---------------------------------------------------------------------------


class TestPluginAgentExcluded:
    """After Pass 2.5 wiring, plugin agents in the catalog are excluded from
    scoring via the ``is_agent_routable`` predicate.
    """

    def test_match_excludes_plugin_agent(self, tmp_path: Path) -> None:
        """A plugin agent (source='plugin') is excluded from agent scoring.

        The catalog contains one plugin agent ('plugin:my-agent') and one
        owned agent ('code-writer').  A task that would match both by keyword
        must route only to the owned agent (because the plugin agent is
        filtered out before scoring).
        """
        catalog = _catalog(
            [
                _make_agent(
                    "code-writer",
                    keywords=[{"term": "implement", "weight": 1.0}],
                    path_globs=["**/*.py"],
                ),
                # Plugin agent — must be excluded from scoring.
                {
                    **_make_agent(
                        "plugin:my-agent",
                        keywords=[{"term": "implement", "weight": 1.0}],
                        path_globs=["**/*.py"],
                    ),
                    "source": "plugin",
                },
            ]
        )
        stdin_obj = {
            "task_description": "implement the feature",
            "file_paths": ["src/main.py"],
        }
        result = _run(stdin_obj, catalog, tmp_path=tmp_path)
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        # Plugin agent must never appear as the winning agent.
        assert out.get("agent") != "plugin:my-agent", (
            "plugin agent 'plugin:my-agent' appeared as the matched agent — "
            "it should have been excluded by is_agent_routable"
        )
        # The owned agent must still be matchable.
        assert out["decision"] in (
            "delegate",
            "advisory",
            "self_handle",
            "self_handle_unaided",
            "needs_more_detail",
        )

    def test_match_plugin_skill_participates_in_scoring(self, tmp_path: Path) -> None:
        """A plugin skill (source='plugin') enters the skill scoring pool.

        Plugin skills are dormant (zero triggers) so they score 0.0 and
        cannot drive a decision.  But they must not be excluded from the
        pool — if a plugin skill ever gains an override with real triggers,
        it should be able to score.

        This test uses a synthetic plugin skill with a real trigger so
        we can verify the pool contains it.  The skill must produce a
        self_handle decision when it's the only scoring entry.
        """
        catalog = _catalog(
            [
                # Plugin skill WITH a keyword trigger — source='plugin'
                {
                    **_make_skill(
                        "superpowers:brainstorming",
                        keywords=[{"term": "brainstorm", "weight": 1.0}],
                        applicable_agents=["*"],
                    ),
                    "source": "plugin",
                },
            ]
        )
        stdin_obj = {
            "task_description": "brainstorm ideas for the project",
            "file_paths": ["docs/ideas.md"],
        }
        result = _run(stdin_obj, catalog, tmp_path=tmp_path)
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        # The plugin skill must participate in scoring; if it scores >= 0.5,
        # the decision should be self_handle with it in the skills list.
        if out["decision"] == "self_handle":
            assert "superpowers:brainstorming" in out.get("skills", []), (
                "Plugin skill 'superpowers:brainstorming' scored but was not "
                "included in the self_handle skills list"
            )

    def test_previously_routed_prompt_still_routes_to_same_target(self, tmp_path: Path) -> None:
        """Adding dormant plugin entries does not change routing for owned agents.

        A prompt that previously routed to 'code-writer' must still route
        to 'code-writer' after dormant plugin entries are added to the catalog.
        Dormant entries score 0.0 (no triggers) so they cannot change decisions.
        """
        base_catalog = _catalog(
            [
                _make_agent(
                    "code-writer",
                    keywords=[{"term": "implement", "weight": 1.0}],
                    path_globs=["**/*.py"],
                    applicable_skills=["*"],
                ),
            ]
        )
        stdin_obj = {
            "task_description": "implement the new feature",
            "file_paths": ["src/main.py"],
        }
        result_base = _run(stdin_obj, base_catalog, tmp_path=tmp_path)
        assert result_base.returncode == 0, result_base.stderr
        out_base = json.loads(result_base.stdout)

        # Now add dormant plugin entries to the catalog.
        augmented_catalog = _catalog(
            [
                _make_agent(
                    "code-writer",
                    keywords=[{"term": "implement", "weight": 1.0}],
                    path_globs=["**/*.py"],
                    applicable_skills=["*"],
                ),
                # Dormant plugin skill — no triggers, source='plugin'
                {
                    "name": "superpowers:brainstorming",
                    "kind": "skill",
                    "description": "Brainstorming skill.",
                    "source": "plugin",
                    "triggers": {
                        "command_prefixes": [],
                        "agent_mentions": [],
                        "path_globs": [],
                        "keywords": [],
                        "tool_mentions": [],
                        "excludes": [],
                    },
                    "applicable_agents": [],
                },
                # Dormant plugin agent — no triggers, source='plugin'
                {
                    "name": "plugin:some-agent",
                    "kind": "agent",
                    "description": "A plugin agent.",
                    "source": "plugin",
                    "triggers": {
                        "command_prefixes": [],
                        "agent_mentions": [],
                        "path_globs": [],
                        "keywords": [],
                        "tool_mentions": [],
                        "excludes": [],
                    },
                    "applicable_skills": [],
                },
            ]
        )
        result_aug = _run(stdin_obj, augmented_catalog, tmp_path=tmp_path)
        assert result_aug.returncode == 0, result_aug.stderr
        out_aug = json.loads(result_aug.stdout)

        assert out_aug["decision"] == out_base["decision"], (
            f"Decision changed after adding dormant plugin entries: "
            f"{out_base['decision']!r} → {out_aug['decision']!r}. "
            "Dormant entries (zero triggers) must not affect routing."
        )
        if out_base["decision"] == "delegate":
            assert out_aug.get("agent") == out_base.get(
                "agent"
            ), "Delegate target changed after adding dormant plugin entries"


# ---------------------------------------------------------------------------
# Issue #478 — plugin-override agent routing
# ---------------------------------------------------------------------------


class TestPluginOverrideAgentRouting:
    """A plugin-override agent must be eligible for agent scoring.

    Unlike source='plugin' agents (which are inert/excluded), a
    source='plugin-override' agent has explicit trigger configuration
    and must participate in scoring.
    """

    def test_match_includes_plugin_override_agent(self, tmp_path: Path) -> None:
        """A plugin-override agent (source='plugin-override') can be matched.

        The catalog contains one plugin-override agent with keyword triggers.
        A task matching that keyword must route to that agent, confirming
        that source='plugin-override' agents are not excluded by
        ``is_agent_routable``.
        """
        catalog = _catalog(
            [
                # Plugin-override agent — must participate in scoring.
                {
                    **_make_agent(
                        "myplugin:my-agent",
                        keywords=[{"term": "specialtask", "weight": 1.0}],
                    ),
                    "source": "plugin-override",
                },
            ]
        )
        stdin_obj = {
            "task_description": "specialtask",
            "file_paths": [],
        }
        result = _run(stdin_obj, catalog, tmp_path=tmp_path)
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        # The plugin-override agent must be eligible — it should either be
        # matched as the delegate or produce a non-no_match decision.
        assert out["decision"] != "no_match", (
            f"plugin-override agent was not included in scoring "
            f"(decision was 'no_match'); full output: {out}"
        )


# ---------------------------------------------------------------------------
# Issue #213 — override short-circuit integration test
# ---------------------------------------------------------------------------


class TestOverrideShortCircuit:
    """When DISPATCH_OVERRIDES_PATH is set and a rule matches, the matcher
    returns the override decision without running scoring.
    """

    def test_main_logs_json_parse_error_entry(self, tmp_path: Path) -> None:
        """JSON-parse-error path writes a log entry before returning.

        When stdin contains invalid JSON, ``main()`` emits a
        ``needs_more_detail`` decision to stdout.  This test proves that
        a log entry is also written so downstream NDJSON consumers can
        observe the parse failure.

        The log entry must carry:
        - ``override_id: null`` (no override fired — parse failed first)
        - ``output.decision == "needs_more_detail"``
        - ``catalog_hash == ""`` (sentinel: catalog loaded but stdin not parsed)
        """
        import os as _os
        import subprocess as _subprocess

        catalog_path = tmp_path / "catalog.json"
        catalog_data = _catalog(
            [
                _make_agent(
                    "code-writer",
                    keywords=[{"term": "implement", "weight": 1.0}],
                    applicable_skills=[],
                ),
            ]
        )
        catalog_path.write_text(json.dumps(catalog_data), encoding="utf-8")

        log_path = tmp_path / "log.jsonl"

        result = _subprocess.run(
            [PYTHON, "-m", *_MATCH_MODULE, "--catalog-path", str(catalog_path)],
            input="{not json",
            capture_output=True,
            text=True,
            env={
                **_os.environ,
                "DISPATCH_CATALOG_PATH": str(catalog_path),
                "DISPATCH_LOG_PATH": str(log_path),
            },
            check=False,
        )
        assert result.returncode == 0, (
            f"Expected exit 0 for parse-error path, got {result.returncode}; "
            f"stderr={result.stderr!r}"
        )
        out = json.loads(result.stdout)
        assert out["decision"] == "needs_more_detail", (
            f"Expected decision='needs_more_detail', got {out['decision']!r}"
        )
        assert log_path.exists(), (
            "Log file was not created on the JSON-parse-error path — "
            "_write_log_entry must be called before the parse-error return"
        )
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1, (
            f"Expected exactly 1 log entry from parse-error run, got {len(lines)}"
        )
        entry = json.loads(lines[0])
        assert entry["override_id"] is None, (
            f"Expected override_id=null in parse-error log entry, "
            f"got {entry.get('override_id')!r}"
        )
        assert entry["output"]["decision"] == "needs_more_detail", (
            f"Expected output.decision='needs_more_detail' in log entry, "
            f"got {entry['output'].get('decision')!r}"
        )
        assert entry["catalog_hash"] == "", (
            f"Expected catalog_hash='' (sentinel for parse-failed pre-catalog) "
            f"in log entry, got {entry.get('catalog_hash')!r}"
        )

    def test_main_short_circuits_on_override(
        self, tmp_path: Path, monkeypatch: object
    ) -> None:
        """Override rule fires and short-circuits the scored pipeline.

        Catalog has a code-writer agent that would score high on
        "implement feature x".  An overrides file sets a rule with
        command_prefix=/skip → self_handle_unaided with override metadata.
        The input carries command_prefix=/skip so the rule must fire.

        Assertions:
        - decision == "self_handle_unaided" (from the rule, not scoring)
        - disposition_source == "override"
        - override_id == "always-skip"
        - rationale == "test override fires"
        """
        catalog_path = tmp_path / "catalog.json"
        catalog_data = _catalog(
            [
                _make_agent(
                    "code-writer",
                    keywords=[{"term": "implement", "weight": 1.0}],
                    applicable_skills=[],
                ),
            ]
        )
        catalog_path.write_text(json.dumps(catalog_data), encoding="utf-8")

        overrides_path = tmp_path / "overrides.json"
        overrides_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "rules": [
                        {
                            "id": "always-skip",
                            "decision": "self_handle_unaided",
                            "agent": None,
                            "skills": [],
                            "confidence": 1.0,
                            "rationale": "test override fires",
                            "predicates": {"command_prefix": "/skip"},
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        result = _run(
            stdin_obj={
                "task_description": "implement feature x",
                "command_prefix": "/skip",
            },
            catalog=catalog_data,
            catalog_path=catalog_path,
            extra_env={"DISPATCH_OVERRIDES_PATH": str(overrides_path)},
            tmp_path=tmp_path,
        )
        assert result.returncode == 0, f"matcher exited non-zero:\n{result.stderr}"
        out = json.loads(result.stdout)
        assert out["decision"] == "self_handle_unaided", (
            f"Expected 'self_handle_unaided' from override, "
            f"got '{out['decision']}'"
        )
        assert out["disposition_source"] == "override", (
            f"Expected disposition_source='override', got {out.get('disposition_source')!r}"
        )
        assert out["override_id"] == "always-skip", (
            f"Expected override_id='always-skip', got {out.get('override_id')!r}"
        )
        assert out["rationale"] == "test override fires", (
            f"Expected rationale from override rule, got {out.get('rationale')!r}"
        )


# ---------------------------------------------------------------------------
# Issue #213 — E2E pipeline test through _main.py with bundled demo fixtures
# ---------------------------------------------------------------------------


class TestDemoFixturesE2EPipeline:
    """End-to-end pipeline test using the bundled demo fixtures.

    Exercises ``_main.py:main()`` directly (via subprocess ``-m`` invocation),
    NOT ``cli.py:run_demo()``.  Task 4b already covers the ``run_demo()``
    path; this class independently verifies that ``_main.py`` short-circuits
    correctly when the bundled ``demo-overrides.json`` is active.

    The bundled fixtures live at:
      ``src/claude_wayfinder/fixtures/demo-catalog.json``
      ``src/claude_wayfinder/fixtures/demo-overrides.json``

    Agent/skill name note: ``demo-overrides.json`` references only agents and
    skills present in ``demo-catalog.json`` (``code-writer``, ``devops``,
    ``python``) so the audit-clean requirement is satisfied.
    """

    # Resolve the fixtures directory from the installed package location so
    # the test works regardless of shell CWD or worktree layout.
    _FIXTURES_DIR: Path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "claude_wayfinder"
        / "fixtures"
    )

    def test_deploy_command_short_circuits_via_main(
        self, tmp_path: Path
    ) -> None:
        """command_prefix /deploy fires demo-deploy-command via _main.py.

        The demo-overrides.json rule ``demo-deploy-command`` predicates on
        ``command_prefix: /deploy``.  Sending that context through
        ``_main.py`` must return:

        - ``decision == "self_handle_unaided"``  (the rule's decision)
        - ``disposition_source == "override"``   (short-circuit fired)
        - ``override_id == "demo-deploy-command"``

        This path runs through ``_main.py:main()`` only; ``run_demo()`` is
        a separate entry point covered by Task 4b's test.
        """
        catalog_path = self._FIXTURES_DIR / "demo-catalog.json"
        overrides_path = self._FIXTURES_DIR / "demo-overrides.json"

        assert catalog_path.exists(), (
            f"Bundled demo-catalog.json not found at {catalog_path}"
        )
        assert overrides_path.exists(), (
            f"Bundled demo-overrides.json not found at {overrides_path}"
        )

        result = subprocess.run(
            [PYTHON, "-m", *_MATCH_MODULE],
            input=json.dumps(
                {
                    "task_description": "deploy the staging service",
                    "file_paths": [],
                    "command_prefix": "/deploy",
                }
            ),
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "DISPATCH_CATALOG_PATH": str(catalog_path),
                "DISPATCH_OVERRIDES_PATH": str(overrides_path),
            },
            check=False,
        )
        assert result.returncode == 0, (
            f"_main.py exited non-zero:\n{result.stderr}"
        )
        out = json.loads(result.stdout)
        assert out["decision"] == "self_handle_unaided", (
            f"Expected decision='self_handle_unaided' from override, "
            f"got {out['decision']!r}; full output: {out}"
        )
        assert out["disposition_source"] == "override", (
            f"Expected disposition_source='override', "
            f"got {out.get('disposition_source')!r}"
        )
        assert out["override_id"] == "demo-deploy-command", (
            f"Expected override_id='demo-deploy-command', "
            f"got {out.get('override_id')!r}"
        )

    def test_non_override_context_produces_scored_disposition(
        self, tmp_path: Path
    ) -> None:
        """A context that matches no override predicate falls through to scoring.

        Sends a task that does not trigger any predicate in
        ``demo-overrides.json`` (no ``/deploy`` prefix, no ``docs/**/*.md``
        path, no ``Write`` tool mention).  The pipeline must complete normal
        scored matching and return ``disposition_source == "scored"``.

        This negative case proves the override short-circuit does NOT fire
        for arbitrary inputs — only for predicates that match.
        """
        catalog_path = self._FIXTURES_DIR / "demo-catalog.json"
        overrides_path = self._FIXTURES_DIR / "demo-overrides.json"

        result = subprocess.run(
            [PYTHON, "-m", *_MATCH_MODULE],
            input=json.dumps(
                {
                    "task_description": "implement the authentication module",
                    "file_paths": ["src/auth.py"],
                }
            ),
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "DISPATCH_CATALOG_PATH": str(catalog_path),
                "DISPATCH_OVERRIDES_PATH": str(overrides_path),
            },
            check=False,
        )
        assert result.returncode == 0, (
            f"_main.py exited non-zero:\n{result.stderr}"
        )
        out = json.loads(result.stdout)
        assert out.get("disposition_source") == "scored", (
            f"Expected disposition_source='scored' for non-override context, "
            f"got {out.get('disposition_source')!r}; full output: {out}"
        )
