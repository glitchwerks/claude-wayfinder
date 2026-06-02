"""Trigger-schema validation for catalog entries.

Validates catalog entry frontmatter (or sidecar dicts) against the
trigger schema documented in ``docs/design/trigger-schema.md`` (v6).
Exports the two public result types (``ValidationIssue``,
``ValidationResult``), the entry validator (``validate_entry``), and
the constants that drive validation (``ALLOWED_WEIGHTS``,
``TRIGGER_FIELDS``).

This module has no dependencies on other ``build_catalog`` submodules
and is purely functional (no module-level I/O or mutable state).
"""

from __future__ import annotations

import dataclasses
from typing import Any, Literal

from claude_wayfinder._trigger_validators import (
    clamp_weight_to_ladder,
    has_whitespace,
    is_weight_in_ladder,
)

# ---------------------------------------------------------------------------
# Public type aliases
# ---------------------------------------------------------------------------

Severity = Literal["fatal", "warning", "info"]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ALLOWED_WEIGHTS: tuple[float, ...] = (0.25, 0.5, 1.0)

TRIGGER_FIELDS: tuple[str, ...] = (
    "command_prefixes",
    "agent_mentions",
    "path_globs",
    "keywords",
    "tool_mentions",
    "excludes",
    "path_globs_excluded",
)

# ``file_extensions`` was removed from TRIGGER_FIELDS.
# Sidecars that still declare it receive a warning and the field is
# stripped from the catalog entry.  This constant exists so the
# deprecation check can reference the field by name without magic
# strings.
_DEPRECATED_FILE_EXTENSIONS: str = "file_extensions"

# Frontmatter keys that belong only in triggers.yml under v6.  Their
# presence in SKILL.md is a v5 migration artefact and must be warned.
_V5_SIDECAR_KEYS: frozenset[str] = frozenset(
    {"triggers", "applicable_agents", "applicable_skills"}
)


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _clamp_weight(w: float) -> float:
    """Return the allowed weight value nearest to ``w``.

    Delegates to
    :func:`claude_wayfinder._trigger_validators.clamp_weight_to_ladder`.
    Ties (equidistant values) resolve to the higher allowed weight,
    so 0.75 → 1.0 rather than 0.5.

    Args:
        w: The raw weight value from the frontmatter.

    Returns:
        The closest value in ``ALLOWED_WEIGHTS``, with ties broken
        in favour of the larger value.
    """
    return clamp_weight_to_ladder(w)


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
        if has_whitespace(term):
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
        if not is_weight_in_ladder(weight_f):
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

        # Preserve the no_stem flag when present (issue #304).
        # Invalid values (non-bool) are silently coerced to False.
        no_stem_raw = item.get("no_stem", False)
        no_stem: bool = bool(no_stem_raw) if isinstance(no_stem_raw, bool) else False

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

        kw_entry: dict[str, Any] = {"term": term, "weight": weight_f}
        if no_stem:
            kw_entry["no_stem"] = True
        seen[term] = kw_entry

    return [seen[t] for t in order], issues


def _validate_keyword_groups(
    name: str,
    raw: Any,
) -> tuple[list[dict[str, Any]], list[ValidationIssue]]:
    """Validate triggers.keyword_groups; return (sanitized, issues).

    Spec § 6 validator rules:
        - slots: must be a list of length 2..8 (fatal outside)
        - slots length 4..8: warning ("real prompts rarely contain N roles")
        - each slot: 'terms' list with >= 1 string (1 term: warning)
        - intra-group term overlap: fatal
        - weight: must be in ALLOWED_WEIGHTS (fatal otherwise)
        - slot.name with whitespace: warning

    Sanitized output is the canonical dict form:
        [{'slots': [{'name': str|None, 'terms': [...]}], 'weight': float}, ...]

    Args:
        name: Entry name, used as ``entry_name`` in any issues.
        raw: The raw value of the ``keyword_groups`` key from frontmatter.

    Returns:
        A 2-tuple of ``(sanitized_groups, issues)``. Groups that fail a
        fatal check are dropped from ``sanitized_groups`` individually;
        only an un-parseable top-level list causes an empty return.
    """
    issues: list[ValidationIssue] = []

    if raw is None:
        return [], issues  # field is optional

    if not isinstance(raw, list):
        issues.append(
            ValidationIssue(
                "fatal",
                name,
                "'triggers.keyword_groups' must be a list — field dropped",
            )
        )
        return [], issues

    sanitized: list[dict[str, Any]] = []

    for g_idx, raw_group in enumerate(raw):
        if not isinstance(raw_group, dict):
            issues.append(
                ValidationIssue(
                    "fatal",
                    name,
                    f"keyword_groups[{g_idx}] is not a mapping — group dropped",
                )
            )
            continue

        raw_slots = raw_group.get("slots")
        if not isinstance(raw_slots, list):
            issues.append(
                ValidationIssue(
                    "fatal",
                    name,
                    f"keyword_groups[{g_idx}].slots must be a list — group dropped",
                )
            )
            continue
        if len(raw_slots) < 2:
            issues.append(
                ValidationIssue(
                    "fatal",
                    name,
                    f"keyword_groups[{g_idx}] needs >= 2 slots; use 'keywords:' "
                    "for single-term triggers — group dropped",
                )
            )
            continue
        if len(raw_slots) > 8:
            issues.append(
                ValidationIssue(
                    "fatal",
                    name,
                    f"keyword_groups[{g_idx}] has {len(raw_slots)} slots; "
                    "max is 8 — group dropped",
                )
            )
            continue
        if len(raw_slots) >= 4:
            issues.append(
                ValidationIssue(
                    "warning",
                    name,
                    f"keyword_groups[{g_idx}] has {len(raw_slots)} slots — "
                    "real prompts rarely contain that many distinct role tokens; "
                    "verify against real user phrasing",
                )
            )

        # Validate each slot.
        slot_results: list[dict[str, Any]] = []
        slot_fatal = False
        all_terms_per_slot: list[set[str]] = []

        for s_idx, raw_slot in enumerate(raw_slots):
            slot_name: str | None = None
            if isinstance(raw_slot, list):
                raw_terms: list[Any] = raw_slot
            elif isinstance(raw_slot, dict):
                raw_terms = raw_slot.get("terms")  # type: ignore[assignment]
                name_val = raw_slot.get("name")
                if isinstance(name_val, str):
                    slot_name = name_val
                    if any(c.isspace() for c in name_val) or not name_val.replace(
                        "_", ""
                    ).isalnum():
                        issues.append(
                            ValidationIssue(
                                "warning",
                                name,
                                f"keyword_groups[{g_idx}].slots[{s_idx}].name "
                                f"'{name_val}' should be a short identifier "
                                "(alphanumeric + underscore)",
                            )
                        )
            else:
                issues.append(
                    ValidationIssue(
                        "fatal",
                        name,
                        f"keyword_groups[{g_idx}].slots[{s_idx}] is neither a "
                        "list nor a mapping — group dropped",
                    )
                )
                slot_fatal = True
                break

            if not isinstance(raw_terms, list) or not raw_terms:
                issues.append(
                    ValidationIssue(
                        "fatal",
                        name,
                        f"keyword_groups[{g_idx}].slots[{s_idx}].terms must "
                        "be a non-empty list — group dropped",
                    )
                )
                slot_fatal = True
                break

            terms: list[str] = []
            for t in raw_terms:
                if not isinstance(t, str) or not t:
                    issues.append(
                        ValidationIssue(
                            "fatal",
                            name,
                            f"keyword_groups[{g_idx}].slots[{s_idx}].terms "
                            "contains a non-string or empty entry — group dropped",
                        )
                    )
                    slot_fatal = True
                    break
                terms.append(t.lower())
            if slot_fatal:
                break

            if len(terms) == 1:
                issues.append(
                    ValidationIssue(
                        "warning",
                        name,
                        f"keyword_groups[{g_idx}].slots[{s_idx}] is a "
                        f"single-term slot ('{terms[0]}') — consider merging into "
                        "an adjacent slot or using 'keywords:' if the term is "
                        "a standalone signal",
                    )
                )

            slot_results.append({"name": slot_name, "terms": terms})
            all_terms_per_slot.append(set(terms))

        if slot_fatal:
            continue

        # Intra-group term overlap check.
        seen_terms: dict[str, int] = {}
        overlap_fatal = False
        for s_idx, term_set in enumerate(all_terms_per_slot):
            for term in term_set:
                if term in seen_terms:
                    issues.append(
                        ValidationIssue(
                            "fatal",
                            name,
                            f"keyword_groups[{g_idx}]: term '{term}' appears "
                            f"in slots[{seen_terms[term]}] AND slots[{s_idx}] — a "
                            "term cannot fill two roles in one expression; "
                            "group dropped",
                        )
                    )
                    overlap_fatal = True
                    break
                seen_terms[term] = s_idx
            if overlap_fatal:
                break
        if overlap_fatal:
            continue

        # Weight validation — clamp + warn (consistent with
        # _validate_keywords).  Per project-reviewer concern C3: keep
        # behavior symmetric with the singleton-keyword validator.
        # Out-of-range weights are still fatal; in-range-but-non-canonical
        # weights are clamped with a warning.
        raw_weight = raw_group.get("weight")
        if isinstance(raw_weight, bool) or not isinstance(
            raw_weight, (int, float)
        ):
            issues.append(
                ValidationIssue(
                    "fatal",
                    name,
                    f"keyword_groups[{g_idx}].weight must be numeric — "
                    "group dropped",
                )
            )
            continue
        weight_f = float(raw_weight)
        if weight_f < 0.0 or weight_f > 1.0:
            issues.append(
                ValidationIssue(
                    "fatal",
                    name,
                    f"keyword_groups[{g_idx}].weight {weight_f} outside "
                    "[0.0, 1.0] — group dropped",
                )
            )
            continue
        if not is_weight_in_ladder(weight_f):
            clamped = _clamp_weight(weight_f)
            issues.append(
                ValidationIssue(
                    "warning",
                    name,
                    f"keyword_groups[{g_idx}].weight {weight_f} not in "
                    f"{{0.25, 0.5, 1.0}} — clamped to {clamped}",
                )
            )
            weight_f = clamped

        sanitized.append({"slots": slot_results, "weight": weight_f})

    # Cross-group term overlap check (project-reviewer concern C2).
    # Spec D5 replacement rule: any singleton whose term appears in ANY
    # satisfied group is suppressed. If two groups on the same entry share
    # a term and only one fires, the singleton is still suppressed — which
    # may surprise authors. Warn (not error) so authors verify intent.
    term_to_groups: dict[str, list[int]] = {}
    for g_idx, group in enumerate(sanitized):
        for slot in group["slots"]:
            for term in slot["terms"]:
                term_to_groups.setdefault(term, []).append(g_idx)
    for term, group_indices in term_to_groups.items():
        unique_groups = sorted(set(group_indices))
        if len(unique_groups) >= 2:
            issues.append(
                ValidationIssue(
                    "warning",
                    name,
                    f"term '{term}' appears in multiple keyword_groups "
                    f"({unique_groups}) — if only one group fires, the "
                    f"singleton for '{term}' is still suppressed (D5 "
                    "replacement rule). Verify this is intended.",
                )
            )

    return sanitized, issues


# ---------------------------------------------------------------------------
# Public validator
# ---------------------------------------------------------------------------


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

    # --- Validate keyword_groups (parallel path — NOT in TRIGGER_FIELDS) ---
    raw_groups = triggers_raw.get("keyword_groups")
    sanitized_groups, group_issues = _validate_keyword_groups(name, raw_groups)
    issues.extend(group_issues)
    if sanitized_groups:
        sanitized_triggers["keyword_groups"] = sanitized_groups

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
