"""Tests for ``claude_wayfinder._trigger_validators`` primitives.

All four exported primitives are covered:
    - ``is_weight_in_ladder``
    - ``clamp_weight_to_ladder``
    - ``has_whitespace``
    - ``count_trigger_dimensions``

Each test is named after the behavior it asserts.  Edge cases are
covered before the happy path so that failures point to the smallest
meaningful behavioural unit.
"""

from __future__ import annotations

from claude_wayfinder._trigger_validators import (
    WEIGHT_LADDER,
    clamp_weight_to_ladder,
    count_trigger_dimensions,
    has_whitespace,
    is_weight_in_ladder,
)

# ---------------------------------------------------------------------------
# WEIGHT_LADDER constant
# ---------------------------------------------------------------------------


class TestWeightLadder:
    """WEIGHT_LADDER must be a frozenset containing exactly {0.25, 0.5, 1.0}."""

    def test_contains_expected_values(self) -> None:
        assert WEIGHT_LADDER == frozenset({0.25, 0.5, 1.0})

    def test_is_frozenset(self) -> None:
        assert isinstance(WEIGHT_LADDER, frozenset)


# ---------------------------------------------------------------------------
# is_weight_in_ladder
# ---------------------------------------------------------------------------


class TestIsWeightInLadder:
    """is_weight_in_ladder(weight) -> bool."""

    # --- exact ladder members -----------------------------------------------

    def test_025_is_in_ladder(self) -> None:
        assert is_weight_in_ladder(0.25) is True

    def test_05_is_in_ladder(self) -> None:
        assert is_weight_in_ladder(0.5) is True

    def test_10_is_in_ladder(self) -> None:
        assert is_weight_in_ladder(1.0) is True

    # --- integer forms of ladder members ------------------------------------

    def test_integer_1_is_in_ladder(self) -> None:
        # int 1 equals float 1.0 in Python, should be in ladder
        assert is_weight_in_ladder(1) is True

    # --- values not in ladder but within [0.0, 1.0] -------------------------

    def test_024999_not_in_ladder(self) -> None:
        assert is_weight_in_ladder(0.24999) is False

    def test_05001_not_in_ladder(self) -> None:
        assert is_weight_in_ladder(0.5001) is False

    def test_0_not_in_ladder(self) -> None:
        assert is_weight_in_ladder(0.0) is False

    def test_075_not_in_ladder(self) -> None:
        assert is_weight_in_ladder(0.75) is False

    def test_033_not_in_ladder(self) -> None:
        assert is_weight_in_ladder(0.33) is False

    # --- values outside [0.0, 1.0] ------------------------------------------

    def test_negative_not_in_ladder(self) -> None:
        assert is_weight_in_ladder(-0.1) is False

    def test_above_one_not_in_ladder(self) -> None:
        assert is_weight_in_ladder(1.5) is False

    def test_large_positive_not_in_ladder(self) -> None:
        assert is_weight_in_ladder(100.0) is False

    # --- boolean rejection (bool is subclass of int) ------------------------

    def test_true_not_treated_as_1(self) -> None:
        # Callers in build_catalog explicitly guard against booleans before
        # calling the weight check; the primitive itself does NOT need to
        # reject booleans — it delegates that responsibility to the caller.
        # True == 1 == 1.0 in Python, so the primitive returns True for True.
        # This documents the expected behaviour so callers know to pre-filter.
        assert is_weight_in_ladder(True) is True  # 1 == 1.0 in ladder

    def test_false_not_in_ladder(self) -> None:
        # False == 0 which is not in {0.25, 0.5, 1.0}
        assert is_weight_in_ladder(False) is False


# ---------------------------------------------------------------------------
# clamp_weight_to_ladder
# ---------------------------------------------------------------------------


class TestClampWeightToLadder:
    """clamp_weight_to_ladder(weight) -> float.

    Returns the nearest ladder value.  Ties resolve to the *higher*
    value (e.g. 0.375 is equidistant between 0.25 and 0.5, resolves
    to 0.5; 0.75 equidistant between 0.5 and 1.0, resolves to 1.0).
    """

    # --- exact ladder members pass through ----------------------------------

    def test_025_stays_025(self) -> None:
        assert clamp_weight_to_ladder(0.25) == 0.25

    def test_05_stays_05(self) -> None:
        assert clamp_weight_to_ladder(0.5) == 0.5

    def test_10_stays_10(self) -> None:
        assert clamp_weight_to_ladder(1.0) == 1.0

    # --- nearest-neighbour --------------------------------------------------

    def test_030_clamps_to_025(self) -> None:
        # 0.30 is closer to 0.25 (dist 0.05) than to 0.5 (dist 0.20)
        assert clamp_weight_to_ladder(0.30) == 0.25

    def test_040_clamps_to_05(self) -> None:
        # 0.40 is closer to 0.5 (dist 0.10) than to 0.25 (dist 0.15)
        assert clamp_weight_to_ladder(0.40) == 0.5

    def test_080_clamps_to_10(self) -> None:
        # 0.80 is closer to 1.0 (dist 0.20) than to 0.5 (dist 0.30)
        assert clamp_weight_to_ladder(0.80) == 1.0

    # --- tie-breaking: equidistant goes to the higher value -----------------

    def test_0375_equidistant_clamps_to_05(self) -> None:
        # 0.375 is equidistant between 0.25 and 0.5; must resolve to 0.5
        assert clamp_weight_to_ladder(0.375) == 0.5

    def test_075_equidistant_clamps_to_10(self) -> None:
        # 0.75 is equidistant between 0.5 and 1.0; must resolve to 1.0
        assert clamp_weight_to_ladder(0.75) == 1.0

    # --- out-of-range values clamp to boundary members ----------------------

    def test_zero_clamps_to_025(self) -> None:
        # 0.0 is closest to 0.25
        assert clamp_weight_to_ladder(0.0) == 0.25

    def test_negative_clamps_to_025(self) -> None:
        # Anything below 0.25 is nearest to 0.25
        assert clamp_weight_to_ladder(-1.0) == 0.25

    def test_above_one_clamps_to_10(self) -> None:
        # Anything above 1.0 is nearest to 1.0
        assert clamp_weight_to_ladder(2.0) == 1.0

    # --- integer form ---------------------------------------------------

    def test_integer_1_returns_float_10(self) -> None:
        result = clamp_weight_to_ladder(1)
        assert result == 1.0
        assert isinstance(result, float)

    # --- return type is always float ----------------------------------------

    def test_return_type_is_float(self) -> None:
        assert isinstance(clamp_weight_to_ladder(0.5), float)
        assert isinstance(clamp_weight_to_ladder(0.25), float)
        assert isinstance(clamp_weight_to_ladder(1.0), float)


# ---------------------------------------------------------------------------
# has_whitespace
# ---------------------------------------------------------------------------


class TestHasWhitespace:
    """has_whitespace(term) -> bool.

    Returns True if the string contains any whitespace character as
    defined by str.isspace() — this includes space, tab, newline, etc.
    """

    # --- True cases ---------------------------------------------------------

    def test_space_in_middle(self) -> None:
        assert has_whitespace("hello world") is True

    def test_tab_in_term(self) -> None:
        assert has_whitespace("hello\tworld") is True

    def test_newline_in_term(self) -> None:
        assert has_whitespace("hello\nworld") is True

    def test_leading_space(self) -> None:
        assert has_whitespace(" hello") is True

    def test_trailing_space(self) -> None:
        assert has_whitespace("hello ") is True

    def test_whitespace_only(self) -> None:
        assert has_whitespace("   ") is True

    def test_single_space_char(self) -> None:
        assert has_whitespace(" ") is True

    # --- False cases --------------------------------------------------------

    def test_clean_single_token(self) -> None:
        assert has_whitespace("hello") is False

    def test_empty_string(self) -> None:
        # An empty string has no whitespace characters
        assert has_whitespace("") is False

    def test_underscores_not_whitespace(self) -> None:
        assert has_whitespace("hello_world") is False

    def test_hyphens_not_whitespace(self) -> None:
        assert has_whitespace("hello-world") is False

    def test_dots_not_whitespace(self) -> None:
        assert has_whitespace("hello.world") is False

    def test_unicode_letters_no_whitespace(self) -> None:
        assert has_whitespace("héllo") is False

    # --- non-breaking space (unicode whitespace) ----------------------------

    def test_non_breaking_space_is_whitespace(self) -> None:
        #   is a non-breaking space; str.isspace() returns True for it
        assert has_whitespace("hello world") is True


# ---------------------------------------------------------------------------
# count_trigger_dimensions
# ---------------------------------------------------------------------------


class TestCountTriggerDimensions:
    """count_trigger_dimensions(triggers) -> int.

    Counts populated positive trigger dimensions on a Triggers-like
    object.  A dimension is "populated" when its list is non-empty.

    The five dimensions checked are:
        command_prefixes, agent_mentions, path_globs, keywords, tool_mentions.
    """

    class _MockTriggers:
        """Minimal triggers stand-in for test isolation."""

        def __init__(
            self,
            *,
            command_prefixes: list = (),
            agent_mentions: list = (),
            path_globs: list = (),
            keywords: list = (),
            tool_mentions: list = (),
        ) -> None:
            self.command_prefixes = list(command_prefixes)
            self.agent_mentions = list(agent_mentions)
            self.path_globs = list(path_globs)
            self.keywords = list(keywords)
            self.tool_mentions = list(tool_mentions)

    # --- zero dimensions ----------------------------------------------------

    def test_all_empty_returns_zero(self) -> None:
        t = self._MockTriggers()
        assert count_trigger_dimensions(t) == 0

    # --- one dimension each -------------------------------------------------

    def test_command_prefixes_only(self) -> None:
        t = self._MockTriggers(command_prefixes=["/foo"])
        assert count_trigger_dimensions(t) == 1

    def test_agent_mentions_only(self) -> None:
        t = self._MockTriggers(agent_mentions=["my-agent"])
        assert count_trigger_dimensions(t) == 1

    def test_path_globs_only(self) -> None:
        t = self._MockTriggers(path_globs=["**/*.py"])
        assert count_trigger_dimensions(t) == 1

    def test_keywords_only(self) -> None:
        t = self._MockTriggers(keywords=[{"term": "deploy", "weight": 1.0}])
        assert count_trigger_dimensions(t) == 1

    def test_tool_mentions_only(self) -> None:
        t = self._MockTriggers(tool_mentions=["Bash"])
        assert count_trigger_dimensions(t) == 1

    # --- multiple dimensions ------------------------------------------------

    def test_two_dimensions(self) -> None:
        t = self._MockTriggers(keywords=[{"term": "x", "weight": 1.0}],
                               path_globs=["**/*.ts"])
        assert count_trigger_dimensions(t) == 2

    def test_all_five_dimensions(self) -> None:
        t = self._MockTriggers(
            command_prefixes=["/foo"],
            agent_mentions=["a"],
            path_globs=["*.py"],
            keywords=[{"term": "k", "weight": 0.5}],
            tool_mentions=["Bash"],
        )
        assert count_trigger_dimensions(t) == 5

    # --- list with multiple entries counts as one dimension -----------------

    def test_multiple_keywords_counts_as_one_dimension(self) -> None:
        t = self._MockTriggers(
            keywords=[
                {"term": "a", "weight": 1.0},
                {"term": "b", "weight": 0.5},
            ]
        )
        assert count_trigger_dimensions(t) == 1

    # --- empty list is not populated ----------------------------------------

    def test_empty_list_does_not_count(self) -> None:
        t = self._MockTriggers(
            command_prefixes=[],
            keywords=[{"term": "x", "weight": 1.0}],
        )
        assert count_trigger_dimensions(t) == 1
