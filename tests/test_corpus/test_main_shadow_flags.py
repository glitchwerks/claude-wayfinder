"""Tests for CLI flags on ``scripts/corpus/__main__.py`` (issues #468, #479).

These tests are written BEFORE the implementation exists — they exercise the
CLI *plumbing* only.  ``build_corpus()`` itself already supports
``shadow_only``, ``exclude_corpus_ids`` (merged in PR #477) and
``join_shadow_from_twins`` (merged in PR #480) as keyword-only parameters,
and is fully covered by ``tests/test_corpus/test_builder.py``; these tests
never assert on filtering/joining behavior, only on what the CLI passes
through to a mocked ``build_corpus``.

New flags under test
---------------------
    --shadow-only
        Boolean flag (``action="store_true"``, default ``False``).  Passed
        straight through as ``build_corpus(..., shadow_only=args.shadow_only)``.

    --exclude-gold-labels-file PATH
        Optional path to a JSONL file shaped like
        ``docs/research/2026-06-12-gold-labels-redacted.jsonl`` — one JSON
        object per line, each with an integer ``corpus_id`` field.  When
        given, the CLI reads the file, extracts every ``corpus_id`` into a
        ``set[int]``, and passes it as
        ``build_corpus(..., exclude_corpus_ids=<that set>)``.  When omitted,
        ``exclude_corpus_ids=None`` (unchanged default behavior).

    --join-shadow-from-twins
        Boolean flag (``action="store_true"``, default ``False``), mirroring
        the ``--shadow-only`` shape exactly.  Passed straight through as
        ``build_corpus(..., join_shadow_from_twins=args.join_shadow_from_twins)``.
        Not yet wired at the CLI layer as of issue #468 — this file is the
        frozen contract the CLI implementation must satisfy (issue #479's
        ``join_shadow_from_twins`` parameter already exists on
        ``build_corpus()`` itself; only CLI wiring is under test here).

Malformed gold-labels lines (author's design choice)
-----------------------------------------------------
Blank lines, invalid JSON, and lines missing the ``corpus_id`` key are
skipped rather than causing a hard failure — a single bad row in a large
gold-labels file should not abort corpus construction.  See
``test_exclude_gold_labels_file_skips_malformed_lines`` below, which is the
single test asserting this choice (per the briefing: "your call whether to
skip-and-warn or hard-fail; write ONE test").

Coverage map
------------
  1. ``--help`` lists all three flags (``--shadow-only``,
     ``--exclude-gold-labels-file``, ``--join-shadow-from-twins``).
  2. Omitting ``--shadow-only`` -> ``build_corpus(..., shadow_only=False)``.
  3. Passing ``--shadow-only`` -> ``build_corpus(..., shadow_only=True)``.
  4. Omitting ``--exclude-gold-labels-file`` -> ``exclude_corpus_ids=None``.
  5. Passing ``--exclude-gold-labels-file`` -> the exact ``set[int]`` of
     ``corpus_id`` values read from the file.
  6. Malformed/empty lines in the gold-labels file are skipped, not fatal.
  7. Both pre-existing flags combined reach ``build_corpus`` correctly at
     the same time.
  8. Omitting ``--join-shadow-from-twins`` ->
     ``build_corpus(..., join_shadow_from_twins=False)``.
  9. Passing ``--join-shadow-from-twins`` ->
     ``build_corpus(..., join_shadow_from_twins=True)``.
  10. All three flags combined reach ``build_corpus`` correctly at the same
      time.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from scripts.corpus import __main__ as corpus_main

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _organic_matcher_decision(session_id: str = "real-session") -> dict[str, Any]:
    """Build a single organic matcher_decision entry.

    Shaped to satisfy ``claude_wayfinder.log_filter.is_organic_entry`` (type
    == "matcher_decision", non-empty session_id, attribution_source ==
    "post_tool_use_hook") so that ``field_profile()`` reports
    ``organic_count >= 1`` and ``main()`` does not hit the zero-organic
    STOP-GATE before reaching the ``build_corpus`` call under test.

    Args:
        session_id: Value for the top-level ``session_id`` field.

    Returns:
        A dict shaped like a dispatch-log JSONL row.
    """
    return {
        "type": "matcher_decision",
        "ts": "2026-06-01T10:00:00.000000Z",
        "session_id": session_id,
        "input": {"task_description": "fix the login bug in auth module"},
        "output": {
            "decision": "delegate",
            "agent": "code-writer",
            "confidence": 1.0,
            "rationale": "matched keywords",
            "alternatives": [],
        },
        "catalog_hash": "sha256:abc123",
        "matcher_version": "abc1234",
        "attribution_source": "post_tool_use_hook",
    }


def _write_jsonl(path: Path, rows: list[Any]) -> Path:
    """Write ``rows`` as one-JSON-object-per-line to ``path``.

    Args:
        path: Destination file path.
        rows: Objects to serialize, one per line.

    Returns:
        The path written to (same as ``path``).
    """
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")
    return path


def _fake_build_corpus_result() -> dict[str, Any]:
    """A minimal-but-complete ``build_corpus()`` return value.

    Shaped so that ``main()``'s downstream calls (``_print_corpus_report``,
    ``write_corpus_artifact``, ``build_manifest``) succeed against a real,
    empty corpus without touching any real fixture data.

    Returns:
        A dict matching the ``build_corpus()`` result contract.
    """
    return {
        "total_organic": 1,
        "total_filtered": 0,
        "total_in_corpus": 0,
        "per_cell_counts": {},
        "shortfall_table": [],
        "entries": [],
        "generation_params": {"sample_floor": 30},
    }


def _base_argv(log_path: Path, tmp_path: Path, extra: list[str] | None = None) -> list[str]:
    """Build the common argv list every test needs plus any extra flags.

    Always points ``--output-dir`` and ``--manifest-out`` at ``tmp_path`` so
    no test ever writes into the real repo or the developer's home
    directory.

    Args:
        log_path: Path to the synthetic dispatch-log JSONL fixture.
        tmp_path: Pytest's per-test temp directory.
        extra: Additional argv tokens (e.g. ``["--shadow-only"]``).

    Returns:
        A full argv list suitable for ``corpus_main.main(argv)``.
    """
    argv = [
        "--log-path",
        str(log_path),
        "--output-dir",
        str(tmp_path / "corpus-out"),
        "--manifest-out",
        str(tmp_path / "manifest.json"),
    ]
    if extra:
        argv.extend(extra)
    return argv


@pytest.fixture
def organic_log(tmp_path: Path) -> Path:
    """A dispatch-log JSONL fixture with exactly one organic entry.

    Args:
        tmp_path: Pytest's per-test temp directory.

    Returns:
        Path to the written log file.
    """
    return _write_jsonl(tmp_path / "dispatch-log.jsonl", [_organic_matcher_decision()])


@pytest.fixture
def mock_build_corpus(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace ``corpus_main.build_corpus`` with a MagicMock.

    Intercepts the call inside ``main()`` without touching the real
    dispatch log, filesystem, or ``build_corpus()`` filtering logic.

    Args:
        monkeypatch: pytest's monkeypatch fixture.

    Returns:
        The MagicMock installed in place of ``build_corpus``, pre-configured
        to return a minimal valid result dict.
    """
    mock = MagicMock(return_value=_fake_build_corpus_result())
    monkeypatch.setattr(corpus_main, "build_corpus", mock)
    return mock


# ---------------------------------------------------------------------------
# 1. --help lists all three flags
# ---------------------------------------------------------------------------


def test_help_lists_all_three_flags(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--help`` output must mention all three flags by name."""
    with pytest.raises(SystemExit) as excinfo:
        corpus_main.main(["--help"])

    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "--shadow-only" in out, f"--shadow-only missing from help text:\n{out}"
    assert "--exclude-gold-labels-file" in out, (
        f"--exclude-gold-labels-file missing from help text:\n{out}"
    )
    assert "--join-shadow-from-twins" in out, (
        f"--join-shadow-from-twins missing from help text:\n{out}"
    )


# ---------------------------------------------------------------------------
# 2 & 3. --shadow-only plumbing
# ---------------------------------------------------------------------------


def test_omitting_shadow_only_calls_build_corpus_with_false(
    organic_log: Path, tmp_path: Path, mock_build_corpus: MagicMock
) -> None:
    """Regression guard: omitting --shadow-only preserves current behavior.

    ``build_corpus`` must be called with ``shadow_only=False`` when the flag
    is not supplied on argv, matching pre-#468 behavior.
    """
    argv = _base_argv(organic_log, tmp_path)
    rc = corpus_main.main(argv)

    assert rc == 0
    mock_build_corpus.assert_called_once()
    assert mock_build_corpus.call_args.kwargs.get("shadow_only") is False


def test_shadow_only_flag_calls_build_corpus_with_true(
    organic_log: Path, tmp_path: Path, mock_build_corpus: MagicMock
) -> None:
    """Passing --shadow-only results in build_corpus(shadow_only=True)."""
    argv = _base_argv(organic_log, tmp_path, extra=["--shadow-only"])
    rc = corpus_main.main(argv)

    assert rc == 0
    mock_build_corpus.assert_called_once()
    assert mock_build_corpus.call_args.kwargs.get("shadow_only") is True


# ---------------------------------------------------------------------------
# 4 & 5. --exclude-gold-labels-file plumbing
# ---------------------------------------------------------------------------


def test_omitting_exclude_gold_labels_file_calls_build_corpus_with_none(
    organic_log: Path, tmp_path: Path, mock_build_corpus: MagicMock
) -> None:
    """Regression guard: omitting the flag preserves exclude_corpus_ids=None."""
    argv = _base_argv(organic_log, tmp_path)
    rc = corpus_main.main(argv)

    assert rc == 0
    mock_build_corpus.assert_called_once()
    assert mock_build_corpus.call_args.kwargs.get("exclude_corpus_ids") is None


def test_exclude_gold_labels_file_builds_exact_corpus_id_set(
    organic_log: Path, tmp_path: Path, mock_build_corpus: MagicMock
) -> None:
    """The CLI reads the gold-labels file and passes the exact corpus_id set.

    Uses the same row shape as
    ``docs/research/2026-06-12-gold-labels-redacted.jsonl`` (extra fields
    beyond ``corpus_id`` must be ignored).
    """
    gold_path = _write_jsonl(
        tmp_path / "gold-labels.jsonl",
        [
            {"corpus_id": 31091, "domain": "code", "gold_agent": "code-writer"},
            {"corpus_id": 31092, "domain": "docs_prose", "gold_agent": "doc-writer"},
            {"corpus_id": 31199, "domain": "code", "gold_agent": "code-writer"},
        ],
    )
    argv = _base_argv(
        organic_log, tmp_path, extra=["--exclude-gold-labels-file", str(gold_path)]
    )
    rc = corpus_main.main(argv)

    assert rc == 0
    mock_build_corpus.assert_called_once()
    exclude_ids = mock_build_corpus.call_args.kwargs.get("exclude_corpus_ids")
    assert exclude_ids == {31091, 31092, 31199}


def test_exclude_gold_labels_file_skips_malformed_lines(
    organic_log: Path, tmp_path: Path, mock_build_corpus: MagicMock
) -> None:
    """Malformed/empty gold-labels lines are skipped, not fatal.

    Author's design choice (per briefing: pick skip-and-warn or hard-fail,
    write one test for whichever): a blank line, an invalid-JSON line, and a
    line missing the ``corpus_id`` key are all skipped silently or with a
    warning — but the CLI must NOT raise/exit non-zero, and the valid rows
    must still make it into ``exclude_corpus_ids``. A single bad row in a
    large gold-labels file should not abort corpus construction.
    """
    gold_path = tmp_path / "gold-labels-malformed.jsonl"
    with gold_path.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps({"corpus_id": 31091, "domain": "code"}) + "\n")
        fh.write("\n")  # blank line
        fh.write("{not valid json\n")  # malformed JSON
        fh.write(json.dumps({"domain": "code"}) + "\n")  # missing corpus_id
        fh.write(json.dumps({"corpus_id": 31200, "domain": "docs_prose"}) + "\n")

    argv = _base_argv(
        organic_log, tmp_path, extra=["--exclude-gold-labels-file", str(gold_path)]
    )
    rc = corpus_main.main(argv)

    assert rc == 0
    mock_build_corpus.assert_called_once()
    exclude_ids = mock_build_corpus.call_args.kwargs.get("exclude_corpus_ids")
    assert exclude_ids == {31091, 31200}


# ---------------------------------------------------------------------------
# 6. Both flags combined
# ---------------------------------------------------------------------------


def test_shadow_only_and_exclude_gold_labels_file_combine(
    organic_log: Path, tmp_path: Path, mock_build_corpus: MagicMock
) -> None:
    """Both new flags reach build_corpus correctly when combined."""
    gold_path = _write_jsonl(
        tmp_path / "gold-labels.jsonl",
        [
            {"corpus_id": 31091, "domain": "code"},
            {"corpus_id": 31312, "domain": "code"},
        ],
    )
    argv = _base_argv(
        organic_log,
        tmp_path,
        extra=["--shadow-only", "--exclude-gold-labels-file", str(gold_path)],
    )
    rc = corpus_main.main(argv)

    assert rc == 0
    mock_build_corpus.assert_called_once()
    kwargs = mock_build_corpus.call_args.kwargs
    assert kwargs.get("shadow_only") is True
    assert kwargs.get("exclude_corpus_ids") == {31091, 31312}


# ---------------------------------------------------------------------------
# 8 & 9. --join-shadow-from-twins plumbing (issue #468 / #479)
# ---------------------------------------------------------------------------


def test_omitting_join_shadow_from_twins_calls_build_corpus_with_false(
    organic_log: Path, tmp_path: Path, mock_build_corpus: MagicMock
) -> None:
    """Regression guard: omitting --join-shadow-from-twins keeps it False.

    ``build_corpus`` must be called with ``join_shadow_from_twins=False``
    when the flag is not supplied on argv — the default must not silently
    become ``True``.
    """
    argv = _base_argv(organic_log, tmp_path)
    rc = corpus_main.main(argv)

    assert rc == 0
    mock_build_corpus.assert_called_once()
    assert mock_build_corpus.call_args.kwargs.get("join_shadow_from_twins") is False


def test_join_shadow_from_twins_flag_calls_build_corpus_with_true(
    organic_log: Path, tmp_path: Path, mock_build_corpus: MagicMock
) -> None:
    """Passing --join-shadow-from-twins results in build_corpus(True)."""
    argv = _base_argv(organic_log, tmp_path, extra=["--join-shadow-from-twins"])
    rc = corpus_main.main(argv)

    assert rc == 0
    mock_build_corpus.assert_called_once()
    assert mock_build_corpus.call_args.kwargs.get("join_shadow_from_twins") is True


# ---------------------------------------------------------------------------
# 10. All three flags combined
# ---------------------------------------------------------------------------


def test_all_three_flags_combine(
    organic_log: Path, tmp_path: Path, mock_build_corpus: MagicMock
) -> None:
    """All three flags reach build_corpus correctly when passed together.

    Covers ``--shadow-only``, ``--join-shadow-from-twins``, and
    ``--exclude-gold-labels-file`` in a single invocation.
    """
    gold_path = _write_jsonl(
        tmp_path / "gold-labels.jsonl",
        [
            {"corpus_id": 31091, "domain": "code"},
            {"corpus_id": 31312, "domain": "code"},
        ],
    )
    argv = _base_argv(
        organic_log,
        tmp_path,
        extra=[
            "--shadow-only",
            "--join-shadow-from-twins",
            "--exclude-gold-labels-file",
            str(gold_path),
        ],
    )
    rc = corpus_main.main(argv)

    assert rc == 0
    mock_build_corpus.assert_called_once()
    kwargs = mock_build_corpus.call_args.kwargs
    assert kwargs.get("shadow_only") is True
    assert kwargs.get("join_shadow_from_twins") is True
    assert kwargs.get("exclude_corpus_ids") == {31091, 31312}
