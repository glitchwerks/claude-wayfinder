/**
 * Tests for hooks/lib/skill-name.js — the bareSkillName() utility.
 *
 * Written before implementation (TDD — red phase).
 */

const { test } = require("node:test");
const assert = require("node:assert/strict");

const { bareSkillName } = require("../lib/skill-name");

// ── Module shape ──────────────────────────────────────────────────────────────

test("skill-name: bareSkillName is exported as a function", () => {
  assert.equal(typeof bareSkillName, "function");
});

// ── Core behavior ─────────────────────────────────────────────────────────────

test("skill-name: bare name returns unchanged", () => {
  assert.equal(bareSkillName("whats-next"), "whats-next");
});

test("skill-name: namespaced plugin form strips namespace prefix", () => {
  assert.equal(bareSkillName("claude-wayfinder:dispatch"), "dispatch");
});

test("skill-name: another namespaced plugin form strips namespace prefix", () => {
  assert.equal(bareSkillName("claude-github-tools:gh-create-issue"), "gh-create-issue");
});

test("skill-name: all INTERACTIVE_SKILLS names strip correctly from namespaced forms", () => {
  const cases = [
    ["claude-github-tools:gh-create-issue", "gh-create-issue"],
    ["claude-github-tools:gh-pr-review-address", "gh-pr-review-address"],
    ["claude-github-tools:gh-refresh-issues", "gh-refresh-issues"],
    ["claude-prospector:claude-audit", "claude-audit"],
    ["some-plugin:project-review", "project-review"],
  ];
  for (const [input, expected] of cases) {
    assert.equal(
      bareSkillName(input),
      expected,
      `bareSkillName("${input}") should equal "${expected}"`
    );
  }
});

test("skill-name: dispatch case — strips to bare 'dispatch'", () => {
  assert.equal(bareSkillName("claude-wayfinder:dispatch"), "dispatch");
});

test("skill-name: already-bare name with no colon returns unchanged", () => {
  assert.equal(bareSkillName("python"), "python");
  assert.equal(bareSkillName("refactoring-discipline"), "refactoring-discipline");
  assert.equal(bareSkillName("init"), "init");
});

test("skill-name: multiple colons — takes everything after last colon", () => {
  // Hypothetical edge: "ns:sub:name" → "name" (split(":").pop() behavior)
  assert.equal(bareSkillName("a:b:c"), "c");
});

test("skill-name: empty string returns empty string", () => {
  assert.equal(bareSkillName(""), "");
});

// ── Non-string / edge inputs ───────────────────────────────────────────────────

test("skill-name: null returns empty string (defensive)", () => {
  assert.equal(bareSkillName(null), "");
});

test("skill-name: undefined returns empty string (defensive)", () => {
  assert.equal(bareSkillName(undefined), "");
});

test("skill-name: number returns empty string (defensive)", () => {
  assert.equal(bareSkillName(42), "");
});

test("skill-name: object returns empty string (defensive)", () => {
  assert.equal(bareSkillName({ skill: "dispatch" }), "");
});

test("skill-name: array returns empty string (defensive)", () => {
  assert.equal(bareSkillName(["dispatch"]), "");
});
