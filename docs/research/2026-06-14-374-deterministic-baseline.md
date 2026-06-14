# Deterministic Baseline Spike — #374

**Date:** 2026-06-14
**Branch:** `spike/374-deterministic-baseline`
**Question:** How much of the two-axis +41.3 pp RC gain (0.3303 → 0.7431)
does a deterministic, label-free, auditable alternative recover?

---

## 1. Context and Anchors

The #362 inquisitor pass argued the two-axis Compose gain (RC +41.3 pp on
the `no_smoke` cut, n=109) is largely a tie-breaking artifact and that the
`2026-06-13-358-oracle-domain-ceiling.md` research doc identifies two cheaper,
deterministic alternatives that were never quantified:

- **`358:275`** — calibrate `_DELEGATE_GAP = 0.2` threshold.
- **`358:150`** — differentiate code-writer vs doc-writer in the lexical scorer.

Reference anchors (no_smoke cut, n=109, same harness):

| System | RC | CW |
|---|---|---|
| Lexical baseline | 0.3303 | 0.2558 |
| Two-axis real-label Compose floor | 0.7431 | 0.2430 |
| Two-axis ORACLE Compose ceiling | 0.7798 | 0.1414 |

Total RC gap to close: 0.7431 − 0.3303 = **0.4128 pp**.

Sources:
- `docs/research/2026-06-13-358-oracle-domain-ceiling.md` §5
- Harness: `scripts/corpus/eval/__main__.py`, systems: `scripts/corpus/eval/_systems.py`

---

## 2. Methodology

### Lever A — Gap threshold sweep

Added `run_lexical_calibrated()` to `scripts/corpus/eval/_systems.py` (see
`_systems.py:573+`). The function runs the identical pipeline as `run_lexical`
(`build_features → score_entries → decide`) but temporarily overrides
`_decide_module._DELEGATE_GAP` / `_DELEGATE_THRESHOLD` / `_ADVISORY_MIN` in
a `try/finally` block, ensuring live defaults are always restored.

The override mechanism was verified: decision counts differ measurably between
gap=0.00 (92 delegates) and gap=0.30 (40 delegates) on the no_smoke corpus.

Sweep range: `delegate_gap ∈ {0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30}`.
`delegate_threshold = 0.85` and `advisory_min = 0.5` held fixed at live values.

### Lever B — Code/doc differentiator

Implemented `_code_doc_boost()` in `_systems.py` (see `_systems.py:574+`).

Signal sources (all deterministic, no labels, no model):

1. **File extension evidence**: `.py/.js/.ts/...` → code vote; `.md/.rst/...`
   → doc vote.
2. **Keyword evidence** (raw unstemmed tokens): `"implement"`, `"function"`,
   `"pytest"`, etc. → code vote; `"readme"`, `"changelog"`, `"document"`,
   etc. → doc vote.
3. **Path-string evidence**: `/src/`, `/lib/`, `.py` in path → code;
   `/docs/`, `.md` in path → doc.

If doc_votes > code_votes → doc-writer gets `+boost`, code-writer gets
`-boost` (floor 0.0). If code_votes > doc_votes → reversed. Tie → no change.
Default `boost = 0.15`.

Sources: `_systems.py:_DOC_KEYWORDS`, `_CODE_KEYWORDS`, `_DOC_EXTENSIONS`,
`_CODE_EXTENSIONS` constants.

---

## 3. Sweep Table — Lever A (gap × RC / CW)

All values on no_smoke cut (n=109). Default `code_doc_boost=0.0`.

| delegate_gap | RC | CW | Delegates | RC delta vs lex |
|---|---|---|---|---|
| 0.00 | **0.4862** | 0.4457 | 92 | +0.1559 |
| 0.05 | 0.3394 | 0.3125 | 48 | +0.0091 |
| 0.10 | 0.3303 | 0.2889 | 45 | 0.0000 |
| 0.15 | 0.3303 | 0.2727 | 44 | 0.0000 |
| **0.20** | **0.3303** | **0.2558** | **43** | **0.0000 (baseline)** |
| 0.25 | 0.3303 | 0.2558 | 43 | 0.0000 |
| 0.30 | 0.3303 | 0.2000 | 40 | 0.0000 |

Sources: `scripts/corpus/eval/_systems.py:run_lexical_calibrated`,
`.tmp/374_sweep.py` (probe), measured 2026-06-14.

### Key finding — Lever A

**gap=0.00 is the only setting that materially lifts RC.** All settings ≥ 0.05
produce the same RC as the baseline (0.3303). The gain at gap=0.00 is
significant (+15.6 pp) but comes at a **severe CW cost (+18.99 pp: 0.2558 →
0.4457)** — nearly doubling the confident-wrong rate.

The mechanism: removing the gap requirement forces `decide()` to delegate any
entry where the top agent scores ≥ 0.85, even when a close competitor also
scores ≥ 0.85 (which was previously counted as a tie → `self_handle` or
`advisory`). This turns many advisory/self_handle results into delegate
decisions, including many wrong ones. The 92 delegates at gap=0.00 vs 43 at
the default is a 2.1× increase in delegations; the wrong ones account for the
CW spike.

---

## 4. Lever B Row — Code/doc differentiator

| Config | RC | CW | Delegates |
|---|---|---|---|
| gap=0.00 (Lever A only) | 0.4862 | 0.4457 | 92 |
| gap=0.00 + boost=0.15 (A+B) | **0.5046** | 0.4194 | 93 |
| gap=0.05 + boost=0.15 | 0.3486 | 0.2800 | 50 |

Lever B adds +1.8 pp RC at gap=0.00 (0.4862 → 0.5046) at a modest CW
reduction (−0.026). The differentiator correctly shifts some code/doc ties
toward the right agent. At gap=0.05, Lever B also adds +0.9 pp but CW
rises by +2.4 pp vs the default, still worse than baseline CW.

Sources: `.tmp/374_sweep.py`, `.tmp/374_sweep2.py`, measured 2026-06-14.

---

## 5. Headline: Fraction of 41 pp Recovered

| Config | RC | Recovered pp | % of 41 pp gap |
|---|---|---|---|
| Lexical baseline | 0.3303 | — | — |
| Best det (gap=0.00, A only) | 0.4862 | +0.1559 | **37.8%** |
| Best det+B (gap=0.00, boost=0.15) | 0.5046 | +0.1743 | **42.2%** |
| Two-axis floor | 0.7431 | +0.4128 | 100% |

**The best deterministic config recovers 37.8–42.2% of the 41 pp gap.**
The remaining 57.8–62.2% (0.2385–0.2569 pp) is not recoverable by gap
calibration or the lexical code/doc differentiator alone.

---

## 6. Per-domain RC Breakdown

### Lever A only (gap=0.00, no Lever B)

| Domain | n | Det RC | Two-axis RC | Delta |
|---|---|---|---|---|
| code | 45 | 0.5778 | 0.8440 | −0.2662 |
| docs_prose | 13 | 0.5385 | 0.9230 | −0.3845 |
| project_meta | 30 | 0.2333 | 0.5670 | −0.3337 |
| is_any | 16 | 0.6250 | 0.8750 | −0.2500 |
| infra_deploy | 5 | 0.6000 | 0.0000 | +0.6000 |

### Lever A + B (gap=0.00, boost=0.15)

| Domain | n | Det+B RC | Two-axis RC | Delta |
|---|---|---|---|---|
| code | 45 | 0.5778 | 0.8440 | −0.2662 |
| docs_prose | 13 | **0.6923** | 0.9230 | −0.2307 |
| project_meta | 30 | 0.2667 | 0.5670 | −0.3003 |
| is_any | 16 | 0.6250 | 0.8750 | −0.2500 |
| infra_deploy | 5 | 0.4000 | 0.0000 | +0.4000 |

Sources: `.tmp/374_sweep2.py`, two-axis per-domain from `#362` measurement.

### Per-domain interpretation

**Where the deterministic baseline closes the gap:**
- **infra_deploy** (n=5): RC 0.60 vs two-axis 0.00 — the deterministic
  baseline does *better* here. Two-axis has RC=0.00 because the composed
  system misroutes these 5 entries (likely domain labeling issues in
  infra_deploy as documented in `358:§6`). The deterministic baseline with
  gap=0.00 correctly delegates some of these.
- **code** (n=45): Gap partially closed — 0.578 vs 0.844 (−26.6 pp
  remaining). Many code entries already score high lexically; gap=0.00
  converts ties to delegates but some remain advisory or wrong.

**Where two-axis helps dramatically (deterministic cannot close the gap):**
- **docs_prose** (n=13): 0.538 vs 0.923 (−38.5 pp). The oracle posture
  label resolves build-posture entries unambiguously to doc-writer. The
  lexical scorer and Lever B together get to 0.692 (+15 pp) but cannot
  reach the oracle ceiling. The remaining gap requires knowing the
  gold posture (e.g. docs vs code intent in ambiguous "implement +
  document" tasks).
- **project_meta** (n=30): 0.233 vs 0.567 (−33.4 pp). This is the
  hardest domain for deterministic routing — project-planner,
  project-reviewer, researcher, and approach-critic all compete. Oracle
  posture labels (plan, research, idea-critique) break ties that lexical
  scoring cannot.
- **is_any** (n=16): 0.625 vs 0.875 (−25.0 pp). Mixed-intent entries
  that need cross-signal disambiguation; Lever B is neutral here.

### Decision distribution

| Config | delegate | self_handle | advisory | other |
|---|---|---|---|---|
| Lexical (gap=0.20) | 43 | 35 | 27 | 4 |
| Det gap=0.00 | 92 | 5 | 8 | 4 |
| Det gap=0.00+B | 93 | 5 | 7 | 4 |
| Two-axis floor | (not published here) | — | — | — |

---

## 7. E8/E11 Structured Signal Coverage

Verified that `command_prefix` (E8) and `agent_mentions` (E11) are present in
corpus entries and flow through `_entry_to_context()` into `build_features()`:

| Signal | no_smoke entries with signal | Fraction |
|---|---|---|
| E8 (command_prefix ≠ None) | 2 / 109 | 1.8% |
| E11 (agent_mentions non-empty) | 34 / 109 | 31.2% |

E8 coverage is sparse (2 entries) in the no_smoke cut — these entries route
ops correctly in both lexical and calibrated configs. E11 is richer (34/109)
and has significant routing-correctness impact when agent_mentions are
present; the lexical scorer scores agent_mentions as a primary signal.

Both signals are retained from #357 (see `_reader.py:CorpusEntry.agent_mentions`
and `CorpusEntry.command_prefix`, `_systems.py:_entry_to_context`).

Sources: `.tmp/374_sweep.py:E8/E11 block`, `_reader.py:164-166`,
`_systems.py:_entry_to_context`.

---

## 8. GO / NO-GO Recommendation

**NO-GO: the two-axis machinery (#362) is justified by its marginal gain.**

### Rationale

The best deterministic config (gap=0.00 + Lever B) recovers **42% of the
41 pp gap (RC 0.5046 vs 0.7431)** at a **CW cost of +16.4 pp (0.2558 →
0.4194)**. That CW trajectory is unacceptable — nearly doubling the
confident-wrong rate is the wrong trade-off for RC gains in a routing system
where wrong-confident decisions cause direct user harm (wrong agent is invoked
with no recourse).

The two-axis floor achieves RC=0.7431 at CW=0.2430 — essentially matching
the lexical CW while gaining +41 pp RC. The deterministic baseline cannot
achieve this: its RC gains only come at the cost of significant CW degradation.

**Key structural reason:** the `_DELEGATE_GAP` threshold exists precisely to
prevent over-confident wrong delegation. Lowering it improves RC by converting
abstains (self_handle, advisory) into delegates — but many of these abstains
were correct abstains on genuinely ambiguous entries. The two-axis machinery
(domain gate + posture cell) resolves the ambiguity informatively, allowing
delegation with low CW. Gap-only calibration resolves it by force, with high CW.

**The 58% of the gap that deterministic cannot close:**
- `project_meta` domain (30 entries) requires posture labels (plan /
  research / idea-critique) to distinguish project-planner, researcher, and
  approach-critic — lexical scoring of these agents is too similar.
- `docs_prose` (13 entries) requires posture labels to distinguish doc-writing
  tasks from code tasks that mention documentation.
- Ambiguous cross-domain entries (7 of the original 19 tie-set entries from
  `358:§11.3`) require a real domain signal — the keyword code/doc
  differentiator (Lever B) helps only marginally (+1.8 pp) because the
  ambiguity is at the planning/posture level, not at the lexical level.

**Auditability note:** the `run_lexical_calibrated()` function IS fully
deterministic and auditable. But the gap=0.00 setting is not production-safe:
its CW=0.44 makes it unsuitable for live routing. Any deterministic config
that keeps CW near baseline (0.26) gains ≤ 0.0091 RC pp — effectively zero.

**Conclusion:** the #362 two-axis system delivers its RC gains without CW
penalty, which no deterministic gap-calibration approach can match. The
LLM-label dependency and auditability cost of #362 are justified by the
remaining ~58% of the gap that the deterministic alternative cannot close
without degrading CW.

---

## 9. Sources and Cite Map

| Citation | Resolves to |
|---|---|
| `358:275` | `docs/research/2026-06-13-358-oracle-domain-ceiling.md` line 275 (gap threshold discussion) |
| `358:150` | `docs/research/2026-06-13-358-oracle-domain-ceiling.md` line 150 (code/doc differentiation suggestion) |
| `_decide.py:40-42` | `src/claude_wayfinder/match/_decide.py` lines 40-42 (`_DELEGATE_THRESHOLD`, `_DELEGATE_GAP`, `_ADVISORY_MIN`) |
| `_systems.py:535` | `scripts/corpus/eval/_systems.py` line 535+ (`run_lexical`) |
| `_systems.py:573+` | `scripts/corpus/eval/_systems.py` line 573+ (`run_lexical_calibrated`, added in this spike) |
| `_metrics.py:503` | `scripts/corpus/eval/_metrics.py` line 503 (`metric_routing_correctness`) |
| `_metrics.py:466` | `scripts/corpus/eval/_metrics.py` line 466 (`metric_confident_wrong_rate`) |
| `.tmp/374_sweep.py` | Probe script (ephemeral, not committed); measurements reproduced from verified harness run |
| `.tmp/374_sweep2.py` | Supplemental per-domain probe (ephemeral, not committed) |

---

## 10. Verification

| Gate | Result |
|---|---|
| Sanity: calibrated at defaults reproduces RC=0.3303/CW=0.2558 | **PASS** |
| ruff `src/ tests/ scripts/` | **PASS (0 errors)** |
| pytest (worktree, excl. integration) | **PASS: 1230 passed, 8 skipped** (baseline was 1219+8; +11 new tests) |
| Override sanity: gap=0.00 delegates=92, gap=0.30 delegates=40 (differ) | **PASS** |
| Module constant restored after call: `_DELEGATE_GAP=0.2` | **PASS** |
