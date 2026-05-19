"""Replay regression test for keyword_groups fixture.

Validates that spec § 7.1 worked examples produce the expected
dispatch decisions when run against the and_groups catalog fixture.
Each prompt is supplied with enough context (file_paths) to clear the
feature-density guard (v5 §3.1.3) and reach the scoring layer, where
keyword_groups influence is visible via confidence.

Spec: docs/superpowers/specs/2026-05-18-and-groups-design.md
Tracking: glitchwerks/claude-wayfinder#135
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from claude_wayfinder import _dispatch as _disp_mod

_FIXTURE_DIR = (
    Path(__file__).parent.parent
    / "src"
    / "claude_wayfinder"
    / "fixtures"
    / "and_groups"
)


def _load_prompts() -> list[dict[str, Any]]:
    """Load prompt fixture entries from prompts.json.

    Returns:
        List of dicts, each with keys: prompt, context_extra,
        expected_decision, expected_agent (or null),
        expected_confidence, and rationale.
    """
    return json.loads(
        (_FIXTURE_DIR / "prompts.json").read_text(encoding="utf-8")
    )


_PROMPTS = _load_prompts()


@pytest.mark.parametrize(
    "prompt_entry",
    _PROMPTS,
    ids=[e["prompt"] for e in _PROMPTS],
)
def test_and_groups_replay_case(prompt_entry: dict[str, Any]) -> None:
    """Each fixture prompt produces the expected decision, agent, and confidence.

    Merges optional ``context_extra`` fields (e.g. ``file_paths``) into the
    dispatch context so prompts clear the feature-density guard and reach the
    scoring layer where keyword_groups influence is visible.

    Args:
        prompt_entry: Dict from prompts.json with keys prompt,
            context_extra, expected_decision, expected_agent,
            expected_confidence, and rationale.
    """
    catalog_path = _FIXTURE_DIR / "catalog.json"
    context: dict[str, Any] = {"task_description": prompt_entry["prompt"]}
    context.update(prompt_entry.get("context_extra") or {})

    result = _disp_mod.dispatch(
        catalog_path=catalog_path,
        context=context,
    )

    assert result["decision"] == prompt_entry["expected_decision"], (
        f"Prompt: {prompt_entry['prompt']!r}\n"
        f"Expected decision: {prompt_entry['expected_decision']}\n"
        f"Got: {result['decision']}\n"
        f"Rationale (matcher): {result.get('rationale')}\n"
        f"Note (spec): {prompt_entry['rationale']}"
    )

    if prompt_entry.get("expected_agent") is not None:
        assert result.get("agent") == prompt_entry["expected_agent"], (
            f"Prompt: {prompt_entry['prompt']!r}\n"
            f"Expected agent: {prompt_entry['expected_agent']}\n"
            f"Got: {result.get('agent')}"
        )

    if prompt_entry.get("expected_confidence") is not None:
        assert result.get("confidence") == pytest.approx(
            prompt_entry["expected_confidence"], abs=0.05
        ), (
            f"Prompt: {prompt_entry['prompt']!r}\n"
            f"Expected confidence: {prompt_entry['expected_confidence']}\n"
            f"Got: {result.get('confidence')}\n"
            f"Note (spec): {prompt_entry['rationale']}"
        )
