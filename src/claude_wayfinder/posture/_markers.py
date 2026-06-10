"""Frozen Tier-C marker sets for the posture-evidence extractor library.

All Tier-C marker sets are module-level frozenset constants. They are
versioned here (``TIER_C_MARKERS_VERSION``) and must NEVER be extended
at runtime. Any change to a set is a design decision requiring a spec
update and version bump, not an implementation detail.

Source: spec §10.2 (E5, E6, E10), §11.1 F1, §12.3 R1–R3.

Design constraints (§10.3 guardrails + §12.3 R1):
- Sets are enumerated exactly as written in the spec; no synonyms added.
- Tier C never *adds* a candidate posture — it selects within an
  A/B-activated set or brakes a confident outcome toward advisory.
- Frozen constants + version make "unchanged at runtime" trivially auditable.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------

#: Source-control version for the Tier-C marker sets.
#: Bump on ANY change to any set below.
TIER_C_MARKERS_VERSION: str = "2026-06-09"

# ---------------------------------------------------------------------------
# E5 — source_of_truth_pair C-assist (§10.2)
#
# Named-doc relational markers that confirm a conformance-check framing
# when ≥2 artifact refs are already present (B core).
# Role per R1: *select* within B-core-activated verify candidate set.
# ---------------------------------------------------------------------------

RELATIONAL_MARKERS: frozenset[str] = frozenset(
    {
        "against",
        "matches",
        "conforms to",
        "consistent with",
        "in sync with",
        "drifted from",
    }
)

#: E5 named-doc nouns (§10.2 E5 C-assist).
#: These confirm a conformance-check framing when ≥2 artifact refs are
#: already present (B core). Role per R1: completes E5's B-core activation,
#: never adds a posture alone.
NAMED_DOC_NOUNS: frozenset[str] = frozenset(
    {
        "release notes",
        "changelog",
        "schema",
        "contract",
        "invariant",
    }
)

# ---------------------------------------------------------------------------
# E6 — cause_stated causal connectives (§10.2 + §12.3 R3)
#
# Causal connectives that, when found in the SAME punctuation-delimited
# clause as a machine-emitted failure reference (E1/E2 fired), flip the
# posture evidence from diagnose → build.
#
# R3: clause-scoped — the connective MUST share a clause with the failure
# mention.  A token-window match (P12 hazard) is incorrect.
# ---------------------------------------------------------------------------

CAUSAL_CONNECTIVES: frozenset[str] = frozenset(
    {
        "after",
        "because",
        "due to",
        "caused by",
        "since",
        "introduced by",
    }
)

# ---------------------------------------------------------------------------
# E10 — frame_markers sets (§10.2)
#
# Three mutually-exclusive frozen sets that split the E9 gate into
# plan / research / idea-critique posture evidence.  Each set is tested
# independently; a prompt may match at most one decisive set.
#
# Bare proposal frames ({what if, idea, approach}) with no decisive set
# stay advisory by design (§10.2: "recoverable by design").
# ---------------------------------------------------------------------------

#: E10 prior-art set → research posture.
FRAME_MARKERS_PRIOR_ART: frozenset[str] = frozenset(
    {
        "prior art",
        "what exists",
        "alternatives",
        "has anyone",
    }
)

#: E10 scope set → plan posture.
FRAME_MARKERS_SCOPE: frozenset[str] = frozenset(
    {
        "roadmap",
        "phases",
        "milestones",
        "scope",
    }
)

#: E10 challenge set → idea-critique posture.
FRAME_MARKERS_CHALLENGE: frozenset[str] = frozenset(
    {
        "is this sound",
        "poke holes",
        "stress-test",
        "challenge",
        "critique",
    }
)

# ---------------------------------------------------------------------------
# E12 — prose_failure_mention (§11.1 F1 + §12.3 R2)
#
# Frozen set of prose failure terms.  E12 is wired with exactly two
# effects:
#   (a) brake: contest a confident non-diagnose outcome → advisory.
#   (b) suppress E9: prevent artifact_absence from misfiring on
#       prose-only failure prompts (R2, P10 fix).
#
# IMPORTANT: E12 NEVER activates diagnose.  It is suppression-only
# (§12.3 R2: "suppression is safe-direction — can only remove
# activations, never add them").
# ---------------------------------------------------------------------------

PROSE_FAILURE_TERMS: frozenset[str] = frozenset(
    {
        "failing",
        "fails",
        "broken",
        "red",
        "errors out",
        "crashes",
    }
)
