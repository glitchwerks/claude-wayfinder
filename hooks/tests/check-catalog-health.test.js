const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const HOOK = path.join(__dirname, "..", "check-catalog-health.js");

// Build a minimal fake ~/.claude tree rooted at `base`:
//   base/state/dispatch-catalog.json
//   base/skills/example/SKILL.md
//   base/agents/example.md
function makeFakeClaudeHome(base) {
  fs.mkdirSync(path.join(base, "state"), { recursive: true });
  fs.mkdirSync(path.join(base, "skills", "example"), { recursive: true });
  fs.mkdirSync(path.join(base, "agents"), { recursive: true });
}

function runHook(catalogPath, claudeHome) {
  const env = {
    ...process.env,
    DISPATCH_CATALOG_PATH: catalogPath,
  };
  if (claudeHome !== undefined) {
    env.CLAUDE_HOME = claudeHome;
  }
  const r = spawnSync("node", [HOOK], { env, input: "{}", encoding: "utf8" });
  return { stdout: r.stdout, status: r.status };
}

// ---------------------------------------------------------------------------
// Existing tests (preserved)
// ---------------------------------------------------------------------------

test("emits banner when catalog file is missing", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "cat-"));
  const out = runHook(path.join(tmp, "absent.json"));
  assert.equal(out.status, 0);
  const parsed = JSON.parse(out.stdout);
  assert.equal(parsed.hookSpecificOutput.hookEventName, "SessionStart");
  assert.match(parsed.hookSpecificOutput.additionalContext, /\[CATALOG ERROR\]/);
});

test("emits banner when catalog has zero entries", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "cat-"));
  const f = path.join(tmp, "empty.json");
  fs.writeFileSync(f, JSON.stringify({ schema_version: 1, entries: [] }));
  const out = runHook(f);
  const parsed = JSON.parse(out.stdout);
  assert.match(parsed.hookSpecificOutput.additionalContext, /\[CATALOG ERROR\]/);
});

test("silent no-op when catalog has entries", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "cat-"));
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "claude-home-"));
  makeFakeClaudeHome(home);

  const f = path.join(tmp, "ok.json");
  fs.writeFileSync(
    f,
    JSON.stringify({
      schema_version: 1,
      entries: [{ name: "x", kind: "skill" }],
    })
  );

  // Set catalog mtime to now, source files to 5 minutes ago — catalog is fresh.
  const pastTime = new Date(Date.now() - 5 * 60 * 1000);
  const skillFile = path.join(home, "skills", "example", "SKILL.md");
  const agentFile = path.join(home, "agents", "example.md");
  fs.writeFileSync(skillFile, "# skill");
  fs.writeFileSync(agentFile, "# agent");
  fs.utimesSync(skillFile, pastTime, pastTime);
  fs.utimesSync(agentFile, pastTime, pastTime);
  // catalog written after source files, so it is newer
  fs.writeFileSync(
    f,
    JSON.stringify({ schema_version: 1, entries: [{ name: "x", kind: "skill" }] })
  );

  const out = runHook(f, home);
  assert.equal(out.stdout.trim(), "");
});

// ---------------------------------------------------------------------------
// New tests
// ---------------------------------------------------------------------------

test("catalog parse-error exits 2 with banner", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "cat-"));
  const f = path.join(tmp, "broken.json");
  fs.writeFileSync(f, "this is not json {{{");
  const out = runHook(f);
  assert.equal(out.status, 2);
  const parsed = JSON.parse(out.stdout);
  assert.match(parsed.hookSpecificOutput.additionalContext, /\[CATALOG ERROR\]/);
});

test("stale catalog emits [CATALOG STALE] banner and exits 0", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "cat-"));
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "claude-home-"));
  makeFakeClaudeHome(home);

  const catalogFile = path.join(tmp, "catalog.json");
  fs.writeFileSync(
    catalogFile,
    JSON.stringify({ schema_version: 1, entries: [{ name: "x", kind: "skill" }] })
  );

  // Set catalog mtime to 10 minutes ago, then write a SKILL.md with a newer mtime.
  const pastTime = new Date(Date.now() - 10 * 60 * 1000);
  fs.utimesSync(catalogFile, pastTime, pastTime);

  const skillFile = path.join(home, "skills", "example", "SKILL.md");
  fs.writeFileSync(skillFile, "# newer skill");
  // skillFile's mtime defaults to now — newer than the catalog

  const out = runHook(catalogFile, home);
  assert.equal(out.status, 0);
  const parsed = JSON.parse(out.stdout);
  assert.match(parsed.hookSpecificOutput.additionalContext, /\[CATALOG STALE\]/);
});

test("fresh catalog (newer than all sources) is silent", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "cat-"));
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "claude-home-"));
  makeFakeClaudeHome(home);

  const skillFile = path.join(home, "skills", "example", "SKILL.md");
  const agentFile = path.join(home, "agents", "example.md");
  fs.writeFileSync(skillFile, "# skill");
  fs.writeFileSync(agentFile, "# agent");

  // Source files are 5 minutes old; catalog written after → catalog is newest.
  const pastTime = new Date(Date.now() - 5 * 60 * 1000);
  fs.utimesSync(skillFile, pastTime, pastTime);
  fs.utimesSync(agentFile, pastTime, pastTime);

  const catalogFile = path.join(tmp, "catalog.json");
  fs.writeFileSync(
    catalogFile,
    JSON.stringify({ schema_version: 1, entries: [{ name: "x", kind: "skill" }] })
  );

  const out = runHook(catalogFile, home);
  assert.equal(out.status, 0);
  assert.equal(out.stdout.trim(), "");
});
