"""Tests for scripts/analyze-drift-causes.py."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# Load the analyzer module from the script path (since it lives in scripts/
# and is not a package).
_SCRIPT_PATH = Path(__file__).parent.parent / "scripts" / "analyze-drift-causes.py"


@pytest.fixture(scope="module")
def analyzer():
    """Load analyze-drift-causes.py as a module.

    Registers the module in sys.modules before exec_module so that
    Python 3.12's dataclasses can resolve the module's global namespace
    when processing string annotations on the Event dataclass.
    """
    spec = importlib.util.spec_from_file_location("analyze_drift_causes", _SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # Must be in sys.modules before exec_module so dataclasses can look up
    # the module dict via sys.modules.get(cls.__module__).
    sys.modules["analyze_drift_causes"] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        sys.modules.pop("analyze_drift_causes", None)
        raise
    return mod


def _now_iso(offset_days: int = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=offset_days)).isoformat()


def write_fixture(path: Path, events: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")


def test_each_cause_appears_in_distribution(tmp_path, analyzer, capsys):
    fixture = tmp_path / "drift.jsonl"
    write_fixture(
        fixture,
        [
            {
                "type": "router_drift",
                "ts": _now_iso(0),
                "category": "skill_mediated",
                "bypass_cause": "skill_mediated_interactive",
                "bypass_signals": {
                    "subagent_type": "code-writer",
                    "dispatch_skill_called_recently": False,
                    "count_agent_since_dispatch": None,
                    "last_skill_call_name": "gh-create-issue",
                    "last_skill_call_is_interactive": True,
                    "turns_since_user_message": 0,
                },
            },
            {
                "type": "router_drift",
                "ts": _now_iso(1),
                "category": "bypass",
                "bypass_cause": "router_direct_no_dispatch",
                "bypass_signals": {
                    "subagent_type": "doc-writer",
                    "dispatch_skill_called_recently": False,
                    "count_agent_since_dispatch": None,
                    "last_skill_call_name": None,
                    "last_skill_call_is_interactive": False,
                    "turns_since_user_message": 0,
                },
            },
            {
                "type": "router_drift",
                "ts": _now_iso(2),
                "category": "bypass",
                "bypass_cause": "router_direct_after_consumed_dispatch",
                "bypass_signals": {
                    "subagent_type": "ops",
                    "dispatch_skill_called_recently": True,
                    "count_agent_since_dispatch": 1,
                    "last_skill_call_name": None,
                    "last_skill_call_is_interactive": False,
                    "turns_since_user_message": 0,
                },
            },
            {
                "type": "router_drift",
                "ts": _now_iso(3),
                "category": "stale_dispatch",
                "bypass_cause": "stale_dispatch",
                "bypass_signals": {
                    "subagent_type": "ops",
                    "dispatch_skill_called_recently": True,
                    "count_agent_since_dispatch": 0,
                    "last_skill_call_name": None,
                    "last_skill_call_is_interactive": False,
                    "turns_since_user_message": 0,
                },
            },
        ],
    )

    rc = analyzer.main(["--drift-path", str(fixture), "--days", "7"])
    assert rc == 0
    out = capsys.readouterr().out
    for cause in [
        "skill_mediated_interactive",
        "router_direct_no_dispatch",
        "router_direct_after_consumed_dispatch",
        "stale_dispatch",
    ]:
        assert cause in out, f"Missing cause in output: {cause}"


def test_malformed_events_skipped(tmp_path, analyzer, capsys):
    fixture = tmp_path / "drift.jsonl"
    fixture.write_text(
        "not json at all\n"
        + json.dumps(
            {
                "type": "router_drift",
                "ts": _now_iso(),
                "category": "bypass",
                "bypass_cause": "router_direct_no_dispatch",
                "bypass_signals": {"subagent_type": "x"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    rc = analyzer.main(["--drift-path", str(fixture)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "router_direct_no_dispatch" in out
    assert "1 enriched events" in out


def test_pre_enrichment_baseline_counted_separately(tmp_path, analyzer, capsys):
    fixture = tmp_path / "drift.jsonl"
    write_fixture(
        fixture,
        [
            {"type": "router_drift", "ts": _now_iso(), "category": "bypass"},
            {
                "type": "router_drift",
                "ts": _now_iso(),
                "category": "bypass",
                "bypass_cause": "router_direct_no_dispatch",
                "bypass_signals": {"subagent_type": "x"},
            },
        ],
    )
    rc = analyzer.main(["--drift-path", str(fixture)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "1 enriched events; 1 pre-enrichment baseline" in out


def test_disagreements_flag_lists_mismatches(tmp_path, analyzer, capsys):
    fixture = tmp_path / "drift.jsonl"
    write_fixture(
        fixture,
        [
            {
                "type": "router_drift",
                "ts": _now_iso(),
                "category": "bypass",
                "bypass_cause": "skill_mediated_interactive",  # wrong on purpose
                "bypass_signals": {
                    "subagent_type": "x",
                    "dispatch_skill_called_recently": False,
                    "count_agent_since_dispatch": None,
                    "last_skill_call_name": None,
                    "last_skill_call_is_interactive": False,
                    "turns_since_user_message": 0,
                },
            }
        ],
    )
    rc = analyzer.main(["--drift-path", str(fixture), "--disagreements"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "1 events" in out
    assert "stored=skill_mediated_interactive" in out
    assert "derived=router_direct_no_dispatch" in out


def test_window_filtering_excludes_old_events(tmp_path, analyzer, capsys):
    fixture = tmp_path / "drift.jsonl"
    write_fixture(
        fixture,
        [
            {
                "type": "router_drift",
                "ts": _now_iso(30),
                "category": "bypass",
                "bypass_cause": "router_direct_no_dispatch",
                "bypass_signals": {"subagent_type": "x"},
            },
            {
                "type": "router_drift",
                "ts": _now_iso(1),
                "category": "bypass",
                "bypass_cause": "router_direct_no_dispatch",
                "bypass_signals": {"subagent_type": "y"},
            },
        ],
    )
    rc = analyzer.main(["--drift-path", str(fixture), "--days", "7"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "1 enriched events" in out


def test_json_output(tmp_path, analyzer, capsys):
    fixture = tmp_path / "drift.jsonl"
    write_fixture(
        fixture,
        [
            {
                "type": "router_drift",
                "ts": _now_iso(),
                "category": "bypass",
                "bypass_cause": "router_direct_no_dispatch",
                "bypass_signals": {"subagent_type": "x"},
            }
        ],
    )
    rc = analyzer.main(["--drift-path", str(fixture), "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["enriched_events"] == 1
    assert payload["distribution"][0]["cause"] == "router_direct_no_dispatch"
    assert payload["distribution"][0]["disposition"] == "unwanted"
