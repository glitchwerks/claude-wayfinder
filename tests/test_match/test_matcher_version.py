"""Tests for _get_matcher_version() dist-version fallback (issue #460).

``_get_matcher_version()`` derives the matcher version by shelling
``git rev-parse --short HEAD`` from the source-file directory.  On
pip-installed wheels there is no ``.git`` directory in site-packages, so
the git lookup always fails and the function returns the literal string
``"unknown"`` — which is what happens for essentially all real consumer
traffic.

Target behavior:

1. When the git lookup is unavailable (missing binary, non-zero return,
   timeout, not a repo, or any other subprocess failure), fall back to
   the installed distribution version via
   ``importlib.metadata.version("claude-wayfinder")`` instead of
   returning ``"unknown"``.
2. ``"unknown"`` is returned ONLY when BOTH the git lookup AND the
   ``importlib.metadata`` lookup fail.
3. When the git lookup succeeds, its short SHA is still returned
   unchanged — existing dev-checkout behavior must not regress.
"""

from __future__ import annotations

import subprocess
from importlib.metadata import PackageNotFoundError
from unittest.mock import patch

from claude_wayfinder.match._catalog import _get_matcher_version

# NOTE for the implementer: every ``importlib.metadata.version`` patch below
# targets the *source* module (``importlib.metadata.version``), not a
# ``claude_wayfinder.match._catalog``-local name. This only intercepts calls
# made as ``importlib.metadata.version(...)`` after ``import
# importlib.metadata`` — matching the convention already established in
# tests/test_version.py. A ``from importlib.metadata import version as
# _version`` local alias would bind its own name and NOT be visible to this
# patch, so the implementation must use the module-attribute form.


class TestMatcherVersionGitFallback:
    """_get_matcher_version() falls back to the installed dist version (#460)."""

    def test_git_missing_binary_falls_back_to_dist_version(self) -> None:
        """A missing git binary (FileNotFoundError) falls back to the
        installed distribution version instead of returning "unknown".
        """
        with (
            patch(
                "claude_wayfinder.match._catalog.subprocess.run",
                side_effect=FileNotFoundError("git binary not found"),
            ),
            patch("importlib.metadata.version", return_value="1.3.0"),
        ):
            result = _get_matcher_version()
        assert result == "1.3.0", (
            f"Expected fallback to dist version '1.3.0', got {result!r}"
        )

    def test_git_nonzero_returncode_falls_back_to_dist_version(self) -> None:
        """A non-zero git returncode (e.g. not a repo) falls back to the
        installed distribution version rather than "unknown".
        """
        fake_result = subprocess.CompletedProcess(
            args=["git", "rev-parse", "--short", "HEAD"],
            returncode=128,
            stdout="",
            stderr="fatal: not a git repository",
        )
        with (
            patch(
                "claude_wayfinder.match._catalog.subprocess.run",
                return_value=fake_result,
            ),
            patch("importlib.metadata.version", return_value="1.3.0"),
        ):
            result = _get_matcher_version()
        assert result == "1.3.0", (
            f"Expected fallback to dist version '1.3.0', got {result!r}"
        )

    def test_git_timeout_falls_back_to_dist_version(self) -> None:
        """A subprocess timeout falls back to the installed distribution
        version rather than "unknown".
        """
        with (
            patch(
                "claude_wayfinder.match._catalog.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="git", timeout=1),
            ),
            patch("importlib.metadata.version", return_value="2.0.1"),
        ):
            result = _get_matcher_version()
        assert result == "2.0.1", (
            f"Expected fallback to dist version '2.0.1', got {result!r}"
        )

    def test_git_not_a_repo_oserror_falls_back_to_dist_version(self) -> None:
        """Any other subprocess failure (e.g. OSError) also falls back to
        the installed distribution version rather than "unknown".
        """
        with (
            patch(
                "claude_wayfinder.match._catalog.subprocess.run",
                side_effect=OSError("permission denied"),
            ),
            patch("importlib.metadata.version", return_value="1.3.0"),
        ):
            result = _get_matcher_version()
        assert result == "1.3.0", (
            f"Expected fallback to dist version '1.3.0', got {result!r}"
        )

    def test_both_git_and_metadata_fail_returns_unknown(self) -> None:
        """"unknown" is returned only when BOTH the git lookup AND the
        importlib.metadata lookup fail.
        """
        with (
            patch(
                "claude_wayfinder.match._catalog.subprocess.run",
                side_effect=FileNotFoundError("git binary not found"),
            ),
            patch(
                "importlib.metadata.version",
                side_effect=PackageNotFoundError("claude-wayfinder"),
            ),
        ):
            result = _get_matcher_version()
        assert result == "unknown", (
            f"Expected 'unknown' when both lookups fail, got {result!r}"
        )

    def test_git_success_returns_sha_unchanged(self) -> None:
        """When the git lookup succeeds, its short SHA is returned
        unchanged — preserves existing dev-checkout behavior.

        Also asserts the dist-version fallback is never consulted when
        git already succeeded (no unnecessary fallback call).
        """
        fake_result = subprocess.CompletedProcess(
            args=["git", "rev-parse", "--short", "HEAD"],
            returncode=0,
            stdout="abc1234\n",
            stderr="",
        )
        with (
            patch(
                "claude_wayfinder.match._catalog.subprocess.run",
                return_value=fake_result,
            ),
            patch(
                "importlib.metadata.version", return_value="1.3.0"
            ) as mock_version,
        ):
            result = _get_matcher_version()
        assert result == "abc1234", (
            f"Expected git SHA 'abc1234' when git succeeds, got {result!r}"
        )
        mock_version.assert_not_called()
