"""Offline posture-evidence extractor library for Matcher v3.

Provides deterministic, pure-Python extractors E1–E12 that map dispatch-
context fields to posture evidence signals.  The extractors consume a
``PostureContext`` (a frozen dataclass mirroring the dispatch-context
schema) and each return an ``ExtractorResult`` with the uniform output
contract from spec §10.3.

Design principles (spec §10.1–§10.3, §12.3 R1–R3):

- **Deterministic and pure**: no LLM, no network, no filesystem access
  inside extractor functions.  Same input always produces the same output.
- **Tier-tagged output**: every result carries its tier (A/B/C) for
  downstream telemetry.
- **Abstain ≠ veto**: a non-firing extractor contributes nothing but
  does not block other extractors.
- **Tier-C constraints** (R1): Tier-C markers can only *select* within
  an A/B-activated candidate set or *brake* a confident outcome toward
  advisory.  They never add a candidate posture.

Public surface
--------------

Types:
    PostureContext, ExtractorResult

Extractors (E1–E12):
    extract_stacktrace_block, extract_test_failure_output,
    extract_vcs_artifact_ref, extract_spec_plan_path,
    extract_source_of_truth_pair, extract_cause_stated, extract_area_span,
    extract_command_prefix, extract_artifact_absence, extract_frame_markers,
    extract_agent_mentions, extract_prose_failure_mention

Markers module (frozen Tier-C sets — versioned):
    Accessible via ``claude_wayfinder.posture._markers``

Areas loader (filesystem helper — NOT an extractor):
    Accessible via ``claude_wayfinder.posture._areas``

Spec reference: ``docs/superpowers/specs/
2026-06-08-semantic-routing-additive-evidence-synthesis.md``
§10 (extractor definitions) and §12.3 (R1–R3 refinements).
"""

from __future__ import annotations

from claude_wayfinder.posture._extractors import (
    extract_agent_mentions as extract_agent_mentions,
)
from claude_wayfinder.posture._extractors import (
    extract_area_span as extract_area_span,
)
from claude_wayfinder.posture._extractors import (
    extract_artifact_absence as extract_artifact_absence,
)
from claude_wayfinder.posture._extractors import (
    extract_cause_stated as extract_cause_stated,
)
from claude_wayfinder.posture._extractors import (
    extract_command_prefix as extract_command_prefix,
)
from claude_wayfinder.posture._extractors import (
    extract_frame_markers as extract_frame_markers,
)
from claude_wayfinder.posture._extractors import (
    extract_prose_failure_mention as extract_prose_failure_mention,
)
from claude_wayfinder.posture._extractors import (
    extract_source_of_truth_pair as extract_source_of_truth_pair,
)
from claude_wayfinder.posture._extractors import (
    extract_spec_plan_path as extract_spec_plan_path,
)
from claude_wayfinder.posture._extractors import (
    extract_stacktrace_block as extract_stacktrace_block,
)
from claude_wayfinder.posture._extractors import (
    extract_test_failure_output as extract_test_failure_output,
)
from claude_wayfinder.posture._extractors import (
    extract_vcs_artifact_ref as extract_vcs_artifact_ref,
)
from claude_wayfinder.posture._types import (
    ExtractorResult as ExtractorResult,
)
from claude_wayfinder.posture._types import (
    PostureContext as PostureContext,
)

__all__ = [
    # Types
    "PostureContext",
    "ExtractorResult",
    # Extractors E1–E12
    "extract_stacktrace_block",
    "extract_test_failure_output",
    "extract_vcs_artifact_ref",
    "extract_spec_plan_path",
    "extract_source_of_truth_pair",
    "extract_cause_stated",
    "extract_area_span",
    "extract_command_prefix",
    "extract_artifact_absence",
    "extract_frame_markers",
    "extract_agent_mentions",
    "extract_prose_failure_mention",
]
