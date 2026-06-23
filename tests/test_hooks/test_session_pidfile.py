"""Tests for the shared hooks/_session_pidfile.py module (Phase-2 contract).

Phase 2 will create ``hooks/_session_pidfile.py`` exporting:

* ``_get_home``          – resolves HOME / USERPROFILE to a Path
* ``_iter_ancestors``    – yields (pid, name, create_time_int) nearest-first
* ``_select_target_pid`` – picks the nearest claude-named ancestor

These tests assert that the module is independently importable and that its
exports exist with the correct names and basic behaviour.  The full selection-
logic coverage already lives in the frozen nine-test suite at
``tests/test_hooks/test_session_start_record_session.py``; this file adds
only the minimum assertions needed to validate the shared module itself.
"""

from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_pidfile_module():
    """Import ``hooks/_session_pidfile.py`` by file path.

    Returns:
        The loaded module object.

    Raises:
        AssertionError: If the module file cannot be found or loaded.
    """
    import importlib.util

    module_path = (
        Path(__file__).parent.parent.parent / "hooks" / "_session_pidfile.py"
    )
    spec = importlib.util.spec_from_file_location("_session_pidfile", module_path)
    assert spec is not None, (
        f"Could not load shared module at {module_path} — "
        "Phase 2 must create hooks/_session_pidfile.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


# ---------------------------------------------------------------------------
# B1 — module exists and exports the three required names
# ---------------------------------------------------------------------------


class TestSharedModuleExports:
    """The shared module must be importable and export the three public names."""

    def test_module_exports_get_home(self) -> None:
        """``_get_home`` is exported from ``hooks/_session_pidfile.py``.

        Raises:
            AssertionError: If the attribute is missing.
        """
        mod = _load_pidfile_module()
        assert hasattr(mod, "_get_home"), (
            "hooks/_session_pidfile.py must export _get_home"
        )
        assert callable(mod._get_home), "_get_home must be callable"

    def test_module_exports_iter_ancestors(self) -> None:
        """``_iter_ancestors`` is exported from ``hooks/_session_pidfile.py``.

        Raises:
            AssertionError: If the attribute is missing.
        """
        mod = _load_pidfile_module()
        assert hasattr(mod, "_iter_ancestors"), (
            "hooks/_session_pidfile.py must export _iter_ancestors"
        )
        assert callable(mod._iter_ancestors), "_iter_ancestors must be callable"

    def test_module_exports_select_target_pid(self) -> None:
        """``_select_target_pid`` is exported from ``hooks/_session_pidfile.py``.

        Raises:
            AssertionError: If the attribute is missing.
        """
        mod = _load_pidfile_module()
        assert hasattr(mod, "_select_target_pid"), (
            "hooks/_session_pidfile.py must export _select_target_pid"
        )
        assert callable(mod._select_target_pid), "_select_target_pid must be callable"


# ---------------------------------------------------------------------------
# B2 — _select_target_pid picks the nearest claude entry (one representative)
# ---------------------------------------------------------------------------


class TestSelectTargetPidBehaviour:
    """``_select_target_pid`` returns the nearest claude-named ancestor."""

    def test_picks_nearest_claude_ancestor(self) -> None:
        """Nearest ``claude.exe`` ancestor is returned from the shared module.

        Chain (nearest-first):
          (111, "node.exe",   900)  -- non-CC, skipped
          (222, "claude.exe", 850)  -- nearest CC → must be selected
          (333, "claude.exe", 800)  -- farther CC → must NOT be selected

        Expected return: ``(222, 850)``.
        """
        mod = _load_pidfile_module()

        chain = [
            (111, "node.exe", 900),
            (222, "claude.exe", 850),
            (333, "claude.exe", 800),
        ]

        result = mod._select_target_pid(iter(chain))

        assert result == (222, 850), (
            f"_select_target_pid must return (222, 850) for nearest CC ancestor; "
            f"got {result!r}"
        )
