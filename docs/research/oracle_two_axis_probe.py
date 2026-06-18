# FROZEN RESEARCH ARTIFACT — historical probe. The canonical cell map is
# src/claude_wayfinder/match/_cells.py. Do NOT treat this copy as live policy.

"""Two-axis oracle experiment for claude-wayfinder #358.

Extends oracle_domain_probe.py with posture as a second axis.

Systems (all evaluated on full / no_smoke / no_mention cuts):
  1. Lexical baseline (re-run for clean side-by-side)
  2. Domain-only oracle hard-gate (re-run)
  3. Posture-only oracle — gold posture → agent via cell map (any domain)
  4a. Domain+posture cell-map oracle — feed gold (domain, posture) into _CELL_MAP directly
  4b. Domain+posture compose oracle — domain hard-gate first, then posture as
      within-domain tiebreaker among surviving in-domain candidates

Run from repo root:
    .venv/Scripts/python.exe .tmp/oracle_two_axis_probe.py
"""

from __future__ import annotations

import json
import pathlib
import sys
from collections import Counter
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path("I:/ai/claude/claude-wayfinder/.claude/worktrees/vigilant-shamir-97d682")
CORPUS_PATH = pathlib.Path.home() / ".claude/state/wayfinder-corpus/2026-06-12/wayfinder-corpus.jsonl"
GOLD_LABELS_PATH = pathlib.Path.home() / ".claude/state/wayfinder-corpus/2026-06-12/gold-labels.jsonl"
CATALOG_PATH = pathlib.Path.home() / ".claude/state/dispatch-catalog.json"

sys.path.insert(0, str(REPO_ROOT))

# ---------------------------------------------------------------------------
# Domain → agent set map (unchanged from oracle_domain_probe.py)
# ---------------------------------------------------------------------------

_ANY_DOMAIN_AGENTS: frozenset[str] = frozenset({
    "investigator",
    "approach-critic",
    "auditor",
    "researcher",
    "ops",
    "project-planner",
})

DOMAIN_AGENT_MAP: dict[str, frozenset[str]] = {
    "code": frozenset({
        "code-writer",
        "debugger",
        "code-reviewer",
        "inquisitor",
    }) | _ANY_DOMAIN_AGENTS,
    "docs_prose": frozenset({
        "doc-writer",
    }) | _ANY_DOMAIN_AGENTS,
    "project_meta": frozenset({
        "project-reviewer",
        "project-planner",
    }) | _ANY_DOMAIN_AGENTS,
    "infra_deploy": frozenset({
        "devops",
    }) | _ANY_DOMAIN_AGENTS,
    None: None,  # type: ignore[assignment]
}

# ---------------------------------------------------------------------------
# §9.1 cell map (from scripts/corpus/eval/_systems.py)
# ---------------------------------------------------------------------------

_POSTURE_PRIORITY: list[str] = [
    "operate",
    "diagnose",
    "assess",
    "verify",
    "plan",
    "research",
    "idea-critique",
    "build",
]

_CELL_MAP: dict[tuple[str, str], str] = {
    # build row
    ("code", "build"): "code-writer",
    ("docs_prose", "build"): "doc-writer",
    ("any", "build"): "code-writer",
    # diagnose row
    ("code", "diagnose"): "debugger",
    ("infra_deploy", "diagnose"): "investigator",
    ("any", "diagnose"): "investigator",
    # assess row
    ("code", "assess"): "code-reviewer",
    ("project_meta", "assess"): "project-reviewer",
    ("any", "assess"): "code-reviewer",
    # critique row
    ("code", "critique"): "inquisitor",
    ("any", "critique"): "approach-critic",
    # idea-critique row
    ("any", "idea-critique"): "approach-critic",
    # verify row
    ("any", "verify"): "auditor",
    # plan row
    ("project_meta", "plan"): "project-planner",
    ("infra_deploy", "plan"): "devops",
    ("any", "plan"): "project-planner",
    # research row
    ("any", "research"): "researcher",
    # operate row
    ("any", "operate"): "ops",
}


def cell_map_lookup(domain: str, posture: str) -> str | None:
    """Look up an agent from (domain, posture) with fallback to (any, posture)."""
    return _CELL_MAP.get((domain, posture), _CELL_MAP.get(("any", posture)))


# ---------------------------------------------------------------------------
# Imports from eval harness
# ---------------------------------------------------------------------------

from claude_wayfinder.match._catalog import load_catalog
from claude_wayfinder.match._decide import decide
from claude_wayfinder.match._match import build_features, score_entries, ScoredEntry
from claude_wayfinder.match_filters import is_agent_routable
from scripts.corpus.eval._reader import CorpusEntry, GoldLabel, load_corpus, load_labels


# ---------------------------------------------------------------------------
# SystemResult (same as in oracle_domain_probe.py)
# ---------------------------------------------------------------------------

@dataclass
class SystemResult:
    corpus_id: int
    decision: str
    agent: str | None
    confidence: float
    extras: dict[str, Any]


# ---------------------------------------------------------------------------
# Data loading + corpus cut helpers (copied from oracle_domain_probe.py)
# ---------------------------------------------------------------------------


def load_data() -> tuple[list[CorpusEntry], dict[int, GoldLabel]]:
    corpus = load_corpus(CORPUS_PATH)
    labels = load_labels(GOLD_LABELS_PATH)
    return corpus, labels


def identify_smoke_ids(corpus: list[CorpusEntry]) -> frozenset[int]:
    smoke_tds = {"update the docs", "implement the new module"}
    return frozenset(e.corpus_id for e in corpus if e.task_description in smoke_tds)


def identify_mention_ids(corpus: list[CorpusEntry]) -> frozenset[int]:
    return frozenset(e.corpus_id for e in corpus if e.agent_mentions)


def apply_cut(
    corpus: list[CorpusEntry],
    labels: dict[int, GoldLabel],
    smoke_ids: frozenset[int],
    mention_ids: frozenset[int],
    cut: str,
) -> tuple[list[CorpusEntry], dict[int, GoldLabel]]:
    if cut == "full":
        return corpus, labels
    elif cut == "no_smoke":
        filtered = [e for e in corpus if e.corpus_id not in smoke_ids]
        filt_labels = {cid: lbl for cid, lbl in labels.items() if cid not in smoke_ids}
        return filtered, filt_labels
    elif cut == "no_mention":
        filtered = [e for e in corpus if e.corpus_id not in mention_ids]
        filt_labels = {cid: lbl for cid, lbl in labels.items() if cid not in mention_ids}
        return filtered, filt_labels
    else:
        raise ValueError(f"Unknown cut: {cut!r}")


def _entry_to_context(entry: CorpusEntry) -> dict[str, Any]:
    return {
        "task_description": entry.task_description,
        "file_paths": list(entry.file_paths),
        "agent_mentions": list(entry.agent_mentions),
        "tool_mentions": list(entry.tool_mentions),
        "command_prefix": entry.command_prefix,
    }


# ---------------------------------------------------------------------------
# System 1: Lexical baseline (unchanged)
# ---------------------------------------------------------------------------


def run_lexical(
    entries: list[CorpusEntry],
    catalog_path: pathlib.Path,
) -> list[SystemResult]:
    catalog = load_catalog(catalog_path)
    results: list[SystemResult] = []
    for entry in entries:
        ctx = _entry_to_context(entry)
        features = build_features(ctx)
        scored_agents, scored_skills = score_entries(catalog, features)
        top_scores = {se.entry.name: round(se.score, 4) for se in scored_agents[:5]}
        decision_dict = decide(scored_agents, scored_skills, features, catalog)
        results.append(SystemResult(
            corpus_id=entry.corpus_id,
            decision=str(decision_dict.get("decision", "")),
            agent=decision_dict.get("agent"),
            confidence=float(decision_dict.get("confidence", 0.0)),
            extras={"scores": top_scores},
        ))
    return results


# ---------------------------------------------------------------------------
# System 2: Domain-only oracle hard-gate (unchanged)
# ---------------------------------------------------------------------------


def run_oracle_hard_gate(
    entries: list[CorpusEntry],
    labels: dict[int, GoldLabel],
    catalog_path: pathlib.Path,
) -> list[SystemResult]:
    catalog = load_catalog(catalog_path)
    results: list[SystemResult] = []
    for entry in entries:
        label = labels.get(entry.corpus_id)
        oracle_domain = label.domain if label else None
        allowed_agents = DOMAIN_AGENT_MAP.get(oracle_domain)

        ctx = _entry_to_context(entry)
        features = build_features(ctx)
        scored_agents, scored_skills = score_entries(catalog, features)

        if allowed_agents is not None:
            gated_agents = [se for se in scored_agents if se.entry.name in allowed_agents]
            if not gated_agents:
                gated_agents = scored_agents
        else:
            gated_agents = scored_agents

        top_scores = {se.entry.name: round(se.score, 4) for se in gated_agents[:5]}
        decision_dict = decide(gated_agents, scored_skills, features, catalog)
        results.append(SystemResult(
            corpus_id=entry.corpus_id,
            decision=str(decision_dict.get("decision", "")),
            agent=decision_dict.get("agent"),
            confidence=float(decision_dict.get("confidence", 0.0)),
            extras={"scores": top_scores, "oracle_domain": oracle_domain},
        ))
    return results


# ---------------------------------------------------------------------------
# System 3: Posture-only oracle
# Gold posture → agent via cell map, domain="any" (posture signal only)
# Note: "build" posture with domain=any maps to code-writer (not doc-writer)
# so posture alone CANNOT resolve build→doc-writer without domain signal.
# ---------------------------------------------------------------------------


def run_oracle_posture_only(
    entries: list[CorpusEntry],
    labels: dict[int, GoldLabel],
    catalog_path: pathlib.Path,
) -> list[SystemResult]:
    """Posture-only oracle: use gold posture label, domain='any'.

    This measures the standalone routing signal of posture when domain is
    NOT available. Because build posture with domain=any maps to code-writer,
    this system cannot distinguish code-writer from doc-writer — it will
    always choose code-writer for build tasks.
    """
    catalog = load_catalog(catalog_path)
    catalog_agent_names = {
        e.name for e in catalog
        if e.kind == "agent" and is_agent_routable(
            name=e.name, kind=e.kind, source=e.source, routable=e.routable
        )
    }

    results: list[SystemResult] = []
    for entry in entries:
        label = labels.get(entry.corpus_id)
        if label is None:
            # No gold label: fall through to advisory
            results.append(SystemResult(
                corpus_id=entry.corpus_id,
                decision="advisory",
                agent=None,
                confidence=0.5,
                extras={"oracle_posture": None},
            ))
            continue

        oracle_posture = label.posture
        # Use domain=any (posture-only mode)
        agent = cell_map_lookup("any", oracle_posture)

        if agent and agent in catalog_agent_names:
            decision = "delegate"
            confidence = 0.9
        else:
            decision = "advisory"
            confidence = 0.5

        results.append(SystemResult(
            corpus_id=entry.corpus_id,
            decision=decision,
            agent=agent,
            confidence=confidence,
            extras={"oracle_posture": oracle_posture, "cell_lookup_domain": "any"},
        ))
    return results


# ---------------------------------------------------------------------------
# System 4a: Domain+posture cell-map oracle
# Feed gold (domain, posture) into _CELL_MAP directly → agent
# ---------------------------------------------------------------------------


def run_oracle_cellmap(
    entries: list[CorpusEntry],
    labels: dict[int, GoldLabel],
    catalog_path: pathlib.Path,
) -> list[SystemResult]:
    """Domain+posture oracle variant (a): cell-map lookup.

    Feeds gold (domain, posture) directly into _CELL_MAP.
    This is the "pure oracle" test of whether the cell-map design is correct.
    If the cell-map is correctly specified, this should give near-perfect RC
    for the entries it covers (excluding self_handle gold targets, which have
    no cell-map entry).
    """
    catalog = load_catalog(catalog_path)
    catalog_agent_names = {
        e.name for e in catalog
        if e.kind == "agent" and is_agent_routable(
            name=e.name, kind=e.kind, source=e.source, routable=e.routable
        )
    }

    results: list[SystemResult] = []
    for entry in entries:
        label = labels.get(entry.corpus_id)
        if label is None:
            results.append(SystemResult(
                corpus_id=entry.corpus_id,
                decision="advisory",
                agent=None,
                confidence=0.5,
                extras={},
            ))
            continue

        oracle_domain = label.domain if label.domain else "any"
        oracle_posture = label.posture

        # Direct cell-map lookup: domain-specific first, then any
        agent = cell_map_lookup(oracle_domain, oracle_posture)

        if agent and agent in catalog_agent_names:
            decision = "delegate"
            confidence = 0.9
        else:
            # Cell-map miss (e.g. no entry for this domain/posture combo)
            decision = "advisory"
            confidence = 0.5

        results.append(SystemResult(
            corpus_id=entry.corpus_id,
            decision=decision,
            agent=agent,
            confidence=confidence,
            extras={
                "oracle_domain": oracle_domain,
                "oracle_posture": oracle_posture,
                "cell_agent": agent,
            },
        ))
    return results


# ---------------------------------------------------------------------------
# System 4b: Domain+posture compose oracle
# Domain hard-gate first (proven), THEN posture as within-domain tiebreaker.
# Among in-domain surviving lexical candidates, posture picks the winner.
# ---------------------------------------------------------------------------


def run_oracle_compose(
    entries: list[CorpusEntry],
    labels: dict[int, GoldLabel],
    catalog_path: pathlib.Path,
) -> list[SystemResult]:
    """Domain+posture oracle variant (b): compose.

    Step 1: Apply domain hard-gate (prunes out-of-domain agents from lexical
    scored list — same as system 2).
    Step 2: From the surviving in-domain candidates, use gold posture to select
    the preferred agent: look up (domain, posture) in cell map, then check if
    the cell-map winner is in the gated candidate list.
    - If cell-map winner IS in gated candidates: delegate to it at 0.9.
    - If cell-map winner is NOT in gated candidates (agent absent from lexical
      scoring or scored 0): fall back to lexical decide() on gated list.
    - If no posture match: fall back to decide() on gated list.

    This tests the value of posture as an within-domain selector on top of the
    domain hard-gate mechanism, which already handles cross-domain tie-breaking.
    """
    catalog = load_catalog(catalog_path)
    catalog_agent_names = {
        e.name for e in catalog
        if e.kind == "agent" and is_agent_routable(
            name=e.name, kind=e.kind, source=e.source, routable=e.routable
        )
    }
    # Build a quick name→ScoredEntry map for catalog agents
    results: list[SystemResult] = []
    for entry in entries:
        label = labels.get(entry.corpus_id)
        oracle_domain = label.domain if label else None
        oracle_posture = label.posture if label else None
        domain_for_lookup = oracle_domain if oracle_domain else "any"

        # Step 1: lexical scoring + domain hard-gate (same as system 2)
        ctx = _entry_to_context(entry)
        features = build_features(ctx)
        scored_agents, scored_skills = score_entries(catalog, features)

        allowed_agents = DOMAIN_AGENT_MAP.get(oracle_domain)
        if allowed_agents is not None:
            gated_agents = [se for se in scored_agents if se.entry.name in allowed_agents]
            if not gated_agents:
                gated_agents = scored_agents
        else:
            gated_agents = scored_agents

        # Step 2: posture-based selection within gated candidates
        posture_routed = False
        agent_out: str | None = None
        decision_out: str = "advisory"
        confidence_out: float = 0.5

        if oracle_posture:
            # Look up preferred agent for this (domain, posture) cell
            preferred_agent = cell_map_lookup(domain_for_lookup, oracle_posture)
            # Check if the preferred agent is among the gated candidates
            gated_names = {se.entry.name for se in gated_agents}
            if preferred_agent and preferred_agent in gated_names and preferred_agent in catalog_agent_names:
                # Posture selects a specific agent from gated set → delegate to it
                agent_out = preferred_agent
                decision_out = "delegate"
                confidence_out = 0.9
                posture_routed = True

        if not posture_routed:
            # Fall back to lexical decide() on the gated list (same as domain-only system)
            decision_dict = decide(gated_agents, scored_skills, features, catalog)
            agent_out = decision_dict.get("agent")
            decision_out = str(decision_dict.get("decision", ""))
            confidence_out = float(decision_dict.get("confidence", 0.0))

        top_scores = {se.entry.name: round(se.score, 4) for se in gated_agents[:5]}
        results.append(SystemResult(
            corpus_id=entry.corpus_id,
            decision=decision_out,
            agent=agent_out,
            confidence=confidence_out,
            extras={
                "scores": top_scores,
                "oracle_domain": oracle_domain,
                "oracle_posture": oracle_posture,
                "posture_routed": posture_routed,
            },
        ))
    return results


# ---------------------------------------------------------------------------
# Metrics (same as oracle_domain_probe.py)
# ---------------------------------------------------------------------------


def compute_confident_wrong_rate(
    results: list[SystemResult],
    labels: dict[int, GoldLabel],
) -> float:
    delegates = [r for r in results if r.decision == "delegate" and r.corpus_id in labels]
    if not delegates:
        return float("nan")
    wrong = sum(1 for r in delegates if r.agent != labels[r.corpus_id].gold_agent)
    return round(wrong / len(delegates), 4)


def compute_routing_correctness(
    results: list[SystemResult],
    labels: dict[int, GoldLabel],
) -> float:
    labeled = [r for r in results if r.corpus_id in labels]
    if not labeled:
        return float("nan")
    correct = sum(1 for r in labeled if r.agent == labels[r.corpus_id].gold_agent)
    return round(correct / len(labeled), 4)


def compute_decision_distribution(
    results: list[SystemResult],
    labels: dict[int, GoldLabel],
) -> dict[str, int]:
    labeled = [r for r in results if r.corpus_id in labels]
    counts: dict[str, int] = {}
    for r in labeled:
        counts[r.decision] = counts.get(r.decision, 0) + 1
    return counts


def compute_domain_breakdown(
    results: list[SystemResult],
    labels: dict[int, GoldLabel],
) -> dict[str, dict[str, Any]]:
    """Break down RC by domain for a set of results."""
    by_domain: dict[str, list[tuple[str | None, str]]] = {}
    for r in results:
        lbl = labels.get(r.corpus_id)
        if lbl is None:
            continue
        domain = lbl.domain or "None"
        if domain not in by_domain:
            by_domain[domain] = []
        by_domain[domain].append((r.agent, lbl.gold_agent))

    breakdown: dict[str, dict[str, Any]] = {}
    for domain, pairs in sorted(by_domain.items()):
        n = len(pairs)
        correct = sum(1 for pred, gold in pairs if pred == gold)
        breakdown[domain] = {"n": n, "rc": round(correct / n, 4) if n > 0 else float("nan")}
    return breakdown


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    print("=== Two-axis oracle experiment (#358) ===")
    print(f"Corpus: {CORPUS_PATH}")
    print(f"Labels: {GOLD_LABELS_PATH}")
    print(f"Catalog: {CATALOG_PATH}")
    print()

    corpus, labels = load_data()
    smoke_ids = identify_smoke_ids(corpus)
    mention_ids = identify_mention_ids(corpus)

    print(f"Total corpus entries: {len(corpus)}")
    print(f"Total gold labels: {len(labels)}")
    print(f"Smoke probe IDs: {len(smoke_ids)}")
    print(f"Entries with agent_mentions: {len(mention_ids)}")
    print()

    # Posture + domain distributions
    posture_counts = Counter(lbl.posture for lbl in labels.values())
    domain_counts = Counter((lbl.domain or "None") for lbl in labels.values())
    print(f"Posture distribution: {dict(posture_counts.most_common())}")
    print(f"Domain distribution: {dict(domain_counts.most_common())}")
    print()

    # Show cell-map coverage: how many (domain,posture) gold combinations
    # are covered by the cell map?
    dp_combos = Counter((lbl.domain or "any", lbl.posture) for lbl in labels.values())
    print("Cell-map coverage for gold (domain, posture) combinations:")
    covered = 0
    total = 0
    for (d, p), cnt in sorted(dp_combos.items(), key=lambda x: -x[1]):
        agent = cell_map_lookup(d if d else "any", p)
        covered += cnt if agent else 0
        total += cnt
        hit = f"-> {agent}" if agent else "MISS"
        print(f"  ({d}, {p}): n={cnt}  {hit}")
    print(f"  Coverage: {covered}/{total} ({covered/total:.1%})")
    print()

    CUTS = ["full", "no_smoke", "no_mention"]
    SYSTEMS = ["lexical", "domain_only", "posture_only", "cellmap", "compose"]
    results_table: dict[str, dict[str, tuple[float, float, dict]]] = {s: {} for s in SYSTEMS}
    # For per-cut domain breakdown on key systems
    domain_breakdown_table: dict[str, dict[str, dict[str, Any]]] = {}

    for cut in CUTS:
        cut_corpus, cut_labels = apply_cut(corpus, labels, smoke_ids, mention_ids, cut)
        print(f"--- Cut: {cut} | entries={len(cut_corpus)}, labeled={len(cut_labels)} ---")

        # 1. Lexical baseline
        lex = run_lexical(cut_corpus, CATALOG_PATH)
        lex_cwr = compute_confident_wrong_rate(lex, cut_labels)
        lex_rc = compute_routing_correctness(lex, cut_labels)
        lex_dist = compute_decision_distribution(lex, cut_labels)
        print(f"  Lexical:         cwr={lex_cwr:.4f}  rc={lex_rc:.4f}  decisions={lex_dist}")
        results_table["lexical"][cut] = (lex_cwr, lex_rc, lex_dist)

        # 2. Domain-only oracle hard-gate
        hg = run_oracle_hard_gate(cut_corpus, cut_labels, CATALOG_PATH)
        hg_cwr = compute_confident_wrong_rate(hg, cut_labels)
        hg_rc = compute_routing_correctness(hg, cut_labels)
        hg_dist = compute_decision_distribution(hg, cut_labels)
        print(f"  Domain-only:     cwr={hg_cwr:.4f}  rc={hg_rc:.4f}  decisions={hg_dist}")
        results_table["domain_only"][cut] = (hg_cwr, hg_rc, hg_dist)

        # 3. Posture-only oracle
        po = run_oracle_posture_only(cut_corpus, cut_labels, CATALOG_PATH)
        po_cwr = compute_confident_wrong_rate(po, cut_labels)
        po_rc = compute_routing_correctness(po, cut_labels)
        po_dist = compute_decision_distribution(po, cut_labels)
        print(f"  Posture-only:    cwr={po_cwr:.4f}  rc={po_rc:.4f}  decisions={po_dist}")
        results_table["posture_only"][cut] = (po_cwr, po_rc, po_dist)

        # 4a. Domain+posture cell-map
        cm = run_oracle_cellmap(cut_corpus, cut_labels, CATALOG_PATH)
        cm_cwr = compute_confident_wrong_rate(cm, cut_labels)
        cm_rc = compute_routing_correctness(cm, cut_labels)
        cm_dist = compute_decision_distribution(cm, cut_labels)
        print(f"  CellMap(d+p):    cwr={cm_cwr:.4f}  rc={cm_rc:.4f}  decisions={cm_dist}")
        results_table["cellmap"][cut] = (cm_cwr, cm_rc, cm_dist)

        # 4b. Domain+posture compose
        cp = run_oracle_compose(cut_corpus, cut_labels, CATALOG_PATH)
        cp_cwr = compute_confident_wrong_rate(cp, cut_labels)
        cp_rc = compute_routing_correctness(cp, cut_labels)
        cp_dist = compute_decision_distribution(cp, cut_labels)
        print(f"  Compose(d+p):    cwr={cp_cwr:.4f}  rc={cp_rc:.4f}  decisions={cp_dist}")
        results_table["compose"][cut] = (cp_cwr, cp_rc, cp_dist)

        # Domain breakdown for compose vs domain-only (no_smoke cut only — the key cut)
        if cut == "no_smoke":
            print()
            print("  Domain breakdown (no_smoke) — routing-correctness by domain:")
            hg_bd = compute_domain_breakdown(hg, cut_labels)
            cp_bd = compute_domain_breakdown(cp, cut_labels)
            po_bd = compute_domain_breakdown(po, cut_labels)
            cm_bd = compute_domain_breakdown(cm, cut_labels)
            print(f"  {'Domain':<15} {'n':>4} {'Lexical':>8} {'Domain-only':>12} {'Posture-only':>13} {'CellMap':>9} {'Compose':>9}")
            lex_bd = compute_domain_breakdown(lex, cut_labels)
            for domain in sorted(set(list(hg_bd.keys()) + list(cp_bd.keys()))):
                n = hg_bd.get(domain, {}).get("n", 0)
                lex_rc_d = lex_bd.get(domain, {}).get("rc", float("nan"))
                hg_rc_d = hg_bd.get(domain, {}).get("rc", float("nan"))
                po_rc_d = po_bd.get(domain, {}).get("rc", float("nan"))
                cm_rc_d = cm_bd.get(domain, {}).get("rc", float("nan"))
                cp_rc_d = cp_bd.get(domain, {}).get("rc", float("nan"))
                print(f"  {domain:<15} {n:>4} {lex_rc_d:>8.4f} {hg_rc_d:>12.4f} {po_rc_d:>13.4f} {cm_rc_d:>9.4f} {cp_rc_d:>9.4f}")

            # Identify compose-posture-routed entries and their outcomes
            posture_routed_results = [r for r in cp if r.extras.get("posture_routed")]
            posture_correct = sum(
                1 for r in posture_routed_results
                if cut_labels.get(r.corpus_id) and r.agent == cut_labels[r.corpus_id].gold_agent
            )
            posture_wrong = len(posture_routed_results) - posture_correct
            print()
            print(f"  Compose posture-routed entries: {len(posture_routed_results)} "
                  f"(correct={posture_correct}, wrong={posture_wrong})")

            # Show entries where compose > domain-only (posture helped)
            hg_map = {r.corpus_id: r for r in hg}
            cp_map = {r.corpus_id: r for r in cp}
            gains = []
            losses = []
            for cid, lbl in cut_labels.items():
                hg_r = hg_map.get(cid)
                cp_r = cp_map.get(cid)
                if not hg_r or not cp_r:
                    continue
                hg_correct = hg_r.agent == lbl.gold_agent
                cp_correct = cp_r.agent == lbl.gold_agent
                if cp_correct and not hg_correct:
                    gains.append((cid, lbl.gold_agent, hg_r.agent, cp_r.agent, lbl.domain, lbl.posture))
                elif hg_correct and not cp_correct:
                    losses.append((cid, lbl.gold_agent, hg_r.agent, cp_r.agent, lbl.domain, lbl.posture))

            print()
            print(f"  Compose gains over domain-only: {len(gains)}")
            for cid, gold, hg_pred, cp_pred, domain, posture in gains:
                print(f"    id={cid}: gold={gold}  domain-only->{hg_pred}  compose->{cp_pred}  "
                      f"[domain={domain}, posture={posture}]")

            print(f"  Compose losses vs domain-only: {len(losses)}")
            for cid, gold, hg_pred, cp_pred, domain, posture in losses:
                print(f"    id={cid}: gold={gold}  domain-only->{hg_pred}  compose->{cp_pred}  "
                      f"[domain={domain}, posture={posture}]")

            # CellMap: compare vs domain-only
            cm_map = {r.corpus_id: r for r in cm}
            cm_gains = []
            cm_losses = []
            for cid, lbl in cut_labels.items():
                hg_r = hg_map.get(cid)
                cm_r = cm_map.get(cid)
                if not hg_r or not cm_r:
                    continue
                hg_correct = hg_r.agent == lbl.gold_agent
                cm_correct = cm_r.agent == lbl.gold_agent
                if cm_correct and not hg_correct:
                    cm_gains.append((cid, lbl.gold_agent, hg_r.agent, cm_r.agent, lbl.domain, lbl.posture))
                elif hg_correct and not cm_correct:
                    cm_losses.append((cid, lbl.gold_agent, hg_r.agent, cm_r.agent, lbl.domain, lbl.posture))
            print()
            print(f"  CellMap gains over domain-only: {len(cm_gains)}")
            for cid, gold, hg_pred, cp_pred, domain, posture in cm_gains:
                print(f"    id={cid}: gold={gold}  domain-only->{hg_pred}  cellmap->{cp_pred}  "
                      f"[domain={domain}, posture={posture}]")
            print(f"  CellMap losses vs domain-only: {len(cm_losses)}")
            for cid, gold, hg_pred, cp_pred, domain, posture in cm_losses:
                print(f"    id={cid}: gold={gold}  domain-only->{hg_pred}  cellmap->{cp_pred}  "
                      f"[domain={domain}, posture={posture}]")

        print()

    # ---------------------------------------------------------------------------
    # Summary table
    # ---------------------------------------------------------------------------
    print("=" * 110)
    print("SUMMARY TABLE (confident_wrong_rate / routing_correctness)")
    print("=" * 110)
    header = (f"{'System':<18} {'full_cwr':>10} {'full_rc':>10} "
              f"{'no_smoke_cwr':>13} {'no_smoke_rc':>12} "
              f"{'no_mention_cwr':>15} {'no_mention_rc':>14}")
    print(header)
    print("-" * 110)

    SYSTEM_LABELS = {
        "lexical": "Lexical",
        "domain_only": "Domain-only",
        "posture_only": "Posture-only",
        "cellmap": "CellMap(d+p)",
        "compose": "Compose(d+p)",
    }
    for sys_key in SYSTEMS:
        sys_name = SYSTEM_LABELS[sys_key]
        cut_data = results_table[sys_key]
        full = cut_data.get("full", (float("nan"), float("nan"), {}))
        ns = cut_data.get("no_smoke", (float("nan"), float("nan"), {}))
        nm = cut_data.get("no_mention", (float("nan"), float("nan"), {}))
        row = (
            f"{sys_name:<18} "
            f"{full[0]:>10.4f} {full[1]:>10.4f} "
            f"{ns[0]:>13.4f} {ns[1]:>12.4f} "
            f"{nm[0]:>15.4f} {nm[1]:>14.4f}"
        )
        print(row)

    print()
    print("HEADROOM DELTAS vs lexical (no-smoke cut)")
    lex_rc_ns = results_table["lexical"]["no_smoke"][1]
    lex_cwr_ns = results_table["lexical"]["no_smoke"][0]
    print(f"  Lexical baseline: rc={lex_rc_ns:.4f}  cwr={lex_cwr_ns:.4f}")
    for sys_key in SYSTEMS[1:]:
        sys_name = SYSTEM_LABELS[sys_key]
        ns = results_table[sys_key].get("no_smoke", (float("nan"), float("nan"), {}))
        rc_delta = ns[1] - lex_rc_ns
        cwr_delta = ns[0] - lex_cwr_ns
        print(f"  {sys_name:<18}: rc={ns[1]:.4f}  drc={rc_delta:+.4f}  cwr={ns[0]:.4f}  dcwr={cwr_delta:+.4f}")

    print()
    print("HEADROOM DELTAS vs domain-only (no-smoke cut) -- the key two-axis question")
    hg_rc_ns = results_table["domain_only"]["no_smoke"][1]
    hg_cwr_ns = results_table["domain_only"]["no_smoke"][0]
    print(f"  Domain-only baseline: rc={hg_rc_ns:.4f}  cwr={hg_cwr_ns:.4f}")
    for sys_key in ["posture_only", "cellmap", "compose"]:
        sys_name = SYSTEM_LABELS[sys_key]
        ns = results_table[sys_key].get("no_smoke", (float("nan"), float("nan"), {}))
        rc_delta = ns[1] - hg_rc_ns
        cwr_delta = ns[0] - hg_cwr_ns
        print(f"  {sys_name:<18}: rc={ns[1]:.4f}  drc={rc_delta:+.4f}  cwr={ns[0]:.4f}  dcwr={cwr_delta:+.4f}")


if __name__ == "__main__":
    main()
