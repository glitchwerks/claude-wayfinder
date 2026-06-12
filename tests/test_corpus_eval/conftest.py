"""Shared fixtures for test_corpus_eval.

Builds P1-P14 fixture corpus + labels from the spike prompt set so
tests never touch the real corpus or live logs.

Corpus format (per docs/research/2026-06-12-corpus-manifest.json
format_spec):
  JSONL; one JSON object per line; fields: original log entry fields +
  corpus_id (int, 1-based) + stratum (dict).

Gold-label format (join file):
  JSONL; one JSON object per line; fields:
    corpus_id, domain, posture, gold_agent, is_any
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# P1-P14 corpus records — derived from spike prompt set + gold labels
# ---------------------------------------------------------------------------
# These are synthetic corpus entries serialized to the corpus format.
# They use the same prompts as tests/test_posture/conftest.py SPIKE_PROMPTS
# and tests/test_domain_encoder/conftest.py SPIKE_GOLD_FOR_EVAL.
# Gold labels assign gold_agent per spec §12.1 table.

_FIXTURE_RECORDS: list[dict[str, Any]] = [
    # P1 — verify happy path → auditor
    # Shape: real phase-A corpus format — original log entry fields + corpus_id
    # + stratum.  Dispatch-context fields are nested under "input" (as emitted
    # by the builder); "output" carries the matcher decision fields.
    {
        "type": "matcher_decision",
        "session_id": "session-test-001",
        "input": {
            "task_description": (
                "Make sure `db/schema.sql` is consistent with the migrations"
                " in `db/migrations/`."
            ),
            "file_paths": ["db/schema.sql", "db/migrations/"],
            "agent_mentions": [],
            "tool_mentions": [],
            "command_prefix": None,
        },
        "output": {
            "decision": "delegate",
            "agent": "auditor",
            "confidence": 0.9,
        },
        "corpus_id": 1,
        "stratum": {
            "decision_band": "delegate",
            "td_length_band": "short",
            "file_paths_present": True,
        },
    },
    # P2 — E5 pair-strictness miss → auditor (advisory by design)
    {
        "type": "matcher_decision",
        "session_id": "session-test-002",
        "input": {
            "task_description": (
                "Does the README still reflect how the build actually works?"
            ),
            "file_paths": [],
            "agent_mentions": [],
            "tool_mentions": [],
            "command_prefix": None,
        },
        "output": {
            "decision": "advisory",
            "agent": None,
            "confidence": 0.5,
        },
        "corpus_id": 2,
        "stratum": {
            "decision_band": "advisory",
            "td_length_band": "short",
            "file_paths_present": False,
        },
    },
    # P3 — prose-failure blind spot → investigator (advisory after R2)
    {
        "type": "matcher_decision",
        "session_id": "session-test-003",
        "input": {
            "task_description": (
                "The app crashes on startup and the config doesn't match what"
                " the docs say — figure out which is right."
            ),
            "file_paths": ["config/app.yaml", "docs/config.md"],
            "agent_mentions": [],
            "tool_mentions": [],
            "command_prefix": None,
        },
        "output": {
            "decision": "advisory",
            "agent": None,
            "confidence": 0.5,
        },
        "corpus_id": 3,
        "stratum": {
            "decision_band": "advisory",
            "td_length_band": "medium",
            "file_paths_present": True,
        },
    },
    # P4 — research happy path → researcher
    {
        "type": "matcher_decision",
        "session_id": "session-test-004",
        "input": {
            "task_description": (
                "I have an idea for caching dispatch results between sessions"
                " — has anyone built something like this?"
            ),
            "file_paths": [],
            "agent_mentions": [],
            "tool_mentions": [],
            "command_prefix": None,
        },
        "output": {
            "decision": "delegate",
            "agent": "researcher",
            "confidence": 0.9,
        },
        "corpus_id": 4,
        "stratum": {
            "decision_band": "delegate",
            "td_length_band": "short",
            "file_paths_present": False,
        },
    },
    # P5 — plan happy path → project-planner
    {
        "type": "matcher_decision",
        "session_id": "session-test-005",
        "input": {
            "task_description": (
                "We should add result caching to the matcher."
                " Lay out the phases and milestones to get there."
            ),
            "file_paths": [],
            "agent_mentions": [],
            "tool_mentions": [],
            "command_prefix": None,
        },
        "output": {
            "decision": "delegate",
            "agent": "project-planner",
            "confidence": 0.9,
        },
        "corpus_id": 5,
        "stratum": {
            "decision_band": "delegate",
            "td_length_band": "short",
            "file_paths_present": False,
        },
    },
    # P6 — ambiguous-by-design → approach-critic (advisory)
    {
        "type": "matcher_decision",
        "session_id": "session-test-006",
        "input": {
            "task_description": (
                "What if we cached the catalog in memory instead of re-reading"
                " it each call?"
            ),
            "file_paths": [],
            "agent_mentions": [],
            "tool_mentions": [],
            "command_prefix": None,
        },
        "output": {
            "decision": "advisory",
            "agent": None,
            "confidence": 0.5,
        },
        "corpus_id": 6,
        "stratum": {
            "decision_band": "advisory",
            "td_length_band": "short",
            "file_paths_present": False,
        },
    },
    # P7 — idea-critique happy path → approach-critic
    {
        "type": "matcher_decision",
        "session_id": "session-test-007",
        "input": {
            "task_description": (
                "Poke holes in this approach before I build it:"
                " store gold labels in issue bodies instead of a file."
            ),
            "file_paths": [],
            "agent_mentions": [],
            "tool_mentions": [],
            "command_prefix": None,
        },
        "output": {
            "decision": "delegate",
            "agent": "approach-critic",
            "confidence": 0.9,
        },
        "corpus_id": 7,
        "stratum": {
            "decision_band": "delegate",
            "td_length_band": "short",
            "file_paths_present": False,
        },
    },
    # P8 — frozen-set miss → inquisitor (advisory by design)
    {
        "type": "matcher_decision",
        "session_id": "session-test-008",
        "input": {
            "task_description": (
                "Tear apart the error handling in `src/matcher/engine.py`"
                " — I think it's too clever."
            ),
            "file_paths": ["src/matcher/engine.py"],
            "agent_mentions": [],
            "tool_mentions": [],
            "command_prefix": None,
        },
        "output": {
            "decision": "advisory",
            "agent": None,
            "confidence": 0.5,
        },
        "corpus_id": 8,
        "stratum": {
            "decision_band": "advisory",
            "td_length_band": "short",
            "file_paths_present": True,
        },
    },
    # P9 — assess/critique boundary → inquisitor (confident-wrong accepted)
    {
        "type": "matcher_decision",
        "session_id": "session-test-009",
        "input": {
            "task_description": (
                "Give PR #214 a really harsh review — don't go easy on it."
            ),
            "file_paths": [],
            "agent_mentions": [],
            "tool_mentions": [],
            "command_prefix": None,
        },
        "output": {
            "decision": "delegate",
            "agent": "code-reviewer",
            "confidence": 0.9,
        },
        "corpus_id": 9,
        "stratum": {
            "decision_band": "delegate",
            "td_length_band": "short",
            "file_paths_present": False,
        },
    },
    # P10 — prose variant § 8.2 → code-writer (advisory)
    {
        "type": "matcher_decision",
        "session_id": "session-test-010",
        "input": {
            "task_description": (
                "tests are failing after the rename, update them to match the"
                " new API."
            ),
            "file_paths": [],
            "agent_mentions": [],
            "tool_mentions": [],
            "command_prefix": None,
        },
        "output": {
            "decision": "advisory",
            "agent": None,
            "confidence": 0.5,
        },
        "corpus_id": 10,
        "stratum": {
            "decision_band": "advisory",
            "td_length_band": "short",
            "file_paths_present": False,
        },
    },
    # P11 — E6 happy flip → code-writer
    {
        "type": "matcher_decision",
        "session_id": "session-test-011",
        "input": {
            "task_description": (
                "Here's pytest: `FAILED tests/test_api.py::test_fetch -"
                " AttributeError: no attribute 'get_user'`. Started after we"
                " renamed get_user → fetch_user. Update the tests to match."
            ),
            "file_paths": [],
            "agent_mentions": [],
            "tool_mentions": [],
            "command_prefix": None,
        },
        "output": {
            "decision": "delegate",
            "agent": "code-writer",
            "confidence": 0.9,
        },
        "corpus_id": 11,
        "stratum": {
            "decision_band": "delegate",
            "td_length_band": "medium",
            "file_paths_present": False,
        },
    },
    # P12 — cross-layer deploy failure → investigator
    {
        "type": "matcher_decision",
        "session_id": "session-test-012",
        "input": {
            "task_description": (
                "The deploy fails every time — logs show `Error: ECONNREFUSED"
                " api.internal:443`. We changed the DNS config last week because"
                " the old provider was slow. Figure out why it fails."
            ),
            "file_paths": [],
            "agent_mentions": [],
            "tool_mentions": [],
            "command_prefix": None,
        },
        "output": {
            "decision": "delegate",
            "agent": "investigator",
            "confidence": 0.9,
        },
        "corpus_id": 12,
        "stratum": {
            "decision_band": "delegate",
            "td_length_band": "long",
            "file_paths_present": False,
        },
    },
    # P13 — operate control → ops
    {
        "type": "matcher_decision",
        "session_id": "session-test-013",
        "input": {
            "task_description": (
                "Run `gh pr checks 214` and summarize what's red."
            ),
            "file_paths": [],
            "agent_mentions": [],
            "tool_mentions": [],
            "command_prefix": "gh",
        },
        "output": {
            "decision": "delegate",
            "agent": "ops",
            "confidence": 0.9,
        },
        "corpus_id": 13,
        "stratum": {
            "decision_band": "delegate",
            "td_length_band": "short",
            "file_paths_present": False,
        },
    },
    # P14 — diagnose × span control → investigator
    {
        "type": "matcher_decision",
        "session_id": "session-test-014",
        "input": {
            "task_description": (
                "Getting this in CI: `Traceback (most recent call last)..."
                " ConnectionError` — happens only in the deploy workflow,"
                " never locally."
            ),
            "file_paths": [
                "src/api/client.py",
                ".github/workflows/deploy.yml",
            ],
            "agent_mentions": [],
            "tool_mentions": [],
            "command_prefix": None,
        },
        "output": {
            "decision": "delegate",
            "agent": "investigator",
            "confidence": 0.9,
        },
        "corpus_id": 14,
        "stratum": {
            "decision_band": "delegate",
            "td_length_band": "medium",
            "file_paths_present": True,
        },
    },
]


# Gold labels for P1-P14 (corpus_id → label dict)
# domain values from _eval.py SPIKE_GOLD_FOR_EVAL
# posture values from conftest.py SPIKE_PROMPTS (gold_agent → posture)
_GOLD_LABELS: list[dict[str, Any]] = [
    {
        "corpus_id": 1,
        "domain": "data",
        "posture": "verify",
        "gold_agent": "auditor",
        "is_any": False,
    },
    {
        "corpus_id": 2,
        "domain": "docs_prose",
        "posture": "verify",
        "gold_agent": "auditor",
        "is_any": False,
    },
    {
        "corpus_id": 3,
        "domain": "code",
        "posture": "diagnose",
        "gold_agent": "investigator",
        "is_any": True,
    },
    {
        "corpus_id": 4,
        "domain": "project_meta",
        "posture": "research",
        "gold_agent": "researcher",
        "is_any": True,
    },
    {
        "corpus_id": 5,
        "domain": "project_meta",
        "posture": "plan",
        "gold_agent": "project-planner",
        "is_any": False,
    },
    {
        "corpus_id": 6,
        "domain": "project_meta",
        "posture": "idea-critique",
        "gold_agent": "approach-critic",
        "is_any": True,
    },
    {
        "corpus_id": 7,
        "domain": "project_meta",
        "posture": "idea-critique",
        "gold_agent": "approach-critic",
        "is_any": True,
    },
    {
        "corpus_id": 8,
        "domain": "code",
        "posture": "critique",
        "gold_agent": "inquisitor",
        "is_any": False,
    },
    {
        "corpus_id": 9,
        "domain": "code",
        "posture": "critique",
        "gold_agent": "inquisitor",
        "is_any": True,
    },
    {
        "corpus_id": 10,
        "domain": "code",
        "posture": "build",
        "gold_agent": "code-writer",
        "is_any": False,
    },
    {
        "corpus_id": 11,
        "domain": "code",
        "posture": "build",
        "gold_agent": "code-writer",
        "is_any": False,
    },
    {
        "corpus_id": 12,
        "domain": "infra_deploy",
        "posture": "diagnose",
        "gold_agent": "investigator",
        "is_any": False,
    },
    {
        "corpus_id": 13,
        "domain": "infra_deploy",
        "posture": "operate",
        "gold_agent": "ops",
        "is_any": True,
    },
    {
        "corpus_id": 14,
        "domain": "infra_deploy",
        "posture": "diagnose",
        "gold_agent": "investigator",
        "is_any": False,
    },
]


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def fixture_corpus_path(tmp_path: Path) -> Path:
    """Write P1-P14 records as a corpus JSONL and return the path."""
    corpus_file = tmp_path / "fixture-corpus.jsonl"
    lines = [
        json.dumps(record, ensure_ascii=False) for record in _FIXTURE_RECORDS
    ]
    corpus_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return corpus_file


@pytest.fixture()
def fixture_labels_path(tmp_path: Path) -> Path:
    """Write P1-P14 gold labels as JSONL and return the path."""
    labels_file = tmp_path / "fixture-labels.jsonl"
    lines = [
        json.dumps(label, ensure_ascii=False) for label in _GOLD_LABELS
    ]
    labels_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return labels_file


@pytest.fixture()
def fixture_records() -> list[dict[str, Any]]:
    """Return the P1-P14 fixture records as a list."""
    return list(_FIXTURE_RECORDS)


@pytest.fixture()
def fixture_gold_labels() -> list[dict[str, Any]]:
    """Return the P1-P14 gold label dicts as a list."""
    return list(_GOLD_LABELS)
