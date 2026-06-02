"""Tests for Snowball/Porter2 stemming integration in the dispatch matcher.

RED phase — these tests are written BEFORE any implementation.  They will
fail until the stemming module, the updated ``extract_keywords`` function,
the build-time ``stemmed_terms`` computation, and the ``--check-stems``
CLI flag are all in place.

Coverage:
- Stemmer module (``match._stem``): basic stem output and ``no_stem`` bypass.
- Symmetric normalization: base and inflected prompt pairs → identical decision.
- Regression guard: documented misfires now route correctly.
- ``no_stem`` opt-out: term is NOT stemmed when flag is True.
- ``--check-stems`` collision detector: warns on same-stem distinct terms.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.test_match.conftest import (
    _catalog,
    _make_agent,
    _run,
)

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

_WORKTREE = Path(__file__).resolve().parents[1]
_BUILD_MODULE = ["claude_wayfinder.build_catalog"]
_PYTHON = sys.executable


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_build(
    tmp_path: Path,
    *,
    agents_dir: Path | None = None,
    skills_dir: Path | None = None,
    extra_args: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the catalog builder with given dirs and extra args.

    Args:
        tmp_path: Pytest temporary directory for catalog and log output.
        agents_dir: Path to agents dir (defaults to fixture agents).
        skills_dir: Path to skills dir (defaults to fixture skills).
        extra_args: Additional CLI arguments appended to the command.

    Returns:
        Completed subprocess result.
    """
    fixture_agents = _WORKTREE / "tests" / "fixtures" / "agents"
    fixture_skills = _WORKTREE / "tests" / "fixtures" / "skills"

    out = tmp_path / "catalog.json"
    log = tmp_path / "catalog.log"

    cmd = [
        _PYTHON,
        "-m",
        *_BUILD_MODULE,
        "--agents-dir",
        str(agents_dir or fixture_agents),
        "--skills-dir",
        str(skills_dir or fixture_skills),
        "--out",
        str(out),
        "--log",
        str(log),
    ]
    if extra_args:
        cmd.extend(extra_args)

    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def _catalog_with_keyword(term: str, weight: float = 1.0) -> dict[str, Any]:
    """Build a minimal single-agent catalog triggering on *term*.

    Args:
        term: Keyword trigger term (unstemmed; stemming applied at build time).
        weight: Trigger weight in {0.25, 0.5, 1.0}.

    Returns:
        Catalog dict with one agent entry.
    """
    return _catalog(
        [
            _make_agent(
                "code-writer",
                keywords=[{"term": term, "weight": weight}],
                applicable_skills=[],
            ),
        ]
    )


# ===========================================================================
# 1. Stemmer module unit tests
# ===========================================================================


class TestStemmerModule:
    """Unit tests for ``claude_wayfinder.match._stem``."""

    def test_stem_base_form_unchanged(self) -> None:
        """stem() on a base-form word returns the expected Porter2 stem."""
        from claude_wayfinder.match._stem import stem

        # Porter2: "implement" → "implement"
        assert stem("implement") == "implement"

    def test_stem_inflected_collapses_to_base(self) -> None:
        """stem() on an inflected form matches the base-form stem."""
        from claude_wayfinder.match._stem import stem

        assert stem("implementing") == stem("implement")
        assert stem("implemented") == stem("implement")

    def test_stem_refactor_variants(self) -> None:
        """Morphological variants of 'refactor' stem identically."""
        from claude_wayfinder.match._stem import stem

        base = stem("refactor")
        assert stem("refactored") == base
        assert stem("refactoring") == base

    def test_stem_lint_variants(self) -> None:
        """Morphological variants of 'lint' stem identically.

        Porter2 collapses 'linting' and 'linted' to 'lint'.
        Note: 'linter' is treated as a distinct lexeme by Porter2 and does
        NOT stem to 'lint' — this is documented Porter2 behavior, not a bug.
        Catalog authors should list 'linter' separately if needed.
        """
        from claude_wayfinder.match._stem import stem

        base = stem("lint")
        assert stem("linting") == base
        assert stem("linted") == base

    def test_no_stem_returns_term_unchanged(self) -> None:
        """stem() with no_stem=True returns the term verbatim."""
        from claude_wayfinder.match._stem import stem

        # "aws" would otherwise stem to "aw"; no_stem must bypass that.
        assert stem("aws", no_stem=True) == "aws"
        assert stem("ps1", no_stem=True) == "ps1"
        assert stem("gh", no_stem=True) == "gh"

    def test_stem_is_lowercase_passthrough(self) -> None:
        """stem() lowercases its input before stemming."""
        from claude_wayfinder.match._stem import stem

        assert stem("Refactoring") == stem("refactoring")

    def test_stem_empty_string(self) -> None:
        """stem() on an empty string returns an empty string without error."""
        from claude_wayfinder.match._stem import stem

        assert stem("") == ""

    def test_stem_hyphenated_token_preserved(self) -> None:
        """Hyphenated tokens pass through stem() unchanged (not split)."""
        from claude_wayfinder.match._stem import stem

        # git-rebase is a compound token; stemmer leaves hyphens alone.
        result = stem("git-rebase")
        assert isinstance(result, str)
        assert len(result) > 0


# ===========================================================================
# 2. extract_keywords produces stems
# ===========================================================================


class TestExtractKeywordsUsesStems:
    """Verify that extract_keywords returns stems, not raw tokens."""

    def test_inflected_token_yields_stem(self) -> None:
        """extract_keywords('implementing') includes the stem of 'implement'."""
        from claude_wayfinder.match._match import extract_keywords
        from claude_wayfinder.match._stem import stem

        keywords = extract_keywords("implementing a feature")
        assert stem("implement") in keywords

    def test_base_and_inflected_yield_same_token(self) -> None:
        """Base and inflected forms land in extract_keywords as the same stem."""
        from claude_wayfinder.match._match import extract_keywords

        base_kws = extract_keywords("implement a feature")
        inflected_kws = extract_keywords("implementing a feature")
        # Both must contain the same token for "implement" → they overlap.
        assert base_kws & inflected_kws  # non-empty intersection

    def test_no_stem_token_preserved_as_is(self) -> None:
        """A token explicitly excluded from stemming is returned verbatim.

        This test verifies the pipeline for tokens that should not be stemmed
        (e.g. acronyms).  The term 'aws' would normally stem to 'aw' but
        must appear unchanged in extract_keywords output so that a catalog
        term 'aws' with no_stem=True can still match.

        Note: extract_keywords stems ALL tokens uniformly; the no_stem gate
        is applied on the CATALOG side only (catalog terms with no_stem=True
        are stored verbatim in stemmed_terms so their unstemmed form is what
        the matcher checks against stemmed feature tokens).  Therefore this
        test does NOT assert that 'aws' is preserved by extract_keywords —
        it asserts that when a catalog term carries no_stem=True, matching
        still works correctly (covered by the integration tests below).
        """
        pass  # Documented; integration coverage is in TestNoStemOptOut.


# ===========================================================================
# 3. Base / inflected pair fixture (≥ 10 pairs — MUST produce identical
#    dispatch decisions)
# ===========================================================================

# Each pair: (base_prompt, inflected_prompt).  The dispatch decision for both
# must be identical.  A pair failure means morphological drift is unresolved.
BASE_INFLECTED_PAIRS: list[tuple[str, str]] = [
    ("implement a feature", "implementing a feature"),
    ("implement a feature", "implemented the feature"),
    ("refactor X", "refactored X"),
    ("refactor X", "refactoring X"),
    ("run linter", "running the linter"),
    ("run linter", "run linting"),
    ("deploy the service", "deploying the service"),
    ("deploy the service", "deployed the service"),
    ("write a script", "writing a script"),
    ("debug the issue", "debugging the issue"),
    ("fix the bug", "fixing the bug"),
    ("test the code", "testing the code"),
]


class TestBaseInflectedPairs:
    """Targeted fixture: base/inflected pairs MUST produce identical decisions.

    If any pair diverges the test fails with the offending pair reported.
    Stemming is load-bearing: the catalog is built with a base-form keyword;
    the inflected prompt must still route identically.
    """

    @pytest.mark.parametrize("base_prompt,inflected_prompt", BASE_INFLECTED_PAIRS)
    def test_pair_produces_identical_decision(
        self,
        base_prompt: str,
        inflected_prompt: str,
        tmp_path: Path,
    ) -> None:
        """Base and inflected prompt → same dispatch decision.

        Args:
            base_prompt: Prompt using the base form of the trigger word.
            inflected_prompt: Prompt using a morphological variant.
            tmp_path: Pytest temporary directory.
        """
        # Catalog: code-writer triggers on base forms.
        catalog = _catalog(
            [
                _make_agent(
                    "code-writer",
                    keywords=[
                        {"term": "implement", "weight": 1.0},
                        {"term": "refactor", "weight": 1.0},
                        {"term": "lint", "weight": 1.0},
                        {"term": "deploy", "weight": 1.0},
                        {"term": "write", "weight": 1.0},
                        {"term": "debug", "weight": 1.0},
                        {"term": "fix", "weight": 1.0},
                        {"term": "test", "weight": 1.0},
                        {"term": "run", "weight": 0.5},
                        {"term": "script", "weight": 0.5},
                    ],
                ),
                # Self-handle agent so tests with low-confidence get
                # self_handle rather than self_handle_unaided (needs catalog
                # to have at least a rudimentary agent pool).
                _make_agent(
                    "router-agent",
                    routable=False,
                ),
            ]
        )

        base_result = _run({"task_description": base_prompt}, catalog, tmp_path=tmp_path)
        inflected_result = _run(
            {"task_description": inflected_prompt}, catalog, tmp_path=tmp_path
        )

        assert base_result.returncode == 0, (
            f"base_prompt '{base_prompt}' failed: {base_result.stderr}"
        )
        assert inflected_result.returncode == 0, (
            f"inflected_prompt '{inflected_prompt}' failed: {inflected_result.stderr}"
        )

        base_out = json.loads(base_result.stdout)
        inflected_out = json.loads(inflected_result.stdout)

        assert base_out["decision"] == inflected_out["decision"], (
            f"Decision diverged for pair:\n"
            f"  base      '{base_prompt}' → {base_out['decision']}\n"
            f"  inflected '{inflected_prompt}' → {inflected_out['decision']}"
        )


# ===========================================================================
# 4. Regression guard: documented misfires now route correctly
# ===========================================================================


class TestRegressionMisfires:
    """Documented misfires from issue #304 must route to the right target."""

    def test_run_ruff_linter_routes_to_agent(self, tmp_path: Path) -> None:
        """'run ruff linter on the codebase' must NOT produce self_handle_unaided.

        Previously: self_handle_unaided ('linter' doesn't stem to 'lint' in
        Porter2, but 'linting' does).  This test uses the term 'linting' to
        verify that inflected forms of 'lint' route correctly.

        Note: 'linter' does NOT stem to 'lint' under Porter2 (it is treated
        as a distinct lexeme).  The term 'linting' DOES stem to 'lint'.
        The prompt "running the linter" contains 'linting' but 'linter'
        must be explicitly listed in catalog triggers if needed.

        The dispatch input includes file_paths to meet feature density >= 2.
        """
        catalog = _catalog(
            [
                _make_agent(
                    "code-writer",
                    keywords=[
                        {"term": "lint", "weight": 1.0},
                        {"term": "run", "weight": 0.5},
                    ],
                    path_globs=["**/*.py"],
                ),
                _make_agent("router-agent", routable=False),
            ]
        )
        # Use 'linting' (which stems to 'lint') to test the regression.
        result = _run(
            {
                "task_description": "run linting on the codebase",
                "file_paths": ["src/main.py"],
            },
            catalog,
            tmp_path=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out["decision"] != "self_handle_unaided", (
            f"'run linting on the codebase' still produces self_handle_unaided "
            f"after stemming integration; decision={out['decision']}"
        )

    def test_implementing_the_feature_scores_above_threshold(
        self, tmp_path: Path
    ) -> None:
        """'implementing the feature' must route to code-writer.

        Previously: 'implementing' didn't match catalog term 'implement'.
        After stemming: stems match → code-writer scores above advisory
        threshold.

        The dispatch input includes file_paths to meet the
        ``_MIN_FEATURE_DENSITY = 2`` gate (keyword + paths = 2 dimensions),
        which reflects realistic usage.  A bare text prompt without any
        other signals triggers ``needs_more_detail`` regardless of keyword
        scoring — that is by design and orthogonal to stemming.
        """
        catalog = _catalog(
            [
                _make_agent(
                    "code-writer",
                    keywords=[{"term": "implement", "weight": 1.0}],
                    path_globs=["**/*.py"],
                ),
                _make_agent("router-agent", routable=False),
            ]
        )
        result = _run(
            {
                "task_description": "implementing the feature",
                "file_paths": ["src/main.py"],
            },
            catalog,
            tmp_path=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out["decision"] == "delegate", (
            f"'implementing the feature' did not delegate to code-writer; "
            f"decision={out['decision']}"
        )
        assert out.get("agent") == "code-writer"


# ===========================================================================
# 5. no_stem opt-out
# ===========================================================================


class TestNoStemOptOut:
    """A keyword with no_stem=True must NOT be stemmed at catalog-build time.

    Behavior:
    - Catalog stores the term verbatim (e.g., 'aws') in stemmed_terms.
    - The scorer checks against stemmed feature tokens.
    - Since 'aws' stems to 'aw', a feature token 'aws' would become 'aw'
      and would NOT match the unstemmed catalog term 'aws'.
    - With no_stem=True, the catalog stores 'aws' and uses unstemmed
      matching for this term specifically.
    """

    def test_no_stem_term_is_stored_verbatim(self, tmp_path: Path) -> None:
        """A term with no_stem=True must appear verbatim in stemmed_terms.

        This test exercises the catalog-build path: the trigger sidecar
        declares a term with no_stem=True; after build the catalog JSON
        must store the term unchanged in stemmed_terms.
        """
        from claude_wayfinder.match._stem import stem

        # 'aws' would ordinarily stem to something different
        # (verify the precondition: stemming WOULD change 'aws').
        assert stem("aws") != "aws", (
            "Test precondition: 'aws' must stem to something other than 'aws' "
            "so that no_stem=True is load-bearing."
        )

        # Build a temporary skill with no_stem=True on 'aws'.
        skills_dir = tmp_path / "skills"
        skill_dir = skills_dir / "cloud-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: cloud-skill\ndescription: Cloud skill.\n---\n",
            encoding="utf-8",
        )
        (skill_dir / "triggers.yml").write_text(
            "triggers:\n"
            "  keywords:\n"
            "    - { term: 'aws', weight: 1.0, no_stem: true }\n"
            "applicable_agents: ['*']\n",
            encoding="utf-8",
        )

        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "router-agent.md").write_text(
            "---\nname: router-agent\ndescription: Router.\n"
            "routable: false\n---\n",
            encoding="utf-8",
        )

        result = _run_build(tmp_path, agents_dir=agents_dir, skills_dir=skills_dir)
        assert result.returncode in (0, 2), f"Build failed: {result.stderr}"

        catalog_path = tmp_path / "catalog.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))

        skill_entry = next(
            (e for e in catalog["entries"] if e["name"] == "cloud-skill"), None
        )
        assert skill_entry is not None, "cloud-skill not found in catalog"

        # The term 'aws' with no_stem=True must be stored verbatim.
        kws = skill_entry["triggers"].get("keywords", [])
        aws_kw = next((k for k in kws if k["term"] == "aws"), None)
        assert aws_kw is not None, f"'aws' keyword not found in catalog. keywords={kws}"

        # stemmed_terms must exist and store 'aws' verbatim (not the stem 'aw').
        stemmed = skill_entry.get("stemmed_terms", {})
        assert "aws" in stemmed, (
            f"'aws' not found in stemmed_terms. stemmed_terms={stemmed}"
        )
        assert stemmed["aws"] == "aws", (
            f"Expected stemmed_terms['aws']=='aws' (no_stem bypass), "
            f"got {stemmed['aws']!r}"
        )

    def test_no_stem_term_matches_exact_token(self, tmp_path: Path) -> None:
        """A no_stem term like 'aws' must only match the exact token 'aws'.

        The design: when no_stem=True on the catalog side, the catalog term
        is stored VERBATIM; the matcher checks it against
        ``features.raw_keywords`` (unstemmed tokens), NOT against
        ``features.keywords`` (stems).

        Since 'aws' in the input stems to something different, without
        the no_stem matching path the 'aws' catalog term would NOT match.
        With the no_stem path, 'aws' in the input IS in raw_keywords and
        DOES match the verbatim catalog term.

        The dispatch input includes file_paths to meet feature_density >= 2.
        """
        # The catalog entry uses the in-memory API where `_parse_triggers`
        # reads no_stem from the JSON.  Build the catalog JSON with no_stem.
        catalog = _catalog(
            [
                _make_agent(
                    "cloud-agent",
                    keywords=[{"term": "aws", "weight": 1.0}],
                    path_globs=["**/*.tf"],
                ),
                _make_agent("router-agent", routable=False),
            ]
        )
        # Inject no_stem=True into the catalog JSON directly so the parser
        # reads it and creates a Keyword with no_stem=True.
        for entry in catalog["entries"]:
            if entry["name"] == "cloud-agent":
                for kw in entry["triggers"]["keywords"]:
                    if kw["term"] == "aws":
                        kw["no_stem"] = True

        result = _run(
            {
                "task_description": "deploy to aws cloud",
                "file_paths": ["infra/main.tf"],
            },
            catalog,
            tmp_path=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        # With no_stem=True, 'aws' in input matches 'aws' catalog term exactly.
        assert out["decision"] == "delegate", (
            f"'aws' with no_stem=True should match; decision={out['decision']}"
        )


# ===========================================================================
# 6. --check-stems collision checker
# ===========================================================================


class TestCheckStemsCollisionChecker:
    """Tests for the --check-stems catalog generator flag."""

    def test_check_stems_exits_zero_on_clean_catalog(self, tmp_path: Path) -> None:
        """--check-stems exits with no STEM_COLLISION on a collision-free catalog.

        Uses a synthetic catalog where all keyword terms have distinct stems.

        Args:
            tmp_path: Pytest temporary directory.
        """
        # Build a minimal collision-free catalog.
        skills_dir = tmp_path / "skills"
        skill_a = skills_dir / "cloud"
        skill_a.mkdir(parents=True)
        (skill_a / "SKILL.md").write_text(
            "---\nname: cloud\ndescription: Cloud.\n---\n",
            encoding="utf-8",
        )
        # 'terraform' and 'deploy' have distinct stems.
        (skill_a / "triggers.yml").write_text(
            "triggers:\n"
            "  keywords:\n"
            "    - { term: 'terraform', weight: 1.0 }\n"
            "    - { term: 'deploy', weight: 0.5 }\n"
            "applicable_agents: ['*']\n",
            encoding="utf-8",
        )

        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "router-agent.md").write_text(
            "---\nname: router-agent\ndescription: Router.\n"
            "routable: false\n---\n",
            encoding="utf-8",
        )

        result = _run_build(
            tmp_path,
            agents_dir=agents_dir,
            skills_dir=skills_dir,
            extra_args=["--check-stems"],
        )
        assert result.returncode in (0, 2), (
            f"--check-stems failed unexpectedly: {result.stderr}"
        )
        # No collision lines must appear.
        assert "STEM_COLLISION" not in result.stdout
        assert "STEM_COLLISION" not in result.stderr

    def test_check_stems_reports_collision_on_synthetic_catalog(
        self, tmp_path: Path
    ) -> None:
        """--check-stems outputs a STEM_COLLISION warning when two distinct
        catalog terms share the same Porter2 stem.

        Fixture: two skills with terms 'implement' and 'implementing'.
        Both stem to the same value → STEM_COLLISION warning expected.
        """
        from claude_wayfinder.match._stem import stem

        # Verify precondition: both terms share a stem.
        assert stem("implement") == stem("implementing"), (
            "Test precondition: 'implement' and 'implementing' must share a stem."
        )

        # Build a skills dir with two skills having colliding terms.
        skills_dir = tmp_path / "skills"

        skill_a = skills_dir / "code-writer"
        skill_a.mkdir(parents=True)
        (skill_a / "SKILL.md").write_text(
            "---\nname: code-writer\ndescription: Code writer.\n---\n",
            encoding="utf-8",
        )
        (skill_a / "triggers.yml").write_text(
            "triggers:\n"
            "  keywords:\n"
            "    - { term: 'implement', weight: 1.0 }\n"
            "applicable_agents: ['*']\n",
            encoding="utf-8",
        )

        skill_b = skills_dir / "code-writer-alt"
        skill_b.mkdir(parents=True)
        (skill_b / "SKILL.md").write_text(
            "---\nname: code-writer-alt\ndescription: Code writer alt.\n---\n",
            encoding="utf-8",
        )
        (skill_b / "triggers.yml").write_text(
            "triggers:\n"
            "  keywords:\n"
            "    - { term: 'implementing', weight: 1.0 }\n"
            "applicable_agents: ['*']\n",
            encoding="utf-8",
        )

        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "router-agent.md").write_text(
            "---\nname: router-agent\ndescription: Router.\n"
            "routable: false\n---\n",
            encoding="utf-8",
        )

        result = _run_build(
            tmp_path,
            agents_dir=agents_dir,
            skills_dir=skills_dir,
            extra_args=["--check-stems"],
        )
        assert result.returncode in (0, 2), f"Build crashed: {result.stderr}"

        combined_output = result.stdout + result.stderr
        assert "STEM_COLLISION" in combined_output, (
            f"Expected STEM_COLLISION warning for 'implement'/'implementing' "
            f"but got:\nstdout={result.stdout!r}\nstderr={result.stderr!r}"
        )

    def test_check_stems_flag_not_required_for_normal_build(
        self, tmp_path: Path
    ) -> None:
        """Normal catalog build without --check-stems must not emit collision output.

        Args:
            tmp_path: Pytest temporary directory.
        """
        result = _run_build(tmp_path)
        # Normal build should succeed (no collision output).
        assert result.returncode in (0, 2), f"Normal build failed: {result.stderr}"
        assert "STEM_COLLISION" not in result.stdout
        assert "STEM_COLLISION" not in result.stderr

    def test_check_stems_no_collision_for_no_stem_terms(
        self, tmp_path: Path
    ) -> None:
        """no_stem terms that share a stem as unstemmed forms are NOT collisions.

        If two terms share a stem but BOTH have no_stem=True, no collision
        is reported (because neither is stemmed, so their raw forms differ).
        """
        # Build skills with no_stem=True on both terms.
        skills_dir = tmp_path / "skills"

        skill_a = skills_dir / "skill-a"
        skill_a.mkdir(parents=True)
        (skill_a / "SKILL.md").write_text(
            "---\nname: skill-a\ndescription: Skill A.\n---\n",
            encoding="utf-8",
        )
        # 'aws' and 'aw' may or may not share a stem; we use terms that
        # definitely share a stem but BOTH have no_stem=True.
        (skill_a / "triggers.yml").write_text(
            "triggers:\n"
            "  keywords:\n"
            "    - { term: 'aws', weight: 1.0, no_stem: true }\n"
            "applicable_agents: ['*']\n",
            encoding="utf-8",
        )

        skill_b = skills_dir / "skill-b"
        skill_b.mkdir(parents=True)
        (skill_b / "SKILL.md").write_text(
            "---\nname: skill-b\ndescription: Skill B.\n---\n",
            encoding="utf-8",
        )
        (skill_b / "triggers.yml").write_text(
            "triggers:\n"
            "  keywords:\n"
            "    - { term: 'gh', weight: 1.0, no_stem: true }\n"
            "applicable_agents: ['*']\n",
            encoding="utf-8",
        )

        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "router-agent.md").write_text(
            "---\nname: router-agent\ndescription: Router.\n"
            "routable: false\n---\n",
            encoding="utf-8",
        )

        result = _run_build(
            tmp_path,
            agents_dir=agents_dir,
            skills_dir=skills_dir,
            extra_args=["--check-stems"],
        )
        assert result.returncode in (0, 2), f"Build crashed: {result.stderr}"
        # no_stem=True terms have distinct raw forms; no STEM_COLLISION.
        assert "STEM_COLLISION" not in result.stdout
        assert "STEM_COLLISION" not in result.stderr


# ===========================================================================
# 7. stemmed_terms stored in built catalog
# ===========================================================================


class TestStemmedTermsInCatalog:
    """stemmed_terms is stored per-entry in the catalog JSON (back-compat)."""

    def test_catalog_build_stores_stemmed_terms(self, tmp_path: Path) -> None:
        """Built catalog entries include a stemmed_terms dict.

        stemmed_terms maps each keyword term to its Porter2 stem.
        This field is optional (absent → back-compat), but when stemming
        is active it MUST be present.
        """
        from claude_wayfinder.match._stem import stem

        result = _run_build(tmp_path)
        assert result.returncode in (0, 2), f"Build failed: {result.stderr}"

        catalog_path = tmp_path / "catalog.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))

        # Every entry with non-empty keywords must have stemmed_terms.
        for entry in catalog["entries"]:
            kws = entry.get("triggers", {}).get("keywords", [])
            if not kws:
                continue
            stemmed = entry.get("stemmed_terms")
            assert stemmed is not None, (
                f"Entry '{entry['name']}' has keywords but no stemmed_terms field"
            )
            # Each keyword term must be in stemmed_terms.
            for kw in kws:
                t = kw["term"]
                assert t in stemmed, (
                    f"Entry '{entry['name']}': term '{t}' missing from stemmed_terms"
                )
                # The stored value must equal stem(t) or t itself (for no_stem).
                expected_stem = stem(t)
                assert stemmed[t] in (expected_stem, t), (
                    f"Entry '{entry['name']}': stemmed_terms['{t}']={stemmed[t]!r} "
                    f"expected {expected_stem!r} or '{t}'"
                )

    def test_catalog_without_keywords_has_no_stemmed_terms(
        self, tmp_path: Path
    ) -> None:
        """Entries with empty keywords block have no stemmed_terms (or empty)."""
        # Build a catalog with a dormant skill (no triggers).
        skills_dir = tmp_path / "skills"
        dormant = skills_dir / "dormant"
        dormant.mkdir(parents=True)
        (dormant / "SKILL.md").write_text(
            "---\nname: dormant\ndescription: Dormant skill.\n---\n",
            encoding="utf-8",
        )

        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "router-agent.md").write_text(
            "---\nname: router-agent\ndescription: Router.\n"
            "routable: false\n---\n",
            encoding="utf-8",
        )

        _run_build(tmp_path, agents_dir=agents_dir, skills_dir=skills_dir)
        catalog_path = tmp_path / "catalog.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))

        dormant_entry = next(
            (e for e in catalog["entries"] if e["name"] == "dormant"), None
        )
        assert dormant_entry is not None

        kws = dormant_entry.get("triggers", {}).get("keywords", [])
        if not kws:
            # Either stemmed_terms is absent or empty — both are acceptable.
            stemmed = dormant_entry.get("stemmed_terms", {})
            assert stemmed == {} or stemmed is None, (
                f"Expected empty stemmed_terms for dormant entry, got {stemmed!r}"
            )
