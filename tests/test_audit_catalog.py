"""Tests for the ``python -m claude_wayfinder audit-catalog`` subcommand.

Covers:
  - The Finding dataclass and Severity enum.
  - The run_audit() entry point on an empty catalog.
  - Per-rule unit tests (added incrementally by later tasks).
  - End-to-end CLI smoke tests via subprocess.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Module-level imports under test
# ---------------------------------------------------------------------------
from claude_wayfinder.audit_catalog import (
    Finding,
    Severity,
    rule_conflict_pairs,
    rule_duplicate_keyword_terms,
    rule_duplicate_trigger_set,
    rule_empty_applicable_agents,
    rule_excludes_overlap_own_keywords,
    rule_one_dimensional_triggers,
    rule_path_glob_footgun,
    rule_source_routable_mismatch,
    rule_tool_name_case_error,
    rule_unreachable_routable,
    rule_weight_not_in_ladder,
    rule_whitespace_in_term,
    run_audit,
)
from claude_wayfinder.match import CatalogEntry, Keyword, Triggers

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _empty_triggers() -> Triggers:
    """Return a Triggers instance with all collections empty."""
    return Triggers(
        command_prefixes=frozenset(),
        agent_mentions=frozenset(),
        path_globs=tuple(),
        keywords=tuple(),
        tool_mentions=frozenset(),
        excludes=frozenset(),
    )


def _entry(name: str, **overrides) -> CatalogEntry:
    """Build a CatalogEntry with sensible defaults for testing.

    Args:
        name: Entry name (e.g. ``"code-writer"``).
        **overrides: Any ``CatalogEntry`` field to override.

    Returns:
        A ``CatalogEntry`` instance suitable for use in audit tests.
    """
    defaults: dict = {
        "name": name,
        "kind": "agent",
        "triggers": _empty_triggers(),
        "applicable_agents": tuple(),
        "applicable_skills": tuple(),
        "source": "owned",
        "routable": True,
    }
    defaults.update(overrides)
    return CatalogEntry(**defaults)


# ---------------------------------------------------------------------------
# Scaffold tests
# ---------------------------------------------------------------------------


class TestFindingDataclass:
    """The Finding type carries severity, rule id, entry name, and message."""

    def test_finding_has_required_fields(self) -> None:
        """Finding stores all four required fields and they are accessible."""
        f = Finding(
            severity=Severity.BLOCKING,
            rule="weight-not-in-ladder",
            entry="example",
            message="weight 0.7 not in {0.25, 0.5, 1.0}",
        )
        assert f.severity == Severity.BLOCKING
        assert f.rule == "weight-not-in-ladder"
        assert f.entry == "example"
        assert "0.7" in f.message


class TestSeverityOrdering:
    """Severity members have exit codes so BLOCKING > CONCERN > NIT."""

    def test_severity_ordering(self) -> None:
        """Each Severity member exposes an exit_code property aligned with spec."""
        assert Severity.BLOCKING.exit_code == 3
        assert Severity.CONCERN.exit_code == 2
        assert Severity.NIT.exit_code == 1


class TestRunAuditEmpty:
    """run_audit() on an empty catalog returns no findings."""

    def test_empty_catalog_no_findings(self) -> None:
        """An empty catalog list should produce zero findings."""
        findings = run_audit([])
        assert findings == []


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    """Invoke ``python -m claude_wayfinder`` with the given arguments.

    Args:
        *args: CLI arguments appended after the module name.

    Returns:
        A ``CompletedProcess`` instance with captured stdout/stderr.
    """
    return subprocess.run(
        [sys.executable, "-m", "claude_wayfinder", *args],
        capture_output=True,
        text=True,
    )


class TestAuditCatalogCliHelp:
    """audit-catalog --help exits 0 and surfaces the documented flags."""

    @pytest.fixture(scope="class")
    def help_output(self) -> str:
        """Run audit-catalog --help and return stdout.

        Returns:
            The help text emitted to stdout.
        """
        cp = _run_cli("audit-catalog", "--help")
        assert cp.returncode == 0, cp.stderr
        return cp.stdout

    def test_help_lists_json_flag(self, help_output: str) -> None:
        """--json flag appears in audit-catalog help text."""
        assert "--json" in help_output

    def test_help_lists_severity_flag(self, help_output: str) -> None:
        """--severity flag appears in audit-catalog help text."""
        assert "--severity" in help_output

    def test_help_lists_target_flag(self, help_output: str) -> None:
        """--target flag appears in audit-catalog help text."""
        assert "--target" in help_output

    def test_help_lists_catalog_flag(self, help_output: str) -> None:
        """--catalog flag appears in audit-catalog help text."""
        assert "--catalog" in help_output


# ---------------------------------------------------------------------------
# Task 7 — rule_weight_not_in_ladder (BLOCKING)
# ---------------------------------------------------------------------------


class TestWeightNotInLadder:
    """BLOCKING: keyword weight outside {0.25, 0.5, 1.0}."""

    def test_clean_catalog_no_finding(self) -> None:
        e = _entry(
            "ok",
            triggers=Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=tuple(),
                keywords=(Keyword("foo", 1.0), Keyword("bar", 0.5)),
                tool_mentions=frozenset(),
                excludes=frozenset(),
            ),
        )
        assert rule_weight_not_in_ladder([e]) == []

    def test_off_ladder_weight_flagged(self) -> None:
        e = _entry(
            "bad",
            triggers=Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=tuple(),
                keywords=(Keyword("foo", 0.7),),
                tool_mentions=frozenset(),
                excludes=frozenset(),
            ),
        )
        findings = rule_weight_not_in_ladder([e])
        assert len(findings) == 1
        assert findings[0].severity == Severity.BLOCKING
        assert findings[0].entry == "bad"
        assert "0.7" in findings[0].message


# ---------------------------------------------------------------------------
# Task 8 — rule_whitespace_in_term (BLOCKING)
# ---------------------------------------------------------------------------


class TestWhitespaceInTerm:
    def test_clean_no_finding(self) -> None:
        e = _entry(
            "ok",
            triggers=Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=tuple(),
                keywords=(Keyword("clean-token", 1.0),),
                tool_mentions=frozenset(),
                excludes=frozenset(),
            ),
        )
        assert rule_whitespace_in_term([e]) == []

    def test_whitespace_flagged(self) -> None:
        e = _entry(
            "bad",
            triggers=Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=tuple(),
                keywords=(Keyword("two words", 1.0),),
                tool_mentions=frozenset(),
                excludes=frozenset(),
            ),
        )
        findings = rule_whitespace_in_term([e])
        assert len(findings) == 1
        assert findings[0].severity == Severity.BLOCKING
        assert "two words" in findings[0].message


# ---------------------------------------------------------------------------
# Task 9 — rule_duplicate_keyword_terms (BLOCKING)
# ---------------------------------------------------------------------------


class TestDuplicateKeywordTerms:
    def test_clean(self) -> None:
        e = _entry(
            "ok",
            triggers=Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=tuple(),
                keywords=(Keyword("a", 1.0), Keyword("b", 0.5)),
                tool_mentions=frozenset(),
                excludes=frozenset(),
            ),
        )
        assert rule_duplicate_keyword_terms([e]) == []

    def test_duplicate_flagged(self) -> None:
        # Note: the in-memory CatalogEntry can hold duplicates only if
        # the loader was bypassed; we construct one directly here.
        e = _entry(
            "dup",
            triggers=Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=tuple(),
                keywords=(Keyword("a", 1.0), Keyword("a", 0.5)),
                tool_mentions=frozenset(),
                excludes=frozenset(),
            ),
        )
        findings = rule_duplicate_keyword_terms([e])
        assert len(findings) == 1
        assert findings[0].severity == Severity.BLOCKING
        assert "'a'" in findings[0].message


# ---------------------------------------------------------------------------
# Task 10 — rule_path_glob_footgun (CONCERN)
# ---------------------------------------------------------------------------


class TestPathGlobFootgun:
    def test_double_star_ok(self) -> None:
        e = _entry(
            "ok",
            triggers=Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=("**/*.py",),
                keywords=tuple(),
                tool_mentions=frozenset(),
                excludes=frozenset(),
            ),
        )
        assert rule_path_glob_footgun([e]) == []

    def test_bare_star_ext_flagged(self) -> None:
        e = _entry(
            "footgun",
            triggers=Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=("*.py",),
                keywords=tuple(),
                tool_mentions=frozenset(),
                excludes=frozenset(),
            ),
        )
        findings = rule_path_glob_footgun([e])
        assert len(findings) == 1
        assert findings[0].severity == Severity.CONCERN
        assert "*.py" in findings[0].message

    def test_bare_with_double_star_sibling_ok(self) -> None:
        # If both `*.py` and `**/*.py` are present, the author opted in.
        e = _entry(
            "both",
            triggers=Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=("*.py", "**/*.py"),
                keywords=tuple(),
                tool_mentions=frozenset(),
                excludes=frozenset(),
            ),
        )
        assert rule_path_glob_footgun([e]) == []


# ---------------------------------------------------------------------------
# Task 11 — rule_tool_name_case_error (CONCERN)
# ---------------------------------------------------------------------------


class TestToolNameCaseError:
    def test_correct_case_ok(self) -> None:
        e = _entry(
            "ok",
            triggers=Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=tuple(),
                keywords=tuple(),
                tool_mentions=frozenset({"Bash"}),
                excludes=frozenset(),
            ),
        )
        assert rule_tool_name_case_error([e]) == []

    def test_wrong_case_flagged(self) -> None:
        e = _entry(
            "bad",
            triggers=Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=tuple(),
                keywords=tuple(),
                tool_mentions=frozenset({"bash"}),
                excludes=frozenset(),
            ),
        )
        findings = rule_tool_name_case_error([e])
        assert len(findings) == 1
        assert findings[0].severity == Severity.CONCERN
        assert "bash" in findings[0].message
        assert "Bash" in findings[0].message

    def test_unknown_tool_not_flagged(self) -> None:
        # Unknown tool names are passed through — only known tools with
        # wrong case are flagged.
        e = _entry(
            "unknown",
            triggers=Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=tuple(),
                keywords=tuple(),
                tool_mentions=frozenset({"CustomToolXYZ"}),
                excludes=frozenset(),
            ),
        )
        assert rule_tool_name_case_error([e]) == []


# ---------------------------------------------------------------------------
# Task 12 — rule_one_dimensional_triggers (CONCERN)
# ---------------------------------------------------------------------------


class TestOneDimensionalTriggers:
    def test_two_dimensions_ok(self) -> None:
        e = _entry(
            "ok",
            triggers=Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=("**/*.py",),
                keywords=(Keyword("python", 1.0),),
                tool_mentions=frozenset(),
                excludes=frozenset(),
            ),
        )
        assert rule_one_dimensional_triggers([e]) == []

    def test_only_keywords_flagged(self) -> None:
        e = _entry(
            "thin",
            triggers=Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=tuple(),
                keywords=(Keyword("python", 1.0),),
                tool_mentions=frozenset(),
                excludes=frozenset(),
            ),
        )
        findings = rule_one_dimensional_triggers([e])
        assert len(findings) == 1
        assert findings[0].severity == Severity.CONCERN
        assert (
            "one dimension" in findings[0].message.lower()
            or "dimension" in findings[0].message.lower()
        )

    def test_non_routable_not_flagged(self) -> None:
        # Skills and non-routable agents are not subject to the floor.
        e = _entry(
            "skill-thin",
            kind="skill",
            routable=False,
            triggers=Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=tuple(),
                keywords=(Keyword("python", 1.0),),
                tool_mentions=frozenset(),
                excludes=frozenset(),
            ),
        )
        assert rule_one_dimensional_triggers([e]) == []


# ---------------------------------------------------------------------------
# Task 13 — rule_unreachable_routable (CONCERN)
# ---------------------------------------------------------------------------


class TestUnreachableRoutable:
    def test_empty_routable_flagged(self) -> None:
        e = _entry(
            "ghost",
            kind="agent",
            routable=True,
            triggers=Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=tuple(),
                keywords=tuple(),
                tool_mentions=frozenset(),
                excludes=frozenset(),
            ),
        )
        findings = rule_unreachable_routable([e])
        assert len(findings) == 1
        assert findings[0].severity == Severity.CONCERN
        assert findings[0].entry == "ghost"

    def test_one_dim_not_flagged_here(self) -> None:
        # The 1-dim case is handled by rule_one_dimensional_triggers.
        e = _entry(
            "thin",
            triggers=Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=tuple(),
                keywords=(Keyword("x", 0.25),),
                tool_mentions=frozenset(),
                excludes=frozenset(),
            ),
        )
        assert rule_unreachable_routable([e]) == []

    def test_non_routable_skipped(self) -> None:
        e = _entry(
            "advisory",
            kind="agent",
            routable=False,
            triggers=Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=tuple(),
                keywords=tuple(),
                tool_mentions=frozenset(),
                excludes=frozenset(),
            ),
        )
        assert rule_unreachable_routable([e]) == []


# ---------------------------------------------------------------------------
# Task 14 — rule_conflict_pairs (CONCERN)
# ---------------------------------------------------------------------------


class TestConflictPairs:
    def _e(self, name: str, terms: list[str], **overrides) -> CatalogEntry:
        return _entry(
            name,
            triggers=Triggers(
                command_prefixes=frozenset(overrides.get("cp", set())),
                agent_mentions=frozenset(),
                path_globs=tuple(overrides.get("pg", ())),
                keywords=tuple(Keyword(t, 1.0) for t in terms),
                tool_mentions=frozenset(overrides.get("tm", set())),
                excludes=frozenset(),
            ),
            **{k: v for k, v in overrides.items() if k not in {"cp", "pg", "tm"}},
        )

    def test_no_overlap_clean(self) -> None:
        a = self._e("a", ["one", "two", "three"])
        b = self._e("b", ["four", "five", "six"])
        assert rule_conflict_pairs([a, b]) == []

    def test_two_overlap_clean(self) -> None:
        a = self._e("a", ["one", "two", "three"])
        b = self._e("b", ["one", "two", "nine"])
        assert rule_conflict_pairs([a, b]) == []

    def test_three_overlap_no_discriminator_flagged(self) -> None:
        a = self._e("a", ["one", "two", "three", "four"])
        b = self._e("b", ["one", "two", "three", "nine"])
        findings = rule_conflict_pairs([a, b])
        assert len(findings) == 1
        assert findings[0].severity == Severity.CONCERN
        assert findings[0].rule == "conflict-pair"
        assert "a" in findings[0].message and "b" in findings[0].message

    def test_three_overlap_with_asymmetric_discriminator_clean(self) -> None:
        # b has a unique path_glob (a has none) — asymmetric discriminator.
        # The matcher can break the tie on any input that fills path_globs,
        # so this is not a conflict.
        a = self._e("a", ["one", "two", "three"])
        b = self._e("b", ["one", "two", "three"], pg=["**/*.py"])
        assert rule_conflict_pairs([a, b]) == []

    def test_three_overlap_disjoint_globs_flagged(self) -> None:
        # Both agents have non-empty path_globs but the sets are disjoint
        # (a covers .py, b covers .ts). The OLD signature-equality check
        # cleared this pair because the sigs differ. The CORRECT check
        # asks whether the discriminator is *single-sided-asymmetric* —
        # one side empty, one side non-empty. Here both sides are non-
        # empty, so neither agent is the "unscoped fallback" the matcher
        # can demote on path-bearing prompts. On the typical no-path
        # prompt, both score identically on the keyword overlap and the
        # matcher emits ambiguous. That is the failure mode this rule
        # must catch.
        a = self._e("a", ["one", "two", "three"], pg=["**/*.py"])
        b = self._e("b", ["one", "two", "three"], pg=["**/*.ts"])
        findings = rule_conflict_pairs([a, b])
        assert len(findings) == 1
        assert findings[0].rule == "conflict-pair"

    def test_case_insensitive_overlap(self) -> None:
        a = self._e("a", ["One", "Two", "Three"])
        b = self._e("b", ["one", "two", "three"])
        findings = rule_conflict_pairs([a, b])
        assert len(findings) == 1

    def test_non_routable_skipped(self) -> None:
        a = self._e("a", ["one", "two", "three"], routable=False)
        b = self._e("b", ["one", "two", "three"])
        assert rule_conflict_pairs([a, b]) == []


# ---------------------------------------------------------------------------
# Task 15 — rule_excludes_overlap_own_keywords (CONCERN)
# ---------------------------------------------------------------------------


class TestExcludesOverlapOwnKeywords:
    def test_no_excludes_clean(self) -> None:
        # Empty excludes — nothing to overlap.
        e = _entry(
            "ok",
            triggers=Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=tuple(),
                keywords=(Keyword("python", 1.0),),
                tool_mentions=frozenset(),
                excludes=frozenset(),
            ),
        )
        assert rule_excludes_overlap_own_keywords([e]) == []

    def test_disjoint_excludes_clean(self) -> None:
        # Excludes present but disjoint from own keywords — the common
        # legitimate case (excludes are meant to dampen other agents'
        # matches, not self-zero).
        e = _entry(
            "ok",
            triggers=Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=tuple(),
                keywords=(Keyword("python", 1.0),),
                tool_mentions=frozenset(),
                excludes=frozenset({"javascript"}),
            ),
        )
        assert rule_excludes_overlap_own_keywords([e]) == []

    def test_self_zero_overlap_flagged(self) -> None:
        e = _entry(
            "selfzero",
            triggers=Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=tuple(),
                keywords=(Keyword("python", 1.0),),
                tool_mentions=frozenset(),
                excludes=frozenset({"python"}),
            ),
        )
        findings = rule_excludes_overlap_own_keywords([e])
        assert len(findings) == 1
        assert findings[0].severity == Severity.CONCERN
        assert "python" in findings[0].message

    def test_case_insensitive_overlap_flagged(self) -> None:
        # The matcher lowercases both sides — "Python" in keywords with
        # "python" in excludes still self-zeros.
        e = _entry(
            "case",
            triggers=Triggers(
                command_prefixes=frozenset(),
                agent_mentions=frozenset(),
                path_globs=tuple(),
                keywords=(Keyword("Python", 1.0),),
                tool_mentions=frozenset(),
                excludes=frozenset({"python"}),
            ),
        )
        assert len(rule_excludes_overlap_own_keywords([e])) == 1


# ---------------------------------------------------------------------------
# Task 15 — rule_source_routable_mismatch (CONCERN)
# ---------------------------------------------------------------------------


class TestSourceRoutableMismatch:
    def test_owned_routable_ok(self) -> None:
        e = _entry("ok", source="owned", routable=True)
        assert rule_source_routable_mismatch([e]) == []

    def test_plugin_routable_flagged(self) -> None:
        e = _entry("bad", source="plugin", routable=True)
        findings = rule_source_routable_mismatch([e])
        assert len(findings) == 1
        assert findings[0].severity == Severity.CONCERN
        assert findings[0].entry == "bad"


# ---------------------------------------------------------------------------
# Task 15 — rule_empty_applicable_agents (NIT)
# ---------------------------------------------------------------------------


class TestEmptyApplicableAgents:
    def test_skill_with_agents_ok(self) -> None:
        e = _entry(
            "ok",
            kind="skill",
            routable=False,
            applicable_agents=("*",),
        )
        assert rule_empty_applicable_agents([e]) == []

    def test_skill_empty_agents_flagged(self) -> None:
        e = _entry(
            "bare",
            kind="skill",
            routable=False,
            applicable_agents=tuple(),
        )
        findings = rule_empty_applicable_agents([e])
        assert len(findings) == 1
        assert findings[0].severity == Severity.NIT
        assert findings[0].entry == "bare"


# ---------------------------------------------------------------------------
# Task 15 — rule_duplicate_trigger_set (NIT)
# ---------------------------------------------------------------------------


class TestDuplicateTriggerSet:
    def _shared_triggers(self) -> Triggers:
        return Triggers(
            command_prefixes=frozenset(),
            agent_mentions=frozenset(),
            path_globs=("**/*.py",),
            keywords=(Keyword("python", 1.0),),
            tool_mentions=frozenset(),
            excludes=frozenset(),
        )

    def test_single_entry_no_finding(self) -> None:
        e = _entry("solo", triggers=self._shared_triggers())
        assert rule_duplicate_trigger_set([e]) == []

    def test_identical_triggers_identical_skills_no_finding(self) -> None:
        # Same triggers AND same applicable_skills — not a copy-paste
        # smell, just two pointers at the same dispatch shape.
        t = self._shared_triggers()
        a = _entry("a", triggers=t, applicable_skills=("python",))
        b = _entry("b", triggers=t, applicable_skills=("python",))
        # Rule fires only when applicable_skills DIFFER (per Task 15 impl).
        assert rule_duplicate_trigger_set([a, b]) == []

    def test_identical_triggers_different_skills_flagged(self) -> None:
        t = self._shared_triggers()
        a = _entry("a", triggers=t, applicable_skills=("python",))
        b = _entry(
            "b", triggers=t, applicable_skills=("python", "testing"),
        )
        findings = rule_duplicate_trigger_set([a, b])
        assert len(findings) == 1
        assert findings[0].severity == Severity.NIT
        assert "a" in findings[0].entry and "b" in findings[0].entry

    def test_both_empty_skills_no_finding(self) -> None:
        # Edge case: two agents with identical triggers and BOTH have
        # empty applicable_skills. The rule fires on *differing* skill
        # sets; empty == empty does not differ.
        t = self._shared_triggers()
        a = _entry("a", triggers=t, applicable_skills=())
        b = _entry("b", triggers=t, applicable_skills=())
        assert rule_duplicate_trigger_set([a, b]) == []

    def test_skills_only_skill_kind_not_flagged(self) -> None:
        # The rule scopes to kind == "agent". Skills with duplicate
        # triggers aren't a copy-paste smell in the same way.
        t = self._shared_triggers()
        a = _entry("a", kind="skill", triggers=t, applicable_skills=("x",))
        b = _entry("b", kind="skill", triggers=t, applicable_skills=("y",))
        assert rule_duplicate_trigger_set([a, b]) == []


# ---------------------------------------------------------------------------
# Task 16 — exit-code contract + --severity filter
# ---------------------------------------------------------------------------


class TestExitCodes:
    """Exit code is the max severity present in the filtered finding set."""

    def test_clean_catalog_exits_zero(self, tmp_path: Path) -> None:
        """An empty catalog exits with code 0 (no findings)."""
        cat = {"entries": []}
        p = tmp_path / "cat.json"
        p.write_text(json.dumps(cat))
        cp = _run_cli("audit-catalog", "--catalog", str(p))
        assert cp.returncode == 0

    def test_blocking_exits_three(self, tmp_path: Path) -> None:
        """A catalog with a BLOCKING finding exits with code 3."""
        cat = {
            "entries": [
                {
                    "name": "bad",
                    "kind": "agent",
                    "routable": True,
                    "source": "owned",
                    "applicable_skills": [],
                    "triggers": {
                        "command_prefixes": [],
                        "agent_mentions": [],
                        "path_globs": ["**/*.py"],
                        "keywords": [{"term": "x", "weight": 0.7}],
                        "tool_mentions": [],
                        "excludes": [],
                    },
                }
            ]
        }
        p = tmp_path / "cat.json"
        p.write_text(json.dumps(cat))
        cp = _run_cli("audit-catalog", "--catalog", str(p))
        assert cp.returncode == 3, cp.stdout + cp.stderr

    def test_severity_filter_changes_exit(self, tmp_path: Path) -> None:
        """--severity blocking filters out NIT findings, reducing exit code."""
        cat = {
            "entries": [
                {
                    "name": "s",
                    "kind": "skill",
                    "routable": False,
                    "source": "owned",
                    "applicable_agents": [],
                    "triggers": {
                        "command_prefixes": ["/x"],
                        "agent_mentions": [],
                        "path_globs": [],
                        "keywords": [{"term": "x", "weight": 1.0}],
                        "tool_mentions": [],
                        "excludes": [],
                    },
                }
            ]
        }
        p = tmp_path / "cat.json"
        p.write_text(json.dumps(cat))
        cp = _run_cli("audit-catalog", "--catalog", str(p))
        assert cp.returncode == 1
        cp = _run_cli(
            "audit-catalog", "--catalog", str(p), "--severity", "blocking"
        )
        assert cp.returncode == 0
