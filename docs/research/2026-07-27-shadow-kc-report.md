# Shadow KC Report — 2026-07-27 Regeneration Cycle (Provisional NO-GO)

**Issue:** #520 ("Execute one regeneration cycle"), sub-issue of #516 ("Implement corpus
regeneration/re-stamping process"), plan
`docs/superpowers/plans/2026-07-25-corpus-regeneration-process.md` §§6, 10, 11.

This report executes the regeneration runbook (`docs/maintenance/corpus-regeneration.md`) end to
end for the first time since it was signed off (#516, closed 2026-07-26): accumulate fresh
traffic, rebuild the corpus, rejoin the existing gold labels unchanged, and re-run
`scripts/shadow-kc-report.py` — the go/no-go report generator that merged as part of #518 and is
exercised here for the first time against production traffic — to produce a fresh KC-1..KC-5
verdict.

## Overall recommendation

> **NO-GO** — failed criteria: KC-1. Insufficient data: KC-4, KC-5.
>
> This verdict is **provisional, not flip-authorizing**, independent of the per-criterion result
> above. The runbook's Step 4 gate (`docs/maintenance/corpus-regeneration.md` Step 4 item 4; design
> basis `docs/superpowers/plans/2026-07-25-corpus-regeneration-process.md` §7 item 4) requires
> `provenance_drift_fraction < 0.25` before any go/no-go run may be trusted for the M15-7 flip
> decision. This run measures **0.2665** (`0.266458` in the raw JSON output;
> `docs/research/2026-07-27-shadow-kc-report.json`), at/above the threshold. The gate rule is
> binary — this run fails the auto-checkable half regardless of whether the per-criterion verdicts
> below look favorable or not.

## Methodology

**Inputs:**

- Corpus: `~/.claude/state/wayfinder-corpus/2026-07-27/wayfinder-corpus.jsonl` — 319 rows (up from
  245 in the 2026-07-19 corpus), sha256 `0f28eeca090857370544083dcbb895e56c4066f6451c21f381729891bef3c603`.
  This value is recorded in the manifest's own `"sha256"` field
  (`docs/research/2026-07-27-corpus-manifest.json`) and was independently confirmed for this report
  by re-hashing the artifact file directly.
- Corpus manifest: `docs/research/2026-07-27-corpus-manifest.json`, produced by `--manifest-out` on
  the rebuild command below.
- Gold labels: `docs/research/2026-07-19-shadow-sample-gold-labels-redacted.jsonl` — 120 labels,
  unchanged from the prior cycle. Per the runbook's Step 3 ("never re-draw"), no new labeling round
  ran this cycle (that is #521's job, blocked on this issue closing); the existing gold set rejoins
  the rebuilt corpus by `corpus_id`.
- Dispatch catalog: no `--catalog-path` flag was passed. `_resolve_catalog_path`
  (`src/claude_wayfinder/match/_catalog.py:195`) falls back to the `DISPATCH_CATALOG_PATH`
  environment variable when the flag is omitted; in the shell this report's author used, that
  variable is set (in the operator's shell profile) to
  `<operator home>\.claude\state\dispatch-catalog.json` — confirmed by echoing it before the
  reproduction run.
- Repo HEAD: `3bb28a9c8b43e6265cf1145c2b2d895161218da4`.

**Traffic accumulation (runbook Step 1).**
`./.venv/Scripts/python.exe -m scripts.corpus --profile-only` reported an organic count of 1,914
against the prior manifest's `"total_organic": 1478`
(`docs/research/2026-07-19-shadow-corpus-manifest.json`) — growth of +436 rows (~29.5%). The repo
maintainer approved proceeding with a rebuild at this volume rather than waiting further. The
rebuild command itself (below), run shortly after, recorded `"total_organic": 1917` in its own
manifest (`docs/research/2026-07-27-corpus-manifest.json`) — three additional rows accrued in the
short interval between the profile check and the rebuild, which is expected under an append-only
log and does not affect anything below; all figures in this report use the rebuild's own manifest,
not the earlier profile-only reading. Against the 2026-07-19 manifest's `"total_organic": 1478`, the
rebuild's own `"total_organic": 1917` is growth of **+439 rows (+29.7%)**.

**Prerequisite:** `DISPATCH_CATALOG_PATH` must be set in the shell before running the commands
below; see Inputs above for the path used in this run.

**Rebuild command (runbook Step 2).** This worktree has no `.venv/` of its own; the
`./.venv/Scripts/python.exe` below is the sibling main-checkout venv's interpreter — see the
"Interpreter note" below for the full explanation.

```shell
./.venv/Scripts/python.exe -m scripts.corpus \
  --output-dir ~/.claude/state/wayfinder-corpus/2026-07-27/ \
  --shadow-only --join-shadow-from-twins \
  --sample-floor 30 \
  --manifest-out docs/research/2026-07-27-corpus-manifest.json
```

`--sample-floor 30` matches the 2026-07-19 run's floor, held constant per the runbook's guidance
against silently changing the floor between comparable runs
(`docs/maintenance/corpus-regeneration.md` Step 2, "Raising `--sample-floor` shifts strata
composition, not just size"). Result: 319 rows in corpus (`"total_in_corpus"` in
`docs/research/2026-07-27-corpus-manifest.json`), up from 245 in the prior manifest — **+74 rows
(+30.2%)**.

**Join-integrity spot check.** Per the runbook's explicit ask (Step 2, "Spot-check this before
trusting the join"), three `corpus_id`s already present in the gold file — 56248, 58944, 58954 —
were checked against the corresponding lines in `~/.claude/state/dispatch-log.jsonl`: all three
still carry `task_description` and are consistent with their existing gold labels (`domain:
project_meta`, `confidence: high`). The `corpus_id` → dispatch-log-line join holds; no drift found.

**Verify command (runbook Step 4), reproduced independently for this report.** Same interpreter
substitution as the rebuild command above — see the "Interpreter note" below.

```shell
./.venv/Scripts/python.exe scripts/shadow-kc-report.py \
  --corpus ~/.claude/state/wayfinder-corpus/2026-07-27/wayfinder-corpus.jsonl \
  --labels docs/research/2026-07-19-shadow-sample-gold-labels-redacted.jsonl \
  --manifest docs/research/2026-07-27-corpus-manifest.json \
  --repo-root . \
  --json docs/research/2026-07-27-shadow-kc-report.json
```

**Interpreter note.** This worktree (`.worktrees/520-execute-regeneration-cycle`) has no `.venv/`
of its own. This report's author ran the sibling main-checkout venv's interpreter while pointing
`--repo-root` at this worktree, so the provenance partition still walked this worktree's git tree,
not the main checkout's. A future reproduction from a worktree without its own venv needs the same
substitution.

This report's author re-ran the command above from a clean invocation and confirmed:

- The resulting JSON is byte-identical to the committed `docs/research/2026-07-27-shadow-kc-report.json`.
- All five KC verdicts, the whole-sample/gated-eligible cuts, and the caller-label breakdown
  (all reproduced below) match exactly.
- The printed `Manifest SHA-256` line reproduces exactly:
  `07f9e9b511b7c7a71decc4f1976c1718c98b91fab7580058fc75705907ae72e3`.

**Note on the two sha256 values cited in this report.** `scripts/shadow-kc-report.py` prints two
distinct hashes that are easy to conflate:

- The manifest JSON's own `sha256` field (`0f28eeca090857370544083dcbb895e56c4066f6451c21f381729891bef3c603`)
  hashes the **corpus artifact** (`wayfinder-corpus.jsonl`) — confirmed above by independently
  re-hashing that file.
- The report's `Manifest SHA-256` line (`07f9e9b511b7c7a71decc4f1976c1718c98b91fab7580058fc75705907ae72e3`)
  hashes the **manifest JSON file's own raw bytes** (`scripts/shadow-kc-report.py:999`,
  `hashlib.sha256(args.manifest.read_bytes())`).

Both are cited by full value here so a future reader checking either number against its named
source does not mistake one for the other.

**Provenance partition.** Running `_provenance_partition` (`scripts/shadow-kc-report.py:607`,
per-row baseline-vs-HEAD `compose_route` comparison) over the full 319-row corpus produced:

| Partition | Rows | Reason |
|---|---|---|
| Included | 234 | `compose_route` agrees between the row's baseline revision and HEAD; all KC criteria below are computed only over these 234 |
| Excluded | 76 | 74 rows: dependency module `src/claude_wayfinder/match/_cells.py` differs between the row's stamped baseline revision and HEAD. 2 rows (`corpus_id` 57925, 59238): an actual `compose_route` decision disagreement between baseline and HEAD, disagreeing on `agent`. |
| Unverifiable | 9 | `matcher_version` was the literal string `"unknown"` and could not resolve to any git revision — pre-#461 traffic, permanently unverifiable per the runbook's "What this process does not do" section |
| **Total** | 319 | 234 + 76 + 9 = 319, reconciling exactly against the full corpus |

`_provenance_drift_fraction` (`scripts/shadow-kc-report.py:175`-`:181`) computes
`(len(excluded) + len(unverifiable)) / (len(included) + len(excluded) + len(unverifiable))` =
(76 + 9) / 319 = **0.2665** (`0.266458` unrounded). This is the value the Step 4 gate checks against
0.25 — see [Overall recommendation](#overall-recommendation) above.

## Per-criterion verdicts (KC-1..KC-5)

Computed over the 234 included rows, per `scripts/corpus/eval/_kc.py`.

| KC | Status | Metrics |
|---|---|---|
| KC-1 | **FAIL** | `lexical_rc: 0.4268`, `shadow_rc: 0.6707` |
| KC-2 (hard block) | **PASS** | `anchor: 0.2558`, `lexical_cw: 0.25`, `shadow_cw: 0.1509` |
| KC-3 | **PASS** | `eligible_n: 65`, `numerator: 62`, `rate: 0.9538` |
| KC-4 | **INSUFFICIENT_DATA** | `eligible_n: 0`, `violations: 0` |
| KC-5 | **INSUFFICIENT_DATA** | `shadow_rc: 0.5714`, `slice_n: 7` |

**Comparison against the 2026-07-19 run** (161 included rows, out of a 245-row corpus,
`docs/research/2026-07-19-shadow-kc-report.md`):

- KC-1 and KC-2's metrics are numerically **identical** to the 2026-07-19 run
  (`lexical_rc: 0.4268`/`shadow_rc: 0.6707`; `lexical_cw: 0.25`/`shadow_cw: 0.1509`) despite the
  included set growing from 161 to 234 rows. This is not evidence of proportional growth — it is
  the expected result of how these two criteria are computed.
  `metric_routing_correctness` and `metric_confident_wrong_rate`
  (`scripts/corpus/eval/_metrics.py:565`, `:515`-`:517`) each filter their input to rows whose
  `corpus_id` has a gold label before computing anything (`labeled = [r for r in results if
  r.corpus_id in labels]`); rows with no gold label never enter the KC-1/KC-2 calculation at all.
  Because the gold set is the unchanged 120-row file (see Inputs above), the gold-joined row set
  feeding KC-1 and KC-2 did not grow between the two runs even though the corpus and its included
  set did — so identical metrics are the expected outcome of an unchanged gold-labeled subset, not
  evidence that the wider corpus reconfirmed the same routing-correctness numbers. The caller-label
  match breakdown below (29 matched / 53 mismatched, 82 total, identical to 2026-07-19) is the same
  fact surfacing a second way.
- KC-3's `eligible_n` grew from 42 to 65 rows, at the same ~0.95 pass rate. Unlike KC-1/KC-2, KC-3's
  eligibility does not filter on gold labels at all (`compute_kc3` explicitly discards its `gold`
  parameter, `scripts/corpus/eval/_kc.py:188`, `del gold`) — its eligible set is a function of
  caller-supplied `domain`/`posture`/`confidence` fields present on every corpus row, gold-labeled or
  not, which is why it grew in step with the wider corpus while KC-1/KC-2 did not.
- KC-4 stays **INSUFFICIENT_DATA** at `eligible_n: 0` — unchanged since PR #506 (merged `bee6683`)
  added the per-row `_route_could_differ` eligibility check (see the 2026-07-19 report's "KC-4 root
  cause" section for the full mechanism). This cycle's rebuild did not add any row that changes
  that outcome.
- KC-5 moved from **FAIL** (`shadow_rc: 0.5714`, `slice_n: 7`) to **INSUFFICIENT_DATA** — with the
  *identical* `shadow_rc` and `slice_n` values. This is not a change in the underlying data; it is a
  scoring-rule change made between the two runs: PR #511 ("add minimum-sample-size floor to KC-5",
  merged as #513) raised `compute_kc5`'s insufficient-data threshold from `slice_n == 0`
  (`_KC5_MIN_SLICE_N` did not exist at the 2026-07-19 run) to `slice_n < _KC5_MIN_SLICE_N` with
  `_KC5_MIN_SLICE_N = 20` (`scripts/corpus/eval/_kc.py:30`, `:304`). At `slice_n: 7`, this run falls
  below that floor, so KC-5 can no longer be scored PASS/FAIL on this dataset — it is a
  data-coverage gap, not a resolved or newly-introduced routing problem, exactly analogous to KC-4's
  existing status.

## Whole-sample vs. gated-eligible cuts

| Cut | n | `shadow_cw` | `shadow_rc` |
|---|---|---|---|
| Whole-sample (all 234 included rows) | 234 | 0.1509 | 0.6707 |
| Gated-eligible subset (KC-3-eligible rows only) | 65 | 0.1905 | 0.8182 |

The `n` column here is the row count of each cut (234 whole-sample, 65 gated-eligible per
`_cut_metrics`, `scripts/shadow-kc-report.py:795`, `len(rows)`), but `shadow_rc`/`shadow_cw`
themselves are computed by the same gold-filtering metric functions described above, so — as with
KC-1/KC-2 — both rate columns are numerically identical to the 2026-07-19 run's cuts (which
reported n=161/n=42) for the same reason: the gold-labeled subset feeding the rates is unchanged.
The gap between the whole-sample and gated-eligible rates (0.6707 vs. 0.8182) is therefore also
unchanged from the prior run, not independently reconfirmed by the larger corpus.

## Caller-label match breakdown

Of the 234 included rows, matched-gold vs. caller-label rows: **29** matched gold, **53**
caller-label mismatch/disagreement (identical counts to the 2026-07-19 run, since the underlying
120-row gold set is unchanged and rejoined the same way).

## What this means for #521

Issue #521 ("Close the KC-4 / KC-5 coverage gap", per the plan's #516e) is scoped exactly to the gap this
run reconfirms: KC-4 has `eligible_n: 0` (zero rows in this gold sample can test KC-4's
routing-neutrality question at all under the corrected #506 eligibility check), and KC-5 has only
`slice_n: 7` — thirteen rows short of the `_KC5_MIN_SLICE_N: 20` floor added by #511/#513. Both
criteria remain starved on the current gold sample regardless of how much unlabeled corpus growth
occurs, because growing the corpus does not by itself add labeled rows in the specific
route-changing-caller-domain and `infra_deploy` strata these two criteria depend on. This is the
coverage gap #521's incremental purposive labeling round exists to close, per the plan's resolved
D3 decision: keep the proportional stratified draw as the substrate for the rate-based criteria
(KC-1/KC-2/KC-3), and draw a separate, explicitly-labeled purposive supplement used only for
KC-4/KC-5 and their dependents, without contaminating the KC-1/KC-2/KC-3 substrate
(`docs/superpowers/plans/2026-07-25-corpus-regeneration-process.md` Step 3, "Targeted vs.
proportional draw"). This report does not re-litigate that design; it hands off the now-reconfirmed
gap to #521 as-is.

## What this means for the M15-7 flip decision

This run **cannot authorize** the M15-7 hard-routing flip: the Step 4 gate fails on the
provenance-drift half (0.2665 >= 0.25), independent of the KC-1 FAIL. It is offered here as one data
point among the ongoing M15 verification effort, not as a verdict on the flip decision's own
go/no-go criteria — those criteria and their disposition are M15-7's separate scope and are not
addressed further in this report. As explained above, KC-1 and KC-2's metrics matching the 2026-07-19
run exactly is not new evidence that the routing-correctness gap is stable on a larger sample — both
runs compute those two criteria over the same unchanged 120-row gold set, so this cycle's corpus
growth could not have moved them regardless of what the underlying routing behavior actually looks
like on the larger, mostly-unlabeled corpus. Whether the gap holds on a larger *labeled* sample
remains untested until #521's incremental labeling round adds gold coverage beyond the current 120
rows.

## Provenance and reproducibility summary

- Corpus manifest: `docs/research/2026-07-27-corpus-manifest.json`, artifact sha256
  `0f28eeca090857370544083dcbb895e56c4066f6451c21f381729891bef3c603`.
- Report JSON: `docs/research/2026-07-27-shadow-kc-report.json`, manifest-file sha256 (as printed by
  the report tool) `07f9e9b511b7c7a71decc4f1976c1718c98b91fab7580058fc75705907ae72e3`.
- Repo HEAD at report time: `3bb28a9c8b43e6265cf1145c2b2d895161218da4`.
- Excluded rows: 76 (74 dependency-drift on `src/claude_wayfinder/match/_cells.py`, 2
  `compose_route` disagreements on `agent`, corpus_id 57925 and 59238).
- Unverifiable rows: 9 (`matcher_version: "unknown"`, pre-#461 traffic).
- Independently reproduced by this report's author; see the Methodology section above for the exact
  command and the reproduction confirmation.
