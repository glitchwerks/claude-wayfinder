"""Shared fixtures for the tests/test_posture/ package.

Defines the P1-P14 spike prompt fixtures from spec §11 (with §12.1 results
and R1-R3 refinements applied), PostureContext instances, and helper builders.

Spike prompt conventions:
- expected_fires: dict mapping extractor name to bool (fired?) or int (count).
- expected_postures: list of posture strings the extraction should produce.
- gold_agent: the routing-table gold agent for that prompt.
- band: qualitative outcome band from §12.1 (confident / advisory / abstain).
- notes: citation of spec section explaining any non-obvious assertion.
"""

from __future__ import annotations

from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Type alias for fixture record
# ---------------------------------------------------------------------------

SpikeRecord = dict[str, Any]


# ---------------------------------------------------------------------------
# P1–P14 spike fixtures
# ---------------------------------------------------------------------------


SPIKE_PROMPTS: list[SpikeRecord] = [
    # -----------------------------------------------------------------------
    # P1 — verify happy path (§11 §12.1)
    # E5 core (2 paths) + relational "consistent with" → verify → auditor
    # HIT under R1: C relational marker *selects within* B-core-activated verify
    # -----------------------------------------------------------------------
    {
        "id": "P1",
        "task_description": (
            "Make sure `db/schema.sql` is consistent with the migrations in"
            " `db/migrations/`."
        ),
        "file_paths": ["db/schema.sql", "db/migrations/"],
        "agent_mentions": [],
        "tool_mentions": [],
        "command_prefix": None,
        "expected_fires": {
            "source_of_truth_pair": True,
            "stacktrace_block": False,
            "test_failure_output": False,
            "vcs_artifact_ref": False,
            "spec_plan_path": False,
            "cause_stated": False,
            "command_prefix_ext": False,
            "artifact_absence": False,
            "agent_mentions_ext": False,
            "prose_failure_mention": False,
        },
        "evidence_postures": ["verify"],
        "gold_agent": "auditor",
        "band": "confident",
        "notes": (
            "§12.1 P1: HIT under R1. E5 B-core (2 file_path artifacts) activates"
            " verify; C relational marker 'consistent with' selects within that"
            " candidate set. R1 blesses this pattern: C selects within A/B-activated"
            " set."
        ),
    },
    # -----------------------------------------------------------------------
    # P2 — ⚠ E5 pair-strictness / E9 false-fire (§11 §12.1)
    # No path token, E5 core fails (≤1 artifact), E9 fires → advisory
    # Miss, recoverable (as designed; confirms F2)
    # -----------------------------------------------------------------------
    {
        "id": "P2",
        "task_description": "Does the README still reflect how the build actually works?",
        "file_paths": [],
        "agent_mentions": [],
        "tool_mentions": [],
        "command_prefix": None,
        "expected_fires": {
            "source_of_truth_pair": False,
            "stacktrace_block": False,
            "test_failure_output": False,
            "vcs_artifact_ref": False,
            "spec_plan_path": False,
            "cause_stated": False,
            "command_prefix_ext": False,
            # E9 artifact_absence fires: no structured artifacts at all
            "artifact_absence": True,
            "agent_mentions_ext": False,
            # E12: no prose failure terms
            "prose_failure_mention": False,
        },
        "evidence_postures": [],
        "gold_agent": "auditor",
        "band": "advisory",
        "notes": (
            "§12.1 P2: miss, recoverable (by design; confirms F2). No path-shaped"
            " token → E5 core fails. E9 fires (no artifacts). E10 finds no decisive"
            " frame-marker set → advisory. Gold is auditor but design accepts"
            " advisory-band recovery."
        ),
    },
    # -----------------------------------------------------------------------
    # P3 — ⚠ prose-failure blind spot (§11 §12.1 + R2)
    # After R2: E12 fires (prose failure terms) → brakes verify confident → advisory
    # Without R2 this was confident-wrong (verify→auditor over investigator gold)
    # -----------------------------------------------------------------------
    {
        "id": "P3",
        "task_description": (
            "The app crashes on startup and the config doesn't match what the docs"
            " say — figure out which is right."
        ),
        "file_paths": ["config/app.yaml", "docs/config.md"],
        "agent_mentions": [],
        "tool_mentions": [],
        "command_prefix": None,
        "expected_fires": {
            "source_of_truth_pair": True,
            "stacktrace_block": False,
            "test_failure_output": False,
            "vcs_artifact_ref": False,
            "spec_plan_path": False,
            "cause_stated": False,
            "command_prefix_ext": False,
            "artifact_absence": False,
            # E12 fires: "crashes" is in the prose failure frozen set
            "prose_failure_mention": True,
            "agent_mentions_ext": False,
        },
        "evidence_postures": ["verify"],
        "gold_agent": "investigator",
        "band": "advisory",
        "notes": (
            "§12.1 P3: originally confident-wrong (verify→auditor). R2 adopted:"
            " E12 'crashes' fires → brakes verify confident outcome → advisory."
            " §12.3 R2: E12 wired as brake on non-diagnose confident outcomes."
            " Band is advisory (not confident), which is the R2-corrected behavior."
        ),
    },
    # -----------------------------------------------------------------------
    # P4 — research happy path (§11 §12.1)
    # E9 + E10 prior-art → research → researcher
    # -----------------------------------------------------------------------
    {
        "id": "P4",
        "task_description": (
            "I have an idea for caching dispatch results between sessions —"
            " has anyone built something like this?"
        ),
        "file_paths": [],
        "agent_mentions": [],
        "tool_mentions": [],
        "command_prefix": None,
        "expected_fires": {
            "source_of_truth_pair": False,
            "stacktrace_block": False,
            "test_failure_output": False,
            "vcs_artifact_ref": False,
            "spec_plan_path": False,
            "cause_stated": False,
            "command_prefix_ext": False,
            "artifact_absence": True,
            "agent_mentions_ext": False,
            "prose_failure_mention": False,
        },
        "evidence_postures": ["research"],
        "gold_agent": "researcher",
        "band": "confident",
        "notes": (
            "§12.1 P4: HIT. E9 fires (no artifacts). E10 prior-art set fires"
            " ('has anyone'). research posture → researcher."
        ),
    },
    # -----------------------------------------------------------------------
    # P5 — plan happy path (§11 §12.1)
    # E9 + E10 scope → plan → project-planner
    # -----------------------------------------------------------------------
    {
        "id": "P5",
        "task_description": (
            "We should add result caching to the matcher. Lay out the phases"
            " and milestones to get there."
        ),
        "file_paths": [],
        "agent_mentions": [],
        "tool_mentions": [],
        "command_prefix": None,
        "expected_fires": {
            "source_of_truth_pair": False,
            "stacktrace_block": False,
            "test_failure_output": False,
            "vcs_artifact_ref": False,
            "spec_plan_path": False,
            "cause_stated": False,
            "command_prefix_ext": False,
            "artifact_absence": True,
            "agent_mentions_ext": False,
            "prose_failure_mention": False,
        },
        "evidence_postures": ["plan"],
        "gold_agent": "project-planner",
        "band": "confident",
        "notes": (
            "§12.1 P5: HIT. E9 fires (no artifacts). E10 scope set fires"
            " ('phases', 'milestones'). plan posture → project-planner."
        ),
    },
    # -----------------------------------------------------------------------
    # P6 — ambiguous-by-design (§11 §12.1)
    # E9 fires; E10 bare proposal only → advisory (designed outcome)
    # -----------------------------------------------------------------------
    {
        "id": "P6",
        "task_description": (
            "What if we cached the catalog in memory instead of re-reading"
            " it each call?"
        ),
        "file_paths": [],
        "agent_mentions": [],
        "tool_mentions": [],
        "command_prefix": None,
        "expected_fires": {
            "source_of_truth_pair": False,
            "stacktrace_block": False,
            "test_failure_output": False,
            "vcs_artifact_ref": False,
            "spec_plan_path": False,
            "cause_stated": False,
            "command_prefix_ext": False,
            "artifact_absence": True,
            "agent_mentions_ext": False,
            "prose_failure_mention": False,
        },
        # No decisive E10 set fires — stays in advisory (no specific posture)
        "evidence_postures": [],
        "gold_agent": "approach-critic",
        "band": "advisory",
        "notes": (
            "§12.1 P6: HIT-by-design. E9 fires. E10 bare proposal frame ('what if')"
            " does not hit any decisive set → advisory. Designed outcome: ambiguous"
            " proposals land in advisory, recoverable by router."
        ),
    },
    # -----------------------------------------------------------------------
    # P7 — idea-critique happy path (§11 §12.1)
    # E9 + E10 challenge → idea-critique → approach-critic
    # -----------------------------------------------------------------------
    {
        "id": "P7",
        "task_description": (
            "Poke holes in this approach before I build it: store gold labels"
            " in issue bodies instead of a file."
        ),
        "file_paths": [],
        "agent_mentions": [],
        "tool_mentions": [],
        "command_prefix": None,
        "expected_fires": {
            "source_of_truth_pair": False,
            "stacktrace_block": False,
            "test_failure_output": False,
            "vcs_artifact_ref": False,
            "spec_plan_path": False,
            "cause_stated": False,
            "command_prefix_ext": False,
            "artifact_absence": True,
            "agent_mentions_ext": False,
            "prose_failure_mention": False,
        },
        "evidence_postures": ["idea-critique"],
        "gold_agent": "approach-critic",
        "band": "confident",
        "notes": (
            "§12.1 P7: HIT. E9 fires. E10 challenge set fires ('poke holes')."
            " idea-critique posture → approach-critic."
        ),
    },
    # -----------------------------------------------------------------------
    # P8 — ⚠ frozen-set synonym miss (§11 §12.1)
    # artifact present → no E9; 'tear apart' ∉ challenge set → no critique
    # mark → default build → code-writer (advisory per §10.4 mitigation)
    # -----------------------------------------------------------------------
    {
        "id": "P8",
        "task_description": (
            "Tear apart the error handling in `src/matcher/engine.py`"
            " — I think it's too clever."
        ),
        "file_paths": ["src/matcher/engine.py"],
        "agent_mentions": [],
        "tool_mentions": [],
        "command_prefix": None,
        "expected_fires": {
            "source_of_truth_pair": False,
            "stacktrace_block": False,
            "test_failure_output": False,
            "vcs_artifact_ref": False,
            "spec_plan_path": False,
            "cause_stated": False,
            "command_prefix_ext": False,
            # E9 does NOT fire: file_paths is non-empty
            "artifact_absence": False,
            "agent_mentions_ext": False,
            "prose_failure_mention": False,
        },
        # No posture extractor fires → build is the unmarked default (§10.4)
        # but it contributes as advisory, not confident
        "evidence_postures": [],
        "gold_agent": "inquisitor",
        "band": "advisory",
        "notes": (
            "§12.1 P8: miss, recoverable. 'tear apart' not in frozen challenge set"
            " (§12.3 R4: no synonym expansion without corpus). No E9 (file present)."
            " Default-build fires (§10.4) → advisory band (mitigation a). Gold:"
            " inquisitor. Frozen set held: no creep."
        ),
    },
    # -----------------------------------------------------------------------
    # P9 — ⚠ assess/critique boundary (§11 §12.1)
    # E3 (PR #214) → assess → code-reviewer; harshness invisible
    # confident-wrong, accepted low-harm (adjacent cells; R4)
    # -----------------------------------------------------------------------
    {
        "id": "P9",
        "task_description": "Give PR #214 a really harsh review — don't go easy on it.",
        "file_paths": [],
        "agent_mentions": [],
        "tool_mentions": [],
        "command_prefix": None,
        "expected_fires": {
            "source_of_truth_pair": False,
            "stacktrace_block": False,
            "test_failure_output": False,
            # E3 fires: PR #214 reference
            "vcs_artifact_ref": True,
            "spec_plan_path": False,
            "cause_stated": False,
            "command_prefix_ext": False,
            "artifact_absence": False,
            "agent_mentions_ext": False,
            "prose_failure_mention": False,
        },
        "evidence_postures": ["assess"],
        "gold_agent": "inquisitor",
        "band": "confident",
        "notes": (
            "§12.1 P9: confident-wrong, accepted low-harm. E3 fires (PR #214)."
            " assess posture → code-reviewer. Gold: inquisitor (harsh review)."
            " §12.3 R4: adjacent posture miss (assess↔critique) is low-harm;"
            " no harshness marker set until corpus frequency justifies one."
        ),
    },
    # -----------------------------------------------------------------------
    # P10 — ⚠ §8.2 worked example, prose variant (§11 §12.1 + R2)
    # E1/E2/E6 silent (prose), E9 fires, E10 silent
    # After R2: E12 fires ('failing') → E9 suppressed → honest abstain
    # -----------------------------------------------------------------------
    {
        "id": "P10",
        "task_description": (
            "tests are failing after the rename, update them to match the new API."
        ),
        "file_paths": [],
        "agent_mentions": [],
        "tool_mentions": [],
        "command_prefix": None,
        "expected_fires": {
            "stacktrace_block": False,
            "test_failure_output": False,
            "vcs_artifact_ref": False,
            "spec_plan_path": False,
            # E6: NOT evaluated (E1/E2 both silent on prose)
            "cause_stated": False,
            "command_prefix_ext": False,
            # E9 suppressed by E12 under R2 (prose_failure_mention fires)
            "artifact_absence": False,
            "agent_mentions_ext": False,
            # E12 fires: 'failing' is in frozen prose-failure set
            "prose_failure_mention": True,
            "source_of_truth_pair": False,
        },
        # No posture activates → abstain (honest, not misleading trio advisory)
        "evidence_postures": [],
        "gold_agent": "code-writer",
        "band": "advisory",
        "notes": (
            "§12.1 P10: miss, recoverable. Under R2: E12 fires ('failing') →"
            " suppresses E9 as gate precondition → honest abstain (not misleading"
            " trio-advisory). §11.1 F1 + §12.3 R2. Gold: code-writer (cause stated"
            " → build) but this is the prose variant; only the P11 pasted-output"
            " variant achieves the E6 flip."
        ),
    },
    # -----------------------------------------------------------------------
    # P11 — E6 happy flip (§11 §12.1)
    # E2 fires → diagnose; E6 'after' in clause → flip → build → code-writer
    # -----------------------------------------------------------------------
    {
        "id": "P11",
        "task_description": (
            "Here's pytest: `FAILED tests/test_api.py::test_fetch -"
            " AttributeError: no attribute 'get_user'`. Started after we renamed"
            " get_user → fetch_user. Update the tests to match."
        ),
        "file_paths": [],
        "agent_mentions": [],
        "tool_mentions": [],
        "command_prefix": None,
        "expected_fires": {
            "stacktrace_block": False,
            # E2 fires: FAILED ...::... pattern
            "test_failure_output": True,
            "vcs_artifact_ref": False,
            "spec_plan_path": False,
            # E6 fires: 'after' in same clause as failure → flip diagnose→build
            "cause_stated": True,
            "command_prefix_ext": False,
            "artifact_absence": False,
            "agent_mentions_ext": False,
            "prose_failure_mention": False,
            "source_of_truth_pair": False,
        },
        # E2 → diagnose; E6 flips → build
        "evidence_postures": ["build"],
        "gold_agent": "code-writer",
        "band": "confident",
        "notes": (
            "§12.1 P11: HIT. E2 fires (FAILED ...::...). E6 'after' in same clause"
            " as failure reference → flip diagnose→build. code-writer (cause known)."
            " §12.3 R3: clause-scoped proximity required."
        ),
    },
    # -----------------------------------------------------------------------
    # P12 — ⚠ E6 misattached connective (§11 §12.1 + R3)
    # E1 fires → diagnose; layer-count {deploy, DNS} = 2 → investigator side
    # E6 hazard: "because" is attached to change's motivation, NOT failure
    # Under R3 (clause-scoped proximity): E6 does NOT fire → investigator (HIT)
    # E12: "fails" is in the frozen set → E12 fires (brake role, Tier C)
    # E12 brakes non-diagnose confident outcomes; P12 IS diagnose → no brake
    # effect on P12's routing, but the extractor still detects the term.
    # -----------------------------------------------------------------------
    {
        "id": "P12",
        "task_description": (
            "The deploy fails every time — logs show `Error: ECONNREFUSED"
            " api.internal:443`. We changed the DNS config last week because"
            " the old provider was slow. Figure out why it fails."
        ),
        "file_paths": [],
        "agent_mentions": [],
        "tool_mentions": [],
        "command_prefix": None,
        "expected_fires": {
            # E1 fires: Error: shape (ECONNREFUSED)
            "stacktrace_block": True,
            "test_failure_output": False,
            "vcs_artifact_ref": False,
            "spec_plan_path": False,
            # E6 does NOT fire under R3: 'because' is in a different sentence
            # from the failure mention (attached to motivation, not failure)
            "cause_stated": False,
            "command_prefix_ext": False,
            "artifact_absence": False,
            "agent_mentions_ext": False,
            # E12 fires: 'fails' is in the frozen prose-failure set.
            # This is correct detection — the brake effect on routing is
            # null because P12's posture is diagnose (E12 only brakes
            # non-diagnose confident outcomes per §12.3 R2).
            "prose_failure_mention": True,
            "source_of_truth_pair": False,
        },
        # E1 → diagnose; layer-count (deploy, DNS ≥ 2) → investigator
        "evidence_postures": ["diagnose"],
        "gold_agent": "investigator",
        "band": "confident",
        "notes": (
            "§12.1 P12: HIT under R3. E1 fires ('Error: ECONNREFUSED')."
            " Layer-count {deploy, DNS} = 2 → investigator side."
            " 'because' is attached to change motivation (separate sentence)."
            " R3 (clause-scoped proximity) keeps E6 silent → no flip → diagnose"
            " survives → investigator. E12 fires ('fails' in frozen set) but"
            " has no routing effect here because P12 IS diagnose (E12 brakes"
            " only non-diagnose confident outcomes, §12.3 R2)."
        ),
    },
    # -----------------------------------------------------------------------
    # P13 — operate control (§11 §12.1)
    # E8 (command_prefix=gh) → operate → ops
    # E12: 'red' in "what's red" is in the frozen set → E12 fires.
    # E12 would brake a non-diagnose confident outcome → advisory in isolation,
    # but E8 (Tier A, strongest extractor) dominates composition. The extractor
    # itself fires; downstream composition handles the brake/override logic.
    # -----------------------------------------------------------------------
    {
        "id": "P13",
        "task_description": "Run `gh pr checks 214` and summarize what's red.",
        "file_paths": [],
        "agent_mentions": [],
        "tool_mentions": [],
        "command_prefix": "gh",
        "expected_fires": {
            "source_of_truth_pair": False,
            "stacktrace_block": False,
            "test_failure_output": False,
            "vcs_artifact_ref": False,
            "spec_plan_path": False,
            "cause_stated": False,
            # E8 fires: command_prefix is non-null
            "command_prefix_ext": True,
            "artifact_absence": False,
            "agent_mentions_ext": False,
            # E12 fires: 'red' is in the frozen prose-failure set.
            # "summarize what's red" — 'red' matches the CI/test failure
            # concept. Composition weights E8 (Tier A operate) over E12
            # (Tier C brake) — §12.1 P13 confident HIT stands.
            "prose_failure_mention": True,
        },
        "evidence_postures": ["operate"],
        "gold_agent": "ops",
        "band": "confident",
        "notes": (
            "§12.1 P13: HIT. E8 fires (command_prefix='gh'). operate posture → ops."
            " E8 is strongest single extractor (§10.2). E12 also fires ('red' in"
            " frozen set, 'what's red' = CI check color) but Tier-A E8 evidence"
            " dominates in composition. The extractor correctly detects 'red'; the"
            " confident HIT persists because E8 outweighs the E12 brake signal."
        ),
    },
    # -----------------------------------------------------------------------
    # P14 — diagnose × span control (§11 §12.1)
    # E1 → diagnose; E7 span=2 (code + infra areas) → investigator
    # -----------------------------------------------------------------------
    {
        "id": "P14",
        "task_description": (
            "Getting this in CI: `Traceback (most recent call last)..."
            " ConnectionError` — happens only in the deploy workflow, never"
            " locally."
        ),
        "file_paths": ["src/api/client.py", ".github/workflows/deploy.yml"],
        "agent_mentions": [],
        "tool_mentions": [],
        "command_prefix": None,
        "expected_fires": {
            # E1 fires: Traceback (most recent call last)
            "stacktrace_block": True,
            "test_failure_output": False,
            "vcs_artifact_ref": False,
            "spec_plan_path": False,
            "cause_stated": False,
            "command_prefix_ext": False,
            "artifact_absence": False,
            "agent_mentions_ext": False,
            "prose_failure_mention": False,
            "source_of_truth_pair": False,
        },
        "evidence_postures": ["diagnose"],
        "gold_agent": "investigator",
        "band": "confident",
        "notes": (
            "§12.1 P14: HIT. E1 fires (Traceback). E7 span=2 (src → code area,"
            " .github/workflows → infra area) → investigator side."
        ),
    },
]


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(params=SPIKE_PROMPTS, ids=lambda r: r["id"])
def spike_record(request: pytest.FixtureRequest) -> SpikeRecord:
    """Parametrized fixture yielding each P1-P14 spike prompt record."""
    return request.param
