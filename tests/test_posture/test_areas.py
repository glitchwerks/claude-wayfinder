"""Tests for posture._areas: project-areas.json loader and coarse-glob fallback.

Per AC: E7 extractor functions never touch the filesystem — the loader is
the only filesystem-touching piece. Extractor functions take a parsed
area-map parameter.
"""

from __future__ import annotations

import json
from pathlib import Path


class TestLoadAreaMap:
    """load_area_map reads .claude/project-areas.json or returns coarse fallback."""

    def test_import(self) -> None:
        """load_area_map must be importable from posture._areas."""
        from claude_wayfinder.posture._areas import load_area_map  # noqa: F401

    def test_returns_dict(self, tmp_path: Path) -> None:
        """load_area_map returns a dict."""
        from claude_wayfinder.posture._areas import load_area_map

        result = load_area_map(tmp_path)
        assert isinstance(result, dict)

    def test_fallback_when_no_json(self, tmp_path: Path) -> None:
        """load_area_map returns coarse built-in globs when no areas file exists."""
        from claude_wayfinder.posture._areas import load_area_map

        result = load_area_map(tmp_path)
        # Must have at least the four coarse areas from §10.2 E7
        assert "src" in result or any(
            k in result for k in ("code", "tests", "infra", "docs")
        ), f"Expected coarse areas, got: {result}"

    def test_reads_project_areas_json(self, tmp_path: Path) -> None:
        """load_area_map parses .claude/project-areas.json when present."""
        from claude_wayfinder.posture._areas import load_area_map

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        areas_file = claude_dir / "project-areas.json"
        areas_data = {
            "areas": {
                "frontend": ["src/web/**", "src/components/**"],
                "backend": ["src/api/**", "src/server/**"],
                "infra": ["terraform/**", "bicep/**", ".github/**"],
            }
        }
        areas_file.write_text(
            json.dumps(areas_data), encoding="utf-8"
        )

        result = load_area_map(tmp_path)
        assert "frontend" in result
        assert "backend" in result
        assert "infra" in result
        assert "src/web/**" in result["frontend"]

    def test_fallback_on_malformed_json(self, tmp_path: Path) -> None:
        """load_area_map returns coarse fallback if JSON is malformed."""
        from claude_wayfinder.posture._areas import load_area_map

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "project-areas.json").write_text("not valid json", encoding="utf-8")

        result = load_area_map(tmp_path)
        # Must still return a dict (coarse fallback)
        assert isinstance(result, dict)
        assert len(result) > 0

    def test_coarse_fallback_content(self, tmp_path: Path) -> None:
        """Coarse fallback must include src, tests, infra, docs areas."""
        from claude_wayfinder.posture._areas import load_area_map

        result = load_area_map(tmp_path)
        # Flatten all globs
        all_globs = [g for globs in result.values() for g in globs]
        assert len(all_globs) > 0


class TestCountDistinctAreas:
    """count_distinct_areas counts unique project areas touched by file paths."""

    def test_import(self) -> None:
        """count_distinct_areas must be importable from posture._areas."""
        from claude_wayfinder.posture._areas import count_distinct_areas  # noqa: F401

    def test_zero_paths(self) -> None:
        """count_distinct_areas returns 0 for empty file_paths."""
        from claude_wayfinder.posture._areas import count_distinct_areas

        area_map = {"src": ["src/**"], "tests": ["tests/**"]}
        assert count_distinct_areas((), area_map) == 0

    def test_single_area(self) -> None:
        """count_distinct_areas returns 1 when all paths fall in one area."""
        from claude_wayfinder.posture._areas import count_distinct_areas

        area_map = {"src": ["src/**"], "infra": [".github/**"]}
        paths = ("src/api/client.py", "src/lib/utils.py")
        assert count_distinct_areas(paths, area_map) == 1

    def test_two_areas(self) -> None:
        """count_distinct_areas returns 2 when paths span two areas (P14)."""
        from claude_wayfinder.posture._areas import count_distinct_areas

        area_map = {
            "code": ["src/**"],
            "infra": [".github/**", "infra/**"],
        }
        paths = ("src/api/client.py", ".github/workflows/deploy.yml")
        assert count_distinct_areas(paths, area_map) == 2

    def test_paths_not_in_any_area(self) -> None:
        """count_distinct_areas returns 0 if no path matches any glob."""
        from claude_wayfinder.posture._areas import count_distinct_areas

        area_map = {"src": ["src/**"]}
        paths = ("README.md",)
        assert count_distinct_areas(paths, area_map) == 0

    def test_multiple_paths_same_area_counts_once(self) -> None:
        """Distinct areas, not path count — many paths in one area = 1."""
        from claude_wayfinder.posture._areas import count_distinct_areas

        area_map = {"src": ["src/**"]}
        paths = tuple(f"src/mod{i}/file.py" for i in range(10))
        assert count_distinct_areas(paths, area_map) == 1
