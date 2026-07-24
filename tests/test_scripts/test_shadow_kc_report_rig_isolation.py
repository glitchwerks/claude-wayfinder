"""Regression tests for shadow KC report revision-import isolation."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Callable, Iterator

import pytest

_SCRIPT_PATH = Path(__file__).parent.parent.parent / "scripts" / "shadow-kc-report.py"

_COMPOSE_TEMPLATE = '''\
"""Compose fixture whose behavior depends on a module constant."""

ROUTE_AGENT = {route_agent!r}


def compose_route(
    labels,
    scored_agents,
    scored_skills,
    features,
    catalog,
    catalog_agent_names,
    diagnostics=None,
):
    return {{"decision": "delegate", "agent": ROUTE_AGENT}}
'''


@pytest.fixture(scope="module")
def kc_report_module() -> ModuleType:
    """Load the report script under a test-specific module name.

    Returns:
        Loaded ``shadow-kc-report.py`` module.
    """
    module_name = "shadow_kc_report_rig_isolation_test"
    spec = importlib.util.spec_from_file_location(module_name, _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def _run_git(repo_root: Path, *arguments: str) -> str:
    """Run Git successfully and return stripped stdout.

    Args:
        repo_root: Repository working directory.
        *arguments: Git arguments after the executable name.

    Returns:
        Captured standard output without surrounding whitespace.
    """
    result = subprocess.run(
        ["git", *arguments],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return result.stdout.strip()


@pytest.fixture
def constant_only_compose_repo(tmp_path: Path) -> tuple[Path, str, str]:
    """Create two revisions differing only in a module constant.

    Args:
        tmp_path: Pytest-provided temporary directory.

    Returns:
        Repository root, baseline SHA, and HEAD SHA.
    """
    repo_root = tmp_path / "repo"
    match_dir = repo_root / "src" / "claude_wayfinder" / "match"
    match_dir.mkdir(parents=True)
    _run_git(repo_root, "init", "-q")
    _run_git(repo_root, "config", "user.email", "test@example.com")
    _run_git(repo_root, "config", "user.name", "Test Fixture")
    _run_git(repo_root, "config", "commit.gpgsign", "false")

    compose_path = match_dir / "_compose.py"
    cells_path = match_dir / "_cells.py"
    compose_path.write_text(
        _COMPOSE_TEMPLATE.format(route_agent="agent-old"),
        encoding="utf-8",
    )
    cells_path.write_text("# cells fixture\n", encoding="utf-8")
    _run_git(repo_root, "add", "-A")
    _run_git(repo_root, "commit", "-q", "-m", "baseline")
    baseline_revision = _run_git(repo_root, "rev-parse", "HEAD")

    compose_path.write_text(
        _COMPOSE_TEMPLATE.format(route_agent="agent-new"),
        encoding="utf-8",
    )
    _run_git(repo_root, "add", "-A")
    _run_git(repo_root, "commit", "-q", "-m", "change module constant")
    head_revision = _run_git(repo_root, "rev-parse", "HEAD")
    return repo_root, baseline_revision, head_revision


def test_rig_accepts_constant_only_compose_change(
    kc_report_module: ModuleType,
    constant_only_compose_repo: tuple[Path, str, str],
) -> None:
    """A module-level behavior change is not an import-rig collision."""
    repo_root, baseline_revision, head_revision = constant_only_compose_repo

    kc_report_module._verify_rig_isolation(
        repo_root,
        baseline_revision,
        head_revision,
        [],
    )


def test_rig_rejects_same_function_object_collision(
    kc_report_module: ModuleType,
    constant_only_compose_repo: tuple[Path, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reusing one loaded function for both revisions still fails closed."""
    repo_root, baseline_revision, head_revision = constant_only_compose_repo

    def _same_compose(*args: object, **kwargs: object) -> dict[str, str]:
        """Return an irrelevant decision from the forced collision."""
        return {"decision": "delegate", "agent": "same"}

    @contextmanager
    def _colliding_loader(
        selected_repo_root: Path,
    ) -> Iterator[Callable[[str], Callable[..., dict[str, str]]]]:
        """Yield a loader that deliberately reuses one function object."""
        assert selected_repo_root == repo_root

        def _load(revision: str) -> Callable[..., dict[str, str]]:
            assert revision in {baseline_revision, head_revision}
            return _same_compose

        yield _load

    monkeypatch.setattr(
        kc_report_module,
        "_revision_compose_loader",
        _colliding_loader,
    )

    with pytest.raises(
        kc_report_module.RigIsolationError,
        match="same function object",
    ):
        kc_report_module._verify_rig_isolation(
            repo_root,
            baseline_revision,
            head_revision,
            [],
        )


def test_eligible_rows_tolerates_missing_posture(
    kc_report_module: ModuleType,
) -> None:
    """An omitted optional posture is equivalent to an explicit null."""
    base_row = {
        "input": {
            "domain": "code",
            "confidence": "high",
        }
    }
    missing_posture = dict(base_row)
    explicit_null = {
        "input": {
            **base_row["input"],
            "posture": None,
        }
    }

    missing_result = kc_report_module._eligible_rows([missing_posture])
    explicit_result = kc_report_module._eligible_rows([explicit_null])
    assert missing_result == explicit_result, (
        "an omitted posture key must be treated identically to an "
        "explicit null"
    )
    assert missing_result == [], (
        "domain='code'/confidence='high' rows with no posture are never "
        "KC-3 eligible (posture is required before any cell-map lookup "
        "is attempted); a regression that incorrectly included them would "
        "escape a bare cross-comparison assertion"
    )
