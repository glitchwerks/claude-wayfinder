"""Tests for scoped hard-routing cutover and shadow schema version 2."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

import claude_wayfinder.match as _match_mod
from claude_wayfinder.match import _main as _main_mod
from claude_wayfinder.match._catalog import load_catalog
from claude_wayfinder.match._decide import decide
from claude_wayfinder.match._main import _parse_hard_routing_domains
from claude_wayfinder.match._match import build_features, score_entries
from tests.test_match.conftest import (
    REPO_ROOT,
    _catalog,
    _make_agent,
    _make_skill,
    _run,
)

# ---------------------------------------------------------------------------
# Worktree-shadowing guard (see agent-memory
# feedback_worktree_python_shadowing) — fail loudly, at collection time,
# if `claude_wayfinder` resolved to a package outside this worktree. The
# parent checkout's shared .venv installs `claude_wayfinder` in editable
# mode pointed at the PARENT's src/; running pytest here without
# PYTHONPATH set to this worktree's src/ silently exercises the wrong
# code and produces a false green.
# ---------------------------------------------------------------------------

_resolved_match_pkg = Path(_match_mod.__file__).resolve()
assert REPO_ROOT in _resolved_match_pkg.parents, (
    f"claude_wayfinder.match resolved to {_resolved_match_pkg}, which is "
    f"NOT under this worktree's root ({REPO_ROOT}). The parent checkout's "
    "shared .venv is shadowing this worktree's src/claude_wayfinder via "
    "its editable-install .pth file. Run pytest with "
    "PYTHONPATH=<this-worktree>/src set (or create a worktree-local venv) "
    "so tests exercise the WORKTREE's code, not the parent's."
)


_POSTURE_CATALOG = _catalog(
    [
        _make_agent(
            "investigator",
            keywords=[
                {"term": "investigate", "weight": 1.0},
                {"term": "analyze", "weight": 1.0},
            ],
        ),
        _make_agent(
            "code-writer",
            keywords=[{"term": "implement", "weight": 0.7}],
            path_globs=["**/*.py"],
            applicable_skills=["python"],
        ),
        _make_skill(
            "python",
            keywords=[{"term": "implement", "weight": 1.0}],
            applicable_agents=["code-writer"],
        ),
    ]
)

_POSTURE_INPUT: dict[str, Any] = {
    "task_description": "investigate analyze and implement this change",
    "file_paths": ["src/main.py"],
    "domain": "code",
    "posture": "build",
    "confidence": "high",
    "area_span": 1,
}

_GATED_CATALOG = _catalog(
    [
        _make_agent(
            "doc-writer",
            keywords=[
                {"term": "document", "weight": 1.0},
                {"term": "explain", "weight": 1.0},
            ],
        ),
        _make_agent(
            "code-writer",
            keywords=[{"term": "implement", "weight": 1.0}],
            path_globs=["**/*.py"],
        ),
    ]
)

_GATED_INPUT: dict[str, Any] = {
    "task_description": "document explain and implement the behavior",
    "file_paths": ["src/main.py"],
    "domain": "code",
}


def _read_single_log(log_path: Path) -> dict[str, Any]:
    """Read the sole JSON object from a matcher JSONL log.

    Args:
        log_path: Path to the matcher JSONL log.

    Returns:
        The single parsed log entry.
    """
    lines = [
        line
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(lines) == 1
    return json.loads(lines[0])


def _run_with_log(
    tmp_path: Path,
    *,
    stdin_obj: dict[str, Any],
    catalog: dict[str, Any],
    log_name: str,
    extra_env: dict[str, str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run the matcher and return stdout plus its single log entry.

    Args:
        tmp_path: Pytest temporary directory for catalog and log files.
        stdin_obj: Context dict sent to matcher stdin.
        catalog: Catalog envelope used for the run.
        log_name: File name for the JSONL log.
        extra_env: Additional environment variables for the subprocess.

    Returns:
        Pair of parsed stdout decision and parsed log entry.
    """
    log_path = tmp_path / log_name
    env = {"DISPATCH_LOG_PATH": str(log_path)}
    if extra_env:
        env.update(extra_env)
    completed = _run(
        stdin_obj,
        catalog,
        extra_env=env,
        tmp_path=tmp_path,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout), _read_single_log(log_path)


def _lexical_result(
    tmp_path: Path,
    stdin_obj: dict[str, Any],
    catalog: dict[str, Any],
) -> dict[str, Any]:
    """Compute the pure lexical result in-process for fixture inputs.

    Args:
        tmp_path: Pytest temporary directory for the catalog file.
        stdin_obj: Context dict used to build matcher features.
        catalog: Catalog envelope to parse and score.

    Returns:
        Result from ``decide()`` over the fixture's scored entries.
    """
    catalog_path = tmp_path / "lexical-catalog.json"
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
    entries = load_catalog(catalog_path)
    features = build_features(stdin_obj)
    scored_agents, scored_skills = score_entries(entries, features)
    return decide(scored_agents, scored_skills, features, entries)


def _without_stdout_metadata(result: dict[str, Any]) -> dict[str, Any]:
    """Remove stdout-only matcher attribution fields from a result.

    Args:
        result: Matcher stdout decision.

    Returns:
        Copy without ``catalog_hash`` and ``matcher_version``.
    """
    stripped = dict(result)
    stripped.pop("catalog_hash", None)
    stripped.pop("matcher_version", None)
    return stripped


class TestHardRoutingDomainParsing:
    """The domain flag fails closed and accepts only recognized tokens."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("", frozenset()),
            ("   ", frozenset()),
            ("is_any,code", frozenset({"is_any", "code"})),
            ("CODE , code", frozenset({"code"})),
            ("code,,CODE,", frozenset({"code"})),
        ],
    )
    def test_normalizes_and_deduplicates_recognized_tokens(
        self,
        monkeypatch: pytest.MonkeyPatch,
        value: str,
        expected: frozenset[str],
    ) -> None:
        """Normalized recognized values resolve without widening scope."""
        monkeypatch.setenv("DISPATCH_HARD_ROUTING_DOMAINS", value)
        assert _parse_hard_routing_domains() == expected

    def test_unset_flag_resolves_to_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An absent flag never grants consent to hard-route."""
        monkeypatch.delenv("DISPATCH_HARD_ROUTING_DOMAINS", raising=False)
        assert _parse_hard_routing_domains() == frozenset()

    def test_unknown_token_is_dropped_with_warning(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A typo warns and preserves only the valid subset."""
        monkeypatch.setenv("DISPATCH_HARD_ROUTING_DOMAINS", "is_any,cod")
        assert _parse_hard_routing_domains() == frozenset({"is_any"})
        assert "cod" in capsys.readouterr().err

    def test_parse_exception_fails_closed_with_warning(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Unexpected environment-read failures never enable routing."""
        with mock.patch.object(
            _main_mod.os.environ,
            "get",
            side_effect=RuntimeError("simulated parse failure"),
        ):
            assert _parse_hard_routing_domains() == frozenset()
        assert "simulated parse failure" in capsys.readouterr().err


class TestHardRoutingServing:
    """Hard routing serves compose only for explicitly enabled domains."""

    def test_absent_flag_serves_in_process_lexical_decision(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fail-closed default keeps stdout equal to lexical ``decide()``."""
        monkeypatch.delenv("DISPATCH_HARD_ROUTING_DOMAINS", raising=False)
        completed = _run(
            _POSTURE_INPUT,
            _POSTURE_CATALOG,
            tmp_path=tmp_path,
        )
        assert completed.returncode == 0, completed.stderr
        stdout = _without_stdout_metadata(json.loads(completed.stdout))
        expected = _lexical_result(tmp_path, _POSTURE_INPUT, _POSTURE_CATALOG)
        assert stdout == expected

    def test_posture_route_is_served_with_full_payload(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A flagged posture route becomes the complete stdout payload."""
        monkeypatch.delenv("DISPATCH_SHADOW", raising=False)
        stdout, _ = _run_with_log(
            tmp_path,
            stdin_obj=_POSTURE_INPUT,
            catalog=_POSTURE_CATALOG,
            log_name="posture.jsonl",
            extra_env={"DISPATCH_HARD_ROUTING_DOMAINS": "code"},
        )
        assert stdout["decision"] == "delegate"
        assert stdout["agent"] == "code-writer"
        assert stdout["disposition_source"] == "posture_routed"
        assert stdout["skills"] == ["python"]
        assert stdout["alternatives"]
        assert stdout["rationale"]

    def test_gated_fallback_excludes_out_of_domain_lexical_winner(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Flagged fallback delegates to the strongest surviving code agent."""
        monkeypatch.delenv("DISPATCH_HARD_ROUTING_DOMAINS", raising=False)
        monkeypatch.delenv("DISPATCH_SHADOW", raising=False)
        unflagged = _run(_GATED_INPUT, _GATED_CATALOG, tmp_path=tmp_path)
        assert unflagged.returncode == 0, unflagged.stderr
        unflagged_stdout = json.loads(unflagged.stdout)
        assert unflagged_stdout["agent"] == "doc-writer"

        flagged = _run(
            _GATED_INPUT,
            _GATED_CATALOG,
            extra_env={"DISPATCH_HARD_ROUTING_DOMAINS": "code"},
            tmp_path=tmp_path,
        )
        assert flagged.returncode == 0, flagged.stderr
        flagged_stdout = json.loads(flagged.stdout)
        assert flagged_stdout["decision"] == "delegate"
        assert flagged_stdout["agent"] == "code-writer"

    def test_shadow_kill_switch_disables_hard_routing(
        self, tmp_path: Path
    ) -> None:
        """``DISPATCH_SHADOW=0`` serves lexical and omits shadow data."""
        stdout, entry = _run_with_log(
            tmp_path,
            stdin_obj=_POSTURE_INPUT,
            catalog=_POSTURE_CATALOG,
            log_name="shadow-off.jsonl",
            extra_env={
                "DISPATCH_HARD_ROUTING_DOMAINS": "code",
                "DISPATCH_SHADOW": "0",
            },
        )
        expected = _lexical_result(tmp_path, _POSTURE_INPUT, _POSTURE_CATALOG)
        assert _without_stdout_metadata(stdout) == expected
        assert "shadow" not in entry

    def test_unflagged_domain_is_byte_identical_to_fully_unflagged_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Enabling ``is_any`` cannot alter a concrete ``code`` request."""
        monkeypatch.delenv("DISPATCH_HARD_ROUTING_DOMAINS", raising=False)
        unflagged = _run(_POSTURE_INPUT, _POSTURE_CATALOG, tmp_path=tmp_path)
        assert unflagged.returncode == 0, unflagged.stderr
        scoped_elsewhere = _run(
            _POSTURE_INPUT,
            _POSTURE_CATALOG,
            extra_env={"DISPATCH_HARD_ROUTING_DOMAINS": "is_any"},
            tmp_path=tmp_path,
        )
        assert scoped_elsewhere.returncode == 0, scoped_elsewhere.stderr
        assert scoped_elsewhere.stdout == unflagged.stdout

    def test_compose_exception_reverts_to_lexical_result(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A compose failure cannot escape or replace lexical stdout."""
        catalog_path = tmp_path / "exception-catalog.json"
        catalog_path.write_text(json.dumps(_POSTURE_CATALOG), encoding="utf-8")
        log_path = tmp_path / "exception.jsonl"
        monkeypatch.setenv("DISPATCH_CATALOG_PATH", str(catalog_path))
        monkeypatch.setenv("DISPATCH_LOG_PATH", str(log_path))
        monkeypatch.setenv("DISPATCH_HARD_ROUTING_DOMAINS", "code")
        monkeypatch.delenv("DISPATCH_SHADOW", raising=False)
        monkeypatch.delenv("DISPATCH_OVERRIDES_PATH", raising=False)
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(_POSTURE_INPUT)))
        captured_stdout = io.StringIO()
        monkeypatch.setattr(sys, "stdout", captured_stdout)

        with mock.patch.object(
            _main_mod,
            "compose_route",
            side_effect=RuntimeError("simulated compose failure"),
        ):
            _main_mod.main([])

        stdout = _without_stdout_metadata(json.loads(captured_stdout.getvalue()))
        expected = _lexical_result(tmp_path, _POSTURE_INPUT, _POSTURE_CATALOG)
        assert stdout == expected
        assert "shadow" not in _read_single_log(log_path)


class TestHardRoutingShadowSchema:
    """Schema v2 records algorithms separately from the served decision."""

    def test_agreement_compares_algorithms_when_compose_is_served(
        self, tmp_path: Path
    ) -> None:
        """Serving compose does not make lexical/compose agreement trivial."""
        stdout, entry = _run_with_log(
            tmp_path,
            stdin_obj=_POSTURE_INPUT,
            catalog=_POSTURE_CATALOG,
            log_name="agreement.jsonl",
            extra_env={
                "DISPATCH_HARD_ROUTING_DOMAINS": "code",
                "DISPATCH_SHADOW": "1",
            },
        )
        shadow = entry["shadow"]
        assert stdout["agent"] == "code-writer"
        assert shadow["served_agent"] == "code-writer"
        assert shadow["live_agent"] == "investigator"
        assert shadow["agreement"] is False

    def test_schema_fields_are_populated_for_compose_and_lexical_rows(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Every shadow row identifies its schema, flag, and served arm."""
        flagged_stdout, flagged_entry = _run_with_log(
            tmp_path,
            stdin_obj=_POSTURE_INPUT,
            catalog=_POSTURE_CATALOG,
            log_name="schema-compose.jsonl",
            extra_env={
                "DISPATCH_HARD_ROUTING_DOMAINS": "is_any,code",
                "DISPATCH_SHADOW": "1",
            },
        )
        flagged_shadow = flagged_entry["shadow"]
        assert flagged_shadow["served_arm"] == "compose"
        assert flagged_shadow["served_agent"] == flagged_stdout["agent"]
        assert flagged_shadow["served_decision"] == flagged_stdout["decision"]
        assert flagged_shadow["served_confidence"] == pytest.approx(
            flagged_stdout["confidence"]
        )
        assert (
            flagged_shadow["served_disposition_source"]
            == flagged_stdout["disposition_source"]
        )
        assert flagged_shadow["shadow_schema_version"] == 2
        assert flagged_shadow["hard_routing_domains"] == ["code", "is_any"]

        monkeypatch.delenv("DISPATCH_HARD_ROUTING_DOMAINS", raising=False)
        lexical_stdout, lexical_entry = _run_with_log(
            tmp_path,
            stdin_obj=_POSTURE_INPUT,
            catalog=_POSTURE_CATALOG,
            log_name="schema-lexical.jsonl",
        )
        lexical_shadow = lexical_entry["shadow"]
        assert lexical_shadow["served_arm"] == "lexical"
        assert lexical_shadow["served_agent"] == lexical_stdout.get("agent")
        assert lexical_shadow["served_decision"] == lexical_stdout["decision"]
        assert lexical_shadow["shadow_schema_version"] == 2
        assert lexical_shadow["hard_routing_domains"] == []
