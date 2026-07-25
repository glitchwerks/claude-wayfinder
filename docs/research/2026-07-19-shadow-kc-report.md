# Shadow KC Report — M15-6c Go/No-Go Verdict

**Issue:** #485 ("M15-6c: Run the KC report on frozen gold + go/no-go verdict"), part of Milestone
**M15 — Matcher v3 live (two-axis routing shipped)**.

This report is the deliverable issue #485 asks for: the frozen-gold KC-1..KC-5 verdicts, the
traffic-mix and caller-label diagnostic cuts, and the overall go/no-go recommendation that feeds
the M15-7 hard-routing flip decision.

## Overall recommendation

> **NO-GO** — failed criteria: KC-1, KC-5. KC-4: **INSUFFICIENT_DATA** (was FAIL in the original
> 2026-07-24 run; see [Addendum 2026-07-25](#addendum-2026-07-25-post-506-re-run) for the
> post-#503/#506 re-run that changed this).

See [What this means for M15-7](#what-this-means-for-m15-7) for the closing interpretation.

## Methodology

**Inputs:**

- Corpus: `~/.claude/state/wayfinder-corpus/2026-06-12/wayfinder-corpus.jsonl` (245 rows)
- Gold labels: `docs/research/2026-07-19-shadow-sample-gold-labels-redacted.jsonl`
- Dispatch catalog: `~/.claude/state/dispatch-catalog.json`
- Repo HEAD: `d0230cd` — the commit that merged PR #502 ("fix(#499): narrow shadow-kc-report
  provenance guard to per-row `compose_route` agreement"), which closed issue #499. #499 was
  issue #485's step-1 precondition (see [Provenance partition](#provenance-partition) below).

**Command run** (exit code 0):

```
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/shadow-kc-report.py \
  --corpus ~/.claude/state/wayfinder-corpus/2026-06-12/wayfinder-corpus.jsonl \
  --labels docs/research/2026-07-19-shadow-sample-gold-labels-redacted.jsonl \
  --repo-root . \
  --catalog-path ~/.claude/state/dispatch-catalog.json \
  --json .tmp/2026-07-24-shadow-kc-report.json
```

**Tooling:** the computation logic lives in `scripts/shadow-kc-report.py` (provenance
partitioning and CLI/report assembly) and `scripts/corpus/eval/_kc.py` (the KC-1..KC-5 metric
functions). All figures below are taken directly from that command's JSON output and stderr
diagnostics; no numbers in this report were invented or estimated.

**Catalog-drift safety note.** `--catalog-path` is consumed only inside `_provenance_partition`
(`scripts/shadow-kc-report.py:607`, invoked at `:906`), where one shared HEAD-loaded catalog scores
`compose_route` at both a row's baseline revision and at HEAD — the function's own docstring
states "One shared HEAD-loaded catalog for both compose runs" (`scripts/shadow-kc-report.py:617`).
Catalog drift therefore cancels by construction for the provenance check, per the parent
remediation plan's design (`docs/superpowers/plans/2026-07-23-shadow-kc-provenance-guard-remediation.md`
§4). The KC-1..KC-5 metrics themselves take only the rows that survive that partition
(`scripts/shadow-kc-report.py:925`) plus their already-logged shadow/live decisions and the frozen
gold labels — no live catalog re-scoring feeds the KC metrics. Today's freshly rebuilt catalog is
therefore safe to use for the provenance check and does not confound the verdicts below.

### Addendum 2026-07-25 (post-#506 re-run)

Issue #503 identified a KC-4 eligibility gap in `compute_kc4`: rows with `caller_domain` in
`{"is_any", "project_meta"}` and `posture=operate` were treated as eligible for the neutrality
check purely on posture, without checking whether the caller/gold domain disagreement could
actually change the route. PR #506 (merged as `bee6683`) fixed this by adding a per-row
`_route_could_differ` check (`scripts/corpus/eval/_kc.py:51`–`:79`) that compares
`cell_map_lookup(caller_domain, posture)` against `cell_map_lookup(gold_domain, posture)` for
each row's actual pair, so a coincidental domain-pair collision (where the route provably could
not change) is correctly excluded from eligibility.

Re-running the command below — identical in every flag to the "Command run" above except the
`--json` output filename — against repo revision `bee6683` (the commit that merged PR #506; the
original run cited everywhere else in this document was against `d0230cd`) produced:

```
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/shadow-kc-report.py \
  --corpus ~/.claude/state/wayfinder-corpus/2026-06-12/wayfinder-corpus.jsonl \
  --labels docs/research/2026-07-19-shadow-sample-gold-labels-redacted.jsonl \
  --repo-root . \
  --catalog-path ~/.claude/state/dispatch-catalog.json \
  --json .tmp/2026-07-25-shadow-kc-report-post-merge.json
```

```text
KC-1: FAIL — {"lexical_rc": 0.4268, "shadow_rc": 0.6707}
KC-2: PASS — {"anchor": 0.2558, "lexical_cw": 0.25, "shadow_cw": 0.1509}
KC-3: PASS — {"eligible_n": 42, "numerator": 40, "rate": 0.9524}
KC-4: INSUFFICIENT_DATA — {"eligible_n": 0, "violations": 0}
KC-5: FAIL — {"shadow_rc": 0.5714, "slice_n": 7}
Whole-sample cut: {"n": 161, "shadow_cw": 0.1509, "shadow_rc": 0.6707}
Gated-eligible subset cut: {"n": 42, "shadow_cw": 0.1905, "shadow_rc": 0.8182}
Caller-label match breakdown: Matched gold: 29; caller-label mismatch/disagreement: 53.
Go/no-go: NO-GO: failed criteria: KC-1, KC-5. Insufficient data: KC-4.
```

KC-1, KC-2, KC-3, and KC-5 are numerically identical to the original 2026-07-24 run — expected,
since #506 touches only `compute_kc4`. KC-4 moved from **FAIL** (`eligible_n: 36`,
`violations: 30`) to **INSUFFICIENT_DATA** (`eligible_n: 0`, `violations: 0`): the corrected
per-row check determined that every one of the 36 previously-"eligible" rows in this gold sample
is a coincidental domain-pair collision where the route provably could not change, so none of
them are genuinely eligible to test KC-4's route-change risk. KC-4 can no longer be assessed as
PASS or FAIL on this dataset — it is a data-coverage gap now, not a resolved or newly-introduced
routing-neutrality violation. The overall verdict stays **NO-GO**, on KC-1 and KC-5, unrelated to
this fix. See #503 for the original gap and #506 for the fix; the sections below carry the
detailed per-criterion breakdown from the original 2026-07-24 run and are annotated where KC-4's
change affects their reading.

## Provenance partition

PR #502 replaced the prior whole-run boolean provenance guard with a per-row
`_provenance_partition` (`scripts/shadow-kc-report.py:607`): each corpus row's `matcher_version` is
individually resolved to a git revision and its dependency modules diffed against HEAD, rather than
requiring one globally consistent version across all 245 rows. This is what unblocked #499/#485.

Running that partition over the full 245-row corpus produced:

| Partition | Rows | Reason |
|---|---|---|
| **Included** | 161 | `compose_route` agrees between the row's baseline revision and HEAD (`scripts/shadow-kc-report.py:925` filters rows to this set; all KC criteria below are computed only over these 161) |
| **Excluded** | 75 | 74 rows: a dependency module (`_cells.py` or one of the other five transitively-checked modules — `_decide.py`, `_types.py`, `_match.py`, `_stem.py`, `match_filters.py`) differs between the row's stamped baseline revision and HEAD. 1 row (`corpus_id` 57925): an actual `compose_route` decision disagreement between baseline and HEAD, disagreeing on `agent`. |
| **Unverifiable** | 9 | `matcher_version` was literally the string `"unknown"` and could not resolve to any git revision |
| **Total** | 245 | 161 + 75 + 9 = 245, reconciling exactly against the full corpus |

**Context on the excluded bucket.** Per issue #499's original triage, the 245-row corpus carries
three `matcher_version` stamps: 162 rows at `1.3.1`, 74 rows at dev-commit `6d5f416` (predating
`1.3.1`), and 9 rows `unknown`. The 74-row `6d5f416` bucket lines up numerically with the 74
dependency-drift exclusions above — i.e. the verdict in this report is computed almost entirely on
the `1.3.1`-stamped subset (161 ≈ 162 − 1, where the 1 is the lone disagreement row, itself
presumably `1.3.1`-stamped).

**This should be stated plainly: the go/no-go verdict below rests on the `1.3.1` subset. The older
`6d5f416` traffic was excluded en bloc by the narrowed guard's per-row dependency-drift check, not
cherry-picked to produce a favorable result.**

## Per-criterion verdicts (KC-1..KC-5)

Computed over the 161 included rows, per `scripts/corpus/eval/_kc.py`.

| KC | Status | Threshold | Metrics |
|---|---|---|---|
| KC-1 | **FAIL** | `shadow_rc >= 0.6891` AND `shadow_rc >= lexical_rc + 0.20` (`scripts/corpus/eval/_kc.py:26`–`:27`, `:98`–`:101`) | `lexical_rc: 0.4268`, `shadow_rc: 0.6707` |
| KC-2 (hard block) | **PASS** | `shadow_cw <= 0.2558` — fixed historical lexical-CW anchor (`scripts/corpus/eval/_kc.py:21`, `:128`–`:130`) | `anchor: 0.2558`, `lexical_cw: 0.25`, `shadow_cw: 0.1509` |
| KC-3 | **PASS** | `rate >= 0.55` (`scripts/corpus/eval/_kc.py:28`, `:189`) | `eligible_n: 42`, `numerator: 40`, `rate: 0.9524` |
| KC-4 | **INSUFFICIENT_DATA** | `violations == 0` on the eligible set; `eligible_n == 0` short-circuits to INSUFFICIENT_DATA (`scripts/corpus/eval/_kc.py:264`–`:267`) | `eligible_n: 0`, `violations: 0` — see the KC-4 root cause note below; was `eligible_n: 36`, `violations: 30` / **FAIL** in the original 2026-07-24 run, before #503/#506 |
| KC-5 | **FAIL** | `shadow_rc >= 0.600` (`scripts/corpus/eval/_kc.py:29`, `:272`) | `shadow_rc: 0.5714`, `slice_n: 7` |

**KC-4 root cause.** In the original 2026-07-24 run, all 30 violations traced to one path:
`caller_domain=is_any`, `posture=operate`, routed to `ops` via
`branch3_generic`/`branch3_ops_veto`. This fired because `is_any` is unconditionally ungated by
design (`src/claude_wayfinder/match/_cells.py:155`) — a design-vs-criterion mismatch, not a
routing bug: `compute_kc4` as originally written had no way to express "posture-routed to a
domain-independent agent" as non-violating. Issue #503 tracked this; PR #506 (merged `bee6683`)
resolved it by adding a per-row `_route_could_differ` check (`scripts/corpus/eval/_kc.py:51`–`:79`)
so eligibility depends on whether the row's specific caller/gold domain pair could actually change
the route under that row's posture, rather than on posture alone. Re-running against `bee6683`
finds `eligible_n: 0` (see [Addendum 2026-07-25](#addendum-2026-07-25-post-506-re-run)): every
row that was previously counted as an eligible violation turned out to be a domain-pair collision
where the route provably could not change, so KC-4 is now **INSUFFICIENT_DATA** rather than
**FAIL** on this gold sample. Full mechanism in #503 and #506.

**KC-5 small-sample caveat.** KC-5's FAIL rests on `slice_n: 7` — the gold `infra_deploy` slice
within the 161 included rows is thin. `scripts/corpus/eval/_kc.py`'s `compute_kc5` (`:243`–`:278`)
only returns `"INSUFFICIENT_DATA"` when `slice_n == 0` (`:269`–`:270`); at n=7 it computes a normal
PASS/FAIL against the routing-correctness floor, so this FAIL is the script's correct, by-design
output rather than a bug or a suppressed insufficient-data case. Even so, a milestone-gating
verdict resting partly on a 7-row slice deserves that flag stated here, in the document itself: KC-5's
FAIL should be read with the caveat that it is a small-sample result, not a high-confidence one.

## Whole-sample vs. gated-eligible cuts

This cut compares the whole sample against the gated-eligible subset (rows outside the two-axis
gate's intended scope excluded), to look for a traffic-mix-dilution effect distinct from genuine
Compose routing underperformance.

| Cut | n | `shadow_cw` | `shadow_rc` |
|---|---|---|---|
| Whole-sample (all 161 included rows) | 161 | 0.1509 | 0.6707 |
| Gated-eligible subset (KC-3-eligible rows only) | 42 | 0.1905 | 0.8182 |

`shadow_rc` rises from 0.6707 on the whole sample to 0.8182 on the gated-eligible subset — routing
correctness is materially better on the subset where the two-axis gate actually applies cleanly.
This gap is consistent with the whole-sample number being diluted by rows outside that gate's
intended scope — the traffic-mix-dilution effect issue #485 asked this cut to look for — though the
cut does not control for other differences between the two subsets, so part of KC-1's whole-sample
shortfall may reflect traffic mix rather than Compose logic on the traffic the gate targets.

## Caller-label match breakdown

This cut isolates caller-label noise (the caller's own declared `domain` disagreeing with the
human-annotated gold `domain`, before Compose ever runs) from genuine Compose-logic error.

Of the 161 included rows, 82 also carry a gold label:

| Outcome | Rows |
|---|---|
| Caller-declared `domain` matches gold `domain` | 29 |
| Caller-declared `domain` disagrees with gold `domain` | 53 |
| **Total gold-labeled, included rows** | 82 |

(The remaining rows of the 120-row gold set fall outside the 161-row included partition and are not
part of this breakdown.)

A 53/82 (~65%) caller-label disagreement rate is a large share of "disagreement" that is not
necessarily Compose's fault — the caller declared a different domain than gold before Compose ever
ran. This matters for interpreting KC-1's FAIL above: some of KC-1's shortfall may trace to upstream
caller-labeling noise rather than to the Compose routing logic itself. (KC-4 is not implicated here:
per `compute_kc4`, caller-domain disagreement with gold is a precondition for a row's KC-4
eligibility, not a candidate cause of the violations counted among those eligible rows — see the
KC-4 root cause note above.) This is stated as a contributing factor worth investigating further,
not a proven explanation — the data here does not by itself decompose how much of KC-1's FAIL is
caller-label noise versus Compose error.

## What this means for M15-7

The verdict is **NO-GO**. The M15-7 hard-routing flip should **not** proceed on this evidence.
Two of five criteria fail — KC-1 (lexical/shadow routing correctness) and KC-5 (infra-deploy
routing correctness, on a thin 7-row slice) — and those failures indicate real Compose
routing-correctness and traffic-mix issues that warrant investigation before any flip decision is
revisited. KC-4 no longer contributes a failing criterion, but not because it passed: after
\#503/\#506 corrected `compute_kc4`'s eligibility check (see
[Addendum 2026-07-25](#addendum-2026-07-25-post-506-re-run)), the gold sample's eligible set
collapsed to `eligible_n: 0`, so KC-4 is **INSUFFICIENT_DATA** — this dataset currently contains
no rows that can test KC-4's routing-neutrality question at all, a data-coverage gap rather than
a resolved-favorable or newly-introduced routing problem. On KC-1 specifically, the failure is on
the absolute floor only: `shadow_rc` (0.6707) falls short of the 0.6891 floor by 0.0184, while it
clears the lexical-margin requirement (0.6707 >= 0.4268 + 0.20 = 0.6268) with room to spare — so
shadow does out-route lexical by the required margin; the shortfall is specifically against the
absolute correctness bar, not against lexical. KC-2, the hard block criterion, does PASS, so
the failure is not of the single most severe kind this gate exists to catch — but a NO-GO on the
two other failing criteria, both of which surface genuine routing-correctness gaps (KC-1, KC-5),
is enough on its own to withhold the flip. KC-4's INSUFFICIENT_DATA status means the M15-7
decision cannot lean on it either way; closing that data gap — a gold sample with rows that
genuinely exercise route-changing caller-domain mislabels — is a prerequisite for KC-4 to
contribute a verdict on any future re-run. The caller-label disagreement rate (~65% of
gold-labeled included rows) and the whole-sample vs. gated-eligible gap are offered above as
diagnostic leads for that follow-up investigation, not as mitigating factors that override the
FAIL verdicts.
