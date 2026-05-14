# Changelog

All notable changes to this project are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
