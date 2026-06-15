"""Honest tuned-lexical comparison sweep — issue #384.

Measures discrimination in isolation (no bar-lowering):

Config 1 — code/doc boost at the LIVE default delegate_gap=0.20,
    sweeping boost magnitude in {0.0, 0.10, 0.15, 0.20, 0.25}.
    boost=0.0 at gap=0.20 is the sanity-check baseline and must
    reproduce the published lexical RC=0.3303 / CW=0.2558 (#350).

Config 2 — generalised multi-domain _domain_boost at gap=0.20
    (no bar-lowering), boost in {0.15, 0.20}.

Both configs are run on cuts: no_smoke (primary) and full (reference).

IMPORTANT: no corpus-mining.  Signal tables in _systems.py are built
from generalizable domain cues only.  Gold labels are read ONLY by the
metric functions after all routing decisions have been made.

Usage::

    python -m scripts.corpus.384_sweep
    # or from the worktree root:
    .venv/Scripts/python.exe scripts/corpus/384_sweep.py

Writes results to stdout and to
``docs/research/2026-06-15-tuned-lexical-honest-comparison.md``.
"""

from __future__ import annotations

import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup — allow running as a plain script from the worktree root.
# ---------------------------------------------------------------------------
_WORKTREE = Path(__file__).resolve().parent.parent.parent
if str(_WORKTREE / "src") not in sys.path:
    sys.path.insert(0, str(_WORKTREE / "src"))
# Add worktree root so ``scripts.corpus.eval.*`` imports in __main__ resolve.
if str(_WORKTREE) not in sys.path:
    sys.path.insert(0, str(_WORKTREE))

from scripts.corpus.eval.__main__ import apply_cut  # noqa: E402
from scripts.corpus.eval._metrics import (  # noqa: E402  (after sys.path)
    metric_confident_wrong_rate,
    metric_routing_correctness,
)
from scripts.corpus.eval._reader import load_corpus, load_labels  # noqa: E402
from scripts.corpus.eval._systems import run_lexical_calibrated  # noqa: E402

# ---------------------------------------------------------------------------
# Paths (resolved relative to worktree root; must already exist).
# ---------------------------------------------------------------------------
_CORPUS_PATH = Path.home() / ".claude/state/wayfinder-corpus/2026-06-12/wayfinder-corpus.jsonl"
_LABELS_PATH = _WORKTREE / "docs/research/2026-06-12-gold-labels-redacted.jsonl"
_CATALOG_PATH = Path.home() / ".claude/state/dispatch-catalog.json"
_REPORT_PATH = _WORKTREE / "docs/research/2026-06-15-tuned-lexical-honest-comparison.md"

# Live default — no bar-lowering.
_LIVE_GAP: float = 0.20

# Config 1 sweep: code/doc boost magnitudes.
_C1_BOOSTS: list[float] = [0.0, 0.10, 0.15, 0.20, 0.25]

# Config 2 sweep: multi-domain boost magnitudes.
_C2_BOOSTS: list[float] = [0.15, 0.20]

# Lexical and oracle baselines from published spikes.
_BASELINE_RC_NO_SMOKE = 0.3303
_BASELINE_CW_NO_SMOKE = 0.2558
_TWO_AXIS_ORACLE_RC = 0.7798
_PHASE0_FLOOR_RC = 0.55  # approximate from #382


def _run_config1(
    entries_ns: list,
    labels_ns: dict,
    entries_full: list,
    labels_full: dict,
) -> list[dict]:
    """Run Config 1: code/doc boost sweep at live gap (no bar-lowering).

    Args:
        entries_ns: no_smoke cut entries.
        labels_ns: no_smoke cut labels.
        entries_full: full cut entries.
        labels_full: full cut labels.

    Returns:
        List of result dicts with keys boost, cut, rc, cw.
    """
    rows: list[dict] = []
    for boost in _C1_BOOSTS:
        for cut_name, cut_entries, cut_labels in [
            ("no_smoke", entries_ns, labels_ns),
            ("full", entries_full, labels_full),
        ]:
            results = run_lexical_calibrated(
                cut_entries,
                _CATALOG_PATH,
                delegate_gap=_LIVE_GAP,
                code_doc_boost=boost,
                domain_boost=0.0,
            )
            rc = metric_routing_correctness(results, cut_labels)
            cw = metric_confident_wrong_rate(results, cut_labels)
            rows.append({
                "config": "C1",
                "boost": boost,
                "cut": cut_name,
                "rc": rc,
                "cw": cw,
            })
            print(
                f"  C1 boost={boost:.2f} cut={cut_name:<8} "
                f"RC={rc:.4f}  CW={cw:.4f}"
            )
    return rows


def _run_config2(
    entries_ns: list,
    labels_ns: dict,
    entries_full: list,
    labels_full: dict,
) -> list[dict]:
    """Run Config 2: generalised multi-domain boost at live gap.

    Args:
        entries_ns: no_smoke cut entries.
        labels_ns: no_smoke cut labels.
        entries_full: full cut entries.
        labels_full: full cut labels.

    Returns:
        List of result dicts with keys boost, cut, rc, cw.
    """
    rows: list[dict] = []
    for boost in _C2_BOOSTS:
        for cut_name, cut_entries, cut_labels in [
            ("no_smoke", entries_ns, labels_ns),
            ("full", entries_full, labels_full),
        ]:
            results = run_lexical_calibrated(
                cut_entries,
                _CATALOG_PATH,
                delegate_gap=_LIVE_GAP,
                code_doc_boost=0.0,
                domain_boost=boost,
            )
            rc = metric_routing_correctness(results, cut_labels)
            cw = metric_confident_wrong_rate(results, cut_labels)
            rows.append({
                "config": "C2",
                "boost": boost,
                "cut": cut_name,
                "rc": rc,
                "cw": cw,
            })
            print(
                f"  C2 boost={boost:.2f} cut={cut_name:<8} "
                f"RC={rc:.4f}  CW={cw:.4f}"
            )
    return rows


def _render_report(
    c1_rows: list[dict],
    c2_rows: list[dict],
) -> str:
    """Render the markdown research report.

    Args:
        c1_rows: Config 1 result rows.
        c2_rows: Config 2 result rows.

    Returns:
        Full markdown string.
    """
    # Separate no_smoke and full cuts.
    c1_ns = [r for r in c1_rows if r["cut"] == "no_smoke"]
    c1_full = [r for r in c1_rows if r["cut"] == "full"]
    c2_ns = [r for r in c2_rows if r["cut"] == "no_smoke"]
    c2_full = [r for r in c2_rows if r["cut"] == "full"]

    def _rc_above_base(rc: float) -> str:
        """Format RC with delta from baseline."""
        delta = rc - _BASELINE_RC_NO_SMOKE
        sign = "+" if delta >= 0 else ""
        return f"{rc:.4f} ({sign}{delta:.4f})"

    def _cw_flag(cw: float) -> str:
        """Flag CW values above the baseline."""
        flag = " !" if cw > _BASELINE_CW_NO_SMOKE else ""
        return f"{cw:.4f}{flag}"

    # --- Config 1 table ---
    c1_ns_header = (
        "| boost | RC (no_smoke) | delta vs base | "
        "CW (no_smoke) | CW flag |\n"
        "|------:|:-------------:|:-------------:|"
        ":--------------:|:-------:|\n"
    )
    c1_ns_rows = "".join(
        f"| {r['boost']:.2f}  | {r['rc']:.4f} | "
        f"{r['rc'] - _BASELINE_RC_NO_SMOKE:+.4f} | "
        f"{r['cw']:.4f} | "
        f"{'CW>base' if r['cw'] > _BASELINE_CW_NO_SMOKE else 'ok'} |\n"
        for r in c1_ns
    )
    c1_full_header = (
        "| boost | RC (full) | CW (full) |\n"
        "|------:|:---------:|:---------:|\n"
    )
    c1_full_rows = "".join(
        f"| {r['boost']:.2f}  | {r['rc']:.4f} | {r['cw']:.4f} |\n"
        for r in c1_full
    )

    # --- Config 2 table ---
    c2_ns_header = (
        "| boost | RC (no_smoke) | delta vs C1@same-boost | "
        "CW (no_smoke) | CW flag |\n"
        "|------:|:-------------:|:----------------------:|"
        ":--------------:|:-------:|\n"
    )

    def _c1_rc_at_boost(boost: float, cut: str) -> float:
        """Look up Config 1 RC for the same boost magnitude and cut."""
        matches = [
            r["rc"] for r in c1_rows
            if r["boost"] == boost and r["cut"] == cut
        ]
        return matches[0] if matches else float("nan")

    c2_ns_rows = "".join(
        f"| {r['boost']:.2f}  | {r['rc']:.4f} | "
        f"{r['rc'] - _c1_rc_at_boost(r['boost'], 'no_smoke'):+.4f} | "
        f"{r['cw']:.4f} | "
        f"{'CW>base' if r['cw'] > _BASELINE_CW_NO_SMOKE else 'ok'} |\n"
        for r in c2_ns
    )
    c2_full_header = (
        "| boost | RC (full) | CW (full) |\n"
        "|------:|:---------:|:---------:|\n"
    )
    c2_full_rows = "".join(
        f"| {r['boost']:.2f}  | {r['rc']:.4f} | {r['cw']:.4f} |\n"
        for r in c2_full
    )

    # --- best no_smoke result for bottom line ---
    all_ns = c1_ns + c2_ns
    flat_cw_candidates = [r for r in all_ns if r["cw"] <= _BASELINE_CW_NO_SMOKE]
    if flat_cw_candidates:
        best = max(flat_cw_candidates, key=lambda r: r["rc"])
        best_desc = (
            f"Config {'1' if best['config'] == 'C1' else '2'} "
            f"boost={best['boost']:.2f}: "
            f"RC={best['rc']:.4f} at CW={best['cw']:.4f}"
        )
        rc_gain = best["rc"] - _BASELINE_RC_NO_SMOKE
        rc_gain_pct = rc_gain / _BASELINE_RC_NO_SMOKE * 100
    else:
        best_desc = "no configuration achieved CW <= baseline"
        rc_gain = 0.0
        rc_gain_pct = 0.0

    remaining_gap = _TWO_AXIS_ORACLE_RC - (
        best["rc"] if flat_cw_candidates else _BASELINE_RC_NO_SMOKE
    )

    # --- signal table sizes (counted from _systems.py constants) ---
    c1_signal_sizes = {
        "_DOC_KEYWORDS": 19,
        "_CODE_KEYWORDS": 15,
        "_DOC_EXTENSIONS": 5,
        "_CODE_EXTENSIONS": 16,
    }
    c2_signal_sizes = {
        "_CODE_DOMAIN_EXTENSIONS": 16,
        "_CODE_DOMAIN_PATHS": 4,
        "_CODE_DOMAIN_KEYWORDS": 13,
        "_DOCS_PROSE_DOMAIN_EXTENSIONS": 5,
        "_DOCS_PROSE_DOMAIN_PATHS": 2,
        "_DOCS_PROSE_DOMAIN_KEYWORDS": 14,
        "_PROJECT_META_DOMAIN_PATHS": 4,
        "_PROJECT_META_DOMAIN_KEYWORDS": 14,
        "_INFRA_DEPLOY_DOMAIN_EXTENSIONS": 5,
        "_INFRA_DEPLOY_DOMAIN_PATHS": 7,
        "_INFRA_DEPLOY_DOMAIN_KEYWORDS": 13,
    }
    c1_total = sum(c1_signal_sizes.values())
    c2_total = sum(c2_signal_sizes.values())
    c2_per_domain = c2_total / 4

    report = f"""\
# Honest Tuned-Lexical Comparison — Issue #384

**Date:** 2026-06-15
**Parent:** #362 (two-axis routing design)
**Follow-up to:** #374 (bar-lowering exposed)
**Corpus:** 168 entries, 2026-06-12 snapshot
**Gold labels:** `docs/research/2026-06-12-gold-labels-redacted.jsonl`

## Background

Issue #374's 42 % RC recovery was dominated by Lever A (gap→0), which
is pure bar-lowering. Lever B (code/doc discriminator) was never
measured WITHOUT the bar-lowering crutch. This spike isolates each
discriminator at the LIVE default `delegate_gap=0.20` (no bar-lowering)
and measures the honest RC/CW trade-off.

**Baselines (no_smoke cut):**

| System | RC | CW |
|--------|----|----|
| Lexical baseline (#350, gap=0.20) | {_BASELINE_RC_NO_SMOKE:.4f} | {_BASELINE_CW_NO_SMOKE:.4f} |
| Phase-0 independent floor (#382) | ~{_PHASE0_FLOOR_RC:.2f} | — |
| Two-axis oracle (#362) | {_TWO_AXIS_ORACLE_RC:.4f} | — |

## Config 1 — Code/Doc Boost, Discrimination Isolated

`_code_doc_boost` applied at `delegate_gap=0.20` (LIVE default, no
bar-lowering). boost=0.0 is the sanity-check: must reproduce the
lexical baseline exactly.

### no_smoke cut (primary)

{c1_ns_header}{c1_ns_rows}
`!` = CW exceeded baseline ({_BASELINE_CW_NO_SMOKE:.4f}).

### full cut (reference)

{c1_full_header}{c1_full_rows}
## Config 2 — Generalised Multi-Domain Boost (Lever B-2)

`_domain_boost` covers code / docs_prose / project_meta / infra_deploy
using principled ext + path + keyword votes. Reuses `DOMAIN_AGENT_MAP`
from `src/claude_wayfinder/match/_cells.py` for domain→agent sets.

Applied at `delegate_gap=0.20` (no bar-lowering).

### no_smoke cut (primary)

{c2_ns_header}{c2_ns_rows}
Delta vs C1@same-boost = incremental RC from adding 3 more domains.

### full cut (reference)

{c2_full_header}{c2_full_rows}
## Characterisation-Cost Note

### Config 1 — `_code_doc_boost`

| Table | Entries |
|-------|--------:|
| `_DOC_KEYWORDS` | {c1_signal_sizes["_DOC_KEYWORDS"]} |
| `_CODE_KEYWORDS` | {c1_signal_sizes["_CODE_KEYWORDS"]} |
| `_DOC_EXTENSIONS` | {c1_signal_sizes["_DOC_EXTENSIONS"]} |
| `_CODE_EXTENSIONS` | {c1_signal_sizes["_CODE_EXTENSIONS"]} |
| **Total** | **{c1_total}** |

Domains covered: 2 (code, docs_prose). Rule-lines ≈ {c1_total + 10}.

### Config 2 — `_domain_boost`

| Table | Entries |
|-------|--------:|
| `_CODE_DOMAIN_EXTENSIONS` | {c2_signal_sizes["_CODE_DOMAIN_EXTENSIONS"]} |
| `_CODE_DOMAIN_PATHS` | {c2_signal_sizes["_CODE_DOMAIN_PATHS"]} |
| `_CODE_DOMAIN_KEYWORDS` | {c2_signal_sizes["_CODE_DOMAIN_KEYWORDS"]} |
| `_DOCS_PROSE_DOMAIN_EXTENSIONS` | {c2_signal_sizes["_DOCS_PROSE_DOMAIN_EXTENSIONS"]} |
| `_DOCS_PROSE_DOMAIN_PATHS` | {c2_signal_sizes["_DOCS_PROSE_DOMAIN_PATHS"]} |
| `_DOCS_PROSE_DOMAIN_KEYWORDS` | {c2_signal_sizes["_DOCS_PROSE_DOMAIN_KEYWORDS"]} |
| `_PROJECT_META_DOMAIN_PATHS` | {c2_signal_sizes["_PROJECT_META_DOMAIN_PATHS"]} |
| `_PROJECT_META_DOMAIN_KEYWORDS` | {c2_signal_sizes["_PROJECT_META_DOMAIN_KEYWORDS"]} |
| `_INFRA_DEPLOY_DOMAIN_EXTENSIONS` | {c2_signal_sizes["_INFRA_DEPLOY_DOMAIN_EXTENSIONS"]} |
| `_INFRA_DEPLOY_DOMAIN_PATHS` | {c2_signal_sizes["_INFRA_DEPLOY_DOMAIN_PATHS"]} |
| `_INFRA_DEPLOY_DOMAIN_KEYWORDS` | {c2_signal_sizes["_INFRA_DEPLOY_DOMAIN_KEYWORDS"]} |
| **Total** | **{c2_total}** |

Domains covered: 4. Rule-lines ≈ {c2_total + 20}. ≈ {c2_per_domain:.0f} entries/domain.
`DOMAIN_AGENT_MAP` reuse: **yes** — boost target sets derived directly
from `_cells.DOMAIN_AGENT_MAP` values (deterministic analog of the
two-axis domain hard-gate, fed by lexical inference instead of LLM label).

## Overfitting Caveat

All discriminator signal tables (extensions, path prefixes, keywords)
are built from **generalizable domain cues** — not by inspecting which
corpus entries are misrouted. This avoids the #364 trap.

The **no_smoke cut** is the honest read for this spike. The full cut
contains smoke-test entries whose ultra-short descriptions give the
lexical scorer artificial signal, inflating absolute numbers.

True generalisation still requires held-out data. The 168-entry corpus
is both train and test here; a future held-out evaluation is needed
before any production decision.

## Bottom Line

At flat CW (≤ {_BASELINE_CW_NO_SMOKE:.4f}), the best deterministic discriminator
reaches:

**{best_desc}**

RC gain over lexical baseline: {rc_gain:+.4f} ({rc_gain_pct:+.1f} %).
Remaining gap to two-axis oracle ({_TWO_AXIS_ORACLE_RC:.4f}): {remaining_gap:.4f}.

### Scaling implication

Config 2 covers 4 domains with ~{c2_total} signal entries (~{c2_per_domain:.0f}/domain).
The incremental RC from C1→C2 (adding project_meta + infra_deploy)
is shown in the Config 2 delta column. If the per-domain RC slope is
sub-linear (each new domain buys less than the previous), diminishing
returns set in quickly and the deterministic ceiling is well below the
oracle. If the slope is roughly linear, there is headroom — but the
signal-table maintenance cost grows proportionally.

The LLM approach (two-axis, #362) achieves {_TWO_AXIS_ORACLE_RC:.4f} RC with a
fixed taxonomy and zero keyword-table upkeep. The characterisation cost
for the deterministic approach scales with the number of domains and
the precision of their lexical boundaries — which shrinks with domain
overlap and grows with new agent types.
"""
    return report


def main() -> None:
    """Run the full #384 sweep and write the report."""
    print("Loading corpus and labels...")
    entries = load_corpus(_CORPUS_PATH)
    labels = load_labels(_LABELS_PATH)

    print(f"  Corpus: {len(entries)} entries")
    print(f"  Labels: {len(labels)} gold labels")

    # Apply cuts.
    entries_ns, labels_ns = apply_cut(entries, labels, "no_smoke")
    entries_full, labels_full = apply_cut(entries, labels, "full")
    print(f"  no_smoke cut: {len(entries_ns)} entries, {len(labels_ns)} labels")
    print(f"  full cut:     {len(entries_full)} entries, {len(labels_full)} labels")

    # Sanity check: boost=0 at gap=0.20 must reproduce the baseline.
    print("\nSanity check (boost=0.0, gap=0.20, no_smoke)...")
    sanity = run_lexical_calibrated(
        entries_ns,
        _CATALOG_PATH,
        delegate_gap=_LIVE_GAP,
        code_doc_boost=0.0,
        domain_boost=0.0,
    )
    rc0 = metric_routing_correctness(sanity, labels_ns)
    cw0 = metric_confident_wrong_rate(sanity, labels_ns)
    print(f"  RC={rc0:.4f} (expected ~{_BASELINE_RC_NO_SMOKE:.4f})")
    print(f"  CW={cw0:.4f} (expected ~{_BASELINE_CW_NO_SMOKE:.4f})")
    rc_ok = abs(rc0 - _BASELINE_RC_NO_SMOKE) < 0.005
    cw_ok = abs(cw0 - _BASELINE_CW_NO_SMOKE) < 0.005
    if not rc_ok or not cw_ok:
        print("  WARNING: sanity check failed — baseline mismatch.")
    else:
        print("  PASS: baseline matches within tolerance.")

    print("\nConfig 1 — code/doc boost sweep:")
    c1_rows = _run_config1(entries_ns, labels_ns, entries_full, labels_full)

    print("\nConfig 2 — multi-domain boost sweep:")
    c2_rows = _run_config2(entries_ns, labels_ns, entries_full, labels_full)

    print("\nRendering report...")
    report = _render_report(c1_rows, c2_rows)

    _REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_REPORT_PATH, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(report)
    print(f"Report written to: {_REPORT_PATH}")

    # Also print summary to stdout.
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    ns_rows = [r for r in c1_rows + c2_rows if r["cut"] == "no_smoke"]
    for r in ns_rows:
        cfg = r["config"]
        b = r["boost"]
        rc = r["rc"]
        cw = r["cw"]
        flag = " <-- CW>base" if cw > _BASELINE_CW_NO_SMOKE else ""
        print(f"  {cfg} boost={b:.2f}  RC={rc:.4f}  CW={cw:.4f}{flag}")


if __name__ == "__main__":
    main()
