"""Tests for the mixed_content decision type added in #210.

Detection rule (all must hold):
1. Decision would otherwise be ``advisory`` (tie scenario: gap < 0.2).
2. At least 2 alternatives at score >= 1.0 - _MIXED_CONTENT_SCORE_EPSILON.
3. Each top alternative has non-zero path-glob contribution.
4. Matched paths partition cleanly — no path appears in two agents'
   ``matched_paths``.  Overlap → fall through to ``advisory``.

The output shape adds ``lanes`` (list of per-agent lane dicts) and
``unassigned_paths`` to the standard decision envelope.

Scoring note: ``_matched_glob_count`` counts distinct *globs* that match
at least one path (each glob counted once even if it hits many paths).
To produce score >= 1.0 via path globs alone the catalog must have
>= 3 globs that each match at least one input path
(3 * 0.4 = 1.2 → clamped to 1.0).  Tests use three per-extension
globs per agent so the score calculation is transparent from the docstring.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.test_match.conftest import (
    _catalog,
    _make_agent,
    _run,
)

# ===========================================================================
# Helpers
# ===========================================================================

# Three .py-extension globs and three .md-extension globs; each glob
# matches exactly one of the input paths listed in the test helpers below.
# With 3 matched globs the score formula gives: 0.4 * 3 = 1.2 → clamped
# to 1.0, which is the minimum needed to trigger the mixed_content path.
_CW_GLOBS = ["src/*.py", "src/tests/*.py", "lib/*.py"]
_DW_GLOBS = ["docs/*.md", "wiki/*.md", "CHANGELOG.md"]

# Disjoint path sets — one path per glob so both agents reach score 1.0.
_CW_PATHS = ["src/main.py", "src/tests/test_main.py", "lib/utils.py"]
_DW_PATHS = ["docs/api.md", "wiki/Home.md", "CHANGELOG.md"]

# Paths that match neither agent's globs.
_UNASSIGNED_PATHS = [".github/workflows/ci.yml", "Makefile"]


def _make_disjoint_catalog() -> dict:
    """Build a two-agent catalog where code-writer and doc-writer have
    completely separate path_globs.

    Returns:
        A catalog dict with schema_version and two agent entries.
    """
    return _catalog(
        [
            _make_agent("code-writer", path_globs=_CW_GLOBS),
            _make_agent("doc-writer", path_globs=_DW_GLOBS),
        ]
    )


def _mixed_input(extra_paths: list[str] | None = None) -> dict:
    """Build a dispatch context with paths that trigger both agents.

    Args:
        extra_paths: Additional paths appended after _CW_PATHS + _DW_PATHS.

    Returns:
        A context dict with task_description and file_paths.
    """
    paths = _CW_PATHS + _DW_PATHS + (extra_paths or [])
    return {
        "task_description": "update the project files",
        "file_paths": paths,
    }


# ===========================================================================
# Positive cases — should emit mixed_content
# ===========================================================================


class TestMixedContentPositive:
    """Cases that SHOULD emit ``mixed_content``."""

    def test_two_agents_tied_at_1_disjoint_paths(
        self, tmp_path: Path
    ) -> None:
        """Two agents clamped at 1.0 with disjoint paths → mixed_content.

        Score breakdown for each agent (3 globs each matching 1 path):
          code-writer: 0.4 * 3 = 1.2 → clamped to 1.0.
          doc-writer:  0.4 * 3 = 1.2 → clamped to 1.0.
          Gap = 1.0 - 1.0 = 0.0 < 0.2 → tie (formerly ``advisory``).
          Both have path-glob contribution; paths are disjoint.
          → must emit ``mixed_content``.
        """
        result = _run(
            _mixed_input(), _make_disjoint_catalog(), tmp_path=tmp_path
        )
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)

        assert out["decision"] == "mixed_content", (
            f"Expected 'mixed_content' for disjoint-lane tie at 1.0, "
            f"got {out['decision']!r}.\n"
            f"Full output: {json.dumps(out, indent=2)}"
        )

    def test_mixed_content_has_lanes_field(self, tmp_path: Path) -> None:
        """``mixed_content`` decision must include a ``lanes`` list.

        Each lane must have ``agent``, ``score``, ``matched_paths``, and
        ``skills`` fields.
        """
        result = _run(
            _mixed_input(), _make_disjoint_catalog(), tmp_path=tmp_path
        )
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out["decision"] == "mixed_content"

        assert "lanes" in out, (
            f"Missing 'lanes' field in: {json.dumps(out, indent=2)}"
        )
        lanes = out["lanes"]
        assert isinstance(lanes, list), (
            f"'lanes' should be a list, got {type(lanes)}"
        )
        assert len(lanes) >= 2, (
            f"Expected >= 2 lanes, got {len(lanes)}: {lanes}"
        )

        required_fields = {"agent", "score", "matched_paths", "skills"}
        for lane in lanes:
            missing = required_fields - set(lane)
            assert not missing, (
                f"Lane missing fields {missing}: {lane}"
            )

    def test_lanes_matched_paths_partitioned_correctly(
        self, tmp_path: Path
    ) -> None:
        """Lanes must contain the correct subset of input paths.

        code-writer should claim the three .py paths;
        doc-writer should claim the three .md paths.
        """
        result = _run(
            _mixed_input(), _make_disjoint_catalog(), tmp_path=tmp_path
        )
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out["decision"] == "mixed_content"

        lanes_by_agent = {
            lane["agent"]: lane["matched_paths"] for lane in out["lanes"]
        }
        assert "code-writer" in lanes_by_agent, (
            f"code-writer lane missing: {out['lanes']}"
        )
        assert "doc-writer" in lanes_by_agent, (
            f"doc-writer lane missing: {out['lanes']}"
        )

        assert set(lanes_by_agent["code-writer"]) == set(_CW_PATHS), (
            f"code-writer matched_paths wrong: "
            f"{lanes_by_agent['code-writer']}"
        )
        assert set(lanes_by_agent["doc-writer"]) == set(_DW_PATHS), (
            f"doc-writer matched_paths wrong: "
            f"{lanes_by_agent['doc-writer']}"
        )

    def test_unassigned_paths_surfaced(self, tmp_path: Path) -> None:
        """Paths not claimed by any top agent appear in ``unassigned_paths``.

        ``.github/workflows/ci.yml`` and ``Makefile`` match neither
        ``_CW_GLOBS`` nor ``_DW_GLOBS``, so they land in
        ``unassigned_paths``.
        """
        result = _run(
            _mixed_input(extra_paths=_UNASSIGNED_PATHS),
            _make_disjoint_catalog(),
            tmp_path=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out["decision"] == "mixed_content"

        assert "unassigned_paths" in out, (
            f"Missing 'unassigned_paths' in: {json.dumps(out, indent=2)}"
        )
        assert set(out["unassigned_paths"]) == set(_UNASSIGNED_PATHS), (
            f"unassigned_paths wrong: {out['unassigned_paths']}"
        )

    def test_alternatives_field_present_with_lower_scoring_agents(
        self, tmp_path: Path
    ) -> None:
        """``alternatives`` field must be present (may be empty or non-empty).

        A third agent scoring below threshold may appear; either way,
        ``alternatives`` key must exist for schema consistency.
        """
        catalog = _catalog(
            [
                _make_agent("code-writer", path_globs=_CW_GLOBS),
                _make_agent("doc-writer", path_globs=_DW_GLOBS),
                _make_agent(
                    "ops",
                    keywords=[{"term": "ci", "weight": 1.0}],
                ),
            ]
        )
        result = _run(_mixed_input(), catalog, tmp_path=tmp_path)
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out["decision"] == "mixed_content"

        assert "alternatives" in out, (
            f"Missing 'alternatives' in: {json.dumps(out, indent=2)}"
        )


# ===========================================================================
# Negative cases — must NOT emit mixed_content
# ===========================================================================


class TestMixedContentNegative:
    """Cases that must NOT emit ``mixed_content`` — fall through to other
    decisions."""

    def test_overlapping_paths_falls_through_to_advisory(
        self, tmp_path: Path
    ) -> None:
        """If any path matches both agents' globs → ``advisory``, not
        ``mixed_content``.

        Both agents have 3 globs that score 1.0, but the .py paths also
        match doc-writer's ``src/*.py`` glob.  At least one path
        (``src/main.py``) appears in both agents' matched sets → overlap
        detected → fall through to ``advisory``.
        """
        # doc-writer has globs that include src/*.py — overlapping with CW
        catalog = _catalog(
            [
                _make_agent("code-writer", path_globs=_CW_GLOBS),
                _make_agent(
                    "doc-writer",
                    # Three globs that together match all 6 input paths:
                    # src/*.py, docs/*.md, wiki/*.md, CHANGELOG.md
                    # Because src/*.py overlaps with code-writer, the
                    # partition is NOT clean.
                    path_globs=["src/*.py", "docs/*.md", "wiki/*.md"],
                ),
            ]
        )
        # Both agents score 1.0: CW gets src/*.py, src/tests/*.py, lib/*.py;
        # DW gets src/*.py, docs/*.md, wiki/*.md.  src/main.py appears in
        # both → overlap detected.
        stdin_obj = {
            "task_description": "update the project files",
            "file_paths": [
                "src/main.py",       # matches CW src/*.py AND DW src/*.py
                "src/tests/test_main.py",  # matches CW src/tests/*.py
                "lib/utils.py",            # matches CW lib/*.py
                "docs/api.md",             # matches DW docs/*.md
                "wiki/Home.md",            # matches DW wiki/*.md
            ],
        }
        result = _run(stdin_obj, catalog, tmp_path=tmp_path)
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)

        assert out["decision"] != "mixed_content", (
            f"Overlapping paths must not produce 'mixed_content':\n"
            f"{json.dumps(out, indent=2)}"
        )
        assert out["decision"] == "advisory", (
            f"Expected 'advisory' for overlapping-path tie, "
            f"got {out['decision']!r}"
        )

    def test_only_one_agent_at_score_1_returns_delegate(
        self, tmp_path: Path
    ) -> None:
        """Single agent at 1.0, other at 0 → ``delegate``, not ``mixed_content``.

        code-writer scores 1.0 (all three CW paths match its three globs);
        doc-writer scores 0.0 (no DW paths provided).
        Gap = 1.0 - 0.0 >= 0.2 and score >= 0.85 → ``delegate``.
        """
        result = _run(
            {
                "task_description": "update the project files",
                "file_paths": _CW_PATHS,  # only .py paths — no .md
            },
            _make_disjoint_catalog(),
            tmp_path=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)

        assert out["decision"] == "delegate", (
            f"Expected 'delegate' (single winner), "
            f"got {out['decision']!r}"
        )
        assert out.get("agent") == "code-writer"

    def test_tied_agents_without_path_glob_contribution_emits_advisory(
        self, tmp_path: Path
    ) -> None:
        """Tied agents via keywords only (zero path-glob) → ``advisory``.

        Both agents score 0.5 via the same keyword; neither has path_globs.
        Condition 3 (non-zero path-glob contribution) fails → fall through
        to ``advisory``.
        """
        catalog = _catalog(
            [
                _make_agent(
                    "code-writer",
                    keywords=[{"term": "implement", "weight": 1.0}],
                    path_globs=[],
                ),
                _make_agent(
                    "doc-writer",
                    keywords=[{"term": "implement", "weight": 1.0}],
                    path_globs=[],
                ),
            ]
        )
        result = _run(
            {
                "task_description": "implement the feature",
                "tool_mentions": ["git"],  # ensures feature_count >= 2
            },
            catalog,
            tmp_path=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)

        assert out["decision"] != "mixed_content", (
            f"Pure keyword tie must not produce 'mixed_content':\n"
            f"{json.dumps(out, indent=2)}"
        )
        assert out["decision"] == "advisory", (
            f"Expected 'advisory' for keyword-only tie, "
            f"got {out['decision']!r}"
        )

    def test_second_agent_below_epsilon_threshold_returns_delegate(
        self, tmp_path: Path
    ) -> None:
        """Second agent below score threshold → ``delegate``, not ``mixed_content``.

        code-writer: 3 globs * 0.4 = 1.2 → clamped to 1.0.
        doc-writer: 1 glob * 0.4 = 0.4 (below 1.0 - 0.05 = 0.95).
        Gap = 0.6 >= 0.2 and cw score >= 0.85 → ``delegate``.
        """
        catalog = _catalog(
            [
                _make_agent("code-writer", path_globs=_CW_GLOBS),
                _make_agent(
                    "doc-writer",
                    path_globs=["docs/*.md"],  # only 1 glob
                ),
            ]
        )
        result = _run(
            {
                "task_description": "update the project files",
                "file_paths": _CW_PATHS + ["docs/api.md"],  # 1 md path
            },
            catalog,
            tmp_path=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)

        assert out["decision"] == "delegate", (
            f"Expected 'delegate' (second agent below epsilon), "
            f"got {out['decision']!r}. "
            f"Output: {json.dumps(out, indent=2)}"
        )
        assert out.get("agent") == "code-writer"
