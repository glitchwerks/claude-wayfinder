"""Tests for claude_wayfinder public API surface (v0.1).

Regression guard: every name declared in ``__all__`` must be importable
from the top-level package.  If a future refactor renames or removes a
public symbol without updating ``__init__.py``, these tests catch it.

Test categories:
  1. ``__all__`` completeness — every name resolves to a real object
  2. Callable / type contracts — spot-check that exports have the right kind
  3. Internal symbols are NOT re-exported
"""

from __future__ import annotations

import inspect

import pytest

# ---------------------------------------------------------------------------
# 1. __all__ completeness
# ---------------------------------------------------------------------------


def test_all_is_defined() -> None:
    """``claude_wayfinder.__all__`` must exist and be a non-empty sequence."""
    import claude_wayfinder

    assert hasattr(claude_wayfinder, "__all__"), (
        "claude_wayfinder.__all__ is not defined"
    )
    assert len(claude_wayfinder.__all__) > 0, (
        "claude_wayfinder.__all__ must not be empty"
    )


def test_all_names_are_importable() -> None:
    """Every name in ``__all__`` must be importable from the package root."""
    import claude_wayfinder

    missing: list[str] = []
    for name in claude_wayfinder.__all__:
        if not hasattr(claude_wayfinder, name):
            missing.append(name)

    assert not missing, (
        f"Names declared in __all__ but not importable: {missing}"
    )


@pytest.mark.parametrize(
    "name",
    [
        "load_catalog",
        "build_features",
        "score",
        "decide",
        "VALID_DECISIONS",
        "CatalogEntry",
        "Features",
        "ScoredEntry",
        "Keyword",
        "Triggers",
    ],
)
def test_expected_public_names_present(name: str) -> None:
    """Each v0.1 public symbol must be importable from ``claude_wayfinder``."""
    import claude_wayfinder

    assert hasattr(claude_wayfinder, name), (
        f"Expected public symbol '{name}' not found in claude_wayfinder"
    )
    assert name in claude_wayfinder.__all__, (
        f"Symbol '{name}' exists but is not listed in __all__"
    )


def test_build_catalog_accessible_via_submodule() -> None:
    """``build_catalog`` function is public via its submodule import path.

    The function cannot be re-exported at the ``claude_wayfinder`` package
    level because the name ``build_catalog`` there refers to the submodule.
    The canonical public path is ``from claude_wayfinder.build_catalog import
    build_catalog``.
    """
    from claude_wayfinder.build_catalog import build_catalog

    assert callable(build_catalog)


# ---------------------------------------------------------------------------
# 2. Callable / type contracts
# ---------------------------------------------------------------------------


def test_load_catalog_is_callable() -> None:
    """``load_catalog`` must be a callable function."""
    from claude_wayfinder import load_catalog

    assert callable(load_catalog)


def test_build_features_is_callable() -> None:
    """``build_features`` must be a callable function."""
    from claude_wayfinder import build_features

    assert callable(build_features)


def test_score_is_callable() -> None:
    """``score`` must be a callable function."""
    from claude_wayfinder import score

    assert callable(score)


def test_decide_is_callable() -> None:
    """``decide`` must be a callable function."""
    from claude_wayfinder import decide

    assert callable(decide)


def test_build_catalog_is_callable() -> None:
    """``build_catalog`` function is callable via its submodule import path."""
    from claude_wayfinder.build_catalog import build_catalog

    assert callable(build_catalog)


def test_valid_decisions_is_frozenset() -> None:
    """``VALID_DECISIONS`` must be a frozenset of strings."""
    from claude_wayfinder import VALID_DECISIONS

    assert isinstance(VALID_DECISIONS, frozenset)
    assert all(isinstance(d, str) for d in VALID_DECISIONS)


def test_valid_decisions_contains_seven_values() -> None:
    """``VALID_DECISIONS`` must enumerate exactly the 7 v0.10.0 routing decisions.

    'ambiguous' was removed in v0.9.0 (#202): tie scenarios now emit
    'advisory' with the top-scored agent named and alternatives populated.
    'mixed_content' was added in v0.10.0 (#210): structural two-handed tasks
    where >= 2 agents clamp at 1.0 on path-disjoint lanes.
    """
    from claude_wayfinder import VALID_DECISIONS

    expected = {
        "delegate",
        "self_handle",
        "self_handle_unaided",
        "advisory",
        "ask_user",
        "needs_more_detail",
        "mixed_content",
    }
    assert VALID_DECISIONS == expected
    assert "ambiguous" not in VALID_DECISIONS, (
        "'ambiguous' was removed in v0.9.0 — it must not reappear in "
        "VALID_DECISIONS"
    )
    assert "mixed_content" in VALID_DECISIONS, (
        "'mixed_content' was added in v0.10.0 (#210)"
    )


def test_dataclasses_are_classes() -> None:
    """Exported dataclasses must be class objects (not instances)."""
    from claude_wayfinder import (
        CatalogEntry,
        Features,
        Keyword,
        ScoredEntry,
        Triggers,
    )

    for cls in (CatalogEntry, Features, Keyword, ScoredEntry, Triggers):
        assert inspect.isclass(cls), f"{cls!r} should be a class"


# ---------------------------------------------------------------------------
# 3. Internal symbols are NOT re-exported
# ---------------------------------------------------------------------------


def test_is_agent_routable_not_in_all() -> None:
    """``is_agent_routable`` is internal in v0.1 and must not appear in __all__."""
    import claude_wayfinder

    assert "is_agent_routable" not in claude_wayfinder.__all__, (
        "is_agent_routable must remain internal (not in __all__)"
    )


def test_health_module_symbols_not_in_all() -> None:
    """No symbol from the internal ``_health`` module should appear in __all__."""
    import claude_wayfinder
    import claude_wayfinder._health as _health_mod

    health_names = {
        name
        for name in dir(_health_mod)
        if not name.startswith("_")
    }
    leaked = health_names & set(claude_wayfinder.__all__)
    assert not leaked, (
        f"Internal _health symbols leaked into __all__: {leaked}"
    )


def test_star_import_does_not_expose_internal_symbols() -> None:
    """A ``from claude_wayfinder import *`` must not expose internal names."""
    # Re-import into a clean namespace to simulate star-import.
    ns: dict[str, object] = {}
    import claude_wayfinder

    for name in claude_wayfinder.__all__:
        ns[name] = getattr(claude_wayfinder, name)

    assert "is_agent_routable" not in ns
    # _health itself should not be in the star-import namespace
    assert "_health" not in ns
