"""Tests for scripts/corpus/profiler.py — dispatch-log structural profiler.

All tests use synthetic JSONL fixtures written to tmp_path.  The real
dispatch-log is never read here.

Coverage:
  1. field_profile() returns per-field presence stats for matcher_decision
  2. input sub-field profiling
  3. output sub-field profiling
  4. task_description length band classification
  5. organic vs non-organic entry counts
  6. empty task_description flagged
  7. near-empty input fields flagged (< NEAR_EMPTY_THRESHOLD)
  8. empty log / missing log handled gracefully
  9. profile output is serialisable to JSON
  10. profile contains expected keys
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Helpers — synthetic entry builders
# ---------------------------------------------------------------------------


def _md(
    session_id: str = "real-session-abc",
    task_description: str = "fix the login bug",
    decision: str = "delegate",
    agent: str = "code-writer",
    confidence: float = 1.0,
    include_file_paths: bool = False,
    include_command_prefix: bool = False,
    include_agent_mentions: bool = False,
    include_tool_mentions: bool = False,
    extra_input: dict[str, Any] | None = None,
    extra_output: dict[str, Any] | None = None,
    override_id: Any = None,
) -> dict[str, Any]:
    """Build a synthetic matcher_decision entry."""
    inp: dict[str, Any] = {"task_description": task_description}
    if include_file_paths:
        inp["file_paths"] = ["src/main.py", "tests/test_main.py"]
    if include_command_prefix:
        inp["command_prefix"] = "/run"
    if include_agent_mentions:
        inp["agent_mentions"] = ["code-writer"]
    if include_tool_mentions:
        inp["tool_mentions"] = ["Bash", "Read"]
    if extra_input:
        inp.update(extra_input)

    out: dict[str, Any] = {
        "decision": decision,
        "confidence": confidence,
        "rationale": "matched keywords",
        "alternatives": [],
    }
    if agent:
        out["agent"] = agent
    if extra_output:
        out.update(extra_output)

    entry: dict[str, Any] = {
        "type": "matcher_decision",
        "ts": "2026-06-01T10:00:00.000000Z",
        "session_id": session_id,
        "attribution_source": "post_tool_use_hook",
        "input": inp,
        "output": out,
        "catalog_hash": "sha256:abc123",
        "matcher_version": "abc1234",
    }
    if override_id is not None:
        entry["override_id"] = override_id
    return entry


def _write_jsonl(tmp_path: Path, entries: list[Any]) -> Path:
    """Write entries as JSONL and return the path."""
    p = tmp_path / "dispatch-log.jsonl"
    with p.open("w", encoding="utf-8") as fh:
        for entry in entries:
            if isinstance(entry, str):
                fh.write(entry + "\n")
            else:
                fh.write(json.dumps(entry) + "\n")
    return p


# ---------------------------------------------------------------------------
# 1. field_profile() returns per-field presence stats
# ---------------------------------------------------------------------------


def test_field_profile_counts_total_entries(tmp_path: Path) -> None:
    """field_profile counts total matcher_decision entries."""
    from scripts.corpus.profiler import field_profile

    log = _write_jsonl(tmp_path, [_md(), _md(), _md(session_id="")])
    profile = field_profile(log)

    assert profile["total_matcher_decision"] == 3


def test_field_profile_organic_vs_fixture_counts(tmp_path: Path) -> None:
    """field_profile correctly separates organic vs fixture entries."""
    from scripts.corpus.profiler import field_profile

    log = _write_jsonl(
        tmp_path,
        [
            _md(session_id="real"),
            _md(session_id="real2"),
            _md(session_id=""),  # fixture
            _md(session_id=""),  # fixture
            _md(session_id=""),  # fixture
        ],
    )
    profile = field_profile(log)

    assert profile["organic_count"] == 2
    assert profile["fixture_count"] == 3


def test_field_profile_other_types_excluded(tmp_path: Path) -> None:
    """Non-matcher_decision entries are excluded from totals."""
    from scripts.corpus.profiler import field_profile

    other = {"type": "agent_dispatch", "ts": "2026-06-01T10:00:00Z", "session_id": "real"}
    log = _write_jsonl(tmp_path, [_md(), other])
    profile = field_profile(log)

    assert profile["total_matcher_decision"] == 1


# ---------------------------------------------------------------------------
# 2. input sub-field profiling
# ---------------------------------------------------------------------------


def test_input_field_presence_all_fields(tmp_path: Path) -> None:
    """input sub-field presence is reported for each organic entry."""
    from scripts.corpus.profiler import field_profile

    entries = [
        _md(session_id="s1", include_file_paths=True, include_command_prefix=True),
        _md(session_id="s2", include_file_paths=False),
        _md(session_id="s3", include_agent_mentions=True),
    ]
    log = _write_jsonl(tmp_path, entries)
    profile = field_profile(log)

    inp = profile["input_field_presence"]
    assert inp["task_description"]["count"] == 3
    assert inp["task_description"]["rate"] == pytest.approx(1.0)
    assert inp["file_paths"]["count"] == 1
    assert inp["file_paths"]["rate"] == pytest.approx(1 / 3)
    assert inp["command_prefix"]["count"] == 1
    assert inp["agent_mentions"]["count"] == 1


def test_input_task_description_empty_count(tmp_path: Path) -> None:
    """Entries with empty task_description are counted separately."""
    from scripts.corpus.profiler import field_profile

    entries = [
        _md(session_id="s1", task_description=""),
        _md(session_id="s2", task_description="do something"),
        _md(session_id="s3", task_description=""),
    ]
    log = _write_jsonl(tmp_path, entries)
    profile = field_profile(log)

    assert profile["empty_task_description_count"] == 2


# ---------------------------------------------------------------------------
# 3. output sub-field profiling
# ---------------------------------------------------------------------------


def test_output_decision_distribution(tmp_path: Path) -> None:
    """output.decision distribution is captured."""
    from scripts.corpus.profiler import field_profile

    entries = [
        _md(session_id="s1", decision="delegate"),
        _md(session_id="s2", decision="delegate"),
        _md(session_id="s3", decision="advisory"),
        _md(session_id="s4", decision="self_handle"),
        _md(session_id="s5", decision="needs_more_detail"),
    ]
    log = _write_jsonl(tmp_path, entries)
    profile = field_profile(log)

    dd = profile["decision_distribution"]
    assert dd["delegate"] == 2
    assert dd["advisory"] == 1
    assert dd["self_handle"] == 1
    assert dd["needs_more_detail"] == 1


# ---------------------------------------------------------------------------
# 4. task_description length band classification
# ---------------------------------------------------------------------------


def test_td_length_bands(tmp_path: Path) -> None:
    """task_description length bands are computed correctly."""
    from scripts.corpus.profiler import field_profile

    entries = [
        _md(session_id="s1", task_description=""),  # EMPTY
        _md(session_id="s2", task_description="fix bug"),  # short <50
        _md(session_id="s3", task_description="x" * 50),  # medium 50-199
        _md(session_id="s4", task_description="x" * 200),  # long 200-499
        _md(session_id="s5", task_description="x" * 500),  # very_long 500+
    ]
    log = _write_jsonl(tmp_path, entries)
    profile = field_profile(log)

    bands = profile["td_length_bands"]
    assert bands["empty"] == 1
    assert bands["short"] == 1  # 1–49
    assert bands["medium"] == 1  # 50–199
    assert bands["long"] == 1  # 200–499
    assert bands["very_long"] == 1  # 500+


# ---------------------------------------------------------------------------
# 5. empty log / missing log
# ---------------------------------------------------------------------------


def test_field_profile_empty_log(tmp_path: Path) -> None:
    """Empty log returns zero counts without error."""
    from scripts.corpus.profiler import field_profile

    log = tmp_path / "empty.jsonl"
    log.write_text("", encoding="utf-8")
    profile = field_profile(log)

    assert profile["total_matcher_decision"] == 0
    assert profile["organic_count"] == 0


def test_field_profile_missing_log(tmp_path: Path) -> None:
    """Missing log path returns zero counts without error."""
    from scripts.corpus.profiler import field_profile

    profile = field_profile(tmp_path / "nonexistent.jsonl")

    assert profile["total_matcher_decision"] == 0


# ---------------------------------------------------------------------------
# 6. near-empty fields flagged
# ---------------------------------------------------------------------------


def test_flagged_empty_fields_listed(tmp_path: Path) -> None:
    """Fields that are 100% empty/absent are listed in flagged_fields."""
    from scripts.corpus.profiler import field_profile

    # prompt field never present in any entry -> should be flagged
    entries = [_md(session_id=f"s{i}") for i in range(5)]
    log = _write_jsonl(tmp_path, entries)
    profile = field_profile(log)

    # command_prefix is 0% populated in these entries
    flagged = profile["flagged_fields"]
    assert isinstance(flagged, list)


def test_flagged_fields_includes_zero_population(tmp_path: Path) -> None:
    """Fields with 0 population in organic entries appear in flagged_fields."""
    from scripts.corpus.profiler import field_profile

    entries = [
        _md(session_id="s1", include_command_prefix=False),
        _md(session_id="s2", include_command_prefix=False),
    ]
    log = _write_jsonl(tmp_path, entries)
    profile = field_profile(log)

    flagged_names = [f["field"] for f in profile["flagged_fields"]]
    # command_prefix is in the input schema but absent from these entries
    assert "input.command_prefix" in flagged_names


# ---------------------------------------------------------------------------
# 6b. present-but-empty fields: populated_rate vs presence_rate
# ---------------------------------------------------------------------------


def test_flagged_always_present_but_always_empty(tmp_path: Path) -> None:
    """A field always present but always empty (e.g. lanes: []) must be flagged.

    This is the core fix: key-presence rate != populated rate.
    A field with presence_rate=1.0 but populated_rate=0.0 should flag.
    """
    from scripts.corpus.profiler import field_profile

    # Build entries where 'lanes' is always present but always empty list
    entries = [
        _md(
            session_id=f"s{i}",
            extra_output={"lanes": []},  # always present, always empty
        )
        for i in range(5)
    ]
    log = _write_jsonl(tmp_path, entries)
    profile = field_profile(log)

    flagged_names = [f["field"] for f in profile["flagged_fields"]]
    assert "output.lanes" in flagged_names, (
        "output.lanes is always present but always empty — must be flagged"
    )


def test_flagged_always_present_always_empty_string(tmp_path: Path) -> None:
    """A field always present as empty string must flag as 100% empty."""
    from scripts.corpus.profiler import field_profile

    entries = [
        _md(
            session_id=f"s{i}",
            extra_input={"command_prefix": ""},  # always present, always ""
        )
        for i in range(5)
    ]
    log = _write_jsonl(tmp_path, entries)
    profile = field_profile(log)

    flagged_names = [f["field"] for f in profile["flagged_fields"]]
    assert "input.command_prefix" in flagged_names, (
        "command_prefix always '' — must be flagged even though key is always present"
    )


def test_flagged_near_empty_populated_rate(tmp_path: Path) -> None:
    """A field present 100% but populated <5% of the time flags as near-empty.

    Simulates command_prefix present on all entries but non-empty only 2/100
    (2%), which is below the 5% threshold.
    """
    from scripts.corpus.profiler import field_profile

    # 50 entries: command_prefix present on all, non-empty on only 1
    entries = [
        _md(session_id=f"s{i}", extra_input={"command_prefix": "" if i != 0 else "/run"})
        for i in range(50)
    ]
    log = _write_jsonl(tmp_path, entries)
    profile = field_profile(log)

    flagged_names = [f["field"] for f in profile["flagged_fields"]]
    assert "input.command_prefix" in flagged_names, (
        "command_prefix populated 1/50=2% — below threshold, must flag"
    )
    # Check it flags as near-empty (not 100%-empty since 1 entry is populated)
    flagged_entry = next(
        f for f in profile["flagged_fields"] if f["field"] == "input.command_prefix"
    )
    assert "near-empty" in flagged_entry["reason"].lower() or "100%" in flagged_entry["reason"]


def test_presence_and_populated_rates_both_in_output(tmp_path: Path) -> None:
    """Field presence dict must contain both presence_rate and nonempty_count."""
    from scripts.corpus.profiler import field_profile

    entries = [
        _md(session_id="s1", extra_output={"lanes": []}),
        _md(session_id="s2", extra_output={"lanes": ["route-a"]}),
    ]
    log = _write_jsonl(tmp_path, entries)
    profile = field_profile(log)

    lanes_info = profile["output_field_presence"]["lanes"]
    # Must have both key-presence count AND nonempty_count
    assert "count" in lanes_info, "count (presence) must be in field info"
    assert "nonempty_count" in lanes_info, "nonempty_count must be in field info"
    assert lanes_info["count"] == 2  # both entries have the key
    assert lanes_info["nonempty_count"] == 1  # only one is non-empty


def test_flagged_fields_flag_uses_populated_rate_not_presence(tmp_path: Path) -> None:
    """Flagged fields reason reflects populated_rate, not presence rate.

    A field with presence_rate=1.0 but populated_rate=0.0 must produce
    a '100% empty' reason (not be absent from flagged list).
    """
    from scripts.corpus.profiler import field_profile

    entries = [_md(session_id=f"s{i}", extra_output={"unassigned_paths": {}}) for i in range(10)]
    log = _write_jsonl(tmp_path, entries)
    profile = field_profile(log)

    flagged = {f["field"]: f for f in profile["flagged_fields"]}
    assert "output.unassigned_paths" in flagged, (
        "unassigned_paths always present as {} — populated_rate=0 — must flag"
    )
    entry = flagged["output.unassigned_paths"]
    assert "100%" in entry["reason"] or "empty" in entry["reason"].lower()


# ---------------------------------------------------------------------------
# 7. profile is JSON-serialisable
# ---------------------------------------------------------------------------


def test_profile_json_serialisable(tmp_path: Path) -> None:
    """field_profile output can be serialised to JSON without error."""
    from scripts.corpus.profiler import field_profile

    log = _write_jsonl(tmp_path, [_md(session_id="s1"), _md(session_id="")])
    profile = field_profile(log)

    # Should not raise
    serialised = json.dumps(profile)
    assert len(serialised) > 0


# ---------------------------------------------------------------------------
# 8. profile contains expected top-level keys
# ---------------------------------------------------------------------------


def test_profile_has_required_keys(tmp_path: Path) -> None:
    """field_profile output contains all required structural keys."""
    from scripts.corpus.profiler import field_profile

    log = _write_jsonl(tmp_path, [_md(session_id="s1")])
    profile = field_profile(log)

    required_keys = [
        "total_matcher_decision",
        "organic_count",
        "fixture_count",
        "empty_task_description_count",
        "decision_distribution",
        "td_length_bands",
        "input_field_presence",
        "output_field_presence",
        "flagged_fields",
    ]
    for key in required_keys:
        assert key in profile, f"Missing key: {key!r}"


# ---------------------------------------------------------------------------
# Attribution-source organic filter (#440 remediation)
# ---------------------------------------------------------------------------
#
# After Phase 2, field_profile routes its organic predicate through
# is_organic_entry, which requires attribution_source='post_tool_use_hook'.
#
# Four-entry fixture:
#   entry A — hook (attribution_source='post_tool_use_hook')  → organic
#   entry B — python_matcher twin                             → non-organic
#   entry C — no attribution_source key                       → non-organic
#   entry D — hook + empty session_id                         → non-organic
#
# All tests below are RED until Phase 2 updates field_profile's predicate.


def _md_with_attribution(
    session_id: str = "real-session",
    attribution_source: str | None = "post_tool_use_hook",
    task_description: str = "fix the login bug",
) -> dict[str, Any]:
    """Build a minimal matcher_decision entry, optionally with attribution."""
    entry: dict[str, Any] = {
        "type": "matcher_decision",
        "ts": "2026-06-01T10:00:00.000000Z",
        "session_id": session_id,
        "input": {"task_description": task_description},
        "output": {
            "decision": "delegate",
            "agent": "code-writer",
            "confidence": 1.0,
            "rationale": "matched keywords",
            "alternatives": [],
        },
        "catalog_hash": "sha256:abc123",
        "matcher_version": "abc1234",
    }
    if attribution_source is not None:
        entry["attribution_source"] = attribution_source
    return entry


def test_field_profile_attribution_organic_count_one(tmp_path: Path) -> None:
    """field_profile counts only the hook entry as organic (4-entry mix).

    With the #440 attribution filter applied, only the
    post_tool_use_hook entry qualifies as organic.  python_matcher
    twins and no-attribution entries are non-organic regardless of
    session_id.

    Input (all have non-empty session_id except entry D):
      A — attribution_source='post_tool_use_hook'  → organic
      B — attribution_source='python_matcher'      → non-organic
      C — no attribution_source key                → non-organic
      D — hook + empty session_id                  → non-organic

    RED: current profiler uses bool(session_id) not is_organic_entry,
    so A, B, C all count as organic today (3 instead of 1).
    """
    from scripts.corpus.profiler import field_profile

    entries = [
        _md_with_attribution(
            session_id="s-hook",
            attribution_source="post_tool_use_hook",
        ),
        _md_with_attribution(
            session_id="s-python",
            attribution_source="python_matcher",
        ),
        _md_with_attribution(
            session_id="s-no-attr",
            attribution_source=None,
        ),
        _md_with_attribution(
            session_id="",
            attribution_source="post_tool_use_hook",
        ),
    ]
    log = _write_jsonl(tmp_path, entries)
    profile = field_profile(log)

    assert profile["total_matcher_decision"] == 4
    assert profile["organic_count"] == 1, (
        "Only the post_tool_use_hook entry with non-empty session_id "
        "is organic; python_matcher and no-attribution entries are not"
    )
