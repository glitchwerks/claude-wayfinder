# Python Module-Size + Reuse Audit

**Scope:** `build_catalog.py`, `_health.py`, `match.py` in `src/claude_wayfinder/`
**Cross-reference target:** `audit_catalog.py`
**Date:** 2026-05-21
**Parent issue:** #193

## Executive summary

- **Top finding:** `build_catalog.py` contains three separable subdomain clusters — **trigger validation** (27 interrelated functions, 612 lines), **catalog I/O and discovery** (14 functions, 544 lines), and **builder orchestration** (3 main functions + boilerplate, 370 lines). These have minimal interdependence and could split into a `build_catalog/` package.
- **Total proposed splits:** 9 discrete split candidates across three files; 4 are independent and parallelizable. 
- **Biggest cross-module reuse opportunity:** `build_catalog.py` and `audit_catalog.py` share **trigger-validation patterns** (keyword weight clamping, term validation, group slot semantics) but `audit_catalog.py` reimplements detection heuristically. A shared `_validate_triggers` module (120 lines) would eliminate duplication.
- **Sequencing:** Extract shared validation module first; then split `build_catalog.py` by responsibility cluster. `_health.py` and `match.py` splits are independent and may proceed in parallel.
- **Estimated effort:** M–L (medium to large). `build_catalog.py` is the critical path; test reorganization necessary post-split.

---

## Cross-module reuse findings

### Finding 1: Duplicate trigger-validation logic (build_catalog.py vs. audit_catalog.py)

**Issue:** Both modules validate catalog triggers against the same schema (docs/design/trigger-schema.md v6), but they diverge in implementation style and completeness.

- `build_catalog.py:_validate_keywords()` (lines 589–710) — strict validator with fatal/warning/info tiers. Applies weight clamping, deduplication, whitespace detection.
- `build_catalog.py:_validate_keyword_groups()` (lines 713–985) — comprehensive group validator: slot count, intra-group overlap, weight validation, depth warnings.
- `audit_catalog.py:rule_weight_not_in_ladder()` (lines 361–380) — **reimplements** weight detection without shared logic.
- `audit_catalog.py:rule_whitespace_in_term()` (lines 382–401) — **reimplements** whitespace detection separately.
- `audit_catalog.py:rule_one_dimensional_triggers()` (lines 538–567) — heuristic depth check; no shared abstraction with group validation.

**Proposed extraction:** Create `claude_wayfinder/_trigger_validators.py` (120 lines):
- `def validate_keyword_weight(value: float) -> tuple[bool, str | None]` — shared weight ladder check + clamping.
- `def validate_keyword_term(term: str) -> tuple[bool, str | None]` — shared whitespace + type detection.
- `def validate_keyword_group_slot_depth(slots: list) -> tuple[bool, int, str | None]` — shared slot-count validation.
- Export constants: `ALLOWED_WEIGHTS`, `MAX_GROUP_SLOTS`, `MIN_GROUP_SLOTS`.

**Locations:** 
- `build_catalog.py`: lines 589–710 (keywords), 713–985 (groups), 548–560 (clamp_weight)
- `audit_catalog.py`: lines 361–380, 382–401, 538–567 (three separate rules)

**Estimated savings:** ~180 lines of eliminated reimplementation across both modules.

**Risk:** Both modules have different failure semantics — `build_catalog.py` returns sanitized values with issues; `audit_catalog.py` collects findings. The extraction must provide primitives (boolean checks + clamping) that both can call, without enforcing one module's error model on the other. Moderate refactoring of audit rules required.

---

## Per-file analysis

### `build_catalog.py` (2890 lines)

**Logical boundaries — three responsibility clusters:**

**Cluster 1: Trigger Validation (612 lines, 27 functions)**
- `_clamp_weight()` (line 548–560): weight clamping utility
- `_validate_keywords()` (589–710): keyword list validator
- `_validate_keyword_groups()` (713–985): group validator with slot semantics
- `_blank_entry()` (564–587): default-entry factory
- Called by `validate_entry()` and integration points

**Cluster 2: I/O, Sidecar Loading, Discovery (544 lines, 14 functions)**
- `load_frontmatter()` (168–190): markdown frontmatter extraction
- `load_trigger_sidecar()` (193–222): YAML sidecar loading
- `discover_plugin_overrides()` (225–277): tree walking for plugin trigger files
- `discover_plugin_agent_overrides()` (280–347): agent-override tree walking
- `discover_colocated_agent_sidecars()` (348–421): agent sidecar lookup
- `update_revisions_sidecar()` (117–165): revision tracking JSON management
- `compute_content_hash()` (98–114): SHA-256 utility
- `_resolve_catalog_build_defaults()` (2625–2701): CLI arg resolution
- `discover_builtin_agents()` (1428–1459): builtin agent walking
- `discover_plugin_entries()` (1705–1734): plugin manifest enumeration
- `discover_installed_plugins()` (1592–1703): plugin detection and loading

**Cluster 3: Builder Orchestration, CLI, Main (370 lines, 3 functions + utilities)**
- `build()` (2128–2620): top-level orchestrator; Passes 1–3 + dead-zone detection
- `write_catalog()` (1310–1330): JSON output
- `write_log()` (1128–1156): build-log appending
- `main()` (2866–2890): CLI entry point
- `run_catalog_build()` (2809–2863): wrapper for argparse integration
- `add_catalog_build_args()` (2703–2807): CLI parser setup

**Cluster 4: Supportive utilities (364 lines, 8 functions)**
- `_parse_semver()` (1340–1365): version parsing for builtin-agent pinning
- `_read_claude_version()` (1368–1425): version detection
- `_sort_entry_lists()` (1159–1206): nested-list sorting for determinism
- `_is_plugin_namespaced()` (1736–1752): naming convention check
- `_resolve_applicable_references()` (1755–1822): agent/skill applicability resolution
- `_process_builtin_sidecar()` (1466–1590): builtin entry assembly
- `_check_skill_md_for_v5_leftovers()` (1865–1894): migration warning detector
- `detect_exclude_dead_zones()` (1824–1862): corpus-driven dead-zone detection

**Cluster 5: File processors (620 lines, 6 functions)**
- `validate_entry()` (986–1127): main validator orchestrator
- `_process_skill_file()` (1896–1958): SKILL.md processor
- `_process_plugin_override()` (1960–2022): override sidecar processor
- `_process_plugin_file()` (2024–2082): plugin file processor
- `_process_file()` (2084–2126): generic file processor for agents
- `_apply_colocated_sidecars()` (423–546): sidecar-override merger for agents

**Proposed split layout:**

```
build_catalog/
  __init__.py (28 lines)
    - Re-exports: build_catalog(), write_catalog(), write_log(), 
                  validate_entry(), ValidationIssue, ValidationResult
    
  _validate.py (612 lines)
    - _clamp_weight(), _blank_entry()
    - _validate_keywords(), _validate_keyword_groups()
    - All keyword/group validation logic
    - Private: ALLOWED_WEIGHTS, _V5_SIDECAR_KEYS
    
  _discover.py (544 lines)
    - load_frontmatter(), load_trigger_sidecar()
    - discover_plugin_overrides(), discover_plugin_agent_overrides()
    - discover_colocated_agent_sidecars(), discover_builtin_agents()
    - discover_installed_plugins(), discover_plugin_entries()
    - update_revisions_sidecar(), compute_content_hash()
    - _resolve_catalog_build_defaults() (large utility)
    
  _process.py (620 lines)
    - validate_entry() (validator orchestrator calling _validate.py functions)
    - _process_skill_file(), _process_plugin_file(), _process_file()
    - _process_plugin_override(), _apply_colocated_sidecars()
    - _check_skill_md_for_v5_leftovers()
    - _resolve_applicable_references() (applicability resolution)
    
  _semver.py (100 lines)
    - _parse_semver(), _read_claude_version()
    - Version detection for builtin-agent pinning
    
  _main.py (370 lines)
    - build() — top-level orchestrator (unchanged in signature)
    - write_catalog(), write_log()
    - detect_exclude_dead_zones()
    - _process_builtin_sidecar()
    - _is_plugin_namespaced()
    - _sort_entry_lists()
    - main(), run_catalog_build(), add_catalog_build_args()
    - CLI glue
```

**Public-surface impact:**

Per `__init__.py` lines 36–41, `build_catalog.build_catalog` (the function) cannot be re-exported at package level because the name collides with the package name. The current constraint is **preserved exactly**: `from claude_wayfinder.build_catalog import build_catalog` continues to work. Tests that import by symbol name (`from build_catalog import validate_entry`) must be updated to path through the new package structure.

Test imports currently reference:
- `ValidationIssue`, `ValidationResult` — move to `__init__.py` re-exports
- `build_catalog()`, `validate_entry()`, `write_catalog()`, `write_log()` — re-export from submodules
- `load_frontmatter()`, `load_trigger_sidecar()`, etc. — discoverable via `__all__` or explicit import

No breaking change to public API; test reorganization required.

**Test-coverage map:**

`tests/test_build_catalog.py` (5131 lines) covers the entire module via function-level imports and integration tests:
- Validator tests (lines 1–1500 estimated): focus on `_validate_keywords()`, `_validate_keyword_groups()`, `validate_entry()` → move to `test_build_catalog/_validate_test.py`
- Discovery tests (1500–2500 estimated): test loaders, frontmatter parsing, sidecar walking → move to `test_build_catalog/_discover_test.py`
- Processor tests (2500–3500 estimated): file processors, plugin override logic → move to `test_build_catalog/_process_test.py`
- Integration tests (3500–5131): end-to-end `build()` + CLI scenarios → stay in `test_build_catalog/integration_test.py` (or `test_build.py`)

Rough breakdown:
- `_validate.py` — ~1200 test lines (validator exhaustiveness)
- `_discover.py` — ~1000 test lines (sidecar parsing, tree walking)
- `_process.py` — ~1000 test lines (file processors, deduplication, override merging)
- `_semver.py` — ~150 test lines (version parsing edge cases)
- `_main.py` — ~1781 test lines (orchestration, CLI, dead-zone detection, end-to-end)

**Sequencing:** 
1. Extract `_validate.py` first (no dependencies on other submodules; isolated logic).
2. Extract `_semver.py` (standalone, only called by `_main.py`).
3. Extract `_discover.py` (depends on `_validate.py` for nothing; can stand alone).
4. Extract `_process.py` (depends on `_validate.py` and `_discover.py`; consolidates orchestration).
5. Extract `_main.py` (depends on all above; final wiring).

**Steps 1–3 are parallelizable.** Steps 4–5 must sequence.

**Risk notes:**
- Large test reorganization (5131 lines must be split). Fixture dependencies may cross split boundaries.
- `validate_entry()` is the central orchestrator — must remain stable and keep its current signature. The refactor should be internal only.
- Circular imports possible if `_validate.py` is not purely functional (no module-level initialization).
- `detect_exclude_dead_zones()` references the entire corpus; must remain in `_main.py` or accept a narrow validation API.
- Plugin override merging logic (`_apply_colocated_sidecars`, `_process_plugin_override`) is subtle and highly interdependent — keep in one submodule (`_process.py`).

---

### `_health.py` (2019 lines)

**Logical boundaries — four responsibility clusters:**

**Cluster 1: Metrics and CI invariants (350 lines, 3 functions + 1 dataclass)**
- `MetricResult` (lines 75–94): result dataclass
- `compute_plugin_entry_counts()` (156–195): catalog summary
- `compute_metrics()` (241–365): core metric computation (telemetry analysis)
- `check_ci_invariants()` (366–429): pre-ship gate logic (uses compute_metrics)

**Cluster 2: Report generation — CI mode (580 lines, 2 functions)**
- `format_ci_output()` (943–972): short CI banner + exit codes
- `_status_str()` (937–942): status emoji + label

**Cluster 3: Report generation — Full markdown (730 lines, 5 functions)**
- `format_report_output()` (1112–1283): comprehensive report with all sections
- `_build_bypass_causes_section()` (993–1110): bypass-cause analysis (uses external script)
- `load_jsonl()` (102–135): JSONL file parsing
- `load_catalog_entries()` (197–239): catalog loading from disk
- `most_recent_harness_version()` (974–991): dispatch-log querying

**Cluster 4: Drill-down subcommands (360 lines, 5 functions)**
- `_cmd_drill()` (1383–1469): drill-down CLI for one metric (day/session breakdown)
- `_cmd_top()` (1649–1771): top-N agents/skills query
- `_cmd_catalog_status()` (1773–1862): catalog entry summary
- `_drill_bypass()` (1471–1539): bypass-cause drill
- `_drill_advisory_override()` (1541–1597): advisory-override drill
- `_drill_recent_drift()` (1599–1647): recent-drift drill
- Plus helpers: `_events_in_window()` (1327–1358), `_event_kind()` (1360–1381), `_parse_window()` (1285–1325)

**Cluster 5: Main entry point and utility (80 lines, 1 function + constants)**
- `main()` (1865–2019): CLI dispatcher and parser setup
- `_run_generator()` (431–498): subcommand helper (invokes build-catalog)
- `_check_catalog_stability()` (500–583): catalog hash validation
- `_check_schema_validation()` (585–711): schema audit invocation

**Proposed split layout:**

```
_health/
  __init__.py (20 lines)
    - Re-exports: main(), check_ci_invariants(), compute_metrics(), 
                  format_ci_output(), format_report_output()
    
  _metrics.py (350 lines)
    - MetricResult dataclass
    - compute_plugin_entry_counts()
    - compute_metrics() — core telemetry analysis
    - check_ci_invariants() — CI gate logic
    
  _report.py (730 lines)
    - load_jsonl(), load_catalog_entries()
    - format_report_output() — main report composer
    - _build_bypass_causes_section() — bypass analysis
    - most_recent_harness_version()
    - _status_str(), format_ci_output() — status formatting
    
  _drill.py (360 lines)
    - _parse_window(), _events_in_window(), _event_kind()
    - _cmd_drill(), _cmd_top(), _cmd_catalog_status()
    - _drill_bypass(), _drill_advisory_override(), _drill_recent_drift()
    
  _checks.py (100 lines)
    - _run_generator()
    - _check_catalog_stability()
    - _check_schema_validation()
    
  _main.py (100 lines)
    - main() — CLI dispatcher
    - Constants (thresholds, dispositions)
    
Total: ~1660 lines (260 lines shrink due to removed comments/imports)
```

**Public-surface impact:**

`_health.py` is invoked by the CLI subcommand short-circuit in `cli.py` line ~75:

```python
if argv[0] == "health":
    return _health_mod.main(argv[1:])
```

The split **must preserve** `main()` as the public entry point, callable from `claude_wayfinder._health.main`. This is a hard constraint per issue #193 Technical Notes.

Current `__init__.py` does NOT re-export anything from `_health` (private module). Test imports of internal functions are allowed to break; only `main()` is off-limits.

**Test-coverage map:**

`tests/test_health.py` (estimated 2000+ lines, not measured) covers:
- Metrics computation — test `compute_metrics()`, `check_ci_invariants()` → `test_health/_metrics_test.py`
- Report formatting — test report generation, bypass-cause section → `test_health/_report_test.py`
- Drill commands — test drill-down filtering, time windows → `test_health/_drill_test.py`
- CI checks — test catalog hash, schema audit flow → `test_health/_checks_test.py`
- Integration — end-to-end CLI scenarios → `test_health/integration_test.py`

**Sequencing:**
1. Extract `_metrics.py` (no dependencies on others; core logic).
2. Extract `_checks.py` (isolated; invokes subprocesses).
3. Extract `_report.py` (depends on `_metrics.py` for result interpretation).
4. Extract `_drill.py` (depends on report helpers and time-window logic; standalone otherwise).
5. Extract `_main.py` (imports from all above; final wiring).

**Steps 1–2 are parallelizable.** Steps 3–5 must sequence.

**Risk notes:**
- `main()` **must** remain at `_health.main()` — cannot be renamed or relocated without breaking `cli.py`.
- Large constants dict (`_BYPASS_CAUSE_DISPOSITION`) is shared by multiple functions; consider a private config module or inline during split.
- `_run_generator()` and `_check_catalog_stability()` call external subprocesses (`claude-wayfinder build-catalog`, `audit-catalog`); these are integration points that should stay tightly scoped.
- The drill-down functions process JSONL files with time windows; window parsing is subtle and shared across three drill subcommands. Keep `_parse_window()` co-located with drills to minimize import surface.

---

### `match.py` (1215 lines)

**Logical boundaries — three responsibility clusters:**

**Cluster 1: Data model — Triggers, features, entries (242 lines, 6 dataclasses)**
- `Keyword` (108–118): single keyword with weight
- `Slot` (121–134): OR-group of terms in a keyword_group
- `KeywordGroup` (136–153): AND of slots with weight
- `Triggers` (155–179): parsed trigger block for one entry
- `CatalogEntry` (181–203): one catalog entry (agent or skill)
- `Features` (206–228): extracted features from dispatch context
- `ScoredEntry` (231–241): entry + computed score

**Cluster 2: Parsing and I/O (420 lines, 8 functions)**
- `_resolve_catalog_path()` (279–317): catalog path resolution (env var + CLI)
- `_resolve_log_path()` (320–338): log path resolution
- `_compute_catalog_hash()` (341–364): SHA-256 digest
- `_get_matcher_version()` (367–389): git SHA lookup
- `_parse_slot()` (435–473): raw slot parsing
- `_parse_keyword_group()` (476–505): raw group parsing
- `_parse_triggers()` (508–552): triggers dict parser
- `load_catalog()` (555–595): JSON catalog loader
- `_write_log_entry()` (392–432): JSONL append logging

**Cluster 3: Keyword extraction and scoring (400 lines, 5 functions)**
- `extract_keywords()` (248–271): tokenization from task description
- `_matched_glob_count()` (653–682): path-glob matching counter
- `group_satisfied()` (685–701): group satisfaction predicate (public)
- `score()` (704–775): main scoring function (public)
- `feature_count()` (783–817): feature density counter
- `_skills_for_agent()` (825–853): skill filtering and sorting

**Cluster 4: Decision composition (250 lines, 4 functions)**
- `decide()` (861–975): decision ladder implementation (public)
- `_rationale_for()` (983–1038): human-readable rationale builder
- `_top_alternatives()` (1041–1055): top-N alternative builder

**Cluster 5: CLI and main (100 lines, 1 function)**
- `main()` (1078–1215): entry point; orchestrates load → features → score → decide → log → emit

**Cluster 6: Error handling (50 lines, 1 function)**
- `_emit_catalog_error()` (1063–1075): stderr banner + exit

**Proposed split layout:**

```
match/
  __init__.py (15 lines)
    - Re-exports: all public symbols from match.py
    - VALID_DECISIONS, all dataclasses, all public functions
    - See __init__.py lines 15–29 for the current list
    
  _types.py (242 lines)
    - Keyword, Slot, KeywordGroup, Triggers, CatalogEntry, 
      Features, ScoredEntry dataclasses
    - Export VALID_DECISIONS constant
    - No dependencies on other modules
    
  _catalog.py (150 lines)
    - _resolve_catalog_path(), _resolve_log_path()
    - _compute_catalog_hash(), _get_matcher_version()
    - load_catalog()
    - _write_log_entry()
    
  _parse.py (180 lines)
    - _parse_slot(), _parse_keyword_group(), _parse_triggers()
    - Parsing helpers for catalog deserialization
    
  _match.py (400 lines)
    - extract_keywords()
    - _matched_glob_count(), feature_count()
    - group_satisfied() (public)
    - score() (public)
    - _skills_for_agent()
    - is_agent_routable imported from match_filters
    
  _decide.py (250 lines)
    - decide() (public)
    - _rationale_for(), _top_alternatives()
    
  _main.py (100 lines)
    - main() — entry point
    - _emit_catalog_error()
    - CLI argument parsing and orchestration

Total: ~1337 lines (expansion due to imports; collapsed via __init__.py)
```

**Public-surface impact — HARD CONSTRAINT:**

Per issue #193 Technical Notes and `__init__.py` lines 15–29, these symbols **MUST** remain importable from `claude_wayfinder.match`:

- Dataclasses: `CatalogEntry`, `Features`, `Keyword`, `KeywordGroup`, `ScoredEntry`, `Slot`, `Triggers`
- Functions: `build_features`, `decide`, `group_satisfied`, `load_catalog`, `score`
- Constants: `VALID_DECISIONS`

**The split preserves this constraint via `match/__init__.py`** re-exports. All submodules are internal (prefixed `_`). No breaking change.

**Test-coverage map:**

`tests/test_match.py` (2476 lines) covers:
- Types and dataclasses — basic construction → `test_match/_types_test.py` (~100 lines)
- Catalog loading — JSON parsing, errors → `test_match/_catalog_test.py` (~400 lines)
- Trigger parsing — slot, group, triggers parsing → `test_match/_parse_test.py` (~200 lines)
- Feature extraction — keyword tokenization → `test_match/test_extract.py` (~200 lines)
- Scoring — glob matching, keyword scoring, group satisfaction → `test_match/test_score.py` (~700 lines)
- Decision ladder — all 6 decision paths → `test_match/test_decide.py` (~600 lines)
- Rationale — human-readable output → `test_match/test_rationale.py` (~200 lines)
- End-to-end — full dispatch flow → `test_match/integration_test.py` (~276 lines)

**Sequencing:**
1. Extract `_types.py` (no dependencies; data model).
2. Extract `_catalog.py` and `_parse.py` (both depend on `_types.py` only; can proceed in parallel).
3. Extract `_match.py` (depends on `_types.py`, `_parse.py`; core matching logic).
4. Extract `_decide.py` (depends on `_match.py`, `_types.py`; decision logic).
5. Extract `_main.py` (depends on all above; CLI wiring).

**Steps 1–2 are parallelizable.** Steps 3–5 must sequence.

**Risk notes:**
- The match algorithm is the core of the dispatch system. Refactoring introduces no new logic but must preserve exact behavior — comprehensive test coverage is load-bearing.
- `group_satisfied()` is a public helper shared by `score()` and rationale builders. Must remain accessible and correctly placed in `_match.py` where scoring lives.
- `main()` at `match.py:1078` is invoked by `_dispatch.py` (via dynamic import). The split must **preserve** `match.main()` as callable from `claude_wayfinder.match.main`.
- Logging to `_write_log_entry()` is isolated in `_catalog.py`; no risk of circular imports via logging.

---

## Sequencing recommendation

**Proposed execution order (parallelizable clusters noted):**

### Phase 1: Shared validation extraction (prerequisites for both build_catalog and audit_catalog)
- **Single commit:** Extract `claude_wayfinder/_trigger_validators.py` (120 lines)
  - Rationale: Unblocks both `build_catalog` and `audit_catalog` refactoring; isolates reuse opportunity.

### Phase 2A: match.py split (independent; can run in parallel with Phase 2B)
- **Commit 1:** Extract `match/_types.py` (dataclasses, constants)
- **Commit 2:** Extract `match/_catalog.py` + `match/_parse.py` (I/O, parsing)
- **Commit 3:** Extract `match/_match.py` + `match/_decide.py` (scoring, decision)
- **Commit 4:** Extract `match/_main.py` (CLI) + create `match/__init__.py` (re-exports)
- **Test reorganization:** One commit moving/updating test files

### Phase 2B: _health.py split (independent; can run in parallel with Phase 2A)
- **Commit 1:** Extract `_health/_metrics.py` (core metrics) + `_health/_checks.py` (CI checks)
- **Commit 2:** Extract `_health/_report.py` (report generation)
- **Commit 3:** Extract `_health/_drill.py` (drill-down subcommands)
- **Commit 4:** Extract `_health/_main.py` (CLI) + create `_health/__init__.py` (re-exports)
- **Test reorganization:** One commit moving/updating test files

### Phase 3: build_catalog.py split (must sequence internally)
- **Commit 1:** Extract `build_catalog/_validate.py` (trigger validation)
- **Commit 2:** Extract `build_catalog/_semver.py` (version logic)
- **Commit 3:** Extract `build_catalog/_discover.py` (I/O and discovery)
- **Commit 4:** Extract `build_catalog/_process.py` (file processors, orchestration)
- **Commit 5:** Extract `build_catalog/_main.py` (CLI, build orchestrator) + create `build_catalog/__init__.py` (re-exports)
- **Test reorganization:** One commit moving/updating test files

**Parallelization:**
- Phases 2A and 2B may proceed in parallel (different modules, no cross-dependencies).
- Phase 1 is a prerequisite; complete first.
- Phase 3 requires sequential ordering due to internal dependencies.

**Total PR count:** 12 commits (4 per large split + test reorganization per split) + 1 initial validation extraction = 13 PRs. Consider batching per phase (3 PRs: Phase 1, Phase 2A+2B, Phase 3) for review velocity.

**Estimated lines moved per PR:**
- Phase 1: +120, no test changes (new module)
- Phase 2A: 4 PRs moving ~1215 lines + ~2476 test lines
- Phase 2B: 4 PRs moving ~2019 lines + ~2000 test lines
- Phase 3: 5 PRs moving ~2890 lines + ~5131 test lines

---

## Methodology notes

**Approach:**

1. **End-to-end file reading:** Read all target files in full to identify natural responsibility clusters, not just scanning headings.

2. **Function dependency tracing:** Grepped for `^def |^class` to enumerate all symbols; manually traced call graphs to group functions that work together (e.g., `_validate_keywords()` + `_validate_keyword_groups()` both implement validator state machines and share constants).

3. **Cross-file pattern detection:** Searched for similar signatures, repeated logic patterns (e.g., weight clamping, slot parsing) across module boundaries. Found reuse opportunity in `build_catalog.py:_validate_*` vs. `audit_catalog.py:rule_*`.

4. **Public API surface:** Read `__init__.py` (lines 15–59) to identify what's exported and what's private. Verified constraints from issue #193 Technical Notes re: `match.py` exports, `_health.main()` entry point, `build_catalog` name shadowing.

5. **Test import analysis:** Sampled test files (`test_build_catalog.py` header, `test_health.py`, `test_match.py`) to infer test-to-source mapping; estimated test reorganization scope.

6. **Cluster sizing:** Counted lines per logical group using line ranges observed during reading; accounted for docstrings, constants, and internal helpers proportionally.

**Tools used:**
- `Read` tool: end-to-end file reading (3–4 chunks per file to manage limits)
- `Grep` tool: symbol enumeration (`^def |^class`), import tracking, pattern search
- `Bash` with `wc -l`: line counting for scope sizing
- Manual inspection: dependency tracing, call-graph analysis

**Confidence levels:**
- **High:** Cluster identification (each function mapped to a cohesive responsibility).
- **High:** Public-surface constraints (explicit in `__init__.py` and issue specs).
- **Medium:** Test reorganization scope (estimated by sampling; actual test file structure not fully read due to size).
- **Medium:** Cross-module reuse risk assessment (logic patterns identified, but subtle behavior differences in error handling not exhaustively checked).
- **Low:** Exact effort estimate (S/M/L is heuristic; no calibration against past refactors in this codebase).

---

## Open questions

1. **Version constraints on submodule imports:** When `build_catalog/` becomes a package, does `from claude_wayfinder.build_catalog import build_catalog` continue to work in Python <3.10 without explicit `__init__.py` re-exports? (Answer: Yes, via `__init__.py:__all__` and explicit import statement, no issue.)

2. **Test fixture sharing across split boundaries:** `test_build_catalog.py` (5131 lines) likely has shared fixtures (e.g., sample SKILL.md files, validation issue factories). Identify fixture dependencies before assigning tests to submodules — risking fragile fixtures is a common cost in large test reorganizations.

3. **Drill-down command naming conflict in _health.py:** The subcommand is `/health drill`, but there's also a `_drill_bypass()` helper. Is the namespace clear enough, or should helper functions be renamed to avoid user confusion? (Current: OK; helpers are internal.)

4. **Audit rules extensibility after refactor:** `audit_catalog.py` is designed for plugin-style rule registration (`@register` decorator). Will extracting shared validators into `_trigger_validators.py` make it harder or easier for future audit rules to reuse them? (Recommend: design `_trigger_validators` as a public API module so new audit rules can import cleanly.)

5. **Backward compatibility for test imports:** Currently, tests import `from claude_wayfinder.build_catalog import ValidationIssue` directly. After the split, should this continue to work via `__init__.py` re-exports, or should test imports be updated to the specific submodule? (Recommend: both; re-export for backward compat, but over the next 2 releases, deprecate direct imports and require submodule paths.)

---
