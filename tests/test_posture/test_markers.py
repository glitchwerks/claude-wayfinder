"""Tests for posture._markers: frozen Tier-C marker sets.

Per spec §10.3 guardrail 1 and §12.3, all Tier-C marker sets must be
module-level frozen constants, versioned in source, never modified at runtime.
"""

from __future__ import annotations


class TestMarkerConstants:
    """All Tier-C marker sets must exist as module-level frozensets."""

    def test_import_markers_module(self) -> None:
        """The _markers module must be importable."""
        from claude_wayfinder.posture import _markers  # noqa: F401

    def test_version_constant(self) -> None:
        """TIER_C_MARKERS_VERSION must be a string constant."""
        from claude_wayfinder.posture._markers import TIER_C_MARKERS_VERSION

        assert isinstance(TIER_C_MARKERS_VERSION, str)
        assert len(TIER_C_MARKERS_VERSION) > 0

    def test_relational_markers_frozenset(self) -> None:
        """RELATIONAL_MARKERS must be a frozenset of strings (E5 C-assist)."""
        from claude_wayfinder.posture._markers import RELATIONAL_MARKERS

        assert isinstance(RELATIONAL_MARKERS, frozenset)
        assert all(isinstance(m, str) for m in RELATIONAL_MARKERS)

    def test_relational_markers_content(self) -> None:
        """RELATIONAL_MARKERS must include the §10.2 E5 set verbatim."""
        from claude_wayfinder.posture._markers import RELATIONAL_MARKERS

        required = {
            "against",
            "matches",
            "conforms to",
            "consistent with",
            "in sync with",
            "drifted from",
        }
        assert required <= RELATIONAL_MARKERS, (
            f"RELATIONAL_MARKERS missing: {required - RELATIONAL_MARKERS}"
        )

    def test_causal_connectives_frozenset(self) -> None:
        """CAUSAL_CONNECTIVES must be a frozenset of strings (E6)."""
        from claude_wayfinder.posture._markers import CAUSAL_CONNECTIVES

        assert isinstance(CAUSAL_CONNECTIVES, frozenset)
        assert all(isinstance(m, str) for m in CAUSAL_CONNECTIVES)

    def test_causal_connectives_content(self) -> None:
        """CAUSAL_CONNECTIVES must include the §10.2 E6 set verbatim."""
        from claude_wayfinder.posture._markers import CAUSAL_CONNECTIVES

        required = {
            "after",
            "because",
            "due to",
            "caused by",
            "since",
            "introduced by",
        }
        assert required <= CAUSAL_CONNECTIVES, (
            f"CAUSAL_CONNECTIVES missing: {required - CAUSAL_CONNECTIVES}"
        )

    def test_frame_markers_frozensets(self) -> None:
        """E10 frame marker sets must exist as frozensets (§10.2)."""
        from claude_wayfinder.posture._markers import (
            FRAME_MARKERS_CHALLENGE,
            FRAME_MARKERS_PRIOR_ART,
            FRAME_MARKERS_SCOPE,
        )

        for name, fset in [
            ("FRAME_MARKERS_PRIOR_ART", FRAME_MARKERS_PRIOR_ART),
            ("FRAME_MARKERS_SCOPE", FRAME_MARKERS_SCOPE),
            ("FRAME_MARKERS_CHALLENGE", FRAME_MARKERS_CHALLENGE),
        ]:
            assert isinstance(fset, frozenset), f"{name} must be a frozenset"
            assert all(isinstance(m, str) for m in fset), (
                f"{name} must contain strings"
            )

    def test_frame_markers_prior_art_content(self) -> None:
        """FRAME_MARKERS_PRIOR_ART must contain §10.2 E10 terms."""
        from claude_wayfinder.posture._markers import FRAME_MARKERS_PRIOR_ART

        required = {"prior art", "what exists", "alternatives", "has anyone"}
        assert required <= FRAME_MARKERS_PRIOR_ART, (
            f"FRAME_MARKERS_PRIOR_ART missing: {required - FRAME_MARKERS_PRIOR_ART}"
        )

    def test_frame_markers_scope_content(self) -> None:
        """FRAME_MARKERS_SCOPE must contain §10.2 E10 terms."""
        from claude_wayfinder.posture._markers import FRAME_MARKERS_SCOPE

        required = {"roadmap", "phases", "milestones", "scope"}
        assert required <= FRAME_MARKERS_SCOPE, (
            f"FRAME_MARKERS_SCOPE missing: {required - FRAME_MARKERS_SCOPE}"
        )

    def test_frame_markers_challenge_content(self) -> None:
        """FRAME_MARKERS_CHALLENGE must contain §10.2 E10 terms."""
        from claude_wayfinder.posture._markers import FRAME_MARKERS_CHALLENGE

        required = {
            "is this sound",
            "poke holes",
            "stress-test",
            "challenge",
            "critique",
        }
        assert required <= FRAME_MARKERS_CHALLENGE, (
            f"FRAME_MARKERS_CHALLENGE missing: {required - FRAME_MARKERS_CHALLENGE}"
        )

    def test_prose_failure_terms_frozenset(self) -> None:
        """PROSE_FAILURE_TERMS must be a frozenset of strings (E12)."""
        from claude_wayfinder.posture._markers import PROSE_FAILURE_TERMS

        assert isinstance(PROSE_FAILURE_TERMS, frozenset)
        assert all(isinstance(m, str) for m in PROSE_FAILURE_TERMS)

    def test_prose_failure_terms_content(self) -> None:
        """PROSE_FAILURE_TERMS must contain §11.1/§12.3 R2 E12 set verbatim."""
        from claude_wayfinder.posture._markers import PROSE_FAILURE_TERMS

        required = {
            "failing",
            "fails",
            "broken",
            "red",
            "errors out",
            "crashes",
        }
        assert required <= PROSE_FAILURE_TERMS, (
            f"PROSE_FAILURE_TERMS missing: {required - PROSE_FAILURE_TERMS}"
        )

    def test_named_doc_nouns_frozenset(self) -> None:
        """NAMED_DOC_NOUNS must be a frozenset of strings (E5 C-assist)."""
        from claude_wayfinder.posture._markers import NAMED_DOC_NOUNS

        assert isinstance(NAMED_DOC_NOUNS, frozenset)
        assert all(isinstance(m, str) for m in NAMED_DOC_NOUNS)

    def test_named_doc_nouns_content(self) -> None:
        """NAMED_DOC_NOUNS must contain the §10.2 E5 C-assist set verbatim."""
        from claude_wayfinder.posture._markers import NAMED_DOC_NOUNS

        required = {
            "release notes",
            "changelog",
            "schema",
            "contract",
            "invariant",
        }
        assert required <= NAMED_DOC_NOUNS, (
            f"NAMED_DOC_NOUNS missing: {required - NAMED_DOC_NOUNS}"
        )

    def test_named_doc_nouns_exact_membership(self) -> None:
        """NAMED_DOC_NOUNS must contain exactly the five §10.2 E5 members."""
        from claude_wayfinder.posture._markers import NAMED_DOC_NOUNS

        expected = frozenset(
            {
                "release notes",
                "changelog",
                "schema",
                "contract",
                "invariant",
            }
        )
        assert NAMED_DOC_NOUNS == expected, (
            f"NAMED_DOC_NOUNS extra/missing: symmetric diff = "
            f"{NAMED_DOC_NOUNS.symmetric_difference(expected)}"
        )

    def test_marker_sets_are_runtime_immutable(self) -> None:
        """Marker sets must not be modifiable at runtime."""
        import pytest

        from claude_wayfinder.posture._markers import (
            CAUSAL_CONNECTIVES,
            FRAME_MARKERS_CHALLENGE,
            FRAME_MARKERS_PRIOR_ART,
            FRAME_MARKERS_SCOPE,
            NAMED_DOC_NOUNS,
            PROSE_FAILURE_TERMS,
            RELATIONAL_MARKERS,
        )

        for name, fset in [
            ("RELATIONAL_MARKERS", RELATIONAL_MARKERS),
            ("CAUSAL_CONNECTIVES", CAUSAL_CONNECTIVES),
            ("FRAME_MARKERS_PRIOR_ART", FRAME_MARKERS_PRIOR_ART),
            ("FRAME_MARKERS_SCOPE", FRAME_MARKERS_SCOPE),
            ("FRAME_MARKERS_CHALLENGE", FRAME_MARKERS_CHALLENGE),
            ("PROSE_FAILURE_TERMS", PROSE_FAILURE_TERMS),
            ("NAMED_DOC_NOUNS", NAMED_DOC_NOUNS),
        ]:
            with pytest.raises((AttributeError, TypeError)):
                fset.add("__test_mutation__")  # type: ignore[union-attr]
