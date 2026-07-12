# Changelog

All notable changes to this project are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.3.1] - 2026-07-11

### Fixed

- **`matcher_version` stamped `"unknown"` on pip-installed builds** (#460,
  PR #461). It was derived from a runtime `git rev-parse --short HEAD`
  lookup, which fails when the package runs from an installed wheel (no
  `.git` in `site-packages`) — so essentially all real consumer traffic
  (and the enabled plugin venv) logged `matcher_version: "unknown"`,
  defeating build attribution for shadow/live telemetry. Fixed by adding
  an `importlib.metadata.version("claude-wayfinder")` fallback between
  the git lookup and the `"unknown"` default: git SHA stays primary for
  dev checkouts, installed builds now stamp the distribution version
  (e.g. `1.3.1`), and `"unknown"` is returned only when both lookups
  fail.

## [1.3.0] - 2026-07-11

Minor release adding shadow-mode dispatch telemetry. **Live routing
behavior is byte-identical to 1.2.0** — every change in this release is
additive, telemetry-only, or internal groundwork. The headline addition is
Matcher-v3's two-axis "Compose" route: the matcher now computes this second
decision alongside the existing lexical decision and logs both, but Compose
does not steer which agent or skill actually gets dispatched. The
live-steering flag flip is deferred to a later release; upgrading to 1.3.0
changes nothing about what gets routed today.

### Added

- **Shadow-mode dispatch telemetry** (M15-2 through M15-5: #427, #428, #429).
  The matcher computes the Matcher-v3 two-axis Compose route alongside the
  live lexical decision on every dispatch and logs both for offline
  comparison, including a `shadow_data` payload and `disposition_source`
  audit field. The live decision returned to the caller is unaffected.
- **`DISPATCH_SHADOW` fail-open env gate** (#457, PR #458). Shadow compute is
  ON by default. Set `DISPATCH_SHADOW` to `0`, `false`, or `no`
  (case-insensitive, exact match) to disable it; any absent, truthy,
  unrecognized, or malformed value resolves to ON. When OFF, shadow compute
  is skipped entirely rather than computed and discarded. Also exposed as
  the `shadow_enabled` plugin `userConfig` field.
- **Four two-axis input fields for `/dispatch`** (M15-8, #431) — `domain`,
  `posture`, `confidence`, and `area_span`, caller-supplied and optional.
  These currently feed shadow telemetry only; they begin steering live
  routes only after the future flag flip.
- **`shadow-summary.py` dispatch-log monitor** (#433, PR #434), for
  inspecting shadow-vs-live agreement from logged dispatch decisions.
- **New discriminators refining the shadow Compose route** — computed and
  logged alongside the live decision, not steering it — test-authoring
  tasks now resolve to `test-implementer` (Branch-3 discriminator, #453); an
  ops read/write tool-shape discriminator in Compose branch 3 (#448,
  PR #449); an ops-scoped GitHub-signal guard in Compose branch 3 (#445,
  PR #447); and an `infra_deploy`/`build` → `code-writer` cell-map gate
  (#364, PR #394).
- **`matcher_version` and `catalog_hash` in dispatch stdout JSON** (#311,
  PR #312).

### Fixed

- **SessionStart pidfile keyed on transient node wrapper instead of the
  nearest Claude Code ancestor** (#441, PR #442).
- **Compose posture-pick could fall through an empty-gate fallback** (#366,
  PR #373).
- **E7 `area_span` diagnose evidence was ungated on `host_condition`** (#347,
  PR #380).
- **Dispatch-log writes leaked across pytest runs** — isolated via an
  autouse conftest fixture (#349, PR #377).
- **`DISPATCH_HOOK_DEBUG` payload dumps** now write to a private `0600`
  directory instead of a world-readable location (#345, PR #379).
- **Health thresholds recalibrated** from the F-1 baseline (#160, PR #317).

### Changed

- Extensive internal Matcher-v3 groundwork — gold-labeled corpus
  construction, scoring-kernel single-sourcing, and encoder-spike
  evaluation — landed offline this release with no runtime effect: encoder
  NO-GO verdicts (PR #354, PR #350, PR #375), corpus construction (PR #341,
  PR #346, PR #348), and scoring-kernel single-sourcing (#389, PR #390;
  #391, PR #392).

## [1.2.0] - 2026-06-03

Minor release adding symmetric morphological normalization to the dispatch
matcher. Trigger terms are now Porter2 (Snowball English) stemmed at both
catalog-build time and dispatch time, so inflected forms of a keyword route
identically to its base form — `implementing` matches `implement`,
`refactored` matches `refactor`, `linting` matches `lint` — without authors
enumerating every inflection. A per-term `no_stem` opt-out preserves verbatim
matching for acronyms, product names, and CLI flags, and a `--check-stems`
collision checker surfaces distinct terms that collapse to the same stem.

### Added

- **Symmetric Porter2/Snowball stemming in the matcher** (#304, PR #306).
  Catalog keyword terms and `keyword_groups` slot terms are stemmed at build
  time; input tokens are stemmed at dispatch time; matching is stem-vs-stem.
  Inflected forms now route to their base term automatically. The scoring
  formula and thresholds are unchanged — only which tokens count as a match.
- **`no_stem` per-term opt-out** (#304). Set `no_stem: true` on a
  `{term, weight}` keyword mapping to match the raw, unstemmed input token
  verbatim — intended for acronyms, product names, and CLI flags (`aws`, `gh`,
  `ps1`) that must not be collapsed by the stemmer.
- **`stemmed_terms` catalog field** (#304). Each catalog entry stores its
  stemmed terms at build time; the addition is back-compatible for existing
  catalog consumers.
- **`--check-stems` collision checker** (#304). `claude_wayfinder catalog build
  --check-stems` emits `STEM_COLLISION` lines to stderr for distinct keyword
  terms from different entries that share a Porter2 stem, so authors can review
  and disambiguate with `no_stem`.

### Changed

- **`snowballstemmer>=2.2` added as a runtime dependency** (#304).

### Documentation

- **Next-stage trigger-algorithm evaluation** (#288, PR #302) —
  `docs/exploration/2026-05-28-trigger-algorithms.md` ranks candidate matcher
  improvements; stemming was the Rank-1 pilot delivered in this release.
- **`dispatch-authoring` skill stemming coverage** (#307, PR #308) — the skill
  now documents symmetric stemming, the `no_stem` opt-out, the `--check-stems`
  pre-flight, and a stemming-collision footgun, consistent with
  `docs/dispatch-authoring-guide.md`.

## [1.1.1] - 2026-06-01

Patch release that makes the `session_id` attribution promised by v1.1.0
actually work in production. After v1.1.0 shipped, `matcher_decision` log
entries were still `session_id`-empty on 100% of real dispatches: the tier-3
PID-keyed session-file walker was structurally broken in production because the
matcher subprocess does not share the Claude Code process ancestry the walker
assumed. This release replaces that approach with a `PostToolUse(Bash)` hook
that reads the session ID directly from the Claude Code hook payload — the one
place it is reliably available — and writes a fully-attributed entry.

Because the hook fires after the Python matcher has already written its own
(session-id-empty) `matcher_decision` entry, both writers now coexist and are
distinguished by an `attribution_source` field. Log consumers and corpus
builders should prefer the hook-attributed entry (`attribution_source:
"post_tool_use_hook"`, which carries a populated `session_id`) and treat the
Python-written entry (no `attribution_source`) as a fallback. This unblocks
using organic dispatch traffic as a labelled-prompt corpus (#288).

### Fixed

- **`session_id` 0% populated on `matcher_decision` entries in production**
  (#299, PR #300). Root cause: the v1.1.0 tier-3 PID-keyed session-file
  attribution could not resolve the Claude Code session from the matcher
  subprocess's process tree in real operation. Replaced with a
  `PostToolUse(Bash)` hook (`hooks/log-dispatch-decision.js`) that fires after
  a real `claude_wayfinder dispatch` command, reads the decision JSON from
  `tool_response.stdout` and the `session_id` from the hook payload, and writes
  a `matcher_decision` entry tagged `attribution_source: "post_tool_use_hook"`.
- **Infinite loop in `parseDecisionFromOutput` hung `node --test` in CI**
  (#299, PR #300). `String.prototype.lastIndexOf("{", -1)` returns `0` (not
  `-1`) for strings beginning with `{`, so the backward JSON-scan loop revisited
  position 0 forever on valid-JSON-without-`decision` and malformed-JSON inputs.
  Fixed with an explicit `if (start === 0) break;` guard. The suite now
  self-terminates in ~2s.

## [1.1.0] - 2026-05-29

Minor release adding auto-population of the matcher's `session_id` field in
`matcher_decision` log entries. Prior to this release, `session_id` was empty
on 100% of dispatched decisions because the field had no populated source.
v1.1.0 ships a three-tier source-of-truth chain (dispatch input JSON →
`CLAUDE_SESSION_ID` env → PID-keyed session file → `""`) and a new SessionStart
/ SessionEnd hook pair that writes per-CC-PID session files so the matcher can
walk its process tree to find the correct session ID even when no env var is
set. Concurrent Claude Code sessions are safe. Adds `psutil` as a runtime
dependency (already present in `pyproject.toml` since v1.0.0, now actively
used by the session-file walker).

Users tracking anthropics/claude-code#59216 (a first-class `CLAUDE_SESSION_ID`
env var) may be able to drop tier 3 (the PID-keyed file approach) once that
issue lands — the tier 2 env-var fallback in this release's chain will pick it
up automatically without any wayfinder update.

### Fixed

- **`session_id` always-empty in `matcher_decision` log entries** (#294,
  PR #295). The matcher's `session_id` field was `""` on every one of 24k+
  logged decisions because `CLAUDE_SESSION_ID` was never set in the matcher
  subprocess environment. Introduces a three-tier resolution chain: dispatch
  input JSON `session_id` field (tier 1, highest priority) → `CLAUDE_SESSION_ID`
  env var (tier 2) → `""` sentinel (tier 3 placeholder, replaced by PR #297).
  Backward-compatible — callers not yet passing `session_id` in dispatch input
  see no change except the field is now populated when the env var is set.
  +7 tests.

### Added

- **SessionStart / SessionEnd hooks write per-CC-PID session files; matcher
  walks process tree to find its CC ancestor** (#296, PR #297). Adds a
  `SessionStart` hook that writes a small JSON file keyed on the Claude Code
  process PID (e.g. `~/.claude/state/sessions/<pid>.json`) containing the
  session ID. The matcher uses `psutil` to walk its own process ancestry until
  it finds a Claude Code PID, then reads the corresponding session file. This
  inserts as tier 3 in the chain established by PR #295 (between the env-var
  tier and the `""` sentinel), so `session_id` is now populated for all
  dispatch decisions in normal operation. Concurrent Claude Code sessions are
  safe because each PID maps to exactly one session. `SessionEnd` hook removes
  the file to avoid unbounded accumulation. Upstream anthropics/claude-code#59216
  (a first-class `CLAUDE_SESSION_ID` env var) is the long-term alternative —
  once that lands, users can skip tier 3 and rely on tier 2 alone. +12 tests.

- **`psutil>=5.9` runtime dependency** (already declared in `pyproject.toml`
  since v1.0.0; this release is its first active consumer — the process-tree
  walker in the session-file resolver).

## [1.0.0] - 2026-05-28

This is the first stable release of `claude-wayfinder`. The deterministic
7-decision matcher and the dispatch catalog schema are now considered stable
contracts — consumers can rely on the decision surface, scoring pipeline, and
catalog fields without expecting breaking changes outside of a future major
version. The headline change motivating the major-version bump is the
`dispatch` mode-detection contract overhaul (#284): `--demo` is now required to
activate fixture mode; the default resolves to the canonical catalog path and
emits `[CATALOG ERROR]` rather than silently falling back to demo output. This
release also ships the `Explore` and `Plan` platform agents as in-package
fixtures (#286) and corrects `path_globs_excluded` to subtractive semantics
(#287).

### Fixed

- **`path_globs_excluded` per-path-subtractive semantics** (#287).
  Previously, if **any** input path matched an excluded glob the entire
  agent's score was zeroed (hard-exclude).  Under the new semantics, a
  matching path simply contributes **0** to that agent's path score;
  other paths in the same input are unaffected.  An agent with five
  input paths, one of which is excluded, now scores on the remaining
  four.  Catalogue authors who need hard-exclude ("never route if ANY
  docs/ path is present") can use a follow-up field in a future release.

### Added

- **Platform agents `Explore` and `Plan` are now in-package fixtures**
  (#286). `claude_wayfinder/fixtures/builtin/` ships `Explore.yml` and
  `Plan.yml` so a fresh install includes both agents in the dispatch
  catalog with no operator configuration. The builtin-agents resolver now
  follows a three-level cascade: explicit `--builtin-agents-dir` flag →
  user directory `~/.claude/triggers/builtin/` (when it exists on disk)
  → bundled fixtures. See `docs/schema.md §8` for trigger weights and
  the conflict-avoidance note on the bare `plan` keyword.

- **`--demo` flag** for `python -m claude_wayfinder dispatch` (and
  `dispatch --batch`). Passing `--demo` opts into the bundled fixture
  prompts and ignores any catalog configuration. This is the only way
  to activate demo mode from Issue #284 onward.

### Changed

- **`dispatch` mode-detection contract** (#284). **Behavior-changing
  for any caller that relied on "no `$DISPATCH_CATALOG_PATH` → demo
  mode".** The new contract:
  - `--demo` passed → demo mode (bundled fixtures, ignores env/catalog).
  - `$DISPATCH_CATALOG_PATH` set and valid → real-catalog mode
    (unchanged).
  - `$DISPATCH_CATALOG_PATH` set but invalid → hard error (unchanged).
  - Neither set, no `--demo` → resolve canonical default
    (`$CLAUDE_HOME/state/dispatch-catalog.json` or
    `~/.claude/state/dispatch-catalog.json`); if it exists →
    real-catalog mode; if absent → `[CATALOG ERROR]` and exit 2.
  - **Demo mode is no longer the implicit default.** Callers that
    depended on the old "no env var → silent demo" path will now see
    `[CATALOG ERROR]` until they either build a catalog at the
    canonical path or pass `--demo`.
  - The internal subprocess entry point
    (`python -m claude_wayfinder.match`) is unaffected — it retains
    the Issue #10 fail-loud contract.

## [0.12.2] - 2026-05-26

Patch release shipping two operator-ergonomics fixes for the
`/setup-wayfinder` and `/dispatch` skills. No schema, catalog, or
matcher-behavior changes.

### Fixed

- **`/setup-wayfinder` failed when `setup-state.json` content was
  interpreted as a shell argument** (#279, PR #280). Step 1's bash
  command now writes the JSON via a heredoc instead of an inline
  string literal, removing the quoting hazard that broke the venv
  rebuild path on some shells.
- **`/dispatch` skill body did not document the canonical catalog
  path, and the `[CATALOG ERROR]` banner was misleading when the env
  var pointed at a non-existent location** (#281, PR #282).
  - `skills/dispatch/SKILL.md` now states the canonical catalog
    location (`~/.claude/state/dispatch-catalog.json`) directly so
    router agents do not have to guess.
  - The `[CATALOG ERROR]` banner emitted by the matcher
    (`src/claude_wayfinder/match/_catalog.py:_emit_catalog_error`)
    now appends the canonical default path and a repair hint
    (`/refresh-catalog`, or send any prompt to trigger
    `refresh-catalog-on-stale.js`). Every catalog-error call site
    benefits without threading a flag through.
  - New test `test_catalog_error_message_names_canonical_default`
    in `tests/test_match/test_catalog.py` locks in the canonical-path
    substring assertion. Existing `_resolve_catalog_path` fail-loud
    behavior (Issue #10) is preserved.

## [0.12.1] - 2026-05-26

Patch release shipping a docs consistency sweep across the canonical
schema, authoring, and matcher-decision surfaces. No code, schema, or
behavior changes — `dispatch/SKILL.md` and `dispatch-authoring/SKILL.md`
ship in the plugin distribution and were updated to remove the stale
`ambiguous` decision (removed in v0.9.0), add the `mixed_content`
decision (added in v0.10.0), and correct decision-count verbiage from
"6" to "7". Several canonical index/reference tables that had silently
lagged behind shipped fields are now in sync with the runtime.

### Fixed

- **`docs/design/trigger-schema.md § 2d` field-reference table** (#268,
  PR #269). Added the missing `keyword_groups` row. The field has been
  shipped since v0.6.0 but the canonical field-enumeration table omitted
  it, so authors scanning § 2d would conclude the field doesn't exist.
- **Decision-ladder drift across canonical docs surfaces** (#270). The
  `ambiguous` decision was removed in v0.9.0 and `mixed_content` was
  added in v0.10.0, but multiple canonical docs still described the old
  ladder. Sweep updates:
  - `docs/schema.md` §3 "Decision types": `mixed_content` documented
    with `lanes[]` / `unassigned_paths[]` output fields.
  - `docs/schema.md` §4 decision-ladder table and pseudocode: now
    reflect the current 6-active-branch ladder including step 3.5.
  - `skills/dispatch-authoring/SKILL.md` §2: `ambiguous` removed,
    `mixed_content` inserted at the correct step (between `self_handle`
    and `advisory`); §5 footguns `ambiguous` references replaced with
    `advisory` outcome for conflict pairs.
  - `skills/dispatch/SKILL.md` frontmatter description and section
    heading updated from "6-decision matcher" → "7-decision matcher";
    decision-branch table extended with the `mixed_content` row.
- **`applicable_agents_intentional` undocumented in canonical index
  tables** (#271). Added rows in `docs/schema.md` §1 catalog-entry
  schema table and `docs/design/trigger-schema.md` §2d field-reference
  table. Field has been shipped since v0.8.0 but only the long-form
  authoring guide documented it.
- **`disposition_source` missing from `docs/schema.md` §3 common-fields
  table** (#272). Added the row enumerating the two valid values
  (`"scored"`, `"override"`) and cross-referencing
  `docs/dispatch-overrides.md`. CHANGELOG for v0.11.0 had committed to
  the field always being present; downstream consumers reading the
  output schema would have missed it entirely.
- **`extensions` dimension counted by `feature_count` but undocumented
  in the dispatch-context schema** (#273). Updated `docs/schema.md` §2
  and §4 plus `README.md` to acknowledge that `file_paths` internally
  derives an `extensions` dimension counted separately from `paths`.
  Affects the ≥ 2 density-floor edge cases; behavior unchanged.
- **`keyword_groups` paragraph missing from the long-form authoring
  guide** (#274). Added to `docs/dispatch-authoring-guide.md`
  schema-reference section, adjacent to `keywords`, with cross-reference
  to the worked example in `trigger-schema.md` §9.10.
- **`health` CLI subcommand family missing from the README CLI
  subcommands list** (#275). Added entries for `health --report`,
  `health drill`, `health top`, `health catalog-status` with
  cross-reference to `skills/router-health/SKILL.md`.

## [0.12.0] - 2026-05-26

Minor release adding the `dispatch --batch` CLI surface for downstream
replay harnesses, fixing two operator footguns in `/router-health`, and
correcting two schema documents that had drifted out of sync with the
runtime. Documentation underwent a large consumer-leakage and docs audit
pass; bulk plan-file lifecycle cleanup removed superseded design
artifacts (the durable design decisions remain in specs and CHANGELOGs).

### Added

- **`dispatch --batch` flag** (#241). Reads NDJSON dispatch contexts from
  stdin (one per line), writes NDJSON decisions to stdout in input order
  with a leading `input_index` field for ordering safety. Catalog is
  resolved exactly once per invocation; blank lines are skipped and
  malformed lines produce per-line error records without crashing the
  batch. Exit 0 on success/partial-success, non-zero on hard errors
  (missing catalog, malformed CLI args). Documented under `--help` and
  in the README. Gives downstream consumers (e.g.
  `claude-configs#703`) a stable batch surface so they no longer have
  to subprocess single-mode N times, import the internal Python API, or
  build on `scripts/replay_mixed_content.py`.

### Fixed

- **`health --report` defaults to `~/.claude/...` paths** (#262). The CLI
  now defaults `--drift-log`/`--dispatch-log`/`--skills-dir`/`--agents-dir`/
  `--plugin-overrides-dir` to the canonical locations under `~/.claude/`
  with env-var override (`ROUTER_DRIFT_PATH`, `DISPATCH_LOG`, etc.). Aligns
  the CLI with the sibling `scripts/analyze-drift-causes.py` so bare
  invocations produce consistent output. Previously, bare
  `claude-wayfinder health --report` silently reported "0 enriched events"
  while the sibling script reported 468 — an operator footgun, not a real
  source-of-truth mismatch.
- **Decision enum sync** (#218, #230, #231). Dropped the stale `ambiguous`
  decision from `schema.md` and `docs/integration.md`; the schema and
  integration docs now reflect the current 7-member set. Affects any
  downstream consumer reading the published schema to validate matcher
  output.
- **Keyword scoring coefficient corrected to 0.5** in `docs/schema.md`
  (#219, #225). Documentation had drifted from the runtime constant.

### Documentation

- Large consumer-leakage audit (#234, #235) plus follow-ups (#236-#250)
  tightening prereqs, jargon, ordering, and quick-start framing across
  `README.md`, `docs/api.md`, `docs/dispatch-authoring-guide.md`,
  `docs/dispatch-discipline.md`, `docs/integration.md`, `docs/schema.md`,
  `docs/design/trigger-schema.md`, and the `router-health` SKILL.
- `docs/release-process.md` relocated to `docs/maintenance/` (#238, #242).
- Dispatch-overrides spec relocated from `docs/superpowers/specs/` to
  `docs/dispatch-overrides.md`; README now explains overrides directly
  (#246, #254).
- Methodology lessons extracted to `docs/design/methodology-lessons.md`
  (#222, #233); sidecar conventions extracted to `docs/schema.md` and
  `docs/design/trigger-schema.md` (#221, #232).
- Closure specs added for #138 (mixed-content disambiguation, #264) and
  #57 (hook opt-in spike — premise invalidated by `/router-health` data
  dependency, #266). Both retained at `docs/superpowers/specs/` as
  durable design evidence.
- Bulk plan-file lifecycle cleanup: deleted shipped plan sets for #143
  and #213 (#224, #229), and the superseded v0.4 bundled-venv plan set
  (#223, #227).

### Chore

- Bumped `@anthropic-ai/claude-code` to 2.1.150 (#259).
- Bumped `softprops/action-gh-release` to v3 (#261).

## [0.11.0] — 2026-05-24

Minor release adding a **deterministic override mechanism** to the dispatch
matcher. When an override rule matches the dispatch context, the matcher
returns the rule's pre-declared `(decision, agent, skills, confidence,
rationale)` verbatim — bypassing scoring and the 7-branch decision ladder —
and tags the output with `disposition_source: "override"` so downstream
tooling can distinguish override-fired decisions from scored ones.

Wayfinder ships the **mechanism only**; rule files are consumer-private per
the public/private boundary established in `#54`. Downstream consumers (the
canonical example being `glitchwerks/claude-configs`) author their own
`dispatch-overrides.json` and point `$DISPATCH_OVERRIDES_PATH` at it — the
follow-up migration is tracked in `glitchwerks/claude-configs#732`.

### Added

- **New `_overrides.py` module** with `load_overrides(path)`,
  `resolve_override(rules, features)`, and `OverridesError` (#213).
  Public surface re-exported from `claude_wayfinder.match.__init__`.
- **`OverrideRule` and `OverrideMatch` dataclasses** (`_types.py`) — frozen,
  Google-style docstrings, three v1 predicates (`command_prefix`,
  `path_globs`, `tool_mentions`), all AND-combined.
- **First-match-wins resolution semantics** by file order; the
  `override-unreachable` audit-catalog rule (NIT) catches the string-identical
  copy/paste footgun. Glob subsumption is intentionally not checked.
- **`disposition_source` field on every matcher decision** —
  `"scored"` for scoring-pipeline outputs, `"override"` for short-circuited
  override matches. Tagged on every return site in `_decide.py` for
  symmetry; downstream tooling can rely on the field always being present.
- **`override_id` field in dispatch-log NDJSON entries** (top-level, sibling
  of `output`/`catalog_hash`/`matcher_version`) — rule id string when an
  override fired, `null` otherwise. Enables cheap NDJSON sweeps to count
  how often each rule fires.
- **`audit-catalog --overrides-path <path>` CLI flag** audits a rules file
  alongside the catalog. Seven new rules: `override-zero-predicates`
  (BLOCKING), `override-unknown-skill` (CONCERN), `override-unknown-agent`
  (CONCERN), `override-unreachable` (NIT, string-identical only),
  `override-load-error` (BLOCKING, CLI-emitted on `OverridesError`),
  `override-duplicate-id` (BLOCKING), `override-tool-case-error` (CONCERN,
  reuses `_CANONICAL_TOOLS_LOWER`).
- **`OverrideRuleFn` registry + `@register_override` decorator** in
  `audit_catalog.py` — parallel registry to existing `RuleFn`/`@register`,
  receives both `list[CatalogEntry]` and `list[OverrideRule]`. Backwards
  compatible: `run_audit(entries)` without the second arg still works.
- **`[DISPATCH WARNING]` staleness check** in `_dispatch.py` — fires when
  `overrides.mtime < catalog.mtime` (the overrides file is older than the
  catalog and may reference renamed/removed agents). Non-fatal; execution
  proceeds.
- **Demo override fixtures** in `src/claude_wayfinder/fixtures/demo-overrides.json`
  (3 rules, one per predicate) + a matching demo prompt in
  `demo-prompts.json`. End-to-end pipeline test runs through `_main.py:main()`.
- **Reviewer-facing spec** at
  `docs/dispatch-overrides.md` — schema,
  predicate vocabulary, resolution order, public/private boundary,
  telemetry shape, audit-rule table, out-of-scope list.

### Changed

- **`_write_log_entry()` signature** gained `override_id: str | None = None`
  (defaults to `None`; existing call sites keep working without
  modification).
- **JSON-parse-error early-return path in `_main.py`** now writes a log
  entry before returning, with `catalog_hash=""` as the sentinel for
  "catalog not loaded; parse failed pre-catalog". Closes a previously-silent
  schema hole that would have tripped any consumer sweeping NDJSON for
  `override_id is null` to count scored decisions.

### Internal

- New tests: `tests/test_match/test_overrides.py` (loader + resolver, 14
  cases), `tests/test_match/test_decide.py` (`disposition_source`
  symmetry), `tests/test_match/test_integration.py` (override short-circuit
  E2E + parse-error log + demo fixture pipeline). `tests/test_audit_catalog.py`
  gained 26 cases covering all 7 override rules (fire + clean paths each).
  `tests/test_cli_dispatch.py` gained override demo-mode + staleness coverage.
- 610 tests passing on `pytest -v --ignore=tests/integration`; `ruff check`
  clean. Demo fixtures pass `audit-catalog` with 0 findings.

## [0.10.0] — 2026-05-23

Minor release adding a new `mixed_content` decision type to the matcher's
decision ladder. This is the C half of the C-then-B plan from `#138`:
the matcher now emits structured per-agent lane breakdowns for
structurally two-handed tasks. The router-side consumer
(`glitchwerks/claude-configs#704`) will fan out across the lanes in a
follow-up; until then, downstream routers can either present the lanes
to the user or fall back to treating the decision like `advisory` (pick
the first lane's agent).

### Added

- **New `mixed_content` decision type emitted when ≥2 agents clamp at 1.0
  on path-disjoint lanes (#210).** Structural mixed-content tasks — where the
  workload is genuinely split between agents (e.g. code-writer for source files,
  doc-writer for docs) — now produce `mixed_content` instead of `advisory`.
  The decision includes a `lanes[]` list (each with `agent`, `score`,
  `matched_paths`, and `skills`) and an `unassigned_paths[]` field for paths
  not claimed by any top-tier agent.  Falls through to `advisory` when
  conditions are not met (paths overlap, only one agent at threshold, or
  tie is keyword-only with no path-glob contribution).  The detection
  epsilon is `_MIXED_CONTENT_SCORE_EPSILON = 0.05`.

### Internal

- Added `scripts/replay_mixed_content.py` — acceptance evidence replay script
  for #210.  Loads the live catalog and ambig-cases fixture and reports which
  cases flip from `ambiguous`/`advisory` to `mixed_content`.  Not a pytest
  test (depends on user-local state); run with `AMBIG_CASES_PATH` pointing
  to the parent checkout's `.tmp/ambig-cases.json`.

## [0.9.0] — 2026-05-22

Minor release removing the `ambiguous` decision branch and reducing the
routing decision surface from 7 to 6 branches. No breaking changes for
consumers that handle `advisory` — which the router already treats as
"pick the top agent" — and no new schema fields.

### Changed

- **Matcher decision surface reduced from 7 branches to 6: `ambiguous` removed
  (#202).** Tie scenarios (multiple agents ≥ 0.5 with gap < 0.2) now emit
  `advisory` with the top-scored agent named and close-scored alternatives
  populated. The tie-vs-marginal distinction is preserved in the `rationale`
  string: the tie case includes `gap=<value>` while the marginal case says
  "match is not conclusive". The `alternatives` list for `advisory` is now
  capped at `n=3` (was `n=2`) to give the tie case room for the full close
  cluster. The `_AMBIGUOUS_MIN` constant and the `"ambiguous"` entry in
  `VALID_DECISIONS` are both removed. Enables the router-side drift-scanner
  counterpart in `glitchwerks/claude-configs#568`.

## [0.8.0] — 2026-05-22

Minor release adding two new schema fields to the trigger and catalog surfaces. `path_globs_excluded` brings explicit path-based exclusion to trigger scoring; `applicable_agents_intentional` gives skill authors a way to document deliberate empty-agent lists without triggering an audit NIT. No breaking changes — existing triggers continue to work without modification.

### Added

- **`path_globs_excluded` — explicit path exclusion in trigger scoring (#24).** The new
  `path_globs_excluded` field accepts a list of fnmatch-style glob patterns. Any catalog
  entry whose active file path matches one of these patterns is dropped from the scored
  pool before additive scoring begins — exclusion wins unconditionally over `path_globs`
  inclusion. Prior to this change, authors achieving path-scoped triggers had to rely on
  scope-by-omission: leave `path_globs` empty and let other fields carry the scoring
  weight, accepting that the entry would float in the pool for every prompt regardless of
  context. Explicit exclusion is more predictable — you can now say "this trigger is
  active everywhere *except* agent definitions" rather than hoping keyword scores do the
  right thing. Both single-star (`agents/*.md`) and double-star (`agents/**/*.md`) forms
  are supported and are additive; include both when you need to exclude files at the root
  of a directory tree as well as in subdirectories. Example — `doc-writer` trigger sidecar
  after migrating from scope-by-omission:

  ```yaml
  path_globs:
    - "docs/**"
    - "**/README.md"
    - "**/CHANGELOG.md"
    - "**/*.rst"
    - "**/*.adoc"
  path_globs_excluded:
    - "agents/**/*.md"
    - "agents/*.md"
  ```

- **`applicable_agents_intentional` — suppress audit NIT for documented-intentional empty
  agent lists (#194).** The catalog audit emits an `empty-applicable-agents` NIT for any
  skill whose `applicable_agents` list is empty, because that configuration means no
  sub-agent will be offered the skill during dispatch. Most of the time the NIT correctly
  flags an authoring gap. For skills that are intentionally router-only or
  interactive-only — prompt skills, setup skills, skills that operate on the router's own
  context — `applicable_agents: []` is correct by design. The new
  `applicable_agents_intentional` field accepts a non-empty rationale string; when present,
  the audit rule is suppressed. The field is accepted in both `CatalogEntry` (compiled
  catalog) and sidecar YAML files, including `~/.claude/triggers/<plugin>/<skill>.yml`
  plugin-override sidecars. If the string is empty or the field is absent, the audit NIT
  fires as before. Example:

  ```yaml
  applicable_agents: []
  applicable_agents_intentional: "Router-only: this skill drives an interactive session and has no sub-agent delegation use case."
  ```

### Internal

- **`build_catalog.py` split into a five-module package** — `build_catalog.py` has been
  refactored into a `build_catalog/` package as Phase 3 of the Python module-size audit
  (#193, tracked in #199, shipped in #204). The public surface is preserved via
  re-exports: `python -m claude_wayfinder build-catalog` and
  `from claude_wayfinder.build_catalog import ...` continue to work without change. This
  is a behavior-preserving refactor with no user-visible effect.

## [0.7.3] — 2026-05-20

Patch release fixing two SessionStart-hook bugs that affected every
Git-Bash-on-Windows user installing the plugin. No matcher, catalog,
or schema changes.

### Fixed

- **`/c/...` POSIX `venv_path` misclassified as BROKEN on Windows.** Git
  Bash on Windows expands `$HOME` to `/c/Users/...`, which `setup-wayfinder`
  was writing verbatim into `setup-state.json`'s `venv_path`. Node's
  `fs.existsSync` does not recognise the `/c/` prefix on Windows, so the
  SessionStart hook (`check-catalog-health.js`) classified every healthy
  venv as BROKEN and emitted a misleading "venv unreachable or corrupt"
  banner on every session. Fixed at both write time (Step 1 of
  `skills/setup-wayfinder/SKILL.md` normalizes via a POSIX `case` block)
  and read time (new `normalizeVenvPath` helper in `hooks/lib/setup-state.js`
  rescues legacy flags without forcing `/setup-wayfinder` re-run). The
  normalization is non-destructive on the in-memory flag, so re-running
  `/setup-wayfinder` remains the canonical fix and downstream callers
  continue to see the original `venv_path` value. (#186, #187)

- **SessionStart banner never reached the user.** All 5 banner sites in
  `hooks/check-catalog-health.js` emitted only via `hookSpecificOutput.additionalContext`,
  which is injected into the model's context — not the terminal. New
  `emitBoth(text)` helper centralizes the dual emit: stdout JSON envelope
  (model) plus stderr line (user). Degraded venv / catalog state now
  surfaces in the terminal at the same fidelity the model sees. (#185, #187)

### Tests

- `node --test hooks/tests/*.test.js` count rises from 27 to 39: 6 new
  units for `normalizeVenvPath`, 1 regression for `readSetupState`
  classifying a legacy `/c/` flag as VALID on Windows, and 5 stderr
  surfacing assertions on degraded states. `runHook` helper extended to
  return stderr.
- Python test counts unchanged (479 pass, no integration-suite changes
  needed — `tests/integration/setup_pipeline.py` uses `pathlib.Path`
  which produces native Windows paths already).

## [0.7.2] — 2026-05-20

Docs-only patch release. Corrects the `keyword_groups` score multiplier
in the authoring-facing docs added in v0.7.1 — they said `0.5 × weight`
when the matcher actually uses `_GROUP_MULTIPLIER = 1.0`, deliberately
distinct from `_KEYWORD_MULTIPLIER = 0.5` so a satisfied weight-1.0
group can solo-decide `delegate`. No matcher, schema, or behavioral
changes.

### Fixed

- **`keyword_groups` scoring multiplier corrected in
  `skills/dispatch-authoring/SKILL.md` and `docs/schema.md`** — the
  schema-fields tables, the § 3 scoring-math bullet, and the worked
  example all now state `1.0 × weight` (matching `_GROUP_MULTIPLIER`
  in `match.py:92`) instead of the `0.5 × weight` shipped in v0.7.1.
  The "(same multiplier as flat keywords)" parenthetical is removed
  everywhere — `_GROUP_MULTIPLIER` is deliberately distinct from
  `_KEYWORD_MULTIPLIER`. The worked example's score column updates
  from `+0.5` to `+1.0` per group fire, with a new paragraph
  explaining the design intent. `docs/design/trigger-schema.md` § 2i
  already had the correct multiplier and is unchanged. (#182)

## [0.7.1] — 2026-05-20

Docs-only patch release. Fixes a Windows footgun in the dispatch
skill body, closes documentation gaps that obscured AND-group triggers
from authoring-facing agents, and codifies the release process in a
tracked runbook. No matcher, schema, or behavioral changes.

### Fixed

- **`skills/dispatch/SKILL.md` now directs callers to the plugin venv's
  interpreter explicitly** — `${CLAUDE_PLUGIN_DATA}/venv/Scripts/python.exe`
  (Windows) or `${CLAUDE_PLUGIN_DATA}/venv/bin/python` (POSIX) —
  instead of bare `python`. Mirrors the pattern already used by
  `skills/router-health/SKILL.md` since v0.7.0. Bare `python` remains
  documented as a shorthand that only works when the venv is activated
  or first on `$PATH`. (#174, PR #175)

### Documentation

- **`skills/dispatch-authoring/SKILL.md` and `docs/schema.md` now
  document `keyword_groups`** (AND-group conjunctive triggers, shipped
  v0.6.0 per #135). Adds the trigger sub-field to the field tables in
  both surfaces, plus a worked scoring-math example, two field-rule
  entries (≥ 2 slots per group; weight ladder), and a footgun for the
  "flat keywords over-fire when terms only make sense together"
  pattern. `docs/design/trigger-schema.md` § 2i was already correct
  and is unchanged. (#179, PR #180)

- **`docs/release-process.md` codifies the release runbook** —
  pre-release checklist, classification table, 12-step sequence,
  7 footguns, rollback procedure, and quick reference card. Modeled
  after `claude-prospector`'s equivalent doc, adapted for wayfinder's
  6-job CI shape, `skill-smoke-ubuntu` runtime gate, the
  currently-manual GH Release step (tracked in #131), and the
  `/setup-wayfinder` re-run requirement. (#178, PR #181)

## [0.7.0] — 2026-05-20

Minor release. Ships the **`router-health`** skill (ported from
`claude-configs` and refactored so its drill-down snippets are real
`claude-wayfinder health` subcommands instead of inline Python
heredocs), plus a bugfix for the colocated agent-sidecar code path
that was bypassing validation and leaving `routable: false` agents
silently inert. No breaking changes.

### Added

- **`router-health` skill** — `/router-health` runs
  `claude-wayfinder health --report` and adds an Analysis section
  (warning-zone drill-down on CI invariants and runtime telemetry)
  plus an Extended Notable Findings section (top dispatched agents,
  top invoked skills, catalog freshness). Env-var path overrides
  for every script flag: `$ROUTER_DRIFT_LOG`, `$DISPATCH_LOG`,
  `$DISPATCH_CATALOG_PATH`, `$ROUTER_SKILLS_DIR`,
  `$ROUTER_AGENTS_DIR`, `$ROUTER_PLUGIN_OVERRIDES_DIR`. (#157, PR #169)

- **`claude-wayfinder health` subcommands** — `drill` (event
  filtering: `--metric {bypass,advisory-override,recent-drift}`),
  `top` (aggregation: `--kind {agents,skills}`), and
  `catalog-status` (catalog mtime age). Plain-text output by
  default, `--json` opt-in for tests and programmatic consumers.
  New `_parse_window("30d" | "Nh")` helper for window-range flags.
  Replaces four inline Python heredocs and a dual-shell invocation
  block previously embedded in `skills/router-health/SKILL.md`,
  making the queries lintable, type-checkable, and unit-tested
  instead of opaque markdown. 28 new unit tests. No behavioral
  regression in `--ci` / `--report` modes. (#170, PR #171)

### Fixed

- **Colocated agent sidecars now validate trigger data through
  `validate_entry`** before merging onto the existing entry —
  mirroring the Pass 3b precedent for plugin-agent sidecars
  (#142). Sidecars carrying out-of-ladder weights, duplicate
  keyword terms, deprecated trigger fields, or whitespace in
  terms are now corrected (with a warning) instead of silently
  passing through. Walker docstring updated to reflect the new
  inline-validation reality. (#151, PR #172)

- **Colocated agent sidecars now write `routable: true`
  defensively** on the matched entry, so an owned agent with
  `routable: false` in its frontmatter plus a colocated
  `*.triggers.yml` no longer stays silently inert to routing.
  Mirrors the L2469 precedent in Pass 3b. (#153, PR #172)

## [0.6.0] — 2026-05-20

Minor release. Ships two net-new user surfaces — the
**`audit-catalog`** static-analysis CLI subcommand and the bundled
**`claude-wayfinder:dispatch-authoring`** knowledge skill — plus the
**AND-group** conjunctive-triggers matcher feature, telemetry
enrichment v2 (bypass-cause taxonomy + analyzer), and assorted
hooks/tooling. No breaking changes.

### Added

- **`audit-catalog` CLI subcommand** — `python -m claude_wayfinder
  audit-catalog` runs catalog-wide static analysis. 12 rules across
  three severity tiers (3 BLOCKING: `weight-not-in-ladder`,
  `whitespace-in-term`, `duplicate-keyword-term` — 7 CONCERN:
  `path-glob-footgun`, `tool-name-case-error`,
  `one-dimensional-triggers`, `unreachable-routable`,
  `conflict-pair`, `excludes-overlap-own-keywords`,
  `source-routable-mismatch` — 2 NIT: `empty-applicable-agents`,
  `duplicate-trigger-set`). Flags: `--catalog`, `--json`,
  `--severity {blocking|concern|nit}`, `--target <substring>`.
  Exit-code contract: 0/1/2/3 = none/NIT/CONCERN/BLOCKING, computed
  from the filtered finding set. `conflict-pair` uses the corrected
  single-sided-asymmetric discriminator rule (disjoint non-empty
  globs still tie on no-path prompts). Companion long-form guide at
  `docs/dispatch-authoring-guide.md`. (#156, PR #164)

- **`claude-wayfinder:dispatch-authoring` bundled skill** —
  matcher-aware authoring and troubleshooting knowledge for trigger
  frontmatter and sidecars, loaded by any agent on `/dispatch-authoring`
  or authoring/troubleshooting keywords. 10 sections covering the
  schema, seven-decision ladder, scoring math, weight ladder
  `{0.25, 0.5, 1.0}`, field rules, footguns (including the clamping
  ceiling and conflict-pair discriminator semantics), authoring +
  tuning + troubleshooting workflows, and the audit-catalog CLI
  pointer. (#156, PR #164; renamed from `frontmatter` in PR #166
  closing #165 — original name was too narrow.)

- **AND-group conjunctive triggers** — new matcher primitive that
  scores only when *all* member terms match the input, not any.
  Schema-additive (`keyword_groups: [{terms: [...], weight: ...}]`),
  catalog builder + match scorer updated, replay test suite covers
  scoring edges. Lets agents narrow their reach to specific
  multi-token phrases without diluting the keyword pool. (#135)

- **Telemetry enrichment v2 — bypass-cause taxonomy** — new
  `hooks/lib/bypass-taxonomy.js` library and
  `hooks/check-agent-dispatch-pairing.js` hook attribute each
  router bypass to a structured cause (`missing_dispatch_pair`,
  `advisory_override`, `self_handle_unaided`, etc.) recorded in
  `~/.claude/state/dispatch-log.jsonl`. Companion analyzer
  `scripts/analyze-drift-causes.py` computes attribution
  histograms over a date range. Postmortem of the v1 attempt
  documented in `docs/superpowers/postmortems/telemetry-enrichment-v1/`.
  (#143, PRs #155, #163)

- **`docs/design/trigger-schema.md`** — durable design-rationale doc
  for the trigger field set, referenced from the dispatch-authoring
  skill and the new audit-catalog guide. (#156)

### Fixed

- `load_catalog()` no longer raises `ValueError("Catalog contains
  zero entries.")` on `{"entries": []}`. Empty catalogs are a valid
  degraded state (fresh checkout pre-build, #506 all-entries-dropped
  path); callers like `audit-catalog` now operate on them without a
  load-time crash. (#156)

- `audit-catalog` reconfigures stdout/stderr to UTF-8 with
  replacement on entry. Windows default cp1252 stdout previously
  crashed `UnicodeEncodeError` on the `↔` glyph in conflict-pair
  entry labels. (#156, fix bundled in PR #164)

### Maintenance

- Plan files for `#135` and `#156` deleted after their parent issues
  closed, per the CLAUDE.md plan-file lifecycle rule. Design
  rationale preserved in PR bodies and commit messages. (#162, #166)

### Known follow-ups (non-blocking)

- `empty-applicable-agents` rule fires on every skill without an
  explicit `applicable_agents: ["*"]`. Rule is correct per spec but
  noisy in practice; candidate for either a rule refinement (require
  the field only on plugin skills) or a catalog-wide convention
  shift toward explicit `["*"]`.
- Slash command `/dispatch-authoring` is long; `/da` or similar may
  be worth picking in a follow-up. No issue yet.

## [0.5.0] — 2026-05-19

Minor release. Adds **sidecar trigger overrides** for all three agent
provenance classes (plugin, user-owned, project-local), so users no
longer need to mutate inline `triggers:` frontmatter in agent `.md`
files — including plugin-shipped agents whose files would be
overwritten on update. Includes one drift-scanner fix and a manifest
cleanup. No breaking changes.

### Added

- **Plugin-agent sidecar overrides** at
  `~/.claude/triggers/<plugin>/agents/<name>.yml`. A user-authored
  sidecar activates an otherwise-dormant `source="plugin"` agent
  entry, flipping it to `source="plugin-override"` and supplying
  triggers + `applicable_skills`. Strict-override semantics: orphan
  sidecars (no matching installed plugin agent) emit a warning and
  are dropped — never produce ghost catalog entries. Builtin agents
  (`triggers/builtin/`) are guarded against accidental capture.
  Catalog builder gains a new Pass 3b walker; the staleness watcher
  gains a `triggers/<plugin>/agents/` walk. (#140, spec #141, PR #142)

- **Owned + project agent sidecar overrides** as colocated
  `<name>.triggers.yml` files next to the agent `.md` — in both
  `~/.claude/agents/` and `<repo>/.claude/agents/`. Sidecar wins
  over inline `triggers:` frontmatter on collision (with a warning);
  orphan sidecars are dropped (with a warning); invalid YAML is
  warn-and-skip. Sources stay `owned` / `project` — sidecar is
  delivery mechanism, not authorship. Inline triggers continue to
  work indefinitely; users opt in to sidecars at their own pace.
  New Pass 2b and Pass 4b walkers; staleness watcher extended to
  `*.triggers.yml` in both agent trees. (#148, spec #149, PR #150)

### Fixed

- `router-health` advisory-override drift scanner no longer stops
  reading the transcript at the first `skill_calls` event, so
  overrides that happen after a skill activation are now counted.
  Previously, sessions where a skill was invoked before an
  advisory-override decision under-reported. (#145)

### Maintenance

- Removed redundant `.claude-plugin/marketplace.json` — the
  marketplace manifest in the `glitchwerks/plugins` repo is the
  authoritative source. Carrying a copy here introduced drift
  risk on every release. (#147)

### Schema

- `docs/schema.md` and `docs/design/trigger-schema.md` document the
  new sidecar locations and `source="plugin-override"` extending
  to `kind="agent"`. No `schema_version` bump — additive only.

### Known follow-ups (non-blocking)

- `_apply_colocated_sidecars` skips `validate_entry`, so sidecar
  inputs bypass weight clamping, keyword dedup, and deprecated-field
  stripping that inline frontmatter receives. Tracked in #151.
- `_apply_colocated_sidecars` doesn't explicitly write `routable=True`,
  so an owned agent with `routable: false` in frontmatter plus a
  sidecar stays silently inert at routing time. Tracked in #153.

## [0.4.2] — 2026-05-18

Patch release: one user-visible bug fix (#134) plus regression-test
coverage and CI hygiene work accumulated against v0.4.1.

### Fixed

- `python -m claude_wayfinder dispatch` no longer emits a spurious
  `RuntimeWarning` about `claude_wayfinder.match` import order — the
  dispatch entry point now invokes `match.main()` in-process instead of
  spawning a `python -m claude_wayfinder.match` subprocess. The runpy
  warning fired in the child because `claude_wayfinder/__init__.py`
  eagerly re-exports from `claude_wayfinder.match`, so the submodule was
  already in `sys.modules` when `runpy` tried to execute it as
  `__main__`. (#134, PR #136)

### Added

- Regression test covering the no-router-agent + stale `disabled: true`
  override warning-paths combo of `catalog build`. The behavior was
  already correct in v0.4.1 (#124 added the discovery-default flags that
  fixed the bare-invocation exit-1 path); this PR closes the coverage
  gap so the warning-paths combo cannot silently regress. (#132, PR #133)

### Maintenance

- `actions/checkout` pinned to v6 across `ci.yml` and `release.yml`.
  (#118, Renovate)
- `actions/setup-node` pinned to v6 in `ci.yml`. (#121, Renovate)
- `Test (Node)` CI job moved from Node 20 to Node 24. (#122, Renovate)
- `actions/upload-artifact` v4 → v7 and `actions/download-artifact`
  v4 → v8 in `release.yml`. (#126, Renovate)

## [0.4.1] — 2026-05-18

Patch release: three bug fixes against v0.4.0, all caught during downstream
integration in `glitchwerks/claude-configs`.

### Fixed

- `catalog build` now defaults `--plugin-overrides-dir`, `--plugins-dir`, and
  `--builtin-agents-dir` to `${CLAUDE_HOME}/triggers`, `${CLAUDE_HOME}/plugins`,
  and `${CLAUDE_HOME}/triggers/builtin` respectively.  Previously these were
  unset, silently disabling Pass 2.5 / Pass 2.6 / trigger-override resolution
  when the plugin-shipped refresh hook ran with no extra args. (#124)
- `skills/setup-wayfinder/SKILL.md` Step 1 now uses the correct plugin slug
  `claude-wayfinder-glitchwerks` (derived from plugin ID
  `claude-wayfinder@glitchwerks`, where `glitchwerks` is the marketplace name).
  Previously hardcoded `claude-wayfinder-claude-wayfinder`, which on a fresh
  install created an orphan venv at the wrong path; hooks then failed to find
  the setup-state flag and re-prompted for setup. (#123)
- Step 1 also now validates the basename of `$CLAUDE_PLUGIN_DATA` against the
  expected slug before honoring it, falling back to the computed path on
  mismatch. The harness exports `$CLAUDE_PLUGIN_DATA` per the active plugin
  surface, so cross-plugin leakage (e.g. from `codex-openai-codex`) previously
  could silently install the venv into a different plugin's data dir. (#123)
- Removed remaining `claude-wayfinder@claude-wayfinder` / `…-claude-wayfinder`
  slug references from `hooks/lib/setup-state.js`, the matching node test,
  `tests/integration/setup_pipeline.py`, `README.md`, and `docs/integration.md`
  for consistency with the corrected canonical slug. Latent only — at runtime
  the harness env var always wins — but the docs and the fallback constant
  now agree with reality. (#128)

## [0.4.0] — 2026-05-18

Major release: replaces per-hook shell discovery of a Python interpreter with
a user-initiated `/setup-wayfinder` skill that materializes a venv at
`${CLAUDE_PLUGIN_DATA}/venv/` and writes a setup-state flag the hooks read.
Closes the v0.3.x regression chain (#76, #80, #82, #87) by eliminating the
shell-discovery surface entirely — there is no longer any "which Python does
the hook find on PATH" question to get wrong.

This is the first PyPI release of `claude-wayfinder`. The setup skill installs
the package from PyPI on first invocation; subsequent sessions read the
recorded venv path from the flag without re-resolving the interpreter.

### Added

- **`/setup-wayfinder` skill** (`skills/setup-wayfinder/SKILL.md`) for one-time
  venv materialization. Discovers Python ≥3.11, wipes-and-recreates the venv at
  `${CLAUDE_PLUGIN_DATA}/venv/`, installs `claude-wayfinder` from PyPI, runs an
  import-probe verification, and writes the setup-state flag. Triggers on
  `/setup-wayfinder` and natural-language phrases like "set up claude-wayfinder",
  "wayfinder isn't working", "fix wayfinder". PR #107.
- **`hooks/lib/setup-state.js`** shared helper exposing `readSetupState`,
  `getCurrentVersion`, `getVenvPython`, with platform-aware path resolution
  and a `$CLAUDE_PLUGIN_DATA` test seam. 15 unit tests. PR #103.
- **`tests/integration/setup_pipeline.py`** executable Python mirror of the
  skill's 8 steps, used by the CI smoke test and exposed for advanced
  scripting. A drift check (`tests/test_skill_pipeline_sync.py`) enforces
  that the skill body's step headings stay aligned with the pipeline's
  function names. PR #107.
- **Skill smoke test** (`tests/integration/test_setup_skill.py`) running on
  Ubuntu in CI via the new `skill-smoke-ubuntu` job. Exercises the real
  `python -m venv` + `pip install` path end-to-end. Directly addresses
  inquisitor pass-1 charge 11 (no subprocess-stubbing test theater). PR #109.
- **Release workflow** (`.github/workflows/release.yml`) publishing to PyPI
  on `v*` tag push via Trusted Publisher OIDC. TestPyPI dry-run job gated on
  `-rc` / `-alpha` / `-beta` pre-release tags. PR #114, #116.
- **`CLAUDE_WAYFINDER_PIP_SPEC` env-var test seam** in `pip_install()` for
  pre-v0.4.0 CI to install from the local checkout. Removed once v0.4.0 ships
  to PyPI (this release). PR #109.

### Changed

- **`check-catalog-health.js`** now reads the setup-state flag at SessionStart
  and emits an `additionalContext` banner when the flag is `MISSING`, `STALE`,
  or `BROKEN`. When the flag is `VALID`, runs a one-per-session
  `import claude_wayfinder` probe against the recorded venv Python; deletes
  the flag on probe failure so the next session re-prompts setup. PR #105.
- **`refresh-catalog-on-stale.js`** now reads the setup-state flag and uses
  the recorded venv-Python path. Removed ~80 LOC of v0.3.x discovery
  scaffolding: the `CLAUDE_WAYFINDER_PYTHON` env-var fallback, the bare
  `python` PATH fallback, and the regex-based command parser used only for
  test overrides (the `DISPATCH_GENERATOR_CMD` test seam is retained
  unchanged). PR #105.
- **README.md and `docs/integration.md`** document the SessionStart banner,
  the `/setup-wayfinder` flow, plugin-update re-setup behavior, and
  cross-machine setup expectations. PR #112.
- **PyPI distribution.** `claude-wayfinder` is now published to PyPI; the
  v0.4 setup skill installs it from there. No more pre-v0.4.0
  `pip install -e` workarounds for downstream installers.

### Removed

- **`CLAUDE_WAYFINDER_PYTHON` env-var override** (deprecated in v0.3.4 as a
  stopgap; superseded by the venv-based architecture). The hook no longer
  consults this variable.
- **`parseCmd` regex parser** in `hooks/refresh-catalog-on-stale.js`'s default
  invocation path. Retained inside the `DISPATCH_GENERATOR_CMD` test-override
  branch to keep the existing test suite stable.
- **Bare `python` on PATH fallback** in `refresh-catalog-on-stale.js`. The hook
  now requires either a `VALID` setup-state flag or a `DISPATCH_GENERATOR_CMD`
  test override; any other state results in a silent no-op (with the
  SessionStart banner from `check-catalog-health.js` surfacing the situation
  to the user).

### Deferred

- **Phase 5: macOS + Windows CI matrix** — accepted as a YAGNI trade-off until
  external adoption justifies the GitHub Actions runner-minute spend. The
  plugin's code is platform-agnostic; CI just doesn't validate that. Inquisitor
  pass-2 charge 18 noted and accepted. PR #110 records the deferral on the
  plan file with the original task structure preserved as an implementation
  template for future revival.

### Migration from v0.3.x

After updating the plugin to v0.4.0 (`/plugin update glitchwerks/claude-wayfinder`):

1. SessionStart shows: _⚠ claude-wayfinder venv is for v0.3.6 but plugin is v0.4.0. Run /setup-wayfinder to refresh._
2. Run `/setup-wayfinder`. The skill discovers Python ≥3.11, creates a venv at
   `${CLAUDE_PLUGIN_DATA}/venv/`, installs `claude-wayfinder` from PyPI,
   verifies, and writes the flag.
3. Open a new session — hooks read the flag and proceed normally.

The `CLAUDE_WAYFINDER_PYTHON` environment variable, if set, is now ignored.
You can remove it from your shell profile.

## [0.3.6] — 2026-05-17

Patch release tightening consistency and CI coverage with no code-behavior
changes. Plugin description alignment ensures users see one voice across the
marketplace listing, post-install fields, and package metadata. The official
Anthropic validator joining CI closes the gap that allowed the
`hooks/hooks.json` flat-array schema bug to ship through v0.1.0–v0.3.1 — the
homegrown manifest test covers field-level conventions; the official validator
covers documented schema shape. No consumer migration required.

### Changed

- **Plugin description unified to canonical marketplace text across all fields.**
  `plugin.json`, `pyproject.toml`, and the GitHub repo description now all read:
  _"Helps Claude make deterministic, auditable choices about which agent and
  skills to use for a given task — replacing prose-scanning agent/skill
  selection with a typed scoring kernel."_ Previously the post-install fields
  used mechanism-focused wording while the marketplace used outcome-focused
  wording; users now see a consistent voice at every touchpoint.
  PR #91, closes #75.

### Added

- **`claude plugin validate` added as official manifest gate in CI.**
  The `Validate Plugin Manifest` job now runs `@anthropic-ai/claude-code@2.1.143`
  (pinned) alongside the existing `tests/test_plugin_manifests.py`. The official
  validator enforces the documented manifest schema (`hooks/hooks.json` shape,
  `userConfig` block, etc.); the homegrown test enforces field-level conventions
  the validator does not cover. They are complementary. Closes the gap that let
  the `hooks/hooks.json` flat-array schema bug (#70) ship undetected before
  being caught and fixed by #71.
  PR #92, closes #72.

## [0.3.5] — 2026-05-17

Patch release shipping CLI-side defaults for `catalog build`. v0.3.4 fixed the
interpreter-discovery problem but exposed the next layer: the bundled hook's
bare `python -m claude_wayfinder catalog build` invocation was missing the
four required path args, exiting 2 on every prompt. v0.3.5 makes those args
optional with sensible defaults anchored to `${CLAUDE_HOME}` (falling back to
`~/.claude`), so the hook's bare invocation Just Works.

This breaks the regression chain — v0.3.2 ENOENT, v0.3.3 wrong interpreter,
v0.3.4 missing args — by moving the defaults to where they belong: the CLI
knows how to be useful by itself, the hook ships a bare invocation, and
consumers needing custom paths still have `DISPATCH_GENERATOR_CMD`.

### Fixed

- **`catalog build` bare invocation now succeeds without `DISPATCH_GENERATOR_CMD` override.**
  The four args `--skills-dir`, `--agents-dir`, `--out`, and `--log` are now optional,
  resolving at runtime to `${CLAUDE_HOME}/skills`, `/agents`,
  `/state/dispatch-catalog.json`, and `/state/catalog-generation.log` respectively.
  `CLAUDE_HOME` defaults to `~/.claude` when unset.  This means the bundled
  `refresh-catalog-on-stale.js` hook's bare `python -m claude_wayfinder catalog build`
  invocation Just Works for consumers who do not set `DISPATCH_GENERATOR_CMD`.
  This is the third regression in three releases — v0.3.2 (ENOENT on the bare
  entry-point shim), v0.3.3 (wrong interpreter when venv is not activated),
  v0.3.4 (missing required args, this issue).  The durable structural fix is
  defaults at the CLI, not at the hook: the hook ships a bare invocation and
  delegates path resolution to the CLI.  Closes #87.

## [0.3.4] — 2026-05-17

Patch release shipping a `CLAUDE_WAYFINDER_PYTHON` env-var override for
consumers whose `python` on PATH does not have `claude_wayfinder` importable
(e.g. the package is installed into a non-activated venv that the plugin's
hook child process cannot discover). This is a v0.3.x stopgap — the canonical
fix is a `${CLAUDE_PLUGIN_DATA}` SessionStart-materialised venv per Anthropic's
documented plugin pattern, tracked in #81 and deferred to a future release line.

### Fixed

- **`refresh-catalog-on-stale.js` now respects `CLAUDE_WAYFINDER_PYTHON` env var.**
  Consumers whose `python` on PATH does not have `claude_wayfinder` importable
  (e.g. installed into a non-activated venv) can set `CLAUDE_WAYFINDER_PYTHON`
  to the absolute path of a Python interpreter that does. Spawn is now an
  explicit args-array invocation, defending against Windows paths with spaces
  in the override value. The `DISPATCH_GENERATOR_CMD` test-override path is
  preserved unchanged — all existing tests continue to pass. Closes #82.
  Refs #80. This is a v0.3.4 stopgap; the canonical fix
  (`${CLAUDE_PLUGIN_DATA}` SessionStart-materialised venv per Anthropic's
  documented plugin pattern) is tracked in #81 and deferred to a future
  release. (#84)

## [0.3.3] — 2026-05-16

Patch release fixing a regression introduced in v0.3.2 (technically PR #67, which
shipped in v0.3.2 via the hooks.json migration). The `refresh-catalog-on-stale.js`
hook called `claude-wayfinder catalog build` as a bare PATH command, but the
`[project.scripts]` entry-point shim only resolves on PATH inside the venv it was
installed into — not from the plugin's hook child process. Result: `spawnSync
claude-wayfinder ENOENT` on every prompt with a loud stale-catalog banner. v0.3.3
switches to `python -m claude_wayfinder catalog build`, which works whenever
`python` on PATH has the package importable (the documented Pattern A install).

A more robust fix using `${CLAUDE_PLUGIN_DATA}` SessionStart-materialized venvs
is tracked separately as v0.4 architectural work.

### Fixed

- **`refresh-catalog-on-stale.js` invocation no longer assumes a venv-activated PATH.**
  Default generator command changed from `claude-wayfinder catalog build` (which
  failed `ENOENT` on every prompt for consumers whose install venv wasn't on the
  interactive PATH) to `python -m claude_wayfinder catalog build`. The override
  path `DISPATCH_GENERATOR_CMD` is unchanged; tests already covered the override
  shape and continue to pass. Closes #76. (#77)

## [0.3.2] — 2026-05-16

Patch release shipping the `hooks/hooks.json` schema migration. The flat-array shape
the plugin had been shipping since v0.1.0 (`[{event, script, description}]`) failed
`claude plugin validate` with `hooks: Invalid input: expected record, received array` —
non-conformant with Anthropic's documented schema. v0.3.2 ships the documented
nested form with `${CLAUDE_PLUGIN_ROOT}` substitution, so consumers' Claude Code
installs actually wire all six hooks per the loader's documented contract.

This is the first release in which all six hooks are guaranteed to be reachable via
the documented loader path. If the previous flat-array shape was silently dropping
hooks under the fallback loader, drift telemetry volume in `~/.claude/state/router-drift.jsonl`
and `dispatch-log.jsonl` will change after upgrading.

### Fixed

- **`hooks/hooks.json` migrated to documented nested schema.** Top-level `hooks` is
  now an object keyed by event name; entries use `type: "command"` + `command: "..."`
  rather than the undocumented `script:` shorthand; tool filtering uses `matcher:
  "<regex>"` on the parent entry; script paths use `${CLAUDE_PLUGIN_ROOT}` substitution
  per Anthropic's hook-troubleshooting guidance. All six hooks (`SessionStart`,
  `UserPromptSubmit`, `PreToolUse(Agent)` × 2, `PostToolUse(Skill)`, `Stop`) remap
  1:1; no behavioral change in any hook script itself. Closes #70. (#71)

## [0.3.1] — 2026-05-16

Patch release fixing two Tier 1 hook regressions caught immediately after v0.3.0. Both
were partial-port omissions from the private harness — the sidecar producer for
`check-agent-dispatch-pairing.js` was missing entirely, and the catalog-refresh hook
still referenced the private-harness Python script path instead of the plugin's own
`claude-wayfinder catalog build` CLI. Both hooks now match what v0.3.0's documentation
already described.

### Fixed

- **`log-skill-invocation.js` PostToolUse(Skill) sidecar writer ported.** The Tier 1 hooks
  port in v0.3.0 shipped `check-agent-dispatch-pairing.js` (which reads
  `~/.claude/state/recent-skill-invocations.jsonl` to classify same-turn `Skill(dispatch) → Agent`
  sequences) but not the hook that writes the sidecar. With no producer, the pairing hook
  silently misclassified router-mediated dispatches as `bypass`, inflating false-positive
  drift metrics. Closes #65. (#66)
- **`refresh-catalog-on-stale.js` now invokes the plugin's own CLI.** The hook previously
  shelled out to `python <CLAUDE_HOME>/scripts/build_dispatch_catalog.py` — a
  private-harness path that does not exist on fresh plugin installs. The hook exited 0
  with `additionalContext` describing a generator failure, so the catalog silently never
  rebuilt. Default generator command flipped to `claude-wayfinder catalog build`, the
  entry-point registered by `pyproject.toml`. Closes #64. (#67)

[0.3.1]: https://github.com/glitchwerks/claude-wayfinder/releases/tag/v0.3.1

## [0.3.0] — 2026-05-15

Closes the gap between documented behavior and shipped behavior. Tier 1 hooks
make the observability layer described in `docs/schema.md` §5 actually
reachable for clean-install consumers. The `/refresh-catalog` skill gives
manual parity with the auto-refresh hook. `docs/dispatch-discipline.md`
documents the four routing-shape rules wayfinder describes but does not
enforce. A new design doc replaces the v5 private-audience artifact with
public-audience rationale.

**Breaking:** the `harness_version` field in `router-drift.jsonl` events is
renamed to `plugin_version`. External consumers of the drift log must update
their field references.

### Changed (Breaking)

- **`harness_version` field renamed to `plugin_version` in `router-drift.jsonl` events** — all
  five event types emitted by `router-drift-scanner.js` (`advisory_override`,
  `self_handle_unaided_invocation`, `needs_more_detail_repeat`, `catalog_degraded_session`,
  `skill_mediated_delegation`) now carry `plugin_version` instead of `harness_version`. (#56)
  External consumers of the drift log must update their field references.
  - `getHarnessVersion()` (in `hooks/lib/`) renamed to `getPluginVersion()`.
  - `hooks/lib/harness-version.js` renamed to `hooks/lib/plugin-version.js`.
  - `HARNESS_VERSION_OVERRIDE` test-injection env var renamed to `PLUGIN_VERSION_OVERRIDE`.

### Added

- **`/refresh-catalog` skill** — bundled skill at `skills/refresh-catalog/` for manually
  regenerating the dispatch catalog with a structured before/after report (mtime delta,
  entry counts by `kind`, warning extraction from the build log). Complements the
  auto-refresh hook for cases the mtime heuristic misses or when diagnosing catalog
  errors. Closes #58.
- **`docs/dispatch-discipline.md`** — reference doc describing the four routing-shape
  rules the matcher assumes (self-dispatch prohibition, Opus-native nested dispatch
  carve-out, skill propagation, one-dispatch-per-Agent-call), with failure modes and
  consumer-side implementation pointers for each. Closes #54.
- **`docs/design.md` § Non-goals** — explicit disclaimer that wayfinder does not ship
  dispatch-shape enforcement hooks; cross-reference to `docs/dispatch-discipline.md`.
- **Tier 1 drift-telemetry hooks** — five Claude Code hooks shipped in `hooks/`
  that automate catalog health, catalog auto-refresh, and routing-quality
  observability without any manual wiring. All hooks exit 0 in all conditions
  and never block a session or prompt. (#53)
  - `check-catalog-health.js` (SessionStart) — emits `[CATALOG ERROR]` or
    `[CATALOG STALE]` banner when the dispatch catalog is missing, empty,
    unparseable, or older than any source file.
  - `refresh-catalog-on-stale.js` (UserPromptSubmit) — auto-rebuilds the
    catalog when a source file is newer or the current project root has changed
    since the last build. Monitors user-global skills/agents, project-local
    `.claude/` trees, plugin cache, and `installed_plugins.json`.
  - `log-agent-dispatch.js` (PreToolUse/Agent) — appends an `agent_dispatch`
    event to `~/.claude/state/dispatch-log.jsonl` for every Agent tool call.
  - `check-agent-dispatch-pairing.js` (PreToolUse/Agent) — classifies each
    Agent call as `router_mediated`, `skill_mediated`, `bypass`, or
    `stale_dispatch`; writes drift events to `router-drift.jsonl` for
    non-router-mediated cases. Integrates a sidecar for same-turn
    Skill→Agent detection.
  - `router-drift-scanner.js` (Stop) — scans the completed session transcript
    and appends five additional drift event types to `router-drift.jsonl`:
    `advisory_override`, `self_handle_unaided_invocation`,
    `needs_more_detail_repeat`, `catalog_degraded_session`,
    `skill_mediated_delegation`.
- **`hooks/hooks.json`** — manifest wiring all five hooks to their Claude Code
  lifecycle events. (#53)
- **Node 20 CI job** (`Test (Node)`) — runs `node --test hooks/tests/*.test.js`
  in GitHub Actions on every push and pull request. 143 tests. (#53)
- **143 hook unit tests** across five test files using the built-in
  `node:test` runner; no npm dependencies required. (#53)

[0.3.0]: https://github.com/glitchwerks/claude-wayfinder/releases/tag/v0.3.0

## [0.2.0] — 2026-05-15

End-to-end integration flow. v0.1 shipped the matcher as an evaluation surface
that ran against bundled fixtures; v0.2 makes the plugin a real router for the
power-user-with-existing-router audience — they can build a catalog from their
own skills and agents, point the dispatch skill at it via an environment
variable, and route real session traffic through the matcher.

### Added

- **`claude-wayfinder catalog build`** — first-class CLI subcommand that
  exposes the full parameter surface of the underlying catalog builder. Plus
  a new `claude-wayfinder-match` console script for direct matcher invocation.
  (#39, PR #43)
- **Mode-aware `/dispatch` skill** — detects `$DISPATCH_CATALOG_PATH`: when
  unset, runs demo mode against bundled fixtures with an explicit banner;
  when set and valid, runs real-catalog mode against the consumer's catalog.
  A set-but-broken catalog path surfaces `[CATALOG ERROR]` and never falls
  back to demo. Stale-mtime emits a warning but proceeds. (#40, PR #44)
- **`docs/integration.md`** — power-user integration guide covering one-time
  catalog build, router-agent prompt snippet with branch logic for all 7
  decision types, tools-frontmatter prerequisite, catalog refresh (pre-commit
  hook + CI job + manual command), drift telemetry pointer, and
  troubleshooting. Linked from a new "Power-user integration" README section.
  (#41, PR #45)
- **`docs/schema.md`** — versioned contract document covering catalog entry
  schema (including `routable` and the five `source` tags), dispatch context
  schema, decision output schema for all 7 decision types (including
  `ask_user` reserved-status note), schema version declaration, and a
  minimal worked example catalog. (#42, PR #46)
- **v0.2 integration design doc** — `docs/design/2026-05-14-v0.2-integration-design.md`
  captures the decision rationale: skill-primary, deliberate invocation, no
  shipped router agent, no auto-firing hook (deferred to v0.3 pending real
  adoption signal). (#37, PR #38)

### Changed

- **`/dispatch` skill body** rewritten end-to-end to support both modes; the
  `triggers:` block stays `command_prefixes: [/dispatch]` — no proactive
  natural-language firing. (#40, PR #44)
- **README** gains a "Power-user integration" section pointing at
  `docs/integration.md`. (#41, PR #45)

### Deferred

- **#6 — bundled-runtime distribution spike** explicitly deferred to v0.3+
  per the v0.2 design's non-goals; v0.2 assumes a Python prerequisite, which
  the power-user audience already has.

[0.2.0]: https://github.com/glitchwerks/claude-wayfinder/releases/tag/v0.2.0

## [0.1.0] — 2026-05-14

First public release. Ships the deterministic 7-decision dispatch matcher as a
sideloadable Claude Code plugin and standalone Python library.

### Added

- **Public Python API** (`load_catalog`, `build_features`, `score`, `decide`,
  `VALID_DECISIONS`, and dataclasses `CatalogEntry`, `Features`, `ScoredEntry`,
  `Keyword`, `Triggers`) — curated in `__init__.py` with a stable `__all__`
  contract. (#12, PR #25)
- **Demo CLI** (`python -m claude_wayfinder demo`) — runs the matcher against
  bundled fixtures and prints all 7 decision branches. (#13, PR #28)
- **`dispatch` SKILL.md** — Claude Code skill that exercises all 7 routing
  decisions against the bundled demo catalog. (#13, PR #28)
- **Plugin manifests** — `.claude-plugin/plugin.json` and
  `.claude-plugin/marketplace.json` for sideload install via
  `/plugin marketplace add glitchwerks/claude-wayfinder`. (#13, PR #28)
- **CI workflow** — GitHub Actions lint (`ruff`) + test (`pytest`) on every
  push and pull request. Includes plugin manifest validation. (#7, PR via
  commit `f4c786a`)
- **`docs/api.md`** — full public API reference with call signatures,
  parameter descriptions, and worked examples. (#12, PR #25, PR #27)
- **Design and exploration docs** — deterministic-first router v5 design doc,
  plugin distribution research spike, and v0.1 plan + inquisitor reviews
  landed under `docs/`. (PR #18, PR #30)
- **`data-driven routable flag`** — catalog entries carry a `routable` boolean;
  no agent name is hardcoded in the matcher. (PR #20)

### Changed

- **Remove `~/.claude` and `CLAUDE_HOME` default path fallbacks** from
  `match.py`, `build_catalog.py`, and `_health.py`. Callers must pass an
  explicit path or set the `DISPATCH_CATALOG_PATH` / `DISPATCH_LOG_PATH`
  environment variables. (#10, PR #22)
- **`health.py` → `_health.py`** — health reporter made internal; public API
  does not expose it. (#12, PR #25)
- **Scrub harness-private references** from `src/` and top-level docs —
  removed issue-number cross-references and `~/.claude` path literals that
  leaked from the private harness. (#9, PR #21)

### Documentation

- **README rewrite** — Why / How-To framing, sideload install instructions,
  contributor quickstart, and evaluation-surface framing that makes clear
  v0.1 is an exploration/evaluation tool, not a daily-driver router.
  (#14, PR #31; #32, PR #33; #34, PR #35)
- **Synthetic test fixtures** — replaced live-catalog test cascade with
  synthetic agent and skill fixtures; removed two harness-invariant tests.
  (#11, PR #23)

[0.1.0]: https://github.com/glitchwerks/claude-wayfinder/releases/tag/v0.1.0
