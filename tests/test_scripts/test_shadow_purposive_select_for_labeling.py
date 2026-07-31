"""Tests for scripts/shadow-purposive-select-for-labeling.py (issue #521).

Spec source: issue #521 body (KC-4/KC-5 coverage gap) plus the
router briefing's eligibility citations into
``scripts/corpus/eval/_kc.py`` -- ``compute_kc4`` (lines 233-275, the
``caller_domain in {"is_any", "project_meta"}`` check at line 255) and
``compute_kc5`` (lines 278-313, the ``gold[row].domain ==
"infra_deploy"`` check at line 295).

KC-4/KC-5 eligibility ultimately depends on a GOLD label, which does
not exist yet for unlabeled pool rows. This script pre-filters an
*unlabeled* raw shadow-corpus JSONL down to the rows most likely to
become KC-4/KC-5-eligible once labeled, using the only pre-label
signal available: each row's own **caller-supplied**
``input.domain`` field (not gold -- the caller's own label, which the
human labeler may later correct). This is a *purposive* (targeted)
filter, distinct from the existing
``scripts/shadow-sample-for-labeling.py``, which draws a proportional
stratified *random* sample -- different tool, not to be confused.

This test module authors the contract in advance of the
implementation (``scripts/shadow-purposive-select-for-labeling.py``
does not exist yet) per the test-implementer / code-implementer split
-- every test below is expected to fail (via the module-scoped
``purposive_module`` fixture raising ``FileNotFoundError`` when it
tries to load the not-yet-existing script) until that script exists.

RED -- written before implementation.

Design judgment calls made by the test-implementer (documented for
the router / code-implementer, since the briefing left some CLI
failure-shape details open):

1. **Exit-code / error-surface shape for CLI failures is left open.**
   The briefing specifies observable CLI contracts ("exit non-zero",
   "error message identifying the bad line number") but not whether
   ``main(argv)`` raises an uncaught exception, raises
   ``SystemExit``, or returns a non-zero int. Tests here tolerate all
   three via the ``_run_main`` / ``_run_main_expect_failure`` helpers
   below (mirroring the ``except SystemExit as exc: rc = exc.code``
   normalization pattern already used in
   ``tests/test_scripts/test_shadow_kc_report.py``), so the suite
   does not lock in one implementation shape.
2. **Bad-line-number indexing assumed 1-based.** "Error message
   identifying the bad line number" doesn't state 0- vs 1-based
   indexing. This suite assumes 1-based (the near-universal
   convention for line numbers shown to a human -- text editors,
   compilers, ``json.JSONDecodeError`` itself), and builds fixtures
   whose corpus_ids avoid the target digit so a substring match on
   the digit is not a coincidental false-positive.
3. **No internal pure-function name is assumed.** Unlike some sibling
   scripts (``draw_stratified_sample``, ``strip_row``), the briefing
   never names an internal selection function -- only the CLI
   surface (``--input``/``--output``/``--kc``). Per the briefing's
   explicit instruction ("the CLI contract above is the source of
   truth regardless of internal factoring"), every test below drives
   the script exclusively through ``main(argv)`` and inspects the
   written output file, so the implementer is free to factor
   selection logic however it wants.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

# Load the script from its path (it lives under scripts/, is not part
# of the installed package, and its filename is not a valid Python
# identifier), mirroring the loader pattern used by
# tests/test_scripts/test_shadow_sample_for_labeling.py for
# scripts/shadow-sample-for-labeling.py.
_SCRIPT_PATH = (
    Path(__file__).parent.parent.parent
    / "scripts"
    / "shadow-purposive-select-for-labeling.py"
)

_KC4_DOMAINS = ("is_any", "project_meta")
_KC5_DOMAIN = "infra_deploy"
_NON_CANDIDATE_DOMAINS = ("code", "docs_prose")


@pytest.fixture(scope="module")
def purposive_module() -> ModuleType:
    """Load ``scripts/shadow-purposive-select-for-labeling.py`` as a module.

    Returns:
        The loaded module, expected to expose ``main(argv) -> int | None``.

    Raises:
        FileNotFoundError: If the script does not exist yet (the
            expected RED state before the implementation lands).
    """
    spec = importlib.util.spec_from_file_location(
        "shadow_purposive_select_for_labeling", _SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["shadow_purposive_select_for_labeling"] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        sys.modules.pop("shadow_purposive_select_for_labeling", None)
        raise
    return mod


def make_row(corpus_id: int, domain: str | None, **extra_top_level: Any) -> dict[str, Any]:
    """Build a synthetic raw shadow-corpus row with a top-level ``input.domain``.

    Args:
        corpus_id: Value for the top-level ``corpus_id`` field.
        domain: Value for ``input.domain``. ``None`` sets the key to a
            JSON ``null`` (present-but-null), distinct from
            ``make_row_without_domain_key`` (key absent entirely).
        **extra_top_level: Additional top-level keys merged onto the
            row, to exercise "all original fields survive" checks
            (e.g. ``output={...}``, ``session_id="..."``).

    Returns:
        A synthetic raw corpus row dict.
    """
    row: dict[str, Any] = {
        "corpus_id": corpus_id,
        "input": {
            "task_description": f"task {corpus_id}",
            "domain": domain,
        },
    }
    row.update(extra_top_level)
    return row


def make_row_without_domain_key(corpus_id: int) -> dict[str, Any]:
    """Build a row whose ``input`` dict has no ``domain`` key at all.

    Args:
        corpus_id: Value for the top-level ``corpus_id`` field.

    Returns:
        A synthetic raw corpus row dict with ``input`` present but no
        ``domain`` key.
    """
    return {
        "corpus_id": corpus_id,
        "input": {"task_description": f"task {corpus_id}"},
    }


def make_row_without_input(corpus_id: int) -> dict[str, Any]:
    """Build a row with no ``input`` key at all.

    Args:
        corpus_id: Value for the top-level ``corpus_id`` field.

    Returns:
        A synthetic raw corpus row dict with no ``input`` key.
    """
    return {"corpus_id": corpus_id}


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


def run_main(module: ModuleType, argv: list[str]) -> int:
    """Invoke ``module.main(argv)``, normalizing ``SystemExit`` to an int.

    Mirrors the ``except SystemExit as exc: rc = exc.code`` pattern
    already used in ``tests/test_scripts/test_shadow_kc_report.py``,
    so tests do not need to know whether the implementation calls
    ``sys.exit`` or simply returns an int.

    Args:
        module: The loaded script module.
        argv: CLI argument list (excluding the program name).

    Returns:
        The normalized exit code (``0`` for a ``None`` return/exit).
    """
    try:
        rc = module.main(argv)
    except SystemExit as exc:
        rc = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    return 0 if rc is None else rc


def run_main_expect_failure(module: ModuleType, argv: list[str]) -> tuple[int, str]:
    """Invoke ``module.main(argv)``, tolerating any "fail loudly" shape.

    A correct implementation may signal a CLI failure by raising an
    uncaught exception, raising ``SystemExit``, or returning a
    non-zero int. This helper accepts all three and returns a single
    normalized ``(exit_code, message)`` pair so tests can assert on
    the failure without coupling to one implementation shape.

    Args:
        module: The loaded script module.
        argv: CLI argument list (excluding the program name).

    Returns:
        ``(exit_code, message)`` where ``exit_code`` is non-zero on
        any recognized failure shape and ``message`` is the
        exception's ``str()`` (empty string if the failure was a
        plain non-zero return).
    """
    try:
        rc = module.main(argv)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
        return code, str(exc.code) if exc.code is not None else ""
    except Exception as exc:  # noqa: BLE001 - any raised exception is a
        # valid "fail loudly" shape per the CLI contract; the specific
        # exception type is an implementation choice this suite does
        # not constrain.
        return 1, str(exc)
    return (0 if rc is None else rc), ""


# ---------------------------------------------------------------------------
# KC-4 candidate selection
# ---------------------------------------------------------------------------


class TestKc4CandidateSelection:
    """A row is a KC-4 candidate iff input.domain is 'is_any' or
    'project_meta' -- tagged purposive_kc == 'KC-4'.
    """

    @pytest.mark.parametrize("domain", _KC4_DOMAINS)
    def test_kc4_domain_selected_and_tagged(
        self, purposive_module: ModuleType, tmp_path: Path, domain: str
    ) -> None:
        source = tmp_path / "corpus.jsonl"
        output = tmp_path / "selected.jsonl"
        write_jsonl(source, [make_row(1, domain=domain)])

        rc = run_main(purposive_module, ["--input", str(source), "--output", str(output)])

        assert rc == 0
        result = read_jsonl(output)
        assert len(result) == 1, f"domain={domain!r} must be selected as a KC-4 candidate"
        assert result[0]["purposive_kc"] == "KC-4"

    @pytest.mark.parametrize("domain", _NON_CANDIDATE_DOMAINS)
    def test_non_kc4_domain_not_selected(
        self, purposive_module: ModuleType, tmp_path: Path, domain: str
    ) -> None:
        source = tmp_path / "corpus.jsonl"
        output = tmp_path / "selected.jsonl"
        write_jsonl(source, [make_row(1, domain=domain)])

        rc = run_main(purposive_module, ["--input", str(source), "--output", str(output)])

        assert rc == 0
        assert read_jsonl(output) == [], f"domain={domain!r} must not be a KC-4/KC-5 candidate"


# ---------------------------------------------------------------------------
# KC-5 candidate selection
# ---------------------------------------------------------------------------


class TestKc5CandidateSelection:
    """A row is a KC-5 candidate iff input.domain == 'infra_deploy' --
    tagged purposive_kc == 'KC-5'.
    """

    def test_infra_deploy_domain_selected_and_tagged(
        self, purposive_module: ModuleType, tmp_path: Path
    ) -> None:
        source = tmp_path / "corpus.jsonl"
        output = tmp_path / "selected.jsonl"
        write_jsonl(source, [make_row(1, domain=_KC5_DOMAIN)])

        rc = run_main(purposive_module, ["--input", str(source), "--output", str(output)])

        assert rc == 0
        result = read_jsonl(output)
        assert len(result) == 1
        assert result[0]["purposive_kc"] == "KC-5"

    @pytest.mark.parametrize("domain", _NON_CANDIDATE_DOMAINS)
    def test_non_kc5_domain_not_selected(
        self, purposive_module: ModuleType, tmp_path: Path, domain: str
    ) -> None:
        source = tmp_path / "corpus.jsonl"
        output = tmp_path / "selected.jsonl"
        write_jsonl(source, [make_row(1, domain=domain)])

        rc = run_main(purposive_module, ["--input", str(source), "--output", str(output)])

        assert rc == 0
        assert read_jsonl(output) == []


# ---------------------------------------------------------------------------
# Absent / null domain -- never a candidate for either
# ---------------------------------------------------------------------------


class TestAbsentOrNullDomainNotCandidate:
    """A row with missing input, missing input.domain, or null
    input.domain is not a candidate for either KC.
    """

    def test_missing_input_key_not_selected(
        self, purposive_module: ModuleType, tmp_path: Path
    ) -> None:
        source = tmp_path / "corpus.jsonl"
        output = tmp_path / "selected.jsonl"
        write_jsonl(source, [make_row_without_input(1)])

        rc = run_main(purposive_module, ["--input", str(source), "--output", str(output)])

        assert rc == 0
        assert read_jsonl(output) == []

    def test_missing_domain_key_not_selected(
        self, purposive_module: ModuleType, tmp_path: Path
    ) -> None:
        source = tmp_path / "corpus.jsonl"
        output = tmp_path / "selected.jsonl"
        write_jsonl(source, [make_row_without_domain_key(1)])

        rc = run_main(purposive_module, ["--input", str(source), "--output", str(output)])

        assert rc == 0
        assert read_jsonl(output) == []

    def test_null_domain_value_not_selected(
        self, purposive_module: ModuleType, tmp_path: Path
    ) -> None:
        source = tmp_path / "corpus.jsonl"
        output = tmp_path / "selected.jsonl"
        write_jsonl(source, [make_row(1, domain=None)])

        rc = run_main(purposive_module, ["--input", str(source), "--output", str(output)])

        assert rc == 0
        assert read_jsonl(output) == []


# ---------------------------------------------------------------------------
# Mutual exclusivity invariant over a mixed fixture
# ---------------------------------------------------------------------------


class TestMutualExclusivityInvariant:
    """No row can match both KC-4 and KC-5 criteria -- verified over a
    mixed fixture spanning every domain category, not merely trusted
    from the per-domain unit tests above.
    """

    def test_no_corpus_id_tagged_both_kc4_and_kc5(
        self, purposive_module: ModuleType, tmp_path: Path
    ) -> None:
        source = tmp_path / "corpus.jsonl"
        output = tmp_path / "selected.jsonl"
        rows = [
            make_row(1, domain="is_any"),
            make_row(2, domain="project_meta"),
            make_row(3, domain="infra_deploy"),
            make_row(4, domain="code"),
            make_row(5, domain="docs_prose"),
            make_row(6, domain=None),
            make_row_without_domain_key(7),
            make_row_without_input(8),
        ]
        write_jsonl(source, rows)

        rc = run_main(purposive_module, ["--input", str(source), "--output", str(output)])

        assert rc == 0
        result = read_jsonl(output)

        kc4_ids = {r["corpus_id"] for r in result if r["purposive_kc"] == "KC-4"}
        kc5_ids = {r["corpus_id"] for r in result if r["purposive_kc"] == "KC-5"}

        assert kc4_ids == {1, 2}
        assert kc5_ids == {3}
        assert kc4_ids.isdisjoint(kc5_ids), (
            "a corpus_id appeared in both the KC-4 and KC-5 tagged sets -- "
            "domain values are mutually exclusive by construction and must "
            "never double-classify a row"
        )

        # No row appears more than once in the output (no duplication
        # across the two candidate checks).
        all_ids = [r["corpus_id"] for r in result]
        assert len(all_ids) == len(set(all_ids)), (
            f"a corpus_id was written to the output more than once: {all_ids}"
        )


# ---------------------------------------------------------------------------
# Output field preservation
# ---------------------------------------------------------------------------


class TestOutputFieldsPreserved:
    """Every selected row's original fields survive, plus purposive_kc
    is added.
    """

    def test_all_original_top_level_fields_survive(
        self, purposive_module: ModuleType, tmp_path: Path
    ) -> None:
        source = tmp_path / "corpus.jsonl"
        output = tmp_path / "selected.jsonl"
        row = make_row(
            1,
            domain="is_any",
            session_id="session-1",
            ts="2026-07-30T00:00:00Z",
            output={"agent": "researcher", "decision": "delegate"},
            matcher_version="abc1234",
        )
        write_jsonl(source, [row])

        rc = run_main(purposive_module, ["--input", str(source), "--output", str(output)])

        assert rc == 0
        result = read_jsonl(output)
        assert len(result) == 1
        selected = result[0]

        for key, value in row.items():
            assert selected[key] == value, f"original field {key!r} was not preserved unaltered"
        assert selected["purposive_kc"] == "KC-4"

    def test_output_row_has_exactly_original_fields_plus_purposive_kc(
        self, purposive_module: ModuleType, tmp_path: Path
    ) -> None:
        source = tmp_path / "corpus.jsonl"
        output = tmp_path / "selected.jsonl"
        row = make_row(1, domain="infra_deploy", session_id="session-1")
        write_jsonl(source, [row])

        rc = run_main(purposive_module, ["--input", str(source), "--output", str(output)])

        assert rc == 0
        selected = read_jsonl(output)[0]
        assert set(selected.keys()) == set(row.keys()) | {"purposive_kc"}

    def test_source_input_file_bytes_unchanged(
        self, purposive_module: ModuleType, tmp_path: Path
    ) -> None:
        source = tmp_path / "corpus.jsonl"
        output = tmp_path / "selected.jsonl"
        write_jsonl(source, [make_row(1, domain="is_any"), make_row(2, domain="code")])
        before = source.read_bytes()

        rc = run_main(purposive_module, ["--input", str(source), "--output", str(output)])

        assert rc == 0
        assert source.read_bytes() == before, (
            "the source corpus file's content changed -- selection must "
            "never mutate the on-disk source corpus"
        )


# ---------------------------------------------------------------------------
# Row order preserved (filter, not a shuffled sample)
# ---------------------------------------------------------------------------


class TestOutputOrderPreserved:
    """Row order in output matches row order in input."""

    def test_output_order_matches_input_order_for_kc_both(
        self, purposive_module: ModuleType, tmp_path: Path
    ) -> None:
        source = tmp_path / "corpus.jsonl"
        output = tmp_path / "selected.jsonl"
        # Interleave KC-4, KC-5, and non-candidate rows so a naive
        # "group by kind" implementation would visibly reorder them.
        rows = [
            make_row(1, domain="infra_deploy"),  # KC-5
            make_row(2, domain="code"),  # excluded
            make_row(3, domain="is_any"),  # KC-4
            make_row(4, domain="project_meta"),  # KC-4
            make_row(5, domain="infra_deploy"),  # KC-5
        ]
        write_jsonl(source, rows)

        rc = run_main(
            purposive_module, ["--input", str(source), "--output", str(output), "--kc", "both"]
        )

        assert rc == 0
        result_ids = [r["corpus_id"] for r in read_jsonl(output)]
        assert result_ids == [1, 3, 4, 5], (
            "output must preserve input row order (a filter, not a "
            "shuffled/regrouped sample)"
        )


# ---------------------------------------------------------------------------
# --kc flag restricts selection
# ---------------------------------------------------------------------------


class TestKcFlagRestrictsSelection:
    """--kc 4 -> KC-4 only; --kc 5 -> KC-5 only; --kc both / omitted ->
    both, each order-preserved and tagged correctly.
    """

    def _mixed_rows(self) -> list[dict[str, Any]]:
        return [
            make_row(1, domain="is_any"),
            make_row(2, domain="infra_deploy"),
            make_row(3, domain="project_meta"),
            make_row(4, domain="code"),
            make_row(5, domain="infra_deploy"),
        ]

    def test_kc_4_excludes_kc5_candidates(
        self, purposive_module: ModuleType, tmp_path: Path
    ) -> None:
        source = tmp_path / "corpus.jsonl"
        output = tmp_path / "selected.jsonl"
        write_jsonl(source, self._mixed_rows())

        rc = run_main(
            purposive_module, ["--input", str(source), "--output", str(output), "--kc", "4"]
        )

        assert rc == 0
        result = read_jsonl(output)
        assert [r["corpus_id"] for r in result] == [1, 3]
        assert all(r["purposive_kc"] == "KC-4" for r in result)

    def test_kc_5_excludes_kc4_candidates(
        self, purposive_module: ModuleType, tmp_path: Path
    ) -> None:
        source = tmp_path / "corpus.jsonl"
        output = tmp_path / "selected.jsonl"
        write_jsonl(source, self._mixed_rows())

        rc = run_main(
            purposive_module, ["--input", str(source), "--output", str(output), "--kc", "5"]
        )

        assert rc == 0
        result = read_jsonl(output)
        assert [r["corpus_id"] for r in result] == [2, 5]
        assert all(r["purposive_kc"] == "KC-5" for r in result)

    def test_kc_both_explicit_includes_both_kinds(
        self, purposive_module: ModuleType, tmp_path: Path
    ) -> None:
        source = tmp_path / "corpus.jsonl"
        output = tmp_path / "selected.jsonl"
        write_jsonl(source, self._mixed_rows())

        rc = run_main(
            purposive_module, ["--input", str(source), "--output", str(output), "--kc", "both"]
        )

        assert rc == 0
        result = read_jsonl(output)
        assert [r["corpus_id"] for r in result] == [1, 2, 3, 5]
        tags = {r["corpus_id"]: r["purposive_kc"] for r in result}
        assert tags == {1: "KC-4", 2: "KC-5", 3: "KC-4", 5: "KC-5"}

    def test_kc_flag_omitted_defaults_to_both(
        self, purposive_module: ModuleType, tmp_path: Path
    ) -> None:
        source = tmp_path / "corpus.jsonl"
        output = tmp_path / "selected.jsonl"
        write_jsonl(source, self._mixed_rows())

        rc = run_main(purposive_module, ["--input", str(source), "--output", str(output)])

        assert rc == 0
        result = read_jsonl(output)
        assert [r["corpus_id"] for r in result] == [1, 2, 3, 5], (
            "omitting --kc must default to 'both', not to zero rows or "
            "just one kind"
        )

    def test_invalid_kc_value_rejected(
        self, purposive_module: ModuleType, tmp_path: Path
    ) -> None:
        source = tmp_path / "corpus.jsonl"
        output = tmp_path / "selected.jsonl"
        write_jsonl(source, self._mixed_rows())

        exit_code, _ = run_main_expect_failure(
            purposive_module,
            ["--input", str(source), "--output", str(output), "--kc", "6"],
        )
        assert exit_code != 0, "--kc is constrained to {4,5,both}; other values must be rejected"


# ---------------------------------------------------------------------------
# Required arguments
# ---------------------------------------------------------------------------


class TestRequiredArguments:
    """--input and --output are both required."""

    def test_missing_input_arg_rejected(
        self, purposive_module: ModuleType, tmp_path: Path
    ) -> None:
        output = tmp_path / "selected.jsonl"
        exit_code, _ = run_main_expect_failure(purposive_module, ["--output", str(output)])
        assert exit_code != 0

    def test_missing_output_arg_rejected(
        self, purposive_module: ModuleType, tmp_path: Path
    ) -> None:
        source = tmp_path / "corpus.jsonl"
        write_jsonl(source, [make_row(1, domain="is_any")])
        exit_code, _ = run_main_expect_failure(purposive_module, ["--input", str(source)])
        assert exit_code != 0


# ---------------------------------------------------------------------------
# Zero matching rows is not an error
# ---------------------------------------------------------------------------


class TestZeroMatchingRowsIsNotAnError:
    """An input with no KC-4/KC-5 candidates still creates an (empty)
    output file and exits 0.
    """

    def test_creates_empty_output_file_and_exits_zero(
        self, purposive_module: ModuleType, tmp_path: Path
    ) -> None:
        source = tmp_path / "corpus.jsonl"
        output = tmp_path / "selected.jsonl"
        write_jsonl(
            source,
            [make_row(1, domain="code"), make_row(2, domain="docs_prose")],
        )

        rc = run_main(purposive_module, ["--input", str(source), "--output", str(output)])

        assert rc == 0
        assert output.exists(), "output file must be created even with zero matches"
        assert read_jsonl(output) == []


# ---------------------------------------------------------------------------
# Exclusive-create output -- never overwrites an existing path
# ---------------------------------------------------------------------------


class TestCliExclusiveCreateOutput:
    """--output must open in exclusive-create ('x') mode; a pre-existing
    path must be left byte-for-byte unchanged and the script must
    exit non-zero.
    """

    def test_existing_output_path_is_rejected_and_left_untouched(
        self, purposive_module: ModuleType, tmp_path: Path
    ) -> None:
        source = tmp_path / "corpus.jsonl"
        output = tmp_path / "selected.jsonl"
        write_jsonl(source, [make_row(1, domain="is_any")])
        sentinel = "PRE-EXISTING CONTENT -- must not be overwritten\n"
        output.write_text(sentinel, encoding="utf-8")
        before_bytes = output.read_bytes()

        exit_code, _ = run_main_expect_failure(
            purposive_module, ["--input", str(source), "--output", str(output)]
        )

        assert exit_code != 0, "a pre-existing --output path must cause a non-zero exit"
        assert output.read_bytes() == before_bytes, (
            "an existing --output file's content must survive a failed "
            "exclusive-create attempt byte-for-byte unchanged"
        )


# ---------------------------------------------------------------------------
# Malformed JSON input lines fail loudly, identifying the line number
# ---------------------------------------------------------------------------


class TestCliRejectsMalformedInputLines:
    """A malformed JSON line must cause a non-zero exit with an error
    message identifying the bad line number -- never a silent skip.
    """

    def test_malformed_line_causes_nonzero_exit(
        self, purposive_module: ModuleType, tmp_path: Path
    ) -> None:
        source = tmp_path / "corpus.jsonl"
        output = tmp_path / "selected.jsonl"
        # Valid rows on lines 1-3 and 5-6; a syntactically-broken JSON
        # line on line 4. corpus_ids deliberately avoid the digit '4'
        # so a bare digit-match on the error message isn't a
        # coincidental false positive from an id like 104.
        lines = [
            json.dumps(make_row(101, domain="is_any")),
            json.dumps(make_row(102, domain="code")),
            json.dumps(make_row(103, domain="infra_deploy")),
            '{"corpus_id": 999, "input": {domain: is_any}',  # malformed: line 4
            json.dumps(make_row(105, domain="project_meta")),
            json.dumps(make_row(106, domain="code")),
        ]
        source.write_text("\n".join(lines) + "\n", encoding="utf-8")

        exit_code, _ = run_main_expect_failure(
            purposive_module, ["--input", str(source), "--output", str(output)]
        )

        assert exit_code != 0, (
            "a malformed JSON input line must cause a non-zero exit, "
            "never a silent skip of that line"
        )

    def test_malformed_line_error_identifies_the_line_number(
        self, purposive_module: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        source = tmp_path / "corpus.jsonl"
        output = tmp_path / "selected.jsonl"
        lines = [
            json.dumps(make_row(101, domain="is_any")),
            json.dumps(make_row(102, domain="code")),
            json.dumps(make_row(103, domain="infra_deploy")),
            '{"corpus_id": 999, "input": {domain: is_any}',  # malformed: line 4
            json.dumps(make_row(105, domain="project_meta")),
            json.dumps(make_row(106, domain="code")),
        ]
        source.write_text("\n".join(lines) + "\n", encoding="utf-8")

        exit_code, message = run_main_expect_failure(
            purposive_module, ["--input", str(source), "--output", str(output)]
        )
        captured = capsys.readouterr()
        combined = "\n".join([message, captured.out, captured.err])

        assert exit_code != 0
        # 1-based line number of the malformed line, as a standalone
        # digit token (not a substring of a larger number like 104).
        assert re.search(r"(?<!\d)4(?!\d)", combined), (
            "expected the error output to identify line 4 (1-based) as "
            f"the malformed line; got: {combined!r}"
        )
