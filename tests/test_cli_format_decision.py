"""Regression pin tests for _format_decision in claude_wayfinder.cli.

M15-4 (#421) audit confirmed that _format_decision is value-agnostic:
it prints disposition_source verbatim via an f-string and never branches
on its value. These tests pin that contract so any future change that
starts branching on the value (e.g. filtering out "posture_routed") will
break here immediately.

These tests are GREEN on write — they characterise existing behaviour and
are NOT red-TDD tests. The code already tolerates any string value for
disposition_source. Noted as required by the M15-3-4 contract (Part B).
"""

from __future__ import annotations

from claude_wayfinder.cli import _format_decision

# ---------------------------------------------------------------------------
# Minimal result dict helpers
# ---------------------------------------------------------------------------


def _make_result(disposition_source: str) -> dict:
    """Return the minimal result dict accepted by _format_decision.

    Args:
        disposition_source: The disposition_source string to embed.

    Returns:
        A dict carrying the four keys _format_decision requires.
    """
    return {
        "decision": "delegate",
        "confidence": 0.9,
        "rationale": "test rationale",
        "disposition_source": disposition_source,
    }


# ---------------------------------------------------------------------------
# M15-4 (#421) — disposition_source pin tests
# ---------------------------------------------------------------------------


class TestFormatDecisionDispositionSourcePin:
    """_format_decision prints disposition_source verbatim (M15-4, #421).

    GREEN on write — these are characterisation / regression-pin tests,
    not red-TDD. The implementation already satisfies them; they exist to
    catch any future refactor that starts branching on the value.
    """

    def test_posture_routed_prints_verbatim(self) -> None:
        """disposition_source="posture_routed" appears verbatim in output.

        Asserts the formatted string contains the line:
          "  disposition_source : posture_routed"
        exactly as the f-string renderer would produce it.
        """
        result = _make_result("posture_routed")
        output = _format_decision(result)
        assert "  disposition_source : posture_routed" in output, (
            f"Expected '  disposition_source : posture_routed' in output; "
            f"got:\n{output}"
        )

    def test_unknown_future_value_prints_verbatim(self) -> None:
        """An unknown future disposition_source value prints without branching.

        Passes disposition_source="something_new" (a value the code has
        never seen) and asserts it appears verbatim in the output.

        Pins the value-agnostic contract: if a future code change starts
        filtering or transforming unknown values, this test breaks.
        """
        result = _make_result("something_new")
        output = _format_decision(result)
        assert "  disposition_source : something_new" in output, (
            f"Expected '  disposition_source : something_new' in output; "
            f"got:\n{output}"
        )
