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
