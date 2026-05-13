"""Tests for .claude-plugin manifest files.

Validates that plugin.json and marketplace.json (when present) contain all
required fields for Claude Code plugin distribution.

Missing fields that are tracked for completion in later issues are marked
``xfail`` so the suite stays green while flagging the gap clearly.

Issue references:
    - Issue #13: marketplace.json
    - Issue #14: version, license, homepage, repository fields in plugin.json
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT: Path = Path(__file__).resolve().parents[1]
_PLUGIN_JSON: Path = _REPO_ROOT / ".claude-plugin" / "plugin.json"
_MARKETPLACE_JSON: Path = _REPO_ROOT / ".claude-plugin" / "marketplace.json"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def plugin_manifest() -> dict[str, Any]:
    """Load and return the parsed plugin.json manifest.

    Returns:
        A dict containing the parsed JSON content of plugin.json.

    Raises:
        FileNotFoundError: If .claude-plugin/plugin.json does not exist.
        json.JSONDecodeError: If the file is not valid JSON.
    """
    return json.loads(_PLUGIN_JSON.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# plugin.json — always-required fields
# ---------------------------------------------------------------------------


class TestPluginManifestRequiredFields:
    """Tests for always-required fields in .claude-plugin/plugin.json."""

    def test_plugin_json_exists(self) -> None:
        """Verify that .claude-plugin/plugin.json is present in the repo."""
        assert _PLUGIN_JSON.exists(), (
            f"Expected {_PLUGIN_JSON} to exist. "
            "Create .claude-plugin/plugin.json with the plugin metadata."
        )

    def test_name_field_present(self, plugin_manifest: dict[str, Any]) -> None:
        """Verify that the 'name' field is present in plugin.json.

        Args:
            plugin_manifest: The parsed plugin.json dict (from fixture).
        """
        assert "name" in plugin_manifest, (
            "plugin.json must contain a 'name' field."
        )

    def test_name_field_non_empty(self, plugin_manifest: dict[str, Any]) -> None:
        """Verify that the 'name' field is a non-empty string.

        Args:
            plugin_manifest: The parsed plugin.json dict (from fixture).
        """
        name = plugin_manifest.get("name", "")
        assert isinstance(name, str) and name.strip(), (
            "plugin.json 'name' must be a non-empty string."
        )

    def test_description_field_present(
        self, plugin_manifest: dict[str, Any]
    ) -> None:
        """Verify that the 'description' field is present in plugin.json.

        Args:
            plugin_manifest: The parsed plugin.json dict (from fixture).
        """
        assert "description" in plugin_manifest, (
            "plugin.json must contain a 'description' field."
        )

    def test_description_field_non_empty(
        self, plugin_manifest: dict[str, Any]
    ) -> None:
        """Verify that the 'description' field is a non-empty string.

        Args:
            plugin_manifest: The parsed plugin.json dict (from fixture).
        """
        desc = plugin_manifest.get("description", "")
        assert isinstance(desc, str) and desc.strip(), (
            "plugin.json 'description' must be a non-empty string."
        )

    def test_author_field_present(self, plugin_manifest: dict[str, Any]) -> None:
        """Verify that the 'author' field is present in plugin.json.

        Args:
            plugin_manifest: The parsed plugin.json dict (from fixture).
        """
        assert "author" in plugin_manifest, (
            "plugin.json must contain an 'author' field."
        )


# ---------------------------------------------------------------------------
# plugin.json — fields pending Issue #14 (xfail)
# ---------------------------------------------------------------------------


class TestPluginManifestPendingFields:
    """Tests for fields not yet populated — tracked by Issue #14.

    All tests in this class are marked xfail.  When Issue #14 is resolved,
    remove the ``@pytest.mark.xfail`` decorators and these tests become
    hard requirements.
    """

    @pytest.mark.xfail(
        reason="version field will be filled in by Issue #14",
        strict=False,
    )
    def test_version_field_present(
        self, plugin_manifest: dict[str, Any]
    ) -> None:
        """Verify that the 'version' field is present in plugin.json.

        Args:
            plugin_manifest: The parsed plugin.json dict (from fixture).
        """
        assert "version" in plugin_manifest, (
            "plugin.json must contain a 'version' field (e.g. '0.1.0')."
        )

    @pytest.mark.xfail(
        reason="license field will be filled in by Issue #14",
        strict=False,
    )
    def test_license_field_present(
        self, plugin_manifest: dict[str, Any]
    ) -> None:
        """Verify that the 'license' field is present in plugin.json.

        Args:
            plugin_manifest: The parsed plugin.json dict (from fixture).
        """
        assert "license" in plugin_manifest, (
            "plugin.json must contain a 'license' field (e.g. 'MIT')."
        )

    @pytest.mark.xfail(
        reason="homepage field will be filled in by Issue #14",
        strict=False,
    )
    def test_homepage_field_present(
        self, plugin_manifest: dict[str, Any]
    ) -> None:
        """Verify that the 'homepage' field is present in plugin.json.

        Args:
            plugin_manifest: The parsed plugin.json dict (from fixture).
        """
        assert "homepage" in plugin_manifest, (
            "plugin.json must contain a 'homepage' field."
        )

    @pytest.mark.xfail(
        reason="repository field will be filled in by Issue #14",
        strict=False,
    )
    def test_repository_field_present(
        self, plugin_manifest: dict[str, Any]
    ) -> None:
        """Verify that the 'repository' field is present in plugin.json.

        Args:
            plugin_manifest: The parsed plugin.json dict (from fixture).
        """
        assert "repository" in plugin_manifest, (
            "plugin.json must contain a 'repository' field."
        )


# ---------------------------------------------------------------------------
# marketplace.json — pending Issue #13 (xfail)
# ---------------------------------------------------------------------------


class TestMarketplaceManifest:
    """Tests for .claude-plugin/marketplace.json.

    marketplace.json is not yet created (tracked by Issue #13).  All tests
    here are marked xfail.  When Issue #13 is resolved, remove the
    ``@pytest.mark.xfail`` decorators.
    """

    @pytest.mark.xfail(
        reason="marketplace.json will be created by Issue #13",
        strict=False,
    )
    def test_marketplace_json_exists(self) -> None:
        """Verify that .claude-plugin/marketplace.json is present."""
        assert _MARKETPLACE_JSON.exists(), (
            f"Expected {_MARKETPLACE_JSON} to exist. "
            "Create .claude-plugin/marketplace.json (tracked by Issue #13)."
        )

    @pytest.mark.xfail(
        reason="marketplace.json will be created by Issue #13",
        strict=False,
    )
    def test_marketplace_json_valid_shape(self) -> None:
        """Verify that marketplace.json is valid JSON with a required shape.

        Expects at minimum a 'categories' list and a 'tags' list.
        """
        if not _MARKETPLACE_JSON.exists():
            pytest.skip(
                "marketplace.json does not exist yet (tracked by Issue #13)"
            )
        manifest: dict[str, Any] = json.loads(
            _MARKETPLACE_JSON.read_text(encoding="utf-8")
        )
        assert "categories" in manifest, (
            "marketplace.json must contain a 'categories' list."
        )
        assert isinstance(manifest["categories"], list), (
            "marketplace.json 'categories' must be a list."
        )
        assert "tags" in manifest, (
            "marketplace.json must contain a 'tags' list."
        )
        assert isinstance(manifest["tags"], list), (
            "marketplace.json 'tags' must be a list."
        )
