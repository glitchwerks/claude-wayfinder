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
- ``build_corpus(log_path, output_dir, sample_floor)``
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
from pathlib import Path
from typing import Any

# Import profiler for length-band classification
sys.path.insert(0, str(Path(__file__).resolve().parent))
from profiler import td_length_band  # noqa: E402

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
    "fields: original log entry fields + corpus_id (int, 1-based file position) "
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
) -> dict[str, Any]:
    """Build a stratified corpus from the dispatch log.

    Reads organic entries (non-empty session_id, non-empty task_description),
    assigns strata, caps at ``sample_floor`` per cell (first-N selection),
    and returns a result dict.

    The ``output_dir`` parameter is reserved for future streaming writes;
    currently the result is returned in-memory and written separately via
    ``write_corpus_artifact()``.

    Args:
        log_path:     Path to the dispatch-log JSONL file.
        output_dir:   Reserved; pass ``None``.
        sample_floor: Maximum entries per strata cell (and floor target).
                      Defaults to 30.

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
    # Load all organic entries
    all_organic = _load_organic_entries(log_path)
    total_organic = len(all_organic)

    # Filter out empty task_description entries
    eligible = [
        e for e in all_organic
        if _get_td(e)
    ]
    total_filtered = total_organic - len(eligible)

    # Assign corpus IDs (1-based position in original log order) and strata
    # corpus_id is the 1-based position of the entry in the eligible list
    augmented: list[dict[str, Any]] = []
    for idx, entry in enumerate(eligible, start=1):
        aug = dict(entry)
        aug["corpus_id"] = idx
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

    return {
        "total_organic": total_organic,
        "total_filtered": total_filtered,
        "total_in_corpus": len(sampled),
        "entries": sampled,
        "per_cell_counts": per_cell_counts,
        "shortfall_table": shortfall_table,
        "generation_params": {
            "sample_floor": sample_floor,
            "log_path": str(log_path),
            "filter_rules": [
                "include: type == matcher_decision",
                "include: session_id non-empty (organic only)",
                "exclude: empty task_description",
                "cap: first sample_floor entries per "
                "(decision_band × td_length_band × file_paths_present) cell",
            ],
        },
    }


def _get_td(entry: dict[str, Any]) -> str:
    """Return the task_description string (or '' if absent/empty)."""
    inp = entry.get("input") or {}
    if not isinstance(inp, dict):
        return ""
    return inp.get("task_description", "") or ""


def _load_organic_entries(log_path: Path) -> list[dict[str, Any]]:
    """Load organic matcher_decision entries from the JSONL log.

    Args:
        log_path: Path to the dispatch-log JSONL file.

    Returns:
        List of parsed dicts in file order, organic only
        (type == matcher_decision AND non-empty session_id).
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
            if not obj.get("session_id", ""):
                continue
            results.append(obj)
    return results


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
        "artifact_path": str(artifact_path),
        "generation_params": result["generation_params"],
    }
