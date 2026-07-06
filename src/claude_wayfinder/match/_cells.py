"""Two-axis routing policy: domain/posture cell map and per-domain agent gates.

Lifted verbatim from the validated probe at ``docs/research/oracle_two_axis_probe.py``.
The following known gold discrepancies are intentionally NOT fixed here (see
referenced issues for tracking):

- ``infra_deploy`` gate now includes ``"code-writer"`` (fix shipped in #364);
  ``devops`` is advisory-only per charter; ``code-writer`` is the infra
  implementation agent with the IaC skill attached by file path.
- ``("code", "diagnose")`` maps to ``"debugger"`` rather than
  ``"investigator"``.
- ``("infra_deploy", "research")`` resolves via ``("any", "research")`` to
  ``"researcher"`` rather than the gold-correct ``"investigator"``.
- ``("project_meta", "build")`` maps to ``SELF_HANDLE_SENTINEL`` (#397);
  these harness self-edits are handled by the router directly and must
  never be delegated to a sub-agent (project_meta × build carve-out).

Public API
----------
- ``ANY_DOMAIN_AGENTS`` -- frozenset of agents valid in every domain.
- ``DOMAIN_AGENT_MAP``  -- maps a domain label (or ``None``) to its
  permitted agent set (or ``None`` for no gate).
- ``SELF_HANDLE_SENTINEL`` -- routing instruction for cells the router
  handles itself; callers must translate this to ``self_handle / agent=None``.
- ``cell_map_lookup``   -- (domain, posture) → preferred agent name or None.
- ``gate_agents``       -- filter a scored list to agents allowed in a domain.
"""

from __future__ import annotations

from claude_wayfinder.match._types import ScoredEntry

# ---------------------------------------------------------------------------
# Constants — verbatim from the validated probe
# ---------------------------------------------------------------------------

# Routing instruction: the router handles this cell itself.  Callers that
# receive this value from cell_map_lookup must translate it to
# decision="self_handle" / agent=None and must never emit it as an agent name.
# Added in #397 to encode the project_meta × build harness carve-out.
SELF_HANDLE_SENTINEL: str = "__self_handle__"

ANY_DOMAIN_AGENTS: frozenset[str] = frozenset({
    "investigator",
    "approach-critic",
    "auditor",
    "researcher",
    "ops",
    "project-planner",
})

DOMAIN_AGENT_MAP: dict[str | None, frozenset[str] | None] = {
    "code": (
        frozenset(
            {
                "code-writer",
                "debugger",
                "code-reviewer",
                "inquisitor",
                # #452: test-implementer authors tests in the test-first
                # split; not a member of ANY_DOMAIN_AGENTS, so it must be
                # listed explicitly to survive gate_agents() for
                # code-domain tasks and reach compose_route's Branch-3
                # test-first discriminator.
                "test-implementer",
            }
        )
        | ANY_DOMAIN_AGENTS
    ),
    "docs_prose": frozenset({"doc-writer"}) | ANY_DOMAIN_AGENTS,
    "project_meta": (
        frozenset({"project-reviewer", "project-planner"}) | ANY_DOMAIN_AGENTS
    ),
    # code-writer added by #364; devops is advisory-only per charter;
    # code-writer is the infra implementation agent (IaC skill by file path)
    "infra_deploy": frozenset({"devops", "code-writer"}) | ANY_DOMAIN_AGENTS,
    None: None,  # is_any / unlabeled → no gate
}

_CELL_MAP: dict[tuple[str, str], str] = {
    ("code", "build"):           "code-writer",
    ("docs_prose", "build"):     "doc-writer",
    # #397: project_meta/build is a harness self-edit; sentinel encodes
    # the router carve-out → self_handle (never delegate to a sub-agent).
    ("project_meta", "build"):   SELF_HANDLE_SENTINEL,
    ("any", "build"):            "code-writer",
    ("code", "diagnose"):        "debugger",
    ("infra_deploy", "diagnose"): "investigator",
    ("any", "diagnose"):         "investigator",
    ("code", "assess"):          "code-reviewer",
    ("project_meta", "assess"):  "project-reviewer",
    ("any", "assess"):           "code-reviewer",
    ("code", "critique"):        "inquisitor",
    ("any", "critique"):         "approach-critic",
    ("any", "idea-critique"):    "approach-critic",
    ("any", "verify"):           "auditor",
    ("project_meta", "plan"):    "project-planner",
    ("infra_deploy", "plan"):    "devops",
    ("any", "plan"):             "project-planner",
    ("any", "research"):         "researcher",
    ("any", "operate"):          "ops",
}


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def cell_map_lookup(domain: str, posture: str) -> str | None:
    """Return the preferred agent for a (domain, posture) pair.

    Checks the direct ``(domain, posture)`` cell first, then falls back
    to ``("any", posture)`` if the direct key is absent.  Returns
    ``None`` when neither key exists in the map.

    Args:
        domain: Domain label, e.g. ``"code"``, ``"docs_prose"``, ``"any"``.
        posture: Posture label, e.g. ``"build"``, ``"diagnose"``, ``"plan"``.

    Returns:
        Agent name string, or ``None`` if no cell matches.
    """
    return _CELL_MAP.get((domain, posture), _CELL_MAP.get(("any", posture)))


def gate_agents(
    scored_agents: list[ScoredEntry],
    domain: str | None,
) -> list[ScoredEntry]:
    """Filter a scored agent list to those permitted in *domain*.

    Gating rules:

    - ``domain`` is ``None``, ``"is_any"``, or an unknown domain string
      (not a key in ``DOMAIN_AGENT_MAP``) → no gate applied; return
      *scored_agents* unchanged.
    - Otherwise keep only entries whose ``entry.name`` is in
      ``DOMAIN_AGENT_MAP[domain]``.
    - If gating would produce an empty list, fall back to ungated and
      return the original *scored_agents* list unchanged.

    Order of survivors is preserved relative to the input list.

    Args:
        scored_agents: Scored agent entries to filter (order preserved).
        domain: Domain label used to select the permitted-agent set, or
            ``None`` / ``"is_any"`` to bypass gating entirely.

    Returns:
        Filtered list of ``ScoredEntry`` objects, or the original list
        when no gate applies or when gating would empty the list.
    """
    # Normalize no-gate sentinels: None, "is_any", and unknown domains.
    if domain is None or domain == "is_any" or domain not in DOMAIN_AGENT_MAP:
        return scored_agents

    allowed = DOMAIN_AGENT_MAP[domain]
    # domain is a known key; allowed is a frozenset (not None) here.
    gated = [se for se in scored_agents if se.entry.name in allowed]  # type: ignore[operator]

    # Empty-after-gate → fall back to ungated.
    if not gated:
        return scored_agents

    return gated
