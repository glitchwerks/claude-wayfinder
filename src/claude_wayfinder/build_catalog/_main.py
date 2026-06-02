"""Top-level catalog orchestration, log/catalog I/O, and CLI wiring.

Provides the ``build()`` function (multi-pass orchestrator), helpers for
writing the catalog JSON and log, the ``detect_project_root`` git helper,
and the CLI entry-point functions ``add_catalog_build_args``,
``run_catalog_build``, and ``main``.

Stemming (issue #304): ``build_catalog()`` computes and stores a
``stemmed_terms`` field on each catalog entry (a ``{term: stem}`` dict
covering all keyword terms).  The ``--check-stems`` flag activates a
post-build collision check that warns when two distinct catalog terms
from different entries share the same Porter2 stem.

Dependencies:
  - ``_validate.py``: ValidationIssue, validate_entry, TRIGGER_FIELDS
  - ``_semver.py``: _read_claude_version
  - ``_discover.py``: all discovery functions, _resolve_catalog_build_defaults,
    update_revisions_sidecar
  - ``_process.py``: all entry-processing functions

No circular dependencies with other ``build_catalog`` submodules.
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, cast

from claude_wayfinder.build_catalog._discover import (
    _resolve_catalog_build_defaults,
    discover_builtin_agents,
    discover_installed_plugins,
    discover_plugin_agent_overrides,
    discover_plugin_entries,
    discover_plugin_overrides,
    update_revisions_sidecar,
)
from claude_wayfinder.build_catalog._process import (
    _apply_colocated_sidecars,
    _process_builtin_sidecar,
    _process_file,
    _process_plugin_file,
    _process_plugin_override,
    _process_skill_file,
    _resolve_applicable_references,
    detect_exclude_dead_zones,
)
from claude_wayfinder.build_catalog._semver import _read_claude_version
from claude_wayfinder.build_catalog._validate import (
    TRIGGER_FIELDS,
    ValidationIssue,
    validate_entry,
)
from claude_wayfinder.match._stem import stem as _stem_word

_logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# Prefix for stem-collision warning lines emitted to stderr/stdout.
_STEM_COLLISION_PREFIX: str = "STEM_COLLISION"


# ---------------------------------------------------------------------------
# Stem-collision checker (--check-stems, issue #304)
# ---------------------------------------------------------------------------


def check_stem_collisions(catalog: dict[str, Any]) -> list[str]:
    """Detect catalog keyword terms from different entries that share a stem.

    Two terms from the SAME entry sharing a stem is harmless (they deduplicate
    to the same stem in the scoring set).  Cross-entry collisions are
    significant: when 'implement' (entry A) and 'implementing' (entry B) both
    stem to 'implement', a prompt containing 'implementing' boosts BOTH A and
    B equally — the terms no longer differentiate them.  This is a warning,
    not an error, because the catalog author may have deliberately accepted the
    overlap.

    Terms with ``no_stem=True`` are stored verbatim; their raw forms are used
    for exact matching and are never subject to stem collision.

    Args:
        catalog: The built catalog dict (as produced by :func:`build_catalog`).

    Returns:
        A list of human-readable collision strings.  Empty when no collisions
        are detected.  Each string starts with ``STEM_COLLISION``.
    """
    # stem_value → list of (entry_name, raw_term) pairs.
    stem_to_terms: dict[str, list[tuple[str, str]]] = {}

    for entry in catalog.get("entries", []):
        entry_name: str = entry.get("name", "<unknown>")
        kws = entry.get("triggers", {}).get("keywords", [])
        for kw in kws:
            term: str = kw.get("term", "")
            no_stem: bool = bool(kw.get("no_stem", False))
            if not term or no_stem:
                # no_stem terms are excluded from collision detection:
                # they are matched verbatim, so same-stem is not a risk.
                continue
            stem_val: str = _stem_word(term)
            stem_to_terms.setdefault(stem_val, []).append((entry_name, term))

    collisions: list[str] = []
    for stem_val, occurrences in stem_to_terms.items():
        # Only report when at least two DIFFERENT entries contribute to the same
        # stem (same-entry duplicates are already deduplicated by validate_entry).
        entry_names = [e for e, _ in occurrences]
        if len(set(entry_names)) < 2:
            continue
        terms_desc = ", ".join(
            f"'{term}' (in '{ename}')" for ename, term in occurrences
        )
        collisions.append(
            f"{_STEM_COLLISION_PREFIX}: stem '{stem_val}' shared by {terms_desc}"
        )

    return collisions


# ---------------------------------------------------------------------------
# Log I/O
# ---------------------------------------------------------------------------


def write_log(
    path: Path,
    issues: list[ValidationIssue],
    *,
    now: str | None = None,
) -> None:
    """Append validation issues to ``catalog-generation.log``.

    Each issue is written as a single line:
    ``<timestamp> <severity> <entry_name> <message>``

    Args:
        path: Log file path. Created if absent. Parent directory
            created if absent.
        issues: Issues to append, in order.
        now: ISO-8601 timestamp used as the prefix for every line.
            Defaults to the current UTC time formatted as
            ``%Y-%m-%dT%H:%M:%SZ``. Tests inject a fixed value for
            deterministic output.
    """
    if now is None:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for issue in issues:
            f.write(f"{now} {issue.severity} {issue.entry_name} {issue.message}\n")


# ---------------------------------------------------------------------------
# Catalog assembly
# ---------------------------------------------------------------------------


def _sort_entry_lists(entry: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *entry* with all list fields sorted.

    Keywords are sorted by ``term``; all other trigger list fields and
    the inverse field (``applicable_agents`` or ``applicable_skills``)
    are sorted lexicographically.

    Args:
        entry: A validated catalog entry dict.

    Returns:
        A new dict with the same top-level keys but sorted list values.
    """
    triggers = dict(entry["triggers"])
    for field in TRIGGER_FIELDS:
        raw_list = triggers.get(field, [])
        if field == "keywords":
            triggers[field] = sorted(raw_list, key=lambda k: k["term"])
        else:
            triggers[field] = sorted(raw_list)

    # Sort keyword_groups deterministically by (slots_len, weight).
    # Author-order is already stable but explicit sort makes output
    # byte-identical across re-runs even if catalog input ordering varies.
    # Handled as a parallel path — NOT via TRIGGER_FIELDS — because groups
    # are dicts, which are not orderable via sorted() directly (Step 6.4).
    groups = triggers.get("keyword_groups")
    if groups:
        triggers["keyword_groups"] = sorted(
            groups,
            key=lambda g: (len(g.get("slots", [])), g.get("weight", 0.0)),
        )

    inverse_field = "applicable_agents" if entry["kind"] == "skill" else "applicable_skills"
    out: dict[str, Any] = {
        "name": entry["name"],
        "kind": entry["kind"],
        "description": entry["description"],
        "source": entry.get("source", "owned"),
        "triggers": triggers,
        inverse_field: sorted(entry.get(inverse_field, [])),
    }
    # Propagate routable field when present (agents only).
    # Absent on skill entries; bool() guard ensures it is never None.
    if "routable" in entry:
        out["routable"] = bool(entry["routable"])

    # --- stemmed_terms (issue #304) ---
    # Compute a {term: stem} mapping for every keyword term.
    # Terms with no_stem=True map to themselves (verbatim, no stemming).
    # Entries with no keywords get an empty dict (or no field at all).
    kw_list = triggers.get("keywords", [])
    if kw_list:
        stemmed: dict[str, str] = {}
        for kw in kw_list:
            term = kw["term"]
            no_stem: bool = bool(kw.get("no_stem", False))
            stemmed[term] = term if no_stem else _stem_word(term)
        out["stemmed_terms"] = stemmed

    return out


def build_catalog(
    entries: list[dict[str, Any]],
    *,
    built_for_project: Path | None = None,
) -> dict[str, Any]:
    """Assemble the catalog dict from validated entries.

    Adds a top-level ``router_agent`` field that names the first entry
    with ``routable=False`` (informational; the per-entry flag is the
    actual exclusion gate).  When no entry declares ``routable: false``,
    ``router_agent`` is set to ``None`` and a warning is emitted to
    stderr via the caller (``build()``).

    Args:
        entries: Validated entries from ``validate_entry``. Each must
            have ``name``, ``kind``, ``description``, ``triggers``,
            and exactly one of ``applicable_agents`` /
            ``applicable_skills``.
        built_for_project: The resolved project root path when a
            project-local scan was performed, or ``None`` when only
            the user-global tree was scanned.  Stored as the top-level
            ``built_for_project`` field in the catalog JSON so the
            refresh hook can detect project switches.

    Returns:
        A catalog dict with keys ``schema_version``,
        ``built_for_project``, ``router_agent``, and ``entries``.
        Entries are sorted by ``(kind, name)``. Within each entry,
        list fields are sorted (keywords by ``term``).
    """
    sorted_entries = sorted(entries, key=lambda e: (e["kind"], e["name"]))
    out_entries = [_sort_entry_lists(e) for e in sorted_entries]

    # Identify the router agent: the first entry (in sort order) that
    # has routable=False.  This field is informational — the per-entry
    # routable flag is the actual gate in is_agent_routable.
    router_agent: str | None = None
    for e in sorted_entries:
        if not e.get("routable", True):
            router_agent = e["name"]
            break

    return {
        "schema_version": SCHEMA_VERSION,
        "built_for_project": (str(built_for_project) if built_for_project is not None else None),
        "router_agent": router_agent,
        "entries": out_entries,
    }


def write_catalog(path: Path, catalog: dict[str, Any]) -> None:
    """Write the catalog as compact, sorted, deterministic JSON.

    Args:
        path: Output path. Parent directory created if absent.
        catalog: Catalog dict from ``build_catalog``.

    Notes:
        - ``json.dumps`` with ``sort_keys=True`` makes top-level and
          nested dict keys deterministic.
        - ``separators=(", ", ": ")`` matches Python's default but
          pinned explicitly so a future Python version's default
          change cannot drift the output.
        - ``ensure_ascii=True`` (default) keeps the file ASCII-only.
        - A single trailing newline is added so ``cat`` and editors
          render the file cleanly without affecting the byte-equality
          comparison (both runs append the same newline).
    """
    text = json.dumps(catalog, sort_keys=True, separators=(", ", ": "))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Git project root detection
# ---------------------------------------------------------------------------


def detect_project_root(
    cwd: Path | None = None,
    user_global_dir: Path | None = None,
) -> Path | None:
    """Detect the git repository root for the given working directory.

    Runs ``git rev-parse --show-toplevel`` in *cwd* (or the process cwd
    when ``None``).  Returns the resolved path when inside a git repo, or
    ``None`` when the command fails (not a git repo, git not installed,
    etc.).

    When the resolved root equals ``user_global_dir``, ``None`` is returned
    to prevent double-scanning that tree as both owned and project sources.
    When ``user_global_dir`` is ``None`` the double-scan guard is skipped.

    The previous hard-coded ``~/.claude`` default for the guard has been
    removed (Issue #10).  Callers that need the guard must pass the user-
    global directory explicitly.

    Args:
        cwd: Directory in which to run the git command.  Defaults to the
            current process working directory when ``None``.
        user_global_dir: Resolved path to the user-global directory (e.g.
            ``~/.claude``).  When provided, the function returns ``None``
            if the detected git root equals this directory.  When ``None``,
            the guard is not applied.

    Returns:
        Resolved ``Path`` of the git repository root, or ``None`` when
        not inside a git repo or when the resolved root equals
        ``user_global_dir``.
    """
    effective_cwd = cwd or Path.cwd()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(effective_cwd),
        )
    except (FileNotFoundError, OSError):
        # git not installed or not accessible
        return None
    if result.returncode != 0:
        return None
    root = Path(result.stdout.strip()).resolve()
    if user_global_dir is not None and root == user_global_dir.resolve():
        return None
    return root


# ---------------------------------------------------------------------------
# Top-level build orchestrator
# ---------------------------------------------------------------------------


def build(
    *,
    skills_dir: Path,
    agents_dir: Path,
    corpus_path: Path | None,
    out_path: Path,
    log_path: Path,
    plugin_overrides_dir: Path | None = None,
    plugins_dir: Path | None = None,
    builtin_agents_dir: Path | None = None,
    project_root: Path | None = None,
    now: str | None = None,
) -> int:
    """Build the catalog.  Top-level orchestrator.

    Scans ``skills_dir`` recursively for ``SKILL.md`` files (v6: trigger
    config read from adjacent ``triggers.yml`` sidecars), scans
    ``agents_dir`` non-recursively for ``*.md`` files (agents retain
    inline frontmatter), and (when provided) scans
    ``plugin_overrides_dir`` for ``<plugin>/<skill>.yml`` overrides.

    Pass 2.5: when ``plugins_dir`` is supplied, reads the plugin manifest
    at ``<plugins_dir>/installed_plugins.json`` and enumerates
    ``SKILL.md`` / ``*.md`` files from each user-scoped install.  Plugin
    entries land **dormant** (zero triggers) with ``source="plugin"`` so
    they participate in cross-reference resolution but cannot drive
    routing decisions until explicitly activated via a plugin-override
    sidecar.

    Pass 2.6: when ``builtin_agents_dir`` is supplied, walks
    ``<builtin_agents_dir>/*.yml`` and emits catalog entries with
    ``source="builtin"`` for each valid, version-compatible sidecar.
    Builtin agents are Claude Code's embedded agents (``Explore``,
    ``Plan``) that cannot be edited but can be given trigger surface via
    operator-authored sidecars.  Each sidecar **must** declare
    ``min_claude_version``; absent pin or out-of-range running version
    causes the entry to be excluded with a logged issue.

    When ``project_root`` is set, additionally scans
    ``<project_root>/.claude/skills/**/SKILL.md`` and
    ``<project_root>/.claude/agents/*.md``.  Project entries carry
    ``source="project"`` and override user-global entries on name
    collision (with a warning logged).

    Each file is loaded, validated, and either included in the catalog
    or excluded (with a fatal issue logged).  Dead-zone detection runs
    after all entries are assembled.  The catalog JSON and log are
    written atomically (write then close) so the files are always
    consistent.

    Args:
        skills_dir: Root of the skills tree.  Recursively globbed for
            ``SKILL.md`` files.  Silently skipped if absent.
        agents_dir: Root of the agents tree.  Non-recursively globbed
            for ``*.md`` files.  Silently skipped if absent.
        corpus_path: Path to ``routing-corpus.jsonl``, or ``None`` to
            skip dead-zone detection.  When a path is given but the file
            is absent, detection is also skipped.
        out_path: Catalog JSON output path.  Parent directory created if
            absent.
        log_path: Log file path.  Parent directory created if absent.
        plugin_overrides_dir: Root of the plugin-override tree
            (``~/.claude/triggers/``).  Silently skipped if ``None``
            or absent.
        plugins_dir: Directory containing ``installed_plugins.json``
            (typically ``~/.claude/plugins/``).  When supplied, Pass
            2.5 reads the manifest and emits dormant entries for all
            user-scoped plugin skills and agents.  Silently skipped
            when ``None``.
        builtin_agents_dir: Directory containing builtin-agent sidecar
            ``.yml`` files (typically ``~/.claude/triggers/builtin/``).
            When supplied, Pass 2.6 reads each sidecar and emits
            ``source="builtin"`` agent entries.  Silently skipped when
            ``None`` or absent.
        project_root: Resolved path of the current git project root,
            when the generator is invoked from inside a project repo.
            When set, ``<project_root>/.claude/`` is scanned for local
            skills and agents.  ``None`` means no project merge.
        now: ISO-8601 timestamp injected into every log line.  Defaults
            to the current UTC time when ``None``.  Tests pass a fixed
            string for deterministic output.

    Returns:
        ``0`` on a clean build.  ``2`` when the catalog is degraded:
        either zero entries were discovered, or more than 25% of
        discovered entries were excluded fatally.
    """
    skill_files = sorted(skills_dir.glob("**/SKILL.md")) if skills_dir.is_dir() else []
    agent_files = sorted(agents_dir.glob("*.md")) if agents_dir.is_dir() else []

    all_issues: list[ValidationIssue] = []
    entries: list[dict[str, Any]] = []
    n_discovered = 0
    n_excluded = 0

    # --- Pass 1: owned skills (sidecar-based) ---
    for path in skill_files:
        n_discovered += 1
        result = _process_skill_file(path, issues_sink=all_issues, source="owned")
        if result is None:
            n_excluded += 1
        else:
            entries.append(result)

    # --- Pass 2: owned agents (inline frontmatter) ---
    for path in agent_files:
        n_discovered += 1
        result = _process_file(path, kind="agent", issues_sink=all_issues)
        if result is None:
            n_excluded += 1
        else:
            entries.append(result)

    # --- Pass 2b: owned-agent colocated sidecars ---
    # Walk agents_dir/*.triggers.yml and apply sidecar dispatch metadata to
    # the owned agent entries assembled in Pass 2.  Matched sidecars override
    # inline triggers (with a warning when both are present — D2).  Orphan
    # sidecars (no matching .md) emit a warning and are dropped (D3).
    # source="owned" is preserved — the sidecar is a delivery mechanism, not
    # an authorship change (D4).
    _apply_colocated_sidecars(
        agents_dir=agents_dir,
        entries=entries,
        source_tag="owned",
        all_issues=all_issues,
    )

    # --- Pass 2.5: plugin-provided skills and agents (dormant) ---
    # Plugin entries land with source="plugin" and zero triggers so they
    # are dormant by default.  This pass runs after the owned-agents pass
    # and before the plugin-overrides pass so that override entries
    # (Pass 3) can supersede plugin-provided ones.
    if plugins_dir is not None:
        plugin_issues: list[ValidationIssue] = []
        installs = discover_installed_plugins(plugins_dir, plugin_issues)
        all_issues.extend(plugin_issues)
        plugin_file_entries = discover_plugin_entries(installs)
        for p_kind, plugin_name, p_path in plugin_file_entries:
            n_discovered += 1
            if p_kind not in ("skill", "agent"):
                all_issues.append(
                    ValidationIssue(
                        "fatal",
                        "",
                        f"Invalid plugin entry kind: {p_kind!r} for {p_path}",
                    )
                )
                n_excluded += 1
                continue
            # Runtime guard above ensures p_kind ∈ {"skill", "agent"}.
            # cast() communicates this to mypy, which cannot infer the
            # Literal narrowing through a ``not in`` check on str.
            p_result = _process_plugin_file(
                p_path,
                kind=cast(Literal["skill", "agent"], p_kind),
                plugin_name=plugin_name,
                issues_sink=all_issues,
            )
            if p_result is None:
                n_excluded += 1
            else:
                entries.append(p_result)

    # --- Pass 2.6: builtin-agent sidecars ---
    # Walk <builtin_agents_dir>/*.yml for operator-authored sidecars
    # describing Claude Code's embedded agents (Explore, Plan).
    # Each sidecar must declare min_claude_version; the running version is
    # read dynamically so CI tests can override it via CLAUDE_VERSION.
    #
    # Version detection is skipped entirely when no sidecar files exist
    # (absent dir OR empty dir).  This avoids spurious errors on CI
    # runners that have neither 'claude' on PATH nor CLAUDE_VERSION set
    # but have nothing to evaluate anyway.
    if builtin_agents_dir is not None:
        builtin_sidecars = discover_builtin_agents(builtin_agents_dir)
        if builtin_sidecars:
            builtin_issues: list[ValidationIssue] = []
            running_version = _read_claude_version(builtin_issues)
            all_issues.extend(builtin_issues)
            if running_version is not None:
                for _stem, b_sidecar in builtin_sidecars:
                    n_discovered += 1
                    b_result = _process_builtin_sidecar(
                        b_sidecar,
                        stem=_stem,
                        running_version=running_version,
                        issues_sink=all_issues,
                    )
                    if b_result is None:
                        n_excluded += 1
                    else:
                        entries.append(b_result)

    # --- Pass 3: plugin overrides ---
    if plugin_overrides_dir is not None:
        # Build an index for collision detection.  Rebuilt after each
        # tombstone deletion so positional lookups stay valid.
        for _kind, entry_name, sidecar in discover_plugin_overrides(plugin_overrides_dir):
            n_discovered += 1
            result = _process_plugin_override(entry_name, sidecar, issues_sink=all_issues)
            if result is None:
                n_excluded += 1
                continue

            # --- Tombstone sentinel: ("disable", name, reason) ---
            if isinstance(result, tuple):
                _, tgt_name, tgt_reason = result
                # Build a name → index map over the *current* entries list.
                by_name: dict[str, int] = {e["name"]: i for i, e in enumerate(entries)}
                if tgt_name in by_name:
                    existing = entries[by_name[tgt_name]]
                    if existing.get("source") == "owned":
                        # Owned entries are immutable — reject tombstone.
                        all_issues.append(
                            ValidationIssue(
                                "warning",
                                tgt_name,
                                f"disable override targets owned entry"
                                f" '{tgt_name}' — rejected; owned entry"
                                " preserved",
                            )
                        )
                    else:
                        # Remove the entry and rebuild the index implicitly
                        # (next iteration rebuilds by_name from scratch).
                        del entries[by_name[tgt_name]]
                        _logger.info(
                            "plugin entry disabled by override (reason: %s)",
                            tgt_reason or "<none>",
                        )
                        all_issues.append(
                            ValidationIssue(
                                "info",
                                tgt_name,
                                f"plugin entry disabled by override"
                                f" (reason: {tgt_reason or '<none>'})",
                            )
                        )
                else:
                    _logger.warning(
                        "disable override targets nonexistent entry '%s'",
                        tgt_name,
                    )
                    all_issues.append(
                        ValidationIssue(
                            "warning",
                            tgt_name,
                            f"disable override targets nonexistent entry"
                            f" '{tgt_name}'",
                        )
                    )
                continue

            # --- Regular override: replace or append ---
            by_name_reg: dict[str, int] = {e["name"]: i for i, e in enumerate(entries)}
            ov_name: str = result["name"]
            if ov_name in by_name_reg:
                existing_entry = entries[by_name_reg[ov_name]]
                if existing_entry.get("source") == "owned":
                    # Owned entries cannot be overridden by plugin overrides.
                    all_issues.append(
                        ValidationIssue(
                            "warning",
                            ov_name,
                            f"plugin override targets owned entry '{ov_name}'"
                            " — rejected; owned entry preserved",
                        )
                    )
                else:
                    # Replace in place (plugin-discovered → override).
                    _logger.info(
                        "override layers on plugin-discovered entry '%s'",
                        ov_name,
                    )
                    all_issues.append(
                        ValidationIssue(
                            "info",
                            ov_name,
                            f"override layers on plugin-discovered entry"
                            f" '{ov_name}'",
                        )
                    )
                    entries[by_name_reg[ov_name]] = result
            else:
                entries.append(result)

    # --- Pass 3b: plugin-agent sidecar overrides ---
    # Walk <plugin_overrides_dir>/<plugin>/agents/*.yml for user-authored
    # sidecars that activate dormant plugin-discovered agents.  Strict
    # Mode 2a: a sidecar must match an existing source="plugin" agent
    # entry.  Unmatched sidecars (ghost sidecars) emit a warning and are
    # dropped — unlike skill overrides, which can append new entries.
    # This asymmetry is intentional: ghost agent entries cause hard
    # Agent({subagent_type: <ghost>}) failures the router cannot recover
    # from, whereas ghost skill entries degrade gracefully (score 0.0).
    if plugin_overrides_dir is not None:
        for _kind, ag_entry_name, ag_sidecar in discover_plugin_agent_overrides(
            plugin_overrides_dir
        ):
            n_discovered += 1
            # Agent sidecars (spec §4) do NOT carry 'kind' — the kind is
            # structural: the file is in agents/ so it is always kind="agent".
            # Validate directly as an agent rather than routing through
            # _process_plugin_override (which defaults to kind="skill" when
            # 'kind' is absent and supports tombstones which agents do not).
            if ag_sidecar.get("disabled") is True:
                all_issues.append(
                    ValidationIssue(
                        "warning",
                        ag_entry_name,
                        f"agent override sidecar '{ag_entry_name}' uses disabled"
                        " tombstone form — tombstones are not supported for"
                        " agent sidecars; sidecar dropped",
                    )
                )
                n_excluded += 1
                continue

            # Strip sidecar-specific keys before passing to validate_entry.
            effective_ag: dict[str, Any] = {
                k: v
                for k, v in ag_sidecar.items()
                if k not in ("kind", "disabled", "reason")
            }
            effective_ag.setdefault("name", ag_entry_name)
            ag_vr = validate_entry(
                effective_ag, kind="agent", source_stem=ag_entry_name
            )
            all_issues.extend(ag_vr.issues)
            if ag_vr.entry is None:
                n_excluded += 1
                continue
            ag_result: dict[str, Any] = ag_vr.entry
            ag_result["source"] = "plugin-override"

            # Strict Mode 2a: match against dormant source="plugin" agents only.
            # Build a (name, kind) index so that a same-named skill entry does
            # not accidentally satisfy the agent match.
            by_name_kind: dict[tuple[str, str], int] = {
                (e["name"], e["kind"]): i for i, e in enumerate(entries)
            }
            match_key = (ag_entry_name, "agent")
            if match_key in by_name_kind:
                existing_ag = entries[by_name_kind[match_key]]
                if existing_ag.get("source") == "owned":
                    # Owned agents are immutable — reject the sidecar.
                    all_issues.append(
                        ValidationIssue(
                            "warning",
                            ag_entry_name,
                            f"agent override sidecar targets owned entry"
                            f" '{ag_entry_name}' — rejected; owned entry"
                            " preserved",
                        )
                    )
                else:
                    # Replace in place (plugin-discovered dormant → override).
                    # Set routable=True explicitly per spec §7 Q5 (defensive,
                    # even though is_agent_routable already gates on source).
                    ag_result["routable"] = True
                    _logger.info(
                        "override layers on plugin-discovered agent '%s'",
                        ag_entry_name,
                    )
                    all_issues.append(
                        ValidationIssue(
                            "info",
                            ag_entry_name,
                            f"override layers on plugin-discovered agent"
                            f" '{ag_entry_name}'",
                        )
                    )
                    entries[by_name_kind[match_key]] = ag_result
            else:
                # Ghost sidecar — no matching dormant plugin agent found.
                _logger.warning(
                    "agent override sidecar '%s' has no matching plugin-discovered"
                    " agent entry — sidecar dropped",
                    ag_entry_name,
                )
                all_issues.append(
                    ValidationIssue(
                        "warning",
                        ag_entry_name,
                        f"agent override sidecar '{ag_entry_name}' has no"
                        " matching plugin-discovered agent entry — sidecar"
                        " dropped",
                    )
                )
                n_excluded += 1

    # --- Pass 4: project-local skills and agents ---
    if project_root is not None:
        project_claude = project_root / ".claude"
        proj_skill_dir = project_claude / "skills"
        proj_agent_dir = project_claude / "agents"

        proj_skill_files = (
            sorted(proj_skill_dir.glob("**/SKILL.md")) if proj_skill_dir.is_dir() else []
        )
        proj_agent_files = (
            sorted(proj_agent_dir.glob("*.md")) if proj_agent_dir.is_dir() else []
        )

        project_entries: list[dict[str, Any]] = []

        for path in proj_skill_files:
            n_discovered += 1
            result = _process_skill_file(path, issues_sink=all_issues, source="project")
            if result is None:
                n_excluded += 1
            else:
                project_entries.append(result)

        for path in proj_agent_files:
            n_discovered += 1
            result = _process_file(path, kind="agent", issues_sink=all_issues)
            if result is None:
                n_excluded += 1
            else:
                result["source"] = "project"
                project_entries.append(result)

        # --- Pass 4b: project-agent colocated sidecars ---
        # Walk proj_agent_dir/*.triggers.yml and apply sidecar dispatch
        # metadata to project agent entries assembled above.  Runs before
        # the merge step so orphan detection uses the pre-merge project
        # entry list (spec §7 Q5).
        _apply_colocated_sidecars(
            agents_dir=proj_agent_dir,
            entries=project_entries,
            source_tag="project",
            all_issues=all_issues,
        )

        # Merge: project entries override user-global entries on collision.
        if project_entries:
            owned_by_name: dict[str, int] = {
                e["name"]: idx for idx, e in enumerate(entries)
            }
            for proj_entry in project_entries:
                name = proj_entry["name"]
                if name in owned_by_name:
                    all_issues.append(
                        ValidationIssue(
                            "warning",
                            name,
                            f"project entry '{name}' overrides user-global entry",
                        )
                    )
                    entries[owned_by_name[name]] = proj_entry
                else:
                    entries.append(proj_entry)

    _resolve_applicable_references(entries, all_issues)
    if corpus_path is not None:
        all_issues.extend(
            detect_exclude_dead_zones(entries=entries, corpus_path=corpus_path)
        )
    else:
        all_issues.append(
            ValidationIssue(
                "info",
                "<catalog>",
                "corpus path not configured; skipping EXCLUDE_DEAD_ZONE checks",
            )
        )

    # Update the per-component revision sidecar.  Only owned components
    # (skills under skills/ and agents under agents/) are tracked —
    # plugin overrides have no authored body to hash, and project-local
    # entries vary by repo so they would race the sidecar.
    trackable: list[dict[str, str]] = [
        {
            "name": e["name"],
            "kind": e["kind"],
            "content_hash": e["content_hash"],
        }
        for e in entries
        if e.get("source") == "owned" and "content_hash" in e
    ]
    sidecar_path = out_path.parent / "component-revisions.json"
    update_revisions_sidecar(trackable, sidecar_path)

    catalog = build_catalog(entries, built_for_project=project_root)

    # Warn when no agent declared routable: false.  The catalog's
    # router_agent field will be null, which means all agents are scored
    # — including the router itself if it appears as an entry.
    if catalog.get("router_agent") is None:
        print(
            "[catalog] WARNING: no router agent declared (routable: false);"
            " all agents will be scored",
            file=sys.stderr,
        )

    write_catalog(out_path, catalog)
    write_log(log_path, all_issues, now=now)

    degraded = (n_discovered == 0) or (n_excluded / max(n_discovered, 1) > 0.25)
    return 2 if degraded else 0


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------


def add_catalog_build_args(parser: argparse.ArgumentParser) -> None:
    """Register all ``catalog build`` flags onto *parser*.

    This helper is extracted so that both the standalone
    ``build_catalog.main()`` entry point and the ``cli.py``
    ``catalog build`` sub-subparser can share an identical parameter
    surface without duplication.

    The four previously-required args (``--skills-dir``, ``--agents-dir``,
    ``--out``, ``--log``) are now optional with ``default=None``.  When not
    supplied, :func:`run_catalog_build` resolves them via
    :func:`_resolve_catalog_build_defaults`, anchoring to ``${CLAUDE_HOME}``
    (or ``~/.claude`` when unset).  This allows the bundled
    ``refresh-catalog-on-stale.js`` hook's bare ``python -m claude_wayfinder
    catalog build`` invocation to succeed without requiring
    ``DISPATCH_GENERATOR_CMD`` override (issue #87).

    Args:
        parser: An ``ArgumentParser`` (or sub-parser) to populate.
    """
    parser.add_argument(
        "--skills-dir",
        type=Path,
        default=None,
        help=(
            "Directory containing skill SKILL.md files.  "
            "Defaults to ${CLAUDE_HOME}/skills (or ~/.claude/skills)."
        ),
    )
    parser.add_argument(
        "--agents-dir",
        type=Path,
        default=None,
        help=(
            "Directory containing agent frontmatter .md files.  "
            "Defaults to ${CLAUDE_HOME}/agents (or ~/.claude/agents)."
        ),
    )
    parser.add_argument(
        "--plugin-overrides-dir",
        type=Path,
        default=None,
        help=(
            "Directory containing plugin-override trigger .yml files.  "
            "Defaults to ${CLAUDE_HOME}/triggers (or ~/.claude/triggers)."
        ),
    )
    parser.add_argument(
        "--plugins-dir",
        type=Path,
        default=None,
        help=(
            "Directory containing installed_plugins.json.  Used for "
            "Pass 2.5 plugin discovery.  Defaults to "
            "${CLAUDE_HOME}/plugins (or ~/.claude/plugins)."
        ),
    )
    parser.add_argument(
        "--builtin-agents-dir",
        type=Path,
        default=None,
        help=(
            "Directory containing builtin-agent sidecar .yml files.  "
            "Used for Pass 2.6 builtin discovery.  Defaults to "
            "${CLAUDE_HOME}/triggers/builtin "
            "(or ~/.claude/triggers/builtin)."
        ),
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        default=None,
        help="Path to routing-corpus.jsonl for corpus-alignment scoring.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help=(
            "Output path for dispatch-catalog.json.  "
            "Defaults to ${CLAUDE_HOME}/state/dispatch-catalog.json "
            "(or ~/.claude/state/dispatch-catalog.json)."
        ),
    )
    parser.add_argument(
        "--log",
        type=Path,
        default=None,
        help=(
            "Output path for the catalog-generation log.  "
            "Defaults to ${CLAUDE_HOME}/state/catalog-generation.log "
            "(or ~/.claude/state/catalog-generation.log)."
        ),
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=None,
        help=(
            "Path to the project git root to merge project-local skills "
            "and agents from <root>/.claude/.  When omitted, auto-detected "
            "via 'git rev-parse --show-toplevel' in the current directory."
        ),
    )
    parser.add_argument(
        "--check-stems",
        action="store_true",
        default=False,
        help=(
            "After building the catalog, report any pairs of distinct keyword "
            "terms from different catalog entries that share the same Porter2 "
            "stem.  Competing-skill stem collisions are printed to stderr as "
            "STEM_COLLISION lines.  Does not affect the catalog output or exit "
            "code; authors should review and either accept the collision or add "
            "no_stem: true to disambiguate.  (issue #304)"
        ),
    )


def run_catalog_build(args: argparse.Namespace) -> int:
    """Execute a catalog build from a pre-parsed argument namespace.

    Resolves the seven optional path args (``skills_dir``, ``agents_dir``,
    ``out``, ``log``, ``plugin_overrides_dir``, ``plugins_dir``,
    ``builtin_agents_dir``) via :func:`_resolve_catalog_build_defaults`
    when they were not supplied, then resolves the project root (explicit
    flag or auto-detection) and delegates to :func:`build`.

    Extracted so that both the standalone ``build_catalog`` entry point and
    the ``cli.py`` ``catalog build`` subcommand share identical post-parse
    behaviour without duplication.

    Args:
        args: A parsed ``argparse.Namespace`` carrying all attributes
            registered by :func:`add_catalog_build_args`.  All seven path
            attrs may be ``None`` when not supplied; this function resolves
            them before delegating to :func:`build`.

    Returns:
        Integer exit code: ``0`` on a clean build, ``2`` when the
        catalog is degraded (see :func:`build`).
    """
    # Resolve all optional path args from CLAUDE_HOME defaults when not
    # explicitly provided.  This covers the original four (issue #87) and
    # the three plugin-discovery flags (issue #124) that previously defaulted
    # to None, silently disabling Pass 2.5 / Pass 2.6 / override resolution.
    resolved = _resolve_catalog_build_defaults(
        skills_dir=args.skills_dir,
        agents_dir=args.agents_dir,
        out=args.out,
        log=args.log,
        plugin_overrides_dir=args.plugin_overrides_dir,
        plugins_dir=args.plugins_dir,
        builtin_agents_dir=args.builtin_agents_dir,
    )

    # Resolve project root: explicit flag takes priority; fall back to
    # auto-detection from the current working directory.
    if args.project_root is not None:
        project_root: Path | None = args.project_root.resolve()
    else:
        project_root = detect_project_root(user_global_dir=None)

    exit_code = build(
        skills_dir=resolved["skills_dir"],
        agents_dir=resolved["agents_dir"],
        plugin_overrides_dir=resolved["plugin_overrides_dir"],
        plugins_dir=resolved["plugins_dir"],
        builtin_agents_dir=resolved["builtin_agents_dir"],
        corpus_path=args.corpus,
        out_path=resolved["out"],
        log_path=resolved["log"],
        project_root=project_root,
    )

    # --- --check-stems collision report (issue #304) ---
    # Runs after a successful build so the catalog JSON exists.  Collisions
    # are printed to stderr as informational warnings; the exit code and the
    # catalog output are unaffected.
    check_stems: bool = getattr(args, "check_stems", False)
    if check_stems and resolved["out"].exists():
        import json as _json

        try:
            catalog_data = _json.loads(
                resolved["out"].read_text(encoding="utf-8")
            )
            collisions = check_stem_collisions(catalog_data)
            if collisions:
                for line in collisions:
                    print(line, file=sys.stderr)
            else:
                print(
                    "[check-stems] No stem collisions detected.", file=sys.stderr
                )
        except Exception as exc:  # pragma: no cover
            print(
                f"[check-stems] Could not read catalog for collision check: {exc}",
                file=sys.stderr,
            )

    return exit_code


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.  Returns process exit code.

    Args:
        argv: Argument list to parse.  Defaults to ``sys.argv[1:]``
            when ``None``.

    Returns:
        Integer exit code: ``0`` on a clean build, ``2`` when the
        catalog is degraded (see ``build()``).
    """
    parser = argparse.ArgumentParser(
        description=(
            "Build the dispatch catalog from skill sidecars and agent "
            "frontmatter.  All directory paths that previously defaulted "
            "to ~/.claude/... now require explicit values (Issue #10)."
        )
    )
    add_catalog_build_args(parser)
    args = parser.parse_args(argv)
    return run_catalog_build(args)
