"""Legacy tests for claude_wayfinder.build_catalog (pre-package refactor).

.. deprecated::
   This file contains tests that reference the old monolith
   ``src/claude_wayfinder/build_catalog.py`` path.  They are superseded by
   the per-submodule test files:

   - ``test_build_catalog_validate.py``
   - ``test_build_catalog_discover.py``
   - ``test_build_catalog_process.py``
   - ``test_build_catalog_main.py``

The remaining test is retained so CI continues to exercise the known-failing
case (build_catalog.py no longer exists as a file — it is now a package).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "src" / "claude_wayfinder" / "build_catalog.py"


def test_cli_help_returns_zero() -> None:
    """The script must respond to --help with exit code 0."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "build" in result.stdout.lower()
