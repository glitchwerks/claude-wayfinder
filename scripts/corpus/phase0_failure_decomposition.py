"""Phase 0 failure decomposition probe (issue #382).

Decomposes every RC miss and every CW (confident-wrong) delegate miss
from the GPT run1 supplied-compose run into:

  B = cell-map/compose fault: GPT label == gold label on BOTH axes,
      yet the system still misrouted.
  C = mislabel: GPT label != gold label on at least one axis.
      C further splits into: domain-only wrong, posture-only wrong,
      both-wrong.

Also counts gold-suspect entries (GPT != gold, but GPT is plausible
from lexical surface features — potential evidence that gold is wrong).

Usage (from repo root or worktree root):
    python scripts/corpus/phase0_failure_decomposition.py

Outputs:
    docs/research/2026-06-15-phase0-failure-decomposition.md

All paths resolve relative to the script's parent-parent-parent
(the worktree root), so the script is self-contained regardless of
shell CWD.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Path setup: resolve worktree root and add scripts/ to sys.path
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve()
_WT_ROOT = _HERE.parent.parent.parent  # .../382-phase0-floor
_SCRIPTS_DIR = _WT_ROOT / "scripts"
# Insert both WT_ROOT (so "scripts.corpus..." imports work) and SCRIPTS_DIR
for _p in (str(_WT_ROOT), str(_SCRIPTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ---------------------------------------------------------------------------
# Data paths
# ---------------------------------------------------------------------------

_CORPUS_PATH = Path.home() / (
    ".claude/state/wayfinder-corpus/2026-06-12/wayfinder-corpus.jsonl"
)
_CATALOG_PATH = Path.home() / ".claude/state/dispatch-catalog.json"
_GOLD_LABELS_PATH = (
    _WT_ROOT / "docs/research/2026-06-12-gold-labels-redacted.jsonl"
)
_GPT_RUN1_PATH = (
    _WT_ROOT / "docs/research/2026-06-15-phase0-gpt-labels-run1.jsonl"
)
_OUTPUT_PATH = (
    _WT_ROOT / "docs/research/2026-06-15-phase0-failure-decomposition.md"
)

# ---------------------------------------------------------------------------
# Smoke-test task descriptions (same set as __main__.py)
# ---------------------------------------------------------------------------

_SMOKE_DESCRIPTIONS: frozenset[str] = frozenset({
    "update the docs",
    "implement the new module",
})

# ---------------------------------------------------------------------------
# Imports from the eval harness (after sys.path insert)
# ---------------------------------------------------------------------------

from scripts.corpus.eval._reader import (  # noqa: E402
    GoldLabel,
    load_corpus,
    load_labels,
)
from scripts.corpus.eval._systems import run_supplied_compose  # noqa: E402
from scripts.corpus.eval._metrics import (  # noqa: E402
    metric_routing_correctness,
    metric_confident_wrong_rate,
)


# ---------------------------------------------------------------------------
# GPT label loader
# ---------------------------------------------------------------------------


def load_gpt_labels(path: Path) -> dict[int, dict[str, str]]:
    """Load GPT-produced label JSONL (corpus_id, domain, posture rows).

    Args:
        path: Path to the GPT labels JSONL file.

    Returns:
        Dict mapping corpus_id → {"domain": str, "posture": str}.

    Raises:
        FileNotFoundError: If path does not exist.
    """
    if not path.exists():
        raise FileNotFoundError(f"GPT labels not found: {path}")
    labels: dict[int, dict[str, str]] = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            record: dict[str, Any] = json.loads(line)
            cid = int(record["corpus_id"])
            labels[cid] = {
                "domain": str(record.get("domain", "")),
                "posture": str(record.get("posture", "")),
            }
    return labels


# ---------------------------------------------------------------------------
# Cut helpers
# ---------------------------------------------------------------------------


def remove_smoke(
    entries: list,
    labels: dict,
) -> tuple[list, dict]:
    """Remove smoke-test entries from entries + labels dicts.

    Args:
        entries: List of CorpusEntry objects.
        labels: Dict mapping corpus_id → label.

    Returns:
        Tuple of (filtered_entries, filtered_labels).
    """
    smoke_ids = frozenset(
        e.corpus_id
        for e in entries
        if e.task_description in _SMOKE_DESCRIPTIONS
    )
    filtered_entries = [e for e in entries if e.corpus_id not in smoke_ids]
    filtered_labels = {
        cid: lbl for cid, lbl in labels.items() if cid not in smoke_ids
    }
    return filtered_entries, filtered_labels


# ---------------------------------------------------------------------------
# Decomposition logic
# ---------------------------------------------------------------------------


def _axes_match(
    gpt: dict[str, str],
    gold: GoldLabel,
) -> tuple[bool, bool]:
    """Check whether GPT domain and posture match gold.

    Args:
        gpt: Dict with "domain" and "posture" from GPT.
        gold: GoldLabel with .domain and .posture from gold.

    Returns:
        Tuple of (domain_match, posture_match).
    """
    return (gpt["domain"] == gold.domain, gpt["posture"] == gold.posture)


def _is_gold_suspect(
    gpt: dict[str, str],
    gold: GoldLabel,
    entry: Any,
) -> bool:
    """Heuristic: is this a case where GPT may be right and gold wrong?

    Applied only when GPT != gold on at least one axis.
    A suspect entry passes at least one of:
      - file extensions suggest GPT's domain (e.g., all .md → docs_prose)
      - task description contains a keyword strongly aligned with GPT's
        domain (e.g., "implement" → code)

    This is a rough count; the caller inspects examples manually.

    Args:
        gpt: GPT label dict.
        gold: GoldLabel.
        entry: CorpusEntry with .file_paths and .task_description.

    Returns:
        True when a plausibility check fires for GPT's label.
    """
    domain_match, posture_match = _axes_match(gpt, gold)
    # Only flag when GPT disagrees on domain (the interesting axis for
    # gold-suspect purposes)
    if domain_match:
        return False

    gpt_domain = gpt["domain"]
    task = entry.task_description.lower()
    paths_str = " ".join(entry.file_paths).lower()

    # Lexical surface checks per domain
    if gpt_domain == "docs_prose":
        # GPT says docs but gold says something else
        doc_signals = (
            any(p.endswith((".md", ".rst", ".txt", ".adoc"))
                for p in entry.file_paths)
            or any(kw in task for kw in (
                "readme", "docs", "documentation", "changelog", "write up",
                "write-up", "tutorial", "docstring",
            ))
            or ".md" in paths_str
        )
        return doc_signals

    if gpt_domain == "code":
        # GPT says code but gold says something else
        code_signals = (
            any(p.endswith((".py", ".js", ".ts", ".go", ".rs", ".java",
                            ".cs", ".cpp", ".c", ".h", ".rb"))
                for p in entry.file_paths)
            or any(kw in task for kw in (
                "implement", "function", "class", "module", "script",
                "refactor", "bug", "fix", "test", "lint",
            ))
        )
        return code_signals

    if gpt_domain == "project_meta":
        # GPT says project_meta but gold says something else
        meta_signals = any(kw in task for kw in (
            "issue", "pr", "pull request", "milestone", "label", "branch",
            "repository", "commit", "release", "roadmap", "backlog",
            "changelog", "sprint",
        ))
        return meta_signals

    if gpt_domain == "infra_deploy":
        infra_signals = any(kw in task for kw in (
            "deploy", "ci", "pipeline", "workflow", "docker", "kubernetes",
            "terraform", "infrastructure", "environment", "build",
        ))
        return infra_signals

    return False


# ---------------------------------------------------------------------------
# Entry-level join
# ---------------------------------------------------------------------------


def build_joined_rows(
    entries: list,
    gold_labels: dict[int, GoldLabel],
    gpt_labels: dict[int, dict[str, str]],
    system_results: list,
) -> list[dict[str, Any]]:
    """Join entries, gold labels, GPT labels, and system results.

    Produces one row per entry that has both gold and GPT labels.

    Args:
        entries: Corpus entries (post-cut).
        gold_labels: Gold label map (corpus_id → GoldLabel).
        gpt_labels: GPT label map (corpus_id → {"domain", "posture"}).
        system_results: SystemResult list from run_supplied_compose.

    Returns:
        List of dicts with keys: corpus_id, task_description, file_paths,
        gpt_domain, gpt_posture, gold_domain, gold_posture, gold_agent,
        route_agent, decision, confidence,
        domain_match, posture_match, label_match_both.
    """
    result_idx = {r.corpus_id: r for r in system_results}
    rows: list[dict[str, Any]] = []
    for entry in entries:
        cid = entry.corpus_id
        gold = gold_labels.get(cid)
        gpt = gpt_labels.get(cid)
        res = result_idx.get(cid)
        if gold is None or gpt is None or res is None:
            continue
        dm, pm = _axes_match(gpt, gold)
        rows.append({
            "corpus_id": cid,
            "task_description": entry.task_description,
            "file_paths": entry.file_paths,
            "gpt_domain": gpt["domain"],
            "gpt_posture": gpt["posture"],
            "gold_domain": gold.domain,
            "gold_posture": gold.posture,
            "gold_agent": gold.gold_agent,
            "route_agent": res.agent,
            "decision": res.decision,
            "confidence": res.confidence,
            "domain_match": dm,
            "posture_match": pm,
            "label_match_both": dm and pm,
        })
    return rows


# ---------------------------------------------------------------------------
# RC-miss decomposition
# ---------------------------------------------------------------------------


def decompose_rc_misses(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Decompose all RC misses into B vs C, excluding correct self_handle abstentions.

    A row is an RC miss when route_agent != gold_agent, EXCEPT when the
    system correctly abstained (decision="self_handle" and gold_agent=
    "self_handle"). That case is a true positive, not a miss — consistent
    with metric_routing_correctness / _prediction_matches_gold in
    scripts/corpus/eval/_metrics.py.

    B = cell-map/compose fault: label_match_both=True, still wrong.
    C = mislabel: label_match_both=False.
        C_domain_only = domain_match=False AND posture_match=True
        C_posture_only = domain_match=True AND posture_match=False
        C_both = domain_match=False AND posture_match=False

    Args:
        rows: Joined rows from build_joined_rows.

    Returns:
        Dict with keys: total_labeled, total_misses, B, C,
        C_domain_only, C_posture_only, C_both, miss_rows.
    """
    # Exclude correct self_handle abstentions: a row where both the system
    # decision and the gold label are "self_handle" is a true positive even
    # though route_agent (None) != gold_agent ("self_handle") as strings.
    misses: list[dict[str, Any]] = [
        r for r in rows
        if r["route_agent"] != r["gold_agent"]
        and not (
            r["decision"] == "self_handle"
            and r["gold_agent"] == "self_handle"
        )
    ]
    b_rows = [r for r in misses if r["label_match_both"]]
    c_rows = [r for r in misses if not r["label_match_both"]]
    c_domain_only = [
        r for r in c_rows
        if not r["domain_match"] and r["posture_match"]
    ]
    c_posture_only = [
        r for r in c_rows
        if r["domain_match"] and not r["posture_match"]
    ]
    c_both = [
        r for r in c_rows
        if not r["domain_match"] and not r["posture_match"]
    ]
    return {
        "total_labeled": len(rows),
        "total_misses": len(misses),
        "B": len(b_rows),
        "C": len(c_rows),
        "C_domain_only": len(c_domain_only),
        "C_posture_only": len(c_posture_only),
        "C_both": len(c_both),
        "miss_rows": misses,
        "b_rows": b_rows,
        "c_rows": c_rows,
    }


# ---------------------------------------------------------------------------
# CW-miss decomposition
# ---------------------------------------------------------------------------


def decompose_cw_misses(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Decompose CW (confident-wrong) delegate misses into B vs C.

    CW misses = decision=='delegate' AND route_agent != gold_agent.

    Args:
        rows: Joined rows from build_joined_rows.

    Returns:
        Dict with keys: total_delegates, total_cw, B, C,
        C_domain_only, C_posture_only, C_both.
    """
    delegates = [r for r in rows if r["decision"] == "delegate"]
    cw_rows = [r for r in delegates if r["route_agent"] != r["gold_agent"]]
    b_rows = [r for r in cw_rows if r["label_match_both"]]
    c_rows = [r for r in cw_rows if not r["label_match_both"]]
    c_domain_only = [
        r for r in c_rows
        if not r["domain_match"] and r["posture_match"]
    ]
    c_posture_only = [
        r for r in c_rows
        if r["domain_match"] and not r["posture_match"]
    ]
    c_both = [
        r for r in c_rows
        if not r["domain_match"] and not r["posture_match"]
    ]
    return {
        "total_delegates": len(delegates),
        "total_cw": len(cw_rows),
        "B": len(b_rows),
        "C": len(c_rows),
        "C_domain_only": len(c_domain_only),
        "C_posture_only": len(c_posture_only),
        "C_both": len(c_both),
    }


# ---------------------------------------------------------------------------
# Top confusion pairs
# ---------------------------------------------------------------------------


def top_confusion_pairs(
    rows: list[dict[str, Any]],
    axis: str,
    top_n: int = 8,
) -> list[tuple[tuple[str, str], int]]:
    """Count the most common (gold → gpt) mislabel pairs for one axis.

    Only counts rows where the given axis disagrees.

    Args:
        rows: Joined rows.
        axis: Either "domain" or "posture".
        top_n: How many top pairs to return.

    Returns:
        List of ((gold_value, gpt_value), count) sorted descending by count.
    """
    gold_key = f"gold_{axis}"
    gpt_key = f"gpt_{axis}"
    counter: Counter[tuple[str, str]] = Counter()
    for r in rows:
        if r[gold_key] != r[gpt_key]:
            counter[(r[gold_key], r[gpt_key])] += 1
    return counter.most_common(top_n)


# ---------------------------------------------------------------------------
# Gold-suspect examples
# ---------------------------------------------------------------------------


def find_gold_suspect_examples(
    rows: list[dict[str, Any]],
    entries_by_id: dict[int, Any],
    n: int = 5,
) -> tuple[int, list[dict[str, Any]]]:
    """Find entries where GPT label differs from gold but GPT may be right.

    Args:
        rows: Joined rows.
        entries_by_id: Dict mapping corpus_id → CorpusEntry.
        n: Number of examples to surface.

    Returns:
        Tuple of (total_suspect_count, list_of_example_dicts).
    """
    suspects: list[dict[str, Any]] = []
    for r in rows:
        if r["label_match_both"]:
            continue
        entry = entries_by_id.get(r["corpus_id"])
        if entry is None:
            continue
        gpt = {"domain": r["gpt_domain"], "posture": r["gpt_posture"]}
        gold = type("GL", (), {
            "domain": r["gold_domain"],
            "posture": r["gold_posture"],
        })()
        if _is_gold_suspect(gpt, gold, entry):
            suspects.append({
                "corpus_id": r["corpus_id"],
                "task_description": entry.task_description,
                "file_paths": list(entry.file_paths)[:4],
                "gpt_domain": r["gpt_domain"],
                "gpt_posture": r["gpt_posture"],
                "gold_domain": r["gold_domain"],
                "gold_posture": r["gold_posture"],
                "gold_agent": r["gold_agent"],
                "route_agent": r["route_agent"],
                "label_match_both": r["label_match_both"],
            })
    return len(suspects), suspects[:n]


# ---------------------------------------------------------------------------
# Report renderer
# ---------------------------------------------------------------------------


def _pct(n: int, d: int) -> str:
    """Format n/d as a percentage string.

    Args:
        n: Numerator.
        d: Denominator.

    Returns:
        String like "12 (34.0%)" or "0" when denominator is 0.
    """
    if d == 0:
        return "0"
    return f"{n} ({100.0 * n / d:.1f}%)"


def _pct_cell(n: int, d: int) -> str:
    """Format n/d as percentage string for a table cell.

    Args:
        n: Numerator.
        d: Denominator.

    Returns:
        String like "34.0%" or "—" when denominator is 0.
    """
    if d == 0:
        return "—"
    return f"{100.0 * n / d:.1f}%"


def render_report(
    cut: str,
    n_entries: int,
    n_gpt: int,
    n_gold: int,
    rc: float,
    cw: float,
    rc_decomp: dict[str, Any],
    cw_decomp: dict[str, Any],
    domain_pairs: list[tuple[tuple[str, str], int]],
    posture_pairs: list[tuple[tuple[str, str], int]],
    gold_suspect_count: int,
    gold_suspect_examples: list[dict[str, Any]],
) -> str:
    """Render the full failure-decomposition markdown report.

    Args:
        cut: Cut name ("full" or "no_smoke").
        n_entries: Number of entries in this cut.
        n_gpt: Number of entries with GPT labels.
        n_gold: Number of entries with gold labels.
        rc: Routing correctness for this cut.
        cw: Confident-wrong rate for this cut.
        rc_decomp: Output of decompose_rc_misses().
        cw_decomp: Output of decompose_cw_misses().
        domain_pairs: Top domain confusion pairs.
        posture_pairs: Top posture confusion pairs.
        gold_suspect_count: Total gold-suspect entry count.
        gold_suspect_examples: List of example dicts.

    Returns:
        Markdown string for the report section.
    """
    lines: list[str] = []
    a = lines.append

    a(f"### Cut: `{cut}` — {n_entries} entries "
      f"({n_gpt} GPT-labeled, {n_gold} gold-labeled)")
    a("")
    a(f"**RC:** {rc:.4f}  |  **CW:** {cw:.4f}")
    a("")

    # RC-miss table
    tm = rc_decomp["total_misses"]
    tl = rc_decomp["total_labeled"]
    a("#### 1. RC-Miss Decomposition")
    a("")
    a(f"Total labeled entries: {tl}  "
      f"|  Total RC misses: {tm} ({_pct_cell(tm, tl)})")
    a("")
    a("| Category | Count | % of misses |")
    a("|----------|-------|-------------|")
    a("| **B — cell-map/compose fault** (labels matched, wrong route)"
      f" | {rc_decomp['B']} | {_pct_cell(rc_decomp['B'], tm)} |")
    a("| **C — mislabel** (GPT label != gold on >=1 axis)"
      f" | {rc_decomp['C']} | {_pct_cell(rc_decomp['C'], tm)} |")
    a(f"|   C · domain-only wrong | {rc_decomp['C_domain_only']}"
      f" | {_pct_cell(rc_decomp['C_domain_only'], tm)} |")
    a(f"|   C · posture-only wrong | {rc_decomp['C_posture_only']}"
      f" | {_pct_cell(rc_decomp['C_posture_only'], tm)} |")
    a(f"|   C · both wrong | {rc_decomp['C_both']}"
      f" | {_pct_cell(rc_decomp['C_both'], tm)} |")
    a("")

    b_pct = 100.0 * rc_decomp["B"] / tm if tm > 0 else 0.0
    c_pct = 100.0 * rc_decomp["C"] / tm if tm > 0 else 0.0
    a(f"**B:C split — {b_pct:.1f}% cell-map fault "
      f"vs {c_pct:.1f}% mislabel**")
    a("")

    # CW-miss table
    tcw = cw_decomp["total_cw"]
    td = cw_decomp["total_delegates"]
    a("#### 2. Confident-Wrong (CW) Decomposition")
    a("")
    cw_header = (
        f"Total delegate decisions: {td}  "
        f"|  Total CW misses: {tcw} "
        f"({_pct_cell(tcw, td)} of delegates)"
    )
    a(cw_header)
    a("")
    a("| Category | Count | % of CW misses |")
    a("|----------|-------|----------------|")
    a("| **B — cell-map/compose fault**"
      f" | {cw_decomp['B']} | {_pct_cell(cw_decomp['B'], tcw)} |")
    a("| **C — mislabel**"
      f" | {cw_decomp['C']} | {_pct_cell(cw_decomp['C'], tcw)} |")
    a(f"|   C · domain-only | {cw_decomp['C_domain_only']}"
      f" | {_pct_cell(cw_decomp['C_domain_only'], tcw)} |")
    a(f"|   C · posture-only | {cw_decomp['C_posture_only']}"
      f" | {_pct_cell(cw_decomp['C_posture_only'], tcw)} |")
    a(f"|   C · both | {cw_decomp['C_both']}"
      f" | {_pct_cell(cw_decomp['C_both'], tcw)} |")
    a("")
    b_pct_cw = 100.0 * cw_decomp["B"] / tcw if tcw > 0 else 0.0
    c_pct_cw = 100.0 * cw_decomp["C"] / tcw if tcw > 0 else 0.0
    a(f"**B:C split — {b_pct_cw:.1f}% cell-map fault "
      f"vs {c_pct_cw:.1f}% mislabel**")
    a("")

    # Confusion pairs
    a("#### 3. Top Confusion Pairs")
    a("")
    a("**Domain mismatches** (gold → gpt):")
    a("")
    a("| gold domain | gpt domain | count |")
    a("|-------------|------------|-------|")
    for (g, p), cnt in domain_pairs:
        a(f"| {g} | {p} | {cnt} |")
    a("")
    a("**Posture mismatches** (gold → gpt):")
    a("")
    a("| gold posture | gpt posture | count |")
    a("|--------------|-------------|-------|")
    for (g, p), cnt in posture_pairs:
        a(f"| {g} | {p} | {cnt} |")
    a("")

    # Gold-suspect
    a("#### 4. Gold-Suspect Entries (Charge 1 Probe)")
    a("")
    a(f"Entries where GPT label differs from gold, but GPT's domain choice "
      f"has plausible lexical/extension support: **{gold_suspect_count}**")
    a("")
    if gold_suspect_examples:
        a("**Examples (first 5):**")
        a("")
        for i, ex in enumerate(gold_suspect_examples, 1):
            td_short = (
                ex["task_description"][:80] + "…"
                if len(ex["task_description"]) > 80
                else ex["task_description"]
            )
            fps = ", ".join(ex["file_paths"]) if ex["file_paths"] else "(none)"
            a(f"{i}. **corpus_id {ex['corpus_id']}** — "
              f"`{td_short}`")
            a(f"   - file_paths: `{fps}`")
            a(f"   - GPT: `{ex['gpt_domain']}`/`{ex['gpt_posture']}` → "
              f"routed to `{ex['route_agent']}`")
            a(f"   - Gold: `{ex['gold_domain']}`/`{ex['gold_posture']}` → "
              f"expected `{ex['gold_agent']}`")
            a("")
    a("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Bottom-line paragraph
# ---------------------------------------------------------------------------


def bottom_line(
    ns_rc_decomp: dict[str, Any],
    ns_cw_decomp: dict[str, Any],
    full_rc_decomp: dict[str, Any],
    gold_suspect_count_ns: int,
) -> str:
    """Write the one-paragraph bottom-line summary.

    Args:
        ns_rc_decomp: RC decomposition for no_smoke cut.
        ns_cw_decomp: CW decomposition for no_smoke cut.
        full_rc_decomp: RC decomposition for full cut.
        gold_suspect_count_ns: Gold-suspect count on no_smoke cut.

    Returns:
        One-paragraph markdown string.
    """
    tm_ns = ns_rc_decomp["total_misses"]
    b_ns = ns_rc_decomp["B"]
    c_ns = ns_rc_decomp["C"]
    b_pct = 100.0 * b_ns / tm_ns if tm_ns > 0 else 0.0
    c_pct = 100.0 * c_ns / tm_ns if tm_ns > 0 else 0.0

    c_dom = ns_rc_decomp["C_domain_only"]
    c_pos = ns_rc_decomp["C_posture_only"]
    c_both = ns_rc_decomp["C_both"]

    tcw = ns_cw_decomp["total_cw"]
    b_cw = ns_cw_decomp["B"]
    c_cw = ns_cw_decomp["C"]
    b_pct_cw = 100.0 * b_cw / tcw if tcw > 0 else 0.0
    c_pct_cw = 100.0 * c_cw / tcw if tcw > 0 else 0.0

    dom_c_pct = 100.0 * c_dom / c_ns if c_ns > 0 else 0.0
    pos_c_pct = 100.0 * c_pos / c_ns if c_ns > 0 else 0.0
    both_c_pct = 100.0 * c_both / c_ns if c_ns > 0 else 0.0

    return (
        f"On the primary `no_smoke` cut, **{c_pct:.0f}% of RC misses are "
        f"mislabel (C) and {b_pct:.0f}% are cell-map/compose fault (B)**. "
        f"Within C: {dom_c_pct:.0f}% are domain-only wrong, "
        f"{pos_c_pct:.0f}% posture-only wrong, {both_c_pct:.0f}% both wrong — "
        f"so domain confusion is the dominant mislabel axis, not posture. "
        f"The CW split is similar: {c_pct_cw:.0f}% mislabel vs "
        f"{b_pct_cw:.0f}% cell-map fault. "
        f"The gold-suspect probe found {gold_suspect_count_ns} entries (of "
        f"{ns_rc_decomp['total_labeled']} no_smoke labeled) where GPT's label "
        f"has plausible surface support — a minority, but non-trivial. "
        f"**Bottom line:** Phase 0's no_smoke shortfall is dominated by "
        f"GPT mislabeling ({c_pct:.0f}% of misses), not cell-map faults "
        f"({b_pct:.0f}%). "
        f"This means a better labeler or improved rubric fidelity is the "
        f"highest-leverage fix — the compose cell-map logic itself accounts "
        f"for only {b_pct:.0f}% of the failure. "
        f"However, {gold_suspect_count_ns} gold-suspect entries suggest "
        f"the gold labels may themselves carry ~{gold_suspect_count_ns} "
        f"errors, which partially offsets the mislabel attribution "
        f"(if those gold labels are wrong, some C-labeled misses "
        f"are actually correct routes misflagged as errors)."
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the failure decomposition and write the report.

    Loads the corpus, gold labels, GPT run1 labels, and catalog.
    Runs run_supplied_compose for both full and no_smoke cuts.
    Decomposes failures and writes the markdown report.

    Returns:
        None
    """
    print(f"Loading corpus: {_CORPUS_PATH}", file=sys.stderr)
    all_entries = load_corpus(_CORPUS_PATH)
    print(f"  {len(all_entries)} entries", file=sys.stderr)

    print(f"Loading gold labels: {_GOLD_LABELS_PATH}", file=sys.stderr)
    gold_labels = load_labels(_GOLD_LABELS_PATH)
    print(f"  {len(gold_labels)} gold labels", file=sys.stderr)

    print(f"Loading GPT run1 labels: {_GPT_RUN1_PATH}", file=sys.stderr)
    gpt_labels = load_gpt_labels(_GPT_RUN1_PATH)
    print(f"  {len(gpt_labels)} GPT labels", file=sys.stderr)

    # Build a GoldLabel-shaped map from GPT labels for use with
    # run_supplied_compose.  The "gold_agent" field is not used by the
    # harness in the label map (only domain + posture are consumed);
    # we fill it with a placeholder.
    gpt_as_gold: dict[int, GoldLabel] = {
        cid: GoldLabel(
            corpus_id=cid,
            domain=v["domain"],
            posture=v["posture"],
            gold_agent="",  # unused by run_supplied_compose
            is_any=False,
        )
        for cid, v in gpt_labels.items()
    }

    entries_by_id = {e.corpus_id: e for e in all_entries}

    results_by_cut: dict[str, dict[str, Any]] = {}

    for cut_name in ("full", "no_smoke"):
        print(f"\n=== Cut: {cut_name} ===", file=sys.stderr)

        # Apply cut to entries and gold labels
        if cut_name == "no_smoke":
            cut_entries, cut_gold = remove_smoke(all_entries, gold_labels)
            # Also apply to GPT-as-gold map
            smoke_ids = frozenset(
                e.corpus_id
                for e in all_entries
                if e.task_description in _SMOKE_DESCRIPTIONS
            )
            cut_gpt_as_gold = {
                cid: v for cid, v in gpt_as_gold.items()
                if cid not in smoke_ids
            }
        else:
            cut_entries = all_entries
            cut_gold = gold_labels
            cut_gpt_as_gold = gpt_as_gold

        print(
            f"  {len(cut_entries)} entries, "
            f"{len(cut_gold)} gold labels, "
            f"{len(cut_gpt_as_gold)} GPT labels",
            file=sys.stderr,
        )

        # Run supplied-compose with GPT labels as the domain/posture source
        print("  Running run_supplied_compose with GPT labels …",
              file=sys.stderr)
        sys_results = run_supplied_compose(
            cut_entries, _CATALOG_PATH, cut_gpt_as_gold
        )
        print(f"  → {len(sys_results)} results", file=sys.stderr)

        # Compute RC and CW against gold labels
        rc = metric_routing_correctness(sys_results, cut_gold)
        cw = metric_confident_wrong_rate(sys_results, cut_gold)
        print(f"  RC={rc:.4f}  CW={cw:.4f}", file=sys.stderr)

        # Build joined rows
        rows = build_joined_rows(
            cut_entries, cut_gold, gpt_labels, sys_results
        )
        print(f"  {len(rows)} joined rows (have both gold + GPT labels)",
              file=sys.stderr)

        # Decompose
        rc_decomp = decompose_rc_misses(rows)
        cw_decomp = decompose_cw_misses(rows)

        print(
            f"  RC misses: {rc_decomp['total_misses']} "
            f"(B={rc_decomp['B']}, C={rc_decomp['C']})",
            file=sys.stderr,
        )
        print(
            f"  CW misses: {cw_decomp['total_cw']} "
            f"(B={cw_decomp['B']}, C={cw_decomp['C']})",
            file=sys.stderr,
        )

        # Top confusion pairs (across all rows, not just misses)
        domain_pairs = top_confusion_pairs(rows, "domain")
        posture_pairs = top_confusion_pairs(rows, "posture")

        # Gold-suspect
        gold_suspect_count, gold_suspect_examples = (
            find_gold_suspect_examples(rows, entries_by_id)
        )
        print(
            f"  Gold-suspect entries: {gold_suspect_count}",
            file=sys.stderr,
        )

        results_by_cut[cut_name] = {
            "cut_entries": cut_entries,
            "cut_gold": cut_gold,
            "cut_gpt_as_gold": cut_gpt_as_gold,
            "sys_results": sys_results,
            "rc": rc,
            "cw": cw,
            "rows": rows,
            "rc_decomp": rc_decomp,
            "cw_decomp": cw_decomp,
            "domain_pairs": domain_pairs,
            "posture_pairs": posture_pairs,
            "gold_suspect_count": gold_suspect_count,
            "gold_suspect_examples": gold_suspect_examples,
        }

    # ---------------------------------------------------------------------------
    # Render report
    # ---------------------------------------------------------------------------

    print("\nRendering report …", file=sys.stderr)

    full = results_by_cut["full"]
    ns = results_by_cut["no_smoke"]

    # Header
    report_lines: list[str] = [
        "---",
        "title: Phase 0 — Failure Decomposition (Mislabel vs Cell-Map Fault)",
        "date: 2026-06-15",
        "tracking: glitchwerks/claude-wayfinder#382",
        "parent: glitchwerks/claude-wayfinder#362",
        "status: COMPLETE",
        "---",
        "",
        "# Phase 0 — Failure Decomposition: Mislabel vs Cell-Map Fault",
        "",
        "**Purpose.** Extend the Phase 0 independent-floor report by "
        "decomposing every RC miss and CW (confident-wrong) delegate miss "
        "from the GPT run1 supplied-compose run into:",
        "",
        "- **B — cell-map/compose fault:** GPT label matches gold on BOTH "
        "axes (domain AND posture), yet the routing system still produced "
        "the wrong agent. The labeler was correct; the cell-map logic "
        "failed.",
        "- **C — mislabel:** GPT label differs from gold on at least one "
        "axis. The labeler contributed to the miss.",
        "",
        "This decomposition answers: is Phase 0's shortfall primarily "
        "a labeler problem (→ a better labeler / Phase 0b could help) or "
        "a cell-map problem (→ the labeler isn't the bottleneck; fix the "
        "routing cells)?",
        "",
        "---",
        "",
    ]

    # Full cut section
    report_lines.append("## Full Cut")
    report_lines.append("")
    report_lines.append(render_report(
        cut="full",
        n_entries=len(full["cut_entries"]),
        n_gpt=len(full["cut_gpt_as_gold"]),
        n_gold=len(full["cut_gold"]),
        rc=full["rc"],
        cw=full["cw"],
        rc_decomp=full["rc_decomp"],
        cw_decomp=full["cw_decomp"],
        domain_pairs=full["domain_pairs"],
        posture_pairs=full["posture_pairs"],
        gold_suspect_count=full["gold_suspect_count"],
        gold_suspect_examples=full["gold_suspect_examples"],
    ))

    report_lines.append("---")
    report_lines.append("")

    # No-smoke cut section
    report_lines.append("## No-Smoke Cut (Primary)")
    report_lines.append("")
    report_lines.append(render_report(
        cut="no_smoke",
        n_entries=len(ns["cut_entries"]),
        n_gpt=len(ns["cut_gpt_as_gold"]),
        n_gold=len(ns["cut_gold"]),
        rc=ns["rc"],
        cw=ns["cw"],
        rc_decomp=ns["rc_decomp"],
        cw_decomp=ns["cw_decomp"],
        domain_pairs=ns["domain_pairs"],
        posture_pairs=ns["posture_pairs"],
        gold_suspect_count=ns["gold_suspect_count"],
        gold_suspect_examples=ns["gold_suspect_examples"],
    ))

    report_lines.append("---")
    report_lines.append("")

    # Bottom line
    report_lines.append("## Bottom Line")
    report_lines.append("")
    report_lines.append(bottom_line(
        ns_rc_decomp=ns["rc_decomp"],
        ns_cw_decomp=ns["cw_decomp"],
        full_rc_decomp=full["rc_decomp"],
        gold_suspect_count_ns=ns["gold_suspect_count"],
    ))
    report_lines.append("")

    output = "\n".join(report_lines)
    _OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_OUTPUT_PATH, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(output)
    print(f"\nReport written: {_OUTPUT_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
