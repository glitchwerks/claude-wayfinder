"""Tests for scripts/shadow-summary.py."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_SCRIPT_PATH = Path(__file__).parent.parent.parent / "scripts" / "shadow-summary.py"


@pytest.fixture(scope="module")
def shadow_summary_module() -> ModuleType:
    """Load ``scripts/shadow-summary.py`` as a module.

    Returns:
        The loaded script module, exposing ``summarize`` and ``main``.
    """
    spec = importlib.util.spec_from_file_location("shadow_summary", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["shadow_summary"] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        sys.modules.pop("shadow_summary", None)
        raise
    return mod


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    """Write rows to a JSONL fixture file.

    Args:
        path: Destination JSONL path.
        rows: JSON-serializable rows to write.
    """
    path.write_text(
        "".join(f"{json.dumps(row)}\n" for row in rows),
        encoding="utf-8",
    )


def test_summarize_counts_served_arms_with_schema_v1_default(
    shadow_summary_module: ModuleType,
    tmp_path: Path,
) -> None:
    """Count explicit served arms and backfill schema-v1 records."""
    log_path = tmp_path / "dispatch-log.jsonl"
    _write_jsonl(
        log_path,
        [
            {"shadow": {"served_arm": "compose"}},
            {"shadow": {"served_arm": "lexical"}},
            {"shadow": {"served_arm": "experimental"}},
            # Schema v1 always served lexical, so a missing key must backfill
            # to lexical rather than being dropped from the served totals.
            {"shadow": {}},
        ],
    )

    result = shadow_summary_module.summarize(log_path)

    assert result["served"] == {"compose": 1, "lexical": 2, "experimental": 1}


def test_summarize_distinguishes_absent_and_empty_hard_routing_domains(
    shadow_summary_module: ModuleType,
    tmp_path: Path,
) -> None:
    """Record distinct present domain lists without counting absent keys."""
    log_path = tmp_path / "dispatch-log.jsonl"
    _write_jsonl(
        log_path,
        [
            {"shadow": {"hard_routing_domains": []}},
            {"shadow": {"hard_routing_domains": ["db", "infra"]}},
            {"shadow": {"hard_routing_domains": ["db", "infra"]}},
            {"shadow": {}},
        ],
    )

    result = shadow_summary_module.summarize(log_path)

    assert result["hard_routing_domains_seen"] == [[], ["db", "infra"]]


def test_summarize_does_not_default_absent_hard_routing_domains_to_empty(
    shadow_summary_module: ModuleType,
    tmp_path: Path,
) -> None:
    """Leave seen domains empty when all records predate the field."""
    log_path = tmp_path / "dispatch-log.jsonl"
    _write_jsonl(log_path, [{"shadow": {}}, {"shadow": {"agreement": True}}])

    result = shadow_summary_module.summarize(log_path)

    assert result["hard_routing_domains_seen"] == []


def test_summarize_preserves_existing_metrics_alongside_new_metrics(
    shadow_summary_module: ModuleType,
    tmp_path: Path,
) -> None:
    """Keep entries, shadow, agreement, and branch metrics unchanged."""
    log_path = tmp_path / "dispatch-log.jsonl"
    _write_jsonl(
        log_path,
        [
            {
                "shadow": {
                    "agreement": True,
                    "branch": "branch3_generic",
                    "served_arm": "compose",
                    "hard_routing_domains": ["infra"],
                }
            },
            {
                "shadow": {
                    "agreement": False,
                    "branch": "branch1_exact",
                    "served_arm": "lexical",
                    "hard_routing_domains": [],
                }
            },
            {"type": "non-shadow-entry"},
        ],
    )

    result = shadow_summary_module.summarize(log_path)

    assert result == {
        "entries": 3,
        "shadow": 2,
        "agreement": 1,
        "branches": {"branch3_generic": 1, "branch1_exact": 1},
        "served": {"compose": 1, "lexical": 1},
        "hard_routing_domains_seen": [[], ["infra"]],
    }


def test_main_human_output_reports_served_arms(
    shadow_summary_module: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Include deterministically ordered served-arm counts in CLI output."""
    log_path = tmp_path / "dispatch-log.jsonl"
    _write_jsonl(
        log_path,
        [
            {"shadow": {"served_arm": "lexical"}},
            {"shadow": {"served_arm": "compose"}},
        ],
    )
    monkeypatch.setattr(sys, "argv", ["shadow-summary.py", str(log_path)])

    shadow_summary_module.main()

    output = capsys.readouterr().out
    assert " served=compose=1 lexical=1" in output
    assert " hard_routing_domains=[]" in output
