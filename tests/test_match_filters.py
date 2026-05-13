"""Tests for claude_wayfinder/match_filters.py.

Covers the ``is_agent_routable`` predicate introduced in Pass 2.5.
Each test validates one exclusion rule or inclusion rule, as specified
in issue #477 §"Shared predicate module".

Finding #1 (PR #487): the predicate was updated from ``dict[str, Any]``
to three named keyword args (``name``, ``kind``, ``source``) to eliminate
per-entry dict allocations in the scoring loop.  Tests updated accordingly.
"""

from __future__ import annotations


class TestIsAgentRoutable:
    """Unit tests for ``is_agent_routable(*, name, kind, source)``."""

    def test_general_purpose_is_excluded(self) -> None:
        """The router agent must always be excluded from scoring."""
        from claude_wayfinder.match_filters import is_agent_routable

        assert is_agent_routable(name="general-purpose", kind="agent", source="owned") is False

    def test_plugin_agent_is_inert_by_default(self) -> None:
        """Plugin agents (source='plugin') are excluded from routing."""
        from claude_wayfinder.match_filters import is_agent_routable

        assert is_agent_routable(name="some-plugin-agent", kind="agent", source="plugin") is False

    def test_owned_agent_is_routable(self) -> None:
        """An owned agent that is not general-purpose must be routable."""
        from claude_wayfinder.match_filters import is_agent_routable

        assert is_agent_routable(name="code-writer", kind="agent", source="owned") is True

    def test_project_agent_is_routable(self) -> None:
        """A project-scoped agent must be routable (not filtered out)."""
        from claude_wayfinder.match_filters import is_agent_routable

        assert is_agent_routable(name="my-project-agent", kind="agent", source="project") is True

    def test_plugin_skill_is_not_excluded(self) -> None:
        """Skills with source='plugin' are NOT filtered by this predicate.

        The predicate guards the *agent* pool only.  A plugin skill with
        kind='skill' and source='plugin' must return True so the matcher
        can include it in the skill scoring pool.  (Skills are dormant
        because they have zero triggers, not because the predicate
        excluded them.)
        """
        from claude_wayfinder.match_filters import is_agent_routable

        # kind='skill' + source='plugin' → predicate does not exclude
        assert (
            is_agent_routable(name="superpowers:brainstorming", kind="skill", source="plugin")
            is True
        )

    def test_plugin_override_skill_is_not_excluded(self) -> None:
        """Skills with source='plugin-override' must pass through unfiltered."""
        from claude_wayfinder.match_filters import is_agent_routable

        assert (
            is_agent_routable(
                name="superpowers:brainstorming", kind="skill", source="plugin-override"
            )
            is True
        )

    def test_is_agent_routable_plugin_override_routable(self) -> None:
        """A plugin-override agent (source='plugin-override') is routable.

        Unlike source='plugin' agents (which are inert by default),
        a plugin-override agent has explicit trigger configuration and
        must be eligible for agent scoring.
        """
        from claude_wayfinder.match_filters import is_agent_routable

        assert (
            is_agent_routable(name="myplugin:my-agent", kind="agent", source="plugin-override")
            is True
        ), (
            "plugin-override agent should be routable — "
            "it has explicit trigger config unlike source='plugin' agents"
        )
