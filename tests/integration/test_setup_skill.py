"""End-to-end smoke test for the setup pipeline.

Runs the full skill body's 8 steps against a real Python ≥3.11 and a
real PyPI (or a local install via $CLAUDE_WAYFINDER_PIP_SPEC). Asserts
that the venv materialises correctly, the import works, and the flag
file is shaped correctly.

This test is NOT path-filtered — runs on every PR per spec § 7 (test
surfaces) and inquisitor pass-1 charge 11.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import pytest

from tests.integration import setup_pipeline


@pytest.fixture()
def fake_plugin_data(monkeypatch: pytest.MonkeyPatch):
    """Provide a temp dir as $CLAUDE_PLUGIN_DATA for the duration of one test.

    Yields:
        Path to a temporary directory that has been set as
        ``$CLAUDE_PLUGIN_DATA`` for the duration of the test.
    """
    with tempfile.TemporaryDirectory(prefix="wayfinder-smoke-") as tmp:
        monkeypatch.setenv("CLAUDE_PLUGIN_DATA", tmp)
        yield Path(tmp)


def _read_plugin_version() -> str:
    """Read the bundled plugin version from pyproject.toml.

    Returns:
        The version string from the ``[project]`` table in
        ``pyproject.toml`` (e.g. ``"0.3.6"``).

    Raises:
        AssertionError: If the version field cannot be found.
    """
    import re

    pyproject = Path(__file__).parent.parent.parent / "pyproject.toml"
    content = pyproject.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
    assert match, "Could not find version in pyproject.toml"
    return match.group(1)


def test_full_pipeline_smoke(fake_plugin_data: Path) -> None:
    """The 8-step pipeline produces a working venv with claude-wayfinder importable.

    Exercises the real subprocess paths: discover_python, create_venv,
    pip_install, verify_import, and write_flag all run against real
    system tools. The $CLAUDE_WAYFINDER_PIP_SPEC env-var override is
    honoured when set, allowing pre-v0.4.0 CI to install from the local
    plugin root rather than PyPI.

    Args:
        fake_plugin_data: Temporary directory injected by the
            ``fake_plugin_data`` fixture, used as $CLAUDE_PLUGIN_DATA.
    """
    version = _read_plugin_version()

    # Run pipeline (uses real Python on $PATH; real PyPI or override spec)
    flag_path = setup_pipeline.run_full_pipeline(version)

    # Step 7 wrote the flag — verify shape
    assert flag_path.exists()
    flag = json.loads(flag_path.read_text(encoding="utf-8"))
    assert flag["version"] == version
    assert "venv_path" in flag
    assert "interpreter" in flag
    assert "installed_at" in flag

    # The venv exists at the recorded path
    venv_dir = Path(flag["venv_path"])
    assert venv_dir.exists()
    assert venv_dir.is_dir()

    # The venv Python exists and is the recorded one's child
    venv_python = setup_pipeline.get_venv_python(venv_dir)
    assert venv_python.exists()

    # claude_wayfinder imports from inside the venv
    result = subprocess.run(
        [
            str(venv_python),
            "-c",
            "import claude_wayfinder; print(claude_wayfinder.__file__)",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"import claude_wayfinder failed: {result.stderr}"
    )
    # Sanity: it imports from inside our venv, not the system Python
    assert str(venv_dir) in result.stdout, (
        f"Imported claude_wayfinder from outside the venv: {result.stdout!r}"
    )


def test_wipe_idempotent(fake_plugin_data: Path) -> None:
    """Step 3 (wipe) is a no-op when no venv exists; succeeds when one does.

    Args:
        fake_plugin_data: Temporary directory injected by the
            ``fake_plugin_data`` fixture.
    """
    venv_dir = fake_plugin_data / "venv"
    # No-op: directory does not exist — should not raise
    setup_pipeline.wipe_venv(venv_dir)
    assert not venv_dir.exists()
    # Create then wipe — directory should be removed
    venv_dir.mkdir()
    (venv_dir / "marker").write_text("hello", encoding="utf-8")
    setup_pipeline.wipe_venv(venv_dir)
    assert not venv_dir.exists()


def test_discover_python_finds_real_interpreter(fake_plugin_data: Path) -> None:
    """Step 2 finds the CI runner's Python ≥3.11.

    Args:
        fake_plugin_data: Temporary directory injected by the
            ``fake_plugin_data`` fixture (unused directly, but ensures
            the env is properly isolated).
    """
    interpreter = setup_pipeline.discover_python()
    assert interpreter, "Should find at least one Python ≥3.11 on CI"
