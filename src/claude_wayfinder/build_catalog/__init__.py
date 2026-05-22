"""Build the dispatch catalog from skill sidecars and agent frontmatter.

Reads trigger configuration from sidecar ``triggers.yml`` files next to
each ``SKILL.md``, from ``triggers/<plugin>/<skill>.yml`` for plugin
overrides, and from inline YAML frontmatter for agent ``.md`` files.
Validates each source against the trigger schema documented in
``docs/design/trigger-schema.md`` (v6) and emits a
deterministically-ordered JSON catalog to
``~/.claude/state/dispatch-catalog.json``.

The v6 sidecar schema supersedes the inline-frontmatter approach used in
v5.  Skills now store trigger config in ``triggers.yml`` sidecars;
plugin-owned skills that cannot be edited use override files at
``~/.claude/triggers/<plugin>/<skill>.yml``.

Project-local merging: when the generator is invoked from inside a git
repository (or ``--project-root`` is supplied explicitly),
``<root>/.claude/skills/**/SKILL.md`` and ``<root>/.claude/agents/*.md``
are scanned and merged into the catalog with ``source="project"``.
Project entries override user-global entries on name collision.

Public surface
--------------
The names below are importable directly from
``claude_wayfinder.build_catalog`` and form the stable public API.

Types:
    ValidationIssue, ValidationResult, Severity

Functions:
    build, build_catalog, validate_entry, write_catalog, write_log
    add_catalog_build_args, run_catalog_build, main
    load_frontmatter, load_trigger_sidecar
    discover_plugin_overrides, discover_plugin_agent_overrides
    discover_colocated_agent_sidecars, discover_builtin_agents
    discover_installed_plugins, discover_plugin_entries
    detect_project_root, detect_exclude_dead_zones
    compute_content_hash, update_revisions_sidecar

Constants:
    ALLOWED_WEIGHTS, TRIGGER_FIELDS, SCHEMA_VERSION
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Cluster 3: I/O, sidecar loading, discovery — imported from _discover
# ---------------------------------------------------------------------------
from claude_wayfinder.build_catalog._discover import (
    _FRONTMATTER_RE as _FRONTMATTER_RE,
)
from claude_wayfinder.build_catalog._discover import (
    _MIN_PLUGIN_MANIFEST_VERSION as _MIN_PLUGIN_MANIFEST_VERSION,
)
from claude_wayfinder.build_catalog._discover import (
    _PLUGINS_MANIFEST_FILENAME as _PLUGINS_MANIFEST_FILENAME,
)
from claude_wayfinder.build_catalog._discover import (
    _resolve_catalog_build_defaults as _resolve_catalog_build_defaults,
)
from claude_wayfinder.build_catalog._discover import (
    compute_content_hash as compute_content_hash,
)
from claude_wayfinder.build_catalog._discover import (
    discover_builtin_agents as discover_builtin_agents,
)
from claude_wayfinder.build_catalog._discover import (
    discover_colocated_agent_sidecars as discover_colocated_agent_sidecars,
)
from claude_wayfinder.build_catalog._discover import (
    discover_installed_plugins as discover_installed_plugins,
)
from claude_wayfinder.build_catalog._discover import (
    discover_plugin_agent_overrides as discover_plugin_agent_overrides,
)
from claude_wayfinder.build_catalog._discover import (
    discover_plugin_entries as discover_plugin_entries,
)
from claude_wayfinder.build_catalog._discover import (
    discover_plugin_overrides as discover_plugin_overrides,
)
from claude_wayfinder.build_catalog._discover import (
    load_frontmatter as load_frontmatter,
)
from claude_wayfinder.build_catalog._discover import (
    load_trigger_sidecar as load_trigger_sidecar,
)
from claude_wayfinder.build_catalog._discover import (
    update_revisions_sidecar as update_revisions_sidecar,
)

# ---------------------------------------------------------------------------
# Cluster 5: Orchestration + CLI — imported from _main submodule
# ---------------------------------------------------------------------------
from claude_wayfinder.build_catalog._main import (
    SCHEMA_VERSION as SCHEMA_VERSION,
)
from claude_wayfinder.build_catalog._main import (
    _sort_entry_lists as _sort_entry_lists,
)
from claude_wayfinder.build_catalog._main import (
    add_catalog_build_args as add_catalog_build_args,
)
from claude_wayfinder.build_catalog._main import (
    build as build,
)
from claude_wayfinder.build_catalog._main import (
    build_catalog as build_catalog,
)
from claude_wayfinder.build_catalog._main import (
    detect_project_root as detect_project_root,
)
from claude_wayfinder.build_catalog._main import (
    main as main,
)
from claude_wayfinder.build_catalog._main import (
    run_catalog_build as run_catalog_build,
)
from claude_wayfinder.build_catalog._main import (
    write_catalog as write_catalog,
)
from claude_wayfinder.build_catalog._main import (
    write_log as write_log,
)

# ---------------------------------------------------------------------------
# Cluster 4: Entry processing — imported from _process submodule
# ---------------------------------------------------------------------------
from claude_wayfinder.build_catalog._process import (
    _PLUGIN_NAME_RE as _PLUGIN_NAME_RE,
)
from claude_wayfinder.build_catalog._process import (
    _apply_colocated_sidecars as _apply_colocated_sidecars,
)
from claude_wayfinder.build_catalog._process import (
    _check_skill_md_for_v5_leftovers as _check_skill_md_for_v5_leftovers,
)
from claude_wayfinder.build_catalog._process import (
    _is_plugin_namespaced as _is_plugin_namespaced,
)
from claude_wayfinder.build_catalog._process import (
    _process_builtin_sidecar as _process_builtin_sidecar,
)
from claude_wayfinder.build_catalog._process import (
    _process_file as _process_file,
)
from claude_wayfinder.build_catalog._process import (
    _process_plugin_file as _process_plugin_file,
)
from claude_wayfinder.build_catalog._process import (
    _process_plugin_override as _process_plugin_override,
)
from claude_wayfinder.build_catalog._process import (
    _process_skill_file as _process_skill_file,
)
from claude_wayfinder.build_catalog._process import (
    _resolve_applicable_references as _resolve_applicable_references,
)
from claude_wayfinder.build_catalog._process import (
    detect_exclude_dead_zones as detect_exclude_dead_zones,
)

# ---------------------------------------------------------------------------
# Cluster 2: Semver helpers — imported from _semver submodule
# ---------------------------------------------------------------------------
from claude_wayfinder.build_catalog._semver import (
    _BUILTIN_AGENTS_SUBDIR as _BUILTIN_AGENTS_SUBDIR,
)
from claude_wayfinder.build_catalog._semver import (
    _parse_semver as _parse_semver,
)
from claude_wayfinder.build_catalog._semver import (
    _read_claude_version as _read_claude_version,
)
from claude_wayfinder.build_catalog._validate import (
    _DEPRECATED_FILE_EXTENSIONS as _DEPRECATED_FILE_EXTENSIONS,
)
from claude_wayfinder.build_catalog._validate import (
    _V5_SIDECAR_KEYS as _V5_SIDECAR_KEYS,
)

# ---------------------------------------------------------------------------
# Cluster 1: Trigger validation — imported from _validate submodule
# ---------------------------------------------------------------------------
from claude_wayfinder.build_catalog._validate import (
    ALLOWED_WEIGHTS as ALLOWED_WEIGHTS,
)
from claude_wayfinder.build_catalog._validate import (
    TRIGGER_FIELDS as TRIGGER_FIELDS,
)
from claude_wayfinder.build_catalog._validate import (
    Severity as Severity,
)
from claude_wayfinder.build_catalog._validate import (
    ValidationIssue as ValidationIssue,
)
from claude_wayfinder.build_catalog._validate import (
    ValidationResult as ValidationResult,
)
from claude_wayfinder.build_catalog._validate import (
    _blank_entry as _blank_entry,
)
from claude_wayfinder.build_catalog._validate import (
    _clamp_weight as _clamp_weight,
)
from claude_wayfinder.build_catalog._validate import (
    _validate_keyword_groups as _validate_keyword_groups,
)
from claude_wayfinder.build_catalog._validate import (
    _validate_keywords as _validate_keywords,
)
from claude_wayfinder.build_catalog._validate import (
    validate_entry as validate_entry,
)
