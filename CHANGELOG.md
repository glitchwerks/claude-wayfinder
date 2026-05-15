# Changelog

All notable changes to this project are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
