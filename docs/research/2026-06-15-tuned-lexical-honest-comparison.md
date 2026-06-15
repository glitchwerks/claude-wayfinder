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
| Lexical baseline (#350, gap=0.20) | 0.3303 | 0.2558 |
| Phase-0 independent floor (#382) | ~0.55 | — |
| Two-axis oracle (#362) | 0.7798 | — |

## Config 1 — Code/Doc Boost, Discrimination Isolated

`_code_doc_boost` applied at `delegate_gap=0.20` (LIVE default, no
bar-lowering). boost=0.0 is the sanity-check: must reproduce the
lexical baseline exactly.

### no_smoke cut (primary)

| boost | RC (no_smoke) | delta vs base | CW (no_smoke) | CW flag |
|------:|:-------------:|:-------------:|:--------------:|:-------:|
| 0.00  | 0.3303 | +0.0000 | 0.2558 | ok |
| 0.10  | 0.3211 | -0.0092 | 0.2326 | ok |
| 0.15  | 0.3211 | -0.0092 | 0.2326 | ok |
| 0.20  | 0.3211 | -0.0092 | 0.2326 | ok |
| 0.25  | 0.3486 | +0.0183 | 0.2500 | ok |

`!` = CW exceeded baseline (0.2558).

### full cut (reference)

| boost | RC (full) | CW (full) |
|------:|:---------:|:---------:|
| 0.00  | 0.3929 | 0.1507 |
| 0.10  | 0.3869 | 0.1370 |
| 0.15  | 0.3869 | 0.1370 |
| 0.20  | 0.3869 | 0.1370 |
| 0.25  | 0.4048 | 0.1538 |

## Config 2 — Generalised Multi-Domain Boost (Lever B-2)

`_domain_boost` covers code / docs_prose / project_meta / infra_deploy
using principled ext + path + keyword votes. Reuses `DOMAIN_AGENT_MAP`
from `src/claude_wayfinder/match/_cells.py` for domain→agent sets.

Applied at `delegate_gap=0.20` (no bar-lowering).

### no_smoke cut (primary)

| boost | RC (no_smoke) | delta vs C1@same-boost | CW (no_smoke) | CW flag |
|------:|:-------------:|:----------------------:|:--------------:|:-------:|
| 0.15  | 0.3486 | +0.0275 | 0.2143 | ok |
| 0.20  | 0.3486 | +0.0275 | 0.1750 | ok |

Delta vs C1@same-boost = incremental RC from adding 3 more domains.

### full cut (reference)

| boost | RC (full) | CW (full) |
|------:|:---------:|:---------:|
| 0.15  | 0.4048 | 0.1250 |
| 0.20  | 0.4048 | 0.1000 |

## Characterisation-Cost Note

### Config 1 — `_code_doc_boost`

| Table | Entries |
|-------|--------:|
| `_DOC_KEYWORDS` | 19 |
| `_CODE_KEYWORDS` | 15 |
| `_DOC_EXTENSIONS` | 5 |
| `_CODE_EXTENSIONS` | 16 |
| **Total** | **55** |

Domains covered: 2 (code, docs_prose). Rule-lines ≈ 65.

### Config 2 — `_domain_boost`

| Table | Entries |
|-------|--------:|
| `_CODE_DOMAIN_EXTENSIONS` | 16 |
| `_CODE_DOMAIN_PATHS` | 4 |
| `_CODE_DOMAIN_KEYWORDS` | 13 |
| `_DOCS_PROSE_DOMAIN_EXTENSIONS` | 5 |
| `_DOCS_PROSE_DOMAIN_PATHS` | 2 |
| `_DOCS_PROSE_DOMAIN_KEYWORDS` | 14 |
| `_PROJECT_META_DOMAIN_PATHS` | 4 |
| `_PROJECT_META_DOMAIN_KEYWORDS` | 14 |
| `_INFRA_DEPLOY_DOMAIN_EXTENSIONS` | 5 |
| `_INFRA_DEPLOY_DOMAIN_PATHS` | 7 |
| `_INFRA_DEPLOY_DOMAIN_KEYWORDS` | 13 |
| **Total** | **97** |

Domains covered: 4. Rule-lines ≈ 117. ≈ 24 entries/domain.
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

## Key Observations

### Config 1: the code/doc discriminator does NOT lift RC at flat CW

At boosts 0.10–0.20, RC *drops* to 0.3211 (−0.0092 vs baseline) while
CW improves to 0.2326. The discriminator is re-routing some entries away
from the correct delegate and into the advisory band. Only at boost=0.25
does RC recover slightly above baseline (+0.0183), still within noise.

**Verdict: `_code_doc_boost` at gap=0.20 provides no honest RC gain.**
The CW improvement (0.2558→0.2326) is real but comes at RC cost.

### Config 2: generalised domain boost = same RC, better CW

Both C2 boosts achieve RC=0.3486 — the same as C1 boost=0.25 — but
with substantially lower CW (0.2143 and 0.1750 vs 0.2500). The
incremental RC gain from adding project_meta and infra_deploy domains
(C2 delta = +0.0275) comes entirely from domain-resolution improvements
in the CW-facing direction, not from true RC uplift beyond C1.

## Bottom Line

At flat CW (≤ 0.2558), the best deterministic discriminator reaches:

**Config 2 boost=0.20: RC=0.3486 at CW=0.1750**
(also: C2 boost=0.15: RC=0.3486 at CW=0.2143; C1 boost=0.25: RC=0.3486 at CW=0.2500)

RC gain over lexical baseline: +0.0183 (+5.5 %).
Remaining gap to two-axis oracle (0.7798): **0.4312**.

The honest read: four discriminator domains and ~97 signal entries buy
5.5 % relative RC gain. The two-axis LLM oracle is at +135 % relative
RC above the same baseline (0.7798 vs 0.3303).

### Slope and scaling

Observed per-domain RC slope from Config 2 data:

- C1 (2 domains, 55 signals): best honest RC = 0.3486, delta = +0.0183
- C2 (4 domains, 97 signals): best honest RC = 0.3486, delta = +0.0183

The RC gain is **identical** between 2 and 4 domains. Adding
project_meta and infra_deploy to the signal tables bought zero
additional RC at flat CW — the slope beyond the first 2 domains
is effectively zero. This is the strongest evidence for a
**deterministic ceiling well below the oracle**.

### Characterisation cost vs LLM cost

| Approach | RC (no_smoke) | Domains | Signal entries | Upkeep |
|----------|:---:|:---:|:---:|--------|
| Lexical baseline | 0.3303 | 0 | 0 | none |
| C1 code/doc boost | 0.3486 | 2 | 55 | per domain add |
| C2 multi-domain boost | 0.3486 | 4 | 97 | per domain add |
| Two-axis oracle (LLM) | 0.7798 | 4 | 0 | fixed taxonomy |

The LLM approach achieves 2.3× the RC of the best deterministic
discriminator with zero keyword-table upkeep and scales to new
agent types by updating the taxonomy, not the signal tables.

**Recommendation:** the deterministic ceiling at flat CW is ~0.35 RC
with diminishing returns from additional domains. The two-axis (#362)
approach is justified not as a refinement but as the necessary path to
reach the Phase-0 floor (~0.55) and approach the oracle (0.78).

### Config 2 covers 4 domains with ~97 signal entries (~24/domain).

`DOMAIN_AGENT_MAP` reuse: **yes** — boost target sets derived directly
from `_cells.DOMAIN_AGENT_MAP` values (deterministic analog of the
two-axis domain hard-gate, fed by lexical inference instead of LLM
label). The architectural mirroring confirms the design is consistent;
the RC gap confirms the LLM label adds information the lexical
inference cannot recover.
