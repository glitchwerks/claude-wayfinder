"""Tests for .claude-plugin manifest files.

Validates that plugin.json and marketplace.json contain all required
fields for Claude Code plugin distribution.

Completed by Issue #13 (marketplace.json + all plugin.json fields).
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


@pytest.fixture(scope="module")
def marketplace_manifest() -> dict[str, Any]:
    """Load and return the parsed marketplace.json manifest.

    Returns:
        A dict containing the parsed JSON content of marketplace.json.

    Raises:
        FileNotFoundError: If .claude-plugin/marketplace.json does not exist.
        json.JSONDecodeError: If the file is not valid JSON.
    """
    return json.loads(_MARKETPLACE_JSON.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# plugin.json — required fields
# ---------------------------------------------------------------------------


class TestPluginManifestRequiredFields:
    """Tests for all required fields in .claude-plugin/plugin.json."""

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

    def test_version_matches_pyproject(
        self, plugin_manifest: dict[str, Any]
    ) -> None:
        """Verify plugin.json version matches pyproject.toml version.

        The two version sources must stay in sync so that the plugin
        version reflects the installed package version.

        Args:
            plugin_manifest: The parsed plugin.json dict (from fixture).
        """
        pyproject_path = _REPO_ROOT / "pyproject.toml"
        pyproject_text = pyproject_path.read_text(encoding="utf-8")
        # Parse version line: version = "X.Y.Z"
        pyproject_version: str | None = None
        for line in pyproject_text.splitlines():
            stripped = line.strip()
            if stripped.startswith("version") and "=" in stripped:
                # Only the [project] section version — skip [tool.*] blocks.
                raw = stripped.split("=", 1)[1].strip().strip('"').strip("'")
                pyproject_version = raw
                break
        assert pyproject_version is not None, (
            "Could not parse version from pyproject.toml."
        )
        plugin_version = plugin_manifest.get("version")
        assert plugin_version == pyproject_version, (
            f"plugin.json version '{plugin_version}' does not match "
            f"pyproject.toml version '{pyproject_version}'. "
            "Keep both in sync."
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
# marketplace.json — required fields
# ---------------------------------------------------------------------------


class TestMarketplaceManifest:
    """Tests for .claude-plugin/marketplace.json.

    marketplace.json ships the marketplace-of-one self-listing for the
    plugin sideload path:

        /plugin marketplace add glitchwerks/claude-wayfinder
    """

    def test_marketplace_json_exists(self) -> None:
        """Verify that .claude-plugin/marketplace.json is present."""
        assert _MARKETPLACE_JSON.exists(), (
            f"Expected {_MARKETPLACE_JSON} to exist."
        )

    def test_marketplace_json_valid_json(self) -> None:
        """Verify that marketplace.json is valid JSON."""
        text = _MARKETPLACE_JSON.read_text(encoding="utf-8")
        manifest = json.loads(text)  # raises on invalid JSON
        assert isinstance(manifest, dict), (
            "marketplace.json must be a JSON object."
        )

    def test_name_field_present(
        self, marketplace_manifest: dict[str, Any]
    ) -> None:
        """Verify that the 'name' field is present in marketplace.json.

        Args:
            marketplace_manifest: The parsed marketplace.json dict.
        """
        assert "name" in marketplace_manifest, (
            "marketplace.json must contain a 'name' field."
        )

    def test_owner_field_present(
        self, marketplace_manifest: dict[str, Any]
    ) -> None:
        """Verify that the 'owner' field is present in marketplace.json.

        Args:
            marketplace_manifest: The parsed marketplace.json dict.
        """
        assert "owner" in marketplace_manifest, (
            "marketplace.json must contain an 'owner' field."
        )

    def test_plugins_field_present_and_non_empty(
        self, marketplace_manifest: dict[str, Any]
    ) -> None:
        """Verify that 'plugins' is a non-empty list in marketplace.json.

        Args:
            marketplace_manifest: The parsed marketplace.json dict.
        """
        assert "plugins" in marketplace_manifest, (
            "marketplace.json must contain a 'plugins' field."
        )
        plugins = marketplace_manifest["plugins"]
        assert isinstance(plugins, list) and len(plugins) > 0, (
            "marketplace.json 'plugins' must be a non-empty list."
        )
