"""Tests for catalog I/O, dispatch-log writing, and catalog-path resolution.

Covers:
- Catalog degradation (missing / malformed / empty → exit 2 + banner)
- Dispatch log: entry shape, append-only, failure resilience, hash stability
- Issue #10 fail-loud catalog resolution (CLAUDE_HOME removed)
- load_catalog on empty entries list (#506)
"""

from __future__ import annotations

import json
import os
import re as _re
import subprocess
from pathlib import Path

from tests.test_match.conftest import (
    _ISO8601_RE,
    _LOG_TEST_CATALOG,
    _LOG_TEST_INPUT,
    _MATCH_MODULE,
    _VALID_DECISIONS,
    PYTHON,
    _catalog,
    _make_agent,
    _match_mod,
    _run,
)

# ===========================================================================
# Catalog degradation
# ===========================================================================


class TestCatalogDegradation:
    """Catalog missing / malformed / empty → exit 2 + stderr banner."""

    BANNER_PREFIX = "[CATALOG ERROR]"

    def test_missing_catalog_exits_2_with_banner(self, tmp_path: Path) -> None:
        """Non-existent catalog file → exit code 2, banner on stderr."""
        missing = tmp_path / "nonexistent.json"
        result = subprocess.run(
            [PYTHON, "-m", *_MATCH_MODULE],
            input=json.dumps({"task_description": "implement something"}),
            capture_output=True,
            text=True,
            env={**os.environ, "DISPATCH_CATALOG_PATH": str(missing)},
            check=False,
        )
        assert result.returncode == 2, f"Expected exit 2, got {result.returncode}"
        assert self.BANNER_PREFIX in result.stderr

    def test_malformed_json_catalog_exits_2_with_banner(self, tmp_path: Path) -> None:
        """Malformed JSON catalog → exit code 2, banner on stderr."""
        bad_catalog = tmp_path / "bad.json"
        bad_catalog.write_text("{not valid json", encoding="utf-8")
        result = subprocess.run(
            [PYTHON, "-m", *_MATCH_MODULE],
            input=json.dumps({"task_description": "implement something"}),
            capture_output=True,
            text=True,
            env={**os.environ, "DISPATCH_CATALOG_PATH": str(bad_catalog)},
            check=False,
        )
        assert result.returncode == 2
        assert self.BANNER_PREFIX in result.stderr

    def test_empty_entries_catalog_exits_2_with_banner(self, tmp_path: Path) -> None:
        """Catalog with zero entries → exit code 2, banner on stderr."""
        empty_catalog = tmp_path / "empty.json"
        empty_catalog.write_text(
            json.dumps({"schema_version": 1, "entries": []}), encoding="utf-8"
        )
        result = subprocess.run(
            [PYTHON, "-m", *_MATCH_MODULE],
            input=json.dumps({"task_description": "implement something"}),
            capture_output=True,
            text=True,
            env={**os.environ, "DISPATCH_CATALOG_PATH": str(empty_catalog)},
            check=False,
        )
        assert result.returncode == 2
        assert self.BANNER_PREFIX in result.stderr

    def test_banner_on_stderr_not_stdout(self, tmp_path: Path) -> None:
        """Banner must appear on stderr only, not on stdout."""
        missing = tmp_path / "nonexistent.json"
        result = subprocess.run(
            [PYTHON, "-m", *_MATCH_MODULE],
            input=json.dumps({"task_description": "implement something"}),
            capture_output=True,
            text=True,
            env={**os.environ, "DISPATCH_CATALOG_PATH": str(missing)},
            check=False,
        )
        assert self.BANNER_PREFIX not in result.stdout
        assert self.BANNER_PREFIX in result.stderr


# ===========================================================================
# Dispatch log tests
# ===========================================================================


class TestDispatchLog:
    """match.py appends a NDJSON decision record to DISPATCH_LOG_PATH."""

    def test_log_entry_written_on_success(self, tmp_path: Path) -> None:
        """A successful matcher run writes exactly one log entry.

        The entry must have the expected shape:
        - type == "matcher_decision"
        - ts matches ISO 8601 UTC regex
        - input.task_description matches what was sent
        - output.decision is one of the 7 valid decisions
        - catalog_hash matches sha256:<64 hex chars>
        - matcher_version is a non-empty string
        """
        log_path = tmp_path / "log.jsonl"
        result = _run(
            _LOG_TEST_INPUT,
            _LOG_TEST_CATALOG,
            extra_env={"DISPATCH_LOG_PATH": str(log_path)},
            tmp_path=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        assert log_path.exists(), "Log file was not created"

        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1, f"Expected 1 log line, got {len(lines)}"

        entry = json.loads(lines[0])
        assert entry["type"] == "matcher_decision"
        assert _ISO8601_RE.match(entry["ts"]), f"ts did not match ISO8601: {entry['ts']!r}"
        assert entry["input"]["task_description"] == _LOG_TEST_INPUT["task_description"]
        assert entry["output"]["decision"] in _VALID_DECISIONS
        assert _re.match(
            r"^sha256:[0-9a-f]{64}$", entry["catalog_hash"]
        ), f"catalog_hash malformed: {entry['catalog_hash']!r}"
        assert entry["matcher_version"], "matcher_version must be a non-empty string"

    def test_log_entry_appends_not_overwrites(self, tmp_path: Path) -> None:
        """A second run appends a second line; file has 2 valid JSON lines."""
        log_path = tmp_path / "append.jsonl"
        extra = {"DISPATCH_LOG_PATH": str(log_path)}

        _run(_LOG_TEST_INPUT, _LOG_TEST_CATALOG, extra_env=extra, tmp_path=tmp_path)
        _run(_LOG_TEST_INPUT, _LOG_TEST_CATALOG, extra_env=extra, tmp_path=tmp_path)

        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2, f"Expected 2 log lines after 2 runs, got {len(lines)}"
        for line in lines:
            entry = json.loads(line)  # raises if invalid JSON
            assert entry["type"] == "matcher_decision"

    def test_log_write_failure_does_not_block_decision(self, tmp_path: Path) -> None:
        """An unwritable log path does not prevent stdout decision output.

        Uses a drive letter that does not exist on the current machine so
        mkdir will fail, triggering the OSError handler in _write_log_entry.
        Falls back to a deeply nested path under a non-existent root on
        POSIX systems.
        """
        if os.name == "nt":
            bad_log = "Z:/nonexistent_drive/subdir/log.jsonl"
        else:
            bad_log = "/proc/nonexistent_dir/log.jsonl"

        result = _run(
            _LOG_TEST_INPUT,
            _LOG_TEST_CATALOG,
            extra_env={"DISPATCH_LOG_PATH": bad_log},
            tmp_path=tmp_path,
        )
        # Decision must still succeed.
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out["decision"] in _VALID_DECISIONS
        # Failure message must appear on stderr.
        assert (
            "[match.py] log write failed" in result.stderr
        ), f"Expected log-write-failed message on stderr; got: {result.stderr!r}"

    def test_catalog_hash_stable_for_identical_catalogs(self, tmp_path: Path) -> None:
        """_compute_catalog_hash produces the same digest for dicts with different key order."""
        dict_a = {"b": 2, "a": 1, "entries": []}
        dict_b = {"a": 1, "entries": [], "b": 2}
        hash_a = _match_mod._compute_catalog_hash(dict_a)
        hash_b = _match_mod._compute_catalog_hash(dict_b)
        assert (
            hash_a == hash_b
        ), f"Hashes differ for semantically identical catalogs: {hash_a!r} vs {hash_b!r}"
        assert _re.match(r"^sha256:[0-9a-f]{64}$", hash_a)

    def test_log_path_env_var_override(self, tmp_path: Path) -> None:
        """DISPATCH_LOG_PATH env var controls where the log is written.

        Confirms the log appears at the custom path and not at the default
        ~/.claude/state/dispatch-log.jsonl location.
        """
        custom_log = tmp_path / "custom.jsonl"
        result = _run(
            _LOG_TEST_INPUT,
            _LOG_TEST_CATALOG,
            extra_env={"DISPATCH_LOG_PATH": str(custom_log)},
            tmp_path=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        assert custom_log.exists(), "Log not written to DISPATCH_LOG_PATH override path"
        lines = custom_log.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1


# ===========================================================================
# Issue #10 fail-loud catalog path resolution
# ===========================================================================


class TestIssue10FailLoudCatalogPath:
    """Catalog path resolution must fail loud when no explicit source is given.

    After Issue #10, the two-step resolution chain is:
      1. ``--catalog-path <path>`` CLI flag
      2. ``DISPATCH_CATALOG_PATH`` env var
      3. **fail loud** — emit ``[CATALOG ERROR]`` banner, exit non-zero.

    ``CLAUDE_HOME`` and ``Path.home()`` are no longer lookup steps.
    """

    def test_no_path_no_env_exits_nonzero_with_catalog_error(
        self, tmp_path: Path
    ) -> None:
        """CLI exits non-zero and emits [CATALOG ERROR] when no path is given.

        Both ``DISPATCH_CATALOG_PATH`` and ``CLAUDE_HOME`` are absent from the
        environment.  The matcher must not fall back to ``~/.claude/...`` —
        it must emit a ``[CATALOG ERROR]`` banner on stderr and exit 2.
        """
        clean_env = {
            k: v
            for k, v in os.environ.items()
            if k not in {"DISPATCH_CATALOG_PATH", "CLAUDE_HOME"}
        }
        result = subprocess.run(
            [PYTHON, "-m", *_MATCH_MODULE],
            input=json.dumps({"task_description": "implement a feature"}),
            capture_output=True,
            text=True,
            env=clean_env,
            check=False,
        )
        assert result.returncode != 0, (
            "Expected non-zero exit when no catalog path is supplied; "
            f"got returncode={result.returncode}, stderr={result.stderr!r}"
        )
        assert "[CATALOG ERROR]" in result.stderr, (
            f"Expected [CATALOG ERROR] banner on stderr; got: {result.stderr!r}"
        )

    def test_catalog_error_message_names_the_fix(self, tmp_path: Path) -> None:
        """[CATALOG ERROR] message tells the user how to supply a path.

        The error text must mention either ``--catalog-path`` or
        ``DISPATCH_CATALOG_PATH`` so the caller knows what to do.
        """
        clean_env = {
            k: v
            for k, v in os.environ.items()
            if k not in {"DISPATCH_CATALOG_PATH", "CLAUDE_HOME"}
        }
        result = subprocess.run(
            [PYTHON, "-m", *_MATCH_MODULE],
            input=json.dumps({"task_description": "implement a feature"}),
            capture_output=True,
            text=True,
            env=clean_env,
            check=False,
        )
        assert any(
            token in result.stderr
            for token in ("--catalog-path", "DISPATCH_CATALOG_PATH")
        ), (
            "Error message must name the fix (--catalog-path or "
            f"DISPATCH_CATALOG_PATH); got: {result.stderr!r}"
        )

    def test_catalog_error_message_names_canonical_default(
        self, tmp_path: Path
    ) -> None:
        """[CATALOG ERROR] message names the canonical default catalog path.

        The error text must mention the canonical default path
        ``~/.claude/state/dispatch-catalog.json`` so a router agent
        that fabricated a bad path can self-correct without a
        deletion misdiagnosis (Issue #281).
        """
        clean_env = {
            k: v
            for k, v in os.environ.items()
            if k not in {"DISPATCH_CATALOG_PATH", "CLAUDE_HOME"}
        }
        result = subprocess.run(
            [PYTHON, "-m", *_MATCH_MODULE],
            input=json.dumps({"task_description": "implement a feature"}),
            capture_output=True,
            text=True,
            env=clean_env,
            check=False,
        )
        assert ".claude/state/dispatch-catalog.json" in result.stderr, (
            "Error message must name the canonical default path "
            "(.claude/state/dispatch-catalog.json); "
            f"got: {result.stderr!r}"
        )

    def test_claude_home_env_var_is_ignored(self, tmp_path: Path) -> None:
        """CLAUDE_HOME env var no longer redirects catalog resolution.

        After Issue #10, ``CLAUDE_HOME`` is not a lookup step.  Even if a
        valid catalog exists under ``$CLAUDE_HOME/state/dispatch-catalog.json``,
        the matcher must not use it — it should still fail loud.
        """
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        catalog = _catalog(
            [
                _make_agent(
                    "code-writer",
                    keywords=[{"term": "implement", "weight": 1.0}],
                ),
            ]
        )
        (state_dir / "dispatch-catalog.json").write_text(
            json.dumps(catalog), encoding="utf-8"
        )

        # Env has CLAUDE_HOME pointing at a valid catalog but no
        # DISPATCH_CATALOG_PATH.
        clean_env = {
            k: v
            for k, v in os.environ.items()
            if k != "DISPATCH_CATALOG_PATH"
        }
        clean_env["CLAUDE_HOME"] = str(tmp_path)

        result = subprocess.run(
            [PYTHON, "-m", *_MATCH_MODULE],
            input=json.dumps({"task_description": "implement a feature"}),
            capture_output=True,
            text=True,
            env=clean_env,
            check=False,
        )
        assert result.returncode != 0, (
            "CLAUDE_HOME must no longer serve as a catalog fallback; "
            "expected non-zero exit but got success. "
            f"stderr={result.stderr!r}, stdout={result.stdout!r}"
        )
        assert "[CATALOG ERROR]" in result.stderr, (
            f"Expected [CATALOG ERROR] on stderr; got: {result.stderr!r}"
        )

    def test_catalog_path_flag_overrides_env(self, tmp_path: Path) -> None:
        """--catalog-path flag supplies the catalog path to the CLI.

        When ``--catalog-path <path>`` is passed, the matcher uses that file
        and succeeds — regardless of whether ``DISPATCH_CATALOG_PATH`` is set.
        """
        catalog = _catalog(
            [
                _make_agent(
                    "code-writer",
                    keywords=[{"term": "implement", "weight": 1.0}],
                    path_globs=["**/*.py"],
                ),
            ]
        )
        catalog_file = tmp_path / "my_catalog.json"
        catalog_file.write_text(json.dumps(catalog), encoding="utf-8")

        clean_env = {
            k: v
            for k, v in os.environ.items()
            if k not in {"DISPATCH_CATALOG_PATH", "CLAUDE_HOME"}
        }

        result = subprocess.run(
            [
                PYTHON,
                "-m", *_MATCH_MODULE,
                "--catalog-path",
                str(catalog_file),
            ],
            input=json.dumps(
                {
                    "task_description": "implement the feature",
                    "file_paths": ["main.py"],
                }
            ),
            capture_output=True,
            text=True,
            env=clean_env,
            check=False,
        )
        assert result.returncode == 0, (
            f"--catalog-path flag should supply catalog and succeed; "
            f"stderr={result.stderr!r}"
        )
        out = json.loads(result.stdout)
        assert out["decision"] in {
            "delegate",
            "self_handle",
            "advisory",
        }, f"Expected a routing decision, got: {out['decision']!r}"

    def test_dispatch_catalog_path_env_still_works(self, tmp_path: Path) -> None:
        """DISPATCH_CATALOG_PATH env var remains the second resolution step.

        The env var is still supported after Issue #10 — only ``CLAUDE_HOME``
        and the ``~/.claude/`` default are removed.
        """
        catalog = _catalog(
            [
                _make_agent(
                    "code-writer",
                    keywords=[{"term": "implement", "weight": 1.0}],
                    path_globs=["**/*.py"],
                ),
            ]
        )
        catalog_file = tmp_path / "env_catalog.json"
        catalog_file.write_text(json.dumps(catalog), encoding="utf-8")

        clean_env = {
            k: v
            for k, v in os.environ.items()
            if k not in {"CLAUDE_HOME"}
        }
        clean_env["DISPATCH_CATALOG_PATH"] = str(catalog_file)

        result = subprocess.run(
            [PYTHON, "-m", *_MATCH_MODULE],
            input=json.dumps(
                {
                    "task_description": "implement the feature",
                    "file_paths": ["main.py"],
                }
            ),
            capture_output=True,
            text=True,
            env=clean_env,
            check=False,
        )
        assert result.returncode == 0, (
            f"DISPATCH_CATALOG_PATH env var must still work; "
            f"stderr={result.stderr!r}"
        )
        out = json.loads(result.stdout)
        assert out["decision"] not in {"needs_more_detail"}, (
            f"Expected a routing decision, got: {out['decision']!r}"
        )

    def test_log_path_missing_disables_logging_silently(
        self, tmp_path: Path
    ) -> None:
        """Missing DISPATCH_LOG_PATH disables log writing without crashing.

        After Issue #10, ``_resolve_log_path()`` returns ``None`` when no log
        path is configured (no env var).  The matcher must still succeed and
        emit a valid decision — no crash, no fallback to ``~/.claude/``.
        """
        catalog = _catalog(
            [
                _make_agent(
                    "code-writer",
                    keywords=[{"term": "implement", "weight": 1.0}],
                ),
            ]
        )
        catalog_file = tmp_path / "catalog.json"
        catalog_file.write_text(json.dumps(catalog), encoding="utf-8")

        clean_env = {
            k: v
            for k, v in os.environ.items()
            if k not in {"DISPATCH_LOG_PATH", "CLAUDE_HOME"}
        }
        clean_env["DISPATCH_CATALOG_PATH"] = str(catalog_file)

        result = subprocess.run(
            [PYTHON, "-m", *_MATCH_MODULE],
            input=json.dumps({"task_description": "implement a feature"}),
            capture_output=True,
            text=True,
            env=clean_env,
            check=False,
        )
        assert result.returncode == 0, (
            "Matcher must succeed even when no log path is configured; "
            f"stderr={result.stderr!r}"
        )
        out = json.loads(result.stdout)
        assert out["decision"] in _VALID_DECISIONS, (
            f"Expected a valid decision; got: {out['decision']!r}"
        )


# ---------------------------------------------------------------------------
# Task 5.5 — load_catalog on empty entries list
# ---------------------------------------------------------------------------


class TestLoadCatalogEmptyEntries:
    """load_catalog accepts an empty entries list (was: raised ValueError).

    Empty catalogs are a valid degraded state (e.g. fresh checkout, or the
    #506 all-entries-dropped path). Callers like audit-catalog need to
    operate on them without a load-time crash.
    """

    def test_empty_entries_returns_empty_tuple(self, tmp_path) -> None:
        """load_catalog on {"entries": []} returns an empty list."""
        from claude_wayfinder.match import load_catalog

        p = tmp_path / "cat.json"
        p.write_text(json.dumps({"entries": []}))
        result = load_catalog(p)
        assert tuple(result) == tuple()


# ---------------------------------------------------------------------------
# Task 5 — _write_log_entry records override_id (#213)
# ---------------------------------------------------------------------------


class TestWriteLogEntryOverrideId:
    """_write_log_entry stores the override_id field in the NDJSON entry.

    Task 4 added ``override_id: str | None = None`` to the signature and
    unconditionally writes ``entry["override_id"]`` to the log record.
    These two tests prove the field is present and carries the correct
    value for both the override case and the default (scored) case.
    """

    def test_write_log_entry_records_override_id(self, tmp_path: Path) -> None:
        """override_id kwarg is written verbatim into the log entry dict.

        Writes one entry with override_id="my-rule", reads back the NDJSON
        line, and asserts the stored value matches.
        """
        from claude_wayfinder.match._catalog import _write_log_entry

        log_path = tmp_path / "log.jsonl"
        _write_log_entry(
            {},
            {"decision": "delegate"},
            "sha256:abc",
            log_path,
            override_id="my-rule",
        )
        entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert entry["override_id"] == "my-rule"

    def test_write_log_entry_override_id_null_default(self, tmp_path: Path) -> None:
        """override_id defaults to None when the kwarg is omitted.

        Scored decisions do not supply override_id; the entry must carry
        ``null`` (JSON) / ``None`` (Python) so NDJSON consumers can
        distinguish override-fired entries from scored entries.
        """
        from claude_wayfinder.match._catalog import _write_log_entry

        log_path = tmp_path / "log.jsonl"
        _write_log_entry(
            {},
            {"decision": "delegate"},
            "sha256:abc",
            log_path,
        )
        entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert entry["override_id"] is None


# ---------------------------------------------------------------------------
# M15-3 (#420) — _write_log_entry shadow_data extension
# ---------------------------------------------------------------------------


class TestWriteLogEntryShadowData:
    """_write_log_entry accepts an optional shadow_data kwarg (M15-3, #420).

    Three tests:
    1. Default-None pin — omitting shadow_data produces a byte-identical
       entry with exactly the current key set and no "shadow" key.
    2. Nested attach — shadow_data is stored under entry["shadow"], not
       merged flat into the entry.
    3. Collision isolation — shadow_data keys that collide with top-level
       entry keys (e.g. "output") are isolated under "shadow" and do not
       overwrite the real top-level value.

    Tests 2 and 3 are RED until the code-writer adds the shadow_data param.
    Test 1 is a GREEN pin; it confirms the default path is byte-unchanged.
    """

    #: Minimal fixed key set expected in every log entry.
    _EXPECTED_KEYS = {
        "type",
        "ts",
        "session_id",
        "input",
        "output",
        "catalog_hash",
        "matcher_version",
        "override_id",
        "attribution_source",
    }

    def test_default_none_produces_no_shadow_key(self, tmp_path: Path) -> None:
        """Omitting shadow_data leaves the entry key set unchanged (pin).

        Calls _write_log_entry without shadow_data and asserts:
        - The returned entry has exactly the 8 expected keys.
        - No "shadow" key is present.

        This test is GREEN on write — it confirms the default-None path
        is byte-identical to the pre-M15-3 shape.
        """
        from claude_wayfinder.match._catalog import _write_log_entry

        log_path = tmp_path / "log.jsonl"
        _write_log_entry(
            {"task_description": "do something"},
            {"decision": "delegate"},
            "sha256:abc",
            log_path,
        )
        entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert set(entry.keys()) == self._EXPECTED_KEYS, (
            f"Unexpected keys in entry: {set(entry.keys()) - self._EXPECTED_KEYS!r}"
        )
        assert "shadow" not in entry, (
            "entry must not have a 'shadow' key when shadow_data is None"
        )

    def test_shadow_data_nested_under_shadow_key(self, tmp_path: Path) -> None:
        """shadow_data is stored as entry["shadow"], not flat-merged (red).

        Calls _write_log_entry with shadow_data={"k": "v", "n": 1} and asserts:
        - entry["shadow"] == {"k": "v", "n": 1}
        - "k" and "n" are NOT present at the top level of the entry.

        RED until code-writer adds shadow_data param (TypeError on call).
        """
        from claude_wayfinder.match._catalog import _write_log_entry

        log_path = tmp_path / "log.jsonl"
        shadow = {"k": "v", "n": 1}
        _write_log_entry(
            {"task_description": "do something"},
            {"decision": "delegate"},
            "sha256:abc",
            log_path,
            shadow_data=shadow,
        )
        entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert entry["shadow"] == shadow, (
            f"entry['shadow'] should be {shadow!r}, got {entry.get('shadow')!r}"
        )
        assert "k" not in entry, (
            "shadow key 'k' must not leak to the top-level entry"
        )
        assert "n" not in entry, (
            "shadow key 'n' must not leak to the top-level entry"
        )

    def test_shadow_data_collision_key_isolated(self, tmp_path: Path) -> None:
        """shadow_data collision with top-level key is isolated (red).

        Calls _write_log_entry with shadow_data={"output": "SHADOW"} and asserts:
        - Top-level entry["output"] is the real output dict, unchanged.
        - entry["shadow"]["output"] == "SHADOW".

        Proves nesting prevents the flat-merge collision that entry.update()
        would cause (spec §F.1 / plan §2).

        RED until code-writer adds shadow_data param (TypeError on call).
        """
        from claude_wayfinder.match._catalog import _write_log_entry

        real_output = {"decision": "delegate"}
        log_path = tmp_path / "log.jsonl"
        _write_log_entry(
            {"task_description": "do something"},
            real_output,
            "sha256:abc",
            log_path,
            shadow_data={"output": "SHADOW"},
        )
        entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert entry["output"] == real_output, (
            f"Top-level output was overwritten; got {entry['output']!r}"
        )
        assert entry["shadow"]["output"] == "SHADOW", (
            f"entry['shadow']['output'] should be 'SHADOW', "
            f"got {entry.get('shadow', {}).get('output')!r}"
        )


# ---------------------------------------------------------------------------
# Issue #440 (Option A) — _write_log_entry writes attribution_source field
# ---------------------------------------------------------------------------


class TestWriteLogEntryAttributionSource:
    """_write_log_entry unconditionally writes attribution_source='python_matcher'.

    Contract A from issue #440:
    - The field is present whether or not shadow_data is supplied.
    - The value is exactly "python_matcher" (not "post_tool_use_hook" or
      any other string).
    - The field does not shadow or displace existing required keys.

    All tests in this class are RED until Phase 2 adds the field.
    """

    def test_attribution_source_present_without_shadow_data(
        self, tmp_path: Path
    ) -> None:
        """attribution_source='python_matcher' appears when shadow_data omitted.

        Calls _write_log_entry without shadow_data and asserts that the
        written entry contains the key "attribution_source" with value
        "python_matcher".  RED: the field is not yet written by the
        implementation.
        """
        from claude_wayfinder.match._catalog import _write_log_entry

        log_path = tmp_path / "log.jsonl"
        _write_log_entry(
            {"task_description": "do something"},
            {"decision": "delegate"},
            "sha256:abc",
            log_path,
        )
        entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert "attribution_source" in entry, (
            "entry must contain 'attribution_source' key even without shadow_data"
        )
        assert entry["attribution_source"] == "python_matcher", (
            f"expected 'python_matcher', got {entry.get('attribution_source')!r}"
        )

    def test_attribution_source_present_with_shadow_data(
        self, tmp_path: Path
    ) -> None:
        """attribution_source='python_matcher' appears when shadow_data is supplied.

        Confirms the field is unconditional — present in both the shadow
        and non-shadow paths.  RED: the field is not yet written.
        """
        from claude_wayfinder.match._catalog import _write_log_entry

        log_path = tmp_path / "log.jsonl"
        _write_log_entry(
            {"task_description": "do something"},
            {"decision": "delegate"},
            "sha256:abc",
            log_path,
            shadow_data={"score": 0.9},
        )
        entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert "attribution_source" in entry, (
            "entry must contain 'attribution_source' key when shadow_data is given"
        )
        assert entry["attribution_source"] == "python_matcher", (
            f"expected 'python_matcher', got {entry.get('attribution_source')!r}"
        )

    def test_attribution_source_does_not_displace_required_keys(
        self, tmp_path: Path
    ) -> None:
        """Adding attribution_source does not remove any existing required key.

        The full required key set (type, ts, session_id, input, output,
        catalog_hash, matcher_version, override_id) must still be present
        alongside the new attribution_source key.  RED: the field is not yet
        written, so attribution_source will be absent.
        """
        from claude_wayfinder.match._catalog import _write_log_entry

        log_path = tmp_path / "log.jsonl"
        _write_log_entry(
            {"task_description": "do something"},
            {"decision": "delegate"},
            "sha256:abc",
            log_path,
        )
        entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        required = {
            "type",
            "ts",
            "session_id",
            "input",
            "output",
            "catalog_hash",
            "matcher_version",
            "override_id",
            "attribution_source",
        }
        missing = required - set(entry.keys())
        assert not missing, f"Entry is missing required keys: {missing!r}"
