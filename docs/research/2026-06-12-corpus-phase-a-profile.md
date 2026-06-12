# Corpus Phase A — Dispatch-Log Profile and Stratified Corpus Construction

**Date**: 2026-06-12
**Issue**: #338 — Matcher v3 corpus phase A
**Branch**: `feat/338-corpus-construction`
**Spec reference**: `docs/superpowers/specs/2026-06-08-semantic-routing-additive-evidence-synthesis.md` §13.2

---

## Summary

The substrate is adequate for phase B labeling.  Phase A produced:

- A **per-field population profile** of the dispatch log (structural only — field names, lengths, rates).
- A **filtered organic set** of 237 entries (265 organic, 28 excluded for empty `task_description`).
- A **stratified corpus** of 168 entries frozen locally.  Only 3 of 17 cells meet the floor of 30.
- A **committed manifest** at `docs/research/2026-06-12-corpus-manifest.json`.

**Stop-gate verdict**: the substrate is marginal but not gate-triggering.  `task_description` is populated in 89.4% of organic entries (237/265); the 28 empty-td entries were excluded, not flagged as a collection bug.  The posture extractor proxy stratifiers fire at < 4% on organic entries and are not viable as strata dimensions — documented below.

**Regeneration note (2026-06-12)**: artifact regenerated after two review fixes to `scripts/corpus/`:
- Fix 1 — `profiler.py` `_compute_flagged_fields` now flags from `nonempty_count / organic_count`
  (populated rate) instead of key-presence rate.  Fields always present but always empty
  (e.g., `command_prefix: ""`) are now automatically caught by the tooling.
- Fix 2 — `builder.py` `_load_organic_entries` now carries the 1-based raw line number of each
  entry through to `corpus_id`.  IDs are now stable join keys to the source log, not compact
  ranks over the filtered list.
- Fix 3 — `builder.py` `_home_relative` now home-relativizes absolute paths before manifest
  serialization.  `artifact_path` and `generation_params.log_path` emit portable `~/…` form.

---

## 1. Log Overview

| Metric | Value |
|---|---|
| Total `matcher_decision` entries | 33,900 |
| Organic (non-empty `session_id`) | 265 |
| Fixture / pre-fix (empty `session_id`) | 33,635 |
| Fixture share | 99.2% |
| Organic time range | 2026-05-29 to 2026-06-12 |

The fixture/organic ratio (99.2% fixture) confirms the #293 finding.  The 265 organic entries represent ~2.5 weeks of post-v1.1.0 session-attribution.

---

## 2. Per-Field Population Profile — `matcher_decision` Entries

### 2.1 Top-level fields (across all 33,900 entries)

| Field | Present | Rate | Non-empty string |
|---|---|---|---|
| `type` | 33,900 | 100% | 100% |
| `ts` | 33,900 | 100% | 100% |
| `session_id` | 33,900 | 100% | **0.8%** (only 265 organic) |
| `input` | 33,900 | 100% | — |
| `output` | 33,900 | 100% | — |
| `catalog_hash` | 33,900 | 100% | 99.6% |
| `matcher_version` | 33,900 | 100% | 99.6% |

**Flagged**: `session_id` is present on all entries but non-empty on only 0.8%.  This is the known v1.1.0 attribution bug (fixture contamination) — the filter handles it.

### 2.2 `input.*` sub-fields (organic entries only, n=265)

| Field | Present | Presence rate | Non-empty | Populated rate | Flagged |
|---|---|---|---|---|---|
| `task_description` | 237 | 89.4% | 237 | 89.4% | |
| `file_paths` | 167 | 63.0% | 133 | 50.2% | |
| `agent_mentions` | 110 | 41.5% | 34 | 12.8% | |
| `command_prefix` | 110 | 41.5% | 2 | 0.8% | **NEAR-EMPTY (populated)** |
| `tool_mentions` | 110 | 41.5% | 70 | 26.4% | |
| `active_skills` | 3 | 1.1% | 0 | 0.0% | *** 100% EMPTY |
| `prompt` | 3 | 1.1% | 3 | 1.1% | *** NEAR-EMPTY |
| `recent_agents` | 3 | 1.1% | 0 | 0.0% | *** 100% EMPTY |

**Notes**:
- `task_description` is 89.4% populated — adequate for corpus use.  The 28 empty-td entries are excluded from the corpus (structural gap, not a collection bug in the session-attribution).
- `command_prefix` is present in 41.5% of entries but non-empty in only 2/265 (0.8%).  **Now automatically flagged by the corrected tooling** (Fix 1).  The field carries a slash-command only when one was typed; most dispatches have no command prefix.
- `active_skills` / `recent_agents` / `prompt`: 3 entries use a legacy or alternate input schema.  Near-empty; ignored for stratification.

### 2.3 `output.*` sub-fields (organic entries only, n=265)

| Field | Present | Presence rate | Non-empty | Populated rate | Flagged |
|---|---|---|---|---|---|
| `decision` | 265 | 100% | 265 | 100% | |
| `confidence` | 265 | 100% | 265 | 100% | |
| `rationale` | 265 | 100% | 265 | 100% | |
| `alternatives` | 265 | 100% | 91 | 34.3% | |
| `disposition_source` | 265 | 100% | 265 | 100% | |
| `skills` | 194 | 73.2% | 63 | 23.8% | |
| `agent` | 159 | 60.0% | 159 | 60.0% | |
| `lanes` | 0 | 0% | 0 | **0%** | *** 100% EMPTY |
| `override_id` | 0 | 0% | 0 | **0%** | *** 100% EMPTY |
| `unassigned_paths` | 0 | 0% | 0 | **0%** | *** 100% EMPTY |

**Flagged (100% empty in organic)**:
- `output.lanes`: present on fixture entries (path-based routing result), 0% on organic.
- `output.override_id`: present on fixture entries, 0% on organic.  No organic overrides in this window.
- `output.unassigned_paths`: co-present with `lanes`, 0% on organic.

These fields cannot serve as strata dimensions.

---

## 3. Decision Distribution (organic, n=265)

| Decision | Count | Share |
|---|---|---|
| `delegate` | 126 | 47.5% |
| `needs_more_detail` | 68 | 25.7% |
| `self_handle` | 35 | 13.2% |
| `advisory` | 33 | 12.5% |
| `self_handle_unaided` | 3 | 1.1% |

`delegate` and `needs_more_detail` together account for 73.2%.  `self_handle_unaided` is rare (3 entries).

### 3.1 `task_description` length bands (organic)

| Band | Range | Count | Share |
|---|---|---|---|
| `empty` | 0 chars | 28 | 10.6% |
| `short` | 1–49 chars | 128 | 48.3% |
| `medium` | 50–199 chars | 9 | 3.4% |
| `long` | 200–499 chars | 87 | 32.8% |
| `very_long` | 500+ chars | 13 | 4.9% |

Notable bimodal shape: most entries are either very short (slash-command dispatches, single-line instructions) or long (full task briefs pasted into the CLI).  The `medium` band (50–199) is near-empty (3.5%).

---

## 4. Proxy Stratifier Assessment

### 4.1 Posture extractor signals (E1–E12, organic entries)

Run offline using `claude_wayfinder.posture` extractors on `task_description` + `file_paths`:

| Signal | Fired | Rate |
|---|---|---|
| `extract_vcs_artifact_ref` (E3) | 9 / 265 | 3.4% |
| `extract_prose_failure_mention` (E12) | 5 / 265 | 1.9% |
| `extract_command_prefix` (E8) | 2 / 265 | 0.8% |
| `extract_spec_plan_path` (E4) | 1 / 265 | 0.4% |
| `extract_stacktrace_block` (E1) | 0 / 265 | 0.0% |
| `extract_test_failure_output` (E2) | 0 / 265 | 0.0% |

**Finding**: posture extractors fire at < 4% on organic entries.  They are **not viable as strata dimensions** — any cell they define would be trivially small.  Reported per the issue-338 design instruction: "if you use proxy stratifiers, record in the report that sample design correlates with systems-under-test".

Since proxy stratifiers are not used, there is no sample/system correlation to record.  Gold labels (phase B) remain fully independent.

### 4.2 Stratification design decision

Stratification uses **observable log fields only** (per spec §13.2):

| Axis | Values | Rationale |
|---|---|---|
| `decision_band` | 5 (delegate, needs\_more\_detail, self\_handle, advisory, self\_handle\_unaided) | Primary routing outcome; always populated (100%) |
| `td_length_band` | 4 non-empty bands (short, medium, long, very\_long) | Structural proxy for task complexity and prompt type |
| `file_paths_present` | bool | Presence of file-path context; affects posture extractor firing rates |

3-axis product = up to 5 × 4 × 2 = 40 cells.  Only 17 cells are observed in the organic corpus.

---

## 5. Filter Rules

Applied in order (see `scripts/corpus/builder.py`):

1. **Include** `type == matcher_decision`
2. **Include** `session_id` non-empty (organic only)
3. **Exclude** `task_description` empty or absent (28 entries removed)
4. **Cap** at first 30 entries per `(decision_band × td_length_band × file_paths_present)` cell (ordering-based, deterministic)

**Filter removed-entry counts**:

| Rule | Removed |
|---|---|
| Non-`matcher_decision` types | 7,260 entries (other event types) |
| Fixture (empty `session_id`) | 33,635 entries |
| Empty `task_description` | 28 entries |
| **Total excluded** | **40,923 entries** |
| **Remaining in corpus** | **168 entries** |

---

## 6. Stratified Corpus — Per-Cell Fill

Floor target: **30 per cell**.  3 of 17 cells meet the floor.

| Cell | Organic eligible | In corpus | Shortfall |
|---|---|---|---|
| `needs_more_detail\|short\|fp=no` | 30+ | 30 | 0 (meets floor) |
| `delegate\|short\|fp=yes` | 30+ | 30 | 0 (meets floor) |
| `delegate\|long\|fp=yes` | 30+ | 30 | 0 (meets floor) |
| `self_handle\|long\|fp=yes` | 15 | 15 | 15 |
| `delegate\|long\|fp=no` | 16 | 16 | 14 |
| `advisory\|long\|fp=yes` | 12 | 12 | 18 |
| `advisory\|long\|fp=no` | 8 | 8 | 22 |
| `delegate\|medium\|fp=no` | 7 | 7 | 23 |
| `self_handle\|very_long\|fp=yes` | 5 | 5 | 25 |
| `advisory\|very_long\|fp=yes` | 4 | 4 | 26 |
| `self_handle\|long\|fp=no` | 2 | 2 | 28 |
| `self_handle\|very_long\|fp=no` | 2 | 2 | 28 |
| `self_handle_unaided\|long\|fp=no` | 2 | 2 | 28 |
| `delegate\|very_long\|fp=yes` | 1 | 1 | 29 |
| `advisory\|very_long\|fp=no` | 1 | 1 | 29 |
| `self_handle_unaided\|long\|fp=yes` | 1 | 1 | 29 |
| `advisory\|medium\|fp=no` | 2 | 2 | 28 |

**Worst shortfalls** (cells with 1 entry): `delegate|very_long|fp=yes`, `advisory|very_long|fp=no`, `self_handle_unaided|long|fp=yes`.

**Phase B implication**: no per-cell conclusions are valid for the 14 cells under floor.  Only the 3 cells at floor (≥30 entries) support per-cell metrics.  Aggregate metrics (error correlation, confident-wrong rate) use the full 168-entry corpus.

### 6.1 Cells with zero organic support (not observed)

The following domain × posture combinations are absent from the organic window: any cell involving `medium` length + `file_paths present`, `short` + `fp=no` for non-`needs_more_detail` decisions, any cell involving `empty` td (excluded by filter).  These are structural gaps in the log window, not construction artifacts.

---

## 7. Corpus Artifact

| Property | Value |
|---|---|
| Local path | `~/.claude/state/wayfinder-corpus/2026-06-12/wayfinder-corpus.jsonl` |
| Format | JSONL (UTF-8), one entry per line |
| Entry count | 168 |
| SHA-256 | `98454ca6544181118b7fb4870d3745be3146f56478f9b95c13f3c99ffa6cb090` |
| Fields per entry | original log fields + `corpus_id` (int, 1-based line number in source log) + `stratum` (dict) |

**`corpus_id` semantics**: the 1-based line number of the entry in the source `dispatch-log.jsonl` at generation time.  This is a stable join key — `sed -n '<N>p' dispatch-log.jsonl` recovers the original row.  IDs are NOT dense/sequential; excluded rows (fixture entries, empty-td, other types) still consume line numbers, so gaps are expected and correct.

**Privacy**: the artifact contains `task_description` values (live personal data).  It MUST remain local and MUST NOT be committed to the repository.

Manifest at: `docs/research/2026-06-12-corpus-manifest.json` (aggregate stats only, no raw text).

---

## 8. Tooling

| File | Purpose |
|---|---|
| `scripts/corpus/profiler.py` | Structural field profiler — `field_profile(path)` |
| `scripts/corpus/builder.py` | Corpus builder + manifest — `build_corpus()`, `write_corpus_artifact()`, `build_manifest()` |
| `scripts/corpus/__main__.py` | CLI: `python -m scripts.corpus [options]` |
| `tests/test_corpus/test_profiler.py` | 18 unit tests against synthetic fixtures |
| `tests/test_corpus/test_builder.py` | 34 unit tests against synthetic fixtures |

Tests run on CI with `.[dev]` only (no model2vec dependency).  All 52 corpus tests pass; baseline 1093 → 1108 (+15 new tests for Fix 1, Fix 2, and Fix 3), 14 skipped unchanged.

---

## 9. Phase A Verdict

**Substrate adequate for phase B** with caveats:

1. Only 3 / 17 cells meet the floor of 30.  Per-cell conclusions require phase B to work only on those 3 cells; aggregate metrics use the full 168-entry corpus.
2. The log window is short (~2.5 weeks, post-v1.1.0).  As organic volume accumulates, re-running the builder will extend under-filled cells automatically.
3. Posture proxy stratifiers are not viable — they fire at < 4%.  Gold labels (phase B) are independent of sample design.
4. `self_handle_unaided` is severely underrepresented (3 organic entries total).  Phase B conclusions for this band are not supportable; flag as "insufficient data" in the metrics report.

**Action for phase B**: proceed with gold labeling against the 168-entry corpus.  Report metrics conditioned on the 3 floor-meeting cells; present aggregate metrics over all 168 where the per-cell sample is insufficient.

---

*Report generated by `scripts/corpus/__main__.py` on 2026-06-12.*
*Regenerated 2026-06-12 after review fixes (populated-rate flagging; corpus_id = raw line number; home-relative path redaction).  Organic count updated to reflect log growth between generation runs; corpus artifact (sha256 unchanged) frozen at 168 entries.*
*Regenerated 2026-06-11 after Fix 4 (`_home_relative` now redacts non-home absolute paths to `<external>/<basename>`).  `total_organic` in the manifest ticked from 265 → 271 (6 new organic log entries since prior run); `total_in_corpus` (168) and sha256 are unchanged — the 6 new entries fall in already-capped cells.  The analysis tables in §2–§6 above reflect the corpus-frozen baseline (n=265 organic, 168 in corpus) and are not re-tabulated since the artifact is unchanged.*
*Aggregates only — no raw prompt text in this file.*
