# Setup-skill architecture design

Tracking epic: [#99](https://github.com/glitchwerks/claude-wayfinder/issues/99) (supersedes [#81](https://github.com/glitchwerks/claude-wayfinder/issues/81))
Author: brainstorming session 2026-05-17 (cbeaulieu-gt + Claude)
Status: **design approved — input to writing-plans**
Predecessors:
- [#81](https://github.com/glitchwerks/claude-wayfinder/issues/81) v0.4 bundled-venv epic (closed/not_planned) — four plan files from that epic (deferred SessionStart-bootstrap design, inquisitor pass 1, planner revision, inquisitor pass 2) were deleted in [#223](https://github.com/glitchwerks/claude-wayfinder/issues/223) after the epic was superseded by this architecture (#99).

---

## § 1. Why this design exists

The v0.3.x patch series (PRs #67/#71/#77/#84/#88) chased the same root cause through four releases: **the plugin hook child process trying to resolve Python via the consumer's interactive shell environment.** v0.3.2 ENOENT, v0.3.3 wrong interpreter, v0.3.4 missing args, v0.3.5 CLI defaults. Each fix exposed the next layer.

The v0.4 epic ([#81](https://github.com/glitchwerks/claude-wayfinder/issues/81)) proposed materializing a plugin-owned venv inside a `SessionStart` hook. After two project-planner passes and two inquisitor critiques, the verdict on the latest revision was **REVISE-AGAIN** — the design rests on doc-fused premises about `SessionStart` blocking semantics that haven't been empirically verified, and the bundled-wheel offline-friendliness claim understates the platform-tag surface.

This spec describes a **simpler architecture** that dissolves most of the inquisitor's MAJOR concerns by construction rather than answering them: **a user-initiated setup skill plus a per-hook flag check, with banners surfacing the setup-required state.** Setup runs in skill context (interactive, LLM-assisted) rather than hook context (constrained, headless). The explicit trade is **one user-typed command per major version** in exchange for **eliminating most of the architectural risk** the inquisitor flagged.

## § 2. Design decisions (locked from brainstorm)

| # | Decision | Rationale |
| - | -------- | --------- |
| D1 | **Install source: PyPI only** | `pip install claude-wayfinder` with no path argument resolves charges 4 (Windows path quoting) and 17 (wheel platform matrix) by construction. Commits PyPI publication to the release pipeline. v0.4.0 becomes the first PyPI release. |
| D2 | **Banner surface: SessionStart `additionalContext` only** | Single non-noisy surface. Trade: if user dismisses banner, UserPromptSubmit silent-no-op makes dispatch look broken. Acceptable for the target audience (Claude Code power-users). |
| D3 | **Re-setup trigger: cheap checks per hook + SessionStart-only import probe** | Per-hook: read flag + `fs.existsSync(venv_path/python)`. SessionStart additionally runs `<venvPython> -c "import claude_wayfinder"` once per session. Subprocess cost paid once, not per-prompt. |
| D4 | **Destructive ops: always wipe + recreate** | `/setup-wayfinder` always deletes `${CLAUDE_PLUGIN_DATA}/venv/` before recreating. Predictable, no partial-state edge cases, self-healing across interruptions. |
| D5 | **Trigger: slash command + natural-language triggers** | Skill description includes triggers like "set up claude-wayfinder", "install wayfinder dependencies", "wayfinder isn't working", "fix wayfinder". Description tuned to avoid false-positive matches on casual mentions. |
| D6 | **Discovery fail: ask user for absolute path** | If `$CLAUDE_WAYFINDER_BOOTSTRAP_PYTHON` → `py -3` (Windows) → `python3` → `python` all fail to probe Python ≥3.11, skill asks user. Provided path is persisted in the flag's `interpreter` field for re-setup reuse. |
| D7 | **Code organization: hybrid** | Skill body owns LLM-judgment surface (discovery, recovery, user dialogue). `hooks/lib/setup-state.js` owns deterministic logic (flag I/O, version comparison, path resolution). Hooks call helper; helper is testable; skill is reviewable as prose. |

## § 3. Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│  User installs plugin via /plugin install glitchwerks/claude-wayfinder│
└──────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│  Next session start                                                    │
│  ─────────────────────                                                 │
│  SessionStart hooks fire (parallel per Anthropic docs):                │
│                                                                        │
│    check-catalog-health.js                                             │
│      │                                                                 │
│      ├── setupState.read()  →  returns FlagStatus enum                 │
│      │      ├── MISSING (flag absent)                                  │
│      │      ├── STALE (version mismatch)                               │
│      │      ├── BROKEN (path exists but venv-Python doesn't)           │
│      │      └── VALID (flag + path + version all match)                │
│      │                                                                 │
│      ├── If VALID: additionally probe `python -c 'import              │
│      │   claude_wayfinder'`.  Failure → flag deleted, becomes MISSING  │
│      │                                                                 │
│      └── If NOT VALID: emit additionalContext banner                   │
│           "⚠ claude-wayfinder requires setup. Run /setup-wayfinder."   │
└──────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│  User reads banner, runs /setup-wayfinder                              │
│  ─────────────────────────────────────────                             │
│  Skill (LLM-driven):                                                   │
│    1. Resolve ${CLAUDE_PLUGIN_DATA} path manually                      │
│    2. Discover Python (D6):                                            │
│         flag.interpreter (from prior run) →                            │
│         $CLAUDE_WAYFINDER_BOOTSTRAP_PYTHON →                           │
│         `py -3` on Windows →                                           │
│         `python3` → `python`                                           │
│       Probe each; on total failure, ASK USER for absolute path         │
│    3. Wipe ${CLAUDE_PLUGIN_DATA}/venv/ if it exists (D4)               │
│    4. Create venv: `<python> -m venv <data>/venv`                      │
│    5. Install: `<venv>/bin/python -m pip install claude-wayfinder==X`  │
│       (X = plugin's current version, read from pyproject.toml)         │
│    6. Verify: `<venv>/bin/python -c "import claude_wayfinder"`         │
│    7. setupState.write({ version, venv_path, interpreter,             │
│         installed_at })                                                │
│    8. Tell user: "Setup complete. Open a new session to activate."     │
└──────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│  Subsequent sessions                                                   │
│  ────────────────────                                                  │
│  SessionStart:                                                         │
│    check-catalog-health.js calls setupState.read() → VALID            │
│    → no banner, proceed to normal catalog health check                 │
│                                                                        │
│  UserPromptSubmit:                                                     │
│    refresh-catalog-on-stale.js calls setupState.read():               │
│      VALID    → use flag.venv_path to spawn Python (existing logic)   │
│      MISSING  → exit 0 silently                                        │
│      STALE    → exit 0 silently (banner fires next session)           │
│      BROKEN   → exit 0 silently (banner fires next session)           │
└──────────────────────────────────────────────────────────────────────┘
```

### Key invariants

- **No hook ever spawns Python without first reading a VALID flag.** The hook check is `setupState.read() === VALID` + `fs.existsSync(flag.venv_path + python-suffix)` — both cheap, no subprocess.
- **The `python -c 'import claude_wayfinder'` probe runs at most once per session,** inside `check-catalog-health.js` only.
- **The setup skill never runs from a hook.** It's user-invoked exclusively. Hooks emit banners; they do not orchestrate.
- **`${CLAUDE_PLUGIN_DATA}` is resolved deterministically** from the documented path shape `~/.claude/plugins/data/{id}/` (where `{id}` is the plugin identifier with non-alphanumeric → `-`) — skill computes it manually so it doesn't depend on env-var injection into skill subprocess context.
- **PyPI is the only install source.** Skill runs `pip install claude-wayfinder==<version>` (version-pinned to match plugin's current version). Network failure → fail loud → user retries.

## § 4. Components

### 4.1 New: `skills/setup-wayfinder/SKILL.md`

User-facing entry point. Frontmatter triggers + body instructions for the LLM.

```yaml
---
name: setup-wayfinder
description: |
  Materialize the claude-wayfinder Python venv at ${CLAUDE_PLUGIN_DATA}/venv/
  and write the setup-state flag. Use when:
  - User types /setup-wayfinder
  - User says "set up claude-wayfinder", "install wayfinder dependencies"
  - User says "wayfinder isn't working", "fix wayfinder", "repair wayfinder"
  - SessionStart banner indicates setup is required and the user wants to proceed
  - Plugin version bumped and re-setup needed
  Do NOT trigger on casual mentions of "wayfinder" without setup/install/fix intent.
---
```

Body (~150 LOC): numbered checklist matching the 8-step pipeline from § 3, with explicit guidance for each step — `${CLAUDE_PLUGIN_DATA}` resolution, discovery order + probe command + ask-user fallback, wipe + create + install + verify sequence, exact flag JSON shape, success/failure reporting.

### 4.2 New: `hooks/lib/setup-state.js`

Shared deterministic helper. Pure functions; no subprocess; no LLM. ~150 LOC.

```js
// Surface contract
const { readSetupState, getVenvPython, getCurrentVersion } = require("./lib/setup-state.js");

// readSetupState(currentVersion: string) → { status, flag? }
//   status: "VALID" | "MISSING" | "STALE" | "BROKEN"

// getVenvPython(venvPath: string) → string
//   Returns Scripts/python.exe on Windows, bin/python on POSIX

// getCurrentVersion() → string
//   Reads from pyproject.toml; falls back to plugin.json
```

Internal helpers (not exported): `computePluginDataDir()`, flag JSON parsing with error tolerance.

### 4.3 Modified: `hooks/check-catalog-health.js`

SessionStart hook. Gains two responsibilities:

- **Read flag at start.** If not VALID, emit setup-required banner via `additionalContext`. Skip the rest of the catalog-health check.
- **Run the import probe once per session.** If flag is VALID, spawn `<venvPython> -c 'import claude_wayfinder'` (the only subprocess per session). On failure, delete the flag file (downgrade to MISSING), then emit banner.

Banner text:

| State | Banner |
| ----- | ------ |
| MISSING | `⚠ claude-wayfinder requires setup. Run /setup-wayfinder to materialize the Python venv. The dispatch matcher and catalog refresh are disabled until setup completes.` |
| STALE | `⚠ claude-wayfinder venv is for v<X> but plugin is v<Y>. Run /setup-wayfinder to refresh.` |
| BROKEN | `⚠ claude-wayfinder venv at <path> is unreachable or corrupt. Run /setup-wayfinder.` |

### 4.4 Modified: `hooks/refresh-catalog-on-stale.js`

UserPromptSubmit hook. Gains a guard at the top:

```js
const { readSetupState, getCurrentVersion, getVenvPython } = require("./lib/setup-state.js");
const state = readSetupState(getCurrentVersion());
if (state.status !== "VALID") {
  process.exit(0); // silent; banner fires from SessionStart
}
const venvPython = getVenvPython(state.flag.venv_path);
// existing logic continues, using venvPython instead of the
// CLAUDE_WAYFINDER_PYTHON env-var / parseCmd discovery chain
```

**Deletes ~80 LOC.** The current `parseCmd` regex parser (L281-289), the `CLAUDE_WAYFINDER_PYTHON` fallback logic (L81), and the discovery branches collapse into: read flag, use `flag.venv_path`. Args-array `spawnSync` stays.

### 4.5 Inventory: other hooks

`log-skill-invocation.js`, `log-agent-dispatch.js`, `router-drift-scanner.js`, `check-agent-dispatch-pairing.js` do not spawn Python. No changes needed.

### 4.6 Net file changes

| File | Change | Approx LOC |
| ---- | ------ | ---------- |
| `skills/setup-wayfinder/SKILL.md` | New | ~150 |
| `hooks/lib/setup-state.js` | New | ~150 |
| `hooks/check-catalog-health.js` | Modified (banner + import probe) | +60 -10 |
| `hooks/refresh-catalog-on-stale.js` | Modified (delete discovery, use flag) | +20 -80 |
| `README.md` | Modified (document setup flow) | +40 |
| `docs/integration.md` | Modified (document setup flow) | +30 |
| `tests/...` | New helper + integration tests | ~200 |
| `.github/workflows/ci.yml` | Modified (add macOS + Windows skill-smoke jobs) | +40 |

Net hook-code reduction: ~90 LOC removed from `refresh-catalog-on-stale.js` (the v0.3.x discovery scaffolding) vs ~150 LOC added in the shared helper. Complexity moves to a unit-tested module.

## § 5. Data flow scenarios

### A. Fresh install (common first-time path)

```
Plugin installed → Session 1 SessionStart: MISSING → banner shown.
User runs /setup-wayfinder → discovery, venv, install, verify, flag written.
Session 2: VALID + import probe passes → no banner; dispatch normal.
```

### B. Normal session (steady state)

```
SessionStart: VALID + probe OK → no banner; catalog refresh works.
UserPromptSubmit: VALID → flag.venv_path used directly; spawn succeeds.
```

### C. Plugin version bump (e.g., v0.4.0 → v0.4.1)

```
User runs /plugin update.
Session: flag.version == "0.4.0", getCurrentVersion() == "0.4.1" → STALE.
Banner: "venv is for v0.4.0 but plugin is v0.4.1. Run /setup-wayfinder."
User runs /setup-wayfinder → wipe old venv, recreate against new version, write new flag.
```

### D. Venv corruption

```
Session start: flag valid, path exists, but `import claude_wayfinder` fails.
check-catalog-health.js deletes the flag file, emits BROKEN/MISSING banner.
User re-runs /setup-wayfinder.
```

### E. Cross-machine sync

```
Machine A: flag has venv_path = C:\Users\alice\.claude\...\venv
Machine B (different OS or home dir; OneDrive syncs the flag):
  fs.existsSync(<machine-A path>) → false → BROKEN.
Banner fires. User runs /setup-wayfinder on machine B; flag overwritten with B's path.
```

**Explicit non-support note:** the design assumes per-machine setup is acceptable. Users actively roaming between machines run `/setup-wayfinder` once per machine. This is an acceptable cost; the alternative (machine-agnostic venv via container or symlinks) would re-introduce most of the complexity this design eliminates.

### F. Setup interrupted mid-run

```
Skill crashes between step 4 (venv created) and step 7 (flag written).
${CLAUDE_PLUGIN_DATA}/venv/ exists half-populated; no flag.
User retries: step 3 wipes the half-built venv; rest succeeds.
```

The always-wipe-first invariant (D4) handles this for free.

### G. PyPI unreachable

```
Skill step 5: pip install → exit 1.
Skill DOES NOT write flag. Surfaces stderr verbatim.
User retries when network/PyPI returns. Banner state unchanged.
```

### State transition summary

```
            ┌─────────┐
   install →│ MISSING │←─────────────────────┐
            └─────────┘                       │
                 │                            │
        /setup-wayfinder ok                   │ flag deleted by
                 │                            │ corrupt-venv detection
                 ▼                            │
            ┌─────────┐                       │
   ┌────────│  VALID  │───── import fails ───┘
   │        └─────────┘
   │             │
   │      plugin version bumps
   │             │
   │             ▼
   │        ┌─────────┐
   │        │  STALE  │
   │        └─────────┘
   │             │
   │      /setup-wayfinder
   │             │
   │             ▼
   │        ┌─────────┐
   └────────│  VALID  │
            └─────────┘

  Plus: VALID → BROKEN when venv_path stops resolving (cross-machine sync);
  fixed by /setup-wayfinder.
```

Four visible states (MISSING / VALID / STALE / BROKEN), one user action that transitions all → VALID (`/setup-wayfinder`), three system transitions (install → MISSING; import-fails → MISSING via flag delete; version-bump → STALE).

## § 6. Error handling

Eight failure surfaces. Discipline: **fail loud in the skill (LLM can help), fail silent in hooks (LLM can't).** Hooks never crash the session; the skill always tells the user what broke.

| ID | Failure | Where | Recovery |
| -- | ------- | ----- | -------- |
| F1 | No Python interpreter found | Skill step 2 | Ask user for absolute path; persist to flag's `interpreter` field |
| F2 | `python -m venv` fails | Skill step 4 | Surface stderr verbatim with diagnostic hint (e.g., `apt install python3-venv`); wipe partial; exit without writing flag |
| F3 | `pip install` fails | Skill step 5 | Surface pip stderr; **wipe half-built venv** (always-wipe invariant); user retries when ready |
| F4 | Import verification fails after install | Skill step 6 | Wipe venv; report import error; suggest `pip cache purge` and retry |
| F5 | Flag write fails | Skill step 7 | Wipe the just-built venv (otherwise orphaned); surface write error |
| F6 | Skill interrupted mid-run | Anywhere in steps 3-7 | Self-healing: next run's step 3 wipes whatever exists |
| F7 | Hook can't parse flag JSON | Any hook calling helper | `readSetupState()` catches parse error, returns `{status: "MISSING"}`; banner fires next session |
| F8 | Banner emission fails | `check-catalog-health.js` | Hook exits 0 (never blocks session); degrades to "v0.3.x silent-no-op" |

### Hook invariants

- Never spawn subprocesses on UserPromptSubmit when state ≠ VALID.
- Never throw uncaught exceptions (all callers wrap `readSetupState()` defensively).
- Never write to the flag file. Only the skill writes; only `check-catalog-health.js` deletes (on corrupt-venv detection).

## § 7. Testing strategy

Direct response to inquisitor pass-2 charge 11 (test stubbing real subprocess paths): the skill smoke test runs on every PR, on every supported platform, against real Python and real PyPI. There is **no `BOOTSTRAP_PYTHON_OVERRIDE` mock seam** in this design — no hook subprocess to mock.

### Test surfaces

| Surface | Coverage | Location |
| ------- | -------- | -------- |
| Helper unit | `readSetupState()` for every flag-state combination | `hooks/tests/setup-state.test.js` (Node) |
| Hook integration (fake-python) | `check-catalog-health.js` + `refresh-catalog-on-stale.js` via existing `spawnSync(process.execPath, [hookPath])` pattern | `hooks/tests/*.test.js` |
| Skill smoke (real Python) | Full setup pipeline end-to-end on real Python in CI | `tests/integration/test_setup_skill.py` |
| Skill/pipeline sync | Diff skill body's numbered steps against `tests/integration/setup_pipeline.py` to catch drift | `tests/test_skill_pipeline_sync.py` |

### Helper unit tests (~12 cases)

| Fixture state | Expected `status` |
| ------------- | ----------------- |
| Flag missing | `MISSING` |
| Flag exists but unparseable JSON | `MISSING` (with stderr log) |
| Flag parseable but `version` field missing | `MISSING` |
| Flag valid, version matches, venv path exists | `VALID` |
| Flag valid, version mismatch | `STALE` |
| Flag valid, version matches, venv path doesn't exist | `BROKEN` |
| Flag valid, version matches, venv exists but `python` symlink missing | `BROKEN` |
| `getCurrentVersion()` reads pyproject.toml correctly | passes |
| `getCurrentVersion()` falls back to plugin.json | passes |
| `computePluginDataDir()` deterministic from plugin ID (Windows + POSIX) | passes |
| `getVenvPython()` returns `Scripts/python.exe` on Windows | passes |
| `getVenvPython()` returns `bin/python` on POSIX | passes |

### Hook integration tests

Reuses existing pattern. Adds: MISSING/STALE/VALID/BROKEN banner assertions on `check-catalog-health.js`; silent-no-op on non-VALID for `refresh-catalog-on-stale.js`; flag-deletion behavior when import probe fails (uses a Node "fake python" shim that exits 1 on the probe command).

### Skill smoke test (the real-subprocess gate)

`tests/integration/test_setup_skill.py`, **not path-filtered, runs on every PR.**

1. Set up a fresh temp dir as fake `${CLAUDE_PLUGIN_DATA}`
2. Call the setup pipeline directly via `tests/integration/setup_pipeline.py` (executable mirror of the skill body's numbered steps)
3. Assert: venv exists at expected path, `<venv>/python -c "import claude_wayfinder"` exits 0, flag file is valid JSON with correct shape
4. Cleanup

The skill body and `setup_pipeline.py` are two views of the same operation. The `tests/test_skill_pipeline_sync.py` diff check prevents drift.

### CI matrix expansion (resolves pass-2 charge 18)

| Existing | Plus new |
| -------- | -------- |
| Lint (Ubuntu) | Skill smoke (Ubuntu 3.11) |
| Test Python 3.11 (Ubuntu) | Skill smoke (Ubuntu 3.12) |
| Test Python 3.12 (Ubuntu) | **Skill smoke (macOS 3.12)** |
| Test Node (Ubuntu) | **Skill smoke (Windows 3.12)** |
| Validate Plugin Manifest (Ubuntu) | |

Pre-release smoke (before v0.4.0 hits PyPI) installs from TestPyPI staging.

### Explicit non-test cases

- pip install with network down (F3 covers this in docs; CI runners have network)
- OneDrive / roaming profile sync conflicts (F-cross-machine; impossible to reproduce reliably)
- Microsoft Store Python shim weirdness (documented as known gotcha; users hit by it use F1's ask-user path)

## § 8. Inquisitor charge cross-check

| Pass-1 / Pass-2 charge | This design |
| ---------------------- | ----------- |
| C1: SessionStart hook ordering | **Gone** — no SessionStart bootstrap; hooks are independent. |
| C2 / C16: SessionStart blocking semantics | **Gone** — no hook blocks. `additionalContext` banner is non-blocking; one import probe at session start is the only subprocess. |
| C3 / C17: Build isolation / wheel platform matrix / "fully offline" | **Gone** — PyPI install handles all platforms; no bundling, no offline claim to defend. |
| C4: Windows path quoting through subprocess chain | **Smaller** — skill spawns Python via args-array form; pip install has no path argument (no `${CLAUDE_PLUGIN_ROOT}` interpolation). |
| C5: `${PY}` env-var collision | Renamed to `CLAUDE_WAYFINDER_BOOTSTRAP_PYTHON`; skill can also ask user (D6). |
| C6: Cross-session / cross-machine invariants | Explicit non-support note (§ 5E); hook validates path existence per fire. |
| C7: Parallel `VERSION` file truth | **Gone** — `getCurrentVersion()` reads pyproject.toml directly. |
| C8: Lint guard substring grep | **Gone** — no hook subprocess to lint against. |
| C9: Partial-state cleanup | **Gone** — always-wipe-first (D4) makes cleanup structural, not error-handling. |
| C10: Alpha release tag semantics | TBD in implementation phase; not load-bearing. PyPI release-candidate suffixes (e.g., `0.4.0rc1`) are standard. |
| C11: Test stubbing real subprocess paths | Skill smoke test on every PR + every platform; no mock seams. |
| C12: `src/` size measurement | N/A — no bundling. |
| C13: Sentinel shape | JSON `{version, venv_path, interpreter, installed_at}` (locked in § 4.2). |
| C14: `hooks/lib/` new directory | Acknowledged; this design creates it. |
| C15: `unverified:` items | None remaining; all decisions cited or empirically resolvable. |
| **NEW C16-19 from pass 2** | All four are products of the SessionStart-bootstrap design choice. None apply to this design. |

## § 9. Non-goals

- Zero-prerequisite "just install the plugin and go" UX. The explicit trade is one user-typed `/setup-wayfinder` per major version.
- Cross-machine roaming venv. Per-machine setup is the supported model.
- Bundled offline install. PyPI is required for setup.
- Automatic install on plugin update without user action. Banner makes the user act; this is by design.

## § 10. Open implementation questions (for writing-plans)

These are details for the implementation plan, not architectural concerns:

1. **Skill body authoring conventions** — markdown structure, code-fence snippets vs prose instructions, how detailed the LLM guidance needs to be for each step. Defer to `agent-authoring` skill conventions.
2. **`tests/integration/setup_pipeline.py` API shape** — function decomposition, parameters, return types. Should mirror the skill body's numbered steps for the sync check.
3. **Release sequence** — TestPyPI publication before v0.4.0; alpha-channel marketplace question.
4. **Migration path for v0.3.x consumers** — banner on first v0.4 session might want to read "this is a new architecture; your previous `CLAUDE_WAYFINDER_PYTHON` env-var is no longer needed."
5. **README/docs updates** — exact prose and section organization.

## § 11. References

- Anthropic plugin docs § Persistent data directory (`${CLAUDE_PLUGIN_DATA}`), fetched 2026-05-17 via WebFetch on https://code.claude.com/docs/en/plugins-reference
- Anthropic hooks docs (complete event list), fetched 2026-05-17 via WebFetch on https://code.claude.com/docs/en/hooks
- v0.3.x regression history: PRs #67 / #71 / #77 / #84 / #88; release commits a5120fa / af08df4 / a2eba19 / 3f9fba3 / a2c7745
- v0.4 SessionStart-bootstrap predecessor and critique track: deferred plan, planner revision, both inquisitor passes (paths listed in this document's header)
- Brainstorm session decisions D1-D7 captured in epic [#99](https://github.com/glitchwerks/claude-wayfinder/issues/99)
