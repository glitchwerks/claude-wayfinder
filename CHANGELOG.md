# Changelog

All notable changes to this project are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
