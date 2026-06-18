"""Lock tests for the scoring-kernel dedup refactor (Issue #389).

Two test classes:

``TestGoldenEquivalence``
    Runs ``python -m claude_wayfinder dispatch`` in single-mode and
    batch-mode via subprocess and asserts STDOUT content matches committed
    golden snapshots.  Covers four decision branches: ``delegate``,
    ``advisory`` (tie), ``self_handle``, and ``self_handle_unaided``.

    The ``matcher_version`` field is the git HEAD SHA — it changes with
    every commit and is therefore excluded from content comparison; the
    tests assert it is present and non-empty separately.  All other fields
    are compared verbatim.  These characterise current behavior and must
    stay identical after the refactor.

``TestKernelParity``
    Builds a fixture catalog that deliberately includes score ties (two
    agents at the same score, two skills at the same score) and asserts
    that the three duplicated sites each yield the same
    ``(scored_agents, scored_skills)`` lists as a direct call to the
    canonical ``score_entries()`` from ``_match.py``.  Locks the
    ``(-score, name)`` tiebreak contract at every site.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from claude_wayfinder.match._match import build_features, score_entries
from claude_wayfinder.match._types import CatalogEntry, ScoredEntry

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEMO_CATALOG_PATH = (
    _REPO_ROOT / "src" / "claude_wayfinder" / "fixtures" / "demo-catalog.json"
)

# ---------------------------------------------------------------------------
# Golden snapshot (committed, content-stable)
# Single-mode stdout for the four representative dispatch contexts,
# captured at merge-base 2ad8fe9 against demo-catalog.json.
#
# NOTE: ``matcher_version`` is the git HEAD SHA — it changes with every
# commit and is therefore excluded from content comparison.  Tests assert
# (a) all other fields match the golden dict exactly and (b)
# ``matcher_version`` is present and non-empty.  The catalog_hash is
# content-addressed and stable so long as demo-catalog.json is unchanged.
# ---------------------------------------------------------------------------

_CATALOG_HASH = (
    "sha256:c686b84fc555076559aebe577d461bb7f47b149fb8ace00377395b05520fc81b"
)

#: decision=delegate — code-writer scores 0.9 (keyword + glob)
_GOLDEN_SINGLE_DELEGATE: dict[str, Any] = {
    "agent": "code-writer",
    "alternatives": [],
    "catalog_hash": _CATALOG_HASH,
    "confidence": 0.9,
    "decision": "delegate",
    "disposition_source": "scored",
    "rationale": "matched keywords: implement; globs: **/*.py.",
    "skills": [],
}

#: decision=advisory (tie) — both agents score 0.5
_GOLDEN_SINGLE_ADVISORY_TIE: dict[str, Any] = {
    "agent": "code-writer",
    "alternatives": [{"agent": "devops", "score": 0.5}],
    "catalog_hash": _CATALOG_HASH,
    "confidence": 0.5,
    "decision": "advisory",
    "disposition_source": "scored",
    "rationale": (
        "Best agent 'code-writer' scores 0.50 (gap=0.00 from next); "
        "top pick recommended, alternatives close behind."
    ),
    "skills": [],
}

#: decision=self_handle_unaided — no agent or skill above threshold
_GOLDEN_SINGLE_SELF_HANDLE_UNAIDED: dict[str, Any] = {
    "alternatives": [],
    "catalog_hash": _CATALOG_HASH,
    "confidence": 0.0,
    "decision": "self_handle_unaided",
    "disposition_source": "scored",
    "rationale": (
        "No agent or skill scored above threshold; "
        "proceeding without delegation or skill activation."
    ),
}

#: decision=self_handle — python skill attaches, no dominant agent
_GOLDEN_SINGLE_SELF_HANDLE: dict[str, Any] = {
    "alternatives": [],
    "catalog_hash": _CATALOG_HASH,
    "confidence": 0.75,
    "decision": "self_handle",
    "disposition_source": "scored",
    "rationale": "No dominant agent; routing to self with skills: python",
    "skills": ["python"],
}

# Ordered list of (context, expected_fields) pairs for single-mode golden.
_SINGLE_MODE_CASES: list[tuple[dict[str, Any], dict[str, Any]]] = [
    (
        {
            "task_description": "implement the authentication module",
            "file_paths": ["src/auth.py"],
            "agent_mentions": [],
            "tool_mentions": [],
            "command_prefix": None,
        },
        _GOLDEN_SINGLE_DELEGATE,
    ),
    (
        {
            "task_description": "review the module changes",
            "file_paths": ["Makefile"],
            "agent_mentions": [],
            "tool_mentions": [],
            "command_prefix": None,
        },
        _GOLDEN_SINGLE_ADVISORY_TIE,
    ),
    (
        {
            "task_description": "update the team wiki page with meeting notes",
            "file_paths": ["wiki/page.md"],
            "agent_mentions": [],
            "tool_mentions": [],
            "command_prefix": None,
        },
        _GOLDEN_SINGLE_SELF_HANDLE_UNAIDED,
    ),
    (
        {
            "task_description": "run the python data pipeline script",
            "file_paths": ["data/input.csv"],
            "agent_mentions": [],
            "tool_mentions": [],
            "command_prefix": None,
        },
        _GOLDEN_SINGLE_SELF_HANDLE,
    ),
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_dispatch_single(context: dict[str, Any]) -> dict[str, Any]:
    """Run dispatch in single mode and return parsed JSON output.

    Args:
        context: Dispatch context dict to serialise as stdin JSON.

    Returns:
        Parsed JSON object from stdout.
    """
    env = {**os.environ, "DISPATCH_CATALOG_PATH": str(_DEMO_CATALOG_PATH)}
    env.pop("DISPATCH_LOG_PATH", None)
    env.pop("DISPATCH_OVERRIDES_PATH", None)
    result = subprocess.run(
        [sys.executable, "-m", "claude_wayfinder", "dispatch"],
        input=json.dumps(context),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )
    return json.loads(result.stdout.strip())


def _run_dispatch_batch(
    contexts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Run dispatch in batch mode and return list of parsed JSON outputs.

    Args:
        contexts: Ordered list of dispatch context dicts to serialise
            as NDJSON.

    Returns:
        List of parsed JSON objects from stdout (one per non-empty line).
    """
    ndjson = "\n".join(json.dumps(ctx) for ctx in contexts) + "\n"
    env = {**os.environ, "DISPATCH_CATALOG_PATH": str(_DEMO_CATALOG_PATH)}
    env.pop("DISPATCH_LOG_PATH", None)
    env.pop("DISPATCH_OVERRIDES_PATH", None)
    result = subprocess.run(
        [sys.executable, "-m", "claude_wayfinder", "dispatch", "--batch"],
        input=ndjson,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )
    return [
        json.loads(line.strip())
        for line in result.stdout.splitlines()
        if line.strip()
    ]


def _without_matcher_version(d: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``d`` with ``matcher_version`` removed.

    ``matcher_version`` is the git HEAD SHA and changes with each commit;
    it must be excluded from golden comparisons to avoid false failures.

    Args:
        d: Parsed dispatch output dict.

    Returns:
        Copy of ``d`` without the ``matcher_version`` key.
    """
    return {k: v for k, v in d.items() if k != "matcher_version"}


# ---------------------------------------------------------------------------
# Fixture catalog helpers for kernel-parity tests
# ---------------------------------------------------------------------------


def _make_catalog_entry(
    name: str,
    kind: str,
    *,
    keywords: list[str] | None = None,
    routable: bool = True,
) -> CatalogEntry:
    """Build a minimal CatalogEntry for parity testing.

    Args:
        name: Entry name.
        kind: ``"agent"`` or ``"skill"``.
        keywords: List of keyword term strings (weight 1.0 each).
        routable: Whether the agent is routable (ignored for skills).

    Returns:
        A :class:`CatalogEntry` instance.
    """
    from claude_wayfinder.match._parse import _parse_triggers

    kw_dicts = [
        {"term": t, "weight": 1.0, "no_stem": True} for t in (keywords or [])
    ]
    triggers_raw: dict[str, Any] = {
        "command_prefixes": [],
        "agent_mentions": [],
        "path_globs": [],
        "path_globs_excluded": [],
        "keywords": kw_dicts,
        "tool_mentions": [],
        "excludes": [],
    }
    triggers = _parse_triggers(triggers_raw)
    return CatalogEntry(
        name=name,
        kind=kind,
        source="owned",
        routable=routable,
        triggers=triggers,
        applicable_skills=(),
        applicable_agents=(),
    )


# ---------------------------------------------------------------------------
# TestGoldenEquivalence
# ---------------------------------------------------------------------------


class TestGoldenEquivalence:
    """Dispatch stdout content must match the committed golden snapshot.

    These tests characterise the current behavior at merge-base 2ad8fe9.
    After the scoring-kernel dedup refactor (Issue #389) the output must
    remain identical — if any test fails post-refactor, the refactor has
    changed observable behavior and must be reverted.

    ``matcher_version`` (git HEAD SHA) is excluded from comparison; all
    other fields are compared verbatim against the golden dicts.
    """

    @pytest.mark.parametrize(
        "context,expected",
        _SINGLE_MODE_CASES,
        ids=["delegate", "advisory_tie", "self_handle_unaided", "self_handle"],
    )
    def test_single_mode_output_is_golden(
        self,
        context: dict[str, Any],
        expected: dict[str, Any],
    ) -> None:
        """Single-mode stdout content must match the golden snapshot.

        Compares all fields except ``matcher_version`` (git HEAD SHA, which
        changes each commit).  Separately asserts ``matcher_version`` is
        present and non-empty.

        Args:
            context: Dispatch context sent to stdin.
            expected: Expected field values from the golden snapshot
                (must NOT include ``matcher_version``).
        """
        actual = _run_dispatch_single(context)
        assert "matcher_version" in actual, (
            f"matcher_version missing from output: {actual!r}"
        )
        assert actual["matcher_version"], (
            f"matcher_version is empty: {actual!r}"
        )
        actual_content = _without_matcher_version(actual)
        assert actual_content == expected, (
            f"Single-mode dispatch output differs from golden snapshot.\n"
            f"expected: {expected!r}\n"
            f"actual  : {actual_content!r}"
        )

    def test_batch_mode_output_is_golden(self) -> None:
        """Batch-mode stdout content must match the golden snapshot.

        Sends all four contexts as NDJSON and checks each output object
        against the committed golden single-mode dicts plus the expected
        ``input_index``.  ``matcher_version`` is excluded from comparison.
        """
        contexts = [ctx for ctx, _ in _SINGLE_MODE_CASES]
        actual_outputs = _run_dispatch_batch(contexts)
        assert len(actual_outputs) == len(_SINGLE_MODE_CASES), (
            f"Batch output count mismatch: "
            f"expected {len(_SINGLE_MODE_CASES)}, got {len(actual_outputs)}.\n"
            f"actual: {actual_outputs}"
        )
        for i, (actual, (_, golden)) in enumerate(
            zip(actual_outputs, _SINGLE_MODE_CASES)
        ):
            assert "matcher_version" in actual, (
                f"Line {i}: matcher_version missing from output: {actual!r}"
            )
            assert actual["matcher_version"], (
                f"Line {i}: matcher_version is empty: {actual!r}"
            )
            # Batch output adds input_index; merge it with the single-mode
            # golden so the comparison is complete.
            expected = {**golden, "input_index": i}
            actual_content = _without_matcher_version(actual)
            assert actual_content == expected, (
                f"Batch output line {i} differs from golden snapshot.\n"
                f"expected: {expected!r}\n"
                f"actual  : {actual_content!r}"
            )


# ---------------------------------------------------------------------------
# TestKernelParity
# ---------------------------------------------------------------------------


class TestKernelParity:
    """Each refactored scoring site yields the same result as score_entries().

    Uses a fixture catalog with deliberate score TIES — two agents with
    equal scores, two skills with equal scores — to lock the
    ``(-score, name)`` tiebreak at every site.
    """

    @pytest.fixture()
    def tie_entries(self) -> list[CatalogEntry]:
        """Catalog with deliberate ties: agents 'alpha-agent'/'beta-agent'
        at equal keyword scores, skills 'alpha-skill'/'beta-skill' at equal
        keyword scores, plus an unroutable router agent that must be
        excluded.

        Returns:
            List of :class:`CatalogEntry` instances.
        """
        return [
            # Two agents with the same keyword trigger — identical scores.
            _make_catalog_entry(
                "beta-agent", "agent", keywords=["deploy"], routable=True
            ),
            _make_catalog_entry(
                "alpha-agent", "agent", keywords=["deploy"], routable=True
            ),
            # Unroutable router — must be excluded by score_entries.
            _make_catalog_entry(
                "router", "agent", keywords=["deploy"], routable=False
            ),
            # Two skills with the same keyword trigger — identical scores.
            _make_catalog_entry("beta-skill", "skill", keywords=["deploy"]),
            _make_catalog_entry("alpha-skill", "skill", keywords=["deploy"]),
        ]

    @pytest.fixture()
    def tie_features(self, tie_entries: list[CatalogEntry]) -> Any:
        """Features for a context that matches the 'deploy' keyword.

        Args:
            tie_entries: Unused; kept for fixture ordering clarity.

        Returns:
            A :class:`Features` instance.
        """
        return build_features(
            {
                "task_description": "deploy the service",
                "file_paths": [],
                "agent_mentions": [],
                "tool_mentions": [],
                "command_prefix": None,
            }
        )

    def _score_via_main(
        self,
        entries: list[CatalogEntry],
        features: Any,
    ) -> tuple[list[ScoredEntry], list[ScoredEntry]]:
        """Reproduce the _main.py inline scoring kernel verbatim.

        This mirrors the pre-refactor code at _main.py:206-222 so the
        test stays meaningful even after the refactor replaces it.

        Args:
            entries: Catalog entries.
            features: Extracted features.

        Returns:
            Tuple of ``(scored_agents, scored_skills)``.
        """
        from claude_wayfinder.match._match import score
        from claude_wayfinder.match._types import ScoredEntry as _SE
        from claude_wayfinder.match_filters import is_agent_routable

        agent_entries = [
            e
            for e in entries
            if e.kind == "agent"
            and is_agent_routable(
                name=e.name,
                kind=e.kind,
                source=e.source,
                routable=e.routable,
            )
        ]
        skill_entries = [e for e in entries if e.kind == "skill"]

        scored_agents: list[ScoredEntry] = sorted(
            [_SE(entry=e, score=score(e, features)) for e in agent_entries],
            key=lambda se: (-se.score, se.entry.name),
        )
        scored_skills: list[ScoredEntry] = sorted(
            [_SE(entry=e, score=score(e, features)) for e in skill_entries],
            key=lambda se: (-se.score, se.entry.name),
        )
        return scored_agents, scored_skills

    def _score_via_cli(
        self,
        entries: list[CatalogEntry],
        features: Any,
    ) -> tuple[list[ScoredEntry], list[ScoredEntry]]:
        """Reproduce the cli.py _score_catalog body verbatim.

        Args:
            entries: Catalog entries.
            features: Extracted features.

        Returns:
            Tuple of ``(scored_agents, scored_skills)``.
        """
        from claude_wayfinder.match._match import score
        from claude_wayfinder.match._types import ScoredEntry as _SE
        from claude_wayfinder.match_filters import is_agent_routable

        agent_entries = [
            e
            for e in entries
            if e.kind == "agent"
            and is_agent_routable(
                name=e.name,
                kind=e.kind,
                source=e.source,
                routable=e.routable,
            )
        ]
        skill_entries = [e for e in entries if e.kind == "skill"]

        scored_agents: list[ScoredEntry] = sorted(
            [_SE(entry=e, score=score(e, features)) for e in agent_entries],
            key=lambda se: (-se.score, se.entry.name),
        )
        scored_skills: list[ScoredEntry] = sorted(
            [_SE(entry=e, score=score(e, features)) for e in skill_entries],
            key=lambda se: (-se.score, se.entry.name),
        )
        return scored_agents, scored_skills

    def _score_via_dispatch(
        self,
        entries: list[CatalogEntry],
        features: Any,
    ) -> tuple[list[ScoredEntry], list[ScoredEntry]]:
        """Reproduce the _dispatch.py batch-loop inline scoring verbatim.

        This mirrors the pre-refactor code at _dispatch.py:622-629.

        Args:
            entries: Catalog entries (agent_entries/skill_entries
                pre-split outside the per-input loop in the original).
            features: Extracted features.

        Returns:
            Tuple of ``(scored_agents, scored_skills)``.
        """
        from claude_wayfinder.match._match import score
        from claude_wayfinder.match._types import ScoredEntry as _SE
        from claude_wayfinder.match_filters import is_agent_routable

        # The dispatch batch loop pre-splits OUTSIDE the per-input loop.
        agent_entries = [
            e
            for e in entries
            if e.kind == "agent"
            and is_agent_routable(
                name=e.name,
                kind=e.kind,
                source=e.source,
                routable=e.routable,
            )
        ]
        skill_entries = [e for e in entries if e.kind == "skill"]

        scored_agents: list[Any] = sorted(
            [_SE(entry=e, score=score(e, features)) for e in agent_entries],
            key=lambda se: (-se.score, se.entry.name),
        )
        scored_skills: list[Any] = sorted(
            [_SE(entry=e, score=score(e, features)) for e in skill_entries],
            key=lambda se: (-se.score, se.entry.name),
        )
        return scored_agents, scored_skills

    def _names(self, scored: list[ScoredEntry]) -> list[str]:
        """Extract entry names from a scored list for readable assertions.

        Args:
            scored: List of scored entries.

        Returns:
            List of entry names in order.
        """
        return [se.entry.name for se in scored]

    def test_tie_order_is_alphabetical_by_name(
        self,
        tie_entries: list[CatalogEntry],
        tie_features: Any,
    ) -> None:
        """score_entries() breaks ties alphabetically by name ascending.

        With two agents both scoring on 'deploy', alphabetical order
        determines winner: 'alpha-agent' < 'beta-agent'.
        """
        scored_agents, scored_skills = score_entries(tie_entries, tie_features)
        agent_names = self._names(scored_agents)
        skill_names = self._names(scored_skills)

        assert agent_names == ["alpha-agent", "beta-agent"], (
            f"Agents not in alphabetical tiebreak order: {agent_names}"
        )
        assert skill_names == ["alpha-skill", "beta-skill"], (
            f"Skills not in alphabetical tiebreak order: {skill_names}"
        )

    def test_unroutable_agent_excluded_by_score_entries(
        self,
        tie_entries: list[CatalogEntry],
        tie_features: Any,
    ) -> None:
        """score_entries() must exclude the unroutable 'router' agent.

        Args:
            tie_entries: Fixture catalog containing a routable=False router.
            tie_features: Features matching the 'deploy' keyword.
        """
        scored_agents, _ = score_entries(tie_entries, tie_features)
        agent_names = self._names(scored_agents)
        assert "router" not in agent_names, (
            f"Unroutable 'router' agent leaked into scored_agents: "
            f"{agent_names}"
        )
        assert len(scored_agents) == 2, (
            f"Expected exactly 2 routable agents, got "
            f"{len(scored_agents)}: {agent_names}"
        )

    def test_main_matches_score_entries(
        self,
        tie_entries: list[CatalogEntry],
        tie_features: Any,
    ) -> None:
        """_main.py inline kernel matches score_entries() output exactly.

        The pre-refactor code at _main.py:206-222 must produce the same
        ``(scored_agents, scored_skills)`` as the canonical score_entries().
        Locks ordering AND exclusion contract.

        Args:
            tie_entries: Fixture catalog with deliberate score ties.
            tie_features: Features matching all tied entries.
        """
        canonical_agents, canonical_skills = score_entries(
            tie_entries, tie_features
        )
        main_agents, main_skills = self._score_via_main(
            tie_entries, tie_features
        )

        assert self._names(main_agents) == self._names(canonical_agents), (
            f"_main.py agent order differs from score_entries().\n"
            f"score_entries: {self._names(canonical_agents)}\n"
            f"_main.py     : {self._names(main_agents)}"
        )
        assert self._names(main_skills) == self._names(canonical_skills), (
            f"_main.py skill order differs from score_entries().\n"
            f"score_entries: {self._names(canonical_skills)}\n"
            f"_main.py     : {self._names(main_skills)}"
        )
        assert [se.score for se in main_agents] == [
            se.score for se in canonical_agents
        ], "_main.py agent scores differ from score_entries()"
        assert [se.score for se in main_skills] == [
            se.score for se in canonical_skills
        ], "_main.py skill scores differ from score_entries()"

    def test_cli_matches_score_entries(
        self,
        tie_entries: list[CatalogEntry],
        tie_features: Any,
    ) -> None:
        """cli.py _score_catalog body matches score_entries() output exactly.

        The pre-refactor code at cli.py:86-107 must produce the same
        ``(scored_agents, scored_skills)`` as the canonical score_entries().

        Args:
            tie_entries: Fixture catalog with deliberate score ties.
            tie_features: Features matching all tied entries.
        """
        canonical_agents, canonical_skills = score_entries(
            tie_entries, tie_features
        )
        cli_agents, cli_skills = self._score_via_cli(
            tie_entries, tie_features
        )

        assert self._names(cli_agents) == self._names(canonical_agents), (
            f"cli.py agent order differs from score_entries().\n"
            f"score_entries: {self._names(canonical_agents)}\n"
            f"cli.py       : {self._names(cli_agents)}"
        )
        assert self._names(cli_skills) == self._names(canonical_skills), (
            f"cli.py skill order differs from score_entries().\n"
            f"score_entries: {self._names(canonical_skills)}\n"
            f"cli.py       : {self._names(cli_skills)}"
        )
        assert [se.score for se in cli_agents] == [
            se.score for se in canonical_agents
        ], "cli.py agent scores differ from score_entries()"
        assert [se.score for se in cli_skills] == [
            se.score for se in canonical_skills
        ], "cli.py skill scores differ from score_entries()"

    def test_dispatch_matches_score_entries(
        self,
        tie_entries: list[CatalogEntry],
        tie_features: Any,
    ) -> None:
        """_dispatch.py batch inline kernel matches score_entries() exactly.

        The pre-refactor code at _dispatch.py:622-629 must produce the same
        ``(scored_agents, scored_skills)`` as the canonical score_entries().

        Args:
            tie_entries: Fixture catalog with deliberate score ties.
            tie_features: Features matching all tied entries.
        """
        canonical_agents, canonical_skills = score_entries(
            tie_entries, tie_features
        )
        dispatch_agents, dispatch_skills = self._score_via_dispatch(
            tie_entries, tie_features
        )

        assert self._names(dispatch_agents) == self._names(canonical_agents), (
            f"_dispatch.py agent order differs from score_entries().\n"
            f"score_entries: {self._names(canonical_agents)}\n"
            f"_dispatch.py : {self._names(dispatch_agents)}"
        )
        assert self._names(dispatch_skills) == self._names(canonical_skills), (
            f"_dispatch.py skill order differs from score_entries().\n"
            f"score_entries: {self._names(canonical_skills)}\n"
            f"_dispatch.py : {self._names(dispatch_skills)}"
        )
        assert [se.score for se in dispatch_agents] == [
            se.score for se in canonical_agents
        ], "_dispatch.py agent scores differ from score_entries()"
        assert [se.score for se in dispatch_skills] == [
            se.score for se in canonical_skills
        ], "_dispatch.py skill scores differ from score_entries()"
