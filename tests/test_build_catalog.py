"""Smoke test for the claude_wayfinder.build_catalog CLI entry point.

build_catalog is now a package (``src/claude_wayfinder/build_catalog/``);
it must be invoked via ``python -m claude_wayfinder.build_catalog``, not as
a file path.  The per-submodule tests live in:

   - ``test_build_catalog_validate.py``
   - ``test_build_catalog_discover.py``
   - ``test_build_catalog_process.py``
   - ``test_build_catalog_main.py``
"""

from __future__ import annotations

import subprocess
import sys


def test_cli_help_returns_zero() -> None:
    """The package entry point must respond to --help with exit code 0."""
    result = subprocess.run(
        [sys.executable, "-m", "claude_wayfinder.build_catalog", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "build" in result.stdout.lower()
