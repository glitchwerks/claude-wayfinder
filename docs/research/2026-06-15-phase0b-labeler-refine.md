---
title: Phase 0b — Labeler Refinement (R1 + R2) Run and Measurement
date: 2026-06-15
tracking: glitchwerks/claude-wayfinder#386
parent: glitchwerks/claude-wayfinder#382
status: BLOCKED — codex account rate-limited until 2026-06-18
---

# Phase 0b — Labeler Refinement: R1 (operate broadening) + R2 (is_any domain)

**Purpose.** Refine the Phase 0 GPT labeler prompt to close two diagnosed failure
gaps (from the failure-decomposition report), re-run on the full corpus, and
measure against the pre-registered acceptance bar.

---

## 1. Pre-Registration (Locked — issue #386)

- **Methodology:** in-sample upper bound — single labeler run on the full corpus,
  no shuffle. Treat RC/CW as a ceiling.
- **Bar (carried from Phase 0, unchanged):** RC ≥ 0.60 AND CW ≤ 0.2558
  (no_smoke cut). Both conditions must hold.

---

## 2. R1/R2 Changes

### R1 — Broadened `operate` Posture Rule

**Problem (Phase 0 decomposition):** Gold `operate` = 36 entries. 16 of these
GPT mislabeled as other postures (`operate→assess`: 10 cases, `operate→build`:
3, `operate→research`: 2, `operate→verify`: 1). The 10 `operate→assess`
collapses were the single dominant confusion pair. Root cause: the original
posture rule 1 fired `operate` only on non-null `command_prefix` OR a literal
git/gh command shape. Gold `operate` entries that were natural-language GitHub
state queries (no command prefix, no command shape) fell through to `assess`
(rule 3, which fires on any "PR #N" reference) or other postures.

**Change made:** Rule 1 now broadens `operate` to ALSO fire on natural-language
GitHub/VCS **state operations** — listing, reading, querying, or checking GitHub
issues, PRs, CI status, commits, repo metadata, merge state, or milestone/label
state — even with no command shape and no file paths.

**Operate/assess boundary decision (based on gold corpus analysis):**
- Gold `assess` entries: only 2 in the corpus.
  - cid=33692: "Read and review the change-request feedback on GitHub PR #11723
    ... evaluate" — tool_mentions includes `get_pull_request*`; explicit review
    intent of PR content.
  - cid=33715: "Read-only inspection and review of the Rust test files added in
    a PR ... group them by purpose" — explicit review/inspection of PR diff
    content for quality.
- Gold `operate` entries (36 total): GitHub state queries (list issues, check
  PR status, CI check rollup, fetch issue content), git merge conflict
  resolution, creating GitHub issues/milestones, reading repos/PRs without
  review intent, `claude -p` CLI commands.

**Boundary placed as:** `assess` = explicit review/critique intent on PR diff
content or change-request feedback (PR review intent, get_pull_request* tools
AND task asks to evaluate/review). `operate` = all other GitHub/VCS state
operations: reading, listing, querying, checking status, creating/writing
issues, CI checks, PR state checks — even when PR is referenced, if there is
no review/critique intent.

**Side-effect risk for assess:** The only 2 gold `assess` entries have strong
review-intent signals ("review the change-request feedback", "review of the
Rust test files"). Broadened `operate` rule explicitly excludes "review/critique
intent" via the CRITICAL BOUNDARY clause. Low risk of cannibalization.

### R2 — Add `is_any` to Domain Vocabulary

**Problem (Phase 0 decomposition):** 16 gold entries have `domain=None`
(gold rubric's `is_any=True` entries — conversational/no-evidence tasks). The
original labeler `ALLOWED_DOMAINS` omitted `is_any`, forcing GPT to assign a
concrete domain. Result: all 16 systematically disagreed with gold (domain
mismatch inflating C counts artificially). The largest single confusion bucket
was `None→project_meta`: 14 entries where GPT defaulted to `project_meta`
when no other domain was clear.

**Change made:**
1. Added `"is_any"` to `ALLOWED_DOMAINS` frozenset (line 35).
2. Added `is_any` to the `ALLOWED VALUES` line in the prompt (domain vocab).
3. Added a DOMAIN RULE for `is_any`: "conversational tasks, simple lookups or
   questions, explanations with NO domain-bearing file paths or artifacts."
4. Added SPECIAL RULE: "Conversational/no-evidence tasks with no domain signal
   → domain: is_any."
5. Updated OUTPUT FORMAT comment from "5 values" to "6 values."

**Blocking pre-check — `is_any` compose/cell-map compatibility:**
Verified BEFORE running the labeler that `run_supplied_compose` handles
`domain="is_any"` correctly:

- `gate_agents(scored_agents, "is_any")`: `"is_any" not in DOMAIN_AGENT_MAP`
  → bypasses gating entirely → returns full scored list. Verified.
- `cell_map_lookup("is_any", posture)`: `("is_any", posture)` not in
  `_CELL_MAP` → falls back to `("any", posture)` → routes correctly. Verified.
- `DOMAIN_AGENT_MAP.get("is_any")` → `None` → `genuine_gated_names =
  gated_names` → no domain filter applied in `run_supplied_compose`. Verified.
- `domain_for_lookup = "is_any"` (truthy) → passed to `cell_map_lookup` →
  falls back to `"any"` posture lookup correctly. Verified.

**Finding: NO compose fix needed.** The existing compose/cell-map logic handles
`domain="is_any"` correctly — it imposes no domain hard-gate and falls through
to the posture-based `decide()` path. This was verified via 6 targeted checks
before burning labeler tokens.

---

## 3. Labeler Run — BLOCKED

**Status: BLOCKED — OpenAI Codex account rate-limited.**

The `codex exec --sandbox read-only -` calls consistently returned:
```
ERROR: You've hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), visit https://chatgpt.com/codex/settings/usage to purchase more credits or try again at Jun 18th, 2026 8:01 AM.
```

All 7 batch calls failed identically. This is an account-level rate limit
(not a per-request error), confirmed by the "retry at Jun 18th" date. The
labeler script itself is functional — the prompt template edits (R1/R2) are
verified to load correctly from the worktree.

**What was confirmed before rate limit hit:**
- Module `scripts.corpus.phase0_gpt_labeler` resolves from worktree (not
  parent checkout) — verified by `__file__` path check.
- `ALLOWED_DOMAINS` now includes `"is_any"`.
- Compose pre-checks all pass (6 assertions, all PASS).
- Ruff + pytest post-edit: ruff clean; pytest 1253 passed / 8 skipped (same
  as baseline, no regression).

**Labels file not produced:** `docs/research/2026-06-15-phase0b-gpt-labels.jsonl`
does not exist (zero rows written before rate limit hit).

---

## 4. RC/CW vs Bar — DEFERRED (no labels produced)

RC and CW cannot be computed without the labeler output. This section will be
completed when the labeler is re-run after the rate limit resets (June 18th).

| Cut       | RC     | Bar (RC ≥ 0.60) | CW     | Bar (CW ≤ 0.2558) | VERDICT |
|-----------|--------|------------------|--------|---------------------|---------|
| full      | —      | —                | —      | —                   | PENDING |
| no\_smoke | —      | —                | —      | —                   | PENDING |

**Phase 0 baseline (for reference when results come in):**
- Phase 0 full: RC=0.7083, CW=0.2699 (CW missed bar by +0.0141)
- Phase 0 no_smoke: RC=0.5505, CW=0.4231 (RC missed bar by −0.0495)

---

## 5. Gap-Closure Analysis — DEFERRED (no labels produced)

The targeted gaps were:
- `operate→assess` collapse: 10 cases (26% of C-type RC misses). R1 targets
  these. Expected: significant reduction toward 0, possibly with some
  `assess→operate` side-effects (none expected given the 2-entry gold assess
  corpus and the explicit boundary clause).
- `is_any` mismatches: 16 of 35 domain mismatches (all 16 `is_any` gold
  entries). R2 targets all 16. Expected: near-complete elimination.
- All 16 `operate` gold mismatches were in the natural-language shape. R1
  targets all 16. Expected: most to shift to correct `operate` label.

These deltas will be computed when the run completes.

---

## 6. Verification

### Ruff

Command (mirrors CI): `python -m ruff check src/ tests/`
from `I:/ai/claude/claude-wayfinder/.worktrees/386-phase0b`.

Baseline (pre-edit): `All checks passed!`
Post-edit: `All checks passed!`

### Pytest

Command (mirrors CI): `pytest tests/ --ignore=tests/integration -q --tb=no`
from `I:/ai/claude/claude-wayfinder/.worktrees/386-phase0b`.

Baseline (pre-edit): 1253 passed, 8 skipped, 3 warnings in 88.88s
Post-edit: 1253 passed, 8 skipped, 3 warnings in 86.52s

No regression. All 1253 tests passing; same 8 skips; same 3 warnings.
The only changed file is `scripts/corpus/phase0_gpt_labeler.py` (prompt
template + ALLOWED_DOMAINS) — it has no test coverage in the unit suite,
so the pass/skip counts are identical to baseline as expected.

---

## 7. Caveats

1. **In-sample upper bound:** even when run completes, these labels come from
   the same 168-entry corpus used during rubric development. The numbers are
   a ceiling, not a true generalization estimate.
2. **Rate limit:** the labeler run must be re-attempted after June 18, 2026
   08:01 AM (UTC per error message). The prompt edits are committed and ready.
3. **R1 boundary calibration:** the operate/assess boundary was set based on
   2 gold `assess` entries. With such a small `assess` gold set, any side-effect
   of R1 (genuine assess entry captured as operate) would be visible in the
   decomposition probe but would not materially change the RC numbers (there
   are only 2 gold assess entries to lose).

---

## 8. Re-Run Instructions (when rate limit resets)

```bash
# From the worktree root, using the cwd-forcing recipe:
"I:/ai/claude/claude-wayfinder/.worktrees/386-phase0b/.venv/Scripts/python.exe" \
  -c "import os,subprocess,sys; \
      os.chdir(r'I:/ai/claude/claude-wayfinder/.worktrees/386-phase0b'); \
      sys.exit(subprocess.call([sys.executable, '-m', \
        'scripts.corpus.phase0_gpt_labeler', \
        '--corpus', str(__import__('pathlib').Path.home() / \
          '.claude/state/wayfinder-corpus/2026-06-12/wayfinder-corpus.jsonl'), \
        '--output', \
          'docs/research/2026-06-15-phase0b-gpt-labels.jsonl']))"

# Verify line count == 168
# Then score:
python -m scripts.corpus.eval \
  --corpus ~/.claude/state/wayfinder-corpus/2026-06-12/wayfinder-corpus.jsonl \
  --labels docs/research/2026-06-12-gold-labels-redacted.jsonl \
  --catalog ~/.claude/state/dispatch-catalog.json \
  --systems compose \
  --compose-labels docs/research/2026-06-15-phase0b-gpt-labels.jsonl \
  --cut full

# Repeat with --cut no_smoke

# Then re-run the decomposition probe:
python -m scripts.corpus.phase0_failure_decomposition
# (Update _GPT_RUN1_PATH in the script to point to phase0b labels)
```
