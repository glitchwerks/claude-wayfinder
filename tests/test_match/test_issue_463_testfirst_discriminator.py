# FROZEN — do not edit without going back through test-implementer (see issue #463)
"""Branch-3 test-first discriminator vs. mixed/inline file_paths (issue #463).

Shadow-mode telemetry showed ``branch3_testfirst`` firing correctly on only
~56% (5/9) of genuine test-authoring tasks. Issue #463 guesses the cause is
that ``_test_authoring_signal()`` ignores ``file_paths`` — i.e. a task that
names both a src file and a test file (or mentions the src filename inline
in the task text) fails to redirect ``code-writer`` -> ``test-implementer``.

These tests exercise the REAL pipeline end to end
(``build_features`` -> ``score_entries`` -> ``compose_route``) with realistic
catalog triggers for ``code-writer`` / ``test-implementer`` (mirroring the
production catalog's ``path_globs`` / ``keywords``, hard-coded here for
hermeticity — the real catalog lives outside the repo on this machine and
must not be a test dependency). A hand-stubbed ``gated`` list with identical
scores for both agents (as the pre-existing
``TestBranch3TestFirstDiscriminator`` in ``test_compose.py`` uses) would
trivially pass regardless of the real scoring path, so it cannot prove
anything about this issue's hypothesis.

The keyword/qualifier-stem signal is held FIXED (the literal phrase
"test-first" appears in every task_description below) across all three
cases, isolating file_paths / inline-mention as the only varying input —
per the primary-source dispatch-log evidence in the issue: the one pair of
real shadow-mode calls that did/did not redirect differed in BOTH the
qualifier-stem signal and file_paths simultaneously, so that pair alone
cannot isolate which dimension actually matters.

Written against the spec/issue text — no diagnosis of the underlying cause
was assumed before writing these; results were confirmed empirically against
the real ``compose_route`` pipeline (see the test-implementer's return to
the router for the diagnosis and code citations).
"""

from __future__ import annotations

from typing import Any

import pytest

from claude_wayfinder.match._compose import compose_route
from claude_wayfinder.match._match import build_features, score_entries
from claude_wayfinder.match._parse import _parse_triggers
from claude_wayfinder.match._types import CatalogEntry, Labels

# ---------------------------------------------------------------------------
# Realistic trigger sets mirroring the production catalog (hard-coded for
# hermeticity; the live catalog at ~/.claude/state/dispatch-catalog.json is
# machine-local and must not be a test dependency). Trimmed to the globs and
# keywords relevant to this scenario -- verified against the real catalog to
# reproduce identical branch/agent outcomes for the cases below.
# ---------------------------------------------------------------------------

_CODE_WRITER_TRIGGERS: dict[str, Any] = {
    "command_prefixes": [],
    "agent_mentions": ["code-writer"],
    "path_globs": ["**/*.py", "**/*.js", "**/*.ts"],
    "path_globs_excluded": [
        "**/*.spec.js",
        "**/*.spec.ts",
        "**/*.test.js",
        "**/*.test.ts",
        "**/*.test.tsx",
        "**/*_test.go",
        "**/*_test.py",
        "**/test_*.py",
    ],
    "keywords": [
        {"term": "add", "weight": 0.5},
        {"term": "build", "weight": 0.5},
        {"term": "create", "weight": 0.5},
        {"term": "implement", "weight": 1.0},
        {"term": "write", "weight": 1.0},
    ],
    "tool_mentions": [],
    "excludes": [],
}

_TEST_IMPLEMENTER_TRIGGERS: dict[str, Any] = {
    "command_prefixes": [],
    "agent_mentions": ["test-implementer"],
    "path_globs": [
        "**/*.spec.js",
        "**/*.spec.ts",
        "**/*.test.js",
        "**/*.test.ts",
        "**/*.test.tsx",
        "**/*_test.go",
        "**/*_test.py",
        "**/__tests__/**",
        "**/test_*.py",
        "**/tests/**",
    ],
    "path_globs_excluded": [],
    "keywords": [
        {"term": "coverage", "weight": 0.5},
        {"term": "spec", "weight": 0.25},
        {"term": "test", "weight": 0.5},
    ],
    "tool_mentions": [],
    "excludes": [],
}

_EMPTY_TRIGGERS: dict[str, Any] = {
    "command_prefixes": [],
    "agent_mentions": [],
    "path_globs": [],
    "path_globs_excluded": [],
    "keywords": [],
    "tool_mentions": [],
    "excludes": [],
}

#: Task text carrying the test-authoring signal via the literal
#: "test-first" phrase, held fixed across all three cases so only
#: file_paths / inline-mention vary between them.
_TASK_TEXT_NO_INLINE_MENTION = (
    "Write failing tests test-first for a new CLI entrypoint that mints "
    "and prints an App JWT and installation token, never printing secret "
    "material."
)

#: Same signal, but the task text additionally names the src file inline
#: (mirrors dispatch-log Call 1's "...on app_auth.py..." phrasing).
_TASK_TEXT_WITH_INLINE_SRC_MENTION = (
    "Write failing tests test-first for a new CLI entrypoint on "
    "app_auth.py that mints and prints an App JWT and installation "
    "token, never printing secret material."
)

_SRC_FILE = "src/chain/app_auth.py"
_TEST_FILE = "tests/chain/test_app_auth.py"


def _entry(name: str, triggers_raw: dict[str, Any]) -> CatalogEntry:
    """Build a routable :class:`CatalogEntry` from a raw triggers dict.

    Args:
        name: Agent name.
        triggers_raw: Raw triggers dict in catalog-JSON shape.

    Returns:
        A routable :class:`CatalogEntry` for the given agent.
    """
    triggers = _parse_triggers(triggers_raw)
    return CatalogEntry(
        name=name,
        kind="agent",
        source="owned",
        routable=True,
        triggers=triggers,
        applicable_skills=(),
        applicable_agents=(),
    )


def _catalog_and_names() -> tuple[list[CatalogEntry], frozenset[str]]:
    """Build the (catalog, catalog_agent_names) pair shared by all cases.

    Returns:
        Tuple of (catalog entries, frozenset of routable agent names).
    """
    catalog = [
        _entry("code-writer", _CODE_WRITER_TRIGGERS),
        _entry("test-implementer", _TEST_IMPLEMENTER_TRIGGERS),
        _entry("debugger", _EMPTY_TRIGGERS),
        _entry("code-reviewer", _EMPTY_TRIGGERS),
    ]
    names = frozenset({"code-writer", "test-implementer", "debugger", "code-reviewer"})
    return catalog, names


def _route(task_description: str, file_paths: list[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run the real pipeline: build_features -> score_entries -> compose_route.

    Args:
        task_description: Raw task text.
        file_paths: File paths named by the task.

    Returns:
        Tuple of (compose_route result dict, diagnostics dict).
    """
    catalog, catalog_agent_names = _catalog_and_names()
    features = build_features(
        {
            "task_description": task_description,
            "file_paths": file_paths,
            "agent_mentions": [],
            "tool_mentions": [],
            "command_prefix": None,
        }
    )
    scored_agents, scored_skills = score_entries(catalog, features)
    labels = Labels(domain="code", posture="build", confidence="high", area_span=1)
    diagnostics: dict[str, Any] = {}
    result = compose_route(
        labels=labels,
        scored_agents=scored_agents,
        scored_skills=scored_skills,
        features=features,
        catalog=catalog,
        catalog_agent_names=catalog_agent_names,
        diagnostics=diagnostics,
    )
    return result, diagnostics


def _assert_redirected_to_test_implementer(
    result: dict[str, Any], diagnostics: dict[str, Any]
) -> None:
    """Assert the standard branch3_testfirst redirect payload.

    Args:
        result: ``compose_route`` return value.
        diagnostics: The out-param diagnostics dict populated by the call.
    """
    assert result["decision"] == "delegate", (
        f"Expected delegate, got decision={result['decision']!r}"
    )
    assert result["agent"] == "test-implementer", (
        "Expected the test-authoring signal to redirect code-writer to "
        f"test-implementer, got agent={result.get('agent')!r}"
    )
    assert result["confidence"] == pytest.approx(0.9)
    assert result["disposition_source"] == "posture_routed"
    assert diagnostics.get("branch") == "branch3_testfirst", (
        "Expected diagnostics['branch']=='branch3_testfirst', got "
        f"{diagnostics.get('branch')!r}"
    )


class TestBranch3TestFirstVsFilePaths:
    """Branch-3 test-first discriminator against mixed/inline file_paths (#463).

    Isolates file_paths and inline src-file mentions as the sole varying
    input, holding the "test-first" keyword/qualifier-stem signal fixed,
    against the REAL scoring + compose_route pipeline (not a hand-stubbed
    ``gated`` list).
    """

    def test_control_test_file_only_paths_redirects_to_test_implementer(
        self,
    ) -> None:
        """Control: test-first signal + test-file-only file_paths.

        Mirrors dispatch-log Call 2 (the call that correctly redirected).
        Must resolve to branch3_testfirst / test-implementer.
        """
        result, diagnostics = _route(_TASK_TEXT_NO_INLINE_MENTION, [_TEST_FILE])
        _assert_redirected_to_test_implementer(result, diagnostics)

    def test_mixed_src_and_test_file_paths_still_redirects_to_test_implementer(
        self,
    ) -> None:
        """Mixed src+test file_paths must not defeat the test-first redirect.

        Same test-first signal as the control, but file_paths now names
        BOTH the source file and the test file (mirrors dispatch-log
        Call 1's file_paths, with the confounding keyword-signal loss
        removed). Per issue #463's intent, a task that names both files
        while still carrying explicit test-authoring intent must still
        redirect to test-implementer.
        """
        result, diagnostics = _route(
            _TASK_TEXT_NO_INLINE_MENTION, [_SRC_FILE, _TEST_FILE]
        )
        _assert_redirected_to_test_implementer(result, diagnostics)

    def test_inline_src_file_mention_still_redirects_to_test_implementer(
        self,
    ) -> None:
        """Inline src-filename mention in task text must not defeat the redirect.

        Same test-first signal and test-file-only file_paths as the
        control, but the task text additionally names the src filename
        inline (mirrors dispatch-log Call 1's "...on app_auth.py..."
        phrasing). Must still resolve to branch3_testfirst /
        test-implementer.
        """
        result, diagnostics = _route(_TASK_TEXT_WITH_INLINE_SRC_MENTION, [_TEST_FILE])
        _assert_redirected_to_test_implementer(result, diagnostics)

    def test_write_failing_tests_phrasing_without_qualifier_stem_redirects_to_test_implementer(
        self,
    ) -> None:
        """Genuine test-authoring intent without a qualifier stem must redirect.

        This is the actual dispatch-log Call 1 text and file_paths
        verbatim (issue #463, session f57d4d6b-7020-43e4-ad53-9850b1b5979f).
        "Write failing tests for..." is unambiguous test-authoring intent,
        but contains no qualifier stem from
        ``_TEST_AUTHORING_QUALIFIER_STEMS`` ("first"/"red"/"pytest"/
        "vitest") -- "failing" was deliberately excluded by #453 (see
        ``test_failing_test_mention_in_build_task_does_not_redirect`` in
        ``test_compose.py``, which this test must NOT be confused with:
        that regression guard covers a *build* task that only mentions a
        failing test incidentally ("Implement the retry helper... so the
        failing integration test passes") and must keep resolving to
        code-writer/branch3_generic -- it is not touched or weakened here).

        Per the confirmed diagnosis, ``_test_authoring_signal()``
        (``_compose.py:262-288``) returns False for this text today
        because it requires either the literal "test-first" token or
        "test" plus one of the four qualifier stems -- neither is present.
        This is the real, still-open gap issue #463 is pointing at
        (mislabeled as a file_paths problem upstream, but confirmed via
        the diagnosis to be a qualifier-stem vocabulary gap). This test
        is expected to be RED until the qualifier-stem vocabulary is
        widened to recognize "write ... tests" / "write failing tests"
        as test-authoring intent without weakening the #453 regression
        guard above.
        """
        call1_text = (
            "Write failing tests for a new CLI entrypoint on app_auth.py "
            "that mints and prints an App JWT and an installation token "
            "given BH_GITHUB_APP_ID, BH_GITHUB_APP_INSTALLATION_ID, "
            "BWS_PEM_SECRET_ID, BWS_ACCESS_TOKEN env vars, never printing "
            "PEM/secret material only resulting tokens. Issue 200 in "
            "glitchwerks/baton-harness."
        )
        call1_file_paths = [
            "src/baton_harness/chain/app_auth.py",
            "tests/chain/test_app_auth.py",
        ]
        result, diagnostics = _route(call1_text, call1_file_paths)
        _assert_redirected_to_test_implementer(result, diagnostics)
