# Telemetry enrichment v2 — bypass-cause taxonomy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enrich `~/.claude/state/router-drift.jsonl` events (`bypass`, `skill_mediated`, `stale_dispatch`) with a `bypass_signals` object and a `bypass_cause` enum hint, plus a Python analyzer + router-health section that surfaces the taxonomy.

**Architecture:** Pure-JS module (`hooks/lib/bypass-taxonomy.js`) computes signals + cause from data the existing PreToolUse hook already has. The hook calls into it just before emitting the drift event. A new Python analyzer (`scripts/analyze-drift-causes.py`) and a new section in `src/claude_wayfinder/_health.py` consume the enriched events.

**Tech Stack:** Node.js (≥18, matches existing hook runtime); Python ≥ 3.11 stdlib only; existing `node:test` for JS unit tests; pytest for Python tests.

**Spec:** `docs/superpowers/specs/2026-05-19-telemetry-bypass-taxonomy-design.md` (v2-draft4)

**Tracking issue:** #143 · **Follow-ups already filed:** #159 (F-1), #160 (F-2), #161 (F-3)

**Boundary notice:** The spec lists `skills/router-health/SKILL.md` under `touches:`. **That file lives in the user's `~/.claude/skills/router-health/` tree, not in this plugin's repo.** The implementing PR ships only in-plugin work. The SKILL.md trigger-phrase update is handled as a **manual user-scope step** at the end of this plan (Task 11). Do not attempt to edit a `skills/router-health/SKILL.md` inside this repo — it does not exist here.

---

## File Structure

| File                                            | Purpose                                                                              | Create/Modify |
| ----------------------------------------------- | ------------------------------------------------------------------------------------ | ------------- |
| `hooks/lib/bypass-taxonomy.js`                  | Pure function `classify(category, toolCall, conversationHistory) → {signals, cause}` and `INTERACTIVE_SKILLS` set. | Create |
| `hooks/tests/bypass-taxonomy.test.js`           | Unit tests for the module (one per cause, plus signal-derivation tests).             | Create |
| `hooks/check-agent-dispatch-pairing.js`         | Load the new module, call `classify()` before emitting drift events, fail-open on any error. | Modify |
| `hooks/tests/check-agent-dispatch-pairing.test.js` | Add 3 failure-mode tests (classify-time failure, module-load failure).            | Modify (or extend) |
| `scripts/analyze-drift-causes.py`               | Python CLI that reads `router-drift.jsonl`, groups by cause, supports `--days`, `--disagreements`, `--by-agent`, `--json`. | Create (also create `scripts/` dir) |
| `tests/test_analyze_drift_causes.py`            | Pytest tests with crafted JSONL fixtures.                                            | Create |
| `src/claude_wayfinder/_health.py`               | New "Bypass causes" section in `format_report_output()`; two new thresholds; low-N guard. | Modify |
| `tests/test_health.py`                          | Add tests for the new section (PASS/WARN/FAIL/low-N rendering, pre-enrichment skipping). | Modify |

**Out-of-plugin (Task 11, manual):** `~/.claude/skills/router-health/SKILL.md` — add "bypass causes" trigger phrase.

---

## Task 1: Create the taxonomy module scaffold

**Files:**
- Create: `hooks/lib/bypass-taxonomy.js`
- Test: `hooks/tests/bypass-taxonomy.test.js`

- [ ] **Step 1.1: Write the failing test for module exports**

Create `hooks/tests/bypass-taxonomy.test.js`:

```js
/**
 * Tests for hooks/lib/bypass-taxonomy.js — the classify() helper that enriches
 * drift events emitted by check-agent-dispatch-pairing.js.
 *
 * See docs/superpowers/specs/2026-05-19-telemetry-bypass-taxonomy-design.md
 */

const { test } = require("node:test");
const assert = require("node:assert/strict");

const { classify, INTERACTIVE_SKILLS } = require("../lib/bypass-taxonomy");

test("module exports classify function and INTERACTIVE_SKILLS set", () => {
  assert.equal(typeof classify, "function");
  assert.ok(INTERACTIVE_SKILLS instanceof Set);
  assert.ok(INTERACTIVE_SKILLS.size >= 5);
  for (const name of [
    "gh-create-issue",
    "project-review",
    "gh-pr-review-address",
    "claude-audit",
    "gh-refresh-issues",
  ]) {
    assert.ok(INTERACTIVE_SKILLS.has(name), `INTERACTIVE_SKILLS missing ${name}`);
  }
});
```

- [ ] **Step 1.2: Run the test and confirm it fails**

Run: `node --test hooks/tests/bypass-taxonomy.test.js`
Expected: FAIL with `Cannot find module '../lib/bypass-taxonomy'`.

- [ ] **Step 1.3: Implement the minimal module**

Create `hooks/lib/bypass-taxonomy.js`:

```js
// Bypass-cause taxonomy for router-drift.jsonl events.
//
// Pure module — no I/O, no module-level mutation. Consumed by
// hooks/check-agent-dispatch-pairing.js. See spec:
//   docs/superpowers/specs/2026-05-19-telemetry-bypass-taxonomy-design.md

/**
 * Skills the router does NOT delegate — they handle their own user-interactive
 * flow. Agent calls from inside one of these skills are expected bypasses.
 *
 * NOTE: this set has no automatic completeness gate. When a new interactive
 * skill ships, its Agent calls fall to `skill_mediated_other` until this set
 * is updated. The F-3 quarterly audit (issue #161) is the manual backstop.
 */
const INTERACTIVE_SKILLS = new Set([
  "gh-create-issue",
  "project-review",
  "gh-pr-review-address",
  "claude-audit",
  "gh-refresh-issues",
]);

/**
 * Classify a drift event by inspecting only the hook's already-computed
 * `category` field and the tool-call shape. No prompt content is read.
 *
 * Pure function — never throws on valid inputs; never performs I/O.
 *
 * @param {"bypass"|"skill_mediated"|"stale_dispatch"} category
 *        The hook's already-computed category for this event.
 * @param {{subagent_type?: string}} toolCall
 *        The Agent tool-call parameters (subagent_type is the only field read).
 * @param {Array<{toolName: string, skillName?: string}>} toolEvents
 *        Tool events extracted from conversation_history + sidecar. Must use
 *        the SAME window the hook uses: full history back to most recent
 *        dispatch, no user-turn boundary. (Spec §Signal set.)
 * @returns {{
 *   signals: {
 *     subagent_type: string,
 *     dispatch_skill_called_recently: boolean,
 *     count_agent_since_dispatch: number|null,
 *     last_skill_call_name: string|null,
 *     last_skill_call_is_interactive: boolean,
 *     turns_since_user_message: number
 *   },
 *   cause: "skill_mediated_interactive" | "skill_mediated_other"
 *        | "router_direct_after_consumed_dispatch"
 *        | "router_direct_no_dispatch"
 *        | "stale_dispatch" | "unknown"
 * }}
 */
function classify(category, toolCall, toolEvents) {
  const signals = extractSignals(toolCall, toolEvents);
  const cause = deriveCause(category, signals);
  return { signals, cause };
}

function extractSignals(toolCall, toolEvents) {
  // Stub — filled in by Task 3.
  return {
    subagent_type: (toolCall && toolCall.subagent_type) || "",
    dispatch_skill_called_recently: false,
    count_agent_since_dispatch: null,
    last_skill_call_name: null,
    last_skill_call_is_interactive: false,
    turns_since_user_message: 0,
  };
}

function deriveCause(category, signals) {
  // Stub — filled in by Task 2.
  return "unknown";
}

module.exports = { classify, INTERACTIVE_SKILLS };
```

- [ ] **Step 1.4: Run the test and confirm it passes**

Run: `node --test hooks/tests/bypass-taxonomy.test.js`
Expected: 1 PASS, 0 FAIL.

- [ ] **Step 1.5: Commit**

```bash
git add hooks/lib/bypass-taxonomy.js hooks/tests/bypass-taxonomy.test.js
git commit -m "feat(#143): scaffold bypass-taxonomy module"
```

---

## Task 2: Implement the cause-derivation decision tree (TDD)

**Files:**
- Modify: `hooks/lib/bypass-taxonomy.js` (replace `deriveCause` stub)
- Modify: `hooks/tests/bypass-taxonomy.test.js`

- [ ] **Step 2.1: Add failing tests, one per cause**

Append to `hooks/tests/bypass-taxonomy.test.js`:

```js
const { classify } = require("../lib/bypass-taxonomy");

// Helper: build a fully-populated signals object with overrides.
function sig(overrides = {}) {
  return {
    subagent_type: "code-writer",
    dispatch_skill_called_recently: false,
    count_agent_since_dispatch: null,
    last_skill_call_name: null,
    last_skill_call_is_interactive: false,
    turns_since_user_message: 1,
    ...overrides,
  };
}

// We test deriveCause indirectly via classify(); since extractSignals is
// stubbed until Task 3, we use the same shape the real signals will have.
// To exercise deriveCause directly here, we re-require the module and call
// the internal helper via a private export added in this task.

const { _deriveCauseForTest } = require("../lib/bypass-taxonomy");

test("skill_mediated + last_skill_call_is_interactive → skill_mediated_interactive", () => {
  const cause = _deriveCauseForTest(
    "skill_mediated",
    sig({ last_skill_call_is_interactive: true, last_skill_call_name: "gh-create-issue" })
  );
  assert.equal(cause, "skill_mediated_interactive");
});

test("skill_mediated + non-interactive skill → skill_mediated_other", () => {
  const cause = _deriveCauseForTest(
    "skill_mediated",
    sig({ last_skill_call_is_interactive: false, last_skill_call_name: "some-other-skill" })
  );
  assert.equal(cause, "skill_mediated_other");
});

test("bypass + count_agent_since_dispatch >= 1 → router_direct_after_consumed_dispatch", () => {
  const cause = _deriveCauseForTest(
    "bypass",
    sig({ dispatch_skill_called_recently: true, count_agent_since_dispatch: 1 })
  );
  assert.equal(cause, "router_direct_after_consumed_dispatch");
});

test("bypass + no dispatch in history → router_direct_no_dispatch", () => {
  const cause = _deriveCauseForTest(
    "bypass",
    sig({ dispatch_skill_called_recently: false, count_agent_since_dispatch: null })
  );
  assert.equal(cause, "router_direct_no_dispatch");
});

test("stale_dispatch category → stale_dispatch cause", () => {
  const cause = _deriveCauseForTest("stale_dispatch", sig({}));
  assert.equal(cause, "stale_dispatch");
});

test("unrecognized category → unknown", () => {
  const cause = _deriveCauseForTest("something_new", sig({}));
  assert.equal(cause, "unknown");
});

test("bypass + dispatch_recent + count == 0 → unknown (defensive)", () => {
  // Should not arise per hook logic, but tree must handle it without crashing.
  const cause = _deriveCauseForTest(
    "bypass",
    sig({ dispatch_skill_called_recently: true, count_agent_since_dispatch: 0 })
  );
  assert.equal(cause, "unknown");
});

test("null count_agent_since_dispatch is never compared with >=", () => {
  // Regression guard: null >= 1 is false in JS (safe), but we want to
  // verify the tree explicitly checks dispatch_skill_called_recently first
  // so null never reaches the comparison.
  const cause = _deriveCauseForTest(
    "bypass",
    sig({ dispatch_skill_called_recently: false, count_agent_since_dispatch: null })
  );
  // Must NOT crash, must return router_direct_no_dispatch.
  assert.equal(cause, "router_direct_no_dispatch");
});
```

- [ ] **Step 2.2: Run tests and confirm failures**

Run: `node --test hooks/tests/bypass-taxonomy.test.js`
Expected: 8 new FAILs (`_deriveCauseForTest` is not exported; all causes return `unknown`).

- [ ] **Step 2.3: Implement `deriveCause` and add private test export**

In `hooks/lib/bypass-taxonomy.js`, replace the `deriveCause` stub with:

```js
function deriveCause(category, signals) {
  switch (category) {
    case "stale_dispatch":
      return "stale_dispatch";

    case "skill_mediated":
      return signals.last_skill_call_is_interactive
        ? "skill_mediated_interactive"
        : "skill_mediated_other";

    case "bypass":
      // Check dispatch presence FIRST so null count_agent_since_dispatch
      // is never reached by the >=1 comparison. (Spec §Cause enum, pass-2 fix.)
      if (!signals.dispatch_skill_called_recently) {
        return "router_direct_no_dispatch";
      }
      if (signals.count_agent_since_dispatch >= 1) {
        return "router_direct_after_consumed_dispatch";
      }
      // bypass + dispatch_recent + count == 0 — should not arise per hook
      // logic (classifyDispatchRich returns router_mediated or stale_dispatch
      // in that shape); defensive bucket, expected ~0.
      return "unknown";

    default:
      return "unknown";
  }
}
```

And update the `module.exports` line at the bottom:

```js
module.exports = { classify, INTERACTIVE_SKILLS, _deriveCauseForTest: deriveCause };
```

- [ ] **Step 2.4: Run tests and confirm all pass**

Run: `node --test hooks/tests/bypass-taxonomy.test.js`
Expected: 9 PASS, 0 FAIL (1 from Task 1 + 8 new).

- [ ] **Step 2.5: Commit**

```bash
git add hooks/lib/bypass-taxonomy.js hooks/tests/bypass-taxonomy.test.js
git commit -m "feat(#143): implement bypass-cause decision tree"
```

---

## Task 3: Implement signal extraction (TDD)

**Files:**
- Modify: `hooks/lib/bypass-taxonomy.js` (replace `extractSignals` stub)
- Modify: `hooks/tests/bypass-taxonomy.test.js`

- [ ] **Step 3.1: Add failing tests for signal extraction**

Append to `hooks/tests/bypass-taxonomy.test.js`:

```js
// Signal-extraction tests. classify() is the public entry; we feed it a
// hand-crafted toolEvents array (same shape as classifyDispatchRich consumes:
// {toolName, skillName?}).

function ev(toolName, skillName) {
  return skillName ? { toolName, skillName } : { toolName };
}

test("signals: no dispatch in history → recently=false, count=null", () => {
  const { signals } = classify("bypass", { subagent_type: "code-writer" }, [
    ev("Read"),
    ev("Edit"),
  ]);
  assert.equal(signals.dispatch_skill_called_recently, false);
  assert.equal(signals.count_agent_since_dispatch, null);
  assert.equal(signals.last_skill_call_name, null);
  assert.equal(signals.last_skill_call_is_interactive, false);
});

test("signals: dispatch in history, no Agent after → recently=true, count=0", () => {
  const { signals } = classify(
    "stale_dispatch",
    { subagent_type: "code-writer" },
    [ev("Skill", "dispatch"), ev("Read"), ev("Edit")]
  );
  assert.equal(signals.dispatch_skill_called_recently, true);
  assert.equal(signals.count_agent_since_dispatch, 0);
});

test("signals: dispatch in history, one Agent after → recently=true, count=1", () => {
  const { signals } = classify(
    "bypass",
    { subagent_type: "code-writer" },
    [ev("Skill", "dispatch"), ev("Agent"), ev("Read")]
  );
  assert.equal(signals.dispatch_skill_called_recently, true);
  assert.equal(signals.count_agent_since_dispatch, 1);
});

test("signals: dispatch + two Agents after → count=2", () => {
  const { signals } = classify(
    "bypass",
    { subagent_type: "ops" },
    [ev("Skill", "dispatch"), ev("Agent"), ev("Edit"), ev("Agent")]
  );
  assert.equal(signals.count_agent_since_dispatch, 2);
});

test("signals: last_skill_call_name is the most recent non-dispatch Skill", () => {
  const { signals } = classify(
    "skill_mediated",
    { subagent_type: "code-writer" },
    [ev("Skill", "gh-create-issue"), ev("Read")]
  );
  assert.equal(signals.last_skill_call_name, "gh-create-issue");
  assert.equal(signals.last_skill_call_is_interactive, true);
});

test("signals: last_skill_call_is_interactive false for unknown skill", () => {
  const { signals } = classify(
    "skill_mediated",
    { subagent_type: "code-writer" },
    [ev("Skill", "weird-custom-skill"), ev("Read")]
  );
  assert.equal(signals.last_skill_call_name, "weird-custom-skill");
  assert.equal(signals.last_skill_call_is_interactive, false);
});

test("signals: subagent_type passes through", () => {
  const { signals } = classify(
    "bypass",
    { subagent_type: "doc-writer" },
    []
  );
  assert.equal(signals.subagent_type, "doc-writer");
});

test("signals: empty/null toolCall gives empty subagent_type, no throw", () => {
  const { signals } = classify("bypass", null, []);
  assert.equal(signals.subagent_type, "");
});
```

- [ ] **Step 3.2: Run and confirm failures**

Run: `node --test hooks/tests/bypass-taxonomy.test.js`
Expected: 8 new FAILs in the signal-extraction tests (stub returns hardcoded zeros).

- [ ] **Step 3.3: Implement `extractSignals`**

In `hooks/lib/bypass-taxonomy.js`, replace the `extractSignals` stub with:

```js
function extractSignals(toolCall, toolEvents) {
  const evts = Array.isArray(toolEvents) ? toolEvents : [];

  // Locate the most recent dispatch Skill call — matches the hook's
  // window (full history, no user-turn boundary).
  let lastDispatchIdx = -1;
  for (let i = evts.length - 1; i >= 0; i--) {
    if (evts[i].toolName === "Skill" && evts[i].skillName === "dispatch") {
      lastDispatchIdx = i;
      break;
    }
  }

  let countAgentSinceDispatch = null;
  if (lastDispatchIdx !== -1) {
    countAgentSinceDispatch = 0;
    for (let i = lastDispatchIdx + 1; i < evts.length; i++) {
      if (evts[i].toolName === "Agent") {
        countAgentSinceDispatch++;
      }
    }
  }

  // Find the most recent non-dispatch Skill call (for skill_mediated cases).
  let lastSkillCallName = null;
  for (let i = evts.length - 1; i >= 0; i--) {
    const e = evts[i];
    if (e.toolName === "Skill" && e.skillName && e.skillName !== "dispatch") {
      lastSkillCallName = e.skillName;
      break;
    }
  }

  return {
    subagent_type: (toolCall && toolCall.subagent_type) || "",
    dispatch_skill_called_recently: lastDispatchIdx !== -1,
    count_agent_since_dispatch: countAgentSinceDispatch,
    last_skill_call_name: lastSkillCallName,
    last_skill_call_is_interactive:
      lastSkillCallName !== null && INTERACTIVE_SKILLS.has(lastSkillCallName),
    // turns_since_user_message — not currently computable from the
    // toolEvents shape alone (which lacks turn-role info). Surface as 0
    // for v1; the analyzer does not depend on it. F-2 review can revisit.
    turns_since_user_message: 0,
  };
}
```

- [ ] **Step 3.4: Run and confirm pass**

Run: `node --test hooks/tests/bypass-taxonomy.test.js`
Expected: 17 PASS, 0 FAIL.

- [ ] **Step 3.5: Commit**

```bash
git add hooks/lib/bypass-taxonomy.js hooks/tests/bypass-taxonomy.test.js
git commit -m "feat(#143): implement signal extraction from tool events"
```

---

## Task 4: Wire the taxonomy module into the hook

**Files:**
- Modify: `hooks/check-agent-dispatch-pairing.js`

- [ ] **Step 4.1: Load the module at hook startup**

In `hooks/check-agent-dispatch-pairing.js`, immediately after the `const parseInput = require("./parse-input");` line (~line 33), add:

```js
// Lazy load the bypass-taxonomy module with explicit module-load error
// handling. A require-time throw cannot kill the hook because the require
// runs inside its own try; the fallback `null` is short-circuited at
// use-time (see emit path below). Spec §Hook integration.
let _bypassTaxonomyClassify = null;
try {
  ({ classify: _bypassTaxonomyClassify } = require("./lib/bypass-taxonomy"));
} catch (err) {
  process.stderr.write(
    `[bypass-taxonomy] module load failed; events will emit without enrichment: ${err.message}\n`
  );
  _bypassTaxonomyClassify = null;
}
```

- [ ] **Step 4.2: Call classify before emitting the drift event**

In `hooks/check-agent-dispatch-pairing.js`, locate the event-emission block at lines 333-341 (the block that builds `const event = { type: "router_drift", ... }`). Replace that block with:

```js
      // Write a drift event for all non-router-mediated categories.
      const event = {
        type: "router_drift",
        ts: new Date().toISOString(),
        session_id: sessionId,
        category,
      };

      // Enrich with bypass_signals + bypass_cause when possible.
      // Three guarded failure modes: module-load throw (handled above by
      // _bypassTaxonomyClassify=null), per-event classify throw (try/catch
      // below), and malformed return shape (if-checks below). All three
      // recover by emitting the event without enrichment.
      if (_bypassTaxonomyClassify) {
        try {
          const result = _bypassTaxonomyClassify(
            category,
            { subagent_type: input?.tool_input?.subagent_type ?? "" },
            toolEvents
          );
          if (
            result &&
            typeof result.cause === "string" &&
            result.signals &&
            typeof result.signals === "object"
          ) {
            event.bypass_signals = result.signals;
            event.bypass_cause = result.cause;
          } else {
            process.stderr.write(
              "[bypass-taxonomy] classify returned malformed shape; emitting without enrichment\n"
            );
          }
        } catch (err) {
          process.stderr.write(
            `[bypass-taxonomy] classify threw; emitting without enrichment: ${err.message}\n`
          );
        }
      }

      appendDriftEvent(event, driftPath);
```

- [ ] **Step 4.3: Run the existing hook tests to confirm nothing breaks**

Run: `node --test hooks/tests/check-agent-dispatch-pairing.test.js`
Expected: all existing tests PASS (the hook's classification logic is unchanged; only the emit shape gained fields).

If any existing test asserts on the exact JSON shape and now sees extra fields, update the assertion to use `.includes` / `.deepInclude` semantics — see Task 5 for the integration-test additions.

- [ ] **Step 4.4: Commit**

```bash
git add hooks/check-agent-dispatch-pairing.js
git commit -m "feat(#143): integrate bypass-taxonomy into PreToolUse hook"
```

---

## Task 5: Hook integration failure-mode tests

**Files:**
- Modify: `hooks/tests/check-agent-dispatch-pairing.test.js`
- Create: `hooks/tests/fixtures/broken-bypass-taxonomy.js` (for module-load test)

- [ ] **Step 5.1: Write the classify-time failure-mode test (parameterized)**

Append to `hooks/tests/check-agent-dispatch-pairing.test.js`:

```js
const path = require("node:path");
const fs = require("node:fs");
const os = require("node:os");
const { spawnSync } = require("node:child_process");
const { test } = require("node:test");
const assert = require("node:assert/strict");

// Helper: spawn the hook as a subprocess with given stdin and env.
// Returns {stdout, stderr, status, driftLines}.
function spawnHook({ stdin, env = {}, classifyOverridePath = null }) {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "btx-hook-"));
  const driftPath = path.join(tmpDir, "router-drift.jsonl");
  const sidecarPath = path.join(tmpDir, "sidecar.jsonl");

  const fullEnv = {
    ...process.env,
    ROUTER_DRIFT_PATH: driftPath,
    SKILL_SIDECAR_PATH: sidecarPath,
    ...env,
  };

  // Optionally override module resolution to point at a broken/stubbed file.
  let nodeArgs = [path.resolve(__dirname, "..", "check-agent-dispatch-pairing.js")];
  if (classifyOverridePath) {
    // Use NODE_OPTIONS to require a preloader that monkey-patches the module path
    // (simpler than mocking require).
    fullEnv.BTX_OVERRIDE = classifyOverridePath;
    nodeArgs = [
      "-e",
      `process.env.BTX_OVERRIDE && (require.cache[require.resolve('${path
        .resolve(__dirname, "..", "lib", "bypass-taxonomy.js")
        .replace(/\\/g, "/")}')] = { exports: require(process.env.BTX_OVERRIDE) });require('${path
        .resolve(__dirname, "..", "check-agent-dispatch-pairing.js")
        .replace(/\\/g, "/")}');`,
    ];
  }

  const result = spawnSync("node", nodeArgs, {
    input: stdin,
    env: fullEnv,
    encoding: "utf8",
  });

  const driftLines = fs.existsSync(driftPath)
    ? fs
        .readFileSync(driftPath, "utf8")
        .split("\n")
        .filter(Boolean)
        .map((l) => JSON.parse(l))
    : [];

  return { stdout: result.stdout, stderr: result.stderr, status: result.status, driftLines };
}

// Helper: build a stdin payload that triggers a bypass categorization.
function bypassPayload() {
  return JSON.stringify({
    tool_name: "Agent",
    tool_input: { subagent_type: "code-writer", prompt: "test" },
    session_id: "test-session-failure-mode",
    conversation_history: [
      {
        role: "assistant",
        content: [{ type: "tool_use", name: "Agent" }],
      },
      {
        role: "assistant",
        content: [{ type: "tool_use", name: "Edit" }],
      },
    ],
  });
}

test("hook emits event without enrichment when classify throws", () => {
  // Build a broken classify implementation that throws.
  const overrideDir = fs.mkdtempSync(path.join(os.tmpdir(), "btx-override-"));
  const overridePath = path.join(overrideDir, "throwing-taxonomy.js");
  fs.writeFileSync(
    overridePath,
    `module.exports = {
       classify: () => { throw new Error("intentional test failure"); },
       INTERACTIVE_SKILLS: new Set(),
     };`
  );

  const { stderr, driftLines } = spawnHook({
    stdin: bypassPayload(),
    classifyOverridePath: overridePath,
  });

  assert.equal(driftLines.length, 1, "exactly one drift event written");
  assert.equal(driftLines[0].type, "router_drift");
  assert.equal(driftLines[0].category, "bypass");
  assert.equal(driftLines[0].bypass_signals, undefined);
  assert.equal(driftLines[0].bypass_cause, undefined);
  assert.match(stderr, /classify threw/);
});

test("hook emits event without enrichment when classify returns malformed shape", () => {
  const overrideDir = fs.mkdtempSync(path.join(os.tmpdir(), "btx-override-"));
  const overridePath = path.join(overrideDir, "malformed-taxonomy.js");
  fs.writeFileSync(
    overridePath,
    `module.exports = {
       classify: () => ({}),  // missing both signals and cause
       INTERACTIVE_SKILLS: new Set(),
     };`
  );

  const { stderr, driftLines } = spawnHook({
    stdin: bypassPayload(),
    classifyOverridePath: overridePath,
  });

  assert.equal(driftLines.length, 1);
  assert.equal(driftLines[0].bypass_signals, undefined);
  assert.equal(driftLines[0].bypass_cause, undefined);
  assert.match(stderr, /malformed shape/);
});

test("hook starts and emits event when bypass-taxonomy module is unloadable", () => {
  // Point at a file that's syntactically broken.
  const overrideDir = fs.mkdtempSync(path.join(os.tmpdir(), "btx-broken-"));
  const overridePath = path.join(overrideDir, "broken-taxonomy.js");
  fs.writeFileSync(overridePath, `this is not valid javascript {{{`);

  // For module-load failure we don't use the cache-override trick — we
  // spawn the hook with NODE_PATH preferring our broken file. Simpler:
  // copy the hook into a tmp dir with a sibling broken lib/.
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "btx-modload-"));
  const tmpHookPath = path.join(tmpDir, "check-agent-dispatch-pairing.js");
  const tmpLibDir = path.join(tmpDir, "lib");
  fs.mkdirSync(tmpLibDir);
  fs.copyFileSync(
    path.resolve(__dirname, "..", "check-agent-dispatch-pairing.js"),
    tmpHookPath
  );
  fs.copyFileSync(
    path.resolve(__dirname, "..", "parse-input.js"),
    path.join(tmpDir, "parse-input.js")
  );
  fs.writeFileSync(path.join(tmpLibDir, "bypass-taxonomy.js"), "this is not valid javascript {{{");

  const driftPath = path.join(tmpDir, "router-drift.jsonl");
  const result = spawnSync("node", [tmpHookPath], {
    input: bypassPayload(),
    env: {
      ...process.env,
      ROUTER_DRIFT_PATH: driftPath,
      SKILL_SIDECAR_PATH: path.join(tmpDir, "sidecar.jsonl"),
    },
    encoding: "utf8",
  });

  assert.equal(result.status, 0, "hook exited 0 despite module-load failure");
  assert.match(result.stderr, /module load failed/);
  assert.ok(fs.existsSync(driftPath), "drift event still emitted");
  const lines = fs.readFileSync(driftPath, "utf8").trim().split("\n");
  assert.equal(lines.length, 1);
  const event = JSON.parse(lines[0]);
  assert.equal(event.bypass_signals, undefined);
  assert.equal(event.bypass_cause, undefined);
});
```

- [ ] **Step 5.2: Run and confirm pass**

Run: `node --test hooks/tests/check-agent-dispatch-pairing.test.js`
Expected: all existing tests PASS + 3 new failure-mode tests PASS.

- [ ] **Step 5.3: Add a positive integration test (enriched event shape)**

Append:

```js
test("hook emits enriched event with bypass_signals and bypass_cause for bypass category", () => {
  const { driftLines } = spawnHook({ stdin: bypassPayload() });
  assert.equal(driftLines.length, 1);
  const event = driftLines[0];
  assert.equal(event.type, "router_drift");
  assert.equal(event.category, "bypass");
  assert.ok(event.bypass_signals, "bypass_signals present");
  assert.equal(typeof event.bypass_cause, "string");
  // bypassPayload has Agent then Edit → bypass with no dispatch → router_direct_no_dispatch
  assert.equal(event.bypass_cause, "router_direct_no_dispatch");
  assert.equal(event.bypass_signals.subagent_type, "code-writer");
  assert.equal(event.bypass_signals.dispatch_skill_called_recently, false);
  assert.equal(event.bypass_signals.count_agent_since_dispatch, null);
});
```

- [ ] **Step 5.4: Run and confirm pass**

Run: `node --test hooks/tests/check-agent-dispatch-pairing.test.js`
Expected: all pass.

- [ ] **Step 5.5: Commit**

```bash
git add hooks/tests/check-agent-dispatch-pairing.test.js
git commit -m "test(#143): hook integration + 3 failure-mode tests"
```

---

## Task 6: Create the analyzer Python script

**Files:**
- Create: `scripts/` directory (does not yet exist in repo)
- Create: `scripts/analyze-drift-causes.py`
- Test: `tests/test_analyze_drift_causes.py` (built in Task 7)

- [ ] **Step 6.1: Create the directory and a placeholder script**

```bash
mkdir -p scripts
```

- [ ] **Step 6.2: Write the analyzer**

Create `scripts/analyze-drift-causes.py`:

```python
"""Analyze bypass causes from router-drift.jsonl.

Reads ~/.claude/state/router-drift.jsonl (overridable via
ROUTER_DRIFT_PATH env), groups events by bypass_cause, and prints a
distribution report.

See docs/superpowers/specs/2026-05-19-telemetry-bypass-taxonomy-design.md
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Disposition mapping for each cause — used in human-readable output.
# Synced with spec §Cause enum.
DISPOSITION: dict[str, str] = {
    "skill_mediated_interactive": "expected",
    "skill_mediated_other": "review",
    "router_direct_after_consumed_dispatch": "unwanted",
    "router_direct_no_dispatch": "unwanted",
    "stale_dispatch": "review",
    "unknown": "review",
}

# Cause set the spec defines — events with a cause outside this set are
# treated as "unknown" for distribution purposes but counted separately
# for visibility.
KNOWN_CAUSES: frozenset[str] = frozenset(DISPOSITION.keys())


def default_drift_path() -> Path:
    """Return the configured drift-log path."""
    env = os.environ.get("ROUTER_DRIFT_PATH")
    if env:
        return Path(env)
    home = Path(os.environ.get("HOME") or os.environ.get("USERPROFILE") or Path.home())
    return home / ".claude" / "state" / "router-drift.jsonl"


@dataclasses.dataclass
class Event:
    """One parsed drift event."""

    ts: datetime | None
    category: str
    cause: str | None
    signals: dict | None
    subagent_type: str | None
    raw: dict


def parse_event(line: str) -> Event | None:
    """Parse one JSONL line into an Event, or None if malformed."""
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    if obj.get("type") != "router_drift":
        return None
    ts_str = obj.get("ts")
    ts: datetime | None = None
    if isinstance(ts_str, str):
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except ValueError:
            ts = None
    signals = obj.get("bypass_signals") if isinstance(obj.get("bypass_signals"), dict) else None
    return Event(
        ts=ts,
        category=str(obj.get("category", "")),
        cause=obj.get("bypass_cause") if isinstance(obj.get("bypass_cause"), str) else None,
        signals=signals,
        subagent_type=(signals or {}).get("subagent_type") if signals else None,
        raw=obj,
    )


def load_events(path: Path) -> list[Event]:
    """Load + parse all drift events from a JSONL file."""
    if not path.exists():
        return []
    out: list[Event] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        ev = parse_event(line)
        if ev is not None:
            out.append(ev)
    return out


def filter_window(
    events: list[Event],
    days: int | None,
    since: datetime | None,
) -> list[Event]:
    """Filter events to a time window. since takes precedence over days."""
    if since is None and days is None:
        return events
    if since is None:
        since = datetime.now(timezone.utc) - timedelta(days=days or 7)
    return [e for e in events if e.ts is not None and e.ts >= since]


def re_derive_cause(ev: Event) -> str | None:
    """Re-derive cause from signals for the --disagreements check.

    Mirrors deriveCause() in hooks/lib/bypass-taxonomy.js. Returns None if
    the event has no signals (pre-enrichment).
    """
    s = ev.signals
    if s is None:
        return None
    cat = ev.category
    if cat == "stale_dispatch":
        return "stale_dispatch"
    if cat == "skill_mediated":
        return (
            "skill_mediated_interactive"
            if s.get("last_skill_call_is_interactive")
            else "skill_mediated_other"
        )
    if cat == "bypass":
        if not s.get("dispatch_skill_called_recently"):
            return "router_direct_no_dispatch"
        c = s.get("count_agent_since_dispatch")
        if isinstance(c, int) and c >= 1:
            return "router_direct_after_consumed_dispatch"
        return "unknown"
    return "unknown"


def render_report(
    events: list[Event],
    *,
    days: int,
    show_disagreements: bool,
    by_agent: bool,
    as_json: bool,
) -> str:
    """Render the report as text or JSON."""
    enriched = [e for e in events if e.cause is not None]
    pre_enrichment = len(events) - len(enriched)

    counter = Counter(e.cause for e in enriched)
    total = sum(counter.values())

    if as_json:
        payload = {
            "window_days": days,
            "total_events": len(events),
            "enriched_events": len(enriched),
            "pre_enrichment_baseline": pre_enrichment,
            "distribution": [
                {
                    "cause": cause,
                    "count": cnt,
                    "share": cnt / total if total else 0,
                    "disposition": DISPOSITION.get(cause, "review"),
                }
                for cause, cnt in counter.most_common()
            ],
        }
        return json.dumps(payload, indent=2)

    lines: list[str] = []
    lines.append(
        f"Bypass cause distribution (last {days} days, {len(enriched)} enriched events;"
        f" {pre_enrichment} pre-enrichment baseline):"
    )
    lines.append("")
    for cause, cnt in counter.most_common():
        share = cnt / total if total else 0
        disp = DISPOSITION.get(cause, "review")
        marker = {"expected": "✓", "unwanted": "⚠", "review": "?"}.get(disp, " ")
        lines.append(f"  {cause:<40} {cnt:>5}  {share*100:>5.1f}%   {marker} {disp}")
    lines.append("")

    # Disagreement check
    disagreements = []
    for e in enriched:
        derived = re_derive_cause(e)
        if derived is not None and derived != e.cause:
            disagreements.append((e, derived))
    lines.append(
        f"Disagreement check: {len(disagreements)} events"
        f" ({(len(disagreements) / total * 100) if total else 0:.1f}%)"
        f" where re-derived cause from signals ≠ stored bypass_cause."
    )
    if show_disagreements and disagreements:
        lines.append("")
        lines.append("Disagreements:")
        for e, derived in disagreements:
            lines.append(
                f"  ts={e.ts.isoformat() if e.ts else '?'}"
                f" stored={e.cause} derived={derived}"
                f" agent={e.subagent_type}"
            )

    if by_agent:
        lines.append("")
        lines.append("By agent × cause:")
        cross: dict[tuple[str, str], int] = {}
        for e in enriched:
            key = (e.subagent_type or "?", e.cause or "?")
            cross[key] = cross.get(key, 0) + 1
        for (agent, cause), cnt in sorted(cross.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {agent:<20} {cause:<40} {cnt:>5}")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--days", type=int, default=7, help="Window in days (default: 7).")
    parser.add_argument("--since", type=str, default=None, help="ISO timestamp overriding --days.")
    parser.add_argument(
        "--disagreements",
        action="store_true",
        help="List events where re-derived cause ≠ stored bypass_cause.",
    )
    parser.add_argument(
        "--by-agent",
        action="store_true",
        help="Cross-tab cause × subagent_type.",
    )
    parser.add_argument("--json", dest="as_json", action="store_true", help="Machine-readable output.")
    parser.add_argument(
        "--drift-path",
        type=str,
        default=None,
        help="Override the drift-log path (default: $ROUTER_DRIFT_PATH or ~/.claude/state/router-drift.jsonl).",
    )
    args = parser.parse_args(argv)

    drift_path = Path(args.drift_path) if args.drift_path else default_drift_path()
    events = load_events(drift_path)

    since: datetime | None = None
    if args.since:
        since = datetime.fromisoformat(args.since.replace("Z", "+00:00"))

    windowed = filter_window(events, args.days, since)
    print(render_report(
        windowed,
        days=args.days,
        show_disagreements=args.disagreements,
        by_agent=args.by_agent,
        as_json=args.as_json,
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 6.3: Verify the script at least imports and shows help**

Run: `./.venv/Scripts/python.exe scripts/analyze-drift-causes.py --help`
Expected: argparse help output, exit 0.

- [ ] **Step 6.4: Commit**

```bash
git add scripts/analyze-drift-causes.py
git commit -m "feat(#143): scripts/analyze-drift-causes.py analyzer CLI"
```

---

## Task 7: Analyzer pytest tests with crafted fixtures

**Files:**
- Create: `tests/test_analyze_drift_causes.py`

- [ ] **Step 7.1: Write the failing tests**

Create `tests/test_analyze_drift_causes.py`:

```python
"""Tests for scripts/analyze-drift-causes.py."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


# Load the analyzer module from the script path (since it lives in scripts/
# and is not a package).
_SCRIPT_PATH = Path(__file__).parent.parent / "scripts" / "analyze-drift-causes.py"


@pytest.fixture(scope="module")
def analyzer():
    spec = importlib.util.spec_from_file_location("analyze_drift_causes", _SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _now_iso(offset_days: int = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=offset_days)).isoformat()


def write_fixture(path: Path, events: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")


def test_each_cause_appears_in_distribution(tmp_path, analyzer, capsys):
    fixture = tmp_path / "drift.jsonl"
    write_fixture(
        fixture,
        [
            {
                "type": "router_drift",
                "ts": _now_iso(0),
                "category": "skill_mediated",
                "bypass_cause": "skill_mediated_interactive",
                "bypass_signals": {
                    "subagent_type": "code-writer",
                    "dispatch_skill_called_recently": False,
                    "count_agent_since_dispatch": None,
                    "last_skill_call_name": "gh-create-issue",
                    "last_skill_call_is_interactive": True,
                    "turns_since_user_message": 0,
                },
            },
            {
                "type": "router_drift",
                "ts": _now_iso(1),
                "category": "bypass",
                "bypass_cause": "router_direct_no_dispatch",
                "bypass_signals": {
                    "subagent_type": "doc-writer",
                    "dispatch_skill_called_recently": False,
                    "count_agent_since_dispatch": None,
                    "last_skill_call_name": None,
                    "last_skill_call_is_interactive": False,
                    "turns_since_user_message": 0,
                },
            },
            {
                "type": "router_drift",
                "ts": _now_iso(2),
                "category": "bypass",
                "bypass_cause": "router_direct_after_consumed_dispatch",
                "bypass_signals": {
                    "subagent_type": "ops",
                    "dispatch_skill_called_recently": True,
                    "count_agent_since_dispatch": 1,
                    "last_skill_call_name": None,
                    "last_skill_call_is_interactive": False,
                    "turns_since_user_message": 0,
                },
            },
            {
                "type": "router_drift",
                "ts": _now_iso(3),
                "category": "stale_dispatch",
                "bypass_cause": "stale_dispatch",
                "bypass_signals": {
                    "subagent_type": "ops",
                    "dispatch_skill_called_recently": True,
                    "count_agent_since_dispatch": 0,
                    "last_skill_call_name": None,
                    "last_skill_call_is_interactive": False,
                    "turns_since_user_message": 0,
                },
            },
        ],
    )

    rc = analyzer.main(["--drift-path", str(fixture), "--days", "7"])
    assert rc == 0
    out = capsys.readouterr().out
    for cause in [
        "skill_mediated_interactive",
        "router_direct_no_dispatch",
        "router_direct_after_consumed_dispatch",
        "stale_dispatch",
    ]:
        assert cause in out, f"Missing cause in output: {cause}"


def test_malformed_events_skipped(tmp_path, analyzer, capsys):
    fixture = tmp_path / "drift.jsonl"
    fixture.write_text(
        "not json at all\n"
        + json.dumps(
            {
                "type": "router_drift",
                "ts": _now_iso(),
                "category": "bypass",
                "bypass_cause": "router_direct_no_dispatch",
                "bypass_signals": {"subagent_type": "x"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    rc = analyzer.main(["--drift-path", str(fixture)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "router_direct_no_dispatch" in out
    # Malformed line ignored — total enriched is 1
    assert "1 enriched events" in out


def test_pre_enrichment_baseline_counted_separately(tmp_path, analyzer, capsys):
    fixture = tmp_path / "drift.jsonl"
    write_fixture(
        fixture,
        [
            # Pre-enrichment event (no bypass_cause/bypass_signals)
            {
                "type": "router_drift",
                "ts": _now_iso(),
                "category": "bypass",
            },
            # Enriched event
            {
                "type": "router_drift",
                "ts": _now_iso(),
                "category": "bypass",
                "bypass_cause": "router_direct_no_dispatch",
                "bypass_signals": {"subagent_type": "x"},
            },
        ],
    )
    rc = analyzer.main(["--drift-path", str(fixture)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "1 enriched events; 1 pre-enrichment baseline" in out


def test_disagreements_flag_lists_mismatches(tmp_path, analyzer, capsys):
    fixture = tmp_path / "drift.jsonl"
    # Build an event where signals say router_direct_no_dispatch but stored
    # cause is something else.
    write_fixture(
        fixture,
        [
            {
                "type": "router_drift",
                "ts": _now_iso(),
                "category": "bypass",
                "bypass_cause": "skill_mediated_interactive",  # wrong on purpose
                "bypass_signals": {
                    "subagent_type": "x",
                    "dispatch_skill_called_recently": False,
                    "count_agent_since_dispatch": None,
                    "last_skill_call_name": None,
                    "last_skill_call_is_interactive": False,
                    "turns_since_user_message": 0,
                },
            }
        ],
    )
    rc = analyzer.main(["--drift-path", str(fixture), "--disagreements"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "1 events" in out  # disagreement count
    assert "stored=skill_mediated_interactive" in out
    assert "derived=router_direct_no_dispatch" in out


def test_window_filtering_excludes_old_events(tmp_path, analyzer, capsys):
    fixture = tmp_path / "drift.jsonl"
    write_fixture(
        fixture,
        [
            {
                "type": "router_drift",
                "ts": _now_iso(30),  # 30 days ago
                "category": "bypass",
                "bypass_cause": "router_direct_no_dispatch",
                "bypass_signals": {"subagent_type": "x"},
            },
            {
                "type": "router_drift",
                "ts": _now_iso(1),  # 1 day ago
                "category": "bypass",
                "bypass_cause": "router_direct_no_dispatch",
                "bypass_signals": {"subagent_type": "y"},
            },
        ],
    )
    rc = analyzer.main(["--drift-path", str(fixture), "--days", "7"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "1 enriched events" in out  # only the 1-day-old event


def test_json_output(tmp_path, analyzer, capsys):
    fixture = tmp_path / "drift.jsonl"
    write_fixture(
        fixture,
        [
            {
                "type": "router_drift",
                "ts": _now_iso(),
                "category": "bypass",
                "bypass_cause": "router_direct_no_dispatch",
                "bypass_signals": {"subagent_type": "x"},
            }
        ],
    )
    rc = analyzer.main(["--drift-path", str(fixture), "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["enriched_events"] == 1
    assert payload["distribution"][0]["cause"] == "router_direct_no_dispatch"
    assert payload["distribution"][0]["disposition"] == "unwanted"
```

- [ ] **Step 7.2: Run and confirm pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_analyze_drift_causes.py -v`
Expected: 6 PASS, 0 FAIL.

- [ ] **Step 7.3: Commit**

```bash
git add tests/test_analyze_drift_causes.py
git commit -m "test(#143): analyzer pytest coverage"
```

---

## Task 8: Add the "Bypass causes" section to `_health.py`

**Files:**
- Modify: `src/claude_wayfinder/_health.py`

- [ ] **Step 8.1: Add thresholds and constants**

In `src/claude_wayfinder/_health.py`, locate the threshold block at lines 36-39. Add the new constants immediately after `_ADVISORY_OVERRIDE_RATE_MAX`:

```python
# Bypass-cause taxonomy thresholds (v2-draft4 spec, both bootstrap;
# recalibrated at F-2 review, issue #160).
_UNWANTED_BYPASS_SHARE_MAX = 0.50  # bootstrap; tighten after baseline
_UNKNOWN_SHARE_WARN = 0.10  # bootstrap; tighten or extend enum at F-2
_BYPASS_CAUSE_MIN_SAMPLE = 100  # low-N guard: section renders N/A below this

# Cause → disposition mapping (mirrors scripts/analyze-drift-causes.py)
_BYPASS_CAUSE_DISPOSITION: dict[str, str] = {
    "skill_mediated_interactive": "expected",
    "skill_mediated_other": "review",
    "router_direct_after_consumed_dispatch": "unwanted",
    "router_direct_no_dispatch": "unwanted",
    "stale_dispatch": "review",
    "unknown": "review",
}
```

- [ ] **Step 8.2: Write the section-building helper function**

In `_health.py`, immediately before `def format_report_output(` (near line 969), add:

```python
def _build_bypass_causes_section(drift_events: list[dict[str, Any]]) -> list[str]:
    """Build the 'Bypass causes (7-day window)' markdown section.

    Reads enriched drift events (with bypass_signals + bypass_cause fields),
    counts by cause within a 7-day window, and returns markdown lines. When
    the enriched-event count is below _BYPASS_CAUSE_MIN_SAMPLE, returns a
    low-N notice instead of distribution + thresholds.

    Args:
        drift_events: Pre-loaded drift events. Mix of pre- and post-
            enrichment is fine; pre-enrichment events are skipped from
            cause counts but reported as a baseline.

    Returns:
        List of markdown lines (no trailing newline per line).
    """
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    since = now - timedelta(days=7)

    def _in_window(ev: dict[str, Any]) -> bool:
        ts = ev.get("ts")
        if not isinstance(ts, str):
            return False
        try:
            t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return False
        return t >= since

    drift_in_window = [
        e for e in drift_events if e.get("type") == "router_drift" and _in_window(e)
    ]
    enriched = [e for e in drift_in_window if isinstance(e.get("bypass_cause"), str)]
    pre_enrichment = len(drift_in_window) - len(enriched)

    lines: list[str] = []
    lines.append(f"## Bypass causes (7-day window, {len(enriched)} enriched events)")
    lines.append("")

    if len(enriched) < _BYPASS_CAUSE_MIN_SAMPLE:
        lines.append(
            f"N/A — insufficient post-enrichment data (have {len(enriched)},"
            f" need {_BYPASS_CAUSE_MIN_SAMPLE}). Pre-enrichment baseline:"
            f" {pre_enrichment} events."
        )
        lines.append("")
        return lines

    # Count by cause
    counts: dict[str, int] = {}
    for e in enriched:
        cause = e.get("bypass_cause", "unknown")
        if not isinstance(cause, str):
            cause = "unknown"
        counts[cause] = counts.get(cause, 0) + 1

    total = sum(counts.values())
    lines.append("| Cause                                   |  Count |  Share | Disposition |")
    lines.append("| --------------------------------------- | -----: | -----: | ----------- |")
    for cause, cnt in sorted(counts.items(), key=lambda kv: -kv[1]):
        share = cnt / total
        disp = _BYPASS_CAUSE_DISPOSITION.get(cause, "review")
        lines.append(f"| {cause:<39} | {cnt:>6} | {share*100:>5.1f}% | {disp:<11} |")
    lines.append("")

    # Threshold evaluation
    unwanted = sum(
        c for cause, c in counts.items()
        if _BYPASS_CAUSE_DISPOSITION.get(cause) == "unwanted"
    )
    unwanted_share = unwanted / total
    unknown_share = counts.get("unknown", 0) / total

    unwanted_status = "PASS" if unwanted_share <= _UNWANTED_BYPASS_SHARE_MAX else "WARN"
    unknown_status = "PASS" if unknown_share <= _UNKNOWN_SHARE_WARN else "WARN"

    lines.append(
        f"{unwanted_status} — unwanted-bypass share {unwanted_share*100:.1f}%"
        f" (threshold: ≤{_UNWANTED_BYPASS_SHARE_MAX*100:.0f}% bootstrap)"
    )
    lines.append(
        f"{unknown_status} — unknown share {unknown_share*100:.1f}%"
        f" (threshold: ≤{_UNKNOWN_SHARE_WARN*100:.0f}% bootstrap)"
    )
    if pre_enrichment > 0:
        lines.append(f"Pre-enrichment baseline (not counted): {pre_enrichment} events")
    lines.append("")

    return lines
```

- [ ] **Step 8.3: Call the section builder from `format_report_output`**

In `format_report_output()`, between the "Runtime Telemetry" section (ends ~line 1065) and the "Informational Metrics" section (starts ~line 1068), add a new section. The simplest hook: add a parameter and emit the lines.

First, change the signature:

```python
def format_report_output(
    invariants: dict[str, MetricResult],
    runtime_metrics: dict[str, MetricResult],
    dispatch_log: list[dict[str, Any]] | None = None,
    catalog_entries: list[dict[str, Any]] | None = None,
    drift_events: list[dict[str, Any]] | None = None,
) -> str:
```

Then, immediately after the "Runtime Telemetry" section's closing blank-line append (the `lines.append("")` that follows the `> All runtime telemetry metrics are within healthy ranges.` block), insert:

```python
    # --- Section 2b: Bypass causes (v2 telemetry enrichment) ---
    if drift_events is not None:
        lines.extend(_build_bypass_causes_section(drift_events))
```

- [ ] **Step 8.4: Update the call site that builds the report**

Locate where `format_report_output` is called in `_health.py` (look for a function like `build_report` or similar — search the file). Pass the loaded drift events as the new `drift_events` argument. If the caller doesn't already load drift events, add the load just before the call:

```python
drift_path = ... # use existing drift-path resolver if present, else load_jsonl(Path.home() / ".claude" / "state" / "router-drift.jsonl")
drift_events = load_jsonl(drift_path)
report_md = format_report_output(
    invariants,
    runtime_metrics,
    dispatch_log=dispatch_log,
    catalog_entries=catalog_entries,
    drift_events=drift_events,
)
```

(If the existing code already loads `drift_events` for `compute_metrics`, reuse that variable — search for "drift" in `_health.py` to find existing load.)

- [ ] **Step 8.5: Commit**

```bash
git add src/claude_wayfinder/_health.py
git commit -m "feat(#143): router-health bypass-causes section"
```

---

## Task 9: Tests for the `_health.py` section

**Files:**
- Modify: `tests/test_health.py`

- [ ] **Step 9.1: Write the failing tests**

Append to `tests/test_health.py` (preserve existing test imports):

```python
from datetime import datetime, timedelta, timezone

from claude_wayfinder._health import (
    _build_bypass_causes_section,
    _UNWANTED_BYPASS_SHARE_MAX,
    _UNKNOWN_SHARE_WARN,
    _BYPASS_CAUSE_MIN_SAMPLE,
)


def _now_iso(offset_days: int = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=offset_days)).isoformat()


def _enriched(cause: str, days_ago: int = 1) -> dict:
    return {
        "type": "router_drift",
        "ts": _now_iso(days_ago),
        "category": "bypass" if cause.startswith("router_direct") else (
            "skill_mediated" if cause.startswith("skill_mediated") else cause
        ),
        "bypass_cause": cause,
        "bypass_signals": {"subagent_type": "x"},
    }


def test_bypass_causes_section_low_n_renders_na():
    # Only 5 enriched events — below the 100 sample threshold.
    events = [_enriched("router_direct_no_dispatch") for _ in range(5)]
    out = "\n".join(_build_bypass_causes_section(events))
    assert "N/A — insufficient post-enrichment data" in out
    assert "have 5, need" in out


def test_bypass_causes_section_high_n_renders_table_and_thresholds():
    # 120 enriched events; mix of expected and unwanted.
    events = [_enriched("skill_mediated_interactive") for _ in range(80)]
    events.extend(_enriched("router_direct_no_dispatch") for _ in range(40))
    out = "\n".join(_build_bypass_causes_section(events))
    assert "## Bypass causes (7-day window, 120 enriched events)" in out
    assert "skill_mediated_interactive" in out
    assert "router_direct_no_dispatch" in out
    assert "expected" in out
    assert "unwanted" in out
    # 40/120 = 33.3% unwanted, ≤ 50% bootstrap → PASS
    assert "PASS — unwanted-bypass share 33.3%" in out


def test_bypass_causes_section_warns_when_over_threshold():
    # 70/120 = 58.3% unwanted, > 50% → WARN
    events = [_enriched("skill_mediated_interactive") for _ in range(50)]
    events.extend(_enriched("router_direct_no_dispatch") for _ in range(70))
    out = "\n".join(_build_bypass_causes_section(events))
    assert "WARN — unwanted-bypass share 58.3%" in out


def test_bypass_causes_section_unknown_share_threshold():
    # 100 events, 15 unknown = 15% > 10% → WARN on unknown
    events = [_enriched("skill_mediated_interactive") for _ in range(85)]
    events.extend(_enriched("unknown") for _ in range(15))
    # Total enriched: 100 — exactly meets _BYPASS_CAUSE_MIN_SAMPLE
    out = "\n".join(_build_bypass_causes_section(events))
    assert "WARN — unknown share 15.0%" in out


def test_bypass_causes_section_pre_enrichment_baseline_reported():
    enriched = [_enriched("skill_mediated_interactive") for _ in range(100)]
    pre = [
        {"type": "router_drift", "ts": _now_iso(2), "category": "bypass"}
        for _ in range(15)
    ]
    out = "\n".join(_build_bypass_causes_section(enriched + pre))
    assert "Pre-enrichment baseline (not counted): 15 events" in out


def test_bypass_causes_section_excludes_out_of_window_events():
    in_window = [_enriched("skill_mediated_interactive", days_ago=1) for _ in range(100)]
    out_of_window = [
        _enriched("router_direct_no_dispatch", days_ago=30) for _ in range(50)
    ]
    out = "\n".join(_build_bypass_causes_section(in_window + out_of_window))
    # Only 100 enriched in the 7-day window
    assert "## Bypass causes (7-day window, 100 enriched events)" in out
    # router_direct_no_dispatch never appears in the distribution table
    assert "router_direct_no_dispatch" not in out


def test_bypass_causes_constants_match_spec():
    assert _UNWANTED_BYPASS_SHARE_MAX == 0.50
    assert _UNKNOWN_SHARE_WARN == 0.10
    assert _BYPASS_CAUSE_MIN_SAMPLE == 100
```

- [ ] **Step 9.2: Run and confirm pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_health.py -v -k bypass_causes`
Expected: 7 new tests PASS.

- [ ] **Step 9.3: Run the full test suite to verify nothing else broke**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: all tests pass, including the pre-existing `_health.py` tests.

- [ ] **Step 9.4: Commit**

```bash
git add tests/test_health.py
git commit -m "test(#143): coverage for bypass-causes router-health section"
```

---

## Task 10: End-to-end smoke test and CI verification

**Files:** None — manual verification.

- [ ] **Step 10.1: Run the full JS test suite**

Run: `node --test hooks/tests/`
Expected: every test file PASSES (existing + new bypass-taxonomy tests + new failure-mode tests).

- [ ] **Step 10.2: Run the full Python test suite**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: every test PASSES.

- [ ] **Step 10.3: Run ruff/format check if CI enforces it**

Run: `./.venv/Scripts/python.exe -m ruff check src/ tests/ scripts/`
Expected: no errors (or fix any introduced — likely just line length on long disposition lines).

If ruff is not used, check `pyproject.toml` for the configured linter and run that instead. If unsure, look at `.github/workflows/*.yml` for the exact CI command set.

- [ ] **Step 10.4: Manual smoke against the live drift log**

Run: `./.venv/Scripts/python.exe scripts/analyze-drift-causes.py --days 30 --drift-path ~/.claude/state/router-drift.jsonl`

Expected: the report renders. Pre-enrichment count will be very high (most of the historical log); enriched count will be 0 or near-zero until the new hook has been firing. This confirms the analyzer handles a pre-enrichment-dominated log gracefully.

- [ ] **Step 10.5: Commit any lint fixes if needed**

```bash
git add -u
git commit -m "chore(#143): lint cleanup"
```

(If no changes from Step 10.3, skip this commit.)

---

## Task 11: Open the PR with the AC #9 merge-gate signal

**Files:** None — PR creation.

- [ ] **Step 11.1: Verify the spec frontmatter's `followups_filed:` matches reality**

Confirm the spec frontmatter still reads:

```yaml
followups_filed:
  F-1: 159
  F-2: 160
  F-3: 161
```

If any of #159/#160/#161 was closed during implementation or replaced by a different issue, update the spec frontmatter and amend the spec-revision commit.

- [ ] **Step 11.2: Push the branch and open the PR**

```bash
git push -u origin spec/143-telemetry-bypass-taxonomy-v2
gh pr create --base main --title "feat(#143): telemetry enrichment v2 — bypass-cause taxonomy" --body-file - <<'EOF'
## Summary

Implements the spec at `docs/superpowers/specs/2026-05-19-telemetry-bypass-taxonomy-design.md` (v2-draft4). Three adversarial review passes; pattern converged.

Enriches `~/.claude/state/router-drift.jsonl` events (`bypass`, `skill_mediated`, `stale_dispatch`) with `bypass_signals` + `bypass_cause`. Adds an analyzer CLI and a `router-health` section.

## What this ships

- `hooks/lib/bypass-taxonomy.js` — pure classify() function + INTERACTIVE_SKILLS
- `hooks/tests/bypass-taxonomy.test.js` — unit tests (one per cause + signal-derivation)
- `hooks/check-agent-dispatch-pairing.js` — wired to call classify() before emit, fail-open on any error
- `hooks/tests/check-agent-dispatch-pairing.test.js` — 3 failure-mode tests + 1 positive integration
- `scripts/analyze-drift-causes.py` — Python CLI analyzer
- `tests/test_analyze_drift_causes.py` — pytest coverage
- `src/claude_wayfinder/_health.py` — new "Bypass causes" section with two thresholds + low-N guard
- `tests/test_health.py` — coverage for the new section

## AC #9 merge gate (followups_filed:)

- F-1 → #159 (2-week baseline review)
- F-2 → #160 (recalibrate thresholds)
- F-3 → #161 (quarterly INTERACTIVE_SKILLS audit)

## Out of plugin

Spec lists `skills/router-health/SKILL.md` under `touches:`. That file lives in user-scope (`~/.claude/skills/router-health/SKILL.md`), not in this repo. The trigger-phrase update is a manual user-side step after this PR merges — tracked in this PR description as a TODO for the merger.

## Test plan

- [x] `node --test hooks/tests/` — all green
- [x] `./.venv/Scripts/python.exe -m pytest -q` — all green
- [x] Lint clean
- [x] Manual smoke: `python scripts/analyze-drift-causes.py --days 30` against live drift log renders without errors

Closes #143

🤖 _Generated by Claude Code on behalf of @cbeaulieu-gt_
EOF
```

- [ ] **Step 11.3: Watch CI**

Run: `scripts/wait-for-pr-checks.sh <PR-number>`

Expected: exit 0 (all checks SUCCESS). If failures, address per `gh-pr-review-address` skill flow.

---

## Manual step (post-merge, user-scope only)

**This is not part of the implementing PR.** After the PR merges:

- [ ] **Update `~/.claude/skills/router-health/SKILL.md`** — add "bypass causes" to the trigger phrases. This file is in the user's `claude-configs` tree, not in `claude-wayfinder`. Open a separate small PR in `claude-configs` for this one-line addition.

The router-health skill will still activate on the existing trigger phrases (`/router-health`, `router health`, etc.) without this change — the new section appears in the report regardless. The trigger-phrase addition just lets natural language like "show me bypass causes" route to it directly.

---

## Self-review notes

- **Spec coverage**: every section of the spec maps to a task — Module API → Task 1+2+3; Hook integration → Task 4; Three failure-mode tests → Task 5; Analyzer → Task 6+7; `_health.py` section → Task 8+9; AC #9 merge gate → Task 11; manual SKILL.md → Manual step.
- **No placeholders**: all code blocks contain full implementation. Module-load test in Task 5 uses a temp-directory approach instead of an unwritable module path (more portable).
- **Type/name consistency**: `classify`, `INTERACTIVE_SKILLS`, `_deriveCauseForTest`, signal field names — all match the spec and are consistent across tasks 1-9. `_BYPASS_CAUSE_DISPOSITION` mirrors the Python `DISPOSITION` constant in the analyzer.
- **Boundary**: the user-scope `SKILL.md` edit is called out as manual; not in any implementing-PR commit.
