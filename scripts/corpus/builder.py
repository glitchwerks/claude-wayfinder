"""Stratified corpus builder for Matcher v3 corpus phase A.

Reads the organic dispatch-log (via ``claude_wayfinder.log_filter``),
assigns strata based on observable entry fields, samples up to
``sample_floor`` entries per cell, and writes the corpus artifact locally.

Stratification axes (observable-only, per issue #338 design input):
  - ``decision_band``      — output.decision value
  - ``td_length_band``     — task_description character-length band
  - ``file_paths_present`` — bool: input.file_paths is non-empty

Sampling discipline:
  - Ordering-based (file position within each cell) for determinism.
  - No random seed required — reproducibility via stable file ordering.
  - When a cell exceeds ``sample_floor``, only the first ``sample_floor``
    entries (in file order) are kept.

Corpus artifact format (JSONL):
  - Each line is a JSON object with the original log entry fields PLUS:
      ``corpus_id``  — stable integer identifier (1-based file position)
      ``stratum``    — {decision_band, td_length_band, file_paths_present}
  - The artifact is written to ``<output_dir>/wayfinder-corpus.jsonl``.
  - Privacy: the artifact stays local; raw prompt text never enters the repo.

Public API
----------
- ``build_corpus(log_path, output_dir, sample_floor, *, shadow_only,
  exclude_corpus_ids)``
        → corpus result dict (entries, counts, strata, shortfalls)
- ``write_corpus_artifact(result, output_dir)``
        → Path to the written JSONL file
- ``build_manifest(result, artifact_path)``
        → manifest dict (no raw text; suitable for repo commit)

Privacy constraint (issue #338 §HC-3):
  No credential-shaped content patterns are scanned.
  The manifest contains only counts, strata keys, sha256, and format spec.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from claude_wayfinder.log_filter import is_organic_entry

# Import profiler for length-band classification
sys.path.insert(0, str(Path(__file__).resolve().parent))
from profiler import td_length_band  # noqa: E402

# ---------------------------------------------------------------------------
# Path utilities
# ---------------------------------------------------------------------------


def _home_relative(path: Path) -> str:
    """Return a commit-safe POSIX string for ``path``, redacting all machine-specific parts.

    Guarantees that no absolute local path survives into a committed manifest:

    * **Under home** → ``~/rest/of/path`` (POSIX slashes).
    * **Rooted/absolute, NOT under home** → ``<external>/<basename>`` — all
      machine-specific directory components are dropped; only the filename is
      kept for informational value.  This handles CI workspace paths
      (``/github/workspace/…``), scratch drives (``D:/tmp/…``), POSIX-style
      rooted paths (``/tmp/…``), and any other host-specific root.
      Detection uses ``path.root`` (non-empty) rather than
      ``path.is_absolute()``, because on Windows a POSIX-style path like
      ``/tmp/x`` has ``root='\'`` but ``is_absolute()`` returns ``False``
      (no drive letter).
    * **Relative path** → ``path.as_posix()`` unchanged.  Relative paths are
      already machine-unspecific, so no transformation is needed.

    This helper is a pure string/path operation and does **not** touch the
    filesystem.

    Args:
        path: Absolute (or relative) :class:`~pathlib.Path` to rewrite.

    Returns:
        A commit-safe string representation of ``path``:
        ``~/rest/of/path`` (home-relative), ``<external>/<basename>``
        (non-home absolute), or ``path.as_posix()`` (relative).
    """
    home = Path.home()
    try:
        relative = path.relative_to(home)
        return "~/" + PurePosixPath(relative).as_posix()
    except ValueError:
        # path is not relative to home.
        # A "rooted" path has a non-empty root (``path.root != ''``): it starts
        # with a drive-letter root (``C:\``) or a POSIX/UNC root (``/``).
        # On Windows, ``Path('/tmp/x')`` has ``root='\'`` but ``is_absolute()``
        # returns False (no drive letter), so we use ``path.root`` rather than
        # ``path.is_absolute()`` to detect machine-rooted paths.
        # Relative paths (``root == ''``) are already machine-unspecific;
        # return them as-is in POSIX form.
        if not path.root:
            return path.as_posix()
        # Absolute/rooted path outside home: drop all machine-specific directory
        # components; keep only the basename to preserve informational value.
        return "<external>/" + path.name


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Default per-cell sample floor (spec §13.2).
DEFAULT_SAMPLE_FLOOR: int = 30

#: Corpus artifact filename.
ARTIFACT_FILENAME: str = "wayfinder-corpus.jsonl"

#: Format specification string embedded in the manifest.
FORMAT_SPEC: str = (
    "JSONL; one JSON object per line; "
    "fields: original log entry fields + corpus_id (int, 1-based line number in the "
    "source dispatch-log.jsonl at generation time; unique and traceable, NOT dense/"
    "sequential — excluded rows still consume line numbers) "
    "+ stratum (dict: decision_band str, td_length_band str, file_paths_present bool); "
    "encoding: UTF-8; "
    "entries: organic matcher_decision only, non-empty task_description, "
    "capped at sample_floor per (decision_band, td_length_band, file_paths_present) cell; "
    "ordering: file order within each cell (first N kept when cap applied)"
)

# ---------------------------------------------------------------------------
# Stratum assignment
# ---------------------------------------------------------------------------


def _assign_stratum(entry: dict[str, Any]) -> dict[str, Any]:
    """Assign observable stratum dimensions to a log entry.

    Args:
        entry: A matcher_decision dict from the dispatch log.

    Returns:
        A stratum dict with keys:
        - ``decision_band``      — string from output.decision
        - ``td_length_band``     — one of empty/short/medium/long/very_long
        - ``file_paths_present`` — bool
    """
    inp = entry.get("input") or {}
    out = entry.get("output") or {}

    td = inp.get("task_description", "") if isinstance(inp, dict) else ""
    decision = out.get("decision", "unknown") if isinstance(out, dict) else "unknown"
    fp = inp.get("file_paths") if isinstance(inp, dict) else None
    file_paths_present = bool(fp)

    return {
        "decision_band": str(decision),
        "td_length_band": td_length_band(td),
        "file_paths_present": file_paths_present,
    }


def _cell_key(stratum: dict[str, Any]) -> tuple[str, str, bool]:
    """Return a hashable cell key from a stratum dict."""
    return (
        stratum["decision_band"],
        stratum["td_length_band"],
        stratum["file_paths_present"],
    )


# ---------------------------------------------------------------------------
# Core builder
# ---------------------------------------------------------------------------


def build_corpus(
    log_path: Path,
    output_dir: Path | None,  # noqa: ARG001  (reserved for future streaming write)
    sample_floor: int = DEFAULT_SAMPLE_FLOOR,
    *,
    shadow_only: bool = False,
    join_shadow_from_twins: bool = False,
    exclude_corpus_ids: set[int] | None = None,
) -> dict[str, Any]:
    """Build a stratified corpus from the dispatch log.

    Reads organic entries (non-empty session_id, non-empty task_description),
    applies optional shadow and corpus ID filters, assigns strata, caps at
    ``sample_floor`` per cell (first-N selection), and returns a result dict.

    The ``output_dir`` parameter is reserved for future streaming writes;
    currently the result is returned in-memory and written separately via
    ``write_corpus_artifact()``.

    Args:
        log_path:     Path to the dispatch-log JSONL file.
        output_dir:   Reserved; pass ``None``.
        sample_floor: Maximum entries per strata cell (and floor target).
                      Defaults to 30.
        shadow_only: Include only entries with a truthy top-level ``shadow``
                     value. Defaults to ``False``.
        join_shadow_from_twins: Attach shadow data from the nearest preceding
                                ``python_matcher`` twin in the same session.
                                Defaults to ``False``.
        exclude_corpus_ids: Source-log line numbers to exclude. Defaults to
                            ``None``.

    Returns:
        Dict with keys:
        - ``total_organic``   — count of organic entries in the log
        - ``total_filtered``  — organic entries dropped for empty td
        - ``total_in_corpus`` — entries after per-cell cap
        - ``entries``         — list of augmented entry dicts
        - ``per_cell_counts`` — {cell_key_str: count} in corpus
        - ``shortfall_table`` — list of {cell, count, shortfall, floor} dicts
        - ``generation_params`` — {sample_floor, log_path, filter_rules}
    """
    # Load all organic entries as (line_no, entry) tuples.
    # line_no is the 1-based position of the raw line in the source file —
    # ALL lines count, including non-matcher_decision and blank lines.
    all_organic_with_lineno = _load_organic_entries(log_path)
    total_organic = len(all_organic_with_lineno)

    if join_shadow_from_twins:
        twin_candidates = _load_twin_candidates(log_path)
        all_organic_with_lineno = _attach_twin_shadows(
            all_organic_with_lineno,
            twin_candidates,
        )

    # Filter out empty task_description entries, preserving line numbers.
    eligible = [
        (line_no, e)
        for line_no, e in all_organic_with_lineno
        if _get_td(e)
    ]
    total_filtered = total_organic - len(eligible)

    # Apply optional filters before stratum assignment and per-cell capping.
    if shadow_only:
        eligible = [
            (line_no, entry)
            for line_no, entry in eligible
            if entry.get("shadow")
        ]
    if exclude_corpus_ids:
        eligible = [
            (line_no, entry)
            for line_no, entry in eligible
            if line_no not in exclude_corpus_ids
        ]

    # Assign corpus IDs: corpus_id = 1-based line number in the source log.
    # This is NOT a compact rank over the eligible list — excluded rows still
    # consume line numbers, so corpus_ids may have gaps.  The line number is
    # the stable join key: `sed -n '<N>p' dispatch-log.jsonl` recovers the row.
    augmented: list[dict[str, Any]] = []
    for line_no, entry in eligible:
        aug = dict(entry)
        aug["corpus_id"] = line_no
        aug["stratum"] = _assign_stratum(entry)
        augmented.append(aug)

    # Group by cell
    cells: dict[tuple[str, str, bool], list[dict[str, Any]]] = {}
    for aug in augmented:
        key = _cell_key(aug["stratum"])
        cells.setdefault(key, []).append(aug)

    # Apply per-cell cap
    sampled: list[dict[str, Any]] = []
    for key, cell_entries in cells.items():
        sampled.extend(cell_entries[:sample_floor])

    # Sort sampled by corpus_id for stable output
    sampled.sort(key=lambda e: e["corpus_id"])

    # Per-cell counts (in corpus)
    per_cell_counts_raw: dict[tuple[str, str, bool], int] = {}
    for aug in sampled:
        key = _cell_key(aug["stratum"])
        per_cell_counts_raw[key] = per_cell_counts_raw.get(key, 0) + 1

    # Per-cell counts (all organic eligible, for shortfall calculation)
    organic_cell_counts: dict[tuple[str, str, bool], int] = {}
    for aug in augmented:
        key = _cell_key(aug["stratum"])
        organic_cell_counts[key] = organic_cell_counts.get(key, 0) + 1

    # Shortfall table: organic cells below sample_floor
    shortfall_table = _compute_shortfall(organic_cell_counts, sample_floor)

    # Serialise cell keys to strings for JSON compatibility
    per_cell_counts = {
        _cell_key_str(k): v for k, v in per_cell_counts_raw.items()
    }

    filter_rules = [
        "include: type == matcher_decision",
        "include: session_id non-empty (organic only)",
        "exclude: empty task_description",
    ]
    if join_shadow_from_twins:
        filter_rules.append(
            "join: shadow from nearest preceding python_matcher twin in session"
        )
    if shadow_only:
        filter_rules.append("include: top-level shadow value is truthy")
    if exclude_corpus_ids:
        filter_rules.append("exclude: corpus_id in exclude_corpus_ids")
    filter_rules.append(
        "cap: first sample_floor entries per "
        "(decision_band × td_length_band × file_paths_present) cell"
    )

    return {
        "total_organic": total_organic,
        "total_filtered": total_filtered,
        "total_in_corpus": len(sampled),
        "entries": sampled,
        "per_cell_counts": per_cell_counts,
        "shortfall_table": shortfall_table,
        "generation_params": {
            "sample_floor": sample_floor,
            "log_path": _home_relative(log_path),
            "filter_rules": filter_rules,
        },
    }


def _get_td(entry: dict[str, Any]) -> str:
    """Return the task_description string (or '' if absent/empty)."""
    inp = entry.get("input") or {}
    if not isinstance(inp, dict):
        return ""
    return inp.get("task_description", "") or ""


def _load_organic_entries(log_path: Path) -> list[tuple[int, dict[str, Any]]]:
    """Load organic matcher_decision entries from the JSONL log, with line numbers.

    Each raw line (including blank and non-JSON lines) increments the line
    counter so that ``line_no`` equals the 1-based position in the file.
    This makes ``corpus_id = line_no`` a stable, traceable join key: given a
    corpus_id you can recover the original log row with
    ``sed -n '<N>p' dispatch-log.jsonl``.

    Organic entries are determined by :func:`~claude_wayfinder.log_filter.\
is_organic_entry` — only ``attribution_source="post_tool_use_hook"`` entries
    with a non-empty ``session_id`` qualify (#440 attribution filter).
    ``python_matcher`` twins and no-attribution entries are excluded.

    Args:
        log_path: Path to the dispatch-log JSONL file.

    Returns:
        List of ``(line_no, entry_dict)`` tuples in file order, organic only.
        ``line_no`` is 1-based and counts ALL lines in the file, not just
        matcher_decision lines.
    """
    if not log_path.exists():
        return []
    results: list[tuple[int, dict[str, Any]]] = []
    with open(log_path, encoding="utf-8") as fh:
        for line_no, raw in enumerate(fh, start=1):
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                obj: Any = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            # Delegate organic predicate to the single source of truth (#440).
            if not is_organic_entry(obj):
                continue
            results.append((line_no, obj))
    return results


def _load_twin_candidates(log_path: Path) -> list[dict[str, Any]]:
    """Load candidate ``python_matcher`` twin rows from a JSONL log.

    Args:
        log_path: Path to the dispatch-log JSONL file.

    Returns:
        Candidate matcher decisions with a non-empty session ID, in file
        order. Invalid JSON and unrelated rows are skipped.
    """
    if not log_path.exists():
        return []
    results: list[dict[str, Any]] = []
    with open(log_path, encoding="utf-8") as fh:
        for raw in fh:
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                obj: Any = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            if obj.get("type") != "matcher_decision":
                continue
            if obj.get("attribution_source") != "python_matcher":
                continue
            if not obj.get("session_id"):
                continue
            results.append(obj)
    return results


def _attach_twin_shadows(
    organic_entries: list[tuple[int, dict[str, Any]]],
    twin_candidates: list[dict[str, Any]],
) -> list[tuple[int, dict[str, Any]]]:
    """Attach truthy shadow data from nearest preceding session twins.

    Args:
        organic_entries: Organic entries paired with source line numbers.
        twin_candidates: Candidate ``python_matcher`` twin rows.

    Returns:
        Organic entries in their original order. Entries with a matching,
        truthy twin shadow are shallow copies containing that shadow value.
    """
    joined: list[tuple[int, dict[str, Any]]] = []
    for line_no, entry in organic_entries:
        try:
            entry_ts = datetime.fromisoformat(
                entry.get("ts").replace("Z", "+00:00")
            )
        except (AttributeError, TypeError, ValueError):
            joined.append((line_no, entry))
            continue

        nearest_twin: dict[str, Any] | None = None
        nearest_ts: datetime | None = None
        for candidate in twin_candidates:
            if candidate.get("session_id") != entry.get("session_id"):
                continue
            try:
                candidate_ts = datetime.fromisoformat(
                    candidate.get("ts").replace("Z", "+00:00")
                )
            except (AttributeError, TypeError, ValueError):
                continue
            if candidate_ts >= entry_ts:
                continue
            if nearest_ts is None or candidate_ts > nearest_ts:
                nearest_twin = candidate
                nearest_ts = candidate_ts

        if nearest_twin is not None and nearest_twin.get("shadow"):
            joined_entry = dict(entry)
            joined_entry["shadow"] = nearest_twin["shadow"]
            joined.append((line_no, joined_entry))
        else:
            joined.append((line_no, entry))
    return joined


def _cell_key_str(key: tuple[str, str, bool]) -> str:
    """Serialise a cell key tuple to a JSON-compatible string."""
    decision, td_band, fp = key
    fp_str = "fp=yes" if fp else "fp=no"
    return f"{decision}|{td_band}|{fp_str}"


def _compute_shortfall(
    organic_cell_counts: dict[tuple[str, str, bool], int],
    floor: int,
) -> list[dict[str, Any]]:
    """Compute per-cell shortfall vs floor.

    Args:
        organic_cell_counts: Organic-eligible entries per cell (pre-cap).
        floor:               Sample floor target.

    Returns:
        List of {cell, count, floor, shortfall} dicts for cells below floor,
        sorted by count ascending (worst shortfall first).
    """
    shortfalls = []
    for key, count in organic_cell_counts.items():
        if count < floor:
            shortfalls.append({
                "cell": _cell_key_str(key),
                "count": count,
                "floor": floor,
                "shortfall": floor - count,
            })
    shortfalls.sort(key=lambda x: x["count"])
    return shortfalls


# ---------------------------------------------------------------------------
# Artifact writer
# ---------------------------------------------------------------------------


def write_corpus_artifact(
    result: dict[str, Any],
    output_dir: Path,
) -> Path:
    """Write the corpus entries to a JSONL file in output_dir.

    Each line is a full augmented entry dict (original log fields +
    corpus_id + stratum).  Privacy: raw task_description text IS
    included in the artifact — this file must remain local and never
    be committed to the repo.

    Args:
        result:     Result dict from ``build_corpus()``.
        output_dir: Local directory to write the artifact into.

    Returns:
        Path to the written JSONL file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = output_dir / ARTIFACT_FILENAME

    with artifact_path.open("w", encoding="utf-8") as fh:
        for entry in result["entries"]:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    return artifact_path


# ---------------------------------------------------------------------------
# Manifest builder
# ---------------------------------------------------------------------------


def build_manifest(
    result: dict[str, Any],
    artifact_path: Path,
) -> dict[str, Any]:
    """Build a commit-safe manifest for the corpus artifact.

    The manifest contains counts, strata table, format spec, sha256,
    and generation parameters.  It MUST NOT contain any raw prompt text.

    All absolute local paths in the manifest are redacted via
    :func:`_home_relative`: under-home paths become ``~/…``, and any
    absolute path outside the home directory (e.g. CI workspace, scratch
    drive) becomes ``<external>/<basename>``.  Only relative paths are
    kept verbatim.  This guarantees no machine-specific directory
    information survives into a committed manifest.

    Args:
        result:        Result dict from ``build_corpus()``.
        artifact_path: Path to the written JSONL artifact.

    Returns:
        JSON-serialisable manifest dict.
    """
    sha256 = hashlib.sha256(artifact_path.read_bytes()).hexdigest()

    # Strata table: per-cell counts (in corpus) + organic totals for reference
    strata_table = result["per_cell_counts"]

    return {
        "total_in_corpus": result["total_in_corpus"],
        "total_organic": result["total_organic"],
        "total_filtered_empty_td": result["total_filtered"],
        "strata_table": strata_table,
        "shortfall_table": result["shortfall_table"],
        "format_spec": FORMAT_SPEC,
        "sha256": sha256,
        "artifact_path": _home_relative(artifact_path),
        "generation_params": result["generation_params"],
    }
