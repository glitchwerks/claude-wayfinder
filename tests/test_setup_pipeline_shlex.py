"""Regression tests for setup_pipeline.py shlex/Windows path handling.

Regression: shlex.split's POSIX default treats backslash as an escape
character, so a Windows path like D:\\a\\claude-wayfinder\\claude-wayfinder
was mangled to D:aclaude-wayfinderclaude-wayfinder before pip saw it.

Fix: pass posix=False on Windows (os.name == "nt").

See: GitHub issue #190, CI failure on windows-latest runner.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

import tests.integration.setup_pipeline as setup_pipeline
from tests.integration.setup_pipeline import pip_install

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_success_result() -> MagicMock:
    """Return a CompletedProcess-like mock that indicates success."""
    result = MagicMock(spec=subprocess.CompletedProcess)
    result.returncode = 0
    result.stdout = ""
    result.stderr = ""
    return result


# ---------------------------------------------------------------------------
# Regression: Windows paths must not be mangled by shlex.split
# ---------------------------------------------------------------------------


def test_pip_install_preserves_windows_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: shlex.split must not mangle Windows-style paths in pip_spec.

    Issue: posix=True (the default) shlex.split treats backslash as an escape
    character, producing 'D:aclaude-wayfinderclaude-wayfinder' from the
    Windows path 'D:\\a\\claude-wayfinder\\claude-wayfinder'. The pip install
    then fails with 'Invalid requirement'.

    Fix: pass posix=False when os.name == 'nt'.
    """
    windows_path = r"D:\a\claude-wayfinder\claude-wayfinder"

    # Simulate a venv layout so get_venv_python resolves correctly.
    venv_dir = tmp_path / ".venv"
    scripts_dir = venv_dir / "Scripts"
    scripts_dir.mkdir(parents=True)
    fake_python = scripts_dir / "python.exe"
    fake_python.write_text("", encoding="utf-8")

    # Set pip spec to a Windows-style path.
    monkeypatch.setenv("CLAUDE_WAYFINDER_PIP_SPEC", windows_path)

    # Force os.name to "nt" so the Windows branch is exercised on any platform.
    monkeypatch.setattr(setup_pipeline.os, "name", "nt")

    captured: list[list[Any]] = []

    def _capture_run(
        args: list[Any], **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        captured.append(list(args))
        return _make_success_result()  # type: ignore[return-value]

    monkeypatch.setattr(setup_pipeline.subprocess, "run", _capture_run)

    pip_install(venv_dir=venv_dir, version="0.0.0")

    assert captured, "subprocess.run was never called"
    args = captured[0]
    # The Windows path must appear verbatim — backslashes must NOT be eaten.
    assert windows_path in args, (
        f"Windows path was mangled. Expected {windows_path!r} in args, "
        f"got: {args}"
    )


def test_pip_install_posix_editable_still_splits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POSIX editable install ('-e /path/to/repo') still splits into two tokens.

    This guards against over-correcting: on non-Windows, posix=True must stay
    active so '-e /path/to/repo' splits correctly into ['-e', '/path/to/repo']
    rather than being passed as a single argument.
    """
    posix_spec = "-e /path/to/repo"

    venv_dir = tmp_path / ".venv"
    bin_dir = venv_dir / "bin"
    bin_dir.mkdir(parents=True)
    fake_python = bin_dir / "python"
    fake_python.write_text("", encoding="utf-8")

    monkeypatch.setenv("CLAUDE_WAYFINDER_PIP_SPEC", posix_spec)
    # Simulate a POSIX environment.
    monkeypatch.setattr(setup_pipeline.os, "name", "posix")

    captured: list[list[Any]] = []

    def _capture_run(
        args: list[Any], **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        captured.append(list(args))
        return _make_success_result()  # type: ignore[return-value]

    monkeypatch.setattr(setup_pipeline.subprocess, "run", _capture_run)

    pip_install(venv_dir=venv_dir, version="0.0.0")

    assert captured, "subprocess.run was never called"
    args = captured[0]
    # '-e' and '/path/to/repo' must be separate tokens, not one fused arg.
    assert "-e" in args, f"'-e' flag missing from args: {args}"
    assert "/path/to/repo" in args, f"'/path/to/repo' missing from args: {args}"
    assert "-e /path/to/repo" not in args, (
        f"POSIX spec was not split — passed as one token: {args}"
    )
