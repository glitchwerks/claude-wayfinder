"""Tests for the ``DISPATCH_SHADOW`` env-var gate (Issue #457).

Pins the behavior of a new environment-variable gate that controls
whether the matcher's shadow-route compute (``_build_shadow_record()`` +
``compose_route()``) runs at all, and whether its output is attached to
the ``matcher_decision`` log entry under the ``"shadow"`` key.

Test inventory:
  1. **Absent → ON.**  ``DISPATCH_SHADOW`` unset: log entry carries a
     ``"shadow"`` key (today's unconditional behavior, preserved as the
     default).
  2. **Explicit truthy → ON.**  ``DISPATCH_SHADOW`` in
     ``{"1", "true", "yes"}`` (case-insensitive) → ``"shadow"`` key
     present.
  3. **Explicit falsey → OFF.**  ``DISPATCH_SHADOW`` in
     ``{"0", "false", "no"}`` (case-insensitive) → shadow compute is
     SKIPPED (``compose_route`` is never called) and the log entry has
     NO ``"shadow"`` key.
  4. **Malformed value → fail-open ON.**  An unrecognized value (e.g.
     ``"banana"``) is treated as ON, matching the module's other
     fail-open conventions (see ``_build_shadow_record``'s
     never-break-live-dispatch contract).
  5. **Live decision byte-identical ON vs OFF.**  Shadow is telemetry
     only — gating it must never change the stdout decision JSON.

All tests drive ``claude_wayfinder.match`` the same way the existing
suite does: via ``tests.test_match.conftest._run`` (subprocess,
``DISPATCH_CATALOG_PATH`` + ``DISPATCH_LOG_PATH`` pointed at tmp files)
for black-box behavior, and via direct ``main()`` calls with
``unittest.mock.patch`` for the "compute was actually skipped"
assertion in case 3 (mirroring the pattern already established in
``test_shadow_mode.py``'s B3 test).

Expected RED reason: no ``DISPATCH_SHADOW`` gate exists yet in
``_main.py`` — shadow compute currently runs unconditionally. Cases 1,
2, 4, and 5 are expected to pass "by accident" (shadow is always on
today and gating it never touches stdout), while case 3 is the RED
anchor: without the gate, ``compose_route`` is always called and the
``"shadow"`` key is always present, so both the "not called" and the
"key absent" assertions fail.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

import claude_wayfinder.match as _match_mod
from tests.test_match.conftest import REPO_ROOT, _catalog, _make_agent, _run

# ---------------------------------------------------------------------------
# Worktree-shadowing guard (see agent-memory
# feedback_worktree_python_shadowing) — fail loudly, at collection time,
# if `claude_wayfinder` resolved to a package outside this worktree. The
# parent checkout's shared .venv installs `claude_wayfinder` in editable
# mode pointed at the PARENT's src/; running pytest here without
# PYTHONPATH set to this worktree's src/ silently exercises the wrong
# code and produces a false green.
# ---------------------------------------------------------------------------

_resolved_match_pkg = Path(_match_mod.__file__).resolve()
assert REPO_ROOT in _resolved_match_pkg.parents, (
    f"claude_wayfinder.match resolved to {_resolved_match_pkg}, which is "
    f"NOT under this worktree's root ({REPO_ROOT}). The parent checkout's "
    "shared .venv is shadowing this worktree's src/claude_wayfinder via "
    "its editable-install .pth file. Run pytest with "
    "PYTHONPATH=<this-worktree>/src set (or create a worktree-local venv) "
    "so tests exercise the WORKTREE's code, not the parent's."
)

# ---------------------------------------------------------------------------
# Catalog and stdin fixtures
# ---------------------------------------------------------------------------

#: Minimal catalog whose sole agent has a clear keyword winner.
_FLAG_CATALOG = _catalog(
    [
        _make_agent(
            "code-writer",
            keywords=[{"term": "implement", "weight": 1.0}],
            path_globs=["**/*.py"],
        ),
    ]
)

#: Scored-path input with labels so parse_labels/compose_route have
#: something to work with.
_FLAG_INPUT: dict[str, Any] = {
    "task_description": "implement the new feature",
    "file_paths": ["src/main.py"],
    "domain": "code",
    "posture": "build",
    "confidence": "high",
    "area_span": 1,
}


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


def _run_and_read_log(
    tmp_path: Path,
    log_name: str,
    *,
    shadow_env: str | None,
) -> dict[str, Any]:
    """Run the matcher once and return the single resulting log entry.

    Args:
        tmp_path: pytest tmp dir for catalog + log files.
        log_name: File name (under ``tmp_path``) for the JSONL log.
        shadow_env: Value to set for ``DISPATCH_SHADOW``, or ``None`` to
            leave it unset in the extra env passed to the subprocess
            (the surrounding test is responsible for ensuring it is
            also absent from the parent process's environment via
            ``monkeypatch.delenv``).

    Returns:
        The single parsed log entry dict written by the run.
    """
    log_path = tmp_path / log_name
    extra_env = {"DISPATCH_LOG_PATH": str(log_path)}
    if shadow_env is not None:
        extra_env["DISPATCH_SHADOW"] = shadow_env
    result = _run(_FLAG_INPUT, _FLAG_CATALOG, extra_env=extra_env, tmp_path=tmp_path)
    assert result.returncode == 0, result.stderr
    entries = _read_log_lines(log_path)
    assert len(entries) == 1, f"Expected exactly one log entry, got {len(entries)}"
    return entries[0]


# ---------------------------------------------------------------------------
# 1 — Absent DISPATCH_SHADOW → shadow ON (default)
# ---------------------------------------------------------------------------


class TestDispatchShadowDefaultOn:
    """No ``DISPATCH_SHADOW`` env var set → shadow compute runs by default."""

    def test_shadow_key_present_when_env_var_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Log entry carries a 'shadow' key when the gate var is unset.

        This is the "shadow ON by default" contract — omitting
        ``DISPATCH_SHADOW`` entirely must behave identically to
        explicitly requesting shadow ON.
        """
        monkeypatch.delenv("DISPATCH_SHADOW", raising=False)
        entry = _run_and_read_log(tmp_path, "shadow_absent.jsonl", shadow_env=None)
        assert "shadow" in entry, (
            "Log entry must have a 'shadow' key when DISPATCH_SHADOW is "
            f"unset (default ON). Entry keys: {sorted(entry.keys())}"
        )


# ---------------------------------------------------------------------------
# 2 — Explicit truthy DISPATCH_SHADOW → shadow ON
# ---------------------------------------------------------------------------


class TestDispatchShadowExplicitTruthy:
    """``DISPATCH_SHADOW`` set to a truthy value → shadow compute runs."""

    @pytest.mark.parametrize(
        "value", ["1", "true", "True", "TRUE", "yes", "Yes", "YES"]
    )
    def test_shadow_key_present_for_truthy_values(
        self, tmp_path: Path, value: str
    ) -> None:
        """Every case-insensitive spelling of {1, true, yes} yields shadow ON."""
        entry = _run_and_read_log(
            tmp_path, f"shadow_truthy_{value}.jsonl", shadow_env=value
        )
        assert "shadow" in entry, (
            f"DISPATCH_SHADOW={value!r} must yield shadow ON (a 'shadow' "
            f"key in the log entry). Entry keys: {sorted(entry.keys())}"
        )


# ---------------------------------------------------------------------------
# 3 — Explicit falsey DISPATCH_SHADOW → shadow OFF, compute SKIPPED
# ---------------------------------------------------------------------------


class TestDispatchShadowExplicitFalsey:
    """``DISPATCH_SHADOW`` set to a falsey value → shadow compute is skipped."""

    @pytest.mark.parametrize(
        "value", ["0", "false", "False", "FALSE", "no", "No", "NO"]
    )
    def test_shadow_key_absent_for_falsey_values(
        self, tmp_path: Path, value: str
    ) -> None:
        """Every case-insensitive spelling of {0, false, no} yields shadow OFF.

        This is a RED anchor: today shadow always runs, so the 'shadow'
        key is always present regardless of this env var.
        """
        entry = _run_and_read_log(
            tmp_path, f"shadow_falsey_{value}.jsonl", shadow_env=value
        )
        assert "shadow" not in entry, (
            f"DISPATCH_SHADOW={value!r} must yield shadow OFF (no "
            f"'shadow' key in the log entry). Entry keys: "
            f"{sorted(entry.keys())}"
        )

    @pytest.mark.parametrize("value", ["0", "false", "no"])
    def test_compose_route_not_called_for_falsey_values(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        """Shadow compute is actually SKIPPED, not just discarded after running.

        Drives ``main()`` in-process (mirroring test_shadow_mode.py's B3
        pattern) so ``unittest.mock.patch`` can observe whether
        ``compose_route`` was invoked at all inside ``_main``'s module
        namespace.

        This is the primary RED anchor for case 3: without the gate,
        ``compose_route`` is called unconditionally, so
        ``mock_compose.assert_not_called()`` fails.
        """
        import claude_wayfinder.match._main as _main_mod

        log_path = tmp_path / f"shadow_skip_{value}.jsonl"
        catalog_path = tmp_path / "catalog.json"
        catalog_path.write_text(json.dumps(_FLAG_CATALOG), encoding="utf-8")

        # A concrete dict return (not a bare MagicMock) keeps the pre-fix
        # (gate absent) path fully functional through _build_shadow_record
        # and json.dumps, so the RED failure surfaces as the intended
        # mock_compose.assert_not_called() AssertionError rather than an
        # unrelated "MagicMock is not JSON serializable" crash.
        with mock.patch.object(
            _main_mod,
            "compose_route",
            return_value={
                "decision": "delegate",
                "agent": "code-writer",
                "confidence": 0.9,
                "disposition_source": "scored",
            },
        ) as mock_compose:
            monkeypatch.setenv("DISPATCH_CATALOG_PATH", str(catalog_path))
            monkeypatch.setenv("DISPATCH_LOG_PATH", str(log_path))
            monkeypatch.setenv("DISPATCH_SHADOW", value)

            captured_stdout = io.StringIO()
            monkeypatch.setattr(sys, "stdout", captured_stdout)
            monkeypatch.setattr(
                sys, "stdin", io.StringIO(json.dumps(_FLAG_INPUT))
            )

            _main_mod.main([])

        mock_compose.assert_not_called()

        stdout_text = captured_stdout.getvalue().strip()
        assert stdout_text, "main() must still emit JSON to stdout when shadow is OFF"
        out = json.loads(stdout_text)
        assert out.get("decision"), f"Live decision missing with shadow OFF: {out!r}"


# ---------------------------------------------------------------------------
# 4 — Malformed DISPATCH_SHADOW value → fail-open ON
# ---------------------------------------------------------------------------


class TestDispatchShadowMalformedFailsOpen:
    """An unrecognized ``DISPATCH_SHADOW`` value fails open to shadow ON."""

    @pytest.mark.parametrize("value", ["banana", "enabled", "disable", "2"])
    def test_shadow_key_present_for_malformed_values(
        self, tmp_path: Path, value: str
    ) -> None:
        """Values outside the recognized truthy/falsey sets default to ON.

        Includes near-miss strings ("enabled", "disable") that are NOT
        exact members of the truthy/falsey sets, to guard against a
        naive substring-match implementation silently treating them as
        falsey.
        """
        entry = _run_and_read_log(
            tmp_path, f"shadow_malformed_{value}.jsonl", shadow_env=value
        )
        assert "shadow" in entry, (
            f"DISPATCH_SHADOW={value!r} is malformed and must fail open "
            f"to shadow ON. Entry keys: {sorted(entry.keys())}"
        )

    @pytest.mark.parametrize("value", [" false ", " 0 "])
    def test_shadow_key_present_for_whitespace_padded_falsey_values(
        self, tmp_path: Path, value: str
    ) -> None:
        """Whitespace-padded falsey spellings fail open to shadow ON.

        Only an EXACT case-insensitive match of {"0", "false", "no"}
        disables shadow. A padded value like " false " is not a member
        of that exact set, so it must fail open to ON — guards against
        an implementation that ``.strip()``s the value before comparing.
        """
        entry = _run_and_read_log(
            tmp_path,
            f"shadow_padded_{value.strip()}.jsonl",
            shadow_env=value,
        )
        assert "shadow" in entry, (
            f"DISPATCH_SHADOW={value!r} is whitespace-padded and must "
            f"fail open to shadow ON. Entry keys: {sorted(entry.keys())}"
        )


# ---------------------------------------------------------------------------
# 5 — Live decision is byte-identical regardless of the gate
# ---------------------------------------------------------------------------


class TestDispatchShadowLiveDecisionUnaffected:
    """The gate is telemetry-only — it must never change the stdout decision."""

    def test_stdout_decision_identical_absent_on_and_off(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Same stdin yields byte-identical stdout with the gate absent, ON, or OFF.

        Runs the matcher three times over the same input — with
        DISPATCH_SHADOW unset, explicitly "1", and explicitly "0" — and
        asserts the parsed stdout decision JSON is identical across all
        three. Shadow gating must never leak into the live decision.
        """
        monkeypatch.delenv("DISPATCH_SHADOW", raising=False)
        result_absent = _run(_FLAG_INPUT, _FLAG_CATALOG, tmp_path=tmp_path)
        assert result_absent.returncode == 0, result_absent.stderr

        result_on = _run(
            _FLAG_INPUT,
            _FLAG_CATALOG,
            extra_env={"DISPATCH_SHADOW": "1"},
            tmp_path=tmp_path,
        )
        assert result_on.returncode == 0, result_on.stderr

        result_off = _run(
            _FLAG_INPUT,
            _FLAG_CATALOG,
            extra_env={"DISPATCH_SHADOW": "0"},
            tmp_path=tmp_path,
        )
        assert result_off.returncode == 0, result_off.stderr

        assert result_absent.stdout == result_on.stdout == result_off.stdout, (
            "Live stdout must be byte-identical regardless of "
            f"DISPATCH_SHADOW. absent={result_absent.stdout!r}, "
            f"on={result_on.stdout!r}, off={result_off.stdout!r}"
        )

        out_absent = json.loads(result_absent.stdout)
        out_on = json.loads(result_on.stdout)
        out_off = json.loads(result_off.stdout)

        assert out_absent == out_on == out_off, (
            "Live stdout decision must be byte-identical regardless of "
            f"DISPATCH_SHADOW. absent={out_absent!r}, on={out_on!r}, "
            f"off={out_off!r}"
        )
