"""Regression test: scripts.corpus.__main__ must add src/ to sys.path.

Reproduces the bug from Codex P2 on c60ccce: ``scripts/corpus/__main__.py``
adds only ``scripts/`` to ``sys.path``, then imports ``corpus.profiler`` and
``corpus.builder``.  Those modules call
``from claude_wayfinder.log_filter import is_organic_entry``, which requires
the top-level ``claude_wayfinder`` package on ``sys.path``.  In a fresh
checkout (no editable install), that package lives under ``src/`` — but
``__main__.py`` never adds ``src/``, so the import raises
``ModuleNotFoundError: No module named 'claude_wayfinder'``.

Why -S is not used here
-----------------------
``python -S`` disables site-packages entirely.  That would also hide
``snowballstemmer``, a third-party dep in the ``claude_wayfinder.match``
import chain, causing a *different* ``ModuleNotFoundError`` even after the
fix.  Instead this test keeps site-packages intact (so all deps resolve) but
strips the editable-install ``.pth`` entry that exposes ``src/`` from
``sys.path`` before the CLI module is imported.  This simulates a fresh
checkout: deps are installed, but the package itself is not editable-installed.

RED (pre-fix): the subprocess fails with
    ModuleNotFoundError: No module named 'claude_wayfinder'
GREEN (post-fix): ``__main__.py`` adds ``src/`` to ``sys.path`` before the
corpus imports, so the subprocess exits 0 or 2 (stop-gate, no log entries).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Absolute repo root: tests/test_corpus/ → tests/ → repo root
REPO_ROOT = Path(__file__).resolve().parents[2]

# The path that the editable-install .pth adds; the fix must re-add this.
_SRC_DIR = REPO_ROOT / "src"


def test_corpus_cli_resolves_claude_wayfinder_without_editable_install() -> None:
    """corpus CLI must import claude_wayfinder without relying on editable install.

    Strips the editable-install src/ entry from sys.path before importing the
    CLI module, simulating a fresh checkout where claude_wayfinder is NOT
    already on sys.path.  The fix (adding src/ inside __main__.py) must make
    the import succeed.

    Reproduces: ModuleNotFoundError: No module named 'claude_wayfinder'
    Root cause: __main__.py adds scripts/ only; corpus sub-modules import
    from claude_wayfinder.log_filter (added in c60ccce).
    """
    # Bootstrap: strip src/ from sys.path, then attempt the import.
    # runpy.run_module executes scripts/corpus/__main__.py with __name__
    # forced to '__main__', reproducing the documented entry point exactly.
    # We pass --help via sys.argv so the CLI exits before touching the log.
    bootstrap = (
        "import sys, runpy\n"
        # Remove all entries that resolve to the editable-install src/ dir.
        f"_src = {str(_SRC_DIR)!r}\n"
        "sys.path = [p for p in sys.path if p != _src]\n"
        # Override argv so argparse handles --help and exits 0.
        "sys.argv = ['scripts.corpus', '--help']\n"
        "runpy.run_module('scripts.corpus', run_name='__main__', alter_sys=True)\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", bootstrap],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert "ModuleNotFoundError" not in result.stderr, (
        f"Import failed with ModuleNotFoundError — __main__.py does not add "
        f"src/ to sys.path before importing corpus sub-modules.\n"
        f"stderr:\n{result.stderr}"
    )
    assert result.returncode == 0, (
        f"Expected exit 0 (--help), got {result.returncode}.\nstderr:\n{result.stderr}"
    )
