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

**Artifact placement.** Label artifacts follow a two-tier rule (PR #348 review finding):

- **Full artifact (local-only):** `~/.claude/state/wayfinder-corpus/2026-06-12/gold-labels.jsonl`
  — all schema fields including free-text `notes` and `dispute_reason`. Never committed to
  the repository (free-text fields paraphrase private work content).
- **Redacted artifact (committed):** `docs/research/2026-06-12-gold-labels-redacted.jsonl`
  — axes-only fields (`corpus_id, domain, is_any, posture, gold_agent, confidence,
  disputed`); free-text fields stripped. Join-compatible with the eval harness `--labels`
  input so a clean checkout can run the #330 gold-dependent metrics without requiring
  the full local artifact.

After the user checkpoint and label freeze, the committed manifest
(`docs/research/2026-06-12-corpus-manifest.json`) gains aggregate counts: total labeled,
disputed count, per-posture distribution, and reliability statistics.

---

## 3. Decision Procedure

Apply steps in order. Earlier steps take precedence; do not skip ahead.

### Step 1 — Posture (from prompt evidence, per Spec E §10)

Identify the posture by looking for structural evidence in the dispatch-context fields.
The eight postures and their primary evidence are:

| Posture | Primary evidence (from §10 extractor definitions) |
|---------|---------------------------------------------------|
| `build` | **Unmarked default** — no posture extractor fires but artifact/file-path evidence or domain signal is present (§10.4). "Write X / add Y" requests with no other marker. |
| `diagnose` | **(a)** Machine-emitted failure output pasted in prompt (stacktrace, test-runner summary, compiler diagnostic, `panic:` — §10 E1/E2 extractor patterns); cause not yet known. **(b)** Read-only investigation of how existing/external code or a system *behaves* — comprehending an unfamiliar codebase, external-repo mechanics, or platform behaviour — with no failure pasted and no prior-art/alternatives markers (broadened per #395 / #364 Q2). |
| `assess` | PR URL, diff hunk, or `PR #N` reference present (§10 E3); or `tool_mentions` includes `get_pull_request*` |
| `critique` | Challenge-frame markers from §10 E10 frozen set **and** either (a) code/architecture artifact present → inquisitor path, or (b) no artifact → approach-critic path |
| `verify` | Two or more distinct artifact references plus relational conformance marker (§10 E5: "consistent with", "matches", "conforms to", "drifted from", etc.) |
| `plan` | No artifact-bearing evidence (§10 E9 gate) plus scope-frame markers from §10 E10 frozen set ("roadmap", "phases", "milestones", "scope") |
| `research` | No artifact-bearing evidence (§10 E9 gate) plus prior-art markers from §10 E10 frozen set ("prior art", "what exists", "alternatives", "has anyone"). Prior-art/alternatives *discovery* only — surveying what already exists out there. Investigating a specific existing codebase's behaviour is `diagnose` branch (b), not `research`. |
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

**Post-reliability clarification (2026-06-12, checkpoint-ratified).** GitHub/VCS state
operations — issue queries, PR queries and writes, repo metadata, CI status checks — carry
`domain: "project_meta"` per the §9.1 grid's own `project_meta` ops row ("project_meta
(VCS)"), even when no file paths are present in the prompt. `is_any` is reserved for prompts
that carry no subject signal at all (pure-conversational, context-free). This resolves the
domain-axis ambiguity that produced a below-target raw agreement of 0.775 on the
n=40 reliability subsample: eight of the nine disagreements were labelers splitting between
`project_meta` and `is_any` on GitHub-operate prompts with no file paths. All eight were
adjudicated to `project_meta`, and the gold labels assigned in pass 1 already followed
the `project_meta` reading; gold labels themselves were unchanged by this clarification.

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

**Branch-(b) diagnose gold-agent rule (#395).** For branch-(b) diagnose — read-only behaviour investigation with no pasted failure — the §10 E7 failure-span criterion does not apply (there is no failure to span); derive the gold agent from investigation **breadth** instead. Comprehending an external repo, an unfamiliar whole codebase, or a system's end-to-end behaviour is inherently cross-cutting → `investigator` (the default for branch b). A narrowly-scoped single-file / single-function behaviour question with no cross-layer reach → `debugger`. (Worked: 35229/35266/35297 are external-repo / whole-codebase comprehension → `investigator`; 34774 keeps `researcher` via the E11 directive-mention override regardless of posture.)

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
- **Explicit agent mention (E11 pass-through analog).** When the prompt *directively* names a routable agent — "delegate to ops", "have the inquisitor review this", "use code-writer", or the `agent_mentions` field is non-empty with directive intent — `gold_agent` is the named agent, provided it appears in the Appendix A vocabulary. Domain and posture are still labeled from the prompt's own evidence (the mention overrides only the agent, not the axes). A merely *descriptive* mention (an agent named as context — "code-writer returned X, now…" — not as an instruction) does not override; in that case derive `gold_agent` normally. Source: Spec E §10.2 E11 (`agent_mentions`, Tier A, "near-dispositive pass-through").

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

> "Are there existing libraries for persisting a build-on-startup catalog across sessions, and is rolling our own a reasonable approach?"
> `file_paths: []`

- **Reading A:** E9 fires (no artifacts); explicit prior-art question ("are there existing
  libraries…") → `research × *any*` → `researcher`. Genuine prior-art/alternatives discovery —
  "what already exists out there" — per the §3 Step 1 narrowed `research` definition.
- **Reading B:** E9 fires; "is rolling our own a reasonable approach?" soundness question →
  `critique × *any* × no-artifact` → `approach-critic`.
- **Both readings are defensible.** The prompt explicitly asks BOTH "what already exists?"
  (researcher) AND "is our approach sound?" (approach-critic); the routing-table delineation
  ("what prior art exists?" → `researcher`; "is this idea sound?" → `approach-critic`) does not
  resolve a prompt that genuinely carries both intents.
- **Label:** `disputed: true`, `dispute_reason: "researcher (explicit prior-art: 'are there existing libraries?') vs approach-critic (soundness: 'is rolling our own reasonable?'); the prompt genuinely mixes prior-art discovery and design-soundness intents"`.
- **Confidence:** `"low"`.

### Ex 7 — Explicit agent mention (directive vs descriptive)

> "Have the inquisitor do a harsh pass over the dispatch module before we merge."
> `file_paths: [src/dispatch/]`, `agent_mentions: ["inquisitor"]`

- **Posture:** Challenge frame + code artifact present → `posture: "critique"`.
- **Domain:** `src/` path → `domain: "code"`.
- **Grid cell:** `code × critique` → `inquisitor` (grid result and E11 agree; override is unambiguous).
- **Gold agent:** `agent_mentions` is non-empty with clear directive intent ("have the inquisitor do…") → E11 pass-through → `gold_agent: "inquisitor"`. The mention overrides only the agent; domain and posture are derived from the prompt's own evidence as above.
- **Confidence:** `"high"`.

**Contrast — descriptive mention (no override).** A prompt like "code-writer already added the flag; now verify it matches the spec in `docs/api.md`" with `agent_mentions: ["code-writer"]` names code-writer as background context, not as a routing instruction. E11 does not fire. Derive normally: E5 (two artifact refs + "matches") → `posture: "verify"`; `domain: "docs_prose"` or `"code"` depending on which artifact anchors the question → `gold_agent: "auditor"`.

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
justification. Label artifacts follow the two-tier placement rule in §2: the full
artifact at `~/.claude/state/wayfinder-corpus/2026-06-12/gold-labels.jsonl` is local-only
and never committed; the redacted axes-only copy at
`docs/research/2026-06-12-gold-labels-redacted.jsonl` is committed and is the artifact
the #330 eval harness consumes via `--labels`.

---

## Appendix A — Full Posture-to-Agent Reference

Condensed from Spec E §9.1 grid and `general-purpose.md § Mandatory Code Routing`:

| Gold agent | Domain | Posture | Key discriminator |
|---|---|---|---|
| `code-writer` | code | build | target behavior known; default-build |
| `doc-writer` | docs_prose | build | prose artifact target |
| `debugger` | code | diagnose | (a) failure + cause unknown + code-bounded (single layer); or (b) narrow single-area code-behaviour investigation, no failure |
| `investigator` | *any* / cross | diagnose | (a) failure + cause unknown + spans layers; or (b) external-repo / whole-codebase / cross-layer behaviour investigation, no failure |
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

---

## Amendment Log

This log records all changes made to the rubric. Amendments that alter the labeling procedure
are noted separately from those that only clarify existing wording. Gold labels already
assigned retain their validity unless the amendment entry says otherwise.

| Date | Change | Commit / authority | Labels affected |
|------|--------|--------------------|-----------------|
| 2026-06-12 | **E11 explicit-agent-mention override added** (§3 Step 3, routing-table overrides): directive `agent_mentions` field now constitutes near-dispositive pass-through; descriptive mentions distinguished and excluded. Added pre-labeling, before pass 1 began. | commit `5e5c57e`; issue #339 | Applies to rows with non-empty `agent_mentions` (34/168 rows have non-empty directive mentions; 109/168 have the key present including empty lists — see PR #348 correction) |
| 2026-06-12 | **Domain-of-operate clarification added to §3 Step 2** (post-reliability, checkpoint-ratified): GitHub/VCS state operations carry `domain: "project_meta"` per §9.1 ops row; `is_any` reserved for no-subject-signal prompts. Ratified at user checkpoint after reliability pass yielded 0.775 domain agreement (target 0.85) traced to this ambiguity. | user checkpoint 2026-06-12; issue #339 | Gold labels unchanged — pass-1 labelers already applied the `project_meta` reading; amendment documents the ruling rather than correcting labels |
| 2026-06-12 | **Two-tier label placement rule adopted** (§2 Artifact placement, §8 Freeze Semantics): full label file (all fields) stays local-only; a redacted axes-only copy (`corpus_id, domain, is_any, posture, gold_agent, confidence, disputed`) is committed at `docs/research/2026-06-12-gold-labels-redacted.jsonl` so a clean checkout can run the #330 gold-dependent metrics. Prior wording made labels frozen on one machine only. Also corrects the E11 coverage count: non-empty `agent_mentions` on 34/168 rows (not 109), E11 fired on 31 rows. | PR #348 review; issue #339 | Gold labels unchanged; only placement rule and reported count corrected |
| 2026-06-19 | **`diagnose` broadened beyond failure-gated** (§3 Step 1): branch (b) added — read-only investigation of how existing/external code or a system *behaves* (no failure pasted, no prior-art markers) now resolves to `diagnose`, not `research`; `research` narrowed to prior-art/alternatives *discovery*. Per #395 / #364 Q2 adjudication. | issue #395; #364 Q2 | Gold relabeled: 35229/35266/35297 research→diagnose (gold_agent researcher→investigator); 34774 posture research→diagnose (gold_agent stays researcher, E11 directive-mention lock); 35414 flagged disputed (3-way research/diagnose/plan). 34909/34912 previously adjudicated under #394. Re-measured RC delta = 0 (neutral) on the eval harness — the lexical matcher does not yet distinguish these diagnose-boundary cells (orthogonal matcher-coverage gap; code×diagnose tracked in #396). |
