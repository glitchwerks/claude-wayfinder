# Project-reviewer findings — spec v1

Run: 2026-05-18, against `docs/superpowers/specs/2026-05-18-telemetry-enrichment-design.md`
at commit `152b955` (spec v1, before the v2 revision).

All findings below were addressed in spec v2 (commit `8938e5c`). Spec v2 was
then submitted to inquisitor, which surfaced a different (and superseding)
class of problems; see `inquisitor.md` for the pivot trigger.

---

## BLOCKING

**[BLOCKING]** `hooks/lib/router-drift-scanner.js` — The spec's Section 2 schema additions treat `bypass` and `skill_mediated` as event types emitted by the drift scanner. They are not. `router-drift-scanner.js` produces `advisory_override`, `self_handle_unaided_invocation`, `needs_more_detail_repeat`, `catalog_degraded_session`, and `skill_mediated_delegation`. The `bypass` and `skill_mediated` events are emitted by the **PreToolUse floor hook** `check-agent-dispatch-pairing.js` — synchronously, at Agent-call time — with schema `{ type: "router_drift", category: "bypass"|"skill_mediated", ts, session_id }`. The scanner's comment at line 11 of `hooks/router-drift-scanner.js` states this explicitly: "`(bypass and stale_dispatch are emitted by the PreToolUse floor hook.)`"

This has three downstream consequences:

1. **The batch shell-out slot** (spec Section 3, step 5) is designed to sit inside `router-drift-scanner.js` after `scanSession()` but before writing. `bypass` events never pass through that slot — they are written synchronously at tool-call time, not at session end. The Node bridge architecture is correct for `advisory_override` enrichment, but wrong for `bypass`/`skill_mediated` enrichment. Enriching those events requires intercepting `check-agent-dispatch-pairing.js`, a PreToolUse hook, not a Stop hook.

2. **The `decision_id` join** is coherent for `advisory_override` (matcher ran; Python wrote a `matcher_decision` row; scanner reads it at Stop time). It is not coherent for `bypass`/`skill_mediated` events because those fire before the session ends — `matcher-decisions.jsonl` for the ongoing session may be partial or empty at that moment.

3. **The enrichment strategy diverges by emitter**. Enriching `advisory_override` is a Stop-hook-time enrichment; enriching `bypass`/`skill_mediated` is a PreToolUse-time enrichment. These require different insertion points and different architectures. The spec treats them as one problem with one solution; they are two problems with different solutions.

Concrete suggestion: split the enrichment work by emission boundary. The scanner retains enrichment for `advisory_override` (it has everything it needs at Stop time: transcript, matcher_decisions.jsonl). For `bypass`/`skill_mediated`, the spec needs to target `check-agent-dispatch-pairing.js` directly — the hook already has access to `conversation_history`, so `raw_input` extraction from history is feasible there. The batch shell-out could be kept but called from that PreToolUse hook instead. Alternatively, treat `bypass`/`skill_mediated` enrichment as a Phase 2 item and scope this PR to `advisory_override` only.

---

**[BLOCKING]** `features` schema — `path_globs_hit` is a scoring-time concept, not an extraction-time concept. The batch CLI (`python -m claude_wayfinder features --batch`) receives only `raw_input` (prompt context) as input, with no catalog. But `path_globs_hit` records which catalog globs fired — that requires matching `raw_input.file_paths` against the catalog's `path_globs` for each entry. Without a catalog, the extractor cannot produce it. The spec's flat-features rationale argues against per-entry storage, but `path_globs_hit` is inherently catalog-coupled: it is "which globs from the catalog matched" not "which paths were in the input."

The same ambiguity applies to `keywords_hit`: the internal `Features.keywords` frozenset is tokens extracted from the task description, but the spec calls this field `keywords_hit`, implying it's the intersection of input tokens and catalog keyword terms. If the intent is raw extracted tokens, the field should be named `keywords_extracted` (or similar) and the distinction made explicit. If the intent is truly "which catalog keywords fired," the extractor needs a catalog.

`build_features()` at `match.py:468-510` returns: `command_prefix`, `agent_mentions`, `keywords` (extracted tokens), `paths`, `extensions`, `tool_mentions` — no glob-hit set, no catalog-filtered keyword-hit set.

Concrete suggestion: either (a) redefine `features` as the raw extractor output (renaming fields to `keywords_extracted`, `paths`, `tools`, `agents`, `command`) — catalog-agnostic and batch-producible — or (b) separate "features" from "match_context" and acknowledge the batch CLI can only produce (a), with (b) requiring a catalog at extraction time. The acceptance criterion 5 ("produces features identical to what the matcher's internal extractor produced") will only hold if both share the same definition. Right now they don't.

---

## CONCERN

**[CONCERN]** `decision_id` — The derivation `{session_id}-{ts}-{sha8(canonical_json(raw_input))}` has a subtle join fragility for `advisory_override`. The `decision_id` is written into `matcher_decision` rows by the Python matcher. The scanner, running at Stop time, needs to look up this id in `matcher-decisions.jsonl`. The spec says the scanner "copies raw_input + features from the paired matcher_decision row." However, the scanner currently identifies `advisory_override` events by parsing the audit line (`🎯 Dispatch → advisory [agent]`) from the transcript, not by reading `matcher-decisions.jsonl`. The join is: find the `matcher_decision` row whose `decision_id` corresponds to the dispatch event seen in the transcript. The `ts` used in `decision_id` is the Python emitter's wall clock time — it may differ from the transcript timestamp by several milliseconds. The only reliable join key between scanner and `matcher_decision` is thus the `sha8(canonical_json(raw_input))` component, which depends on exact `raw_input` shape agreement between what the matcher received and what the scanner reconstructs from the transcript. If the transcript does not preserve the full `raw_input` object verbatim (it likely preserves the hook's `input` payload, not the matcher's stdin), reconstruction will not produce the same hash. The `decision_id` specification needs to describe the scanner's join strategy, not just the construction recipe.

Concrete suggestion: include `decision_id` in the dispatch audit line emitted to the transcript (the `🎯 Dispatch →` line), so the scanner can extract it directly without reconstructing it. This makes the join O(1) per event rather than a file scan, and eliminates the hash-reconstruction ambiguity entirely.

---

**[CONCERN]** `catalog_content_hash` naming collision — `match.py` already computes `_compute_catalog_hash()` (whole-catalog SHA-256 from the catalog JSON) and stores it as `catalog_hash` in existing `_write_log_entry()` output. The spec proposes a new field `catalog_content_hash` on `matcher_decision` rows and also on drift events. If the spec intends this to be the same hash as `_compute_catalog_hash()`, the naming should be harmonized (either migrate existing `catalog_hash` field name to `catalog_content_hash`, or use `catalog_hash` everywhere). If the spec intends `catalog_content_hash` to be a new roll-up computed from per-entry hashes in `build_catalog.py` — a different value — that distinction needs to be explicit, and both fields will coexist with different semantics on the same records.

Per the Explore map item 7, "catalog-wide roll-up does NOT exist yet." But `_compute_catalog_hash()` at `match.py:294` does compute a whole-catalog hash right now. Clarify whether the spec is proposing to (a) reuse this existing hash under a new name, (b) add a separate roll-up hash from `build_catalog.py` with different semantics, or (c) embed the existing hash into the catalog JSON (which would also make it available to Node without a Python spawn).

---

**[CONCERN]** `groups_hit` field — Section 1 schema defines `"groups_hit": []` as "always present as an array; [] when AND-groups not yet shipped." But `groups_hit` is not in the JSON schema example at the top of Section 1. It appears only in the field contracts bullet list. This means either the JSON example is incomplete (omission) or `groups_hit` was added to the contract but not reflected in the canonical schema representation. Given that AND-groups (#135) is a related but separate PR that bumps `feature_schema_version` from 1 → 2, adding `groups_hit` as an always-present field in v1 rows (always `[]`) creates a field whose meaning is version-dependent: in v1 the empty array means "feature not supported" while in v2 it means "no groups fired." This is a semantic overload that queries must handle with `feature_schema_version` filtering anyway — but the field existing in v1 with no real content adds noise. Consider omitting `groups_hit` from v1 entirely and adding it in the AND-groups PR as a new field whose presence implies v2.

---

**[CONCERN]** `advisory_override` lookup timing — The scanner reads `matcher-decisions.jsonl` at Stop time. For `advisory_override`, the `matcher_decision` row should already exist (it was written synchronously during the same session before the Stop hook fired). However, the spec's step 4 says: "look up matcher_decision row by decision_id (file read)." The lookup strategy — scanning the full `matcher-decisions.jsonl` file to find one row — will become expensive as the file grows. At ~100 KB/day, a year's growth is 36 MB. A full-file scan per session-end invocation that had advisory_override events is manageable now but architecturally brittle. At minimum, the spec should document that this is O(N) in the size of `matcher-decisions.jsonl` and flag rotation policy as a prerequisite for keeping the lookup cheap, not just a "nice to have."

---

**[CONCERN]** `feature_schema_version` vs. `matcher_version` — The spec introduces both fields on `matcher_decision` rows, and the versioning table is clear in intent. But for the Node scanner (which reads `matcher-decisions.jsonl` to copy features into drift events), the scanner must know `feature_schema_version` to validate that it's reading features in a schema it understands. This creates an implicit contract: the Node scanner needs to skip or flag `matcher_decision` rows whose `feature_schema_version` it doesn't support. The spec does not describe how the Node scanner handles a row with `feature_schema_version: 2` when the scanner was written against v1. If the scanner copies features blindly (no version check), a v2 row (with AND-groups data) could be spliced into a drift event that the consumer later queries under v1 semantics. The query convention ("MUST filter by `feature_schema_version`") is a consumer responsibility, but the producer (scanner enrichment) should not silently mix versions into the same drift event row. Add a version-check at the scanner's lookup step.

---

**[CONCERN]** No `__init__.py` export specified — `touches:` includes `src/claude_wayfinder/__init__.py`, but the spec does not describe what new public surface is being added there. The Explore map notes the batch CLI follows the existing `argparse subparsers` pattern in `cli.py`, which is not in `__init__.py`. If the `features` batch subcommand is added to `cli.py` (as implied by the implementation order), the `__init__.py` touch is either unneeded or the spec intends to export a new public symbol (e.g., `build_features` or `FEATURE_SCHEMA_VERSION`). Clarify what `__init__.py` change is required and why.

---

## NIT

**[NIT]** The storage growth estimate states "no rotation policy needed in v1" at ~36 MB/year. The migration section notes that the *referential integrity rule* for rotation ("never prune matcher-decisions rows whose decision_id is still referenced") is deferred to whenever rotation is introduced. That rule is correct and the deferral is defensible — but it should appear in the Non-goals section (Section 6) rather than only in the storage section (Section 5) so that whoever implements rotation discovers it in the right place.

**[NIT]** Acceptance criterion 3 requires that every new `advisory_override` row has a `decision_id` that "resolves to a row in `matcher-decisions.jsonl`." This is untestable in unit tests (it depends on runtime file state) and the spec does not describe an integration test fixture or CI strategy for it. The criterion as written is a deployment invariant, not a CI assertion. Consider rewriting it as: "The scanner test suite includes a fixture with a paired `matcher_decision` row and verifies that the enriched `advisory_override` row carries the correct `decision_id` and matching fields."

**[NIT]** Timestamp format: the existing `_write_log_entry` produces `%Y-%m-%dT%H:%M:%S.%f` + `Z` (microsecond precision), while the schema example shows `.123Z` (millisecond precision). The `%f` directive in Python's `strftime` is microseconds (6 digits), not milliseconds (3 digits). The spec should state which precision is intended and whether the format should be truncated before emitting.

---

## Declared-scope completeness

The `touches:` list omits `hooks/check-agent-dispatch-pairing.js`, which must be modified if `bypass` and `skill_mediated` enrichment proceeds as described in Section 2 — this is a material omission given the BLOCKING finding above. All other declared files are genuinely in scope.

---

## Notes for the planner

The two BLOCKING items are structurally linked: they both stem from treating the drift log as a single event stream with a single enrichment strategy, when it's actually written by two hooks with different lifecycles (PreToolUse vs. Stop). Once you resolve where `bypass`/`skill_mediated` enrichment actually happens (PreToolUse context vs. deferred to Stop), the batch shell-out architecture and the `features` schema can be re-evaluated with a clear picture of what's available at each insertion point.

The cleanest separation may be: Stop-hook enrichment for `advisory_override` (scanner already has the full transcript and can read `matcher-decisions.jsonl`); embed `raw_input` directly into the `bypass`/`skill_mediated` events at PreToolUse time (the `conversation_history` is already available in that hook's input); and defer feature extraction to an offline analysis step rather than a PreToolUse spawn. That framing also avoids adding Python startup latency to a synchronous PreToolUse hook that currently never blocks.

The `features` schema ambiguity (catalog-coupled vs. input-only) is worth resolving before writing any code, because it determines whether the batch CLI needs a catalog path argument or can be truly catalog-agnostic. The flat-features design rationale is sound; the field names just need to match what `build_features()` actually produces.
