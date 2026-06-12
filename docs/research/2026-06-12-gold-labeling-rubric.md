---
title: Gold-Labeling Rubric — Phase-A Dispatch-Log Corpus
date: 2026-06-12
tracking: glitchwerks/claude-wayfinder#339
milestone: "Milestone 14 — Matcher v3"
status: COMMITTED — labels may not begin until this file is merged
---

# Gold-Labeling Rubric — Phase-A Corpus (#339)

**Purpose.** This document is the committed methodology record governing how gold labels
are assigned to the phase-A dispatch-log corpus (168 entries, manifest SHA
`98454ca6...`) before the #330 measurement run. It is written before labeling begins and
does not change during labeling. Labels assigned without reference to this rubric are not
valid gold.

**Lineage.** Issue #339 (gold-labeling task) under Milestone 14 "Matcher v3"; parent
measurement issue #330; design authority Spec E §8.5 (routing-table-as-rubric rationale,
`docs/superpowers/specs/2026-06-08-semantic-routing-additive-evidence-synthesis.md`).

---

## 1. Sources of Truth

| Source | Role in labeling |
|--------|-----------------|
| Spec E §9.1 — domain × posture grid | Primary cell derivation |
| Spec E §10 — posture definitions E1–E12 | Posture identification rules |
| Spec E §12.3 — R-rules R1–R4 | Authoritative overrides where §10 text is ambiguous or incorrect |
| `agents/general-purpose.md § Mandatory Code Routing` | Gold-agent vocabulary; harness carve-out; deployed routing-table overrides |

**Conflict resolution order:** §12.3 R-rules override §10 text (Spec E §12.3 states they are
the "authoritative layer over §10 — §10 text intentionally not retro-edited"). Where the §9.1
grid and the deployed routing table in `general-purpose.md` disagree, the deployed routing
table wins — it is the operational ground truth the matcher must reproduce.

---

## 2. Label Schema

One JSONL record per corpus entry, joined to the phase-A artifact on `corpus_id`.

| Field | Type | Notes |
|-------|------|-------|
| `corpus_id` | `int` | Phase-A stable ID: 1-based source-log line number; matches `corpus_id` in `wayfinder-corpus.jsonl` |
| `domain` | `"code" \| "infra_deploy" \| "data" \| "docs_prose" \| "project_meta" \| null` | null only when `is_any` is true |
| `is_any` | `bool` | True when no domain is inferable from the prompt (e.g. pure-conversational "continue", "merge it") |
| `posture` | `"build" \| "diagnose" \| "assess" \| "critique" \| "verify" \| "plan" \| "research" \| "operate"` | Required even when `is_any` is true |
| `gold_agent` | `string` | Resolved from §9.1 cell, then routing-table overrides applied; `self_handle` is valid for harness carve-outs and router-handled classes |
| `confidence` | `"high" \| "medium" \| "low"` | Labeler confidence in the full (domain, posture, gold_agent) assignment |
| `disputed` | `bool` | True when two or more readings are genuinely defensible under the rubric |
| `dispute_reason` | `string \| null` | Both candidate readings named when `disputed` is true; null otherwise |
| `notes` | `string \| null` | Free text for edge cases, extractor-hint recording, or flags for the user checkpoint |

Dispatch-context fields live under `record["input"]` in the corpus JSONL. Relevant input
fields: `task_description` (required), `file_paths`, `agent_mentions`, `tool_mentions`,
`command_prefix` (all optional per Spec E §10.2 extractor input contracts).

**Artifact placement.** The label file is LOCAL: `~/.claude/state/wayfinder-corpus/2026-06-12/gold-labels.jsonl`.
It is never committed to the repository. After the user checkpoint and label freeze, the
committed manifest (`docs/research/2026-06-12-corpus-manifest.json`) gains aggregate counts:
total labeled, disputed count, per-posture distribution, and reliability statistics.

---

## 3. Decision Procedure

Apply steps in order. Earlier steps take precedence; do not skip ahead.

### Step 1 — Posture (from prompt evidence, per Spec E §10)

Identify the posture by looking for structural evidence in the dispatch-context fields.
The eight postures and their primary evidence are:

| Posture | Primary evidence (from §10 extractor definitions) |
|---------|---------------------------------------------------|
| `build` | **Unmarked default** — no posture extractor fires but artifact/file-path evidence or domain signal is present (§10.4). "Write X / add Y" requests with no other marker. |
| `diagnose` | Machine-emitted failure output pasted in prompt (stacktrace, test-runner summary, compiler diagnostic, `panic:` — §10 E1/E2 extractor patterns); cause not yet known |
| `assess` | PR URL, diff hunk, or `PR #N` reference present (§10 E3); or `tool_mentions` includes `get_pull_request*` |
| `critique` | Challenge-frame markers from §10 E10 frozen set **and** either (a) code/architecture artifact present → inquisitor path, or (b) no artifact → approach-critic path |
| `verify` | Two or more distinct artifact references plus relational conformance marker (§10 E5: "consistent with", "matches", "conforms to", "drifted from", etc.) |
| `plan` | No artifact-bearing evidence (§10 E9 gate) plus scope-frame markers from §10 E10 frozen set ("roadmap", "phases", "milestones", "scope") |
| `research` | No artifact-bearing evidence (§10 E9 gate) plus prior-art markers from §10 E10 frozen set ("prior art", "what exists", "alternatives", "has anyone") |
| `operate` | Non-null `command_prefix` field, or VCS-command shape in prompt (§10 E8) |

**The unmarked default is `build`, not low-confidence.** A prompt with no posture marker
receives `posture: "build"` and `confidence: "high"` if domain evidence is present. It
does not receive `confidence: "low"` merely because the posture was inferred by default
(Spec E §10.4: "a prompt with no posture marker is `build`, not low-confidence").

**E6 modifier — cause_stated flips diagnose → build.** When E1/E2 evidence fires (machine
failure output) AND a causal connective ("after", "because", "due to", "caused by",
"since", "introduced by") shares a punctuation-delimited clause with the failure mention
(R3 clause-scoping rule, §12.3), the posture flips to `build`. The connective must be
clause-adjacent to the failure, not anywhere in the prompt; a connective explaining a
prior decision's motivation does not satisfy this condition (Spec E §12.3 R3, §11 P12
finding).

**E12 modifier — prose failure mention brakes confident non-diagnose.** When prose failure
language is present ("failing", "fails", "broken", "red", "errors out", "crashes") but no
machine-emitted output fired E1/E2, this suppresses E9 as a gate input and brakes any
non-diagnose confident result to advisory-tier confidence. Label as `confidence: "low"`
when E12 applies (Spec E §12.3 R2).

### Step 2 — Domain (from task content and file paths)

Use task content and `file_paths` to identify domain. Five values:

| Domain | Signal |
|--------|--------|
| `code` | `.py`, `.ts`, `.go`, `.js`, `.rs`, etc.; `src/**`, `tests/**`; explicit code references |
| `infra_deploy` | Infrastructure files (`terraform/`, `bicep/`, `.github/workflows/`), deployment commands (`az`, `kubectl`, `docker`, `terraform`), topology/provider questions |
| `data` | Database schemas, migrations, data pipeline files, query languages |
| `docs_prose` | `docs/**`, `*.md`, `*.rst`, `*.adoc`, README files, prose artifact targets |
| `project_meta` | Issue/PR scope questions, project planning, spec/plan file paths (`docs/superpowers/specs/`, `docs/superpowers/plans/`), VCS metadata |

When no domain signal is present and the prompt is conversational or context-free ("continue",
"merge it", "sounds good"), set `is_any: true, domain: null`. Note: four agents have
domain `*any*` in §9.1 (investigator, approach-critic, auditor, researcher) — prompts
routing to them need not be `is_any`; they may carry a clear domain and still route there
via posture.

### Step 3 — Gold agent (grid cell + routing-table overrides)

Derive `gold_agent` in two sub-steps:

**3a. §9.1 grid cell.** Look up (domain, posture) in the grid:

| | `build` | `diagnose` | `assess` | `critique` | `verify` | `plan` | `research` | `operate` |
|---|---|---|---|---|---|---|---|---|
| `code` | `code-writer` | `debugger`† | `code-reviewer` | `inquisitor`‡ | `auditor` | — | `researcher` | `ops` |
| `infra_deploy` | — | `investigator` | — | — | `auditor` | `devops` | `researcher` | `ops` |
| `data` | — | — | — | — | `auditor` | — | `researcher` | `ops` |
| `docs_prose` | `doc-writer` | — | — | — | `auditor` | — | `researcher` | `ops` |
| `project_meta` | — | `investigator` | `project-reviewer` | — | `auditor` | `project-planner` | `researcher` | `ops` |
| `*any*` | — | `investigator`† | — | `approach-critic`‡ | `auditor` | — | `researcher` | `ops` |

† `diagnose` split: single-layer (code stacktrace, `file_paths` span ≤ 1 area) → `debugger`;
spans multiple layers (`file_paths` across code + infra + data areas, or layer nouns name
≥ 2 distinct layers) → `investigator` (Spec E §9.1, §10 E7).

‡ `critique` split: code/architecture artifact present → `inquisitor`; idea only, no artifact
→ `approach-critic` (Spec E §9.1, §10.2 E9/E10 note).

Cells marked `—` are not covered by the current agent roster; a prompt landing there is
either a labeler error (re-examine posture or domain), `is_any`, or a genuine gap — flag
with `notes`.

**3b. Routing-table overrides.** After deriving the grid cell, apply the routing table in
`agents/general-purpose.md § Mandatory Code Routing`. Overrides that change the grid result:

- **Harness paths** (`agents/**/*.md`, `skills/**/SKILL.md`, `CLAUDE.md`, `AGENTS.md`,
  `GEMINI.md`, root harness config): `gold_agent: "self_handle"` regardless of domain/posture.
- **GitHub read queries** (list, search, CI status — no write intent): `gold_agent: "ops"`.
- **Adversarial harsh review of existing code/architecture** (including "give PR #N a harsh review"): `gold_agent: "inquisitor"`, not `code-reviewer`, per the routing table's explicit delineation (Spec E §11 P9 / §12.3 R4).
- **Known-cause fix** (cause stated in prompt, E6 flip): `gold_agent: "code-writer"` even if failure vocabulary is present.

### Step 4 — Confidence

Set `confidence` based on how clean the evidence is:

- `"high"`: one dominant evidence path; routing-table override is unambiguous or inapplicable.
- `"medium"`: two signals that agree, or one signal with minor noise.
- `"low"`: borderline evidence, prose-failure-only (E12 brake applies), or a grid cell where
  §10.5 flags coverage as weak (verify, plan, research, critique without agent mention).

Confidence is the labeler's epistemic assessment, not the matcher's future confidence band.

---

## 4. Worked Examples (Synthetic)

These examples use **invented prompts**. No raw corpus prompt text appears in this document.

### Ex 1 — Unmarked default-build

> "Add a `--dry-run` flag to the export command."
> `file_paths: [src/cli/export.py]`

- **Posture:** No E1/E2/E3/E8 fires. File path present → not E9. Default build (§10.4). `posture: "build"`.
- **Domain:** `.py` file → `domain: "code"`.
- **Gold agent:** `code × build` → `code-writer`.
- **Confidence:** `"high"` — default-build with file-path domain evidence is unambiguous.

### Ex 2 — Diagnose with machine failure output

> "Getting this on every run: `Traceback (most recent call last): File 'src/ingest.py', line 42, in run — KeyError: 'session_id'`. Never saw it before."
> `file_paths: [src/ingest.py]`

- **Posture:** E1 fires (Traceback + frame + exception shape). Cause not stated; no causal connective in the same clause as the failure. `posture: "diagnose"`.
- **Domain:** `.py` + `src/` path → `domain: "code"`. E7 area span = 1 → debugger side.
- **Gold agent:** `code × diagnose × span=1` → `debugger`.
- **Confidence:** `"high"`.

### Ex 3 — E6 flip (machine failure output but cause stated → build)

> "Got `FAILED tests/test_router.py::test_dispatch — AssertionError: expected delegate`. Broke after we renamed `route()` to `dispatch()` last PR. Fix the tests to match."
> `file_paths: [tests/test_router.py]`

- **Posture:** E2 fires (`FAILED …::…`). E6 check: "after" shares a clause with the failure
  reference → cause stated → flip diagnose → `posture: "build"`.
- **Domain:** test file in `tests/` → `domain: "code"`.
- **Gold agent:** `code × build` → `code-writer`.
- **Confidence:** `"high"`.

### Ex 4 — Pure conversational, is_any

> "Looks good, go ahead and merge it."
> `file_paths: []`, `command_prefix: null`

- **Posture:** No extractor fires. No file-path or domain evidence → E9 fires; E10 has no
  decisive set for "go ahead and merge". Posture is ambiguous. The closest structural read
  is an operate intent (merge = VCS action) but `command_prefix` is absent → `posture: "operate"` (weakly; flag).
- **Domain:** No signal → `is_any: true, domain: null`.
- **Gold agent:** `*any* × operate` → `ops`. Routing-table override: merge = GitHub write →
  `self_handle` (router handles GitHub writes directly). `gold_agent: "self_handle"`.
- **Confidence:** `"medium"`. Note: "merge it" is conversational; if no PR context exists,
  the prompt may be `needs_more_detail`. Flag in `notes`.

### Ex 5 — Harness carve-out

> "Update the trigger keywords in the code-writer agent definition to add 'script'."
> `file_paths: [agents/code-writer.md]`

- **Posture:** Default build (target behavior known, no failure).
- **Domain:** Would be `project_meta` under the grid, but harness-path override applies first.
- **Gold agent:** `file_paths` matches `agents/**/*.md` → harness carve-out →
  `gold_agent: "self_handle"` (routing table, `general-purpose.md § Harness carve-out`).
- **Confidence:** `"high"`.

### Ex 6 — Genuinely disputed (two defensible readings)

> "What if we stored the catalog on disk between sessions instead of rebuilding it on every call? Is that a reasonable approach?"
> `file_paths: []`

- **Reading A:** E9 fires (no artifacts); E10 proposal frame + implicit prior-art question →
  `research × *any*` → `researcher`.
- **Reading B:** E9 fires; E10 bare proposal + "reasonable approach" soundness question →
  `critique × *any* × no-artifact` → `approach-critic`.
- **Both readings are defensible.** The routing table's delineation: "what prior art exists?" →
  `researcher`; "is this idea sound?" → `approach-critic`. The prompt combines both intents
  without a decisive signal.
- **Label:** `disputed: true`, `dispute_reason: "researcher (prior-art reading: 'what already exists?') vs approach-critic (soundness reading: 'is this idea reasonable?'); routing table delineation does not resolve a prompt that mixes both intents"`.
- **Confidence:** `"low"`.

---

## 5. Independence Constraint

**Labels are assigned by applying this rubric only.** The matcher, the domain encoder,
the posture extractors (E1–E12), and any system under test in #330 must not be run during
labeling. Gold labels generated by any system under test are circular and invalid.

This constraint applies to the double-label subsample as well: the second-pass labeler uses
this rubric document from a fresh context, without access to first-pass labels.

---

## 6. Dispute Protocol

A prompt with two genuinely defensible readings under this rubric receives:
- `disputed: true`
- `dispute_reason`: both candidate readings named with the rubric path that supports each

Never force a disputed prompt into one class. Disputed entries count toward the
**disputed rate** reported before the user checkpoint. The disputed subset goes to a
user-review checkpoint before labels freeze — see §8 Freeze Semantics.

A dispute is itself a finding: it marks a routing-table ambiguity worth capturing as
a separate signal (Spec E §8.5: "prompts whose label is disputed under the rubric are
themselves signal — they mark real routing-table ambiguities").

---

## 7. Reliability Design

To measure labeling consistency before labels freeze, a stratified subsample is double-labeled.

**Subsample size:** n=40, stratified by the same three bands used in phase A:
`decision_band × td_length_band × file_paths_present` (matching the strata in
`docs/research/2026-06-12-corpus-manifest.json`). Draws proportionally from each populated
stratum cell; rare cells are oversampled to floor = 2.

**Second-pass independence:** the second labeler receives only this rubric document and
the corpus entries. No access to first-pass labels, notes, or intermediate decisions until
both passes are complete.

**Pre-stated agreement targets** (written here before measurement; must not be adjusted
after seeing the data):

| Axis | Agreement metric | Minimum target |
|------|-----------------|----------------|
| Posture | Per-axis raw agreement on the 8-way enum | ≥ 0.85 |
| Domain | Per-axis raw agreement (treating `is_any` as its own class) | ≥ 0.85 |
| Both axes (exact cell) | Exact match on (domain, posture) pair | ≥ 0.75 |

**Below-target handling:** all disagreements between the two passes are adjudicated and
documented with cause analysis (rubric ambiguity vs. labeler error). Labels freeze only
after the user checkpoint, regardless of whether targets are met. A below-target result
does not automatically invalidate labels — it elevates disputed entries and the cause
analysis to the user checkpoint agenda.

---

## 8. Freeze Semantics

Labels are frozen when this issue's (#339) PR merges into the default branch. The #330
measurement run executes only against frozen labels.

Before freeze, the following must be satisfied:
1. All 168 corpus entries have a complete label record (all required fields present).
2. The double-label reliability pass is complete and results are documented.
3. Disputed entries (all, not just the n=40 subsample) have been reviewed at the user checkpoint.
4. Aggregate counts are written back to the manifest: total labeled, disputed count,
   per-posture distribution, reliability statistics.

After freeze, no label record may be amended except via a new issue with a documented
justification. The `gold-labels.jsonl` file at
`~/.claude/state/wayfinder-corpus/2026-06-12/gold-labels.jsonl` is the frozen artifact;
it is never committed to the repository.

---

## Appendix A — Full Posture-to-Agent Reference

Condensed from Spec E §9.1 grid and `general-purpose.md § Mandatory Code Routing`:

| Gold agent | Domain | Posture | Key discriminator |
|---|---|---|---|
| `code-writer` | code | build | target behavior known; default-build |
| `doc-writer` | docs_prose | build | prose artifact target |
| `debugger` | code | diagnose | failure + cause unknown + code-bounded (single layer) |
| `investigator` | *any* / cross | diagnose | failure + cause unknown + spans layers |
| `code-reviewer` | code | assess | PR / diff present (non-harsh review) |
| `inquisitor` | code | assess / critique | harsh review or adversarial code critique |
| `project-reviewer` | project_meta | assess | spec / plan document present |
| `approach-critic` | *any* | critique | idea only — no code artifact |
| `auditor` | *any* | verify | source-of-truth named; no failure observed |
| `researcher` | *any* | research | prior-art discovery; no failure, no source-of-truth |
| `project-planner` | project_meta | plan | scope / requirements ask |
| `devops` | infra_deploy | plan | workload / topology / provider question |
| `ops` | *any* | operate | command-shaped read; GitHub read queries |
| `self_handle` | — | — | harness paths; GitHub writes |
| `test-implementer` | code | build | Phase 1 test-first only — uncommon in organic logs |
