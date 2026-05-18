"""Regression test for issue #134 — no RuntimeWarning from dispatch.

Verifies that invoking ``run_dispatch()`` in-process does NOT emit the
``RuntimeWarning: 'claude_wayfinder.match' found in sys.modules``
warning that was previously produced when the dispatch entry point
spawned ``python -m claude_wayfinder.match`` as a subprocess (because
``claude_wayfinder.__init__`` had already imported match, so runpy
found it in ``sys.modules`` before executing it as ``__main__``).

The fix replaces the subprocess with a direct in-process call to
``claude_wayfinder.match.main()``.  This test asserts the warning is
absent and that the dispatch result is still correct JSON.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_DEMO_CATALOG_PATH = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "claude_wayfinder"
    / "fixtures"
    / "demo-catalog.json"
)

#: Minimal valid dispatch context JSON (5-field shape from design § 2.2).
_VALID_CONTEXT: dict[str, Any] = {
    "task_description": "implement the authentication module",
    "file_paths": ["src/auth.py"],
    "agent_mentions": [],
    "tool_mentions": [],
    "command_prefix": None,
}


@pytest.fixture()
def minimal_catalog(tmp_path: Path) -> Path:
    """Write a copy of the demo catalog to a tmp directory and return its path.

    Args:
        tmp_path: pytest temporary directory.

    Returns:
        Path to the temporary catalog file.
    """
    catalog = tmp_path / "dispatch-catalog.json"
    catalog.write_text(
        _DEMO_CATALOG_PATH.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return catalog


# ---------------------------------------------------------------------------
# Regression: issue #134 — no RuntimeWarning on dispatch
# ---------------------------------------------------------------------------


class TestNoRuntimeWarningOnDispatch:
    """``run_dispatch()`` must not emit a RuntimeWarning about
    ``claude_wayfinder.match`` import order (#134)."""

    def test_no_runtimewarning_in_stderr(
        self,
        minimal_catalog: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Calling run_dispatch() must not produce a RuntimeWarning.

        The warning phrase
        ``RuntimeWarning: 'claude_wayfinder.match' found in sys.modules``
        must be absent from captured stderr.
        """
        from claude_wayfinder._dispatch import run_dispatch

        monkeypatch.setenv(
            "DISPATCH_CATALOG_PATH", str(minimal_catalog)
        )
        # Suppress DISPATCH_LOG_PATH so match.py does not try to write a log.
        monkeypatch.delenv("DISPATCH_LOG_PATH", raising=False)

        out = io.StringIO()
        stdin_data = json.dumps(_VALID_CONTEXT)

        run_dispatch(stdin_data=stdin_data, out=out)

        captured = capsys.readouterr()
        assert "RuntimeWarning" not in captured.err, (
            "RuntimeWarning appeared in stderr — the subprocess invocation "
            "of 'python -m claude_wayfinder.match' is still in use.\n"
            f"stderr: {captured.err}"
        )
        assert "claude_wayfinder.match" not in captured.err or (
            # Allow "claude_wayfinder.match" to appear in stderr ONLY if it
            # is NOT part of the specific runpy warning phrase.
            "found in sys.modules" not in captured.err
        ), (
            "Runpy warning phrase detected in stderr.\n"
            f"stderr: {captured.err}"
        )

    def test_dispatch_still_returns_valid_json(
        self,
        minimal_catalog: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Replacing subprocess with in-process call must preserve output.

        The JSON written to ``out`` must parse and contain a ``decision``
        field, confirming the dispatch pipeline still runs correctly.
        """
        from claude_wayfinder._dispatch import run_dispatch

        monkeypatch.setenv(
            "DISPATCH_CATALOG_PATH", str(minimal_catalog)
        )
        monkeypatch.delenv("DISPATCH_LOG_PATH", raising=False)

        out = io.StringIO()
        stdin_data = json.dumps(_VALID_CONTEXT)

        rc = run_dispatch(stdin_data=stdin_data, out=out)

        assert rc == 0, (
            f"run_dispatch returned non-zero exit code {rc}"
        )

        output = out.getvalue()
        try:
            decision = json.loads(output)
        except json.JSONDecodeError as exc:
            pytest.fail(
                f"run_dispatch output is not valid JSON: {exc}\n"
                f"output: {output!r}"
            )

        assert "decision" in decision, (
            f"'decision' key missing from dispatch output.\n"
            f"output: {decision}"
        )
