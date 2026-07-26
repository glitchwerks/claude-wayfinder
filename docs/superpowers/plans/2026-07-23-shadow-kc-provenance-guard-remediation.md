---
title: Unblock #485 — remediate the shadow-kc-report provenance guard (issue #499)
touches:
  - scripts/shadow-kc-report.py            # provenance guard narrowing (behavioral per-row check) — the durable fix
  - scripts/corpus/__main__.py             # NEW --matcher-version filter flag OR a post-hoc filter script (see §5)
  - scripts/corpus/builder.py              # only if the version filter lands in the builder rather than a standalone script
  - src/claude_wayfinder/match/_compose.py # READ-ONLY — compose_route IS the §4 HEAD-vs-tag vehicle; subject of the 07eb3dd delta; imported at two versions, not edited
  - src/claude_wayfinder/match/_main.py    # READ-ONLY — _build_shadow_record confirms production drives compose_route off CALLER labels (§4)
  - src/claude_wayfinder/match/_cells.py   # READ-ONLY — second guarded dependency module
  - scripts/corpus/eval/_systems.py        # READ-ONLY REFERENCE ONLY — run_supplied_compose is a DIVERGENT copy of the algorithm, NOT touched by 07eb3dd; must NOT be used as the §4 vehicle
  - pyproject.toml                         # version bump 1.3.1 -> 1.3.2 (re-accumulate branch only, §6)
  - .claude-plugin/plugin.json             # version bump (re-accumulate branch only, §6)
  - CHANGELOG.md                           # release entry (re-accumulate branch only, §6)
  - tests/test_scripts/test_shadow_kc_report.py  # PRIMARY guard-test location (TestMatcherVersionGuard :421-641) — narrowed-guard tests land here
  - tests/test_corpus_eval/                # ADD ONLY IF the §4 HEAD-vs-tag harness produces new eval-pipeline tests (does not replace the primary location above)
skills_relevant:
  - python
---

# Unblock #485 — remediate the shadow-kc-report provenance guard (issue #499)

> **SUPERSEDED — see `docs/superpowers/plans/2026-07-25-corpus-regeneration-process.md` (issue #516).**
> The status line below ("PLAN / DECISIONS OPEN") is stale; this plan's open decisions were resolved
> when PR #502 landed the narrow, per-row provenance partition. **Kept on disk, not deleted** — the
> retention rule in CLAUDE.md § Document Files ("persistence rule wins over the lifecycle rule") applies
> because `docs/research/2026-07-19-shadow-kc-report.md:50-51` still cites this plan's §4 as the
> authoritative design reference for the catalog-drift-cancellation argument. Three corrections, recorded
> here per the corpus-regeneration-process plan §7:
>
> 1. **§4a's narrow-guard branch landed, via PR #502.** This plan's §1 description of
>    `_provenance_guard` (`scripts/shadow-kc-report.py:131`–`:214`, the whole-run, two-check gate) no
>    longer matches the code: the guard is now a per-row behavioral partition
>    (`_provenance_partition`, `scripts/shadow-kc-report.py:617-719`), not the file-diff proxy this plan
>    describes and proposes replacing.
> 2. **§5's `--matcher-version` builder-flag branch is moot.** §4.1 of this plan already makes the
>    recommendation conditional: "if §4 lands, prefer the behavioral partition (`--substrate-check`) and
>    skip the stamp flag" (`:277-279`). §4 landed (correction 1, above), so by this plan's own
>    conditional, no stamp-filter flag is needed. Do not build it from this document — a mixed-stamp
>    corpus is now handled natively by the per-row partition.
> 3. **§6's claim that the marketplace pin bump is "downstream housekeeping with no functional bearing
>    on the guard"** (`:296-299`) **is wrong for the pin bump.** The pin gates which `claude-wayfinder`
>    version the plugin venv installs, which gates what version newly-logged traffic stamps as
>    `matcher_version`, which gates whether that traffic survives the provenance partition on a future
>    regeneration. See `docs/maintenance/corpus-regeneration.md` (precondition P3) for the corrected
>    account.

**Status (as originally written; superseded above):** PLAN / DECISIONS OPEN. This is a **follow-on** to
`docs/superpowers/plans/2026-07-19-m15-6-shadow-kc-report.md` (the M15-6 parent plan), **not a
replacement**. That plan built the guard as a deliverable (§4.4); this plan remediates the guard
now that it correctly fired and is blocking #485 (M15-6c, the go/no-go report). Per CLAUDE.md
§ Issue Tracking, nothing here authorizes implementation — the §7 decision tree branches on facts an
executor must compute first (§3), and the branch choice is the user's.

**Initiative.** #499 blocks #485 (M15-6c), which is the last step before the M15-7 hard-routing flip
on Matcher v3 (`docs/superpowers/plans/2026-06-19-matcher-v3-ship-live.md`). Milestone **M15 —
matcher-v3-ship-live**. This plan's job is to get a *valid* KC substrate past the provenance guard so
issue #485 can produce the flip go/no-go verdict, and to decide whether the guard's coarse file-diff check
should be replaced with a per-row behavioral check so this class of block cannot silently recur.

**Author's tooling constraint (flag to router).** This plan was produced by a planner sub-agent with
**no shell and no GitHub read access**. Every git fact (commit ancestry, file diffs), the
`corpus_id → matcher_version` join, and all issue/PR states below are therefore **`unverified:`** and
are written as **explicit early mechanical tasks (§3)** for an executor who has a shell. The decision
tree's **branching** (§7) is parametric on those task outputs. Note this is a claim about *missing
facts*, not about design: the §7.3 recommended branch is additionally contingent on §4 being built as
the label-/catalog-invariant HEAD-vs-tag comparison (a design requirement, not a fact to look up) — see
the §7.3 prerequisite gate. If the router wants the numbers pre-computed before user review, dispatch an
agent with a shell to run §3 first.

---

## 1. The failure, precisely (verified on disk)

`scripts/shadow-kc-report.py._provenance_guard` (`:131`–`:214`) enforces two things over the corpus
rows, in order:

1. **One consistent, resolvable version.** All rows must share a single non-`"unknown"`
   `matcher_version` (`:143`–`:153`), resolved as a git rev — bare first, then `v`-prefixed fallback
   (`:155`–`:179`).
2. **The version's runtime modules match HEAD.** For each of `_DEPENDENCY_MODULES`
   (`:46`–`:49` = exactly `src/claude_wayfinder/match/_compose.py` and `.../match/_cells.py`), both
   `git diff --quiet <resolved_rev> HEAD -- <module>` must be clean (`:181`–`:197`) **and**
   `git status --porcelain -- <module>` must be clean (`:199`–`:210`).

Two failures were hit against the 245-row corpus
(`~/.claude/state/wayfinder-corpus/2026-06-12/wayfinder-corpus.jsonl`):

- **Check 1 fails on the full corpus.** The corpus carries **three** `matcher_version` stamps —
  `1.3.1` (162 rows), `6d5f416` (dev-commit, 74 rows), `unknown` (9 rows) — so `len(versions) != 1`
  (`:144`). *(Distribution is from the issue-#499 reconnaissance; re-confirm in §3.1.)*
- **Check 2 fails even after filtering to `1.3.1`.** `v1.3.1`'s `_compose.py` differs from HEAD's
  because a commit (reported as `07eb3dd`, PR #464, "widen test-authoring qualifier stems", 4+/4-)
  landed on `_compose.py` after the `v1.3.1` tag. `_cells.py` is reported unchanged since the tag.
  *(Both git facts are `unverified:` — §3.2.)*

**Why rebuilding the corpus does not fix Check 1.** `matcher_version` is stamped **at dispatch time**
into the append-only dispatch log (each corpus row carries it top-level — confirmed on the first
corpus row, alongside a duplicate copy nested in `output`). The corpus builder reads those stamps; it
does not re-derive them. So re-running `python -m scripts.corpus ...` against the existing log
**cannot** collapse three stamps into one. **Filtering is the only lever on Check 1.** This is the
pivot the rest of the plan turns on.

---

## 2. Verified ground truth (checked on disk 2026-07-23)

1. **Guard internals** are as summarized in §1 (`scripts/shadow-kc-report.py:46`–`:214`). The guard
   already fails-closed on git/subprocess errors (`:211`–`:212`) and on a dirty working tree
   (`:199`–`:210`).
2. **The corpus builder has no version filter.** `scripts/corpus/__main__.py:219`–`:272` exposes
   `--log-path`, `--output-dir`, `--sample-floor`, `--profile-only`, `--shadow-only`,
   `--join-shadow-from-twins`, `--exclude-gold-labels-file`, `--manifest-out` — and **no
   `--matcher-version`**. Any version-filtered regeneration needs a new flag or a post-hoc filter
   step (§5).
3. **The provenance vehicle is `compose_route`, and its production input is the CALLER labels — not
   gold.** `compose_route` (`src/claude_wayfinder/match/_compose.py:296`) is the live composer that
   produced every logged shadow decision: `_build_shadow_record` records "The `compose_route()` result
   dict" driven by the dispatch `labels` (`_main.py:77`, `:85`–`:88`, `:95`), i.e. the caller's
   `input.{domain,posture,confidence,area_span}` — present on all 245 rows — **not** the 120 gold
   labels. **Do NOT use `scripts/corpus/eval/_systems.run_supplied_compose` as the vehicle:** it is a
   **divergent reimplementation** of the algorithm (inline `cell_map_lookup`/`gate_agents`/`decide` at
   `_systems.py:1229`–`:1312`), it requires a `dict[int, GoldLabel]` and drives routing off *oracle*
   labels (`:1161`, `:1218`–`:1220`), and — decisively — `07eb3dd` touched `_compose.py`, **not**
   `_systems.py`, so swapping `_compose.py` versions would leave `run_supplied_compose`'s output
   unchanged (a silent no-op). `_compose.py:308`–`:309` records that `compose_route` was *ported from*
   `run_supplied_compose`; they have since diverged. §4 imports `compose_route` at two versions.
4. **The frozen gold set is 120 rows** (`docs/research/2026-07-19-shadow-sample-gold-labels-redacted.jsonl`,
   schema `corpus_id`/`domain`/`is_any`/`posture`/`gold_agent`/`confidence`/`disputed`), drawn from
   the same 245-corpus by stratified sample (seed `483`, floor `2` —
   `docs/research/2026-07-19-shadow-sample-gold-labeling.md:33`–`:38`).
5. **`corpus_id` is a stable join key** (1-based dispatch-log line number, append-only) — established
   in the parent plan's reconnaissance and in the #499 brief; existing rows' `corpus_id`s do not shift
   on regeneration, so gold labels rejoin by `corpus_id` whenever a row survives filtering.
6. **INSUFFICIENT-DATA is the established denominator-collapse behavior** — parent plan §4.2 and §8
   (`docs/superpowers/plans/2026-07-19-m15-6-shadow-kc-report.md`) already mandate reporting
   INSUFFICIENT-DATA rather than a vacuous PASS when a KC's eligible set empties. This plan reuses that
   precedent; it does not re-derive it.

---

## 3. Mechanical triage — DO THESE FIRST (executor with a shell; ~30–60 min total)

These are cheap, deterministic, and gate the whole decision tree. None require code changes. Record
outputs in the #499 issue thread before the user picks a §7 branch.

### 3.1 Confirm the version distribution and the gold join
Cross-reference the 120 gold `corpus_id`s against their top-level `matcher_version` in the corpus:

```python
# executor runs with the project interpreter (./.venv/Scripts/python.exe)
import json
from collections import Counter
corpus = r"~/.claude/state/wayfinder-corpus/2026-06-12/wayfinder-corpus.jsonl"  # expanduser
gold   = r"docs/research/2026-07-19-shadow-sample-gold-labels-redacted.jsonl"
cid2ver = {}
for line in open(corpus, encoding="utf-8"):
    if line.strip():
        r = json.loads(line); cid2ver[r["corpus_id"]] = r.get("matcher_version")
by_ver = Counter(); infra_by_ver = Counter()
for line in open(gold, encoding="utf-8"):
    if line.strip():
        g = json.loads(line); v = cid2ver.get(g["corpus_id"], "MISSING")
        by_ver[v] += 1
        if g.get("domain") == "infra_deploy": infra_by_ver[v] += 1
print("gold by version:", by_ver); print("infra_deploy by version:", infra_by_ver)
```

Output: how many of the 120 gold rows fall in each version bucket, and — critically — the
`infra_deploy` count per bucket (KC-5's slice is n≈3–5 and one lost row can collapse it).
**Planner placeholder (could not run — no shell):** if the gold sample preserves the corpus's version
proportions (162/74/9 of 245 ≈ 66/30/4 %), expect **≈79 `1.3.1` / ≈36 `6d5f416` / ≈5 `unknown`** of
120. Treat as a labeled guess; the script above is authoritative.

### 3.2 Git triage — where does `6d5f416` sit, and is `07eb3dd` isolated?
```bash
git -C <repo> merge-base --is-ancestor 07eb3dd 6d5f416 && echo "6d5f416 includes 07eb3dd" || echo "no"
git -C <repo> diff --quiet 6d5f416 HEAD -- src/claude_wayfinder/match/_compose.py; echo "compose 6d5f416..HEAD exit=$?"
git -C <repo> diff --quiet 6d5f416 HEAD -- src/claude_wayfinder/match/_cells.py;   echo "cells   6d5f416..HEAD exit=$?"
git -C <repo> diff --quiet v1.3.1  HEAD -- src/claude_wayfinder/match/_compose.py; echo "compose v1.3.1..HEAD exit=$?"
git -C <repo> diff --quiet v1.3.1  HEAD -- src/claude_wayfinder/match/_cells.py;   echo "cells   v1.3.1..HEAD exit=$?"
git -C <repo> log --oneline v1.3.1..HEAD -- src/claude_wayfinder/match/_compose.py src/claude_wayfinder/match/_cells.py
```

**Why this matters:** if `6d5f416` is at-or-after `07eb3dd` **and** both modules are clean
`6d5f416..HEAD`, then **the 74 `6d5f416` rows pass the guard today, unmodified** — a zero-code-change
valid substrate (but sub-100, so only useful combined with the `1.3.1` rows via §4, or as a fallback).

### 3.3 Count post-`07eb3dd` traffic already in the live log
```bash
# rows logged under a dev-SHA that already includes 07eb3dd, i.e. potential guard-clean re-accumulation
git -C <repo> log --oneline 07eb3dd..HEAD   # how far HEAD has moved past the compose change
python -c "import json,os; p=os.path.expanduser('~/.claude/state/dispatch-log.jsonl'); \
from collections import Counter; c=Counter(json.loads(l).get('matcher_version') for l in open(p,encoding='utf-8') if l.strip()); \
print(c.most_common())"
```

**Why this matters:** re-accumulation may **not** mean "wait weeks." The dispatch log is live and the
74 `6d5f416` rows prove dev-stamped traffic already accumulates. If substantial post-`07eb3dd` traffic
already exists, the re-accumulate branch (§7.5) is cheap, not slow.

### 3.4 Harness sanity-check — prove the two-version rig actually swaps `_compose.py`
The §4 design is HEAD-vs-tag over identical caller-label + catalog inputs (label- and catalog-drift
invariant by construction — both runs vary *only* `_compose.py`), so the old catalog-drift null-test is
moot. What must be proven instead is that the two-version rig is wired correctly: that importing
`compose_route` from a `v1.3.1` worktree genuinely differs from HEAD's on at least one synthetic input
that exercises the `07eb3dd` code path. **Sanity check:** construct a minimal input that the `07eb3dd`
diff should flip, run it through both versions, and confirm they disagree. If they agree on an input
that `07eb3dd` should change, the rig is loading the same `_compose.py` twice (a common failure when
Python caches the module) and every "unaffected" verdict below would be a false negative. Only proceed
to §4 once the rig demonstrably distinguishes the two versions.

---

## 4. The elegant unblock — HEAD-vs-tag compose comparison (label- and catalog-invariant)

The guard's file-diff is a **coarse proxy** for the invariant it actually protects: *does the
`07eb3dd` `_compose.py` delta change any corpus row's compose decision?* A 4+/4- change may touch
**zero** rows' decisions. If so, the logged evidence is HEAD-valid and the guard is a **false positive
for this corpus**.

**The check must be HEAD-vs-tag, NOT replay-vs-logged.** The naive design (replay HEAD compose and
compare to the logged decision) is confounded and must be avoided:
- The production shadow decision was produced by `compose_route` driven by the **caller's** dispatch
  labels (`_main.py:77`, `:85`–`:88`, `:95`), against the catalog live at dispatch time. A
  replay-vs-logged mismatch could therefore come from **caller-label input drift** or **catalog drift**,
  not from the `07eb3dd` code change — and the design cannot tell them apart.
- (For completeness: routing off *gold* labels instead is even worse — only 120 of 245 rows are
  gold-labeled, and gold ≠ the caller labels production actually used.)

**The label-/catalog-invariant design:** run `compose_route` (the production composer,
`_compose.py:296` — the actual thing `07eb3dd` changed) **twice** over each row, using **identical
inputs** both times, varying **only** `_compose.py`:
- **Inputs (identical for both runs):** the row's **own logged caller labels**
  (`input.{domain,posture,confidence,area_span}` — present on all 245 rows) built into a `Labels`
  object, plus lexical scores from `build_features` + `score_entries` against **one fixed catalog**.
- **Run 1:** import `compose_route` from a `v1.3.1` worktree (`git worktree add`, or the row's own
  resolvable `matcher_version` as the per-row baseline).
- **Run 2:** import `compose_route` from HEAD.
- **Compare** the two `agent`/`decision`/`posture_routed` results. **Agreement ⇒ the row is unaffected
  by `07eb3dd` and is HEAD-valid** — regardless of whether either run matches the logged decision, and
  regardless of catalog drift (both runs used the same catalog) or label drift (both used the same
  caller labels). Disagreement ⇒ exclude the row (genuinely stale under HEAD).

This is **not** the confounded vehicle: it does **not** use `run_supplied_compose` (a divergent copy
`07eb3dd` never touched — §2.3) and it does **not** use gold labels. If §3.2 confirms `07eb3dd` is the
**only** `_compose.py` change in the window (and `_cells.py` unchanged), the per-row baseline collapses
to a single pre-`07eb3dd`-vs-HEAD comparison, and even `unknown`-stamped rows can be assessed (their
stamp is irrelevant — what matters is whether the `07eb3dd` code path touches their caller-label
inputs). `labels` dict passed to the composer = **each row's own caller labels** (built per row), the
**same explicit set for both runs**; no `dict[int, GoldLabel]` is involved.

**KC computation is untouched.** KC-1..KC-5 **still use the logged shadow decisions** (the in-situ
production property the parent plan §4.1 requires). The HEAD-vs-tag comparison produces only *evidence*
that the logged decisions are still what HEAD would produce; it never feeds a KC number. This is a
provenance check, not a recomputation — conflating the two would violate §4.1.

**Payoff.** If `07eb3dd` changes 0 rows, the entire resolvable-stamp corpus (up to 236 rows = 245
minus the 9 `unknown`, or all 245 under the collapsed single-comparison case) is HEAD-valid — the
guard was a pure false positive. If it changes K rows, exclude those K and keep the rest — far better
than dropping all 162 `1.3.1` rows on a coarse file-diff. The 120 gold labels rejoin by `corpus_id`
(§2.5) against whichever rows survive. And the **same per-row HEAD-vs-tag check is the durable
standing-process fix** (§8): it replaces the file-diff proxy that produced this false block.

**Guard-exception shapes (pick in §7.3):**
- **(a) Narrow the guard to the HEAD-vs-tag check.** Replace the **entire two-part provenance model** —
  **both** the one-consistent-version gate (check 1, `shadow-kc-report.py:143`–`:153`) **and** the
  module file-diff (check 2, `:181`–`:210`) — with per-row "HEAD `compose_route` agrees with the row's
  baseline-version `compose_route` on identical caller-label + catalog inputs." **Dropping check 1 is
  required for the payoff:** a mixed `1.3.1`+`6d5f416`(+`unknown`) substrate has `len(versions) > 1`
  and check 1 rejects it outright, so narrowing check 2 alone leaves the mixed substrate blocked. Per-
  row HEAD-vs-tag agreement subsumes the intent of both checks (it verifies the actual invariant
  directly, per row, invariant to label/catalog drift), so dropping check 1's stamp-homogeneity
  requirement is sound, not a weakening. Strongest; also the standing fix (§8).
- **(b) Whitelist the specific divergence.** Allow-list `07eb3dd` (or the `v1.3.1..HEAD` compose
  delta) *conditioned on* recorded HEAD-vs-tag equivalence evidence, **and** drop check 1 to admit the
  mixed substrate. Narrower, faster, but leaves the file-diff proxy in place for the next drift.

Option (a) is recommended and dovetails with §8; (b) is the minimal-change fallback if the user wants
issue #485 unblocked with the least new code. **A `posture_routed=False` shortcut is NOT assumed safe:**
`07eb3dd` "widen test-authoring qualifier stems" could plausibly flip a row's `_is_lexically_plausible`
result (`_compose.py:335`) and newly trigger *or* suppress posture-routing, so run the HEAD-vs-tag
comparison over **all** rows rather than pre-excluding any class — it is cheap (245 deterministic rows)
and avoids an unproven assumption. If §3.2 shows `07eb3dd` only edits a branch that `posture_routed=False`
rows never reach, that shortcut may be adopted then, on evidence.

---

## 5. Corpus version-filtering mechanism (needed for any stamp-filtered branch)

No `--matcher-version` flag exists (§2.2). Three shapes, in ascending durability:

- **One-off post-hoc filter script.** A ~15-line `jq`-free Python filter (`shadow-kc-report` already
  loads rows) writing a filtered JSONL. Cheapest; fine if this never recurs. It **will** recur
  (every post-tag `_compose.py`/`_cells.py` change reproduces this), so treat as throwaway only if the
  behavioral-substrate branch (§4) makes stamp-filtering unnecessary.
- **New `--matcher-version` builder flag** (`scripts/corpus/__main__.py` + `builder.py`). Reusable,
  discoverable, testable. Right answer **if** the chosen branch keeps stamp-based filtering as a
  standing need.
- **A `--substrate-check` mode on `shadow-kc-report.py`** that emits the §4 behavioral partition
  directly. Subsumes stamp-filtering entirely and is the natural home if §7.3(a) is chosen.

**Recommendation is conditional on §7:** if §4 lands, prefer the behavioral partition
(`--substrate-check`) and skip the stamp flag; if the user takes a pure stamp-filter branch, add the
reusable `--matcher-version` flag rather than a discarded one-off.

---

## 6. Version bump + tag — load-bearing ONLY for the re-accumulate branch (§7.5)

Issue #499 point 1 asks for a patch bump (`1.3.1 → 1.3.2`) + tag. **Verify-contradictions note for the
user:** a bump+tag does **not** unblock the existing 162 `1.3.1` rows — they still resolve to `v1.3.1`
and still diff dirty against HEAD (§1). A bump+tag only helps **future** traffic stamped at a
HEAD-clean version. So bump+tag is a prerequisite of the **re-accumulate** branch (§7.5), **not** "the
fix." If the user takes the §7.3 behavioral-unblock branch, **no release is required at all**.

If the re-accumulate branch is chosen, follow `docs/maintenance/release-process.md` exactly — this is
a **patch** per the classification table (`release-process.md:25`–`:32`; bug/tooling-only, no schema
change): bump `pyproject.toml` `version` (`release-process.md:16`, `:40`), `.claude-plugin/plugin.json`
`version`, add a `## [1.3.2] - YYYY-MM-DD` CHANGELOG heading in the exact format (`release-process.md:15`);
`src/claude_wayfinder/__init__.py` self-derives (`release-process.md:40`). After merge, maintainer
tags + pushes (`release-process.md:46`–`:58`), which triggers `release.yml`. The **marketplace pin
bump** (`release-process.md:91`–`:108`) and **buildwithclaude sync** (`:114`–`:145`) are downstream
housekeeping with no functional bearing on the guard — track them as a release-hygiene follow-up, not
part of the #485 unblock.

---

## 7. Decision tree (branch on §3 outputs — user picks)

1. **Run §3**, including the §3.4 rig sanity-check. Get: gold-by-version + infra-by-version (§3.1), the
   git-diff exit codes + ancestry (§3.2), the post-`07eb3dd` live-log count (§3.3), and confirmation
   that the two-version rig distinguishes `v1.3.1` from HEAD (§3.4).
2. **Run the §4 HEAD-vs-tag comparison** over all rows → the valid substrate (rows where HEAD and the
   baseline version agree) + its size + its per-KC denominators (especially `infra_deploy`).
3. **If the substrate ≥100 with adequate gold coverage → BEHAVIORAL UNBLOCK (recommended, but gated).**
   **Prerequisite gate:** this branch is only trustworthy once §4 is implemented as the label-/catalog-
   invariant HEAD-vs-tag comparison (using `compose_route` at two versions over identical caller-label +
   catalog inputs — NOT `run_supplied_compose`, NOT gold labels, NOT replay-vs-logged). A non-agreement
   result under the correct design has exactly one cause — the `07eb3dd` code delta — so exclusions are
   attributable. If an executor instead ships the confounded replay-vs-logged design, a mismatch has
   three possible causes (code delta / caller-label drift / catalog drift) and the substrate is
   untrustworthy; in that (undesired) case, stratify mismatches and do NOT diagnose before the design is
   corrected. Then: narrow the guard (§4a) or whitelist with evidence (§4b), rejoin the existing 120 gold
   labels by `corpus_id` (§2.5), run #485's report. **Cheapest path; no release; unblocks #485 directly.**
4. **If gold survival collapses a KC denominator** (e.g. `infra_deploy` drops below a gateable n):
   either **backfill-label** the gap (a *partial* Phase A reopen — draw replacement rows from the
   valid substrate and label only the gap via the existing #483 sample/strip tooling, minding the
   `"x"` exclusive-create footgun — target NEW output paths), **or** report **INSUFFICIENT-DATA** for
   that KC per the §2.6 precedent. Prefer INSUFFICIENT-DATA if the collapsed KC is not a hard block;
   backfill only if a hard-block KC (KC-2) or the overall verdict depends on it.
5. **Re-accumulate** — only if the valid substrate is genuinely too small even combined. Requires the
   §6 bump+tag so new traffic stamps clean; scope its cost from §3.3 (may already be cheap). This
   reopens Phase A labeling for the new rows (new `corpus_id`s). **Last resort.**

**Recommended default:** branch 3 (behavioral unblock) + branch 4's INSUFFICIENT-DATA fallback for any
thin KC. This unblocks #485 with no release and simultaneously motivates the §8 durable fix.

---

## 8. Standing-process question (#499 point 4) → separate sub-issue, do NOT block #485

The file-diff guard is a **coarse proxy**: a single 4+/4- commit landing without a version bump
silently invalidated a corpus that a per-row HEAD-vs-tag check would have shown is mostly still valid.
The durable fix is exactly the §4 check — replace the **whole two-part provenance model** (both the
one-consistent-version gate and the module file-diff, §4a) with per-row "HEAD `compose_route` agrees
with the row's baseline-version `compose_route` on identical caller-label + catalog inputs," optionally
wired as a CI/release gate so drift between corpus accumulation and the flip cannot reopen this gap
unseen.

**Recommendation:** file this as a **separate sub-issue under M15**, dependent on the §4 work but
**not blocking #485**. If §7.3(a) is chosen, the immediate unblock *is* the first increment of this
fix — the sub-issue then covers hardening (CI wiring, test matrix, docs) rather than net-new logic.
Keep the mechanical #485 unblock and the process hardening as distinct issues so the flip is not held
hostage to the durability work.

---

## 9. Proposed sub-issue decomposition (create only on user go-ahead)

| Proposed sub-issue | Scope | Depends on | Notes |
|---|---|---|---|
| **#499a — Triage + HEAD-vs-tag substrate** | §3 mechanical triage + §4 HEAD-vs-tag `compose_route` comparison; produce the valid substrate + gold-join numbers | — | Investigation + a throwaway two-version harness; output drives the §7 branch. No production code change. |
| **#499b — Guard narrowing / unblock** | §4a narrow-guard (or §4b whitelist) + tests; rejoin gold; hand #485 a passing substrate | #499a | The actual #485 unblock. Small if §4b, medium if §4a. |
| **#499c — Backfill labels (conditional)** | Only if §7.4 fires: label the gap via #483 tooling | #499a, #499b | Skip entirely if substrate ≥100 with adequate gold. |
| **#499d — Standing-process hardening (§8)** | CI/release-gate the behavioral check; docs; full test matrix | #499b | Separate; must NOT block #485. |
| **#499e — Re-accumulate + release (fallback)** | §6 bump+tag + version-filtered rebuild + Phase A on new rows | #499a | Last resort; only if substrate too small. |

Keep #499 as the tracking parent; close it when #499b (+ #499c if triggered) lands and #485 is
unblocked. #499d and #499e are independently schedulable.

**Effort estimate (recommended path, branches 3+4):** #499a ~½–1 day (triage + wiring the two-version
HEAD-vs-tag `compose_route` harness, incl. the §3.4 rig sanity-check); #499b ~1 day if §4b whitelist,
~2 days if §4a full narrow-guard with tests in `tests/test_scripts/test_shadow_kc_report.py`; #499c
0–1 day conditional. Re-accumulate (#499e) is multi-day and reopens labeling — avoided on the
recommended path.

---

## 10. Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **`07eb3dd` changes many corpus rows' compose decision** | Unknown until §4 HEAD-vs-tag run | Medium | §4 is the first executed task; branch on its output (§7). A large hit routes to §7.4/§7.5. |
| **§4 built on the confounded replay-vs-logged design** — mismatch conflates code delta with caller-label / catalog drift, or uses `run_supplied_compose` (a divergent copy `07eb3dd` never touched, `_systems.py:1229`–`:1312`) which no-ops on a `_compose.py`-only change | Medium (easy trap) | High (untrustworthy or vacuous substrate) | §4 mandates HEAD-vs-tag via `compose_route` at two versions over identical caller-label + catalog inputs; §3.4 rig sanity-check proves the two versions actually differ; §7.3 prerequisite gate blocks diagnosis on the wrong design. |
| **`infra_deploy` (KC-5) collapses under filtering** — one lost row of n≈3–5 | Medium | High (KC-5 ungateable) | §3.1 measures infra-by-version explicitly; §7.4 backfill or INSUFFICIENT-DATA per §2.6 precedent. |
| **Guard-narrowing weakens a real safety check** — a genuinely stale row slips through | Low | High (KC evidence validates wrong code) | §4a check is *stricter* per-row than the file-diff, not weaker: it verifies per-row HEAD-vs-baseline decision agreement. Whitelist (§4b) is evidence-conditioned, not blanket. |
| **Re-accumulation misdates/clobbers the frozen corpus dir** — builder default output is a hardcoded `2026-06-12` path | Medium | High (lose the frozen 245-corpus) | §7.5 only; always pass explicit `--output-dir` to a NEW dated dir; never rely on the default. |
| **Sample/strip re-run hits `FileExistsError`** — both tools open output in `"x"` mode | Medium (if §7.4 backfill) | Low | Target NEW output paths for every backfill run; never overwrite in place. |
| **`6d5f416` turns out unresolvable / dirty vs HEAD** | Unknown until §3.2 | Low | Baseline resolution falls back per-row; §4 HEAD-vs-tag still works using `v1.3.1` (or the collapsed single pre-`07eb3dd`-vs-HEAD comparison if §3.2 confirms `07eb3dd` is the sole delta). |

---

## 11. Citations & `unverified:` flags

**Verified on disk (this pass, 2026-07-23):** guard internals
(`scripts/shadow-kc-report.py:46`–`:214`); no builder version flag
(`scripts/corpus/__main__.py:219`–`:272`); the §4 vehicle is `compose_route`
(`src/claude_wayfinder/match/_compose.py:296`) driven by caller labels per `_build_shadow_record`
(`src/claude_wayfinder/match/_main.py:77`, `:85`–`:88`, `:95`); `run_supplied_compose` is a divergent
copy (`scripts/corpus/eval/_systems.py:1158`–`:1162`, `:1206`, `:1229`–`:1312`) that must NOT be the
vehicle. **Citation-correction note:** an earlier revision cited `_systems.py:521`/`:699` for the
"scores against supplied catalog, not pinned `catalog_hash`" claim — those lines are the sibling
lexical-runner functions (`run_lexical`/`run_lexical_calibrated`), same pattern but wrong function;
`run_supplied_compose`'s own `load_catalog` is at `:1206`. The claim is moot under the HEAD-vs-tag
design (both runs share one catalog, so catalog drift cancels). Gold set + schema
(`docs/research/2026-07-19-shadow-sample-gold-labels-redacted.jsonl`,
`docs/research/2026-07-19-shadow-sample-gold-labeling.md:33`–`:38`); release runbook + patch
classification (`docs/maintenance/release-process.md:15`–`:16`, `:25`–`:32`, `:40`, `:46`–`:58`,
`:91`–`:108`, `:114`–`:145`); corpus row structure (first row of the corpus JSONL — top-level +
`output`-nested `matcher_version`); parent plan precedents
(`docs/superpowers/plans/2026-07-19-m15-6-shadow-kc-report.md` §4.1, §4.2, §4.4, §8).

**`unverified:` — must be confirmed by an executor with a shell / GitHub read:**
1. **The corpus version distribution** (162 `1.3.1` / 74 `6d5f416` / 9 `unknown`) — from the #499
   reconnaissance, not re-counted here. → §3.1.
2. **All git facts:** that `07eb3dd`/PR #464 is the sole `_compose.py` change since `v1.3.1`, its
   4+/4- size, `_cells.py` being unchanged, and `6d5f416`'s ancestry vs `07eb3dd`/HEAD. No git access.
   → §3.2.
3. **The `corpus_id → matcher_version` gold join** and `infra_deploy`-by-version counts — the ≈79/36/5
   figures in §3.1 are a proportional guess, not a computed join. → §3.1 script.
4. **Whether `07eb3dd` changes any corpus row's compose decision** — unknowable until the §4 HEAD-vs-tag
   comparison runs.
5. **Issue/PR states** (#485 open/blocked, #499 open, PR #464 merged, M15 milestone membership) — taken
   from the #499 brief and parent plan; not checked against GitHub by this sub-agent.
