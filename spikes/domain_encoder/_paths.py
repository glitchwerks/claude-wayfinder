"""Path / repository-id helpers for the domain-encoder spike.

Kept in a model2vec-free module so they are importable (and testable) in CI
environments that install only ``.[dev]`` (no ``.[spike]`` extra).
"""

from __future__ import annotations

import os


def _is_hf_repo_id(s: str) -> bool:
    """Return True when *s* looks like a HuggingFace Hub repo id.

    A HF repo id has the shape ``"<owner>/<name>"`` — exactly one forward
    slash, no backslash, no leading ``.``, and is not an existing filesystem
    path.  The logic is explicit and platform-independent: the separator
    character is never read from ``os`` (which differs between POSIX and
    Windows and would make ``"minishlab/potion-base-8M"`` invisible as a
    repo id on Linux/Mac).

    Args:
        s: The string to test — a model name or path argument as received
            by ``DomainClassifier.from_pretrained``.

    Returns:
        ``True`` if *s* matches the ``"<owner>/<name>"`` repo-id shape and
        is **not** an existing filesystem path.  ``False`` for local paths,
        absolute paths, relative dot-paths, backslash paths, bare names, and
        anything that ``os.path.exists`` resolves.

    Examples::

        >>> _is_hf_repo_id("minishlab/potion-base-8M")
        True
        >>> _is_hf_repo_id("C:/models/foo")
        False
        >>> _is_hf_repo_id("./local/model")
        False
    """
    if not s:
        return False

    # Backslash → Windows path.
    if "\\" in s:
        return False

    # Leading dot → relative path reference (".", "..", "./foo", "../bar").
    if s.startswith("."):
        return False

    # Leading slash → absolute POSIX path.
    if s.startswith("/"):
        return False

    # Count forward slashes; a valid repo id has exactly one.
    slash_count = s.count("/")
    if slash_count != 1:
        return False

    # Verify neither segment is empty (e.g. "owner/" or "/name").
    owner, name = s.split("/", 1)
    if not owner or not name:
        return False

    # Final guard: if the path exists on the local filesystem treat it as a
    # local directory even if it coincidentally looks like "org/name".
    if os.path.exists(s):
        return False

    return True
