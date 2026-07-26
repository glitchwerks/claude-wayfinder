# Corpus Regeneration Process

_Maintainer doc — relevant only if you regenerate the shadow-KC corpus used by `scripts/shadow-kc-report.py`
go/no-go reports (e.g. for an M15-7 pre-flip decision). Consumers do not need to follow this runbook._

Authoritative runbook for regenerating the shadow-KC corpus (`scripts/corpus/`) and its gold-label
sample. Every command shown uses the `git -C <repo>` pattern (CLAUDE.md § Shell — never `cd <repo> && git ...`).

**Design basis:** `docs/superpowers/plans/2026-07-25-corpus-regeneration-process.md` (issue #516),
signed off 2026-07-26. That plan is the source of truth for the reasoning behind every step below;
this document is the executable distillation of its §6 (regeneration steps) and §7 (sign-off).

---

## Regeneration is manual and event-driven — not scheduled, not automated in CI

Regenerate the corpus when **either**:

- **(T1) a PR merges that touches any of the seven provenance-guarded modules** —
  `src/claude_wayfinder/match/_compose.py`, `_cells.py`, `_decide.py`, `_types.py`, `_match.py`,
  `_stem.py`, and `src/claude_wayfinder/match_filters.py` (the guarded-module tuple in
  `scripts/shadow-kc-report.py`). CI posts an advisory PR comment linking this runbook when this
  happens (`.github/workflows/corpus-guard.yml`) — the comment is a *notice*, not a gate: it does not
  regenerate anything itself; or
- **(T2) a go/no-go run is about to be trusted for a flip decision** and either
  `provenance_drift_fraction >= 0.25` or a guarded module has changed since the last regeneration.

T1 is a notice; T2 is the gate. Only T2 blocks a flip decision. There is no calendar cadence and no
automated regeneration — CI cannot regenerate the corpus at all (the dispatch log and corpus artifact
are local-only and are never committed; see the design-basis plan §1.7).

**Two firm "no"s, stated as principles, not options:**

- **No `--matcher-version` filter.** The builder has no such flag and none is needed — the per-row
  provenance partition in `scripts/shadow-kc-report.py` already resolves each row's stamp
  individually, so a mixed-stamp corpus is handled natively.
- **The stamp is evidence, not metadata. Rows are added, never re-stamped.** `matcher_version` records
  which code produced a logged decision. Rewriting that stamp on an existing row would assert that
  HEAD's code produced a decision older code actually produced — manufacturing exactly the provenance
  the guard exists to verify, silently, in a file that is never committed and therefore never
  reviewed. Regeneration only ever appends new rows and unions new labels; it never rewrites an
  existing row's stamp.

---

## Preconditions (all must hold before you start accumulating traffic)

- [ ] **P1 — Check whether any guarded module changed since the newest tag.**

  ```bash
  git -C <repo> diff --stat <newest-tag> HEAD -- \
    src/claude_wayfinder/match/_compose.py \
    src/claude_wayfinder/match/_cells.py \
    src/claude_wayfinder/match/_decide.py \
    src/claude_wayfinder/match/_types.py \
    src/claude_wayfinder/match/_match.py \
    src/claude_wayfinder/match/_stem.py \
    src/claude_wayfinder/match_filters.py
  ```

  If clean, skip to P3 — no release is needed.

- [ ] **P2 — If dirty, cut a release first**, per `docs/maintenance/release-process.md`. This is the
  only way for future plugin-stamped traffic to carry a version whose tag diffs clean against HEAD —
  `matcher_version` is stamped from `git rev-parse --short HEAD` (dev tree) or the installed
  distribution version (plugin venv), so a stale tag means every newly-logged row is born already
  excluded.

- [ ] **P3 — Complete the full publish chain, not just the tag.** The plugin venv installs whatever
  version the *marketplace pin* points at — a tag alone does not change what new traffic stamps. Walk
  the full chain in `docs/maintenance/release-process.md`: release PR → tag → confirm `publish-pypi`
  and `github-release` are both green for that tag push → dereference the annotated tag → bump the
  marketplace pin in `glitchwerks/claude-plugins` → verify the live pin → re-run `/setup-wayfinder`.

  **Do not treat the marketplace pin bump as optional housekeeping.** It gates which version the
  plugin venv installs, which gates what version newly-logged traffic stamps, which gates whether that
  traffic survives a future provenance partition. See the corrected account in the supersession header
  on `docs/superpowers/plans/2026-07-23-shadow-kc-provenance-guard-remediation.md`.

  **Symptom to recognize:** a `pip` error reading `No matching distribution found for
  claude-wayfinder==X.Y.Z` at the `/setup-wayfinder` step means `publish-pypi` was not confirmed green
  before the pin bump, not a network problem — `/setup-wayfinder`'s install step
  (`skills/setup-wayfinder/SKILL.md`) always installs from PyPI, never from the local plugin cache.

- [ ] **P4 — Verify the refresh landed.** Inspect the `claude_wayfinder-<version>.dist-info` directory
  name under `~/.claude/plugins/data/claude-wayfinder-glitchwerks/venv/Lib/site-packages/`. If it does
  not match the version from P2, traffic keeps stamping the old version and the whole accumulation
  cycle that follows is wasted. **Do not start accumulating until this check passes.**

- [ ] **P5 — Freeze the seven guarded modules for the accumulation window.** P1 checks them *before*
  accumulating, but a merge touching any of them *during* the window re-strands every row accumulated
  so far — the transitive-module exclusion is en bloc by baseline stamp, not per-row. Either freeze
  those seven files for the accumulation window, or accept that a mid-window merge restarts the cycle.
  T2 (above) re-checks this at report time and will catch a broken freeze, but a broken freeze is what
  T2's provisional-verdict rule exists to catch, not something to rely on routinely.

---

## Step 1 — Accumulate

Traffic volume is the rate limiter. Measure, don't guess:

```bash
"./.venv/Scripts/python.exe" -m scripts.corpus --profile-only
```

Compare the reported organic count against the previous manifest's `total_organic` field
(`docs/research/<previous-date>-corpus-manifest.json`). There is no fixed wait time to target — accrue
until the count and per-cell strata (below) justify a rebuild.

## Step 2 — Rebuild the corpus

```bash
"./.venv/Scripts/python.exe" -m scripts.corpus \
  --output-dir ~/.claude/state/wayfinder-corpus/<NEW-DATE>/ \
  --shadow-only --join-shadow-from-twins \
  --sample-floor <30 or higher> \
  --manifest-out docs/research/<NEW-DATE>-corpus-manifest.json
```

- **Always pass `--output-dir` to a new dated directory.** The default is a hardcoded
  `~/.claude/state/wayfinder-corpus/2026-06-12/` and the writer opens the artifact file in `"w"` mode —
  omitting `--output-dir` silently overwrites a previously frozen corpus artifact.
- **Do not pass `--exclude-gold-labels-file` on this build.** That flag belongs on the labeling-pool
  build only (Step 4) — passing the current gold-labels file here drops every gold-labeled row from
  the KC corpus and would silently produce a run with near-zero gold coverage.
- Because the dispatch log is append-only with no rotation, `corpus_id` (the log's 1-based line
  number at generation time) is stable across rebuilds — existing gold labels rejoin the rebuilt
  corpus by `corpus_id` with zero loss. **Spot-check this before trusting the join:** pick two or
  three `corpus_id`s from the existing gold file and confirm the corresponding line in
  `~/.claude/state/dispatch-log.jsonl` still matches. This should always pass; a failure means
  something is wrong with the log and is worth catching immediately rather than downstream.
- **Raising `--sample-floor` shifts strata composition, not just size.** Some cells are already at the
  floor and are otherwise frozen — new traffic in a saturated cell never enters the corpus at the old
  floor. Raising the floor unlocks those cells, but moves their share of the corpus, so KC-1/KC-2 rates
  from a higher-floor rebuild are not directly comparable to a prior lower-floor run for reasons
  unrelated to routing quality. Either hold `--sample-floor` at the value used by the run you're
  comparing against, or raise it deliberately and state the composition change in the go/no-go report
  — the manifest's strata table records per-cell counts for every build, so the shift is auditable
  either way.

## Step 3 — Never re-draw the gold sample

**The single most important operational fact in this runbook: re-running the labeling draw with the
same seed over a larger corpus produces a different sample, not a superset.** Both the strata
membership and the per-cell counts change as the corpus grows, so re-drawing would strand an arbitrary
fraction of already-labeled rows. If you need to close a coverage gap (e.g. a thin KC slice), draw
**incrementally** and **union** — never re-draw:

1. Rebuild the KC corpus with no gold exclusion (Step 2) — existing labels rejoin by `corpus_id`
   unchanged.
2. Build a separate labeling pool, disjoint from what's already labeled, using
   `--exclude-gold-labels-file <current gold file>` into its **own** `--output-dir`. This is the only
   place that flag belongs.
3. Draw from the pool with `scripts/shadow-sample-for-labeling.py`, using a new `--seed` and a new
   `--output` path sized to the gap you're closing.
4. Strip the draw to a labeler-safe view with `scripts/shadow-strip-for-labeling.py` (drops caller
   labels, matcher decisions, and decision-adjacent metadata, keeping only the corpus id and raw
   input signal) before handing it to a labeler.
5. Label the new rows, then **union** them into the gold JSONL. Never replace the existing file.

**Footgun:** both `shadow-sample-for-labeling.py`'s and `shadow-strip-for-labeling.py`'s output
arguments open their output file in exclusive-create (`"x"`) mode — every run needs a fresh output
path, or it will fail rather than silently overwrite. (Contrast Step 2: the corpus builder's artifact
writer opens in `"w"` mode instead — the opposite failure mode, silent overwrite — which is why Step 2
requires a new dated `--output-dir` on every rebuild.)

**Targeted vs. proportional draw.** Keep the proportional stratified draw as the substrate for the
rate-based KCs (KC-1/KC-2/KC-3). If a route-changing-caller-domain KC (e.g. KC-4) still starves under
a proportional draw, draw a **separate, explicitly-labeled purposive supplement** used **only** for
that KC and its dependents (e.g. KC-5), with that restriction stated in the go/no-go report. Do not let
a purposive supplement contaminate the KC-1/KC-2/KC-3 substrate — it would bias those rates.

## Step 4 — Verify and sign off

Sign-off is a manifest-backed provenance chain, not a separate approval workflow:

1. **Every regeneration commits its manifest** to `docs/research/<date>-corpus-manifest.json` via
   `--manifest-out` (Step 2). The manifest is commit-safe by construction — sha256 of the artifact,
   generation params, strata and shortfall tables, all machine-specific paths redacted, no raw prompt
   text.
2. **Every go/no-go report cites that sha256** alongside the run's `provenance_drift_fraction`, repo
   HEAD, and the included/excluded/unverifiable row counts.
3. **Sign-off is the repo maintainer merging the PR that adds the manifest and the report.** That is
   the project's existing approval mechanism; the manifest makes the corpus behind a verdict auditable
   without ever committing the corpus itself (the corpus contains raw task-description text and must
   never be committed).
4. **Gate (restated from the pre-flip checklist):** do not treat a go/no-go verdict as flip-authorizing
   if `provenance_drift_fraction >= 0.25`, or if a guarded module changed after the newest committed
   manifest's regeneration date. A provisional verdict is fine and useful on its own terms — it just
   cannot authorize a flip.

---

## What this process does not do

- **It does not recover rows stamped `"unknown"`.** Those predate the fallback stamping mechanism and
  are permanently unverifiable — nothing recovers them.
- **It does not recover rows excluded by transitive-module drift.** The guard excludes those en bloc
  by baseline stamp; recovering them requires a rig change to the guard itself (a separate, larger
  design task — tracked independently, not part of this runbook).
- **It does not automate regeneration.** The only CI addition is a notice (T1, above); it never runs
  the corpus builder or the KC report itself.
- **It does not add new labels by itself.** A rebuild grows the corpus with unlabeled rows; closing a
  coverage gap (Problem B, in the design-basis plan's terms) requires the incremental labeling round
  in Step 3.

---

## Quick reference card

```
Preconditions
  [ ] P1  git -C <repo> diff --stat <newest-tag> HEAD -- <seven guarded modules>
  [ ] P2  If dirty: cut a release first (release-process.md)
  [ ] P3  Full publish chain: PR -> tag -> publish-pypi + github-release green ->
          deref tag -> marketplace pin bump -> verify live pin -> /setup-wayfinder
  [ ] P4  Verify plugin venv dist-info matches the new version before accumulating
  [ ] P5  Freeze the seven guarded modules for the accumulation window

1. Accumulate
   "./.venv/Scripts/python.exe" -m scripts.corpus --profile-only
   # compare against previous manifest's total_organic

2. Rebuild (new dated --output-dir; never omit it)
   "./.venv/Scripts/python.exe" -m scripts.corpus \
     --output-dir ~/.claude/state/wayfinder-corpus/<NEW-DATE>/ \
     --shadow-only --join-shadow-from-twins \
     --sample-floor <30 or higher> \
     --manifest-out docs/research/<NEW-DATE>-corpus-manifest.json
   # do NOT pass --exclude-gold-labels-file here

3. Close a coverage gap only if needed (never re-draw the existing sample)
   a. Rebuild with no gold exclusion -> existing labels rejoin by corpus_id
   b. Build a disjoint pool: --exclude-gold-labels-file <gold file> --output-dir <new dir>
   c. Draw from the pool: shadow-sample-for-labeling.py --seed <new> --output <new path>
   d. Strip to a labeler-safe view: shadow-strip-for-labeling.py --output <new path>
   e. Label, then union into the gold JSONL (never replace)

4. Sign off
   - Commit the manifest (docs/research/<date>-corpus-manifest.json)
   - Go/no-go report cites the manifest sha256 + provenance_drift_fraction + repo HEAD
   - Maintainer merges the PR adding both = sign-off
   - Gate: drift >= 0.25, or a guarded module changed since the manifest date
     => provisional verdict only, cannot authorize a flip
```
