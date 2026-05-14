"""Tests for claude_wayfinder/match_filters.py.

Covers the ``is_agent_routable`` predicate introduced in Pass 2.5.
Each test validates one exclusion rule or inclusion rule, as specified
in issue #477 §"Shared predicate module".

Finding #1 (PR #487): the predicate was updated from ``dict[str, Any]``
to three named keyword args (``name``, ``kind``, ``source``) to eliminate
per-entry dict allocations in the scoring loop.  Tests updated accordingly.

Issue #19: the predicate now also accepts a ``routable`` keyword
parameter (``bool``, default ``True``).  The hardcoded
``_EXCLUDED_AGENT_NAME`` constant was removed; callers pass the
catalog entry's ``routable`` field directly.
"""

from __future__ import annotations


class TestIsAgentRoutable:
    """Unit tests for ``is_agent_routable(*, name, kind, source, routable)``."""

    def test_router_agent_is_excluded(self) -> None:
        """The router agent must always be excluded from scoring.

        Exclusion is driven by the ``routable=False`` flag, not by name.
        """
        from claude_wayfinder.match_filters import is_agent_routable

        assert (
            is_agent_routable(
                name="general-purpose",
                kind="agent",
                source="owned",
                routable=False,
            )
            is False
        )

    def test_routable_default_true(self) -> None:
        """An agent with no explicit ``routable`` flag is treated as routable.

        The default value of ``True`` keeps callers that do not yet
        pass the new argument backward-compatible.
        """
        from claude_wayfinder.match_filters import is_agent_routable

        assert (
            is_agent_routable(name="some-agent", kind="agent", source="owned")
            is True
        )

    def test_routable_false_explicit(self) -> None:
        """An agent with ``routable=False`` is excluded from scoring."""
        from claude_wayfinder.match_filters import is_agent_routable

        assert (
            is_agent_routable(
                name="my-router",
                kind="agent",
                source="owned",
                routable=False,
            )
            is False
        )

    def test_routable_true_explicit(self) -> None:
        """An agent with ``routable=True`` is included regardless of name.

        Previously the name ``"general-purpose"`` caused exclusion.
        After issue #19 the name is irrelevant — only the flag matters.
        """
        from claude_wayfinder.match_filters import is_agent_routable

        # The name that used to be hardcoded as excluded now passes
        # through because routable=True overrides any name check.
        assert (
            is_agent_routable(
                name="general-purpose",
                kind="agent",
                source="owned",
                routable=True,
            )
            is True
        )

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
