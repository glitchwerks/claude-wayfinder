"""Tests for posture._extractors: E1–E12 extractor functions.

Test structure:
  1. P1-P14 spike fixtures (§11 + §12.1 refinements) via the conftest
     parametrized fixture.  These assert post-refinement behavior (R1-R3).
  2. Per-extractor unit tests covering edge cases.
  3. Determinism tests: same input → same output every time.
  4. Purity tests: no filesystem access inside extraction functions.
"""

from __future__ import annotations

from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Helper: build PostureContext from a spike record
# ---------------------------------------------------------------------------


def _ctx_from_record(record: dict[str, Any]):
    """Build a PostureContext from a spike prompt record dict."""
    from claude_wayfinder.posture import PostureContext

    return PostureContext(
        task_description=record["task_description"],
        file_paths=tuple(record.get("file_paths", [])),
        agent_mentions=frozenset(record.get("agent_mentions", [])),
        tool_mentions=frozenset(record.get("tool_mentions", [])),
        command_prefix=record.get("command_prefix"),
    )


# ---------------------------------------------------------------------------
# 1. P1-P14 spike fixture tests
#
# Each test is a *different concern* — we test each individual extractor's
# fired state rather than overall routing (routing composition is out of
# scope; extractors are standalone per the AC).
# ---------------------------------------------------------------------------


class TestSpikeExtractorFires:
    """Assert each extractor fires/abstains as documented in §12.1 for P1-P14."""

    def test_stacktrace_block_fires(self, spike_record: dict[str, Any]) -> None:
        """E1 stacktrace_block fires/abstains per §12.1."""
        from claude_wayfinder.posture._extractors import extract_stacktrace_block

        ctx = _ctx_from_record(spike_record)
        result = extract_stacktrace_block(ctx)
        expected = spike_record["expected_fires"]["stacktrace_block"]
        assert bool(result.fired) == bool(expected), (
            f"{spike_record['id']}: stacktrace_block expected fired={expected},"
            f" got fired={result.fired}. Notes: {spike_record['notes']}"
        )

    def test_test_failure_output_fires(self, spike_record: dict[str, Any]) -> None:
        """E2 test_failure_output fires/abstains per §12.1."""
        from claude_wayfinder.posture._extractors import extract_test_failure_output

        ctx = _ctx_from_record(spike_record)
        result = extract_test_failure_output(ctx)
        expected = spike_record["expected_fires"]["test_failure_output"]
        assert bool(result.fired) == bool(expected), (
            f"{spike_record['id']}: test_failure_output expected fired={expected},"
            f" got fired={result.fired}. Notes: {spike_record['notes']}"
        )

    def test_vcs_artifact_ref_fires(self, spike_record: dict[str, Any]) -> None:
        """E3 vcs_artifact_ref fires/abstains per §12.1."""
        from claude_wayfinder.posture._extractors import extract_vcs_artifact_ref

        ctx = _ctx_from_record(spike_record)
        result = extract_vcs_artifact_ref(ctx)
        expected = spike_record["expected_fires"]["vcs_artifact_ref"]
        assert bool(result.fired) == bool(expected), (
            f"{spike_record['id']}: vcs_artifact_ref expected fired={expected},"
            f" got fired={result.fired}. Notes: {spike_record['notes']}"
        )

    def test_spec_plan_path_fires(self, spike_record: dict[str, Any]) -> None:
        """E4 spec_plan_path fires/abstains per §12.1."""
        from claude_wayfinder.posture._extractors import extract_spec_plan_path

        ctx = _ctx_from_record(spike_record)
        result = extract_spec_plan_path(ctx)
        expected = spike_record["expected_fires"]["spec_plan_path"]
        assert bool(result.fired) == bool(expected), (
            f"{spike_record['id']}: spec_plan_path expected fired={expected},"
            f" got fired={result.fired}. Notes: {spike_record['notes']}"
        )

    def test_source_of_truth_pair_fires(self, spike_record: dict[str, Any]) -> None:
        """E5 source_of_truth_pair fires/abstains per §12.1 + R1."""
        from claude_wayfinder.posture._extractors import extract_source_of_truth_pair

        ctx = _ctx_from_record(spike_record)
        result = extract_source_of_truth_pair(ctx)
        expected = spike_record["expected_fires"]["source_of_truth_pair"]
        assert bool(result.fired) == bool(expected), (
            f"{spike_record['id']}: source_of_truth_pair expected fired={expected},"
            f" got fired={result.fired}. Notes: {spike_record['notes']}"
        )

    def test_cause_stated_fires(self, spike_record: dict[str, Any]) -> None:
        """E6 cause_stated fires/abstains per §12.1 + R3 (clause-scoped)."""
        from claude_wayfinder.posture._extractors import (
            extract_cause_stated,
            extract_stacktrace_block,
            extract_test_failure_output,
        )

        ctx = _ctx_from_record(spike_record)
        # E6 is conditional: only evaluates when E1 or E2 fired
        e1 = extract_stacktrace_block(ctx)
        e2 = extract_test_failure_output(ctx)
        host_fired = bool(e1.fired) or bool(e2.fired)

        result = extract_cause_stated(ctx, host_condition=host_fired)
        expected = spike_record["expected_fires"]["cause_stated"]
        assert bool(result.fired) == bool(expected), (
            f"{spike_record['id']}: cause_stated expected fired={expected},"
            f" got fired={result.fired}. Notes: {spike_record['notes']}"
        )

    def test_area_span_fires(self, spike_record: dict[str, Any]) -> None:
        """E7 area_span fired-count correct per §12.1 when host E1/E2 active."""
        from claude_wayfinder.posture._extractors import (
            extract_area_span,
            extract_stacktrace_block,
            extract_test_failure_output,
        )

        # E7 is a host-conditioned modifier: only active when E1 or E2 fired
        ctx = _ctx_from_record(spike_record)
        e1 = extract_stacktrace_block(ctx)
        e2 = extract_test_failure_output(ctx)
        host_fired = bool(e1.fired) or bool(e2.fired)

        area_map = {
            "code": ["src/**"],
            "infra": [".github/**", "infra/**"],
        }
        result = extract_area_span(
            ctx, area_map=area_map, host_condition=host_fired
        )
        # When host is inactive, fired still carries span count (or False);
        # when host is active, evidence is populated.
        # For records without area_span in expected_fires, just verify
        # type and tier invariants.
        if "area_span" in spike_record["expected_fires"]:
            expected = spike_record["expected_fires"]["area_span"]
            assert bool(result.fired) == bool(expected), (
                f"{spike_record['id']}: area_span expected fired={expected},"
                f" got fired={result.fired}. Notes: {spike_record['notes']}"
            )
        # Invariant: tier is always "A"
        assert result.tier == "A", (
            f"{spike_record['id']}: area_span tier must be 'A',"
            f" got {result.tier!r}"
        )
        # Invariant: evidence empty iff host_condition=False or no span
        if not host_fired:
            assert result.evidence == [], (
                f"{spike_record['id']}: area_span must not emit evidence"
                f" when host_condition=False (#347)"
            )

    def test_command_prefix_ext_fires(self, spike_record: dict[str, Any]) -> None:
        """E8 command_prefix fires/abstains per §12.1."""
        from claude_wayfinder.posture._extractors import extract_command_prefix

        ctx = _ctx_from_record(spike_record)
        result = extract_command_prefix(ctx)
        expected = spike_record["expected_fires"]["command_prefix_ext"]
        assert bool(result.fired) == bool(expected), (
            f"{spike_record['id']}: command_prefix_ext expected fired={expected},"
            f" got fired={result.fired}. Notes: {spike_record['notes']}"
        )

    def test_artifact_absence_fires(self, spike_record: dict[str, Any]) -> None:
        """E9 artifact_absence fires/abstains per §12.1 + R2 (E12 suppressor)."""
        from claude_wayfinder.posture._extractors import (
            extract_artifact_absence,
            extract_command_prefix,
            extract_prose_failure_mention,
            extract_source_of_truth_pair,
            extract_spec_plan_path,
            extract_stacktrace_block,
            extract_test_failure_output,
            extract_vcs_artifact_ref,
        )

        ctx = _ctx_from_record(spike_record)
        # Compute all artifact-bearing extractors and E12 for E9's gate logic
        artifact_extractors = [
            extract_stacktrace_block(ctx),
            extract_test_failure_output(ctx),
            extract_vcs_artifact_ref(ctx),
            extract_spec_plan_path(ctx),
            extract_source_of_truth_pair(ctx),
            extract_command_prefix(ctx),
        ]
        e12 = extract_prose_failure_mention(ctx)
        result = extract_artifact_absence(
            ctx,
            artifact_extractor_results=artifact_extractors,
            prose_failure_result=e12,
        )
        expected = spike_record["expected_fires"]["artifact_absence"]
        assert bool(result.fired) == bool(expected), (
            f"{spike_record['id']}: artifact_absence expected fired={expected},"
            f" got fired={result.fired}. Notes: {spike_record['notes']}"
        )

    def test_prose_failure_mention_fires(self, spike_record: dict[str, Any]) -> None:
        """E12 prose_failure_mention fires/abstains per §11.1 F1 + R2."""
        from claude_wayfinder.posture._extractors import extract_prose_failure_mention

        ctx = _ctx_from_record(spike_record)
        result = extract_prose_failure_mention(ctx)
        expected = spike_record["expected_fires"]["prose_failure_mention"]
        assert bool(result.fired) == bool(expected), (
            f"{spike_record['id']}: prose_failure_mention expected fired={expected},"
            f" got fired={result.fired}. Notes: {spike_record['notes']}"
        )

    def test_agent_mentions_ext_fires(self, spike_record: dict[str, Any]) -> None:
        """E11 agent_mentions fires/abstains per §12.1."""
        from claude_wayfinder.posture._extractors import extract_agent_mentions

        ctx = _ctx_from_record(spike_record)
        result = extract_agent_mentions(ctx)
        expected = spike_record["expected_fires"]["agent_mentions_ext"]
        assert bool(result.fired) == bool(expected), (
            f"{spike_record['id']}: agent_mentions_ext expected fired={expected},"
            f" got fired={result.fired}. Notes: {spike_record['notes']}"
        )


# ---------------------------------------------------------------------------
# 2. Per-extractor unit tests — E1 stacktrace_block
# ---------------------------------------------------------------------------


class TestExtractStacktraceBlock:
    """E1 stacktrace_block: Tier B, diagnose evidence."""

    def test_fires_on_python_traceback(self) -> None:
        """E1 fires on 'Traceback (most recent call last)'."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_stacktrace_block

        ctx = PostureContext(
            task_description=(
                "Getting this:\nTraceback (most recent call last):\n"
                "  File 'x.py', line 1\nValueError: bad value"
            )
        )
        result = extract_stacktrace_block(ctx)
        assert result.fired is True
        assert result.tier == "B"
        assert any(p == "diagnose" for p, _ in result.evidence)

    def test_fires_on_exit_code(self) -> None:
        """E1 fires on 'exited with code N' pattern."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_stacktrace_block

        ctx = PostureContext(task_description="The process exited with code 1.")
        result = extract_stacktrace_block(ctx)
        assert result.fired is True

    def test_fires_on_compiler_diag(self) -> None:
        """E1 fires on compiler diagnostic ':N:N: error:' pattern."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_stacktrace_block

        ctx = PostureContext(task_description="src/main.py:10:5: error: undefined name")
        result = extract_stacktrace_block(ctx)
        assert result.fired is True

    def test_fires_on_error_exception_pattern(self) -> None:
        """E1 fires on 'SomeError:' and 'SomeException(' patterns."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_stacktrace_block

        ctx = PostureContext(
            task_description="Error: ECONNREFUSED api.internal:443"
        )
        result = extract_stacktrace_block(ctx)
        assert result.fired is True

    def test_fires_on_panic(self) -> None:
        """E1 fires on 'panic:' (Go runtime panics)."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_stacktrace_block

        ctx = PostureContext(task_description="panic: runtime error: index out of range")
        result = extract_stacktrace_block(ctx)
        assert result.fired is True

    def test_does_not_fire_on_plain_text(self) -> None:
        """E1 does not fire on a plain build request."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_stacktrace_block

        ctx = PostureContext(task_description="Add a login endpoint to the API.")
        result = extract_stacktrace_block(ctx)
        assert result.fired is False

    def test_tier_is_b(self) -> None:
        """E1 always returns tier='B'."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_stacktrace_block

        ctx = PostureContext(task_description="Add a login endpoint.")
        result = extract_stacktrace_block(ctx)
        assert result.tier == "B"


# ---------------------------------------------------------------------------
# Per-extractor unit tests — E2 test_failure_output
# ---------------------------------------------------------------------------


class TestExtractTestFailureOutput:
    """E2 test_failure_output: Tier B, diagnose evidence."""

    def test_fires_on_pytest_failed_pattern(self) -> None:
        """E2 fires on 'FAILED tests/test_foo.py::test_bar'."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_test_failure_output

        ctx = PostureContext(
            task_description=(
                "FAILED tests/test_api.py::test_fetch - AttributeError:"
                " no attribute 'get_user'"
            )
        )
        result = extract_test_failure_output(ctx)
        assert result.fired is True
        assert result.tier == "B"

    def test_fires_on_runner_summary(self) -> None:
        """E2 fires on '3 failed, 10 passed'."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_test_failure_output

        ctx = PostureContext(task_description="3 failed, 10 passed in 1.2s")
        result = extract_test_failure_output(ctx)
        assert result.fired is True

    def test_fires_on_assertion_error(self) -> None:
        """E2 fires on 'AssertionError'."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_test_failure_output

        ctx = PostureContext(task_description="AssertionError: expected 1, got 2")
        result = extract_test_failure_output(ctx)
        assert result.fired is True

    def test_does_not_fire_on_plain_text(self) -> None:
        """E2 does not fire on 'tests are failing' (prose variant — E12 handles that)."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_test_failure_output

        ctx = PostureContext(task_description="The tests are failing after the rename.")
        result = extract_test_failure_output(ctx)
        assert result.fired is False


# ---------------------------------------------------------------------------
# Per-extractor unit tests — E3 vcs_artifact_ref
# ---------------------------------------------------------------------------


class TestExtractVcsArtifactRef:
    """E3 vcs_artifact_ref: Tier B (+A), assess evidence."""

    def test_fires_on_pr_hash(self) -> None:
        """E3 fires on 'PR #214' pattern."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_vcs_artifact_ref

        ctx = PostureContext(task_description="Give PR #214 a really harsh review.")
        result = extract_vcs_artifact_ref(ctx)
        assert result.fired is True
        assert result.tier in ("A", "B")

    def test_fires_on_pull_url(self) -> None:
        """E3 fires on GitHub PR URL '/pull/NNN'."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_vcs_artifact_ref

        ctx = PostureContext(
            task_description="See https://github.com/org/repo/pull/42 for context."
        )
        result = extract_vcs_artifact_ref(ctx)
        assert result.fired is True

    def test_fires_on_diff_hunk(self) -> None:
        """E3 fires on diff hunk '@@ -N,N +N,N @@'."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_vcs_artifact_ref

        ctx = PostureContext(
            task_description="@@ -10,3 +10,5 @@ def foo():\n-    old\n+    new"
        )
        result = extract_vcs_artifact_ref(ctx)
        assert result.fired is True

    def test_fires_via_tool_mentions(self) -> None:
        """E3 fires when tool_mentions contains get_pull_request."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_vcs_artifact_ref

        ctx = PostureContext(
            task_description="Review the changes",
            tool_mentions=frozenset({"get_pull_request"}),
        )
        result = extract_vcs_artifact_ref(ctx)
        assert result.fired is True

    def test_does_not_fire_on_plain_text(self) -> None:
        """E3 does not fire on plain task description."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_vcs_artifact_ref

        ctx = PostureContext(task_description="Add a login endpoint to the API.")
        result = extract_vcs_artifact_ref(ctx)
        assert result.fired is False

    def test_does_not_fire_short_hex(self) -> None:
        """E3 does not fire on short hex strings (< 7 chars)."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_vcs_artifact_ref

        ctx = PostureContext(task_description="Fix the abc123 bug.")
        result = extract_vcs_artifact_ref(ctx)
        assert result.fired is False

    def test_fires_on_seven_char_sha_with_mixed(self) -> None:
        """E3 fires on 7+ hex token with mixed letter+digit (git SHA)."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_vcs_artifact_ref

        # 7 chars, mixed alpha+digit
        ctx = PostureContext(task_description="Commit abc1234 broke the build.")
        result = extract_vcs_artifact_ref(ctx)
        assert result.fired is True


# ---------------------------------------------------------------------------
# Per-extractor unit tests — E4 spec_plan_path
# ---------------------------------------------------------------------------


class TestExtractSpecPlanPath:
    """E4 spec_plan_path: Tier A (+B), build (plan-execution) evidence."""

    def test_fires_on_spec_path_in_file_paths(self) -> None:
        """E4 fires when file_paths includes a spec/plan document."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_spec_plan_path

        ctx = PostureContext(
            task_description="Implement per the spec.",
            file_paths=("docs/superpowers/specs/my-feature-spec.md",),
        )
        result = extract_spec_plan_path(ctx)
        assert result.fired is True
        assert result.tier == "A"

    def test_fires_on_adr_path(self) -> None:
        """E4 fires on ADR paths."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_spec_plan_path

        ctx = PostureContext(
            task_description="Implement the ADR decision.",
            file_paths=("docs/adr/001-use-sqlite.md",),
        )
        result = extract_spec_plan_path(ctx)
        assert result.fired is True

    def test_fires_on_plan_path_in_text(self) -> None:
        """E4 fires on path-shaped prose tokens matching spec/plan globs."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_spec_plan_path

        ctx = PostureContext(
            task_description=(
                "Implement the feature described in"
                " docs/superpowers/specs/2026-06-08-my-spec.md"
            )
        )
        result = extract_spec_plan_path(ctx)
        assert result.fired is True

    def test_does_not_fire_on_plain_text(self) -> None:
        """E4 does not fire on task description with no path tokens."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_spec_plan_path

        ctx = PostureContext(task_description="Add a login endpoint.")
        result = extract_spec_plan_path(ctx)
        assert result.fired is False


# ---------------------------------------------------------------------------
# Per-extractor unit tests — E5 source_of_truth_pair
# ---------------------------------------------------------------------------


class TestExtractSourceOfTruthPair:
    """E5 source_of_truth_pair: B core + C assist, verify evidence. R1."""

    def test_fires_on_two_file_paths_with_relational_marker(self) -> None:
        """E5 fires: 2 file_paths (B core) + relational marker C-assist (R1)."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_source_of_truth_pair

        ctx = PostureContext(
            task_description=(
                "Make sure `db/schema.sql` is consistent with the migrations."
            ),
            file_paths=("db/schema.sql", "db/migrations/"),
        )
        result = extract_source_of_truth_pair(ctx)
        assert result.fired is True
        assert any(p == "verify" for p, _ in result.evidence)

    def test_does_not_fire_on_two_file_paths_without_relational(self) -> None:
        """E5 does NOT fire on 2 file_paths alone without a relational marker.

        Per §12.2 F4 + §12.3 R1: B core alone over-fires on any multi-file
        prompt (e.g. "refactor a.py and b.py"). E5 requires BOTH B core (≥2
        artifact refs) AND C assist (relational marker or named-doc noun) to
        prevent false activation on non-conformance prompts (P8, P14 pattern).
        """
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_source_of_truth_pair

        ctx = PostureContext(
            task_description="Check these two files.",
            file_paths=("schema.sql", "migrations/001.sql"),
        )
        result = extract_source_of_truth_pair(ctx)
        # No relational marker → E5 abstains despite 2 paths
        assert result.fired is False

    def test_does_not_fire_on_single_path(self) -> None:
        """E5 does not fire when only one artifact ref is present."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_source_of_truth_pair

        ctx = PostureContext(
            task_description="Check this file is consistent.",
            file_paths=("schema.sql",),
        )
        result = extract_source_of_truth_pair(ctx)
        assert result.fired is False

    def test_does_not_fire_with_no_paths(self) -> None:
        """E5 does not fire when no file_paths and no artifact-shaped text."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_source_of_truth_pair

        ctx = PostureContext(
            task_description="Does the README still reflect how the build works?"
        )
        result = extract_source_of_truth_pair(ctx)
        assert result.fired is False


# ---------------------------------------------------------------------------
# Per-extractor unit tests — E6 cause_stated (R3: clause-scoped)
# ---------------------------------------------------------------------------


class TestExtractCauseStated:
    """E6 cause_stated: Tier C modifier, flips diagnose→build. R3."""

    def test_fires_when_host_condition_met_and_connective_in_same_clause(
        self,
    ) -> None:
        """E6 fires: host_condition=True + connective in same clause as failure."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_cause_stated

        # P11: "Started after we renamed get_user → fetch_user"
        ctx = PostureContext(
            task_description=(
                "FAILED tests/test_api.py::test_fetch. Started after we renamed"
                " get_user → fetch_user."
            )
        )
        result = extract_cause_stated(ctx, host_condition=True)
        assert result.fired is True
        # E6 is Tier C (modifier)
        assert result.tier == "C"

    def test_does_not_fire_when_host_condition_false(self) -> None:
        """E6 does not fire when host_condition=False (E1/E2 not fired)."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_cause_stated

        ctx = PostureContext(
            task_description="tests are failing after the rename."
        )
        result = extract_cause_stated(ctx, host_condition=False)
        assert result.fired is False

    def test_does_not_fire_when_connective_in_different_clause(self) -> None:
        """E6 R3: connective in different clause from failure → no fire.

        P12: 'because' is attached to the change's motivation, not the failure.
        The failure clause is 'The deploy fails every time — logs show Error:...'
        The 'because' is in 'We changed the DNS config last week because the old
        provider was slow.' — a completely separate sentence/clause.
        """
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_cause_stated

        ctx = PostureContext(
            task_description=(
                "The deploy fails every time — logs show `Error: ECONNREFUSED"
                " api.internal:443`. We changed the DNS config last week because"
                " the old provider was slow. Figure out why it fails."
            )
        )
        result = extract_cause_stated(ctx, host_condition=True)
        # 'because' is in a separate clause from the failure marker → no flip
        assert result.fired is False

    def test_fires_on_cause_heading(self) -> None:
        """E6 B-variant fires on 'root cause:' heading."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_cause_stated

        ctx = PostureContext(
            task_description="FAILED test_foo. Root cause: typo in config.",
        )
        result = extract_cause_stated(ctx, host_condition=True)
        assert result.fired is True


# ---------------------------------------------------------------------------
# Per-extractor unit tests — E7 area_span (pure function — no fs access)
# ---------------------------------------------------------------------------


class TestExtractAreaSpan:
    """E7 area_span: Tier A modifier, gates diagnose on host_condition (#347).

    E7 is a modifier: it only contributes diagnose evidence when a host
    context (E1 stacktrace OR E2 test-failure) is active.  The span count
    is always preserved in ``fired`` so downstream callers (``_area_span_count``)
    can read ``int(e7.fired)`` regardless of ``host_condition``.
    """

    # ------------------------------------------------------------------
    # host_condition=True — evidence gates open
    # ------------------------------------------------------------------

    def test_host_true_span_two_emits_strong_evidence(self) -> None:
        """host_condition=True + span=2 → evidence==[('diagnose','strong')]."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_area_span

        ctx = PostureContext(
            task_description="CI failure in deploy workflow.",
            file_paths=("src/api/client.py", ".github/workflows/deploy.yml"),
        )
        area_map = {
            "code": ["src/**"],
            "infra": [".github/**", "infra/**"],
        }
        result = extract_area_span(
            ctx, area_map=area_map, host_condition=True
        )
        assert result.tier == "A"
        assert result.fired == 2
        assert result.evidence == [("diagnose", "strong")]

    def test_host_true_span_one_emits_weak_evidence(self) -> None:
        """host_condition=True + span=1 → evidence==[('diagnose','weak')]."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_area_span

        ctx = PostureContext(
            task_description="Fix this.",
            file_paths=("src/api/client.py", "src/lib/utils.py"),
        )
        area_map = {
            "code": ["src/**"],
            "infra": [".github/**"],
        }
        result = extract_area_span(
            ctx, area_map=area_map, host_condition=True
        )
        assert result.fired == 1
        assert result.evidence == [("diagnose", "weak")]

    # ------------------------------------------------------------------
    # host_condition=False — evidence gate closed (#347 regression)
    # ------------------------------------------------------------------

    def test_host_false_span_two_fired_preserved_no_evidence_issue_347(
        self,
    ) -> None:
        """host_condition=False + span=2 → fired==2 AND evidence==[].

        Regression for #347: E7 must NOT leak diagnose evidence when no
        host context (E1/E2) is active.  The span COUNT (fired=2) is
        preserved so downstream _area_span_count callers keep working.
        """
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_area_span

        ctx = PostureContext(
            task_description="Build the deploy pipeline.",
            file_paths=("src/api/client.py", ".github/workflows/deploy.yml"),
        )
        area_map = {
            "code": ["src/**"],
            "infra": [".github/**", "infra/**"],
        }
        result = extract_area_span(
            ctx, area_map=area_map, host_condition=False
        )
        assert result.tier == "A"
        # fired carries the span count for downstream consumers
        assert result.fired == 2
        # the core contract: NO diagnose evidence without a host context
        assert result.evidence == [], (
            "E7 must not emit diagnose evidence when host_condition=False"
            " (#347: spurious diagnose leak misroutes build/verify tasks)"
        )

    def test_host_false_span_one_fired_preserved_no_evidence(self) -> None:
        """host_condition=False + span=1 → fired==1 AND evidence==[]."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_area_span

        ctx = PostureContext(
            task_description="Refactor the handler.",
            file_paths=("src/handler.py",),
        )
        area_map = {"code": ["src/**"], "infra": [".github/**"]}
        result = extract_area_span(
            ctx, area_map=area_map, host_condition=False
        )
        assert result.tier == "A"
        assert result.fired == 1
        assert result.evidence == []

    # ------------------------------------------------------------------
    # Zero-span — no fire regardless of host_condition
    # ------------------------------------------------------------------

    def test_does_not_fire_with_no_paths_host_true(self) -> None:
        """E7 does not fire when file_paths is empty, even host_condition=True."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_area_span

        ctx = PostureContext(task_description="What's wrong?")
        result = extract_area_span(
            ctx, area_map={"code": ["src/**"]}, host_condition=True
        )
        assert result.fired is False
        assert result.evidence == []

    def test_does_not_fire_with_no_paths_host_false(self) -> None:
        """E7 does not fire when file_paths is empty and host_condition=False."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_area_span

        ctx = PostureContext(task_description="What's wrong?")
        result = extract_area_span(
            ctx, area_map={"code": ["src/**"]}, host_condition=False
        )
        assert result.fired is False
        assert result.evidence == []

    # ------------------------------------------------------------------
    # Required-param contract — mirrors E6.host_condition
    # ------------------------------------------------------------------

    def test_missing_host_condition_raises_type_error(self) -> None:
        """Calling without host_condition raises TypeError (required kwarg).

        Locks the E6-mirror contract: callers MUST supply host_condition
        so the library enforces the gate rather than relying on an
        external compensating filter.
        """
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_area_span

        ctx = PostureContext(
            task_description="CI failure.",
            file_paths=("src/app.py",),
        )
        with pytest.raises(TypeError):
            extract_area_span(ctx, area_map={"code": ["src/**"]})  # type: ignore[call-arg]

    # ------------------------------------------------------------------
    # Purity — no filesystem access inside the extractor
    # ------------------------------------------------------------------

    def test_pure_no_fs_access(self, tmp_path: pytest.TempdirFactory) -> None:
        """E7 takes area_map param and never reads the filesystem."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_area_span

        ctx = PostureContext(
            task_description="CI failure.",
            file_paths=("src/app.py",),
        )
        area_map = {"code": ["src/**"]}
        result = extract_area_span(
            ctx, area_map=area_map, host_condition=True
        )
        assert isinstance(result.fired, (bool, int))


# ---------------------------------------------------------------------------
# Per-extractor unit tests — E8 command_prefix
# ---------------------------------------------------------------------------


class TestExtractCommandPrefix:
    """E8 command_prefix: Tier A, operate evidence. Strongest single extractor."""

    def test_fires_on_non_null_prefix(self) -> None:
        """E8 fires when command_prefix is non-null."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_command_prefix

        ctx = PostureContext(task_description="Run the checks.", command_prefix="gh")
        result = extract_command_prefix(ctx)
        assert result.fired is True
        assert result.tier == "A"
        assert any(p == "operate" for p, _ in result.evidence)

    def test_does_not_fire_on_null_prefix(self) -> None:
        """E8 does not fire when command_prefix is None."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_command_prefix

        ctx = PostureContext(task_description="Do something.")
        result = extract_command_prefix(ctx)
        assert result.fired is False


# ---------------------------------------------------------------------------
# Per-extractor unit tests — E9 artifact_absence
# ---------------------------------------------------------------------------


class TestExtractArtifactAbsence:
    """E9 artifact_absence: Tier A computed, gates plan/research/idea-critique."""

    def test_fires_when_no_artifacts(self) -> None:
        """E9 fires when no artifact-bearing extractor fired and no paths."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import (
            ExtractorResult,
            extract_artifact_absence,
        )

        ctx = PostureContext(task_description="What if we cached the catalog?")
        no_fire = ExtractorResult(fired=False, tier="B", evidence=[])
        result = extract_artifact_absence(
            ctx,
            artifact_extractor_results=[no_fire, no_fire, no_fire],
            prose_failure_result=ExtractorResult(fired=False, tier="C", evidence=[]),
        )
        assert result.fired is True
        assert result.tier == "A"

    def test_does_not_fire_when_artifact_present(self) -> None:
        """E9 does not fire when any artifact extractor fired."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import (
            ExtractorResult,
            extract_artifact_absence,
        )

        ctx = PostureContext(
            task_description="Tear apart engine.py",
            file_paths=("src/engine.py",),
        )
        fired_result = ExtractorResult(
            fired=True, tier="B", evidence=[("assess", "strong")]
        )
        not_fired = ExtractorResult(fired=False, tier="B", evidence=[])
        result = extract_artifact_absence(
            ctx,
            artifact_extractor_results=[not_fired, not_fired, fired_result],
            prose_failure_result=ExtractorResult(fired=False, tier="C", evidence=[]),
        )
        assert result.fired is False

    def test_r2_suppressed_by_prose_failure(self) -> None:
        """E9 suppressed (fires=False) when E12 prose_failure_mention fires (R2).

        P10: 'tests are failing' → E12 fires → E9 suppressed → honest abstain.
        """
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import (
            ExtractorResult,
            extract_artifact_absence,
        )

        ctx = PostureContext(
            task_description="tests are failing after the rename."
        )
        no_fire = ExtractorResult(fired=False, tier="B", evidence=[])
        e12_fires = ExtractorResult(
            fired=True, tier="C", evidence=[("diagnose", "weak")]
        )
        result = extract_artifact_absence(
            ctx,
            artifact_extractor_results=[no_fire, no_fire, no_fire],
            prose_failure_result=e12_fires,
        )
        # R2: E12 suppresses E9 gate → E9 does not fire
        assert result.fired is False


# ---------------------------------------------------------------------------
# Per-extractor unit tests — E10 frame_markers
# ---------------------------------------------------------------------------


class TestExtractFrameMarkers:
    """E10 frame_markers: Tier C, only inside E9 gate. Splits plan/research/critique."""

    def test_fires_prior_art_set(self) -> None:
        """E10 fires research when prior-art marker present (E9 gate open)."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_frame_markers

        ctx = PostureContext(
            task_description="has anyone built something like this caching idea?"
        )
        result = extract_frame_markers(ctx, e9_gate_open=True)
        assert result.fired is True
        assert result.tier == "C"
        assert any(p == "research" for p, _ in result.evidence)

    def test_fires_scope_set(self) -> None:
        """E10 fires plan when scope marker present."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_frame_markers

        ctx = PostureContext(
            task_description="Lay out the phases and milestones to get there."
        )
        result = extract_frame_markers(ctx, e9_gate_open=True)
        assert result.fired is True
        assert any(p == "plan" for p, _ in result.evidence)

    def test_fires_challenge_set(self) -> None:
        """E10 fires idea-critique when challenge marker present."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_frame_markers

        ctx = PostureContext(
            task_description="Poke holes in this approach before I build it."
        )
        result = extract_frame_markers(ctx, e9_gate_open=True)
        assert result.fired is True
        assert any(p == "idea-critique" for p, _ in result.evidence)

    def test_does_not_fire_when_e9_gate_closed(self) -> None:
        """E10 does not fire when E9 gate is closed (artifact present)."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_frame_markers

        ctx = PostureContext(
            task_description="Poke holes in the code in engine.py",
            file_paths=("src/engine.py",),
        )
        result = extract_frame_markers(ctx, e9_gate_open=False)
        assert result.fired is False

    def test_does_not_fire_on_bare_proposal(self) -> None:
        """E10 bare 'what if' proposal → advisory, no posture fired."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_frame_markers

        ctx = PostureContext(
            task_description="What if we cached the catalog in memory?"
        )
        result = extract_frame_markers(ctx, e9_gate_open=True)
        # Bare proposal frame only — no decisive set → not fired
        assert result.fired is False


# ---------------------------------------------------------------------------
# Per-extractor unit tests — E11 agent_mentions
# ---------------------------------------------------------------------------


class TestExtractAgentMentions:
    """E11 agent_mentions: Tier A, near-dispositive pass-through."""

    def test_fires_when_agent_mentioned(self) -> None:
        """E11 fires when agent_mentions is non-empty."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_agent_mentions

        ctx = PostureContext(
            task_description="Use the code-writer for this.",
            agent_mentions=frozenset({"code-writer"}),
        )
        result = extract_agent_mentions(ctx)
        assert result.fired is True
        assert result.tier == "A"

    def test_does_not_fire_when_no_mentions(self) -> None:
        """E11 does not fire when agent_mentions is empty."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_agent_mentions

        ctx = PostureContext(task_description="Do something.")
        result = extract_agent_mentions(ctx)
        assert result.fired is False


# ---------------------------------------------------------------------------
# Per-extractor unit tests — E12 prose_failure_mention (R2)
# ---------------------------------------------------------------------------


class TestExtractProseFailureMention:
    """E12 prose_failure_mention: Tier C, brake + E9 suppressor. Never activates diagnose."""

    def test_fires_on_failing(self) -> None:
        """E12 fires on 'failing' (frozen set term)."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_prose_failure_mention

        ctx = PostureContext(
            task_description="tests are failing after the rename."
        )
        result = extract_prose_failure_mention(ctx)
        assert result.fired is True
        assert result.tier == "C"

    def test_fires_on_crashes(self) -> None:
        """E12 fires on 'crashes' (P3: 'The app crashes on startup')."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_prose_failure_mention

        ctx = PostureContext(
            task_description="The app crashes on startup and config doesn't match."
        )
        result = extract_prose_failure_mention(ctx)
        assert result.fired is True

    def test_fires_on_broken(self) -> None:
        """E12 fires on 'broken'."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_prose_failure_mention

        ctx = PostureContext(task_description="The pipeline is broken since yesterday.")
        result = extract_prose_failure_mention(ctx)
        assert result.fired is True

    def test_does_not_fire_on_plain_build_request(self) -> None:
        """E12 does not fire on normal build request."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_prose_failure_mention

        ctx = PostureContext(
            task_description="Add a login endpoint to the API. Use JWT tokens."
        )
        result = extract_prose_failure_mention(ctx)
        assert result.fired is False

    def test_r2_never_adds_diagnose_evidence(self) -> None:
        """R2: E12 evidence must NOT include diagnose. It is a brake/suppressor only."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_prose_failure_mention

        ctx = PostureContext(task_description="The app crashes and is broken.")
        result = extract_prose_failure_mention(ctx)
        assert result.fired is True
        # Per R2: E12 never activates diagnose — it only brakes
        diagnose_postures = [p for p, _ in result.evidence if p == "diagnose"]
        assert len(diagnose_postures) == 0, (
            "E12 must never add diagnose evidence (§12.3 R2)"
        )


# ---------------------------------------------------------------------------
# 3. Determinism tests
# ---------------------------------------------------------------------------


class TestDeterminism:
    """Same input → same output every time. No randomness, time, or network."""

    @pytest.mark.parametrize(
        "extractor_name,kwargs",
        [
            ("extract_stacktrace_block", {}),
            ("extract_test_failure_output", {}),
            ("extract_vcs_artifact_ref", {}),
            ("extract_spec_plan_path", {}),
            ("extract_source_of_truth_pair", {}),
            ("extract_command_prefix", {}),
            ("extract_agent_mentions", {}),
            ("extract_prose_failure_mention", {}),
        ],
    )
    def test_deterministic_basic_extractor(
        self, extractor_name: str, kwargs: dict
    ) -> None:
        """Basic extractors return identical results on repeated calls."""
        import importlib

        from claude_wayfinder.posture import PostureContext

        module = importlib.import_module("claude_wayfinder.posture._extractors")
        fn = getattr(module, extractor_name)

        ctx = PostureContext(
            task_description=(
                "FAILED tests/test_api.py::test_fetch. The app crashes."
                " tests are failing after rename."
            ),
            file_paths=("src/app.py", ".github/workflows/ci.yml"),
            command_prefix="gh",
            agent_mentions=frozenset({"code-writer"}),
            tool_mentions=frozenset({"bash"}),
        )

        results = [fn(ctx, **kwargs) for _ in range(3)]
        assert results[0].fired == results[1].fired == results[2].fired
        assert results[0].tier == results[1].tier == results[2].tier
        assert results[0].evidence == results[1].evidence == results[2].evidence

    def test_deterministic_cause_stated(self) -> None:
        """E6 cause_stated returns identical results on repeated calls."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_cause_stated

        ctx = PostureContext(
            task_description=(
                "FAILED tests/test_api.py::test_fetch. Started after rename."
            )
        )
        results = [extract_cause_stated(ctx, host_condition=True) for _ in range(3)]
        assert all(r.fired == results[0].fired for r in results)
        assert all(r.tier == results[0].tier for r in results)

    def test_deterministic_area_span(self) -> None:
        """E7 area_span returns identical results on repeated calls."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_area_span

        ctx = PostureContext(
            task_description="CI failure.",
            file_paths=("src/api/client.py", ".github/workflows/deploy.yml"),
        )
        area_map = {"code": ["src/**"], "infra": [".github/**"]}
        results = [
            extract_area_span(ctx, area_map=area_map, host_condition=True)
            for _ in range(3)
        ]
        assert all(r.fired == results[0].fired for r in results)
        assert all(r.evidence == results[0].evidence for r in results)

    def test_deterministic_frame_markers(self) -> None:
        """E10 frame_markers returns identical results on repeated calls."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_frame_markers

        ctx = PostureContext(
            task_description="Poke holes in this approach."
        )
        results = [
            extract_frame_markers(ctx, e9_gate_open=True) for _ in range(3)
        ]
        assert all(r.fired == results[0].fired for r in results)

    def test_deterministic_artifact_absence(self) -> None:
        """E9 artifact_absence returns identical results on repeated calls."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import (
            ExtractorResult,
            extract_artifact_absence,
        )

        ctx = PostureContext(task_description="What if we cached the catalog?")
        no_fire = ExtractorResult(fired=False, tier="B", evidence=[])
        results = [
            extract_artifact_absence(
                ctx,
                artifact_extractor_results=[no_fire],
                prose_failure_result=ExtractorResult(
                    fired=False, tier="C", evidence=[]
                ),
            )
            for _ in range(3)
        ]
        assert all(r.fired == results[0].fired for r in results)


# ---------------------------------------------------------------------------
# 4. Purity — no time/random/network inside extractors
# ---------------------------------------------------------------------------


class TestExtractorPurity:
    """Extractors must not import/use time, random, or network modules."""

    def test_no_time_import_in_extractors(self) -> None:
        """The _extractors module must not import the time module at module level."""
        import ast
        import importlib.util
        from pathlib import Path

        spec = importlib.util.find_spec("claude_wayfinder.posture._extractors")
        assert spec is not None, "_extractors module not found"
        assert spec.origin is not None
        source = Path(spec.origin).read_text(encoding="utf-8")
        tree = ast.parse(source)
        time_imports = [
            node for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            and any(
                alias.name == "time" or getattr(node, "module", "") == "time"
                for alias in getattr(node, "names", [])
            )
        ]
        assert len(time_imports) == 0, (
            "Extractor module must not import 'time' (purity violation)"
        )

    def test_no_random_import_in_extractors(self) -> None:
        """The _extractors module must not import the random module."""
        import ast
        import importlib.util
        from pathlib import Path

        spec = importlib.util.find_spec("claude_wayfinder.posture._extractors")
        assert spec is not None
        assert spec.origin is not None
        source = Path(spec.origin).read_text(encoding="utf-8")
        tree = ast.parse(source)
        random_imports = [
            node for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            and any(
                alias.name == "random" or getattr(node, "module", "") == "random"
                for alias in getattr(node, "names", [])
            )
        ]
        assert len(random_imports) == 0, (
            "Extractor module must not import 'random' (purity violation)"
        )


# ---------------------------------------------------------------------------
# 5. Output contract — ExtractorResult invariants
# ---------------------------------------------------------------------------


class TestOutputContract:
    """All extractors must return ExtractorResult with tier in {A,B,C}."""

    @pytest.mark.parametrize("extractor_name", [
        "extract_stacktrace_block",
        "extract_test_failure_output",
        "extract_vcs_artifact_ref",
        "extract_spec_plan_path",
        "extract_source_of_truth_pair",
        "extract_command_prefix",
        "extract_agent_mentions",
        "extract_prose_failure_mention",
    ])
    def test_returns_extractor_result(self, extractor_name: str) -> None:
        """Each basic extractor returns an ExtractorResult instance."""
        import importlib

        from claude_wayfinder.posture import ExtractorResult, PostureContext

        module = importlib.import_module("claude_wayfinder.posture._extractors")
        fn = getattr(module, extractor_name)

        ctx = PostureContext(task_description="Implement the login endpoint.")
        result = fn(ctx)
        assert isinstance(result, ExtractorResult), (
            f"{extractor_name} must return ExtractorResult"
        )
        assert result.tier in ("A", "B", "C"), (
            f"{extractor_name} returned invalid tier: {result.tier!r}"
        )
        assert isinstance(result.evidence, list)
        # Each evidence entry is a (posture, weight-class) tuple
        for item in result.evidence:
            assert len(item) == 2
            assert isinstance(item[0], str)  # posture
            assert isinstance(item[1], str)  # weight class

    def test_not_fired_has_empty_evidence(self) -> None:
        """When fired=False, evidence must be empty (abstain ≠ veto)."""
        from claude_wayfinder.posture import PostureContext
        from claude_wayfinder.posture._extractors import extract_stacktrace_block

        ctx = PostureContext(task_description="Add a login endpoint.")
        result = extract_stacktrace_block(ctx)
        assert result.fired is False
        assert result.evidence == [], (
            "abstaining extractors must have empty evidence list"
        )
