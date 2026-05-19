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
