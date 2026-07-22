---
title: Gold-Anchoring Report — Shadow-Dispatch Sample (M15-6a)
date: 2026-07-19
tracking: glitchwerks/claude-wayfinder#483
milestone: "M15 — matcher-v3-ship-live, Phase 3 (M15-6)"
status: FROZEN — labels frozen on PR merge, redacted artifact committed
---

# Gold-Anchoring Report — Shadow-Dispatch Sample (#483)

**Purpose.** This document records the process, reliability results, and adjudication
findings from gold-anchoring a 120-row stratified sample of the 245-entry in-situ
shadow-dispatch corpus (`docs/research/2026-07-19-shadow-corpus-manifest.json`). The
sample produces ground-truth `gold_agent` / `domain` / `posture` labels that feed the
KC-1..KC-5 computation tooling built in #484 (`scripts/corpus/eval/_kc.py`) for the M15-6
go/no-go report. This is the Phase A half of M15-6 (parent issue #423,
`docs/superpowers/plans/2026-07-19-m15-6-shadow-kc-report.md`); Phase B (#484) is tooling
only and does not depend on this report's prose, only on the frozen redacted gold JSONL.

---

## Methodology

Method chosen was **D-LABEL Option 2 — Calibrated middle**
(`docs/superpowers/plans/2026-07-19-m15-6-shadow-kc-report.md` §3.2, §6), the same shape
as the original 168-corpus process (`docs/research/2026-06-12-gold-labeling-report.md`)
minus the fourth parallel rater, plus a §3.1 independence-hardening strip step this
corpus specifically requires (its `input` object carries the caller's own
`domain`/`posture`/`confidence` labels, unlike the 168-corpus).

### Sampling

120 rows were drawn from the 245-entry corpus via stratified sampling (seed `483`, floor
`2` per populated stratum cell), using `_assign_stratum`/`_cell_key` from
`scripts/corpus/builder.py` — the same `decision_band × td_length_band ×
file_paths_present` stratification as the corpus manifest
(`docs/research/2026-07-19-shadow-corpus-manifest.json`). The draw tool is
`scripts/shadow-sample-for-labeling.py`.

### Stripping for labeler independence

Each sampled row was stripped to a labeler-safe view via
`scripts/shadow-strip-for-labeling.py`: only `corpus_id`, `task_description`,
`file_paths`, `agent_mentions`, `tool_mentions`, and `command_prefix` were shown to
raters. `input.{domain,posture,confidence,area_span}`, the matcher's own `output`, and
the entire `shadow` dict were dropped before any rater saw a row — raters were blind to
the caller's labels, the matcher's decision, and Compose's shadow decision throughout
both passes.

### Pass 1 — Full-coverage independent labeling

Three independent rater agents each labeled a 40-row batch (partitioned across the full
120-row sample) against the governing rubric
(`docs/research/2026-06-12-gold-labeling-rubric.md`), applying the rubric only —
independence constraint per rubric §5.

### Pass 2 — Reliability subsample

A fourth, fresh rater agent — blind to Pass 1 labels — independently relabeled a
stratified n=40 reliability subsample drawn from the 120-row Pass 1 sample (seed `4832`,
same stratification scheme).

---

## Pre-Registered Reliability Bars and Results

Targets were written before measurement per rubric §7, with one addition made explicit
mid-process: the original rubric §7 pre-registered bars for posture, domain, and exact
cell only. The `gold_agent` bar (≥0.85) was pre-registered separately, via a GitHub
comment on issue #483, mid-process before the reliability measurement — consistent with the plan's D-LABEL
requirement to "pre-register the `gold_agent` bar (the load-bearing axis) in addition to
domain/posture" (plan §3.2 Option 2). All four bars are reported here as pre-registered,
not post-hoc.

| Axis | Subsample agreement | Target | Result |
|------|--------------------:|--------|--------|
| Posture | 35/40 = 0.875 | ≥ 0.85 | Pass |
| Domain | 37/40 = 0.925 | ≥ 0.85 | Pass |
| Gold agent | 35/40 = 0.875 | ≥ 0.85 | Pass |
| Exact cell (domain × posture) | 32/40 = 0.800 | ≥ 0.75 | Pass |

All four pre-registered bars passed. Unlike the original 168-corpus run — where the
domain axis missed its target (0.775 vs 0.85,
`docs/research/2026-06-12-gold-labeling-report.md:129`) — this pass cleared domain
comfortably, consistent with the plan's expectation that a re-run against the
post-checkpoint-amended rubric would start from a stronger baseline
(`docs/superpowers/plans/2026-07-19-m15-6-shadow-kc-report.md` §3.2).

---

## Adjudication

46 of the 120 rows were adjudicated in total: **8 real Pass-1-vs-Pass-2 label
disagreements** on the reliability subsample, plus **38 Pass-1-rater-flagged ambiguous
entries** (rows a rater themselves marked uncertain against the rubric, most carrying a
`"Flag for adjudication"` note). These two sets overlap — several rows (e.g. 58597,
58632, 58026) are both a subsample disagreement and independently rater-flagged, and at
least one flagged subsample row (58145) had both passes agree — so the sets are not
disjoint and are not summed. All 46 rows were re-judged by a fresh adjudicator, blind to
which pass produced which label, against the rubric text. Net outcome across the full 46:
**10 corrected, 36 confirmed**.

### The 8 Pass-1-vs-Pass-2 disagreements

| `corpus_id` | Pass 1 | Pass 2 | Final resolution | Rationale |
|---|---|---|---|---|
| 58597 | `project_meta` / `operate` / `ops` | `infra_deploy` / `operate` / `ops` | `infra_deploy` / `operate` / `ops` | Subject scanned is `.github/workflows/*.yml` **file content** (infra_deploy domain-table signal), not GitHub issue/PR/CI metadata (the scope of the 2026-06-12 project_meta-ops clarification). |
| 58632 | `project_meta` / `operate` / `ops` | `infra_deploy` / `operate` / `ops` | `infra_deploy` / `operate` / `ops` | Same domain correction as 58597, single-repo narrower-scope variant — subject is `.github/workflows` directory content. |
| 58026 | `project_meta` / `build` / `self_handle` | `code` / `build` / `code-writer` | `code` / `build` / `code-writer` | A version-string bump changes no agent/router behavior, so it does not meet the harness-carve-out's purpose; `plugin.json`/`pyproject.toml` here are ordinary source/config getting a mechanical edit. |
| 56575 | `infra_deploy` / `operate` / `ops` | `infra_deploy` / `verify` / `auditor` | `infra_deploy` / `verify` / `auditor` | Dereferencing a pinned Action tag to its commit SHA is a conformance check (nominal reference vs. actual value) — E5's relational-conformance pattern in substance even without the literal "consistent with/matches" keyword. |
| 56695 | `project_meta` / `plan` / `self_handle` | `project_meta` / `diagnose` / `self_handle` | `project_meta` / `plan` / `self_handle` | `gold_agent` unchanged either way (CLAUDE.md harness-path override); posture is scoping-for-restructuring per the R1 precedent (reading a harness-governance doc to scope an extraction plan), not a bare behavior investigation. |
| 57832 | `code` / `verify` / `auditor` | `code` / `diagnose` / `investigator` | `code` / `diagnose` / `investigator` | The task checks three distinct mechanisms (config gating, telemetry stamping, a live self-check), not a clean two-named-artifact conformance check — branch-(b) diagnose; cross-mechanism breadth favors `investigator` over `debugger`. |
| 58092 | `project_meta` / `operate` / `ops` | `project_meta` / `assess` / `project-reviewer` | `project_meta` / `operate` / `ops` | A draft third answer (`verify`/`auditor`) was also considered and rejected on self-review. Neither alternate posture's defining marker fired (no E5 conformance pair; no PR#/diff for assess), so the read reverted to Pass 1's operate/ops default for a GitHub-state read bundle with no other clean marker. Low confidence retained. |
| 58899 | `project_meta` / `operate` / `ops` | `project_meta` / `plan` / `project-planner` | `project_meta` / `plan` / `project-planner` | Literal E10 "scope" marker present ("to scope work before implementation"); no file paths (E9 gate satisfied); the issue-body reads serve a reference/baseline role for the scoping deliverable, not the deliverable itself. |

### Flagged-entry review

The 38 flagged rows were flagged by their own Pass-1 rater as ambiguous against the
rubric (uncovered grid cells, harness-carve-out judgment calls, E11 vocabulary edge
cases, and similar rubric-boundary questions) — a rater's own doubt, not necessarily a
Pass-1/Pass-2 mismatch. As noted above, this set overlaps with the 8 subsample
disagreements rather than sitting apart from them. All flagged rows were adjudicated the
same way — re-judged against the rubric by the fresh adjudicator — and are folded into
the "10 corrected, 36 confirmed" total reported for the full 46.

---

## Notable Patterns Observed

**`project_meta` vs `infra_deploy` boundary confusion on workflow-file content.** Two of
the eight disagreements (58597, 58632) were the identical pattern: raters classified
GitHub Actions workflow *file content* edits/reads as `project_meta` (treating them as
GitHub/CI metadata) when the correct domain is `infra_deploy`, because the rubric's
`project_meta` domain table lists "CI status" as a signal and it is easy to over-apply
that line to any `.github/workflows/` touch. The correct read, per the rubric's domain
table, is that `.github/workflows/` file *content* is an `infra_deploy` signal, and the
`project_meta` "CI status" line is scoped to CI-run-state queries (pass/fail, logs), not
workflow-definition content. This recurred twice independently in an 8-row disagreement
set and is a good candidate for an explicit rubric clarification in a future amendment.

**`operate` as the default sink posture when a stricter marker doesn't clearly fire.**
Several disagreements turned on whether a stricter posture's defining marker — `verify`'s
E5 relational-conformance marker, `plan`'s E10 scope-frame marker — actually fired on
tasks that bundle GitHub-state reads with an implicit judgment call, or whether `operate`
was the safer default. 58092 is the clearest example: a "what's left before cutting the
next release" audit (commits + issues + PRs + readiness). The adjudicator considered both
`assess`/`project-reviewer` (rejected — no PR#/diff present) and `verify`/`auditor`
(rejected — no two-artifact conformance relation actually stated) before reverting to
Pass 1's `operate`/`ops`: when neither alternate posture's own defining marker clears the
bar, the bundle-of-reads-with-implicit-judgment case defaults to `operate`, not to
whichever fancier posture looks superficially plausible.

**Adjudicator self-correction is visible in the log, not just rubber-stamping.** Two
distinct self-corrections happened during the flagged-entry review pass itself — the
adjudicator caught and reversed its own draft errors mid-pass rather than accepting the
first read: (1) one row (56523) had an unreachable-cell error — an earlier draft read
reached for `infra_deploy × diagnose × debugger`, but the code-vs-cross-layer breadth
split (debugger vs. investigator) only exists within the `code` domain row; `infra_deploy
× diagnose` has a single populated cell, `investigator`. Corrected to `investigator`. (2)
Two entries (58284, 58642) had briefly been flipped from the raters' original
`operate`/`ops` to `plan`/`project-planner` during review, then reverted back to
`operate`/`ops` for consistency once it was clear the defining E10 scope-frame marker had
not actually fired on either — the same standard applied to reject 58092's `verify` pick.
This self-correcting behavior is evidence the flagged-entry review step is doing real
rubric-application work, not rubber-stamping whichever label arrived first.

**Despite the noise, all four reliability bars passed.** 46/120 (38%) of rows were
touched by adjudication in some form, but only 8/120 (~7%) were genuine Pass-1-vs-Pass-2
label disagreements on the reliability subsample. That gap — high touch rate, low true
disagreement rate — is consistent with a routing rubric that has genuinely borderline
cells (workflow-file domain, bundle-of-reads posture, harness-carve-out boundaries), not
with systemic rater unreliability. The reliability numbers back this up directly: all
four bars cleared their pre-registered targets.

**Tooling gap — nothing enforces the `domain: null` convention for `is_any: true`
rows.** One Pass 2 row (58229, generic local filesystem cleanup with no code/infra/docs/
project_meta subject signal) was hand-typed with `domain: "is_any"` — a string literal —
instead of `domain: null, is_any: true`, the schema's actual convention (rubric §2:
"`domain` is null only when `is_any` is true"). This was caught and normalized during
adjudication; both passes agreed on the substantive labels (`operate`/`ops`), so no
gold-agent decision was affected, but the malformed field would have broken a
schema-strict downstream reader. Recommend adding a schema validator to the labeling
pipeline (e.g. a `--validate` mode on `scripts/shadow-strip-for-labeling.py` or a
standalone check run before freeze) so future labeling rounds catch this class of error
before it reaches the committed redacted artifact rather than during manual adjudication.

---

## Deliverables

- `scripts/shadow-sample-for-labeling.py` (PR #491) — stratified sampling tool used to
  draw the 120-row sample from the 245-entry corpus.
- `docs/research/2026-07-19-shadow-sample-gold-labels-redacted.jsonl` (this PR) — the
  120-row frozen redacted gold-label set (`corpus_id, domain, is_any, posture,
  gold_agent, confidence, disputed`), join-compatible with the eval harness's `--labels`
  input for #484's KC computation.
- This report.

Full labeling artifacts — rater notes, dispute reasoning, and the adjudication log with
per-row rationale — remain local-only at
`~/.claude/state/wayfinder-corpus/2026-06-12/` per the two-tier artifact placement rule
established in the original labeling rubric (`docs/research/2026-06-12-gold-labeling-rubric.md`
§2), not committed. This is by design: free-text fields would paraphrase private work
content, and the redacted axes-only JSONL above is the artifact downstream tooling
actually consumes.

---

## Next Step

This report closes the Phase A prerequisite for M15-6 (#423). The frozen gold labels
feed directly into the KC-1..KC-5 computation tooling built under #484
(`scripts/corpus/eval/_kc.py`) to produce the M15-6 go/no-go report that gates the
Matcher v3 hard-routing flip.
