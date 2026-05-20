// hooks/tests/setup-state.test.js
// Unit tests for the setup-state helper. Pure-function tests; no subprocess.

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const fs = require("node:fs");
const os = require("node:os");

const { readSetupState, getVenvPython, getCurrentVersion, _computePluginDataDir, normalizeVenvPath } =
  require("../lib/setup-state.js");

/**
 * Helper: run callback with $CLAUDE_PLUGIN_DATA pointing at a temp dir.
 * Restores env var (or unsets) afterward; removes temp dir.
 */
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

/** Plant a fake venv python binary so existsSync() in the helper succeeds. */
function plantVenvPython(venvDir) {
  const binDir = path.join(venvDir, process.platform === "win32" ? "Scripts" : "bin");
  fs.mkdirSync(binDir, { recursive: true });
  const pythonFile = path.join(binDir, process.platform === "win32" ? "python.exe" : "python");
  fs.writeFileSync(pythonFile, ""); // empty file is fine; only existence is checked
  return pythonFile;
}

// ─── readSetupState classification ──────────────────────────────────────────

test("readSetupState returns MISSING when flag file absent", () => {
  withTempPluginData(() => {
    const result = readSetupState("0.4.0");
    assert.equal(result.status, "MISSING");
  });
});

test("readSetupState returns MISSING when flag file is unparseable JSON", () => {
  withTempPluginData((dir) => {
    fs.writeFileSync(path.join(dir, "setup-state.json"), "{not-valid-json");
    const result = readSetupState("0.4.0");
    assert.equal(result.status, "MISSING");
  });
});

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

test("readSetupState returns MISSING when flag JSON lacks venv_path field", () => {
  withTempPluginData((dir) => {
    fs.writeFileSync(
      path.join(dir, "setup-state.json"),
      JSON.stringify({ version: "0.4.0", installed_at: "2026-05-17" })
    );
    const result = readSetupState("0.4.0");
    assert.equal(result.status, "MISSING");
  });
});

test("readSetupState returns VALID when version matches and venv path exists", () => {
  withTempPluginData((dir) => {
    const venvDir = path.join(dir, "venv");
    plantVenvPython(venvDir);
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

test("readSetupState returns STALE when flag version differs from currentVersion", () => {
  withTempPluginData((dir) => {
    const venvDir = path.join(dir, "venv");
    plantVenvPython(venvDir);
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

test("readSetupState returns BROKEN when venv dir exists but python binary missing", () => {
  withTempPluginData((dir) => {
    const venvDir = path.join(dir, "venv");
    fs.mkdirSync(venvDir, { recursive: true });
    // Intentionally do NOT create the python binary
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

// ─── getCurrentVersion ──────────────────────────────────────────────────────

test("getCurrentVersion reads version from pyproject.toml", () => {
  const result = getCurrentVersion();
  // The bundled pyproject.toml should have a semver-like version
  assert.match(result, /^\d+\.\d+\.\d+/, `Expected semver-like version, got: ${result}`);
});

test("getCurrentVersion trims whitespace from version string", () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), "wayfinder-root-trim-"));
  const restore = process.env.CLAUDE_PLUGIN_ROOT;
  process.env.CLAUDE_PLUGIN_ROOT = tempRoot;
  try {
    fs.mkdirSync(path.join(tempRoot, ".claude-plugin"));
    fs.writeFileSync(
      path.join(tempRoot, ".claude-plugin", "plugin.json"),
      JSON.stringify({ name: "test", version: "  0.4.0  " })
    );
    assert.equal(getCurrentVersion(), "0.4.0");
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true });
    if (restore === undefined) delete process.env.CLAUDE_PLUGIN_ROOT;
    else process.env.CLAUDE_PLUGIN_ROOT = restore;
  }
});

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
    // Re-require to pick up the new env var via getPluginRoot()
    // Note: getCurrentVersion reads env on every call; no re-require needed
    assert.equal(getCurrentVersion(), "9.9.9");
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true });
    if (restore === undefined) delete process.env.CLAUDE_PLUGIN_ROOT;
    else process.env.CLAUDE_PLUGIN_ROOT = restore;
  }
});

test("readSetupState logs diagnostic when flag has malformed version (empty string)", () => {
  withTempPluginData((dir) => {
    fs.writeFileSync(
      path.join(dir, "setup-state.json"),
      JSON.stringify({ version: "", venv_path: "/tmp/venv", interpreter: "x", installed_at: "y" })
    );
    // Capture stderr writes
    const origWrite = process.stderr.write.bind(process.stderr);
    let captured = "";
    process.stderr.write = (chunk) => { captured += chunk; return true; };
    try {
      const result = readSetupState("0.4.0");
      assert.equal(result.status, "MISSING");
      assert.match(captured, /malformed version field/);
    } finally {
      process.stderr.write = origWrite;
    }
  });
});

// ─── getVenvPython platform-aware ───────────────────────────────────────────

test("getVenvPython returns Scripts/python.exe on Windows", { skip: process.platform !== "win32" }, () => {
  const result = getVenvPython("C:\\venv");
  assert.match(result, /Scripts[\\/]python\.exe$/);
});

test("getVenvPython returns bin/python on POSIX", { skip: process.platform === "win32" }, () => {
  const result = getVenvPython("/tmp/venv");
  assert.equal(result, "/tmp/venv/bin/python");
});

// ─── _computePluginDataDir deterministic ────────────────────────────────────

test("_computePluginDataDir computes deterministic path from plugin ID", () => {
  // Temporarily unset CLAUDE_PLUGIN_DATA to test the fallback path
  const restore = process.env.CLAUDE_PLUGIN_DATA;
  delete process.env.CLAUDE_PLUGIN_DATA;
  try {
    const result = _computePluginDataDir();
    const expected = path.join(
      os.homedir(),
      ".claude",
      "plugins",
      "data",
      "claude-wayfinder-glitchwerks"
    );
    assert.equal(result, expected);
  } finally {
    if (restore !== undefined) process.env.CLAUDE_PLUGIN_DATA = restore;
  }
});

// ─── normalizeVenvPath ───────────────────────────────────────────────────────

test(
  "normalizeVenvPath converts /c/foo to C:/foo on Windows",
  { skip: process.platform !== "win32" },
  () => {
    assert.equal(normalizeVenvPath("/c/foo"), "C:/foo");
  }
);

test(
  "normalizeVenvPath converts /c/Users/chris/.claude/... to C:/Users/... on Windows",
  { skip: process.platform !== "win32" },
  () => {
    assert.equal(
      normalizeVenvPath("/c/Users/chris/.claude/plugins/data/claude-wayfinder-glitchwerks/venv"),
      "C:/Users/chris/.claude/plugins/data/claude-wayfinder-glitchwerks/venv"
    );
  }
);

test(
  "normalizeVenvPath is idempotent for already-native Windows paths",
  { skip: process.platform !== "win32" },
  () => {
    assert.equal(normalizeVenvPath("C:/Users/chris/venv"), "C:/Users/chris/venv");
  }
);

test(
  "normalizeVenvPath does not rewrite single-component /foo path (no drive letter pattern)",
  { skip: process.platform !== "win32" },
  () => {
    // /foo has no second slash immediately after a single letter → not a POSIX drive path
    assert.equal(normalizeVenvPath("/foo"), "/foo");
  }
);

test(
  "normalizeVenvPath returns non-string input unchanged",
  { skip: process.platform !== "win32" },
  () => {
    assert.equal(normalizeVenvPath(null), null);
    assert.equal(normalizeVenvPath(undefined), undefined);
    assert.equal(normalizeVenvPath(42), 42);
  }
);

test(
  "normalizeVenvPath is a no-op on POSIX",
  { skip: process.platform === "win32" },
  () => {
    assert.equal(normalizeVenvPath("/c/foo"), "/c/foo");
  }
);

// ─── readSetupState with legacy POSIX /c/ venv_path (regression #186) ───────

test(
  "readSetupState classifies legacy POSIX /c/ venv_path as VALID on Windows",
  { skip: process.platform !== "win32" },
  () => {
    // Simulate a flag written by Git Bash where $HOME was /c/Users/...
    // The venv_path stored is in POSIX form; the actual venv lives at C:/...
    withTempPluginData((dir) => {
      const venvDir = path.join(dir, "venv");
      plantVenvPython(venvDir);

      // venvDir is a real Windows path; construct its POSIX-style /X/... equivalent.
      // e.g. "C:\Users\...\venv" → "/c/Users/.../venv"
      const driveLetter = venvDir[0].toLowerCase(); // e.g. "c"
      const rest = venvDir.slice(2).replace(/\\/g, "/"); // strip "C:" and normalize slashes
      const posixVenvPath = `/${driveLetter}${rest}`;

      fs.writeFileSync(
        path.join(dir, "setup-state.json"),
        JSON.stringify({
          version: "0.4.0",
          venv_path: posixVenvPath, // <-- POSIX form, as Git Bash would write
          interpreter: "python",
          installed_at: "2026-05-20T00:00:00Z",
        })
      );

      const result = readSetupState("0.4.0");
      assert.equal(result.status, "VALID", `Expected VALID, got ${result.status}`);
    });
  }
);
