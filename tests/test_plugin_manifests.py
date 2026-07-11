"""Tests for .claude-plugin manifest files.

Validates that plugin.json contains all required fields for Claude Code
plugin distribution.  marketplace.json was removed in favour of the
shared glitchwerks/plugins hub marketplace (Issue #147).
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
# plugin.json — userConfig.shadow_enabled (Issue #457)
# ---------------------------------------------------------------------------


class TestPluginManifestUserConfigShadowEnabled:
    """Tests for the ``userConfig.shadow_enabled`` field (Issue #457).

    The plugin manifest must declare a ``userConfig`` block with a
    ``shadow_enabled`` boolean entry so Claude Code can prompt the user
    to toggle the ``DISPATCH_SHADOW`` gate at plugin-enable time. Per
    the plugin-authoring schema (``userConfig.<key>``), each entry needs
    a ``type``, a non-empty ``title``, and a non-empty ``description``.

    All tests in this class are expected to FAIL until plugin.json is
    updated — no ``userConfig`` block exists yet.
    """

    def test_user_config_block_present(
        self, plugin_manifest: dict[str, Any]
    ) -> None:
        """Verify that plugin.json contains a top-level 'userConfig' block.

        Args:
            plugin_manifest: The parsed plugin.json dict (from fixture).
        """
        assert "userConfig" in plugin_manifest, (
            "plugin.json must contain a 'userConfig' block declaring "
            "'shadow_enabled' (Issue #457)."
        )

    def test_shadow_enabled_key_present(
        self, plugin_manifest: dict[str, Any]
    ) -> None:
        """Verify that 'userConfig' declares a 'shadow_enabled' key.

        Args:
            plugin_manifest: The parsed plugin.json dict (from fixture).
        """
        user_config = plugin_manifest.get("userConfig", {})
        assert "shadow_enabled" in user_config, (
            "plugin.json 'userConfig' must declare a 'shadow_enabled' key."
        )

    def test_shadow_enabled_type_is_boolean(
        self, plugin_manifest: dict[str, Any]
    ) -> None:
        """Verify 'userConfig.shadow_enabled.type' is exactly 'boolean'.

        Args:
            plugin_manifest: The parsed plugin.json dict (from fixture).
        """
        user_config = plugin_manifest.get("userConfig", {})
        shadow_cfg = user_config.get("shadow_enabled", {})
        assert shadow_cfg.get("type") == "boolean", (
            "userConfig.shadow_enabled 'type' must be 'boolean', got "
            f"{shadow_cfg.get('type')!r}."
        )

    def test_shadow_enabled_title_non_empty(
        self, plugin_manifest: dict[str, Any]
    ) -> None:
        """Verify 'userConfig.shadow_enabled.title' is a non-empty string.

        Args:
            plugin_manifest: The parsed plugin.json dict (from fixture).
        """
        user_config = plugin_manifest.get("userConfig", {})
        shadow_cfg = user_config.get("shadow_enabled", {})
        title = shadow_cfg.get("title", "")
        assert isinstance(title, str) and title.strip(), (
            "userConfig.shadow_enabled 'title' must be a non-empty string."
        )

    def test_shadow_enabled_description_non_empty(
        self, plugin_manifest: dict[str, Any]
    ) -> None:
        """Verify 'userConfig.shadow_enabled.description' is non-empty.

        Args:
            plugin_manifest: The parsed plugin.json dict (from fixture).
        """
        user_config = plugin_manifest.get("userConfig", {})
        shadow_cfg = user_config.get("shadow_enabled", {})
        description = shadow_cfg.get("description", "")
        assert isinstance(description, str) and description.strip(), (
            "userConfig.shadow_enabled 'description' must be a non-empty "
            "string."
        )


# ---------------------------------------------------------------------------
# Validation helpers — unit tests with synthetic manifests
# ---------------------------------------------------------------------------


def _validate_plugin_entries(plugins: list[dict[str, Any]]) -> list[str]:
    """Validate a list of plugin entries and return a list of error messages.

    This mirrors the checks performed by the CI manifest-validation job,
    allowing the rules to be exercised against synthetic inputs without
    touching the real marketplace.json.

    Validation rules applied:
    - Every entry must have a ``name`` field (non-empty string).
    - Every entry must have a ``source`` field.
    - The ``path`` field is forbidden (use ``source`` instead).
    - If ``source`` is a string it must start with ``./``.
    - If ``source`` is an object it must contain a ``source`` discriminator
      whose value is one of ``github``, ``url``, ``git-subdir``, or ``npm``.

    Args:
        plugins: A list of plugin-entry dicts to validate.

    Returns:
        A list of human-readable error strings, one per violation.
        An empty list means all entries are valid.
    """
    _VALID_DISCRIMINATORS: frozenset[str] = frozenset(
        {"github", "url", "git-subdir", "npm"}
    )
    errors: list[str] = []

    for i, entry in enumerate(plugins):
        label = f"plugins[{i}]"
        name = entry.get("name", "")
        if not (isinstance(name, str) and name.strip()):
            errors.append(
                f"{label}: 'name' must be a non-empty string, "
                f"got {name!r}."
            )
        if "path" in entry:
            errors.append(
                f"{label} (name={name!r}): 'path' is not a valid field. "
                "Use 'source' instead."
            )
        if "source" not in entry:
            errors.append(
                f"{label} (name={name!r}): missing required 'source' field."
            )
        else:
            src = entry["source"]
            if isinstance(src, str):
                if not src.startswith("./"):
                    errors.append(
                        f"{label} (name={name!r}): string 'source' must "
                        f"start with './', got {src!r}."
                    )
            elif isinstance(src, dict):
                discriminator = src.get("source")
                if discriminator not in _VALID_DISCRIMINATORS:
                    errors.append(
                        f"{label} (name={name!r}): object 'source' must "
                        f"have a 'source' key in "
                        f"{sorted(_VALID_DISCRIMINATORS)}, "
                        f"got {discriminator!r}."
                    )
            else:
                errors.append(
                    f"{label} (name={name!r}): 'source' must be a string "
                    f"or object, got {type(src).__name__}."
                )

    return errors


class TestValidatePluginEntries:
    """Unit tests for the _validate_plugin_entries helper.

    Each test exercises the validation rules in isolation using
    synthetic plugin-entry dicts so that failures are deterministic
    and independent of the actual marketplace.json on disk.
    """

    def test_valid_relative_path_source_passes(self) -> None:
        """A well-formed entry with a relative-path source has no errors."""
        entries = [{"name": "my-plugin", "source": "./plugins/my-plugin"}]
        assert _validate_plugin_entries(entries) == []

    def test_valid_dot_slash_root_source_passes(self) -> None:
        """'source': './' (marketplace root) is a valid relative path."""
        entries = [{"name": "my-plugin", "source": "./"}]
        assert _validate_plugin_entries(entries) == []

    def test_valid_github_object_source_passes(self) -> None:
        """A well-formed github object source has no errors."""
        entries = [
            {
                "name": "my-plugin",
                "source": {"source": "github", "repo": "owner/repo"},
            }
        ]
        assert _validate_plugin_entries(entries) == []

    def test_valid_npm_object_source_passes(self) -> None:
        """A well-formed npm object source has no errors."""
        entries = [
            {
                "name": "my-plugin",
                "source": {"source": "npm", "package": "@scope/pkg"},
            }
        ]
        assert _validate_plugin_entries(entries) == []

    def test_path_field_rejected(self) -> None:
        """An entry using 'path' instead of 'source' produces an error.

        This is the exact regression guard for the bug in Issue #13:
        marketplace.json shipped with 'path': './' instead of
        'source': './', causing '/plugin install' to fail with
        'unsupported source type'.
        """
        entries = [{"name": "claude-wayfinder", "path": "./"}]
        errors = _validate_plugin_entries(entries)
        assert any("'path' is not a valid field" in e for e in errors), (
            f"Expected an error about 'path', got: {errors}"
        )
        assert any("missing required 'source'" in e for e in errors), (
            f"Expected a missing-source error, got: {errors}"
        )

    def test_missing_source_field_rejected(self) -> None:
        """An entry without any 'source' field produces an error."""
        entries = [{"name": "my-plugin"}]
        errors = _validate_plugin_entries(entries)
        assert any("missing required 'source'" in e for e in errors), (
            f"Expected missing-source error, got: {errors}"
        )

    def test_string_source_without_dot_slash_rejected(self) -> None:
        """A string source not starting with './' produces an error."""
        entries = [{"name": "my-plugin", "source": "plugins/my-plugin"}]
        errors = _validate_plugin_entries(entries)
        assert any("start with './'" in e for e in errors), (
            f"Expected dot-slash error, got: {errors}"
        )

    def test_missing_name_field_rejected(self) -> None:
        """An entry without a 'name' field produces an error."""
        entries = [{"source": "./plugins/my-plugin"}]
        errors = _validate_plugin_entries(entries)
        assert any("'name' must be a non-empty string" in e for e in errors), (
            f"Expected name error, got: {errors}"
        )

    def test_object_source_with_unknown_discriminator_rejected(self) -> None:
        """An object source with an unknown discriminator produces an error."""
        entries = [
            {"name": "my-plugin", "source": {"source": "s3", "bucket": "x"}}
        ]
        errors = _validate_plugin_entries(entries)
        assert any("'source' key in" in e for e in errors), (
            f"Expected discriminator error, got: {errors}"
        )

    def test_empty_list_passes(self) -> None:
        """An empty plugin list is valid (no entries to validate)."""
        assert _validate_plugin_entries([]) == []

    def test_multiple_entries_all_valid(self) -> None:
        """Multiple valid entries produce no errors."""
        entries = [
            {"name": "plugin-a", "source": "./a"},
            {
                "name": "plugin-b",
                "source": {"source": "github", "repo": "o/r"},
            },
        ]
        assert _validate_plugin_entries(entries) == []

    def test_multiple_entries_one_invalid_reports_that_entry(self) -> None:
        """Only the invalid entry in a mixed list is reported."""
        entries = [
            {"name": "plugin-a", "source": "./a"},
            {"name": "plugin-b", "path": "./b"},
        ]
        errors = _validate_plugin_entries(entries)
        assert len(errors) >= 1
        assert all("plugin-b" in e for e in errors), (
            f"Expected errors to name 'plugin-b', got: {errors}"
        )
