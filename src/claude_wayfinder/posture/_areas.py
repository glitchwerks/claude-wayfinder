"""Project-areas loader and area-span counter for E7 (area_span).

The filesystem-touching code lives here and NOWHERE else in the posture
library.  All extractor functions in ``_extractors.py`` receive a
pre-loaded ``area_map`` dict so they never touch the filesystem
themselves (purity requirement in the AC).

Two public functions:

- ``load_area_map(project_root)`` — reads ``<root>/.claude/project-areas.json``
  when present; returns the coarse built-in fallback otherwise.
- ``count_distinct_areas(file_paths, area_map)`` — counts how many distinct
  area keys have at least one path matching their globs.  Pure; no I/O.
"""

from __future__ import annotations

import fnmatch
import json
import logging
from pathlib import Path

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Coarse built-in fallback area map (§10.2 E7)
#
# Used when .claude/project-areas.json is absent or malformed.
# Mirrors the §9.2 Project Areas example from CLAUDE.md.
# ---------------------------------------------------------------------------

_COARSE_AREA_MAP: dict[str, list[str]] = {
    "code": [
        "src/**",
        "lib/**",
        "pkg/**",
        "app/**",
    ],
    "tests": [
        "tests/**",
        "test/**",
        "spec/**",
        "__tests__/**",
    ],
    "infra": [
        ".github/**",
        "infra/**",
        "terraform/**",
        "bicep/**",
        "deploy/**",
        "k8s/**",
        "helm/**",
        "docker/**",
        "Dockerfile",
        "docker-compose*.yml",
    ],
    "docs": [
        "docs/**",
        "*.md",
        "*.rst",
        "*.txt",
    ],
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_area_map(project_root: Path) -> dict[str, list[str]]:
    """Load the project area map from ``.claude/project-areas.json``.

    Reads the per-project area definitions when present; falls back to the
    built-in coarse-glob map (``_COARSE_AREA_MAP``) when the file is
    absent or contains invalid JSON.

    Args:
        project_root: Absolute path to the repository root (the directory
            that contains ``.claude/``).

    Returns:
        A dict mapping area name strings to lists of fnmatch-style glob
        patterns.  Always non-empty.
    """
    areas_path = project_root / ".claude" / "project-areas.json"
    if not areas_path.exists():
        return {k: list(v) for k, v in _COARSE_AREA_MAP.items()}

    try:
        text = areas_path.read_text(encoding="utf-8")
        data = json.loads(text)
    except (json.JSONDecodeError, OSError) as exc:
        _log.warning(
            "posture._areas: could not load %s (%s); using coarse fallback",
            areas_path,
            exc,
        )
        return {k: list(v) for k, v in _COARSE_AREA_MAP.items()}

    # Support both {"areas": {...}} and bare {"name": [...]} formats.
    if isinstance(data, dict) and "areas" in data:
        area_dict = data["areas"]
    elif isinstance(data, dict):
        area_dict = data
    else:
        _log.warning(
            "posture._areas: unexpected JSON shape in %s; using coarse fallback",
            areas_path,
        )
        return {k: list(v) for k, v in _COARSE_AREA_MAP.items()}

    if not isinstance(area_dict, dict):
        return {k: list(v) for k, v in _COARSE_AREA_MAP.items()}

    result: dict[str, list[str]] = {}
    for area_name, globs in area_dict.items():
        if isinstance(globs, list) and all(isinstance(g, str) for g in globs):
            result[area_name] = globs

    if not result:
        return {k: list(v) for k, v in _COARSE_AREA_MAP.items()}

    return result


def count_distinct_areas(
    file_paths: tuple[str, ...],
    area_map: dict[str, list[str]],
) -> int:
    """Count the number of distinct project areas touched by ``file_paths``.

    Pure function — no filesystem access.  Uses fnmatch glob matching
    against the provided area map.

    A path "matches" an area when it matches at least one of the area's
    glob patterns.  The count is the number of distinct area KEYS
    that have at least one matching path — not the number of matching paths.

    Args:
        file_paths: Tuple of file path strings from the dispatch context.
        area_map: Dict mapping area name → list of fnmatch globs, as
            returned by ``load_area_map``.

    Returns:
        Integer count of distinct areas (0 when ``file_paths`` is empty
        or no path matches any area).
    """
    if not file_paths:
        return 0

    matched_areas: set[str] = set()

    for area_name, globs in area_map.items():
        for path in file_paths:
            # Normalise path separators to forward slashes for fnmatch.
            norm_path = path.replace("\\", "/")
            for glob in globs:
                if fnmatch.fnmatch(norm_path, glob):
                    matched_areas.add(area_name)
                    break
            if area_name in matched_areas:
                break  # no need to check more paths for this area

    return len(matched_areas)
