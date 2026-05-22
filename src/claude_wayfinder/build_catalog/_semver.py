"""Semver helpers for Pass 2.6 builtin-agent version pinning.

Provides utilities for parsing Claude Code version strings and comparing
them against the ``min_claude_version`` / ``max_claude_version`` fields
declared in builtin-agent sidecars.

This module has no dependencies on other ``build_catalog`` submodules.
"""

from __future__ import annotations

import subprocess

from claude_wayfinder.build_catalog._validate import ValidationIssue

# Reserved sub-directory name inside the triggers root that holds
# builtin-agent sidecars.  Skill-override walkers skip this directory;
# Pass 2.6 targets it exclusively.
_BUILTIN_AGENTS_SUBDIR: str = "builtin"


def _parse_semver(version_str: str) -> tuple[int, ...]:
    """Parse a semver string into a tuple of integers for comparison.

    Accepts formats like ``"2.1"``, ``"2.1.138"``, ``"3.0.0"``.  Each
    dot-separated component is coerced to ``int``.  Non-numeric suffixes
    (pre-release labels etc.) are not supported — the sidecar schema
    restricts values to plain numeric dotted strings.

    Args:
        version_str: Semver string, e.g. ``"2.1.138"``.

    Returns:
        Tuple of ints, e.g. ``(2, 1, 138)`` or ``(2, 1)`` for ``"2.1"``.

    Raises:
        ValueError: If any component is not a non-negative integer.
    """
    parts = version_str.strip().split(".")
    result: list[int] = []
    for part in parts:
        if not part.isdigit():
            raise ValueError(
                f"semver component {part!r} in {version_str!r} is not a"
                " non-negative integer"
            )
        result.append(int(part))
    return tuple(result)


def _read_claude_version(
    issues_sink: list[ValidationIssue],
) -> str | None:
    """Return the running Claude Code version string, or ``None`` on failure.

    Resolution order:

    1. Shell out to ``claude --version`` and parse the first token before
       any space (e.g. ``"2.1.138 (Claude Code)"`` → ``"2.1.138"``).
    2. Fall back to the ``CLAUDE_VERSION`` environment variable.
    3. If neither succeeds, append a fatal ``ValidationIssue`` to
       *issues_sink* and return ``None``.

    Args:
        issues_sink: Mutable list to which a fatal issue is appended
            when the version cannot be determined.

    Returns:
        Version string (e.g. ``"2.1.138"``), or ``None`` when
        unresolvable.
    """
    import os

    # Attempt 1: claude --version
    try:
        result = subprocess.run(
            ["claude", "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            # Format: "2.1.138 (Claude Code)" — take the first token.
            raw = result.stdout.strip().split()[0]
            return raw
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        pass

    # Attempt 2: CLAUDE_VERSION env var
    env_version = os.environ.get("CLAUDE_VERSION", "").strip()
    if env_version:
        return env_version

    # Neither source available — warn and exclude builtin entries.
    # This is an environment problem (CI runner, fresh install) rather than
    # an authoring error, so we demote to warning so the catalog build can
    # still complete.  The version-pin discipline is preserved because all
    # builtin entries are excluded when the version is unknown.
    issues_sink.append(
        ValidationIssue(
            "warning",
            "<builtin>",
            "cannot determine running Claude Code version (claude --version"
            " failed and CLAUDE_VERSION not set); builtin entries excluded",
        )
    )
    return None
