"""Tests for the ``python -m claude_wayfinder demo`` CLI command.

Verifies that the demo sub-command runs against bundled fixtures and
prints output containing all 6 decision branches.  The test runs the
demo via ``subprocess.run`` so it exercises the real entry point on
the real bundled fixtures — no mocking of the matcher internals.

All 6 decision branches must be present in the demo output:
    delegate, self_handle, self_handle_unaided, advisory,
    ask_user, needs_more_detail

Note: 'ambiguous' was removed in v0.9.0 (#202); tie scenarios now emit
'advisory'.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_demo() -> subprocess.CompletedProcess[str]:
    """Run ``python -m claude_wayfinder demo`` and return the result.

    Returns:
        A ``CompletedProcess`` with ``stdout`` and ``stderr`` captured as
        strings.
    """
    return subprocess.run(
        [sys.executable, "-m", "claude_wayfinder", "demo"],
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# Basic invocation
# ---------------------------------------------------------------------------


class TestDemoInvocation:
    """The demo command must start, run to completion, and exit cleanly."""

    def test_demo_exits_zero(self) -> None:
        """``python -m claude_wayfinder demo`` exits with code 0."""
        result = _run_demo()
        assert result.returncode == 0, (
            f"demo exited {result.returncode}.\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )

    def test_demo_produces_stdout(self) -> None:
        """Demo must write non-empty output to stdout."""
        result = _run_demo()
        assert result.stdout.strip(), (
            "demo produced no stdout output."
        )


# ---------------------------------------------------------------------------
# All 6 decision branches must appear in output
# ---------------------------------------------------------------------------

_SIX_BRANCHES = [
    "delegate",
    "self_handle",
    "self_handle_unaided",
    "advisory",
    "ask_user",
    "needs_more_detail",
]


class TestAllSixBranches:
    """Each of the 6 decision branches must appear in the demo output.

    The demo iterates the bundled demo-prompts.json and annotates each
    output line with the decision branch name.  These tests assert that
    the full set of 6 branches is exercised.

    'ambiguous' was removed in v0.9.0 (#202); tie scenarios now emit
    'advisory' instead.
    """

    @pytest.fixture(scope="class")
    def demo_output(self) -> str:
        """Run the demo once and return its stdout for the whole class.

        Returns:
            The captured stdout of ``python -m claude_wayfinder demo``.
        """
        result = _run_demo()
        assert result.returncode == 0, (
            f"demo failed; cannot check branches.\n"
            f"stderr: {result.stderr}"
        )
        return result.stdout

    @pytest.mark.parametrize("branch", _SIX_BRANCHES)
    def test_branch_appears_in_output(
        self, branch: str, demo_output: str
    ) -> None:
        """Verify that decision branch ``branch`` appears in demo stdout.

        Args:
            branch: One of the 6 decision branch names.
            demo_output: Captured stdout from the demo (class fixture).
        """
        assert branch in demo_output, (
            f"Decision branch '{branch}' was not found in demo output.\n"
            f"Full output:\n{demo_output}"
        )

    def test_ambiguous_not_in_output(self, demo_output: str) -> None:
        """Demo output must not contain 'ambiguous' as a decision branch.

        'ambiguous' was removed in v0.9.0 (#202).  If it appears, a demo
        fixture or catalog was not updated.
        """
        # Check for the decision JSON field value, not just the word
        assert '"decision": "ambiguous"' not in demo_output, (
            "Found 'ambiguous' decision in demo output — "
            "remove the ambiguous demo fixture entry (#202)."
        )
