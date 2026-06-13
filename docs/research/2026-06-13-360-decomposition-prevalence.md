# Decomposition Prevalence Scan — #360

**Date:** 2026-06-13
**Branch:** feat/330-measurement-run
**Question:** How often do real tasks require decomposition into compound (parallel) or phased (sequential) subtasks, and how does that interact with idea #1's two-axis (domain/posture) routing?

---

## 1. Setup

### Interpreter
`.venv/Scripts/python.exe` (project venv, `I:/ai/claude/claude-wayfinder/.claude/worktrees/vigilant-shamir-97d682/.venv`)

### Files
| Resource | Path |
|---|---|
| Corpus | `~/.claude/state/wayfinder-corpus/2026-06-12/wayfinder-corpus.jsonl` (168 entries) |
| Gold labels | `~/.claude/state/wayfinder-corpus/2026-06-12/gold-labels.jsonl` (168 entries) |
| Per-entry classification output | `.tmp/decomp-classification.jsonl` |
| Analysis script | `.tmp/decomp_analysis.py` |
| Export script | `.tmp/decomp_export.py` |

### Prior context
`docs/research/2026-06-13-358-oracle-domain-ceiling.md` — oracle domain + posture two-axis results. Key baseline: no-smoke routing-correctness = 0.3303 (lexical), 0.4954 (oracle domain-only), 0.8073 (oracle two-axis CellMap). **Note:** this report is a co-landing sibling research doc, pending merge in PR #359; it will resolve to the path above on `main` once #359 merges.

---

## 2. Definitions Applied

| Class | Definition | Conservative threshold |
|---|---|---|
| **atomic** | Single-agent task; one domain, one posture; a single router call dispatches it correctly | touching 2 files or doing 2 sub-steps is NOT compound if the same agent type handles both |
| **compound** | Multi-part task whose parts need DIFFERENT agent types, doable in parallel; router would need to fan out | e.g. "build the feature + document it + add CI" where code-writer, doc-writer, and devops are all genuinely needed |
| **phased** | Ordered pipeline where later phase depends on earlier; often different agent types; parallel impossible | e.g. "investigate then fix", "research then plan", "read issue then scope implementation" |

Conservative application: a task that mentions code AND updating a README in the same breath is still **atomic** if a single code-writer (or doc-writer) handles it end to end. Compound requires genuinely different agent types with no single-agent solution.

---

## 3. Corpus Cuts

| Cut | n | Description |
|---|---|---|
| full | 168 | All entries |
| no-smoke | 109 | Minus 59 smoke probes ("implement the new module" x29, "update the docs" x30) |

Smoke probes are atomic by construction — they are minimal generic probes chosen specifically to test single-signal routing.

---

## 4. Prevalence

### 4.1 Full cut (n=168)

| Class | Count | % |
|---|---|---|
| atomic | 167 | 99.4% |
| compound | 0 | 0.0% |
| phased | 1 | 0.6% |

### 4.2 No-smoke cut (n=109)

| Class | Count | % |
|---|---|---|
| atomic | 108 | 99.1% |
| compound | 0 | 0.0% |
| phased | 1 | 0.9% |

**One phased entry in 109 non-smoke tasks. Zero compound entries across all 168.**

---

## 5. The Single Phased Entry

**ID=35378** — domain=project_meta, posture=plan, gold_agent=project-planner

Task (paraphrased): "Find and read the context finder GitHub issue in the current repo, then scope it out for implementation. Read-only GitHub issue lookup followed by feature scoping and planning."

Matcher output: delegate → project-planner, confidence=1.0

**Why phased:** The task description explicitly describes two sub-operations with different agent signatures:
1. Read-only GitHub issue lookup (natural ops territory, operate posture)
2. Feature scoping and planning (project-planner territory, plan posture)

**How the router actually handles it:** The whole task is delegated to `project-planner` at confidence=1.0. The planner performs both the GH read and the scoping — no decomposition is needed in practice. The "then" language describes methodology, not a hard agent boundary.

**Proposed decomposition if forced:**
- Sub-task 1: `ops` — read GitHub issue #N, return issue body + AC checkboxes. Domain=project_meta, posture=operate.
- Sub-task 2: `project-planner` — scope issue for implementation, produce plan. Domain=project_meta, posture=plan.

**Decomposition verdict:** Unnecessary. A single project-planner delegated with confidence=1.0 handles both phases correctly. The "phased" reading is an artifact of explicit methodology description, not a structural agent-boundary requirement.

---

## 6. Disposition Impact: Did Compound/Phased Land in Weaker Dispositions?

### 6.1 Decision distribution (no-smoke cut)

| Decision | Atomic (n=108) | % | Compound+Phased (n=1) | % |
|---|---|---|---|---|
| delegate | 53 | 49.1% | 1 | 100.0% |
| advisory | 27 | 25.0% | 0 | 0.0% |
| self_handle | 24 | 22.2% | 0 | 0.0% |
| self_handle_unaided | 3 | 2.8% | 0 | 0.0% |
| needs_more_detail | 1 | 0.9% | 0 | 0.0% |

### 6.2 Confidence comparison (no-smoke)

| Group | Mean confidence |
|---|---|
| Atomic | 0.887 |
| Compound+Phased | 1.000 |

**The single phased entry got a clean delegate at confidence=1.0.** There is no evidence of disposition skew — the sample is n=1 and the result is the strongest possible disposition. This renders any confidence-dilution analysis inconclusive by sample size.

---

## 7. Domain/Posture Combination Analysis (Idea #1 x Idea #2)

**The key user question:** Does decomposition AMPLIFY two-axis routing (each subtask hits a confident domain/posture cell the whole task couldn't), is it ORTHOGONAL, or MARGINAL?

### 7.1 The one phased case (ID=35378)

Whole-task route: project_meta + plan → project-planner. Confident single-agent delegation. No routing failure.

Hypothetical per-subtask route:
- Sub-task 1 (ops read): project_meta + operate → ops. CellMap entry exists, would route confidently.
- Sub-task 2 (planning): project_meta + plan → project-planner. CellMap entry exists, would route confidently.

**Does decomposition unlock routing confidence that the whole task couldn't achieve?** No — the whole task already routes confidently (delegate, conf=1.0) to project-planner. Decomposition would add two routing calls to achieve what one call already does correctly.

### 7.2 Structural analysis: why compound/phased is rare in this corpus

The corpus reflects real router invocations from a working agent system. The user-confirmation of the two target failure modes (compound parallel fan-out, phased sequential pipeline) describes failure modes the router *could* encounter — not failure modes that *are occurring* in the corpus.

The corpus was collected from actual usage where:
1. **The human router already decomposes.** Before dispatching, the router already does hand-decomposition for clearly multi-agent requests. What reaches the matcher is already pre-filtered to single-dispatch shape.
2. **Agent capability overlap.** Agents like project-planner can perform read-only GitHub queries as part of planning work. The agent boundary that looks phased on paper collapses into atomic dispatch in practice.
3. **Task description granularity.** Users and the router describe tasks at the right granularity for a single agent — "plan the feature" not "first read the issue, then plan the feature, then write the spec."

### 7.3 Would per-subtask two-axis routing be more confident than whole-task routing?

For the 1 phased entry: whole-task routing is already confident. No gain.

For the 108 atomic entries where the matcher struggles (27 advisory, 24 self_handle, 3 self_handle_unaided, 1 needs_more_detail = 55 non-delegate outcomes): **none are struggling because the task is compound/phased.** They are struggling because:
- Lexical ambiguity (code-writer vs doc-writer for tasks that frame implementation work in docs language — the tie-set from #358)
- is_any/project_meta entries with self_handle gold (no agent cell exists)
- Low-information tasks (generic descriptions without enough signal)

Decomposition does not address any of these root causes.

---

## 8. Representative Cases (5 entries examined for decomposability)

These are the closest-to-compound/phased entries in the no-smoke corpus:

### Case A — ID=35378 (phased — the only classified one)
**Task:** "Find and read the context finder GitHub issue, then scope it out for implementation."
**Gold:** project-planner | delegate, conf=1.0
**Proposed decomp:** ops (read GH issue) → project-planner (scope)
**Routing impact:** None — already delegates correctly to a planner who does both. Decomposition adds latency with no quality gain.

### Case B — ID=35414 (borderline phased, classified atomic)
**Task:** "Investigate how baton-harness uses the GitHub PAT and design a startup validation that computes minimal fine-grained PAT permissions."
**Gold:** researcher | advisory, conf=0.5
**Proposed decomp if forced:** investigator (PAT usage investigation) → project-planner (validation design)
**Routing impact:** Matcher abstains (advisory, 0.5) on this task — a genuine miss. But the miss is NOT because it's phased; it's because the lexical scorer sees "investigate" and "design" and produces a mixed signal. The follow-up task (35416, code-writer, delegate conf=1.0) handles the actual implementation separately. Decomposition within 35414 would route to researcher confidently (both phases are researcher-territory). The advisory outcome would improve to a confident delegate whether or not we decompose.

### Case C — ID=33660 (atomic, investigation spanning multiple sources)
**Task:** "Investigate why a specific GitHub repo is not being discovered by the scraper. Read scraper source code and GitHub Actions workflow, then determine why the repo fails to match."
**Gold:** investigator | delegate, conf=1.0
**Analysis:** "then determine" is investigative methodology, not a different agent. Single investigator reads code + runs GH queries. Already routes correctly.

### Case D — ID=34677 (atomic, multi-issue creation)
**Task:** "Create a GitHub milestone and decompose the pilot harness scope from design docs into well-scoped GitHub issues assigned to that milestone."
**Gold:** self_handle | self_handle, conf=0.5
**Analysis:** Multiple GitHub writes (1 milestone + N issues) but all are self_handle GitHub operations. The matcher underestimates this (self_handle at 0.5) but not because of decomposition — it's a self_handle ambiguity.

### Case E — ID=34655 (atomic with phased hint)
**Task:** "Check the user's open PR, identify which PR is theirs, and determine its merge-conflict status... Read-only query first to locate the PR and confirm the conflict, then likely a git rebase or merge to resolve."
**Gold:** ops | advisory, conf=0.5
**Analysis:** The "then likely resolve" is conditional future work, not a committed second phase. The matcher abstains at advisory/0.5. The root cause is ambiguity between read (ops) and write (code-writer for merge resolution), not a genuine two-agent pipeline in the current task.

### Summary table

| ID | Class | Matcher outcome | Would decomp help? |
|---|---|---|---|
| 35378 | phased | delegate, conf=1.0 | No — already correct |
| 35414 | atomic | advisory, conf=0.5 | No — both phases are researcher; miss has other root cause |
| 33660 | atomic | delegate, conf=1.0 | No — already correct |
| 34677 | atomic | self_handle, conf=0.5 | No — self_handle ambiguity, not compound |
| 34655 | atomic | advisory, conf=0.5 | No — conditional second phase not committed |

---

## 9. Verdict: NO-GO

### 9.1 Prevalence finding

**Compound: 0/109 (0%) in no-smoke corpus. Phased: 1/109 (0.9%).**

The failure modes the user confirmed (compound parallel fan-out, phased sequential pipeline) are essentially absent from the real routing corpus. One entry in 109 has a "phased" reading, and even that entry routes correctly without any decomposition.

### 9.2 Disposition impact finding

**The single phased entry received the strongest possible disposition (delegate, conf=1.0).** There is no evidence that compound/phased structure causes disposition skew or confidence dilution in this corpus. The matcher's abstentions and low-confidence decisions (55/109 non-delegate outcomes) are entirely explained by lexical ambiguity, self_handle structural gaps, and low-information tasks — not by multi-agent structure.

### 9.3 Combination verdict (Idea #2 x Idea #1): ORTHOGONAL

Decomposition does NOT amplify two-axis routing. The 55 weak-disposition atomic entries are weak because of within-domain lexical ambiguity and self_handle structural mismatches — both of which two-axis routing addresses directly (as shown in #358's +28–31 pp oracle ceiling for domain+posture). Adding a decomposition layer on top of those entries would not help: they are already atomic and the routing failure is within-task, not multi-agent.

The 1 phased entry is already routed correctly. Decomposing it adds coordination overhead to fix something that isn't broken.

### 9.4 Why the failure modes are absent

Two structural reasons:
1. **Pre-filtering by the human router.** The corpus reflects post-decomposition dispatch events. The router already hand-decomposes compound/phased requests before sending to the matcher. The corpus is not a sample of raw user input; it is a sample of already-scoped dispatch calls.
2. **Agent capability overlap is wide.** Most "phased" readings dissolve because the first-phase agent can perform the second-phase work: a researcher does investigate + design; a project-planner does GH read + scope; an investigator does code read + root-cause determination. The binary agent-boundary assumption underlying "phased" routing overstates how specialized agents are in practice.

### 9.5 Risk of over-building

A formal decomposition mechanism would require: LLM compound/phased classifier, subtask segmentation, per-subtask routing calls, result assembly. This is a significant complexity increase. Given a 0–0.9% base rate in the corpus and no evidence of routing failures attributable to compound/phased structure, the mechanism would be solving a problem that does not materially exist in the data.

**Verdict: NO-GO. The failure mode is too rare and too well-handled by the current single-dispatch model to justify a decomposition mechanism.**

---

## 10. Open Questions

1. **Corpus representativeness.** The corpus is drawn from the existing router's output — meaning it is already filtered by a human router who decomposes before dispatching. A corpus drawn from raw user inputs (before any human pre-filtering) might show higher compound/phased rates. This is a known limitation of the measurement design.

2. **Future corpus growth.** As the system scales and raw-user-input routing becomes the primary mode (less human pre-filtering), compound/phased rates may increase. The scan should be re-run on a raw-input corpus if that transition occurs.

3. **Are there domains where compound/phased is more likely?** This corpus is heavily weighted toward code-writer and ops tasks. Some domains (e.g. "build a feature + write tests + add CI" in a greenfield project) might have higher compound rates. The current corpus does not sample those patterns.

4. **Missing knowledge file.** No knowledge file was attached for the decomposition routing domain. A future `knowledge/routing/decomposition-patterns.md` could capture: (a) the pre-filtering assumption, (b) the agent-capability-overlap argument, (c) the raw-vs-filtered corpus distinction.
