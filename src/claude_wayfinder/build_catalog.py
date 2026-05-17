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
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import logging
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, cast

import yaml

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)

_logger = logging.getLogger(__name__)

Severity = Literal["fatal", "warning", "info"]

ALLOWED_WEIGHTS: tuple[float, ...] = (0.25, 0.5, 1.0)

TRIGGER_FIELDS: tuple[str, ...] = (
    "command_prefixes",
    "agent_mentions",
    "path_globs",
    "keywords",
    "tool_mentions",
    "excludes",
)

# ``file_extensions`` was removed from TRIGGER_FIELDS.
# Sidecars that still declare it receive a warning and the field is
# stripped from the catalog entry.  This constant exists so the
# deprecation check can reference the field by name without magic strings.
_DEPRECATED_FILE_EXTENSIONS: str = "file_extensions"

# Frontmatter keys that belong only in triggers.yml under v6.  Their
# presence in SKILL.md is a v5 migration artefact and must be warned.
_V5_SIDECAR_KEYS: frozenset[str] = frozenset({"triggers", "applicable_agents", "applicable_skills"})


@dataclasses.dataclass(frozen=True, slots=True)
class ValidationIssue:
    """One validation finding produced by the schema validator.

    Attributes:
        severity: Per the docs/design/trigger-schema.md severity ladder.
        entry_name: ``name`` field of the entry, or the file path
            stem if the file lacked a parseable ``name``.
        message: Human-readable detail. Goes verbatim to the log.
    """

    severity: Severity
    entry_name: str
    message: str


@dataclasses.dataclass(frozen=True, slots=True)
class ValidationResult:
    """Outcome of validating one entry.

    Attributes:
        entry: The sanitized catalog entry, or ``None`` if a fatal
            issue means the entry must be excluded.
        issues: All issues found, in the order produced by the
            validator (deterministic).
    """

    entry: dict[str, Any] | None
    issues: list[ValidationIssue]


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


def _clamp_weight(w: float) -> float:
    """Return the allowed weight value nearest to ``w``.

    Ties (equidistant values) resolve to the higher allowed weight,
    so 0.75 → 1.0 rather than 0.5.

    Args:
        w: The raw weight value from the frontmatter.

    Returns:
        The closest value in ``ALLOWED_WEIGHTS``, with ties broken
        in favour of the larger value.
    """
    return min(ALLOWED_WEIGHTS, key=lambda v: (abs(v - w), -v))


def _blank_entry(
    name: str,
    fm: dict[str, Any],
    kind: Literal["skill", "agent"],
) -> dict[str, Any]:
    """Build a dormant catalog entry with empty trigger lists.

    Args:
        name: Validated entry name.
        fm: Source frontmatter mapping (used for description).
        kind: Whether the source is a skill or agent file.

    Returns:
        A catalog entry dict with all trigger fields set to ``[]``.
    """
    inverse_field = "applicable_agents" if kind == "skill" else "applicable_skills"
    return {
        "name": name,
        "kind": kind,
        "description": fm.get("description", ""),
        "triggers": {f: [] for f in TRIGGER_FIELDS},
        inverse_field: [],
    }


def _validate_keywords(
    name: str,
    raw: Any,
) -> tuple[list[dict[str, Any]], list[ValidationIssue]]:
    """Validate ``triggers.keywords`` and return a sanitized list.

    Applies weight clamping (with a warning) and last-wins
    deduplication (with a warning) in a single pass.

    Args:
        name: Entry name, used as ``entry_name`` in any issues.
        raw: The raw value of the ``keywords`` key from frontmatter.

    Returns:
        A 2-tuple of ``(sanitized_keywords, issues)``.  If a fatal
        issue is produced, ``sanitized_keywords`` will be empty and
        the caller must check ``issues`` for fatals before using the
        result.
    """
    issues: list[ValidationIssue] = []

    if not isinstance(raw, list):
        issues.append(
            ValidationIssue(
                "fatal",
                name,
                "'triggers.keywords' must be a list — entry excluded",
            )
        )
        return [], issues

    seen: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    for idx, item in enumerate(raw):
        if not isinstance(item, dict) or "term" not in item or "weight" not in item:
            issues.append(
                ValidationIssue(
                    "fatal",
                    name,
                    f"keywords[{idx}] is not a {{term, weight}} mapping — entry excluded",
                )
            )
            return [], issues

        term = item["term"]
        weight = item["weight"]

        if not isinstance(term, str) or not term:
            issues.append(
                ValidationIssue(
                    "fatal",
                    name,
                    f"keywords[{idx}].term must be a non-empty string — entry excluded",
                )
            )
            return [], issues

        # Keywords must be single tokens.  A term containing whitespace
        # cannot match anything (the matcher works on individual tokens).
        # Warn and skip the entry rather than fatally excluding it — the
        # remaining keywords may still be valid.
        if any(c.isspace() for c in term):
            issues.append(
                ValidationIssue(
                    "warning",
                    name,
                    f"keywords[{idx}].term '{term}' contains whitespace — "
                    "keywords must be single tokens; keyword dropped",
                )
            )
            continue

        # Reject booleans — bool is a subclass of int in Python and
        # would otherwise pass the numeric isinstance check.
        if isinstance(weight, bool) or not isinstance(weight, (int, float)):
            issues.append(
                ValidationIssue(
                    "fatal",
                    name,
                    f"keywords[{idx}].weight must be numeric — entry excluded",
                )
            )
            return [], issues

        weight_f = float(weight)
        if weight_f < 0.0 or weight_f > 1.0:
            issues.append(
                ValidationIssue(
                    "fatal",
                    name,
                    f"keywords[{idx}].weight {weight_f} is outside [0.0, 1.0] — entry excluded",
                )
            )
            return [], issues
        if weight_f not in ALLOWED_WEIGHTS:
            clamped = _clamp_weight(weight_f)
            issues.append(
                ValidationIssue(
                    "warning",
                    name,
                    f"keywords[{idx}].weight {weight_f} not in"
                    f" {{0.25, 0.5, 1.0}} — clamped to {clamped}",
                )
            )
            weight_f = clamped

        if term in seen:
            issues.append(
                ValidationIssue(
                    "warning",
                    name,
                    f"keywords duplicate term '{term}'"
                    f" — deduplicated (last wins, weight {weight_f})",
                )
            )
        else:
            order.append(term)

        seen[term] = {"term": term, "weight": weight_f}

    return [seen[t] for t in order], issues


def validate_entry(
    fm: dict[str, Any],
    *,
    kind: Literal["skill", "agent"],
    source_stem: str,
) -> ValidationResult:
    """Validate one frontmatter mapping against the trigger schema.

    The validator collects every issue it can find before returning
    (rather than stopping at the first error), except when a fatal
    makes it impossible to continue processing the current section.

    For v6 skills this function receives the sidecar dict (or the
    agent inline frontmatter); the SKILL.md frontmatter is stripped
    of trigger keys before this function is called.

    Args:
        fm: Parsed YAML mapping.  For skills this is the sidecar dict
            merged with the minimal runtime fields from SKILL.md.
            For agents this is the full inline frontmatter.
        kind: Whether this came from a skill file or agent file.
            Determines which of ``applicable_agents`` /
            ``applicable_skills`` is the relevant inverse field.
        source_stem: File stem to use as ``entry_name`` if ``fm``
            lacks a parseable ``name``.

    Returns:
        A ``ValidationResult``. ``entry`` is ``None`` when a fatal
        issue means this entry must be excluded from the catalog.
    """
    name = fm.get("name")
    if not isinstance(name, str) or not name:
        return ValidationResult(
            entry=None,
            issues=[
                ValidationIssue(
                    "fatal",
                    source_stem,
                    "missing or non-string 'name'",
                )
            ],
        )

    issues: list[ValidationIssue] = []
    triggers_raw = fm.get("triggers")

    # --- Dormant case: no triggers block at all ---
    if triggers_raw is None:
        issues.append(ValidationIssue("info", name, "no triggers block — entry dormant"))
        return ValidationResult(
            entry=_blank_entry(name, fm, kind),
            issues=issues,
        )

    if not isinstance(triggers_raw, dict):
        issues.append(
            ValidationIssue(
                "fatal",
                name,
                "'triggers' is not a mapping — entry excluded",
            )
        )
        return ValidationResult(entry=None, issues=issues)

    # --- Deprecation check: file_extensions ---
    # ``file_extensions`` was removed from TRIGGER_FIELDS.
    # If a sidecar still declares it, warn and drop the field so authors
    # are nudged to migrate to ``path_globs``.
    if _DEPRECATED_FILE_EXTENSIONS in triggers_raw:
        issues.append(
            ValidationIssue(
                "warning",
                name,
                f"'triggers.{_DEPRECATED_FILE_EXTENSIONS}' is deprecated "
                "— use 'triggers.path_globs' instead; field dropped",
            )
        )

    # --- Validate each trigger field ---
    sanitized_triggers: dict[str, Any] = {}
    for field in TRIGGER_FIELDS:
        raw = triggers_raw.get(field, [])
        if field == "keywords":
            sanitized, kw_issues = _validate_keywords(name, raw)
            issues.extend(kw_issues)
            if any(i.severity == "fatal" for i in kw_issues):
                return ValidationResult(entry=None, issues=issues)
            sanitized_triggers["keywords"] = sanitized
        else:
            if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
                issues.append(
                    ValidationIssue(
                        "fatal",
                        name,
                        f"'triggers.{field}' must be a list of strings — entry excluded",
                    )
                )
                return ValidationResult(entry=None, issues=issues)
            sanitized_triggers[field] = list(raw)

    # --- Validate inverse field (applicable_agents / applicable_skills) ---
    inverse_field = "applicable_agents" if kind == "skill" else "applicable_skills"
    inverse = fm.get(inverse_field, [])
    if not isinstance(inverse, list) or not all(isinstance(x, str) for x in inverse):
        issues.append(
            ValidationIssue(
                "fatal",
                name,
                f"'{inverse_field}' must be a list of strings — entry excluded",
            )
        )
        return ValidationResult(entry=None, issues=issues)

    # Warn when triggers exist but the inverse list is empty — the
    # entry can never match anything at routing time.
    has_triggers = any(sanitized_triggers.get(f) for f in TRIGGER_FIELDS)
    if has_triggers and not inverse:
        issues.append(
            ValidationIssue(
                "warning",
                name,
                f"triggers declared but {inverse_field} is empty — entry will never match",
            )
        )

    entry: dict[str, Any] = {
        "name": name,
        "kind": kind,
        "description": fm.get("description", ""),
        "triggers": sanitized_triggers,
        inverse_field: list(inverse),
    }
    return ValidationResult(entry=entry, issues=issues)


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


SCHEMA_VERSION = 1


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
    return out


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


_BUILTIN_AGENTS_SUBDIR: str = "builtin"

# ---------------------------------------------------------------------------
# Semver helpers for Pass 2.6 builtin-agent version pinning
# ---------------------------------------------------------------------------


def _parse_semver(version_str: str) -> tuple[int, ...]:
    """Parse a semver string into a tuple of integers for comparison.

    Accepts formats like ``"2.1"``, ``"2.1.138"``, ``"3.0.0"``.  Each
    dot-separated component is coerced to ``int``.  Non-numeric suffixes
    (pre-release labels etc.) are not supported — the sidecar schema
    restricts values to plain numeric dotted strings.

    Args:
        version_str: Semver string, e.g. ``"2.1.138"``.

    Returns:
        Tuple of ints, e.g. ``(2, 1, 138)`` or ``(2, 1)`` for ``"2.1"``.

    Raises:
        ValueError: If any component is not a non-negative integer.
    """
    parts = version_str.strip().split(".")
    result: list[int] = []
    for part in parts:
        if not part.isdigit():
            raise ValueError(
                f"semver component {part!r} in {version_str!r} is not a" " non-negative integer"
            )
        result.append(int(part))
    return tuple(result)


def _read_claude_version(
    issues_sink: list[ValidationIssue],
) -> str | None:
    """Return the running Claude Code version string, or ``None`` on failure.

    Resolution order:

    1. Shell out to ``claude --version`` and parse the first token before
       any space (e.g. ``"2.1.138 (Claude Code)"`` → ``"2.1.138"``).
    2. Fall back to the ``CLAUDE_VERSION`` environment variable.
    3. If neither succeeds, append a fatal ``ValidationIssue`` to
       *issues_sink* and return ``None``.

    Args:
        issues_sink: Mutable list to which a fatal issue is appended
            when the version cannot be determined.

    Returns:
        Version string (e.g. ``"2.1.138"``), or ``None`` when
        unresolvable.
    """
    import os

    # Attempt 1: claude --version
    try:
        result = subprocess.run(
            ["claude", "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            # Format: "2.1.138 (Claude Code)" — take the first token.
            raw = result.stdout.strip().split()[0]
            return raw
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        pass

    # Attempt 2: CLAUDE_VERSION env var
    env_version = os.environ.get("CLAUDE_VERSION", "").strip()
    if env_version:
        return env_version

    # Neither source available — warn and exclude builtin entries.
    # This is an environment problem (CI runner, fresh install) rather than
    # an authoring error, so we demote to warning so the catalog build can
    # still complete.  The version-pin discipline is preserved because all
    # builtin entries are excluded when the version is unknown.
    issues_sink.append(
        ValidationIssue(
            "warning",
            "<builtin>",
            "cannot determine running Claude Code version (claude --version"
            " failed and CLAUDE_VERSION not set); builtin entries excluded",
        )
    )
    return None


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
    effective: dict[str, Any] = {k: v for k, v in sidecar.items() if k not in _BUILTIN_ONLY_KEYS}
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


_PLUGINS_MANIFEST_FILENAME: str = "installed_plugins.json"

# Minimum supported manifest schema version.  The spec says accept
# ``version >= 2`` for forward-compatibility with future supersets.
_MIN_PLUGIN_MANIFEST_VERSION: int = 2


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


_PLUGIN_NAME_RE: re.Pattern[str] = re.compile(r"^[^:]+:[^:]+$")


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
                f"plugin override has invalid kind {raw_kind!r};" " must be 'skill' or 'agent'",
            )
        )
        return None
    resolved_kind = cast(Literal["skill", "agent"], raw_kind)

    # Inject the synthesised name so validate_entry can use it.
    # Strip the sidecar-specific 'kind' and 'disabled'/'reason' fields
    # before passing to validate_entry so they do not pollute the entry.
    effective = {k: v for k, v in sidecar.items() if k not in ("kind", "disabled", "reason")}
    effective.setdefault("name", entry_name)
    result = validate_entry(effective, kind=resolved_kind, source_stem=entry_name)
    issues_sink.extend(result.issues)
    if result.entry is not None:
        result.entry["source"] = "plugin-override"
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
        issues_sink.append(ValidationIssue("fatal", canonical_name, f"YAML parse error: {exc}"))
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
        issues_sink.append(ValidationIssue("fatal", stem, "no frontmatter — entry excluded"))
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
                            f"disable override targets nonexistent entry" f" '{tgt_name}'",
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
                            f"override layers on plugin-discovered entry" f" '{ov_name}'",
                        )
                    )
                    entries[by_name_reg[ov_name]] = result
            else:
                entries.append(result)

    # --- Pass 4: project-local skills and agents ---
    if project_root is not None:
        project_claude = project_root / ".claude"
        proj_skill_dir = project_claude / "skills"
        proj_agent_dir = project_claude / "agents"

        proj_skill_files = (
            sorted(proj_skill_dir.glob("**/SKILL.md")) if proj_skill_dir.is_dir() else []
        )
        proj_agent_files = sorted(proj_agent_dir.glob("*.md")) if proj_agent_dir.is_dir() else []

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

        # Merge: project entries override user-global entries on collision.
        if project_entries:
            owned_by_name: dict[str, int] = {e["name"]: idx for idx, e in enumerate(entries)}
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


def _resolve_catalog_build_defaults(
    skills_dir: Path | None,
    agents_dir: Path | None,
    out: Path | None,
    log: Path | None,
) -> dict[str, Path]:
    """Resolve the four catalog-build paths, substituting defaults when None.

    The default base directory is ``${CLAUDE_HOME}`` when the env var is set,
    otherwise ``Path.home() / ".claude"``.  Individual args that were supplied
    explicitly (non-None) are returned unchanged; only ``None`` entries are
    filled from the defaults.

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

    Returns:
        A dict with keys ``"skills_dir"``, ``"agents_dir"``, ``"out"``, and
        ``"log"``, each containing a resolved ``Path``.
    """
    import os

    claude_home_env = os.environ.get("CLAUDE_HOME")
    if claude_home_env:
        base = Path(claude_home_env)
    else:
        base = Path.home() / ".claude"

    return {
        "skills_dir": skills_dir if skills_dir is not None else base / "skills",
        "agents_dir": agents_dir if agents_dir is not None else base / "agents",
        "out": out if out is not None else base / "state" / "dispatch-catalog.json",
        "log": log if log is not None else base / "state" / "catalog-generation.log",
    }


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
        help="Directory containing plugin-override trigger .yml files.",
    )
    parser.add_argument(
        "--plugins-dir",
        type=Path,
        default=None,
        help=(
            "Directory containing installed_plugins.json.  Used for "
            "Pass 2.5 plugin discovery."
        ),
    )
    parser.add_argument(
        "--builtin-agents-dir",
        type=Path,
        default=None,
        help=(
            "Directory containing builtin-agent sidecar .yml files.  "
            "Used for Pass 2.6 builtin discovery."
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


def run_catalog_build(args: argparse.Namespace) -> int:
    """Execute a catalog build from a pre-parsed argument namespace.

    Resolves the four optional path args (``skills_dir``, ``agents_dir``,
    ``out``, ``log``) via :func:`_resolve_catalog_build_defaults` when they
    were not supplied, then resolves the project root (explicit flag or
    auto-detection) and delegates to :func:`build`.

    Extracted so that both the standalone ``build_catalog`` entry point and
    the ``cli.py`` ``catalog build`` subcommand share identical post-parse
    behaviour without duplication.

    Args:
        args: A parsed ``argparse.Namespace`` carrying all attributes
            registered by :func:`add_catalog_build_args`.  The four path
            attrs (``skills_dir``, ``agents_dir``, ``out``, ``log``) may be
            ``None`` when not supplied; this function resolves them before
            delegating to :func:`build`.

    Returns:
        Integer exit code: ``0`` on a clean build, ``2`` when the
        catalog is degraded (see :func:`build`).
    """
    # Resolve the four formerly-required path args from CLAUDE_HOME defaults
    # when not explicitly provided.  This is the structural fix for issue #87:
    # defaults live at the CLI, not at the hook.
    resolved = _resolve_catalog_build_defaults(
        skills_dir=args.skills_dir,
        agents_dir=args.agents_dir,
        out=args.out,
        log=args.log,
    )

    # Resolve project root: explicit flag takes priority; fall back to
    # auto-detection from the current working directory.
    if args.project_root is not None:
        project_root: Path | None = args.project_root.resolve()
    else:
        project_root = detect_project_root(user_global_dir=None)

    return build(
        skills_dir=resolved["skills_dir"],
        agents_dir=resolved["agents_dir"],
        plugin_overrides_dir=args.plugin_overrides_dir,
        plugins_dir=args.plugins_dir,
        builtin_agents_dir=args.builtin_agents_dir,
        corpus_path=args.corpus,
        out_path=resolved["out"],
        log_path=resolved["log"],
        project_root=project_root,
    )


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


if __name__ == "__main__":
    sys.exit(main())
