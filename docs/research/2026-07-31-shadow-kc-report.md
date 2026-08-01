# Shadow KC Report — regenerated against 146-row unioned gold set

**Issue:** #538, filed after an `auditor` pass checking current data state against #450's
go/no-go gates flagged that the committed 2026-07-27 report still cited the 120-row gold-label
set even though #521's purposive KC-4 supplement (merged via #536/#537, commit `35bd0a2`) grew
gold labels to 146 rows.

This is a **relabel-only regeneration** — the underlying shadow corpus is unchanged from the
2026-07-27 report (`~/.claude/state/wayfinder-corpus/2026-07-27/wayfinder-corpus.jsonl`, 319
rows, same manifest `docs/research/2026-07-27-corpus-manifest.json`). Only the `--labels` input
changed, from `docs/research/2026-07-19-shadow-sample-gold-labels-redacted.jsonl` (120 rows) to
`docs/research/2026-07-31-shadow-gold-labels-unioned.jsonl` (146 rows).

## Command

```
./.venv/Scripts/python.exe scripts/shadow-kc-report.py \
  --corpus ~/.claude/state/wayfinder-corpus/2026-07-27/wayfinder-corpus.jsonl \
  --labels docs/research/2026-07-31-shadow-gold-labels-unioned.jsonl \
  --manifest docs/research/2026-07-27-corpus-manifest.json \
  --repo-root . \
  --json docs/research/2026-07-31-shadow-kc-report.json
```

**Interpreter note.** Same pattern as the 2026-07-27 report: this worktree
(`.worktrees/538-regen-shadow-kc-report`) has no `.venv/` of its own — ran the sibling
main-checkout venv's interpreter while pointing `--repo-root` at this worktree so the provenance
partition walked this worktree's git tree.

## KC-1..5 results

| Criterion | Status | Metrics |
|---|---|---|
| KC-1 | **FAIL** | `lexical_rc: 0.3922, shadow_rc: 0.6667` |
| KC-2 | PASS | `anchor: 0.2558, lexical_cw: 0.3077, shadow_cw: 0.129` |
| KC-3 | PASS | `eligible_n: 65, numerator: 62, rate: 0.9538` |
| KC-4 | PASS | `eligible_n: 1, violations: 0` |
| KC-5 | **INSUFFICIENT_DATA** | `shadow_rc: 0.5714, slice_n: 7` |

- Whole-sample cut: `n: 234, shadow_cw: 0.129, shadow_rc: 0.6667`
- Gated-eligible subset cut: `n: 65, shadow_cw: 0.1818, shadow_rc: 0.8261`
- Caller-label match breakdown: matched gold 32; caller-label mismatch/disagreement 70.

## Go/no-go recommendation

> **NO-GO** — failed criteria: KC-1. Insufficient data: KC-5.

Unchanged from the 2026-07-27 report's verdict — the corpus and its provenance partition are
identical; only the gold-label join changed, and KC-1/KC-2/KC-3/KC-5 are whole-sample or
gated-eligible-subset metrics that don't consume gold labels at all (only KC-4 does).

## Report provenance

- Repository HEAD: `35bd0a2704772e65a3cda33c5bc3e7c85a589368`
- Provenance drift fraction: `0.2664576802507837` (identical to the 2026-07-27 report — same
  corpus, same HEAD at generation time; drift-threshold gate FAILs, `>= 0.25`)
- Excluded from KC computation: 76 rows (74 via `_cells.py` dependency drift, 2 via an actual
  `compose_route` agent disagreement between baseline and HEAD — `corpus_id` 57925, 59238)
- Unverifiable: 9 rows (`matcher_version 'unknown'` could not be resolved to a git revision)

## Open question — KC-4 `eligible_n` unchanged at 1 despite 26 new gold rows

Of the 26 gold-label rows added by the 146-row union (146 − 120), **20 are present in the
2026-07-27 corpus** and none of those 20 fall in the excluded-76 or unverifiable-9 sets — they
are otherwise "clean" rows. Yet KC-4's `eligible_n` is still 1, unchanged from the 2026-07-27
report. This means KC-4's eligibility filter (in `scripts/corpus/eval/_kc.py`) selects on some
criterion narrower than "gold-labeled + in corpus + not excluded/unverifiable" — likely a
specific caller-domain-mislabel condition per its docstring. Not investigated further here (out
of scope for a relabel-only regeneration); flagging for whoever next works KC-4 or #450's
build/operate-slice eval gap.

## Gate

A go/no-go verdict is not flip-authorizing unless both gate conditions hold. Rule: the
auto-checkable provenance drift fraction PASSES when below 0.25 and FAILS when at or above it.
This run: the auto-checkable drift-threshold half FAILS (0.266 >= 0.25).
