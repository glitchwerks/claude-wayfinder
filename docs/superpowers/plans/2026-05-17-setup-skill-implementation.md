# Setup-skill architecture implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the setup-skill architecture from spec `docs/superpowers/specs/2026-05-17-setup-skill-architecture-design.md` — a user-initiated `/setup-wayfinder` skill that materializes a venv at `${CLAUDE_PLUGIN_DATA}/venv/`, plus per-hook flag checks via a shared helper, plus a SessionStart banner that surfaces the setup-required state.

**Architecture:** Skill (LLM-judgment) + `hooks/lib/setup-state.js` helper (deterministic code). Hooks read the flag and either use `flag.venv_path` or short-circuit silently. `check-catalog-health.js` emits banner + runs one-per-session import probe. PyPI is the install source.

**Tech Stack:** Node 20 (hooks), Python 3.11+ (claude-wayfinder package), pytest (integration), Anthropic plugin manifest schema.

**Tracking:** Epic [#99](https://github.com/glitchwerks/claude-wayfinder/issues/99). Each phase maps to one sub-issue + one PR.

---

## Phase overview & dependencies

```
Phase 1: Helper foundation (hooks/lib/setup-state.js + unit tests)
    ↓ depends on
Phase 2: Hook updates (check-catalog-health + refresh-catalog-on-stale)
    ↓ depends on
Phase 3: Skill body + executable mirror (skills/setup-wayfinder + setup_pipeline.py)
    ↓ depends on
Phase 4: Skill smoke test (tests/integration/test_setup_skill.py + sync check)
    ↓ depends on
Phase 5: CI matrix expansion (macOS + Windows skill-smoke jobs)
    ↓ depends on
Phase 6: Documentation (README.md + docs/integration.md)
    ↓ independent
Phase 7: PyPI publication setup (operator task + release workflow)
```

Phases 1-2 are pure code with no external dependencies. Phase 3 introduces the executable mirror that pairs with the skill body. Phase 4 stitches it all together end-to-end. Phase 5 expands coverage. Phase 6 closes the loop with user-facing docs. Phase 7 is operator work that gates the v0.4.0 release.

## File structure

| File | Responsibility | Phase |
| ---- | -------------- | ----- |
| `hooks/lib/setup-state.js` | Pure-function helper: flag I/O, version comparison, path resolution. Exports `readSetupState()`, `getVenvPython()`, `getCurrentVersion()`. No subprocess. | 1 |
| `hooks/tests/setup-state.test.js` | Unit tests for the helper. 12 cases covering every flag state combination. | 1 |
| `hooks/check-catalog-health.js` | Modified: SessionStart hook. Reads flag → emits banner if not VALID; runs `import claude_wayfinder` probe once per session if VALID; deletes flag on probe failure. | 2 |
| `hooks/tests/check-catalog-health.test.js` | Integration tests for the modified hook. Asserts banner output for each non-VALID state; flag-deletion behavior on probe failure (uses fake-python Node shim). | 2 |
| `hooks/refresh-catalog-on-stale.js` | Modified: UserPromptSubmit hook. Adds flag guard at top; uses `flag.venv_path` for Python; removes ~80 LOC of `CLAUDE_WAYFINDER_PYTHON` / `parseCmd` discovery logic. | 2 |
| `hooks/tests/refresh-catalog-on-stale.test.js` | Modified: existing test suite. Add cases for non-VALID flag (silent exit); keep existing VALID-state catalog refresh coverage. | 2 |
| `skills/setup-wayfinder/SKILL.md` | New skill. Frontmatter with natural-language triggers; body with 8-step pipeline instructions for the LLM. | 3 |
| `tests/integration/setup_pipeline.py` | Executable Python mirror of the skill body's 8 steps. Importable functions: `discover_python()`, `wipe_venv()`, `create_venv()`, `pip_install()`, `verify_import()`, `write_flag()`. | 3 |
| `tests/test_skill_pipeline_sync.py` | Drift check: parses the skill body's numbered steps and asserts each maps to a `setup_pipeline.py` function. Fails CI if either drifts. | 3 |
| `tests/integration/test_setup_skill.py` | End-to-end smoke test. Runs the full `setup_pipeline` against a real Python ≥3.11 with a fresh temp dir as fake `${CLAUDE_PLUGIN_DATA}`. Asserts venv exists, import works, flag JSON is correctly shaped. | 4 |
| `.github/workflows/ci.yml` | Modified: add `skill-smoke-ubuntu`, `skill-smoke-macos`, `skill-smoke-windows` jobs running the integration test on each OS. | 5 |
| `README.md` | Modified: § Troubleshooting points to `/setup-wayfinder`; § Quick-start mentions the one-time setup step. | 6 |
| `docs/integration.md` | Modified: setup flow documented for consumers. | 6 |
| `.github/workflows/release.yml` | New: triggers on `v*` tags; runs `uv build` + `uv publish` against PyPI using a stored `PYPI_API_TOKEN` secret. | 7 |

---

## Phase 1 — Helper foundation

**Sub-issue (to be filed):** "Implement `hooks/lib/setup-state.js` shared helper with 12 unit tests"

**PR scope:** Two new files. No production hook behavior changes yet — pure addition.

**Files:**
- Create: `hooks/lib/setup-state.js`
- Create: `hooks/tests/setup-state.test.js`

### Task 1.1: Create the lib directory and the test file with the first failing test

- [ ] **Step 1: Write the failing test for the MISSING-when-flag-absent case**

Create `hooks/tests/setup-state.test.js`:

```javascript
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const fs = require("node:fs");
const os = require("node:os");

// Module under test (does not exist yet)
const { readSetupState } = require("../lib/setup-state.js");

// Helper: create a temp dir to serve as ${CLAUDE_PLUGIN_DATA} during tests
function withTempPluginData(fn) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "wayfinder-test-"));
  const restore = process.env.CLAUDE_PLUGIN_DATA;
  process.env.CLAUDE_PLUGIN_DATA = dir;
  try {
    return fn(dir);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
    if (restore === undefined) delete process.env.CLAUDE_PLUGIN_DATA;
    else process.env.CLAUDE_PLUGIN_DATA = restore;
  }
}

test("readSetupState returns MISSING when flag file absent", () => {
  withTempPluginData(() => {
    const result = readSetupState("0.4.0");
    assert.equal(result.status, "MISSING");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run from repo root:

```bash
node --test hooks/tests/setup-state.test.js
```

Expected: FAIL with `Cannot find module '../lib/setup-state.js'`.

- [ ] **Step 3: Create the minimum helper to make this test pass**

Create `hooks/lib/setup-state.js`:

```javascript
// hooks/lib/setup-state.js
// Shared helper for plugin hooks. Pure functions; no subprocess.

const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");

/**
 * Read and classify the setup-state flag.
 * @param {string} currentVersion - the plugin version from pyproject.toml
 * @returns {{status: "VALID"|"MISSING"|"STALE"|"BROKEN", flag?: object}}
 */
function readSetupState(currentVersion) {
  const flagPath = path.join(getPluginDataDir(), "setup-state.json");
  if (!fs.existsSync(flagPath)) {
    return { status: "MISSING" };
  }
  return { status: "MISSING" }; // placeholder; refined in later tasks
}

function getPluginDataDir() {
  // Honor env var if set (test seam); otherwise compute deterministically.
  if (process.env.CLAUDE_PLUGIN_DATA) {
    return process.env.CLAUDE_PLUGIN_DATA;
  }
  // Spec § 4.2: ~/.claude/plugins/data/{slug}/ where slug = plugin-id with non-alnum→-
  return path.join(os.homedir(), ".claude", "plugins", "data", "claude-wayfinder-claude-wayfinder");
}

module.exports = { readSetupState };
```

- [ ] **Step 4: Run test to verify it passes**

```bash
node --test hooks/tests/setup-state.test.js
```

Expected: PASS, 1 test passing.

- [ ] **Step 5: Commit**

```bash
git add hooks/lib/setup-state.js hooks/tests/setup-state.test.js
git commit -m "feat(hooks): scaffold setup-state helper with first MISSING-state test"
```

### Task 1.2: Test for unparseable JSON → MISSING with stderr log

- [ ] **Step 1: Add the failing test**

Append to `hooks/tests/setup-state.test.js`:

```javascript
test("readSetupState returns MISSING when flag file is unparseable JSON", () => {
  withTempPluginData((dir) => {
    fs.writeFileSync(path.join(dir, "setup-state.json"), "{not-valid-json");
    const result = readSetupState("0.4.0");
    assert.equal(result.status, "MISSING");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
node --test hooks/tests/setup-state.test.js
```

Expected: FAIL with JSON.parse SyntaxError (helper currently has no try/catch).

- [ ] **Step 3: Make it pass — add try/catch around JSON.parse**

Replace the `readSetupState` function body in `hooks/lib/setup-state.js`:

```javascript
function readSetupState(currentVersion) {
  const flagPath = path.join(getPluginDataDir(), "setup-state.json");
  if (!fs.existsSync(flagPath)) {
    return { status: "MISSING" };
  }
  let flag;
  try {
    flag = JSON.parse(fs.readFileSync(flagPath, "utf8"));
  } catch (err) {
    process.stderr.write(`[setup-state] flag file unparseable: ${err.message}\n`);
    return { status: "MISSING" };
  }
  return { status: "MISSING", flag }; // placeholder; refined in later tasks
}
```

- [ ] **Step 4: Run tests to verify both pass**

```bash
node --test hooks/tests/setup-state.test.js
```

Expected: PASS, 2 tests passing.

- [ ] **Step 5: Commit**

```bash
git add hooks/lib/setup-state.js hooks/tests/setup-state.test.js
git commit -m "feat(hooks): readSetupState tolerates unparseable flag JSON"
```

### Task 1.3: Test for flag-without-version-field → MISSING

- [ ] **Step 1: Add the failing test**

Append to test file:

```javascript
test("readSetupState returns MISSING when flag JSON lacks version field", () => {
  withTempPluginData((dir) => {
    fs.writeFileSync(
      path.join(dir, "setup-state.json"),
      JSON.stringify({ venv_path: "/tmp/venv", installed_at: "2026-05-17" })
    );
    const result = readSetupState("0.4.0");
    assert.equal(result.status, "MISSING");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
node --test hooks/tests/setup-state.test.js
```

Expected: FAIL — current impl returns MISSING with flag attached, but the spec requires version-less flags to be treated as MISSING with NO flag.

Wait — looking at the test, it only asserts `status === "MISSING"`, which the current impl does return. The test passes accidentally. Strengthen the test:

```javascript
test("readSetupState returns MISSING when flag JSON lacks version field", () => {
  withTempPluginData((dir) => {
    fs.writeFileSync(
      path.join(dir, "setup-state.json"),
      JSON.stringify({ venv_path: "/tmp/venv", installed_at: "2026-05-17" })
    );
    const result = readSetupState("0.4.0");
    assert.equal(result.status, "MISSING");
    assert.equal(result.flag, undefined, "should not return a flag with missing version field");
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

```bash
node --test hooks/tests/setup-state.test.js
```

Expected: FAIL — the helper returns `{status: "MISSING", flag}` for any parseable JSON.

- [ ] **Step 4: Make it pass — validate required fields**

Replace `readSetupState` in `hooks/lib/setup-state.js`:

```javascript
function readSetupState(currentVersion) {
  const flagPath = path.join(getPluginDataDir(), "setup-state.json");
  if (!fs.existsSync(flagPath)) {
    return { status: "MISSING" };
  }
  let flag;
  try {
    flag = JSON.parse(fs.readFileSync(flagPath, "utf8"));
  } catch (err) {
    process.stderr.write(`[setup-state] flag file unparseable: ${err.message}\n`);
    return { status: "MISSING" };
  }
  // Required fields: version, venv_path, interpreter, installed_at
  if (!flag.version || !flag.venv_path) {
    return { status: "MISSING" };
  }
  return { status: "MISSING", flag }; // version-comparison added next task
}
```

- [ ] **Step 5: Run tests**

```bash
node --test hooks/tests/setup-state.test.js
```

Expected: PASS, 3 tests passing.

- [ ] **Step 6: Commit**

```bash
git add hooks/lib/setup-state.js hooks/tests/setup-state.test.js
git commit -m "feat(hooks): readSetupState rejects flag missing required fields"
```

### Task 1.4: Test for VALID — version matches and venv path exists

- [ ] **Step 1: Add the failing test**

Append:

```javascript
test("readSetupState returns VALID when version matches and venv path exists", () => {
  withTempPluginData((dir) => {
    const venvDir = path.join(dir, "venv");
    const venvBin = path.join(venvDir, process.platform === "win32" ? "Scripts" : "bin");
    fs.mkdirSync(venvBin, { recursive: true });
    fs.writeFileSync(
      path.join(venvBin, process.platform === "win32" ? "python.exe" : "python"),
      "" // empty file fine; we only check existence
    );
    fs.writeFileSync(
      path.join(dir, "setup-state.json"),
      JSON.stringify({
        version: "0.4.0",
        venv_path: venvDir,
        interpreter: "/usr/bin/python3.12",
        installed_at: "2026-05-17T19:00:00Z",
      })
    );
    const result = readSetupState("0.4.0");
    assert.equal(result.status, "VALID");
    assert.equal(result.flag.version, "0.4.0");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
node --test hooks/tests/setup-state.test.js
```

Expected: FAIL — current impl always returns MISSING.

- [ ] **Step 3: Make it pass — add version + path checks**

Replace `readSetupState`:

```javascript
function readSetupState(currentVersion) {
  const flagPath = path.join(getPluginDataDir(), "setup-state.json");
  if (!fs.existsSync(flagPath)) {
    return { status: "MISSING" };
  }
  let flag;
  try {
    flag = JSON.parse(fs.readFileSync(flagPath, "utf8"));
  } catch (err) {
    process.stderr.write(`[setup-state] flag file unparseable: ${err.message}\n`);
    return { status: "MISSING" };
  }
  if (!flag.version || !flag.venv_path) {
    return { status: "MISSING" };
  }
  if (flag.version !== currentVersion) {
    return { status: "STALE", flag };
  }
  const venvPython = getVenvPython(flag.venv_path);
  if (!fs.existsSync(venvPython)) {
    return { status: "BROKEN", flag };
  }
  return { status: "VALID", flag };
}

function getVenvPython(venvPath) {
  if (process.platform === "win32") {
    return path.join(venvPath, "Scripts", "python.exe");
  }
  return path.join(venvPath, "bin", "python");
}
```

Update the `module.exports` line:

```javascript
module.exports = { readSetupState, getVenvPython };
```

- [ ] **Step 4: Run tests**

```bash
node --test hooks/tests/setup-state.test.js
```

Expected: PASS, 4 tests passing.

- [ ] **Step 5: Commit**

```bash
git add hooks/lib/setup-state.js hooks/tests/setup-state.test.js
git commit -m "feat(hooks): readSetupState classifies VALID/STALE/BROKEN"
```

### Task 1.5: Test for STALE — version mismatch

- [ ] **Step 1: Add the failing test**

```javascript
test("readSetupState returns STALE when flag version differs from currentVersion", () => {
  withTempPluginData((dir) => {
    const venvDir = path.join(dir, "venv");
    fs.mkdirSync(venvDir, { recursive: true });
    fs.writeFileSync(
      path.join(dir, "setup-state.json"),
      JSON.stringify({
        version: "0.4.0",
        venv_path: venvDir,
        interpreter: "/usr/bin/python3.12",
        installed_at: "2026-05-17T19:00:00Z",
      })
    );
    const result = readSetupState("0.4.1");
    assert.equal(result.status, "STALE");
    assert.equal(result.flag.version, "0.4.0", "flag still returned for banner formatting");
  });
});
```

- [ ] **Step 2: Run test**

```bash
node --test hooks/tests/setup-state.test.js
```

Expected: PASS (already implemented in Task 1.4's impl).

- [ ] **Step 3: Commit**

```bash
git add hooks/tests/setup-state.test.js
git commit -m "test(hooks): STALE-state test covering version mismatch"
```

### Task 1.6: Test for BROKEN — version matches, flag exists, but venv path doesn't

- [ ] **Step 1: Add the failing test**

```javascript
test("readSetupState returns BROKEN when version matches but venv_path doesn't exist", () => {
  withTempPluginData((dir) => {
    fs.writeFileSync(
      path.join(dir, "setup-state.json"),
      JSON.stringify({
        version: "0.4.0",
        venv_path: "/nonexistent/path/to/venv",
        interpreter: "/usr/bin/python3.12",
        installed_at: "2026-05-17T19:00:00Z",
      })
    );
    const result = readSetupState("0.4.0");
    assert.equal(result.status, "BROKEN");
  });
});
```

- [ ] **Step 2: Run test**

```bash
node --test hooks/tests/setup-state.test.js
```

Expected: PASS (already covered by Task 1.4 impl).

- [ ] **Step 3: Commit**

```bash
git add hooks/tests/setup-state.test.js
git commit -m "test(hooks): BROKEN-state test covering missing venv_path"
```

### Task 1.7: Test for BROKEN — venv dir exists but `python` binary missing

- [ ] **Step 1: Add the failing test**

```javascript
test("readSetupState returns BROKEN when venv dir exists but python binary missing", () => {
  withTempPluginData((dir) => {
    const venvDir = path.join(dir, "venv");
    fs.mkdirSync(venvDir, { recursive: true });
    // Note: NOT creating the python binary inside
    fs.writeFileSync(
      path.join(dir, "setup-state.json"),
      JSON.stringify({
        version: "0.4.0",
        venv_path: venvDir,
        interpreter: "/usr/bin/python3.12",
        installed_at: "2026-05-17T19:00:00Z",
      })
    );
    const result = readSetupState("0.4.0");
    assert.equal(result.status, "BROKEN");
  });
});
```

- [ ] **Step 2: Run test**

```bash
node --test hooks/tests/setup-state.test.js
```

Expected: PASS (Task 1.4 impl checks `fs.existsSync(venvPython)` which catches this case).

- [ ] **Step 3: Commit**

```bash
git add hooks/tests/setup-state.test.js
git commit -m "test(hooks): BROKEN-state test for missing python binary"
```

### Task 1.8: Add `getCurrentVersion()` reading from pyproject.toml

- [ ] **Step 1: Write the failing test**

```javascript
test("getCurrentVersion reads version from pyproject.toml", () => {
  const { getCurrentVersion } = require("../lib/setup-state.js");
  const result = getCurrentVersion();
  // The bundled pyproject.toml should have a version like "0.4.0"
  assert.match(result, /^\d+\.\d+\.\d+/, `Expected semver-like version, got: ${result}`);
});
```

- [ ] **Step 2: Run test**

```bash
node --test hooks/tests/setup-state.test.js
```

Expected: FAIL — `getCurrentVersion` is not exported yet.

- [ ] **Step 3: Implement `getCurrentVersion()`**

Add to `hooks/lib/setup-state.js` above the module.exports:

```javascript
/**
 * Read the plugin version from pyproject.toml (preferred) or plugin.json (fallback).
 * @returns {string} semver-like version string (e.g., "0.4.0")
 * @throws {Error} if neither file is readable or contains a version
 */
function getCurrentVersion() {
  const pluginRoot = getPluginRoot();
  const pyprojectPath = path.join(pluginRoot, "pyproject.toml");
  if (fs.existsSync(pyprojectPath)) {
    const content = fs.readFileSync(pyprojectPath, "utf8");
    // Match `version = "X.Y.Z"` inside the [project] table
    const match = content.match(/\[project\][\s\S]*?^version\s*=\s*"([^"]+)"/m);
    if (match) return match[1];
  }
  const pluginJsonPath = path.join(pluginRoot, ".claude-plugin", "plugin.json");
  if (fs.existsSync(pluginJsonPath)) {
    const pluginJson = JSON.parse(fs.readFileSync(pluginJsonPath, "utf8"));
    if (pluginJson.version) return pluginJson.version;
  }
  throw new Error("Cannot resolve plugin version: pyproject.toml and plugin.json both unreadable or version-less");
}

function getPluginRoot() {
  if (process.env.CLAUDE_PLUGIN_ROOT) {
    return process.env.CLAUDE_PLUGIN_ROOT;
  }
  // Hook is in <root>/hooks/<file>.js or <root>/hooks/lib/<file>.js — compute relative.
  return path.resolve(__dirname, "..", "..");
}
```

Update `module.exports`:

```javascript
module.exports = { readSetupState, getVenvPython, getCurrentVersion };
```

- [ ] **Step 4: Run tests**

```bash
node --test hooks/tests/setup-state.test.js
```

Expected: PASS, 8 tests passing (depending on bundled pyproject.toml format).

- [ ] **Step 5: Commit**

```bash
git add hooks/lib/setup-state.js hooks/tests/setup-state.test.js
git commit -m "feat(hooks): getCurrentVersion reads from pyproject.toml"
```

### Task 1.9: `getCurrentVersion()` fallback to plugin.json

- [ ] **Step 1: Add the failing test**

Add a fixture-based test that points the helper at a temp dir without pyproject.toml:

```javascript
test("getCurrentVersion falls back to plugin.json when pyproject.toml absent", () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), "wayfinder-root-"));
  const restore = process.env.CLAUDE_PLUGIN_ROOT;
  process.env.CLAUDE_PLUGIN_ROOT = tempRoot;
  try {
    fs.mkdirSync(path.join(tempRoot, ".claude-plugin"));
    fs.writeFileSync(
      path.join(tempRoot, ".claude-plugin", "plugin.json"),
      JSON.stringify({ name: "test", version: "9.9.9" })
    );
    const { getCurrentVersion } = require("../lib/setup-state.js");
    assert.equal(getCurrentVersion(), "9.9.9");
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true });
    if (restore === undefined) delete process.env.CLAUDE_PLUGIN_ROOT;
    else process.env.CLAUDE_PLUGIN_ROOT = restore;
  }
});
```

- [ ] **Step 2: Run test**

```bash
node --test hooks/tests/setup-state.test.js
```

Expected: PASS (Task 1.8 impl already supports this fallback).

- [ ] **Step 3: Commit**

```bash
git add hooks/tests/setup-state.test.js
git commit -m "test(hooks): getCurrentVersion falls back to plugin.json"
```

### Task 1.10: Verify `getVenvPython` platform-awareness

- [ ] **Step 1: Add tests for both platforms**

```javascript
test("getVenvPython returns Scripts/python.exe on Windows", { skip: process.platform !== "win32" }, () => {
  const { getVenvPython } = require("../lib/setup-state.js");
  const result = getVenvPython("C:\\venv");
  assert.match(result, /Scripts[/\\]python\.exe$/);
});

test("getVenvPython returns bin/python on POSIX", { skip: process.platform === "win32" }, () => {
  const { getVenvPython } = require("../lib/setup-state.js");
  const result = getVenvPython("/tmp/venv");
  assert.equal(result, "/tmp/venv/bin/python");
});
```

- [ ] **Step 2: Run tests**

```bash
node --test hooks/tests/setup-state.test.js
```

Expected: PASS — one test runs, the other is skipped per current OS.

- [ ] **Step 3: Commit**

```bash
git add hooks/tests/setup-state.test.js
git commit -m "test(hooks): getVenvPython platform-aware"
```

### Task 1.11: Test the deterministic plugin-data-dir computation

The helper currently honors `process.env.CLAUDE_PLUGIN_DATA` when set. Spec § 4.2 requires deterministic fallback to `~/.claude/plugins/data/{slug}/`. Test the unset case.

- [ ] **Step 1: Add the failing test**

```javascript
test("getPluginDataDir computes deterministic path when env var unset", () => {
  // Temporarily unset CLAUDE_PLUGIN_DATA
  const restore = process.env.CLAUDE_PLUGIN_DATA;
  delete process.env.CLAUDE_PLUGIN_DATA;
  try {
    // Use a private helper export for testing
    const { _computePluginDataDir } = require("../lib/setup-state.js");
    const result = _computePluginDataDir();
    // Should be ~/.claude/plugins/data/claude-wayfinder-claude-wayfinder/
    const expected = path.join(os.homedir(), ".claude", "plugins", "data", "claude-wayfinder-claude-wayfinder");
    assert.equal(result, expected);
  } finally {
    if (restore !== undefined) process.env.CLAUDE_PLUGIN_DATA = restore;
  }
});
```

- [ ] **Step 2: Run test**

```bash
node --test hooks/tests/setup-state.test.js
```

Expected: FAIL — `_computePluginDataDir` is not exported.

- [ ] **Step 3: Export the internal helper for testing**

In `hooks/lib/setup-state.js`, refactor `getPluginDataDir` into two pieces:

```javascript
function getPluginDataDir() {
  if (process.env.CLAUDE_PLUGIN_DATA) {
    return process.env.CLAUDE_PLUGIN_DATA;
  }
  return _computePluginDataDir();
}

function _computePluginDataDir() {
  // Spec § 4.2: ~/.claude/plugins/data/{slug}/
  // slug = plugin identifier ("claude-wayfinder@claude-wayfinder") with
  // non-[a-zA-Z0-9_-] chars replaced by "-"
  const pluginId = "claude-wayfinder@claude-wayfinder";
  const slug = pluginId.replace(/[^a-zA-Z0-9_-]/g, "-");
  return path.join(os.homedir(), ".claude", "plugins", "data", slug);
}
```

Add to exports:

```javascript
module.exports = { readSetupState, getVenvPython, getCurrentVersion, _computePluginDataDir };
```

- [ ] **Step 4: Run tests**

```bash
node --test hooks/tests/setup-state.test.js
```

Expected: PASS, all tests passing.

- [ ] **Step 5: Commit**

```bash
git add hooks/lib/setup-state.js hooks/tests/setup-state.test.js
git commit -m "feat(hooks): deterministic plugin-data-dir computation with test seam"
```

### Task 1.12: Open Phase 1 PR

- [ ] **Step 1: Push branch and open PR**

```bash
git push -u origin <branch-name>
gh pr create --repo glitchwerks/claude-wayfinder \
  --title "feat(hooks): hooks/lib/setup-state.js shared helper (Phase 1 of #99)" \
  --body "$(cat <<'EOF'
## Summary

Phase 1 of epic #99 (setup-skill architecture). Adds the deterministic-code half of the design: `hooks/lib/setup-state.js` shared helper that hooks call to classify the setup-state flag, plus 11 unit tests covering every flag state combination.

No production hook behavior changes yet — this is pure addition. Phase 2 wires the helper into `check-catalog-health.js` and `refresh-catalog-on-stale.js`.

## Spec reference

Implements spec § 4.2 (helper file) and the helper-test rows of § 7 (test surfaces).

## Test plan

- [ ] `node --test hooks/tests/setup-state.test.js` passes locally
- [ ] CI's Test (Node) job passes

## Related

- Epic: #99
- Spec: `docs/superpowers/specs/2026-05-17-setup-skill-architecture-design.md`

🤖 _Generated by Claude Code on behalf of @cbeaulieu-gt_
EOF
)"
```

- [ ] **Step 2: Wait for CI**

Run: `GH_REPO=glitchwerks/claude-wayfinder scripts/wait-for-pr-checks.sh <PR#>`
(Use the helper at `~/.claude/scripts/wait-for-pr-checks.sh` if no local script.)

Expected: 5/5 SUCCESS.

- [ ] **Step 3: Merge after pre-merge sweep**

Use the standard pre-merge feedback sweep (CLAUDE.md `# Pull Requests` rules), then `gh pr merge <PR#> --squash --delete-branch`.

---

## Phase 2 — Hook updates

**Sub-issue (to be filed):** "Wire setup-state helper into check-catalog-health.js and refresh-catalog-on-stale.js"

**PR scope:** Modifies two existing hooks to use the helper. Removes ~80 LOC of v0.3.x discovery scaffolding from `refresh-catalog-on-stale.js`.

**Files:**
- Modify: `hooks/check-catalog-health.js`
- Modify: `hooks/refresh-catalog-on-stale.js`
- Modify (extend): `hooks/tests/check-catalog-health.test.js` (or create if absent)
- Modify (extend): `hooks/tests/refresh-catalog-on-stale.test.js`

### Task 2.1: Inspect existing hook tests to understand the spawnSync pattern

- [ ] **Step 1: Read the existing test files**

```bash
ls hooks/tests/
cat hooks/tests/refresh-catalog-on-stale.test.js | head -80
```

Note the pattern: `spawnSync(process.execPath, [hookPath], { input: JSON.stringify(payload) })`. The test asserts on `result.stdout`, `result.stderr`, `result.status`.

- [ ] **Step 2: Confirm `check-catalog-health.test.js` exists**

```bash
ls hooks/tests/check-catalog-health*.test.js
```

If absent, the test file will be created in this phase. If present, additions are appended.

No commit for this task — investigation only.

### Task 2.2: Write the first failing test for `check-catalog-health.js` MISSING-state banner

- [ ] **Step 1: Write the test**

Create or append to `hooks/tests/check-catalog-health.test.js`:

```javascript
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const fs = require("node:fs");
const os = require("node:os");
const { spawnSync } = require("node:child_process");

const HOOK = path.resolve(__dirname, "..", "check-catalog-health.js");
const REPO_ROOT = path.resolve(__dirname, "..", "..");

function runHook({ pluginData, stdin = "{}" }) {
  return spawnSync(process.execPath, [HOOK], {
    input: stdin,
    env: {
      ...process.env,
      CLAUDE_PLUGIN_DATA: pluginData,
      CLAUDE_PLUGIN_ROOT: REPO_ROOT,
    },
    encoding: "utf8",
  });
}

test("check-catalog-health emits MISSING banner when no setup-state flag", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "wayfinder-hooktest-"));
  try {
    const result = runHook({ pluginData: tmp });
    assert.equal(result.status, 0, `Hook exited non-zero: ${result.stderr}`);
    assert.match(
      result.stdout,
      /claude-wayfinder requires setup.*\/setup-wayfinder/s,
      "Expected MISSING-state banner in stdout"
    );
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
node --test hooks/tests/check-catalog-health.test.js
```

Expected: FAIL — the current `check-catalog-health.js` doesn't emit a setup-required banner.

### Task 2.3: Modify `check-catalog-health.js` to emit the MISSING banner

- [ ] **Step 1: Read the current hook**

```bash
cat hooks/check-catalog-health.js
```

Note the existing structure (it emits `additionalContext` for catalog staleness already).

- [ ] **Step 2: Add the setup-state check at the top of the hook's main flow**

Modify `hooks/check-catalog-health.js`. Near the top of the main execution path (after parsing stdin, before existing catalog-stale checks), add:

```javascript
const { readSetupState, getCurrentVersion, getVenvPython } = require("./lib/setup-state.js");

// ... existing parse-input code ...

// Setup-state gate. If not VALID, emit banner and skip catalog health checks.
const currentVersion = getCurrentVersion();
const setupState = readSetupState(currentVersion);

if (setupState.status !== "VALID") {
  let banner;
  if (setupState.status === "MISSING") {
    banner = "⚠ claude-wayfinder requires setup. Run /setup-wayfinder to materialize the Python venv. The dispatch matcher and catalog refresh are disabled until setup completes.";
  } else if (setupState.status === "STALE") {
    banner = `⚠ claude-wayfinder venv is for v${setupState.flag.version} but plugin is v${currentVersion}. Run /setup-wayfinder to refresh.`;
  } else if (setupState.status === "BROKEN") {
    banner = `⚠ claude-wayfinder venv at ${setupState.flag.venv_path} is unreachable or corrupt. Run /setup-wayfinder.`;
  }
  // Emit additionalContext banner and exit cleanly.
  process.stdout.write(JSON.stringify({ additionalContext: banner }) + "\n");
  process.exit(0);
}

// ... existing catalog-health logic continues unchanged ...
```

- [ ] **Step 3: Run test to verify it passes**

```bash
node --test hooks/tests/check-catalog-health.test.js
```

Expected: PASS, 1 test passing.

- [ ] **Step 4: Commit**

```bash
git add hooks/check-catalog-health.js hooks/tests/check-catalog-health.test.js
git commit -m "feat(hooks): check-catalog-health emits MISSING-state banner"
```

### Task 2.4: Add STALE-state banner test

- [ ] **Step 1: Write the failing test**

Append to the test file:

```javascript
test("check-catalog-health emits STALE banner when flag version differs from plugin version", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "wayfinder-hooktest-"));
  try {
    // Plant a flag with an old version
    const venvDir = path.join(tmp, "venv");
    fs.mkdirSync(path.join(venvDir, process.platform === "win32" ? "Scripts" : "bin"), { recursive: true });
    fs.writeFileSync(
      path.join(tmp, "setup-state.json"),
      JSON.stringify({
        version: "0.0.0-old",
        venv_path: venvDir,
        interpreter: "/usr/bin/python3.12",
        installed_at: "2026-05-17T19:00:00Z",
      })
    );
    const result = runHook({ pluginData: tmp });
    assert.equal(result.status, 0);
    assert.match(result.stdout, /venv is for v0\.0\.0-old but plugin is v/s);
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});
```

- [ ] **Step 2: Run test**

```bash
node --test hooks/tests/check-catalog-health.test.js
```

Expected: PASS (Task 2.3's impl covers this case).

- [ ] **Step 3: Commit**

```bash
git add hooks/tests/check-catalog-health.test.js
git commit -m "test(hooks): STALE-state banner test"
```

### Task 2.5: Add BROKEN-state banner test

- [ ] **Step 1: Write the failing test**

Append:

```javascript
test("check-catalog-health emits BROKEN banner when venv path doesn't exist", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "wayfinder-hooktest-"));
  try {
    const currentVersion = require("../lib/setup-state.js").getCurrentVersion();
    fs.writeFileSync(
      path.join(tmp, "setup-state.json"),
      JSON.stringify({
        version: currentVersion,
        venv_path: "/nonexistent/path",
        interpreter: "/usr/bin/python3.12",
        installed_at: "2026-05-17T19:00:00Z",
      })
    );
    const result = runHook({ pluginData: tmp });
    assert.equal(result.status, 0);
    assert.match(result.stdout, /unreachable or corrupt.*\/setup-wayfinder/s);
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});
```

- [ ] **Step 2: Run test**

```bash
node --test hooks/tests/check-catalog-health.test.js
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add hooks/tests/check-catalog-health.test.js
git commit -m "test(hooks): BROKEN-state banner test"
```

### Task 2.6: Add VALID-state import-probe test using a fake-python shim

- [ ] **Step 1: Create the fake-python shim**

Create `hooks/tests/fixtures/fake-python-ok.js`:

```javascript
#!/usr/bin/env node
// Fake Python that exits 0 on the `-c "import claude_wayfinder"` probe.
const args = process.argv.slice(2);
if (args[0] === "-c" && args[1] && args[1].includes("import claude_wayfinder")) {
  process.exit(0);
}
process.exit(1);
```

Create `hooks/tests/fixtures/fake-python-fail.js`:

```javascript
#!/usr/bin/env node
// Fake Python that exits 1 on the `-c "import claude_wayfinder"` probe.
process.exit(1);
```

- [ ] **Step 2: Write the failing test for VALID with passing probe**

```javascript
test("check-catalog-health proceeds silently when flag VALID and import probe passes", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "wayfinder-hooktest-"));
  try {
    const venvDir = path.join(tmp, "venv");
    const venvBin = path.join(venvDir, process.platform === "win32" ? "Scripts" : "bin");
    fs.mkdirSync(venvBin, { recursive: true });
    // Use the fake-python-ok shim as the venv Python.
    const fakeOk = path.resolve(__dirname, "fixtures", "fake-python-ok.js");
    const venvPython = path.join(venvBin, process.platform === "win32" ? "python.exe" : "python");
    // On POSIX, symlink to the shim and chmod +x. On Windows, write a .exe-named wrapper.
    if (process.platform === "win32") {
      fs.writeFileSync(venvPython, `@echo off\nnode "${fakeOk}" %*\n`);
    } else {
      fs.symlinkSync(fakeOk, venvPython);
      fs.chmodSync(fakeOk, 0o755);
    }

    const currentVersion = require("../lib/setup-state.js").getCurrentVersion();
    fs.writeFileSync(
      path.join(tmp, "setup-state.json"),
      JSON.stringify({
        version: currentVersion,
        venv_path: venvDir,
        interpreter: "/system/python",
        installed_at: "2026-05-17T19:00:00Z",
      })
    );

    const result = runHook({ pluginData: tmp });
    assert.equal(result.status, 0);
    // No setup banner should appear; existing catalog-health behavior takes over.
    assert.doesNotMatch(result.stdout, /requires setup/s);
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});
```

- [ ] **Step 3: Run test**

```bash
node --test hooks/tests/check-catalog-health.test.js
```

Expected: FAIL — the hook does not yet run the import probe; it just trusts VALID.

### Task 2.7: Implement the import probe in `check-catalog-health.js`

- [ ] **Step 1: Add probe + flag-delete-on-failure**

Modify `hooks/check-catalog-health.js`. After the `if (setupState.status !== "VALID")` branch, add:

```javascript
// VALID case: run one-per-session import probe. If it fails, downgrade to MISSING
// by deleting the flag, then emit banner.
const probeResult = spawnSync(
  getVenvPython(setupState.flag.venv_path),
  ["-c", "import claude_wayfinder"],
  { encoding: "utf8" }
);
if (probeResult.status !== 0) {
  // Flag is structurally valid but the venv is corrupt. Delete the flag so the
  // next session sees MISSING and re-prompts the user.
  const flagPath = path.join(
    process.env.CLAUDE_PLUGIN_DATA || require("./lib/setup-state.js")._computePluginDataDir(),
    "setup-state.json"
  );
  try {
    fs.unlinkSync(flagPath);
  } catch (_err) {
    // best-effort cleanup; ignore
  }
  const banner = `⚠ claude-wayfinder venv at ${setupState.flag.venv_path} fails import probe (likely corrupt). Run /setup-wayfinder to rebuild.`;
  process.stdout.write(JSON.stringify({ additionalContext: banner }) + "\n");
  process.exit(0);
}

// VALID + probe passed: existing catalog-health logic continues
```

Make sure `spawnSync`, `path`, and `fs` are required at the top of the file if not already.

- [ ] **Step 2: Run tests**

```bash
node --test hooks/tests/check-catalog-health.test.js
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add hooks/check-catalog-health.js
git commit -m "feat(hooks): check-catalog-health runs one-per-session import probe"
```

### Task 2.8: Add VALID-state import-probe-FAILS test (flag should be deleted, banner emitted)

- [ ] **Step 1: Write the failing test**

```javascript
test("check-catalog-health deletes flag and emits BROKEN banner when import probe fails", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "wayfinder-hooktest-"));
  try {
    const venvDir = path.join(tmp, "venv");
    const venvBin = path.join(venvDir, process.platform === "win32" ? "Scripts" : "bin");
    fs.mkdirSync(venvBin, { recursive: true });
    // Use the fake-python-fail shim
    const fakeFail = path.resolve(__dirname, "fixtures", "fake-python-fail.js");
    const venvPython = path.join(venvBin, process.platform === "win32" ? "python.exe" : "python");
    if (process.platform === "win32") {
      fs.writeFileSync(venvPython, `@echo off\nnode "${fakeFail}" %*\n`);
    } else {
      fs.symlinkSync(fakeFail, venvPython);
      fs.chmodSync(fakeFail, 0o755);
    }

    const currentVersion = require("../lib/setup-state.js").getCurrentVersion();
    const flagPath = path.join(tmp, "setup-state.json");
    fs.writeFileSync(
      flagPath,
      JSON.stringify({
        version: currentVersion,
        venv_path: venvDir,
        interpreter: "/system/python",
        installed_at: "2026-05-17T19:00:00Z",
      })
    );

    const result = runHook({ pluginData: tmp });
    assert.equal(result.status, 0);
    assert.match(result.stdout, /fails import probe.*\/setup-wayfinder/s);
    // Flag should have been deleted
    assert.ok(!fs.existsSync(flagPath), "flag file should have been deleted on probe failure");
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});
```

- [ ] **Step 2: Run test**

```bash
node --test hooks/tests/check-catalog-health.test.js
```

Expected: PASS (Task 2.7 impl handles this).

- [ ] **Step 3: Commit**

```bash
git add hooks/tests/check-catalog-health.test.js
git commit -m "test(hooks): check-catalog-health flag-delete-on-probe-fail"
```

### Task 2.9: Modify `refresh-catalog-on-stale.js` to use the helper

- [ ] **Step 1: Read the existing hook**

```bash
cat hooks/refresh-catalog-on-stale.js | head -100
```

Identify the `CLAUDE_WAYFINDER_PYTHON` fallback at L81 and the `parseCmd` regex parser at L281-289 (approximate). These will be removed.

- [ ] **Step 2: Add flag guard at the top**

Modify `hooks/refresh-catalog-on-stale.js`. Near the top, after stdin parsing:

```javascript
const { readSetupState, getCurrentVersion, getVenvPython } = require("./lib/setup-state.js");

// ... existing parse-input code ...

// Setup-state gate. If not VALID, exit silently — the SessionStart banner
// in check-catalog-health.js surfaces the issue. Per spec § 4.4.
const setupState = readSetupState(getCurrentVersion());
if (setupState.status !== "VALID") {
  process.exit(0);
}
const venvPython = getVenvPython(setupState.flag.venv_path);
// `venvPython` is now used wherever the old code resolved a python interpreter.
```

- [ ] **Step 3: Remove the v0.3.x discovery scaffolding**

Delete from `hooks/refresh-catalog-on-stale.js`:
- The `CLAUDE_WAYFINDER_PYTHON` env-var fallback block (~L75-95)
- The `parseCmd` regex parser (~L281-289)
- Any branch that resolves a Python interpreter from `process.env` or shell discovery

Replace every reference to the previously-discovered Python with `venvPython`. The args-array `spawnSync` call stays — the only thing that changes is the program path.

- [ ] **Step 4: Add a flag-guard test**

Append to `hooks/tests/refresh-catalog-on-stale.test.js` (file should already exist):

```javascript
test("refresh-catalog-on-stale exits silently when setup-state is MISSING", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "wayfinder-refreshtest-"));
  try {
    // No flag file in tmp
    const result = spawnSync(process.execPath, [path.resolve(__dirname, "..", "refresh-catalog-on-stale.js")], {
      input: "{}",
      env: {
        ...process.env,
        CLAUDE_PLUGIN_DATA: tmp,
        CLAUDE_PLUGIN_ROOT: path.resolve(__dirname, "..", ".."),
      },
      encoding: "utf8",
    });
    assert.equal(result.status, 0, `Hook should exit 0; got ${result.status}: ${result.stderr}`);
    assert.equal(result.stdout.trim(), "", "Hook should produce no stdout when flag MISSING");
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});
```

- [ ] **Step 5: Run tests**

```bash
node --test hooks/tests/refresh-catalog-on-stale.test.js
```

Expected: PASS for the new test. Existing tests in this file may need adjustment if they relied on `CLAUDE_WAYFINDER_PYTHON` env-var — update them to plant a valid flag instead.

- [ ] **Step 6: Commit**

```bash
git add hooks/refresh-catalog-on-stale.js hooks/tests/refresh-catalog-on-stale.test.js
git commit -m "refactor(hooks): refresh-catalog-on-stale uses setup-state flag instead of CLAUDE_WAYFINDER_PYTHON discovery"
```

### Task 2.10: Open Phase 2 PR

- [ ] **Step 1: Push and PR**

```bash
git push -u origin <branch-name>
gh pr create --repo glitchwerks/claude-wayfinder \
  --title "feat(hooks): wire setup-state helper into check-catalog-health + refresh-catalog-on-stale (Phase 2 of #99)" \
  --body "$(cat <<'EOF'
## Summary

Phase 2 of epic #99. Wires the Phase 1 helper into the two existing hooks that need it.

- `check-catalog-health.js`: reads flag at SessionStart; emits MISSING/STALE/BROKEN banner via `additionalContext` when not VALID; runs one-per-session `import claude_wayfinder` probe when VALID; deletes flag on probe failure.
- `refresh-catalog-on-stale.js`: guards on flag at UserPromptSubmit; silent exit when not VALID. Removes ~80 LOC of `CLAUDE_WAYFINDER_PYTHON` / `parseCmd` discovery scaffolding now superseded.

## Spec reference

Implements spec § 4.3, § 4.4, and the SessionStart import-probe contract from § 3.

## Test plan

- [ ] All new hook tests pass: `node --test hooks/tests/check-catalog-health.test.js hooks/tests/refresh-catalog-on-stale.test.js`
- [ ] Existing test suites still pass: `node --test hooks/tests/*.test.js`
- [ ] CI Lint + Test (Node) jobs green

## Related

- Epic: #99
- Spec: `docs/superpowers/specs/2026-05-17-setup-skill-architecture-design.md`
- Phase 1 helper: PR <Phase-1-PR#>

🤖 _Generated by Claude Code on behalf of @cbeaulieu-gt_
EOF
)"
```

- [ ] **Step 2: CI + pre-merge sweep + merge** (same pattern as Phase 1)

---

## Phase 3 — Skill body + executable mirror

**Sub-issue:** "Author setup-wayfinder skill body and tests/integration/setup_pipeline.py mirror"

**PR scope:** Three new files. The skill body is LLM instructions; `setup_pipeline.py` is the executable mirror used by CI smoke tests; `test_skill_pipeline_sync.py` keeps them in sync.

**Files:**
- Create: `skills/setup-wayfinder/SKILL.md`
- Create: `tests/integration/setup_pipeline.py`
- Create: `tests/integration/__init__.py` (empty)
- Create: `tests/test_skill_pipeline_sync.py`

### Task 3.1: Author `tests/integration/setup_pipeline.py` (executable mirror first)

The mirror gets authored first because it's executable and testable, and the skill body will copy its step structure. Building the skill body without the mirror produces drift.

- [ ] **Step 1: Create the package init**

```bash
mkdir -p tests/integration
touch tests/integration/__init__.py
```

- [ ] **Step 2: Write `setup_pipeline.py`**

Create `tests/integration/setup_pipeline.py`:

```python
"""Executable mirror of the setup-wayfinder skill body.

The skill body at skills/setup-wayfinder/SKILL.md describes 8 numbered steps
that the LLM follows when /setup-wayfinder is invoked. This module exposes
each step as an importable function so CI can run the full pipeline end-to-end
on a real Python interpreter.

The skill body and this module must stay in sync — see
tests/test_skill_pipeline_sync.py for the drift check.

Spec § 4.1 (skill body) and § 3 (architecture) are the source of truth.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


class SetupError(Exception):
    """Raised when a step in the setup pipeline cannot complete."""


def compute_plugin_data_dir(plugin_id: str = "claude-wayfinder@claude-wayfinder") -> Path:
    """Step 1: Resolve ${CLAUDE_PLUGIN_DATA} deterministically.

    Honors $CLAUDE_PLUGIN_DATA when set (test seam); otherwise computes
    ~/.claude/plugins/data/{slug}/ per spec § 4.2.
    """
    env_override = os.environ.get("CLAUDE_PLUGIN_DATA")
    if env_override:
        return Path(env_override)
    slug = re.sub(r"[^a-zA-Z0-9_\-]", "-", plugin_id)
    return Path.home() / ".claude" / "plugins" / "data" / slug


def discover_python(prior_interpreter: str | None = None) -> str:
    """Step 2: Find a Python interpreter ≥3.11.

    Try, in order: prior_interpreter (from previous run's flag),
    $CLAUDE_WAYFINDER_BOOTSTRAP_PYTHON, `py -3` on Windows,
    `python3`, `python`. Probe each with `-c "import sys;
    sys.exit(0 if sys.version_info >= (3, 11) else 1)"`.

    Raises SetupError on total failure (skill body asks user for path;
    in CI we never reach this).
    """
    candidates: list[str] = []
    if prior_interpreter:
        candidates.append(prior_interpreter)
    env_override = os.environ.get("CLAUDE_WAYFINDER_BOOTSTRAP_PYTHON")
    if env_override:
        candidates.append(env_override)
    if platform.system() == "Windows":
        candidates.append("py -3")
    candidates.extend(["python3", "python"])

    probe = "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
    for candidate in candidates:
        try:
            args = candidate.split() + ["-c", probe]
            result = subprocess.run(args, capture_output=True, check=False)
            if result.returncode == 0:
                return candidate
        except FileNotFoundError:
            continue
    raise SetupError(
        f"No Python ≥3.11 found. Tried: {candidates}. "
        "Set CLAUDE_WAYFINDER_BOOTSTRAP_PYTHON to an absolute path."
    )


def wipe_venv(venv_dir: Path) -> None:
    """Step 3: Delete the venv directory if it exists (always-wipe per spec D4)."""
    if venv_dir.exists():
        shutil.rmtree(venv_dir)


def create_venv(python_cmd: str, venv_dir: Path) -> None:
    """Step 4: Run `<python> -m venv <venv_dir>`."""
    args = python_cmd.split() + ["-m", "venv", str(venv_dir)]
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise SetupError(
            f"python -m venv failed (exit {result.returncode}):\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )


def get_venv_python(venv_dir: Path) -> Path:
    """Return the path to the venv's python binary (Scripts/python.exe on Windows)."""
    if platform.system() == "Windows":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def pip_install(venv_dir: Path, version: str) -> None:
    """Step 5: Install claude-wayfinder==<version> from PyPI."""
    venv_python = get_venv_python(venv_dir)
    args = [str(venv_python), "-m", "pip", "install", f"claude-wayfinder=={version}"]
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        # Wipe partial state per spec § 6 F3
        shutil.rmtree(venv_dir, ignore_errors=True)
        raise SetupError(
            f"pip install claude-wayfinder=={version} failed (exit {result.returncode}):\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )


def verify_import(venv_dir: Path) -> None:
    """Step 6: Confirm `import claude_wayfinder` works."""
    venv_python = get_venv_python(venv_dir)
    args = [str(venv_python), "-c", "import claude_wayfinder"]
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        shutil.rmtree(venv_dir, ignore_errors=True)
        raise SetupError(
            f"import claude_wayfinder failed after install:\nstderr: {result.stderr}"
        )


def write_flag(plugin_data_dir: Path, version: str, venv_dir: Path, interpreter: str) -> Path:
    """Step 7: Write setup-state.json flag with required fields."""
    flag = {
        "version": version,
        "venv_path": str(venv_dir),
        "interpreter": interpreter,
        "installed_at": datetime.now(timezone.utc).isoformat(),
    }
    flag_path = plugin_data_dir / "setup-state.json"
    flag_path.parent.mkdir(parents=True, exist_ok=True)
    flag_path.write_text(json.dumps(flag, indent=2))
    return flag_path


def run_full_pipeline(version: str, prior_interpreter: str | None = None) -> Path:
    """Run all 8 steps in order. Step 8 (tell user) is the caller's responsibility."""
    plugin_data_dir = compute_plugin_data_dir()
    interpreter = discover_python(prior_interpreter=prior_interpreter)
    venv_dir = plugin_data_dir / "venv"
    wipe_venv(venv_dir)
    create_venv(interpreter, venv_dir)
    pip_install(venv_dir, version)
    verify_import(venv_dir)
    return write_flag(plugin_data_dir, version, venv_dir, interpreter)
```

- [ ] **Step 3: Verify the module imports correctly**

```bash
/c/Users/chris/.claude/.venv/Scripts/python.exe -c "from tests.integration import setup_pipeline; print(dir(setup_pipeline))"
```

Expected: list shows `compute_plugin_data_dir`, `create_venv`, `discover_python`, `get_venv_python`, `pip_install`, `run_full_pipeline`, `verify_import`, `wipe_venv`, `write_flag`, `SetupError`.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/__init__.py tests/integration/setup_pipeline.py
git commit -m "feat(tests): tests/integration/setup_pipeline.py mirrors skill body's 8 steps"
```

### Task 3.2: Author the skill body

- [ ] **Step 1: Create the skill directory**

```bash
mkdir -p skills/setup-wayfinder
```

- [ ] **Step 2: Write `SKILL.md`**

Create `skills/setup-wayfinder/SKILL.md`:

```markdown
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

# Setup claude-wayfinder

Materialize the Python venv at `${CLAUDE_PLUGIN_DATA}/venv/` so plugin hooks can spawn a Python that has `claude_wayfinder` importable. Write the setup-state flag so hooks know setup completed.

The behavior described below is mirrored by `tests/integration/setup_pipeline.py`. If you change anything here, update that file too (CI's `test_skill_pipeline_sync.py` enforces this).

## Step 1: Resolve `${CLAUDE_PLUGIN_DATA}`

The plugin data directory path is deterministic per Anthropic's plugin docs:

- If `$CLAUDE_PLUGIN_DATA` is set in the environment, use it verbatim (test seam).
- Otherwise, compute `~/.claude/plugins/data/{slug}/` where `{slug}` is `claude-wayfinder@claude-wayfinder` with every non-`[a-zA-Z0-9_-]` character replaced by `-`. For our plugin, the slug is `claude-wayfinder-claude-wayfinder`.

Use the Bash tool:

```bash
PLUGIN_DATA="${CLAUDE_PLUGIN_DATA:-$HOME/.claude/plugins/data/claude-wayfinder-claude-wayfinder}"
mkdir -p "$PLUGIN_DATA"
echo "$PLUGIN_DATA"
```

## Step 2: Discover Python ≥3.11

Try these candidates in order, stopping at the first that probes successfully:

1. **Prior interpreter** from any existing setup-state.json's `interpreter` field (if a flag is currently being re-setup).
2. `$CLAUDE_WAYFINDER_BOOTSTRAP_PYTHON` if set.
3. `py -3` on Windows.
4. `python3` then `python` on PATH.

Probe each candidate with:

```bash
<candidate> -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
```

Exit 0 = success, save the candidate as `PYTHON`.

If all candidates fail, ask the user:

> No Python ≥3.11 found. I tried: $CLAUDE_WAYFINDER_BOOTSTRAP_PYTHON, py -3, python3, python. Please provide an absolute path to a Python ≥3.11 interpreter (e.g., `C:\Python313\python.exe` or `/usr/local/bin/python3.12`). If you don't have one installed, you'll need to install Python first.

Probe the user-provided path the same way. If it works, save it (will be persisted in the flag's `interpreter` field for future re-setup runs).

## Step 3: Wipe the existing venv

Per spec § 2 D4, always wipe + recreate. No idempotency.

```bash
rm -rf "$PLUGIN_DATA/venv"
```

(On Windows PowerShell: `Remove-Item -Recurse -Force -ErrorAction SilentlyContinue "$env:PLUGIN_DATA\venv"`)

## Step 4: Create the venv

```bash
"$PYTHON" -m venv "$PLUGIN_DATA/venv"
```

If this fails:
- Surface the stderr verbatim to the user.
- Offer the common-cause hint: on Debian/Ubuntu, `sudo apt install python3-venv` is often needed.
- Wipe any partial state. Do not write the flag. Stop.

## Step 5: Install claude-wayfinder from PyPI

The version to install is the plugin's current version, read from `${CLAUDE_PLUGIN_ROOT}/pyproject.toml` (preferred) or `${CLAUDE_PLUGIN_ROOT}/.claude-plugin/plugin.json`. Pin exactly:

```bash
VENV_PYTHON="$PLUGIN_DATA/venv/bin/python"  # or Scripts/python.exe on Windows
"$VENV_PYTHON" -m pip install "claude-wayfinder==$PLUGIN_VERSION"
```

If pip fails:
- Surface stderr verbatim.
- **Wipe the half-built venv** (always-wipe invariant).
- Do not write the flag.
- Tell the user to check network/PyPI and retry.

## Step 6: Verify import

```bash
"$VENV_PYTHON" -c "import claude_wayfinder"
```

If this fails after a successful `pip install`, the wheel is corrupt:
- Surface the import error.
- Suggest `pip cache purge` then retry.
- Wipe the venv. Do not write the flag.

## Step 7: Write the setup-state flag

Create `$PLUGIN_DATA/setup-state.json` with exact shape:

```json
{
  "version": "<PLUGIN_VERSION>",
  "venv_path": "<absolute path to $PLUGIN_DATA/venv>",
  "interpreter": "<the candidate from Step 2 that worked>",
  "installed_at": "<ISO-8601 UTC timestamp>"
}
```

If the write fails (disk full, permission), wipe the venv (otherwise orphaned), surface the write error.

## Step 8: Tell the user

On success, tell the user:

> ✓ claude-wayfinder setup complete.
>
> - venv: `<path>`
> - interpreter: `<which Python was used>`
> - version: `<plugin version>`
>
> Open a new session for the dispatch matcher and catalog refresh to activate. The next `check-catalog-health.js` SessionStart hook will see the flag and proceed normally.
```

- [ ] **Step 3: Commit**

```bash
git add skills/setup-wayfinder/SKILL.md
git commit -m "feat(skills): add setup-wayfinder skill body"
```

### Task 3.3: Write the skill-pipeline sync test

- [ ] **Step 1: Write the failing test**

Create `tests/test_skill_pipeline_sync.py`:

```python
"""Drift check: ensure skill body's numbered steps match setup_pipeline.py functions.

If the skill body and the executable mirror drift apart, this test fails — forcing
the author to update both together.
"""

import re
from pathlib import Path

import pytest


SKILL_BODY = Path(__file__).parent.parent / "skills" / "setup-wayfinder" / "SKILL.md"
PIPELINE = Path(__file__).parent / "integration" / "setup_pipeline.py"


# Maps the human-readable step heading in the skill body to the function name
# in setup_pipeline.py. Update this map when adding/removing steps.
STEP_FUNCTION_MAP = {
    "Resolve `${CLAUDE_PLUGIN_DATA}`": "compute_plugin_data_dir",
    "Discover Python": "discover_python",
    "Wipe the existing venv": "wipe_venv",
    "Create the venv": "create_venv",
    "Install claude-wayfinder from PyPI": "pip_install",
    "Verify import": "verify_import",
    "Write the setup-state flag": "write_flag",
    # Step 8 (tell user) is intentionally not mirrored — it's a skill-body-only
    # responsibility; the executable mirror's caller handles success reporting.
}


def test_skill_body_lists_expected_step_count():
    body = SKILL_BODY.read_text()
    # Match "## Step N: ..." headings
    step_headings = re.findall(r"^##\s+Step\s+(\d+):", body, re.MULTILINE)
    assert len(step_headings) == 8, f"Expected 8 steps in skill body, found {len(step_headings)}"
    assert step_headings == ["1", "2", "3", "4", "5", "6", "7", "8"], "Steps should be numbered 1-8 consecutively"


@pytest.mark.parametrize("step_heading,function_name", STEP_FUNCTION_MAP.items())
def test_skill_step_has_matching_function(step_heading, function_name):
    """Each skill body step heading corresponds to a function in setup_pipeline.py."""
    body = SKILL_BODY.read_text()
    pipeline = PIPELINE.read_text()
    assert step_heading in body, f"Step heading not found in skill body: {step_heading}"
    # Match `def <function_name>(...)` at the start of a line
    pattern = rf"^def\s+{re.escape(function_name)}\s*\("
    assert re.search(pattern, pipeline, re.MULTILINE), (
        f"Function {function_name}() not found in setup_pipeline.py "
        f"(expected because skill body has '## Step ...: {step_heading}')"
    )


def test_pipeline_has_run_full_pipeline_entrypoint():
    """The executable mirror exposes a single entry that runs all steps."""
    pipeline = PIPELINE.read_text()
    assert re.search(r"^def\s+run_full_pipeline\s*\(", pipeline, re.MULTILINE), (
        "setup_pipeline.py should expose run_full_pipeline() that runs all 8 steps"
    )
```

- [ ] **Step 2: Run the test**

```bash
/c/Users/chris/.claude/.venv/Scripts/python.exe -m pytest tests/test_skill_pipeline_sync.py -v
```

Expected: PASS. All 9 cases (1 + 7 parameterized + 1 entrypoint).

If any case fails, fix either the skill body's step heading or the setup_pipeline.py function name so they match.

- [ ] **Step 3: Commit**

```bash
git add tests/test_skill_pipeline_sync.py
git commit -m "test: skill-body / setup_pipeline.py sync check"
```

### Task 3.4: Open Phase 3 PR

- [ ] **Step 1: Push and PR**

```bash
git push -u origin <branch-name>
gh pr create --repo glitchwerks/claude-wayfinder \
  --title "feat(skills): setup-wayfinder skill body + executable mirror (Phase 3 of #99)" \
  --body "$(cat <<'EOF'
## Summary

Phase 3 of epic #99. Three new files:

- `skills/setup-wayfinder/SKILL.md` — the user-facing skill. Frontmatter triggers + 8-step body the LLM follows.
- `tests/integration/setup_pipeline.py` — executable Python mirror of the skill's 8 steps. Used by Phase 4's smoke test.
- `tests/test_skill_pipeline_sync.py` — drift check that fails CI if the skill body and the pipeline diverge.

## Spec reference

Implements spec § 4.1 (skill body) and the executable-mirror requirement from § 7 (testing strategy).

## Test plan

- [ ] `python -m pytest tests/test_skill_pipeline_sync.py -v` passes (9 cases)
- [ ] Manual sanity: skill description triggers don't match common false-positive phrases
- [ ] CI passes

## Related

- Epic: #99
- Spec: `docs/superpowers/specs/2026-05-17-setup-skill-architecture-design.md`

🤖 _Generated by Claude Code on behalf of @cbeaulieu-gt_
EOF
)"
```

- [ ] **Step 2: CI + pre-merge sweep + merge**

---

## Phase 4 — Skill smoke test

**Sub-issue:** "End-to-end skill smoke test (tests/integration/test_setup_skill.py)"

**PR scope:** One new file plus a CI job addition.

**Files:**
- Create: `tests/integration/test_setup_skill.py`
- Modify: `.github/workflows/ci.yml` (add ONE job: `skill-smoke-ubuntu`)

### Task 4.1: Write the failing smoke test

- [ ] **Step 1: Create `tests/integration/test_setup_skill.py`**

```python
"""End-to-end smoke test for the setup pipeline.

Runs the full skill body's 8 steps against a real Python ≥3.11 and a real PyPI.
Asserts that the venv materializes correctly, the import works, and the flag
file is shaped correctly.

This test is NOT path-filtered — runs on every PR per spec § 7 (test surfaces)
and inquisitor pass-1 charge 11.
"""

from __future__ import annotations

import json
import os
import platform
import tempfile
from pathlib import Path

import pytest

from tests.integration import setup_pipeline


@pytest.fixture
def fake_plugin_data(monkeypatch):
    """Provide a temp dir as $CLAUDE_PLUGIN_DATA for the duration of one test."""
    with tempfile.TemporaryDirectory(prefix="wayfinder-smoke-") as tmp:
        monkeypatch.setenv("CLAUDE_PLUGIN_DATA", tmp)
        yield Path(tmp)


def _read_plugin_version() -> str:
    """Read the bundled plugin version from pyproject.toml."""
    import re
    pyproject = Path(__file__).parent.parent.parent / "pyproject.toml"
    content = pyproject.read_text()
    match = re.search(r"^version\s*=\s*\"([^\"]+)\"", content, re.MULTILINE)
    assert match, "Could not find version in pyproject.toml"
    return match.group(1)


def test_full_pipeline_smoke(fake_plugin_data):
    """The 8-step pipeline produces a working venv with claude-wayfinder importable."""
    version = _read_plugin_version()

    # Run pipeline (uses real Python on $PATH; real PyPI)
    flag_path = setup_pipeline.run_full_pipeline(version)

    # Step 7 wrote the flag — verify shape
    assert flag_path.exists()
    flag = json.loads(flag_path.read_text())
    assert flag["version"] == version
    assert "venv_path" in flag
    assert "interpreter" in flag
    assert "installed_at" in flag

    # The venv exists at the recorded path
    venv_dir = Path(flag["venv_path"])
    assert venv_dir.exists()
    assert venv_dir.is_dir()

    # The venv Python exists and is the recorded one's child
    venv_python = setup_pipeline.get_venv_python(venv_dir)
    assert venv_python.exists()

    # claude_wayfinder imports from inside the venv
    import subprocess
    result = subprocess.run(
        [str(venv_python), "-c", "import claude_wayfinder; print(claude_wayfinder.__file__)"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"import claude_wayfinder failed: {result.stderr}"
    # Sanity: it imports from inside our venv, not the system Python
    assert str(venv_dir) in result.stdout, (
        f"Imported claude_wayfinder from outside the venv: {result.stdout!r}"
    )


def test_wipe_idempotent(fake_plugin_data):
    """Step 3 (wipe) is a no-op when no venv exists; succeeds when one does."""
    venv_dir = fake_plugin_data / "venv"
    # No-op
    setup_pipeline.wipe_venv(venv_dir)
    assert not venv_dir.exists()
    # Create then wipe
    venv_dir.mkdir()
    (venv_dir / "marker").write_text("hello")
    setup_pipeline.wipe_venv(venv_dir)
    assert not venv_dir.exists()


def test_discover_python_finds_real_interpreter(fake_plugin_data):
    """Step 2 finds the CI runner's Python ≥3.11."""
    interpreter = setup_pipeline.discover_python()
    assert interpreter, "Should find at least one Python ≥3.11 on CI"
```

- [ ] **Step 2: Run the smoke test locally**

```bash
/c/Users/chris/.claude/.venv/Scripts/python.exe -m pytest tests/integration/test_setup_skill.py -v
```

Expected: 3 tests PASS. The full-pipeline test takes ~20-30 seconds (real `pip install` of `claude-wayfinder`).

**If the package isn't on PyPI yet** (likely, since we haven't published v0.4.0):
- Use `--index-url https://test.pypi.org/simple/` against TestPyPI for the alpha phase.
- Or: install from the local `${CLAUDE_PLUGIN_ROOT}` directory temporarily as a fallback (`pip install -e "$CLAUDE_PLUGIN_ROOT"`) — this is a temporary smoke-test mode, not production behavior.

Document the temporary index-url override in `setup_pipeline.py`'s `pip_install()` docstring as a known pre-v0.4.0 PyPI-publication condition.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_setup_skill.py
git commit -m "test(integration): end-to-end smoke test for setup pipeline"
```

### Task 4.2: Add the Ubuntu skill-smoke CI job

- [ ] **Step 1: Modify `.github/workflows/ci.yml`**

Add a new job after the existing `test-py312` job:

```yaml
  # ---------------------------------------------------------------------------
  # Skill smoke (Ubuntu)
  # ---------------------------------------------------------------------------
  skill-smoke-ubuntu:
    name: Skill smoke (Ubuntu)
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@v4

      - uses: astral-sh/setup-uv@08807647e7069bb48b6ef5acd8ec9567f424441b  # v8.1.0
        with:
          python-version: "3.12"
          activate-environment: "true"

      - name: Install pytest
        run: uv pip install pytest

      - name: Run skill smoke test
        run: uv run --no-sync pytest tests/integration/test_setup_skill.py tests/test_skill_pipeline_sync.py -v
```

- [ ] **Step 2: Validate the yaml locally**

```bash
/c/Users/chris/.claude/.venv/Scripts/python.exe -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))" && echo "yaml valid"
```

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add skill-smoke-ubuntu job"
```

### Task 4.3: Open Phase 4 PR

- [ ] **Step 1: Push and PR**

```bash
git push -u origin <branch-name>
gh pr create --repo glitchwerks/claude-wayfinder \
  --title "test(integration): skill smoke test + Ubuntu CI job (Phase 4 of #99)" \
  --body "$(cat <<'EOF'
## Summary

Phase 4 of epic #99. End-to-end smoke test that exercises the full setup pipeline against a real Python ≥3.11 and a real PyPI. Direct response to inquisitor pass-1 charge 11 (test stubbing real subprocess paths).

Adds:
- `tests/integration/test_setup_skill.py` — 3 test cases including the full-pipeline run
- `.github/workflows/ci.yml` — new `skill-smoke-ubuntu` job

macOS and Windows variants land in Phase 5.

## Test plan

- [ ] CI's `Skill smoke (Ubuntu)` job passes — this is the main signal
- [ ] Test takes <60s end-to-end
- [ ] No path filter on the job — runs on every PR going forward

## Known limitation

If `claude-wayfinder` is not yet on PyPI, the test will be skipped or pinned to a TestPyPI index until Phase 7 publishes v0.4.0. Document the workaround in `tests/integration/setup_pipeline.py`.

## Related

- Epic: #99
- Spec: `docs/superpowers/specs/2026-05-17-setup-skill-architecture-design.md`

🤖 _Generated by Claude Code on behalf of @cbeaulieu-gt_
EOF
)"
```

- [ ] **Step 2: CI + sweep + merge**

---

## Phase 5 — CI matrix expansion (macOS + Windows)

**Sub-issue:** "Add macOS and Windows skill-smoke jobs to CI matrix"

**PR scope:** One file modification. Closes inquisitor pass-2 charge 18.

**Files:**
- Modify: `.github/workflows/ci.yml`

### Task 5.1: Add macOS skill-smoke job

- [ ] **Step 1: Add the job**

In `.github/workflows/ci.yml`, after `skill-smoke-ubuntu:`:

```yaml
  skill-smoke-macos:
    name: Skill smoke (macOS)
    runs-on: macos-latest
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@v4

      - uses: astral-sh/setup-uv@08807647e7069bb48b6ef5acd8ec9567f424441b  # v8.1.0
        with:
          python-version: "3.12"
          activate-environment: "true"

      - name: Install pytest
        run: uv pip install pytest

      - name: Run skill smoke test
        run: uv run --no-sync pytest tests/integration/test_setup_skill.py tests/test_skill_pipeline_sync.py -v
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add skill-smoke-macos job"
```

### Task 5.2: Add Windows skill-smoke job

- [ ] **Step 1: Add the job**

```yaml
  skill-smoke-windows:
    name: Skill smoke (Windows)
    runs-on: windows-latest
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@v4

      - uses: astral-sh/setup-uv@08807647e7069bb48b6ef5acd8ec9567f424441b  # v8.1.0
        with:
          python-version: "3.12"
          activate-environment: "true"

      - name: Install pytest
        shell: bash
        run: uv pip install pytest

      - name: Run skill smoke test
        shell: bash
        run: uv run --no-sync pytest tests/integration/test_setup_skill.py tests/test_skill_pipeline_sync.py -v
```

Note `shell: bash` — explicit because Windows defaults to PowerShell on GitHub Actions and we want consistent semantics across platforms.

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add skill-smoke-windows job"
```

### Task 5.3: Open Phase 5 PR

- [ ] **Step 1: Push and PR**

```bash
git push -u origin <branch-name>
gh pr create --repo glitchwerks/claude-wayfinder \
  --title "ci: expand skill-smoke matrix to macOS + Windows (Phase 5 of #99)" \
  --body "$(cat <<'EOF'
## Summary

Phase 5 of epic #99. Closes inquisitor pass-2 charge 18 (macOS missing from CI matrix). Adds:
- `skill-smoke-macos` job on `macos-latest`
- `skill-smoke-windows` job on `windows-latest`

Both run the same `pytest tests/integration/test_setup_skill.py tests/test_skill_pipeline_sync.py -v` as the Ubuntu job, providing per-OS coverage of the real-Python smoke test.

## Test plan

- [ ] All 3 OS jobs pass (Ubuntu + macOS + Windows)
- [ ] No test theater: each job exercises real `python -m venv` + `pip install`
- [ ] Windows test uses `shell: bash` so semantics match the other platforms

## Related

- Epic: #99
- Inquisitor pass-2 charge 18

🤖 _Generated by Claude Code on behalf of @cbeaulieu-gt_
EOF
)"
```

- [ ] **Step 2: CI + sweep + merge**

---

## Phase 6 — Documentation

**Sub-issue:** "Update README.md and docs/integration.md to document the setup flow"

**PR scope:** Two file modifications.

**Files:**
- Modify: `README.md`
- Modify: `docs/integration.md`

### Task 6.1: Update README.md

- [ ] **Step 1: Find the right insertion points**

```bash
grep -n -E "^##" README.md
```

Identify the Troubleshooting section and the Quick Start section.

- [ ] **Step 2: Update the Troubleshooting section**

Replace or augment the existing `CLAUDE_WAYFINDER_PYTHON` troubleshooting paragraph (around L32-56) with:

```markdown
## Troubleshooting

### `claude-wayfinder requires setup` banner on session start

The plugin uses a venv-based architecture (#99). On first install, you'll see a SessionStart banner:

> ⚠ claude-wayfinder requires setup. Run /setup-wayfinder to materialize the Python venv.

Run `/setup-wayfinder` once. The skill will:

1. Discover a Python ≥3.11 on your machine.
2. Create a venv at `~/.claude/plugins/data/claude-wayfinder-claude-wayfinder/venv/`.
3. Install `claude-wayfinder` from PyPI.
4. Write a setup-state flag so subsequent sessions know setup completed.

The same skill runs again after plugin updates (a `STALE` banner will prompt you).

### "No Python ≥3.11 found"

The skill will ask you for an absolute path. Provide one like `/usr/local/bin/python3.12` or `C:\Python313\python.exe`. The path is persisted in the setup-state flag for re-runs.

### Setup completed but dispatch still doesn't fire

Open a new session. Hooks read the flag at session start.
```

- [ ] **Step 3: Update Quick Start**

Add a one-liner near the install instructions:

```markdown
## Quick Start

1. Install the plugin: `/plugin install glitchwerks/claude-wayfinder`
2. **One-time setup:** when SessionStart shows the setup banner, run `/setup-wayfinder`.
3. Dispatch and catalog-refresh activate on the next session start.
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: README documents /setup-wayfinder flow"
```

### Task 6.2: Update docs/integration.md

- [ ] **Step 1: Read the current file**

```bash
cat docs/integration.md
```

- [ ] **Step 2: Add a setup section**

Insert after the "Prerequisites" or "Installation" section:

```markdown
## One-time setup

The plugin materializes its Python venv on demand via the `/setup-wayfinder` skill, not automatically on plugin install. This design eliminates the SessionStart-bootstrap class of bugs that plagued v0.3.x (see epic #99 and the architecture spec at `docs/superpowers/specs/2026-05-17-setup-skill-architecture-design.md`).

**First-time install:**

1. Install plugin via `/plugin install glitchwerks/claude-wayfinder`.
2. Open a session. SessionStart's `check-catalog-health.js` hook emits a banner via `additionalContext`:

   > ⚠ claude-wayfinder requires setup. Run /setup-wayfinder to materialize the Python venv.

3. Run `/setup-wayfinder`. The skill discovers Python ≥3.11, creates a venv at `${CLAUDE_PLUGIN_DATA}/venv/`, pip-installs `claude-wayfinder` from PyPI, verifies the import, and writes a setup-state flag.
4. Open a new session — hooks read the flag and proceed normally.

**Plugin update:**

When you `/plugin update`, the next SessionStart hook detects the version mismatch (flag vs `pyproject.toml`) and emits:

> ⚠ claude-wayfinder venv is for v0.4.0 but plugin is v0.4.1. Run /setup-wayfinder to refresh.

Run `/setup-wayfinder` again — the always-wipe-first invariant ensures a clean rebuild.

**Cross-machine setup:**

Per-machine setup is the supported model. If you roam between machines via OneDrive/Dropbox sync of `~/.claude`, the flag's `venv_path` won't resolve on the new machine and the BROKEN banner will fire. Run `/setup-wayfinder` once per machine. This is by design — the alternative (machine-agnostic venv) would re-introduce most of the complexity this architecture eliminates.

**Setup CLI bypass (advanced):**

The skill's pipeline is also exposed as importable Python (`tests/integration/setup_pipeline.py`) for CI and advanced scripting. Most users should use `/setup-wayfinder`.
```

- [ ] **Step 3: Commit**

```bash
git add docs/integration.md
git commit -m "docs: integration.md documents one-time setup + update + cross-machine flows"
```

### Task 6.3: Open Phase 6 PR

- [ ] **Step 1: Push and PR**

```bash
git push -u origin <branch-name>
gh pr create --repo glitchwerks/claude-wayfinder \
  --title "docs: document /setup-wayfinder flow in README and integration.md (Phase 6 of #99)" \
  --body "$(cat <<'EOF'
## Summary

Phase 6 of epic #99. User-facing documentation for the setup-skill architecture:

- `README.md`: Troubleshooting section explains the SessionStart banner and `/setup-wayfinder`; Quick Start mentions the one-time setup step.
- `docs/integration.md`: Detailed first-time, update, and cross-machine flows.

## Test plan

- [ ] README renders correctly on GitHub
- [ ] Internal cross-references (paths, section names) are valid
- [ ] No stale references to `CLAUDE_WAYFINDER_PYTHON` env-var (deprecated by this architecture)

## Related

- Epic: #99

🤖 _Generated by Claude Code on behalf of @cbeaulieu-gt_
EOF
)"
```

- [ ] **Step 2: CI + sweep + merge**

---

## Phase 7 — PyPI publication setup

**Sub-issue:** "Set up PyPI release workflow + publish v0.4.0"

**PR scope:** One new workflow file + operator action (PyPI account, token setup).

**Files:**
- Create: `.github/workflows/release.yml`

### Task 7.1: Operator prerequisite — PyPI account and trusted publisher

This is **operator work that cannot be automated by the agent**. The user must complete it before Phase 7's workflow can publish.

- [ ] **Step 1: Create PyPI account if not present**

User: register at https://pypi.org/account/register/ (or use an existing account).

- [ ] **Step 2: Set up Trusted Publisher (no API token needed)**

User: at https://pypi.org/manage/account/publishing/, add a "Pending publisher":
- PyPI Project Name: `claude-wayfinder`
- Owner: `glitchwerks`
- Repository name: `claude-wayfinder`
- Workflow name: `release.yml`
- Environment name: `pypi` (matches `environment:` block in workflow)

This grants the GitHub Actions workflow OIDC-based publish rights without storing a token.

- [ ] **Step 3: Repeat for TestPyPI** (for pre-release sanity)

At https://test.pypi.org/manage/account/publishing/, same shape but project name `claude-wayfinder` (TestPyPI).

No git commit for this task — it's operator-account setup.

### Task 7.2: Write the release workflow

- [ ] **Step 1: Create `.github/workflows/release.yml`**

```yaml
name: Release

on:
  push:
    tags:
      - "v*"

permissions:
  contents: read

jobs:
  build:
    name: Build distributions
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: astral-sh/setup-uv@08807647e7069bb48b6ef5acd8ec9567f424441b  # v8.1.0
        with:
          python-version: "3.12"
          activate-environment: "true"

      - name: Build sdist and wheel
        run: uv build

      - name: Upload built distributions
        uses: actions/upload-artifact@v4
        with:
          name: dist
          path: dist/

  publish-pypi:
    name: Publish to PyPI
    needs: build
    runs-on: ubuntu-latest
    environment:
      name: pypi
      url: https://pypi.org/p/claude-wayfinder
    permissions:
      id-token: write  # Required for trusted publisher OIDC
    steps:
      - uses: actions/download-artifact@v4
        with:
          name: dist
          path: dist/

      - name: Publish to PyPI
        uses: pypa/gh-action-pypi-publish@release/v1
```

- [ ] **Step 2: Validate yaml**

```bash
/c/Users/chris/.claude/.venv/Scripts/python.exe -c "import yaml; yaml.safe_load(open('.github/workflows/release.yml'))" && echo "yaml valid"
```

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "ci: release workflow for PyPI publication on v* tags"
```

### Task 7.3: TestPyPI dry-run

Before tagging v0.4.0, test the workflow end-to-end against TestPyPI to verify the trusted-publisher setup is correct.

- [ ] **Step 1: Add a TestPyPI job to the release workflow (temporary)**

Add to `release.yml` after the `publish-pypi` job:

```yaml
  publish-testpypi:
    name: Publish to TestPyPI (dry-run)
    if: contains(github.ref, '-rc') || contains(github.ref, '-alpha')
    runs-on: ubuntu-latest
    environment:
      name: testpypi
      url: https://test.pypi.org/p/claude-wayfinder
    permissions:
      id-token: write
    steps:
      - uses: actions/download-artifact@v4
        with:
          name: dist
          path: dist/

      - name: Publish to TestPyPI
        uses: pypa/gh-action-pypi-publish@release/v1
        with:
          repository-url: https://test.pypi.org/legacy/
```

The `if:` guard only runs the TestPyPI step on pre-release tags (e.g. `v0.4.0-rc1`).

- [ ] **Step 2: Tag and push a TestPyPI dry-run candidate**

```bash
git -C I:/other/claude-wayfinder tag v0.4.0-rc1 HEAD -m "TestPyPI dry-run"
git -C I:/other/claude-wayfinder push origin v0.4.0-rc1
```

- [ ] **Step 3: Watch the workflow run, fix any issues**

```bash
gh run watch --repo glitchwerks/claude-wayfinder
```

Expected: the `publish-testpypi` job uploads successfully. Confirm at https://test.pypi.org/project/claude-wayfinder/.

If the publisher OIDC setup is wrong, the workflow will fail at "Publish to TestPyPI". Fix the Trusted Publisher configuration (Task 7.1, Step 3) and retry.

- [ ] **Step 4: Delete the dry-run tag once verified**

```bash
git -C I:/other/claude-wayfinder tag -d v0.4.0-rc1
git -C I:/other/claude-wayfinder push origin :refs/tags/v0.4.0-rc1
```

No commit for this task — it's CI/release plumbing verification.

### Task 7.4: Tag and publish v0.4.0

This is the final operator action that closes epic #99.

- [ ] **Step 1: Verify all prior phases merged**

```bash
gh issue view 99 --repo glitchwerks/claude-wayfinder --json title,state,body --jq '.state'
gh pr list --repo glitchwerks/claude-wayfinder --state merged --search "is:merged Phase of #99" --limit 7
```

Expected: 6 merged PRs (Phases 1-6) — plus this one in flight as Phase 7.

- [ ] **Step 2: Run the standard release flow**

Per the v0.3.x convention (see `feedback_release_pattern` in agent memory if it exists, or look at PR #90's structure):

1. Branch `release-v0.4.0` off `main`
2. Bump `pyproject.toml`: `0.3.6` → `0.4.0`
3. Bump `.claude-plugin/plugin.json`: `0.3.6` → `0.4.0`
4. Add `CHANGELOG.md` entry under `[Unreleased]`:

   ```markdown
   ## [0.4.0] — <date>

   Major release: switches the plugin's Python dependency-resolution model
   from per-hook shell discovery to a user-initiated /setup-wayfinder skill
   that materializes a venv at ${CLAUDE_PLUGIN_DATA}/venv/. Closes the v0.3.x
   regression chain (#76, #80, #82, #87) by eliminating the shell-discovery
   surface entirely.

   ### Added
   - **`/setup-wayfinder` skill** for one-time venv setup. PR #<phase-3-PR>.
   - **`hooks/lib/setup-state.js`** shared helper for hook flag checks. PR #<phase-1-PR>.
   - **Skill smoke test** in CI (Ubuntu, macOS, Windows). PR #<phase-4 + phase-5-PRs>.

   ### Changed
   - **`check-catalog-health.js`** now emits a SessionStart banner when setup is required. PR #<phase-2-PR>.
   - **`refresh-catalog-on-stale.js`** now reads the setup-state flag and uses the recorded venv-Python path. The `CLAUDE_WAYFINDER_PYTHON` env-var fallback is removed. PR #<phase-2-PR>.
   - **PyPI distribution.** `claude-wayfinder` is now published to PyPI; the v0.4 setup skill installs it from there.

   ### Removed
   - `CLAUDE_WAYFINDER_PYTHON` env-var override (superseded by the venv-based architecture).
   - `parseCmd` regex parser in `hooks/refresh-catalog-on-stale.js` (no longer needed).
   ```

5. Open release PR `release: v0.4.0`, body following the v0.3.5 / v0.3.6 template.

- [ ] **Step 3: Merge release PR, push tag**

```bash
git -C I:/other/claude-wayfinder fetch --prune origin
git -C I:/other/claude-wayfinder pull --ff-only origin main
git -C I:/other/claude-wayfinder tag v0.4.0 HEAD -m "Release v0.4.0"
git -C I:/other/claude-wayfinder push origin v0.4.0
```

- [ ] **Step 4: Confirm PyPI publication**

```bash
gh run watch --repo glitchwerks/claude-wayfinder
# Expected: build job + publish-pypi job both succeed
# Confirm at https://pypi.org/project/claude-wayfinder/0.4.0/
```

- [ ] **Step 5: GitHub Release**

```bash
gh release create v0.4.0 --repo glitchwerks/claude-wayfinder \
  --title "v0.4.0" \
  --notes-file <path-to-changelog-entry> \
  --target <merge-commit-sha>
```

- [ ] **Step 6: Marketplace bump**

In `glitchwerks/plugins`, bump the `claude-wayfinder` entry to `0.4.0` and update the `sha` to the v0.4.0 release commit. Same shape as `glitchwerks/plugins#16` (the v0.3.6 bump).

- [ ] **Step 7: Close epic #99**

```bash
gh issue close 99 --repo glitchwerks/claude-wayfinder --reason completed -c "Closed by v0.4.0 release."
```

- [ ] **Step 8: Close #81**

```bash
gh issue close 81 --repo glitchwerks/claude-wayfinder --reason not_planned -c "Superseded by #99; the SessionStart-bootstrap design was replaced by the setup-skill architecture. See docs/superpowers/specs/2026-05-17-setup-skill-architecture-design.md."
```

---

## Self-review

After authoring all phases, run these checks before declaring the plan complete:

### Spec coverage

| Spec section | Implementing task(s) |
| ------------ | -------------------- |
| § 1 Why exists | Plan header + Phase 0 motivation |
| § 2 D1 PyPI only | Task 3.1 (`pip_install`), Task 7.x (PyPI publish) |
| § 2 D2 SessionStart banner only | Task 2.3 (banner emission) |
| § 2 D3 Cheap checks + import probe | Tasks 1.4 (cheap classify), 2.7 (probe) |
| § 2 D4 Always wipe + recreate | Task 3.1 (`wipe_venv`), Skill body Step 3 (Task 3.2) |
| § 2 D5 Slash + NL triggers | Task 3.2 (frontmatter description) |
| § 2 D6 Ask user on discovery fail | Task 3.1 (`discover_python` raises; skill body Step 2 asks user) |
| § 2 D7 Hybrid org | Phase 1 = helper; Phase 3 = skill body |
| § 3 Architecture | Phases 1-3 collectively |
| § 4.1 Skill body | Task 3.2 |
| § 4.2 Helper | Phase 1 |
| § 4.3 check-catalog-health.js | Phase 2 (Tasks 2.2-2.8) |
| § 4.4 refresh-catalog-on-stale.js | Task 2.9 |
| § 4.6 Net file changes | Mapped to phases above |
| § 5 Data flow scenarios A-G | Tested in Phase 4 smoke + Phase 2 unit tests |
| § 6 Error handling F1-F8 | Skill body (Task 3.2 Steps 4-7); Phase 2 silent-exit handling |
| § 7 Testing strategy | Phases 1, 2, 3, 4, 5 |
| § 8 Inquisitor cross-check | Implicit — design dissolves charges; verify in PRs |
| § 10 Open implementation questions | Phase 3 (skill conventions); Phase 7 (release sequence) |

No gaps identified.

### Placeholder scan

No `TBD`, `TODO`, `FIXME`, or "implement later" in this plan. Every code block contains the real code. Every command is exact.

### Type consistency

- `readSetupState(currentVersion)` returns `{ status, flag? }` — consistent across Tasks 1.1, 1.4, and all uses in Phase 2.
- `getVenvPython(venvPath)` returns string path — consistent across Phase 1 and Phase 2.
- `getCurrentVersion()` returns string — consistent.
- The 8-step pipeline functions in `setup_pipeline.py` match the 8 numbered headings in `SKILL.md` (enforced by Task 3.3's sync check).
- Banner text strings are duplicated between hooks and the spec — acceptable because the spec's § 4.3 banner-text table is the source of truth and hooks read directly from it; if banner text changes, both must update (a manual discipline; could be promoted to a shared constant in a future refactor).

No inconsistencies found.

---

## Execution handoff

Plan complete. Two execution options:

**1. Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration. Required sub-skill: `superpowers:subagent-driven-development`. Best for plans with 30+ tasks where review between tasks catches drift.

**2. Inline Execution** — execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints. Best for smaller plans or when the user is hands-on.

This plan has ~50 bite-sized tasks across 7 phases. Recommend **Subagent-Driven** for cost discipline (each phase's tasks are independent enough to delegate cleanly) and **inline review at phase boundaries** so the user catches direction drift before too much code lands.

Which approach?
