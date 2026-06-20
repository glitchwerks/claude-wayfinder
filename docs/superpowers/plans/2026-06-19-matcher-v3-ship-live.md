---
title: Matcher v3 two-axis routing — ship offline-validated Compose live to the marketplace
touches:
  - src/claude_wayfinder/match/_main.py
  - src/claude_wayfinder/match/_compose.py        # NEW — hot-path Compose layer (proposed)
  - src/claude_wayfinder/match/_types.py           # NEW Labels value object
  - src/claude_wayfinder/match/_cells.py           # consume only (cell-map content owned elsewhere; #364 gate fix already shipped)
  - scripts/corpus/eval/_systems.py                # READ-NOT-MODIFY — M15-2 normative source (run_supplied_compose)
  - src/claude_wayfinder/match/_catalog.py         # _write_log_entry shadow_data extension
  - src/claude_wayfinder/cli.py                     # disposition_source consumer audit
  - tests/test_match/test_compose.py               # NEW integration tests for live path
  - pyproject.toml                                  # version bump
  - CHANGELOG.md
  - .claude-plugin/plugin.json                      # version bump
  - glitchwerks/claude-plugins/.claude-plugin/marketplace.json  # SEPARATE REPO — pin bump
skills_relevant:
  - python
  - claude-code-plugin-authoring
  - claude-github-tools:github-actions
---

# Matcher v3 two-axis routing — ship live to the marketplace

**Status:** PLAN / FOR REVIEW. This plan is *not* authorization to implement. Per CLAUDE.md
§ Issue Tracking, creating the proposed M15 milestone + issues is the user's go-ahead; this document
is the input to that decision. Several DECISION REQUIRED items (§7) must be resolved before issues are
created.

**Initiative:** Wire the offline-validated Matcher v3 two-axis (domain × posture) Compose routing into
the live `python -m claude_wayfinder dispatch` runtime, validate it in shadow mode against the lexical
baseline, flip it on behind a flag, and ship the result to consumers via a coordinated two-repo release.

**Design source:** `docs/superpowers/specs/2026-06-14-two-axis-labeling-design.md` (Spec v2 — the
canonical two-axis design; this plan is the *shipping* counterpart it defers to in its §D.4 / §F / §G.2).

---

## 0. Material correction to the dispatching brief (verify before relying on the original framing)

> **Revision note (post project-reviewer pass, verdict: revise-then-proceed).** This plan was revised
> after a `project-reviewer` pass and a router-confirmed ground-truth check against disk + GitHub. The
> most consequential change: **#364 has CLOSED/COMPLETED** (2026-06-19T12:30:15Z) — `code-writer` is now
> in the `infra_deploy` gate (`src/claude_wayfinder/match/_cells.py:63`). The old "`infra_deploy` Compose
> scores 0.000 until #364 lands" framing is **stale and has been removed** wherever it drove a decision
> (risk table, D-CARVE1, KC-5, §3 NOT-in-M15). The remaining intentional discrepancies are **cell-map
> CONTENT** (`("code","diagnose")→"debugger"`, `("infra_deploy","research")→"researcher"`), tracked
> separately — M15 still must not touch `_CELL_MAP` / `DOMAIN_AGENT_MAP` literals (`_cells.py:4`–`:16`,
> `:52`–`:65`, `:67`–`:80`). The §10 `unverified:` GitHub flags are now resolved against confirmed state.

### Decisions log

- **2026-06-19** — Q3/Q5 RESOLVED: caller-supplied labels (`area_span` = caller field, default 1); no
  runtime encoder/extractors; M15-9 dropped. Decided by user/SME via router. (Cascade applied to §1.1,
  §1.2, §2 Phase 1, §3, §5, §6, §7, §10; frontmatter `touches:` `pyproject.toml` rationale narrowed to
  version bump.)

The brief I was dispatched with contained three claims that on-disk inspection **falsifies or
qualifies**. They materially change the plan, so they are surfaced up front:

1. **"Phase 0 = a go/no-go gate the user must still decide; the plan must NOT assume v3 ships."**
   The evidence-hardening Phase 0 gate the spec demanded (`...two-axis-labeling-design.md:124`–`:155`,
   §A.3) has **already been executed twice and now CLEARS**:
   - Phase 0 (independent GPT labeler, #382): **NO-GO** against the pre-registered bar
     (`docs/research/2026-06-15-phase0-independent-floor.md:206`–`:219`, `:265` — no_smoke RC 0.5505
     < 0.60 floor; full-cut CW 0.2699 > 0.2558). It explicitly recommended reopening the design loop
     and running Phase 0b (`:309`–`:325`).
   - Phase 0b held-out validation (#387, parent #386, dated 2026-06-17 — two days before this plan):
     **GO**. On a *true held-out* set of 150 fresh contexts the cell-map and rubric were never tuned
     against, scoped RC = 0.7391 (bar ≥ 0.60, PASS) and CW = 0.2031 (bar ≤ 0.2558, PASS), with
     near-zero generalization gap vs in-sample (RC −0.0005, CW +0.0009)
     (`docs/research/2026-06-17-heldout-validation.md:57`–`:70`, `:159`–`:171`, `:204`–`:214`).
     Verdict, verbatim: "the two-axis labeler is **production-ready as the oracle label supply for
     Phase 1 implementation**" (`:213`–`:214`).

   **Consequence:** the heavy, kill-or-proceed *evidence* gate is **done and passed**. What remains is a
   lighter **production-readiness go/no-go** (does the held-out evidence justify spending the
   auditability cost and shipping the wiring — §E / D-AUDIT1 of the spec), plus the in-situ shadow
   confirmation that the offline floor reproduces on live traffic. This plan keeps a Phase 0 *gate* but
   recasts it from "earn the floor" to "ratify the earned floor and authorize wiring." The plan does NOT
   assume the user *will* ratify — but it would be wrong to plan as if the offline evidence were still
   unestablished.

2. **"The live path runs an inline scoring loop (`_main.py:215`–`:222`) that the BLOCKING-1 prep PR must
   dedup into `score_entries`."** On-disk, `_main.py:203` **already calls** `score_entries(entries,
   features)` — the shared kernel at `src/claude_wayfinder/match/_match.py:469`–`:502`
   (`src/claude_wayfinder/match/_main.py:199`–`:206`). The inline-loop dual-path the spec's impl-A
   (`...two-axis-labeling-design.md:639`–`:641`) and the brief's gap #2 both describe **is already
   resolved**; the live matcher and the offline harness share one scoring kernel today. **The dedup prep
   PR is therefore unnecessary** — this removes a whole work item from the brief's 7-gap list. (The
   *Compose-glue* refactor — brief gap #2's other half, moving `run_supplied_compose` into `src/` — is
   still needed and is its own item below.)

3. **Brief gap #2 names `_route_from_postures` / `_run_all_extractors` /
   `_postures_from_extractor_results` as the glue to move into `src/`.** These are the **deterministic
   E1–E12 posture-extractor** route (`scripts/corpus/eval/_systems.py:203`, `:271`, `:429`), which is
   the **killed #357 regex line**, NOT the GO design. The spec's chosen path is
   **`run_supplied_compose`** (`_systems.py:1158`; caller-supplied labels —
   `...two-axis-labeling-design.md:56`–`:71`, §A.1; "Move the judgment to the LLM caller … rather than
   regex-inferring them"). **Wiring the extractor glue would resurrect the approach #357 killed.** The
   item to lift into `src/` is `run_supplied_compose` and its `_cells.py` consumers — *not* the
   extractor functions or `POSTURE_PRIORITY`. This is the single most important correction in the plan:
   getting it wrong reintroduces a measured regression.

These corrections are why this plan is shorter and more shippable than the brief's 7-gap framing implies:
one gap (scoring dedup) is already done, and one (which glue to move) was pointed at the wrong code.

---

## 1. Overview

### 1.1 The crux (confirmed on disk)

`python -m claude_wayfinder dispatch` runs **only the lexical/keyword scorer** today. The live path
(`src/claude_wayfinder/match/_main.py:147`–`:218`) is `build_features` → `score_entries` → `decide` (the
7-branch ladder). No domain encoder, no posture labels, no `_cells.py` cell-map, and no `posture/`
extractors are called at runtime — the v3 code is present in `src/` but **dormant** (the `_cells.py`
header documents its own deferred discrepancies to #364; the `posture/` package is imported only by the
offline eval harness). The Compose integration glue lives **only** in
`scripts/corpus/eval/_systems.py:1158`–`:1312` (`run_supplied_compose` @ `079e787`) and must move into a
shippable `src/` module. (The `_cells.py` header documents the *remaining* intentional cell-map content
discrepancies; the `infra_deploy` gate fix it once deferred to #364 has since **shipped** — `_cells.py:7`,
`:63`.)

**[RESOLVED 2026-06-19: caller-supplied]** The lifted module consumes `domain`, `posture`, `confidence`,
**and `area_span`** from the dispatch-context stdin JSON (caller-supplied, read by `parse_labels` / the
`Labels` value object) — it runs **no runtime encoder and no runtime extractors (E1–E12)** on the hot path.
`area_span` defaults to **1** when the caller omits it. The `posture/` extractor package and the domain
encoder stay offline-only (eval harness). See §5 and §7 RESOLVED decisions (P0).

### 1.2 Phasing strategy

| Phase | Goal | Gate to exit |
|---|---|---|
| **P0 — Production-readiness ratification** | SME/user confirms the held-out GO (§0.1) justifies shipping, and resolves the remaining OPEN DECISION REQUIRED items (§7): D-AUDIT1, D-LBL1, D-SRC1, D-CARVE1, D-KC1, plus this plan's D-FLAG1, D-SEMVER1. (**Q3/Q5/D-ENCODER1 already RESOLVED 2026-06-19** — caller-supplied labels; no runtime encoder/extractors.) | Written go-ahead + decisions recorded on the M15 tracking issue. |
| **P1 — Compose lifted into `src/` (no behavior change)** | Move `run_supplied_compose` + the `Labels` type + label parsing into `src/`; add the matcher-side plausibility veto. Live dispatch behavior **unchanged** (Compose computed but not emitted). | New `src/` Compose module + unit tests green; live stdout byte-identical to current over the gold corpus. |
| **P2 — Shadow mode** | Compute Compose per-dispatch, log it alongside the live lexical decision via the `shadow_data` log extension; emit the lexical decision unchanged. | Shadow JSONL accumulating clean records (per #316, `attribution_source == post_tool_use_hook` entries are the clean comparison set); analysis tooling computes KC-1..KC-5. |
| **P3 — Kill-criteria evaluation** | Run the written kill criteria (spec §F.3) on ≥ 100 gold-anchored in-situ shadow dispatches. | KC report produced; user makes the **flip** go/no-go. |
| **P4 — Flip (flag flip, gated)** | Enable hard routing via `hard_routing_domains`, with lexical fallback intact. `infra_deploy` is **no longer pre-excluded for the #364 reason** (#364 closed, `code-writer` now gated in — `_cells.py:63`); its inclusion is decided by a P0 **re-measurement** of `infra_deploy` RC under the current `_cells.py` (KC-5 / D-CARVE1). | Flag flipped on `main`; integration tests cover the live v3 path; rollback rehearsed. |
| **P5 — Release (two-repo)** | SemVer bump + tag + GH Release on `claude-wayfinder`; marketplace pin bump on `glitchwerks/claude-plugins`; buildwithclaude listing sync. | Both repos updated and verified (release-process.md steps 8–12); consumers told to re-run `/setup-wayfinder`. |

P1–P5 are gated on P0 ratification. P4 is additionally gated on the P3 KC pass and on the **cross-repo
`confidence`-emission issue (M15-8a/b) existing, closed, and its PR merged** — the dispatch-caller skill
must emit `confidence` before the flip (spec §G.2 impl-E named dependency,
`...two-axis-labeling-design.md:651`–`:654`). M15-8a (open that cross-repo issue + record its URL on the
M15 tracking issue) runs in **P0**, so the dependency is trackable from the start.

### 1.3 Non-negotiable invariants (baked into every phase)

- **Lexical fallback is permanent and non-negotiable.** Compose must degrade to the existing
  `decide()` ladder whenever: the encoder/labels are unavailable, no cell exists, the preferred agent is
  not in the gated set, confidence is not `high` (fail-safe default — absent ⇒ low, spec §D.1,
  `...two-axis-labeling-design.md:407`–`:413`), **or** the matcher-side plausibility veto fires (spec
  §B.1 step 4, `:209`–`:227`). Routing never hard-fails.
- **Shadow/canary before flip.** v3 is computed in shadow and compared to lexical on clean in-situ
  telemetry *before* the default is flipped. The telemetry to do this exists (#316).
- **Two-repo release.** Neither the `claude-wayfinder` release nor the `glitchwerks/claude-plugins`
  marketplace pin bump alone makes v3 installable; both are required (release-process.md
  `docs/maintenance/release-process.md:171`–`:177`, the "marketplace repo bump is required" footgun).

---

## 2. Phases & deliverables (detail)

### Phase 0 — Production-readiness ratification (SME/user-owned gate)

**Goal.** This is the recast go/no-go. The *offline evidence* gate is cleared (§0.1); P0 confirms that
(a) the held-out GO is accepted as sufficient to ship, (b) the auditability trade (spec §E, D-AUDIT1) is
accepted, and (c) the open design decisions (§7) are resolved so the implementation issues are
unambiguous.

**Entry criteria.** Held-out validation report exists and reads GO
(`docs/research/2026-06-17-heldout-validation.md:204`–`:214`). This plan reviewed.

**Exit criteria.** Written go-ahead recorded on the M15 tracking issue; every §7 DECISION REQUIRED item
answered. **If P0 is a no-go, nothing downstream proceeds** — the line stops here with the offline
evidence preserved.

**P0 sub-task — re-measure `infra_deploy` RC under the current `_cells.py` (M15-1a, BLOCKING from the
review).** #364 closed on 2026-06-19 (`code-writer` is now in the `infra_deploy` gate, `_cells.py:63`),
so the offline `infra_deploy` Compose RC must be **re-measured against HEAD `_cells.py`**, not assumed to
be 0.000. This measurement sets KC-5's threshold and decides whether the D-CARVE1 `infra_deploy` exclusion
is needed at all. Run the existing offline harness System 5 (`run_supplied_compose`,
`scripts/corpus/eval/_systems.py:1158`) over the gold corpus filtered to `oracle_domain=="infra_deploy"`
and record the RC. **Outcome feeds D-CARVE1 and KC-5.** This is a read-only measurement task (no
`_cells.py` edit); it can run in P0 because it needs no new `src/` code.

**Deliverable.** Decisions log (issue comment) + M15 milestone created + the `infra_deploy` RC
re-measurement result recorded.

**Owner.** User / designated SME. This plan does not pre-empt the call.

### Phase 1 — Compose lifted into `src/` (behavior-preserving)

**Goal.** Make the validated Compose algorithm a shippable runtime module, with no change to emitted
dispatch decisions.

**Normative source for M15-2 — HEAD function, NOT the spec pseudo-code (BLOCKING from the review).**
The port's normative source is **`run_supplied_compose` at `scripts/corpus/eval/_systems.py:1158`–`:1312`
@ commit `079e787`**, *not* the spec §B.3 pseudo-code. The spec pseudo-code predates commits #396/#411
and #397 and is **superseded**: the HEAD function carries two branches the spec lacks, and one
normalization the spec corrects. Any delta between the two is **deliberate behavior to port**, not drift.
`scripts/corpus/eval/_systems.py` is therefore **study-not-modify** source for M15-2 (read it to port; it
stays in the offline harness). The three deltas:

1. **`diagnose` + `area_span >= 2 → investigator` override** (`_systems.py:1239`–`:1251`, #396/#411). When
   `oracle_posture == "diagnose"` and `area_span >= 2`, the route is forced to `investigator` (with a
   routability guard — fires only when `investigator` is a routable catalog agent, else falls through to
   `decide()`). **[RESOLVED 2026-06-19: caller-supplied]** `area_span` is a **caller-supplied field** on the
   dispatch-context stdin JSON, read by `parse_labels` / the `Labels` value object **alongside**
   `domain` / `posture` / `confidence` — **not** E7-derived at runtime. When the caller **omits**
   `area_span`, it **defaults to 1** (matching the harness default at `_systems.py:1238`), so the
   `diagnose + area_span >= 2 → investigator` branch fires live **only when the caller explicitly supplies
   `area_span >= 2`**. **Behavioral consequence to note:** the broad-diagnose route is **dormant** until
   the dispatch-caller skill begins emitting `area_span >= 2` (a fail-safe narrowing — the cell map's
   `("any","diagnose")→"investigator"` / `("code","diagnose")→"debugger"` paths still fire). This is a
   caller-side dependency to track alongside the `confidence` emission (M15-8a/b). The offline harness
   E7 path (`extract_area_span`, `_systems.py:235`) is **not** on the live hot path. (Q5 RESOLVED — see
   §7 RESOLVED decisions; no runtime extractor.)
2. **`SELF_HANDLE_SENTINEL` path** (`_systems.py:1256`–`:1260`, #397). When
   `cell_map_lookup(...) == SELF_HANDLE_SENTINEL` (`_cells.py:41`, `:72` — the `("project_meta","build")`
   carve-out), the decision is `self_handle` / `agent=None` / `posture_routed=True`. The sentinel is a
   routing instruction, **never** an agent name, and must never reach `genuine_gated_names` or
   `extras["scores"]`.
3. **`is_any` normalization for the cell lookup (BLOCKING-3).** The harness computes
   `domain_for_lookup = oracle_domain if oracle_domain else "any"` (`_systems.py:1220`) — it maps `None →
   "any"` but passes `"is_any"` through **unchanged**. The spec requires `domain not in (None, "is_any")
   → "any"` for the cell lookup, so `("is_any", posture)` falls back to `("any", posture)`. **M15-2 must
   use the spec's corrected form, not copy `_systems.py:1220` verbatim.** (Note: `gate_agents` *already*
   normalizes `"is_any"` to no-gate at `_cells.py:142`, so the gate path is fine — only the
   `cell_map_lookup` domain needs the correction.)

**Key deliverables.**
- New `src/claude_wayfinder/match/_compose.py` (proposed name) containing the production port of
  `run_supplied_compose` (`scripts/corpus/eval/_systems.py:1158`–`:1312` @ `079e787`) — the
  **caller-supplied-label** path, **not** the E1–E12 extractor path (§0.3). All three deltas above are
  ported. The module imports `gate_agents` / `cell_map_lookup` / `DOMAIN_AGENT_MAP` /
  `SELF_HANDLE_SENTINEL` from `_cells.py` (consume only; cell-map *content* owned elsewhere, spec §D.3
  `...two-axis-labeling-design.md:465`–`:485`). **M15-2 may correct the self-contradictory `_cells.py:4`
  docstring** when it touches the module — line 4 says the listed discrepancies are "intentionally NOT
  fixed here" but the very first bullet (`:7`) says the `infra_deploy` `code-writer` fix *shipped in #364*;
  reconcile the framing so the docstring reflects "#364 shipped; remaining items are cell-map content."
- **`genuine_gated_names` (#366) guard — port it (recommended) (CONCERN-1).** The HEAD function applies a
  `DOMAIN_AGENT_MAP.get(oracle_domain)` intersection to distinguish genuine gate-survivors from the
  empty-gate ungated fallback population (`_systems.py:1262`–`:1280`) — more conservative than the spec's
  simpler "preferred in `gated_names` AND in catalog" check. **Recommend porting the guard for
  floor-fidelity** (the measured floor was produced *with* this guard; the simpler check would let an
  out-of-domain `preferred` route when `gate_agents` fell back to the ungated list). M15-2 decides
  port-guard vs simpler-check; either way, add an **empty-gate fallback test** (a domain whose gate
  empties → the guard must keep the out-of-domain `preferred` from routing).
- A frozen `Labels` value object on `_types.py` (`domain` / `posture` / `confidence` / **`area_span`**) + a
  `parse_labels(context)` reader (spec §C.1 / D-LBL1, `:315`–`:343`). `area_span` is a **caller-supplied
  field** read by `parse_labels` alongside the other three, defaulting to **1** when the caller omits it
  ([RESOLVED 2026-06-19: caller-supplied] — Q3/Q5, §7). Recommended: a separate `Labels` dataclass,
  **not** new `Features` fields, so label data never leaks into lexical scoring.
- The **matcher-side plausibility veto** `_is_lexically_plausible(preferred, gated)` — cell-winner must
  be top-k (k=3) or above a lexical floor before a confident posture-route may fire (spec §B.1 step 4,
  `:209`–`:243`). This is a *veto*, not a selector — it only ever blocks a posture-route into the lexical
  fallback.
- Unit tests asserting gate → cell-map → plausibility → fallback ordering, the confidence fail-safe, and
  that `decide()` is unchanged. **The golden-equivalence/branch tests must cover the three HEAD deltas:**
  (a) the **`diagnose` + `area_span >= 2 → investigator`** override (and its routability-guard
  fall-through when `investigator` is absent), (b) the **`SELF_HANDLE_SENTINEL`** path
  (`("project_meta","build") → self_handle`, `agent=None`, sentinel never in `extras["scores"]`), and
  (c) the **`is_any` → `any` cell-lookup** normalization (a context with `domain="is_any"` must look up
  against `("any", posture)`). The `area_span` branch test uses **caller-supplied `area_span`**
  ([RESOLVED 2026-06-19: caller-supplied] — Q3/Q5, §7): assert the override fires when the context supplies
  `area_span >= 2` and stays dormant (defaults to 1) when the field is absent. No runtime extractor is
  exercised.

**Entry criteria.** P0 ratified.

**Exit criteria.** New module + tests green under the Python gate (`ruff check src/ tests/` +
`pytest --ignore=tests/integration`, py3.11/3.12). Crucially: a **golden-equivalence test** asserting
live `_main.py` **stdout** over the gold corpus is byte-identical before and after — Compose is wired but
not yet emitting, so the live decision must not move (spec §B.2 tightened requirement,
`...two-axis-labeling-design.md:266`–`:270`).

**Note on the resolved dedup.** The spec's impl-A scoring-kernel dedup is **already done** on disk
(`_main.py:203` calls `score_entries`, §0.2) — Phase 1 does not repeat it. The golden-equivalence test
still applies, now pinning the *Compose-off* path rather than a scoring refactor.

**Supersession note (carry this verbatim into the M15-2 issue body) (CONCERN-3).** The spec §B.3
pseudo-code for the supplied-compose algorithm **predates commits #396/#411 (the `diagnose` +
`area_span >= 2 → investigator` override) and #397 (the `SELF_HANDLE_SENTINEL` carve-out)**, and does not
correct the `is_any` cell-lookup normalization. It is **superseded by `run_supplied_compose` at
`scripts/corpus/eval/_systems.py:1158`–`:1312` @ commit `079e787`**, which is M15-2's normative source.
Any difference between the spec pseudo-code and the HEAD function is **deliberate behavior to port**, not
an error to reconcile back toward the spec. An implementer who ports from the spec pseudo-code instead of
the HEAD function will silently drop two routing branches and one normalization.

### Phase 2 — Shadow mode

**Goal.** Compute the Compose route on every live dispatch and log it next to the lexical decision the
user actually receives, changing no behavior.

**Key deliverables.**
- Extend `_write_log_entry` with `shadow_data: dict[str, Any] | None = None` (new keyword param after
  `override_id`); when not None, attach it **nested under a single `"shadow"` key** —
  `entry["shadow"] = shadow_data` — **NOT** a flat `entry.update(shadow_data)` merge (CONCERN-2 from the
  review). The flat merge risks future key collisions with `output` / `catalog_hash` and complicates the
  M15-6 shadow-analysis join; the nested form is the **required** schema and is part of M15-3's DoD.
  Default None ⇒ all existing call sites byte-unchanged (spec §G.1 BLOCKING-1,
  `...two-axis-labeling-design.md:624`; call site at `src/claude_wayfinder/match/_main.py:209`).
- Shadow record fields per spec §F.1 (`:546`–`:556`): the labels under test, the live decision, the
  shadow decision, the per-step Compose intermediate state, and the `lexical_agreement` plausibility
  signal (the §E auditability mitigation — a logged posture-route is partially self-justifying because it
  *required* lexical corroboration).
- `disposition_source` **consumer audit** (spec §C.3 / Charge 9, `:373`–`:388`): adding the new value
  `"posture_routed"` to the existing discriminator key requires every consumer tolerate it —
  `src/claude_wayfinder/cli.py:136`–`:138` (prints verbatim; pin print-only with a test) and the
  shadow-analysis join (P3 tooling must classify `posture_routed` explicitly).

**Entry criteria.** Phase 1 merged.

**Exit criteria.** Shadow JSONL accumulating on real dispatches; spot-check confirms `live_*` fields
equal current behavior and `shadow_*` fields populate. Comparison restricted to clean telemetry
(`attribution_source == post_tool_use_hook`, #316).

### Phase 3 — Kill-criteria evaluation

**Goal.** Confirm the offline floor reproduces in production before any flip.

**Key deliverables.** Shadow-analysis tooling + a written KC report computing the spec §F.3 criteria
(`...two-axis-labeling-design.md:575`–`:605`) on **≥ 100 gold-anchored in-situ shadow dispatches**:
- **KC-1** (RC): (i) ≥ `F_indep_lo − 0.05` **AND** (ii) ≥ lexical RC + 0.20. The Phase-0b independent
  floor supplies the anchor — held-out scoped RC 0.7391 (`docs/research/2026-06-17-heldout-validation.md:60`),
  so `F_indep_lo` ≈ 0.7391 (D-KC1 confirms the exact value).
- **KC-2** (CW): ≤ lexical CW 0.2558. HARD BLOCK (`docs/research/2026-06-15-phase0-independent-floor.md:128`,
  `:132`). The fail-safe confidence default + the plausibility veto are the two independent backstops.
- **KC-3** (decisiveness on the eligible set): ≥ 0.55.
- **KC-4** (`is_any`/`project_meta` mislabels remain routing-neutral): 0 route changes.
- **KC-5** (no `infra_deploy` regression vs lexical 0.600): **#364 has landed** (closed
  2026-06-19T12:30:15Z; `code-writer` now in the `infra_deploy` gate — `_cells.py:63`), so the old
  "Compose 0.000 until #364" trigger no longer holds. **The threshold and the carve-out decision are now
  gated on a P0 re-measurement (M15-1a) of `infra_deploy` RC under the current `_cells.py`.** If the
  re-measured RC ≥ lexical 0.600 (or whatever bar D-KC1 sets), `infra_deploy` need **not** be excluded
  from `hard_routing_domains` and KC-5 is satisfied by measurement. If the re-measured RC still regresses
  (because the remaining `("infra_deploy","research")→"researcher"` cell-map *content* discrepancy or
  another slice drags it down — `_cells.py:13`), exclude `infra_deploy` via `hard_routing_domains` per
  D-CARVE1. The carve-out is now **measurement-driven, not #364-driven**.

**Entry criteria.** Shadow data accumulated (Phase 2).

**Exit criteria.** KC report produced. **User makes the flip go/no-go.** KC-2 fail ⇒ do not flip (CW
regression is cardinal).

### Phase 4 — Flip (flag flip, gated)

**Goal.** Make Compose the live routing decision behind a flag, lexical fallback intact.

**Key deliverables.**
- Rollout flag `hard_routing_domains: set[str]` — domain-scoped enable (spec §G.1 BLOCKING-2, `:625`);
  empty set = full shadow. `infra_deploy` is included or excluded **per the M15-1a re-measurement**
  (D-CARVE1) — no longer auto-excluded "until #364", since #364 has shipped (`_cells.py:63`). Shape (env
  var vs CLI vs config file) is D-FLAG1 (§7).
- **Integration tests for the live v3 path** — today only the offline eval exists (brief gap #5). New
  `tests/test_match/test_compose.py` exercises the *emitted* posture-routed decision, the fallback
  branches, and the flag gating, end-to-end through `main()`.
- The named caller dependency: the dispatch-caller skill must emit `confidence` before the flag is
  flipped on (spec §G.2 impl-E, `...two-axis-labeling-design.md:651`–`:654`), so the fail-safe default is
  never the operative live path. This is tracked by the **cross-repo issue opened in P0 (M15-8a)** and
  implemented by M15-8b; its URL must be recorded on the M15 tracking issue.

**Entry criteria.** P3 KC pass + user flip go-ahead + **the M15-8a/b cross-repo `confidence` issue
exists, is closed, and its PR is merged** (verify `mergedAt` is non-null per CLAUDE.md § Pull Requests —
a closed-but-unmerged PR means the caller still does not emit `confidence`).

**Exit criteria.** Flag flipped on `main` (for the ratified domain set); integration tests green;
rollback procedure (§8) rehearsed.

### Phase 5 — Release (two-repo, coordinated)

**Goal.** Ship to consumers.

**Key deliverables.** Follow the authoritative runbook `docs/maintenance/release-process.md`:
- SemVer bump (D-SEMVER1, §7) in `pyproject.toml` + `.claude-plugin/plugin.json` + `CHANGELOG.md`
  (heading exactly `## [X.Y.Z] - YYYY-MM-DD`, `release-process.md:15`). **`model2vec` stays an offline-only
  `spike` extra — NOT promoted to a core dep** ([RESOLVED 2026-06-19: caller-supplied], D-ENCODER1 / §5), so
  the core dependency set is unchanged by M15; re-run `uv lock` only if the version bump itself changes the
  lockfile, and confirm `model2vec` remains `spike`-scoped (`release-process.md:18`).
- Tag the merge commit (`vX.Y.Z`), push → `release.yml` builds + publishes PyPI + auto-creates the GH
  Release (`release-process.md:46`–`:79`).
- **Marketplace pin bump** in the **separate** `glitchwerks/claude-plugins` repo: deref the annotated tag
  with `^{commit}` (the SHA trap, `release-process.md:83`–`:89`, `:161`–`:167`), bump `source.sha` +
  `version`, merge (`release-process.md:91`–`:108`). The current pin is **router-confirmed**: sha
  `e0d1884` / `v1.2.0`, **47 commits behind** HEAD `079e787` (no longer an estimate).
- Sync the external buildwithclaude listing (`release-process.md:114`–`:145`).
- Announce the `/setup-wayfinder` re-run requirement — consumers stay silently on the old version
  otherwise (`release-process.md:110`–`:112`, `:211`–`:215`).

**Entry criteria.** Phase 4 flipped + green CI on `main` (all six jobs, `release-process.md:14`).

**Exit criteria.** Live pin verified (`release-process.md:101`–`:108`); GH Release present with sdist +
wheel assets.

---

## 3. Proposed Milestone + issue breakdown (M15)

**Milestone: M15 — "Matcher v3 live (two-axis routing shipped)".** M14 (Matcher v3 design + offline
validation) is closed; M15 is its shipping successor. **Do not create until P0 sign-off** (CLAUDE.md
§ Issue Tracking — creating issues is not permission to start, and here it is the user's go-ahead).

One issue per coherent work item, sequenced. Dependency notation: `→` = "blocks".

| # | Issue (proposed title) | Phase | Depends on | Complexity | Notes |
|---|---|---|---|---|---|
| M15-1 | **Production-readiness ratification + decisions log** | P0 | — | Low | The go/no-go + resolve all §7 DECISIONs. Owner: user/SME. Gate for everything. |
| M15-1a | **Re-measure `infra_deploy` Compose RC under HEAD `_cells.py`** | P0 | M15-1 | Low | BLOCKING from review. #364 closed → re-run System 5 over `oracle_domain=="infra_deploy"` slice; result sets KC-5 threshold + the D-CARVE1 carve-out decision. Read-only (no `_cells.py` edit). |
| M15-2 | **Lift `run_supplied_compose` into `src/match/_compose.py` + `Labels` type + plausibility veto** | P1 | M15-1 | High | The de-risking core. **Normative source: `scripts/corpus/eval/_systems.py:1158`–`:1312` @ `079e787` (study-not-modify), NOT spec §B.3.** Caller-supplied path ONLY (not E1–E12). **`Labels`/`parse_labels` reads `domain`/`posture`/`confidence`/`area_span` from caller stdin; `area_span` defaults to 1** ([RESOLVED 2026-06-19: caller-supplied], Q3/Q5). Port all three HEAD deltas (#396/#411 `diagnose+area_span` using caller-supplied `area_span`, #397 sentinel, `is_any`→`any`). Port `genuine_gated_names` guard (CONCERN-1). Golden-equivalence + branch tests vs live stdout (area_span branch uses caller-supplied `area_span`). Carry the supersession note. |
| M15-3 | **`_write_log_entry` shadow_data extension + shadow record schema** | P2 | M15-2 | Medium | Default-None keyword param; all existing call sites byte-unchanged. |
| M15-4 | **`disposition_source` consumer audit for `posture_routed`** | P2 | M15-2 | Low | `cli.py:136`–`:138` print-only test + shadow-analysis classifier. Can run parallel to M15-3. |
| M15-5 | **Compose in shadow mode (compute + log, no behavior change)** | P2 | M15-3, M15-4 | Medium | Live decision unchanged; Compose logged via shadow_data. |
| M15-6 | **Shadow-analysis tooling + KC-1..KC-5 report** | P3 | M15-5 (data) | Medium | Must handle `posture_routed`. Produces the flip go/no-go evidence. |
| M15-7 | **`hard_routing_domains` flag + integration tests for live v3 path** | P4 | M15-6 (KC pass) | High | Flag shape per D-FLAG1. New `tests/test_match/test_compose.py` for the *emitted* path. |
| M15-8a | **Identify the dispatch-caller skill file + open the cross-repo `confidence`-emission issue THERE** | P0 | M15-1 | Low | Must happen **before P0 ratification** so the cross-repo dependency has a real, trackable home. Deliverable: the specific skill file path + a created issue (its URL recorded on the M15 tracking issue). Flag to router for the right repo/skill. |
| M15-8b | **Caller-side: dispatch-caller skill emits `confidence` (+ `is_any`/`project_meta` rubric note)** | P4 | M15-8a | Medium | **Separate surface** (dispatch-caller skill, not this matcher repo). The actual emission work, tracked by the M15-8a issue. Named, hard dependency of the M15-7 flip — see P4 entry criterion. |
| ~~M15-9~~ | ~~**`model2vec` runtime encoder availability (per D-ENCODER1)**~~ **DROPPED** | — | — | — | **DROPPED 2026-06-19 (caller-supplied decision).** Q3/Q5/D-ENCODER1 RESOLVED: the matcher consumes `domain`/`posture`/`confidence`/`area_span` from the caller and runs **no runtime encoder and no runtime extractors (E1–E12)** on the hot path. `model2vec` stays an **offline-only `spike` extra** used by the eval harness — not promoted to a core dep. No issue. |
| M15-10 | **Two-repo release: SemVer bump + tag + GH Release + marketplace pin + buildwithclaude sync** | P5 | M15-7 (flipped) | Medium | Follows `release-process.md`. SemVer per D-SEMVER1. |
| M15-11 | **Shipping/integration spec in `docs/`** (brief gap #7) | P0/P1 | M15-1 | Low | Spec E is design-only; this captures the *runtime wiring* contract. This plan + the spec may suffice — confirm with user whether a separate spec is wanted. |

**Explicitly NOT in M15 (owned elsewhere):**
- `_CELL_MAP` / `DOMAIN_AGENT_MAP` **content** fixes (spec §D.3). M15 issues must **not** modify cell-map
  literals (`_cells.py:52`–`:65`, `:67`–`:80`). **#364 (the `infra_deploy` `code-writer` gate) has
  already CLOSED** (2026-06-19; `_cells.py:63`), so it is no longer a pending dependency. The remaining
  *intentional* discrepancies are **cell-map content, tracked separately**: `("code","diagnose")→
  "debugger"` (not investigator, `_cells.py:10`–`:11`,`:74`) and `("infra_deploy","research")→
  "researcher"` via the `("any","research")` fallback (not investigator, `_cells.py:12`–`:13`). M15 ships
  against HEAD `_cells.py`; the measured floor must be **re-confirmed for `infra_deploy` in P0** (M15-1a)
  now that the gate changed.
- The dispatch brief's "scoring-kernel dedup" prep PR → **already done** on disk (§0.2); no issue.

---

## 4. Step-by-step tasks (per phase, sequential)

Detailed file:line change-sites are in spec §G.1 (`...two-axis-labeling-design.md:611`–`:627`) and are
not duplicated here. The sequencing is: **M15-1 → M15-2 → {M15-3 ∥ M15-4} → M15-5 → M15-6 → (KC gate)
→ M15-7 → M15-10**, with the **M15-8a/b** caller-`confidence` track starting in **P0** (M15-8a opens the
cross-repo issue and records its URL on the M15 tracking issue) and which must be **closed + merged**
before M15-7's flag is flipped on. (**M15-9 (encoder) is DROPPED** — Q3/Q5/D-ENCODER1 RESOLVED
caller-supplied; no runtime encoder/extractor, §5.) M15-11 (spec) early in P0/P1.

**One pre-implementation resolution M15-2 cannot start without** (surfaced by the review): the
**port-the-`genuine_gated_names`-guard vs simpler-check** decision (CONCERN-1, recommended: port). (The
**live `area_span` source** is now RESOLVED — caller-supplied field defaulting to 1 — so it no longer
gates M15-2; the branch test simply uses caller-supplied `area_span`.)

The single hardest-to-get-right task is **M15-2**: it must port `run_supplied_compose` (caller-supplied
labels) and **must not** port `_route_from_postures` / `_run_all_extractors` (the killed #357 extractor
line). A code reviewer should verify the imports of the new `_compose.py` reference only
`run_supplied_compose`-equivalent logic and `_cells.py` consumers.

---

## 5. The runtime encoder question (D-ENCODER1) — RESOLVED 2026-06-19: caller-supplied, no encoder

**[RESOLVED 2026-06-19: caller-supplied — Q3/Q5/D-ENCODER1 resolved together by user/SME via the router.]**
The production v3 design is **purely caller-supplied labels**: the matcher **consumes** `domain`,
`posture`, `confidence`, **and `area_span`** from the dispatch-context stdin JSON (spec §C.1,
`...two-axis-labeling-design.md:315`–`:333`; §A.1 "move the judgment to the LLM caller"). On that path the
**matcher runs no encoder and no runtime posture/area extractors (E1–E12) on the hot path** — it consumes
labels the caller already produced.

**Consequences:**
- **No runtime encoder is needed in the matcher.** `model2vec==0.8.2` stays an **offline-only `spike`
  extra** in `pyproject.toml`, imported only by the eval harness (`scripts/corpus/eval/_systems.py`,
  wrapped in try/except). It is **not promoted** to a core dependency. **M15-9 is DROPPED** (§3).
- **`area_span` is caller-supplied** (read by `parse_labels` / `Labels`, defaulting to 1 when omitted), so
  the `diagnose + area_span >= 2 → investigator` branch (`_systems.py:1239`, #396/#411) does **not** require
  a runtime E7 extractor. The offline E7 path (`extract_area_span`, `_systems.py:235`) stays in the harness.
  The only residual consequence is a **caller-side dependency**: the broad-diagnose route is dormant until
  the dispatch-caller skill begins emitting `area_span >= 2` (tracked alongside the `confidence` emission,
  M15-8a/b — see §6 risk note and §2 Phase 1 delta #1).

> `unverified:` the precise `model2vec` extras grouping in `pyproject.toml` was not independently
> re-grepped this pass; it is **not load-bearing** under the resolved decision (the matcher path uses no
> encoder regardless of the extras grouping), so the release-PR step (§2 P5 / §3 M15-10) need only keep
> `model2vec` out of the core deps — confirm the extra stays `spike`-scoped when bumping the version.

---

## 6. Risks & mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **Wrong glue ported** — M15-2 ports the E1–E12 extractor route, resurrecting the #357-killed approach | Medium (the brief points at the wrong functions) | High (measured regression) | §0.3 + §4 call this out explicitly; reviewer verifies `_compose.py` imports only `run_supplied_compose`-equivalent logic. |
| **Live path diverges from the measured floor** | Low (scoring kernel already shared, §0.2) | High (invalidates KC) | Golden-equivalence test vs live stdout (M15-2 exit). |
| **In-situ floor < held-out floor** (distribution shift) | Medium | High (flip unjustified) | Shadow mode + KC-1 two-clause bar (absolute AND no-regression). KC-1 fail ⇒ investigate before flip, do not flip. |
| **CW regression on live traffic** | Low (held-out CW 0.2031 < 0.2558 bar) | Cardinal | KC-2 hard block; fail-safe confidence + plausibility veto are dual backstops; flag-flip is reversible (§8). |
| **Caller doesn't emit `confidence` before flip** ⇒ fail-safe default makes Compose inert | Medium | Medium (no harm, just no gain) | M15-8a opens the cross-repo issue in P0 and records its URL on the M15 tracker; M15-7's flip is blocked until that issue is closed + its PR merged (`mergedAt` non-null). |
| **`infra_deploy` regression on live traffic** (formerly "Compose 0.000 vs lexical 0.600") | Unknown until re-measured (#364 closed; `code-writer` now gated in — `_cells.py:63`) | Medium | **M15-1a re-measures `infra_deploy` RC under HEAD `_cells.py` in P0**; if it regresses, exclude via `hard_routing_domains` (KC-5 / D-CARVE1). No longer #364-gated — the residual risk is the cell-map *content* item `("infra_deploy","research")→"researcher"` (`_cells.py:13`), tracked separately. |
| **Broad-diagnose route dormant** — `diagnose + area_span >= 2 → investigator` (#396/#411) fires live only when the caller supplies `area_span >= 2`; if the dispatch-caller skill never emits it, the route stays dormant | Medium (caller must add the field) | Low (fail-safe narrowing — the cell map's `("any","diagnose")→"investigator"` / `("code","diagnose")→"debugger"` paths still fire; no wrong route) | **[RESOLVED 2026-06-19: caller-supplied]** `area_span` is a caller field defaulting to 1 — no runtime extractor. **Acceptable, fail-safe**, but a **caller-side dependency to track alongside the `confidence` emission (M15-8a/b)**: if the broad-diagnose route is wanted live, the caller skill must emit `area_span >= 2`. Golden-equivalence/branch test pins both the supplied (`>= 2` fires) and absent (defaults to 1, dormant) cases. |
| **Ported wrong branch set** — M15-2 ports the spec §B.3 pseudo-code (superseded) instead of HEAD `_systems.py:1158`–`:1312`, dropping the #396/#411 and #397 branches | Medium (spec predates the branches) | High (two routing behaviors silently lost) | Supersession note carried into the M15-2 issue body (CONCERN-3); branch tests for all three deltas are M15-2 exit criteria. |
| **Two-repo release half-done** — code released but marketplace pin stale ⇒ not installable | Medium (easy to forget the second repo) | High (users can't get it) | release-process.md steps 8–10 mandatory; step 10 verifies the live pin. The "marketplace repo bump is required" footgun (`release-process.md:171`–`:177`). |
| ~~**Encoder dependency surprise at runtime**~~ **RESOLVED/REMOVED** | — | — | **[RESOLVED 2026-06-19: caller-supplied]** No encoder is on the runtime path (Q3/D-ENCODER1, §5) — the matcher consumes caller-supplied labels and runs no encoder/extractors. `model2vec` stays an offline-only `spike` extra. This risk no longer exists. |
| **Auditability spend not accepted post-hoc** | Low (D-AUDIT1 is P0) | Medium | D-AUDIT1 ratified in P0 before any wiring; mitigations (logged labels + lexical-agreement signal) make wrong routes log-triageable (spec §E). |

---

## 7. DECISION REQUIRED — clarifying questions (resolve in P0, before M15 issues are created)

### RESOLVED decisions (P0) — recorded 2026-06-19

These were resolved by the user/SME via the router (2026-06-19). All OTHER P0 items below remain **OPEN**.

- **Q3 + Q5 + D-ENCODER1 — RESOLVED: CALLER-SUPPLIED LABELS.** The matcher **consumes** `domain`,
  `posture`, `confidence`, **and `area_span`** from the dispatch-context stdin JSON (caller-supplied). It
  runs **NO runtime encoder and NO runtime posture/area extractors (E1–E12)** on the hot path.
  - `area_span` is a **caller-supplied field** on the dispatch context, read by `parse_labels` / the
    `Labels` value object alongside `domain` / `posture` / `confidence`. When the caller **omits** it, it
    **defaults to 1** — so the `diagnose + area_span >= 2 → investigator` branch (#396/#411) fires live
    **only when the caller explicitly supplies `area_span >= 2`** (a fail-safe behavioral narrowing; see §2
    Phase 1 delta #1 and the §6 risk note).
  - Therefore **`model2vec` is NOT promoted** to a core dependency — it stays an offline-only `spike` extra
    used by the eval harness. **M15-9 is DROPPED** (§3).
  - **Caller-side dependency to track:** the broad-diagnose route stays dormant until the dispatch-caller
    skill emits `area_span >= 2` — tracked alongside the `confidence` emission in M15-8a/b.

### Still OPEN (resolve in P0, before M15 issues are created)

Inherited from the spec (still open — these were handed to the user, not resolved):
- **D-AUDIT1** — Is the conditional determinism (identical matcher inputs + different caller labels →
  different routes) acceptable, given wayfinder's post-cognitive premise and the §E log mitigations?
  (`...two-axis-labeling-design.md:529`–`:535`)
- **D-LBL1** — Separate frozen `Labels` dataclass (recommended) vs nullable `Features` fields. (`:336`–`:343`)
- **D-SRC1** — Static `DOMAIN_AGENT_MAP` (recommended, measurement-faithful) vs declarative
  `CatalogEntry.domain` frontmatter. (`:361`–`:364`)
- **D-CARVE1** — Domain-scoped `hard_routing_domains` set (recommended) vs all-or-nothing KC-5 block
  (`:591`–`:597`). **Reframed for #364-merged reality:** the `infra_deploy` exclusion is **no longer
  triggered by "#364 hasn't landed"** (#364 closed 2026-06-19, `code-writer` now gated in — `_cells.py:63`).
  Whether `infra_deploy` is excluded is now decided by the **P0 re-measurement (M15-1a)** of its RC under
  HEAD `_cells.py`: include it if the re-measured RC clears the bar, exclude it if it still regresses
  (likely cause: the `("infra_deploy","research")→"researcher"` cell-map content item, `_cells.py:13`).
- **D-KC1** — Confirm/adjust KC-1 no-regression margin (+0.20), KC-2 (≤ lexical CW), KC-3 (≥ 0.55), **and
  set KC-5's `infra_deploy` threshold from the M15-1a re-measurement** (not the old 0.000-until-#364
  assumption). The KC-1 absolute clause binds to the Phase-0b floor 0.7391. (`:603`–`:605`)

New to this shipping plan (the four the brief explicitly asks me to surface):
- **D-FLAG1 (flag-rollout shape)** — Should `hard_routing_domains` be read from an **env var**, a **CLI
  flag**, or a **config file**? The only existing CLI flag in `_main.py` is `--catalog-path` (brief), so
  there is no precedent for a routing-behavior flag — recommend an env var (matches the existing
  `DISPATCH_CATALOG_PATH` pattern) so the flip is operator-controlled without a catalog rebuild, but this
  is the user's call.
- **D-SEMVER1 (SemVer choice + which increment closes M15)** — The brief proposes 1.2.0 → 2.0.0 (breaking
  routing change). I **agree a major bump is defensible** because the *emitted routing decision changes*
  for label-bearing dispatches — but note it is only breaking *behaviorally*, not at the wire/API level
  (the stdin/stdout JSON contract gains optional fields and one new `disposition_source` value, both
  backward-compatible). **Recommended sequencing (resolves the NIT-1 ambiguity):** M15's P5 ships
  **`1.3.0`** — a **minor** bump, because the new `hard_routing_domains` flag defaults **off** (Compose
  inert until an operator opts in), so the *default* behavior on the new version is byte-identical to
  `1.2.0`. **`2.0.0` is deferred to the later default-on flip** (when `hard_routing_domains` defaults to a
  non-empty set), which is the point at which the emitted decision changes for an operator who did
  nothing. Under this recommendation, **`1.3.0` is the increment that closes M15**; the `2.0.0` default-on
  flip is post-M15 work (a follow-up milestone). The alternative — ship `2.0.0` in P5 with the flag
  default-on from day one — is viable but front-loads the breaking change before in-the-wild operator
  exposure to the flag. **Which: minor `1.3.0` closes M15 (flag default-off) with `2.0.0` deferred, or
  `2.0.0` ships in P5 with the flag default-on?**
- **D-ENCODER1 (encoder as core vs optional+fallback) — RESOLVED 2026-06-19: caller-supplied; no runtime
  encoder; `model2vec` stays an offline-only `spike` extra (NOT promoted).** See the RESOLVED decisions
  (P0) subsection above and §5. M15-9 is DROPPED.

Genuinely-open clarifying questions (not just decisions — answers I need before the plan is final):

*(Q1 "lexical permanence" was removed in this revision — it is already answered by the §1.3
non-negotiable invariant ("Lexical fallback is permanent and non-negotiable"); it added no decision
surface. NIT-2.)*

- **Q2 (Phase 0 recast)** — Do you accept that the *evidence-hardening* Phase 0 gate is **cleared** by
  the #387 held-out GO (§0.1), so P0 here is the lighter production-readiness ratification, not a re-run
  of the kill-criteria evidence work? If you want an *additional* independent re-validation before
  shipping, that becomes a P0 sub-task and the plan grows.
- **Q3 (runtime encoder on the path at all) — RESOLVED 2026-06-19: caller-supplied; no runtime encoder.**
  The production v3 design is **purely caller-supplied labels** (matcher consumes `domain`/`posture`/
  `confidence`/`area_span` from stdin, runs no encoder — spec §A.1/§C.1). M15-9 / `model2vec` promotion is
  dropped. See the RESOLVED decisions (P0) subsection above and §5. (Resolved jointly with Q5/D-ENCODER1.)
- **Q5 (live `area_span` source) — RESOLVED 2026-06-19: option (a), caller-supplied (default 1).**
  `area_span` arrives in the stdin context JSON alongside `domain`/`posture`/`confidence`, read by
  `parse_labels` / the `Labels` value object, **defaulting to 1** when the caller omits it — so the
  `diagnose + area_span >= 2 → investigator` branch (#396/#411, `_systems.py:1239`–`:1251`) fires live only
  when the caller explicitly supplies `area_span >= 2`. **No runtime E7 extractor** (the offline E7 path
  `extract_area_span`, `_systems.py:235`, stays in the harness). The behavioral narrowing (broad-diagnose
  dormant until the caller emits `area_span >= 2`) is **accepted as fail-safe** and tracked as a caller-side
  dependency alongside the `confidence` emission (M15-8a/b). See the RESOLVED decisions (P0) subsection
  above, §2 Phase 1 delta #1, §5, and the §6 risk note.
- **Q4 (milestone reconciliation) — RESOLVED in this revision.** Router-confirmed GitHub state: **M14 is
  CLOSED at 34/34** and **#386/#387 are closed M14 issues** (the Phase-0b held-out work), so M14 *is*
  genuinely complete. **No v3-live tracker exists** — **M15 is genuinely new**, not a reuse of an existing
  milestone. No action needed beyond creating M15 at P0 sign-off.

Implementation-level decision M15-2 cannot start without (still OPEN — recommended, pending ratification):
- **D-KC-GUARD1 (`genuine_gated_names` #366 guard — port vs simpler-check) (CONCERN-1)** — Port the HEAD
  function's `DOMAIN_AGENT_MAP.get(oracle_domain)` intersection guard (`_systems.py:1262`–`:1280`,
  **recommended** for floor-fidelity — the measured floor was produced *with* it) or use the spec's simpler
  "preferred in `gated_names` AND in catalog" check? Either way M15-2 must add an **empty-gate fallback
  test**. This is a pre-implementation resolution for M15-2 (§2 Phase 1, §4). **Independent of the
  caller-supplied resolution** above.

---

## 8. Rollback strategy for the flag flip

The flip (Phase 4) is the highest-consequence, most-reversible step. Two rollback layers:

**Layer 1 — flag revert (fast, no release).** Because the flip is gated on `hard_routing_domains`, the
first rollback is to **empty the set** (or remove `infra_deploy`/the offending domain). If the flag is an
env var (D-FLAG1), this is an operator action requiring no code change, no release, and no marketplace
bump — Compose drops back to full shadow and lexical resumes as the emitted decision. **This is why the
domain-scoped flag (D-CARVE1) is recommended over a single bool:** a regression in one domain is excised
without disabling the others.

**Layer 2 — release rollback (if a shipped version is bad).** If the regression is only discovered after
P5 shipped, follow `docs/maintenance/release-process.md:229`–`:235` (Rollback procedure):
1. Yank the bad version from PyPI (web UI — yank, not delete). This is the primary lever — yank stops new
   installs from resolving to the bad version while leaving existing pins reproducible.
2. **Leave the `vX.Y.Z` tag in place** (CONCERN-5 from the review). Do **not** routinely delete it:
   a consumer with a pinned `pip install claude-wayfinder==X.Y.Z` (or a marketplace `source.sha` pointing
   at the tagged commit) still needs the tag to resolve for reproducibility. Delete the tag **only** on a
   security/legal walk-back where the tagged commit itself must become unreachable — and document the
   reason on the M15 tracking issue when you do.
3. **Revert the marketplace pin** on `glitchwerks/claude-plugins` to the prior `sha` + `version`; merge
   immediately (no CI on that repo). This is what actually stops *new* consumers from pulling the bad
   version, independent of whether the tag stays.
4. Comment on the M15 tracking issue with symptom + next steps; do not re-close until a corrected release
   lands.
5. Post-mortem: CHANGELOG entry for the reverted version + update the relevant footgun / memory.

**Pre-flip rehearsal (Phase 4 exit criterion).** Before flipping on `main`, rehearse Layer 1: set
`hard_routing_domains`, observe a posture-routed decision, then empty the set and confirm the emitted
decision returns to the lexical `decide()` output byte-for-byte. The plausibility veto and fail-safe
confidence default mean even a "stuck on" flag cannot fire a confident-wrong delegate the lexical scorer
disagrees with — but the rehearsal proves the operator control works.

---

## 9. Definition of done

- v3 Compose computes the live dispatch decision for the ratified `hard_routing_domains` set, with
  lexical fallback intact and verified.
- KC-1..KC-5 passed on ≥ 100 gold-anchored in-situ shadow dispatches (KC-2 the hard block).
- Integration tests cover the emitted v3 path (not just the offline eval).
- `claude-wayfinder` released (PyPI + tag + GH Release) at the chosen SemVer.
- `glitchwerks/claude-plugins` marketplace pin bumped and live-verified; buildwithclaude listing synced.
- Consumers notified to re-run `/setup-wayfinder`.
- Rollback (Layer 1 flag revert) rehearsed and documented.

---

## 10. Citations

All decision-driving claims above cite one of: the canonical design spec
(`docs/superpowers/specs/2026-06-14-two-axis-labeling-design.md:Lx`), the Phase-0 independent-floor report
(`docs/research/2026-06-15-phase0-independent-floor.md:Lx`), the Phase-0b held-out validation report
(`docs/research/2026-06-17-heldout-validation.md:Lx`), the Phase-0 failure decomposition
(`docs/research/2026-06-15-phase0b-failure-decomposition.md:Lx`), the live-code map
(`src/claude_wayfinder/match/_main.py:Lx`, `_match.py:Lx`), the offline harness
(`scripts/corpus/eval/_systems.py:Lx`), or the release runbook
(`docs/maintenance/release-process.md:Lx`), inline at point of use.

**`unverified:` flags (status after this revision):**
1. **Largely RESOLVED (decision-level):** Q3/Q5/D-ENCODER1 resolved 2026-06-19 to **caller-supplied
   labels** — the live v3 path needs **no runtime encoder and no runtime extractors** (§5 / §7 RESOLVED
   decisions). The encoder-dependency claim from the brief is therefore **no longer load-bearing**. The
   only residual `unverified:` item is cosmetic: the precise `model2vec` extras grouping in `pyproject.toml`
   was not re-grepped this pass — confirm `model2vec` stays `spike`-scoped (not promoted to core) when
   bumping the version in P5 (§2 P5 / §3 M15-10).
2. **RESOLVED (router-confirmed against GitHub this revision):** M14 is **CLOSED 34/34**; **#386/#387 are
   closed M14 issues**; **no open v3-live tracker exists** (M15 is genuinely new). The §0 framing and Q4
   (§7) reflect this — these are no longer unverified.
3. **RESOLVED (router-confirmed):** the marketplace pin is sha `e0d1884` / `v1.2.0`, **47 commits behind**
   HEAD `079e787` (was a "~40" estimate from the brief; now confirmed).
4. **RESOLVED (verified on disk this revision):** #364 has **closed** (`code-writer` in the `infra_deploy`
   gate, `_cells.py:63`); the HEAD `run_supplied_compose` branches (#396/#411 `diagnose+area_span`, #397
   `SELF_HANDLE_SENTINEL`) and the `is_any`/`genuine_gated_names` behaviors are read directly from
   `scripts/corpus/eval/_systems.py:1158`–`:1312` @ `079e787` and `_cells.py:41`,`:52`–`:80`,`:97`–`:153`.
