"""I/O, sidecar loading, and file discovery for the catalog builder.

Provides all filesystem-walking and YAML-loading functions used by the
catalog build passes.  Also provides ``_resolve_catalog_build_defaults``,
which resolves the seven optional path arguments from ``${CLAUDE_HOME}``
defaults.

Dependencies: ``_validate.py`` (for ``ValidationIssue`` in signatures).
No circular dependencies with other ``build_catalog`` submodules.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

import yaml

from claude_wayfinder.build_catalog._semver import _BUILTIN_AGENTS_SUBDIR
from claude_wayfinder.build_catalog._validate import ValidationIssue

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)

_logger = logging.getLogger(__name__)

_PLUGINS_MANIFEST_FILENAME: str = "installed_plugins.json"

# Minimum supported manifest schema version.  The spec says accept
# ``version >= 2`` for forward-compatibility with future supersets.
_MIN_PLUGIN_MANIFEST_VERSION: int = 2


# ---------------------------------------------------------------------------
# Content hashing
# ---------------------------------------------------------------------------


def compute_content_hash(path: Path) -> str:
    """Return the SHA-256 of the file's bytes as a 12-character hex prefix.

    12 hex characters = 48 bits of entropy.  With ~30 owned components and
    one rev bump per file edit, the relevant collision space is the set of
    distinct file states observed across the project's lifetime — well below
    2^24 (the birthday-bound for 50% collision risk at 12 hex chars).  The
    truncated hash keeps log entries compact while remaining
    collision-resistant for this domain.

    Args:
        path: Path to the file to hash.

    Returns:
        Lowercase 12-character hex prefix of the SHA-256 digest.
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Revision sidecar
# ---------------------------------------------------------------------------


def update_revisions_sidecar(
    components: list[dict[str, str]],
    sidecar_path: Path,
) -> None:
    """Update the per-component revision sidecar atomically.

    For each component in *components*, look up its prior entry in the
    sidecar (keyed ``"<kind>:<name>"``).  If the stored hash matches the
    current hash, the entry is left unchanged (monotonic — no spurious
    bump).  Otherwise rev is incremented by 1 and the new hash stored.
    Components not previously present are added at ``rev=1``.

    The sidecar file is created when absent.  It is gitignored (lives under
    ``state/``) and stores only the latest ``(rev, content_hash)`` pair per
    component — historical revs are not retained in this v1 design.

    Args:
        components: List of dicts, each with keys ``"name"``, ``"kind"``,
            and ``"content_hash"`` (12-char hex string).
        sidecar_path: Filesystem path to read from and write to.
    """
    if sidecar_path.exists():
        try:
            data: dict[str, Any] = json.loads(sidecar_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {"version": 1, "components": {}}
    else:
        data = {"version": 1, "components": {}}

    if not isinstance(data.get("components"), dict):
        data["components"] = {}

    for comp in components:
        key = f"{comp['kind']}:{comp['name']}"
        prev: dict[str, Any] = data["components"].get(key, {})
        prev_hash: str | None = prev.get("content_hash")
        prev_rev: int = prev.get("rev", 0)
        if prev_hash == comp["content_hash"]:
            continue  # hash unchanged — keep existing rev
        data["components"][key] = {
            "rev": prev_rev + 1,
            "content_hash": comp["content_hash"],
        }

    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    sidecar_path.write_text(
        json.dumps(data, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Frontmatter and sidecar loading
# ---------------------------------------------------------------------------


def load_frontmatter(path: Path) -> dict[str, Any] | None:
    """Extract the YAML frontmatter block from a markdown file.

    Args:
        path: Path to a SKILL.md or agent .md file.

    Returns:
        The parsed YAML mapping, or ``None`` if the file has no
        leading ``---``-fenced block.

    Raises:
        yaml.YAMLError: If the YAML inside the fence is malformed.
            Callers are expected to catch this and emit a fatal
            validation issue.
    """
    text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return None
    parsed = yaml.safe_load(m.group(1))
    if not isinstance(parsed, dict):
        return None
    return parsed


def load_trigger_sidecar(skill_dir: Path) -> dict[str, Any] | None:
    """Load and parse the trigger sidecar file for a skill directory.

    Looks for ``<skill_dir>/triggers.yml``.  Returns the parsed YAML
    mapping on success, or ``None`` when the file is absent, empty,
    or unparseable.  Parse failures are logged as warnings (not raised)
    so the caller can treat the skill as dormant rather than fatal.

    Args:
        skill_dir: Directory containing (or expected to contain)
            a ``triggers.yml`` file alongside the ``SKILL.md``.

    Returns:
        The parsed YAML mapping as a ``dict``, or ``None`` if the file
        is missing, empty, or contains malformed YAML.
    """
    sidecar_path = skill_dir / "triggers.yml"
    if not sidecar_path.exists():
        return None
    text = sidecar_path.read_text(encoding="utf-8").strip()
    if not text:
        return None
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        _logger.warning("YAML parse error in %s: %s", sidecar_path, exc)
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


# ---------------------------------------------------------------------------
# Plugin override discovery
# ---------------------------------------------------------------------------


def discover_plugin_overrides(
    triggers_root: Path,
) -> list[tuple[str, str, dict[str, Any]]]:
    """Walk the plugin-override tree and return sidecar entries.

    Scans ``<triggers_root>/<plugin>/<skill>.yml`` for all ``.yml``
    files exactly two levels deep.  Each file is parsed and returned as
    a tuple ``("skill", "<plugin>:<skill>", parsed_dict)``.  The
    plugin-namespaced name matches the loader's convention, e.g.
    ``superpowers:brainstorming``.

    The reserved sub-directory ``builtin/`` is **skipped** — those
    files are processed exclusively by Pass 2.6 via
    ``discover_builtin_agents`` and must not be treated as
    plugin-override entries.

    Files that fail to parse are silently skipped (callers will see
    them missing from the returned list and log them accordingly).

    Args:
        triggers_root: Root directory of the plugin override tree
            (typically ``~/.claude/triggers/``).

    Returns:
        A list of ``(kind, name, sidecar_dict)`` tuples, one per
        valid ``.yml`` file found.  Empty list when the directory is
        absent or contains no valid files.
    """
    if not triggers_root.is_dir():
        return []
    results: list[tuple[str, str, dict[str, Any]]] = []
    for plugin_dir in sorted(triggers_root.iterdir()):
        if not plugin_dir.is_dir():
            continue
        # Reserved sub-directory: handled by Pass 2.6, not Pass 3.
        if plugin_dir.name == _BUILTIN_AGENTS_SUBDIR:
            continue
        for skill_file in sorted(plugin_dir.glob("*.yml")):
            try:
                text = skill_file.read_text(encoding="utf-8").strip()
                if not text:
                    continue
                parsed = yaml.safe_load(text)
            except yaml.YAMLError as exc:
                _logger.warning("YAML parse error in %s: %s", skill_file, exc)
                continue
            if not isinstance(parsed, dict):
                continue
            plugin_name = plugin_dir.name
            skill_name = skill_file.stem
            entry_name = f"{plugin_name}:{skill_name}"
            results.append(("skill", entry_name, parsed))
    return results


def discover_plugin_agent_overrides(
    triggers_root: Path,
) -> list[tuple[str, str, dict[str, Any]]]:
    """Walk the plugin-agent-override tree and return sidecar entries.

    Scans ``<triggers_root>/<plugin>/agents/<name>.yml`` for all ``.yml``
    files exactly three levels deep (one deeper than skill overrides).
    Each file is parsed and returned as a tuple
    ``("agent", "<plugin>:<name>", parsed_dict)``.  The plugin-namespaced
    name matches the loader's convention, e.g.
    ``superpowers:doc-writer``.

    The reserved sub-directory ``builtin/`` is **skipped** entirely,
    including any ``builtin/agents/`` subtree.  Builtin sidecars are
    processed exclusively by Pass 2.6 via ``discover_builtin_agents``
    and must not be treated as plugin-agent-override entries.

    Unlike ``discover_plugin_overrides``, which can append new entries
    when no matching dormant entry exists, this walker is used for
    strict Mode 2a (match-required) semantics: the application loop
    must emit a warning and drop any sidecar that does not match a
    dormant ``source="plugin"`` agent entry.

    Files that fail to parse are silently skipped.

    Args:
        triggers_root: Root directory of the plugin override tree
            (typically ``~/.claude/triggers/``).

    Returns:
        A list of ``("agent", name, sidecar_dict)`` tuples, one per
        valid ``.yml`` file found.  Empty list when the directory is
        absent or contains no valid ``agents/`` subdirectories.
    """
    if not triggers_root.is_dir():
        return []
    results: list[tuple[str, str, dict[str, Any]]] = []
    for plugin_dir in sorted(triggers_root.iterdir()):
        if not plugin_dir.is_dir():
            continue
        # Reserved sub-directory: handled by Pass 2.6, not Pass 3b.
        # Skip the entire subtree, including any agents/ inside builtin/.
        if plugin_dir.name == _BUILTIN_AGENTS_SUBDIR:
            continue
        agents_subdir = plugin_dir / "agents"
        if not agents_subdir.is_dir():
            continue
        for agent_file in sorted(agents_subdir.glob("*.yml")):
            try:
                text = agent_file.read_text(encoding="utf-8").strip()
                if not text:
                    continue
                parsed = yaml.safe_load(text)
            except yaml.YAMLError as exc:
                _logger.warning(
                    "YAML parse error in %s: %s", agent_file, exc
                )
                continue
            if not isinstance(parsed, dict):
                continue
            plugin_name = plugin_dir.name
            agent_name = agent_file.stem
            entry_name = f"{plugin_name}:{agent_name}"
            results.append(("agent", entry_name, parsed))
    return results


def discover_colocated_agent_sidecars(
    agents_dir: Path,
    issues_sink: list[ValidationIssue] | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    """Walk an agents directory for colocated ``*.triggers.yml`` sidecars.

    Scans ``agents_dir/*.triggers.yml`` (non-recursive, matching the
    ``agents_dir/*.md`` agent discovery pattern) and returns one tuple per
    valid sidecar file.  These sidecars carry dispatch metadata —
    ``triggers:`` and optionally ``applicable_skills:`` — for an agent
    ``.md`` file with the same stem.

    This function only parses the YAML; matching against discovered agent
    entries and applying the sidecar data happens in the caller (Pass 2b
    for owned agents, Pass 4b for project-local agents).

    Files that fail to parse as valid YAML emit a ``_logger.warning``, add
    a ``warning`` :class:`ValidationIssue` to *issues_sink* when supplied,
    and are dropped from the result.  Files that parse to something other
    than a dict are silently skipped (same shape as
    :func:`discover_plugin_agent_overrides`).  Missing or non-directory
    paths return an empty list.

    Args:
        agents_dir: Directory to scan.  Non-recursively globbed for
            ``*.triggers.yml`` files.  Silently ignored if absent or
            not a directory.
        issues_sink: Optional accumulator for :class:`ValidationIssue`
            records.  YAML parse failures append a ``warning`` entry here
            so callers can surface them in the build log.  Pass ``None``
            to suppress issue accumulation (useful for unit-testing the
            walker in isolation).

    Returns:
        A list of ``(stem, sidecar_dict)`` tuples — one per successfully
        parsed ``.triggers.yml`` file.  ``stem`` is the bare filename
        without the ``.triggers.yml`` suffix (e.g. ``"code-writer"`` for
        ``code-writer.triggers.yml``).  Empty list when the directory
        is absent or contains no ``.triggers.yml`` files.
    """
    if not agents_dir.is_dir():
        return []
    results: list[tuple[str, dict[str, Any]]] = []
    for sidecar_file in sorted(agents_dir.glob("*.triggers.yml")):
        # Stem for ``code-writer.triggers.yml`` is ``code-writer``.
        # Path.stem strips only the last suffix, so we strip manually.
        raw_stem = sidecar_file.name
        stem = raw_stem[: raw_stem.index(".triggers.yml")]
        try:
            text = sidecar_file.read_text(encoding="utf-8").strip()
            if not text:
                continue
            parsed = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            _logger.warning(
                "YAML parse error in colocated agent sidecar '%s': %s",
                sidecar_file,
                exc,
            )
            if issues_sink is not None:
                issues_sink.append(
                    ValidationIssue(
                        "warning",
                        stem,
                        f"YAML parse error in colocated agent sidecar"
                        f" '{sidecar_file.name}': {exc}",
                    )
                )
            continue
        if not isinstance(parsed, dict):
            continue
        results.append((stem, parsed))
    return results


# ---------------------------------------------------------------------------
# Builtin-agent discovery
# ---------------------------------------------------------------------------


def discover_builtin_agents(
    builtin_dir: Path,
) -> list[tuple[str, dict[str, Any]]]:
    """Walk the builtin-agents directory and return sidecar entries.

    Scans ``<builtin_dir>/*.yml`` (one level only — no subdirectories).
    Each ``.yml`` file is parsed and returned as a tuple
    ``(stem, parsed_dict)``.

    Files that fail to parse are silently skipped; callers log them.

    Args:
        builtin_dir: Directory containing builtin sidecar ``.yml`` files
            (typically ``~/.claude/triggers/builtin/``).

    Returns:
        List of ``(stem, sidecar_dict)`` tuples, sorted by stem for
        determinism.  Empty list when the directory is absent or contains
        no valid files.
    """
    if not builtin_dir.is_dir():
        return []
    results: list[tuple[str, dict[str, Any]]] = []
    for sidecar_file in sorted(builtin_dir.glob("*.yml")):
        try:
            text = sidecar_file.read_text(encoding="utf-8").strip()
            if not text:
                continue
            parsed = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            _logger.warning("YAML parse error in %s: %s", sidecar_file, exc)
            continue
        if not isinstance(parsed, dict):
            continue
        results.append((sidecar_file.stem, parsed))
    return results


# ---------------------------------------------------------------------------
# Plugin manifest discovery
# ---------------------------------------------------------------------------


def discover_installed_plugins(
    plugins_root: Path,
    issues_sink: list[ValidationIssue],
) -> list[tuple[str, str, Path]]:
    """Discover user-scoped plugins from the Claude plugin manifest.

    Reads ``<plugins_root>/installed_plugins.json`` and returns one tuple
    per valid user-scoped installation.  Results are sorted by plugin key
    for determinism.

    Failure modes (each appends a ``ValidationIssue`` to *issues_sink*):

    * Manifest file absent — ``info``, returns ``[]``.
    * Manifest JSON malformed — ``warning``, returns ``[]``.
    * ``version`` absent or ``< 2`` — ``warning``, returns ``[]``.
    * ``plugins`` key absent — ``warning``, returns ``[]``.
    * Install entry missing ``installPath`` — ``warning``, entry skipped.
    * ``installPath`` does not exist on disk — ``warning``, entry skipped.
    * ``scope != "user"`` — ``info``, skipped silently (no issue appended).

    Args:
        plugins_root: Directory that contains ``installed_plugins.json``.
            Typically ``~/.claude/plugins/``.
        issues_sink: Mutable list to which any ``ValidationIssue`` objects
            are appended.

    Returns:
        List of ``(plugin_name, version, install_path)`` tuples — one per
        valid user-scoped install.  Sorted by plugin name for determinism.
    """
    manifest_path = plugins_root / _PLUGINS_MANIFEST_FILENAME
    if not manifest_path.exists():
        issues_sink.append(
            ValidationIssue(
                "info",
                "<plugins>",
                f"plugin manifest not found at {manifest_path} — no plugins loaded",
            )
        )
        return []

    try:
        data: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        issues_sink.append(
            ValidationIssue(
                "warning",
                "<plugins>",
                f"plugin manifest malformed JSON: {exc}",
            )
        )
        return []

    version = data.get("version")
    if not isinstance(version, int) or version < _MIN_PLUGIN_MANIFEST_VERSION:
        issues_sink.append(
            ValidationIssue(
                "warning",
                "<plugins>",
                f"plugin manifest version {version!r} is not supported "
                f"(require >= {_MIN_PLUGIN_MANIFEST_VERSION})",
            )
        )
        return []

    plugins_map = data.get("plugins")
    if not isinstance(plugins_map, dict):
        issues_sink.append(
            ValidationIssue(
                "warning",
                "<plugins>",
                "'plugins' key missing or not a mapping in manifest",
            )
        )
        return []

    results: list[tuple[str, str, Path]] = []
    for plugin_name, install_entries in sorted(plugins_map.items()):
        if not isinstance(install_entries, list):
            continue
        for entry in install_entries:
            if not isinstance(entry, dict):
                continue
            scope = entry.get("scope")
            if scope != "user":
                # Non-user scopes (e.g. workspace) are silently skipped.
                continue
            raw_path = entry.get("installPath")
            if raw_path is None:
                issues_sink.append(
                    ValidationIssue(
                        "warning",
                        plugin_name,
                        "install entry missing 'installPath' — skipped",
                    )
                )
                continue
            install_path = Path(str(raw_path))
            if not install_path.exists():
                issues_sink.append(
                    ValidationIssue(
                        "warning",
                        plugin_name,
                        f"installPath {install_path} does not exist — skipped",
                    )
                )
                continue
            plugin_version: str = str(entry.get("version", ""))
            results.append((plugin_name, plugin_version, install_path))

    return results


def discover_plugin_entries(
    installs: list[tuple[str, str, Path]],
) -> list[tuple[str, str, Path]]:
    """Enumerate skill and agent files provided by installed plugins.

    For each install tuple in *installs*, globs:

    * ``<installPath>/skills/*/SKILL.md`` for skills.
    * ``<installPath>/agents/*.md`` for agents.

    Args:
        installs: List of ``(plugin_name, version, install_path)`` tuples
            as returned by ``discover_installed_plugins``.

    Returns:
        Sorted list of ``(kind, plugin_name, file_path)`` tuples where
        ``kind`` is ``"skill"`` or ``"agent"``.  Sorted lexicographically
        by ``(kind, plugin_name, str(file_path))`` for determinism.
    """
    results: list[tuple[str, str, Path]] = []
    for plugin_name, _version, install_path in installs:
        for skill_md in install_path.glob("skills/*/SKILL.md"):
            results.append(("skill", plugin_name, skill_md))
        for agent_md in install_path.glob("agents/*.md"):
            results.append(("agent", plugin_name, agent_md))
    return sorted(results, key=lambda t: (t[0], t[1], str(t[2])))


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def _bundled_builtin_agents_dir() -> Path:
    """Return the path to the in-package builtin-agent sidecar fixtures.

    The bundled fixtures live at ``claude_wayfinder/fixtures/builtin/``
    inside the installed package.  They provide Explore.yml and Plan.yml
    as a zero-configuration default so a fresh install immediately includes
    platform agents in the dispatch catalog without requiring the operator
    to author sidecars manually (Issue #286).

    Returns:
        Absolute path to the ``fixtures/builtin/`` directory inside the
        installed ``claude_wayfinder`` package.
    """
    # fixtures/ is a sibling of build_catalog/ under claude_wayfinder/.
    # Path(__file__) → …/claude_wayfinder/build_catalog/_discover.py
    # .parent → …/claude_wayfinder/build_catalog/
    # .parent → …/claude_wayfinder/
    # / "fixtures" / "builtin" → …/claude_wayfinder/fixtures/builtin/
    return Path(__file__).parent.parent / "fixtures" / "builtin"


def _resolve_catalog_build_defaults(
    skills_dir: Path | None,
    agents_dir: Path | None,
    out: Path | None,
    log: Path | None,
    plugin_overrides_dir: Path | None = None,
    plugins_dir: Path | None = None,
    builtin_agents_dir: Path | None = None,
) -> dict[str, Path]:
    """Resolve all catalog-build paths, substituting defaults when None.

    The default base directory is ``${CLAUDE_HOME}`` when the env var is set,
    otherwise ``Path.home() / ".claude"``.  Individual args that were supplied
    explicitly (non-None) are returned unchanged; only ``None`` entries are
    filled from the defaults.

    For ``builtin_agents_dir`` the resolution follows a three-level cascade
    (Issue #286):

    1. **Explicit argument** — an explicitly supplied ``builtin_agents_dir``
       value is returned unchanged regardless of filesystem state.
    2. **User directory** — ``<base>/triggers/builtin`` when it exists on
       disk (the operator has placed custom sidecars there).
    3. **Bundled fallback** — ``claude_wayfinder/fixtures/builtin/`` inside
       the installed package, which ships ``Explore.yml`` and ``Plan.yml``
       so platform agents are available on a fresh install with zero
       operator configuration.

    All other path args follow a simpler two-level cascade: explicit value
    wins; absent explicit value falls back to the ``<base>/...`` default.

    This helper is the single source of truth for the default-resolution
    logic, called both from :func:`run_catalog_build` and directly by the
    test suite (which mocks ``Path.home()`` and ``os.environ``).

    Args:
        skills_dir: Explicit ``--skills-dir`` value, or ``None`` to use the
            default (``<base>/skills``).
        agents_dir: Explicit ``--agents-dir`` value, or ``None`` to use the
            default (``<base>/agents``).
        out: Explicit ``--out`` value, or ``None`` to use the default
            (``<base>/state/dispatch-catalog.json``).
        log: Explicit ``--log`` value, or ``None`` to use the default
            (``<base>/state/catalog-generation.log``).
        plugin_overrides_dir: Explicit ``--plugin-overrides-dir`` value,
            or ``None`` to use the default (``<base>/triggers``).
        plugins_dir: Explicit ``--plugins-dir`` value, or ``None`` to
            use the default (``<base>/plugins``).
        builtin_agents_dir: Explicit ``--builtin-agents-dir`` value, or
            ``None`` to trigger the three-level cascade described above.

    Returns:
        A dict with keys ``"skills_dir"``, ``"agents_dir"``, ``"out"``,
        ``"log"``, ``"plugin_overrides_dir"``, ``"plugins_dir"``, and
        ``"builtin_agents_dir"``, each containing a resolved ``Path``.
    """
    import os

    claude_home_env = os.environ.get("CLAUDE_HOME")
    if claude_home_env:
        base = Path(claude_home_env)
    else:
        base = Path.home() / ".claude"

    # Three-level cascade for builtin_agents_dir (Issue #286):
    #   1. Explicit arg → use as-is.
    #   2. User directory exists on disk → use it.
    #   3. Fallback to in-package bundled fixtures.
    if builtin_agents_dir is not None:
        resolved_builtin: Path = builtin_agents_dir
    else:
        user_builtin = base / "triggers" / "builtin"
        if user_builtin.is_dir():
            resolved_builtin = user_builtin
        else:
            resolved_builtin = _bundled_builtin_agents_dir()

    return {
        "skills_dir": (
            skills_dir if skills_dir is not None else base / "skills"
        ),
        "agents_dir": (
            agents_dir if agents_dir is not None else base / "agents"
        ),
        "out": (
            out if out is not None
            else base / "state" / "dispatch-catalog.json"
        ),
        "log": (
            log if log is not None
            else base / "state" / "catalog-generation.log"
        ),
        "plugin_overrides_dir": (
            plugin_overrides_dir if plugin_overrides_dir is not None
            else base / "triggers"
        ),
        "plugins_dir": (
            plugins_dir if plugins_dir is not None else base / "plugins"
        ),
        "builtin_agents_dir": resolved_builtin,
    }
