/**
 * Tests for hooks/lib/bypass-taxonomy.js — the classify() helper that enriches
 * drift events emitted by check-agent-dispatch-pairing.js.
 *
 * See docs/superpowers/specs/2026-05-19-telemetry-bypass-taxonomy-design.md
 */

const { test } = require("node:test");
const assert = require("node:assert/strict");

const { classify, DISPATCH_SKILL_NAME, INTERACTIVE_SKILLS } = require("../lib/bypass-taxonomy");

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

// ── Regression: DISPATCH_SKILL_NAME is the plugin-namespaced form (issue #322) ──

test("DISPATCH_SKILL_NAME is the plugin-namespaced form 'claude-wayfinder:dispatch'", () => {
  // Before fix #322 this constant did not exist; the bare string "dispatch" was
  // compared directly, which never matched real Skill invocations.
  assert.equal(typeof DISPATCH_SKILL_NAME, "string");
  assert.equal(
    DISPATCH_SKILL_NAME,
    "claude-wayfinder:dispatch",
    "DISPATCH_SKILL_NAME must be the fully-qualified plugin-namespaced form"
  );
  assert.notEqual(
    DISPATCH_SKILL_NAME,
    "dispatch",
    "DISPATCH_SKILL_NAME must NOT be the bare unqualified form"
  );
});

test("signals: namespaced dispatch 'claude-wayfinder:dispatch' is recognized (regression #322)", () => {
  // This test would have FAILED before the fix: the bare-name comparison
  // "dispatch" never matched "claude-wayfinder:dispatch", so
  // dispatch_skill_called_recently was always false.
  const { signals } = classify(
    "stale_dispatch",
    { subagent_type: "code-writer" },
    [{ toolName: "Skill", skillName: "claude-wayfinder:dispatch" }, { toolName: "Read" }]
  );
  assert.equal(
    signals.dispatch_skill_called_recently,
    true,
    "dispatch_skill_called_recently must be true when 'claude-wayfinder:dispatch' is in history"
  );
  assert.equal(signals.count_agent_since_dispatch, 0);
});

test("signals: bare 'dispatch' skillName IS recognized as the dispatch skill via normalization", () => {
  // After refactor: bareSkillName() normalization is used for comparisons, so
  // both the fully-qualified "claude-wayfinder:dispatch" and the bare "dispatch"
  // form map to the same "dispatch" sentinel. This is acceptable because the
  // dispatch skill is the only skill with this name in the controlled set.
  // (Prior to the normalization refactor, only the fully-qualified form matched.)
  const { signals } = classify(
    "stale_dispatch",
    { subagent_type: "code-writer" },
    [{ toolName: "Skill", skillName: "dispatch" }, { toolName: "Read" }]
  );
  assert.equal(
    signals.dispatch_skill_called_recently,
    true,
    "Bare 'dispatch' is recognized as the dispatch skill after normalization"
  );
});

// ── Task 2: deriveCause decision tree ────────────────────────────────────────

const { _deriveCauseForTest } = require("../lib/bypass-taxonomy");

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
  const cause = _deriveCauseForTest(
    "bypass",
    sig({ dispatch_skill_called_recently: true, count_agent_since_dispatch: 0 })
  );
  assert.equal(cause, "unknown");
});

test("null count_agent_since_dispatch is never compared with >=", () => {
  const cause = _deriveCauseForTest(
    "bypass",
    sig({ dispatch_skill_called_recently: false, count_agent_since_dispatch: null })
  );
  assert.equal(cause, "router_direct_no_dispatch");
});

// ── Task 3: signal extraction ─────────────────────────────────────────────────

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
    [ev("Skill", "claude-wayfinder:dispatch"), ev("Read"), ev("Edit")]
  );
  assert.equal(signals.dispatch_skill_called_recently, true);
  assert.equal(signals.count_agent_since_dispatch, 0);
});

test("signals: dispatch in history, one Agent after → recently=true, count=1", () => {
  const { signals } = classify(
    "bypass",
    { subagent_type: "code-writer" },
    [ev("Skill", "claude-wayfinder:dispatch"), ev("Agent"), ev("Read")]
  );
  assert.equal(signals.dispatch_skill_called_recently, true);
  assert.equal(signals.count_agent_since_dispatch, 1);
});

test("signals: dispatch + two Agents after → count=2", () => {
  const { signals } = classify(
    "bypass",
    { subagent_type: "ops" },
    [ev("Skill", "claude-wayfinder:dispatch"), ev("Agent"), ev("Edit"), ev("Agent")]
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

test("signals: dispatch Skill is excluded from last_skill_call_name (not treated as non-dispatch skill)", () => {
  // The dispatch skill should not appear as last_skill_call_name — it is
  // specifically excluded from the non-dispatch Skill walk.
  const { signals } = classify(
    "stale_dispatch",
    { subagent_type: "code-writer" },
    [ev("Skill", "claude-wayfinder:dispatch"), ev("Read")]
  );
  assert.equal(
    signals.last_skill_call_name,
    null,
    "dispatch Skill must be excluded from last_skill_call_name"
  );
});

// ── Regression: INTERACTIVE_SKILLS lookup must work with namespaced skill names ──
// The INTERACTIVE_SKILLS set stores bare names but lastSkillCallName arrives
// namespaced for plugin skills, causing the lookup to be always false.
// Fix: normalize with bareSkillName() before the Set.has() call.

test("signals: namespaced interactive skill yields last_skill_call_is_interactive=true (regression #322 sibling)", () => {
  // Before the fix, INTERACTIVE_SKILLS.has("claude-github-tools:gh-create-issue")
  // was always false because the set stores bare names. This test would FAIL on
  // the unfixed code and PASS after normalization is applied.
  const { signals } = classify(
    "skill_mediated",
    { subagent_type: "code-writer" },
    [ev("Skill", "claude-github-tools:gh-create-issue"), ev("Read")]
  );
  assert.equal(
    signals.last_skill_call_is_interactive,
    true,
    "Namespaced 'claude-github-tools:gh-create-issue' must resolve to interactive=true"
  );
  // last_skill_call_name must retain the RAW (namespaced) value for telemetry accuracy.
  assert.equal(
    signals.last_skill_call_name,
    "claude-github-tools:gh-create-issue",
    "last_skill_call_name must retain the namespaced form for telemetry"
  );
});

test("classify: namespaced interactive skill yields skill_mediated_interactive cause (regression #322 sibling)", () => {
  // End-to-end: a fully-namespaced interactive skill should produce the
  // skill_mediated_interactive cause, not skill_mediated_other.
  const { cause, signals } = classify(
    "skill_mediated",
    { subagent_type: "code-writer" },
    [ev("Skill", "claude-github-tools:gh-create-issue"), ev("Read")]
  );
  assert.equal(
    cause,
    "skill_mediated_interactive",
    "Namespaced interactive skill must produce skill_mediated_interactive cause"
  );
  // Telemetry accuracy: last_skill_call_name must remain namespaced.
  assert.equal(
    signals.last_skill_call_name,
    "claude-github-tools:gh-create-issue",
    "last_skill_call_name must retain the namespaced form for telemetry"
  );
});
