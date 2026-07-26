---
title: Corpus regeneration process — event-driven manual runbook (issue #516)
touches:
  - docs/maintenance/corpus-regeneration.md                              # NEW — the runbook this plan recommends (the main deliverable)
  - docs/superpowers/plans/2026-07-23-shadow-kc-provenance-guard-remediation.md  # supersession header (§5 stamp-flag branch is moot)
  - docs/superpowers/plans/2026-07-19-m15-6-shadow-kc-report.md          # §10 pre-flip checklist gains a pointer to the runbook
  - docs/research/2026-07-19-shadow-kc-report.md                         # report template gains a corpus-manifest sha256 citation line
  - scripts/corpus/builder.py                                            # OPTIONAL, one field — record exclude-gold-labels-file in generation_params
  - tests/test_corpus/test_builder.py                                    # only if the optional builder.py field lands
  - .github/workflows/corpus-guard.yml                                   # NEW — T1 paths-filter PR-comment advisory (§3)
skills_relevant:
  - python
  - claude-github-tools:github-actions
---

# Corpus regeneration process — event-driven manual runbook (issue #516)

**Status:** SIGNED OFF 2026-07-26 — D1-D5 (§11) resolved, all recommendations accepted. Implementation
authorized on the sub-issue decomposition (§10): #517-522, filed 2026-07-25.

**Issue:** glitchwerks/claude-wayfinder#516, "Implement corpus regeneration/re-stamping process
(follow-up to #510)" (open, no labels, no milestone — verified via the public issue page,
2026-07-25). Follow-up to #510, which delivered drift *detection* only and explicitly deferred
regeneration (`docs/superpowers/plans/2026-07-19-m15-6-shadow-kc-report.md:544-549`).

---

## 0. Recommendation in one paragraph

**Build a documented manual runbook, not automation.** Regeneration should be **event-driven**
(triggered by a change to one of the seven provenance-guarded modules, or by a pre-flip checklist
run), **manual**, and gated into the M15-7 pre-flip checklist that already exists at
`docs/superpowers/plans/2026-07-19-m15-6-shadow-kc-report.md:535-542`. **Add no `--matcher-version`
flag** and **implement no re-stamping mechanism** — the first was made moot when the per-row
provenance partition landed (PR #502), and the second would fabricate the exact provenance the guard
exists to verify. Gold labels are **never re-drawn**, only **incrementally unioned**. Sign-off rides
on the corpus **manifest** that `build_manifest()` already produces
(`scripts/corpus/builder.py:538-576`), committed per regeneration and cited by sha256 in the go/no-go
report. Total new code on the recommended path: **zero required application code, one small CI
advisory workflow (notification only — it does not regenerate anything, §3), and one optional manifest
field.** Separately, file the one change that actually removes the erosion mechanism (§9) as its own
issue — it is a rig change, not a regeneration process, and it carries real design risk.

---

## 1. Verified ground truth

Every claim below was read on disk on 2026-07-25 unless marked `unverified:`.

### 1.1 How `matcher_version` is produced — three stamps, three deployment modes

`_get_matcher_version()` (`src/claude_wayfinder/match/_catalog.py:298-325`) tries three sources in
order:

1. `git rev-parse --short HEAD` with `cwd=Path(__file__).parent` (`:310-318`) → a short dev SHA.
2. `importlib.metadata.version("claude-wayfinder")` (`:321-322`) → the **installed distribution
   version**.
3. Literal `"unknown"` (`:325`).

This exactly explains the corpus's three stamps (162 × `1.3.1`, 74 × `6d5f416`, 9 × `unknown` —
`docs/research/2026-07-19-shadow-kc-report.md:120-123`). CHANGELOG `[1.3.1]` (`CHANGELOG.md:56-70`)
documents the fallback's introduction (#460, PR #461) and states plainly that before it, "essentially
all real consumer traffic (and the enabled plugin venv) logged `matcher_version: "unknown"`" — so the
9 `unknown` rows are pre-#461 traffic and are **permanently unverifiable**; no regeneration recovers
them.

**The dominant production path stamps a release version, not a SHA.** The enabled plugin runs from a
pip-installed tree at
`~/.claude/plugins/data/claude-wayfinder-glitchwerks/venv/Lib/site-packages/claude_wayfinder/` (verified
by glob, 2026-07-25) — no `.git` there, so step 1 fails and step 2 fires. This is the load-bearing
fact for the whole trigger question: **ongoing traffic is stamped with whatever version is installed
in the plugin venv**, and that stamp resolves to a git *tag* in the guard
(`scripts/shadow-kc-report.py:223` tries the bare value then the `v`-prefixed form).

### 1.2 The plugin venv is a release behind (new, actionable)

- Repo version: `1.4.0` (`pyproject.toml:7`), released 2026-07-24 (`CHANGELOG.md:7`).
- Plugin cache carries 1.4.0: `~/.claude/plugins/cache/glitchwerks/claude-wayfinder/1.4.0/` (glob, 2026-07-25).
- **Plugin runtime venv still carries 1.3.1**: `~/.claude/plugins/data/claude-wayfinder-glitchwerks/venv/Lib/site-packages/claude_wayfinder-1.3.1.dist-info/` (glob, 2026-07-25).

So **every dispatch logged today still stamps `1.3.1`**, not `1.4.0`. The `setup-wayfinder` skill's own
trigger list names "Plugin version bumped and re-setup needed" as a reason to re-run it — i.e. the venv
refresh is a known manual step that has not been performed since the 1.4.0 release. This is benign
*today* (CHANGELOG `1.4.0` states "No live routing behavior changes" — `CHANGELOG.md:11-13`), but it is
the single step that determines whether newly-accumulated rows are born fresh or born stale. It belongs
in the runbook as a hard precondition.

### 1.3 What the guard actually excludes, and why

The narrowed guard (`_provenance_partition`, `scripts/shadow-kc-report.py:617-719`) treats
`_compose.py` and the six transitive modules differently:

- `_compose.py` differences are resolved **behaviorally** — `compose_route` is imported at both the
  row's baseline revision and HEAD and the outputs compared (`:685-713`). A `_compose.py` change
  therefore excludes only rows whose decision actually changes.
- The six transitive modules (`_cells.py`, `_decide.py`, `_types.py`, `_match.py`, `_stem.py`,
  `match_filters.py` — `:66-73`) are checked by **raw blob diff** (`_dependency_drift_reason`,
  `:359-384`). Any byte difference excludes **every row** stamped at that baseline, en bloc.

The reason is a documented limitation of the import rig, not a property of provenance:
`_revision_compose_loader` snapshots **only `_compose.py`** to a temp file and imports it
(`:434-467`); its docstring states "Transitive first-party dependencies remain HEAD-loaded and are
guarded separately by raw blob comparison before this loader is used" (`:414-417`). The blob diff is a
**fail-closed compensator for a rig that can only swap one file.** This matters for §9.

### 1.4 Current partition — revision-stamped, not current

As of repo revision `bee6683` (`docs/research/2026-07-19-shadow-kc-report.md:68-69`):

| Partition | Rows | Composition |
|---|---|---|
| Included | 161 | essentially the `1.3.1` bucket (`:115`, `:124-125`) |
| Excluded | 75 | **74** = the legacy `6d5f416` bucket, excluded en bloc by transitive-module drift; **1** = a genuine `compose_route` disagreement (`corpus_id` 57925, on `agent`) (`:116`) |
| Unverifiable | 9 | literal `"unknown"` stamps (`:117`) |

Drift fraction = 84/245 = **34.3%**, above the `_DRIFT_WARNING_THRESHOLD = 0.25`
(`scripts/shadow-kc-report.py:76`), so the stderr warning at `:925-933` is currently firing.

`unverified:` HEAD is now `93316d5`, two commits past `bee6683`. Neither commit message suggests a
guarded-module edit (`56dc70a` pins a `_cells.py` snapshot in *tests*; `93316d5` changes `_kc.py`), but
this planner has no shell and could not run `git diff`. **Re-verify the partition at current HEAD
before acting on any number in this section.**

### 1.5 A rebuild is monotone-additive — with one exception

- `corpus_id` = the 1-based line number in the source dispatch log at generation time
  (`scripts/corpus/builder.py:122-124`, `:255-262`). **The log is append-only with no rotation —
  verified, not assumed:** the Python writer opens the log with mode `"a"`
  (`src/claude_wayfinder/match/_catalog.py:392-395`) and the JS hook writer uses `fs.appendFileSync`
  (`hooks/lib/dispatch-log.js:160`); a grep of `src/claude_wayfinder/` for
  `rotat|truncat|\.bak|maxBytes|unlink|open("w"` finds only session-state file cleanup
  (`_catalog.py:104`, `:124`), never the dispatch log. This matters more than it looks: **any
  rotation or truncation would shift every line number and silently invalidate all 120 gold labels.**
  Because it is append-only, existing rows keep their IDs across rebuilds and **gold labels rejoin by
  `corpus_id` with zero loss**.
- The per-cell cap keeps the **first N in file order** (`builder.py:275`, `:129`) — i.e. oldest-first.
  A cell already at the floor is frozen: new traffic in it never enters the corpus.
- **But only 3 of 21 cells are at the floor of 30** (`delegate|medium|fp=no`, `self_handle|long|fp=yes`,
  `delegate|long|fp=yes` — `docs/research/2026-07-19-shadow-corpus-manifest.json:5-27`); the other 18
  are below floor, with shortfalls up to 29 (`:28-137`). So a plain rebuild at `--sample-floor 30`
  **does** grow the corpus materially; raising the floor unlocks the 3 saturated cells too.
- Corpus was drawn from 1478 organic entries, of which 245 survived the shadow-only + twin-join +
  empty-td filters (`:2-4`, `:141-152`).

### 1.6 Tooling that exists (and does not)

- **No `--matcher-version` flag.** `scripts/corpus/__main__.py:222-272` exposes `--log-path`,
  `--output-dir`, `--sample-floor`, `--profile-only`, `--shadow-only`, `--join-shadow-from-twins`,
  `--exclude-gold-labels-file`, `--manifest-out`. Nothing else.
- **No regeneration or re-stamping code anywhere** — confirmed by the router's pre-run grep over
  `scripts/` and `docs/` for "regenerat"/"re-stamp"/"restamp"/"matcher_version".
- **The default output dir is hardcoded** to `~/.claude/state/wayfinder-corpus/2026-06-12`
  (`scripts/corpus/__main__.py:67-69`) — a rebuild without an explicit `--output-dir` **overwrites the
  frozen 245-row artifact in place** (`write_corpus_artifact` opens `"w"`, `builder.py:526`).
- **The sample tool opens its output in `"x"` exclusive-create mode**
  (`scripts/shadow-sample-for-labeling.py:171`) — every draw needs a new output path.
- **`build_manifest()` already emits a commit-safe provenance record**: sha256 of the artifact,
  `generation_params`, strata and shortfall tables, with all machine-specific paths redacted
  (`scripts/corpus/builder.py:538-576`).

### 1.7 CI cannot regenerate — structurally

- The corpus and the dispatch log are **local-only and never committed**; `write_corpus_artifact`'s
  docstring states "raw `task_description` text IS included in the artifact — this file must remain
  local and never be committed to the repo" (`scripts/corpus/builder.py:511-514`).
- `.github/workflows/` contains only `ci.yml` and `release.yml`; `ci.yml` runs lint, pytest (3.11 /
  3.12), node hook tests, a skill smoke test, actionlint, and manifest validation
  (`.github/workflows/ci.yml:17-177`). A grep of `.github/` for `shadow-kc-report`, `scripts.corpus`,
  and `wayfinder-corpus` returns **no matches**.

A GitHub-hosted runner has no `~/.claude/state/dispatch-log.jsonl` and no corpus artifact. **CI cannot
run the builder, cannot run the KC report, and cannot regenerate anything.** This is not a
cost/benefit judgement; it is a hard constraint.

---

## 2. Reframe — #516 conflates two problems with different fixes

The issue frames regeneration as a cure for drift erosion. The evidence says there are two distinct
problems, and regeneration only addresses one of them.

**Problem A — drift recovery.** Rows excluded because a guarded module changed since their stamp.
Fixed by accumulating and rebuilding from *fresher* traffic. Needs **no new labels** — the existing
120 rejoin by `corpus_id`.

**Problem B — coverage expansion.** KC-4 currently reports `eligible_n: 0` (INSUFFICIENT_DATA) and
KC-5 rests on `slice_n: 7` (`docs/research/2026-07-19-shadow-kc-report.md:84-85`, `:140-141`). The
report states that closing the KC-4 gap — "a gold sample with rows that genuinely exercise
route-changing caller-domain mislabels" — "is a prerequisite for KC-4 to contribute a verdict on any
future re-run" (`:227-230`). **A rebuild does not fix this.** New rows arrive unlabeled; KC-4 and KC-5
denominators only grow if someone labels them.

Two corollaries the plan turns on:

1. **The 34.3% headline overstates ongoing erosion.** Of the 84 drifted rows, 74 are the legacy
   `6d5f416` dev bucket and 9 are pre-#461 `"unknown"`; **exactly one** is a genuine post-hoc compose
   disagreement (`:116`). The corpus is not visibly eroding month over month — it is carrying a
   one-time legacy tail. That argues for "regenerate once, event-driven thereafter," not for standing
   automation.
2. **Erosion is lumpy and event-driven, not linear.** One commit to any of the six transitive modules
   excludes every row stamped before it, in one step (§1.3). A calendar cadence is therefore the wrong
   shape on its own merits, independent of the CI constraint: it would run when nothing changed and
   miss the single PR that mattered.

---

## 3. Q1 — Regeneration trigger

**Recommendation: manual, event-driven, gated into the M15-7 pre-flip checklist. No CI hook that
regenerates, no schedule — a lightweight CI *advisory* is in scope (below).**

**Why not a CI hook that regenerates.** §1.7's constraint is narrower than "CI can't help with
triggers" — it is specifically that CI **cannot regenerate**: runners have no dispatch log and no
corpus artifact to rebuild from. Nothing in §1.7 says CI cannot *notice* a guarded-module change and
say so. **Reject regeneration-in-CI**; the rest of this section is about notification, which §1.7 does
not constrain.

**Why a CI advisory is not redundant with `provenance_drift_fraction`.** The two operate on different
detection surfaces. `provenance_drift_fraction` is **reactive**: it is computed inside
`shadow-kc-report.py` (`:960-965`) and only fires — including its stderr warning at ≥ 25%
(`:76`, `:924-933`) — when an operator runs the report, by which point the excluding commit has
already merged and rows are already stranded. A `pull_request`-triggered, paths-filtered CI check is
**proactive**: it fires at PR-merge time, before the guarded-module change lands and before any
accumulation cycle starts, and needs no corpus, no dispatch log, and no invocation of
`shadow-kc-report.py` at all — it only inspects the PR's diff against the seven guarded module paths
(`_DEPENDENCY_MODULES` + `_COMPOSE_MODULE_PATH`, `scripts/shadow-kc-report.py:64-75`). A report-time
signal that a maintainer must remember to run is not a substitute for a merge-time signal that appears
whether or not anyone remembered. **Adopt** this as T1's concrete mechanism, below — it closes the
"record the intent" gap without requiring a contributor to already know the runbook exists.

**Why not a schedule.** Erosion is event-driven (§2, corollary 2), and regeneration has a real cost
(release + venv refresh + traffic accumulation + labeling — §6). A cadence pays that cost on a timer
regardless of whether anything drifted. **Reject.**

**What to adopt.** The checklist at `docs/superpowers/plans/2026-07-19-m15-6-shadow-kc-report.md:535-542`
already says: confirm none of the six modules changed since the corpus was last regenerated, and treat
the verdict as **provisional** if any did. Keep that as the trigger and give it a runbook to point at
(§6). Concretely, regenerate when **either**:

- **(T1)** a PR merges that touches any of the seven guarded modules
  (`scripts/shadow-kc-report.py:64-75`) — record the intent, act before the next flip decision. The
  mechanism is `.github/workflows/corpus-guard.yml`: a `pull_request` trigger with a `paths:` filter on
  the seven guarded modules, posting a PR comment that links
  `docs/maintenance/corpus-regeneration.md`. This is notification only — it does not run the builder or
  the KC report, and it does not regenerate anything (§1.7 still applies to regeneration itself); or
- **(T2)** a go/no-go run is about to be trusted for an M15-7 decision **and** either
  `provenance_drift_fraction ≥ 0.25` or a guarded module has changed since the last regeneration.

T1 is a *notice*; T2 is the *gate*. Only T2 blocks.

---

## 4. Q2 — Tooling: two firm "no"s

### 4.1 No `--matcher-version` filter flag — and the #499 plan agrees

The #499 remediation plan proposed the flag, but its recommendation was **explicitly conditional**:
"if §4 lands, prefer the behavioral partition (`--substrate-check`) and skip the stamp flag; if the
user takes a pure stamp-filter branch, add the reusable `--matcher-version` flag"
(`docs/superpowers/plans/2026-07-23-shadow-kc-provenance-guard-remediation.md:277-279`).

**§4 landed.** PR #502 replaced the whole-run boolean guard with the per-row `_provenance_partition`
and closed #499 (`docs/research/2026-07-19-shadow-kc-report.md:25-27`, `:106-109`; `CHANGELOG.md:35-48`).
The old "one globally consistent `matcher_version`" gate — the *only* thing stamp-filtering existed to
satisfy — no longer exists in the code (`scripts/shadow-kc-report.py:617-719` resolves each row's stamp
individually). A mixed-stamp corpus is now handled natively.

So the reconciliation is not a judgement call: **by the #499 plan's own conditional, the stamp flag is
moot.** There is no competing mechanism to build. To make sure nobody rebuilds it from the stale
document, add a supersession header to that plan (§7).

**Do not delete the #499 plan.** CLAUDE.md's lifecycle rule (parent issue closed → delete) is
outranked here by the persistence rule: `docs/research/2026-07-19-shadow-kc-report.md:50-51` cites that
plan's §4 as the authoritative design reference for the catalog-drift-cancellation argument. Mark it
superseded; keep the file.

### 4.2 No re-stamping mechanism — ever

`matcher_version` records **which code produced the logged decision**. KC-1..KC-5 consume the *logged*
decisions (the in-situ production property the parent plan requires;
`docs/superpowers/plans/2026-07-23-shadow-kc-provenance-guard-remediation.md:227-230`). Rewriting the
stamp on an existing row would assert that HEAD's code produced a decision that older code actually
produced — it manufactures precisely the provenance the guard exists to verify, and it would do so
silently, in a file that is never committed and therefore never reviewed.

State this as a principle in the runbook, not as a rejected option: **the stamp is evidence, not
metadata. Rows are added, never re-stamped.** #516's title says "regeneration/re-stamping"; this plan
answers "regeneration, and never re-stamping."

### 4.3 What regeneration actually uses — existing flags only

No new flags are needed. The runbook (§6) composes `--log-path`, `--output-dir`, `--sample-floor`,
`--shadow-only`, `--join-shadow-from-twins`, `--manifest-out` — all present at
`scripts/corpus/__main__.py:222-272`.

---

## 5. Q3 — Gold-label implications

**Headline: never re-draw the sample. Draw incrementally and union.**

`draw_sample` groups rows into strata cells, computes per-cell counts, and calls
`rng.sample(cell_rows, counts[key])` (`scripts/shadow-sample-for-labeling.py:102-116`). Both the cell
contents and the per-cell counts change when the corpus grows — so **re-running with the same seed
(483) over a larger corpus produces a different 120 rows, not a superset.** Re-drawing would strand an
arbitrary fraction of the existing 120 human-annotated labels. This is the single most important
operational fact in this section.

The incremental workflow uses only tools that already exist:

1. **Rebuild the KC corpus with no gold exclusion.** Row composition is monotone-additive (§1.5), so
   all 120 existing labels rejoin by `corpus_id` unchanged.
2. **Build a separate labeling pool** with `--exclude-gold-labels-file <current gold file>`
   (`scripts/corpus/__main__.py:261-266`, implemented at `builder.py:248-253`) into its **own**
   `--output-dir`. This yields a pool disjoint from what is already labeled.
3. **Draw from the pool** with `shadow-sample-for-labeling.py` — new `--seed`, new `--output` path,
   sized to the gap you are closing (KC-4 eligibility, KC-5 `infra_deploy`).
4. **Label, then union** the new labels into the gold JSONL. Never replace.

**Footgun — state it in bold in the runbook:** passing the *current* gold-labels file to
`--exclude-gold-labels-file` on the **KC corpus** build drops every gold-labeled row from the corpus
and would silently produce a KC run with near-zero gold coverage. That flag belongs on the **pool**
build only. (Note the 2026-06-12 build did use an exclusion file —
`docs/research/2026-07-19-shadow-corpus-manifest.json:150` — but necessarily a *different, earlier* one,
since 82 of the 161 included rows carry current gold labels
(`docs/research/2026-07-19-shadow-kc-report.md:188-194`). Which file is **not recorded** in the
manifest — see §7.4.)

**Second footgun:** both `shadow-sample-for-labeling.py` (`:171`) and, per the #499 plan, the strip
tool open output in `"x"` exclusive-create mode. Every run needs a fresh output path.

**Targeted vs. proportional draw — open question.** Closing the KC-4 gap needs rows where the caller
domain disagrees with gold *and* the route could actually differ (`scripts/corpus/eval/_kc.py:51-79`
per `docs/research/2026-07-19-shadow-kc-report.md:62-65`). A proportional stratified draw
(`shadow-sample-for-labeling.py:31-48`) is not aimed at that property and may again yield
`eligible_n: 0`. A purposive draw closes the gap faster but breaks the sample's
representativeness — and KC-1/KC-2 rates computed over a purposively-enriched gold set would be
biased. **Recommended handling:** keep the proportional gold set as the KC-1/KC-2/KC-3 substrate and,
if KC-4 still starves, draw a **separate, explicitly-labeled purposive supplement** used **only** for
KC-4/KC-5, with that restriction stated in the report. Flagged as decision **D3** (§11) — this is a
methodology call, not a tooling one.

---

## 6. The deliverable — `docs/maintenance/corpus-regeneration.md`

A runbook, in the same directory as `release-process.md`. Sketch of its steps; each is an existing
command:

**Preconditions (all must hold before accumulating):**

- **P1.** Determine whether any of the seven guarded modules
  (`scripts/shadow-kc-report.py:64-75`) changed since the newest tag:
  `git diff --stat <newest-tag> HEAD -- src/claude_wayfinder/match/_compose.py _cells.py _decide.py _types.py _match.py _stem.py src/claude_wayfinder/match_filters.py`
  If clean, skip to P3 — no release needed.
- **P2.** If dirty, **cut a release first** per `docs/maintenance/release-process.md`. This is the only
  way for future plugin-stamped traffic to carry a version whose tag diffs clean against HEAD (§1.1).
  Without it, every newly-accumulated row is born already-excluded.
- **P3.** **Complete the full publish chain, not just the tag.** The plugin venv installs the version
  the *marketplace pin* points at, so a tag alone does not change what new traffic stamps. The chain is
  **release PR → tag → confirm the `publish-pypi` job in `.github/workflows/release.yml` has succeeded
  for that tag push (`release-process.md:62-69`, "Do not proceed until both `publish-pypi` and
  `github-release` are confirmed green") → dereference the annotated tag (`release-process.md:83-89`)
  → marketplace pin bump in `glitchwerks/claude-plugins` (`:91-99`) → verify the live pin
  (`:101-108`) → re-run `/setup-wayfinder` (`:110-112`)**. The release runbook already states the
  consequence of skipping the last step: "The plugin venv pins an exact version. Without re-running
  `/setup-wayfinder`, consumers stay silently on the old version" (`:112`).

  **Why the publish-pypi check is its own step, not folded into "tag."** `/setup-wayfinder`'s Step 5
  runs `"$VENV_PYTHON" -m pip install "claude-wayfinder==$PLUGIN_VERSION"`
  (`skills/setup-wayfinder/SKILL.md:132-138`) against PyPI itself, not any local or cached copy.
  Bumping the marketplace pin and re-running `/setup-wayfinder` before `publish-pypi` finishes fails
  that install. **Symptom to recognize:** a `pip` error reading `No matching distribution found for
  claude-wayfinder==X.Y.Z` at this stage is not a network problem — it means this check was skipped and
  the wheel is not on PyPI yet.
- **P4.** **Verify the refresh landed** by inspecting the `claude_wayfinder-<version>.dist-info`
  directory name under `~/.claude/plugins/data/claude-wayfinder-glitchwerks/venv/Lib/site-packages/`.
  If it does not match the version from P2, traffic keeps stamping the old version (§1.2) and the whole
  accumulation cycle is wasted. **Do not start accumulating until this check passes.**

  > **Second contradiction with the stale #499 plan.** That plan classes the marketplace pin bump as
  > "downstream housekeeping with no functional bearing on the guard"
  > (`docs/superpowers/plans/2026-07-23-shadow-kc-provenance-guard-remediation.md:296-299`). Under §1.1
  > that is wrong: the pin gates which version the venv installs, which gates what new traffic stamps,
  > which gates whether newly-accumulated rows survive the provenance partition. Record this alongside
  > the §5 stamp-flag correction in the supersession header (§7).

  **Resolved (was `unverified:` — see §13):** `/setup-wayfinder`'s Step 5 always runs
  `pip install "claude-wayfinder==$PLUGIN_VERSION"` against PyPI (`skills/setup-wayfinder/SKILL.md:132-138`);
  it never installs from the version-keyed local plugin cache
  (`~/.claude/plugins/cache/glitchwerks/claude-wayfinder/1.4.0/`). So P3's publish-pypi-before-pin-bump
  ordering is mandatory, not merely tidy, confirming the risk this precondition guards against.

- **P5. Freeze the seven guarded modules for the accumulation window.** P1 checks them *before*
  accumulating, but a merge touching any of them *during* the window re-strands every row accumulated
  so far (§1.3 — exclusion is en bloc by baseline stamp). Either freeze those seven files for the
  duration, or accept that a mid-window merge restarts the cycle. This is the same check T2 re-runs at
  report time (§3); stating it as a precondition is what makes T2 rarely fire.

**Accumulate.** Traffic volume is the rate limiter. Measure, don't guess: run
`python -m scripts.corpus --profile-only` (`scripts/corpus/__main__.py:240-245`, `:293-294`) to see the
organic count, and compare against the previous manifest's `total_organic` (245-row build: 1478,
`docs/research/2026-07-19-shadow-corpus-manifest.json:3`). `unverified:` this planner has no shell and
cannot state the current log size or the accrual rate — the runbook must instruct the operator to
measure it rather than assert a wait time.

**Rebuild.**

```
python -m scripts.corpus \
  --output-dir ~/.claude/state/wayfinder-corpus/<NEW-DATE>/ \
  --shadow-only --join-shadow-from-twins \
  --sample-floor <30 or higher> \
  --manifest-out docs/research/<NEW-DATE>-corpus-manifest.json
```

- **Always pass `--output-dir` to a new dated directory** — the default is hardcoded `2026-06-12` and
  the writer opens `"w"` (§1.6). Never clobber the frozen artifact.
- **Do not pass `--exclude-gold-labels-file`** on this build (§5).
- **Spot-check `corpus_id` stability before trusting the gold join:** pick two or three `corpus_id`s
  from the existing gold file and confirm `sed -n '<N>p' ~/.claude/state/dispatch-log.jsonl` still
  returns the matching row. The log is append-only (§1.5), so this should always pass — which is
  exactly why a failure is worth catching loudly rather than discovering downstream.
- Raising `--sample-floor` is the lever that unfreezes the 3 saturated cells (§1.5). **Caveat — it
  shifts strata composition, not just size.** The 18 below-floor cells are already fully included, so a
  higher floor grows *only* the 3 saturated cells, moving them from ~37% of the corpus (90/245) to a
  materially larger share. KC-1 and KC-2 are computed over the whole sample, so a post-regen
  `shadow_rc` would not be directly comparable to the current 0.6707 for reasons unrelated to routing
  quality — the same traffic-mix confound the report's whole-sample-vs-gated-eligible cut was probing
  (`docs/research/2026-07-19-shadow-kc-report.md:165-181`). The shift is auditable: the manifest's
  `strata_table` records per-cell counts for every build
  (`docs/research/2026-07-19-shadow-corpus-manifest.json:5-27`). Either hold `--sample-floor` at 30 for
  comparability, or raise it deliberately and state the composition change in the report.

**Label the gap** (only if closing Problem B) — §5, steps 2-4.

**Verify and sign off** — §7.

---

## 7. Q4 — Ownership and sign-off

**Recommendation: a manifest-backed provenance chain, not an approval workflow.** The artifact already
exists; connect it.

1. **Every regeneration commits its manifest** to `docs/research/<date>-corpus-manifest.json` via the
   existing `--manifest-out` flag. `build_manifest()` is commit-safe by construction — sha256,
   `generation_params`, strata/shortfall tables, all machine paths redacted, no raw prompt text
   (`scripts/corpus/builder.py:538-576`, `:544-552`).
2. **Every go/no-go report cites that sha256** alongside the run's `provenance_drift_fraction`,
   `repo HEAD`, and the included/excluded/unverifiable counts — extending the existing Methodology
   section pattern (`docs/research/2026-07-19-shadow-kc-report.md:20-43`, `:111-118`).
3. **Sign-off = the repo maintainer merging the PR that adds the manifest and the report.** That is
   already the project's approval mechanism; the manifest makes the corpus behind a verdict auditable
   without ever committing the corpus itself.
4. **Gate, stated as a rule in the runbook:** do not treat a go/no-go verdict as flip-authorizing if
   `provenance_drift_fraction ≥ 0.25`, or if a guarded module changed after the newest committed
   manifest's regeneration date. Provisional verdicts are fine and useful — they just cannot authorize
   a flip. This restates the existing checklist language
   (`docs/superpowers/plans/2026-07-19-m15-6-shadow-kc-report.md:535-542`) as a mechanical check.

**Optional one-field improvement (in scope for sign-off, flagged separately).** `generation_params`
records `sample_floor`, `log_path`, and `filter_rules` (`builder.py:300-316`,
`docs/research/2026-07-19-shadow-corpus-manifest.json:141-152`) but **not which file was passed to
`--exclude-gold-labels-file`**, even though the 2026-06-12 build used one (`:150`). So "re-run with the
same flags" is not reproducible from committed artifacts today. Recording the exclusion file's path (or
its sha256) is a one-line change in `build_corpus`/`build_manifest` plus a test in
`tests/test_corpus/test_builder.py`. **Recommended, but optional** — the runbook works without it.

**Supersession housekeeping (part of this deliverable):** add a header to
`docs/superpowers/plans/2026-07-23-shadow-kc-provenance-guard-remediation.md` recording three
corrections, and keep the file (§4.1):

1. Its §4a narrow-guard branch **landed via PR #502**; its §1 description of `_provenance_guard` at
   `:131-214` no longer matches the code.
2. Its §5 `--matcher-version` branch is **moot** by its own conditional (§4.1 above).
3. Its §6 claim that the marketplace pin bump and buildwithclaude sync are "downstream housekeeping
   with no functional bearing on the guard" (`:296-299`) is **wrong for the pin bump** — it gates the
   installed plugin version and therefore what new traffic stamps (§1.1, §6 P3).

---

## 8. What this plan explicitly does NOT do

- It does not recover the 9 `"unknown"` rows. Nothing can — they predate #461 (§1.1).
- It does not recover the 74 `6d5f416` rows. Their stamp is a dev SHA whose transitive modules differ
  from HEAD; only §9 would recover them.
- It does not automate regeneration. The one CI addition (§3, T1) posts a notice; it never runs the
  builder or the KC report. That is the point.
- It does not make the corpus stop eroding. §9 does.

---

## 9. The durable fix — separate issue, not this one

The erosion mechanism is not "the corpus gets old." It is that **the import rig can only swap one
file**, so six modules' worth of provenance must be handled by a fail-closed blob diff that excludes
rows en bloc (§1.3, `scripts/shadow-kc-report.py:414-417`, `:359-384`).

**The change that removes it:** extend the rig to materialize the whole matcher package at the baseline
revision (e.g. a temporary `git worktree` at that revision) and import `compose_route` from *there*, so
transitive-module differences become per-row **behavioral** comparisons exactly as `_compose.py`
differences already are. If adopted, a `_cells.py` edit would exclude only the rows whose decision it
actually changes — likely a small number — instead of every row stamped before it. This would probably
recover most of the 74 `6d5f416` rows immediately and immunize future corpora.

**Why it is not this issue's scope, and why it is not free.** The current design keeps transitive
dependencies HEAD-loaded *on purpose*: the script builds `Labels`, `ScoredEntry`, and `CatalogEntry`
objects from HEAD modules (`scripts/shadow-kc-report.py:680-682`) and passes them into the
revision-loaded `compose_route` (`:685-702`). Loading the whole package at an older revision means
HEAD-typed objects crossing into baseline-typed code — `isinstance` checks and dataclass field drift
between revisions can break the call or, worse, silently change behavior. Making it sound requires
either a duck-typed boundary or per-revision reconstruction of the inputs, plus an extension of the
existing `_verify_rig_isolation` self-check (`:476-549`) to cover the whole tree. That is a real design
task with a real false-negative risk — **it deserves its own issue, its own review, and its own tests**,
and must not be bundled into a documentation deliverable.

---

## 10. Proposed issue decomposition

`unverified:` — this planner has **no shell and no GitHub write tools**, so the issues and milestone
below **were not created**. CLAUDE.md § Issue Tracking requires them; the router or the user must
create them. Issue/PR states cited in this document (#516 open; #499, #503, #510 closed; PRs #502,
#506 merged) come from the issue page fetched 2026-07-25 and from
`docs/research/2026-07-19-shadow-kc-report.md` / `CHANGELOG.md`, and were **not** re-checked against
the GitHub API.

| Proposed | Scope | Depends on | Size |
|---|---|---|---|
| **#516a — Regeneration runbook** | `docs/maintenance/corpus-regeneration.md` (§6); pointer from the M15-6 plan's §10 checklist; supersession header on the #499 plan (§7); `.github/workflows/corpus-guard.yml` — T1's `pull_request` + `paths:` advisory on the seven guarded modules, posting a PR comment that links the runbook (§3) | — | Docs + one small CI workflow, ~half a day |
| **#516b — Manifest citation in the go/no-go report** | §7 items 2 and 4: report cites corpus sha256 + drift fraction; the "provisional verdict" gate written down | #516a | Docs only, small |
| **#516c — Record the exclusion file in `generation_params`** *(optional)* | §7 optional field + test in `tests/test_corpus/test_builder.py` | — | ~1 line + 1 test |
| **#516d — Execute one regeneration cycle** | Run §6 end to end: P1-P5 (incl. the full publish chain and the module freeze), accumulate, rebuild, commit manifest, re-run the KC report | #516a | Operator time; gated on traffic accrual |
| **#516e — Close the KC-4 / KC-5 coverage gap** | §5 incremental labeling; resolve D3 (proportional vs purposive) first | #516d | Human labeling round |
| **#516f — Rig extension: behavioral check for transitive modules** | §9. **Do not bundle.** | — | Multi-day, design risk |

Suggested milestone: **M15 — matcher-v3-ship-live** for #516a/b/d/e (they gate the flip verdict);
#516c and #516f are independently schedulable.

---

## 11. Open decisions for the user

**Resolved 2026-07-26 — user sign-off, all recommendations accepted:**

- **D1 — Scope of #516. RESOLVED: confirmed.** Runbook + manifest sign-off + a small CI
  notification workflow (§3, T1), zero required application code otherwise. The alternative the issue
  implies (a `--matcher-version` flag plus regeneration automation) is rejected in §3 and §4 with
  reasons; user did not dissent.
- **D2 — Refresh the plugin venv now? RESOLVED: yes.** Run `/setup-wayfinder` now, independent of
  #516d scheduling, and confirm the 1.4.0 marketplace pin landed (`release-process.md:101-108`) —
  §1.2 shows the runtime is a release behind (venv at 1.3.1, repo and cache at 1.4.0).
- **D3 — Proportional vs purposive draw for the KC-4 gap. RESOLVED: proportional + supplement.**
  Proportional draw remains the KC-1/2/3 substrate; a separate, explicitly-labeled purposive
  supplement is drawn for KC-4/KC-5 only, with that restriction stated in the report (§5).
- **D4 — Is #516f in this milestone? RESOLVED: no, defer.** #516f stays filed but unscheduled —
  outside M15, revisit after the flip decision. It is the only change that stops monotone shrinkage,
  but it is also the riskiest (multi-day, real design risk).
- **D5 — Optional manifest field (§7, #516c). RESOLVED: in.** Build it — record the
  `--exclude-gold-labels-file` path (or its sha256) in `generation_params` plus a test in
  `tests/test_corpus/test_builder.py`, per §7's optional-field sketch.

---

## 12. Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Rebuild clobbers the frozen 245-row corpus via the hardcoded default output dir (`__main__.py:67-69`, `builder.py:526`) | Medium | High — irrecoverable evidence loss | Runbook mandates an explicit `--output-dir` to a new dated dir; make it the first bolded line |
| Gold labels stranded by a same-seed re-draw over a grown corpus (`shadow-sample-for-labeling.py:110-115`) | Medium | High — 120 human annotations wasted | §5: never re-draw; incremental union only |
| `--exclude-gold-labels-file` passed on the KC build, dropping every gold row (`builder.py:248-253`) | Medium | High — silent near-zero gold coverage | §5 footgun called out in bold; the flag belongs on the pool build only |
| Regeneration runs before the publish chain completes, so new rows are born stamped at a stale version and immediately excluded | **High** — the venv is stale *right now* (§1.2) and the marketplace pin was previously dismissed as inconsequential | High — full cycle wasted | P1-P4 are hard preconditions ending in a verifiable check (dist-info dir name); the pin-bump correction goes in the #499 supersession header |
| A guarded module is merged **during** the accumulation window, re-stranding everything accumulated so far | Medium | High — cycle restarts | P5 freeze for the window; T2 re-check at report time catches it if the freeze is broken |
| `--sample-floor` raised for volume, silently shifting strata composition and making KC-1/KC-2 non-comparable to prior runs | Medium | Medium — misread as a routing regression/improvement | §6 caveat; hold at 30 for comparability, or state the shift in the report and cite the manifest `strata_table` |
| Accumulation takes longer than expected and blocks the flip | Unknown — no shell to measure | Medium | `--profile-only` measurement step before committing to a cycle; provisional verdicts remain permitted |
| Purposive KC-4 draw biases KC-1/KC-2 | Medium if D3 resolves purposive | Medium | Separate supplement, used only for KC-4/KC-5, restriction stated in the report |
| The stale #499 plan is used as a current spec and the `--matcher-version` flag gets built anyway | Medium | Medium — duplicate/competing mechanism | Supersession header (§7); this is the specific failure #516 Q2 asks to prevent |
| §1.4 partition numbers are stale (computed at `bee6683`, HEAD is `93316d5`) | Medium | Low — planning-only | Runbook step 1 re-runs the report and reads the live numbers before any decision |

---

## 13. Citations and `unverified:` flags

**Verified on disk / by fetch, 2026-07-25.** `_get_matcher_version` three-source ladder
(`src/claude_wayfinder/match/_catalog.py:298-325`); guarded module tuples and drift threshold
(`scripts/shadow-kc-report.py:64-76`); per-row partition (`:617-719`); transitive blob diff
(`:359-384`); rig limitation docstring (`:414-417`); rig isolation self-check (`:476-549`); drift
fraction plumbed into `--json` and stderr (`:167-173`, `:924-933`, `:960-965`); builder corpus_id
semantics and first-N cap (`scripts/corpus/builder.py:122-129`, `:255-262`, `:275`); exclusion filter
(`:248-253`); artifact writer privacy note and `"w"` mode (`:505-530`); manifest builder
(`:538-576`); CLI flag surface and hardcoded default dir (`scripts/corpus/__main__.py:67-69`,
`:222-272`); sample draw and `"x"` mode (`scripts/shadow-sample-for-labeling.py:102-116`, `:171`);
corpus manifest strata/shortfall/generation params
(`docs/research/2026-07-19-shadow-corpus-manifest.json:2-27`, `:28-137`, `:141-152`); go/no-go report
figures, partition table, and KC-4/KC-5 status (`docs/research/2026-07-19-shadow-kc-report.md:22-27`,
`:50-51`, `:62-69`, `:84-85`, `:104-129`, `:140-141`, `:188-194`, `:227-230`); #510 out-of-scope note
and pre-flip checklist (`docs/superpowers/plans/2026-07-19-m15-6-shadow-kc-report.md:526-549`); #499
plan's conditional stamp-flag recommendation and no-flag finding
(`docs/superpowers/plans/2026-07-23-shadow-kc-provenance-guard-remediation.md:85-89`, `:277-279`);
repo version (`pyproject.toml:7`); 1.4.0 and 1.3.1 release notes (`CHANGELOG.md:7-13`, `:35-48`,
`:56-70`); dispatch-log append-only with no rotation (`src/claude_wayfinder/match/_catalog.py:392-395`
mode `"a"`; `hooks/lib/dispatch-log.js:160` `appendFileSync`; grep of `src/claude_wayfinder/` for
rotation/truncation patterns returning only session-state cleanup at `_catalog.py:104`, `:124`);
publish chain and venv-pin footgun (`docs/maintenance/release-process.md:62-69`, `:83-89`, `:91-99`,
`:101-108`, `:110-112`); `publish-pypi` job definition (`.github/workflows/release.yml:40-63`);
`/setup-wayfinder`'s PyPI-only install step (`skills/setup-wayfinder/SKILL.md:132-138`); the #499
plan's marketplace-bump dismissal
(`docs/superpowers/plans/2026-07-23-shadow-kc-provenance-guard-remediation.md:296-299`); CI job list
and absence of corpus jobs (`.github/workflows/ci.yml:17-177`, plus a grep of
`.github/` returning no matches for `shadow-kc-report` / `scripts.corpus` / `wayfinder-corpus`);
installed plugin venv dist version and plugin cache version (globs over
`~/.claude/plugins/`, 2026-07-25); issue #516 body, state, and four open questions (public issue page,
fetched 2026-07-25).

**`unverified:` — confirm before acting.**

1. **Current partition numbers.** 161/75/9 and the 34.3% drift fraction were computed at `bee6683`;
   HEAD is `93316d5`. No shell available to diff guarded modules or re-run the report. → runbook P1.
2. **Whether any guarded module changed between `v1.3.1`/`v1.4.0` and HEAD.** Inferred as "probably
   not" from commit subjects only. → runbook P1.
3. **Whether a `v1.4.0` git tag exists and is pushed.** `CHANGELOG.md:7` records the release; the tag
   itself was not verified. Load-bearing for P2/P3.
4. **Dispatch-log size and traffic accrual rate.** Cannot count lines without a shell; the runbook
   instructs the operator to measure with `--profile-only` rather than asserting a wait time.
5. **Which exclusion file the 2026-06-12 build used.** Inferred to be an earlier gold set (not the
   current 120), because 82 included rows carry current gold labels; the manifest does not record it
   (§7 optional field).
6. **Issue/PR states** (#516 open; #499/#503/#510 closed; PRs #502/#506 merged) — from the fetched
   issue page and committed docs, not re-checked via the GitHub API.
7. **That the plugin venv's staleness is the sole reason today's traffic stamps `1.3.1`.** The
   dist-info directory name is verified; that `git rev-parse` fails in that tree is inferred from it
   being a `site-packages` install (consistent with `CHANGELOG.md:60-69`), not directly observed.
8. ~~What `/setup-wayfinder` can install.~~ **Resolved during this revision:**
   `skills/setup-wayfinder/SKILL.md:132-138` (Step 5) shows it always runs
   `pip install "claude-wayfinder==$PLUGIN_VERSION"` against PyPI — it never installs from the
   version-keyed local plugin cache. So the P3 publish-pypi-before-pin-bump ordering is mandatory, not
   merely tidy (§6 P3).
