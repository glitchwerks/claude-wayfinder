"""Row-schema contract tests for compute_kc1..compute_kc5 (issue #508).

Three prior incidents (#493, #497, and the #503 precursor) each crashed or
mis-computed ``scripts/corpus/eval/_kc.py`` because one shadow-corpus row
omitted an expected key -- discovered live, one incident at a time, because
no test pinned the row-schema contract itself. This module is that missing
contract test: it sweeps every documented ABSENT-key case (not merely the
null-value case, which prior fixes already handle via ``dict.get()``) across
all five ``compute_kcN`` functions in one pass.

RED — written before implementation. Current ``_kc.py`` reads several
fields via direct bracket indexing (``row["shadow"]``, ``row["input"]``,
``shadow[f"{arm}_decision"]``, ``row["shadow"]["posture_routed"]``, etc.)
rather than ``.get()``, so a row that omits any of those keys entirely
currently raises an uncaught ``KeyError`` instead of producing a defined
eligibility/verdict outcome. See the module-level "Vulnerability map"
comment below for exactly which (function, key) pairs are expected to
raise today.

The desired contract (what a correct implementation must satisfy): a row
missing any of these keys is tolerated exactly like the key being present
with value ``None`` (or, for ``gated_agent_names``, like an empty/falsy
list) -- it must never raise, and it must resolve to a defined, non-
crashing eligibility/verdict outcome (typically: excluded from the
relevant eligible set).

Row/gold builders below are independent, minimal re-implementations of the
schema documented in ``tests/test_corpus_eval/test_kc.py`` -- kept
self-contained here on purpose so this contract module does not depend on
another test module's private helpers.

Vulnerability map (current ``_kc.py``, confirmed by reading the source
before writing these tests):

    Top-level "input" missing entirely:
        compute_kc1/kc2/kc5 -- never read row["input"] -- NOT vulnerable.
        compute_kc3/kc4     -- unconditional ``row["input"]`` bracket
                               access in the eligibility scan over EVERY
                               row -- KeyError('input').

    Top-level "shadow" missing entirely:
        compute_kc1/kc2/kc5 -- ``_system_results`` does
                               ``shadow = row["shadow"]`` unconditionally
                               for every row -- KeyError('shadow').
        compute_kc3/kc4     -- only reached once a row is already eligible
                               (input-derived criteria), then
                               ``row["shadow"][...]`` -- KeyError('shadow').

    shadow.shadow_decision / shadow.shadow_agent / shadow.shadow_confidence
    missing:
        compute_kc1/kc2/kc5 -- ``_system_results(rows, "shadow")`` indexes
                               ``shadow[f"shadow_{field}"]`` unconditionally
                               -- KeyError.
        compute_kc3         -- also reads shadow_decision directly in the
                               numerator loop (only when the row is
                               eligible AND posture_routed is False).

    shadow.live_decision / shadow.live_agent / shadow.live_confidence
    missing:
        compute_kc1/kc2     -- ``_system_results(rows, "live")`` -- KeyError.
        compute_kc5         -- never computes the live arm -- NOT vulnerable.

    shadow.posture_routed missing:
        compute_kc3/kc4     -- direct ``row["shadow"]["posture_routed"]``
                               access, only for already-eligible rows --
                               KeyError.
        compute_kc1/kc2/kc5 -- never read this field -- NOT vulnerable.

    shadow.gated_agent_names missing:
        compute_kc3         -- read only when an eligible row has
                               posture_routed=False AND
                               shadow_decision=="delegate" -- KeyError.
        compute_kc1/kc2/kc4/kc5 -- never read this field -- NOT vulnerable.
"""

from __future__ import annotations

import copy
from typing import Any, Callable

import pytest

from scripts.corpus.eval._kc import (
    KCVerdict,
    compute_kc1,
    compute_kc2,
    compute_kc3,
    compute_kc4,
    compute_kc5,
)
from scripts.corpus.eval._reader import GoldLabel

CorpusRow = dict[str, Any]
ComputeKC = Callable[[list[CorpusRow], dict[int, GoldLabel]], KCVerdict]

_ALL_COMPUTE_KC: dict[str, ComputeKC] = {
    "KC-1": compute_kc1,
    "KC-2": compute_kc2,
    "KC-3": compute_kc3,
    "KC-4": compute_kc4,
    "KC-5": compute_kc5,
}
_VALID_STATUSES = {"PASS", "FAIL", "INSUFFICIENT_DATA"}


# ---------------------------------------------------------------------------
# Synthetic corpus-row + gold builders (independent of test_kc.py)
# ---------------------------------------------------------------------------


def _row(
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
) -> CorpusRow:
    """Build one fully-populated corpus row matching the shadow-join schema.

    Args:
        corpus_id: Row identifier, matched against gold labels.
        domain: Caller-supplied domain label (mirrored into input/shadow).
        posture: Caller-supplied posture label (mirrored into input/shadow).
        confidence: Caller-supplied confidence label (mirrored into
            input/shadow).
        area_span: Caller-supplied area-span count.
        shadow_decision: Compose (shadow arm) routing decision.
        shadow_agent: Compose (shadow arm) target agent, or None.
        shadow_confidence: Compose (shadow arm) confidence.
        live_decision: Lexical decide() (live arm) routing decision.
        live_agent: Lexical decide() (live arm) target agent, or None.
        live_confidence: Lexical decide() (live arm) confidence.
        posture_routed: Whether the shadow arm's decision came from the
            posture-cell lookup rather than the decide() fallback.
        gated_agent_names: Domain-gated candidate agent names surviving
            the shadow arm's gate, or None.

    Returns:
        A corpus row dict with every key the KC computations may read.
    """
    return {
        "corpus_id": corpus_id,
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
        "matcher_version": "6d5f416",
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


def _gold(
    corpus_id: int,
    gold_agent: str = "code-writer",
    domain: str = "code",
    posture: str = "build",
    is_any: bool = False,
    area_span: int = 1,
) -> GoldLabel:
    """Build one GoldLabel for the gold map.

    Args:
        corpus_id: Row identifier this label applies to.
        gold_agent: Corrected preferred agent.
        domain: Corrected domain.
        posture: Corrected posture.
        is_any: Whether the gold label marks this row domain-any.
        area_span: Corrected area-span count.

    Returns:
        A GoldLabel for use in a gold map.
    """
    return GoldLabel(
        corpus_id=corpus_id,
        domain=domain,
        posture=posture,
        gold_agent=gold_agent,
        is_any=is_any,
        area_span=area_span,
    )


def _gold_map(*labels: GoldLabel) -> dict[int, GoldLabel]:
    """Build a corpus_id -> GoldLabel dict.

    Args:
        *labels: GoldLabel instances to index.

    Returns:
        Dict keyed by each label's corpus_id.
    """
    return {label.corpus_id: label for label in labels}


def _drop(
    row: CorpusRow,
    *,
    drop_input: bool = False,
    drop_shadow: bool = False,
    input_keys: tuple[str, ...] = (),
    shadow_keys: tuple[str, ...] = (),
) -> CorpusRow:
    """Return a copy of ``row`` with the given keys entirely ABSENT.

    This is the fixture generator this contract sweeps over: any test in
    this module builds its row via one call to ``_row`` followed by one
    call here, naming exactly which keys should vanish. Deleting a key is
    deliberately distinct from setting it to ``None`` -- prior fixes
    (#493/#497) only handled the null-value case via ``dict.get()``; this
    module targets the ABSENT-key case those fixes did not reach for every
    key still accessed via direct bracket indexing.

    Args:
        row: Source row, not mutated.
        drop_input: Remove the top-level "input" key entirely.
        drop_shadow: Remove the top-level "shadow" key entirely.
        input_keys: Sub-keys to delete from ``row["input"]`` (ignored
            when ``drop_input`` is set, since the whole dict is gone).
        shadow_keys: Sub-keys to delete from ``row["shadow"]`` (ignored
            when ``drop_shadow`` is set).

    Returns:
        A new row dict with the specified keys entirely absent.
    """
    out = copy.deepcopy(row)
    if drop_input:
        del out["input"]
    elif input_keys:
        for key in input_keys:
            del out["input"][key]
    if drop_shadow:
        del out["shadow"]
    elif shadow_keys:
        for key in shadow_keys:
            del out["shadow"][key]
    return out


# ---------------------------------------------------------------------------
# Top-level "input" key entirely absent
# ---------------------------------------------------------------------------


class TestTopLevelInputKeyOmission:
    """A row with no "input" key at all must not crash any compute_kcN.

    compute_kc1/kc2/kc5 never read ``row["input"]``, so this axis is
    already tolerated for them today -- asserted here to pin the full
    five-function contract in one place rather than leaving it implicit.
    compute_kc3/kc4 scan ``row["input"]`` unconditionally for every row
    (not just eligible ones) and currently raise ``KeyError('input')``.
    """

    @pytest.mark.parametrize("kc_name", ["KC-1", "KC-2", "KC-3", "KC-4", "KC-5"])
    def test_missing_input_key_does_not_raise(self, kc_name: str) -> None:
        row = _drop(_row(1, shadow_agent="devops"), drop_input=True)
        gold = _gold_map(_gold(1, gold_agent="devops", domain="infra_deploy"))
        compute = _ALL_COMPUTE_KC[kc_name]

        verdict = compute([row], gold)

        assert verdict.status in _VALID_STATUSES


# ---------------------------------------------------------------------------
# Top-level "shadow" key entirely absent
# ---------------------------------------------------------------------------


class TestTopLevelShadowKeyOmission:
    """A row with no "shadow" key at all must not crash any compute_kcN.

    All five functions eventually index into ``row["shadow"]``:
    compute_kc1/kc2/kc5 unconditionally via ``_system_results`` for every
    row; compute_kc3/kc4 only once a row has already cleared the
    input-derived eligibility gate. Both paths currently raise
    ``KeyError('shadow')``.
    """

    def test_kc1_missing_shadow_key_does_not_raise(self) -> None:
        row = _drop(_row(1, shadow_agent="devops"), drop_shadow=True)
        gold = _gold_map(_gold(1, gold_agent="devops", domain="infra_deploy"))

        verdict = compute_kc1([row], gold)

        assert verdict.status in _VALID_STATUSES

    def test_kc2_missing_shadow_key_does_not_raise(self) -> None:
        row = _drop(_row(1, shadow_agent="devops"), drop_shadow=True)
        gold = _gold_map(_gold(1, gold_agent="devops", domain="infra_deploy"))

        verdict = compute_kc2([row], gold)

        assert verdict.status in _VALID_STATUSES

    def test_kc5_missing_shadow_key_does_not_raise(self) -> None:
        """Row must land in the infra_deploy slice to exercise this path."""
        row = _drop(_row(1, shadow_agent="devops"), drop_shadow=True)
        gold = _gold_map(_gold(1, gold_agent="devops", domain="infra_deploy"))

        verdict = compute_kc5([row], gold)

        assert verdict.status in _VALID_STATUSES

    def test_kc3_missing_shadow_key_does_not_raise(self) -> None:
        """Row must clear KC-3 eligibility (gated x cell x high-conf) first."""
        row = _drop(
            _row(1, domain="code", posture="build", confidence="high"),
            drop_shadow=True,
        )
        gold = _gold_map(_gold(1))

        verdict = compute_kc3([row], gold)

        assert verdict.status in _VALID_STATUSES

    def test_kc4_missing_shadow_key_does_not_raise(self) -> None:
        """Row must clear KC-4's mislabel-eligibility gate first."""
        row = _drop(
            _row(1, domain="project_meta", posture="build"),
            drop_shadow=True,
        )
        gold = _gold_map(_gold(1, domain="code", posture="build"))

        verdict = compute_kc4([row], gold)

        assert verdict.status in _VALID_STATUSES


# ---------------------------------------------------------------------------
# shadow.{shadow,live}_{decision,agent,confidence} sub-fields absent
# ---------------------------------------------------------------------------


_SHADOW_ARM_FIELDS: tuple[str, ...] = (
    "shadow_decision",
    "shadow_agent",
    "shadow_confidence",
)
_LIVE_ARM_FIELDS: tuple[str, ...] = (
    "live_decision",
    "live_agent",
    "live_confidence",
)


class TestShadowArmFieldOmission:
    """``_system_results(rows, "shadow")`` indexes these three fields for
    every row, unconditionally, for KC-1, KC-2, and KC-5. A row omitting
    any one of them currently raises ``KeyError`` before a verdict can be
    produced.
    """

    @pytest.mark.parametrize("kc_name", ["KC-1", "KC-2", "KC-5"])
    @pytest.mark.parametrize("field", _SHADOW_ARM_FIELDS)
    def test_missing_shadow_arm_field_does_not_raise(
        self, kc_name: str, field: str
    ) -> None:
        row = _drop(_row(1, shadow_agent="devops"), shadow_keys=(field,))
        gold = _gold_map(_gold(1, gold_agent="devops", domain="infra_deploy"))
        compute = _ALL_COMPUTE_KC[kc_name]

        verdict = compute([row], gold)

        assert verdict.status in _VALID_STATUSES


class TestLiveArmFieldOmission:
    """``_system_results(rows, "live")`` indexes these three fields for
    every row, unconditionally, for KC-1 and KC-2. KC-5 never computes
    the live arm, so it is not exercised here.
    """

    @pytest.mark.parametrize("kc_name", ["KC-1", "KC-2"])
    @pytest.mark.parametrize("field", _LIVE_ARM_FIELDS)
    def test_missing_live_arm_field_does_not_raise(
        self, kc_name: str, field: str
    ) -> None:
        row = _drop(_row(1), shadow_keys=(field,))
        gold = _gold_map(_gold(1))
        compute = _ALL_COMPUTE_KC[kc_name]

        verdict = compute([row], gold)

        assert verdict.status in _VALID_STATUSES

    def test_kc5_ignores_missing_live_arm_field(self) -> None:
        """Documents that KC-5 never reads the live arm -- always tolerant."""
        row = _drop(_row(1, shadow_agent="devops"), shadow_keys=("live_decision",))
        gold = _gold_map(_gold(1, gold_agent="devops", domain="infra_deploy"))

        verdict = compute_kc5([row], gold)

        assert verdict.status in _VALID_STATUSES


# ---------------------------------------------------------------------------
# KC-3's posture_routed / shadow_decision / gated_agent_names sub-fields
# ---------------------------------------------------------------------------


class TestKC3ShadowFieldOmission:
    """KC-3's numerator loop reads ``posture_routed``, ``shadow_decision``,
    and ``gated_agent_names`` directly off already-eligible rows. Each is
    reached only along a specific short-circuit branch; the fixtures below
    are built to actually reach each one, not merely to omit the key.
    """

    def test_missing_posture_routed_does_not_raise(self) -> None:
        """The very first access in the numerator loop is posture_routed."""
        row = _drop(
            _row(1, domain="code", posture="build", confidence="high"),
            shadow_keys=("posture_routed",),
        )
        gold = _gold_map(_gold(1))

        verdict = compute_kc3([row], gold)

        assert verdict.status in _VALID_STATUSES

    def test_missing_shadow_decision_does_not_raise(self) -> None:
        """posture_routed=False forces evaluation into the delegate clause,
        which reads shadow_decision next."""
        row = _drop(
            _row(
                1,
                domain="code",
                posture="build",
                confidence="high",
                posture_routed=False,
            ),
            shadow_keys=("shadow_decision",),
        )
        gold = _gold_map(_gold(1))

        verdict = compute_kc3([row], gold)

        assert verdict.status in _VALID_STATUSES

    def test_missing_gated_agent_names_does_not_raise(self) -> None:
        """posture_routed=False + shadow_decision=='delegate' reaches the
        gated_agent_names truthiness check last in the chain."""
        row = _drop(
            _row(
                1,
                domain="code",
                posture="build",
                confidence="high",
                posture_routed=False,
                shadow_decision="delegate",
            ),
            shadow_keys=("gated_agent_names",),
        )
        gold = _gold_map(_gold(1))

        verdict = compute_kc3([row], gold)

        assert verdict.status in _VALID_STATUSES


class TestKC4ShadowFieldOmission:
    """KC-4's violations count reads ``posture_routed`` directly off
    already-eligible mislabel rows.
    """

    def test_missing_posture_routed_does_not_raise(self) -> None:
        row = _drop(
            _row(1, domain="project_meta", posture="build", posture_routed=False),
            shadow_keys=("posture_routed",),
        )
        gold = _gold_map(_gold(1, domain="code", posture="build"))

        verdict = compute_kc4([row], gold)

        assert verdict.status in _VALID_STATUSES


# ---------------------------------------------------------------------------
# Audit-gap closure: input.domain omission for KC-3 (not covered by
# test_kc.py's TestKC3MissingOptionalKeys, which only sweeps confidence
# and posture). Verified, not assumed, per the #508 briefing.
# ---------------------------------------------------------------------------


class TestKC3InputDomainOmissionGapClosure:
    """KC-3 already reads ``input.domain`` via ``.get()``, so an absent
    domain key should already verdict-match an explicit ``domain=None`` --
    this closes the one input-field gap test_kc.py's existing
    ``TestKC3MissingOptionalKeys`` class does not cover.
    """

    def test_missing_domain_key_equals_null_domain(self) -> None:
        row_null = _row(1, domain=None, posture_routed=True)
        row_absent = _drop(
            _row(2, domain=None, posture_routed=True), input_keys=("domain",)
        )
        gold = _gold_map(_gold(1), _gold(2))

        verdict_null = compute_kc3([row_null], gold)
        verdict_absent = compute_kc3([row_absent], gold)

        assert verdict_absent.metrics == verdict_null.metrics
        assert verdict_absent.status == verdict_null.status


# ---------------------------------------------------------------------------
# Kitchen sink: the maximally-sparse row (#493/#497/#503 shape, unified)
# ---------------------------------------------------------------------------


class TestMinimalRowKitchenSink:
    """A single row carrying only ``corpus_id`` -- no "input", no "shadow"
    at all -- is the union of the #493, #497, and #503-precursor failure
    shapes. One contract test sweeping this row across all five functions
    is exactly what would have caught all three incidents in a single
    pass instead of three separate live discoveries.
    """

    @pytest.mark.parametrize("kc_name", ["KC-1", "KC-2", "KC-3", "KC-4", "KC-5"])
    def test_maximally_sparse_row_does_not_raise(self, kc_name: str) -> None:
        row: CorpusRow = {"corpus_id": 1}
        gold = _gold_map(_gold(1, gold_agent="devops", domain="infra_deploy"))
        compute = _ALL_COMPUTE_KC[kc_name]

        verdict = compute([row], gold)

        assert verdict.status in _VALID_STATUSES
