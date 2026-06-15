"""Deterministic posture-evidence extractors E1–E12 for the posture library.

Each function takes a ``PostureContext`` (and any required pre-computed
parameters) and returns an ``ExtractorResult`` with the uniform output
contract from spec §10.3.

Design constraints:
- Pure and deterministic: no LLM, no network, no filesystem access.
  Same input always produces the same output.
- E7 (area_span) takes a pre-loaded ``area_map`` parameter so the
  extractor function itself never reads any file.
- E6 (cause_stated) takes ``host_condition: bool`` (True when E1 or E2
  fired).  It never evaluates unless the host extractor fired (§10.3
  guardrail 3).
- E9 (artifact_absence) takes the list of artifact-extractor results and
  the E12 result; it is suppressed by E12 firing (§12.3 R2).
- E10 (frame_markers) takes ``e9_gate_open: bool``; it only evaluates
  inside the E9 gate (§10.2, §10.3 guardrail 3).
- All Tier-C marker sets are imported from ``_markers.py``; they are
  never extended or modified here.

Spec references: §10.1 (tier model), §10.2 (extractor definitions),
§10.3 (output contract + guardrails), §12.3 (R1–R3 refinements).

Posture vocabulary (§9.3): build · diagnose · assess · verify · plan ·
research · idea-critique · operate.
"""

from __future__ import annotations

import fnmatch
import re
from typing import Sequence

from claude_wayfinder.posture._markers import (
    CAUSAL_CONNECTIVES,
    FRAME_MARKERS_CHALLENGE,
    FRAME_MARKERS_PRIOR_ART,
    FRAME_MARKERS_SCOPE,
    NAMED_DOC_NOUNS,
    PROSE_FAILURE_TERMS,
    RELATIONAL_MARKERS,
)
from claude_wayfinder.posture._types import ExtractorResult, PostureContext

# ---------------------------------------------------------------------------
# Re-export ExtractorResult so tests can import it from this module
# without a separate _types import.
# ---------------------------------------------------------------------------

__all__ = [
    "ExtractorResult",
    "extract_agent_mentions",
    "extract_area_span",
    "extract_artifact_absence",
    "extract_cause_stated",
    "extract_command_prefix",
    "extract_frame_markers",
    "extract_prose_failure_mention",
    "extract_source_of_truth_pair",
    "extract_spec_plan_path",
    "extract_stacktrace_block",
    "extract_test_failure_output",
    "extract_vcs_artifact_ref",
]

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# Compiled regexes for Tier-B shape-matching (E1, E2, E3).
# These match machine-emitted artifact shapes, not free prose.

_RE_TRACEBACK = re.compile(
    r"Traceback \(most recent call last\)", re.IGNORECASE
)
_RE_AT_FRAME = re.compile(
    r"^\s+at \S+\(.*:\d+(:\d+)?\)", re.MULTILINE
)
_RE_EXCEPTION_LINE = re.compile(
    r"^[\w.]*\b(?:Error|Exception)\b[:(]", re.MULTILINE
)
_RE_ERROR_COLON = re.compile(
    r"\bError:\s+\S", re.IGNORECASE
)
_RE_COMPILER_DIAG = re.compile(
    r":\d+:\d+:\s+(?:error|warning):", re.IGNORECASE
)
_RE_EXIT_CODE = re.compile(
    r"\bexit(?:ed)?\s+(?:with\s+)?(?:code|status)\s+\d+", re.IGNORECASE
)
_RE_PANIC = re.compile(r"\bpanic:\s+", re.IGNORECASE)

_RE_PYTEST_FAILED = re.compile(
    r"\bFAILED\s+\S+::\w+", re.MULTILINE
)
_RE_RUNNER_SUMMARY = re.compile(
    r"\d+\s+(?:failed|errors?)\b", re.IGNORECASE
)
_RE_ASSERTION_ERROR = re.compile(r"\bAssertionError\b")

_RE_PR_HASH = re.compile(r"\bPR\s*#\d+", re.IGNORECASE)
_RE_PULL_URL = re.compile(r"/pull/\d+")
_RE_DIFF_HUNK = re.compile(r"^@@\s+-\d+", re.MULTILINE)
_RE_DIFF_GIT = re.compile(r"^diff --git\s", re.MULTILINE)
# SHA: ≥7 hex chars with at least one letter AND one digit
_RE_SHA_TOKEN = re.compile(r"\b([0-9a-f]{7,})\b", re.IGNORECASE)

_RE_CAUSE_HEADING = re.compile(
    r"(?:root\s+)?cause\s*:", re.IGNORECASE
)

# Punctuation that delimits clauses for R3 clause-scoped proximity.
# A clause boundary is: ., ;, :, —, –, --, (, ), [, ], |, \n
_CLAUSE_SPLIT_RE = re.compile(r"[.;:—–]|\s--\s|\n")

# Spec/plan path globs (E4).
_SPEC_PLAN_GLOBS: tuple[str, ...] = (
    "docs/**/*spec*.md",
    "docs/**/*plan*.md",
    "docs/superpowers/specs/**",
    "docs/superpowers/plans/**",
    "**/adr*/**",
    "**/adr*.md",
    "docs/adr/**",
)

# VCS tool mentions that trigger E3 (Tier A path).
_VCS_TOOL_PREFIXES = frozenset(
    {
        "get_pull_request",
        "create_pull_request",
        "list_pull_requests",
        "merge_pull_request",
        "update_pull_request",
    }
)


def _has_mixed(token: str) -> bool:
    """Return True when *token* contains both a letter and a digit."""
    return any(c.isdigit() for c in token) and any(c.isalpha() for c in token)


def _split_clauses(text: str) -> list[str]:
    """Split *text* into punctuation-delimited clauses (R3).

    Returns a list of clause strings; each clause is stripped of leading
    and trailing whitespace.
    """
    return [c.strip() for c in _CLAUSE_SPLIT_RE.split(text) if c.strip()]


def _path_matches_any_glob(path: str, globs: tuple[str, ...]) -> bool:
    """Return True when *path* matches any glob in *globs* (fnmatch)."""
    norm = path.replace("\\", "/")
    for glob in globs:
        if fnmatch.fnmatch(norm, glob):
            return True
    return False


def _extract_path_tokens(text: str) -> list[str]:
    """Extract path-shaped tokens from prose text.

    A path-shaped token has at least one '/' and ends with a file
    extension or a trailing '/'.  This is used by E4 to detect spec/plan
    references embedded in prose.
    """
    # Match tokens that look like paths: non-space sequences containing /
    # and optionally a file extension.
    pattern = re.compile(r"\S+/\S*(?:\.\w+)?")
    return pattern.findall(text)


# ---------------------------------------------------------------------------
# E1 — stacktrace_block (Tier B)
# ---------------------------------------------------------------------------


def extract_stacktrace_block(ctx: PostureContext) -> ExtractorResult:
    """E1: Detect machine-emitted stack traces and error patterns.

    Tier B — reads ``task_description`` but only matches machine-emitted
    / syntax-constrained shapes.

    Posture evidence: diagnose (strong).

    Args:
        ctx: The dispatch context.

    Returns:
        ExtractorResult with tier="B" and diagnose evidence when fired.
    """
    text = ctx.task_description

    fired = (
        bool(_RE_TRACEBACK.search(text))
        or bool(_RE_AT_FRAME.search(text))
        or bool(_RE_EXCEPTION_LINE.search(text))
        or bool(_RE_ERROR_COLON.search(text))
        or bool(_RE_COMPILER_DIAG.search(text))
        or bool(_RE_EXIT_CODE.search(text))
        or bool(_RE_PANIC.search(text))
    )

    if not fired:
        return ExtractorResult(fired=False, tier="B", evidence=[])
    return ExtractorResult(
        fired=True, tier="B", evidence=[("diagnose", "strong")]
    )


# ---------------------------------------------------------------------------
# E2 — test_failure_output (Tier B)
# ---------------------------------------------------------------------------


def extract_test_failure_output(ctx: PostureContext) -> ExtractorResult:
    """E2: Detect test runner failure output (pytest, jest, etc.).

    Tier B — matches machine-emitted runner summary shapes.

    Posture evidence: diagnose (strong).

    Args:
        ctx: The dispatch context.

    Returns:
        ExtractorResult with tier="B" and diagnose evidence when fired.
    """
    text = ctx.task_description

    fired = (
        bool(_RE_PYTEST_FAILED.search(text))
        or bool(_RE_RUNNER_SUMMARY.search(text))
        or bool(_RE_ASSERTION_ERROR.search(text))
    )

    if not fired:
        return ExtractorResult(fired=False, tier="B", evidence=[])
    return ExtractorResult(
        fired=True, tier="B", evidence=[("diagnose", "strong")]
    )


# ---------------------------------------------------------------------------
# E3 — vcs_artifact_ref (Tier B + A)
# ---------------------------------------------------------------------------


def extract_vcs_artifact_ref(ctx: PostureContext) -> ExtractorResult:
    """E3: Detect VCS artifact references (PR URL, diff hunk, SHA token).

    Tier B (text shape) plus Tier A (tool_mentions).  When a VCS tool
    mention is present the tier is "A"; otherwise "B".

    Posture evidence: assess (strong).

    Args:
        ctx: The dispatch context.

    Returns:
        ExtractorResult with assess evidence when fired.
    """
    text = ctx.task_description

    # Tier A path: VCS tool mention
    tool_fired = bool(
        ctx.tool_mentions
        and any(
            t.startswith(tuple(
                p for p in _VCS_TOOL_PREFIXES
            ))
            for t in ctx.tool_mentions
        )
    )
    if tool_fired:
        return ExtractorResult(
            fired=True, tier="A", evidence=[("assess", "strong")]
        )

    # Tier B path: text-shape patterns
    pr_fired = bool(
        _RE_PR_HASH.search(text) or _RE_PULL_URL.search(text)
    )
    diff_fired = bool(
        _RE_DIFF_HUNK.search(text) or _RE_DIFF_GIT.search(text)
    )
    sha_fired = any(
        len(m.group(1)) >= 7 and _has_mixed(m.group(1))
        for m in _RE_SHA_TOKEN.finditer(text)
    )

    if pr_fired or diff_fired or sha_fired:
        return ExtractorResult(
            fired=True, tier="B", evidence=[("assess", "strong")]
        )

    return ExtractorResult(fired=False, tier="B", evidence=[])


# ---------------------------------------------------------------------------
# E4 — spec_plan_path (Tier A + B)
# ---------------------------------------------------------------------------


def extract_spec_plan_path(ctx: PostureContext) -> ExtractorResult:
    """E4: Detect spec/plan document paths in file_paths or prose tokens.

    Tier A when a matching path is in ``file_paths``; Tier B when a
    path-shaped prose token matches a spec/plan glob.

    Posture evidence: build / plan-execution (strong).

    Args:
        ctx: The dispatch context.

    Returns:
        ExtractorResult with build evidence when fired.
    """
    # Tier A: structured file_paths
    for path in ctx.file_paths:
        if _path_matches_any_glob(path, _SPEC_PLAN_GLOBS):
            return ExtractorResult(
                fired=True, tier="A", evidence=[("build", "strong")]
            )

    # Tier B: path-shaped tokens in prose
    prose_tokens = _extract_path_tokens(ctx.task_description)
    for token in prose_tokens:
        if _path_matches_any_glob(token, _SPEC_PLAN_GLOBS):
            return ExtractorResult(
                fired=True, tier="B", evidence=[("build", "strong")]
            )

    return ExtractorResult(fired=False, tier="A", evidence=[])


# ---------------------------------------------------------------------------
# E5 — source_of_truth_pair (Tier B core + C assist)
# ---------------------------------------------------------------------------


def extract_source_of_truth_pair(ctx: PostureContext) -> ExtractorResult:
    """E5: Detect conformance-check framing (≥2 artifact refs + C assist).

    Requires BOTH B core AND C assist (per §12.2 F4 + §12.3 R1):

    B core (necessary): ≥2 distinct artifact references (file_paths count
    plus URL/path tokens in prose, deduplicated).  B core alone over-fires
    on any multi-file prompt (e.g. "refactor a.py and b.py") so it is
    not sufficient on its own (F4).

    C assist (required to activate): relational markers from
    RELATIONAL_MARKERS OR named-doc nouns {release notes, changelog,
    schema, contract, invariant} in the text.  Per R1, C *selects* within
    the B-core-activated candidate set — it never adds a posture by
    itself, but here it completes the activation condition.

    Posture evidence: verify (strong).

    Args:
        ctx: The dispatch context.

    Returns:
        ExtractorResult with verify evidence when ≥2 artifact refs AND
        a C relational/doc marker are found together.
    """
    text = ctx.task_description
    text_lower = text.lower()

    # C assist check first (cheaper than path counting).
    # Named-doc nouns that imply conformance framing (module-level constant).

    # Relational marker matching: per §10.3, stemming applies within the
    # frozen set.  Check for each marker and its common stem variants
    # (strip trailing 's' for single-word markers to match "match" vs
    # "matches", "drifted" vs "drifts", etc.).  Multi-word markers (e.g.
    # "conforms to") are checked as substrings directly.
    def _relational_hits(text_l: str) -> bool:
        """Return True if any relational marker (or its stem) is present."""
        for marker in RELATIONAL_MARKERS:
            if marker in text_l:
                return True
            # Simple stem: strip trailing 's' or 'ed' for single-word markers
            if " " not in marker:
                stem = marker.rstrip("s").rstrip("ed") if len(marker) > 3 else marker
                if stem and len(stem) > 2 and stem in text_l:
                    return True
        return False

    relational_match = _relational_hits(text_lower)
    doc_noun_match = any(noun in text_lower for noun in NAMED_DOC_NOUNS)
    c_assist = relational_match or doc_noun_match

    if not c_assist:
        return ExtractorResult(fired=False, tier="B", evidence=[])

    # Count distinct artifact references.
    # Tier A artifact refs: file_paths entries (strip backtick quoting).
    def _norm(p: str) -> str:
        return p.strip("`").replace("\\", "/")

    file_paths_norm = {_norm(p) for p in ctx.file_paths}
    artifact_count = len(file_paths_norm)

    # Tier B artifact refs: path-shaped tokens in prose not already in
    # file_paths, to avoid double-counting.
    prose_tokens = _extract_path_tokens(text)
    unique_prose_paths = [
        t for t in prose_tokens if _norm(t) not in file_paths_norm
    ]
    artifact_count += len(unique_prose_paths)

    if artifact_count < 2:
        return ExtractorResult(fired=False, tier="B", evidence=[])

    return ExtractorResult(
        fired=True, tier="B", evidence=[("verify", "strong")]
    )


# ---------------------------------------------------------------------------
# E6 — cause_stated (Tier C modifier)
# ---------------------------------------------------------------------------


def extract_cause_stated(
    ctx: PostureContext,
    *,
    host_condition: bool,
) -> ExtractorResult:
    """E6: Detect stated causal explanation that flips diagnose → build.

    Tier C (modifier) — conditional: only evaluates when ``host_condition``
    is True (i.e. E1 or E2 fired).  When disabled, always abstains.

    R3 (§12.3): causal connective and failure mention must share a
    punctuation-delimited clause.  Token-window matching is disallowed
    (P12 hazard: misattached rationale-"because").

    Clause-scope implementation (R3):
    - ``after``: checked in the same sentence AND the immediately following
      sentence (temporal connectives typically appear in the sentence that
      explains what happened after the failure).
    - Other connectives (``because``, ``due to``, ``caused by``, ``since``,
      ``introduced by``): same-sentence only.  In P12 "because" explains
      the DNS-change motivation in a separate sentence — stricter scope
      correctly keeps it silent.

    B-variant: fires immediately on "root cause:" heading pattern
    (machine-structured, not free prose).

    Posture evidence: emits a modifier that flips diagnose → build
    ("modifier" weight class signals this role to consumers).

    Args:
        ctx: The dispatch context.
        host_condition: True when E1 (stacktrace_block) or E2
            (test_failure_output) fired.

    Returns:
        ExtractorResult.  When fired=False (host absent, or no connective
        in correct clause scope), evidence is empty.
    """
    if not host_condition:
        return ExtractorResult(fired=False, tier="C", evidence=[])

    text = ctx.task_description

    # B-variant: explicit "root cause:" or "cause:" heading
    if _RE_CAUSE_HEADING.search(text):
        return ExtractorResult(
            fired=True,
            tier="C",
            evidence=[("build", "modifier")],
        )

    # Split on sentence boundaries for clause-scoped proximity.
    # Use ". " followed by a capital letter as a sentence separator so
    # internal periods (e.g., in file paths like "test_api.py") don't
    # fragment sentences incorrectly.
    _SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")
    sentences = _SENTENCE_SPLIT_RE.split(text)
    if len(sentences) <= 1:
        sentences = _split_clauses(text)

    failure_patterns = [
        _RE_TRACEBACK,
        _RE_EXCEPTION_LINE,
        _RE_ERROR_COLON,
        _RE_COMPILER_DIAG,
        _RE_EXIT_CODE,
        _RE_PANIC,
        _RE_PYTEST_FAILED,
        _RE_RUNNER_SUMMARY,
        _RE_ASSERTION_ERROR,
    ]

    # Connectives that use same-sentence scope only
    _STRICT_CONNECTIVES = frozenset(
        {"because", "due to", "caused by", "since", "introduced by"}
    )
    # Connectives that allow one-adjacent-sentence scope
    _ADJACENT_CONNECTIVES = frozenset({"after"})

    for i, sentence in enumerate(sentences):
        sentence_lower = sentence.lower()
        has_failure = any(p.search(sentence) for p in failure_patterns)
        if not has_failure:
            continue

        # Same-sentence check for all connectives
        for connective in CAUSAL_CONNECTIVES:
            if connective in sentence_lower:
                return ExtractorResult(
                    fired=True,
                    tier="C",
                    evidence=[("build", "modifier")],
                )

        # Adjacent-sentence check for temporal connective 'after'
        if i + 1 < len(sentences):
            next_sent_lower = sentences[i + 1].lower()
            for connective in _ADJACENT_CONNECTIVES:
                if connective in next_sent_lower:
                    return ExtractorResult(
                        fired=True,
                        tier="C",
                        evidence=[("build", "modifier")],
                    )

    return ExtractorResult(fired=False, tier="C", evidence=[])


# ---------------------------------------------------------------------------
# E7 — area_span (Tier A modifier — pure, no filesystem access)
# ---------------------------------------------------------------------------


def extract_area_span(
    ctx: PostureContext,
    *,
    area_map: dict[str, list[str]],
    host_condition: bool,
) -> ExtractorResult:
    """E7: Count distinct project areas spanned by file_paths.

    Tier A (structured-field modifier) — pure function.  The caller
    must supply a pre-loaded ``area_map`` (from ``_areas.load_area_map``)
    and the ``host_condition`` flag indicating whether E1 (stacktrace) or
    E2 (test-failure) fired; this function never reads the filesystem.

    E7 is a modifier: it refines an already-active diagnose context per
    §10/§11. Posture evidence is only emitted when the diagnose host is
    active (``host_condition=True``).  The span count is always preserved
    in ``result.fired`` so downstream callers (``_area_span_count``) can
    read ``int(e7.fired)`` regardless of ``host_condition``.

    Inside a diagnose context (E1/E2 fired): span ≤ 1 hints debugger;
    span ≥ 2 hints investigator.  Without an active host, posture evidence
    is suppressed to avoid misrouting plain build/verify tasks (#347).

    Args:
        ctx: The dispatch context.
        area_map: Pre-loaded project area → globs mapping.
        host_condition: True when E1 (stacktrace_block) or E2
            (test_failure_output) has fired in the same dispatch.
            Controls whether diagnose evidence is emitted.

    Returns:
        ExtractorResult with ``fired``=<span count> (int) when
        ``file_paths`` non-empty and at least one area matched;
        ``fired``=False when no paths present or span == 0.
        ``evidence`` carries diagnose weight only when
        ``host_condition=True``; empty otherwise.
    """
    from claude_wayfinder.posture._areas import count_distinct_areas

    if not ctx.file_paths:
        return ExtractorResult(fired=False, tier="A", evidence=[])

    span = count_distinct_areas(ctx.file_paths, area_map)
    if span == 0:
        return ExtractorResult(fired=False, tier="A", evidence=[])

    # Emit diagnose evidence only when the host context (E1/E2) is active.
    # The span count is always returned in fired for downstream consumers.
    if not host_condition:
        return ExtractorResult(fired=span, tier="A", evidence=[])

    weight_class = "strong" if span >= 2 else "weak"
    return ExtractorResult(
        fired=span,
        tier="A",
        evidence=[("diagnose", weight_class)],
    )


# ---------------------------------------------------------------------------
# E8 — command_prefix (Tier A)
# ---------------------------------------------------------------------------


def extract_command_prefix(ctx: PostureContext) -> ExtractorResult:
    """E8: Detect command_prefix (strongest single extractor per §10.2).

    Tier A — reads the structured ``command_prefix`` field directly.
    A non-null prefix indicates an operate-posture task.

    The prefix value also provides a domain hint (e.g. "git"/"gh" →
    VCS-operate, "az"/"terraform"/"kubectl" → infra-operate) but domain
    extraction is out of scope for this library.

    Posture evidence: operate (strong).

    Args:
        ctx: The dispatch context.

    Returns:
        ExtractorResult with operate evidence when command_prefix is set.
    """
    if not ctx.command_prefix:
        return ExtractorResult(fired=False, tier="A", evidence=[])
    return ExtractorResult(
        fired=True, tier="A", evidence=[("operate", "strong")]
    )


# ---------------------------------------------------------------------------
# E9 — artifact_absence (Tier A computed)
# ---------------------------------------------------------------------------


def extract_artifact_absence(
    ctx: PostureContext,
    *,
    artifact_extractor_results: Sequence[ExtractorResult],
    prose_failure_result: ExtractorResult,
) -> ExtractorResult:
    """E9: Detect absence of all artifact-bearing extractors (gate trigger).

    Tier A (computed) — fires when ALL of the following are true:
    1. No artifact-bearing extractor fired (E1–E5, E8).
    2. ``file_paths`` is empty.
    3. No path-shaped token in ``task_description``.
    4. ``command_prefix`` is absent.
    5. E12 (prose_failure_mention) did NOT fire (§12.3 R2 suppressor).
       When E12 fires, E9 is suppressed → honest abstain instead of a
       misleading gate-trio advisory (P10 fix).

    When E9 fires it opens the gate for E10 (frame_markers) to split
    plan / research / idea-critique.

    Posture evidence: none directly — E9 is a gate, not a posture vote.
    Its ``fired`` state is consumed by ``extract_frame_markers``.

    Args:
        ctx: The dispatch context.
        artifact_extractor_results: Results from E1–E5 and E8 (any
            subset may be passed; E9 checks whether *any* fired).
        prose_failure_result: Result from E12 (prose_failure_mention).
            When fired, E9 is suppressed per R2.

    Returns:
        ExtractorResult with fired=True (and empty evidence) when the
        artifact-absence gate is open; fired=False otherwise.
    """
    # R2: E12 suppresses E9
    if prose_failure_result.fired:
        return ExtractorResult(fired=False, tier="A", evidence=[])

    # Check all artifact-bearing extractors
    if any(bool(r.fired) for r in artifact_extractor_results):
        return ExtractorResult(fired=False, tier="A", evidence=[])

    # Check structured fields
    if ctx.file_paths:
        return ExtractorResult(fired=False, tier="A", evidence=[])
    if ctx.command_prefix:
        return ExtractorResult(fired=False, tier="A", evidence=[])

    # Check for path-shaped tokens in prose
    prose_tokens = _extract_path_tokens(ctx.task_description)
    if prose_tokens:
        return ExtractorResult(fired=False, tier="A", evidence=[])

    # All checks passed — artifact absence gate is open
    return ExtractorResult(fired=True, tier="A", evidence=[])


# ---------------------------------------------------------------------------
# E10 — frame_markers (Tier C, inside E9 gate)
# ---------------------------------------------------------------------------


def extract_frame_markers(
    ctx: PostureContext,
    *,
    e9_gate_open: bool,
) -> ExtractorResult:
    """E10: Detect frame markers that split the E9 gate into posture classes.

    Tier C — conditional on ``e9_gate_open`` (E9 fired).  Only evaluates
    inside the artifact-absence gate; always abstains when the gate is
    closed (§10.3 guardrail 3).

    Three frozen sets split the gate:
    - FRAME_MARKERS_PRIOR_ART  → research posture
    - FRAME_MARKERS_SCOPE      → plan posture
    - FRAME_MARKERS_CHALLENGE  → idea-critique posture

    Bare proposal frames ("what if", "idea", "approach") with no decisive
    set are intentionally NOT fired — they resolve to advisory (§10.2:
    "recoverable by design").

    R1: E10 is the canonical example of C *selecting* within an
    A/B-activated candidate set (the E9 gate is an A extractor).

    Args:
        ctx: The dispatch context.
        e9_gate_open: True when E9 (artifact_absence) fired.

    Returns:
        ExtractorResult with the matching posture evidence, or
        fired=False when gate closed or no decisive marker found.
    """
    if not e9_gate_open:
        return ExtractorResult(fired=False, tier="C", evidence=[])

    text_lower = ctx.task_description.lower()

    # Check prior-art set → research
    if any(marker in text_lower for marker in FRAME_MARKERS_PRIOR_ART):
        return ExtractorResult(
            fired=True, tier="C", evidence=[("research", "strong")]
        )

    # Check scope set → plan
    if any(marker in text_lower for marker in FRAME_MARKERS_SCOPE):
        return ExtractorResult(
            fired=True, tier="C", evidence=[("plan", "strong")]
        )

    # Check challenge set → idea-critique
    if any(marker in text_lower for marker in FRAME_MARKERS_CHALLENGE):
        return ExtractorResult(
            fired=True, tier="C", evidence=[("idea-critique", "strong")]
        )

    # No decisive set found — advisory by design
    return ExtractorResult(fired=False, tier="C", evidence=[])


# ---------------------------------------------------------------------------
# E11 — agent_mentions (Tier A)
# ---------------------------------------------------------------------------


def extract_agent_mentions(ctx: PostureContext) -> ExtractorResult:
    """E11: Detect explicit specialist agent mentions (near-dispositive).

    Tier A — reads the structured ``agent_mentions`` field directly.
    Per §10.2, explicit agent name → near-dispositive pass-through
    (mirrors existing matcher behavior).

    Posture evidence: the mentioned agent's posture is inferred
    contextually by downstream composition; here we emit a generic
    "as-named" marker.  We surface the agent names in evidence for
    telemetry.

    Args:
        ctx: The dispatch context.

    Returns:
        ExtractorResult with fired=True and operate evidence when any
        agent is mentioned; fired=False otherwise.
    """
    if not ctx.agent_mentions:
        return ExtractorResult(fired=False, tier="A", evidence=[])

    # Surface each mention as an evidence entry (agent name as posture key)
    evidence = [
        (f"as-named:{agent}", "strong") for agent in sorted(ctx.agent_mentions)
    ]
    return ExtractorResult(fired=True, tier="A", evidence=evidence)


# ---------------------------------------------------------------------------
# E12 — prose_failure_mention (Tier C — brake + E9 suppressor)
# ---------------------------------------------------------------------------


def extract_prose_failure_mention(ctx: PostureContext) -> ExtractorResult:
    """E12: Detect prose failure terms from the frozen Tier-C set.

    Tier C — wired with exactly two effects (§12.3 R2):
    (a) **brake**: contest a confident non-diagnose outcome → advisory.
    (b) **E9 suppressor**: prevent artifact_absence from misfiring on
        prose-only failure prompts (P10 fix: honest abstain instead of
        misleading trio-advisory).

    NEVER activates diagnose.  This is a suppression/brake-only extractor.
    The constraint is structural: its evidence list contains no diagnose
    entry.

    Note: E12 uses whole-word matching against the frozen PROSE_FAILURE_TERMS
    set to reduce false positives.  Multi-word terms (e.g. "errors out")
    are checked as substrings.

    Args:
        ctx: The dispatch context.

    Returns:
        ExtractorResult with fired=True and brake evidence when a
        prose failure term is found; fired=False otherwise.
    """
    text_lower = ctx.task_description.lower()

    for term in PROSE_FAILURE_TERMS:
        if " " in term:
            # Multi-word term: substring match
            if term in text_lower:
                return ExtractorResult(
                    fired=True,
                    tier="C",
                    evidence=[("brake", "weak")],
                )
        else:
            # Single-word term: whole-word match to reduce false positives
            pattern = re.compile(r"\b" + re.escape(term) + r"\b", re.IGNORECASE)
            if pattern.search(ctx.task_description):
                return ExtractorResult(
                    fired=True,
                    tier="C",
                    evidence=[("brake", "weak")],
                )

    return ExtractorResult(fired=False, tier="C", evidence=[])
