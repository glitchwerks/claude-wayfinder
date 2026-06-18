# FROZEN RESEARCH ARTIFACT — historical probe. The canonical cell map is
# src/claude_wayfinder/match/_cells.py. Do NOT treat this copy as live policy.

"""Blind LLM labeling accuracy scorer — claude-wayfinder #358.

Reads:
    .tmp/labeler-output.jsonl   -- LLM-produced labels: {corpus_id, domain, posture}
    Gold labels (local):        ~/.claude/state/wayfinder-corpus/2026-06-12/gold-labels.jsonl
    Corpus (local):             ~/.claude/state/wayfinder-corpus/2026-06-12/wayfinder-corpus.jsonl
    Catalog:                    ~/.claude/state/dispatch-catalog.json

Computes:
    1. Per-axis labeling accuracy: domain (5-way) and posture (8-way) on the no-smoke cut.
    2. Confusion breakdown: which gold classes get mislabeled to what.
    3. Routes via five systems using REAL (imperfect) labeler labels and reports
       expected RC + CW — compared side-by-side with lexical, oracle domain-only,
       and oracle two-axis Compose.

Usage (from repo root):
    .venv/Scripts/python.exe .tmp/score_labeling.py [--labeler .tmp/labeler-output.jsonl]

Run from repo root:
    .venv/Scripts/python.exe .tmp/score_labeling.py
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path("I:/ai/claude/claude-wayfinder/.claude/worktrees/vigilant-shamir-97d682")
CORPUS_PATH = pathlib.Path.home() / ".claude/state/wayfinder-corpus/2026-06-12/wayfinder-corpus.jsonl"
GOLD_LABELS_PATH = pathlib.Path.home() / ".claude/state/wayfinder-corpus/2026-06-12/gold-labels.jsonl"
CATALOG_PATH = pathlib.Path.home() / ".claude/state/dispatch-catalog.json"
DEFAULT_LABELER_OUTPUT = REPO_ROOT / ".tmp" / "labeler-output.jsonl"

sys.path.insert(0, str(REPO_ROOT))

# ---------------------------------------------------------------------------
# Routing logic — copied verbatim from oracle_two_axis_probe.py
# (Do NOT import that file; it has a main() that would execute)
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
from claude_wayfinder.match._match import build_features, score_entries
from claude_wayfinder.match_filters import is_agent_routable
from scripts.corpus.eval._reader import CorpusEntry, GoldLabel, load_corpus, load_labels


# ---------------------------------------------------------------------------
# Labeler output
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LabelerLabel:
    corpus_id: int
    domain: str
    posture: str


def load_labeler_output(path: pathlib.Path) -> dict[int, LabelerLabel]:
    """Load labeler-output.jsonl → corpus_id → LabelerLabel."""
    if not path.exists():
        print(f"ERROR: Labeler output not found: {path}", file=sys.stderr)
        print("Run the labeler first, then re-run this scorer.", file=sys.stderr)
        sys.exit(1)
    labels: dict[int, LabelerLabel] = {}
    with open(path, encoding="utf-8") as fh:
        for i, raw_line in enumerate(fh, 1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"ERROR: Invalid JSON on line {i} of {path}: {e}", file=sys.stderr)
                sys.exit(1)
            cid = int(rec["corpus_id"])
            labels[cid] = LabelerLabel(
                corpus_id=cid,
                domain=str(rec.get("domain", "")),
                posture=str(rec.get("posture", "")),
            )
    return labels


# ---------------------------------------------------------------------------
# Data loading + cut helpers (same logic as oracle_two_axis_probe.py)
# ---------------------------------------------------------------------------


def identify_smoke_ids(corpus: list[CorpusEntry]) -> frozenset[int]:
    smoke_tds = {"update the docs", "implement the new module"}
    return frozenset(e.corpus_id for e in corpus if e.task_description in smoke_tds)


def apply_no_smoke_cut(
    corpus: list[CorpusEntry],
    labels: dict[int, GoldLabel],
    smoke_ids: frozenset[int],
) -> tuple[list[CorpusEntry], dict[int, GoldLabel]]:
    filtered = [e for e in corpus if e.corpus_id not in smoke_ids]
    filt_labels = {cid: lbl for cid, lbl in labels.items() if cid not in smoke_ids}
    return filtered, filt_labels


def _entry_to_context(entry: CorpusEntry) -> dict[str, Any]:
    return {
        "task_description": entry.task_description,
        "file_paths": list(entry.file_paths),
        "agent_mentions": list(entry.agent_mentions),
        "tool_mentions": list(entry.tool_mentions),
        "command_prefix": entry.command_prefix,
    }


# ---------------------------------------------------------------------------
# Routing systems (re-implemented inline, matching oracle_two_axis_probe.py)
# ---------------------------------------------------------------------------

@dataclass
class SystemResult:
    corpus_id: int
    decision: str
    agent: str | None
    confidence: float
    extras: dict[str, Any]


def run_lexical(entries: list[CorpusEntry]) -> list[SystemResult]:
    catalog = load_catalog(CATALOG_PATH)
    results: list[SystemResult] = []
    for entry in entries:
        ctx = _entry_to_context(entry)
        features = build_features(ctx)
        scored_agents, scored_skills = score_entries(catalog, features)
        decision_dict = decide(scored_agents, scored_skills, features, catalog)
        results.append(SystemResult(
            corpus_id=entry.corpus_id,
            decision=str(decision_dict.get("decision", "")),
            agent=decision_dict.get("agent"),
            confidence=float(decision_dict.get("confidence", 0.0)),
            extras={},
        ))
    return results


def run_oracle_domain_hard_gate(
    entries: list[CorpusEntry],
    gold: dict[int, GoldLabel],
) -> list[SystemResult]:
    """Oracle domain hard-gate (uses gold domain labels — upper bound for domain)."""
    catalog = load_catalog(CATALOG_PATH)
    results: list[SystemResult] = []
    for entry in entries:
        label = gold.get(entry.corpus_id)
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

        decision_dict = decide(gated_agents, scored_skills, features, catalog)
        results.append(SystemResult(
            corpus_id=entry.corpus_id,
            decision=str(decision_dict.get("decision", "")),
            agent=decision_dict.get("agent"),
            confidence=float(decision_dict.get("confidence", 0.0)),
            extras={"oracle_domain": oracle_domain},
        ))
    return results


def run_oracle_compose(
    entries: list[CorpusEntry],
    gold: dict[int, GoldLabel],
) -> list[SystemResult]:
    """Oracle two-axis Compose (gold domain + gold posture — the oracle ceiling)."""
    catalog = load_catalog(CATALOG_PATH)
    catalog_agent_names = {
        e.name for e in catalog
        if e.kind == "agent" and is_agent_routable(
            name=e.name, kind=e.kind, source=e.source, routable=e.routable
        )
    }
    results: list[SystemResult] = []
    for entry in entries:
        label = gold.get(entry.corpus_id)
        oracle_domain = label.domain if label else None
        oracle_posture = label.posture if label else None
        domain_for_lookup = oracle_domain if oracle_domain else "any"

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

        posture_routed = False
        agent_out: str | None = None
        decision_out = "advisory"
        confidence_out = 0.5

        if oracle_posture:
            preferred_agent = cell_map_lookup(domain_for_lookup, oracle_posture)
            gated_names = {se.entry.name for se in gated_agents}
            if preferred_agent and preferred_agent in gated_names and preferred_agent in catalog_agent_names:
                agent_out = preferred_agent
                decision_out = "delegate"
                confidence_out = 0.9
                posture_routed = True

        if not posture_routed:
            decision_dict = decide(gated_agents, scored_skills, features, catalog)
            agent_out = decision_dict.get("agent")
            decision_out = str(decision_dict.get("decision", ""))
            confidence_out = float(decision_dict.get("confidence", 0.0))

        results.append(SystemResult(
            corpus_id=entry.corpus_id,
            decision=decision_out,
            agent=agent_out,
            confidence=confidence_out,
            extras={"oracle_domain": oracle_domain, "oracle_posture": oracle_posture},
        ))
    return results


def run_real_label_compose(
    entries: list[CorpusEntry],
    labeler_labels: dict[int, LabelerLabel],
) -> list[SystemResult]:
    """Real-label two-axis Compose: same architecture as oracle Compose, but uses
    the LLM labeler's (possibly imperfect) domain + posture labels instead of gold.

    Entries the labeler did not cover fall back to lexical (no gate applied).
    """
    catalog = load_catalog(CATALOG_PATH)
    catalog_agent_names = {
        e.name for e in catalog
        if e.kind == "agent" and is_agent_routable(
            name=e.name, kind=e.kind, source=e.source, routable=e.routable
        )
    }
    results: list[SystemResult] = []
    for entry in entries:
        ll = labeler_labels.get(entry.corpus_id)
        real_domain = ll.domain if ll else None
        real_posture = ll.posture if ll else None

        # Normalize is_any → None so DOMAIN_AGENT_MAP.get() returns None (pass-through)
        if real_domain == "is_any":
            real_domain = None
        domain_for_lookup = real_domain if real_domain else "any"

        ctx = _entry_to_context(entry)
        features = build_features(ctx)
        scored_agents, scored_skills = score_entries(catalog, features)

        allowed_agents = DOMAIN_AGENT_MAP.get(real_domain)
        if allowed_agents is not None:
            gated_agents = [se for se in scored_agents if se.entry.name in allowed_agents]
            if not gated_agents:
                gated_agents = scored_agents
        else:
            gated_agents = scored_agents

        posture_routed = False
        agent_out: str | None = None
        decision_out = "advisory"
        confidence_out = 0.5

        if real_posture:
            preferred_agent = cell_map_lookup(domain_for_lookup, real_posture)
            gated_names = {se.entry.name for se in gated_agents}
            if preferred_agent and preferred_agent in gated_names and preferred_agent in catalog_agent_names:
                agent_out = preferred_agent
                decision_out = "delegate"
                confidence_out = 0.9
                posture_routed = True

        if not posture_routed:
            decision_dict = decide(gated_agents, scored_skills, features, catalog)
            agent_out = decision_dict.get("agent")
            decision_out = str(decision_dict.get("decision", ""))
            confidence_out = float(decision_dict.get("confidence", 0.0))

        results.append(SystemResult(
            corpus_id=entry.corpus_id,
            decision=decision_out,
            agent=agent_out,
            confidence=confidence_out,
            extras={
                "real_domain": real_domain,
                "real_posture": real_posture,
                "posture_routed": posture_routed,
                "labeler_covered": ll is not None,
            },
        ))
    return results


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def compute_rc(results: list[SystemResult], gold: dict[int, GoldLabel]) -> float:
    labeled = [r for r in results if r.corpus_id in gold]
    if not labeled:
        return float("nan")
    correct = sum(1 for r in labeled if r.agent == gold[r.corpus_id].gold_agent)
    return round(correct / len(labeled), 4)


def compute_cwr(results: list[SystemResult], gold: dict[int, GoldLabel]) -> float:
    delegates = [r for r in results if r.decision == "delegate" and r.corpus_id in gold]
    if not delegates:
        return float("nan")
    wrong = sum(1 for r in delegates if r.agent != gold[r.corpus_id].gold_agent)
    return round(wrong / len(delegates), 4)


def compute_decision_dist(results: list[SystemResult], gold: dict[int, GoldLabel]) -> dict[str, int]:
    labeled = [r for r in results if r.corpus_id in gold]
    counts: Counter[str] = Counter(r.decision for r in labeled)
    return dict(counts.most_common())


# ---------------------------------------------------------------------------
# Labeling accuracy metrics
# ---------------------------------------------------------------------------


def compute_labeling_accuracy(
    labeler_labels: dict[int, LabelerLabel],
    gold: dict[int, GoldLabel],
    no_smoke_ids: frozenset[int],
) -> dict[str, Any]:
    """Compute per-axis accuracy and confusion matrices for the no-smoke cut."""
    # Work on only no-smoke entries that exist in BOTH gold and labeler output
    common_ids = (set(gold.keys()) & set(labeler_labels.keys())) - no_smoke_ids  # note: no_smoke_ids are the EXCLUDED ids
    # Actually: no_smoke cut = entries NOT in smoke_ids. Let me redo:
    # no_smoke_ids passed in here ARE the smoke ids to exclude.
    in_scope = {
        cid for cid in gold.keys()
        if cid not in no_smoke_ids  # exclude smoke
    }
    covered_ids = in_scope & set(labeler_labels.keys())
    missing_ids = in_scope - set(labeler_labels.keys())
    extra_ids = set(labeler_labels.keys()) - in_scope

    domain_total = len(in_scope)
    domain_correct = 0
    domain_confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))  # gold→pred→count
    posture_total = len(in_scope)
    posture_correct = 0
    posture_confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for cid in in_scope:
        g = gold[cid]
        ll = labeler_labels.get(cid)
        if ll is None:
            continue  # missing entry — treat as wrong (counted in coverage report)

        # Domain axis: normalize gold is_any
        gold_domain = "is_any" if g.is_any else (g.domain or "is_any")
        pred_domain = ll.domain

        if pred_domain == gold_domain:
            domain_correct += 1
        domain_confusion[gold_domain][pred_domain] += 1

        # Posture axis
        gold_posture = g.posture
        pred_posture = ll.posture

        if pred_posture == gold_posture:
            posture_correct += 1
        posture_confusion[gold_posture][pred_posture] += 1

    # Accuracy denominators include all in-scope, including missing (treated as wrong)
    n_in_scope = len(in_scope)
    return {
        "n_in_scope": n_in_scope,
        "n_covered": len(covered_ids),
        "n_missing": len(missing_ids),
        "n_extra": len(extra_ids),
        "coverage_rate": round(len(covered_ids) / n_in_scope, 4) if n_in_scope else 0.0,
        "domain_accuracy": round(domain_correct / n_in_scope, 4) if n_in_scope else 0.0,
        "posture_accuracy": round(posture_correct / n_in_scope, 4) if n_in_scope else 0.0,
        "domain_accuracy_covered": round(domain_correct / len(covered_ids), 4) if covered_ids else 0.0,
        "posture_accuracy_covered": round(posture_correct / len(covered_ids), 4) if covered_ids else 0.0,
        "domain_confusion": {g: dict(preds) for g, preds in domain_confusion.items()},
        "posture_confusion": {g: dict(preds) for g, preds in posture_confusion.items()},
        "missing_ids": sorted(missing_ids),
        "extra_ids": sorted(extra_ids),
    }


# ---------------------------------------------------------------------------
# Pretty-print helpers
# ---------------------------------------------------------------------------

def _bar(val: float, width: int = 20) -> str:
    if val != val:  # nan
        return " " * width
    filled = int(round(val * width))
    return "#" * filled + "." * (width - filled)


def print_confusion(title: str, confusion: dict[str, dict[str, int]]) -> None:
    """Print a compact confusion table: rows = gold, cols = predictions."""
    gold_classes = sorted(confusion.keys())
    all_preds: set[str] = set()
    for preds in confusion.values():
        all_preds.update(preds.keys())
    pred_classes = sorted(all_preds)

    col_w = max(len(c) for c in pred_classes + ["GOLD \\ PRED"]) + 1

    print(f"\n{title}")
    header = f"  {'GOLD \\ PRED':<{col_w}}" + "".join(f"{c:>{col_w}}" for c in pred_classes) + f"  {'TOTAL':>6}"
    print(header)
    print("  " + "-" * (len(header) - 2))

    for g_cls in gold_classes:
        row_total = sum(confusion[g_cls].values())
        correct_n = confusion[g_cls].get(g_cls, 0)
        row = f"  {g_cls:<{col_w}}"
        for p_cls in pred_classes:
            n = confusion[g_cls].get(p_cls, 0)
            marker = f"[{n}]" if p_cls == g_cls else f" {n} "
            row += f"{marker:>{col_w}}"
        row += f"  {row_total:>6}  (correct: {correct_n}/{row_total})"
        print(row)


def print_section(title: str) -> None:
    print()
    print("=" * 80)
    print(f"  {title}")
    print("=" * 80)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    # Ensure UTF-8 output on Windows where the default codec may be cp1252
    import io
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    else:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Score blind LLM labeling accuracy (#358)")
    parser.add_argument(
        "--labeler",
        type=pathlib.Path,
        default=DEFAULT_LABELER_OUTPUT,
        help="Path to labeler-output.jsonl (default: .tmp/labeler-output.jsonl)",
    )
    args = parser.parse_args()

    print("=== Blind LLM Labeling Accuracy Scorer (#358) ===")
    print(f"  Corpus:          {CORPUS_PATH}")
    print(f"  Gold labels:     {GOLD_LABELS_PATH}")
    print(f"  Labeler output:  {args.labeler}")
    print(f"  Catalog:         {CATALOG_PATH}")

    # ------------------------------------------------------------------
    # Load data
    # ------------------------------------------------------------------
    corpus = load_corpus(CORPUS_PATH)
    gold = load_labels(GOLD_LABELS_PATH)
    labeler_labels = load_labeler_output(args.labeler)
    smoke_ids = identify_smoke_ids(corpus)

    no_smoke_corpus, no_smoke_gold = apply_no_smoke_cut(corpus, gold, smoke_ids)
    no_smoke_id_set = frozenset(e.corpus_id for e in no_smoke_corpus)  # IDs in no-smoke cut

    print(f"\n  Total corpus: {len(corpus)}  |  no-smoke cut: {len(no_smoke_corpus)}")
    print(f"  Smoke probe IDs excluded: {len(smoke_ids)}")
    print(f"  Labeler output entries: {len(labeler_labels)}")

    # ------------------------------------------------------------------
    # Coverage check
    # ------------------------------------------------------------------
    acc = compute_labeling_accuracy(labeler_labels, gold, smoke_ids)  # smoke_ids are the EXCLUDED ids

    print_section("1. LABELING COVERAGE")
    print(f"  No-smoke entries in scope: {acc['n_in_scope']}")
    print(f"  Covered by labeler:        {acc['n_covered']}  ({acc['coverage_rate']:.1%})")
    if acc["n_missing"]:
        print(f"  MISSING from labeler:      {acc['n_missing']}  ids={acc['missing_ids'][:10]}{'...' if len(acc['missing_ids']) > 10 else ''}")
    if acc["n_extra"]:
        print(f"  Extra (not in no-smoke):   {acc['n_extra']}  ids={acc['extra_ids'][:10]}{'...' if len(acc['extra_ids']) > 10 else ''}")

    # ------------------------------------------------------------------
    # Per-axis labeling accuracy
    # ------------------------------------------------------------------
    print_section("2. LABELING ACCURACY (no-smoke cut)")
    print(f"  {'Axis':<12} {'Accuracy (all in-scope)':>24}  {'Accuracy (covered only)':>24}")
    print(f"  {'-'*60}")
    print(f"  {'domain':<12} {acc['domain_accuracy']:>24.4f}  {acc['domain_accuracy_covered']:>24.4f}")
    print(f"  {'posture':<12} {acc['posture_accuracy']:>24.4f}  {acc['posture_accuracy_covered']:>24.4f}")
    print()
    print(f"  Note: 'all in-scope' denominator = {acc['n_in_scope']} (missing entries counted as wrong)")
    print(f"        'covered only' denominator  = {acc['n_covered']} (only labeled entries)")

    # ------------------------------------------------------------------
    # Confusion matrices
    # ------------------------------------------------------------------
    print_section("3. CONFUSION MATRICES")
    print_confusion("Domain confusion  (rows=gold, cols=predicted, [n]=correct):",
                    acc["domain_confusion"])
    print_confusion("\nPosture confusion (rows=gold, cols=predicted, [n]=correct):",
                    acc["posture_confusion"])

    # Which gold classes get mislabeled — top misclassifications
    print("\n  Top domain mislabelings (gold -> predicted, n>0, excluding correct):")
    mismatches: list[tuple[int, str, str]] = []
    for g_cls, preds in acc["domain_confusion"].items():
        for p_cls, n in preds.items():
            if p_cls != g_cls and n > 0:
                mismatches.append((n, g_cls, p_cls))
    for n, g_cls, p_cls in sorted(mismatches, reverse=True):
        print(f"    {g_cls} -> {p_cls}: {n}")

    print("\n  Top posture mislabelings (gold -> predicted, n>0, excluding correct):")
    posture_mismatches: list[tuple[int, str, str]] = []
    for g_cls, preds in acc["posture_confusion"].items():
        for p_cls, n in preds.items():
            if p_cls != g_cls and n > 0:
                posture_mismatches.append((n, g_cls, p_cls))
    for n, g_cls, p_cls in sorted(posture_mismatches, reverse=True):
        print(f"    {g_cls} -> {p_cls}: {n}")

    # ------------------------------------------------------------------
    # Routing systems comparison
    # ------------------------------------------------------------------
    print_section("4. ROUTING SYSTEMS COMPARISON (no-smoke cut, n=109)")
    print("  Running all 4 routing systems...")
    print()

    # Run systems
    lex_results = run_lexical(no_smoke_corpus)
    ora_domain = run_oracle_domain_hard_gate(no_smoke_corpus, no_smoke_gold)
    ora_compose = run_oracle_compose(no_smoke_corpus, no_smoke_gold)
    real_compose = run_real_label_compose(no_smoke_corpus, labeler_labels)

    # Metrics for each
    systems = [
        ("Lexical",                   lex_results),
        ("Oracle domain-only",         ora_domain),
        ("Oracle Compose(d+p)",        ora_compose),
        ("Real-label Compose(d+p)",    real_compose),
    ]

    rows: list[tuple[str, float, float, dict[str, int]]] = []
    for name, results in systems:
        rc = compute_rc(results, no_smoke_gold)
        cwr = compute_cwr(results, no_smoke_gold)
        dist = compute_decision_dist(results, no_smoke_gold)
        rows.append((name, rc, cwr, dist))

    # Header
    col0 = 26
    print(f"  {'System':<{col0}} {'RC':>8} {'dRC vs lex':>12} {'CWR':>8} {'dCWR vs lex':>13}  Decisions")
    print(f"  {'-' * (col0 + 8 + 12 + 8 + 13 + 20)}")

    lex_rc  = rows[0][1]
    lex_cwr = rows[0][2]
    for name, rc, cwr, dist in rows:
        drc  = f"{rc  - lex_rc:+.4f}" if rc == rc and lex_rc == lex_rc else "   n/a"
        dcwr = f"{cwr - lex_cwr:+.4f}" if cwr == cwr and lex_cwr == lex_cwr else "   n/a"
        dist_str = "  ".join(f"{k}={v}" for k, v in sorted(dist.items(), key=lambda x: -x[1]))
        print(f"  {name:<{col0}} {rc:>8.4f} {drc:>12} {cwr:>8.4f} {dcwr:>13}  {dist_str}")

    # Key comparison: real-label vs oracle ceiling
    print()
    print("  Key comparison: real-label Compose vs oracle Compose (the floor-vs-ceiling gap):")
    ora_rc  = rows[2][1]
    ora_cwr = rows[2][2]
    real_rc  = rows[3][1]
    real_cwr = rows[3][2]
    if ora_rc == ora_rc and real_rc == real_rc:
        print(f"    Oracle Compose(d+p) RC:    {ora_rc:.4f}")
        print(f"    Real-label Compose(d+p) RC: {real_rc:.4f}")
        print(f"    Gap (oracle - real):        {ora_rc - real_rc:+.4f}  ({(ora_rc-real_rc)*109:.1f} entries lost to label noise on n=109)")
        print(f"    CWR oracle: {ora_cwr:.4f}  |  CWR real: {real_cwr:.4f}  |  delta: {real_cwr - ora_cwr:+.4f}")

    # Fraction of oracle headroom recovered
    lex_rc_ = rows[0][1]
    if (ora_rc - lex_rc_) > 0 and (real_rc - lex_rc_) >= 0:
        fraction = (real_rc - lex_rc_) / (ora_rc - lex_rc_)
        print(f"\n    Real-label Compose recovers {fraction:.1%} of the oracle RC headroom above lexical")
        print(f"    ({real_rc:.4f} - {lex_rc_:.4f}) / ({ora_rc:.4f} - {lex_rc_:.4f}) = {fraction:.3f}")

    # ------------------------------------------------------------------
    # Per-domain routing breakdown (real-label compose vs oracle)
    # ------------------------------------------------------------------
    print_section("5. PER-DOMAIN RC BREAKDOWN (no-smoke cut, real-label vs oracle)")

    def domain_rc_breakdown(results: list[SystemResult], gold: dict[int, GoldLabel]) -> dict[str, tuple[int, float]]:
        by_domain: dict[str, list[bool]] = defaultdict(list)
        for r in results:
            g = gold.get(r.corpus_id)
            if g is None:
                continue
            d = "is_any" if g.is_any else (g.domain or "is_any")
            by_domain[d].append(r.agent == g.gold_agent)
        return {
            d: (len(v), round(sum(v) / len(v), 4) if v else float("nan"))
            for d, v in sorted(by_domain.items())
        }

    lex_bd   = domain_rc_breakdown(lex_results, no_smoke_gold)
    ora_d_bd = domain_rc_breakdown(ora_domain, no_smoke_gold)
    ora_c_bd = domain_rc_breakdown(ora_compose, no_smoke_gold)
    real_bd  = domain_rc_breakdown(real_compose, no_smoke_gold)

    domains = sorted(set(list(lex_bd.keys()) + list(real_bd.keys())))
    col_d = 14
    print(f"\n  {'Domain':<{col_d}} {'n':>4} {'Lexical':>9} {'OracleDom':>10} {'OracleComp':>11} {'RealComp':>10}  {'RealComp-OraComp':>18}")
    print(f"  {'-' * (col_d + 4 + 9 + 10 + 11 + 10 + 20)}")
    for d in domains:
        n, lex_rc_d  = lex_bd.get(d, (0, float("nan")))
        _,  ora_d_rc = ora_d_bd.get(d, (0, float("nan")))
        _,  ora_c_rc = ora_c_bd.get(d, (0, float("nan")))
        _,  real_rc_d = real_bd.get(d, (0, float("nan")))
        delta = real_rc_d - ora_c_rc if (real_rc_d == real_rc_d and ora_c_rc == ora_c_rc) else float("nan")
        delta_str = f"{delta:+.4f}" if delta == delta else "   n/a"
        print(f"  {d:<{col_d}} {n:>4} {lex_rc_d:>9.4f} {ora_d_rc:>10.4f} {ora_c_rc:>11.4f} {real_rc_d:>10.4f}  {delta_str:>18}")

    # ------------------------------------------------------------------
    # Where real-label compose gains/loses vs oracle compose
    # ------------------------------------------------------------------
    print_section("6. ENTRY-LEVEL DELTA: real-label Compose vs oracle Compose")

    ora_map  = {r.corpus_id: r for r in ora_compose}
    real_map = {r.corpus_id: r for r in real_compose}

    gains: list[tuple[int, str, str, str, str, str, str, str]] = []
    losses: list[tuple[int, str, str, str, str, str, str, str]] = []
    for cid, g in no_smoke_gold.items():
        ora_r  = ora_map.get(cid)
        real_r = real_map.get(cid)
        if not ora_r or not real_r:
            continue
        ora_ok  = ora_r.agent == g.gold_agent
        real_ok = real_r.agent == g.gold_agent
        ll = labeler_labels.get(cid)
        real_domain = ll.domain if ll else "?"
        real_posture = ll.posture if ll else "?"
        entry_tuple = (cid, g.gold_agent, ora_r.agent or "?", real_r.agent or "?",
                       g.domain or "is_any", g.posture, real_domain, real_posture)
        if real_ok and not ora_ok:
            gains.append(entry_tuple)
        elif ora_ok and not real_ok:
            losses.append(entry_tuple)

    print(f"\n  Real-label gains over oracle ({len(gains)} entries — oracle was wrong, real-label was right):")
    if gains:
        print(f"  {'id':>6}  {'gold_agent':<16} {'ora->':<16} {'real->':<16} {'gold_dom':<14} {'gold_pos':<10} {'pred_dom':<14} {'pred_pos'}")
        for t in gains:
            print(f"  {t[0]:>6}  {t[1]:<16} {t[2]:<16} {t[3]:<16} {t[4]:<14} {t[5]:<10} {t[6]:<14} {t[7]}")
    else:
        print("  (none)")

    print(f"\n  Real-label losses vs oracle ({len(losses)} entries — oracle was right, real-label was wrong):")
    if losses:
        print(f"  {'id':>6}  {'gold_agent':<16} {'ora->':<16} {'real->':<16} {'gold_dom':<14} {'gold_pos':<10} {'pred_dom':<14} {'pred_pos'}")
        for t in losses:
            print(f"  {t[0]:>6}  {t[1]:<16} {t[2]:<16} {t[3]:<16} {t[4]:<14} {t[5]:<10} {t[6]:<14} {t[7]}")
    else:
        print("  (none)")

    # ------------------------------------------------------------------
    # Summary verdict
    # ------------------------------------------------------------------
    print_section("7. SUMMARY")
    print(f"  Labeling accuracy  — domain:  {acc['domain_accuracy']:.4f} ({acc['domain_accuracy']*100:.1f}%)")
    print(f"  Labeling accuracy  — posture: {acc['posture_accuracy']:.4f} ({acc['posture_accuracy']*100:.1f}%)")
    print()
    print(f"  {'System':<30} {'RC':>8} {'CWR':>8}")
    print(f"  {'-'*48}")
    for name, rc, cwr, _ in rows:
        print(f"  {name:<30} {rc:>8.4f} {cwr:>8.4f}")
    print()
    if ora_rc == ora_rc and real_rc == real_rc and lex_rc_ == lex_rc_:
        headroom_fraction = (real_rc - lex_rc_) / (ora_rc - lex_rc_) if (ora_rc - lex_rc_) > 0 else float("nan")
        print(f"  Real-label Compose recovers {headroom_fraction:.1%} of oracle headroom above lexical")
        if real_rc > lex_rc_:
            print(f"  Real-label BEATS lexical by +{real_rc - lex_rc_:.4f} RC pts on no-smoke cut")
        elif real_rc < lex_rc_:
            print(f"  Real-label UNDERPERFORMS lexical by {real_rc - lex_rc_:.4f} RC pts — label noise dominates")
        else:
            print(f"  Real-label TIES lexical — net label noise cancels any gain")


if __name__ == "__main__":
    main()
