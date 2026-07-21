"""Tests for scripts/shadow-strip-for-labeling.py.

Spec source: docs/superpowers/plans/2026-07-19-m15-6-shadow-kc-report.md
Sec 3.1 (independence-hardening mandate) and Sec 3.3 item 2 (Phase A test
fixtures for the strip-and-present script).

The script must turn a raw shadow-corpus row (matcher-decision JSON, one
per line in ``wayfinder-corpus.jsonl``) into a labeler-safe view that
hides every label under test:

1. ``input.domain`` / ``input.posture`` / ``input.confidence`` /
   ``input.area_span`` (the caller labels under test) are stripped.
2. The entire top-level ``output`` field (the matcher's own decision) is
   stripped.
3. The entire top-level ``shadow`` field (the shadow decision dict) is
   stripped.
4. ``input.task_description`` / ``file_paths`` / ``agent_mentions`` /
   ``tool_mentions`` / ``command_prefix`` are retained unaltered, and the
   view stays joinable by top-level ``corpus_id``.
5. Absent/null fields among the four stripped keys must not error and
   must not leave a spurious ``None``-valued key behind.
6. The on-disk corpus file is never mutated in place.

The plan's Sec 3.1 language ("Labelers must see ONLY the raw signal")
is whitelist-shaped, not merely "strip these three named things" --
so this suite also enforces a top-level whitelist of
``{"corpus_id", "input"}`` on the stripped view (judgment call, see the
test-implementer return notes): any other top-level field
(e.g. ``stratum.decision_band``, which reveals the matcher's decision
disposition) is just as much a label-under-test leak as ``output`` or
``shadow``, even though the plan does not name it explicitly.

This test module authors the contract in advance of the implementation
(``scripts/shadow-strip-for-labeling.py`` does not exist yet) per the
test-implementer / code-implementer split -- every test below is
expected to fail at collection time (``ModuleNotFoundError`` /
``FileNotFoundError`` from the module loader) until that script exists.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

# Load the script from its path (it lives under scripts/, is not part of
# the installed package, and its filename is not a valid Python
# identifier), mirroring the loader pattern used by
# tests/test_analyze_drift_causes.py for scripts/analyze-drift-causes.py.
_SCRIPT_PATH = Path(__file__).parent.parent.parent / "scripts" / "shadow-strip-for-labeling.py"

# The four caller-label fields under ``input`` that must be stripped.
_STRIPPED_INPUT_KEYS = ("domain", "posture", "confidence", "area_span")

# The raw-signal fields under ``input`` that must survive stripping,
# unaltered.
_RETAINED_INPUT_KEYS = (
    "task_description",
    "file_paths",
    "agent_mentions",
    "tool_mentions",
    "command_prefix",
)


@pytest.fixture(scope="module")
def strip_module() -> ModuleType:
    """Load ``scripts/shadow-strip-for-labeling.py`` as a module.

    Returns:
        The loaded module, exposing ``strip_row`` and ``main``.

    Raises:
        FileNotFoundError: If the script does not exist yet (the
            expected RED state before the implementation lands).
    """
    spec = importlib.util.spec_from_file_location("shadow_strip_for_labeling", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["shadow_strip_for_labeling"] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        sys.modules.pop("shadow_strip_for_labeling", None)
        raise
    return mod


def make_row(**overrides: Any) -> dict[str, Any]:
    """Build a synthetic corpus row matching the real on-disk shape.

    Shape confirmed against a real row in
    ``~/.claude/state/wayfinder-corpus/2026-06-12/wayfinder-corpus.jsonl``
    (2026-07-20) -- this fixture is a hand-built synthetic stand-in, not
    data read from that file.

    Args:
        **overrides: Top-level keys to override on the default row.
            ``input`` overrides are shallow-merged into the default
            ``input`` dict rather than replacing it wholesale.

    Returns:
        A synthetic corpus row dict.
    """
    row: dict[str, Any] = {
        "type": "matcher_decision",
        "ts": "2026-06-24T22:28:03.749Z",
        "session_id": "1a4af679-2b30-450e-96e9-3a10bc617e13",
        "input": {
            "task_description": "Research popular Claude Code skills.",
            "file_paths": ["src/claude_wayfinder/match/_compose.py"],
            "agent_mentions": ["researcher"],
            "tool_mentions": ["WebSearch", "WebFetch"],
            "command_prefix": None,
            "domain": "is_any",
            "posture": "research",
            "confidence": "high",
            "area_span": 1,
        },
        "output": {
            "agent": "researcher",
            "decision": "delegate",
            "confidence": 1,
            "rationale": "matched keywords: research, scout.",
        },
        "catalog_hash": "sha256:47dc88dc",
        "matcher_version": "6d5f416",
        "corpus_id": 56092,
        "shadow": {
            "shadow_decision": "delegate",
            "shadow_agent": "researcher",
            "posture_routed": True,
            "gated_agent_names": ["researcher", "ops"],
            "branch": "branch3_generic",
            "agreement": True,
        },
        "stratum": {
            "decision_band": "delegate",
            "td_length_band": "long",
            "file_paths_present": False,
        },
    }
    input_overrides = overrides.pop("input", None)
    row.update(overrides)
    if input_overrides is not None:
        row["input"] = {**row["input"], **input_overrides}
    return row


class TestStripRowRemovesCallerLabels:
    """Item 1 -- input.{domain,posture,confidence,area_span} stripped."""

    @pytest.mark.parametrize("key", _STRIPPED_INPUT_KEYS)
    def test_stripped_key_absent_from_input(self, strip_module: ModuleType, key: str) -> None:
        row = make_row()
        stripped = strip_module.strip_row(row)
        assert key not in stripped["input"], (
            f"input.{key} is a caller label under test and must not leak to the labeler"
        )

    def test_all_four_caller_labels_stripped_together(self, strip_module: ModuleType) -> None:
        row = make_row()
        stripped = strip_module.strip_row(row)
        leaked = [k for k in _STRIPPED_INPUT_KEYS if k in stripped["input"]]
        assert leaked == [], f"caller labels leaked to labeler view: {leaked}"


class TestStripRowRemovesMatcherDecisions:
    """Items 2/3 -- top-level output and shadow dicts stripped entirely."""

    def test_output_field_removed(self, strip_module: ModuleType) -> None:
        row = make_row()
        stripped = strip_module.strip_row(row)
        assert "output" not in stripped, (
            "output is the matcher's own decision and predates gold -- "
            "it must not reach the labeler"
        )

    def test_shadow_field_removed(self, strip_module: ModuleType) -> None:
        row = make_row()
        stripped = strip_module.strip_row(row)
        assert "shadow" not in stripped, (
            "shadow is the shadow-mode decision dict and predates gold "
            "-- it must not reach the labeler"
        )

    def test_no_extra_top_level_keys_leak_decision_adjacent_metadata(
        self, strip_module: ModuleType
    ) -> None:
        # Judgment call (see return notes): the plan is explicit that
        # "Labelers must see ONLY the raw signal" -- a whitelist, not
        # merely the three named blacklist items. This matters beyond
        # output/shadow: make_row()'s "stratum" field carries
        # decision_band ("delegate"/"self_handle"), which is the same
        # leakage category as "output" even though the plan's item list
        # never names "stratum" by key. Enforce the top level as a
        # whitelist of {"corpus_id", "input"} so any such
        # decision-adjacent field is caught, not just the two named ones.
        row = make_row()
        stripped = strip_module.strip_row(row)
        allowed_top_level = {"corpus_id", "input"}
        extra = set(stripped.keys()) - allowed_top_level
        assert extra == set(), (
            "labeler-facing view leaked top-level fields beyond "
            f"{allowed_top_level}: {extra} -- e.g. stratum.decision_band "
            "reveals the matcher's decision disposition just like output"
        )


class TestStripRowRetainsRawSignal:
    """Item 4 -- raw-signal input fields retained unaltered, and the
    view stays joinable by corpus_id.
    """

    @pytest.mark.parametrize("key", _RETAINED_INPUT_KEYS)
    def test_retained_key_unaltered(self, strip_module: ModuleType, key: str) -> None:
        row = make_row()
        stripped = strip_module.strip_row(row)
        assert stripped["input"][key] == row["input"][key], (
            f"input.{key} is the only signal a labeler should see and "
            "must survive stripping unaltered"
        )

    def test_corpus_id_retained_at_top_level_for_joining(self, strip_module: ModuleType) -> None:
        # Judgment call (per briefing): corpus_id is not itself a label
        # under test, and plan Sec 3.3 requires the labeling output to be
        # joinable back to the corpus by corpus_id -- so it must survive
        # stripping at the top level.
        row = make_row(corpus_id=99001)
        stripped = strip_module.strip_row(row)
        assert stripped.get("corpus_id") == 99001

    def test_no_extra_keys_added_to_input(self, strip_module: ModuleType) -> None:
        row = make_row()
        stripped = strip_module.strip_row(row)
        expected_keys = set(_RETAINED_INPUT_KEYS)
        assert set(stripped["input"].keys()) <= expected_keys, (
            "the stripped input view must not contain fields beyond the "
            "permitted raw-signal set: "
            f"found extras {set(stripped['input'].keys()) - expected_keys}"
        )


class TestStripRowHandlesAbsentOrNullFields:
    """Item 5 -- absent/null caller-label fields must not error and must
    not leave a spurious present-with-None key behind.
    """

    @pytest.mark.parametrize("key", _STRIPPED_INPUT_KEYS)
    def test_missing_key_does_not_error_and_stays_absent(
        self, strip_module: ModuleType, key: str
    ) -> None:
        row = make_row()
        del row["input"][key]
        stripped = strip_module.strip_row(row)  # must not raise
        assert key not in stripped["input"]

    @pytest.mark.parametrize("key", _STRIPPED_INPUT_KEYS)
    def test_null_valued_key_does_not_error_and_becomes_absent(
        self, strip_module: ModuleType, key: str
    ) -> None:
        row = make_row(input={key: None})
        stripped = strip_module.strip_row(row)  # must not raise
        assert key not in stripped["input"], (
            f"input.{key} was present-with-None on input; after "
            "stripping the key must be absent, not present-with-None"
        )

    def test_missing_output_field_does_not_error(self, strip_module: ModuleType) -> None:
        row = make_row()
        del row["output"]
        stripped = strip_module.strip_row(row)  # must not raise
        assert "output" not in stripped

    def test_missing_shadow_field_does_not_error(self, strip_module: ModuleType) -> None:
        row = make_row()
        del row["shadow"]
        stripped = strip_module.strip_row(row)  # must not raise
        assert "shadow" not in stripped

    def test_null_output_field_does_not_error(self, strip_module: ModuleType) -> None:
        row = make_row(output=None)
        stripped = strip_module.strip_row(row)  # must not raise
        assert "output" not in stripped

    def test_null_shadow_field_does_not_error(self, strip_module: ModuleType) -> None:
        row = make_row(shadow=None)
        stripped = strip_module.strip_row(row)  # must not raise
        assert "shadow" not in stripped


class TestStripRowDoesNotMutateSourceRow:
    """The input row dict passed in must not be mutated -- the caller
    (e.g. the CLI reading the corpus file) may reuse or re-serialize it.
    """

    def test_original_row_unchanged_after_strip(self, strip_module: ModuleType) -> None:
        row = make_row()
        original = json.loads(json.dumps(row))  # deep copy for comparison
        strip_module.strip_row(row)
        assert row == original, (
            "strip_row must not mutate the row it was given -- it produces a new labeler-safe view"
        )


class TestCliLeavesSourceCorpusFileUnmodified:
    """Item 6 -- stripping produces a NEW file; the source corpus file
    on disk must never be mutated in place.
    """

    def _write_corpus(self, path: Path, rows: list[dict[str, Any]]) -> None:
        path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    def test_source_file_bytes_and_mtime_unchanged_after_cli_run(
        self, strip_module: ModuleType, tmp_path: Path
    ) -> None:
        source = tmp_path / "wayfinder-corpus.jsonl"
        output = tmp_path / "wayfinder-corpus.labeling.jsonl"
        self._write_corpus(source, [make_row(corpus_id=1), make_row(corpus_id=2)])

        before_bytes = source.read_bytes()
        before_mtime_ns = os.stat(source).st_mtime_ns

        rc = strip_module.main(["--input", str(source), "--output", str(output)])

        after_bytes = source.read_bytes()
        after_mtime_ns = os.stat(source).st_mtime_ns

        assert rc == 0
        assert after_bytes == before_bytes, (
            "the source corpus file's content changed -- stripping must "
            "never mutate the on-disk corpus in place"
        )
        assert after_mtime_ns == before_mtime_ns, (
            "the source corpus file's mtime changed -- stripping must "
            "never touch (even rewrite-in-place) the on-disk corpus"
        )

    def test_cli_writes_a_separate_output_file(
        self, strip_module: ModuleType, tmp_path: Path
    ) -> None:
        source = tmp_path / "wayfinder-corpus.jsonl"
        output = tmp_path / "wayfinder-corpus.labeling.jsonl"
        self._write_corpus(source, [make_row(corpus_id=1)])

        rc = strip_module.main(["--input", str(source), "--output", str(output)])

        assert rc == 0
        assert output.exists(), "the CLI must write a new output file"
        assert output != source

    def test_cli_output_rows_are_stripped_and_joinable_by_corpus_id(
        self, strip_module: ModuleType, tmp_path: Path
    ) -> None:
        source = tmp_path / "wayfinder-corpus.jsonl"
        output = tmp_path / "wayfinder-corpus.labeling.jsonl"
        rows = [make_row(corpus_id=101), make_row(corpus_id=102)]
        self._write_corpus(source, rows)

        rc = strip_module.main(["--input", str(source), "--output", str(output)])
        assert rc == 0

        out_lines = [
            json.loads(line)
            for line in output.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert len(out_lines) == len(rows)
        out_by_id = {row["corpus_id"]: row for row in out_lines}
        assert set(out_by_id.keys()) == {101, 102}
        for stripped in out_lines:
            assert set(stripped.keys()) <= {"corpus_id", "input"}
            for key in _STRIPPED_INPUT_KEYS:
                assert key not in stripped["input"]
