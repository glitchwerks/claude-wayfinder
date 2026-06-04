"""Tests for the ``python -m claude_wayfinder dispatch --batch`` flag.

Verifies the NDJSON batch processing contract:

- ``--batch`` flag is present and documented under ``--help``.
- Reads NDJSON dispatch contexts from stdin (one per line).
- Writes NDJSON decisions to stdout (one per line) in input order.
- Each output line carries an ``input_index`` field (0-based line number).
- Blank lines in stdin are silently skipped.
- Malformed JSON lines produce an error record and do not crash the batch.
- Exit code 0 when every input line produces a decision or an error record.
- Exit code non-zero on hard errors (no catalog, malformed CLI args).
- Catalog is loaded exactly once per invocation (not once per line).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Constants / Helpers
# ---------------------------------------------------------------------------

_TESTS_DIR = Path(__file__).resolve().parent
_FIXTURES_DIR = _TESTS_DIR / "fixtures"

_DEMO_CATALOG_PATH = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "claude_wayfinder"
    / "fixtures"
    / "demo-catalog.json"
)

_CATALOG_ERROR_PREFIX = "[CATALOG ERROR]"

#: Minimal valid dispatch context dicts for use in tests.
_CONTEXT_A: dict[str, Any] = {
    "task_description": "implement the authentication module",
    "file_paths": ["src/auth.py"],
    "agent_mentions": [],
    "tool_mentions": [],
    "command_prefix": None,
}

_CONTEXT_B: dict[str, Any] = {
    "task_description": "write unit tests for the database layer",
    "file_paths": [],
    "agent_mentions": [],
    "tool_mentions": [],
    "command_prefix": None,
}

_CONTEXT_C: dict[str, Any] = {
    "task_description": "fix the CSS layout bug on mobile",
    "file_paths": ["src/styles.css"],
    "agent_mentions": [],
    "tool_mentions": [],
    "command_prefix": None,
}


def _ndjson(*contexts: dict[str, Any]) -> str:
    """Serialise dicts as NDJSON (one JSON object per line).

    Args:
        *contexts: Dicts to serialise.

    Returns:
        String with each dict serialised as one line, joined by newlines.
    """
    return "\n".join(json.dumps(ctx) for ctx in contexts) + "\n"


def _run_batch(
    stdin_data: str,
    env_overrides: dict[str, str | None] | None = None,
    extra_args: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ``python -m claude_wayfinder dispatch --batch`` and return result.

    Args:
        stdin_data: NDJSON string to pass on stdin.
        env_overrides: Mapping of env var names to values (or ``None`` to
            unset a variable) to layer on top of the current environment.
        extra_args: Additional CLI arguments to append after ``--batch``
            (e.g. ``["--demo"]``).

    Returns:
        A ``CompletedProcess`` with ``stdout`` and ``stderr`` captured as
        strings.
    """
    env = os.environ.copy()
    # Always strip DISPATCH_CATALOG_PATH and CLAUDE_HOME so tests that want
    # demo mode or canonical-fallback mode are not polluted by the caller's
    # environment.
    env.pop("DISPATCH_CATALOG_PATH", None)
    env.pop("CLAUDE_HOME", None)

    if env_overrides:
        for key, val in env_overrides.items():
            if val is None:
                env.pop(key, None)
            else:
                env[key] = val

    cmd = [sys.executable, "-m", "claude_wayfinder", "dispatch", "--batch"]
    if extra_args:
        cmd.extend(extra_args)

    return subprocess.run(
        cmd,
        input=stdin_data,
        capture_output=True,
        text=True,
        env=env,
    )


def _parse_ndjson_stdout(stdout: str) -> list[dict[str, Any]]:
    """Parse NDJSON stdout into a list of dicts, skipping blank lines.

    Args:
        stdout: Raw stdout string from a batch run.

    Returns:
        List of parsed dicts, one per non-blank line.
    """
    results: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        stripped = line.strip()
        if stripped:
            results.append(json.loads(stripped))
    return results


# ---------------------------------------------------------------------------
# Help / flag registration
# ---------------------------------------------------------------------------


class TestBatchFlagHelp:
    """``--batch`` must appear in the ``dispatch`` subcommand help text."""

    def test_batch_flag_appears_in_dispatch_help(self) -> None:
        """``python -m claude_wayfinder dispatch --help`` must mention --batch."""
        result = subprocess.run(
            [sys.executable, "-m", "claude_wayfinder", "dispatch", "--help"],
            capture_output=True,
            text=True,
        )
        assert "--batch" in result.stdout, (
            "Expected '--batch' to appear in dispatch --help output.\n"
            f"stdout: {result.stdout!r}"
        )


# ---------------------------------------------------------------------------
# Happy-path: ordered NDJSON output with input_index
# ---------------------------------------------------------------------------


class TestBatchHappyPath:
    """Valid NDJSON input must produce one decision per line, in order."""

    def test_two_lines_produce_two_decisions(self) -> None:
        """Two NDJSON input lines produce two output lines."""
        stdin = _ndjson(_CONTEXT_A, _CONTEXT_B)
        result = _run_batch(
            stdin,
            env_overrides={
                "DISPATCH_CATALOG_PATH": str(_DEMO_CATALOG_PATH)
            },
        )
        assert result.returncode == 0, (
            f"Batch exited {result.returncode}.\n"
            f"stderr: {result.stderr}"
        )
        decisions = _parse_ndjson_stdout(result.stdout)
        assert len(decisions) == 2, (
            f"Expected 2 output lines, got {len(decisions)}.\n"
            f"stdout: {result.stdout!r}"
        )

    def test_output_preserves_input_order(self) -> None:
        """Output lines appear in the same order as the input lines."""
        stdin = _ndjson(_CONTEXT_A, _CONTEXT_B, _CONTEXT_C)
        result = _run_batch(
            stdin,
            env_overrides={
                "DISPATCH_CATALOG_PATH": str(_DEMO_CATALOG_PATH)
            },
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        decisions = _parse_ndjson_stdout(result.stdout)
        assert len(decisions) == 3, (
            f"Expected 3 output lines, got {len(decisions)}.\n"
            f"stdout: {result.stdout!r}"
        )
        indices = [d["input_index"] for d in decisions]
        assert indices == [0, 1, 2], (
            f"input_index values out of order: {indices}"
        )

    def test_each_output_has_input_index_field(self) -> None:
        """Every output decision must carry an ``input_index`` field."""
        stdin = _ndjson(_CONTEXT_A, _CONTEXT_B)
        result = _run_batch(
            stdin,
            env_overrides={
                "DISPATCH_CATALOG_PATH": str(_DEMO_CATALOG_PATH)
            },
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        decisions = _parse_ndjson_stdout(result.stdout)
        for i, dec in enumerate(decisions):
            assert "input_index" in dec, (
                f"Output line {i} missing 'input_index'.\n"
                f"line: {dec!r}"
            )

    def test_input_index_values_are_zero_based(self) -> None:
        """``input_index`` must be 0-based (first line = 0)."""
        stdin = _ndjson(_CONTEXT_A, _CONTEXT_B)
        result = _run_batch(
            stdin,
            env_overrides={
                "DISPATCH_CATALOG_PATH": str(_DEMO_CATALOG_PATH)
            },
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        decisions = _parse_ndjson_stdout(result.stdout)
        assert decisions[0]["input_index"] == 0, (
            f"Expected first decision to have input_index=0, "
            f"got {decisions[0]['input_index']}"
        )
        assert decisions[1]["input_index"] == 1, (
            f"Expected second decision to have input_index=1, "
            f"got {decisions[1]['input_index']}"
        )

    def test_each_output_has_decision_field(self) -> None:
        """Every output decision must carry the standard ``decision`` field."""
        stdin = _ndjson(_CONTEXT_A, _CONTEXT_B)
        result = _run_batch(
            stdin,
            env_overrides={
                "DISPATCH_CATALOG_PATH": str(_DEMO_CATALOG_PATH)
            },
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        decisions = _parse_ndjson_stdout(result.stdout)
        for i, dec in enumerate(decisions):
            assert "decision" in dec, (
                f"Output line {i} missing 'decision' key.\n"
                f"line: {dec!r}"
            )

    def test_exit_code_zero_on_full_success(self) -> None:
        """Exit code must be 0 when all lines produce decisions."""
        stdin = _ndjson(_CONTEXT_A, _CONTEXT_B)
        result = _run_batch(
            stdin,
            env_overrides={
                "DISPATCH_CATALOG_PATH": str(_DEMO_CATALOG_PATH)
            },
        )
        assert result.returncode == 0, (
            f"Expected exit code 0, got {result.returncode}.\n"
            f"stderr: {result.stderr}"
        )

    def test_each_output_includes_catalog_hash(self) -> None:
        """Every batch output decision must carry a ``catalog_hash`` field
        matching ``sha256:<64 hex chars>`` (issue #311 — consistency with
        single-mode fix).
        """
        import re

        stdin = _ndjson(_CONTEXT_A, _CONTEXT_B)
        result = _run_batch(
            stdin,
            env_overrides={
                "DISPATCH_CATALOG_PATH": str(_DEMO_CATALOG_PATH)
            },
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        decisions = _parse_ndjson_stdout(result.stdout)
        for i, dec in enumerate(decisions):
            assert "catalog_hash" in dec, (
                f"Output line {i} missing 'catalog_hash' key (issue #311).\n"
                f"line: {dec!r}"
            )
            assert re.match(r"^sha256:[0-9a-f]{64}$", dec["catalog_hash"]), (
                f"Output line {i} catalog_hash malformed: "
                f"{dec['catalog_hash']!r}"
            )

    def test_each_output_includes_matcher_version(self) -> None:
        """Every batch output decision must carry a non-empty
        ``matcher_version`` string (issue #311 — consistency with
        single-mode fix).
        """
        stdin = _ndjson(_CONTEXT_A, _CONTEXT_B)
        result = _run_batch(
            stdin,
            env_overrides={
                "DISPATCH_CATALOG_PATH": str(_DEMO_CATALOG_PATH)
            },
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        decisions = _parse_ndjson_stdout(result.stdout)
        for i, dec in enumerate(decisions):
            assert "matcher_version" in dec, (
                f"Output line {i} missing 'matcher_version' key (issue #311).\n"
                f"line: {dec!r}"
            )
            assert isinstance(dec["matcher_version"], str) and dec["matcher_version"], (
                f"Output line {i} matcher_version must be a non-empty string, "
                f"got: {dec['matcher_version']!r}"
            )


# ---------------------------------------------------------------------------
# Blank-line handling
# ---------------------------------------------------------------------------


class TestBatchBlankLines:
    """Blank lines in stdin must be silently skipped."""

    def test_blank_lines_skipped(self) -> None:
        """Blank lines between context lines produce no output lines."""
        stdin = (
            json.dumps(_CONTEXT_A) + "\n"
            "\n"
            "   \n"
            + json.dumps(_CONTEXT_B) + "\n"
        )
        result = _run_batch(
            stdin,
            env_overrides={
                "DISPATCH_CATALOG_PATH": str(_DEMO_CATALOG_PATH)
            },
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        decisions = _parse_ndjson_stdout(result.stdout)
        assert len(decisions) == 2, (
            f"Expected 2 decisions (blank lines skipped), "
            f"got {len(decisions)}.\nstdout: {result.stdout!r}"
        )

    def test_blank_lines_do_not_shift_input_index(self) -> None:
        """input_index must reflect position among non-blank lines only."""
        stdin = (
            json.dumps(_CONTEXT_A) + "\n"
            "\n"
            + json.dumps(_CONTEXT_B) + "\n"
        )
        result = _run_batch(
            stdin,
            env_overrides={
                "DISPATCH_CATALOG_PATH": str(_DEMO_CATALOG_PATH)
            },
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        decisions = _parse_ndjson_stdout(result.stdout)
        # input_index counts non-blank lines, so first non-blank=0,
        # second non-blank=1, regardless of blank lines in between.
        assert decisions[0]["input_index"] == 0
        assert decisions[1]["input_index"] == 1


# ---------------------------------------------------------------------------
# Malformed-line resilience
# ---------------------------------------------------------------------------


class TestBatchMalformedLines:
    """A malformed JSON line must produce an error record without aborting."""

    def test_malformed_line_produces_error_record(self) -> None:
        """A bad JSON line emits an error record (has 'error' key)."""
        stdin = (
            json.dumps(_CONTEXT_A) + "\n"
            "THIS IS NOT JSON\n"
            + json.dumps(_CONTEXT_B) + "\n"
        )
        result = _run_batch(
            stdin,
            env_overrides={
                "DISPATCH_CATALOG_PATH": str(_DEMO_CATALOG_PATH)
            },
        )
        # Must not crash — exit code 0 (partial success is still success)
        assert result.returncode == 0, (
            f"Expected exit 0 on partial batch with one bad line, "
            f"got {result.returncode}.\nstderr: {result.stderr}"
        )
        lines = _parse_ndjson_stdout(result.stdout)
        # We expect 3 output lines: decision, error-record, decision
        assert len(lines) == 3, (
            f"Expected 3 output lines, got {len(lines)}.\n"
            f"stdout: {result.stdout!r}"
        )
        error_line = lines[1]
        assert "error" in error_line, (
            f"Expected 'error' key in malformed-line record.\n"
            f"record: {error_line!r}"
        )

    def test_malformed_line_carries_input_index(self) -> None:
        """Error record for a malformed line must include ``input_index``."""
        stdin = (
            json.dumps(_CONTEXT_A) + "\n"
            "BAD LINE\n"
            + json.dumps(_CONTEXT_B) + "\n"
        )
        result = _run_batch(
            stdin,
            env_overrides={
                "DISPATCH_CATALOG_PATH": str(_DEMO_CATALOG_PATH)
            },
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        lines = _parse_ndjson_stdout(result.stdout)
        error_rec = lines[1]
        assert "input_index" in error_rec, (
            f"Error record missing 'input_index'.\nrecord: {error_rec!r}"
        )
        assert error_rec["input_index"] == 1, (
            f"Expected input_index=1 on error record, "
            f"got {error_rec['input_index']}"
        )

    def test_good_lines_still_get_decisions_after_bad_line(self) -> None:
        """Lines after a malformed line still produce decisions."""
        stdin = (
            "NOT JSON\n"
            + json.dumps(_CONTEXT_B) + "\n"
        )
        result = _run_batch(
            stdin,
            env_overrides={
                "DISPATCH_CATALOG_PATH": str(_DEMO_CATALOG_PATH)
            },
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        lines = _parse_ndjson_stdout(result.stdout)
        assert len(lines) == 2, (
            f"Expected 2 output lines, got {len(lines)}.\n"
            f"stdout: {result.stdout!r}"
        )
        # The second line (index 1) should be a valid decision.
        good_decision = lines[1]
        assert "decision" in good_decision, (
            f"Expected 'decision' in output for valid line after bad one.\n"
            f"record: {good_decision!r}"
        )

    def test_malformed_line_error_record_has_input_line(self) -> None:
        """Error record must include ``input_line`` with the raw bad line."""
        bad_line = "{ not valid json !!!"
        stdin = bad_line + "\n"
        result = _run_batch(
            stdin,
            env_overrides={
                "DISPATCH_CATALOG_PATH": str(_DEMO_CATALOG_PATH)
            },
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        lines = _parse_ndjson_stdout(result.stdout)
        assert len(lines) == 1
        error_rec = lines[0]
        assert "input_line" in error_rec, (
            f"Error record missing 'input_line'.\nrecord: {error_rec!r}"
        )
        assert error_rec["input_line"] == bad_line, (
            f"Expected input_line={bad_line!r}, "
            f"got {error_rec['input_line']!r}"
        )


# ---------------------------------------------------------------------------
# Hard-error mode (no catalog)
# ---------------------------------------------------------------------------


class TestBatchHardErrors:
    """Hard errors (no catalog, bad catalog path) must exit non-zero."""

    def test_missing_catalog_exits_nonzero(self, tmp_path: Path) -> None:
        """Batch exits non-zero when catalog file is missing."""
        missing = tmp_path / "nonexistent-catalog.json"
        result = _run_batch(
            _ndjson(_CONTEXT_A),
            env_overrides={"DISPATCH_CATALOG_PATH": str(missing)},
        )
        assert result.returncode != 0, (
            f"Expected non-zero exit for missing catalog, "
            f"got 0.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_missing_catalog_emits_catalog_error(
        self, tmp_path: Path
    ) -> None:
        """[CATALOG ERROR] appears on stderr when catalog is missing."""
        missing = tmp_path / "nonexistent-catalog.json"
        result = _run_batch(
            _ndjson(_CONTEXT_A),
            env_overrides={"DISPATCH_CATALOG_PATH": str(missing)},
        )
        assert _CATALOG_ERROR_PREFIX in result.stderr, (
            f"Expected '{_CATALOG_ERROR_PREFIX}' in stderr.\n"
            f"stderr: {result.stderr!r}"
        )


# ---------------------------------------------------------------------------
# Catalog-once semantics
# ---------------------------------------------------------------------------


class TestBatchCatalogLoadedOnce:
    """Catalog must be loaded exactly once per batch invocation.

    Uses the in-process ``run_batch_dispatch`` helper to monkeypatch
    ``load_catalog`` and count call sites.
    """

    def test_catalog_loaded_once_for_multi_line_batch(
        self,
        tmp_path: Path,
    ) -> None:
        """``load_catalog`` is called exactly once for a 3-line batch.

        Monkeypatches ``claude_wayfinder._dispatch.load_catalog`` (the name
        as imported in the dispatch module) to count invocations.  Uses the
        module-level ``run_batch_dispatch`` function directly rather than
        spawning a subprocess so the patch takes effect.
        """
        import io

        from claude_wayfinder._dispatch import run_batch_dispatch

        ndjson_input = _ndjson(_CONTEXT_A, _CONTEXT_B, _CONTEXT_C)
        out_buf = io.StringIO()

        with patch(
            "claude_wayfinder._dispatch.load_catalog",
            wraps=_real_load_catalog,
        ) as mock_load:
            env_overrides = {
                "DISPATCH_CATALOG_PATH": str(_DEMO_CATALOG_PATH)
            }
            old_env = {
                k: os.environ.get(k) for k in env_overrides
            }
            try:
                for k, v in env_overrides.items():
                    os.environ[k] = v
                run_batch_dispatch(
                    stdin_data=ndjson_input,
                    out=out_buf,
                )
            finally:
                for k, orig in old_env.items():
                    if orig is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = orig

        assert mock_load.call_count == 1, (
            f"Expected load_catalog to be called exactly once, "
            f"got {mock_load.call_count} calls."
        )


def _real_load_catalog(path: Path) -> Any:
    """Thin wrapper around the real load_catalog for use as a spy wraps= arg.

    Args:
        path: Path to the catalog JSON file.

    Returns:
        Whatever the real ``load_catalog`` returns.
    """
    from claude_wayfinder.match import load_catalog as _lc

    return _lc(path)


# ---------------------------------------------------------------------------
# --demo flag in batch mode
# ---------------------------------------------------------------------------


class TestBatchDemoFlag:
    """``--demo`` must activate demo mode in batch mode too."""

    def test_batch_demo_flag_exits_zero(self) -> None:
        """``dispatch --batch --demo`` exits 0 with demo banner."""
        result = _run_batch(
            _ndjson(_CONTEXT_A),
            extra_args=["--demo"],
        )
        assert result.returncode == 0, (
            f"dispatch --batch --demo exited {result.returncode}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_batch_demo_flag_overrides_catalog_env_var(self) -> None:
        """``--demo`` wins even when ``$DISPATCH_CATALOG_PATH`` is also set."""
        result = _run_batch(
            _ndjson(_CONTEXT_A),
            env_overrides={"DISPATCH_CATALOG_PATH": str(_DEMO_CATALOG_PATH)},
            extra_args=["--demo"],
        )
        assert result.returncode == 0, (
            f"dispatch --batch --demo exited {result.returncode} with "
            f"valid catalog set.\nstdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )
        assert _CATALOG_ERROR_PREFIX not in result.stderr, (
            "Unexpected [CATALOG ERROR] when --demo overrides catalog env.\n"
            f"stderr: {result.stderr}"
        )


# ---------------------------------------------------------------------------
# Canonical-default fallback in batch mode
# ---------------------------------------------------------------------------


class TestBatchCanonicalDefaultFallback:
    """Without ``--demo`` or ``$DISPATCH_CATALOG_PATH``, batch must resolve
    the canonical default path."""

    def test_batch_canonical_path_absent_emits_catalog_error(
        self, tmp_path: Path
    ) -> None:
        """With empty ``$CLAUDE_HOME`` and no flags, batch emits catalog error.

        This ensures that "no env var, no --demo" does NOT silently demo-mode
        in batch; instead it resolves canonical and emits [CATALOG ERROR].
        """
        result = _run_batch(
            _ndjson(_CONTEXT_A),
            env_overrides={"CLAUDE_HOME": str(tmp_path)},
        )
        assert result.returncode != 0, (
            "batch should exit non-zero when canonical catalog is absent.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert _CATALOG_ERROR_PREFIX in result.stderr, (
            "Expected [CATALOG ERROR] when canonical catalog is absent.\n"
            f"stderr: {result.stderr}"
        )
