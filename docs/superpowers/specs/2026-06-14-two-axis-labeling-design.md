---
title: Matcher v3 — two-axis (domain × posture) LLM-labeling → Compose routing
touches:
  - src/claude_wayfinder/match/_main.py
  - src/claude_wayfinder/match/_match.py
  - src/claude_wayfinder/match/_cells.py
  - src/claude_wayfinder/match/_types.py
  - src/claude_wayfinder/match/_catalog.py
  - src/claude_wayfinder/match/_decide.py
  - src/claude_wayfinder/cli.py
  - src/claude_wayfinder/build_catalog/_process.py
  - src/claude_wayfinder/build_catalog/_validate.py
  - tests/test_match/test_cells.py
skills_relevant:
  - python
---

# Matcher v3 — two-axis (domain × posture) LLM-labeling → Compose routing

**Issue:** #362 (Milestone 14 — Matcher v3, semantic two-axis). **Date:** 2026-06-14.
**Status:** DESIGN / BRAINSTORM ONLY. This spec is *not* authorization to implement. It is the input
to (and now the revised output of) a `project-reviewer` + `inquisitor` review; implementation lands
under separate impl issue(s) on Milestone 14 (§G.2), gated on the new **Phase 0 — Evidence hardening**
(§A.3) clearing first.

**Version:** **v2 — addresses #362 project-reviewer (2 BLOCKING, 5 CONCERN) + inquisitor (10 charges,
verdict "do not proceed on current evidence basis") review; folds in #374 deterministic-baseline spike.**
The full review record is the historical companion `2026-06-14-two-axis-labeling-design.reviews.md`
(do not edit — it is the frozen record). v1→v2 changelog is §0.

---

## 0. v1 → v2 changelog (what this revision changed and why)

| # | Review finding | v2 change | Section |
|---|---|---|---|
| Inq. C1–C3 | Evidence misrepresented; "floor" is an optimistic point estimate; no held-out distribution; domain axis FAILED its inter-rater target | §A.2 recharacterized honestly (single-rater ≠ label quality; domain inter-rater 0.775 < 0.85 target). New **Phase 0 — Evidence hardening** (§A.3) gates everything: independent non-same-family labeler, ≥2 runs for a variance band, held-out corpus cut, pre-registered bar. Shadow-mode does not start until Phase 0 clears. | §A.2, §A.3 |
| Inq. C4 | Confidence-gating is theater (caller-supplied, unverifiable, fail-open default) | §B adds a **matcher-side plausibility check** (lexical corroboration required before a confident posture-route fires). D-CONF1 flipped to **fail-safe** (absent confidence ⇒ low ⇒ advisory). | §B.1, §D.1 |
| Inq. C5 | Auditability (wayfinder's defining property) quietly spent; trade never confronted | New **§E — Auditability** confronts the trade head-on; logs the labels + lexical-agreement signal + which arm fired; marks the conditional-determinism acceptance a DECISION REQUIRED. | §E |
| Inq. C6 | 41pp gain "largely a tie-breaking artifact"; cheaper deterministic alternative never quantified | **FALSIFIED by #374.** Best deterministic config recovers only ~42% of the gap and only by driving CW to 0.44. New §A.4 "Evidence: deterministic baseline rejected (#374)". | §A.4 |
| Inq. C7 | Kill criteria anchored to pass, not falsify | KC-1 re-anchored to a pre-registered absolute **AND** a no-regression-vs-lexical clause, against the **Phase-0 independent floor** (not the v1 optimistic 0.7431). KC-3 denominator defined. KC-2 retained as hard block, now backstopped by the §B plausibility check. | §F.3 |
| PR-1 (BLOCKING) | Shadow-log injection gap | §G.1 specifies the exact `_write_log_entry` signature extension (`shadow_data: dict[str, Any] | None = None`, merged before `json.dumps`). | §G.1 |
| PR-2 (BLOCKING) | infra_deploy carve-out needs a domain-scoped flag, not a bool | §G.1 replaces the binary toggle with `hard_routing_domains: set[str]`. | §G.1, §F.3 |
| PR CONCERN (a) | `is_any` normalization | §B.1 / §G.1 specify explicit `domain_for_lookup = domain if domain not in (None,"is_any") else "any"`. | §B.1, §G.1 |
| PR CONCERN (b) | BLOCKING-1 prep PR golden test | impl-A golden-equivalence test must assert against current `_main.py` **stdout** (live behavior), not merely `score_entries()`. | §B.2, §G.2 |
| PR CONCERN (c) / Inq. C9 | `posture_routed` rides a consumed serialization contract | §G.1 adds a change-site note to audit every `disposition_source` consumer (`cli.py:136–138`, the §F.2 join). | §C.3, §G.1 |
| PR CONCERN (d) | D-CONF1 sequencing | Caller-side `label_confidence` emission is now a NAMED dependency of impl-E. | §D.1, §G.2 |
| Router (C10) | #364 / #366 states load-bearing & unverified | §H updated: #364 confirmed OPEN; #366 confirmed NOT merged (PR #373 open) → §B.1 fallback is original `_cells.py:132–134` code; "just-merged" wording corrected. | §B.1, §D.3, §H |

---

## A. Scope and evidence basis

### A.1 Scope

Design the **two-axis (domain × posture) LLM-labeling path**: the dispatch caller supplies a `domain`
(5-way) and `posture` (8-way) categorical label per task; the deterministic matcher consumes them via
a **Compose routing variant** that hard-gates on domain, tiebreaks on posture via a cell-map, and falls
back to the existing `decide()` ladder. The matcher stays deterministic; it gains two trusted input
signals (with a matcher-side plausibility check, §B.1, so "trusted" is no longer "blindly trusted").
Out of scope: gain-breakdown reconciliation (37 vs 33, HTML-commented at
`docs/research/2026-06-13-358-oracle-domain-ceiling.md:387`) and task decomposition (#360, NO-GO — per
issue #362 body).

This is the **GO successor to the posture-from-prose regex extractor line killed in #357**. The pivot:
regex extractors routed *worse* than the lexical baseline because framing ambiguity defeats rule-based
inference (`docs/research/2026-06-13-358-oracle-domain-ceiling.md:538` — "framing ambiguity that killed
the regex posture extractors (#357) is resolved by the LLM at 92.7% posture accuracy"). Move the
judgment to the LLM caller, which already reads the full task, and ask it for two cheap categorical
labels rather than regex-inferring them. **The auditability cost of that move is confronted in §E**, and
the residual marginal value over the cheaper deterministic alternative is quantified in §A.4.

### A.2 Evidence basis — HONEST recharacterization (revised per inquisitor Charges 1–3)

All headline numbers below are from `docs/research/2026-06-13-358-oracle-domain-ceiling.md` (the #358
oracle-domain-ceiling experiment). Primary cut: `no_smoke`, n=109. Lexical baseline RC=0.3303,
CW=0.2558 (`:78`, `:320`).

| Claim | Value | Citation |
|---|---|---|
| Domain-only oracle RC gain | +16.5 pp (0.4954) | `:79`, `:534` |
| Two-axis oracle Compose RC | 0.7798 (+44.9 pp) | `:324`, `:535` |
| Two-axis oracle Compose CW | 0.1414 | `:324` |
| Two-axis **real-label** Compose RC | 0.7431 (+41.3 pp) — see CAVEATS below | `:496` |
| Two-axis real-label Compose CW | 0.2430 | `:496` |
| Headroom recovery (real recovers oracle) | 91.8% of RC headroom; oracle↔real gap = 4 entries/109 | `:500`, `:506` |
| Blind **single-rater** labeling accuracy | domain 69.7%, posture 92.7% | `:457`–`:460` |
| Domain errors concentrate in routing-neutral `is_any→project_meta` | 16/16, zero routing cost (is_any ungated) | `:465`–`:469`, `:518` |

**CAVEAT 1 — single-rater accuracy is NOT label quality.** v1 presented domain 69.7% / posture 92.7% as
"label quality" evidence. That is single-rater agreement-against-gold, not reliability. The
pre-registered **inter-rater reliability target for domain was MISSED**: the domain axis scored
**31/40 = 0.775 against a ≥ 0.85 target**, "Below target"
(`docs/research/2026-06-12-gold-labeling-report.md:124`–`:128`). Exact-cell (domain × posture) was
**30/40 = 0.750**, "Pass (at threshold)" — i.e. exactly at the bar, no margin. Only **posture passed
(39/40 = 0.975)** (`:126`). A production caller reproduces the *un-adjudicated* domain noise (≈0.775
inter-rater), not the post-adjudication gold. The gold itself was adjudicated and the rubric
patched post-checkpoint so the 16 systematic `is_any→project_meta` disagreements land routing-neutral
(`docs/research/2026-06-12-gold-labeling-report.md:133`–`:145`); the production caller does not get that
adjudication step.

**CAVEAT 2 — the "floor" (RC 0.7431) is an optimistic point estimate, not a conservative floor.** It is
**a single real-label run** (`run_real_label_compose`, reading one labeler dict from one uncommitted
file) — same-model-family labeler scoring against gold that family produced, under a rubric tuned on
this same 168-entry corpus. There is **no second labeler, no variance band, no held-out distribution**.
Same-family error correlation + rubric overfit + n=1 (no confidence interval) all push the estimate
*up*. Calling 0.7431 "the production floor" (as v1 did) is unjustified. It is a single optimistic point
estimate on the corpus the rubric was tuned to.

**CAVEAT 3 — circular evaluation (no held-out distribution).** The corpus is 168 Phase-A
dispatch-log entries from this project's own usage; the gold agents, `DOMAIN_AGENT_MAP`, `_CELL_MAP`,
and rubric were all derived from / frozen against that one distribution. A KC-1 sample drawn from the
same project's continued usage tests "reproduces on data like what we fit to," not generalization.
`infra_deploy` (n=5 → 0.000 RC) is the canary for distribution-shift exposure.

**Why Compose (not CellMap, not posture-only):** Compose has the lowest CW among two-axis systems
(0.1414 oracle vs CellMap 0.1927) and preserves an abstention fallback — when posture does not select a
gated candidate, it degrades to the existing `decide()` ladder rather than firing a confident-wrong
delegate (`docs/research/2026-06-13-358-oracle-domain-ceiling.md:424`–`:431`). CellMap "always delegates
at 0.9" — every cell-map miss becomes a confident-wrong dispatch (`:429`). Posture-only is non-viable
as a primary axis: `docs_prose` collapses to RC=0.0769 because `(any, build)→code-writer` never reaches
`doc-writer` (`:361`, `:439`).

### A.3 Phase 0 — Evidence hardening (NEW; MANDATORY GATE on everything downstream)

The inquisitor verdict ("do not proceed on the current evidence basis") is a **premise** flaw, not a
wiring detail — it cannot be closed by a doc edit. Phase 0 is a **prerequisite measurement task** that
must clear before any shadow-mode (§F) work begins, and certainly before any hard routing. It exists to
replace the optimistic point estimate (§A.2 CAVEAT 2–3) with a defensible floor.

**Phase 0 deliverables (a research/spike issue, owned on Milestone 14, sequenced BEFORE impl-C):**

1. **Independent labeler.** Re-label the evaluation corpus with a labeler that is **NOT the same model
   family** as the gold producer (the same-family correlation is the core of CAVEAT 2). The label schema
   reuses the existing gold `confidence` field (`docs/research/2026-06-12-gold-labeling-report.md:109`–
   `:115`), not a freshly invented one (see §D.1).
2. **Variance band.** **≥ 2 independent labeling runs** (different seeds/raters), reporting RC and CW as
   a band (min–max or mean ± spread), not a single number. The point of the band is to expose how much
   of 0.7431 is run-to-run noise.
3. **Held-out corpus cut.** Measure on a corpus slice the cell-map AND rubric were **NOT tuned against**
   (CAVEAT 3). If no untuned slice exists, Phase 0's first sub-task is to curate one (a fresh
   dispatch-log cut, de-duplicated against the 168-entry tuned set and against the 35.1% repeated smoke
   probes noted by the inquisitor). This is the only measurement that speaks to generalization rather
   than reproduction.
4. **Pre-registered acceptance bar.** Write the numeric pass bar (the "real-label independent floor")
   **BEFORE** running step 1–3, in this spec or a companion, so the bar cannot be retrofit to the result.
   The Phase-0 floor — not v1's 0.7431 — becomes the anchor for the kill criteria (§F.3).

**Gate statement (plain):** **shadow-mode (§F) does not start, and impl-C does not merge, until Phase 0
clears its pre-registered bar.** If Phase 0's independent, banded, held-out floor lands materially below
0.7431 (e.g. the same-family / overfit optimism was real), the go/no-go on the whole two-axis line is
re-opened at that point — Phase 0 is a real kill gate, not a formality. **DECISION REQUIRED — D-P0:**
the user (or a designated SME) sets the **pre-registered Phase-0 acceptance bar** — this spec proposes a
*method* but deliberately does not invent the number, because pre-registration by the proposing agent is
the same self-grading the inquisitor flagged. See §F.3 for how the bar feeds KC-1.

### A.4 Evidence: deterministic baseline rejected (#374) — inquisitor Charge 6 FALSIFIED

Charge 6 argued the +41pp gain is "largely a tie-breaking artifact" recoverable by the cheaper,
dependency-free, auditability-preserving deterministic alternatives the #358 doc names twice
(calibrate `_DELEGATE_GAP`, `358:275`; differentiate code-writer vs doc-writer lexically, `358:150`).
The #374 spike **quantified** that alternative and **falsifies** the charge on the marginal-gain
question:

| Config | RC | CW | % of the 41.3pp gap recovered | Citation |
|---|---|---|---|---|
| Lexical baseline | 0.3303 | 0.2558 | — | `#374 report §3` (`:85`) |
| Best deterministic, gap=0.00 (Lever A only) | 0.4862 | **0.4457** | 37.8% | `#374 report §5` (`:131`) |
| Best deterministic + code/doc boost (A+B) | **0.5046** | **0.4194** | **42.2%** | `#374 report §5` (`:132`) |
| Two-axis real-label Compose | 0.7431 | **0.2430** | 100% | `#374 report §1` (`:25`) |

**Finding:** the best deterministic config recovers only **~42% of the 41.3pp gap (RC 0.5046 vs
0.7431), and only by driving CW to 0.44** — nearly doubling the confident-wrong rate
(`#374 report §8`, `:232`–`:241`). Every **CW-safe** deterministic setting (gap ≥ 0.05) gains
**≤ 0.0091 RC pp — effectively zero** (`#374 report §3` `:94`, `§8` `:264`). Two-axis holds RC 0.7431 at
CW 0.2430 — matching lexical CW while gaining +41pp (`#374 report §8` `:239`–`:241`). The #374
recommendation is **NO-GO on the deterministic alternative** = two-axis IS justified on marginal gain
(`#374 report §8` `:228`).

**Scope of what this settles, honestly:** Charge 6 (marginal value vs the deterministic alternative) is
falsified. It does **not** settle Charges 1–3 (the floor's own quality) or Charge 5 (auditability) —
#374 measured the *gap to* 0.7431, taking 0.7431 as given. Phase 0 (§A.3) still has to establish that
0.7431 is real on an independent, held-out basis; §E still has to confront the auditability trade. The
deterministic baseline being rejected does not make the two-axis floor itself trustworthy — those are
separate questions, kept separate here.

---

## B. The Compose routing algorithm, wired into the LIVE matcher

### B.1 Algorithm (mirrors the validated offline reference; now with a matcher-side plausibility check)

The live wiring must mirror `scripts/corpus/eval/_systems.py:963`–`1075` (`run_supplied_compose`,
issue #363 reference impl), **plus the new plausibility gate (step 3b) that the offline reference does
not have** — added per inquisitor Charge 4 to remove the "blindly trust an opaque external label"
failure. Per-task steps, in order:

1. **Lexical scoring** (unchanged) — `build_features(context)` → score all routable agents/skills.
2. **Domain hard-gate** — `gate_agents(scored_agents, domain)` drops agents whose declared domain ≠
   labeled domain. `is_any` / `null` / unknown-domain = pass-through, no gate
   (`src/claude_wayfinder/match/_cells.py:124`–`:126`). Empty-after-gate falls back to ungated
   (`_cells.py:132`–`:134`). **This empty-gate fallback is original `_cells.py` code, not the #366
   fix** — see §H (the v1 "just-merged #366" wording was wrong; #366/PR #373 touches the `_systems.py`
   posture-pick guard, a different code path).
3. **Posture tiebreak** — within surviving gated candidates, `cell_map_lookup(domain_for_lookup,
   posture)` (`_cells.py:80`) selects a preferred agent. **Normalization is explicit (CONCERN a):**
   `domain_for_lookup = domain if domain not in (None, "is_any") else "any"` — truthy `"is_any"` must
   normalize to `"any"`, which `domain or "any"` (v1) did NOT do.
4. **Matcher-side plausibility check (NEW, step 3b — inquisitor Charge 4).** A posture-routed
   delegate@0.9 fires **only when BOTH** hold:
   - **(i) caller `label_confidence` is high** (see §D.1 — now fail-safe), AND
   - **(ii) the matcher independently corroborates** the cell-winner: the `preferred` agent must also be
     **lexically plausible** — concretely, `preferred` is among the top-k lexically scored gated agents
     (recommend **k = 3**) **OR** scores above a lexical floor (recommend `score(preferred) ≥
     _DELEGATE_THRESHOLD − ε`; ε small, e.g. 0.15). This is the matcher's own signal, computed from
     `build_features` — it cannot be spoofed by an opaque caller label.
   - Guard before delegating (unchanged from the offline ref): `preferred in gated_names AND preferred in
     catalog_agent_names` (`_systems.py:1044`–`:1048`).
   - **If the lexical signal DISAGREES** with the caller's label (cell-winner is neither top-k nor above
     the floor) → **do NOT fire the confident delegate; fall through to `decide()`** (step 5). The
     disagreement is itself a logged signal (§E, §F.1).
   - On pass of (i) + (ii) + guard → `delegate` at confidence 0.9, `disposition_source="posture_routed"`.
5. **Lexical fallback** — if posture did not select (no cell, preferred not gated, low confidence per
   §D.1, **or the plausibility check (ii) failed**) → fall through to the **existing**
   `decide(gated_agents, scored_skills, features, entries)` on the *gated* candidate list
   (`_systems.py:1054`–`:1058`). This preserves advisory/abstention/mixed_content and the 7-branch
   surface unchanged.

`decide()` thresholds still govern the fallback disposition: delegate (≥0.85 & gap≥0.2), advisory
(≥0.5), etc. (`src/claude_wayfinder/match/_decide.py:40`–`:42`). **No `decide()` branch or threshold
changes** (constraint from issue #362 item C).

**Why the plausibility check matters (Charge 4 rationale):** the §D.1 confidence gate alone is
caller-supplied and unverifiable from inside the deterministic matcher — an overconfident "high" on a
wrong label would otherwise fire a confident-wrong 0.9 delegate with no internal check. The plausibility
check adds a *matcher-owned* second condition that the caller cannot fake: the cell-winner has to also
look right to the lexical scorer. A confident-wrong label now only fires a confident delegate when the
*lexical signal also agrees* — which is exactly the case where firing is least harmful. This keeps
CW bounded by the matcher's own evidence, not solely by the caller's self-assessment. **Note:** the §A.4
finding that CW-safe deterministic config gains ~0 RC does NOT make the plausibility check a "lexical
re-route" — it is a *veto*, not a *selector*. It only ever *blocks* a posture-route into the lexical
fallback; it never *creates* a delegate the lexical scorer wouldn't have allowed. So it cannot drag CW up
the way gap=0.00 did.

### B.2 BLOCKING-1 resolution: the dual scoring-path problem

**Confirmed (and stronger than v1 stated — inquisitor Charge 8):** `_main.py:215`–`:222` has its OWN
inline scoring loop (a sorted `ScoredEntry` list built from `score(e, features)`) that does NOT call
`_match.py:469`–`:503` (`score_entries`) — and `score_entries` is **never called by `main()`** today.
The two are byte-identical logic, but **the validated offline floor runs through `score_entries`
(`_systems.py:1030`) while production runs through the inline `_main.py` copy** — so the production
matcher and the measured floor execute *different copies of the same logic* right now. The reviewer
confirmed they are currently logic-identical → the dedup is a zero-risk extraction.

**RESOLUTION (recommended): refactor-to-single-scoring-path FIRST, as a standalone prep PR, before any
two-axis wiring.** Extract the `_main.py:206`–`:222` inline loop into `score_entries()` (or a thin shared
helper that both call), so the live matcher and the offline harness share one scoring kernel. Rationale:

- The whole floor claim (§A.2) rests on the offline harness's behavior. If the live path diverges in
  scoring (even subtly — sort tiebreak, routable filtering), the in-production RC will not match the
  measured floor, silently invalidating the kill criteria in §F. Deduplicating first makes the live
  Compose wiring a *thin* addition on top of the exact code the floor was measured against.
- A behavior-preserving extraction is independently testable and independently reviewable, keeping the
  two-axis PR focused.

**Golden-equivalence test requirement (CONCERN c / Charge 8 — TIGHTENED):** impl-A's equivalence test
must assert against the **current `_main.py` stdout (live behavior)** over the gold corpus — NOT merely
that `score_entries()` returns the same lists. The two copies are byte-identical *today*; the test must
pin that the refactor preserves the **end-to-end emitted decision** (the production contract), so a
future scoring tweak cannot silently re-diverge the live path from the measured floor.

**DECISION REQUIRED — D-BLK1** (recommended: refactor-first). Alternative is the dual change-site
approach: wire Compose into `_main.py` directly *and* leave `score_entries()` as the harness path,
accepting two scoring implementations kept in lockstep by tests. Cheaper up front but carries permanent
drift risk between the measured floor and production. **Recommend refactor-first; user sign-off requested
because it adds a prep PR to Milestone 14 before feature work.**

Note: `_main.py:206`–`:212` already excludes non-routable/plugin agents via `is_agent_routable`; the
offline `run_supplied_compose` reproduces this at `_systems.py:1015`–`:1017` and threads
`catalog_agent_names` into the posture guard. The refactor must preserve this routable-filtering
behavior in the shared kernel.

### B.3 Where Compose sits in the live `main()` flow

Compose inserts between the existing **score** step (`_main.py:201`–`:222`) and the **decide** step
(`_main.py:225`). Concretely, after the (refactored) scoring call and before `decide(...)`:

```
# pseudo — replaces the single decide() call at _main.py:225
gated = gate_agents(scored_agents, labels.domain)                  # _cells.gate_agents
domain_for_lookup = (labels.domain
                     if labels.domain not in (None, "is_any") else "any")   # CONCERN (a)
preferred = cell_map_lookup(domain_for_lookup, labels.posture)     # _cells.cell_map_lookup
gated_names = {se.entry.name for se in gated}
# matcher-side plausibility (Charge 4): cell-winner must also be lexically plausible
lex_plausible = _is_lexically_plausible(preferred, gated)          # top-k OR score floor; §B.1 step 4
if (labels.posture and preferred and preferred in gated_names
        and preferred in catalog_agent_names
        and labels.confidence_is_high                              # §D.1 — fail-safe (absent ⇒ low)
        and lex_plausible):                                        # §B.1 (ii) — matcher veto
    result = {decision: "delegate", agent: preferred, confidence: 0.9,
              disposition_source: "posture_routed", ...}
else:
    result = decide(gated, scored_skills, features, entries)       # unchanged ladder, gated list
```

The override short-circuit (`_main.py:170`–`:199`) is **upstream** of and unaffected by Compose —
explicit override rules still win first. The density guard, mixed_content, advisory, and all other
`decide()` branches remain reachable via the fallback path.

---

## C. Input-payload and type-model delta

### C.1 Where domain/posture enter the dispatch context (caller → matcher)

The dispatch caller (the LLM that reads the full task) adds optional fields to the stdin JSON documented
at `_main.py:61`–`:72`. **Reuse the existing gold `confidence` field semantics** (the gold schema already
has a `high`/`medium`/`low` `confidence` field —
`docs/research/2026-06-12-gold-labeling-report.md:109`–`:115`); do NOT invent a parallel
`label_confidence` vocabulary (inquisitor Charge 4 flagged v1 inventing a new field while the gold field
existed). Name the wire field `confidence` to match the corpus:

```jsonc
{
  "task_description": "...",
  "file_paths": ["..."],
  // NEW (all optional; absence = is_any / no posture / LOW confidence per the fail-safe default):
  "domain":     "code" | "infra_deploy" | "docs_prose" | "project_meta" | "is_any" | null,
  "posture":    "build" | "diagnose" | "assess" | "critique" | "verify" | "plan" | "research" | "operate" | null,
  "confidence": "high" | "medium" | "low" | null   // §D.1 — reuses the gold field; absent ⇒ treated as LOW
}
```

These ride into a new `Labels` value object threaded alongside `features` through `main()`.
**DECISION REQUIRED — D-LBL1** (recommended: a small frozen `Labels` dataclass, not new `Features`
fields). Rationale: `Features` is the *lexical* feature surface (stemmed keywords, paths, tool mentions)
consumed by `score()`. Domain/posture/confidence are *routing-control* signals consumed only by the
Compose layer, never by `score()`. Keeping them off `Features` avoids label data leaking into lexical
scoring and keeps `build_features` (`_match.py:128`) signature-stable. `build_features` (or a sibling
`parse_labels(context)` helper) reads `context.get("domain")` etc. into the `Labels` object. Alternative:
add nullable fields to `Features` — fewer new types, but couples lexical and routing concerns.
**Recommend the separate `Labels` object.**

### C.2 How the per-agent domain gate is sourced

The hard-gate needs each agent's *declared* domain to compare against the labeled domain. Two options:

- **Option A (recommended): static `DOMAIN_AGENT_MAP` constant** — already implemented and validated at
  `src/claude_wayfinder/match/_cells.py:39`–`:51`. It is the exact map the §A.2 numbers were measured
  against. No catalog-build changes, no frontmatter churn across agents, no migration. Changing the
  sourcing mechanism would re-open the measurement.
- **Option B: declarative `CatalogEntry.domain` from agent frontmatter** — add a `domain:` frontmatter
  field, thread it through `build_catalog/_process.py` (frontmatter fields already flow onto entries
  this way — see `_process.py:545`–`:549` for the `applicable_agents_intentional` precedent) and
  `_catalog.py:407`–`:419` (`CatalogEntry` construction), plus a new `CatalogEntry.domain` field at
  `_types.py:199`–`:206`. More "data-driven" but: (a) decentralizes the validated map into per-agent
  frontmatter, (b) requires `_validate.py` to enforce the 5-way enum, (c) any frontmatter typo silently
  changes gating, (d) does not match what was measured.

**DECISION REQUIRED — D-SRC1** (recommended: **Option A, static map, for v1**). Keep `DOMAIN_AGENT_MAP`
as the single source of truth so production gating is bit-identical to the measured/Phase-0 floor. Defer
declarative frontmatter to a v2 follow-up *after* shadow-mode confirms the map matches live behavior.
**User sign-off requested** — trades author-ergonomics for measurement fidelity.

### C.3 Riding the decision ladder without changing `decide()` — and the serialization-contract audit

Compose adds exactly one new disposition (`disposition_source="posture_routed"`, a `delegate` at 0.9)
*ahead* of `decide()`, and otherwise calls `decide()` **unchanged** on the gated list. None of the 7
branches (`_decide.py:166`–`:177`) and none of the thresholds (`_decide.py:37`–`:48`) are touched. The
only change to `decide()`'s *inputs* is that its `scored_agents` argument is the domain-gated subset.

**Serialization-contract audit (CONCERN c / inquisitor Charge 9 — REQUIRED).** `disposition_source` is a
**consumed discriminator field**, not an ignorable extra key. Existing producers emit only `"scored"`
(`_decide.py:150,198,221,235,274,286`; `_main.py:131`) and `"override"` (`_main.py:179`;
`_dispatch.py:595`; `cli.py:253`). Adding a new **value** (`"posture_routed"`) on that existing key is a
schema change every consumer must tolerate. The impl issues MUST audit and, where needed, update every
`disposition_source` consumer:

- `src/claude_wayfinder/cli.py:136`–`:138` — prints `disposition_source` verbatim; confirm it does not
  branch on the value set (it currently just prints, so adding a value is safe — but pin this with a
  test).
- The **§F.2 shadow-analysis join** that computes the kill criteria — this is itself a consumer that
  *switches on* `disposition_source` to classify routes; a discriminator bug here would corrupt KC
  measurement undetectably. The shadow-analysis tooling (impl-D) must explicitly handle `posture_routed`.

v1 asserted "consumers ignore unknown *keys*" — true for keys, **false for a new value on a consumed
key**. The audit closes that gap.

---

## D. Resolution of the four open design items

### D.1 Item 1 — Preserve an abstention/advisory path + FAIL-SAFE confidence default (revised per Charge 4)

**Problem:** real-label CW (0.243) ≈ lexical (0.256); label noise lands in CW, not RC
(`docs/research/2026-06-13-358-oracle-domain-ceiling.md:522`–`:524`). Real-label Compose delegates
107/109 entries, so a wrong label becomes a confident-wrong dispatch rather than an abstention (`:522`).
A non-confident label must degrade to ADVISORY, not fire a confident-wrong delegate.

**RESOLUTION:** Reuse the caller-supplied `confidence` field (§C.1). The posture-routed delegate (the
only path that fires at fixed 0.9 bypassing `decide()`) is gated on `confidence == "high"` **AND** the
§B.1 matcher-side plausibility check. When confidence is `"medium"`/`"low"` (**or absent — see the
flipped default below**), the matcher **skips the posture-routed shortcut and falls through to `decide()`
on the gated list**, which can abstain to advisory/self_handle via its existing thresholds.

**D-CONF1 — DEFAULT FLIPPED TO FAIL-SAFE (was fail-open in v1).** When `confidence` is **absent**, treat
it as **LOW (degrade to advisory)**, NOT high. Justification (inquisitor Charge 4, direct evidence the
labeling population is *overconfident*): the gold corpus self-rated `low` on only **7 of 168** entries
(`docs/research/2026-06-12-gold-labeling-report.md:109`–`:115`) while single-rater domain accuracy was
**69.7%** — i.e. labelers are wrong far more often (~30% domain error) than they flag themselves as
uncertain. A population that under-reports its own uncertainty must not have "absent" read as "confident."
On a CW-sensitive path, the safe default when the signal is missing is to **degrade**, not to fire.

- **Divergence from the v1 floor-fidelity argument, stated plainly:** v1 argued absent⇒high so the
  measured 0.7431 floor (which posture-routed *every* cell-hit, no confidence gating) would be
  reachable. v2 accepts that fail-safe makes the raw 0.7431 unreachable until the caller emits
  `confidence` — **and that is the correct trade.** The 0.7431 is itself an optimistic point estimate
  (§A.2), so chasing it with a fail-open default optimizes for the wrong number; the CW safety win
  dominates. Phase 0 (§A.3) re-measures the floor *with* the confidence field present anyway, so the
  fail-safe default does not corrupt the Phase-0 anchor.
- **Threshold:** binary `high` vs not-`high` for v1. The named guard `POSTURE_ROUTE_MIN_CONFIDENCE`
  lets a future numeric/calibrated confidence replace the binary without touching the call site.
- **Caller-emission is a NAMED dependency (CONCERN b).** Hard routing (impl-E) MUST NOT enable until the
  dispatch-caller skill emits `confidence`; this dependency is pinned in §G.2 so the fail-safe default
  is never the *operative* path under live routing.

### D.2 Item 2 — Sharpen the `is_any` vs `project_meta` domain rubric

**Problem:** 16/16 `is_any` entries were blind-mislabeled to `project_meta` — VCS/GitHub tasks with no
file paths read as `project_meta` to the labeler
(`docs/research/2026-06-13-358-oracle-domain-ceiling.md:465`–`:469`; corroborated as a *single systematic
rubric ambiguity* at `docs/research/2026-06-12-gold-labeling-report.md:133`–`:139`). **Zero routing cost
today** because `is_any` is ungated (`_cells.py:50`, `DOMAIN_AGENT_MAP[None] = None`) AND `project_meta`'s
gate includes `project-planner` via `ANY_DOMAIN_AGENTS` (`_cells.py:45`–`:47`, `:30`–`:37`), so the
mislabel does not change routing (`:518`).

**RESOLUTION:** a **rubric/labeler-prompt fix, not a matcher-code fix**, **not load-bearing for v1**
(zero routing cost confirmed). (a) Document in the caller's labeling rubric that repo-level VCS/GitHub
activity *without file-path scope* is `is_any`, reserving `project_meta` for tasks that operate *on the
project's meta-artifacts* (issues, PRs, milestones) as the primary object. (Note: the gold rubric was
already amended post-checkpoint for this exact ambiguity —
`docs/research/2026-06-12-gold-labeling-report.md:144`–`:145` — so this is harmonizing the *caller's*
rubric with the already-amended *gold* rubric.) (b) Carry as a **tracked follow-up that becomes
load-bearing IF and only IF `is_any` gating is ever introduced** (`:543`). No v1 matcher change. The
rubric text is owned by the dispatch-caller skill, not this matcher repo — flagged in §G.2.

### D.3 Item 3 — Fix the `infra_deploy` cell-map mis-cell (coordinate with #364)

**Problem:** both oracle and real Compose score **0.000 RC on `infra_deploy`** (n=5)
(`docs/research/2026-06-13-358-oracle-domain-ceiling.md:355`, `:514`). The defect is in `_CELL_MAP` /
`DOMAIN_AGENT_MAP`, not the labeler (`:544`, `:518`). Two failure modes:

1. `(infra_deploy, research) → researcher` via the `(any, research)` fallback, but gold is
   `investigator` (`_cells.py` has no `(infra_deploy, research)` key; falls back to
   `(any, research)→researcher` at `_cells.py:70`; loss analysis at `:435`).
2. `infra_deploy` gate omits `code-writer`, costing the one hard-gate loss id=34760
   (`_cells.py:48`–`:49`; `:106`, `:139`).

**Coordination with #364 — explicit ownership ruling:** The `_cells.py` module header
(`src/claude_wayfinder/match/_cells.py:4`–`:11`) already documents these discrepancies as **deferred to
#364**: (a) `infra_deploy` gate omits `"code-writer"`; (b) `("code", "diagnose")→"debugger"` not
`"investigator"`; (c) `("infra_deploy", "research")` resolving to `"researcher"` not `"investigator"`.

**RULING: #364 OWNS the cell-map / `DOMAIN_AGENT_MAP` data fix. This spec (and its impl issues) does NOT
modify `_CELL_MAP` or `DOMAIN_AGENT_MAP` contents.** The discrepancies are catalogued against #364 in the
module header (the durable on-disk record), and the fix is a gold-corpus-tuning exercise, not a wiring
exercise. Coordination contract:

- **#362 impl depends on #364 landing the `infra_deploy` cell fix** to realize the `infra_deploy` RC.
  Until #364 lands, `infra_deploy` (n=5, 4.6% of no_smoke) remains at 0.000 RC — the measured 0.7431
  *already* includes this 0.000-`infra_deploy` slice (`:514`), so #362 can ship and hit its floor
  *without* #364; #364 is *strictly additive*.
- **Sequencing:** #362 wiring and #364 cell-map fix are independent PRs. When #364 merges, the live
  Compose path picks up the corrected cells automatically (reads `_cells.py` at runtime). No #362-side
  change needed.
- **Anti-duplication guard:** the #362 impl issues must NOT touch `_CELL_MAP`/`DOMAIN_AGENT_MAP` literal
  contents. Any test pinning `infra_deploy` routing belongs to #364. The #362 tests assert *wiring*
  (gate → cell-map → fallback order), parameterized over the *current* cell-map contents, so they stay
  green across the #364 fix.

**#364 state (router-verified):** **#364 is OPEN / unmerged as of 2026-06-14**
(`2026-06-14-two-axis-labeling-design.reviews.md:6`). The §D.3 coordination contract holds: #362 ships
against the current cell-map (incl. the `infra_deploy` 0.000-RC mis-cell). The *content* of the deferral
is verified on disk at `_cells.py:4`–`:11`.

### D.4 Item 4 — Shadow-mode rollout

Resolved in §F. Summary: **after Phase 0 (§A.3) clears, ship Compose in shadow-mode — log the labeled
route alongside the live lexical decision, change no dispatch behavior — and only enable hard routing
after the written kill criteria (§F.3) are met on in-situ data.**

---

## E. Auditability — confronting the trade head-on (NEW; inquisitor Charge 5)

Auditability is wayfinder's defining property: a lexical decision is fully reproducible from the matcher
inputs — anyone can re-run `build_features → score → decide` and get the same answer and *see why*. The
two-axis path **moves the decisive judgment (domain + posture) to the caller's LLM labels, which the
matcher cannot reproduce.** A `posture_routed@0.9` delegate's rationale is, at root, "the caller said
domain=code, posture=build" — and re-labeling the same task is not deterministic. This is the same
property #357's regex extractors were killed for lacking *accuracy to justify*; here we are spending it
deliberately, so the spec must confront the trade rather than bury it.

**What is lost:** the *why* behind a posture-route is outside the matcher. The matcher can log *what*
labels arrived and *what* it did with them, but not *why* the caller chose them.

**Mitigations (make a wrong `posture_routed` delegate triageable from the log alone, not only by hand
re-labeling):** the shadow log (§F.1) and the live log on every posture-routed dispatch capture:

1. The **labels** (`domain`, `posture`, `confidence`) that arrived.
2. The **lexical-agreement signal** — the §B.1 plausibility result: was `preferred` top-k / above floor,
   and the lexical scores of the gated candidates. This is the matcher's *own* reproducible evidence,
   recorded alongside the un-reproducible label.
3. **Which arm fired** (`disposition_source`: `posture_routed` vs the gated-`decide()` fallback) and, on
   fallback, *why* (no cell / not gated / low confidence / plausibility veto).

With (1)–(3), a wrong route is diagnosable from the log: you can see the label, see whether the matcher's
own lexical signal corroborated or was overridden by confidence, and see which arm fired — without
re-labeling. The plausibility check (§B.1) is what makes (2) meaningful: because a confident posture-route
now *requires* lexical corroboration, a logged `posture_routed` with the lexical-agreement field present
is partially self-justifying.

**What remains genuinely un-auditable:** when the caller's label is the *sole* reason a route differs
from the lexical decision and the plausibility check passed (lexical was merely *compatible*, not
*decisive*), the matcher still cannot reconstruct the caller's reasoning. The conditional determinism is
real: identical matcher inputs + different caller labels → different routes.

**DECISION REQUIRED — D-AUDIT1 (genuinely the user's call):** is the conditional determinism acceptable,
given wayfinder's "post-cognitive" premise — the caller IS the router that already read the full task, so
moving the domain/posture judgment to it is arguably *where that judgment belongs*, and the mitigations
above make wrong routes log-triageable? This spec's position: the trade is **defensible** under the
post-cognitive premise *and* the §B.1 plausibility backstop, but the acceptance is a value judgment about
how much auditability wayfinder is willing to spend, which the proposing agent should not make
unilaterally. **Flagged as DECISION REQUIRED for the user.**

---

## F. Shadow-mode rollout specification

### F.1 What gets logged (no behavior change in shadow mode)

In shadow mode, `main()` computes the Compose decision **but emits the live lexical `decide()` decision**
(current behavior) to stdout. The Compose decision goes to a **shadow log record** only. Per dispatch:

| Field | Source | Purpose |
|---|---|---|
| `session_id`, `catalog_hash`, `matcher_version` | existing log plumbing (`_catalog.py:359`, `:366`–`:367`) | join key + provenance |
| `domain`, `posture`, `confidence` | caller-supplied (§C.1) | the labels under test |
| `live_decision`, `live_agent`, `live_confidence`, `live_disposition_source` | the emitted lexical `decide()` result | the actual dispatch the user got |
| `shadow_decision`, `shadow_agent`, `shadow_confidence`, `shadow_disposition_source` | the Compose result | the route Compose *would* have taken |
| `gated_agent_names`, `posture_preferred`, `posture_routed` (bool) | Compose intermediate state | per-step diagnosis |
| `lexical_agreement` (bool + scores) | §B.1 plausibility check result | **auditability (§E): was the cell-winner lexically corroborated** |
| `posture_veto_reason` | which §B.1 condition failed (if fallback) | triage |
| `agreement` (bool) | `live_agent == shadow_agent` | headline shadow metric |

The shadow record rides the **existing dispatch-log JSONL** path via the explicit `_write_log_entry`
signature extension specified in §G.1 (BLOCKING-1).

### F.2 Comparison method (labeled route vs live lexical decision)

1. **In-situ agreement rate** — fraction where `shadow_agent == live_agent`. Establishes how often
   Compose changes the route at all. Very high agreement → Compose inert; moderate disagreement expected.
2. **Disagreement triage** — every disagreement carries enough state (`gated_agent_names`,
   `posture_preferred`, `lexical_agreement`, both dispositions) to classify *why* Compose diverged. This
   is the in-production analogue of the §358 gains breakdown, **and the §E auditability mitigation in
   action.**
3. **Gold-anchored accuracy where available** — when a shadow-logged dispatch later acquires a gold
   label, compute RC and CW for *both* live and shadow on that subset. This is the only way to confirm
   the **Phase-0 floor** (§A.3) reproduces in production rather than just measuring agreement.

**The analysis tooling (impl-D) is a `disposition_source` consumer** (§C.3 / Charge 9) — it switches on
the value to classify routes, so it must explicitly handle `posture_routed`.

### F.3 WRITTEN kill criteria (must be met before hard routing) — re-anchored to falsify (Charge 7)

Hard routing is enabled **only if all of the following hold** on a shadow-mode sample of **≥ 100
gold-anchored in-situ dispatches** (matching the n=109 no_smoke scale, `:50`). The numeric anchors are
the **Phase-0 independent floor** (§A.3), NOT v1's optimistic 0.7431. Let **`F_indep`** = the
pre-registered Phase-0 real-label independent floor (RC), and **`F_indep_lo`** = the low end of its
variance band.

| # | Criterion | Threshold | Anchor / rationale (re-anchored to falsify) |
|---|---|---|---|
| KC-1 | Shadow Compose RC on the gold-anchored sample | **(i) ≥ `F_indep_lo` − 0.05** (pre-registered absolute) **AND (ii) ≥ lexical RC + 0.20** (no-regression-vs-lexical margin) | v1's RC≥0.65 was a 10pp rubber-stamp *below* the point estimate — it would pass a result that merely "beat lexical a bit." The two-clause form FALSIFIES: clause (i) ties the bar to the *independent, banded* floor (so it can't be passed by an optimistic single run), clause (ii) requires the gain over lexical (0.3303) to be substantial (≥ +20pp), not marginal. If `F_indep_lo` lands much below 0.7431, KC-1 tightens automatically — which is the point. **The exact `F_indep_lo` is set by D-P0 before measuring.** |
| KC-2 | Shadow Compose CW on the gold-anchored sample | **≤ lexical CW (0.2558, `:78`)** | HARD BLOCK. CW must not exceed lexical. The §D.1 fail-safe + the **§B.1 matcher-side plausibility check** are the two independent backstops holding CW at-or-below lexical — KC-2 is no longer enforced *solely* by the caller's confidence (Charge 4 fix). CW above lexical means a backstop failed → do not enable. |
| KC-3 | Decisiveness on the **eligible set** | **≥ 0.55 of the ELIGIBLE set** routes as posture-routed *or* gated-delegate | **Denominator defined (CONCERN d):** the eligible set = dispatches where (domain is gated, i.e. not `is_any`/`null`) AND (a cell exists for `(domain_for_lookup, posture)`) AND (confidence is `high`). On *that* set, real-label Compose delegated ≈ 0.98 (`:522`); requiring ≥ 0.55 ensures Compose is not silently degrading the eligible set to all-advisory (which would mean it adds nothing). Dispatches outside the eligible set (ungated, no cell, or low-confidence) are *expected* to fall through and are excluded from the denominator. |
| KC-4 | Domain-label routing-neutrality holds | `is_any`/`project_meta` mislabels cause **0** route changes vs their oracle routing on the sample | the 16/16 `is_any→project_meta` mislabels must remain zero-cost (`:518`); non-zero cost ⇒ gate semantics drifted. |
| KC-5 | No `infra_deploy` regression vs current lexical | `infra_deploy` shadow RC **≥** `infra_deploy` lexical RC (0.600, `#374 report §6` `:151`) **OR** `infra_deploy` is excluded from hard routing via `hard_routing_domains` (see below) **OR** #364 has landed | Compose scores 0.000 on `infra_deploy` until #364 (`:514`); #374 confirms lexical scores **0.600** there (`:151`) — so enabling hard routing on `infra_deploy` while Compose underperforms lexical is a clear regression. |

**infra_deploy carve-out — DOMAIN-SCOPED FLAG, not a bool (BLOCKING-2):** the rollout flag is
**`hard_routing_domains: set[str]`** (the set of domains for which posture-routing is live), NOT a binary
shadow→live toggle. This lets `infra_deploy` be excluded from hard routing (left on lexical) until #364
lands, while the other domains go live. **Alternative (if the user prefers simplicity):** drop the
carve-out entirely and make **KC-5 failure block hard-routing for ALL domains** until #364 lands.
**DECISION REQUIRED — D-CARVE1: pick the domain-scoped `hard_routing_domains` set (recommended,
finer-grained) OR the all-or-nothing KC-5 block.** Specified in §G.1 either way.

**Kill decision:** KC-2 fail → do not enable (CW regression is cardinal). KC-1 fail → investigate
distribution shift / labeler quality before enabling. KC-5 fail → exclude `infra_deploy` from
`hard_routing_domains` (or block all per D-CARVE1). KC-4 fail → code investigation (gate-semantics drift).

**DECISION REQUIRED — D-KC1:** confirm/adjust KC-1's no-regression margin (+0.20), KC-2 (≤ lexical CW),
KC-3 (≥ 0.55 of the eligible set). These are the go/no-go gate for hard routing. **The KC-1 absolute
clause is bound to D-P0 (the Phase-0 bar) and cannot be finalized until Phase 0 sets `F_indep_lo`.**

---

## G. Concrete change-sites and proposed implementation issue breakdown

### G.1 Change-sites (file:line)

| Concern | File:line | Change |
|---|---|---|
| Dual scoring path (BLOCKING-1 / Charge 8) | `src/claude_wayfinder/match/_main.py:206`–`:222` ↔ `src/claude_wayfinder/match/_match.py:469`–`:503` (`score_entries`) | refactor to single shared kernel (§B.2); golden test vs current `_main.py` **stdout** |
| Compose insertion point | `src/claude_wayfinder/match/_main.py:225` (the `decide(...)` call) | wrap with gate → cell-map → plausibility → fallback (§B.1/§B.3) |
| `is_any` normalization (CONCERN a) | inside the Compose helper | `domain_for_lookup = domain if domain not in (None, "is_any") else "any"  # truthy is_any must map to any` |
| Matcher-side plausibility (Charge 4) | new `_is_lexically_plausible(preferred, gated)` helper near the Compose insertion | top-k (k=3) OR `score(preferred) ≥ _DELEGATE_THRESHOLD − ε`; veto-only (§B.1 step 4) |
| Label parsing | `src/claude_wayfinder/match/_match.py:128` (`build_features`) or new `parse_labels` | read `domain`/`posture`/`confidence` from context (§C.1) |
| Label value object | `src/claude_wayfinder/match/_types.py:209`–`:241` | new frozen `Labels` dataclass (D-LBL1) |
| Gate + cell-map (consume only) | `src/claude_wayfinder/match/_cells.py:80`, `:97` | imported, **not modified** (#364 owns contents) |
| Posture-routed disposition | `src/claude_wayfinder/match/_decide.py` | NOT modified; new disposition built in the Compose helper, NOT in `decide()` (§C.3) |
| **`disposition_source` consumer audit (CONCERN c / Charge 9)** | `src/claude_wayfinder/cli.py:136`–`:138` (prints value); impl-D shadow-analysis join (§F.2) | audit every consumer of the `disposition_source` *value* set for the new `"posture_routed"`; pin `cli.py` print-only behavior with a test; impl-D must classify `posture_routed` explicitly |
| **Shadow logging (BLOCKING-1)** | `src/claude_wayfinder/match/_catalog.py:322`–`:328` (`_write_log_entry` signature) + entry dict `:360`–`:369` + `json.dumps` `:373`; called from `_main.py:228` | **extend signature: add `shadow_data: dict[str, Any] | None = None`** as a new keyword param (after `override_id`); when not None, **merge `shadow_data` into the `entry` dict before `json.dumps`** (e.g. `entry.update(shadow_data)` or `entry["shadow"] = shadow_data`); **default None ⇒ all existing non-shadow call sites are byte-unchanged** |
| **Rollout flag (BLOCKING-2)** | rollout-control surface near the Compose insertion (`_main.py`) + a config read | **`hard_routing_domains: set[str]`** — domain-scoped enable; empty set = full shadow mode; `infra_deploy ∉ set` until #364 lands (or D-CARVE1 all-or-nothing) |
| Domain source (only if Option B) | `_types.py:199`–`:206`, `_catalog.py:407`–`:419`, `build_catalog/_process.py:545`-precedent, `build_catalog/_validate.py` | only if D-SRC1 picks declarative frontmatter — **not recommended for v1** |
| Wiring tests | `tests/test_match/test_cells.py` + new `tests/test_match/test_compose.py` | assert gate→cell-map→plausibility→fallback order, confidence fail-safe, plausibility veto, no `decide()` change, `posture_routed` serialization |

### G.2 Proposed implementation issues (sequenced, Milestone 14)

*Proposed* impl issues to create after spec sign-off (do NOT create yet; do NOT implement). **Phase 0
(§A.3) is the first gate and precedes the shadow-mode core.**

0. **#362-impl-0 — Phase 0: Evidence hardening (research/spike, MANDATORY GATE).** Independent
   (non-same-family) labeler, ≥2 runs for a variance band, held-out corpus cut, pre-registered
   acceptance bar `F_indep` (D-P0). Output: the real-label independent floor + band that anchors KC-1.
   **Blocks impl-C from merging.** Resolves inquisitor Charges 1–3 (or kills the line if the floor
   collapses).
1. **#362-impl-A — Scoring-kernel dedup (prep, BLOCKING-1).** Extract `_main.py:206`–`:222` into the
   shared `score_entries()` kernel; **golden-equivalence test vs current `_main.py` stdout** on the gold
   corpus (CONCERN c). No behavior change. *Blocks B.* (Resolves D-BLK1 once user confirms refactor-first.)
2. **#362-impl-B — `Labels` type + context parsing.** Add the frozen `Labels` dataclass and
   `domain`/`posture`/`confidence` parsing; no routing change yet. *Depends on A.*
3. **#362-impl-C — Compose wiring in shadow mode** (incl. the §B.1 matcher-side plausibility check and
   the §E auditability log fields). Gate → cell-map → plausibility → fallback computed; shadow record
   logged via the `shadow_data` param; **live decision unchanged**. *Depends on B **and on impl-0
   (Phase 0) clearing**.* The de-risking core.
4. **#362-impl-D — Shadow analysis tooling + kill-criteria report.** Compute KC-1..KC-5 from shadow JSONL
   against gold (§F.2/§F.3); **must handle the `posture_routed` `disposition_source` value** (§C.3).
   *Depends on C producing data.*
5. **#362-impl-E — Enable hard routing (gated on KC pass).** Flip via `hard_routing_domains` behind the
   config flag; merges only after §F.3 kill criteria are met. **NAMED DEPENDENCY (CONCERN b / D-CONF1):
   the dispatch-caller skill must emit `confidence` BEFORE impl-E enables**, so the fail-safe default is
   never the operative live path. *Depends on D + Phase-0 floor + caller-emission + user go.*
6. **Caller-side (separate repo/skill, tracked from #362):** labeling rubric update for `is_any` vs
   `project_meta` (§D.2) and **`confidence` emission (§D.1, a NAMED dependency of impl-E)**. Owned by the
   dispatch-caller skill, not this matcher repo — flag to router for routing to the right surface.

**Out of #362 scope (owned elsewhere):** `_CELL_MAP`/`DOMAIN_AGENT_MAP` content fixes → **#364** (§D.3).
Gain-breakdown reconciliation and task decomposition → deferred (§A.1).

---

## H. Citations

Every decision-driving claim cites the research doc
(`docs/research/2026-06-13-358-oracle-domain-ceiling.md:Lx`), the gold-labeling report
(`docs/research/2026-06-12-gold-labeling-report.md:Lx`), the #374 deterministic-baseline report, the
review record (`2026-06-14-two-axis-labeling-design.reviews.md:Lx`), or the live-code map
(`src/.../file.py:Lx`), inline at point of use above.

**Router-resolved (formerly `unverified:`) — both now confirmed (`reviews.md:5`–`:7`):**

1. **#364 is OPEN / unmerged as of 2026-06-14** (`reviews.md:6`). The §D.3 coordination contract holds:
   #362 ships against the current cell-map incl. the `infra_deploy` 0.000-RC mis-cell.
2. **#366 is NOT merged (PR #373 OPEN, CI pending)** (`reviews.md:7`). v1's §B.1 "just-merged #366"
   wording is **corrected** in this v2: the §B.1 empty-after-gate fallback the algorithm relies on is
   **original `_cells.py:132`–`:134` code**, not the #366 fix (#366/PR #373 touches the `_systems.py`
   posture-pick guard, a different path), so the dependency holds regardless of #366's state.

**Remaining provenance flag (NEW in v2 — must be resolved before the #374 evidence is treated as durable):**

3. `unverified:` **the #374 report's `main`-merge status.** This design pass found
   `2026-06-14-374-deterministic-baseline.md` **only in the `.worktrees/374-deterministic-baseline/`
   worktree**, NOT under `docs/research/` on `main`; the report header states branch
   `spike/374-deterministic-baseline` (`#374 report:4`). The brief states it was "merged as PR #375
   work" and cites a `main`-relative path. The *content* (the recovery percentages, CW figures, and
   NO-GO recommendation in §A.4) is read directly from the report and is accurate as cited; the
   **`main`-merge of PR #375 was not re-confirmed via GitHub in this pass.** Router/user to confirm PR
   #375 landed before §A.4 is relied on as a durable `main` citation; if #375 is still open, cite the
   report at its worktree/branch provenance until merge. All §A.4 figures cite the report by section/line
   so they are verifiable wherever the file resolves.

All other claims are file:line- or research-section-grounded.
