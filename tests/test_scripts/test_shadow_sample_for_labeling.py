"""Tests for scripts/shadow-sample-for-labeling.py.

Spec source: docs/superpowers/plans/2026-07-19-m15-6-shadow-kc-report.md
Sec 3 (pipeline stages) and Sec 6 item D-N (~120-entry Phase A gold-anchor
labeling sample; no oversampling needed per the #483 denominator-estimate
comment).

The script draws a deterministic, seeded, stratified subsample from a RAW
(unstripped) shadow-corpus JSONL, before the separate strip-for-labeling
tool runs. Stratification reuses ``_assign_stratum`` / ``_cell_key`` from
``scripts/corpus/builder.py`` -- each row's cell key is
``(decision_band, td_length_band, file_paths_present)``, and
``decision_band`` reads ``row["output"]["decision"]``, which only exists
on the raw (unstripped) corpus.

Public API under test:

- ``draw_stratified_sample(rows, n, seed, floor=2)`` -- pure function.
  Groups rows by cell, draws proportionally toward a total of ``n``, but
  never fewer than ``min(floor, cell_size)`` from any populated cell.
  Deterministic given the same ``rows`` + ``seed`` (uses a local
  ``random.Random(seed)`` instance, never the global ``random`` module).
  Returns full, unmodified row dicts; does not mutate its input.
- ``main(argv)`` -- CLI mirroring ``scripts/shadow-strip-for-labeling.py``'s
  argparse + JSONL-stream style: ``--input`` (required, Path), ``--output``
  (required, Path, exclusive-create), ``--n`` (default 120), ``--seed``
  (required, no default), ``--floor`` (default 2).

This test module authors the contract in advance of the implementation
(``scripts/shadow-sample-for-labeling.py`` does not exist yet) per the
test-implementer / code-implementer split -- every test below is expected
to fail at collection time (``ModuleNotFoundError`` / ``FileNotFoundError``
from the module loader) until that script exists.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

# Load the script from its path (it lives under scripts/, is not part of
# the installed package, and its filename is not a valid Python
# identifier), mirroring the loader pattern used by
# tests/test_scripts/test_shadow_strip_for_labeling.py for
# scripts/shadow-strip-for-labeling.py.
_SCRIPT_PATH = Path(__file__).parent.parent.parent / "scripts" / "shadow-sample-for-labeling.py"

# Task-description lengths chosen to land in specific td_length_band values
# per scripts/corpus/profiler.py (TD_BAND_SHORT_MAX=50, MEDIUM_MAX=200,
# LONG_MAX=500): short is [1, 50), medium is [50, 200), long is [200, 500).
_SHORT_TD_LEN = 10
_MEDIUM_TD_LEN = 100
_LONG_TD_LEN = 250


@pytest.fixture(scope="module")
def sample_module() -> ModuleType:
    """Load ``scripts/shadow-sample-for-labeling.py`` as a module.

    Returns:
        The loaded module, exposing ``draw_stratified_sample`` and ``main``.

    Raises:
        FileNotFoundError: If the script does not exist yet (the expected
            RED state before the implementation lands).
    """
    spec = importlib.util.spec_from_file_location("shadow_sample_for_labeling", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["shadow_sample_for_labeling"] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        sys.modules.pop("shadow_sample_for_labeling", None)
        raise
    return mod


def make_row(
    corpus_id: int,
    decision: str = "delegate",
    td_len: int = _SHORT_TD_LEN,
    file_paths: list[str] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Build a synthetic RAW (unstripped) shadow-corpus row.

    Shaped to match the fields ``scripts.corpus.builder._assign_stratum``
    reads (``input.task_description``, ``input.file_paths``,
    ``output.decision``), plus enough surrounding fields to exercise the
    "full raw row survives unmodified" contract.

    Args:
        corpus_id: Value for the top-level ``corpus_id`` field.
        decision: Value for ``output.decision`` (drives ``decision_band``).
        td_len: Character length of ``input.task_description`` (drives
            ``td_length_band`` -- see the module-level ``_*_TD_LEN``
            constants).
        file_paths: Value for ``input.file_paths``; ``None`` becomes ``[]``
            (drives ``file_paths_present``).
        **extra: Additional top-level keys merged onto the row.

    Returns:
        A synthetic raw corpus row dict.
    """
    row: dict[str, Any] = {
        "corpus_id": corpus_id,
        "session_id": f"session-{corpus_id}",
        "input": {
            "task_description": "x" * td_len,
            "file_paths": list(file_paths) if file_paths else [],
        },
        "output": {"decision": decision, "confidence": 1.0},
    }
    row.update(extra)
    return row


def build_three_cell_corpus() -> list[dict[str, Any]]:
    """Build a synthetic corpus spanning three distinct strata cells.

    Cell A -- ("delegate", "short", False):    10 rows (corpus_id 1-10)
    Cell B -- ("self_handle", "medium", True):  8 rows (corpus_id 11-18)
    Cell C -- ("delegate", "long", False):      1 row  (corpus_id 19) -- thin

    Returns:
        19 synthetic raw corpus rows spanning three cells, one of which
        (Cell C) has only a single row.
    """
    rows: list[dict[str, Any]] = []
    cid = 1
    for _ in range(10):
        rows.append(make_row(cid, decision="delegate", td_len=_SHORT_TD_LEN))
        cid += 1
    for _ in range(8):
        rows.append(
            make_row(
                cid,
                decision="self_handle",
                td_len=_MEDIUM_TD_LEN,
                file_paths=["src/a.py"],
            )
        )
        cid += 1
    rows.append(make_row(cid, decision="delegate", td_len=_LONG_TD_LEN))
    return rows


def row_cell_key(row: dict[str, Any]) -> tuple[str, str, bool]:
    """Compute a row's strata cell key via the reused builder helpers.

    Args:
        row: A raw corpus row (or subset containing ``input``/``output``).

    Returns:
        The ``(decision_band, td_length_band, file_paths_present)`` key.
    """
    from scripts.corpus.builder import _assign_stratum, _cell_key

    return _cell_key(_assign_stratum(row))


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    """Write ``rows`` to ``path`` as JSONL, one object per line.

    Args:
        path: Destination file path.
        rows: Row dicts to serialise.
    """
    path.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n",
        encoding="utf-8",
    )


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read a JSONL file into a list of dicts, skipping blank lines.

    Args:
        path: Source file path.

    Returns:
        Parsed row dicts in file order.
    """
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class TestDrawStratifiedSampleIsDeterministic:
    """Behavior 1 -- same rows + same seed -> identical output, every time."""

    def test_pure_function_identical_across_repeated_calls(
        self, sample_module: ModuleType
    ) -> None:
        rows = build_three_cell_corpus()
        first = sample_module.draw_stratified_sample(rows, n=6, seed=123, floor=2)
        second = sample_module.draw_stratified_sample(rows, n=6, seed=123, floor=2)
        assert first == second, (
            "draw_stratified_sample must be byte-identical (same rows, same "
            "list content and order) across repeated calls with the same seed"
        )

    def test_pure_function_identical_across_fresh_row_list_instances(
        self, sample_module: ModuleType
    ) -> None:
        # A fresh (but structurally identical) rows list must not perturb
        # the result -- determinism is a function of content, not identity.
        rows_a = build_three_cell_corpus()
        rows_b = build_three_cell_corpus()
        result_a = sample_module.draw_stratified_sample(rows_a, n=6, seed=123, floor=2)
        result_b = sample_module.draw_stratified_sample(rows_b, n=6, seed=123, floor=2)
        ids_a = [r["corpus_id"] for r in result_a]
        ids_b = [r["corpus_id"] for r in result_b]
        assert ids_a == ids_b

    def test_cli_output_bytes_identical_across_runs_with_same_seed(
        self, sample_module: ModuleType, tmp_path: Path
    ) -> None:
        source = tmp_path / "raw-corpus.jsonl"
        write_jsonl(source, build_three_cell_corpus())
        output_1 = tmp_path / "sample-1.jsonl"
        output_2 = tmp_path / "sample-2.jsonl"

        rc1 = sample_module.main(
            ["--input", str(source), "--output", str(output_1), "--n", "6", "--seed", "123"]
        )
        rc2 = sample_module.main(
            ["--input", str(source), "--output", str(output_2), "--n", "6", "--seed", "123"]
        )

        assert rc1 == 0
        assert rc2 == 0
        assert output_1.read_bytes() == output_2.read_bytes(), (
            "two CLI runs with identical input and seed must produce "
            "byte-identical output JSONL"
        )


class TestDrawStratifiedSampleVariesWithSeed:
    """Behavior 2 -- different seeds (same input, same n) draw differently.

    Judgment call (see test-implementer return notes): the spec's exact
    proportional/tie-breaking algorithm is intentionally unspecified (any
    correct implementation is acceptable), so this suite cannot pre-compute
    one exact expected output list without coupling to one implementation
    shape. Instead it draws a sample far smaller than a single, large,
    single-cell pool (8 of 20) with two fixed seeds and asserts the drawn
    corpus_id sets differ -- collision probability for two honestly-seeded
    draws over C(20, 8) = 125,970 combinations is astronomically small,
    so this is not a flaky assertion in practice.
    """

    def test_two_fixed_seeds_produce_different_draws(self, sample_module: ModuleType) -> None:
        # Single cell (all same decision/td-band/file-paths) so cell
        # membership cannot be the source of any observed difference --
        # only the seed can be.
        rows = [make_row(cid) for cid in range(1, 21)]
        result_seed_1 = sample_module.draw_stratified_sample(rows, n=8, seed=1, floor=2)
        result_seed_2 = sample_module.draw_stratified_sample(rows, n=8, seed=2, floor=2)
        ids_1 = {r["corpus_id"] for r in result_seed_1}
        ids_2 = {r["corpus_id"] for r in result_seed_2}
        assert ids_1 != ids_2, (
            "seed=1 and seed=2 produced the identical drawn set over a "
            "20-row single-cell pool -- the draw does not appear to be "
            "using the seed"
        )


class TestDrawStratifiedSampleRespectsStratificationFloor:
    """Behavior 3 -- every populated cell gets min(floor, cell_size)."""

    def test_every_cell_meets_its_floor_minimum(self, sample_module: ModuleType) -> None:
        rows = build_three_cell_corpus()
        floor = 2
        result = sample_module.draw_stratified_sample(rows, n=6, seed=7, floor=floor)

        cell_sizes: dict[tuple[str, str, bool], int] = {}
        for row in rows:
            key = row_cell_key(row)
            cell_sizes[key] = cell_sizes.get(key, 0) + 1

        result_cell_counts: dict[tuple[str, str, bool], int] = {}
        for row in result:
            key = row_cell_key(row)
            result_cell_counts[key] = result_cell_counts.get(key, 0) + 1

        assert set(cell_sizes) == {
            ("delegate", "short", False),
            ("self_handle", "medium", True),
            ("delegate", "long", False),
        }, "fixture did not land in the three intended distinct cells"

        for key, size in cell_sizes.items():
            expected_min = min(floor, size)
            actual = result_cell_counts.get(key, 0)
            assert actual >= expected_min, (
                f"cell {key} (size={size}) got only {actual} rows in the "
                f"draw; expected at least min(floor={floor}, size)={expected_min}"
            )

    def test_thin_single_row_cell_is_fully_represented(self, sample_module: ModuleType) -> None:
        rows = build_three_cell_corpus()
        result = sample_module.draw_stratified_sample(rows, n=6, seed=7, floor=2)
        thin_key = ("delegate", "long", False)
        thin_rows_in_result = [r for r in result if row_cell_key(r) == thin_key]
        assert len(thin_rows_in_result) == 1, (
            "the single-row thin cell (min(floor=2, size=1)=1) must appear "
            "exactly once in the draw -- it cannot appear more than it has "
            "rows, and floor must not starve it to zero"
        )
        assert thin_rows_in_result[0]["corpus_id"] == 19


class TestDrawStratifiedSampleTotalCount:
    """Behavior 4 -- with a large corpus and default floor, len(result) is
    close to the requested n.
    """

    def test_result_length_close_to_n_for_well_stocked_equal_cells(
        self, sample_module: ModuleType
    ) -> None:
        # Three equal-size (50-row), well-stocked cells; n=120 is exactly
        # divisible by 3 (40 per cell), so a floor of 2 never binds and a
        # reasonable proportional split has no remainder to argue about --
        # any correct implementation should land at or extremely near 120.
        rows: list[dict[str, Any]] = []
        cid = 1
        for _ in range(50):
            rows.append(make_row(cid, decision="delegate", td_len=_SHORT_TD_LEN))
            cid += 1
        for _ in range(50):
            rows.append(
                make_row(
                    cid,
                    decision="self_handle",
                    td_len=_MEDIUM_TD_LEN,
                    file_paths=["src/a.py"],
                )
            )
            cid += 1
        for _ in range(50):
            rows.append(make_row(cid, decision="delegate", td_len=_LONG_TD_LEN))
            cid += 1

        result = sample_module.draw_stratified_sample(rows, n=120, seed=42, floor=2)
        assert abs(len(result) - 120) <= 3, (
            f"expected a draw close to n=120 from a 150-row, 3-equal-cell "
            f"corpus; got {len(result)}"
        )


class TestDrawStratifiedSampleSmallCorpusEdgeCase:
    """Behavior 5 -- corpus smaller than n does not raise."""

    def test_corpus_smaller_than_n_does_not_raise(self, sample_module: ModuleType) -> None:
        rows = [
            make_row(1, decision="delegate", td_len=_SHORT_TD_LEN),
            make_row(2, decision="self_handle", td_len=_MEDIUM_TD_LEN, file_paths=["a.py"]),
            make_row(3, decision="delegate", td_len=_LONG_TD_LEN),
        ]
        result = sample_module.draw_stratified_sample(rows, n=120, seed=1, floor=2)
        assert len(result) <= len(rows)

    def test_floor_minimums_summing_above_n_does_not_raise(
        self, sample_module: ModuleType
    ) -> None:
        # 3 cells x floor(2) = 6 > n=4; the function must not raise, and
        # may simply return more than n (per spec, exact-n is not
        # guaranteed when floor minimums exceed it).
        rows = build_three_cell_corpus()
        result = sample_module.draw_stratified_sample(rows, n=4, seed=1, floor=2)
        assert len(result) <= len(rows)
        assert len(result) > 0


class TestDrawStratifiedSampleReturnsRawUnmodifiedRows:
    """Behavior 6 -- returned rows are the full, unmodified RAW dicts, not
    a stripped view (asserts a non-``input`` key, ``output``, survives).
    """

    def test_output_key_survives_in_returned_rows(self, sample_module: ModuleType) -> None:
        rows = build_three_cell_corpus()
        result = sample_module.draw_stratified_sample(rows, n=6, seed=7, floor=2)
        assert result, "expected a non-empty draw from a 19-row corpus with n=6"
        by_id = {r["corpus_id"]: r for r in rows}
        for drawn in result:
            original = by_id[drawn["corpus_id"]]
            assert "output" in drawn, (
                "output is the matcher's own decision on the RAW corpus -- "
                "the sampler (unlike the strip tool) must not remove it"
            )
            assert drawn["output"] == original["output"]
            assert drawn["input"] == original["input"]
            assert drawn == original, (
                "draw_stratified_sample must return full, unmodified row "
                "dicts, not a stripped/summarized view"
            )

    def test_does_not_mutate_input_rows_list_or_dicts(self, sample_module: ModuleType) -> None:
        rows = build_three_cell_corpus()
        original = json.loads(json.dumps(rows))  # deep copy for comparison
        sample_module.draw_stratified_sample(rows, n=6, seed=7, floor=2)
        assert rows == original, (
            "draw_stratified_sample must not mutate the rows list or the "
            "dicts inside it"
        )


class TestCliLeavesSourceCorpusFileUnmodified:
    """Behavior 7 -- source input JSONL is byte-for-byte unmodified."""

    def test_source_file_bytes_unchanged_after_cli_run(
        self, sample_module: ModuleType, tmp_path: Path
    ) -> None:
        source = tmp_path / "raw-corpus.jsonl"
        output = tmp_path / "sample.jsonl"
        write_jsonl(source, build_three_cell_corpus())

        before_hash = hashlib.sha256(source.read_bytes()).hexdigest()

        rc = sample_module.main(
            ["--input", str(source), "--output", str(output), "--n", "6", "--seed", "7"]
        )

        after_hash = hashlib.sha256(source.read_bytes()).hexdigest()

        assert rc == 0
        assert after_hash == before_hash, (
            "the source raw-corpus file's content changed -- sampling must "
            "never mutate the on-disk source corpus"
        )


class TestCliExclusiveCreate:
    """Behavior 8 -- CLI never overwrites an existing --output path."""

    def test_existing_output_path_raises_file_exists_error(
        self, sample_module: ModuleType, tmp_path: Path
    ) -> None:
        source = tmp_path / "raw-corpus.jsonl"
        output = tmp_path / "sample.jsonl"
        write_jsonl(source, build_three_cell_corpus())
        sentinel_content = "PRE-EXISTING CONTENT -- must not be overwritten\n"
        output.write_text(sentinel_content, encoding="utf-8")

        with pytest.raises(FileExistsError):
            sample_module.main(
                ["--input", str(source), "--output", str(output), "--n", "6", "--seed", "7"]
            )

        assert output.read_text(encoding="utf-8") == sentinel_content, (
            "an existing --output file's content must survive a failed "
            "exclusive-create attempt untouched"
        )


class TestCliRejectsMalformedInputLines:
    """Behavior 9 -- a non-JSON-object input line raises TypeError."""

    def test_json_array_line_raises_type_error(
        self, sample_module: ModuleType, tmp_path: Path
    ) -> None:
        source = tmp_path / "raw-corpus.jsonl"
        output = tmp_path / "sample.jsonl"
        rows = build_three_cell_corpus()
        lines = [json.dumps(r) for r in rows]
        lines.insert(1, json.dumps([1, 2, 3]))  # malformed: a JSON array
        source.write_text("\n".join(lines) + "\n", encoding="utf-8")

        with pytest.raises(TypeError):
            sample_module.main(
                ["--input", str(source), "--output", str(output), "--n", "6", "--seed", "7"]
            )

    def test_json_scalar_line_raises_type_error(
        self, sample_module: ModuleType, tmp_path: Path
    ) -> None:
        source = tmp_path / "raw-corpus.jsonl"
        output = tmp_path / "sample.jsonl"
        rows = build_three_cell_corpus()
        lines = [json.dumps(r) for r in rows]
        lines.insert(1, json.dumps("just a scalar string"))  # malformed
        source.write_text("\n".join(lines) + "\n", encoding="utf-8")

        with pytest.raises(TypeError):
            sample_module.main(
                ["--input", str(source), "--output", str(output), "--n", "6", "--seed", "7"]
            )


class TestCliSeedIsRequired:
    """Behavior 10 -- omitting --seed exits argparse non-zero."""

    def test_missing_seed_exits_nonzero(self, sample_module: ModuleType, tmp_path: Path) -> None:
        source = tmp_path / "raw-corpus.jsonl"
        output = tmp_path / "sample.jsonl"
        write_jsonl(source, build_three_cell_corpus())

        with pytest.raises(SystemExit) as exc_info:
            sample_module.main(["--input", str(source), "--output", str(output)])

        assert exc_info.value.code != 0, (
            "argparse must reject a missing required --seed with a "
            "non-zero exit code"
        )


class TestCliRoundTripMatchesPureFunction:
    """Integration -- the CLI's written output matches
    ``draw_stratified_sample`` called directly on the same rows, seed, n,
    and floor (same order, same corpus_ids).
    """

    def test_cli_output_rows_match_direct_pure_function_call(
        self, sample_module: ModuleType, tmp_path: Path
    ) -> None:
        source = tmp_path / "raw-corpus.jsonl"
        output = tmp_path / "sample.jsonl"
        rows = build_three_cell_corpus()
        write_jsonl(source, rows)

        rc = sample_module.main(
            [
                "--input",
                str(source),
                "--output",
                str(output),
                "--n",
                "6",
                "--seed",
                "99",
                "--floor",
                "2",
            ]
        )
        assert rc == 0

        expected = sample_module.draw_stratified_sample(rows, n=6, seed=99, floor=2)
        actual = read_jsonl(output)

        assert [r["corpus_id"] for r in actual] == [r["corpus_id"] for r in expected]
        assert actual == expected
