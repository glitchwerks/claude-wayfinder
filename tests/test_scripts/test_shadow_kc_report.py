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

1. **[SUPERSEDED by issue #501 -- see items 4-8 below.] Exit-code
   resolution on a matcher_version provenance-guard failure.** The
   original two-part boolean guard (``_provenance_guard(rows,
   repo_root) -> bool``, whole-run abort on any mismatch) asserted a
   **non-zero** exit code on ANY provenance failure -- mixed
   ``matcher_version`` stamps across rows, or any dependency-module diff
   since the recorded version. Issue #501 replaces that whole two-part
   model with a **per-row** HEAD-vs-baseline ``compose_route`` agreement
   check that partitions rows into included / excluded / unverifiable
   buckets rather than aborting the whole run on any single row's
   provenance problem. The tests below implementing that per-row model
   supersede ``TestMatcherVersionGuard`` from the original design; see
   item 4 for the new contract. A **dirty working tree** on either
   dependency module (see item 6) is the one case that still hard-aborts
   the whole run -- there is no stable HEAD baseline to compare against
   in that case, which is a global problem, not a per-row one.
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
4. **[NEW, issue #501] Guard function name/signature and partition
   shape.** ``_provenance_partition(rows: list[CorpusRow], repo_root:
   Path, catalog: list[CatalogEntry]) -> ProvenancePartition`` replaces
   ``_provenance_guard(rows, repo_root) -> bool`` entirely (both the
   one-consistent-version gate and the module file-diff check are
   dropped per issue #501's §4a "full narrow" design -- per-row
   HEAD-vs-baseline ``compose_route`` agreement subsumes the intent of
   both). ``ProvenancePartition`` is a frozen dataclass with three
   fields: ``included: frozenset[int]`` (corpus_ids where HEAD and the
   row's baseline ``compose_route`` agree, or where baseline == HEAD
   trivially), ``excluded: dict[int, str]`` (corpus_id -> a string
   naming which field(s) disagreed, e.g. mentions ``"agent"``,
   ``"decision"``, or ``"posture_routed"``), and ``unverifiable: dict[int,
   str]`` (corpus_id -> a reason the row's ``matcher_version`` could not
   be resolved to a git revision at all). Every corpus_id in the input
   ``rows`` appears in exactly one bucket. The comparison vehicle is
   ``compose_route`` (``src/claude_wayfinder/match/_compose.py:296``)
   run against the row's own logged caller labels
   (``input.{domain,posture,confidence,area_span}``) and ONE shared,
   caller-supplied ``catalog`` for both the baseline and HEAD calls --
   never ``scripts/corpus/eval/_systems.run_supplied_compose`` (a
   divergent reimplementation blind to ``_compose.py`` changes) and
   never gold labels.
5. **[NEW, issue #501] Rig-isolation self-check.**
   ``_verify_rig_isolation(repo_root: Path, baseline_revision: str,
   head_revision: str, catalog: list[CatalogEntry]) -> None`` is a
   dedicated self-check that ``_provenance_partition`` calls (whenever a
   row's resolved baseline revision differs from HEAD's resolved
   revision) BEFORE trusting that row's ``compose_route`` comparison. It
   raises ``RigIsolationError`` when the two-version import mechanism
   appears to have loaded the *same* code for two textually-different
   revisions (the module-cache-collision false-negative documented in
   issue #500 §3.4) -- a false-negative here means every row would
   silently show "agreement" even though the import rig is broken. When
   a row's resolved baseline revision equals HEAD's resolved revision
   (the common case -- most corpus rows are logged against a version at
   or near HEAD), there is nothing to isolate (only one version exists)
   and the row is included without invoking the self-check.
6. **[NEW, issue #501] Exception hierarchy for whole-run aborts.**
   ``class ProvenanceGuardError(RuntimeError)`` is raised by
   ``_provenance_partition`` (not returned as a partition) when the
   guard cannot proceed at all: a dirty working tree on either
   dependency module at HEAD (no stable HEAD baseline exists to compare
   any row against), or ``repo_root`` not being a git repository at all.
   ``class RigIsolationError(ProvenanceGuardError)`` is the rig-isolation
   self-check's specific failure. ``main()`` catches
   ``ProvenanceGuardError`` and returns a non-zero exit code, mirroring
   the old boolean guard's whole-run-abort behavior for these two global
   cases only -- everything else is now a per-row partition, not a
   global abort.
7. **[NEW, issue #501] `--catalog-path PATH` (new, optional CLI flag).**
   ``_provenance_partition`` needs a real, fixed ``list[CatalogEntry]``
   to drive ``build_features``/``score_entries``/``compose_route`` for
   its per-row comparisons -- reusing the project's existing
   ``--catalog-path`` / ``DISPATCH_CATALOG_PATH`` resolution convention
   (``src/claude_wayfinder/match/_catalog.py:_resolve_catalog_path``:
   explicit flag wins, then the env var, else a loud ``[CATALOG ERROR]``
   and non-zero exit) rather than inventing new resolution semantics.
   This suite's ``_run_main`` helper auto-provisions a small hermetic
   default catalog and passes ``--catalog-path`` under the hood so
   existing (non-guard-focused) test call sites do not need to change.
8. **[NEW, issue #501] KC computation scope after partitioning.** KC-1
   through KC-5 computation (unchanged per issue #501's explicit "KC
   computation is untouched" -- the plan this issue's design rests on,
   §4) still runs over the corpus rows, but only the ``included``
   partition -- ``excluded`` and ``unverifiable`` rows are dropped
   before KC compute, not fed to it. This suite does not re-pin the
   exact KC-per-row filtering call site (that is an implementation
   detail); it asserts the externally observable behavior: a run with
   some excluded/unverifiable rows still completes (exit 0) and the
   report/stderr surfaces the exclusion, rather than the whole run
   aborting non-zero the way the old boolean guard did.
9. **[NEW, issue #510] Provenance drift-fraction warning.** A new pure
   function, ``_provenance_drift_fraction(partition: ProvenancePartition)
   -> float``, computes ``(len(excluded) + len(unverifiable)) /
   total_rows`` where ``total_rows = len(included) + len(excluded) +
   len(unverifiable)``, returning ``0.0`` when ``total_rows == 0`` (no
   divide-by-zero). A module-level constant, ``_DRIFT_WARNING_THRESHOLD
   = 0.25``, names the 25% threshold from the issue's scoping decision.
   ``main()`` prints a ``WARNING:`` line to stderr (naming the fraction,
   the excluded/unverifiable counts, and a pointer to issue #510) when
   the fraction is ``>= _DRIFT_WARNING_THRESHOLD``, BEFORE the KC
   verdicts are printed to stdout -- this suite pins that ordering by
   monkeypatching ``sys.stdout``/``sys.stderr`` with write-order
   recorders rather than merely asserting presence on each stream
   independently (two ``capsys.readouterr()`` calls cannot recover
   cross-stream ordering). The ``--json`` payload always carries a
   ``"provenance_drift_fraction"`` float field, regardless of whether
   the threshold was crossed, so consumers can see the number on every
   run, not just flagged ones. This suite does not pin an exact wording
   or percent-vs-fraction display format for the warning text beyond
   what the spec requires (fraction, counts, issue pointer all
   discoverable) -- see ``TestProvenanceDriftWarningMainIntegration``.
10. **[NEW, issue #518] Manifest citation, drift-in-report, repo HEAD,
    and the explicit Gate section.** ``--manifest PATH`` (new, optional
    CLI flag) points at a corpus-manifest JSON file; when provided, the
    report cites ``hashlib.sha256`` of that file's raw bytes, and when
    omitted the report states manifest citation is unavailable rather
    than silently dropping the section or crashing. The already-computed
    ``provenance_drift_fraction`` (item 9) must now also render in the
    report body unconditionally, not only as a stderr WARNING (which
    only fires over-threshold) or the ``--json`` payload. The report
    also cites the git HEAD SHA of ``--repo-root`` at generation time.
    An explicit "Gate" section states the provisional-verdict rule as
    fixed text -- a go/no-go verdict is NOT flip-authorizing if
    ``provenance_drift_fraction >= _DRIFT_WARNING_THRESHOLD`` (reusing
    the item-9 module constant, not a second hardcoded literal) OR if a
    guarded module changed after the manifest's regeneration date (the
    latter is NOT auto-checked by this script -- rendered as an explicit
    manual-verification reminder for the human operator) -- and states
    which case applies for the run just generated (whether the
    auto-checkable drift half PASSES or FAILS). This suite does not pin
    exact wording beyond the discoverable concepts above (mirroring item
    9's precedent) -- see ``TestManifestCitationInReport``,
    ``TestProvenanceDriftFractionInReportBody``,
    ``TestRepoHeadCitationInReport``, ``TestGateSection``.
11. **[NEW, issue #532] Gate encoded in the ``--json`` payload, not just
    prose.** ``TestGateSection`` (item 10) only pins the gate's rule and
    per-run verdict in the human-readable Markdown report body. The
    ``--json`` payload never carries an equivalent machine-readable
    signal -- a caller parsing only ``criteria``/``overall_recommendation``
    can see an all-PASS report and conclude "go" while the evidence
    itself is untrustworthy (high provenance drift), because nothing in
    the JSON says so independent of the KC verdicts. This suite designs
    three new top-level ``--json`` keys (no prior contract existed):
    ``"gate_threshold"`` (float, must equal the module's own
    ``_DRIFT_WARNING_THRESHOLD`` -- not a second hardcoded literal),
    ``"gate_status"`` (``"PASS"`` or ``"FAIL"``, the auto-checkable
    drift-vs-threshold half of the Gate section, mirrored into JSON),
    and ``"flip_authorized"`` (bool -- whether this run's provenance
    drift is low enough that the KC verdicts may be trusted to
    authorize a flip decision AT ALL, independent of what the
    individual KC criteria say). **Scope note:** per the existing Gate
    section's own prose (item 10), flip-authorization has TWO
    conditions -- the auto-checkable drift-vs-threshold comparison, and
    a guarded-module-changed-since-manifest-regeneration check that
    this script does NOT auto-check (rendered as a manual-verification
    reminder). ``"flip_authorized"`` in JSON mirrors ONLY the
    auto-checkable half (``gate_status == "PASS"``), identically to how
    the report's own "This run: ... PASSES/FAILS" line only ever speaks
    to that same half. It is not a claim that a flip is fully cleared --
    the manual guarded-module check is still required regardless of
    this field's value. See ``TestGateEncodedInJson``.
12. **[NEW, issue #532] Corpus-hash integrity check.** The manifest is
    hashed for citation (item 10, ``hashlib.sha256`` of the manifest
    file's own bytes -- ``TestManifestCitationInReport``), but the
    manifest's OWN recorded ``"sha256"`` field (the corpus artifact's
    hash, per ``build_manifest`` -- confirmed schema in
    ``tests/test_corpus/test_builder.py::test_manifest_sha256_matches_artifact``)
    is never compared against the actual bytes of the file supplied via
    ``--corpus``. This suite designs: when the manifest (loaded via
    ``--manifest``, JSON) records a ``"sha256"`` key, and that value
    does not match ``hashlib.sha256(Path(--corpus).read_bytes()).hexdigest()``,
    the generator must fail loudly (non-zero exit, no KC verdicts
    printed) rather than silently computing KC results against a
    corpus that does not match its own recorded provenance. A manifest
    with no ``"sha256"`` key at all (as already exercised by
    ``TestManifestCitationInReport``) is unaffected -- there is nothing
    to compare, so citation-only behavior continues unchanged. See
    ``TestCorpusHashIntegrityCheck``.

Public API designed here (the implementer builds to match):

    scripts/shadow-kc-report.py exposes ``main(argv: list[str] | None) -> int``
    (mirrors the ``scripts/corpus/eval/__main__.py`` convention).

    CLI flags: ``--corpus PATH`` (required), ``--labels PATH`` (required),
    ``--json PATH`` (optional), ``--repo-root PATH`` (optional, default cwd),
    ``--catalog-path PATH`` (optional, falls back to ``DISPATCH_CATALOG_PATH``
    env var, else fails loud -- issue #501, item 7), ``--manifest PATH``
    (optional, issue #518 item 10).

    ``_provenance_drift_fraction(partition: ProvenancePartition) -> float``
    and module-level ``_DRIFT_WARNING_THRESHOLD: float = 0.25`` (issue
    #510, item 9). ``--json`` payload gains a ``"provenance_drift_fraction"``
    float key alongside the existing ``"criteria"``/``"overall_recommendation"``
    keys.

    ``--json`` payload additionally gains ``"gate_threshold"`` (float),
    ``"gate_status"`` (``"PASS"``/``"FAIL"``), and ``"flip_authorized"``
    (bool) top-level keys (issue #532, item 11).

    When ``--manifest``'s JSON records a ``"sha256"`` key, its value
    must match the actual sha256 of the ``--corpus`` file's bytes, or
    ``main()`` returns non-zero and does not print KC verdicts (issue
    #532, item 12).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

import pytest

from claude_wayfinder.match._parse import _parse_triggers
from claude_wayfinder.match._types import CatalogEntry

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


def _make_agent_dict(name: str) -> dict[str, Any]:
    """Build a minimal on-disk (JSON) catalog agent entry for ``name``.

    Args:
        name: Agent name.

    Returns:
        A dict matching the ``load_catalog``-consumed on-disk schema.
    """
    return {
        "name": name,
        "kind": "agent",
        "description": f"Agent {name}.",
        "source": "owned",
        "routable": True,
        "triggers": {
            "command_prefixes": [],
            "agent_mentions": [],
            "path_globs": [],
            "path_globs_excluded": [],
            "keywords": [],
            "tool_mentions": [],
            "excludes": [],
        },
        "applicable_skills": [],
    }


#: Agent names covered by the default hermetic catalog -- chosen to match
#: names already used by ``_corpus_row``/``_gold_row`` default and
#: parametrized call sites across this test module.
_DEFAULT_CATALOG_AGENT_NAMES = (
    "code-writer",
    "ops",
    "investigator",
    "test-implementer",
    "researcher",
)


def _default_catalog_json() -> dict[str, Any]:
    """Build the on-disk (JSON) default hermetic catalog envelope."""
    return {
        "schema_version": 1,
        "entries": [_make_agent_dict(n) for n in _DEFAULT_CATALOG_AGENT_NAMES],
    }


def _provision_default_catalog_path(near: Path) -> Path:
    """Write (idempotently) the default hermetic catalog next to ``near``.

    Args:
        near: A path inside the directory the catalog file should live in
            (typically a ``tmp_path``-derived corpus/labels file).

    Returns:
        Path to the written catalog JSON file.
    """
    catalog_path = near.parent / "_default_catalog.json"
    catalog_path.write_text(
        json.dumps(_default_catalog_json()), encoding="utf-8"
    )
    return catalog_path


def _make_catalog_entry(name: str) -> CatalogEntry:
    """Build a minimal in-memory ``CatalogEntry`` for direct guard calls.

    Args:
        name: Agent name.

    Returns:
        A routable agent :class:`CatalogEntry` with no triggers.
    """
    triggers = _parse_triggers(
        {
            "command_prefixes": [],
            "agent_mentions": [],
            "path_globs": [],
            "path_globs_excluded": [],
            "keywords": [],
            "tool_mentions": [],
            "excludes": [],
        }
    )
    return CatalogEntry(
        name=name,
        kind="agent",
        source="owned",
        routable=True,
        triggers=triggers,
        applicable_skills=(),
        applicable_agents=(),
    )


def _default_catalog_entries() -> list[CatalogEntry]:
    """Build the default hermetic catalog as in-memory ``CatalogEntry``\\ s.

    Returns:
        A list of routable agent entries covering
        ``_DEFAULT_CATALOG_AGENT_NAMES``, for direct (non-subprocess,
        non-CLI) calls into the guard function under test.
    """
    return [_make_catalog_entry(n) for n in _DEFAULT_CATALOG_AGENT_NAMES]


def _run_main(
    mod: ModuleType,
    corpus: Path,
    labels: Path,
    repo_root: Path,
    json_path: Path | None = None,
    catalog_path: Path | None = None,
    manifest_path: Path | None = None,
) -> int:
    """Invoke ``mod.main`` with the standard required flags.

    Args:
        mod: The loaded ``shadow-kc-report.py`` module.
        corpus: Path to the corpus JSONL fixture.
        labels: Path to the gold-labels JSONL fixture.
        repo_root: Path passed as ``--repo-root``.
        json_path: Optional path passed as ``--json``.
        catalog_path: Optional path passed as ``--catalog-path``. When
            ``None``, a small hermetic default catalog is auto-provisioned
            next to ``corpus`` so existing (non-guard-focused) call sites
            do not need to supply one explicitly (issue #501, judgment
            call 7).
        manifest_path: Optional path passed as ``--manifest`` (issue
            #518, judgment call 10). When ``None``, ``--manifest`` is
            omitted entirely so existing (non-manifest-focused) call
            sites do not need to supply one explicitly.

    Returns:
        The CLI's exit code.
    """
    if catalog_path is None:
        catalog_path = _provision_default_catalog_path(corpus)
    argv = [
        "--corpus",
        str(corpus),
        "--labels",
        str(labels),
        "--repo-root",
        str(repo_root),
        "--catalog-path",
        str(catalog_path),
    ]
    if json_path is not None:
        argv += ["--json", str(json_path)]
    if manifest_path is not None:
        argv += ["--manifest", str(manifest_path)]
    return mod.main(argv)


def _kc_status_appears(report: str, kc: str, status: str) -> bool:
    """Return True if some report line mentions both ``kc`` and ``status``."""
    return any(kc in line and status in line for line in report.splitlines())


def _build_all_kc_pass_fixture(
    tmp_path: Path,
    sha: str,
    unverifiable_ids: frozenset[int] = frozenset(),
) -> tuple[Path, Path]:
    """Build the same 10-row corpus as ``TestReportStructure._build_fixture``
    (KC-1/2/3 PASS, KC-4/5 INSUFFICIENT_DATA on the full, no-drift
    sample), optionally marking a subset of row ids as unverifiable
    (an unresolvable ``matcher_version``) to control
    ``provenance_drift_fraction`` independently of the KC verdicts --
    used by ``TestGateEncodedInJson`` to build both a below-threshold
    (no unverifiable rows) and an at/above-threshold fixture that both
    still yield PASS on KC-1/2/3 over their respective ``included``
    subsets.

    With ``unverifiable_ids == {6, 9, 10}``: rows 9-10 (already the
    "both shadow and lexical wrong, ungated-delegate" rows) are dropped
    entirely from computation, and row 6 (one of the "both correct,
    posture-routed" rows) is also dropped -- leaving rows
    {1, 2, 3, 4, 5, 7, 8} (7 rows) included. Over that included subset:
    shadow_rc = 7/7 = 1.0 (rows 7-8 still shadow-correct); lexical_rc =
    5/7 (rows 7-8 lexical-wrong) approx 0.714; KC-1 PASS (1.0 >= 0.6891
    and 1.0 >= 0.714 + 0.20). KC-2: 0 wrong-delegates / 7 delegates = 0.0
    <= 0.2558, PASS. KC-3: all 7 included rows are posture_routed=True,
    numerator/eligible = 7/7 = 1.0 >= 0.55, PASS. Drift = 3 unverifiable
    / 10 total = 0.3, at/above the 0.25 threshold.

    Args:
        tmp_path: Pytest tmp_path fixture directory to write fixtures under.
        sha: A resolvable git revision (``guard_repo``'s commit SHA) used
            for every row NOT listed in ``unverifiable_ids``.
        unverifiable_ids: Corpus ids to stamp with an unresolvable
            ``matcher_version`` instead of ``sha``.

    Returns:
        Tuple of ``(corpus_path, labels_path)``.
    """

    def _mv(corpus_id: int) -> str:
        if corpus_id in unverifiable_ids:
            return f"not-a-real-revision-{corpus_id}"
        return sha

    return TestReportStructure()._build_fixture(
        tmp_path,
        sha,
        matcher_version_fn=_mv,
    )


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
# Two-commit ("baseline" + "HEAD") fixture for the per-row HEAD-vs-baseline
# compose_route provenance partition (issue #501). The fixture's
# ``_compose.py`` is a small, self-contained FAKE ``compose_route`` -- not
# the real production algorithm -- so these tests stay fast/isolated and do
# not need the full production Labels/Features/ScoredEntry/CatalogEntry
# machinery wired through a real git worktree per commit. It reads only
# ``labels.domain`` off whatever ``Labels`` object the guard passes in,
# which is a real, stable production ``Labels`` instance (built by HEAD's
# own ``parse_labels`` -- ``_types.Labels`` is not one of the two guarded
# dependency modules, so it is safe to rely on its real attribute shape
# here without pulling in a fixture-repo copy of it).
# ---------------------------------------------------------------------------

#: Domain value the "v1"/"v2" fake compose_route bodies disagree on --
#: rows whose caller-logged ``input.domain`` is this value get a DIFFERENT
#: routing decision at "v1" vs "v2", simulating a small, partial-impact
#: change like the real 07eb3dd delta (issue #499/#500) that only flips
#: some corpus rows, not all of them.
_FLAKY_DOMAIN = "flaky"

_FAKE_COMPOSE_V1 = '''\
"""Fake compose_route fixture (v1) -- disposable guard-repo test only."""


def compose_route(
    labels,
    scored_agents,
    scored_skills,
    features,
    catalog,
    catalog_agent_names,
    diagnostics=None,
):
    if labels.domain == "flaky":
        return {
            "decision": "delegate",
            "agent": "agent-old",
            "posture_routed": True,
        }
    return {
        "decision": "delegate",
        "agent": "agent-stable",
        "posture_routed": False,
    }
'''

_FAKE_COMPOSE_V2 = '''\
"""Fake compose_route fixture (v2) -- disposable guard-repo test only."""


def compose_route(
    labels,
    scored_agents,
    scored_skills,
    features,
    catalog,
    catalog_agent_names,
    diagnostics=None,
):
    if labels.domain == "flaky":
        return {
            "decision": "self_handle",
            "agent": None,
            "posture_routed": True,
        }
    return {
        "decision": "delegate",
        "agent": "agent-stable",
        "posture_routed": False,
    }
'''


@pytest.fixture
def versioned_guard_repo(tmp_path: Path) -> tuple[Path, str]:
    """A disposable git repo with two commits carrying diverging fake
    ``compose_route`` bodies, simulating a small partial-impact
    ``_compose.py`` change (like the real 07eb3dd delta).

    Commit 1 ("baseline"): fake ``compose_route`` returns
    ``agent="agent-old"`` for ``labels.domain == "flaky"`` rows and
    ``agent="agent-stable"`` for everything else.

    Commit 2 (the repo's HEAD): fake ``compose_route`` CHANGES behavior
    only for ``labels.domain == "flaky"`` rows (now
    ``decision="self_handle"``, ``agent=None``) -- rows with any other
    domain get byte-identical treatment across both commits.

    Returns:
        Tuple of ``(repo_root, baseline_matcher_version)`` where
        ``baseline_matcher_version`` is the short SHA of commit 1. The
        repo's HEAD (commit 2) is a distinct, later revision.
    """
    repo_root = tmp_path / "versioned_repo"
    repo_root.mkdir()
    _init_fixture_repo(repo_root)
    _write_dep_files(
        repo_root,
        compose_content=_FAKE_COMPOSE_V1,
        cells_content="# cells v1\n",
    )
    baseline_sha = _commit_all(repo_root, "baseline compose_route")

    _write_dep_files(
        repo_root,
        compose_content=_FAKE_COMPOSE_V2,
        cells_content="# cells v1\n",
    )
    _commit_all(repo_root, "07eb3dd-like partial-impact change")

    return repo_root, baseline_sha


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
# matcher_version provenance guard (issue #501 -- per-row HEAD-vs-baseline
# compose_route partition, replacing the old TestMatcherVersionGuard
# whole-run boolean guard entirely; see module docstring judgment calls
# 4-8).
# ---------------------------------------------------------------------------


class TestProvenancePartitionDirect:
    """Direct (non-subprocess, non-main()) calls to
    ``_provenance_partition(rows, repo_root, catalog) ->
    ProvenancePartition``. Fast/isolated: uses disposable, throwaway git
    fixture repos carrying a small self-contained FAKE ``compose_route``
    (see ``versioned_guard_repo``), not the real production algorithm.
    """

    def test_row_with_baseline_equal_to_head_is_included(
        self,
        kc_report_module: ModuleType,
        guard_repo: tuple[Path, str],
    ) -> None:
        """The common case -- a row's resolved baseline revision equals
        HEAD's resolved revision (nothing to compare, trivially valid).
        ``guard_repo``'s single-commit ``_compose.py`` content
        (``"# compose v1\\n"``) defines no ``compose_route`` function at
        all, so this also pins that the implementation must SHORTCUT
        the comparison in this case rather than attempting to import a
        nonexistent function from both (identical) revisions.
        """
        repo_root, sha = guard_repo
        rows = [_corpus_row(1, matcher_version=sha)]
        catalog = _default_catalog_entries()

        partition = kc_report_module._provenance_partition(rows, repo_root, catalog)

        assert partition.included == frozenset({1})
        assert partition.excluded == {}
        assert partition.unverifiable == {}

    def test_row_where_baseline_and_head_compose_route_agree_is_included(
        self,
        kc_report_module: ModuleType,
        versioned_guard_repo: tuple[Path, str],
    ) -> None:
        """A row whose caller-logged domain is NOT ``"flaky"`` gets
        byte-identical treatment from the fixture's v1 (baseline) and v2
        (HEAD) fake ``compose_route`` bodies -- agreement, included.
        """
        repo_root, baseline_sha = versioned_guard_repo
        rows = [_corpus_row(1, domain="code", matcher_version=baseline_sha)]
        catalog = _default_catalog_entries()

        partition = kc_report_module._provenance_partition(rows, repo_root, catalog)

        assert 1 in partition.included
        assert 1 not in partition.excluded
        assert 1 not in partition.unverifiable

    def test_row_where_baseline_and_head_compose_route_disagree_is_excluded(
        self,
        kc_report_module: ModuleType,
        versioned_guard_repo: tuple[Path, str],
    ) -> None:
        """A row whose caller-logged domain IS ``"flaky"`` gets DIFFERENT
        treatment from the fixture's v1 (baseline) vs v2 (HEAD) fake
        ``compose_route`` -- genuine disagreement, excluded.

        This is also the test that would fail if the implementation
        wrongly used ``scripts/corpus/eval/_systems.run_supplied_compose``
        (or any vehicle blind to this fixture's throwaway
        ``_compose.py``) instead of actually running the per-revision
        ``compose_route`` found in ``repo_root``: such an implementation
        has no way to observe the fixture's v1/v2 divergence and would
        report every row as agreeing (``run_supplied_compose`` only
        knows about ``scripts/corpus/eval/_systems.py``, which this
        fixture never touches), so this row would incorrectly land in
        ``included`` instead of ``excluded``.
        """
        repo_root, baseline_sha = versioned_guard_repo
        rows = [_corpus_row(1, domain=_FLAKY_DOMAIN, matcher_version=baseline_sha)]
        catalog = _default_catalog_entries()

        partition = kc_report_module._provenance_partition(rows, repo_root, catalog)

        assert 1 in partition.excluded, (
            "a row whose baseline and HEAD compose_route disagree must be "
            f"excluded, not included; partition: {partition!r}"
        )
        assert 1 not in partition.included
        assert 1 not in partition.unverifiable

    def test_excluded_reason_names_a_disagreeing_field(
        self,
        kc_report_module: ModuleType,
        versioned_guard_repo: tuple[Path, str],
    ) -> None:
        """The excluded-row reason must name which ``compose_route``
        field(s) disagreed (``agent``/``decision``/``posture_routed``),
        not just a generic "mismatch" string -- issue #501 acceptance
        criterion: "reports which rows are excluded ... not just
        pass/fail".
        """
        repo_root, baseline_sha = versioned_guard_repo
        rows = [_corpus_row(1, domain=_FLAKY_DOMAIN, matcher_version=baseline_sha)]
        catalog = _default_catalog_entries()

        partition = kc_report_module._provenance_partition(rows, repo_root, catalog)

        reason = partition.excluded[1]
        assert isinstance(reason, str) and reason.strip(), (
            "the excluded reason must be a non-empty, human-legible string"
        )
        assert re.search(r"agent|decision|posture_routed", reason, re.IGNORECASE), (
            f"the excluded reason must name a disagreeing compose_route "
            f"field; got: {reason!r}"
        )

    def test_unresolvable_matcher_version_is_unverifiable_not_a_global_failure(
        self,
        kc_report_module: ModuleType,
        versioned_guard_repo: tuple[Path, str],
    ) -> None:
        """A row whose ``matcher_version`` cannot be resolved to any git
        revision (bare or ``v``-prefixed) is UNVERIFIABLE -- a distinct
        third bucket, not silently dropped into included or excluded,
        and NOT a reason to abort the whole guard call (contrast with
        ``test_dirty_dependency_module_raises_provenance_guard_error``,
        which IS a whole-call abort).
        """
        repo_root, _baseline_sha = versioned_guard_repo
        rows = [_corpus_row(1, matcher_version="zzz-not-a-real-revision")]
        catalog = _default_catalog_entries()

        partition = kc_report_module._provenance_partition(rows, repo_root, catalog)

        assert 1 in partition.unverifiable
        assert 1 not in partition.included
        assert 1 not in partition.excluded
        reason = partition.unverifiable[1]
        assert isinstance(reason, str) and reason.strip()

    def test_partition_covers_every_row_exactly_once_across_all_buckets(
        self,
        kc_report_module: ModuleType,
        versioned_guard_repo: tuple[Path, str],
    ) -> None:
        """A corpus mixing all three outcomes -- included, excluded,
        unverifiable -- partitions every corpus_id into EXACTLY one
        bucket: no row lost, no row double-counted (issue #501
        acceptance criterion, spec item 2).
        """
        repo_root, baseline_sha = versioned_guard_repo
        rows = [
            _corpus_row(1, domain="code", matcher_version=baseline_sha),  # agree
            _corpus_row(2, domain=_FLAKY_DOMAIN, matcher_version=baseline_sha),  # disagree
            _corpus_row(3, matcher_version="not-a-real-revision-either"),  # unresolvable
        ]
        catalog = _default_catalog_entries()

        partition = kc_report_module._provenance_partition(rows, repo_root, catalog)

        all_ids = set(partition.included) | set(partition.excluded) | set(
            partition.unverifiable
        )
        assert all_ids == {1, 2, 3}, f"every corpus_id must appear once; got {all_ids!r}"
        assert 1 in partition.included
        assert 2 in partition.excluded
        assert 3 in partition.unverifiable
        # No row appears in more than one bucket.
        assert set(partition.included) & set(partition.excluded) == set()
        assert set(partition.included) & set(partition.unverifiable) == set()
        assert set(partition.excluded) & set(partition.unverifiable) == set()

    def test_dirty_dependency_module_raises_provenance_guard_error(
        self,
        kc_report_module: ModuleType,
        guard_repo: tuple[Path, str],
    ) -> None:
        """A dirty (uncommitted) dependency-module change at HEAD means
        there is no stable, trustworthy HEAD baseline to compare ANY row
        against -- a global problem, so the whole guard call fails
        closed (raises), rather than degrading to a per-row bucket.
        """
        repo_root, sha = guard_repo
        target = repo_root / "src" / "claude_wayfinder" / "match" / "_compose.py"
        target.write_text(
            target.read_text(encoding="utf-8") + "\n# uncommitted\n",
            encoding="utf-8",
        )
        rows = [_corpus_row(1, matcher_version=sha)]
        catalog = _default_catalog_entries()

        with pytest.raises(kc_report_module.ProvenanceGuardError):
            kc_report_module._provenance_partition(rows, repo_root, catalog)

    def test_repo_root_without_git_raises_provenance_guard_error(
        self,
        kc_report_module: ModuleType,
        tmp_path: Path,
    ) -> None:
        """``repo_root`` not being a git repository at all is a global,
        whole-call problem (nothing is resolvable for any row) -- the
        guard fails closed rather than degrading every row to
        unverifiable one at a time.
        """
        plain_dir = tmp_path / "not_a_repo"
        _write_dep_files(plain_dir, compose_content="# x\n", cells_content="# y\n")
        rows = [_corpus_row(1, matcher_version="abc1234")]
        catalog = _default_catalog_entries()

        with pytest.raises(kc_report_module.ProvenanceGuardError):
            kc_report_module._provenance_partition(rows, plain_dir, catalog)


class TestRigIsolationSelfCheck:
    """The dedicated rig-isolation self-check
    (``_verify_rig_isolation(repo_root, baseline_revision, head_revision,
    catalog) -> None``, raising ``RigIsolationError`` on a detected
    module-cache-collision false-negative -- issue #500 §3.4 / issue
    #501 acceptance criterion) must be part of ``_provenance_partition``'s
    normal call path, not merely available to call manually.
    """

    def test_self_check_is_exercised_in_the_normal_partition_call_path(
        self,
        kc_report_module: ModuleType,
        versioned_guard_repo: tuple[Path, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Force the self-check to always report a collision, then prove
        ``_provenance_partition`` actually invokes it (and fails closed
        because of it) on an otherwise-normal, genuinely-two-version
        corpus. If the self-check were merely defined but never called
        by the guard's normal path, this forced failure would never
        fire and the call would return a partition instead of raising.
        """
        repo_root, baseline_sha = versioned_guard_repo

        def _always_flag_collision(*args: Any, **kwargs: Any) -> None:
            raise kc_report_module.RigIsolationError(
                "forced collision for test -- proves the self-check runs"
            )

        monkeypatch.setattr(
            kc_report_module, "_verify_rig_isolation", _always_flag_collision
        )
        rows = [_corpus_row(1, domain="code", matcher_version=baseline_sha)]
        catalog = _default_catalog_entries()

        with pytest.raises(kc_report_module.ProvenanceGuardError):
            kc_report_module._provenance_partition(rows, repo_root, catalog)

    def test_self_check_does_not_fire_when_baseline_equals_head(
        self,
        kc_report_module: ModuleType,
        guard_repo: tuple[Path, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When a row's resolved baseline revision equals HEAD's
        resolved revision, there is only one version in play -- nothing
        to isolate. Forcing the self-check to always fail must NOT
        affect this row: if the guard incorrectly ran (and trusted) the
        self-check here, this forced failure would wrongly abort a
        request that should trivially succeed.
        """
        repo_root, sha = guard_repo  # sha IS this repo's sole commit / HEAD

        def _always_flag_collision(*args: Any, **kwargs: Any) -> None:
            raise kc_report_module.RigIsolationError(
                "must not fire when baseline == HEAD"
            )

        monkeypatch.setattr(
            kc_report_module, "_verify_rig_isolation", _always_flag_collision
        )
        rows = [_corpus_row(1, matcher_version=sha)]
        catalog = _default_catalog_entries()

        partition = kc_report_module._provenance_partition(rows, repo_root, catalog)

        assert 1 in partition.included


class TestCatalogPathFlag:
    """``--catalog-path PATH`` (issue #501, judgment call 7) resolves
    like the project's existing ``--catalog-path`` /
    ``DISPATCH_CATALOG_PATH`` convention: explicit flag, else the env
    var, else fail loud.
    """

    def test_explicit_catalog_path_flag_is_sufficient(
        self,
        kc_report_module: ModuleType,
        guard_repo: tuple[Path, str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("DISPATCH_CATALOG_PATH", raising=False)
        repo_root, sha = guard_repo
        corpus_path = tmp_path / "corpus.jsonl"
        labels_path = tmp_path / "labels.jsonl"
        _write_jsonl(corpus_path, [_corpus_row(1, matcher_version=sha)])
        _write_jsonl(labels_path, [_gold_row(1)])
        catalog_path = _provision_default_catalog_path(corpus_path)

        rc = _run_main(
            kc_report_module,
            corpus_path,
            labels_path,
            repo_root,
            catalog_path=catalog_path,
        )
        assert rc == 0

    def test_missing_catalog_path_and_env_var_fails_loud(
        self,
        kc_report_module: ModuleType,
        guard_repo: tuple[Path, str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.delenv("DISPATCH_CATALOG_PATH", raising=False)
        repo_root, sha = guard_repo
        corpus_path = tmp_path / "corpus.jsonl"
        labels_path = tmp_path / "labels.jsonl"
        _write_jsonl(corpus_path, [_corpus_row(1, matcher_version=sha)])
        _write_jsonl(labels_path, [_gold_row(1)])

        argv = [
            "--corpus",
            str(corpus_path),
            "--labels",
            str(labels_path),
            "--repo-root",
            str(repo_root),
        ]
        rc = kc_report_module.main(argv)
        captured = capsys.readouterr()

        assert rc != 0
        assert re.search(r"catalog", captured.err, re.IGNORECASE), (
            f"missing --catalog-path (and no env var) must fail loud "
            f"naming the catalog problem; stderr:\n{captured.err}"
        )


class TestProvenanceGuardMainIntegration:
    """End-to-end ``main()`` behavior of the per-row provenance
    partition -- carries forward the invariants
    ``TestMatcherVersionGuard`` established for the old boolean guard,
    updated to the new per-row semantics (module docstring judgment
    calls 4-8).
    """

    def test_consistent_matching_version_and_agreeing_dependencies_passes(
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
        assert "Traceback (most recent call last)" not in captured.err

    def test_mixed_matcher_versions_across_rows_now_succeeds_when_each_row_agrees(
        self,
        kc_report_module: ModuleType,
        versioned_guard_repo: tuple[Path, str],
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """[Was ``test_mixed_matcher_versions_across_rows_fails`` under
        the old boolean guard.] Issue #501 explicitly DROPS the
        one-consistent-version gate: a corpus with rows stamped at
        different (individually resolvable, individually agreeing)
        ``matcher_version`` values must NOT itself fail the run -- the
        real-world 245-row corpus (issue #499/#500) carried three
        different stamps and this was exactly the false-positive block
        issue #501 exists to remove.
        """
        repo_root, baseline_sha = versioned_guard_repo
        head_sha = _run_git(["rev-parse", "HEAD"], cwd=repo_root).stdout.strip()
        rows = [
            _corpus_row(1, domain="code", matcher_version=baseline_sha),
            _corpus_row(2, domain="code", matcher_version=head_sha),
        ]
        gold = [_gold_row(1), _gold_row(2)]
        corpus_path = tmp_path / "corpus.jsonl"
        labels_path = tmp_path / "labels.jsonl"
        _write_jsonl(corpus_path, rows)
        _write_jsonl(labels_path, gold)

        rc = _run_main(kc_report_module, corpus_path, labels_path, repo_root)
        captured = capsys.readouterr()

        assert rc == 0, (
            "rows stamped at two DIFFERENT but individually-agreeing "
            f"matcher_versions must not fail the run; stderr:\n{captured.err}"
        )
        assert "Traceback (most recent call last)" not in captured.err

    def test_dirty_working_tree_on_dependency_file_still_fails(
        self,
        kc_report_module: ModuleType,
        guard_repo: tuple[Path, str],
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Carried forward unchanged: a dirty dependency-module working
        tree at HEAD means no stable baseline exists for ANY row, so
        this remains a whole-run abort (module docstring judgment call
        1/6) even under the new per-row partition design.
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

    def test_unknown_matcher_version_is_unverifiable_run_still_completes(
        self,
        kc_report_module: ModuleType,
        guard_repo: tuple[Path, str],
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """[Was ``test_unknown_matcher_version_string_fails`` under the
        old boolean guard, asserting ``rc != 0``.] Under the new per-row
        partition, an unresolvable ``matcher_version`` (e.g. the
        dist-version fallback ``"unknown"``) marks ONLY that row
        unverifiable -- it is not a reason to abort the whole run.
        """
        repo_root, sha = guard_repo
        rows = [
            _corpus_row(1, matcher_version="unknown"),
            _corpus_row(2, matcher_version=sha),
        ]
        gold = [_gold_row(1), _gold_row(2)]
        corpus_path = tmp_path / "corpus.jsonl"
        labels_path = tmp_path / "labels.jsonl"
        _write_jsonl(corpus_path, rows)
        _write_jsonl(labels_path, gold)

        rc = _run_main(kc_report_module, corpus_path, labels_path, repo_root)
        captured = capsys.readouterr()

        assert rc == 0, (
            "an unresolvable matcher_version on one row must not abort the "
            f"whole run; stderr:\n{captured.err}"
        )
        assert "Traceback (most recent call last)" not in captured.err

    def test_repo_root_without_git_fails_safe(
        self,
        kc_report_module: ModuleType,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Carried forward unchanged: ``repo_root`` not being a git repo
        at all remains a whole-run, fail-safe abort.
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
        """Carried forward: real corpus data records ``matcher_version``
        as a bare semver string (e.g. ``"1.3.1"``, no ``v`` prefix)
        while this project's release tags are named ``vX.Y.Z`` (issue
        #485 bug report). A bare rev-parse of the recorded string alone
        fails, but the version genuinely is current: the guard must fall
        back to resolving against the ``v``-prefixed tag name before
        declaring the row unverifiable. Under the new design this row
        also resolves to a baseline == HEAD trivial-agree (the tagged
        commit is this fixture's sole commit), so the run must PASS.
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
        assert "unverifiable" not in captured.err.lower()

    def test_row_with_diverging_compose_route_is_excluded_but_run_completes(
        self,
        kc_report_module: ModuleType,
        versioned_guard_repo: tuple[Path, str],
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A row whose baseline-vs-HEAD compose_route genuinely disagrees
        is excluded from the KC-computation substrate, but -- unlike the
        old boolean guard -- this does NOT abort the whole run, and the
        exclusion is surfaced (report/stderr), not silently dropped
        (issue #501 acceptance criterion).
        """
        repo_root, baseline_sha = versioned_guard_repo
        rows = [
            _corpus_row(1, domain=_FLAKY_DOMAIN, matcher_version=baseline_sha),
            _corpus_row(2, domain="code", matcher_version=baseline_sha),
        ]
        gold = [_gold_row(1, domain=_FLAKY_DOMAIN), _gold_row(2)]
        corpus_path = tmp_path / "corpus.jsonl"
        labels_path = tmp_path / "labels.jsonl"
        _write_jsonl(corpus_path, rows)
        _write_jsonl(labels_path, gold)

        rc = _run_main(kc_report_module, corpus_path, labels_path, repo_root)
        captured = capsys.readouterr()
        combined = captured.out + "\n" + captured.err

        assert rc == 0, (
            "one excluded row alongside one included row must not abort "
            f"the whole run; stderr:\n{captured.err}"
        )
        assert re.search(r"exclud", combined, re.IGNORECASE), (
            "the excluded row must be surfaced in the report/stderr, not "
            f"silently dropped; combined output:\n{combined}"
        )
        assert "1" in combined, (
            "the excluded row's corpus_id (1) should be discoverable in "
            f"the surfaced exclusion detail; combined output:\n{combined}"
        )


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

    def _build_fixture(
        self,
        tmp_path: Path,
        sha: str,
        matcher_version_fn: Callable[[int], str] | None = None,
    ) -> tuple[Path, Path]:
        """Build the shared 10-row report fixture.

        Args:
            tmp_path: Pytest temporary directory for fixture files.
            sha: Default matcher version assigned to every corpus row.
            matcher_version_fn: Optional function deriving a matcher
                version from each corpus ID. When None, every row uses
                ``sha``.

        Returns:
            Tuple of ``(corpus_path, labels_path)``.
        """
        rows = [
            # 1-6: shadow and lexical both correct, posture-routed.
            *[
                _corpus_row(
                    i,
                    shadow_agent="code-writer",
                    live_agent="code-writer",
                    posture_routed=True,
                    matcher_version=(
                        sha
                        if matcher_version_fn is None
                        else matcher_version_fn(i)
                    ),
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
                    matcher_version=(
                        sha
                        if matcher_version_fn is None
                        else matcher_version_fn(i)
                    ),
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
                    matcher_version=(
                        sha
                        if matcher_version_fn is None
                        else matcher_version_fn(i)
                    ),
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


# ---------------------------------------------------------------------------
# Provenance drift-fraction warning (issue #510)
# ---------------------------------------------------------------------------


class TestProvenanceDriftFraction:
    """``_provenance_drift_fraction(partition: ProvenancePartition) ->
    float`` computes ``(len(excluded) + len(unverifiable)) / total_rows``,
    where ``total_rows = len(included) + len(excluded) +
    len(unverifiable)``, returning ``0.0`` when ``total_rows == 0`` (no
    divide-by-zero). Exercised directly against constructed
    ``ProvenancePartition`` instances -- no git fixture or subprocess
    needed, since this is a pure function of the partition's bucket sizes.
    """

    def test_all_included_partition_has_zero_drift(
        self, kc_report_module: ModuleType
    ) -> None:
        partition = kc_report_module.ProvenancePartition(
            included=frozenset({1, 2, 3}),
            excluded={},
            unverifiable={},
        )
        assert kc_report_module._provenance_drift_fraction(partition) == 0.0

    def test_all_excluded_partition_has_full_drift(
        self, kc_report_module: ModuleType
    ) -> None:
        partition = kc_report_module.ProvenancePartition(
            included=frozenset(),
            excluded={1: "r", 2: "r", 3: "r"},
            unverifiable={},
        )
        assert kc_report_module._provenance_drift_fraction(partition) == 1.0

    def test_all_unverifiable_partition_has_full_drift(
        self, kc_report_module: ModuleType
    ) -> None:
        partition = kc_report_module.ProvenancePartition(
            included=frozenset(),
            excluded={},
            unverifiable={1: "r", 2: "r"},
        )
        assert kc_report_module._provenance_drift_fraction(partition) == 1.0

    def test_mixed_partition_above_threshold(
        self, kc_report_module: ModuleType
    ) -> None:
        """4 excluded + 1 unverifiable out of 10 total rows = 0.5 drift."""
        partition = kc_report_module.ProvenancePartition(
            included=frozenset(range(1, 6)),
            excluded={i: "r" for i in range(6, 10)},
            unverifiable={10: "r"},
        )
        fraction = kc_report_module._provenance_drift_fraction(partition)
        assert fraction == pytest.approx(0.5)
        assert fraction >= kc_report_module._DRIFT_WARNING_THRESHOLD

    def test_mixed_partition_below_threshold(
        self, kc_report_module: ModuleType
    ) -> None:
        """1 excluded out of 10 total rows = 0.1 drift."""
        partition = kc_report_module.ProvenancePartition(
            included=frozenset(range(1, 10)),
            excluded={10: "r"},
            unverifiable={},
        )
        fraction = kc_report_module._provenance_drift_fraction(partition)
        assert fraction == pytest.approx(0.1)
        assert fraction < kc_report_module._DRIFT_WARNING_THRESHOLD

    def test_exactly_at_threshold_counts_as_drifted(
        self, kc_report_module: ModuleType
    ) -> None:
        """1 excluded out of 4 total rows = 0.25, exactly at the
        threshold -- the boundary is inclusive (">=", not ">").
        """
        partition = kc_report_module.ProvenancePartition(
            included=frozenset({1, 2, 3}),
            excluded={4: "r"},
            unverifiable={},
        )
        fraction = kc_report_module._provenance_drift_fraction(partition)
        assert fraction == pytest.approx(0.25)
        assert fraction >= kc_report_module._DRIFT_WARNING_THRESHOLD

    def test_empty_partition_has_zero_drift_no_divide_by_zero(
        self, kc_report_module: ModuleType
    ) -> None:
        """Zero total rows must not raise ``ZeroDivisionError``."""
        partition = kc_report_module.ProvenancePartition(
            included=frozenset(),
            excluded={},
            unverifiable={},
        )
        assert kc_report_module._provenance_drift_fraction(partition) == 0.0

    def test_drift_warning_threshold_constant_is_one_quarter(
        self, kc_report_module: ModuleType
    ) -> None:
        assert kc_report_module._DRIFT_WARNING_THRESHOLD == 0.25


class TestProvenanceDriftWarningMainIntegration:
    """``main()`` surfaces the provenance drift fraction: a ``WARNING:``
    stderr line (naming the fraction, the excluded/unverifiable counts,
    and a pointer to issue #510) when drift is at or above
    ``_DRIFT_WARNING_THRESHOLD``, printed before the KC verdicts are
    written to stdout; and always includes a ``"provenance_drift_fraction"``
    float field in the ``--json`` payload, whether or not the threshold
    was crossed (issue #510).
    """

    def test_below_threshold_drift_prints_no_warning(
        self,
        kc_report_module: ModuleType,
        guard_repo: tuple[Path, str],
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """All rows trivially agree (baseline == HEAD) -- 0.0 drift,
        well below the 0.25 threshold -- no WARNING line.
        """
        repo_root, sha = guard_repo
        rows = [_corpus_row(i, matcher_version=sha) for i in range(1, 5)]
        gold = [_gold_row(i) for i in range(1, 5)]
        corpus_path = tmp_path / "corpus.jsonl"
        labels_path = tmp_path / "labels.jsonl"
        _write_jsonl(corpus_path, rows)
        _write_jsonl(labels_path, gold)

        rc = _run_main(kc_report_module, corpus_path, labels_path, repo_root)
        captured = capsys.readouterr()

        assert rc == 0
        assert "WARNING" not in captured.err, (
            "drift below the threshold (0.0) must not print a WARNING "
            f"line; stderr:\n{captured.err}"
        )

    def test_below_threshold_json_field_present_with_actual_value(
        self,
        kc_report_module: ModuleType,
        guard_repo: tuple[Path, str],
        tmp_path: Path,
    ) -> None:
        """The JSON field must be present and correct even when the run
        is entirely clean -- always emitted, not only when flagged.
        """
        repo_root, sha = guard_repo
        rows = [_corpus_row(i, matcher_version=sha) for i in range(1, 5)]
        gold = [_gold_row(i) for i in range(1, 5)]
        corpus_path = tmp_path / "corpus.jsonl"
        labels_path = tmp_path / "labels.jsonl"
        json_path = tmp_path / "report.json"
        _write_jsonl(corpus_path, rows)
        _write_jsonl(labels_path, gold)

        rc = _run_main(kc_report_module, corpus_path, labels_path, repo_root, json_path)
        assert rc == 0

        data = json.loads(json_path.read_text(encoding="utf-8"))
        assert "provenance_drift_fraction" in data, (
            "the JSON payload must always include provenance_drift_fraction"
        )
        assert data["provenance_drift_fraction"] == pytest.approx(0.0)

    def test_at_threshold_drift_prints_warning_naming_fraction_counts_and_issue(
        self,
        kc_report_module: ModuleType,
        versioned_guard_repo: tuple[Path, str],
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """1 excluded row of 4 total = 0.25 drift, exactly at the
        threshold -- the inclusive ">=" boundary must still warn.
        """
        repo_root, baseline_sha = versioned_guard_repo
        rows = [
            _corpus_row(1, domain=_FLAKY_DOMAIN, matcher_version=baseline_sha),
            _corpus_row(2, domain="code", matcher_version=baseline_sha),
            _corpus_row(3, domain="code", matcher_version=baseline_sha),
            _corpus_row(4, domain="code", matcher_version=baseline_sha),
        ]
        gold = [_gold_row(i) for i in range(1, 5)]
        corpus_path = tmp_path / "corpus.jsonl"
        labels_path = tmp_path / "labels.jsonl"
        _write_jsonl(corpus_path, rows)
        _write_jsonl(labels_path, gold)

        rc = _run_main(kc_report_module, corpus_path, labels_path, repo_root)
        captured = capsys.readouterr()

        assert rc == 0
        assert "WARNING" in captured.err, (
            f"drift at exactly the 0.25 threshold must warn; stderr:\n{captured.err}"
        )
        assert "510" in captured.err, (
            f"the warning must point to issue #510; stderr:\n{captured.err}"
        )
        assert re.search(r"0\.25\b|25(\.0)?%", captured.err), (
            f"the warning must name the drift fraction (0.25 / 25%); stderr:\n{captured.err}"
        )
        excluded_pattern = r"exclud\w*\D{0,15}1\b|\b1\D{0,15}exclud\w*"
        assert re.search(excluded_pattern, captured.err, re.IGNORECASE), (
            f"the warning must name the excluded-row count (1); stderr:\n{captured.err}"
        )

    def test_above_threshold_drift_with_both_excluded_and_unverifiable_rows(
        self,
        kc_report_module: ModuleType,
        versioned_guard_repo: tuple[Path, str],
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """2 of 4 rows drifted (1 excluded, 1 unverifiable) = 0.5 drift --
        both counts, and the combined fraction, must be discoverable.
        """
        repo_root, baseline_sha = versioned_guard_repo
        rows = [
            _corpus_row(1, domain=_FLAKY_DOMAIN, matcher_version=baseline_sha),
            _corpus_row(2, matcher_version="not-a-real-revision-at-all"),
            _corpus_row(3, domain="code", matcher_version=baseline_sha),
            _corpus_row(4, domain="code", matcher_version=baseline_sha),
        ]
        gold = [_gold_row(i) for i in range(1, 5)]
        corpus_path = tmp_path / "corpus.jsonl"
        labels_path = tmp_path / "labels.jsonl"
        json_path = tmp_path / "report.json"
        _write_jsonl(corpus_path, rows)
        _write_jsonl(labels_path, gold)

        rc = _run_main(kc_report_module, corpus_path, labels_path, repo_root, json_path)
        captured = capsys.readouterr()

        assert rc == 0
        assert "WARNING" in captured.err
        assert re.search(r"0\.5\b|50(\.0)?%", captured.err), (
            f"the warning must name the 0.5 drift fraction; stderr:\n{captured.err}"
        )
        excluded_pattern = r"exclud\w*\D{0,15}1\b|\b1\D{0,15}exclud\w*"
        assert re.search(excluded_pattern, captured.err, re.IGNORECASE), (
            f"the warning must name the excluded-row count (1); stderr:\n{captured.err}"
        )
        unverifiable_pattern = r"unverif\w*\D{0,15}1\b|\b1\D{0,15}unverif\w*"
        assert re.search(unverifiable_pattern, captured.err, re.IGNORECASE), (
            f"the warning must name the unverifiable-row count (1); stderr:\n{captured.err}"
        )

        data = json.loads(json_path.read_text(encoding="utf-8"))
        assert data["provenance_drift_fraction"] == pytest.approx(0.5)

    def test_warning_printed_before_kc_verdicts(
        self,
        kc_report_module: ModuleType,
        versioned_guard_repo: tuple[Path, str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The WARNING must reach stderr BEFORE the KC verdict report
        reaches stdout. Two independent ``capsys.readouterr()`` calls
        cannot recover cross-stream ordering, so this test replaces
        ``sys.stdout``/``sys.stderr`` with recorders sharing one
        ordered event log and checks relative position directly.
        """
        repo_root, baseline_sha = versioned_guard_repo
        rows = [
            _corpus_row(1, domain=_FLAKY_DOMAIN, matcher_version=baseline_sha),
            _corpus_row(2, domain="code", matcher_version=baseline_sha),
            _corpus_row(3, domain="code", matcher_version=baseline_sha),
            _corpus_row(4, domain="code", matcher_version=baseline_sha),
        ]
        gold = [_gold_row(i) for i in range(1, 5)]
        corpus_path = tmp_path / "corpus.jsonl"
        labels_path = tmp_path / "labels.jsonl"
        _write_jsonl(corpus_path, rows)
        _write_jsonl(labels_path, gold)

        events: list[tuple[str, str]] = []

        class _RecordingStream:
            """A minimal file-like recorder that logs non-blank writes."""

            def __init__(self, label: str) -> None:
                self._label = label

            def write(self, text: str) -> int:
                if text.strip():
                    events.append((self._label, text))
                return len(text)

            def flush(self) -> None:
                """No-op; satisfies the file-like interface."""

        monkeypatch.setattr(sys, "stdout", _RecordingStream("stdout"))
        monkeypatch.setattr(sys, "stderr", _RecordingStream("stderr"))

        rc = _run_main(kc_report_module, corpus_path, labels_path, repo_root)

        assert rc == 0
        warning_index = next(
            (
                i
                for i, (label, text) in enumerate(events)
                if label == "stderr" and "WARNING" in text
            ),
            None,
        )
        kc_index = next(
            (
                i
                for i, (label, text) in enumerate(events)
                if label == "stdout" and "KC-1" in text
            ),
            None,
        )
        assert warning_index is not None, (
            f"no WARNING line was ever written to stderr; events: {events!r}"
        )
        assert kc_index is not None, (
            f"no KC verdict content was ever written to stdout; events: {events!r}"
        )
        assert warning_index < kc_index, (
            "the WARNING must be printed before the KC verdicts; observed "
            f"event order: {[label for label, _ in events]!r}"
        )


# ---------------------------------------------------------------------------
# Manifest citation, provenance-drift-fraction-in-report-body, repo HEAD
# citation, and the explicit go/no-go Gate section (issue #518; plan
# docs/superpowers/plans/2026-07-25-corpus-regeneration-process.md Sec 7
# items 2 and 4; module docstring judgment call 10).
# ---------------------------------------------------------------------------


class TestManifestCitationInReport:
    """``--manifest PATH`` (new, optional CLI flag) points at a corpus-
    manifest JSON file (e.g. ``docs/research/<date>-corpus-manifest.json``,
    produced by ``build_manifest()`` in ``scripts/corpus/builder.py``).
    When provided, the report must cite the sha256 of that manifest
    file's own raw bytes (``hashlib.sha256``). When omitted, the report
    must say manifest citation is unavailable -- never crash, never
    silently drop the section.
    """

    def test_manifest_flag_cites_correct_sha256_of_manifest_bytes(
        self,
        kc_report_module: ModuleType,
        guard_repo: tuple[Path, str],
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """The cited hash must be the manifest file's own sha256 -- the
        fixture's manifest content is deliberately unrelated to the
        expected value's shape (no ``sha256`` key inside it) so a naive
        implementation that echoes some in-file field instead of hashing
        the bytes cannot pass by coincidence.
        """
        repo_root, sha = guard_repo
        corpus_path = tmp_path / "corpus.jsonl"
        labels_path = tmp_path / "labels.jsonl"
        _write_jsonl(corpus_path, [_corpus_row(1, matcher_version=sha)])
        _write_jsonl(labels_path, [_gold_row(1)])
        manifest_path = tmp_path / "2026-07-25-corpus-manifest.json"
        manifest_path.write_text(
            json.dumps({"generated_at": "2026-07-25", "row_count": 1}),
            encoding="utf-8",
        )
        expected_sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()

        rc = _run_main(
            kc_report_module,
            corpus_path,
            labels_path,
            repo_root,
            manifest_path=manifest_path,
        )
        report = capsys.readouterr().out

        assert rc == 0
        assert expected_sha256.lower() in report.lower(), (
            "the report must cite the manifest file's own sha256, not a "
            f"placeholder, a different hash, or a field from inside the "
            f"manifest; expected {expected_sha256!r}; report:\n{report}"
        )

    def test_manifest_flag_omitted_states_citation_unavailable_and_exits_zero(
        self,
        kc_report_module: ModuleType,
        guard_repo: tuple[Path, str],
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        repo_root, sha = guard_repo
        corpus_path = tmp_path / "corpus.jsonl"
        labels_path = tmp_path / "labels.jsonl"
        _write_jsonl(corpus_path, [_corpus_row(1, matcher_version=sha)])
        _write_jsonl(labels_path, [_gold_row(1)])

        rc = _run_main(kc_report_module, corpus_path, labels_path, repo_root)
        report = capsys.readouterr().out

        assert rc == 0, "omitting --manifest must not crash the run"
        manifest_lines = [
            line for line in report.splitlines() if re.search(r"manifest", line, re.IGNORECASE)
        ]
        assert manifest_lines, f"no line mentions 'manifest' in report:\n{report}"
        assert any(
            re.search(
                r"unavailable|not provided|no manifest|not supplied|omitted",
                line,
                re.IGNORECASE,
            )
            for line in manifest_lines
        ), (
            "a line mentioning 'manifest' must also state citation is "
            f"unavailable when --manifest is omitted; manifest-mentioning "
            f"lines: {manifest_lines!r}"
        )


class TestProvenanceDriftFractionInReportBody:
    """The already-computed ``provenance_drift_fraction`` (issue #510)
    must now also appear in the human-readable Markdown report body
    itself (stdout), unconditionally -- not only as a stderr WARNING
    (which only fires over-threshold, per
    ``TestProvenanceDriftWarningMainIntegration``) and not only in the
    optional ``--json`` payload (per ``TestProvenanceDriftFraction``).
    """

    def test_report_body_states_drift_fraction_when_below_threshold(
        self,
        kc_report_module: ModuleType,
        guard_repo: tuple[Path, str],
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """All rows trivially agree (baseline == HEAD): 0.0 drift, well
        below the warning threshold. Before issue #518 this value only
        ever reached a human via the (suppressed, below-threshold)
        stderr WARNING or the optional ``--json`` payload -- it must now
        be discoverable, quantified, in the report body on every run.
        """
        repo_root, sha = guard_repo
        rows = [_corpus_row(i, matcher_version=sha) for i in range(1, 4)]
        gold = [_gold_row(i) for i in range(1, 4)]
        corpus_path = tmp_path / "corpus.jsonl"
        labels_path = tmp_path / "labels.jsonl"
        _write_jsonl(corpus_path, rows)
        _write_jsonl(labels_path, gold)

        rc = _run_main(kc_report_module, corpus_path, labels_path, repo_root)
        report = capsys.readouterr().out

        assert rc == 0
        drift_lines = [
            line for line in report.splitlines() if re.search(r"drift", line, re.IGNORECASE)
        ]
        assert drift_lines, f"no drift-fraction line found in report body:\n{report}"
        assert any(re.search(r"\d", line) for line in drift_lines), (
            "the drift-fraction line(s) must quantify the fraction (e.g. "
            f"'0.0' / '0%'), not just name it; matched lines: {drift_lines!r}"
        )


class TestRepoHeadCitationInReport:
    """The report must cite the git HEAD commit SHA of ``--repo-root`` at
    report-generation time, reusing this file's own ``_run_git``-style
    git subprocess pattern (issue #518, item 3).
    """

    def test_report_body_contains_repo_head_sha(
        self,
        kc_report_module: ModuleType,
        guard_repo: tuple[Path, str],
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        repo_root, sha = guard_repo
        corpus_path = tmp_path / "corpus.jsonl"
        labels_path = tmp_path / "labels.jsonl"
        _write_jsonl(corpus_path, [_corpus_row(1, matcher_version=sha)])
        _write_jsonl(labels_path, [_gold_row(1)])
        expected_head = _run_git(["rev-parse", "HEAD"], cwd=repo_root).stdout.strip()

        rc = _run_main(kc_report_module, corpus_path, labels_path, repo_root)
        report = capsys.readouterr().out

        assert rc == 0
        assert expected_head, "fixture setup problem: could not resolve repo HEAD"
        assert expected_head in report, (
            "the report must cite --repo-root's actual git HEAD SHA (not "
            f"a placeholder); expected {expected_head!r}; report:\n{report}"
        )


class TestGateSection:
    """An explicit "Gate" section states the provisional-verdict rule as
    fixed text: a go/no-go verdict is NOT flip-authorizing if
    ``provenance_drift_fraction >= _DRIFT_WARNING_THRESHOLD`` (reusing
    the existing module constant, not a second hardcoded ``0.25``
    literal) OR if a guarded module changed after the manifest's
    regeneration date (NOT auto-checked by this script -- rendered as an
    explicit reminder for the human operator to verify manually). The
    section must state which case applies for THIS run: whether the
    auto-checkable half of the gate (drift >= threshold) currently
    PASSES or FAILS.
    """

    @staticmethod
    def _gate_section(report: str) -> str:
        """Extract the Gate section, distinct from the unrelated
        "gated-eligible-subset" cut section (``TestWholeVsGatedCuts``):
        the pattern requires ``gate`` on a Markdown heading line itself
        (not merely somewhere in the report), so an earlier, unrelated
        line that happens to mention "gate" cannot be mistaken for the
        section's opening heading. ``\\bgate\\b`` additionally does not
        match inside "gated" because "gate" is immediately followed by
        the word character "d" there, so no word boundary exists at that
        position.
        """
        return _extract_section(report, r"^#{1,6}\s.*\bgate\b")

    @staticmethod
    def _lines_with_only(section: str, keep_word: str, exclude_word: str) -> list[str]:
        """Lines in ``section`` matching ``keep_word`` but not
        ``exclude_word`` (both ``\\b``-bounded, case-insensitive).

        Used to find a per-run verdict line (e.g. "This run: PASS")
        distinct from the gate's two-branch rule-statement line, which
        legitimately names both PASS and FAIL together when it states
        the rule as fixed text.
        """
        keep_re = re.compile(rf"\b{keep_word}\b", re.IGNORECASE)
        exclude_re = re.compile(rf"\b{exclude_word}\b", re.IGNORECASE)
        return [
            line
            for line in section.splitlines()
            if keep_re.search(line) and not exclude_re.search(line)
        ]

    def test_gate_section_states_the_drift_threshold_rule_using_the_module_constant(
        self,
        kc_report_module: ModuleType,
        guard_repo: tuple[Path, str],
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        repo_root, sha = guard_repo
        corpus_path = tmp_path / "corpus.jsonl"
        labels_path = tmp_path / "labels.jsonl"
        _write_jsonl(corpus_path, [_corpus_row(1, matcher_version=sha)])
        _write_jsonl(labels_path, [_gold_row(1)])
        threshold = kc_report_module._DRIFT_WARNING_THRESHOLD
        candidates = [
            str(threshold),
            f"{threshold:.2f}",
            f"{threshold * 100:.0f}%",
            f"{threshold * 100:.1f}%",
        ]

        rc = _run_main(kc_report_module, corpus_path, labels_path, repo_root)
        report = capsys.readouterr().out
        gate_section = self._gate_section(report)

        assert rc == 0
        assert re.search(r"drift", gate_section, re.IGNORECASE)
        assert any(c in gate_section for c in candidates), (
            "the Gate section must state the drift threshold using the "
            f"module constant's actual value ({threshold!r}), not a "
            f"disconnected literal; gate section:\n{gate_section}"
        )
        assert re.search(r"flip", gate_section, re.IGNORECASE), (
            "the Gate section must state the spec's flip-authorizing "
            f"framing; gate section:\n{gate_section}"
        )

    def test_gate_section_states_the_manual_guarded_module_check_as_a_reminder(
        self,
        kc_report_module: ModuleType,
        guard_repo: tuple[Path, str],
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """The second gate condition -- a guarded module changed after
        the manifest's regeneration date -- is NOT auto-checked by this
        script; the Gate section must render it as an explicit reminder
        for the human operator to verify manually, not silently omit it.
        """
        repo_root, sha = guard_repo
        corpus_path = tmp_path / "corpus.jsonl"
        labels_path = tmp_path / "labels.jsonl"
        _write_jsonl(corpus_path, [_corpus_row(1, matcher_version=sha)])
        _write_jsonl(labels_path, [_gold_row(1)])

        rc = _run_main(kc_report_module, corpus_path, labels_path, repo_root)
        report = capsys.readouterr().out
        gate_section = self._gate_section(report)

        assert rc == 0
        assert re.search(r"manifest", gate_section, re.IGNORECASE), (
            "the Gate section must reference the manifest's regeneration "
            f"date; gate section:\n{gate_section}"
        )
        assert re.search(
            r"guard(ed)?\s+module|module", gate_section, re.IGNORECASE
        ), (
            "the Gate section must reference a guarded module changing "
            f"after the manifest's regeneration date; gate section:\n{gate_section}"
        )
        assert re.search(
            r"manual(ly)?|verify|operator|reminder|by hand",
            gate_section,
            re.IGNORECASE,
        ), (
            "the Gate section must present the guarded-module check as a "
            f"manual reminder for the human operator, since this script "
            f"does not auto-check it; gate section:\n{gate_section}"
        )

    def test_gate_states_auto_checkable_half_passes_when_drift_below_threshold(
        self,
        kc_report_module: ModuleType,
        guard_repo: tuple[Path, str],
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """All rows trivially agree (0.0 drift) -- well below the
        threshold -- so the auto-checkable half of the gate must state
        PASS for this run.
        """
        repo_root, sha = guard_repo
        rows = [_corpus_row(i, matcher_version=sha) for i in range(1, 4)]
        gold = [_gold_row(i) for i in range(1, 4)]
        corpus_path = tmp_path / "corpus.jsonl"
        labels_path = tmp_path / "labels.jsonl"
        _write_jsonl(corpus_path, rows)
        _write_jsonl(labels_path, gold)

        rc = _run_main(kc_report_module, corpus_path, labels_path, repo_root)
        report = capsys.readouterr().out
        gate_section = self._gate_section(report)

        assert rc == 0
        # A line naming PASS but not FAIL -- distinct from the gate's
        # two-branch rule-statement line, which legitimately names both
        # PASS and FAIL together when stating the rule as fixed text
        # (see test_gate_section_states_the_drift_threshold_rule_...).
        verdict_lines = self._lines_with_only(gate_section, "PASS(ES)?", "FAIL(S)?")
        assert verdict_lines, (
            "the Gate section must carry a line stating the auto-checkable "
            "half PASSES for THIS run (distinct from the two-branch rule "
            f"text, which may name both words together); gate section:\n"
            f"{gate_section}"
        )

    def test_gate_states_auto_checkable_half_fails_when_drift_at_or_above_threshold(
        self,
        kc_report_module: ModuleType,
        versioned_guard_repo: tuple[Path, str],
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """1 excluded row of 4 total = 0.25 drift, exactly at the
        inclusive ">=" threshold (mirrors
        ``TestProvenanceDriftWarningMainIntegration``'s at-threshold
        fixture) -- the auto-checkable half of the gate must state FAIL
        for this run.
        """
        repo_root, baseline_sha = versioned_guard_repo
        rows = [
            _corpus_row(1, domain=_FLAKY_DOMAIN, matcher_version=baseline_sha),
            _corpus_row(2, domain="code", matcher_version=baseline_sha),
            _corpus_row(3, domain="code", matcher_version=baseline_sha),
            _corpus_row(4, domain="code", matcher_version=baseline_sha),
        ]
        gold = [_gold_row(i) for i in range(1, 5)]
        corpus_path = tmp_path / "corpus.jsonl"
        labels_path = tmp_path / "labels.jsonl"
        _write_jsonl(corpus_path, rows)
        _write_jsonl(labels_path, gold)

        rc = _run_main(kc_report_module, corpus_path, labels_path, repo_root)
        report = capsys.readouterr().out
        gate_section = self._gate_section(report)

        assert rc == 0
        # A line naming FAIL but not PASS -- distinct from the gate's
        # two-branch rule-statement line, which legitimately names both
        # PASS and FAIL together when stating the rule as fixed text.
        verdict_lines = self._lines_with_only(gate_section, "FAIL(S)?", "PASS(ES)?")
        assert verdict_lines, (
            "the Gate section must carry a line stating the auto-checkable "
            "half FAILS for THIS run (distinct from the two-branch rule "
            f"text, which may name both words together); gate section:\n"
            f"{gate_section}"
        )


# ---------------------------------------------------------------------------
# Gate encoded in --json (issue #532, module docstring judgment call 11).
# TestGateSection (above) only pins the gate's rule/verdict in the
# human-readable Markdown report body; this class pins the same signal
# machine-readably in the --json payload.
# ---------------------------------------------------------------------------


class TestGateEncodedInJson:
    """The ``--json`` payload must independently encode the
    provenance-drift gate: the threshold value, a PASS/FAIL gate
    status, and a ``flip_authorized`` boolean -- distinct from
    ``overall_recommendation``/``criteria``, which reflect the KC
    verdicts only. This closes the reported bug: a NO-GO-worthy run's
    JSON can look "clean" today because the gate is only ever rendered
    as prose (the Gate section) or a bare fraction
    (``provenance_drift_fraction``), never as an explicit,
    independently-checkable pass/fail + authorization boolean a caller
    can branch on without parsing Markdown.
    """

    def test_drift_below_threshold_gate_passes_and_authorizes_flip(
        self,
        kc_report_module: ModuleType,
        guard_repo: tuple[Path, str],
        tmp_path: Path,
    ) -> None:
        repo_root, sha = guard_repo
        corpus_path, labels_path = _build_all_kc_pass_fixture(tmp_path, sha)
        json_path = tmp_path / "report.json"

        rc = _run_main(kc_report_module, corpus_path, labels_path, repo_root, json_path)
        assert rc == 0

        data = json.loads(json_path.read_text(encoding="utf-8"))
        assert data["provenance_drift_fraction"] == pytest.approx(0.0)

        assert "gate_threshold" in data, f"missing gate_threshold key; data: {data!r}"
        assert data["gate_threshold"] == pytest.approx(
            kc_report_module._DRIFT_WARNING_THRESHOLD
        ), (
            "gate_threshold must mirror the module's own "
            f"_DRIFT_WARNING_THRESHOLD constant, not a disconnected "
            f"literal; data: {data!r}"
        )

        assert "gate_status" in data, f"missing gate_status key; data: {data!r}"
        assert data["gate_status"] == "PASS", (
            "drift 0.0 is below the threshold; gate_status must be PASS; "
            f"data: {data!r}"
        )

        assert "flip_authorized" in data, f"missing flip_authorized key; data: {data!r}"
        assert data["flip_authorized"] is True, (
            "drift below threshold must authorize a flip decision; "
            f"data: {data!r}"
        )

    def test_drift_above_threshold_gate_fails_and_blocks_flip_even_when_kc_all_pass(
        self,
        kc_report_module: ModuleType,
        guard_repo: tuple[Path, str],
        tmp_path: Path,
    ) -> None:
        """The actual bug (#532): KC-1, KC-2, and KC-3 all independently
        PASS on the ``included`` subset (see
        ``_build_all_kc_pass_fixture``'s docstring-pinned arithmetic),
        yet 3 of 10 rows are unverifiable (drift 0.3, above the 0.25
        threshold). The gate must still report FAIL / flip_authorized
        False -- before this field existed, a consumer reading only
        ``criteria``/``overall_recommendation`` could see an all-PASS
        report and conclude go, missing that the evidence itself is
        untrustworthy.
        """
        repo_root, sha = guard_repo
        corpus_path, labels_path = _build_all_kc_pass_fixture(
            tmp_path, sha, unverifiable_ids=frozenset({6, 9, 10})
        )
        json_path = tmp_path / "report.json"

        rc = _run_main(kc_report_module, corpus_path, labels_path, repo_root, json_path)
        assert rc == 0, "an above-threshold-drift run still completes (exit 0)"

        data = json.loads(json_path.read_text(encoding="utf-8"))
        assert data["provenance_drift_fraction"] == pytest.approx(0.3)

        by_kc = {c["kc"]: c["status"] for c in data["criteria"]}
        assert by_kc.get("KC-1") == "PASS", by_kc
        assert by_kc.get("KC-2") == "PASS", by_kc
        assert by_kc.get("KC-3") == "PASS", by_kc

        assert "gate_status" in data, f"missing gate_status key; data: {data!r}"
        assert data["gate_status"] == "FAIL", (
            "drift 0.3 is above the 0.25 threshold; gate_status must be "
            f"FAIL even though every computed KC criterion PASSED; "
            f"data: {data!r}, criteria: {by_kc!r}"
        )

        assert "flip_authorized" in data, f"missing flip_authorized key; data: {data!r}"
        assert data["flip_authorized"] is False, (
            "drift above threshold must NOT authorize a flip, "
            f"regardless of individual KC verdicts; data: {data!r}, "
            f"criteria: {by_kc!r}"
        )

    def test_drift_exactly_at_threshold_boundary_gate_fails_and_blocks_flip(
        self,
        kc_report_module: ModuleType,
        guard_repo: tuple[Path, str],
        tmp_path: Path,
    ) -> None:
        """The inclusive ``>=`` boundary at exactly 0.25 must be pinned
        on the JSON side too, matching the ``>=`` semantics already
        pinned elsewhere in this suite for the same constant
        (``TestProvenanceDriftFraction.test_exactly_at_threshold_counts_as_drifted``,
        ``TestProvenanceDriftWarningMainIntegration.test_at_threshold_drift_prints_warning...``,
        and the report-side
        ``TestGateSection.test_gate_states_auto_checkable_half_fails_when_drift_at_or_above_threshold``).
        An implementation using a strict ``>`` comparison for the JSON
        gate fields would silently disagree with those ``>=``-based
        checks at exactly this boundary; this test exists specifically
        to catch that divergence. 1 of 4 rows unverifiable == 0.25
        drift, exactly at the threshold -- does not assert on
        individual KC verdicts, since the boundary itself is the point.
        """
        repo_root, sha = guard_repo
        rows = [
            _corpus_row(1, matcher_version="not-a-real-revision-1"),
            _corpus_row(2, matcher_version=sha),
            _corpus_row(3, matcher_version=sha),
            _corpus_row(4, matcher_version=sha),
        ]
        gold = [_gold_row(i) for i in range(1, 5)]
        corpus_path = tmp_path / "corpus.jsonl"
        labels_path = tmp_path / "labels.jsonl"
        json_path = tmp_path / "report.json"
        _write_jsonl(corpus_path, rows)
        _write_jsonl(labels_path, gold)

        rc = _run_main(kc_report_module, corpus_path, labels_path, repo_root, json_path)
        assert rc == 0, "an at-threshold-drift run still completes (exit 0)"

        data = json.loads(json_path.read_text(encoding="utf-8"))
        assert data["provenance_drift_fraction"] == pytest.approx(0.25)

        assert "gate_status" in data, f"missing gate_status key; data: {data!r}"
        assert data["gate_status"] == "FAIL", (
            "drift exactly at the 0.25 threshold must count as FAIL (the "
            f"boundary is inclusive, '>=' not '>'); data: {data!r}"
        )

        assert "flip_authorized" in data, f"missing flip_authorized key; data: {data!r}"
        assert data["flip_authorized"] is False, (
            "drift exactly at the threshold must NOT authorize a flip; "
            f"data: {data!r}"
        )


# ---------------------------------------------------------------------------
# Corpus-hash integrity check (issue #532, module docstring judgment
# call 12).
# ---------------------------------------------------------------------------


class TestCorpusHashIntegrityCheck:
    """``--manifest``, when it records a ``"sha256"`` key (the corpus
    artifact's own hash per ``build_manifest`` -- confirmed schema in
    ``tests/test_corpus/test_builder.py::test_manifest_sha256_matches_artifact``),
    must be validated against the ACTUAL bytes of the file supplied via
    ``--corpus``. This is distinct from the existing manifest-file
    citation hash (``TestManifestCitationInReport``), which hashes the
    manifest file itself for citation and never touches this
    ``"sha256"`` field recorded inside the manifest.
    """

    def test_matching_corpus_sha256_proceeds_normally(
        self,
        kc_report_module: ModuleType,
        guard_repo: tuple[Path, str],
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        repo_root, sha = guard_repo
        corpus_path = tmp_path / "corpus.jsonl"
        labels_path = tmp_path / "labels.jsonl"
        _write_jsonl(corpus_path, [_corpus_row(1, matcher_version=sha)])
        _write_jsonl(labels_path, [_gold_row(1)])
        corpus_sha256 = hashlib.sha256(corpus_path.read_bytes()).hexdigest()
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(
            json.dumps({"sha256": corpus_sha256, "row_count": 1}),
            encoding="utf-8",
        )

        rc = _run_main(
            kc_report_module,
            corpus_path,
            labels_path,
            repo_root,
            manifest_path=manifest_path,
        )
        report = capsys.readouterr().out

        assert rc == 0, (
            "a manifest sha256 that matches the actual corpus bytes must "
            "proceed normally"
        )
        assert "KC-1" in report, "KC computation must still run and be reported"

    def test_mismatched_corpus_sha256_fails_loudly_and_skips_kc_computation(
        self,
        kc_report_module: ModuleType,
        guard_repo: tuple[Path, str],
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """The actual bug (#532): today the manifest's recorded corpus
        sha256 is never compared against ``--corpus``'s real bytes, so a
        tampered, stale, or simply wrong corpus file silently produces a
        normal-looking report. A wrong recorded hash must make the
        generator fail loudly (non-zero exit, no KC verdicts printed)
        rather than silently computing KC results against unverified
        data.
        """
        repo_root, sha = guard_repo
        corpus_path = tmp_path / "corpus.jsonl"
        labels_path = tmp_path / "labels.jsonl"
        _write_jsonl(corpus_path, [_corpus_row(1, matcher_version=sha)])
        _write_jsonl(labels_path, [_gold_row(1)])
        wrong_sha256 = "0" * 64
        actual_sha256 = hashlib.sha256(corpus_path.read_bytes()).hexdigest()
        assert wrong_sha256 != actual_sha256, "fixture sanity: hashes must genuinely differ"
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(
            json.dumps({"sha256": wrong_sha256, "row_count": 1}),
            encoding="utf-8",
        )

        rc = _run_main(
            kc_report_module,
            corpus_path,
            labels_path,
            repo_root,
            manifest_path=manifest_path,
        )
        captured = capsys.readouterr()

        assert rc != 0, (
            "a manifest-recorded corpus sha256 that does not match the "
            f"actual --corpus file bytes must fail loudly; stdout:\n"
            f"{captured.out}\nstderr:\n{captured.err}"
        )
        assert re.search(r"sha256|hash|checksum|integrity", captured.err, re.IGNORECASE), (
            f"the failure must name the hash/integrity problem; stderr:\n{captured.err}"
        )
        assert re.search(r"corpus", captured.err, re.IGNORECASE), (
            f"the failure must reference the corpus file; stderr:\n{captured.err}"
        )
        assert "KC-1" not in captured.out, (
            "KC verdicts must not be computed/printed when the corpus "
            f"hash integrity check fails; stdout:\n{captured.out}"
        )
        assert "Traceback (most recent call last)" not in captured.err, (
            "must fail cleanly (a handled error), not with an unhandled "
            f"traceback; stderr:\n{captured.err}"
        )

    def test_malformed_manifest_warns_and_proceeds_without_citation(
        self,
        kc_report_module: ModuleType,
        guard_repo: tuple[Path, str],
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Verify a malformed explicit manifest warns without aborting.

        Args:
            kc_report_module: Loaded shadow KC report script module.
            guard_repo: Disposable repository and its current commit SHA.
            tmp_path: Pytest temporary directory for fixture files.
            capsys: Pytest fixture capturing stdout and stderr.
        """
        repo_root, sha = guard_repo
        corpus_path = tmp_path / "corpus.jsonl"
        labels_path = tmp_path / "labels.jsonl"
        manifest_path = tmp_path / "manifest.json"
        _write_jsonl(corpus_path, [_corpus_row(1, matcher_version=sha)])
        _write_jsonl(labels_path, [_gold_row(1)])
        manifest_path.write_text("{not valid json", encoding="utf-8")

        rc = _run_main(
            kc_report_module,
            corpus_path,
            labels_path,
            repo_root,
            manifest_path=manifest_path,
        )
        captured = capsys.readouterr()

        assert rc == 0, (
            "invalid manifest JSON must disable manifest validation "
            f"without aborting the report; stderr:\n{captured.err}"
        )
        assert re.search(
            r"warning.*manifest.*(?:json|pars)",
            captured.err,
            re.IGNORECASE,
        ), (
            "an explicit malformed manifest must emit a parse warning; "
            f"stderr:\n{captured.err}"
        )
        assert (
            "Manifest citation unavailable: --manifest not provided or unreadable."
            in captured.out
        )
        assert "KC-1" in captured.out, (
            "KC computation must proceed when manifest JSON is malformed"
        )

    def test_non_object_manifest_warns_and_proceeds_with_citation(
        self,
        kc_report_module: ModuleType,
        guard_repo: tuple[Path, str],
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Verify a non-object manifest warns without aborting.

        Args:
            kc_report_module: Loaded shadow KC report script module.
            guard_repo: Disposable repository and its current commit SHA.
            tmp_path: Pytest temporary directory for fixture files.
            capsys: Pytest fixture capturing stdout and stderr.
        """
        repo_root, sha = guard_repo
        corpus_path = tmp_path / "corpus.jsonl"
        labels_path = tmp_path / "labels.jsonl"
        manifest_path = tmp_path / "manifest.json"
        _write_jsonl(corpus_path, [_corpus_row(1, matcher_version=sha)])
        _write_jsonl(labels_path, [_gold_row(1)])
        manifest_path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")

        rc = _run_main(
            kc_report_module,
            corpus_path,
            labels_path,
            repo_root,
            manifest_path=manifest_path,
        )
        captured = capsys.readouterr()

        assert rc == 0, (
            "a non-object manifest must disable manifest validation "
            f"without aborting the report; stderr:\n{captured.err}"
        )
        assert re.search(
            rf"warning.*manifest.*{re.escape(str(manifest_path))}.*"
            r"(?:did not parse to|is not) a json object",
            captured.err,
            re.IGNORECASE,
        ), (
            "an explicit non-object manifest must emit a warning naming "
            f"the manifest path; stderr:\n{captured.err}"
        )
        assert "Manifest SHA-256:" in captured.out
        assert "KC-1" in captured.out, (
            "KC computation must proceed when manifest JSON is not an object"
        )
