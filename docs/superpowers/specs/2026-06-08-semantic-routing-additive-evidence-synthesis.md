---
title: Semantic routing — Spec E — Additive Evidence Synthesis (HYPOTHESIS STUB)
date: 2026-06-08
tracking: glitchwerks/claude-wayfinder#325
status: HYPOTHESIS STUB — superseded IN DIRECTION by the 2026-06-08 scoped approach-critic
  pass (BOTH §4 claims broke) and the orthogonal-axes pivot that resolves them (see §8). The
  surviving principle (additive, non-destructive composition) still holds; the open work is now
  empirical — #293 gold-labeled error-correlation. Do not mature into a full spec until the
  §8.6 spike and the corpus measurement land.
touches: []  # deferred — no implementation surface defined until the hypothesis is validated
related:
  - glitchwerks/claude-wayfinder#325
  - glitchwerks/claude-wayfinder#293
  - docs/research/2026-06-07-semantic-routing.md
  - docs/superpowers/specs/2026-06-07-semantic-routing-dual-signal-ensemble.md      # A
  - docs/superpowers/specs/2026-06-07-semantic-routing-classifier-prefilter.md      # B
  - docs/superpowers/specs/2026-06-07-semantic-routing-structured-predicate-refinement.md  # C
  - docs/superpowers/specs/2026-06-08-semantic-routing-predicate-first-additive.md  # D
---

# Spec E — Additive Evidence Synthesis (hypothesis stub)

> **Read first.** This is a *hypothesis capture*, not a spec. It records where the
> A/B/C/D exploration converged, so the conclusion is durable and a scoped critic has a
> concrete artifact to attack. It deliberately does **not** specify weights, schema, or
> integration — those are empirical and depend on the corpus (#293). Do not expand or
> "lock" this until §4's claims survive a critic pass and the corpus can calibrate it.

## 1. Purpose

Capture the single principle the four-spec exploration converged on, and the two
conceptual claims that still need independent verification before the principle is
trustworthy. Companion evidence: the approach-critic critique of Spec D and the
field-revisit, both on #325.

## 2. The convergence

A/B/C/D were not four competing architectures — they were four probes of one design
space. Each variant that **destroyed information** failed (B's classifier *prune*; C
inherited it), and each variant that made **one signal carry too much** failed (D's
predicate-*primacy* + flat decision-capable boost → empty feasible set, FLAW 3 on #325).
What survived every critique is one principle:

> **Additive, non-destructive composition over the full roster.** Keep every agent
> scored; let multiple signals *add* evidence; let the existing rank/gap ladder decide;
> surface close calls as `ambiguous` to the router. No signal prunes; no single signal
> auto-delegates.

## 3. The synthesis (each signal in its correct role)

| Signal | Role | Borrowed from | Fatal flaw it avoids |
|---|---|---|---|
| **Coarse semantic** classification | Topical reach — additive *boost*, never a gate | B's classifier (strong coarse regime), made additive | B's destructive prune; FLAW 5 (semantic is *not* optional — it carries topical reach) |
| **Structured predicates** | Postural disambiguation — *bounded* evidence, never decisive alone | C's predicates, promoted to first-class but capped | D's FLAW 3 (no single signal auto-delegates); FLAW 1 (brittleness degrades a boost, not a gate) |
| **Keyword / glob / tool** | Lexical baseline contributor | the existing matcher | — |
| **Per-agent priors** | Base-rate floor | (new) | FLAW 6 (uniform-prior tilt) |
| **Aggregation** | Additive sum; *no single signal auto-delegates*; existing 0.85 / 0.2-gap ladder ranks; close → `ambiguous` → router | A's "agreement = confidence, disagreement → router" | A's fine-grained weak-regime encoder; D's empty feasible set |

Claimed advantage over each sibling: non-destructive (vs B/C), no single-signal primacy
(vs D), coarse-not-fine semantic (vs A).

## 4. The two un-self-verified claims (targets of the scoped critic)

These were asserted by the router during the field revisit and are **marking its own
homework** — they are the load-bearing conceptual claims an independent pass must verify
or break:

1. **Stacking escapes FLAW 3.** Requiring *stacked* evidence to `delegate` is claimed to
   dissolve the empty-feasible-set (no single boost needs ≥ 0.85). **Unverified risk:**
   does it just relocate the impossibility into the multi-signal weights — and does the
   "needs ≥ 2 signals" rule break the legitimate **single-dominant-signal** case (e.g.,
   an explicit command, or "review this PR" with one strong keyword) that *should*
   delegate on one signal?

2. **"Agreement = confidence" under correlated signals.** Confident-wrong is claimed to
   now require *multiple* signals to err together (a high bar). **Unverified risk:** the
   semantic category (`investigation`) and the predicate (`failure_observed`) fire on the
   *same* failure prompt — they are correlated **by construction**. If signals aren't
   independent, "agreement" is cheap, confident-wrong returns, and the A-style
   "disagreement → router" safety net is weaker than claimed. (This is FLAW 4 —
   correlation — resurfacing at the synthesis level; the "add a correlation guard"
   hand-wave is the thing to attack.)

## 5. Open / empirical questions (corpus-dependent — #293)

Not resolvable by more design; only by the labelled corpus:
- Calibration of the additive weights (the §4.1 stacking rule made concrete).
- Coarse-category separability in the chosen local encoder (Model2Vec).
- Distribution of no-keyword prompts by clean-predicate count (≥ 2 vs 1) — settles the
  FLAW-3 contingency.
- Correlation structure between semantic, predicate, and keyword signals (settles §4.2).
- Whether per-agent base-rate priors materially help.

## 6. Discipline / non-goals

This is intended to be the **last conceptual artifact** before pivoting to the corpus.
After the scoped §4 critic pass, further adversarial passes on an under-specified sketch
are diminishing returns. The next real work is **#293 (the corpus)** — it gates
calibration and validation of this synthesis and every sibling.

## 7. Sources / cross-references

- #325 — tracking issue; field-revisit and approach-critic critique of Spec D.
- #293 — labelled prompt corpus (the recurring prerequisite).
- Specs A/B/C/D and `docs/research/2026-06-07-semantic-routing.md` — the exploration this synthesizes.
- `unverified:` All quantitative claims carried from the session (multi-category rate, separability, predicate-count distribution) remain unvalidated pending #293.

## 8. Resolution — scoped-critic outcome and the orthogonal-axes pivot (2026-06-08)

The scoped approach-critic pass on §4 ran. **Both load-bearing claims broke.** This section
records the outcome and the design pivot that resolves them, so the conclusion is durable.

### 8.1 Critic outcome (both §4 claims fail)

Root cause: the synthesis used signal **count** as the safety variable when the operative
variable is signal **independence**. Two of the four signals — the coarse semantic category and
the `failure_observed` predicate — are correlated *by construction*: they fire off the *same*
failure language in the prompt.

- **§4.1 ("stacking escapes FLAW 3") fails.** A "needs ≥ 2 signals" rule adds no safety when two
  of the signals are one cue counted twice; it also breaks the legitimate single-dominant-signal
  delegate (explicit command, one strong keyword) that *should* route on one signal.
- **§4.2 ("agreement = confidence") fails.** Correlated agreement is *cheap*. The double-counted
  cue **inflates the additive sum into overconfidence**, which (a) promotes a recoverable
  `advisory` into an unrecoverable confident `delegate`, and (b) **disables the `ambiguous`→router
  safety net**, which was built to *trust* agreement. Grounded in ensemble-diversity theory
  (ensembles only help when errors are decorrelated) and naive-Bayes double-counting of correlated
  features. Source: scoped approach-critic critique, #325.

### 8.2 The pivot — re-aim the two signals to orthogonal axes

The correlated signals are *not* redundant; the overlap is in their **input** (both read the
failure words), not their potential information. The fix is to ask each a *different question on a
different axis* instead of the same intent question:

- **Semantic classifier → DOMAIN** (code-internals / infrastructure-deploy / data / docs), not
  intent. Reads the whole sentence, so it carries topical reach the predicate cannot represent.
- **Predicate(s) → EVIDENCE-TYPE / posture** (stack trace, error code, file path, failing-test
  ref, command, PR ref), not failure-presence. Crisp structural facts the embedding blurs.
- Their **combination is `domain × evidence-type`** — orthogonal axes that *narrow* correctly
  (infra-domain + ops-posture → devops) instead of piling onto one agent. Disagreement becomes
  *meaningful* again, which re-arms the §4.2 safety net legitimately.
- Worked example — `"tests are failing after the rename, update them to match the new API"`: the
  failure language fires *both* correlated intent-signals toward diagnosis, but the gold route is
  **code-writer** — the root cause is *known* (the rename), and per the routing table known-cause
  fixes are build-posture work. Re-aimed, the evidence predicate reads **cause-stated-in-prompt**
  and posture resolves to *build*, overriding the failure vocabulary. The shared cue no longer
  decides; the structural evidence does.
- *(Correction 2026-06-09: an earlier draft of this section — and the 2026-06-08 #325 capture
  comment — used `"the deploy keeps failing, figure out why"` with devops as the implied gold. By
  the roster's own routing table that prompt's gold is **investigator** (failure observed + cause
  unknown + spans layers; devops is a design consultant and explicitly does not do failure
  diagnosis), and the re-aimed grid routes it correctly via the cell-product
  `infra-domain × diagnose-posture → investigator` — no single axis value carries the decision.
  The mislabel itself is evidence for §8.5: gold labels must come from the routing table as
  rubric, not designer intuition.)*

### 8.3 Decorrelation is a hierarchy (weak → strong)

| Level | Change | Effect |
|---|---|---|
| 1 | same input, **same** question | fully redundant — the failure mode in 8.1 |
| 2 | same input, **different** question (8.2) | recovers each signal's *residual* unique info; lowers error-correlation |
| 3 | **different** input, different question — predicate sourced from structured dispatch fields (`file_paths`, `command_prefix`, doc refs) the encoder never sees | genuine independence; two sources can't both be fooled by the same phrasing. = the critic's literal fix #1 |

8.2 is Level 2; pair it with Level 3 wherever the dispatch context carries structured fields
disjoint from the prose.

### 8.4 The metric that actually decides it — *error* correlation, not firing correlation

"Distinct enough to be independent" must be measured as: *when one signal mis-routes, how often
does the other mis-route in the same direction?* (conditioned on the gold-correct route). Low → the
additive combination is safe; high → re-aiming did not decorrelate. Firing correlation (do they
fire together) is **not** the test.

### 8.5 Consequence for #293

To compute error-correlation the corpus must carry **gold routing labels** (the correct agent per
prompt), not just representative prompts. This is a larger labeling scope than prior framing and
must be made explicit when #293 is scoped. `unverified:` the residual error-correlation after
re-aiming is unmeasured until #293 exists.

**Labeling-rubric requirement (2026-06-09):** gold labels must be assigned by applying the routing
table's predicates, not by intuition — this session's own first worked example (§8.2) was initially
mislabeled by the designer (devops vs investigator), demonstrating the failure mode at n=1. The
routing table in `agents/general-purpose.md § Mandatory Code Routing` is the labeling rubric;
prompts whose label is disputed under the rubric are themselves signal (they mark real routing-table
ambiguities worth capturing).

### 8.6 Cheap de-risk before full labeling

Run a ~12-prompt adversarial hand-spike (deploy-as-investigation, review-as-investigation, etc.)
through rough domain + evidence-type extractors to confirm re-aiming *directionally* flips errors,
before committing to gold-labeling the full corpus.

### 8.7 Next-step order

(1) capture (this section + #325) → (2) define the two axes concretely → (3) hand-spike →
(4) scope #293 for gold-labeled error-correlation measurement.

## 9. Roster → grid validation (2026-06-09)

Step (2) of §8.7, executed against the live roster: 14 files in `~/.claude/agents/`, minus
`general-purpose` (the router; excluded from the scored pool) = **13 routable specialists**,
including `approach-critic` (added 2026-06-08, post-dating the original 12-agent survey).

### 9.1 The grid

| Agent | Domain | Posture | Tie-breaking evidence |
|---|---|---|---|
| code-writer | code | build | target behavior known (spec / issue / *cause stated*) |
| doc-writer | docs/prose | build | prose artifact target |
| debugger | code | diagnose | failure + cause unknown + code-bounded (stacktrace, failing test) |
| investigator | *cross-layer* | diagnose | failure + cause unknown + **spans layers** |
| code-reviewer | code | assess | PR / diff present |
| project-reviewer | project/meta | assess | spec / plan doc present |
| inquisitor | code/arch | critique | code or architecture **artifact present** |
| approach-critic | *any* | critique | idea-only — **no code artifact** |
| auditor | *any* | verify | source-of-truth named (schema, release notes, contract) |
| researcher | *any* | research | no failure, no source-of-truth — external discovery |
| project-planner | project/meta | plan | scope/requirements ask |
| devops | infra/deploy | plan | workload/topology question |
| ops | project/meta (VCS) | operate | command-shaped read (list/show/status) |

**Result: every agent occupies a distinguishable cell.** Posture is the primary separator
(8 values; no row holds > 2 agents). The five 2-agent rows resolve:

| Posture row | Pair | Split by |
|---|---|---|
| build | code-writer / doc-writer | domain ✓ |
| assess | code-reviewer / project-reviewer | domain ✓ (+ artifact type: PR vs spec — structural) |
| plan | project-planner / devops | domain ✓ |
| diagnose | debugger / investigator | ✗ domain cannot — **scope predicate** (single-layer vs spans-layers) |
| critique | inquisitor / approach-critic | ✗ domain cannot — **artifact presence** (code present vs idea-only) |

### 9.2 Findings

1. **Sharpened division of labor.** Domain finishes 3 of 5 doubles; the remaining 2 splits are
   *structural* facts (spans-layers, artifact-presence) — i.e. Level-3 evidence (§8.3): file paths
   attached, PR ref present, stacktrace block, layers named. Topic-domain fails exactly where the
   structured-fields signal is the natural discriminator. The three signals interlock rather than
   overlap.
2. **Cells route; axis values never do.** `infra` alone is ambiguous (devops? investigator?);
   `diagnose` alone is ambiguous (debugger? investigator?); the product `infra × diagnose` is
   unique → investigator. This is the additive principle (§2: no single signal decides) made
   concrete on the grid.
3. **Roster-level axis correlation is low where traffic is high.** code spans 4 postures, meta
   spans 3 — the axes are not roster-redundant in the high-traffic domains. docs/infra/data are
   posture-poor on the roster but prompt-space still varies within them.
4. **Four agents are domain-"any"** (investigator≈cross, approach-critic, auditor, researcher) —
   domain *abstains* rather than votes for them (abstain ≠ veto under additive composition). A
   high-entropy/diffuse encoder distribution is itself signal pointing at this class. Corollary:
   **no agent owns the data domain**; data prompts route on posture alone.
5. **Gold-label rubric proven necessary at n=1** — see the §8.2 correction (deploy example:
   intuited gold devops, rubric gold investigator).

### 9.3 Refined axis values (draft, pre-spike)

- **Domain** (encoder's target, 5-way coarse): `code · infra/deploy · data · docs/prose ·
  project/meta` (+ derived feature: distribution entropy → domain-"any" agent class).
- **Posture** (predicate's target, 8-way): `build · diagnose · assess · critique · verify · plan ·
  research · operate`, derived from evidence features: stacktrace/error block, failing-test ref,
  PR/diff ref, spec/plan doc path, source-of-truth named, **cause-stated**, layers-named,
  command-prefix, idea-without-artifact.

### 9.4 Open edge (the extractor-definition step)

Posture must be derived **predominantly from structural evidence**, not prose verbs — if posture
extraction leans on the same verbs the encoder embeds, the §8.1 correlation re-enters through the
side door. Defining which evidence features are extractable deterministically (and which need the
weak lexical-verb assist) is the core of the next work item, ahead of the §8.6 hand-spike.

## 10. Deterministic evidence extractors (step 1, 2026-06-09)

Executes §8.7 item (2) for the **posture axis**: for each §9.3 evidence feature, define what the
extractor reads, the rule it applies, and — the load-bearing classification — how deterministic it
is. Domain-axis extraction stays with the encoder and is out of scope here. All rule sketches are
drafts; the §8.6 hand-spike refines them before any code exists.

Extractor inputs are the matcher's dispatch-context fields (`task_description` required;
`file_paths`, `agent_mentions`, `tool_mentions`, `command_prefix` optional — input schema per
`skills/dispatch/SKILL.md`).

### 10.1 Tier model (determinism × decorrelation)

| Tier | Reads | Question asked | §8.3 level | Correlation risk |
|------|-------|----------------|------------|------------------|
| **A — structured-field** | `file_paths`, `command_prefix`, `tool_mentions`, `agent_mentions`, computed absences | "What artifacts accompany the request?" | Level 3 — input the encoder never sees | none |
| **B — text-shape** | `task_description`, but only machine-emitted / syntax-constrained shapes | "Is a machine artifact pasted or referenced here?" | Level 2, strong | low — matches shape, not word choice |
| **C — closed-marker lexical** | `task_description` prose | "Did the user use one of these N frozen markers?" | Level 2, weak | **high — this is the §8.1 side door** |

Why Tier B is safe enough: a stacktrace, diff hunk, runner summary, URL, or path token has a
fixed, machine- or syntax-imposed shape. The user's free phrasing varies; the artifact's shape
does not. The extractor asks a *structural* question of the same text the encoder reads
*topically* — different question, same input (Level 2), with the question pinned to content the
user did not author.

Cross-axis note: Tier-A fields may legitimately feed **both** axes (e.g. `command_prefix: az`
hints infra domain AND operate posture) without re-importing correlation — the §8.1/§8.3 concern
is signals sharing the encoder's input *and* question; Tier A never shares the input.

### 10.2 Extractor definitions (draft rule sketches)

| # | Extractor | Tier | Inputs | Rule sketch | Posture evidence |
|---|-----------|------|--------|-------------|------------------|
| E1 | `stacktrace_block` | B | text | `Traceback (most recent call last)`; ≥2 frame lines `^\s+at \S+\(.*:\d+(:\d+)?\)`; `^[\w.]*\b(Error\|Exception)\b[:(]`; compiler diag `:\d+:\d+: (error\|warning):`; `exit(ed)? (with )?(code\|status) \d+`; `panic:`; pytest `^E\s{3}` | diagnose |
| E2 | `test_failure_output` | B | text | runner summaries `\d+ (failed\|errors?)`; `FAILED \S+::\w+`; `AssertionError`; jest `✕` marks | diagnose |
| E3 | `vcs_artifact_ref` | B (+A) | text, tool_mentions | PR URL `/pull/\d+`; `\bPR ?#\d+`; diff hunk `^@@ -\d+` / `^diff --git`; ≥7-hex SHA token (require letter+digit mix); tool_mentions `get_pull_request*` | assess |
| E4 | `spec_plan_path` | A (+B) | file_paths, path tokens in text | globs `docs/**/*{spec,plan}*.md`, `docs/superpowers/{specs,plans}/**`, `**/adr*/**`; same glob test on path-shaped prose tokens | build (plan-execution) |
| E5 | `source_of_truth_pair` | B core + C assist | text, file_paths | core: ≥2 distinct artifact refs (paths/URLs); C assist: named-doc nouns {release notes, changelog, schema, contract, invariant} + relational markers {against, matches, conforms to, consistent with, in sync with, drifted from} | verify |
| E6 | `cause_stated` | C (modifier) | text | evaluated **only when E1/E2 fired**: causal connective {after, because, due to, caused by, since, introduced by} within proximity of the failure mention; B variant: `(root )?cause:` heading/bullet in issue-shaped briefs | flips diagnose → build |
| E7 | `area_span` | A (modifier) | file_paths × project areas | distinct-area count via `.claude/project-areas.json` globs (user-global CLAUDE.md § Project Areas); fallback coarse globs (src / tests / infra / docs) | inside diagnose: span ≤ 1 → debugger, ≥ 2 → investigator |
| E8 | `command_prefix` | A | command_prefix | non-null → operate; prefix table refines: `git`/`gh` → VCS-operate, `az`/`terraform`/`kubectl`/`docker` → infra-operate (+ domain hint, see §10.1 cross-axis note) | operate — strongest single extractor |
| E9 | `artifact_absence` | A (computed) | E1–E5, E8, file_paths | NOT(any artifact-bearing extractor fired ∨ file_paths nonempty ∨ command_prefix present ∨ path-shaped token in text) | gates {plan, research, idea-critique} |
| E10 | `frame_markers` | C | text | three frozen sets splitting the E9 gate: prior-art {prior art, what exists, alternatives, has anyone} → research; scope {roadmap, phases, milestones, scope} → plan; challenge {is this sound, poke holes, stress-test, challenge, critique} → idea-critique. Bare proposal frames ({what if, idea, approach}) with no decisive set stay **ambiguous → advisory band** (recoverable by design) | plan / research / critique split |
| E11 | `agent_mentions` | A | agent_mentions | explicit specialist named → near-dispositive pass-through (existing matcher behavior) | as named |

Notes:

- **Layer-noun counting** ({database, DNS, deploy, container, network, frontend, API} → count) is
  the Tier-C cousin of E7. It emits a **count**, never a domain — the guard against axis bleed:
  §9.2.1's scope predicate must stay on the posture/scope side even though the nouns are
  domain-flavored.
- **Test paths** (`tests/**`, `*.spec.*`) deliberately evidence **no posture** alone — "work
  involves tests" is domain-ish context. Only failure *output* (E2) evidences diagnose; paths
  feed the E7 area count and the domain axis.
- **E3 cross-fire is intended**: PR ref + E8 → operate; PR ref + review frame → assess; PR ref
  alone → weak assess. Extractors are not mutually exclusive — cells route, single features
  don't (§9.2.2).
- **Code-critique vs idea-critique**: E9-gated critique is idea-critique (approach-critic).
  Code-critique (inquisitor) = artifact present + challenge markers (C) or E11. Both variants
  are C-dependent absent an agent mention — flagged in §10.5.

### 10.3 Output contract and composition rules

Every extractor emits `{fired: bool|count, tier: A|B|C, evidence: [(posture, weight-class)]}`
and **abstains** otherwise — abstain ≠ veto (§9.2.4). Accumulation is additive per §2; no
extractor prunes the roster.

**Tier-C guardrails** (the §9.4 requirement made mechanical):

1. **Frozen closed sets.** Every Tier-C marker list is enumerated in this spec, versioned, and
   never extended at runtime. Stemming applies to markers only within the frozen set.
2. **No solo activation.** Tier C can never move a posture from abstain → fired by itself: its
   weight is capped below the posture-activation threshold. Its role is disambiguation among
   postures Tier A/B evidence already activated (e.g. the E10 split inside the E9 gate).
3. **Conditional firing.** Modifier extractors evaluate only in their host context: E6 only when
   E1/E2 fired; E5's relational markers only when the artifact pair is present; E10 only inside
   the E9 gate.
4. **Tier telemetry.** Each contribution is logged with its tier so the #293 corpus run can
   measure the **Tier-C decisiveness rate** — how often C flipped the winning cell. A high rate
   means the §8.1 correlation re-entered; treat as a failing result, not a tuning knob.

### 10.4 Decision — `build` is the unmarked default posture

"Write me X / add Y" requests carry **no reliable structural posture marker** — and build is the
highest-traffic posture (§9.1: code × build). Rather than invent a prose-verb extractor for build
(maximal §8.1 exposure), build is the **unmarked default**: when artifact-bearing evidence exists
(file_paths, domain signal) but no posture extractor fires, posture = build. Every other posture
must be *marked* by evidence.

Risk: extraction misses degrade silently into build. Mitigations: (a) the decision ladder still
gates — default-build contributes a posture read, not confidence, so thin evidence lands in
`advisory`/`ambiguous`, not confident `delegate`; (b) #293 tracks **false-default-build rate** as
a named error class.

### 10.5 Coverage matrix → flagged weak spots

| Posture | Strongest evidence | Coverage |
|---------|--------------------|----------|
| operate | E8 (A) | strong |
| diagnose | E1/E2 (B) + E7 split (A) | strong |
| build | default (§10.4) + E4 (A) | strong by construction |
| assess | E3 (B/A) | adequate |
| verify | E5 (B core, C assist) | **medium — flag** |
| plan | E9 gate (A) + E10 split (C) | **medium — flag** |
| research | E9 gate (A) + E10 split (C) | **medium — flag** |
| critique | E11 (A) else C only | **weak — flag** |

The four flags are where the §8.6 hand-spike concentrates: ≥3 adversarial prompts each for
verify, the plan/research gate-split, and critique-without-agent-mention; plus the §8.2 rename
example and proximity stress on E6 (the weakest load-bearing rule).

### 10.6 Step-1 exit → step-2 entry

Step 1 is done on paper when: (a) every §9.3 feature has an extractor row — ✓ (E1–E9 cover all
nine; E10/E11 support); (b) every Tier-C dependency is guarded by a §10.3 rule — ✓; (c) the
spike prompt set covering the §10.5 flags is drafted — **drafted below as §11**. Step 2 (§8.6)
then hand-routes the prompts through `domain × posture` using only these rules — no code until
the paper grid survives.

## 11. Hand-spike prompt set (step-2 input, drafted 2026-06-09)

Fourteen prompts. Gold labels assigned by the routing table
(`agents/general-purpose.md § Mandatory Code Routing`) per the §8.5 rubric requirement — rubric
citation inline on each. Designed-failure probes are marked ⚠ (the prompt is *expected* to miss;
the spike measures whether the miss lands in the recoverable `advisory` band or in confident
`delegate`). Execution convention: for each prompt, record fired extractors, resulting cell,
decision-ladder outcome, and hit/miss vs gold.

**Flag coverage:** verify P1–P3 · plan/research gate-split P4–P6 · critique P7–P9 ·
E6/`cause_stated` P10–P12 · controls P13–P14.

1. **P1 — verify happy path.** "Make sure `db/schema.sql` is consistent with the migrations in
   `db/migrations/`." `fields: file_paths=[db/schema.sql, db/migrations/]`. Expect: E5 core
   (pair) + relational marker → verify → **auditor**. Gold: auditor (conformance vs named source
   of truth, no failure observed).
2. **P2 — ⚠ E5 pair-strictness / E9 false-fire.** "Does the README still reflect how the build
   actually works?" `fields: none`. Expect: no path token (no extension), E5 core fails (≤1
   artifact), E9 fires (no artifacts) → gate, E10 no decisive set → ambiguous/advisory. Gold:
   auditor (conformance question; source of truth is live behavior). Probes: implicit-artifact
   prompts are invisible to E5's pair rule AND false-trigger E9.
3. **P3 — ⚠ prose-failure blind spot.** "The app crashes on startup and the config doesn't match
   what the docs say — figure out which is right." `fields: file_paths=[config/app.yaml,
   docs/config.md]`. Expect: E5 fires (pair + "match") → verify → auditor; E1/E2 silent (crash
   stated in prose, no machine output). Gold: investigator (failure observed, cause unknown,
   spans config+code). Probes: prose failure mentions are invisible to E1/E2, so verify
   outscores diagnose on a failure prompt.
4. **P4 — research happy path.** "I have an idea for caching dispatch results between sessions —
   has anyone built something like this?" `fields: none`. Expect: E9 + E10 prior-art → research
   → **researcher**. Gold: researcher (prior-art discovery, no failure, no source of truth).
5. **P5 — plan happy path.** "We should add result caching to the matcher. Lay out the phases
   and milestones to get there." `fields: none`. Expect: E9 + E10 scope → plan →
   **project-planner**. Gold: project-planner.
6. **P6 — ambiguous-by-design.** "What if we cached the catalog in memory instead of re-reading
   it each call?" `fields: none`. Expect: E9 fires; E10 proposal frame only, no decisive set →
   **advisory** (designed outcome). Gold: approach-critic — *disputed-label candidate*
   (researcher and project-planner arguable; rubric: novel-approach soundness implied). The
   dispute itself is §8.5 signal.
7. **P7 — idea-critique happy path.** "Poke holes in this approach before I build it: store gold
   labels in issue bodies instead of a file." `fields: none`. Expect: E9 + E10 challenge →
   idea-critique → **approach-critic**. Gold: approach-critic.
8. **P8 — ⚠ frozen-set synonym miss.** "Tear apart the error handling in
   `src/matcher/engine.py` — I think it's too clever." `fields:
   file_paths=[src/matcher/engine.py]`. Expect: artifact present → no E9; "tear apart" ∉
   challenge set → no critique mark → default build → code-writer. Gold: inquisitor (harsh
   critique of existing code). Probes: Tier-C closed-set brittleness; is the miss
   advisory-recoverable?
9. **P9 — ⚠ assess/critique boundary.** "Give PR #214 a really harsh review — don't go easy on
   it." `fields: none`. Expect: E3 (PR ref) → assess → code-reviewer; harshness structurally
   invisible. Gold: inquisitor (routing table: harsh review → inquisitor). Probes: the
   assess↔critique split has no structural witness — measure the ladder outcome.
10. **P10 — ⚠ the §8.2 worked example, prose variant.** "tests are failing after the rename,
    update them to match the new API." `fields: none`. Expect under §10 rules as written: E1/E2
    silent (prose), E6 never evaluates (conditional on E1/E2), no artifacts → **E9 false-fires**
    → gate → no decisive E10 set → advisory. Gold: code-writer (cause stated → build). Probes:
    see drafting finding F1 — §8.2 assumed this routes to build; under the drafted extractors it
    lands advisory instead (recoverable, but not the §8.2 story).
11. **P11 — E6 happy flip.** "Here's pytest: `FAILED tests/test_api.py::test_fetch -
    AttributeError: no attribute 'get_user'`. Started after we renamed get_user → fetch_user.
    Update the tests to match." `fields: none`. Expect: E2 fires → diagnose; E6 ("after" in
    proximity to failure) → flip → build → **code-writer**. Gold: code-writer (cause known).
12. **P12 — ⚠ E6 misattached connective.** "The deploy fails every time — logs show `Error:
    ECONNREFUSED api.internal:443`. We changed the DNS config last week because the old provider
    was slow. Figure out why it fails." `fields: none`. Expect: E1 fires (Error: shape) →
    diagnose; layer-noun count {deploy, DNS} ≥ 2 → investigator-side. E6 hazard: "because" is
    attached to the *change's motivation*, not the failure — a naive anywhere-match flips
    diagnose → build (wrong). Gold: investigator. Probes: E6's proximity rule is load-bearing.
13. **P13 — operate control.** "Run `gh pr checks 214` and summarize what's red."
    `fields: command_prefix=gh`. Expect: E8 → operate → **ops**. Gold: ops.
14. **P14 — diagnose × span control.** "Getting this in CI: `Traceback (most recent call last)
    ... ConnectionError` — happens only in the deploy workflow, never locally." `fields:
    file_paths=[src/api/client.py, .github/workflows/deploy.yml]`. Expect: E1 → diagnose; E7
    span=2 (code + infra areas) → **investigator**. Gold: investigator.

### 11.1 Drafting findings (pre-execution — discovered while assigning expected fires)

- **F1 — prose-failure blind spot is structural, not incidental** (P3, P10, P12-variant).
  E1/E2 require machine-emitted shapes by design, so "the app crashes" / "tests are failing"
  contribute zero diagnose evidence. Consequences: E6 can never evaluate on prose-only failure
  prompts (host condition unmet), and E9 false-fires when the prompt is artifact-free.
  **Candidate E12** `prose_failure_mention` — Tier C, frozen set {failing, fails, broken, red,
  errors out, crashes}, weak diagnose marker; would also extend E6's host condition and act as
  an E9 suppressor. **Deferred to spike execution**: adopt only if P3/P10 misses land outside
  the advisory band (per §10.3 guardrail 2, E12 alone could still never confidently delegate).
- **F2 — E9 needs an implicit-artifact story** (P2). Prompts referencing artifacts without
  path-shaped tokens ("the README", "the catalog") look artifact-free to E9. Same deferral: if
  the miss is advisory-recoverable, tolerate; if confident-wrong, E9's definition needs a
  named-artifact noun set (Tier C) as a suppressor.
- **F3 — §8.2's worked example routes differently than §8.2 claims.** Under the drafted rules
  the prose variant (P10) reaches code-writer only via the advisory band, not via cell-product
  build — the E6 flip requires pasted output (P11). §8.2's narrative stands corrected by P10/P11
  jointly; do not edit §8.2 retroactively — this section is the record.

## 12. Spike execution (step 2, 2026-06-09)

Paper execution of P1–P14 using **only the §10 rules as written** — uncharitable reading on
purpose; where a rule is ambiguous, both readings are run and the ambiguity is itself a finding.

**Conventions.** Domain axis is simulated as an assumed-correct coarse encoder read (the spike
tests posture extraction and composition, not the encoder; entropy noted where genuinely
diffuse). Numeric weights don't exist yet, so ladder outcomes are qualitative bands:
`confident` (single clean cell, would plausibly clear delegate thresholds) · `advisory`
(competing/thin evidence, router reviews) · `abstain` (nothing activates).

### 12.1 Results

| P | Fired (per §10 as written) | Cell → agent | Band | Gold | Verdict |
|---|----------------------------|--------------|------|------|---------|
| P1 | E5 core (2 paths) + relational "consistent with" | data × verify → auditor | confident | auditor | **HIT — but only under R1**; strict guardrail-2 reading blocks E5's own C assist from completing activation (→ F4) |
| P2 | E9 (no path token); E10 no decisive set | gate trio unresolved | advisory | auditor | miss, **recoverable** (as designed; confirms F2) |
| P3 | E5 core + "doesn't match"; E1/E2 silent on prose "crashes" | × verify → auditor | **confident** | investigator | **CONFIDENT-WRONG** — nothing contests verify; drives R2 (E12 + brake) |
| P4 | E9 + E10 prior-art ("has anyone") | any × research → researcher | confident | researcher | HIT |
| P5 | E9 + E10 scope ("phases", "milestones") | meta × plan → project-planner | confident | project-planner | HIT |
| P6 | E9; E10 bare proposal only | trio jointly active | advisory | approach-critic (disputed) | **HIT-by-design** — advisory was the intended outcome |
| P7 | E9 + E10 challenge ("poke holes") | any × critique → approach-critic | confident | approach-critic | HIT |
| P8 | no posture fires ("tear apart" ∉ set); default build (§10.4) | code × build → code-writer | advisory (per §10.4 mitigation a) | inquisitor | miss, **recoverable** — default-build correctly lands advisory; frozen set held, no synonym creep |
| P9 | E3 (PR #214) → assess; harshness invisible | code × assess → code-reviewer | confident | inquisitor | **confident-wrong, accepted low-harm** — adjacent cell (assess↔critique), same artifact, work-product class correct (→ R4) |
| P10 | E1/E2/E6 silent; E9 fires; E10 silent | trio unresolved | advisory | code-writer | miss, recoverable — but candidate list actively misleading (trio excludes build); improves to honest `abstain` under R2's E9-suppressor wiring |
| P11 | E2 (`FAILED …::…`) → diagnose; E6 "after" in-clause → flip | code × build → code-writer | confident | code-writer | HIT — the E6 happy flip works |
| P12 | E1 (`Error: ECONNREFUSED`) → diagnose; layer-count {deploy, DNS} = 2 → investigator side | infra × diagnose × span≥2 → investigator | confident | investigator | **HIT — but only under R3**: token-window proximity wrongly fires E6 on the misattached "because" (→ flip → confident-wrong); clause-scoped proximity stays silent |
| P13 | E8 (`gh`) → operate | VCS-meta × operate → ops | confident | ops | HIT |
| P14 | E1 (Traceback) → diagnose; E7 span=2 (src/** + .github/**) | × diagnose × span≥2 → investigator | confident | investigator | HIT |

**Tally:** 9 hits (P1, P12 conditional on R1/R3; P6 by-design) · 3 recoverable misses (P2, P8,
P10) · 2 confident-wrong (P3 → fixed by R2; P9 → accepted low-harm). After refinements:
**zero unaccepted confident-wrongs.**

### 12.2 Execution findings

- **F4 — guardrail 2 as written deadlocks E5** (P1). E5's B core alone (≥2 artifact refs)
  over-fires on any multi-file prompt ("refactor a.py and b.py"); gating activation on the C
  relational marker violates "no solo activation" read strictly. The E10-within-gate example in
  §10.3 already blesses the needed pattern — it just wasn't stated generally. → R1.
- **F5 — Tier C's safe roles are *select* and *brake*, never *add*** (P3). A C signal that can
  only (a) choose within an A/B-activated candidate set or (b) drag a confident outcome down to
  advisory can **never** push toward confident delegation — the §8.1 failure mode (correlated
  inflation → overconfident delegate) becomes structurally impossible for Tier C, not just
  guarded. This upgrades guardrail 2 from a weight cap to a direction constraint. → R1/R2.
- **F6 — E6 proximity must be clause-scoped** (P12). The causal connective must share a
  clause/sentence (deterministic punctuation segmentation) with a failure reference; a token
  window misattaches rationale-"because" to the failure. → R3.

### 12.3 Adopted refinements (authoritative layer over §10 — §10 text intentionally not retro-edited)

- **R1 — guardrail 2 restated as a direction constraint.** Tier C never *adds* a candidate
  posture. It may: (a) **select** within a candidate set already activated by A/B evidence
  (E10 inside the E9 gate; E5's relational marker completing its own B core; layer-count
  splitting E1/E2-activated diagnose), or (b) **brake** — contest a confident outcome down to
  advisory. Both motions point toward router review; neither can produce confident delegation.
- **R2 — E12 adopted** (`prose_failure_mention`, Tier C, frozen set {failing, fails, broken,
  red, errors out, crashes}), with exactly two wired effects: brake non-diagnose confident
  outcomes (P3: verify → advisory), and suppress E9 as a gate-precondition input (P10: trio →
  honest abstain). E12 **never activates diagnose**. Suppression is safe-direction: it can only
  remove activations, never add them.
- **R3 — clause-scoped causal proximity** for E6 (and E12's failure references): connective and
  failure mention must share a punctuation-delimited clause. Deterministic; no token windows.
- **R4 — cell-distance as miss severity, adopted into #293.** Adjacent-posture misses
  (assess↔critique, P9) are low-harm — work-product class survives; cross-posture and
  cross-domain misses are the expensive class. #293 measures error *severity distribution*, not
  just hit rate; no harshness marker set until corpus frequency justifies one.

### 12.4 Verdict and step-3 entry

The architecture survived its own adversarial prompt set: every falsification was rule-level
(one guardrail statement, one proximity rule, one missing extractor), none was
architecture-level (the additive principle, the two-axis split, and the cells-route invariant
held on all 14). Residual open items → #293 scoping (step 3): error-severity distribution (R4),
Tier-C decisiveness rate (§10.3 guardrail 4), false-default-build rate (§10.4), candidate-list
quality on braked outcomes (P3 residual: advisory recovers the *decision*, but the alternatives
list may not contain the gold agent), and the §8.5 gold-label rubric requirement.

## 13. Step 3 — corpus-measurement scope (2026-06-09)

### 13.1 Reality check on the "#293" reference

#293 **closed 2026-06-04 (completed)** under Milestone 12 — it delivered the dispatch-log
fixture-filter script and an organic-only failure taxonomy for the *current lexical* matcher
(motivated by the fixture-contaminated #288 spike data: 24,339 `matcher_decision` entries, 100%
empty `session_id`, ~939 unique prompts). All §8/§9/§12 references to "scope #293" resolve to:
**file a successor issue** that reuses #293's substrate — the post-v1.1.0 session-attributed
organic window and the filter script — for the two-axis design's measurement. (Frozen-reference
lesson, same shape as §8.5.)

### 13.2 Measurement design

- **Substrate**: organic-only, session-attributed log window (post v1.1.0 fix of the session_id
  bug); reuse the #293 filter script. **Pre-analysis per-field population profiling is
  mandatory** — the #288 spike drew conclusions from a log whose `session_id` was 100% empty.
- **Corpus**: stratified by decision band and (post-extraction) by posture; oversample rare
  postures (verify, critique, research); per-cell floor (~30) before any per-cell conclusion.
- **Gold labels**: routing-table-as-rubric (§8.5); record `(gold agent, gold cell)`; disputed
  labels are first-class output (each is a routing-table ambiguity finding); double-label a
  subsample to measure rubric reliability.
- **Systems run over the same corpus**: (a) current lexical matcher — baseline; (b) domain
  encoder alone; (c) posture extractors E1–E12 + R1–R3 alone; (d) composed `domain × posture`
  cells.

### 13.3 Metrics (six, all previously derived)

1. **Error correlation** (§8.4, the decisive one): P(domain wrong ∧ posture wrong, same
   direction) vs the independence product, conditioned on gold.
2. **Error-severity distribution** by cell distance (R4): adjacent-posture vs cross-posture vs
   cross-domain.
3. **Tier-C decisiveness rate** (§10.3 g4): how often a C select/brake changed the final
   cell/band. Above threshold = §8.1 re-entry = failing result.
4. **False-default-build rate** (§10.4).
5. **Braked-outcome candidate quality** (P3 residual): gold ∈ alternatives when
   band = advisory-via-brake.
6. **Confident-wrong rate vs baseline**: the new system's delegate-band misses must be ≤ the
   lexical baseline's — the original overconfidence problem, measured.

### 13.4 Kill criteria (written before measurement; evaluation-first, no hot-path integration)

- Error correlation indistinguishable from a single signal → the architecture premise fails →
  stop the line (shadow-mode discipline: kill criteria are written down before the experiment).
- Tier-C decisiveness above threshold → extractor redesign before any integration.
- Confident-wrong rate not improved vs baseline → no-go regardless of aggregate hit rate.

### 13.5 Follow-on issue set (to file on approval — design loop ends here)

1. **Extractor library** — E1–E12 + R1–R3 as an offline module (no hot-path changes).
2. **Encoder spike** — potion-base-8M, 5-way domain + entropy, offline, deterministic.
3. **Successor measurement issue** — this §13 scope (substrate, labels, metrics, kill criteria).

Milestone open question: extend Milestone 12 (lexical trigger improvements) vs a new "Matcher
v3 — semantic two-axis" milestone. The work is a distinct initiative; a new milestone is the
default recommendation.
