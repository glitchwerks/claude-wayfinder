"""Generate the shadow-routing knowledge-criteria go/no-go report."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import inspect
import json
import subprocess
import sys
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Iterator, Literal

# Direct script execution does not automatically put the repository root on
# sys.path. Add it before importing the scripts namespace.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_SRC_DIR = _REPO_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from claude_wayfinder.match._catalog import (  # noqa: E402
    _resolve_catalog_path,
    load_catalog,
)
from claude_wayfinder.match._cells import cell_map_lookup  # noqa: E402
from claude_wayfinder.match._compose import parse_labels  # noqa: E402
from claude_wayfinder.match._match import (  # noqa: E402
    build_features,
    score_entries,
)
from claude_wayfinder.match._types import CatalogEntry  # noqa: E402
from scripts.corpus.eval._kc import (  # noqa: E402
    KCVerdict,
    compute_kc1,
    compute_kc2,
    compute_kc3,
    compute_kc4,
    compute_kc5,
)
from scripts.corpus.eval._metrics import (  # noqa: E402
    metric_confident_wrong_rate,
    metric_routing_correctness,
)
from scripts.corpus.eval._reader import (  # noqa: E402
    GoldLabel,
    load_corpus,
    load_labels,
)
from scripts.corpus.eval._systems import SystemResult  # noqa: E402

CorpusRow = dict[str, Any]
Arm = Literal["shadow", "live"]
ComposeRoute = Callable[..., dict[str, Any]]
ComposeLoader = Callable[[str], ComposeRoute]

_COMPOSE_MODULE_PATH = "src/claude_wayfinder/match/_compose.py"
# First-party modules used transitively by ``compose_route``.
_TRANSITIVE_DEPENDENCY_MODULES = (
    "src/claude_wayfinder/match/_cells.py",
    "src/claude_wayfinder/match/_decide.py",
    "src/claude_wayfinder/match/_types.py",
    "src/claude_wayfinder/match/_match.py",
    "src/claude_wayfinder/match/_stem.py",
    "src/claude_wayfinder/match_filters.py",
)
# Every dependency that must be clean before provenance comparison.
_DEPENDENCY_MODULES = (_COMPOSE_MODULE_PATH,) + _TRANSITIVE_DEPENDENCY_MODULES
_DRIFT_WARNING_THRESHOLD: float = 0.25


def _gate_passes(provenance_drift_fraction: float) -> bool:
    """Return True when the auto-checkable drift half of the gate passes.

    Args:
        provenance_drift_fraction: Fraction of corpus rows excluded or
            unverifiable under the matcher_version provenance guard.

    Returns:
        True when the fraction is strictly below
        ``_DRIFT_WARNING_THRESHOLD`` (gate PASSES); False otherwise
        (gate FAILS, inclusive of the exact-boundary case).
    """
    return provenance_drift_fraction < _DRIFT_WARNING_THRESHOLD


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Arguments excluding the program name, or None for sys.argv.

    Returns:
        Parsed command-line namespace.
    """
    parser = argparse.ArgumentParser(
        description="Generate the shadow-routing KC go/no-go report.",
    )
    parser.add_argument(
        "--corpus",
        required=True,
        type=Path,
        metavar="PATH",
        help="Shadow-corpus JSONL file.",
    )
    parser.add_argument(
        "--labels",
        required=True,
        type=Path,
        metavar="PATH",
        help="Gold-labels JSONL file.",
    )
    parser.add_argument(
        "--json",
        default=None,
        type=Path,
        metavar="PATH",
        help="Optional path for the machine-readable JSON report.",
    )
    parser.add_argument(
        "--repo-root",
        default=Path.cwd(),
        type=Path,
        metavar="PATH",
        help="Git repository root used for provenance checks (default: cwd).",
    )
    parser.add_argument(
        "--catalog-path",
        default=None,
        type=Path,
        metavar="PATH",
        help=("Dispatch catalog JSON file (default: DISPATCH_CATALOG_PATH environment variable)."),
    )
    parser.add_argument(
        "--manifest",
        default=None,
        type=Path,
        metavar="PATH",
        help="Optional corpus-manifest JSON file to cite in the report.",
    )
    return parser.parse_args(argv)


def _load_manifest_for_integrity_check(
    manifest_path: Path | None,
) -> tuple[str | None, dict[str, Any] | None]:
    """Read and parse an optional manifest for citation and integrity.

    Args:
        manifest_path: Optional ``--manifest`` path.

    Returns:
        A tuple of ``(manifest_sha256, manifest)``. Both values are None
        when no path was supplied or when the manifest could not be read
        or parsed. Explicit read and parse failures also emit a warning.
    """
    if manifest_path is None:
        return None, None

    try:
        manifest_bytes = manifest_path.read_bytes()
    except OSError as exc:
        print(
            f"WARNING: --manifest {manifest_path} could not be read "
            f"({exc}); skipping corpus-hash integrity validation "
            "(manifest citation unavailable).",
            file=sys.stderr,
        )
        return None, None

    try:
        parsed_manifest = json.loads(manifest_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(
            f"WARNING: --manifest {manifest_path} could not be parsed "
            f"as JSON ({exc}); skipping corpus-hash integrity "
            "validation (manifest citation unavailable).",
            file=sys.stderr,
        )
        return None, None

    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    if not isinstance(parsed_manifest, dict):
        print(
            f"WARNING: --manifest {manifest_path} did not parse to a JSON "
            "object; skipping corpus-hash integrity validation "
            "(manifest citation unavailable).",
            file=sys.stderr,
        )
        return manifest_sha256, None
    return manifest_sha256, parsed_manifest


def _check_corpus_hash_integrity(
    corpus_path: Path,
    manifest: dict[str, Any] | None,
) -> str | None:
    """Validate corpus bytes against a manifest-recorded SHA-256 digest.

    Args:
        corpus_path: Corpus file whose bytes should be validated.
        manifest: Parsed manifest dictionary, or None when unavailable.

    Returns:
        An error message when the corpus cannot be read or its digest
        differs from a string digest recorded by the manifest; otherwise
        None.
    """
    recorded = manifest.get("sha256") if isinstance(manifest, dict) else None
    if not isinstance(recorded, str):
        return None

    try:
        corpus_bytes = corpus_path.read_bytes()
    except OSError as exc:
        return (
            "ERROR: corpus SHA-256 integrity check could not read "
            f"{corpus_path}: {exc}."
        )

    corpus_sha256 = hashlib.sha256(corpus_bytes).hexdigest()
    recorded_sha256 = recorded.strip().lower()
    if corpus_sha256 != recorded_sha256:
        return (
            "ERROR: corpus SHA-256 integrity check failed for "
            f"{corpus_path}: manifest records {recorded_sha256}, "
            f"actual {corpus_sha256}."
        )
    return None


def _run_git(repo_root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Run a captured git command in the selected repository.

    Args:
        repo_root: Repository working directory.
        *arguments: Git subcommand and arguments.

    Returns:
        Completed git process without automatic return-code checking.

    Raises:
        OSError: If the process cannot be started or the cwd is invalid.
    """
    return subprocess.run(
        ["git", *arguments],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


@dataclass(frozen=True)
class ProvenancePartition:
    """Per-row matcher provenance outcomes.

    Attributes:
        included: Corpus IDs whose baseline and HEAD routing agree.
        excluded: Corpus IDs whose routing differs, with reasons.
        unverifiable: Corpus IDs whose matcher version cannot be resolved.
    """

    included: frozenset[int]
    excluded: dict[int, str]
    unverifiable: dict[int, str]


def _provenance_drift_fraction(partition: ProvenancePartition) -> float:
    """Return the fraction of provenance rows excluded from KC computation."""
    drifted_rows = len(partition.excluded) + len(partition.unverifiable)
    total_rows = len(partition.included) + drifted_rows
    if total_rows == 0:
        return 0.0
    return drifted_rows / total_rows


class ProvenanceGuardError(RuntimeError):
    """Raised when matcher provenance cannot be checked safely."""


class RigIsolationError(ProvenanceGuardError):
    """Raised when revision-isolated compose imports cannot be trusted."""


def _git_failure_detail(
    result: subprocess.CompletedProcess[str],
    fallback: str,
) -> str:
    """Return the most useful captured Git failure text.

    Args:
        result: Completed Git subprocess.
        fallback: Detail used when Git emitted no diagnostic text.

    Returns:
        A non-empty error detail.
    """
    return result.stderr.strip() or result.stdout.strip() or fallback


def _resolve_matcher_revision(
    repo_root: Path,
    matcher_version: object,
) -> tuple[str | None, str | None]:
    """Resolve a logged matcher version to a full commit SHA.

    Direct revision resolution is attempted first, followed by the
    project's ``v``-prefixed release-tag convention.

    Args:
        repo_root: Git repository containing the revision.
        matcher_version: Raw version value from a corpus row.

    Returns:
        ``(full_sha, None)`` on success, otherwise ``(None, reason)``.
    """
    if not isinstance(matcher_version, str) or not matcher_version:
        return (
            None,
            f"matcher_version {matcher_version!r} is not a non-empty string",
        )

    details: list[str] = []
    for candidate in (matcher_version, f"v{matcher_version}"):
        result = _run_git(
            repo_root,
            "rev-parse",
            "--verify",
            f"{candidate}^{{commit}}",
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip(), None
        details.append(
            _git_failure_detail(
                result,
                f"git could not resolve {candidate!r}",
            )
        )

    return (
        None,
        f"matcher_version {matcher_version!r} could not be resolved "
        f"(direct or v-prefixed): {'; '.join(details)}",
    )


def _validate_provenance_repository(repo_root: Path) -> str:
    """Validate global repository preconditions and resolve HEAD.

    Args:
        repo_root: Repository to inspect.

    Returns:
        The full commit SHA for HEAD.

    Raises:
        ProvenanceGuardError: If the path is not a Git worktree, HEAD
            cannot be resolved, or a guarded dependency is dirty.
    """
    try:
        repository = _run_git(
            repo_root,
            "rev-parse",
            "--is-inside-work-tree",
        )
    except OSError as exc:
        raise ProvenanceGuardError(f"cannot inspect repository {repo_root}: {exc}") from exc

    if repository.returncode != 0 or repository.stdout.strip() != "true":
        detail = _git_failure_detail(repository, "not a git repository")
        raise ProvenanceGuardError(f"repo_root {repo_root} is not a git repository: {detail}")

    head = _run_git(
        repo_root,
        "rev-parse",
        "--verify",
        "HEAD^{commit}",
    )
    if head.returncode != 0 or not head.stdout.strip():
        detail = _git_failure_detail(head, "git could not resolve HEAD")
        raise ProvenanceGuardError(f"cannot resolve HEAD: {detail}")
    head_revision = head.stdout.strip()

    for module_path in _DEPENDENCY_MODULES:
        status = _run_git(
            repo_root,
            "status",
            "--porcelain",
            "--",
            module_path,
        )
        if status.returncode != 0:
            detail = _git_failure_detail(status, "git status failed")
            raise ProvenanceGuardError(f"cannot inspect {module_path}: {detail}")
        if status.stdout.strip():
            raise ProvenanceGuardError(f"{module_path} has uncommitted changes")

    return head_revision


def _compose_blob(repo_root: Path, revision: str) -> str:
    """Read the committed ``_compose.py`` source at one revision.

    Args:
        repo_root: Git repository containing the blob.
        revision: Full commit SHA to inspect.

    Returns:
        UTF-8 source text for the committed compose module.

    Raises:
        ImportError: If Git cannot extract the blob.
    """
    blob_path = _COMPOSE_MODULE_PATH
    result = _run_git(
        repo_root,
        "show",
        f"{revision}:{blob_path}",
    )
    if result.returncode != 0:
        detail = _git_failure_detail(result, "git show failed")
        raise ImportError(f"cannot load {blob_path} at {revision}: {detail}")
    return result.stdout


def _blob_or_none(repo_root: Path, revision: str, path: str) -> str | None:
    """Read a committed blob at one revision, or None if absent there.

    Args:
        repo_root: Git repository containing the revision.
        revision: Full commit SHA to inspect.
        path: Repository-relative blob path.

    Returns:
        The committed blob text, or None when the path is absent.

    Raises:
        ProvenanceGuardError: If Git finds the blob but cannot read it.
    """
    exists = _run_git(
        repo_root,
        "cat-file",
        "-e",
        f"{revision}:{path}",
    )
    if exists.returncode != 0:
        return None

    result = _run_git(
        repo_root,
        "show",
        f"{revision}:{path}",
    )
    if result.returncode != 0:
        detail = _git_failure_detail(result, "git show failed")
        raise ProvenanceGuardError(f"cannot read {path} at {revision}: {detail}")
    return result.stdout


def _dependency_drift_reason(
    repo_root: Path,
    baseline_revision: str,
    head_revision: str,
) -> str | None:
    """Return the first transitive compose dependency drift reason, if any.

    Args:
        repo_root: Git repository containing both revisions.
        baseline_revision: Full baseline commit SHA.
        head_revision: Full HEAD commit SHA.

    Returns:
        A fail-closed exclusion reason for the first differing module, or
        None when all transitive dependency blobs match.
    """
    for module_path in _TRANSITIVE_DEPENDENCY_MODULES:
        baseline_blob = _blob_or_none(repo_root, baseline_revision, module_path)
        head_blob = _blob_or_none(repo_root, head_revision, module_path)
        if baseline_blob != head_blob:
            return (
                f"dependency module {module_path} differs between baseline and HEAD; "
                "compose_route comparison cannot verify decisions that transitively "
                "depend on it"
            )
    return None


def _compose_function(module: ModuleType) -> ComposeRoute:
    """Extract a callable ``compose_route`` from an imported module.

    Args:
        module: Revision-isolated compose module.

    Returns:
        The module's callable ``compose_route`` attribute.

    Raises:
        AttributeError: If the module lacks ``compose_route``.
        TypeError: If the attribute is not callable.
    """
    function = getattr(module, "compose_route")
    if not callable(function):
        raise TypeError(f"{module.__name__}.compose_route is not callable")
    return function


@contextmanager
def _revision_compose_loader(
    repo_root: Path,
) -> Iterator[ComposeLoader]:
    """Yield a cached loader for isolated ``_compose.py`` revisions.

    Each revision is extracted to a unique temporary source path and
    registered under a unique module name. Both artifacts remain alive
    for the context lifetime so the self-check can verify each loaded
    module's full source file against its intended Git blob. Transitive
    first-party dependencies remain HEAD-loaded and are guarded separately
    by raw blob comparison before this loader is used.

    Args:
        repo_root: Git repository containing revision blobs.

    Yields:
        A function loading and caching ``compose_route`` by full SHA.
    """
    module_names: list[str] = []
    cache: dict[str, ComposeRoute] = {}

    with tempfile.TemporaryDirectory(
        prefix=".shadow-kc-compose-",
        dir=repo_root,
    ) as temporary_directory:
        snapshot_root = Path(temporary_directory)

        def _load(revision: str) -> ComposeRoute:
            cached = cache.get(revision)
            if cached is not None:
                return cached

            token = uuid.uuid4().hex
            snapshot_path = snapshot_root / f"_compose_{token}.py"
            module_name = f"_shadow_kc_compose_{token}"
            snapshot_path.write_text(
                _compose_blob(repo_root, revision),
                encoding="utf-8",
                newline="",
            )

            spec = importlib.util.spec_from_file_location(
                module_name,
                snapshot_path,
            )
            if spec is None or spec.loader is None:
                raise ImportError(f"could not create import spec for {snapshot_path}")

            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            module_names.append(module_name)
            try:
                spec.loader.exec_module(module)
            except Exception:
                sys.modules.pop(module_name, None)
                module_names.remove(module_name)
                raise

            compose_route = _compose_function(module)
            cache[revision] = compose_route
            return compose_route

        try:
            yield _load
        finally:
            for module_name in module_names:
                sys.modules.pop(module_name, None)


def _verify_rig_isolation(
    repo_root: Path,
    baseline_revision: str,
    head_revision: str,
    catalog: list[CatalogEntry],
) -> None:
    """Verify that distinct compose revisions load as distinct code.

    Args:
        repo_root: Git repository containing both revisions.
        baseline_revision: Full baseline commit SHA.
        head_revision: Full HEAD commit SHA.
        catalog: Shared catalog supplied to the provenance partition.

    Raises:
        RigIsolationError: If textually different revisions resolve to the
            same function object or snapshot path, or a loaded snapshot's
            full text does not match its intended Git blob.
    """
    del catalog  # The source-level isolation check is catalog-independent.

    baseline_blob = _compose_blob(repo_root, baseline_revision)
    head_blob = _compose_blob(repo_root, head_revision)
    if baseline_blob == head_blob:
        return

    try:
        with _revision_compose_loader(repo_root) as load_compose:
            baseline_compose = load_compose(baseline_revision)
            head_compose = load_compose(head_revision)
            if baseline_compose is head_compose:
                raise RigIsolationError(
                    "compose import rig returned the same function object "
                    f"for {baseline_revision} and {head_revision}"
                )

            baseline_source_file = inspect.getsourcefile(baseline_compose)
            head_source_file = inspect.getsourcefile(head_compose)
            if baseline_source_file is None:
                raise RigIsolationError(
                    "compose import rig could not resolve the baseline "
                    f"source file for {baseline_revision}"
                )
            if head_source_file is None:
                raise RigIsolationError(
                    f"compose import rig could not resolve the HEAD source file for {head_revision}"
                )

            baseline_source_path = Path(baseline_source_file).resolve()
            head_source_path = Path(head_source_file).resolve()
            if baseline_source_path == head_source_path:
                raise RigIsolationError(
                    "compose import rig loaded baseline and HEAD from the "
                    f"same source file: {baseline_source_path}"
                )

            baseline_loaded_text = baseline_source_path.read_text(encoding="utf-8")
            head_loaded_text = head_source_path.read_text(encoding="utf-8")
            if baseline_loaded_text != baseline_blob:
                raise RigIsolationError(
                    "compose import rig baseline snapshot does not match "
                    f"Git blob {baseline_revision}: {baseline_source_path}"
                )
            if head_loaded_text != head_blob:
                raise RigIsolationError(
                    "compose import rig HEAD snapshot does not match "
                    f"Git blob {head_revision}: {head_source_path}"
                )
    except RigIsolationError:
        raise
    except Exception as exc:
        raise RigIsolationError(
            f"compose import rig could not isolate {baseline_revision} and {head_revision}: {exc}"
        ) from exc


def _compose_decision(
    compose_route: ComposeRoute,
    labels: Any,
    scored_agents: list[Any],
    scored_skills: list[Any],
    features: Any,
    catalog: list[CatalogEntry],
    catalog_agent_names: frozenset[str],
) -> dict[str, Any]:
    """Run and normalize one revision-specific compose decision.

    Args:
        compose_route: Revision-specific compose function.
        labels: Shared parsed caller labels.
        scored_agents: Shared scored catalog agents.
        scored_skills: Shared scored catalog skills.
        features: Shared caller-input features.
        catalog: Shared catalog entries.
        catalog_agent_names: Shared routable agent names.

    Returns:
        The three normalized provenance comparison fields.
    """
    diagnostics: dict[str, Any] = {}
    output = compose_route(
        labels,
        scored_agents,
        scored_skills,
        features,
        catalog,
        catalog_agent_names,
        diagnostics=diagnostics,
    )
    return {
        "agent": output.get("agent"),
        "decision": output.get("decision"),
        "posture_routed": diagnostics.get("posture_routed"),
    }


def _caller_context(row: CorpusRow) -> dict[str, Any]:
    """Build a missing-key-safe matcher context from one corpus row.

    Args:
        row: Raw shadow-corpus record.

    Returns:
        Context containing every feature and label input used by the real
        HEAD matcher helpers.
    """
    raw_input = row.get("input")
    caller_input = raw_input if isinstance(raw_input, dict) else {}
    return {
        "domain": caller_input.get("domain"),
        "posture": caller_input.get("posture"),
        "confidence": caller_input.get("confidence"),
        "area_span": caller_input.get("area_span"),
        "task_description": caller_input.get("task_description") or "",
        "file_paths": caller_input.get("file_paths") or [],
        "agent_mentions": caller_input.get("agent_mentions") or [],
        "tool_mentions": caller_input.get("tool_mentions") or [],
        "command_prefix": caller_input.get("command_prefix"),
    }


def _provenance_partition(
    rows: list[CorpusRow],
    repo_root: Path,
    catalog: list[CatalogEntry],
) -> ProvenancePartition:
    """Partition rows by dependency drift and baseline-vs-HEAD compose agreement.

    Args:
        rows: Raw shadow-corpus records.
        repo_root: Git repository containing matcher dependency modules.
        catalog: One shared HEAD-loaded catalog for both compose runs.

    Returns:
        A complete, mutually exclusive provenance partition.

    Raises:
        ProvenanceGuardError: If repository-wide safety checks fail or the
            revision import rig cannot be trusted.
    """
    head_revision = _validate_provenance_repository(repo_root)
    included: set[int] = set()
    excluded: dict[int, str] = {}
    unverifiable: dict[int, str] = {}
    verified_pairs: set[tuple[str, str]] = set()
    dependency_drift_cache: dict[tuple[str, str], str | None] = {}

    with _revision_compose_loader(repo_root) as load_compose:
        for row in rows:
            corpus_id = int(row["corpus_id"])
            baseline_revision, reason = _resolve_matcher_revision(
                repo_root,
                row.get("matcher_version"),
            )
            if baseline_revision is None:
                unverifiable[corpus_id] = reason or "matcher version could not be resolved"
                continue

            if baseline_revision == head_revision:
                included.add(corpus_id)
                continue

            revision_pair = (baseline_revision, head_revision)
            if revision_pair not in dependency_drift_cache:
                dependency_drift_cache[revision_pair] = _dependency_drift_reason(
                    repo_root,
                    baseline_revision,
                    head_revision,
                )
            dependency_drift_reason = dependency_drift_cache[revision_pair]
            if dependency_drift_reason is not None:
                excluded[corpus_id] = dependency_drift_reason
                continue

            if revision_pair not in verified_pairs:
                _verify_rig_isolation(
                    repo_root,
                    baseline_revision,
                    head_revision,
                    catalog,
                )
                verified_pairs.add(revision_pair)

            context = _caller_context(row)
            labels = parse_labels(context)
            features = build_features(context)
            scored_agents, scored_skills = score_entries(catalog, features)
            catalog_agent_names = frozenset(scored.entry.name for scored in scored_agents)

            baseline_decision = _compose_decision(
                load_compose(baseline_revision),
                labels,
                scored_agents,
                scored_skills,
                features,
                catalog,
                catalog_agent_names,
            )
            head_decision = _compose_decision(
                load_compose(head_revision),
                labels,
                scored_agents,
                scored_skills,
                features,
                catalog,
                catalog_agent_names,
            )
            disagreeing_fields = [
                field
                for field in ("agent", "decision", "posture_routed")
                if baseline_decision[field] != head_decision[field]
            ]
            if disagreeing_fields:
                excluded[corpus_id] = (
                    f"baseline and HEAD compose_route disagree on: {', '.join(disagreeing_fields)}"
                )
            else:
                included.add(corpus_id)

    return ProvenancePartition(
        included=frozenset(included),
        excluded=excluded,
        unverifiable=unverifiable,
    )


def _eligible_rows(rows: list[CorpusRow]) -> list[CorpusRow]:
    """Return the exact gated, mapped, high-confidence KC-3 partition.

    This filter mirrors ``compute_kc3`` in ``scripts/corpus/eval/_kc.py``,
    which remains the source of truth for KC-3 eligibility.

    Args:
        rows: Raw shadow-corpus records.

    Returns:
        Rows eligible for KC-3 and the gated-subset RC/CW cut.
    """
    eligible: list[CorpusRow] = []
    for row in rows:
        caller_input = row["input"]
        domain = caller_input.get("domain")
        posture = caller_input.get("posture")
        confidence = caller_input.get("confidence")
        domain_for_lookup = domain if domain not in (None, "is_any") else "any"
        is_gated = domain not in (None, "is_any")
        cell_exists = (
            posture is not None and cell_map_lookup(domain_for_lookup, posture) is not None
        )
        if is_gated and cell_exists and confidence == "high":
            eligible.append(row)
    return eligible


def _system_results(rows: list[CorpusRow], arm: Arm) -> list[SystemResult]:
    """Adapt raw corpus records for the validated RC/CW metric kernels.

    Args:
        rows: Raw shadow-corpus records.
        arm: Either the logged shadow or live routing arm.

    Returns:
        Metric-compatible system results.
    """
    return [
        SystemResult(
            corpus_id=row["corpus_id"],
            decision=row["shadow"][f"{arm}_decision"],
            agent=row["shadow"][f"{arm}_agent"],
            confidence=row["shadow"][f"{arm}_confidence"],
            extras={},
        )
        for row in rows
    ]


def _cut_metrics(
    rows: list[CorpusRow],
    gold: dict[int, GoldLabel],
) -> dict[str, float | int]:
    """Compute shadow RC/CW for one report cut.

    Args:
        rows: Raw corpus rows in the selected cut.
        gold: Full gold-label map.

    Returns:
        Cut size, routing correctness, and confident-wrong rate.
    """
    results = _system_results(rows, "shadow")
    return {
        "n": len(rows),
        "shadow_rc": metric_routing_correctness(results, gold),
        "shadow_cw": metric_confident_wrong_rate(results, gold),
    }


def _recommendation(verdicts: list[KCVerdict]) -> str:
    """Build the overall go/no-go recommendation.

    Args:
        verdicts: KC-1 through KC-5 verdicts.

    Returns:
        Non-empty recommendation with insufficient-data criteria named.
    """
    by_kc = {verdict.kc: verdict for verdict in verdicts}
    failed = [verdict.kc for verdict in verdicts if verdict.status == "FAIL"]
    insufficient = [verdict.kc for verdict in verdicts if verdict.status == "INSUFFICIENT_DATA"]

    if by_kc["KC-2"].status != "PASS" or failed:
        recommendation = "NO-GO"
        if failed:
            recommendation += f": failed criteria: {', '.join(failed)}."
        else:
            recommendation += ": KC-2 hard block lacks a passing verdict."
    else:
        recommendation = "GO: all criteria with sufficient data passed."

    if insufficient:
        recommendation += f" Insufficient data: {', '.join(insufficient)}."
    return recommendation


def _render_report(
    verdicts: list[KCVerdict],
    rows: list[CorpusRow],
    gold: dict[int, GoldLabel],
    recommendation: str,
    provenance_drift_fraction: float,
    repo_head: str,
    manifest_sha256: str | None,
) -> str:
    """Render the human-readable Markdown report.

    Args:
        verdicts: KC-1 through KC-5 verdicts.
        rows: Full raw corpus row set.
        gold: Gold labels keyed by corpus ID.
        recommendation: Overall recommendation text.
        provenance_drift_fraction: Fraction of rows excluded or
            unverifiable by the provenance guard.
        repo_head: Full Git HEAD commit SHA for the selected repository.
        manifest_sha256: SHA-256 of the manifest's raw bytes, or None when
            the citation is unavailable.

    Returns:
        Complete Markdown report text.
    """
    lines = ["# Shadow KC Report", ""]
    for verdict in verdicts:
        lines.extend(
            [
                f"## {verdict.kc}",
                f"{verdict.kc}: {verdict.status} — "
                f"metrics: {json.dumps(verdict.metrics, sort_keys=True)}",
                "",
            ]
        )

    whole_metrics = _cut_metrics(rows, gold)
    gated_metrics = _cut_metrics(_eligible_rows(rows), gold)
    lines.extend(
        [
            "## Whole-sample cut",
            f"RC/CW: {json.dumps(whole_metrics, sort_keys=True)}",
            "",
            "## Gated-eligible subset cut",
            f"RC/CW: {json.dumps(gated_metrics, sort_keys=True)}",
            "",
        ]
    )

    manifest_citation = (
        f"Manifest SHA-256: {manifest_sha256}."
        if manifest_sha256 is not None
        else "Manifest citation unavailable: --manifest not provided or unreadable."
    )
    gate_verdict = (
        "PASSES" if _gate_passes(provenance_drift_fraction) else "FAILS"
    )
    matched = 0
    mismatched = 0
    for row in rows:
        label = gold.get(row["corpus_id"])
        if label is None:
            continue
        if row["input"].get("domain") == label.domain:
            matched += 1
        else:
            mismatched += 1
    lines.extend(
        [
            "## Caller-label match breakdown",
            f"Matched gold: {matched}; caller-label mismatch/disagreement: {mismatched}.",
            "",
            "## Go/no-go recommendation",
            recommendation,
            "",
            "## Report provenance",
            f"Repository HEAD: {repo_head}.",
            f"Provenance drift fraction: {provenance_drift_fraction:.6f}.",
            manifest_citation,
            "",
            "## Gate",
            "A go/no-go verdict is not flip-authorizing unless both gate "
            "conditions hold.",
            "Rule: the auto-checkable provenance drift fraction PASSES when "
            f"below {_DRIFT_WARNING_THRESHOLD} and FAILS when at or above it.",
            f"This run: the auto-checkable drift-threshold half {gate_verdict}.",
            "Guarded-module reminder: this script does not auto-check whether "
            "a guarded module changed after the manifest's regeneration date; "
            "the operator must verify that condition manually.",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Load inputs, enforce provenance, and emit the shadow KC report.

    Args:
        argv: Command-line arguments excluding the program name. When None,
            argparse reads ``sys.argv[1:]``.

    Returns:
        Zero after a completed report; non-zero for loading, provenance, or
        output errors.
    """
    args = _parse_args(argv)

    manifest_sha256, manifest = _load_manifest_for_integrity_check(args.manifest)
    integrity_error = _check_corpus_hash_integrity(args.corpus, manifest)
    if integrity_error is not None:
        print(integrity_error, file=sys.stderr)
        return 1

    try:
        catalog_path = _resolve_catalog_path(args.catalog_path)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) and exc.code != 0 else 1

    try:
        catalog = load_catalog(catalog_path)
    except Exception as exc:
        print(f"ERROR loading dispatch catalog: {exc}", file=sys.stderr)
        return 1

    try:
        entries = load_corpus(args.corpus)
        gold = load_labels(args.labels)
    except Exception as exc:
        print(f"ERROR loading report inputs: {exc}", file=sys.stderr)
        return 1

    rows = [entry.raw for entry in entries]
    try:
        partition = _provenance_partition(rows, args.repo_root, catalog)
    except ProvenanceGuardError as exc:
        print(
            f"ERROR: matcher_version provenance guard: {exc}",
            file=sys.stderr,
        )
        return 1

    provenance_drift_fraction = _provenance_drift_fraction(partition)
    if not _gate_passes(provenance_drift_fraction):
        print(
            "WARNING: provenance drift "
            f"{provenance_drift_fraction:.1%} "
            f"(excluded rows: {len(partition.excluded)}; "
            f"unverifiable rows: {len(partition.unverifiable)}); "
            "see issue #510.",
            file=sys.stderr,
        )

    for corpus_id, reason in sorted(partition.excluded.items()):
        print(
            f"Excluded corpus_id {corpus_id} from KC computation: {reason}",
            file=sys.stderr,
        )
    for corpus_id, reason in sorted(partition.unverifiable.items()):
        print(
            f"Unverifiable corpus_id {corpus_id}; excluded from KC computation: {reason}",
            file=sys.stderr,
        )

    rows = [row for row in rows if int(row["corpus_id"]) in partition.included]

    try:
        repo_head_result = _run_git(args.repo_root, "rev-parse", "HEAD")
        repo_head = repo_head_result.stdout.strip()
        if repo_head_result.returncode != 0 or not repo_head:
            detail = repo_head_result.stderr.strip() or "git rev-parse returned no SHA"
            raise RuntimeError(f"cannot resolve repository HEAD: {detail}")

        verdicts = [
            compute_kc1(rows, gold),
            compute_kc2(rows, gold),
            compute_kc3(rows, gold),
            compute_kc4(rows, gold),
            compute_kc5(rows, gold),
        ]
        recommendation = _recommendation(verdicts)
        report = _render_report(
            verdicts,
            rows,
            gold,
            recommendation,
            provenance_drift_fraction,
            repo_head,
            manifest_sha256,
        )
        print(report)

        if args.json is not None:
            gate_passes = _gate_passes(provenance_drift_fraction)
            gate_status = "PASS" if gate_passes else "FAIL"
            payload = {
                "criteria": [asdict(verdict) for verdict in verdicts],
                "flip_authorized": gate_passes,
                "gate_status": gate_status,
                "gate_threshold": _DRIFT_WARNING_THRESHOLD,
                "overall_recommendation": recommendation,
                "provenance_drift_fraction": provenance_drift_fraction,
            }
            args.json.write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
    except Exception as exc:
        print(f"ERROR generating report: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
