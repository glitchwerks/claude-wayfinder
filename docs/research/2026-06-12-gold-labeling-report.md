---
title: Gold-Labeling Report — Phase-A Dispatch-Log Corpus
date: 2026-06-12
tracking: glitchwerks/claude-wayfinder#339
milestone: "Milestone 14 — Matcher v3"
status: FROZEN — labels frozen on PR merge per rubric §8
---

> **Gold adjudication notice (#402).** This is a frozen, dated record. The gold labels it consumed have since been adjudicated **in place** (#364/#394: 5 entries; #398/#399: corpus 33692 `assess`→`operate`; plus any later gold-ownership edits). Counts, distributions, and the gold sha cited below reflect the gold **as of this report's date** and are intentionally **not** updated — the committed redacted jsonl (`docs/research/2026-06-12-gold-labels-redacted.jsonl`) is the live source of truth. A reader cross-referencing current gold will see expected differences (e.g. `assess`/`operate`, `diagnose`/`research` posture counts). Per the frozen-snapshot model decided in #402, this record is preserved as historical evidence, not rewritten.

# Gold-Labeling Report — Phase-A Corpus (#339)

**Purpose.** This document records the process, quantitative results, adjudication
decisions, and findings from the gold-labeling pass over the phase-A dispatch-log corpus
(168 entries, manifest SHA `98454ca6...`). It is the committed evidence base supporting
the #330 measurement run and satisfies rubric §8 freeze requirement 2 (reliability pass
documented) and requirement 4 (aggregate counts written to manifest).

**Lineage.** Issue #339 (gold-labeling task) under Milestone 14 "Matcher v3"; parent
measurement issue #330; governing rubric `docs/research/2026-06-12-gold-labeling-rubric.md`.
Label artifact: `~/.claude/state/wayfinder-corpus/2026-06-12/gold-labels.jsonl` (local
only, never committed; aggregate counts below are the committed record).

---

## Process

### Pass 1 — Independent labeling

Four parallel independent labeler agents (auditor-class subagents) each labeled one
batch of 42 entries. Labelers received only the `corpus_id` and `input` fields from the
corpus JSONL; the matcher's recorded `output` field was stripped to prevent anchoring on
the system's own prior decision. Labelers applied the committed rubric
(`docs/research/2026-06-12-gold-labeling-rubric.md`) only — no system under test was
executed (independence constraint, rubric §5). The E11 explicit-agent-mention override
had been added to the rubric before pass 1 began (commit `5e5c57e`; rubric amendment log).

### Pass 2 — Reliability subsample

A fresh labeler agent (rubric-only, no access to pass-1 labels or notes) independently
relabeled a stratified n=40 subsample. Subsample drawn using seed 339 against the phase-A
strata (`decision_band × td_length_band × file_paths_present`) with floor = 2 per
populated cell, matching the design in rubric §7.

### Adjudication and user checkpoint

Disagreements between passes 1 and 2 were surfaced and adjudicated by the router. All
disputed entries (both pass-level disagreements and intra-pass rubric ambiguities flagged
by labelers) were presented at a user checkpoint on 2026-06-12 before labels froze. The
domain clarification identified during adjudication was ratified at the checkpoint and
appended to the rubric (§3 Step 2 post-reliability clarification; amendment log entry
2026-06-12).

---

## Results

### Coverage

| Metric | Count |
|--------|-------|
| Total corpus entries | 168 |
| Entries with complete label records | 168 |
| Coverage | 100% |
| Disputed entries retained post-checkpoint | 1 (0.6%) |
| Disputed entries pre-adjudication | 11 (6.5%) |

### Gold-agent distribution

| Gold agent | Count |
|------------|-------|
| `code-writer` | 61 |
| `doc-writer` | 43 |
| `ops` | 31 |
| `self_handle` | 13 |
| `investigator` | 6 |
| `researcher` | 6 |
| `project-planner` | 5 |
| `auditor` | 2 |
| `code-reviewer` | 1 |
| `debugger` | 0 |
| `inquisitor` | 0 |
| `approach-critic` | 0 |
| `devops` | 0 |
| `test-implementer` | 0 |

### Domain distribution

| Domain | Count |
|--------|-------|
| `code` | 74 |
| `docs_prose` | 43 |
| `project_meta` | 30 |
| `is_any` | 16 |
| `infra_deploy` | 5 |
| `data` | 0 |

### Posture distribution

| Posture | Count |
|---------|-------|
| `build` | 111 |
| `operate` | 36 |
| `research` | 8 |
| `plan` | 5 |
| `diagnose` | 4 |
| `assess` | 2 |
| `verify` | 2 |
| `critique` | 0 |

### Confidence distribution

| Confidence | Count |
|------------|-------|
| `high` | 112 |
| `medium` | 49 |
| `low` | 7 |

---

## Reliability vs Pre-Stated Targets

Targets were written in rubric §7 before measurement; they were not adjusted after
seeing the data.

| Axis | Subsample agreement | Target | Result |
|------|--------------------:|--------|--------|
| Posture | 39/40 = 0.975 | ≥ 0.85 | Pass |
| Domain | 31/40 = 0.775 | ≥ 0.85 | **Below target** |
| Exact cell (domain × posture) | 30/40 = 0.750 | ≥ 0.75 | Pass (at threshold) |
| Gold agent (informational) | 39/40 = 0.975 | — | — |

### Cause analysis for the domain miss

Eight of the nine domain disagreements were a single systematic rubric ambiguity:
whether GitHub/VCS operate-posture records (issue queries, PR queries, CI status checks)
carry `domain: "project_meta"` or `is_any` when no file paths are present. In all eight
cases the adjudicated reading is `project_meta`, consistent with Spec E §9.1's own
`project_meta (VCS)` ops-row label. The ninth disagreement was domain inference on a
bare smoke-probe prompt ("implement the new module") where domain defaults to `code`
by content.

The `gold_agent` axis was unaffected: for operate-posture records, both `project_meta`
and `is_any` resolve to `ops` via the §9.1 grid, so the domain miss is benign for
routing-gold quality. The miss is classified as rubric ambiguity, not labeler error.
The rubric was amended post-checkpoint (§3 Step 2 clarification, amendment log
2026-06-12) to resolve the ambiguity for future labeling passes.

---

## Adjudication Log

Summaries only — no raw corpus prompt text per the public-repo privacy rule.

**Schema fixes (3 records).** Three harness-carve-out records had `domain: null` with
`is_any: false`, which is an invalid schema combination (rubric §2: `domain` is null only
when `is_any` is true). All three were corrected to `domain: "project_meta"`, matching
the pattern of adjacent harness-path records that triggered the same routing-table
carve-out. No gold-agent change (all three were already `self_handle`).

**Smoke-test repeat normalization (29 records).** The two harness-emitted probe prompts
— "implement the new module" and "update the docs" — each appear multiple times in the
corpus (29 and 30 occurrences respectively; see Findings §1). Labelers split on the
is_any reading for a minority of these identical prompts. All 29 affected records were
normalized to the majority reading: `domain: "code"`, `posture: "build"`,
`gold_agent: "code-writer"` for "implement the new module"; `domain: "docs_prose"`,
`posture: "build"`, `gold_agent: "doc-writer"` for "update the docs". The minority
`is_any` reading was recorded in the `notes` field and counts in the dispute tally
before adjudication.

**Individual checkpoint rulings (5 records).**

- `corpus_id 33686` — adjudicated to `gold_agent: "code-writer"`. Competing read was
  `ops`; ruling turned on whether the prompt's `command_prefix` was present (absent →
  build, not operate).
- `corpus_id 34712` — adjudicated to `gold_agent: "doc-writer"` per the deployed routing
  table's explicit "plan files" line in `agents/general-purpose.md § Mandatory Code
  Routing`. Competing read was `project-planner`. Routing table wins per rubric §1
  conflict-resolution order.
- `corpus_id 34909` and `34912` — both adjudicated to `gold_agent: "investigator"` via
  E11 pass-through: `agent_mentions` field carries a directive intent naming investigator.
  Competing read was `debugger` (single-layer code stacktrace).
- `corpus_id 35378` — adjudicated to `gold_agent: "project-planner"` with pass-2
  labeler concurring. Competing read was `doc-writer` (docs_prose file paths present,
  but the task_description is unambiguously scope-framing, not prose-artifact build).
- `corpus_id 35405` — `disputed: true` retained post-checkpoint. Three distinct readings
  across pass 1 and pass 2; user declined to rule definitively. Recorded with all three
  candidate readings in `dispute_reason`. Does not block freeze per rubric §6 (disputes
  count toward disputed rate, not as blockers).

**Class ruling — infra_deploy×build (3 records).** Three records whose prompts target
GitHub Actions workflow YAML files (`file_paths` matching `.github/workflows/`) land in
`infra_deploy × build`, a cell the §9.1 grid leaves blank (`—`). The deployed routing
table in `agents/general-purpose.md § Mandatory Code Routing` makes `devops` advisory-only
and routes workflow-YAML edits to `code-writer`. Adjudicated to `gold_agent: "code-writer"`
for all three, consistent with the routing-table-wins rule (rubric §1).

---

## Findings

### 1. Smoke-test pollution is substantial

59 of 168 records (35.1%) are one of two repeated harness probe prompts: "implement
the new module" (29 occurrences) and "update the docs" (30 occurrences). These
harness-emitted strings are not user content; they inflate the `build` cells and the
`code-writer` / `doc-writer` counts with trivially easy cases. Issue #330 must report
evaluation metrics both including and excluding these rows to give an accurate picture of
matcher performance on organic prompts. A separate issue should be filed to investigate
why the dispatch log contains so many repeated probe entries.

### 2. Grid gaps are real; the routing table fills them

Two cells used by actual corpus entries are blank in the §9.1 grid: `infra_deploy × build`
(3 records, workflow-YAML edits) and `project_meta × build` (several records, plan-file
prose edits). Both are resolved by the deployed routing table in `general-purpose.md`
(`code-writer` and `doc-writer` respectively via the routing-table-wins rule). This is
working as designed (rubric §1: "where the §9.1 grid and the deployed routing table
disagree, the deployed routing table wins"), but the gaps confirm that §9.1 is not
complete and the routing table carries load the grid does not.

### 3. Tail postures are nearly absent organically

Postures `critique` (0), `verify` (2), `assess` (2), and `diagnose` (4) are rare in the
organic corpus. The #330 measurement run cannot assess matcher performance on Tier-C
decisiveness or brake quality (E12 modifier, R2/R3 rules) from organic data alone; it
must rely on the P1–P14 synthetic fixtures for those postures.

### 4. E11 is the largest single override class; two distinct non-mention subsets

The `agent_mentions` key is present on 109/168 rows, but the field is non-empty (i.e.
carries directive intent) on only **34/168 (20.2%)** rows. The earlier count of 109 was
a key-presence count that included empty lists; it was caught in the PR #348 review
against the phase-A population profile.

E11 directive pass-through determined or confirmed `gold_agent` on **31 rows (18.5%)**,
all within the 34 non-empty rows — E11 was never applied to a row with an empty
mentions list, so gold labels are unaffected; only the reported count was wrong. The
non-empty mention values break down as: ops x21, code-writer x5, investigator x3,
project-planner x2, plus 2 rows mentioning claude-code-guide (not in the gold
vocabulary; E11 did not fire on the not-routable mention alone).

E11 remains the largest single gold-agent override class, driving most `ops` gold labels.
Two distinct subsets are relevant for downstream measurement (correction per PR #348
review round 2):

- **No-mention subset: 134 rows** — no non-empty `agent_mentions` field; E11
  structurally could not fire. Use this cut for measuring encoder/extractor value-add,
  because these rows are entirely free of mention signal.
- **E11-not-fired subset: 137 rows** — the 134 above plus corpus_ids 34627, 34659,
  and 34779, which carry a non-empty `["ops"]` mention but whose gold was determined
  by normal derivation, not pass-through (34779's gold is `self_handle`, differing
  from the mentioned agent). This is the correct count for "gold not determined by
  mention pass-through."

Issue #330 should use the **134-row no-mention cut** for value-add measurement: the
three additional rows still expose a mention that the matcher's E11 path may act on at
eval time, so they do not cleanly isolate encoder/extractor signal. Use 137 only when
the specific question is gold provenance rather than signal-path contribution.

### 5. Disputed rate is low post-adjudication

One record remains disputed after the user checkpoint (0.6%). This is consistent with
Spec E §8.5's claim that the routing-table-as-rubric is highly decidable. The pre-
adjudication disputed rate of 6.5% (11 records) fell to 0.6% after bulk normalization of
identical smoke-test prompts and schema-fix corrections, confirming that most apparent
disputes were procedural rather than substantive routing ambiguities.

---

## Freeze Statement

Labels freeze on this PR's merge into the default branch (rubric §8). Label artifacts
follow a two-tier placement rule (PR #348 review; rubric §8 amended 2026-06-12):

- **Full artifact (local-only):** `~/.claude/state/wayfinder-corpus/2026-06-12/gold-labels.jsonl`
  — all fields including free-text `notes` and `dispute_reason`; never committed to the
  repository. SHA-256: `c38be6564b78e0de8a5358315783189bc9ff7ee548bb53924584e590c8de4cad`.
- **Redacted artifact (committed):** `docs/research/2026-06-12-gold-labels-redacted.jsonl`
  — axes-only fields (`corpus_id, domain, is_any, posture, gold_agent, confidence,
  disputed`); free-text fields stripped to avoid paraphrasing private work content.
  Join-compatible with the eval harness `--labels` input so a clean checkout can run the
  #330 gold-dependent metrics. SHA-256:
  `e2be279be40037557d61a2079ca69d225fb323347e5815e4f7d69382a6e989d3`.

The aggregate counts in this report and in `docs/research/2026-06-12-corpus-manifest.json`
are the committed record. No label record may be amended after freeze except via a new
issue with documented justification (rubric §8).
