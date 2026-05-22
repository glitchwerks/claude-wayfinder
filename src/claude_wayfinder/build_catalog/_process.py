"""Entry processing and cross-reference resolution for the catalog builder.

Provides functions that validate, transform, and reconcile catalog entries
after discovery.  Also contains ``detect_exclude_dead_zones`` (corpus-based
dead-zone detection, currently deferred).

Dependencies:
  - ``_validate.py``: ValidationIssue, ValidationResult, validate_entry,
    TRIGGER_FIELDS, _V5_SIDECAR_KEYS
  - ``_semver.py``: _parse_semver
  - ``_discover.py``: discover_colocated_agent_sidecars, load_frontmatter,
    load_trigger_sidecar, compute_content_hash

No circular dependencies with other ``build_catalog`` submodules.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Literal, cast

import yaml

from claude_wayfinder.build_catalog._discover import (
    compute_content_hash,
    discover_colocated_agent_sidecars,
    load_frontmatter,
    load_trigger_sidecar,
)
from claude_wayfinder.build_catalog._semver import _parse_semver
from claude_wayfinder.build_catalog._validate import (
    _V5_SIDECAR_KEYS,
    TRIGGER_FIELDS,
    ValidationIssue,
    validate_entry,
)

_logger = logging.getLogger(__name__)

_PLUGIN_NAME_RE: re.Pattern[str] = re.compile(r"^[^:]+:[^:]+$")


# ---------------------------------------------------------------------------
# Plugin-name helpers
# ---------------------------------------------------------------------------


def _is_plugin_namespaced(name: str) -> bool:
    """Return True when *name* follows the ``<plugin>:<skill>`` convention.

    Plugin-namespaced names refer to skills provided by installed plugins
    (e.g. ``microsoft-docs:microsoft-docs``, ``superpowers:brainstorming``).
    They cannot be verified at catalog-build time because the plugin's
    skill tree is not part of the owned-skills scan.  The pattern is
    exactly one colon with non-empty segments on each side.

    Args:
        name: A candidate skill or agent name string.

    Returns:
        ``True`` when *name* matches ``<plugin>:<skill>`` (one colon,
        non-empty prefix and suffix); ``False`` otherwise.
    """
    return bool(_PLUGIN_NAME_RE.match(name))


# ---------------------------------------------------------------------------
# Cross-reference resolution
# ---------------------------------------------------------------------------


def _resolve_applicable_references(
    entries: list[dict[str, Any]],
    issues_sink: list[ValidationIssue],
) -> None:
    """Drop and warn for ``applicable_*`` entries that reference unknown names.

    Mutates each entry's inverse-field list in place.  The wildcard ``"*"``
    is allowed and never warned.  Plugin-namespaced names
    (``<plugin>:<skill>`` format) are treated as external references to
    runtime-installed plugin skills; they cannot be verified at build time
    and are kept with an ``info`` log entry rather than dropped.  Per spec
    §5 / §9.8.

    After all entries have been loaded, build the universe of known agent
    and skill names, then iterate every entry's inverse field.  Any name
    that is neither ``"*"``, a plugin-namespaced reference, nor present in
    the corresponding known-names set is removed from the field and a
    warning ``ValidationIssue`` is appended to ``issues_sink`` with the
    exact format the spec mandates.

    Args:
        entries: All validated catalog entries produced by the two
            ``_process_file`` loops in ``build()``.  Each entry must
            carry ``name``, ``kind``, and exactly one of
            ``applicable_agents`` / ``applicable_skills``.
        issues_sink: Mutable list to which warning issues are appended.
            Ordering follows the entry order in ``entries``, then the
            declaration order within each entry's inverse field.
    """
    known_agents: set[str] = {e["name"] for e in entries if e["kind"] == "agent"}
    known_skills: set[str] = {e["name"] for e in entries if e["kind"] == "skill"}

    for entry in entries:
        if entry["kind"] == "skill":
            field: str = "applicable_agents"
            known: set[str] = known_agents
        else:
            field = "applicable_skills"
            known = known_skills

        original: list[str] = entry.get(field, [])
        resolved: list[str] = []
        for name in original:
            if name == "*" or name in known:
                resolved.append(name)
            elif _is_plugin_namespaced(name):
                # Plugin-provided skill: cannot verify at build time.
                # Keep the reference and log at info level so catalog
                # consumers know it is an external (unverified) pointer.
                resolved.append(name)
                issues_sink.append(
                    ValidationIssue(
                        "info",
                        entry["name"],
                        f"{field} contains plugin skill reference '{name}'"
                        " — kept as external reference (unverified at build time)",
                    )
                )
            else:
                issues_sink.append(
                    ValidationIssue(
                        "warning",
                        entry["name"],
                        f"{field} references unknown name '{name}' — dropped",
                    )
                )
        entry[field] = resolved


# ---------------------------------------------------------------------------
# Dead-zone detection (deferred)
# ---------------------------------------------------------------------------


def detect_exclude_dead_zones(
    *,
    entries: list[dict[str, Any]],
    corpus_path: Path,
) -> list[ValidationIssue]:
    """Detect ``excludes`` terms that never affect a decision.

    Per ``docs/design/trigger-schema.md`` §7. Warning only; never
    excludes an entry.

    Args:
        entries: Validated catalog entries.
        corpus_path: Path to the captured routing corpus
            (``~/.claude/state/routing-corpus.jsonl``).

    Returns:
        A list of ``ValidationIssue``.

    Notes:
        Full simulation requires the matcher. Until that integration
        lands, this function emits a single ``info`` line documenting
        the deferral when the corpus is present, and a single ``info``
        line documenting the skip when absent.
    """
    if not corpus_path.exists() or corpus_path.stat().st_size == 0:
        return [
            ValidationIssue(
                "info",
                "<catalog>",
                "corpus unavailable; skipping EXCLUDE_DEAD_ZONE checks",
            )
        ]
    return [
        ValidationIssue(
            "info",
            "<catalog>",
            "EXCLUDE_DEAD_ZONE checks deferred: matcher not yet integrated",
        )
    ]


# ---------------------------------------------------------------------------
# v5 leftover detection
# ---------------------------------------------------------------------------


def _check_skill_md_for_v5_leftovers(
    fm: dict[str, Any],
    name: str,
    issues_sink: list[ValidationIssue],
) -> None:
    """Emit warnings for v5 trigger keys found in SKILL.md frontmatter.

    Under v6, ``triggers:``, ``applicable_agents:``, and
    ``applicable_skills:`` must not appear in ``SKILL.md``.  When a
    SKILL.md still carries those keys (migration artefact from v5),
    the generator warns once per offending key and ignores the values.
    The sidecar file (or its absence) is the only authoritative source.

    Args:
        fm: The parsed SKILL.md frontmatter mapping.
        name: Entry name used in warning messages.
        issues_sink: Mutable list to which warnings are appended.
    """
    leftover = _V5_SIDECAR_KEYS & set(fm.keys())
    if leftover:
        keys_str = ", ".join(sorted(leftover))
        issues_sink.append(
            ValidationIssue(
                "warning",
                name,
                f"SKILL.md contains v5 trigger keys ({keys_str}) — "
                "ignored under v6; use triggers.yml instead",
            )
        )


# ---------------------------------------------------------------------------
# Colocated sidecar application
# ---------------------------------------------------------------------------


def _apply_colocated_sidecars(
    agents_dir: Path,
    entries: list[dict[str, Any]],
    source_tag: str,
    all_issues: list[ValidationIssue],
) -> None:
    """Apply colocated ``*.triggers.yml`` sidecars to already-assembled entries.

    Mutates *entries* in place.  This is the shared implementation for
    Pass 2b (owned agents, ``source_tag="owned"``) and Pass 4b (project
    agents, ``source_tag="project"``).

    For each sidecar found in *agents_dir*:

    - **Matched** — a sibling ``.md`` was discovered and produced a catalog
      entry with ``name == stem`` and ``kind == "agent"`` with the correct
      ``source``: the entry's ``triggers`` and ``applicable_skills`` are
      replaced with the sidecar's values.  When the existing entry already
      carries non-empty inline triggers a ``_logger.warning`` is emitted
      (D2 — sidecar shadows inline triggers).
    - **Orphan** — no matching entry found: emit ``_logger.warning`` and
      drop the sidecar (D3 — no new entry is created).

    Sidecar files that fail YAML parsing are already dropped by
    :func:`discover_colocated_agent_sidecars` before this function is
    called.

    Args:
        agents_dir: Directory containing ``.triggers.yml`` sidecars.
        entries: The in-progress entries list to mutate.
        source_tag: ``"owned"`` or ``"project"`` — used to filter the
            lookup index so a same-named entry with a different source
            (e.g. from a plugin or builtin) is not accidentally matched.
        all_issues: Accumulator for :class:`ValidationIssue` records.
            Warnings are appended here as well as being logged via
            ``_logger``.
    """
    sidecars = discover_colocated_agent_sidecars(agents_dir, issues_sink=all_issues)
    if not sidecars:
        return

    # Build a (name, kind, source) → index lookup over current entries.
    # source_tag scoping ensures a same-named plugin entry is not matched.
    by_name_kind_source: dict[tuple[str, str, str], int] = {
        (e["name"], e["kind"], e.get("source", "owned")): i
        for i, e in enumerate(entries)
    }

    for stem, sidecar in sidecars:
        match_key = (stem, "agent", source_tag)
        if match_key not in by_name_kind_source:
            # Orphan sidecar — no matching agent .md discovered.
            _logger.warning(
                "colocated agent sidecar '%s.triggers.yml' in '%s' has no"
                " matching agent .md file — sidecar dropped",
                stem,
                agents_dir,
            )
            all_issues.append(
                ValidationIssue(
                    "warning",
                    stem,
                    f"colocated agent sidecar '{stem}.triggers.yml' has no"
                    " matching agent .md file — sidecar dropped",
                )
            )
            continue

        idx = by_name_kind_source[match_key]
        existing = entries[idx]

        # D2: warn when the entry already has non-empty inline triggers.
        existing_triggers = existing.get("triggers", {})
        has_inline_triggers = any(
            existing_triggers.get(f) for f in TRIGGER_FIELDS
        )
        if has_inline_triggers:
            sidecar_path_d2 = agents_dir / f"{stem}.triggers.yml"
            _logger.warning(
                "colocated agent sidecar '%s' shadows inline triggers in"
                " '%s.md' — sidecar takes precedence",
                sidecar_path_d2,
                stem,
            )
            all_issues.append(
                ValidationIssue(
                    "warning",
                    stem,
                    f"colocated agent sidecar '{stem}.triggers.yml' shadows"
                    f" inline triggers in '{stem}.md' — sidecar takes"
                    " precedence",
                )
            )

        # Validate sidecar trigger fields through the standard pipeline so
        # weight clamping, keyword dedup, deprecated-field stripping, and
        # whitespace stripping all apply — mirroring Pass 3b for plugin-agent
        # sidecars (#142).
        effective: dict[str, Any] = {"name": stem}
        if "triggers" in sidecar:
            effective["triggers"] = sidecar["triggers"]
        if "applicable_skills" in sidecar:
            effective["applicable_skills"] = sidecar["applicable_skills"]

        vr = validate_entry(effective, kind="agent", source_stem=stem)
        all_issues.extend(vr.issues)
        if vr.entry is None:
            # Validation rejected the sidecar outright — warn and skip.
            _logger.warning(
                "colocated agent sidecar '%s.triggers.yml' rejected by"
                " validate_entry — sidecar dropped",
                stem,
            )
            continue

        if "triggers" in vr.entry:
            existing["triggers"] = vr.entry["triggers"]
        if "applicable_skills" in vr.entry:
            existing["applicable_skills"] = vr.entry["applicable_skills"]

        # #153: defensive write so a ``routable: false`` frontmatter doesn't
        # silently keep the agent inert after a sidecar is applied.
        existing["routable"] = True


# ---------------------------------------------------------------------------
# Builtin sidecar processing
# ---------------------------------------------------------------------------


def _process_builtin_sidecar(
    sidecar: dict[str, Any],
    *,
    stem: str,
    running_version: str,
    issues_sink: list[ValidationIssue],
) -> dict[str, Any] | None:
    """Validate a builtin-agent sidecar and return a catalog entry.

    Enforces version pinning:

    * ``min_claude_version`` **must** be present — absence is fatal.
    * If ``max_claude_version`` is present, the running version must be
      ``<= max_claude_version`` — violation is a warning and the entry is
      excluded.
    * If the running version is ``< min_claude_version`` — warning +
      exclude.

    On success, returns a ``dict[str, Any]`` catalog entry with
    ``source="builtin"`` and ``kind="agent"``.

    Args:
        sidecar: Parsed YAML dict from the sidecar file.
        stem: File stem (used as ``entry_name`` when ``name`` is absent).
        running_version: The running Claude Code version string, e.g.
            ``"2.1.138"``.
        issues_sink: Mutable list to which ``ValidationIssue`` objects
            are appended.

    Returns:
        A validated entry dict on success, or ``None`` when the sidecar
        must be excluded.
    """
    entry_name: str = str(sidecar.get("name") or stem)

    # --- Require min_claude_version ---
    min_ver_raw = sidecar.get("min_claude_version")
    if min_ver_raw is None:
        issues_sink.append(
            ValidationIssue(
                "fatal",
                entry_name,
                f"builtin sidecar '{entry_name}' is missing"
                " min_claude_version — entry excluded; add"
                " 'min_claude_version: \"<version>\"' to pin it",
            )
        )
        return None

    # --- Parse and compare versions ---
    try:
        min_ver = _parse_semver(str(min_ver_raw))
        running_ver = _parse_semver(running_version)
    except ValueError as exc:
        issues_sink.append(
            ValidationIssue(
                "fatal",
                entry_name,
                f"builtin sidecar '{entry_name}' has unparseable version:"
                f" {exc} — entry excluded",
            )
        )
        return None

    if running_ver < min_ver:
        issues_sink.append(
            ValidationIssue(
                "warning",
                entry_name,
                f"builtin '{entry_name}' pinned to"
                f" min={min_ver_raw}, current Claude Code version is"
                f" {running_version} — entry excluded",
            )
        )
        return None

    max_ver_raw = sidecar.get("max_claude_version")
    if max_ver_raw is not None:
        try:
            max_ver = _parse_semver(str(max_ver_raw))
        except ValueError as exc:
            issues_sink.append(
                ValidationIssue(
                    "fatal",
                    entry_name,
                    f"builtin sidecar '{entry_name}' has unparseable"
                    f" max_claude_version: {exc} — entry excluded",
                )
            )
            return None
        if running_ver > max_ver:
            issues_sink.append(
                ValidationIssue(
                    "warning",
                    entry_name,
                    f"builtin '{entry_name}' pinned to"
                    f" max={max_ver_raw}, current Claude Code version is"
                    f" {running_version} — entry excluded",
                )
            )
            return None

    # --- Build effective mapping and validate ---
    # Strip builtin-specific keys before passing to validate_entry.
    _BUILTIN_ONLY_KEYS = frozenset({"min_claude_version", "max_claude_version", "kind"})
    effective: dict[str, Any] = {
        k: v for k, v in sidecar.items() if k not in _BUILTIN_ONLY_KEYS
    }
    effective.setdefault("name", entry_name)

    result = validate_entry(
        effective,
        kind="agent",
        source_stem=stem,
    )
    issues_sink.extend(result.issues)
    if result.entry is not None:
        result.entry["source"] = "builtin"
    return result.entry


# ---------------------------------------------------------------------------
# File-level processing
# ---------------------------------------------------------------------------


def _process_skill_file(
    skill_md: Path,
    *,
    issues_sink: list[ValidationIssue],
    source: str = "owned",
) -> dict[str, Any] | None:
    """Load, validate, and return the catalog entry for one skill.

    Under v6 the trigger config is read from ``triggers.yml`` next to
    the ``SKILL.md``.  The ``SKILL.md`` provides only runtime fields
    (``name``, ``description``).  Any trigger keys still in the SKILL.md
    frontmatter are warned about and ignored.

    Args:
        skill_md: Path to the ``SKILL.md`` file.
        issues_sink: Mutable list to which all ``ValidationIssue``
            objects produced during processing are appended.
        source: Catalog source tag — ``"owned"`` for skills in the
            ``skills/`` tree.  Plugin overrides use a separate path.

    Returns:
        A validated entry dict on success, or ``None`` on fatal error.
    """
    stem = skill_md.parent.name
    try:
        fm = load_frontmatter(skill_md)
    except yaml.YAMLError as exc:
        issues_sink.append(ValidationIssue("fatal", stem, f"YAML parse error: {exc}"))
        return None
    if fm is None:
        issues_sink.append(ValidationIssue("fatal", stem, "no frontmatter — entry excluded"))
        return None

    name = fm.get("name", stem)
    # Warn about v5 leftover keys; they are ignored for trigger resolution.
    _check_skill_md_for_v5_leftovers(fm, name, issues_sink)

    # Load the sidecar and merge its keys into the effective mapping
    # used for validation.  Runtime fields (name, description) come
    # from SKILL.md; trigger fields come from the sidecar.
    sidecar = load_trigger_sidecar(skill_md.parent)
    if sidecar is not None:
        effective: dict[str, Any] = {
            "name": fm.get("name"),
            "description": fm.get("description", ""),
        }
        effective.update(sidecar)
    else:
        # No sidecar — pass only the runtime fields so validate_entry
        # produces a dormant entry (no triggers block).
        effective = {
            "name": fm.get("name"),
            "description": fm.get("description", ""),
        }

    content_hash = compute_content_hash(skill_md)
    result = validate_entry(effective, kind="skill", source_stem=stem)
    issues_sink.extend(result.issues)
    if result.entry is not None:
        result.entry["source"] = source
        result.entry["content_hash"] = content_hash
        intentional = effective.get("applicable_agents_intentional", "")
        if intentional:
            result.entry["applicable_agents_intentional"] = str(intentional)
    return result.entry


def _process_plugin_override(
    entry_name: str,
    sidecar: dict[str, Any],
    *,
    issues_sink: list[ValidationIssue],
) -> dict[str, Any] | tuple[str, str, str] | None:
    """Validate a plugin-override sidecar and return a catalog entry.

    Supports three sidecar forms:

    * **Tombstone**: ``disabled: true`` (with optional ``reason: str``).
      Returns the sentinel tuple ``("disable", entry_name, reason)`` so
      that the caller can remove the matching entry from the catalog.
    * **Agent override**: ``kind: agent`` causes the entry to be validated
      and emitted as ``kind="agent"`` instead of the default ``"skill"``.
    * **Skill override** (default): omit ``kind`` or set ``kind: skill``.

    An invalid ``kind`` value (anything other than ``"skill"`` or
    ``"agent"``) is a fatal configuration error: a ``ValidationIssue``
    with severity ``"fatal"`` is appended to *issues_sink* and ``None``
    is returned.

    Args:
        entry_name: Plugin-namespaced entry name, e.g.
            ``"superpowers:brainstorming"``.
        sidecar: Parsed sidecar YAML dict.
        issues_sink: Mutable list to which all ``ValidationIssue``
            objects are appended.

    Returns:
        * ``("disable", entry_name, reason)`` — tombstone sentinel.
        * A validated entry ``dict`` on success.
        * ``None`` on fatal validation error.
    """
    # --- Tombstone path ---
    if sidecar.get("disabled") is True:
        reason: str = str(sidecar.get("reason") or "")
        return ("disable", entry_name, reason)

    # --- Kind resolution ---
    raw_kind = sidecar.get("kind", "skill")
    if raw_kind not in ("skill", "agent"):
        issues_sink.append(
            ValidationIssue(
                "fatal",
                entry_name,
                f"plugin override has invalid kind {raw_kind!r};"
                " must be 'skill' or 'agent'",
            )
        )
        return None
    resolved_kind = cast(Literal["skill", "agent"], raw_kind)

    # Inject the synthesised name so validate_entry can use it.
    # Strip the sidecar-specific 'kind' and 'disabled'/'reason' fields
    # before passing to validate_entry so they do not pollute the entry.
    effective = {
        k: v for k, v in sidecar.items() if k not in ("kind", "disabled", "reason")
    }
    effective.setdefault("name", entry_name)
    result = validate_entry(effective, kind=resolved_kind, source_stem=entry_name)
    issues_sink.extend(result.issues)
    if result.entry is not None:
        result.entry["source"] = "plugin-override"
        intentional = effective.get("applicable_agents_intentional", "")
        if intentional:
            result.entry["applicable_agents_intentional"] = str(intentional)
    return result.entry


def _process_plugin_file(
    path: Path,
    *,
    kind: Literal["skill", "agent"],
    plugin_name: str,
    issues_sink: list[ValidationIssue],
) -> dict[str, Any] | None:
    """Load a plugin-provided skill or agent file and return a dormant entry.

    Plugin-provided files are read for their ``description`` frontmatter
    field only.  Any trigger configuration in the frontmatter is
    intentionally ignored — plugin entries land dormant (zero triggers)
    with ``source="plugin"`` per Pass 2.5 specification.

    The entry name is **always** synthesised as ``"<plugin>:<stem>"``
    where ``plugin`` is the short plugin identifier (before ``@``) and
    ``stem`` is the parent directory name for skills or the file stem for
    agents.  This canonical form ensures that ``applicable_skills``
    references like ``"superpowers:brainstorming"`` resolve to a real
    catalog entry and do not fire the info-level
    "kept as external reference" log.

    Args:
        path: Path to the ``SKILL.md`` or agent ``.md`` file.
        kind: Whether this file represents a skill or an agent.
        plugin_name: Full plugin identifier (e.g. ``"superpowers@mkt"``).
            The short name (before ``@``) is used as the namespace prefix.
        issues_sink: Mutable list to which ``ValidationIssue`` objects
            are appended.

    Returns:
        A dormant catalog entry dict on success, or ``None`` on fatal
        error (YAML parse failure or unreadable file).
    """
    stem = path.parent.name if kind == "skill" else path.stem
    # Derive a short plugin namespace from the full identifier.
    # e.g. "superpowers@my-plugin-registry" → "superpowers"
    plugin_short = plugin_name.split("@")[0]
    # Always synthesise the canonical namespaced name so references in
    # applicable_skills / applicable_agents resolve correctly.
    canonical_name = f"{plugin_short}:{stem}"

    try:
        fm = load_frontmatter(path)
    except yaml.YAMLError as exc:
        issues_sink.append(
            ValidationIssue("fatal", canonical_name, f"YAML parse error: {exc}")
        )
        return None

    description: str = str((fm or {}).get("description") or "")

    # Build a minimal effective mapping so validate_entry produces a dormant
    # entry (no triggers block → all trigger lists empty).
    effective: dict[str, Any] = {"name": canonical_name, "description": description}
    result = validate_entry(effective, kind=kind, source_stem=stem)
    issues_sink.extend(result.issues)
    if result.entry is not None:
        result.entry["source"] = "plugin"
    return result.entry


def _process_file(
    path: Path,
    *,
    kind: Literal["skill", "agent"],
    issues_sink: list[ValidationIssue],
) -> dict[str, Any] | None:
    """Load, validate, and return the catalog entry for an agent file.

    Agents retain inline frontmatter as the trigger source under v6.
    Skills use the dedicated ``_process_skill_file`` path instead.

    Args:
        path: Path to the agent markdown file.
        kind: Must be ``"agent"`` for this function.
        issues_sink: Mutable list to which all ``ValidationIssue``
            objects produced during processing are appended.

    Returns:
        A validated entry dict if the file is valid, or ``None`` when
        a fatal issue means the entry must be excluded from the catalog.
    """
    stem = path.parent.name if kind == "skill" else path.stem
    try:
        fm = load_frontmatter(path)
    except yaml.YAMLError as exc:
        issues_sink.append(ValidationIssue("fatal", stem, f"YAML parse error: {exc}"))
        return None
    if fm is None:
        issues_sink.append(
            ValidationIssue("fatal", stem, "no frontmatter — entry excluded")
        )
        return None
    content_hash = compute_content_hash(path)
    result = validate_entry(fm, kind=kind, source_stem=stem)
    issues_sink.extend(result.issues)
    if result.entry is not None:
        result.entry["source"] = "owned"
        result.entry["content_hash"] = content_hash
        # Read routable flag from frontmatter (default True).
        # Agents that declare ``routable: false`` are excluded from the
        # matcher's scored pool via is_agent_routable (match_filters.py).
        routable_raw = fm.get("routable", True)
        result.entry["routable"] = bool(routable_raw)
    return result.entry
