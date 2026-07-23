"""Tests for scripts/shadow-kc-report.py (issue #485, M15-6c).

Spec sources: docs/superpowers/plans/2026-07-19-m15-6-shadow-kc-report.md
Sec 4.2 (per-KC formulas, reused unchanged from ``scripts/corpus/eval/_kc.py``),
Sec 4.4 (report structure -- both whole-sample and gated-eligible-subset RC/CW
cuts, caller-label-match breakdown, matcher_version provenance guard), Sec 4.5
(Phase B deliverables + the provenance-guard test matrix); issue #484 body
Sec 4.5 item 2; issue #485 body.

This test module authors the contract in advance of the implementation
(``scripts/shadow-kc-report.py`` does not exist yet) per the test-implementer
/ code-implementer split -- every test below is expected to fail (via the
module-scoped ``kc_report_module`` fixture raising ``FileNotFoundError``
when it tries to load the not-yet-existing script) until that script exists.

RED -- written before implementation.

Design judgment calls made by the test-implementer (documented for the
router / code-implementer, since no prior contract existed for these):

1. **Exit-code resolution on a matcher_version provenance-guard failure.**
   The router briefing that produced this test file says a provenance
   mismatch should degrade like an INSUFFICIENT-DATA verdict (warn, exit 0).
   That contradicts three more durable sources this briefing itself names:
   plan Sec 4.4/4.5 ("emit a hard warning and exit non-zero ... not silently
   pick the first/majority value" / "... exiting non-zero on divergence in
   any of them"), issue #484 Sec 4.5 item 2 ("hard warning + non-zero exit on
   divergence or unresolvable version" -- the exact text the briefing cites
   as its own source), and issue #485 step 1 ("if it diverges, re-accumulate
   shadow data ... before running", a block-and-stop framing). All guard
   tests below therefore assert a **non-zero** exit code on a provenance
   failure, matching the plan/issues. **This conflicts with the briefing's
   items 4 and 6 -- flagged for the router to confirm before this contract
   is frozen for the code-implementer.** Both sources agree the guard must
   also emit a legible warning (stderr + reflected in the report) rather
   than an unhandled crash; that much is asserted without conflict.
2. **`--repo-root PATH` (new, optional CLI flag, default: cwd).** The
   provenance guard's git-state comparison is inherently tied to a real git
   worktree containing ``src/claude_wayfinder/match/_compose.py`` and
   ``_cells.py``. To keep the guard's tests hermetic (not coupled to this
   worktree's own mutable git history), this suite designs a `--repo-root`
   flag so tests can point the guard at disposable, purpose-built temp git
   repos. This is an additive CLI surface, not a behavior change to any
   spec'd flag.
3. **`--json` output schema.** The spec only requires "machine-readable
   JSON output"; no prior schema exists. This suite designs: a top-level
   object with a ``"criteria"`` key (a list of ``{"kc", "status",
   "metrics"}`` objects, one per KC-1..KC-5, mirroring
   ``scripts.corpus.eval._kc.KCVerdict`` field names) and an
   ``"overall_recommendation"`` string key.

Public API designed here (the implementer builds to match):

    scripts/shadow-kc-report.py exposes ``main(argv: list[str] | None) -> int``
    (mirrors the ``scripts/corpus/eval/__main__.py`` convention).

    CLI flags: ``--corpus PATH`` (required), ``--labels PATH`` (required),
    ``--json PATH`` (optional), ``--repo-root PATH`` (optional, default cwd).
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

# Load the script from its path (it lives under scripts/, is not part of
# the installed package, and its filename is not a valid Python
# identifier), mirroring the loader pattern used by
# tests/test_scripts/test_shadow_strip_for_labeling.py.
_SCRIPT_PATH = Path(__file__).parent.parent.parent / "scripts" / "shadow-kc-report.py"


@pytest.fixture(scope="module")
def kc_report_module() -> ModuleType:
    """Load ``scripts/shadow-kc-report.py`` as a module.

    Returns:
        The loaded module, exposing ``main``.

    Raises:
        FileNotFoundError: If the script does not exist yet (the expected
            RED state before the implementation lands).
    """
    spec = importlib.util.spec_from_file_location("shadow_kc_report", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["shadow_kc_report"] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        sys.modules.pop("shadow_kc_report", None)
        raise
    return mod


# ---------------------------------------------------------------------------
# Synthetic corpus-row / gold-label builders (mirror
# tests/test_corpus_eval/test_kc.py's _row()/_gold() idiom, extended to a
# full raw JSONL record so fixtures round-trip through
# scripts.corpus.eval._reader.load_corpus rather than being handed to the
# KC functions as in-memory dicts).
# ---------------------------------------------------------------------------


def _corpus_row(
    corpus_id: int,
    *,
    domain: str | None = "code",
    posture: str | None = "build",
    confidence: str | None = "high",
    area_span: int = 1,
    shadow_decision: str = "delegate",
    shadow_agent: str | None = "code-writer",
    shadow_confidence: float = 0.9,
    live_decision: str = "delegate",
    live_agent: str | None = "code-writer",
    live_confidence: float = 0.9,
    posture_routed: bool | None = False,
    gated_agent_names: list[str] | None = None,
    matcher_version: str = "abc1234",
) -> dict[str, Any]:
    """Build one raw corpus JSONL record matching the on-disk shadow schema."""
    return {
        "type": "matcher_decision",
        "corpus_id": corpus_id,
        "matcher_version": matcher_version,
        "input": {
            "task_description": "synthetic",
            "file_paths": [],
            "agent_mentions": [],
            "tool_mentions": [],
            "command_prefix": None,
            "domain": domain,
            "posture": posture,
            "confidence": confidence,
            "area_span": area_span,
        },
        "output": {},
        "stratum": {
            "decision_band": "delegate",
            "td_length_band": "short",
            "file_paths_present": False,
        },
        "shadow": {
            "domain": domain,
            "posture": posture,
            "confidence": confidence,
            "area_span": area_span,
            "live_decision": live_decision,
            "live_agent": live_agent,
            "live_confidence": live_confidence,
            "live_disposition_source": "decide",
            "shadow_decision": shadow_decision,
            "shadow_agent": shadow_agent,
            "shadow_confidence": shadow_confidence,
            "shadow_disposition_source": "decide",
            "gated_agent_names": gated_agent_names,
            "posture_preferred": None,
            "posture_routed": posture_routed,
            "branch": None,
            "lexical_agreement": None,
            "posture_veto_reason": None,
            "agreement": shadow_agent == live_agent,
        },
    }


def _corpus_row_missing_input_key(
    corpus_id: int,
    missing_key: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Build a corpus row whose ``input`` dict entirely omits a key.

    Mirrors real dispatch-context JSON, which is permitted to omit an
    optional caller-input field (``domain``/``posture``/``confidence``/
    ``area_span``) entirely rather than setting it to ``null`` -- per
    the dispatch skill's contract ("omit or pass null"). Distinct from
    ``_corpus_row(..., domain=None)``: this produces a row where
    ``"domain" not in row["input"]`` is True, not merely
    ``row["input"]["domain"] is None``.

    Args:
        corpus_id: Synthetic corpus row ID.
        missing_key: The key to delete from ``row["input"]``.
        **kwargs: Forwarded to ``_corpus_row``.

    Returns:
        A raw corpus JSONL record with ``missing_key`` entirely absent
        from ``row["input"]``.
    """
    row = _corpus_row(corpus_id, **kwargs)
    del row["input"][missing_key]
    return row


def _gold_row(
    corpus_id: int,
    gold_agent: str = "code-writer",
    domain: str = "code",
    posture: str = "build",
    is_any: bool = False,
    area_span: int = 1,
) -> dict[str, Any]:
    """Build one gold-label JSONL record."""
    return {
        "corpus_id": corpus_id,
        "domain": domain,
        "posture": posture,
        "gold_agent": gold_agent,
        "is_any": is_any,
        "area_span": area_span,
    }


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    """Write rows as newline-delimited JSON to ``path``."""
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def _run_main(
    mod: ModuleType,
    corpus: Path,
    labels: Path,
    repo_root: Path,
    json_path: Path | None = None,
) -> int:
    """Invoke ``mod.main`` with the standard required flags.

    Args:
        mod: The loaded ``shadow-kc-report.py`` module.
        corpus: Path to the corpus JSONL fixture.
        labels: Path to the gold-labels JSONL fixture.
        repo_root: Path passed as ``--repo-root``.
        json_path: Optional path passed as ``--json``.

    Returns:
        The CLI's exit code.
    """
    argv = [
        "--corpus",
        str(corpus),
        "--labels",
        str(labels),
        "--repo-root",
        str(repo_root),
    ]
    if json_path is not None:
        argv += ["--json", str(json_path)]
    return mod.main(argv)


def _kc_status_appears(report: str, kc: str, status: str) -> bool:
    """Return True if some report line mentions both ``kc`` and ``status``."""
    return any(kc in line and status in line for line in report.splitlines())


_HEADING_RE = re.compile(r"^#{1,6}\s")


def _extract_section(report: str, start_pattern: str) -> str:
    """Return the report text from the first line matching ``start_pattern``
    up to (but not including) the next Markdown heading line, or EOF.

    Args:
        report: Full report text.
        start_pattern: Case-insensitive regex identifying the section's
            opening line.

    Returns:
        The matched section's text block.

    Raises:
        AssertionError: If no line matches ``start_pattern``.
    """
    lines = report.splitlines()
    pat = re.compile(start_pattern, re.IGNORECASE)
    start_idx = next((i for i, line in enumerate(lines) if pat.search(line)), None)
    assert start_idx is not None, (
        f"no section header matched {start_pattern!r} in report:\n{report}"
    )
    end_idx = len(lines)
    for j in range(start_idx + 1, len(lines)):
        if _HEADING_RE.match(lines[j]):
            end_idx = j
            break
    return "\n".join(lines[start_idx:end_idx])


# ---------------------------------------------------------------------------
# Disposable git-repo fixtures for the matcher_version provenance guard
# ---------------------------------------------------------------------------


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run a git subcommand in ``cwd`` and return the completed process."""
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _init_fixture_repo(root: Path) -> None:
    """Initialize a disposable git repo at ``root`` for guard fixtures."""
    _run_git(["init", "-q"], cwd=root)
    _run_git(["config", "user.email", "test@example.com"], cwd=root)
    _run_git(["config", "user.name", "Test Fixture"], cwd=root)
    _run_git(["config", "commit.gpgsign", "false"], cwd=root)


def _write_dep_files(root: Path, compose_content: str, cells_content: str) -> None:
    """Write stand-in ``_compose.py`` / ``_cells.py`` files at ``root``."""
    match_dir = root / "src" / "claude_wayfinder" / "match"
    match_dir.mkdir(parents=True, exist_ok=True)
    (match_dir / "_compose.py").write_text(compose_content, encoding="utf-8")
    (match_dir / "_cells.py").write_text(cells_content, encoding="utf-8")


def _commit_all(root: Path, message: str) -> str:
    """Stage and commit everything in ``root``; return the new short SHA."""
    _run_git(["add", "-A"], cwd=root)
    _run_git(["commit", "-q", "-m", message], cwd=root)
    result = _run_git(["rev-parse", "--short", "HEAD"], cwd=root)
    return result.stdout.strip()


@pytest.fixture
def guard_repo(tmp_path: Path) -> tuple[Path, str]:
    """A disposable git repo with committed ``_compose.py``/``_cells.py``.

    Returns:
        Tuple of ``(repo_root, matcher_version)`` where ``matcher_version``
        is the short SHA of the sole commit, matching both dependency
        files' current on-disk content.
    """
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _init_fixture_repo(repo_root)
    _write_dep_files(
        repo_root,
        compose_content="# compose v1\n",
        cells_content="# cells v1\n",
    )
    sha = _commit_all(repo_root, "initial")
    return repo_root, sha


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------


class TestCliArgumentParsing:
    """--corpus and --labels are required; --json is optional; --help works."""

    def test_missing_corpus_arg_is_enforced(
        self, kc_report_module: ModuleType, tmp_path: Path
    ) -> None:
        labels_path = tmp_path / "labels.jsonl"
        _write_jsonl(labels_path, [_gold_row(1)])
        rc: int | None
        try:
            rc = kc_report_module.main(["--labels", str(labels_path)])
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 1
        assert rc not in (0, None), "--corpus is required and must be enforced"

    def test_missing_labels_arg_is_enforced(
        self, kc_report_module: ModuleType, tmp_path: Path
    ) -> None:
        corpus_path = tmp_path / "corpus.jsonl"
        _write_jsonl(corpus_path, [_corpus_row(1)])
        rc: int | None
        try:
            rc = kc_report_module.main(["--corpus", str(corpus_path)])
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 1
        assert rc not in (0, None), "--labels is required and must be enforced"

    def test_help_flag_exits_cleanly(self, kc_report_module: ModuleType) -> None:
        rc: int | None
        try:
            rc = kc_report_module.main(["--help"])
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 0
        assert rc == 0

    def test_json_flag_is_optional(
        self,
        kc_report_module: ModuleType,
        guard_repo: tuple[Path, str],
        tmp_path: Path,
    ) -> None:
        """Running without --json still succeeds (JSON output is opt-in)."""
        repo_root, sha = guard_repo
        corpus_path = tmp_path / "corpus.jsonl"
        labels_path = tmp_path / "labels.jsonl"
        _write_jsonl(corpus_path, [_corpus_row(1, matcher_version=sha)])
        _write_jsonl(labels_path, [_gold_row(1)])

        rc = _run_main(kc_report_module, corpus_path, labels_path, repo_root)
        assert rc == 0


# ---------------------------------------------------------------------------
# matcher_version provenance guard
# ---------------------------------------------------------------------------


class TestMatcherVersionGuard:
    """Guard: consistent matcher_version + clean dependency modules -> pass;
    any divergence, dirtiness, inconsistency, or unresolvable version ->
    hard warning + non-zero exit (see module docstring judgment call 1).
    """

    def test_consistent_matching_version_and_clean_dependencies_passes(
        self,
        kc_report_module: ModuleType,
        guard_repo: tuple[Path, str],
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        repo_root, sha = guard_repo
        rows = [
            _corpus_row(1, matcher_version=sha),
            _corpus_row(2, matcher_version=sha),
        ]
        gold = [_gold_row(1), _gold_row(2)]
        corpus_path = tmp_path / "corpus.jsonl"
        labels_path = tmp_path / "labels.jsonl"
        _write_jsonl(corpus_path, rows)
        _write_jsonl(labels_path, gold)

        rc = _run_main(kc_report_module, corpus_path, labels_path, repo_root)
        captured = capsys.readouterr()

        assert rc == 0
        assert "diverg" not in captured.err.lower()
        assert "mismatch" not in captured.err.lower()

    def test_mixed_matcher_versions_across_rows_fails(
        self,
        kc_report_module: ModuleType,
        guard_repo: tuple[Path, str],
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Inconsistent matcher_version across rows is itself a provenance
        failure -- the tool must not silently pick the first/majority value.
        """
        repo_root, sha = guard_repo
        rows = [
            _corpus_row(1, matcher_version=sha),
            _corpus_row(2, matcher_version="deadbee"),
        ]
        gold = [_gold_row(1), _gold_row(2)]
        corpus_path = tmp_path / "corpus.jsonl"
        labels_path = tmp_path / "labels.jsonl"
        _write_jsonl(corpus_path, rows)
        _write_jsonl(labels_path, gold)

        rc = _run_main(kc_report_module, corpus_path, labels_path, repo_root)
        captured = capsys.readouterr()

        assert rc != 0, "mixed matcher_version values must fail the guard"
        assert "Traceback (most recent call last)" not in captured.err

    @pytest.mark.parametrize("dep_file", ["_compose.py", "_cells.py"])
    def test_dependency_file_diverged_since_recorded_commit_fails(
        self,
        kc_report_module: ModuleType,
        guard_repo: tuple[Path, str],
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        dep_file: str,
    ) -> None:
        repo_root, sha1 = guard_repo
        target = repo_root / "src" / "claude_wayfinder" / "match" / dep_file
        target.write_text(
            target.read_text(encoding="utf-8") + "\n# changed after sha1\n",
            encoding="utf-8",
        )
        _commit_all(repo_root, f"modify {dep_file}")

        rows = [_corpus_row(1, matcher_version=sha1)]
        gold = [_gold_row(1)]
        corpus_path = tmp_path / "corpus.jsonl"
        labels_path = tmp_path / "labels.jsonl"
        _write_jsonl(corpus_path, rows)
        _write_jsonl(labels_path, gold)

        rc = _run_main(kc_report_module, corpus_path, labels_path, repo_root)
        captured = capsys.readouterr()

        assert rc != 0, (
            f"{dep_file} changed between the recorded matcher_version and "
            "HEAD -- the guard must fail"
        )
        assert "Traceback (most recent call last)" not in captured.err

    def test_dirty_working_tree_on_dependency_file_fails(
        self,
        kc_report_module: ModuleType,
        guard_repo: tuple[Path, str],
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A dirty (uncommitted) dependency-module change must fail the
        guard even when matcher_version == HEAD -- a commit-to-commit diff
        alone cannot see this.
        """
        repo_root, sha = guard_repo
        target = repo_root / "src" / "claude_wayfinder" / "match" / "_compose.py"
        target.write_text(
            target.read_text(encoding="utf-8") + "\n# uncommitted\n",
            encoding="utf-8",
        )

        rows = [_corpus_row(1, matcher_version=sha)]
        gold = [_gold_row(1)]
        corpus_path = tmp_path / "corpus.jsonl"
        labels_path = tmp_path / "labels.jsonl"
        _write_jsonl(corpus_path, rows)
        _write_jsonl(labels_path, gold)

        rc = _run_main(kc_report_module, corpus_path, labels_path, repo_root)
        captured = capsys.readouterr()

        assert rc != 0, "a dirty dependency-module working tree must fail the guard"
        assert "Traceback (most recent call last)" not in captured.err

    def test_unknown_matcher_version_string_fails(
        self,
        kc_report_module: ModuleType,
        guard_repo: tuple[Path, str],
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A dist-version-fallback ``"unknown"`` matcher_version cannot be
        resolved to a commit -- provenance is unverifiable, guard fails.
        """
        repo_root, _sha = guard_repo
        rows = [_corpus_row(1, matcher_version="unknown")]
        gold = [_gold_row(1)]
        corpus_path = tmp_path / "corpus.jsonl"
        labels_path = tmp_path / "labels.jsonl"
        _write_jsonl(corpus_path, rows)
        _write_jsonl(labels_path, gold)

        rc = _run_main(kc_report_module, corpus_path, labels_path, repo_root)
        captured = capsys.readouterr()

        assert rc != 0
        assert "Traceback (most recent call last)" not in captured.err

    def test_repo_root_without_git_fails_safe(
        self,
        kc_report_module: ModuleType,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A git subprocess failure (not a repo) must fail safe -- non-zero
        exit and a clean warning, not an unhandled crash and not a silent
        pass.
        """
        plain_dir = tmp_path / "not_a_repo"
        _write_dep_files(plain_dir, compose_content="# x\n", cells_content="# y\n")

        rows = [_corpus_row(1, matcher_version="abc1234")]
        gold = [_gold_row(1)]
        corpus_path = tmp_path / "corpus.jsonl"
        labels_path = tmp_path / "labels.jsonl"
        _write_jsonl(corpus_path, rows)
        _write_jsonl(labels_path, gold)

        rc = _run_main(kc_report_module, corpus_path, labels_path, plain_dir)
        captured = capsys.readouterr()

        assert rc != 0
        assert "Traceback (most recent call last)" not in captured.err

    def test_bare_semver_matcher_version_resolves_against_v_prefixed_tag(
        self,
        kc_report_module: ModuleType,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Real corpus data records ``matcher_version`` as a bare semver
        string (e.g. ``"1.3.1"``, no ``v`` prefix) while this project's
        release tags are named ``vX.Y.Z`` (issue #485 bug report). A bare
        rev-parse of the recorded string alone fails
        (``git rev-parse 1.3.1`` -> "unknown revision"), but the version
        genuinely is current: the guard must fall back to resolving
        against the ``v``-prefixed tag name before declaring the
        provenance unverifiable. This is a resolution-logic gap, not a
        real divergence, so the guard must PASS (exit 0, no
        diverged/mismatch/unverifiable warning) when the tagged commit's
        dependency files are unchanged at HEAD.
        """
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        _init_fixture_repo(repo_root)
        _write_dep_files(
            repo_root,
            compose_content="# compose v1\n",
            cells_content="# cells v1\n",
        )
        _commit_all(repo_root, "initial")
        # Pick a version that will not collide with any real release tag.
        _run_git(["tag", "v9.9.9"], cwd=repo_root)

        bare_version = "9.9.9"
        rows = [_corpus_row(1, matcher_version=bare_version)]
        gold = [_gold_row(1)]
        corpus_path = tmp_path / "corpus.jsonl"
        labels_path = tmp_path / "labels.jsonl"
        _write_jsonl(corpus_path, rows)
        _write_jsonl(labels_path, gold)

        rc = _run_main(kc_report_module, corpus_path, labels_path, repo_root)
        captured = capsys.readouterr()

        assert rc == 0, (
            f"bare semver '{bare_version}' should resolve against tag "
            f"'v{bare_version}' when a direct rev-parse fails; guard "
            f"reported non-zero exit with stderr:\n{captured.err}"
        )
        assert "diverg" not in captured.err.lower()
        assert "mismatch" not in captured.err.lower()
        assert "unverifiable" not in captured.err.lower()


# ---------------------------------------------------------------------------
# Report structure: all 5 KC sections + correct PASS/FAIL/INSUFFICIENT_DATA
# ---------------------------------------------------------------------------


class TestReportStructure:
    """A synthetic 10-row fixture with hand-computed, pinned expected
    verdicts for every KC (arithmetic mirrors the validated patterns already
    used in tests/test_corpus_eval/test_kc.py):

        KC-1 PASS   shadow_rc 0.8 (8/10), lexical_rc 0.6 (6/10):
                    0.8 >= 0.6891 and 0.8 >= 0.6 + 0.20 == 0.8.
        KC-2 PASS   shadow_cw 0.2 (2 wrong / 10 delegates) <= 0.2558.
        KC-3 PASS   eligible_n 10, numerator 8 (rows 1-8 posture-routed;
                    rows 9-10 ungated-delegate, excluded), rate 0.8 >= 0.55.
        KC-4 INSUFFICIENT_DATA  no row's caller domain is is_any/project_meta.
        KC-5 INSUFFICIENT_DATA  no row's gold.domain is infra_deploy.
    """

    def _build_fixture(self, tmp_path: Path, sha: str) -> tuple[Path, Path]:
        rows = [
            # 1-6: shadow and lexical both correct, posture-routed.
            *[
                _corpus_row(
                    i,
                    shadow_agent="code-writer",
                    live_agent="code-writer",
                    posture_routed=True,
                    matcher_version=sha,
                )
                for i in range(1, 7)
            ],
            # 7-8: shadow correct, lexical wrong, posture-routed.
            *[
                _corpus_row(
                    i,
                    shadow_agent="code-writer",
                    live_agent="ops",
                    posture_routed=True,
                    matcher_version=sha,
                )
                for i in (7, 8)
            ],
            # 9-10: shadow AND lexical wrong; ungated-delegate (excluded
            # from the KC-3 numerator).
            *[
                _corpus_row(
                    i,
                    shadow_agent="ops",
                    live_agent="ops",
                    posture_routed=False,
                    gated_agent_names=None,
                    matcher_version=sha,
                )
                for i in (9, 10)
            ],
        ]
        gold = [_gold_row(i, gold_agent="code-writer") for i in range(1, 11)]
        corpus_path = tmp_path / "corpus.jsonl"
        labels_path = tmp_path / "labels.jsonl"
        _write_jsonl(corpus_path, rows)
        _write_jsonl(labels_path, gold)
        return corpus_path, labels_path

    def test_all_five_kc_sections_present_with_expected_verdicts(
        self,
        kc_report_module: ModuleType,
        guard_repo: tuple[Path, str],
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        repo_root, sha = guard_repo
        corpus_path, labels_path = self._build_fixture(tmp_path, sha)

        rc = _run_main(kc_report_module, corpus_path, labels_path, repo_root)
        report = capsys.readouterr().out

        assert rc == 0, "a completed report run exits 0 regardless of verdict content"

        for kc in ("KC-1", "KC-2", "KC-3", "KC-4", "KC-5"):
            assert kc in report, f"{kc} section missing from report"

        assert _kc_status_appears(report, "KC-1", "PASS")
        assert _kc_status_appears(report, "KC-2", "PASS")
        assert _kc_status_appears(report, "KC-3", "PASS")
        assert _kc_status_appears(report, "KC-4", "INSUFFICIENT_DATA")
        assert _kc_status_appears(report, "KC-5", "INSUFFICIENT_DATA")

    def test_overall_go_no_go_recommendation_present(
        self,
        kc_report_module: ModuleType,
        guard_repo: tuple[Path, str],
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        repo_root, sha = guard_repo
        corpus_path, labels_path = self._build_fixture(tmp_path, sha)

        rc = _run_main(kc_report_module, corpus_path, labels_path, repo_root)
        report = capsys.readouterr().out

        assert rc == 0
        assert re.search(r"go[/ -]?no[- ]?go|recommendation", report, re.IGNORECASE), (
            "report must state an overall flip go/no-go recommendation"
        )

    def test_json_output_mirrors_report_verdicts(
        self,
        kc_report_module: ModuleType,
        guard_repo: tuple[Path, str],
        tmp_path: Path,
    ) -> None:
        repo_root, sha = guard_repo
        corpus_path, labels_path = self._build_fixture(tmp_path, sha)
        json_path = tmp_path / "report.json"

        rc = _run_main(kc_report_module, corpus_path, labels_path, repo_root, json_path)
        assert rc == 0
        assert json_path.exists(), "--json must write a machine-readable file"

        data = json.loads(json_path.read_text(encoding="utf-8"))
        assert "criteria" in data
        by_kc = {c["kc"]: c["status"] for c in data["criteria"]}
        assert by_kc == {
            "KC-1": "PASS",
            "KC-2": "PASS",
            "KC-3": "PASS",
            "KC-4": "INSUFFICIENT_DATA",
            "KC-5": "INSUFFICIENT_DATA",
        }
        assert "overall_recommendation" in data
        assert isinstance(data["overall_recommendation"], str)
        assert data["overall_recommendation"] != ""


# ---------------------------------------------------------------------------
# Whole-sample vs gated-eligible-subset cuts
# ---------------------------------------------------------------------------


class TestWholeVsGatedCuts:
    """Both cuts must appear, and must show different content when the
    fixture has entries the KC-3 gate excludes.

    Fixture: 4 gated-eligible rows (domain=code/posture=build/high-conf, all
    correct -> gated-subset RC 1.0) plus 4 ungated (domain=is_any) rows that
    are all wrong vs gold -> whole-sample RC 0.5 (4/8). The two cuts must
    diverge (1.0 vs 0.5).
    """

    def test_whole_sample_and_gated_subset_sections_present_and_differ(
        self,
        kc_report_module: ModuleType,
        guard_repo: tuple[Path, str],
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        repo_root, sha = guard_repo
        rows = [
            *[
                _corpus_row(
                    i,
                    domain="code",
                    posture="build",
                    confidence="high",
                    shadow_agent="code-writer",
                    live_agent="code-writer",
                    posture_routed=True,
                    matcher_version=sha,
                )
                for i in range(1, 5)
            ],
            *[
                _corpus_row(
                    i,
                    domain="is_any",
                    posture="research",
                    confidence="high",
                    shadow_agent="ops",
                    live_agent="ops",
                    posture_routed=False,
                    gated_agent_names=None,
                    matcher_version=sha,
                )
                for i in range(5, 9)
            ],
        ]
        gold = [
            *[
                _gold_row(i, gold_agent="code-writer", domain="code", posture="build")
                for i in range(1, 5)
            ],
            *[
                _gold_row(
                    i,
                    gold_agent="researcher",
                    domain="is_any",
                    posture="research",
                    is_any=True,
                )
                for i in range(5, 9)
            ],
        ]
        corpus_path = tmp_path / "corpus.jsonl"
        labels_path = tmp_path / "labels.jsonl"
        _write_jsonl(corpus_path, rows)
        _write_jsonl(labels_path, gold)

        rc = _run_main(kc_report_module, corpus_path, labels_path, repo_root)
        report = capsys.readouterr().out

        assert rc == 0
        whole = _extract_section(report, r"whole[- ]?sample")
        gated = _extract_section(report, r"gated[- ]?(eligible|subset)|eligible[- ]?subset")
        assert whole != gated, (
            "whole-sample (RC 0.5, 4/8) and gated-eligible-subset (RC 1.0, "
            "4/4) cuts must show different content, not a duplicated table"
        )


# ---------------------------------------------------------------------------
# Caller-label-match breakdown
# ---------------------------------------------------------------------------


class TestCallerLabelMatchBreakdown:
    """A breakdown section distinguishes rows where the caller's label
    matched gold from rows where it did not -- isolating caller-label noise
    from Compose-logic error (plan Sec 4.4).
    """

    def test_breakdown_section_reports_both_matched_and_mismatched_buckets(
        self,
        kc_report_module: ModuleType,
        guard_repo: tuple[Path, str],
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        repo_root, sha = guard_repo
        rows = [
            # 3 rows: caller domain matches gold domain.
            *[_corpus_row(i, domain="code", matcher_version=sha) for i in (1, 2, 3)],
            # 1 row: caller domain does NOT match gold domain.
            _corpus_row(4, domain="code", matcher_version=sha),
        ]
        gold = [
            _gold_row(1, domain="code"),
            _gold_row(2, domain="code"),
            _gold_row(3, domain="code"),
            _gold_row(4, domain="data"),  # caller said "code"; gold is "data"
        ]
        corpus_path = tmp_path / "corpus.jsonl"
        labels_path = tmp_path / "labels.jsonl"
        _write_jsonl(corpus_path, rows)
        _write_jsonl(labels_path, gold)

        rc = _run_main(kc_report_module, corpus_path, labels_path, repo_root)
        report = capsys.readouterr().out

        assert rc == 0
        section = _extract_section(report, r"caller.{0,20}label|label.{0,20}caller")
        assert re.search(r"match", section, re.IGNORECASE)
        assert re.search(r"mismatch|disagree|differ", section, re.IGNORECASE)
        assert re.search(r"\d", section), (
            "the breakdown must quantify the matched/mismatched buckets, not just name them"
        )


# ---------------------------------------------------------------------------
# Optional caller-input fields entirely omitted (not just null)
# ---------------------------------------------------------------------------


class TestOptionalCallerInputFieldsOmitted:
    """Real dispatch-context JSON is permitted to omit ``domain``,
    ``posture``, ``confidence``, and ``area_span`` entirely rather than
    setting them to ``null`` (dispatch skill contract: "omit or pass
    null"). A row whose ``input`` dict genuinely lacks one of these keys
    must produce the exact same report as the same row with that key
    explicit and ``null`` -- not crash with ``KeyError`` (issue #485 bug
    report from real telemetry; same bug class already fixed in
    ``scripts/corpus/eval/_kc.py`` for #493/PR #495/#496, but present
    here in two separate direct-dict-index sites: ``_eligible_rows``'s
    ``caller_input["confidence"]`` and ``_render_report``'s
    ``row["input"]["domain"]`` caller-label-match comparison).
    """

    def test_row_with_confidence_key_entirely_omitted_matches_explicit_null(
        self,
        kc_report_module: ModuleType,
        guard_repo: tuple[Path, str],
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """``_eligible_rows`` indexes ``caller_input["confidence"]``
        directly. A row whose ``input`` dict has no ``"confidence"`` key
        at all must be tolerated exactly like ``confidence=None``, not
        crash with ``KeyError: 'confidence'``.
        """
        repo_root, sha = guard_repo
        labels_path = tmp_path / "labels.jsonl"
        _write_jsonl(labels_path, [_gold_row(1)])

        null_corpus_path = tmp_path / "corpus_null.jsonl"
        _write_jsonl(
            null_corpus_path,
            [_corpus_row(1, confidence=None, matcher_version=sha)],
        )
        rc_null = _run_main(kc_report_module, null_corpus_path, labels_path, repo_root)
        report_null = capsys.readouterr().out
        assert rc_null == 0, "the explicit-null baseline row must itself succeed"

        omitted_corpus_path = tmp_path / "corpus_omitted.jsonl"
        _write_jsonl(
            omitted_corpus_path,
            [_corpus_row_missing_input_key(1, "confidence", matcher_version=sha)],
        )
        rc_omitted = _run_main(kc_report_module, omitted_corpus_path, labels_path, repo_root)
        captured_omitted = capsys.readouterr()

        assert rc_omitted == 0, (
            "a row with 'confidence' entirely omitted from its input dict "
            "must generate a report just like confidence=None does, not "
            f"crash. stderr:\n{captured_omitted.err}"
        )
        assert captured_omitted.out == report_null, (
            "omitting 'confidence' must be behaviorally identical to "
            "confidence=None, not merely non-crashing"
        )

    def test_row_with_domain_key_entirely_omitted_matches_explicit_null(
        self,
        kc_report_module: ModuleType,
        guard_repo: tuple[Path, str],
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """The caller-label-match breakdown indexes
        ``row["input"]["domain"]`` directly. A row whose ``input`` dict
        has no ``"domain"`` key at all must be tolerated exactly like
        ``domain=None``, not crash with ``KeyError: 'domain'``.
        """
        repo_root, sha = guard_repo
        labels_path = tmp_path / "labels.jsonl"
        _write_jsonl(labels_path, [_gold_row(1)])

        null_corpus_path = tmp_path / "corpus_null.jsonl"
        _write_jsonl(
            null_corpus_path,
            [_corpus_row(1, domain=None, matcher_version=sha)],
        )
        rc_null = _run_main(kc_report_module, null_corpus_path, labels_path, repo_root)
        report_null = capsys.readouterr().out
        assert rc_null == 0, "the explicit-null baseline row must itself succeed"

        omitted_corpus_path = tmp_path / "corpus_omitted.jsonl"
        _write_jsonl(
            omitted_corpus_path,
            [_corpus_row_missing_input_key(1, "domain", matcher_version=sha)],
        )
        rc_omitted = _run_main(kc_report_module, omitted_corpus_path, labels_path, repo_root)
        captured_omitted = capsys.readouterr()

        assert rc_omitted == 0, (
            "a row with 'domain' entirely omitted from its input dict must "
            "generate a report just like domain=None does, not crash. "
            f"stderr:\n{captured_omitted.err}"
        )
        assert captured_omitted.out == report_null, (
            "omitting 'domain' must be behaviorally identical to "
            "domain=None, not merely non-crashing"
        )


# ---------------------------------------------------------------------------
# Execution errors (genuine failures, distinct from provenance-guard fails)
# ---------------------------------------------------------------------------


class TestExecutionErrors:
    """Missing input files are a genuine execution error -> non-zero exit,
    distinct from a matcher_version provenance-guard failure.
    """

    def test_missing_corpus_file_returns_nonzero(
        self,
        kc_report_module: ModuleType,
        guard_repo: tuple[Path, str],
        tmp_path: Path,
    ) -> None:
        repo_root, _sha = guard_repo
        missing_corpus = tmp_path / "does-not-exist.jsonl"
        labels_path = tmp_path / "labels.jsonl"
        _write_jsonl(labels_path, [_gold_row(1)])

        rc = _run_main(kc_report_module, missing_corpus, labels_path, repo_root)
        assert rc != 0

    def test_missing_labels_file_returns_nonzero(
        self,
        kc_report_module: ModuleType,
        guard_repo: tuple[Path, str],
        tmp_path: Path,
    ) -> None:
        repo_root, sha = guard_repo
        corpus_path = tmp_path / "corpus.jsonl"
        _write_jsonl(corpus_path, [_corpus_row(1, matcher_version=sha)])
        missing_labels = tmp_path / "does-not-exist-labels.jsonl"

        rc = _run_main(kc_report_module, corpus_path, missing_labels, repo_root)
        assert rc != 0
