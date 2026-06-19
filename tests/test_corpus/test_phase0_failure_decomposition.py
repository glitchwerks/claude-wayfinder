"""Tests for decompose_rc_misses in scripts/corpus/phase0_failure_decomposition.py.

Coverage:
  1. Self-handle abstention (decision="self_handle", gold_agent="self_handle")
     is NOT counted as an RC miss — the fix under test.
  2. Self-handle decision with a real gold_agent IS still counted as a miss.
  3. Real-agent mis-route (decision="delegate", wrong agent) IS a miss.
  4. Real-agent correct route is NOT a miss.
  5. Mixed batch: one correct abstention + one genuine miss → total_misses == 1.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Row factory
# ---------------------------------------------------------------------------


def _row(
    route_agent: str | None,
    gold_agent: str,
    decision: str,
    label_match_both: bool = True,
    domain_match: bool = True,
    posture_match: bool = True,
) -> dict[str, Any]:
    """Build a minimal joined-row dict matching build_joined_rows output.

    Args:
        route_agent: The agent the system routed to (None for self_handle).
        gold_agent: The correct gold agent label.
        decision: The system decision ("delegate" or "self_handle").
        label_match_both: True when domain AND posture axes both match.
        domain_match: True when the domain axis matches gold.
        posture_match: True when the posture axis matches gold.

    Returns:
        A dict with every key that decompose_rc_misses reads.
    """
    return {
        "corpus_id": 1,
        "task_description": "a task",
        "file_paths": [],
        "gpt_domain": "code",
        "gpt_posture": "fix",
        "gold_domain": "code",
        "gold_posture": "fix",
        "gold_agent": gold_agent,
        "route_agent": route_agent,
        "decision": decision,
        "confidence": 1.0,
        "domain_match": domain_match,
        "posture_match": posture_match,
        "label_match_both": label_match_both,
    }


# ---------------------------------------------------------------------------
# 1. Positive (the fix): correct self_handle abstention excluded from misses
# ---------------------------------------------------------------------------


def test_correct_self_handle_abstention_not_counted_as_miss() -> None:
    """A correctly-abstained self_handle row must not appear in total_misses.

    The bug: route_agent=None != gold_agent="self_handle" evaluates True,
    so current code incorrectly adds this row to misses.
    The fix gates misses on decision != "self_handle" when gold matches.
    """
    from scripts.corpus.phase0_failure_decomposition import decompose_rc_misses

    rows = [
        _row(
            route_agent=None,
            gold_agent="self_handle",
            decision="self_handle",
        )
    ]
    result = decompose_rc_misses(rows)

    assert result["total_misses"] == 0, (
        f"Expected 0 misses for a correct self_handle abstention, "
        f"got {result['total_misses']}"
    )


# ---------------------------------------------------------------------------
# 2. Negative: self_handle decision but real gold — still a miss
# ---------------------------------------------------------------------------


def test_self_handle_decision_with_real_gold_agent_is_a_miss() -> None:
    """When decision="self_handle" but gold_agent is a real agent, count as miss.

    The normalization must be precise: only exclude when BOTH decision AND
    gold_agent are "self_handle".
    """
    from scripts.corpus.phase0_failure_decomposition import decompose_rc_misses

    rows = [
        _row(
            route_agent=None,
            gold_agent="code-writer",
            decision="self_handle",
            label_match_both=False,
            domain_match=False,
            posture_match=False,
        )
    ]
    result = decompose_rc_misses(rows)

    assert result["total_misses"] == 1, (
        f"Expected 1 miss when self_handle was wrong (gold='code-writer'), "
        f"got {result['total_misses']}"
    )


# ---------------------------------------------------------------------------
# 3. Regression: real-agent mis-route is counted as a miss
# ---------------------------------------------------------------------------


def test_real_agent_misroute_is_counted_as_miss() -> None:
    """A delegate decision routed to the wrong agent must appear in total_misses."""
    from scripts.corpus.phase0_failure_decomposition import decompose_rc_misses

    rows = [
        _row(
            route_agent="code-writer",
            gold_agent="doc-writer",
            decision="delegate",
        )
    ]
    result = decompose_rc_misses(rows)

    assert result["total_misses"] == 1, (
        f"Expected 1 miss for a mis-routed delegate row, "
        f"got {result['total_misses']}"
    )


# ---------------------------------------------------------------------------
# 4. Regression: real-agent correct route is not a miss
# ---------------------------------------------------------------------------


def test_real_agent_correct_route_not_a_miss() -> None:
    """A delegate decision that matched gold_agent must NOT appear in total_misses."""
    from scripts.corpus.phase0_failure_decomposition import decompose_rc_misses

    rows = [
        _row(
            route_agent="code-writer",
            gold_agent="code-writer",
            decision="delegate",
        )
    ]
    result = decompose_rc_misses(rows)

    assert result["total_misses"] == 0, (
        f"Expected 0 misses for a correctly routed delegate row, "
        f"got {result['total_misses']}"
    )


# ---------------------------------------------------------------------------
# 5. Mixed batch: abstention + one genuine miss → total_misses == 1
# ---------------------------------------------------------------------------


def test_mixed_batch_self_handle_plus_real_miss_counts_one() -> None:
    """One correct self_handle abstention plus one real mis-route → total_misses == 1.

    The self_handle row must be excluded; the mis-route must be included.
    """
    from scripts.corpus.phase0_failure_decomposition import decompose_rc_misses

    rows = [
        _row(
            route_agent=None,
            gold_agent="self_handle",
            decision="self_handle",
        ),
        _row(
            route_agent="code-writer",
            gold_agent="doc-writer",
            decision="delegate",
        ),
    ]
    result = decompose_rc_misses(rows)

    assert result["total_misses"] == 1, (
        f"Expected 1 miss (self_handle abstention excluded, mis-route counted), "
        f"got {result['total_misses']}"
    )
    # Verify the miss row is the real mis-route, not the abstention
    assert len(result["miss_rows"]) == 1
    assert result["miss_rows"][0]["route_agent"] == "code-writer"
