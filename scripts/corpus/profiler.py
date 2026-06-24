"""Dispatch-log structural profiler for corpus phase A.

Produces a per-field, per-entry-type population profile of the dispatch log.
Structural scanning only — field names, types, presence, and lengths.
Never reads content values for credential-shaped fields.

Public API
----------
- ``field_profile(path)`` — return structural profile dict for matcher_decision
  entries in the given JSONL log file.

The profile is JSON-serialisable and carries:
  - total_matcher_decision, organic_count, fixture_count
  - empty_task_description_count
  - decision_distribution
  - td_length_bands  (empty / short / medium / long / very_long)
  - input_field_presence  (per-field: count + rate)
  - output_field_presence (per-field: count + rate)
  - flagged_fields         (list of {field, presence_count, presence_rate,
                            nonempty_count, populated_rate, reason})

Privacy constraint (issue #338 §HC-3):
  Field names, structural lengths, and population rates are collected.
  No log content values are inspected for credential patterns.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from claude_wayfinder.log_filter import is_organic_entry

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: "near empty" threshold: fields with population rate below this fraction
#: are flagged (in addition to 0%-populated fields).
NEAR_EMPTY_THRESHOLD: float = 0.05

#: Task-description length band boundaries (chars).
TD_BAND_SHORT_MAX: int = 50    # [1, 50)  → "short"
TD_BAND_MEDIUM_MAX: int = 200  # [50, 200) → "medium"
TD_BAND_LONG_MAX: int = 500    # [200, 500) → "long"
                               # [500, ∞)  → "very_long"

#: Canonical input sub-fields defined in the log schema.
_KNOWN_INPUT_FIELDS: tuple[str, ...] = (
    "task_description",
    "file_paths",
    "command_prefix",
    "agent_mentions",
    "tool_mentions",
    "prompt",
    "active_skills",
    "recent_agents",
)

#: Canonical output sub-fields defined in the log schema.
_KNOWN_OUTPUT_FIELDS: tuple[str, ...] = (
    "decision",
    "confidence",
    "rationale",
    "alternatives",
    "agent",
    "skills",
    "disposition_source",
    "lanes",
    "unassigned_paths",
    "override_id",
)


# ---------------------------------------------------------------------------
# Length band
# ---------------------------------------------------------------------------


def td_length_band(text: str) -> str:
    """Classify a task_description string into a length band.

    Args:
        text: The task_description value (may be empty string).

    Returns:
        One of ``"empty"``, ``"short"``, ``"medium"``, ``"long"``,
        ``"very_long"``.
    """
    if not text:
        return "empty"
    n = len(text)
    if n < TD_BAND_SHORT_MAX:
        return "short"
    if n < TD_BAND_MEDIUM_MAX:
        return "medium"
    if n < TD_BAND_LONG_MAX:
        return "long"
    return "very_long"


# ---------------------------------------------------------------------------
# Core profiler
# ---------------------------------------------------------------------------


def field_profile(path: Path) -> dict[str, Any]:
    """Produce a structural population profile of matcher_decision entries.

    Reads only field names, types, presence, and structural lengths from
    the JSONL file.  Content values are never inspected for credential
    patterns.

    Args:
        path: Path to the dispatch-log JSONL file.

    Returns:
        A JSON-serialisable dict with the following keys:

        - ``total_matcher_decision`` — total entries of this type
        - ``organic_count``          — entries with non-empty session_id
        - ``fixture_count``          — entries with empty/absent session_id
        - ``empty_task_description_count`` — organic entries with empty td
        - ``decision_distribution``  — Counter-style dict of decision values
        - ``td_length_bands``        — band → count for organic entries
        - ``input_field_presence``   — field → {count, rate} for organic
        - ``output_field_presence``  — field → {count, rate} for organic
        - ``flagged_fields``         — list of {field, presence_count,
                                       presence_rate, nonempty_count,
                                       populated_rate, reason}
    """
    if not path.exists():
        return _empty_profile()

    total_md = 0
    organic_count = 0
    fixture_count = 0
    empty_td_count = 0

    decision_dist: Counter[str] = Counter()
    td_bands: Counter[str] = Counter()
    inp_presence: Counter[str] = Counter()
    inp_nonempty: Counter[str] = Counter()
    out_presence: Counter[str] = Counter()
    out_nonempty: Counter[str] = Counter()

    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                obj: Any = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            if obj.get("type") != "matcher_decision":
                continue

            total_md += 1
            is_organic = is_organic_entry(obj)

            if is_organic:
                organic_count += 1
            else:
                fixture_count += 1
                continue  # only profile organic entries in sub-field stats

            # task_description
            inp = obj.get("input") or {}
            td = inp.get("task_description", "") if isinstance(inp, dict) else ""
            if not td:
                empty_td_count += 1

            # input sub-field presence
            if isinstance(inp, dict):
                for k, v in inp.items():
                    inp_presence[k] += 1
                    if v:  # non-empty / non-null / non-zero
                        inp_nonempty[k] += 1

            # output sub-field presence
            out = obj.get("output") or {}
            if isinstance(out, dict):
                for k, v in out.items():
                    out_presence[k] += 1
                    if v is not None and v != "" and v != [] and v != {}:
                        out_nonempty[k] += 1

            # decision
            decision = out.get("decision", "MISSING") if isinstance(out, dict) else "MISSING"
            decision_dist[str(decision)] += 1

            # td length band
            td_bands[td_length_band(td)] += 1

    n = organic_count if organic_count > 0 else 1  # avoid /0

    # Build per-field presence dicts
    all_input_fields = set(_KNOWN_INPUT_FIELDS) | set(inp_presence.keys())
    all_output_fields = set(_KNOWN_OUTPUT_FIELDS) | set(out_presence.keys())

    input_field_presence = {
        field: {
            "count": inp_presence.get(field, 0),
            "rate": inp_presence.get(field, 0) / n,
            "nonempty_count": inp_nonempty.get(field, 0),
        }
        for field in sorted(all_input_fields)
    }

    output_field_presence = {
        field: {
            "count": out_presence.get(field, 0),
            "rate": out_presence.get(field, 0) / n,
            "nonempty_count": out_nonempty.get(field, 0),
        }
        for field in sorted(all_output_fields)
    }

    # Flagged fields: 0% or near-empty population in organic entries
    flagged_fields = _compute_flagged_fields(
        input_field_presence, output_field_presence, organic_count
    )

    return {
        "total_matcher_decision": total_md,
        "organic_count": organic_count,
        "fixture_count": fixture_count,
        "empty_task_description_count": empty_td_count,
        "decision_distribution": dict(decision_dist),
        "td_length_bands": dict(td_bands),
        "input_field_presence": input_field_presence,
        "output_field_presence": output_field_presence,
        "flagged_fields": flagged_fields,
    }


def _empty_profile() -> dict[str, Any]:
    """Return a zero-value profile for missing/empty logs."""
    return {
        "total_matcher_decision": 0,
        "organic_count": 0,
        "fixture_count": 0,
        "empty_task_description_count": 0,
        "decision_distribution": {},
        "td_length_bands": {},
        "input_field_presence": {},
        "output_field_presence": {},
        "flagged_fields": [],
    }


def _compute_flagged_fields(
    input_fp: dict[str, dict[str, Any]],
    output_fp: dict[str, dict[str, Any]],
    organic_count: int,
) -> list[dict[str, Any]]:
    """Return flagged-field records for fields with 0% or near-empty POPULATED rate.

    Flagging is based on ``nonempty_count / organic_count`` (the populated rate),
    NOT on key-presence rate.  A field that is always present but always empty
    (e.g. ``lanes: []``, ``command_prefix: ""``) has presence_rate=1.0 but
    populated_rate=0.0 — it must be flagged.

    Args:
        input_fp:      input sub-field presence dict (field → {count, rate,
                       nonempty_count}).
        output_fp:     output sub-field presence dict.
        organic_count: Total organic entry count (denominator for populated rate).

    Returns:
        List of {field, presence_count, presence_rate, nonempty_count,
        populated_rate, reason} dicts, sorted by populated_rate ascending.
    """
    flagged: list[dict[str, Any]] = []
    n = organic_count if organic_count > 0 else 1  # guard against /0

    for prefix, fp in (("input", input_fp), ("output", output_fp)):
        for field, info in fp.items():
            nonempty = info.get("nonempty_count", 0)
            populated_rate = nonempty / n
            qualified = f"{prefix}.{field}"
            if populated_rate == 0.0:
                flagged.append({
                    "field": qualified,
                    "presence_count": info["count"],
                    "presence_rate": info["rate"],
                    "nonempty_count": nonempty,
                    "populated_rate": populated_rate,
                    "reason": "100% empty / never populated in organic entries",
                })
            elif populated_rate < NEAR_EMPTY_THRESHOLD:
                flagged.append({
                    "field": qualified,
                    "presence_count": info["count"],
                    "presence_rate": info["rate"],
                    "nonempty_count": nonempty,
                    "populated_rate": populated_rate,
                    "reason": (
                        f"near-empty: {populated_rate * 100:.1f}% populated"
                        f" < {NEAR_EMPTY_THRESHOLD * 100:.0f}% threshold"
                    ),
                })

    flagged.sort(key=lambda x: x["populated_rate"])
    return flagged
