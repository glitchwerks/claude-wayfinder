"""Tests for posture._types: PostureContext and ExtractorResult contracts."""

from __future__ import annotations

import pytest


class TestPostureContext:
    """PostureContext must be a frozen dataclass accepting dispatch fields."""

    def test_import(self) -> None:
        """PostureContext must be importable from claude_wayfinder.posture."""
        from claude_wayfinder.posture import PostureContext  # noqa: F401

    def test_construction_required_field(self) -> None:
        """PostureContext requires task_description."""
        from claude_wayfinder.posture import PostureContext

        ctx = PostureContext(task_description="implement the login endpoint")
        assert ctx.task_description == "implement the login endpoint"

    def test_construction_all_fields(self) -> None:
        """PostureContext accepts all optional dispatch-context fields."""
        from claude_wayfinder.posture import PostureContext

        ctx = PostureContext(
            task_description="fix the deploy",
            file_paths=("src/app.py", "infra/main.tf"),
            agent_mentions=frozenset({"code-writer"}),
            tool_mentions=frozenset({"bash"}),
            command_prefix="gh",
        )
        assert ctx.task_description == "fix the deploy"
        assert ctx.file_paths == ("src/app.py", "infra/main.tf")
        assert ctx.agent_mentions == frozenset({"code-writer"})
        assert ctx.tool_mentions == frozenset({"bash"})
        assert ctx.command_prefix == "gh"

    def test_defaults(self) -> None:
        """PostureContext optional fields default to empty/None."""
        from claude_wayfinder.posture import PostureContext

        ctx = PostureContext(task_description="x")
        assert ctx.file_paths == ()
        assert ctx.agent_mentions == frozenset()
        assert ctx.tool_mentions == frozenset()
        assert ctx.command_prefix is None

    def test_frozen(self) -> None:
        """PostureContext must be immutable (frozen dataclass)."""
        from claude_wayfinder.posture import PostureContext

        ctx = PostureContext(task_description="x")
        with pytest.raises((AttributeError, TypeError)):
            ctx.task_description = "y"  # type: ignore[misc]


class TestExtractorResult:
    """ExtractorResult must match the uniform output contract from §10.3."""

    def test_import(self) -> None:
        """ExtractorResult must be importable from claude_wayfinder.posture."""
        from claude_wayfinder.posture import ExtractorResult  # noqa: F401

    def test_construction_fired_bool(self) -> None:
        """ExtractorResult supports fired=bool, tier str, evidence list."""
        from claude_wayfinder.posture import ExtractorResult

        result = ExtractorResult(
            fired=True,
            tier="B",
            evidence=[("diagnose", "strong")],
        )
        assert result.fired is True
        assert result.tier == "B"
        assert result.evidence == [("diagnose", "strong")]

    def test_construction_fired_count(self) -> None:
        """ExtractorResult supports fired=int (count variant)."""
        from claude_wayfinder.posture import ExtractorResult

        result = ExtractorResult(
            fired=3,
            tier="A",
            evidence=[("operate", "strong")],
        )
        assert result.fired == 3

    def test_construction_not_fired(self) -> None:
        """ExtractorResult with fired=False has empty evidence."""
        from claude_wayfinder.posture import ExtractorResult

        result = ExtractorResult(fired=False, tier="A", evidence=[])
        assert result.fired is False
        assert result.evidence == []

    def test_frozen(self) -> None:
        """ExtractorResult must be immutable."""
        from claude_wayfinder.posture import ExtractorResult

        result = ExtractorResult(fired=True, tier="A", evidence=[("operate", "strong")])
        with pytest.raises((AttributeError, TypeError)):
            result.fired = False  # type: ignore[misc]

    def test_tier_values(self) -> None:
        """ExtractorResult tier must be one of A, B, C."""
        from claude_wayfinder.posture import ExtractorResult

        for tier in ("A", "B", "C"):
            r = ExtractorResult(fired=True, tier=tier, evidence=[("build", "strong")])
            assert r.tier == tier
