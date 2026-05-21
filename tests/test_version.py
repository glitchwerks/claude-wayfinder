"""Tests for claude_wayfinder.__version__ metadata consistency.

Regression guard: ``__version__`` must be derived from installed package
metadata rather than hardcoded, so it never drifts from the version in
``pyproject.toml`` / the installed dist-info.
"""

from __future__ import annotations

import importlib.metadata


def test_version_matches_installed_metadata() -> None:
    """``claude_wayfinder.__version__`` must equal the installed package version.

    When the package is installed (including editable ``pip install -e .``),
    ``importlib.metadata.version("claude-wayfinder")`` reads the dist-info
    written at install time.  If ``__version__`` is derived from that same
    source, the two must be identical.  A mismatch means the attribute was
    hardcoded and has drifted.
    """
    import claude_wayfinder

    installed = importlib.metadata.version("claude-wayfinder")
    assert claude_wayfinder.__version__ == installed, (
        f"claude_wayfinder.__version__ ({claude_wayfinder.__version__!r}) "
        f"does not match installed metadata ({installed!r}). "
        "Derive __version__ from importlib.metadata instead of hardcoding it."
    )
