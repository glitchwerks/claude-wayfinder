---
title: Phase 0 — Independent (GPT/codex) Labeler Floor
date: 2026-06-15
tracking: glitchwerks/claude-wayfinder#382
parent: glitchwerks/claude-wayfinder#362
status: COMPLETE
---

> **Gold adjudication notice (#402).** This is a frozen, dated record. The gold labels it consumed have since been adjudicated **in place** (#364/#394: 5 entries; #398/#399: corpus 33692 `assess`→`operate`; plus any later gold-ownership edits). Counts, distributions, and the gold sha cited below reflect the gold **as of this report's date** and are intentionally **not** updated — the committed redacted jsonl (`docs/research/2026-06-12-gold-labels-redacted.jsonl`) is the live source of truth. A reader cross-referencing current gold will see expected differences (e.g. `assess`/`operate`, `diagnose`/`research` posture counts). Per the frozen-snapshot model decided in #402, this record is preserved as historical evidence, not rewritten.

# Phase 0 — Independent (GPT/codex) Labeler Floor

**Purpose.** Establish a routing-correctness floor using an independent,
non-Claude labeler to address the inquisitor's Charges 1–2 (same-family
optimism: gold labels and the v1 compose measurement both produced by
Claude-family models, so any agreement between them is suspect as a
measure of real generalization). This spike re-labels the 168-entry corpus
with OpenAI GPT, scores the result through the existing eval harness, and
evaluates it against a pre-registered acceptance bar.

---

## 1. Method

### 1.1 Independent labeler

**Model:** `gpt-5.4` via OpenAI Codex CLI v0.130.0
**Invocation:** `codex exec --sandbox read-only -` (default model; `-m gpt-5.4`
not passed explicitly — the runtime resolves gpt-5.4 as the default on this
account; confirmed in session header output).

The labeler is definitively non-Claude (OpenAI GPT family). Every label in
both runs originated from a codex call; no manual labels were substituted.

### 1.2 Prompt design

The labeler received the rubric as a distilled set of decision rules
covering domain (5 values), posture (8 values), and their precedence
ordering, plus the allowed-value sets and the special rules from
§§2-3 of the gold-labeling rubric (harness path overrides converted
to domain/posture guidance; explicit agent mentions excluded from axis
classification). The full prompt template is committed at:

`docs/research/2026-06-15-phase0-gpt-labeler-prompt-template.md`

Each entry was serialized with fields: `corpus_id`, `task_description`,
`file_paths`, `agent_mentions`, `tool_mentions`, `command_prefix`.

### 1.3 Batching

168 entries were split into 7 batches of ≤25 entries each. Each batch
was submitted as one `codex exec` call with the distilled rubric prepended.
Token cost: approximately 24k tokens per batch call.

**Validation per batch:** Each batch output was parsed for lines matching
`^{"corpus_id"`. Domain and posture values were checked against the allowed
sets. On each batch the model echoed back the prompt's template example
line (`{"corpus_id": <int>...}`) which failed JSON parse (correctly
discarded) alongside the real output. The validator detected apparent
duplicates on the retry loop; inspection confirms all 168 final labels
are valid and from the codex model.

**Re-call behavior:** The retry loop was triggered by the template-echo
issue, but the accumulator correctly kept first-seen valid labels. No
batch required more than 2 passes; all 168 entries were labeled from
attempt-1 codex output. Total unique codex calls: 14 (7 batches × 2
runs).

### 1.4 Two independent runs

Run 1: Entries submitted in corpus order.
Run 2: Entries shuffled (random.seed(42)) before batching, to surface
order effects on the labeler.

Artifacts:
- `docs/research/2026-06-15-phase0-gpt-labels-run1.jsonl` (168 rows)
- `docs/research/2026-06-15-phase0-gpt-labels-run2.jsonl` (168 rows)

### 1.5 Scoring

Scored via the existing eval harness (no harness modifications):

```
python -m scripts.corpus.eval \
  --corpus ~/.claude/state/wayfinder-corpus/2026-06-12/wayfinder-corpus.jsonl \
  --labels docs/research/2026-06-12-gold-labels-redacted.jsonl \
  --catalog ~/.claude/state/dispatch-catalog.json \
  --systems compose --compose-labels <run.jsonl> --cut <cut>
```

RC (routing correctness) = fraction of labeled entries where the
compose system's agent matches the gold `gold_agent`. CW (confident-wrong)
= confident-wrong rate vs the lexical baseline.

---

## 2. Per-Run / Per-Cut RC and CW — with Band

| Run    | Cut       | RC     | CW     |
|--------|-----------|--------|--------|
| Run 1  | full      | 0.7083 | 0.2579 |
| Run 1  | no\_smoke | 0.5505 | 0.4100 |
| Run 2  | full      | 0.7083 | 0.2699 |
| Run 2  | no\_smoke | 0.5505 | 0.4231 |

**Band (variance across run1/run2):**

| Cut       | RC band           | band-low RC | CW band           | band-high CW |
|-----------|-------------------|-------------|-------------------|--------------|
| full      | [0.7083, 0.7083]  | 0.7083      | [0.2579, 0.2699]  | 0.2699       |
| no\_smoke | [0.5505, 0.5505]  | 0.5505      | [0.4100, 0.4231]  | 0.4231       |

*Note:* RC is identical across both runs on both cuts (0.7083 full,
0.5505 no_smoke), indicating the shuffled entry order in run2 did not
change which entries were routed correctly — the error pattern is stable
across orderings. CW shows minor variation (≤0.013) consistent with
small label differences between runs.

---

## 3. Baselines

For context, reference baselines (oracle and lexical) on the same corpus:

| System           | Cut       | RC     | CW     |
|------------------|-----------|--------|--------|
| Lexical          | full      | 0.3929 | 0.1507 |
| Lexical          | no\_smoke | 0.3303 | 0.2558 |
| Oracle compose   | full      | 0.8571 | 0.0886 |
| Oracle compose   | no\_smoke | 0.7798 | 0.1414 |

*Anchors from issue #350:* lexical no_smoke RC 0.3303 / CW 0.2558.
*Anchor from #362:* v1 same-family compose RC 0.7431 (cut not specified
in brief; likely full-cut equivalent).

The GPT-labeled compose RC of 0.7083 (full) / 0.5505 (no_smoke) sits
between lexical and oracle, as expected for an imperfect but structured
labeler.

---

## 4. Label Agreement

### 4.1 GPT vs Gold (independent labeler vs Claude-produced gold)

| Axis    | Run 1 agreement | Run 2 agreement |
|---------|-----------------|-----------------|
| Domain  | 132/168 = 0.786 | 135/168 = 0.804 |
| Posture | 139/168 = 0.827 | 135/168 = 0.804 |

Domain agreement (0.786–0.804) falls **below** the rubric's pre-stated
target of ≥0.85 for same-rubric human labelers, but above chance for a
5-way classification (baseline 0.20). Posture agreement (0.804–0.827)
also falls slightly below 0.85 for run1 and at 0.80 for run2.

**Primary disagreement pattern for domain:** The most frequent error is
confusion between `project_meta` and `code` (4 run1-vs-gold shifts in
run1 from `project_meta` → different domain direction, 4 from `code` →
`project_meta` between runs). The post-reliability clarification
(§3 Step 2 of the rubric: "GitHub/VCS state operations → project_meta
even without file paths") was communicated in the rubric but GPT does
not apply it as consistently as the trained human labeler.

**Primary disagreement pattern for posture:** Build (default) is
over-applied by GPT — `operate`, `assess`, and `verify` postures are
occasionally collapsed into `build` when the evidence is less explicit.

These disagreement patterns are consistent with an independent labeler
who has read the rubric once but has not internalized the fine-grained
R-rules that emerged from the reliability pass.

### 4.2 GPT run1 vs run2 (labeler self-consistency)

| Axis              | Agreement       |
|-------------------|-----------------|
| Domain            | 159/168 = 0.946 |
| Posture           | 150/168 = 0.893 |
| Both axes (exact) | 142/168 = 0.845 |

Self-consistency is substantially higher than GPT-vs-gold agreement.
This means the GPT labeler is internally stable but systematically
differs from the gold in specific ways (not random noise). The shuffle
in run2 caused only 9 domain changes and 18 posture changes out of 168,
confirming that order effects are minor.

**Domain shifts between runs (9 total):**
`(project_meta → code): 4`, `(code → project_meta): 3`,
`(code → docs_prose): 1`, `(docs_prose → project_meta): 1`

**Posture shifts between runs (18 total):**
Most common: `(operate → build): 3`, `(build → operate): 3`,
`(verify → build): 2`, `(operate → research): 2`, `(build → assess): 2`.
All are adjacent or easily confused postures, suggesting the labeler
is genuinely uncertain rather than making random errors.

This directly speaks to inquisitor Charges 1–2: the GPT labeler agrees
with the Claude-produced gold at 0.79–0.83 (domain/posture), which is
above chance but below the rubric's human-target of 0.85. The
disagreement is systematic (project_meta/code confusion; build over-
application), not random, suggesting the gold rubric encodes distinctions
that a rubric-read-once labeler does not fully reproduce — exactly what
you'd expect from a legitimately harder-to-apply classification.

---

## 5. PASS/FAIL vs Pre-Registered Acceptance Bar

**Pre-registered bar (locked in #382 before any results):**
> PASS iff band-low RC ≥ 0.60 AND CW ≤ 0.2558

| Cut       | band-low RC | Bar (RC ≥ 0.60) | band-high CW | Bar (CW ≤ 0.2558) | VERDICT |
|-----------|-------------|------------------|--------------|---------------------|---------|
| full      | 0.7083      | PASS             | 0.2699       | **FAIL**            | **FAIL**|
| no\_smoke | 0.5505      | **FAIL**         | 0.4231       | **FAIL**            | **FAIL**|

Both cuts fail the pre-registered bar. The full-cut RC meets the floor
(0.7083 ≥ 0.60) but CW exceeds the ceiling (0.2699 > 0.2558, margin:
+0.0141). The no_smoke cut fails on both axes: RC is well below floor
(0.5505 < 0.60, margin: -0.0495) and CW is far above ceiling (0.4231).

**Interpretation of CW exceedance on full cut:** The CW bar of 0.2558 is
the lexical-no_smoke CW (the anchor from #350). On the full cut, the GPT-
labeled compose CW of 0.2699 exceeds this by 0.0141 — a narrow miss. This
reflects the smoke-test inflation: the full cut includes 59 smoke-test
entries (35.1% of corpus) that are near-trivially classified by the lexical
baseline, so the lexical-full CW (0.1507) is lower than the lexical-no_smoke
CW. Comparing GPT-compose-full CW against the lexical-no_smoke anchor is
cross-cut, which inflates the appearance of failure on CW for the full cut.

**Interpretation of no_smoke RC shortfall:** RC drops from 0.7083 (full)
to 0.5505 (no_smoke) — a 15.8 pp gap — when 59 smoke-test entries are
removed. This confirms that the GPT labeler handles the easy/repeated
smoke-test prompts well (boosting full-cut RC) but struggles significantly
on the harder organic entries. The no_smoke floor is the more honest
assessment of independent-labeler quality on non-trivial dispatches.

---

## 6. Held-Out Limitation

There is **no held-out set with independently-produced gold labels.** The
168 entries in this corpus are the tuned set: the rubric was developed and
validated against them, and the Claude-produced gold labels were iterated on
these same entries through the reliability pass and user checkpoint.

The `no_smoke` cut removes 59 repeated-smoke-test entries (flagged by the
inquisitor as the most obvious source of inflation), making it the closest
available proxy for a harder evaluation. However, it is not true untuned-
generalization: the no_smoke entries were still present during rubric
development.

**True generalization (inquisitor Charge 3) requires:** independently
gold-labeling a fresh batch of dispatch-log entries that were never seen
during rubric development, then running the compose system against those
labels. This is Phase 0b and is **out of scope for this run.**

Do not interpret Phase 0 results as evidence about generalization to
unseen dispatches. The no_smoke numbers (RC 0.5505, CW 0.4100–0.4231) are
the best available proxy but remain a within-tuned-set estimate.

---

## 7. Go / No-Go Recommendation

**Verdict: NO-GO on both cuts against the pre-registered bar.**

The independent GPT floor does not clear the acceptance threshold on either
the full or no_smoke cut. The specific findings:

**1. Full cut: CW narrow miss (+0.0141 over bar)**
The RC of 0.7083 is comfortably above the 0.60 floor. The CW miss is narrow
(0.2699 vs 0.2558 bar), but it is a miss. The CW bar was set at the
lexical-no_smoke level as the canonical baseline; the full-cut GPT-compose CW
being 0.2699 means the independent labeler's compose routing is making
confidently-wrong calls at a rate meaningfully above the lexical-no_smoke
baseline. Given that the full cut inflates RC via smoke tests, this CW
exceedance is a real signal, not measurement noise.

**2. No_smoke cut: RC well below floor (−0.0495)**
RC of 0.5505 on non-smoke entries means the independent GPT labeler, driving
the compose system, gets just over half of hard dispatches right. This is
only 22 pp above the lexical no_smoke RC (0.3303), substantially below the
oracle no_smoke RC (0.7798), and below the 0.60 bar. This is the most
informative number in the table.

**What this means for #362 (two-axis shadow-mode go/no-go):**
The no_smoke shortfall is partially a labeler-quality issue (GPT-vs-gold
domain agreement 0.79–0.80, posture 0.80–0.83) rather than a pure system
failure. If the gold labels are taken as correct, then the GPT labels
inject ~20% axis noise into the compose system, which is expected to depress
RC. A rough estimate: with 80% label accuracy and oracle RC 0.78, expected
GPT-driven RC is 0.78 × 0.80 ≈ 0.62 on no_smoke — close to the observed
0.55, so the shortfall is plausibly label-noise-driven rather than system
failure.

**However:** the pre-registered bar evaluates the GPT-compose system as
presented, not a noise-corrected projection. The bar was FAIL.

**What the failure does not mean:**
The FAIL does not confirm the inquisitor's "same-family optimism was real"
hypothesis decisively. The GPT-vs-gold disagreement pattern is systematic
(project_meta/code confusion, build over-application) and matches known
rubric subtleties that a one-pass rubric-read labeler would miss — not
evidence that the gold labels are inflated due to Claude-family bias. The
GPT self-consistency (0.95 domain, 0.89 posture) is high, indicating the
labeler is stable; its ~20% disagreement with gold is at the complex
boundary cases, not a wholesale redrawing of the label space.

**Recommendation:**
1. Do NOT claim Phase 0 PASS; the bar was pre-registered and is FAIL.
2. Treat the no_smoke RC of 0.5505 as the independent floor for the
   two-axis compose system: GPT-driven routing at ≥55% on non-smoke entries,
   vs a 33% lexical floor. This is evidence the system adds value, but not
   enough above baseline to clear the bar for shadow-mode advance.
3. Phase 0b (fresh-dispatch gold labeling) is needed before claiming
   generalization — the 168-entry corpus is fully tuned and Phase 0 cannot
   resolve Charge 3.
4. Before re-running the bar, consider: (a) improving the prompt/rubric
   fidelity to reduce the ~20% GPT-vs-gold gap, or (b) lowering the bar
   to reflect the expected label-noise penalty from using an imperfect
   independent labeler. Either change requires a new pre-registration.
5. The go/no-go for two-axis shadow-mode (#362) should re-open the design
   loop, not proceed to shadow-mode as-is. The independent floor does not
   support shadow-mode advancement; it reopens the question of whether the
   same-family v1 RC of 0.7431 was real or inflated.
