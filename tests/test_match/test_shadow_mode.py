"""Shadow-mode wiring tests for M15-5 (#422): Compose in shadow mode.

Covers the end-to-end behavior of ``main()`` after the shadow-wiring
in ``_main.py`` is implemented.  All four tests drive ``main()`` via
subprocess with a ``tmp_path`` ``DISPATCH_LOG_PATH`` and the project's
synthetic catalog, mirroring the pattern in ``test_integration.py`` and
``test_catalog.py``.

Test inventory (Part B of the M15-5 contract):
  B1 — **Live unchanged**: stdout JSON of a scored dispatch is
       byte-identical with and without the shadow mode present.
  B2 — **Shadow populates**: a scored dispatch with labels in the
       stdin context produces a log line whose ``"shadow"`` key is
       populated with the §F.1 fields.
  B3 — **Non-fatal**: if ``compose_route`` raises, the live dispatch
       still emits the correct stdout and the log line has NO
       ``"shadow"`` key (graceful degradation).
  B4 — **Override/parse paths unchanged**: an override-matched dispatch
       log line has NO ``"shadow"`` key (shadow is scored-path only).

All four tests are expected to FAIL until the shadow wiring in
``_main.py`` is implemented — B1 passes (stdout unchanged is already
true), but B2/B3/B4 fail because ``"shadow"`` is absent / present
(respectively).

Specifically:
  B1 — GREEN from day 0 (no wiring changes stdout, so it already holds).
  B2 — RED: ``"shadow"`` key absent from log line until wiring lands.
  B3 — RED: needs monkeypatching ``compose_route`` in ``_main``'s module
       namespace; test infrastructure confirms graceful-degradation path.
  B4 — RED: no ``"shadow"`` key is also expected for overrides; the test
       asserts the absence explicitly, so it should actually pass if
       override path is already unchanged — but its paired assertion
       (B2 shows shadow present on scored path) is still RED.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from tests.test_match.conftest import (
    _catalog,
    _make_agent,
    _run,
)

# ---------------------------------------------------------------------------
# §F.1 required keys in the shadow record
# ---------------------------------------------------------------------------

#: Top-level keys that must appear inside ``log_entry["shadow"]``.
_SHADOW_REQUIRED_KEYS = {
    "domain",
    "posture",
    "confidence",
    "area_span",
    "live_decision",
    "live_agent",
    "live_confidence",
    "live_disposition_source",
    "shadow_decision",
    "shadow_agent",
    "shadow_confidence",
    "shadow_disposition_source",
    "gated_agent_names",
    "posture_preferred",
    "posture_routed",
    "branch",
    "lexical_agreement",
    "posture_veto_reason",
    "agreement",
}

# ---------------------------------------------------------------------------
# Catalog and stdin helpers
# ---------------------------------------------------------------------------

#: Minimal catalog whose sole agent has a clear keyword winner.
_SHADOW_CATALOG = _catalog(
    [
        _make_agent(
            "code-writer",
            keywords=[{"term": "implement", "weight": 1.0}],
            path_globs=["**/*.py"],
        ),
        _make_agent(
            "debugger",
            keywords=[{"term": "debug", "weight": 1.0}],
        ),
    ]
)

#: Scored-path input — no labels ⇒ fallback to decide().
_SCORED_INPUT_NO_LABELS: dict[str, Any] = {
    "task_description": "implement the new feature",
    "file_paths": ["src/main.py"],
}

#: Scored-path input WITH labels so ``parse_labels`` extracts them.
_SCORED_INPUT_WITH_LABELS: dict[str, Any] = {
    "task_description": "implement the new feature",
    "file_paths": ["src/main.py"],
    "domain": "code",
    "posture": "build",
    "confidence": "high",
    "area_span": 1,
}


def _run_with_log(
    stdin_obj: dict[str, Any],
    catalog: dict[str, Any],
    log_path: Path,
    tmp_path: Path,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the matcher with a DISPATCH_LOG_PATH set.

    Args:
        stdin_obj: Context dict sent as stdin JSON.
        catalog: Catalog dict to write to a temp file.
        log_path: Path where the JSONL log will be written.
        tmp_path: pytest temp dir for the catalog file.
        extra_env: Additional environment variable overrides.

    Returns:
        CompletedProcess with stdout/stderr captured.
    """
    env_extra = {"DISPATCH_LOG_PATH": str(log_path)}
    if extra_env:
        env_extra.update(extra_env)
    return _run(
        stdin_obj,
        catalog,
        extra_env=env_extra,
        tmp_path=tmp_path,
    )


def _read_log_lines(log_path: Path) -> list[dict[str, Any]]:
    """Read all JSONL log lines from *log_path*.

    Args:
        log_path: Path to the ``.jsonl`` dispatch log.

    Returns:
        List of parsed log-entry dicts.
    """
    text = log_path.read_text(encoding="utf-8")
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    return [json.loads(ln) for ln in lines]


# ---------------------------------------------------------------------------
# B1 — Live stdout unchanged (expected GREEN from day 0)
# ---------------------------------------------------------------------------


class TestShadowLiveUnchanged:
    """Shadow presence must not alter the live stdout decision.

    B1: The stdout ``result`` of a scored dispatch is byte-identical
    whether or not shadow wiring is present.  Concretely: with and
    without a ``DISPATCH_LOG_PATH``, the same stdin produces the
    same JSON on stdout.

    This test is expected to be GREEN from day 0 — if shadow mode
    changed stdout, that would be a regression.
    """

    def test_stdout_identical_with_and_without_log(
        self, tmp_path: Path
    ) -> None:
        """Scored dispatch stdout is identical with and without a log path.

        Asserts that enabling ``DISPATCH_LOG_PATH`` (which activates the
        shadow-wiring code path) does not alter the stdout JSON.
        """
        log_path = tmp_path / "shadow_b1.jsonl"

        # Run without log
        result_no_log = _run(
            _SCORED_INPUT_NO_LABELS,
            _SHADOW_CATALOG,
            tmp_path=tmp_path,
        )
        assert result_no_log.returncode == 0, result_no_log.stderr

        # Run with log (activates shadow path once wired)
        result_with_log = _run_with_log(
            _SCORED_INPUT_NO_LABELS,
            _SHADOW_CATALOG,
            log_path=log_path,
            tmp_path=tmp_path,
        )
        assert result_with_log.returncode == 0, result_with_log.stderr

        # Stdout must be byte-identical (parse to compare semantically
        # in case field-order differs).
        out_no_log = json.loads(result_no_log.stdout)
        out_with_log = json.loads(result_with_log.stdout)

        # Strip the fields added after the log write (catalog_hash,
        # matcher_version) — they are present in both and must match too.
        assert out_no_log == out_with_log, (
            "Shadow presence altered stdout. "
            f"without_log={out_no_log!r}, "
            f"with_log={out_with_log!r}"
        )

    def test_stdout_decision_matches_live_fields_in_log(
        self, tmp_path: Path
    ) -> None:
        """live_* fields in the shadow record match the stdout decision.

        Once shadow is wired, the log entry's ``shadow.live_*`` fields
        must equal the stdout output's decision/agent/confidence/
        disposition_source.  This verifies the §F.1 live mirror
        requirement.

        Expected to be PARTIALLY RED until wiring lands (log line will
        have no ``"shadow"`` key yet, so the assertion will fail on
        ``assert "shadow" in entry``).
        """
        log_path = tmp_path / "shadow_b1b.jsonl"
        result = _run_with_log(
            _SCORED_INPUT_WITH_LABELS,
            _SHADOW_CATALOG,
            log_path=log_path,
            tmp_path=tmp_path,
        )
        assert result.returncode == 0, result.stderr

        stdout_decision = json.loads(result.stdout)
        entries = _read_log_lines(log_path)
        assert len(entries) == 1
        entry = entries[0]

        # This assertion is the RED trigger until wiring lands.
        assert "shadow" in entry, (
            "Log entry must contain a 'shadow' key after M15-5 wiring. "
            f"Entry keys: {sorted(entry.keys())}"
        )
        shadow = entry["shadow"]
        assert shadow["live_decision"] == stdout_decision["decision"], (
            "shadow.live_decision must match stdout decision"
        )
        # live_agent may be absent from stdout (self_handle etc.) → None
        assert shadow["live_agent"] == stdout_decision.get("agent"), (
            "shadow.live_agent must match stdout agent (or None)"
        )
        assert shadow["live_confidence"] == pytest.approx(
            stdout_decision["confidence"]
        ), "shadow.live_confidence must match stdout confidence"
        assert (
            shadow["live_disposition_source"]
            == stdout_decision.get("disposition_source")
        ), "shadow.live_disposition_source must match stdout disposition_source"


# ---------------------------------------------------------------------------
# B2 — Shadow record populates §F.1 keys (expected RED)
# ---------------------------------------------------------------------------


class TestShadowPopulates:
    """After a scored dispatch with labels, ``log_entry["shadow"]`` is present.

    B2: The log line must have a nested ``"shadow"`` key containing all
    §F.1 required keys, with ``live_*`` mirroring stdout and
    ``shadow_*`` holding the Compose result.

    Expected to be RED until the shadow wiring in ``_main.py`` lands.
    """

    def test_shadow_key_present_in_log_for_scored_dispatch(
        self, tmp_path: Path
    ) -> None:
        """Scored dispatch with labels produces a log line with 'shadow' key.

        Expected RED: ``"shadow"`` key is absent until wiring is done.
        """
        log_path = tmp_path / "shadow_b2.jsonl"
        result = _run_with_log(
            _SCORED_INPUT_WITH_LABELS,
            _SHADOW_CATALOG,
            log_path=log_path,
            tmp_path=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        assert log_path.exists(), "Log file was not created"

        entries = _read_log_lines(log_path)
        assert len(entries) == 1
        entry = entries[0]

        # This is the primary RED trigger.
        assert "shadow" in entry, (
            "Log entry must have a 'shadow' key for a scored dispatch "
            f"with labels. Entry keys: {sorted(entry.keys())}"
        )

    def test_shadow_contains_all_f1_required_keys(
        self, tmp_path: Path
    ) -> None:
        """shadow record contains every §F.1 required key.

        Expected RED: ``"shadow"`` key absent until wiring.
        """
        log_path = tmp_path / "shadow_b2_keys.jsonl"
        result = _run_with_log(
            _SCORED_INPUT_WITH_LABELS,
            _SHADOW_CATALOG,
            log_path=log_path,
            tmp_path=tmp_path,
        )
        assert result.returncode == 0, result.stderr

        entries = _read_log_lines(log_path)
        assert len(entries) == 1
        entry = entries[0]

        assert "shadow" in entry, (
            f"No 'shadow' key in log entry. Keys: {sorted(entry.keys())}"
        )
        shadow = entry["shadow"]
        missing = _SHADOW_REQUIRED_KEYS - set(shadow.keys())
        assert not missing, (
            f"shadow record is missing §F.1 required keys: "
            f"{sorted(missing)}. "
            f"Present keys: {sorted(shadow.keys())}"
        )

    def test_shadow_shadow_fields_populated(
        self, tmp_path: Path
    ) -> None:
        """shadow_decision and shadow_agent are populated by Compose.

        Expected RED: ``"shadow"`` absent until wiring.
        """
        log_path = tmp_path / "shadow_b2_shadow.jsonl"
        result = _run_with_log(
            _SCORED_INPUT_WITH_LABELS,
            _SHADOW_CATALOG,
            log_path=log_path,
            tmp_path=tmp_path,
        )
        assert result.returncode == 0, result.stderr

        entries = _read_log_lines(log_path)
        entry = entries[0]

        assert "shadow" in entry, (
            f"No 'shadow' key in log entry. Keys: {sorted(entry.keys())}"
        )
        shadow = entry["shadow"]
        # shadow_decision must be one of the valid decisions
        valid_decisions = {
            "delegate",
            "self_handle",
            "self_handle_unaided",
            "advisory",
            "ask_user",
            "needs_more_detail",
            "mixed_content",
        }
        assert shadow.get("shadow_decision") in valid_decisions, (
            f"shadow.shadow_decision must be a valid decision, "
            f"got {shadow.get('shadow_decision')!r}"
        )
        # shadow_confidence must be a float
        assert isinstance(shadow.get("shadow_confidence"), (int, float)), (
            "shadow.shadow_confidence must be numeric, "
            f"got {shadow.get('shadow_confidence')!r}"
        )

    def test_shadow_label_fields_reflect_stdin_context(
        self, tmp_path: Path
    ) -> None:
        """shadow domain/posture/confidence/area_span reflect stdin labels.

        Expected RED: ``"shadow"`` absent until wiring.
        """
        log_path = tmp_path / "shadow_b2_labels.jsonl"
        result = _run_with_log(
            _SCORED_INPUT_WITH_LABELS,
            _SHADOW_CATALOG,
            log_path=log_path,
            tmp_path=tmp_path,
        )
        assert result.returncode == 0, result.stderr

        entries = _read_log_lines(log_path)
        entry = entries[0]

        assert "shadow" in entry, (
            f"No 'shadow' key in log entry. Keys: {sorted(entry.keys())}"
        )
        shadow = entry["shadow"]
        assert shadow.get("domain") == _SCORED_INPUT_WITH_LABELS["domain"], (
            "shadow.domain must match stdin domain"
        )
        assert shadow.get("posture") == _SCORED_INPUT_WITH_LABELS["posture"], (
            "shadow.posture must match stdin posture"
        )
        assert shadow.get("confidence") == _SCORED_INPUT_WITH_LABELS[
            "confidence"
        ], "shadow.confidence must match stdin confidence"
        assert shadow.get("area_span") == _SCORED_INPUT_WITH_LABELS[
            "area_span"
        ], "shadow.area_span must match stdin area_span"


# ---------------------------------------------------------------------------
# B3 — Non-fatal: compose_route raises ⇒ no shadow key, stdout intact
# ---------------------------------------------------------------------------


class TestShadowNonFatal:
    """Shadow compute failure must not break live dispatch.

    B3: If ``compose_route`` raises during shadow computation, the live
    dispatch still emits the correct stdout decision and the log line
    has NO ``"shadow"`` key.

    This test uses monkeypatching to simulate ``compose_route`` raising
    inside ``_main``'s module namespace.

    The test is structured to import ``main`` and call it in-process
    (with a patched environment) rather than via subprocess, so that
    ``unittest.mock.patch`` can reach into the live ``_main`` module.

    Expected to be RED: once shadow wiring is merged, the
    monkeypatched path must be tested; before wiring, the test will
    fail because the patching target does not exist yet.
    """

    def test_compose_raise_does_not_block_stdout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """compose_route raising must not prevent the live decision on stdout.

        Monkeypatches ``claude_wayfinder.match._main.compose_route``
        (the name that will exist after wiring) to raise ``RuntimeError``.
        Asserts that the live decision is still printed and the log line
        has no ``"shadow"`` key.

        Expected RED: the target attribute
        ``claude_wayfinder.match._main.compose_route`` does not exist
        until wiring, so the patch will raise ``AttributeError``.
        """
        import io

        import claude_wayfinder.match._main as _main_mod

        log_path = tmp_path / "shadow_b3.jsonl"
        catalog_path = tmp_path / "catalog.json"
        catalog_path.write_text(
            json.dumps(_SHADOW_CATALOG), encoding="utf-8"
        )

        # Patch compose_route inside _main's namespace to raise.
        # This will be the RED trigger until wiring is done:
        # AttributeError: module has no attribute 'compose_route'.
        with mock.patch.object(
            _main_mod,
            "compose_route",
            side_effect=RuntimeError("simulated shadow failure"),
        ):
            monkeypatch.setenv("DISPATCH_CATALOG_PATH", str(catalog_path))
            monkeypatch.setenv("DISPATCH_LOG_PATH", str(log_path))

            captured_stdout = io.StringIO()
            monkeypatch.setattr(sys, "stdout", captured_stdout)
            monkeypatch.setattr(
                sys,
                "stdin",
                io.StringIO(json.dumps(_SCORED_INPUT_WITH_LABELS)),
            )

            _main_mod.main([])

        stdout_text = captured_stdout.getvalue().strip()
        assert stdout_text, "main() must emit JSON to stdout even when shadow fails"
        out = json.loads(stdout_text)
        valid_decisions = {
            "delegate",
            "self_handle",
            "self_handle_unaided",
            "advisory",
            "ask_user",
            "needs_more_detail",
            "mixed_content",
        }
        assert out.get("decision") in valid_decisions, (
            f"Live decision invalid after shadow failure: {out!r}"
        )

        # Log entry must exist but have NO "shadow" key.
        assert log_path.exists(), "Log file must be created even on shadow failure"
        entries = _read_log_lines(log_path)
        assert len(entries) == 1
        entry = entries[0]
        assert "shadow" not in entry, (
            "Log entry must NOT have a 'shadow' key when compose_route "
            f"raises (graceful degradation). Entry keys: {sorted(entry.keys())}"
        )


# ---------------------------------------------------------------------------
# B4 — Override/parse paths have NO shadow key (expected RED until wiring)
# ---------------------------------------------------------------------------


class TestShadowNotOnOverridePath:
    """Override-matched dispatch log lines must NOT contain a 'shadow' key.

    B4: Shadow mode is for the scored path only.  Override-matched
    dispatches, whose ``_write_log_entry`` call is at lines ~184-190,
    must remain unmodified.

    The test verifies that an override-matched dispatch produces a log
    line WITHOUT a ``"shadow"`` key.

    This test is expected to be GREEN from day 0 for the override path
    (the override ``_write_log_entry`` is not modified), but it
    documents the invariant explicitly so a future regression would be
    caught.
    """

    def test_override_dispatch_log_has_no_shadow_key(
        self, tmp_path: Path
    ) -> None:
        """Override-matched dispatch: log line must not contain 'shadow'.

        Builds a catalog with one agent and an overrides file that
        matches the test input, triggering the override short-circuit
        path in ``_main.py``.  The resulting log entry must have no
        ``"shadow"`` key.
        """
        # Build a catalog with an agent that will not be reached.
        catalog_path = tmp_path / "catalog.json"
        catalog_path.write_text(
            json.dumps(_SHADOW_CATALOG), encoding="utf-8"
        )

        # Build an overrides file using a SUPPORTED predicate (path_globs).
        # _SCORED_INPUT_WITH_LABELS includes file_paths=["src/main.py"],
        # which matches "**/*.py" — so this override will fire on the
        # scored-path input we send.
        #
        # The supported predicates in _overrides._rule_matches are:
        #   command_prefix, path_globs, tool_mentions.
        # "task_description_contains" is NOT supported and must not be used.
        overrides = {
            "version": 1,
            "rules": [
                {
                    "id": "test-override-b4",
                    "decision": "delegate",
                    "agent": "code-writer",
                    "skills": [],
                    "confidence": 0.99,
                    "rationale": "Override matched for test (path_globs).",
                    "predicates": {"path_globs": ["**/*.py"]},
                }
            ],
        }
        overrides_path = tmp_path / "overrides.json"
        overrides_path.write_text(json.dumps(overrides), encoding="utf-8")

        log_path = tmp_path / "shadow_b4.jsonl"

        result = _run_with_log(
            _SCORED_INPUT_WITH_LABELS,
            _SHADOW_CATALOG,
            log_path=log_path,
            tmp_path=tmp_path,
            extra_env={
                "DISPATCH_OVERRIDES_PATH": str(overrides_path),
            },
        )
        # Override dispatches exit 0 and emit a decision.
        assert result.returncode == 0, result.stderr

        assert log_path.exists(), (
            "Log file was not created — the override path must still write "
            "a log entry (DISPATCH_LOG_PATH is set)."
        )

        entries = _read_log_lines(log_path)
        assert entries, "Log file is empty — no dispatch was recorded."

        entry = entries[0]
        # Confirm the override path ran (not the scoring path).
        assert entry.get("override_id") == "test-override-b4", (
            "Log entry override_id must be 'test-override-b4' — the "
            f"path_globs predicate did not fire. entry={entry!r}"
        )

        assert "shadow" not in entry, (
            "Override-path log entry must NOT have a 'shadow' key. "
            f"Entry keys: {sorted(entry.keys())}"
        )
