"""Tests for scripts/corpus/builder.py — stratified corpus construction.

All tests use synthetic JSONL fixtures.  The real dispatch-log is never read.

Coverage:
  1. build_corpus() returns only organic entries with non-empty task_description
  2. Stable entry IDs assigned (deterministic, position-based)
  3. Strata assigned to each entry (decision × td_length_band × file_paths_present)
  4. Per-cell counts computed correctly
  5. Shortfall table: cells below floor are flagged
  6. Corpus capped at sample_floor entries per cell when organic supports it
  7. Sampling is deterministic (same seed → same order, no randomness unless seeded)
  8. write_corpus_artifact() writes valid JSONL to output_dir
  9. Manifest includes counts, strata table, format_spec, sha256, generation_params
  10. Manifest sha256 matches the written artifact
  11. No raw task_description text in the manifest (privacy)
  12. Empty corpus (no organic entries) handled gracefully
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _md(
    session_id: str = "real-session",
    task_description: str = "fix the login bug in auth module",
    decision: str = "delegate",
    agent: str = "code-writer",
    confidence: float = 1.0,
    include_file_paths: bool = False,
    ts: str = "2026-06-01T10:00:00.000000Z",
) -> dict[str, Any]:
    """Build a synthetic matcher_decision entry."""
    inp: dict[str, Any] = {"task_description": task_description}
    if include_file_paths:
        inp["file_paths"] = ["src/main.py"]

    return {
        "type": "matcher_decision",
        "ts": ts,
        "session_id": session_id,
        "input": inp,
        "output": {
            "decision": decision,
            "agent": agent,
            "confidence": confidence,
            "rationale": "matched keywords",
            "alternatives": [],
        },
        "catalog_hash": "sha256:abc123",
        "matcher_version": "abc1234",
    }


def _write_jsonl(tmp_path: Path, entries: list[Any], filename: str = "dispatch-log.jsonl") -> Path:
    """Write entries as JSONL."""
    p = tmp_path / filename
    with p.open("w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry) + "\n")
    return p


# ---------------------------------------------------------------------------
# 1. build_corpus filters correctly
# ---------------------------------------------------------------------------


def test_build_corpus_excludes_fixture_entries(tmp_path: Path) -> None:
    """Fixture entries (empty session_id) are excluded from the corpus."""
    from scripts.corpus.builder import build_corpus

    log = _write_jsonl(
        tmp_path,
        [
            _md(session_id=""),         # fixture → excluded
            _md(session_id="real"),      # organic → included
        ],
    )
    result = build_corpus(log, output_dir=None)
    assert result["total_organic"] == 1
    assert result["total_in_corpus"] == 1


def test_build_corpus_excludes_empty_task_description(tmp_path: Path) -> None:
    """Organic entries with empty task_description are excluded from corpus."""
    from scripts.corpus.builder import build_corpus

    log = _write_jsonl(
        tmp_path,
        [
            _md(session_id="s1", task_description=""),         # empty → excluded
            _md(session_id="s2", task_description="fix bug"),  # included
        ],
    )
    result = build_corpus(log, output_dir=None)
    assert result["total_organic"] == 2  # both counted as organic
    assert result["total_in_corpus"] == 1  # only non-empty td


# ---------------------------------------------------------------------------
# 2. Stable entry IDs
# ---------------------------------------------------------------------------


def test_entry_ids_are_assigned(tmp_path: Path) -> None:
    """Each corpus entry receives a stable corpus_id field."""
    from scripts.corpus.builder import build_corpus

    log = _write_jsonl(tmp_path, [_md(session_id=f"s{i}") for i in range(5)])
    result = build_corpus(log, output_dir=None)

    ids = [e["corpus_id"] for e in result["entries"]]
    assert len(ids) == 5
    assert len(set(ids)) == 5  # all unique


def test_entry_ids_are_deterministic(tmp_path: Path) -> None:
    """Entry IDs are the same across two runs on the same input."""
    from scripts.corpus.builder import build_corpus

    entries_data = [
        _md(session_id=f"s{i}", ts=f"2026-06-01T{i:02d}:00:00.000000Z") for i in range(5)
    ]
    log = _write_jsonl(tmp_path, entries_data)

    r1 = build_corpus(log, output_dir=None)
    r2 = build_corpus(log, output_dir=None)

    assert [e["corpus_id"] for e in r1["entries"]] == [e["corpus_id"] for e in r2["entries"]]


def test_entry_ids_are_integers_or_strings(tmp_path: Path) -> None:
    """corpus_id can be an int or a string — must be JSON-serialisable."""
    from scripts.corpus.builder import build_corpus

    log = _write_jsonl(tmp_path, [_md(session_id="s1")])
    result = build_corpus(log, output_dir=None)
    cid = result["entries"][0]["corpus_id"]
    # Must be JSON serialisable
    json.dumps(cid)


# ---------------------------------------------------------------------------
# 3. Strata assigned
# ---------------------------------------------------------------------------


def test_strata_keys_present_on_entries(tmp_path: Path) -> None:
    """Each corpus entry has stratum fields: decision_band, td_length_band, file_paths_present."""
    from scripts.corpus.builder import build_corpus

    log = _write_jsonl(tmp_path, [_md(session_id="s1", include_file_paths=True)])
    result = build_corpus(log, output_dir=None)
    entry = result["entries"][0]

    assert "stratum" in entry
    s = entry["stratum"]
    assert "decision_band" in s
    assert "td_length_band" in s
    assert "file_paths_present" in s


def test_strata_decision_band_maps_decision(tmp_path: Path) -> None:
    """decision_band reflects the output.decision value."""
    from scripts.corpus.builder import build_corpus

    log = _write_jsonl(
        tmp_path,
        [
            _md(session_id="s1", decision="delegate"),
            _md(session_id="s2", decision="advisory"),
            _md(session_id="s3", decision="needs_more_detail"),
        ],
    )
    result = build_corpus(log, output_dir=None)
    bands = [e["stratum"]["decision_band"] for e in result["entries"]]
    assert "delegate" in bands
    assert "advisory" in bands
    assert "needs_more_detail" in bands


def test_strata_td_length_band_short(tmp_path: Path) -> None:
    """td_length_band == 'short' for task_description < 50 chars."""
    from scripts.corpus.builder import build_corpus

    log = _write_jsonl(tmp_path, [_md(session_id="s1", task_description="fix bug")])
    result = build_corpus(log, output_dir=None)
    assert result["entries"][0]["stratum"]["td_length_band"] == "short"


def test_strata_td_length_band_long(tmp_path: Path) -> None:
    """td_length_band == 'long' for task_description 200-499 chars."""
    from scripts.corpus.builder import build_corpus

    log = _write_jsonl(tmp_path, [_md(session_id="s1", task_description="x" * 250)])
    result = build_corpus(log, output_dir=None)
    assert result["entries"][0]["stratum"]["td_length_band"] == "long"


def test_strata_file_paths_present_true(tmp_path: Path) -> None:
    """file_paths_present is True when non-empty file_paths provided."""
    from scripts.corpus.builder import build_corpus

    log = _write_jsonl(tmp_path, [_md(session_id="s1", include_file_paths=True)])
    result = build_corpus(log, output_dir=None)
    assert result["entries"][0]["stratum"]["file_paths_present"] is True


def test_strata_file_paths_present_false(tmp_path: Path) -> None:
    """file_paths_present is False when no file_paths."""
    from scripts.corpus.builder import build_corpus

    log = _write_jsonl(tmp_path, [_md(session_id="s1", include_file_paths=False)])
    result = build_corpus(log, output_dir=None)
    assert result["entries"][0]["stratum"]["file_paths_present"] is False


# ---------------------------------------------------------------------------
# 4. Per-cell counts
# ---------------------------------------------------------------------------


def test_per_cell_counts_correct(tmp_path: Path) -> None:
    """per_cell_counts accurately tallies entries per stratum cell."""
    from scripts.corpus.builder import build_corpus

    entries = [
        _md(session_id="s1", decision="delegate", task_description="fix bug"),
        _md(session_id="s2", decision="delegate", task_description="fix bug"),
        _md(session_id="s3", decision="advisory", task_description="fix bug"),
    ]
    log = _write_jsonl(tmp_path, entries)
    result = build_corpus(log, output_dir=None)

    cell_counts = result["per_cell_counts"]
    # Should have counts per (decision, td_length_band, file_paths_present) tuple
    assert sum(cell_counts.values()) == 3


# ---------------------------------------------------------------------------
# 5. Shortfall table
# ---------------------------------------------------------------------------


def test_shortfall_table_populated_when_below_floor(tmp_path: Path) -> None:
    """Cells with fewer entries than the floor appear in shortfall_table."""
    from scripts.corpus.builder import build_corpus

    # Only 2 entries in the corpus -> floor of 30 not met for any cell
    entries = [_md(session_id=f"s{i}") for i in range(2)]
    log = _write_jsonl(tmp_path, entries)
    result = build_corpus(log, output_dir=None, sample_floor=30)

    assert len(result["shortfall_table"]) > 0
    first_shortfall = result["shortfall_table"][0]
    assert "cell" in first_shortfall
    assert "count" in first_shortfall
    assert "shortfall" in first_shortfall
    assert first_shortfall["shortfall"] > 0


def test_shortfall_table_empty_when_floor_met(tmp_path: Path) -> None:
    """Shortfall table is empty when every cell meets or exceeds the floor."""
    from scripts.corpus.builder import build_corpus

    # 3 entries, floor = 1 -> floor met
    entries = [_md(session_id=f"s{i}") for i in range(3)]
    log = _write_jsonl(tmp_path, entries)
    result = build_corpus(log, output_dir=None, sample_floor=1)

    assert result["shortfall_table"] == []


# ---------------------------------------------------------------------------
# 6. Per-cell cap
# ---------------------------------------------------------------------------


def test_per_cell_cap_applied(tmp_path: Path) -> None:
    """Entries are capped at sample_floor per cell when organic exceeds floor."""
    from scripts.corpus.builder import build_corpus

    # 10 entries in the same cell, floor = 3
    entries = [
        _md(session_id=f"s{i}", decision="delegate", task_description="fix bug")
        for i in range(10)
    ]
    log = _write_jsonl(tmp_path, entries)
    result = build_corpus(log, output_dir=None, sample_floor=3)

    assert result["total_in_corpus"] == 3


def test_all_entries_kept_when_below_cap(tmp_path: Path) -> None:
    """All entries are kept when count is below the per-cell cap."""
    from scripts.corpus.builder import build_corpus

    entries = [_md(session_id=f"s{i}") for i in range(5)]
    log = _write_jsonl(tmp_path, entries)
    result = build_corpus(log, output_dir=None, sample_floor=30)

    assert result["total_in_corpus"] == 5


# ---------------------------------------------------------------------------
# 7. Sampling determinism
# ---------------------------------------------------------------------------


def test_sampling_is_deterministic(tmp_path: Path) -> None:
    """Same input produces the same corpus selection in two runs."""
    from scripts.corpus.builder import build_corpus

    entries = [_md(session_id=f"s{i:03d}") for i in range(20)]
    log = _write_jsonl(tmp_path, entries)

    r1 = build_corpus(log, output_dir=None, sample_floor=5)
    r2 = build_corpus(log, output_dir=None, sample_floor=5)

    ids1 = [e["corpus_id"] for e in r1["entries"]]
    ids2 = [e["corpus_id"] for e in r2["entries"]]
    assert ids1 == ids2


# ---------------------------------------------------------------------------
# 8. write_corpus_artifact() writes valid JSONL
# ---------------------------------------------------------------------------


def test_write_corpus_artifact_creates_jsonl(tmp_path: Path) -> None:
    """write_corpus_artifact() writes a JSONL file to output_dir."""
    from scripts.corpus.builder import build_corpus, write_corpus_artifact

    log = _write_jsonl(
        tmp_path,
        [_md(session_id=f"s{i}") for i in range(5)],
    )
    out_dir = tmp_path / "corpus-out"
    out_dir.mkdir()

    result = build_corpus(log, output_dir=None)
    artifact_path = write_corpus_artifact(result, out_dir)

    assert artifact_path.exists()
    # Each line must be valid JSON
    lines = [ln for ln in artifact_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == result["total_in_corpus"]
    for line in lines:
        obj = json.loads(line)
        assert "corpus_id" in obj


# ---------------------------------------------------------------------------
# 9. Manifest contains required keys
# ---------------------------------------------------------------------------


def test_build_manifest_has_required_keys(tmp_path: Path) -> None:
    """build_manifest() returns dict with all required manifest keys."""
    from scripts.corpus.builder import build_corpus, build_manifest, write_corpus_artifact

    log = _write_jsonl(tmp_path, [_md(session_id="s1")])
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    result = build_corpus(log, output_dir=None)
    artifact_path = write_corpus_artifact(result, out_dir)
    manifest = build_manifest(result, artifact_path)

    required = [
        "total_in_corpus",
        "total_organic",
        "strata_table",
        "shortfall_table",
        "format_spec",
        "sha256",
        "artifact_path",
        "generation_params",
    ]
    for key in required:
        assert key in manifest, f"Missing manifest key: {key!r}"


def test_manifest_format_spec_documented(tmp_path: Path) -> None:
    """Manifest format_spec field is non-empty."""
    from scripts.corpus.builder import build_corpus, build_manifest, write_corpus_artifact

    log = _write_jsonl(tmp_path, [_md(session_id="s1")])
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    result = build_corpus(log, output_dir=None)
    artifact_path = write_corpus_artifact(result, out_dir)
    manifest = build_manifest(result, artifact_path)

    assert manifest["format_spec"]  # non-empty


# ---------------------------------------------------------------------------
# 10. Manifest sha256 matches artifact
# ---------------------------------------------------------------------------


def test_manifest_sha256_matches_artifact(tmp_path: Path) -> None:
    """Manifest sha256 matches the actual sha256 of the written artifact."""
    from scripts.corpus.builder import build_corpus, build_manifest, write_corpus_artifact

    log = _write_jsonl(
        tmp_path,
        [_md(session_id=f"s{i}") for i in range(3)],
    )
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    result = build_corpus(log, output_dir=None)
    artifact_path = write_corpus_artifact(result, out_dir)
    manifest = build_manifest(result, artifact_path)

    actual_sha256 = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    assert manifest["sha256"] == actual_sha256


# ---------------------------------------------------------------------------
# 11. No raw task_description text in manifest (privacy guard)
# ---------------------------------------------------------------------------


def test_manifest_has_no_task_description_text(tmp_path: Path) -> None:
    """Manifest JSON does not contain any task_description text values."""
    from scripts.corpus.builder import build_corpus, build_manifest, write_corpus_artifact

    # Use a very distinctive task description that can't appear accidentally
    sentinel = "SENTINEL_TASK_DESCRIPTION_TEXT_xyz987"
    log = _write_jsonl(tmp_path, [_md(session_id="s1", task_description=sentinel)])
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    result = build_corpus(log, output_dir=None)
    artifact_path = write_corpus_artifact(result, out_dir)
    manifest = build_manifest(result, artifact_path)

    manifest_json = json.dumps(manifest)
    assert sentinel not in manifest_json, "Raw task_description text found in manifest!"


# ---------------------------------------------------------------------------
# 12. Empty corpus handled gracefully
# ---------------------------------------------------------------------------


def test_empty_corpus_no_organic_entries(tmp_path: Path) -> None:
    """build_corpus returns 0 entries without error when no organic entries."""
    from scripts.corpus.builder import build_corpus

    log = _write_jsonl(tmp_path, [_md(session_id="")])  # all fixture
    result = build_corpus(log, output_dir=None)

    assert result["total_in_corpus"] == 0
    assert result["entries"] == []


def test_write_artifact_empty_corpus(tmp_path: Path) -> None:
    """write_corpus_artifact on empty corpus creates an empty JSONL file."""
    from scripts.corpus.builder import build_corpus, write_corpus_artifact

    log = _write_jsonl(tmp_path, [_md(session_id="")])
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    result = build_corpus(log, output_dir=None)
    artifact_path = write_corpus_artifact(result, out_dir)

    assert artifact_path.exists()
    content = artifact_path.read_text(encoding="utf-8").strip()
    assert content == ""
