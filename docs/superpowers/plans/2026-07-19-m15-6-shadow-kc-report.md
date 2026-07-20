---
title: M15-6 — Shadow-analysis gold-labeling + KC-1..KC-5 go/no-go report (issue #423)
touches:
  - docs/research/  # NEW gold-labeling report + labeling rubric addendum + KC report (this pass)
  - scripts/corpus/eval/_kc.py            # NEW — spec-exact KC-1..KC-5 logic + KC-2 0.2558 anchor-provenance assertion
  - scripts/corpus/eval/_metrics.py       # READ-ONLY reuse of RC/CW kernels (metric_routing_correctness, metric_confident_wrong_rate)
  - scripts/corpus/eval/_reader.py        # READ-ONLY — GoldLabel type + label loader
  - scripts/shadow-strip-for-labeling.py  # NEW — Phase A strip-and-present tool (§3.1 stripping logic → labeler-safe entries)
  - scripts/shadow-kc-report.py           # NEW — CLI: join gold→shadow, compute KCs, extract+compare matcher_version, write report
  - scripts/shadow-summary.py             # reference building block (agreement/branch stats), likely unchanged
  - src/claude_wayfinder/match/_cells.py  # READ-ONLY — cell_map_lookup for KC-3 eligibility (and KC-4 if counterfactual)
  - src/claude_wayfinder/match/_compose.py # READ-ONLY — only if KC-4 counterfactual method is chosen (D-KC4)
  - tests/test_corpus_eval/test_kc.py     # NEW — KC computation unit tests with synthetic fixtures (flat tests/test_<module>/ convention)
skills_relevant:
  - python
---

# M15-6 — Shadow-analysis tooling + KC-1..KC-5 go/no-go report (issue #423)

**Status:** PLAN / ALL §6 DECISIONS RESOLVED (2026-07-19, rev 3). All 6 DECISION REQUIRED items
in §6 have been resolved by the user; work on M15-6a (#483) and M15-6b (#484) may begin (#485 also
exists under M15 — see §7). Per CLAUDE.md § Issue Tracking, issue creation already happened for
`#483`/`#484`/`#485` — this status update does not itself authorize implementation beyond what those
issues scope.

**Revision log.**
- **2026-07-19 (rev 2, post `project-reviewer`):** Architectural review returned **0 BLOCKING**,
  **5 CONCERN**, **3 NIT** (user declined the `inquisitor` escalation, so this review is final). This
  revision closes all 5 CONCERN findings: (1) KC-4 INSUFFICIENT-DATA handling on empty eligible set +
  infra_deploy domain-accuracy bar added to the Option-1 justification (§3.2, §4.2, §4.5, §6 D-LABEL);
  (2) KC-3 numerator pinned to the exact three-field `posture_routed`/`shadow_decision`/`gated_agent_names`
  logic (§4.2); (3) the KC-2 0.2558 anchor-provenance check moved from pre-report prose into a gated
  assertion inside `_kc.py` (§4.3, §4.5, §9); (4) `matcher_version` extraction + `_compose.py` git-state
  divergence check made a tooling requirement of `scripts/shadow-kc-report.py`, not a report caveat
  (§4.4, §4.5); (5) the Phase A strip-and-present script added to `touches:` and the test file pinned to
  `tests/test_corpus_eval/test_kc.py` (frontmatter, §3.3, §4.5).
- **2026-07-19 (rev 3):** All 6 §6 decisions resolved by user — see updated bullets below (D-KC4,
  D-LABEL, D-KC1-MARGIN, D-KC2-ANCHOR, D-N, D-LOC all now RESOLVED).

**Initiative.** #423 (M15-6, Phase 3 of the `matcher-v3-ship-live` plan
`docs/superpowers/plans/2026-06-19-matcher-v3-ship-live.md:319`–`:345`) is the **flip go/no-go
gate** for enabling hard routing on Matcher v3. It must compute the written kill criteria
KC-1..KC-5 (spec §F.3, `docs/superpowers/specs/2026-06-14-two-axis-labeling-design.md:575`–`:605`)
on **≥ 100 gold-anchored in-situ shadow dispatches** and produce a report with a per-criterion
verdict. KC-2 is a HARD BLOCK.

**Design source.** Spec §F.1–F.3 (`...two-axis-labeling-design.md:539`–`:605`) is the normative
definition of the shadow-log schema, the comparison method, and the exact KC formulas. This plan
does not restate the spec; it scopes the two phases needed to execute §F.3 on real data.

---

## 1. What this issue actually requires (two phases, not one)

The `matcher-v3-ship-live` plan scoped M15-6 as a single "Medium" item
(`...2026-06-19-matcher-v3-ship-live.md:414`). On-disk reconnaissance shows it is **two phases**,
because the accumulated shadow sample is **entirely unlabeled** and no repeatable gold-labeling
tooling exists:

- **Phase A — Gold-anchor the in-situ sample.** Produce ground-truth `gold_agent` (+ `domain` /
  `posture` / `confidence`) for ≥ 100 of the 245 accumulated shadow dispatches. This is an
  unavoidable prerequisite: KC computation is `shadow_agent == gold_agent`, and there is no gold
  today for these rows.
- **Phase B — KC computation tooling + report.** Join gold labels (by `corpus_id`) to the logged
  shadow dict, compute KC-1..KC-5 per the exact §F.3 formulas, and write the go/no-go report.

Phase B is blocked on Phase A. Both are blocked on the §6 decisions.

---

## 2. Verified ground truth (reconnaissance confirmed on disk 2026-07-19)

These are the load-bearing facts the plan is built on. Each was checked, not assumed.

1. **The accumulated sample is real, in-situ, and label-bearing — but has no gold.** The 245-entry
   corpus `~/.claude/state/wayfinder-corpus/2026-06-12/wayfinder-corpus.jsonl` (manifest
   `docs/research/2026-07-19-shadow-corpus-manifest.json`) is organic `matcher_decision` traffic
   (`manifest:144`–`:152`), and it was built with `exclude: corpus_id in exclude_corpus_ids`
   (`manifest:150`) against the existing 168-entry gold set — so **every row is currently
   unlabeled** (router discovery #1, confirmed).

2. **Each row already carries BOTH the live/lexical decision and the shadow/Compose decision, with
   real non-null caller labels.** Inspected rows show `input.domain` / `input.posture` /
   `input.confidence` / `input.area_span` populated, and a `shadow` dict where
   `shadow_disposition_source == "posture_routed"` actively fires (e.g. `corpus_id 56092`, 56172,
   56248). The production dispatch-caller **was already emitting labels** when these were logged, so
   Compose is genuinely routing in-situ — the shadow decisions are not degenerate lexical
   fall-throughs. **This is what makes the sample a valid KC substrate.** (If it had been unlabeled
   at emission time, every `shadow_agent == live_agent` and KC-1(ii) would fail by construction —
   verified this is NOT the case.)

3. **The dual-decision log means one gold corpus yields both RCs on the same sample.** The `shadow`
   dict carries `shadow_agent` (Compose) AND `live_agent` (the emitted lexical `decide()` output,
   which in shadow mode *is* the lexical baseline) — schema built by `_build_shadow_record`
   (`src/claude_wayfinder/match/_main.py:62`–`:108`). So shadow RC and lexical RC (KC-1's two
   operands) come from the **same** gold-labeled rows — no re-run, no second corpus, no join-key
   gap (`corpus_id` is present on every row).

4. **RC and CW already have validated kernels; KC-3/4/5 do not.** `metric_routing_correctness`
   (`scripts/corpus/eval/_metrics.py:534`–`:573`) and `metric_confident_wrong_rate` (`:497`–`:526`)
   implement RC and CW with the load-bearing `self_handle` normalization
   (`_prediction_matches_gold`, `:119`–`:137`), indexed by `corpus_id` against
   `dict[int, GoldLabel]`. These match the spec KC-1 / KC-2 definitions structurally. KC-3
   (eligible-set denominator), KC-4 (routing-neutrality), and KC-5 (domain-sliced RC) have no
   existing implementation.

5. **Gold-label schema** (`docs/research/2026-06-12-gold-labels-redacted.jsonl:1`):
   `corpus_id` (int), `domain`, `is_any` (bool), `posture`, `gold_agent`, `confidence`,
   `disputed` (bool). Join-compatible with the eval reader (`scripts/corpus/eval/_reader.py`).

6. **`scripts/shadow-summary.py` is a thin reference, not a KC computer** — it counts
   agreement/branch distribution from the raw log with no gold join and no RC/CW/KC logic
   (`scripts/shadow-summary.py:27`–`:83`). Useful as a JSONL-reading pattern; not a building block
   for the metrics.

**GitHub-state note (router-verifiable, not checked by this sub-agent):** M15-6 depends on M15-5
(Compose in shadow mode). The presence of a `shadow` dict on every corpus row is on-disk evidence
that M15-5 shipped; confirm the corresponding issue is closed before starting.

---

## 3. Phase A — Gold-anchor the in-situ sample

### 3.0 MANDATE: pre-labeling per-KC denominator estimate (do this BEFORE labeling anything)

The "≥ 100" figure in #423 is a **whole-sample** count. It is **not** the constraint that decides
validity — each KC has its own effective denominator, and two are at risk of collapsing on a sample
that was stratified by `decision_band × td_length_band × file_paths_present`
(`manifest:138`), **not** by domain/posture:

- **KC-5 (infra_deploy slice).** The original 168-corpus had only **5** infra_deploy rows
  (`docs/research/2026-06-12-gold-labeling-report.md:95`). A go/no-go RC computed on n=3 is not a
  gate.
- **KC-3 (eligible set = gated ∧ cell-exists ∧ high-confidence).** Heavy `is_any` prevalence shrinks
  this denominator — `is_any` is ungated and excluded (e.g. `corpus_id 56092` is `is_any` → falls
  through).

**Task:** before committing to label exactly N, compute the *expected* per-KC denominators from the
**already-logged caller `domain`/`posture`/`confidence`** (no labeling needed — these fields are on
every row), plus a `cell_map_lookup` existence check (`src/claude_wayfinder/match/_cells.py`). Output
a small table: expected eligible-set size (KC-3), expected infra_deploy count (KC-5), expected
gated-delegate count. **If infra_deploy or the eligible set is thin, oversample those strata** when
choosing which of the 245 to label (draw beyond a random 100 to hit per-KC floors). This is a
concrete gate on Phase A — do not label a random 100 and discover KC-5 is uncomputable.

### 3.1 MANDATE: independence hardening — strip ALL labels before labeling (new vs the 168-corpus)

The original process gave labelers "only `corpus_id` and `input`"
(`docs/research/2026-06-12-gold-labeling-report.md:31`–`:33`). **That instruction is now unsafe.**
The 168-corpus predated caller labels; this corpus's `input` object **contains the caller's
`domain` / `posture` / `confidence` / `area_span`** — the very labels under test. It also carries
`output` (the matcher's own decision) and the `shadow` dict (Compose's decision). A labeler who sees
any of these anchors on them and inflates apparent agreement, defeating the independence of the gold.

**Labelers must see ONLY the raw signal:** `task_description`, `file_paths`, `agent_mentions`,
`tool_mentions`, `command_prefix`. Strip `input.{domain,posture,confidence,area_span}`, the entire
`output` field, and the entire `shadow` dict before handing entries to any rater. (`agent_mentions`
stays — it is legitimate E11 directive signal, not a label-under-test, and the original rubric
depends on it — `...gold-labeling-report.md:36`, `:229`–`:243`.) This stripping step is part of the
Phase A tooling deliverable.

### 3.2 Methodology — three options (recommendation is LINKED to the KC-4 method, D-KC4)

Frame for the user. The load-bearing axis for RC/CW (KC-1, KC-2, KC-5) is **`gold_agent`**, which
already scored **0.975** informational inter-rater agreement in the original run
(`...gold-labeling-report.md:131`); the axis that *missed* its target (domain, 0.775 vs 0.85 —
`:129`) only matters for **KC-4**. And this is a **re-run with the post-checkpoint-amended rubric**
(`...gold-labeling-report.md:51`–`:52`, `:146`–`:147`), so it starts stronger than the original pass.
Therefore the methodology weight should be chosen *together with* the KC-4 method (D-KC4):

- **Option 1 — Light (single independent rater, rubric-only, blind-to-all-labels).** One
  auditor-class labeler labels the sample against the amended rubric with §3.1 stripping applied. No
  reliability subsample. **Defensible IF KC-4 is done structurally (D-KC4 option a),** because then no
  criterion depends on robust *domain* reliability for the KC-4 counterfactual — `gold_agent` is the
  load-bearing axis for KC-1/KC-2/KC-5 and it already clears 0.975. This is a genuine option, not a
  corner-cut: the original ceremony's full cost produced only 1 residual dispute in 168 (`:65`), and
  the marginal reliability gain over a solid single pass on the load-bearing axis is small.
  - **Caveat — the domain axis is not fully off the load-bearing path even with structural KC-4.**
    KC-4 and KC-5 both retain a dependency on the *domain* axis specifically, and domain was the axis
    that **missed** its reliability target in the original exercise (**0.775 vs ≥0.85 target**,
    `...gold-labeling-report.md:129`). KC-5's `infra_deploy` slice is tiny (n≈3–5, §3.0), so a **single
    mislabeled `infra_deploy` domain** flips a row in or out of that slice and can trigger the
    denominator-collapse path flagged in §3.0/§8. Therefore, before Option 1 is accepted as sufficient,
    the reliability bar it must clear is **not only `gold_agent` accuracy but also `infra_deploy` domain
    accuracy on the labeled slice** — i.e. the single rater's `infra_deploy` domain calls must be
    spot-checked (or a small targeted second pass run over just the `infra_deploy` candidates), so a
    2-of-5 domain error cannot silently collapse KC-5's denominator. If that spot-check cannot be
    afforded, Option 1 is not defensible and D-LABEL should move to Option 2.
- **Option 2 — Calibrated middle (RECOMMENDED default).** Pass 1: full-coverage independent labeling
  (partitioned across 2–3 raters, blind per §3.1). Pass 2: a fresh rater relabels a stratified n≈40
  subsample for inter-rater reliability. **Pre-register the `gold_agent` bar** (the load-bearing axis)
  in addition to domain/posture. Adjudicate disagreements; user checkpoint before freeze. This is the
  original validated process (`...gold-labeling-report.md:28`–`:52`) minus the 4th rater, plus the
  §3.1 shadow/caller-label strip and the pre-registered gold_agent bar.
- **Option 3 — Heavy (full original ceremony).** 4 parallel raters + reliability subsample +
  adjudication + checkpoint. Warranted only if the user wants a robust **counterfactual KC-4**
  (D-KC4 option b), which puts real weight on domain-label reliability.

**Recommendation:** Option 2 as the default, but **the choice is bound to D-KC4** — if the user
picks structural KC-4 (recommended), Option 1 becomes genuinely defensible and cheaper; if the user
wants counterfactual KC-4, move to Option 2/3 to earn the domain reliability KC-4 then consumes.

### 3.3 Phase A deliverables

- A **strip-and-present** script (`scripts/shadow-strip-for-labeling.py`) that produces labeler-safe
  entries per §3.1 — part of the labeling tooling, reusable for future passes (unlike the ad-hoc 168
  process). This is a real deliverable with non-trivial stripping logic (drop
  `input.{domain,posture,confidence,area_span}`, the entire `output` field, and the entire `shadow`
  dict; retain `agent_mentions`), so it is listed in the `touches:` frontmatter.
- **Test fixtures for the strip-and-present script itself** (not just the KC computation tests in
  §4.5) asserting: (1) caller labels — `input.domain`/`posture`/`confidence`/`area_span` — are
  stripped from the labeler-facing view; (2) the entire `output` field is stripped; (3) the entire
  `shadow` dict is stripped; (4) all permitted raw-signal fields — `task_description`, `file_paths`,
  `agent_mentions`, `tool_mentions`, `command_prefix` — remain present and unaltered; (5) absent/null
  fields on any of the above are handled without error (no spurious key insertion); and (6) the
  **source corpus file is left unmodified** — stripping produces a new file/view, never an in-place
  mutation of the on-disk corpus. These fixtures close the §3.1 independence-hardening mandate with
  verifiable coverage rather than a tooling description alone.
- The **per-KC denominator estimate** table (§3.0), recorded before labeling.
- The **redacted gold-labels JSONL** for the labeled sample (same schema as
  `docs/research/2026-06-12-gold-labels-redacted.jsonl`), committed; full artifact (with notes) local
  only, per the two-tier placement rule (`...gold-labeling-report.md:271`–`:284`).
- A **gold-labeling report** (`docs/research/2026-07-19-shadow-sample-gold-labeling.md`) recording
  method, per-axis reliability vs pre-registered bars, adjudication log, and coverage — framed
  **explicitly as gold-anchoring the in-situ sample, NOT a re-run of Phase 0** (Phase 0 set
  `F_indep_lo = 0.7391` and is done — `docs/research/2026-06-17-heldout-validation.md:60`; this pass
  produces ground-truth `gold_agent` for the 245-corpus, a different artifact — do not conflate).

---

## 4. Phase B — KC computation tooling + go/no-go report

### 4.1 Computational architecture — TWO modes (be explicit)

- **KC-1, KC-2, KC-3, KC-5 use the LOGGED shadow decision** (in-situ production performance),
  anchored to `gold_agent`. This is the spec §F.2 item 3 intent — "confirm the Phase-0 floor
  reproduces **in production** rather than just measuring agreement"
  (`...two-axis-labeling-design.md:568`–`:570`). **Do not re-run Compose for these** — the logged
  decision IS the measurement.
- **KC-4 is a counterfactual by nature** ("would the route change if the domain label were correct?").
  Two candidate methods — see D-KC4 (§6). The recommended structural method uses only logged data;
  the alternative re-runs `compose_route`.

### 4.2 Per-KC computation (exact §F.3 formulas)

Denominators and thresholds are quoted from the spec / #423, cited inline.

- **KC-1 (RC), two clauses** (`...two-axis-labeling-design.md:585`; #423 body):
  - shadow RC = RC over all labeled rows on `shadow_agent`/`shadow_decision` vs `gold_agent`.
  - lexical RC = same on `live_agent`/`live_decision` vs `gold_agent`.
  - PASS iff **(i)** shadow RC ≥ `F_indep_lo − 0.05` = **0.7391 − 0.05 = 0.6891** AND **(ii)** shadow
    RC ≥ lexical RC + **0.20**. `F_indep_lo = 0.7391` from Phase-0b held-out
    (`docs/research/2026-06-17-heldout-validation.md:60`).
  - Compute via `metric_routing_correctness` (`_metrics.py:534`) on adapter objects (see §4.3).
- **KC-2 (CW) — HARD BLOCK** (`...two-axis-labeling-design.md:586`; #423 body):
  - shadow CW = wrong-delegates / all-delegates on the shadow decision, via
    `metric_confident_wrong_rate` (`_metrics.py:497`).
  - PASS iff shadow CW ≤ **0.2558** (the historical lexical anchor, `:586`). **Also report the
    in-situ lexical CW** (same metric on `live_*`) for transparency — see D-KC2 on whether the bar is
    the fixed 0.2558 or the in-situ lexical CW.
- **KC-3 (decisiveness on the eligible set)** (`...two-axis-labeling-design.md:587`):
  - Eligible set = rows where (caller `domain` gated, i.e. not `is_any`/`null`) AND
    (a cell exists for `(domain_for_lookup, posture)` — via `cell_map_lookup`,
    `src/claude_wayfinder/match/_cells.py`) AND (caller `confidence == "high"`).
    `domain_for_lookup = domain if domain not in (None,"is_any") else "any"`
    (`...two-axis-labeling-design.md:207`, `:617`).
  - Numerator = eligible rows that routed as **posture-routed OR gated-delegate**. Pin the exact
    discriminating field set from `_build_shadow_record` (`src/claude_wayfinder/match/_main.py:95`–`:103`
    — `shadow_decision` at `:95`, `gated_agent_names`/`posture_routed`/`branch` at `:100`–`:103`;
    confirmed on disk 2026-07-19) — do **not** classify off `shadow_disposition_source == "posture_routed"`
    alone, which catches the posture-route arm but MISSES gated-delegates that fell through a posture-veto
    to a gated `decide()` delegate. The three-field logic:
    - **posture-routed:** `posture_routed == True` (the `diag`-sourced bool, not the string
      `shadow_disposition_source`).
    - **gated-delegate:** `posture_routed == False AND shadow_decision == "delegate" AND gated_agent_names`
      is non-empty. (An **ungated-delegate** — `gated_agent_names` empty — is NOT in the numerator.)
    - A row counts toward the numerator if it satisfies **either** clause.
  - PASS iff numerator/eligible ≥ **0.55** (`:587`). New spec-exact logic. If eligible-n = 0, report
    **INSUFFICIENT-DATA**, not a vacuous PASS (§3.0 denominator-collapse path).
- **KC-4 (routing-neutrality)** (`...two-axis-labeling-design.md:588`): among rows where caller
  `domain ∈ {is_any, project_meta}` but `gold` differs (a mislabel), **0** route changes vs the
  gold/oracle routing. Method per D-KC4.
  - **INSUFFICIENT-DATA guard (required).** The KC-4 eligible set is `caller domain ∈ {is_any,
    project_meta} AND gold differs`. If a single Option-1 rater reproduces the caller's
    `is_any→project_meta` mislabel pattern, gold never differs from the caller label and the eligible
    set is **empty** — at which point a "0 route changes" numerator is vacuously satisfied and KC-4
    would silently **PASS on no evidence**. KC-4 MUST report **INSUFFICIENT-DATA when eligible-n = 0**,
    identically to the KC-5 empty-slice path — do not let an empty eligible set read as PASS. Add this
    to the §4.5 test matrix.
- **KC-5 (infra_deploy no regression)** (`...two-axis-labeling-design.md:589`; #423 body): on the
  `gold.domain == "infra_deploy"` slice, shadow RC ≥ lexical-anchor **0.600** OR infra_deploy excluded
  via `hard_routing_domains` OR #364 landed (it has — `...2026-06-19-matcher-v3-ship-live.md:42`).
  Report the slice n and flag if too small to gate (§3.0). Ties to M15-1a's offline re-measurement.

### 4.3 Reuse `_metrics.py`, do NOT re-implement RC/CW (recommendation)

Write a thin adapter that turns each corpus row's `live` and `shadow` dicts into two lightweight
result objects exposing `corpus_id`, `agent`, `decision` (the only fields `metric_routing_correctness`
/ `metric_confident_wrong_rate` / `_prediction_matches_gold` touch — `_metrics.py:119`–`:137`,
`:497`–`:526`, `:534`–`:573`), load gold into `dict[int, GoldLabel]` via `_reader.py`, and call the
existing kernels for KC-1/KC-2/KC-5. Rationale: the `self_handle` normalization is easy to get subtly
wrong; reusing the validated kernel eliminates divergence between the KC report and the offline eval.
KC-3 and KC-4 are genuinely new and are written fresh in `scripts/corpus/eval/_kc.py`.

**KC-2 anchor-provenance guard — a gated assertion IN `_kc.py`, not a prose pre-check.** KC-2 is the
HARD BLOCK, and its pass/fail is `shadow CW ≤ 0.2558`. If the 0.2558 lexical-CW anchor (`:586`) was
computed with a **different denominator** than the one `metric_confident_wrong_rate` uses here
(wrong-delegates / all-delegates), the hard-block comparison is invalid — not merely imprecise. This
must be legible in code review, so it is a **Phase B tooling deliverable, not a separable manual step**:
`_kc.py` records the 0.2558 anchor's definition/denominator as a documented, asserted constant (e.g. a
module-level constant with a provenance docstring citing `:586`, plus an assertion/comment that pins the
denominator to `metric_confident_wrong_rate`'s wrong-delegates/all-delegates form). The KC-2 computation
path references that constant so the apples-to-apples assumption is visible at the point of comparison
rather than living in a pre-report note that a reviewer cannot see. (See the forward note in §7 M15-6b
on recording the confirmed provenance in the issue body once created.)

### 4.4 Report structure — report BOTH whole-sample and gated-subset cuts

KC-1(ii)'s "+0.20" is spec-literal on the **whole sample** (`:585`), but fall-through rows (ungated
`is_any`, plausibility-vetoed, low-confidence) have `shadow_agent == live_agent` by construction and
dilute both RCs equally. A whole-sample KC-1(ii) miss could therefore be **traffic-mix dilution**
(Compose fine, mostly out of its wheelhouse) rather than Compose underperformance — a materially
different go/no-go story. The report MUST:

- Report RC/CW on both the **whole sample** and the **gated-eligible subset** (reuse the KC-3
  eligibility partition).
- Break disagreements down by whether the **caller label matched gold** — isolating caller-label
  noise (ties to the M15-8a/b caller-emission dependency,
  `...2026-06-19-matcher-v3-ship-live.md:416`–`:417`) from Compose-logic error.
- Handle `shadow_disposition_source == "posture_routed"` explicitly as a route class
  (§C.3 / Charge 9, `...two-axis-labeling-design.md:572`–`:573`).
- State a per-criterion PASS/FAIL/INSUFFICIENT-DATA verdict + an overall flip go/no-go, with KC-2 as
  the hard block.
- Pin **version provenance — enforced in tooling, not documentation-only.** Recording the version as a
  prose caveat does not stop a flip on stale KC evidence if `_compose.py` is patched between corpus
  accumulation and M15-7's flip. **The guard's scope is not limited to `_compose.py`** — KC-3's
  eligible-set denominator calls `cell_map_lookup` from `src/claude_wayfinder/match/_cells.py` (§3.0,
  §4.2), so a change to `_cells.py` after corpus accumulation can silently shift KC-3's result even
  though `_compose.py` is untouched. Therefore `scripts/shadow-kc-report.py` MUST: (1) **extract and
  validate `matcher_version`** from the corpus rows — first verifying that **every row resolves to
  the exact same `matcher_version` value** before trusting it, rather than a hand-entered value.
  **If rows disagree** (mixed/inconsistent `matcher_version` across the corpus), that is itself a
  **provenance failure** — the evidence spans multiple code versions and cannot be silently collapsed
  to one — so the tool MUST emit a hard warning and exit non-zero, not silently pick the
  first/majority value; (2) **compare each runtime module a selected KC method depends on — at minimum
  `_compose.py` and `_cells.py`, and any other module a chosen KC/D-KC4 method reads at runtime —
  between that commit and HEAD** — `matcher_version` is a **commit short-SHA** (`git rev-parse
  --short HEAD`, per `_get_matcher_version` / `tests/test_match/test_matcher_version.py:19`–`:20`,
  `:133`–`:159`), so the correct check is a semantic file diff per module, e.g. **`git diff --quiet
  <matcher_version> HEAD -- src/claude_wayfinder/match/_compose.py`** and the equivalent for
  `src/claude_wayfinder/match/_cells.py`** (exit 0 = unchanged, exit 1 = the file changed between
  corpus accumulation and now). **Do NOT compare `matcher_version` against `git rev-parse
  HEAD:src/claude_wayfinder/match/_compose.py`** — that yields a *blob* hash, which never equals a
  *commit* SHA, so the check would fire on every run and become noise; (3) **emit a hard warning
  and exit non-zero when any of these files diverges**, so a KC report generated against a
  `_compose.py` or `_cells.py` newer than the one that produced the shadow log cannot be silently
  treated as valid. **Non-SHA edge case:** if `matcher_version` is `"unknown"` or a dist-version string
  (the pip-install fallbacks — same test file `:1`–`:20`), the commit diff is not resolvable; in that
  case emit the same hard warning (provenance unverifiable) and exit non-zero rather than passing
  silently; and (4) **also check for a dirty/uncommitted working tree.** The `git diff --quiet
  <matcher_version> HEAD -- <module>` comparison only diffs two *committed* states — it does **not**
  detect uncommitted or staged changes in the current working tree, so a report could run against code
  newer than `HEAD` (a dirty checkout) and this check would still pass. The tool MUST additionally run
  a working-tree check — e.g. `git status --porcelain -- <module>`, or a `git diff --quiet` against the
  worktree rather than only `<matcher_version>` vs `HEAD` — for every module a chosen KC method depends
  on, and treat **any dirty state on a dependency module as the same hard-warning/non-zero-exit
  failure** as a resolved commit-to-commit divergence. The report still records both values as a
  caveat, but the gate is the tooling check, not the prose. **The KC evidence only validates the set of
  runtime modules that produced it** — M15-7's flip must use that same version, or shadow data must be
  re-accumulated.

### 4.5 Phase B deliverables

- `scripts/corpus/eval/_kc.py` — spec-exact KC-1..KC-5 logic (reusing `_metrics.py` RC/CW), including
  the KC-2 0.2558 anchor-provenance assertion (§4.3) as a documented, asserted module constant.
- `scripts/shadow-kc-report.py` — CLI: load corpus + gold, compute KCs, emit the report (Markdown +
  optional `--json`). Also **extracts `matcher_version` from the corpus rows — first verifying all
  rows resolve to the exact same value (hard warning + non-zero exit on mismatch, not a silent
  first/majority pick) — and compares it against the current git state of every runtime module a
  selected KC method depends on — at minimum `_compose.py` and `_cells.py` (KC-3's `cell_map_lookup`)
  — exiting non-zero on divergence in any of them.** This git-state comparison covers both (a)
  committed divergence (`<matcher_version>` vs `HEAD`) and (b) a dirty/uncommitted working tree on any
  dependency module (`git status --porcelain` or an equivalent worktree diff) — a `HEAD`-only compare
  cannot see uncommitted changes, so both checks are required (§4.4). (Location alternative: extend
  the existing eval CLI — minor, D-LOC.)
- Unit tests (`tests/test_corpus_eval/test_kc.py` — flat `tests/test_<module>/` convention, matching the
  existing `tests/test_corpus_eval/` dir) with synthetic fixtures covering each KC, the eligible-set
  boundary (ungated / no-cell / low-confidence exclusions), the `self_handle` normalization path, the
  three-field KC-3 numerator classification (posture-routed vs gated-delegate vs excluded
  ungated-delegate, §4.2), and the INSUFFICIENT-DATA paths — the empty infra_deploy slice (KC-5), the
  empty KC-4 eligible set (KC-4 not falsely PASS, §4.2), and the empty KC-3 eligible set (§4.2) — so
  none reads as a vacuous PASS/FAIL. **This matrix covers KC computation only** — coverage for the
  Phase A label-stripping tool (`scripts/shadow-strip-for-labeling.py`) is a separate deliverable,
  specified in §3.3.
- **Test fixtures for the provenance guard itself** (§4.4), in `tests/test_corpus_eval/test_kc.py` or a
  dedicated provenance test module, whichever reads better — separate from the KC-computation matrix
  above because it exercises `shadow-kc-report.py`'s version/git-state checks rather than KC formulas.
  Required coverage: (a) a valid, consistent short SHA across all corpus rows → guard passes; (b)
  mixed/inconsistent `matcher_version` values across rows → guard fails (hard warning, non-zero exit);
  (c) `_compose.py` or `_cells.py` changed since the recorded `matcher_version` commit → guard fails;
  (d) an `"unknown"` or pip-dist-version string that cannot be resolved to a commit → guard fails
  (provenance unverifiable); (e) a git command/subprocess failure (not a git repo, git not on `PATH`) →
  guard fails safe rather than silently passing; and (f) a dirty/uncommitted working tree on a
  dependency module (`_compose.py` or `_cells.py`) → guard fails, even when `matcher_version` vs `HEAD`
  is otherwise clean.
- The **KC report** (`docs/research/2026-07-19-shadow-kc-report.md`) with the go/no-go verdict.

---

## 5. Sequencing

`§6 decisions` → `Phase A.0 denominator estimate` → `Phase A labeling (per D-LABEL)` → `gold frozen`
→ `Phase B tooling` → `KC report` → **user makes the flip go/no-go** (feeds M15-7).

Phase B tooling (the `_kc.py` module + CLI + tests) can be **built in parallel with Phase A labeling**
against synthetic fixtures — only the *final report run* needs the frozen gold. This shortens the
critical path.

---

## 6. DECISION REQUIRED — surface to user via router before work begins

- **D-KC4 (KC-4 method) — RESOLVED: (a) Structural / logged-data.** LINKED to D-LABEL. (a)
  **Structural / logged-data** (RECOMMENDED, CHOSEN): among
  rows where caller `domain ∈ {is_any, project_meta}` and gold differs, observe whether any actually
  `posture_routed` to a *differential* agent in the log; if none did, the mislabel was zero-cost by
  observation. No `_compose.py` dependency; faithful to "in-situ." (b) **Counterfactual re-run**:
  substitute the gold domain, re-run `compose_route`, diff the agent. Heavier; pulls a `src`
  dependency into the tooling and puts real weight on domain-label reliability. **Recommend (a).**
  **Accepted limitation of (a):** the structural method observes the route that was already logged
  under the (possibly wrong) caller domain — it does not re-run Compose with the gold domain
  substituted, so it is an observed-data proxy for the counterfactual, not a true counterfactual
  re-run. This is a deliberate tradeoff of choosing (a) — lightweight, no live re-routing — not an
  oversight; it was weighed against option (b) when D-KC4 was resolved and (a) was still recommended.
- **D-LABEL (Phase A methodology) — RESOLVED: Option 2 (Calibrated middle).** LINKED to D-KC4. Option 1
  (light single-rater) / Option 2 (calibrated middle, RECOMMENDED default, **CHOSEN**) / Option 3
  (heavy full ceremony), per §3.2. Chosen shape: Pass 1
  full-coverage independent labeling (2–3 raters, blind per §3.1); Pass 2 a fresh rater relabels a
  stratified n≈40 subsample for inter-rater reliability; pre-register the `gold_agent` bar; adjudicate;
  user checkpoint before freeze. If D-KC4 = (a)
  structural, Option 1 is genuinely defensible; if D-KC4 = (b) counterfactual, use Option 2/3.
  **Two guards on the Option-1 branch (do not defend it on `gold_agent` reliability alone):**
  (a) the reliability bar Option 1 must clear includes **`infra_deploy` domain accuracy on the labeled
  slice**, not just `gold_agent` accuracy — because KC-5's n≈3–5 `infra_deploy` slice means one
  mislabeled domain collapses the denominator (§3.2 caveat, §3.0); budget an `infra_deploy`-domain
  spot-check or drop to Option 2. (b) Regardless of methodology, **KC-4 must report INSUFFICIENT-DATA
  when its eligible set is empty** (§4.2) — a single Option-1 rater reproducing the caller's
  `is_any→project_meta` mislabel pattern makes gold never differ from the caller, empties the KC-4
  eligible set, and would otherwise let KC-4 vacuously PASS.
- **D-KC1-MARGIN (whole-sample vs gated-subset for KC-1(ii)) — RESOLVED: whole sample (spec-literal).**
  Spec is literal on whole-sample
  (`:585`). Given the dilution in §4.4, confirm the +0.20 gate is judged on the **whole sample**
  (report both cuts regardless). Recommend: keep the spec-literal whole-sample gate as the pass/fail,
  present the gated-subset cut as interpretive context only, not a second gate.
- **D-KC2-ANCHOR (KC-2 bar) — RESOLVED: fixed 0.2558 (spec-literal).** Is the hard block ≤ the fixed
  **0.2558** (spec-literal, `:586`), or ≤
  the **in-situ lexical CW** computed on this same sample? Recommend: keep the fixed 0.2558 as the
  gate (comparable across passes, does not replace the gate), report in-situ lexical CW alongside for transparency.
- **D-N (target label count) — RESOLVED: ~120 + oversample thin strata.** Recommend labeling ~**120** (margin above the ≥100 floor for
  disputes/exclusions) **plus** any oversample the §3.0 estimate says KC-3/KC-5 need. Confirm N and
  whether oversampling infra_deploy/gated-high-confidence is acceptable (it biases the sample away from
  natural traffic mix — acceptable for a gate, but a conscious call).
- **D-LOC (tooling location, minor) — RESOLVED: standalone CLI.** New `scripts/shadow-kc-report.py` + `scripts/corpus/eval/_kc.py`
  vs extending the existing eval CLI. Recommend the standalone CLI (keeps the offline eval harness and
  the shadow-KC report as separate entry points).

---

## 7. Proposed sub-issue decomposition (DO NOT create until user go-ahead)

`#423` is scoped large enough that a split clarifies the dependency and lets tooling proceed in parallel
with labeling. Under milestone **M15** (`...2026-06-19-matcher-v3-ship-live.md:398`–`:420`):

| Proposed sub-issue | Phase | Depends on | Notes |
|---|---|---|---|
| **M15-6a — Gold-anchor the shadow sample (denominator estimate + strip tooling + labeling + report)** | A | M15-5 (data), §6 decisions | Owner: user/SME + labeler agents. Produces committed redacted gold JSONL + labeling report. |
| **M15-6b — KC-1..KC-5 tooling (`_kc.py` + `shadow-kc-report.py` + tests)** | B | §6 decisions (not on A — builds against synthetic fixtures) | Reuses `_metrics.py` RC/CW. Can run parallel to M15-6a. **When this issue is created, its body should carry a task to close the 0.2558 KC-2 anchor-provenance check (§4.3) and record the confirmed denominator definition in the issue body** — forward guidance for issue-creation time, complementing the in-code assertion. |
| **M15-6c — Run the KC report on frozen gold + go/no-go verdict** | B | M15-6a + M15-6b | Produces `docs/research/2026-07-19-shadow-kc-report.md`. Feeds the M15-7 flip decision. |

Alternatively keep #423 as one issue if the user prefers not to split — but the labeling prerequisite
(A) and the tooling (B) are independently workable and the split reflects that.

---

## 8. Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **Per-KC denominator collapse** (KC-5 infra_deploy n≈3; KC-3 eligible set thin from `is_any` prevalence; KC-4 eligible set empty if a single rater reproduces the caller's `is_any→project_meta` mislabel) | Medium | High (a criterion becomes uncomputable, or worse vacuously PASSes → the gate is ungateable) | §3.0 pre-labeling estimate + oversampling; KC-3/KC-4/KC-5 all report INSUFFICIENT-DATA on empty denominator rather than a false PASS/FAIL (§4.2); Option-1 `infra_deploy` domain-accuracy spot-check (§3.2). |
| **Label leakage / anchoring** (labeler sees caller labels in `input`, the `output`, or the `shadow` dict) | Medium (new — 168-corpus didn't have these fields) | High (inflated agreement invalidates the gold) | §3.1 strip mandate, enforced in the strip-and-present tooling. |
| **Whole-sample KC-1(ii) dilution misread as Compose failure** | Medium | Medium (a good flip blocked, or a bad one passed, for the wrong reason) | §4.4 dual-cut reporting + caller-label-match breakdown; D-KC1-MARGIN. |
| **RC/CW divergence from the offline eval** (re-implementing the kernel) | Low | High (KC-2 hard block on a non-comparable number) | §4.3 reuse `_metrics.py`; §4.3 in-code KC-2 0.2558 anchor-provenance assertion inside `_kc.py`. |
| **Stale runtime module** (logged shadow produced by a `_compose.py` or `_cells.py` older than what M15-7 flips — e.g. `cell_map_lookup` changes shift KC-3 silently) | Low | High (KC evidence validates the wrong code) | §4.4 tooling-enforced provenance check: `shadow-kc-report.py` extracts `matcher_version` and exits non-zero on git-state divergence of `_compose.py`, `_cells.py`, or any other runtime module a selected KC method depends on; flip must use the pinned version or re-accumulate. |
| **KC-4 counterfactual pulls `src` into tooling + needs robust domain labels** | Low (if D-KC4=structural) | Medium | Recommend structural KC-4 (D-KC4 a); if counterfactual chosen, escalate D-LABEL to Option 2/3. |
| **Phase A conflated with Phase 0 re-run** (scope creep) | Medium | Medium | §3.3 explicit framing: gold-anchoring, not re-earning `F_indep_lo`. |

---

## 9. Citations

Decision-driving claims cite one of: the canonical spec
(`docs/superpowers/specs/2026-06-14-two-axis-labeling-design.md:Lx`), the ship-live plan
(`docs/superpowers/plans/2026-06-19-matcher-v3-ship-live.md:Lx`), the gold-labeling report
(`docs/research/2026-06-12-gold-labeling-report.md:Lx`), the Phase-0b held-out report
(`docs/research/2026-06-17-heldout-validation.md:Lx` — for `F_indep_lo = 0.7391`), the shadow-corpus
manifest (`docs/research/2026-07-19-shadow-corpus-manifest.json:Lx`), the live shadow-record builder
(`src/claude_wayfinder/match/_main.py:62`–`:108`), the eval metrics (`scripts/corpus/eval/_metrics.py:Lx`),
the gold-label schema (`docs/research/2026-06-12-gold-labels-redacted.jsonl:1`), or the shadow-summary
reference (`scripts/shadow-summary.py:Lx`), inline at point of use.

**`unverified:` flags:**
1. **GitHub issue state** (M15-5 closed; #423 = M15-6; #364 closed) is taken from the ship-live plan and
   the on-disk shadow-data evidence — not re-checked against GitHub by this sub-agent (no GitHub read).
   Router should confirm before starting.
2. **The exact `_compose.py` version that produced the logged shadow decisions** was not diffed against
   HEAD this pass — §4.4 now mandates a tooling-enforced check (`shadow-kc-report.py` extracts
   `matcher_version` and exits non-zero on `_compose.py` git-state divergence); treat as a
   tooling/report-time verification, not a planning assumption.
3. **The 0.2558 lexical-CW anchor's provenance** (same `metric_confident_wrong_rate` definition) is not
   yet confirmed. §4.3 now makes this an **in-code gated assertion inside `_kc.py`** (a documented,
   asserted module constant) rather than a separable prose pre-check, and §7 M15-6b carries forward
   guidance to record the confirmed denominator definition in the issue body once created.
