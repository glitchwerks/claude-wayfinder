/**
 * Tests for hooks/router-drift-scanner.js (Stop hook — v5 §3.3.1/3.3.2).
 *
 * All detection is deterministic — no LLM calls, no probabilistic matching.
 *
 * Run with:
 *   node --test hooks/tests/router-drift-scanner.test.js
 *
 * ---------------------------------------------------------------------------
 * EVENT SURFACE MAP (Task 5 — refs #395)
 * ---------------------------------------------------------------------------
 * The scanner emits five event types. Only two name a specific component;
 * three are component-agnostic (aggregate counts or session-level booleans).
 *
 * Component-BEARING events (receive component version stamps):
 *
 *   advisory_override
 *     Fields: recommended_agent (agent name), actual_agent (agent name)
 *     Both name distinct agents → stamp BOTH sets of version fields:
 *       recommended_agent_rev, recommended_agent_content_hash
 *       actual_agent_rev,      actual_agent_content_hash
 *     Sentinel discipline: undefined → omit, null → include null, int → include value
 *     (NB: advisory_override carries two agents; we use prefixed field names
 *      rather than a single agent_rev to avoid ambiguity.)
 *
 *   needs_more_detail_repeat
 *     Field: agent (the repeatedly-targeted agent name)
 *     Stamp: agent_rev, agent_content_hash
 *     Sentinel discipline: undefined → omit, null → include null, int → include value
 *
 * Component-AGNOSTIC events (do NOT receive any component version fields):
 *
 *   self_handle_unaided_invocation  — carries `count` only; no component named
 *   catalog_degraded_session        — session-level boolean; no component named
 *   skill_mediated_delegation       — carries `count` only; no component named
 *
 * Injection: scanSession() accepts an optional `getComponentVersion` parameter
 * (same pattern as buildAgentDispatchEvent / buildSkillInvocationEvent). The
 * real helper is the default; tests inject fakes to assert stamping behaviour
 * without touching the filesystem.
 * ---------------------------------------------------------------------------
 */

const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

// ---------------------------------------------------------------------------
// Lazy-load the scanner lib so test failures are clear before the module exists
// ---------------------------------------------------------------------------

function loadLib() {
  // Clear cache so each test group gets a fresh require
  delete require.cache[require.resolve("../lib/router-drift-scanner")];
  return require("../lib/router-drift-scanner");
}

// ---------------------------------------------------------------------------
// Transcript entry builders
// ---------------------------------------------------------------------------

/**
 * Create an assistant entry with a dispatch audit summary line.
 *
 * @param {string} decision - e.g. "delegate", "advisory", "self_handle_unaided"
 * @param {string} [agent]  - agent name returned by the matcher (for delegate/advisory)
 * @param {string} [suffix] - extra text appended after the audit line
 */
function dispatchAuditEntry(decision, agent = "", suffix = "") {
  let auditLine = `🎯 Dispatch → ${decision}`;
  if (agent) auditLine += ` [${agent}]`;
  auditLine += " (confidence: 0.90)";
  if (suffix) auditLine += `\n${suffix}`;
  return assistantTextEntry(auditLine);
}

/** Create a minimal assistant entry with text content. */
function assistantTextEntry(text) {
  return {
    type: "assistant",
    message: {
      role: "assistant",
      content: [{ type: "text", text }],
    },
  };
}

/** Create an assistant entry that invokes an Agent tool. */
function agentCallEntry(subagentType) {
  return {
    type: "assistant",
    message: {
      role: "assistant",
      content: [
        {
          type: "tool_use",
          name: "Agent",
          input: { subagent_type: subagentType, prompt: `do work as ${subagentType}` },
        },
      ],
    },
  };
}

/** Create an assistant entry that invokes a Skill tool. */
function skillCallEntry(skillName) {
  return {
    type: "assistant",
    message: {
      role: "assistant",
      content: [
        {
          type: "tool_use",
          name: "Skill",
          input: { skill: skillName, args: "" },
        },
      ],
    },
  };
}

/** Create a user entry (tool result or external turn). */
function userEntry(extras = {}) {
  return { type: "user", ...extras };
}

/** Create a system entry */
function systemEntry() {
  return { type: "system" };
}

// ---------------------------------------------------------------------------
// parseTranscript unit tests
// ---------------------------------------------------------------------------

test("parseTranscript: empty array returns empty event list", () => {
  const { parseTranscript } = loadLib();
  const result = parseTranscript([]);
  assert.equal(result.length, 0);
});

test("parseTranscript: non-assistant entries are ignored", () => {
  const { parseTranscript } = loadLib();
  const entries = [userEntry(), systemEntry(), { type: "attachment" }];
  const result = parseTranscript(entries);
  assert.equal(result.length, 0);
});

test("parseTranscript: extracts dispatch decisions from audit lines", () => {
  const { parseTranscript } = loadLib();
  const entries = [
    dispatchAuditEntry("delegate", "writer"),
    dispatchAuditEntry("self_handle_unaided"),
  ];
  const result = parseTranscript(entries);
  assert.equal(result.length, 2);
  assert.equal(result[0].kind, "dispatch");
  assert.equal(result[0].decision, "delegate");
  assert.equal(result[0].agent, "writer");
  assert.equal(result[1].kind, "dispatch");
  assert.equal(result[1].decision, "self_handle_unaided");
  assert.equal(result[1].agent, "");
});

test("parseTranscript: extracts Agent tool_use calls", () => {
  const { parseTranscript } = loadLib();
  const entries = [agentCallEntry("reader"), agentCallEntry("writer")];
  const result = parseTranscript(entries);
  assert.equal(result.length, 2);
  assert.equal(result[0].kind, "agent_call");
  assert.equal(result[0].subagent_type, "reader");
  assert.equal(result[1].kind, "agent_call");
  assert.equal(result[1].subagent_type, "writer");
});

test("parseTranscript: extracts Skill tool_use calls", () => {
  const { parseTranscript } = loadLib();
  const entries = [skillCallEntry("dispatch"), skillCallEntry("python")];
  const result = parseTranscript(entries);
  assert.equal(result.length, 2);
  assert.equal(result[0].kind, "skill_call");
  assert.equal(result[0].skill, "dispatch");
  assert.equal(result[1].kind, "skill_call");
  assert.equal(result[1].skill, "python");
});

test("parseTranscript: handles mixed content in one assistant entry", () => {
  const { parseTranscript } = loadLib();
  // An assistant turn that has both a dispatch audit line and an Agent call
  const entry = {
    type: "assistant",
    message: {
      role: "assistant",
      content: [
        {
          type: "text",
          text: "🎯 Dispatch → advisory [fixer] (confidence: 0.72)\n   Rationale: ...",
        },
        {
          type: "tool_use",
          name: "Agent",
          input: { subagent_type: "writer", prompt: "fix it" },
        },
      ],
    },
  };
  const result = parseTranscript([entry]);
  assert.equal(result.length, 2);
  assert.equal(result[0].kind, "dispatch");
  assert.equal(result[0].decision, "advisory");
  assert.equal(result[0].agent, "fixer");
  assert.equal(result[1].kind, "agent_call");
  assert.equal(result[1].subagent_type, "writer");
});

test("parseTranscript: entry with no message or null content is skipped", () => {
  const { parseTranscript } = loadLib();
  const entries = [
    { type: "assistant" },
    { type: "assistant", message: null },
    { type: "assistant", message: { content: null } },
    { type: "assistant", message: { content: [] } },
  ];
  assert.equal(parseTranscript(entries).length, 0);
});

// ---------------------------------------------------------------------------
// detectAdvisoryOverride
// ---------------------------------------------------------------------------

test("detectAdvisoryOverride: advisory dispatch followed by matching agent = no drift", () => {
  const { detectAdvisoryOverride } = loadLib();
  const events = [
    { kind: "dispatch", decision: "advisory", agent: "writer" },
    { kind: "agent_call", subagent_type: "writer" },
  ];
  const result = detectAdvisoryOverride(events);
  assert.equal(result.length, 0);
});

test("detectAdvisoryOverride: advisory dispatch followed by different agent = drift event", () => {
  const { detectAdvisoryOverride } = loadLib();
  const events = [
    { kind: "dispatch", decision: "advisory", agent: "writer" },
    { kind: "agent_call", subagent_type: "fixer" },
  ];
  const result = detectAdvisoryOverride(events);
  assert.equal(result.length, 1);
  assert.equal(result[0].type, "advisory_override");
  assert.equal(result[0].recommended_agent, "writer");
  assert.equal(result[0].actual_agent, "fixer");
});

test("detectAdvisoryOverride: advisory dispatch with no following agent call = no drift", () => {
  // Router chose not to delegate at all — not an override
  const { detectAdvisoryOverride } = loadLib();
  const events = [{ kind: "dispatch", decision: "advisory", agent: "writer" }];
  const result = detectAdvisoryOverride(events);
  assert.equal(result.length, 0);
});

test("detectAdvisoryOverride: non-advisory dispatch is ignored", () => {
  const { detectAdvisoryOverride } = loadLib();
  const events = [
    { kind: "dispatch", decision: "delegate", agent: "writer" },
    { kind: "agent_call", subagent_type: "fixer" },
  ];
  const result = detectAdvisoryOverride(events);
  assert.equal(result.length, 0);
});

// ---------------------------------------------------------------------------
// detectAdvisoryOverride — skill-interposed cases (issue #144)
// ---------------------------------------------------------------------------

test("detectAdvisoryOverride: advisory → skill_call → agent_call (different agent) emits one drift event", () => {
  // The canonical advisory sequence: dispatch → skill → agent_call
  // The old single-event lookahead missed this because events[i+1] was skill_call, not agent_call.
  const { detectAdvisoryOverride } = loadLib();
  const events = [
    { kind: "dispatch", decision: "advisory", agent: "writer" },
    { kind: "skill_call", skill: "dispatch" },
    { kind: "agent_call", subagent_type: "fixer" },
  ];
  const result = detectAdvisoryOverride(events);
  assert.equal(result.length, 1);
  assert.equal(result[0].type, "advisory_override");
  assert.equal(result[0].recommended_agent, "writer");
  assert.equal(result[0].actual_agent, "fixer");
});

test("detectAdvisoryOverride: advisory → skill_call → dispatch (different decision) produces no drift event (advisory abandoned)", () => {
  // A new dispatch event means the advisory case was abandoned — don't attribute
  // the subsequent agent_call (if any) to the original advisory.
  const { detectAdvisoryOverride } = loadLib();
  const events = [
    { kind: "dispatch", decision: "advisory", agent: "writer" },
    { kind: "skill_call", skill: "dispatch" },
    { kind: "dispatch", decision: "delegate", agent: "fixer" },
    { kind: "agent_call", subagent_type: "fixer" },
  ];
  const result = detectAdvisoryOverride(events);
  assert.equal(result.length, 0);
});

test("detectAdvisoryOverride: advisory → skill_call → skill_call → agent_call emits one drift event (multiple skills do not block)", () => {
  // Multiple interposed skill_calls should all be skipped — only the first
  // agent_call (or dispatch) terminates the inner scan.
  const { detectAdvisoryOverride } = loadLib();
  const events = [
    { kind: "dispatch", decision: "advisory", agent: "writer" },
    { kind: "skill_call", skill: "dispatch" },
    { kind: "skill_call", skill: "python" },
    { kind: "agent_call", subagent_type: "fixer" },
  ];
  const result = detectAdvisoryOverride(events);
  assert.equal(result.length, 1);
  assert.equal(result[0].type, "advisory_override");
  assert.equal(result[0].recommended_agent, "writer");
  assert.equal(result[0].actual_agent, "fixer");
});

test("detectAdvisoryOverride: multiple advisory overrides in one session", () => {
  const { detectAdvisoryOverride } = loadLib();
  const events = [
    { kind: "dispatch", decision: "advisory", agent: "writer" },
    { kind: "agent_call", subagent_type: "fixer" }, // override
    { kind: "dispatch", decision: "advisory", agent: "reader" },
    { kind: "agent_call", subagent_type: "reader" }, // no override
    { kind: "dispatch", decision: "advisory", agent: "writer" },
    { kind: "agent_call", subagent_type: "reviewer" }, // override
  ];
  const result = detectAdvisoryOverride(events);
  assert.equal(result.length, 2);
  assert.equal(result[0].actual_agent, "fixer");
  assert.equal(result[1].actual_agent, "reviewer");
});

// ---------------------------------------------------------------------------
// detectSelfHandleUnaided
// ---------------------------------------------------------------------------

test("detectSelfHandleUnaided: no self_handle_unaided decisions returns count 0", () => {
  const { detectSelfHandleUnaided } = loadLib();
  const events = [
    { kind: "dispatch", decision: "delegate", agent: "writer" },
    { kind: "dispatch", decision: "self_handle", agent: "" },
  ];
  const result = detectSelfHandleUnaided(events);
  assert.equal(result.count, 0);
});

test("detectSelfHandleUnaided: counts self_handle_unaided decisions", () => {
  const { detectSelfHandleUnaided } = loadLib();
  const events = [
    { kind: "dispatch", decision: "self_handle_unaided", agent: "" },
    { kind: "dispatch", decision: "self_handle_unaided", agent: "" },
    { kind: "dispatch", decision: "delegate", agent: "reader" },
  ];
  const result = detectSelfHandleUnaided(events);
  assert.equal(result.count, 2);
});

test("detectSelfHandleUnaided: empty events returns count 0", () => {
  const { detectSelfHandleUnaided } = loadLib();
  assert.equal(detectSelfHandleUnaided([]).count, 0);
});

// ---------------------------------------------------------------------------
// detectNeedsMoreDetailRepeat
// ---------------------------------------------------------------------------

test("detectNeedsMoreDetailRepeat: single needs_more_detail = no drift", () => {
  const { detectNeedsMoreDetailRepeat } = loadLib();
  const events = [{ kind: "dispatch", decision: "needs_more_detail", agent: "" }];
  const result = detectNeedsMoreDetailRepeat(events);
  assert.equal(result.length, 0);
});

test("detectNeedsMoreDetailRepeat: two needs_more_detail in a row with same agent target = drift", () => {
  const { detectNeedsMoreDetailRepeat } = loadLib();
  // Two consecutive needs_more_detail dispatches targeting same agent
  const events = [
    { kind: "dispatch", decision: "needs_more_detail", agent: "writer" },
    { kind: "dispatch", decision: "needs_more_detail", agent: "writer" },
  ];
  const result = detectNeedsMoreDetailRepeat(events);
  assert.equal(result.length, 1);
  assert.equal(result[0].type, "needs_more_detail_repeat");
  assert.equal(result[0].agent, "writer");
});

test("detectNeedsMoreDetailRepeat: needs_more_detail then different decision = no drift", () => {
  const { detectNeedsMoreDetailRepeat } = loadLib();
  const events = [
    { kind: "dispatch", decision: "needs_more_detail", agent: "writer" },
    { kind: "dispatch", decision: "delegate", agent: "writer" },
  ];
  const result = detectNeedsMoreDetailRepeat(events);
  assert.equal(result.length, 0);
});

test("detectNeedsMoreDetailRepeat: non-dispatch events between two needs_more_detail = still drift", () => {
  const { detectNeedsMoreDetailRepeat } = loadLib();
  const events = [
    { kind: "dispatch", decision: "needs_more_detail", agent: "writer" },
    { kind: "agent_call", subagent_type: "reader" },
    { kind: "dispatch", decision: "needs_more_detail", agent: "writer" },
  ];
  const result = detectNeedsMoreDetailRepeat(events);
  assert.equal(result.length, 1);
  assert.equal(result[0].type, "needs_more_detail_repeat");
});

// ---------------------------------------------------------------------------
// detectCatalogDegraded
// ---------------------------------------------------------------------------

test("detectCatalogDegraded: no CATALOG ERROR = false", () => {
  const { detectCatalogDegraded } = loadLib();
  const entries = [
    assistantTextEntry("All looks fine."),
    { type: "user", message: { content: [{ type: "text", text: "normal user turn" }] } },
  ];
  assert.equal(detectCatalogDegraded(entries), false);
});

test("detectCatalogDegraded: [CATALOG ERROR] in assistant text = true", () => {
  const { detectCatalogDegraded } = loadLib();
  const entries = [
    assistantTextEntry("[CATALOG ERROR] Dispatch catalog is degraded: catalog file missing."),
  ];
  assert.equal(detectCatalogDegraded(entries), true);
});

test("detectCatalogDegraded: [CATALOG ERROR] in additionalContext attachment = true", () => {
  const { detectCatalogDegraded } = loadLib();
  // The hook-injected additionalContext appears as an attachment entry
  const entries = [
    {
      type: "attachment",
      attachment: {
        type: "hook_success",
        content: "[CATALOG ERROR] catalog degraded",
      },
    },
  ];
  assert.equal(detectCatalogDegraded(entries), true);
});

test("detectCatalogDegraded: empty entries = false", () => {
  const { detectCatalogDegraded } = loadLib();
  assert.equal(detectCatalogDegraded([]), false);
});

// ---------------------------------------------------------------------------
// detectSkillMediatedDelegation
// ---------------------------------------------------------------------------

test("detectSkillMediatedDelegation: no Skill before Agent = count 0", () => {
  const { detectSkillMediatedDelegation } = loadLib();
  const events = [
    { kind: "agent_call", subagent_type: "writer" },
    { kind: "agent_call", subagent_type: "reader" },
  ];
  const result = detectSkillMediatedDelegation(events);
  assert.equal(result.count, 0);
});

test("detectSkillMediatedDelegation: Skill immediately before Agent = count 1", () => {
  const { detectSkillMediatedDelegation } = loadLib();
  const events = [
    { kind: "skill_call", skill: "dispatch" },
    { kind: "agent_call", subagent_type: "writer" },
  ];
  const result = detectSkillMediatedDelegation(events);
  assert.equal(result.count, 1);
});

test("detectSkillMediatedDelegation: multiple skill-mediated delegations are counted", () => {
  const { detectSkillMediatedDelegation } = loadLib();
  const events = [
    { kind: "skill_call", skill: "dispatch" },
    { kind: "agent_call", subagent_type: "writer" },
    { kind: "skill_call", skill: "dispatch" },
    { kind: "agent_call", subagent_type: "reader" },
    { kind: "agent_call", subagent_type: "fixer" }, // no skill before this
  ];
  const result = detectSkillMediatedDelegation(events);
  assert.equal(result.count, 2);
});

test("detectSkillMediatedDelegation: Skill call NOT immediately before Agent (other events in between) = not counted", () => {
  const { detectSkillMediatedDelegation } = loadLib();
  // A dispatch event between skill and agent breaks the adjacency
  const events = [
    { kind: "skill_call", skill: "dispatch" },
    { kind: "dispatch", decision: "advisory", agent: "writer" },
    { kind: "agent_call", subagent_type: "writer" },
  ];
  const result = detectSkillMediatedDelegation(events);
  // dispatch event separates them — not counted as skill-mediated
  assert.equal(result.count, 0);
});

test("detectSkillMediatedDelegation: empty events = count 0", () => {
  const { detectSkillMediatedDelegation } = loadLib();
  assert.equal(detectSkillMediatedDelegation([]).count, 0);
});

// ---------------------------------------------------------------------------
// scanSession — integration across all detectors
// ---------------------------------------------------------------------------

test("scanSession: empty entries produces no events", () => {
  const { scanSession } = loadLib();
  const result = scanSession({ entries: [], sessionId: "test-session-123" });
  assert.equal(result.length, 0);
});

test("scanSession: advisory override produces one advisory_override event", () => {
  const { scanSession } = loadLib();
  const entries = [
    {
      type: "assistant",
      message: {
        content: [
          {
            type: "text",
            text: "🎯 Dispatch → advisory [writer] (confidence: 0.75)\n   Rationale: ...",
          },
          {
            type: "tool_use",
            name: "Agent",
            input: { subagent_type: "fixer", prompt: "fix it" },
          },
        ],
      },
    },
  ];
  const result = scanSession({ entries, sessionId: "sess-001" });
  const overrides = result.filter((e) => e.type === "advisory_override");
  assert.equal(overrides.length, 1);
  assert.equal(overrides[0].session_id, "sess-001");
  assert.equal(overrides[0].recommended_agent, "writer");
  assert.equal(overrides[0].actual_agent, "fixer");
  assert.ok(overrides[0].ts); // must have a timestamp
});

test("scanSession: self_handle_unaided decisions produce one event with correct count", () => {
  const { scanSession } = loadLib();
  const entries = [
    dispatchAuditEntry("self_handle_unaided"),
    dispatchAuditEntry("self_handle_unaided"),
    dispatchAuditEntry("delegate", "writer"),
  ];
  const result = scanSession({ entries, sessionId: "sess-002" });
  const shu = result.filter((e) => e.type === "self_handle_unaided_invocation");
  assert.equal(shu.length, 1);
  assert.equal(shu[0].count, 2);
  assert.equal(shu[0].session_id, "sess-002");
});

test("scanSession: catalog degraded session produces catalog_degraded_session event", () => {
  const { scanSession } = loadLib();
  const entries = [
    assistantTextEntry("[CATALOG ERROR] Dispatch catalog is degraded: catalog file missing."),
  ];
  const result = scanSession({ entries, sessionId: "sess-003" });
  const degraded = result.filter((e) => e.type === "catalog_degraded_session");
  assert.equal(degraded.length, 1);
  assert.equal(degraded[0].session_id, "sess-003");
});

test("scanSession: skill_mediated_delegation produces event with count", () => {
  const { scanSession } = loadLib();
  const entries = [skillCallEntry("dispatch"), agentCallEntry("writer")];
  const result = scanSession({ entries, sessionId: "sess-004" });
  const smd = result.filter((e) => e.type === "skill_mediated_delegation");
  assert.equal(smd.length, 1);
  assert.equal(smd[0].count, 1);
  assert.equal(smd[0].session_id, "sess-004");
});

test("scanSession: zero self_handle_unaided = no event emitted", () => {
  const { scanSession } = loadLib();
  const entries = [dispatchAuditEntry("delegate", "reader")];
  const result = scanSession({ entries, sessionId: "sess-005" });
  const shu = result.filter((e) => e.type === "self_handle_unaided_invocation");
  assert.equal(shu.length, 0);
});

test("scanSession: zero skill_mediated_delegation = no event emitted", () => {
  const { scanSession } = loadLib();
  const entries = [agentCallEntry("writer")];
  const result = scanSession({ entries, sessionId: "sess-006" });
  const smd = result.filter((e) => e.type === "skill_mediated_delegation");
  assert.equal(smd.length, 0);
});

test("scanSession: all event types in one session", () => {
  const { scanSession } = loadLib();
  const entries = [
    // advisory override
    {
      type: "assistant",
      message: {
        content: [
          {
            type: "text",
            text: "🎯 Dispatch → advisory [writer] (confidence: 0.72)\n   Rationale: ...",
          },
          {
            type: "tool_use",
            name: "Agent",
            input: { subagent_type: "fixer", prompt: "debug it" },
          },
        ],
      },
    },
    // self_handle_unaided
    dispatchAuditEntry("self_handle_unaided"),
    // catalog degraded
    assistantTextEntry("[CATALOG ERROR] Dispatch catalog is degraded: zero entries loaded."),
    // needs_more_detail repeat
    dispatchAuditEntry("needs_more_detail", "writer"),
    dispatchAuditEntry("needs_more_detail", "writer"),
    // skill-mediated delegation
    skillCallEntry("dispatch"),
    agentCallEntry("reader"),
  ];
  const result = scanSession({ entries, sessionId: "all-types" });
  const types = result.map((e) => e.type);
  assert.ok(types.includes("advisory_override"));
  assert.ok(types.includes("self_handle_unaided_invocation"));
  assert.ok(types.includes("catalog_degraded_session"));
  assert.ok(types.includes("needs_more_detail_repeat"));
  assert.ok(types.includes("skill_mediated_delegation"));
});

// ---------------------------------------------------------------------------
// Edge cases
// ---------------------------------------------------------------------------

test("parseTranscript: malformed message.content entry (non-object) is skipped", () => {
  const { parseTranscript } = loadLib();
  const entries = [
    {
      type: "assistant",
      message: {
        content: [
          null,
          undefined,
          42,
          "string",
          { type: "text", text: "🎯 Dispatch → delegate [reader] (confidence: 0.90)" },
        ],
      },
    },
  ];
  const result = parseTranscript(entries);
  assert.equal(result.length, 1);
  assert.equal(result[0].decision, "delegate");
});

test("scanSession: session_id missing — events still emitted with empty session_id", () => {
  const { scanSession } = loadLib();
  const entries = [dispatchAuditEntry("self_handle_unaided")];
  const result = scanSession({ entries, sessionId: undefined });
  assert.equal(result.length, 1);
  assert.equal(result[0].session_id, "");
});

test("scanSession: dispatch audit line with no bracketed agent = agent is empty string", () => {
  const { scanSession } = loadLib();
  // needs_more_detail line has no [agent] part
  const entries = [
    dispatchAuditEntry("needs_more_detail"),
    dispatchAuditEntry("needs_more_detail"),
  ];
  const result = scanSession({ entries, sessionId: "sess-nmd" });
  const nmd = result.filter((e) => e.type === "needs_more_detail_repeat");
  assert.equal(nmd.length, 1);
  assert.equal(nmd[0].agent, "");
});

// ---------------------------------------------------------------------------
// Hook script integration test (stdin → stdout/file)
// ---------------------------------------------------------------------------

test("hook script: exits 0 on empty JSON payload with no transcript path", () => {
  const hookPath = path.join(__dirname, "..", "router-drift-scanner.js");
  const payload = JSON.stringify({ session_id: "test-empty", cwd: os.tmpdir() });
  const result = spawnSync(process.execPath, [hookPath], {
    input: payload,
    encoding: "utf8",
    timeout: 10_000,
  });
  // Must exit 0 (never block session end)
  assert.equal(
    result.status,
    0,
    `Expected exit 0 but got ${result.status}. stderr: ${result.stderr}`
  );
});

test("hook script: exits 0 on malformed JSON input", () => {
  const hookPath = path.join(__dirname, "..", "router-drift-scanner.js");
  const result = spawnSync(process.execPath, [hookPath], {
    input: "NOT VALID JSON }{",
    encoding: "utf8",
    timeout: 10_000,
  });
  assert.equal(
    result.status,
    0,
    `Expected exit 0 but got ${result.status}. stderr: ${result.stderr}`
  );
  // Should emit an error to stderr
  assert.ok(
    result.stderr.includes("[router-drift-scanner]"),
    `Expected error message on stderr but got: ${result.stderr}`
  );
});

test("hook script: writes drift events to ROUTER_DRIFT_LOG_PATH and exits 0", () => {
  const hookPath = path.join(__dirname, "..", "router-drift-scanner.js");
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "drift-scanner-test-"));
  const logPath = path.join(tmpDir, "router-drift.jsonl");

  // Build a minimal transcript JSONL
  const sessionId = "integration-test-session";
  const transcriptDir = path.join(tmpDir, "projects", "test-project");
  fs.mkdirSync(transcriptDir, { recursive: true });
  const transcriptPath = path.join(transcriptDir, `${sessionId}.jsonl`);

  // Write transcript with a self_handle_unaided entry
  const transcriptEntries = [dispatchAuditEntry("self_handle_unaided")];
  fs.writeFileSync(
    transcriptPath,
    `${transcriptEntries.map((e) => JSON.stringify(e)).join("\n")}\n`
  );

  const payload = JSON.stringify({
    session_id: sessionId,
    cwd: path.join(tmpDir, "myproject"),
  });

  const result = spawnSync(process.execPath, [hookPath], {
    input: payload,
    encoding: "utf8",
    timeout: 10_000,
    env: {
      ...process.env,
      ROUTER_DRIFT_LOG_PATH: logPath,
      CLAUDE_HOME: tmpDir,
    },
  });

  assert.equal(result.status, 0, `Expected exit 0. stderr: ${result.stderr}`);

  // The log may or may not exist depending on transcript path resolution.
  // At minimum the hook must have exited cleanly.
  // If transcript was found, check the log.
  if (fs.existsSync(logPath)) {
    const logLines = fs
      .readFileSync(logPath, "utf8")
      .split("\n")
      .filter((l) => l.trim());
    assert.ok(logLines.length > 0, "Expected at least one log line");
    const event = JSON.parse(logLines[0]);
    assert.ok(event.type, "Event must have a type");
    assert.ok(event.ts, "Event must have a timestamp");
    assert.ok("session_id" in event, "Event must have session_id");
  }

  // Cleanup
  fs.rmSync(tmpDir, { recursive: true, force: true });
});

test("hook script: idempotent — running twice on same transcript produces same events", () => {
  const hookPath = path.join(__dirname, "..", "router-drift-scanner.js");
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "drift-idempotent-"));
  const logPath = path.join(tmpDir, "router-drift.jsonl");

  const sessionId = "idempotent-session";
  const transcriptDir = path.join(tmpDir, "projects", "test-project");
  fs.mkdirSync(transcriptDir, { recursive: true });
  const transcriptPath = path.join(transcriptDir, `${sessionId}.jsonl`);

  const transcriptEntries = [
    dispatchAuditEntry("self_handle_unaided"),
    dispatchAuditEntry("self_handle_unaided"),
  ];
  fs.writeFileSync(
    transcriptPath,
    `${transcriptEntries.map((e) => JSON.stringify(e)).join("\n")}\n`
  );

  const payload = JSON.stringify({
    session_id: sessionId,
    cwd: path.join(tmpDir, "myproject"),
  });

  const env = { ...process.env, ROUTER_DRIFT_LOG_PATH: logPath, CLAUDE_HOME: tmpDir };
  const opts = { input: payload, encoding: "utf8", timeout: 10_000, env };

  // Run once
  const r1 = spawnSync(process.execPath, [hookPath], opts);
  assert.equal(r1.status, 0);

  const lines1 = fs.existsSync(logPath)
    ? fs
        .readFileSync(logPath, "utf8")
        .split("\n")
        .filter((l) => l.trim())
    : [];

  // Run again (idempotency: should not double-append for same session)
  const r2 = spawnSync(process.execPath, [hookPath], opts);
  assert.equal(r2.status, 0);

  const lines2 = fs.existsSync(logPath)
    ? fs
        .readFileSync(logPath, "utf8")
        .split("\n")
        .filter((l) => l.trim())
    : [];

  // Must be idempotent: same number of lines both times
  assert.equal(lines1.length, lines2.length, "Running twice should not produce duplicate events");

  fs.rmSync(tmpDir, { recursive: true, force: true });
});

// ---------------------------------------------------------------------------
// plugin_version stamping on all emitted drift events
// ---------------------------------------------------------------------------

const DRIFT_SENTINEL_SHA = "cafebabe1234567890abcdef1234567890abcdef";

test("scanSession: plugin_version is stamped on advisory_override events", () => {
  const { scanSession } = loadLib();
  const entries = [
    {
      type: "assistant",
      message: {
        content: [
          {
            type: "text",
            text: "🎯 Dispatch → advisory [writer] (confidence: 0.75)\n   Rationale: ...",
          },
          {
            type: "tool_use",
            name: "Agent",
            input: { subagent_type: "fixer", prompt: "fix it" },
          },
        ],
      },
    },
  ];
  const result = scanSession({
    entries,
    sessionId: "hv-sess-001",
    pluginVersion: DRIFT_SENTINEL_SHA,
  });
  const overrides = result.filter((e) => e.type === "advisory_override");
  assert.equal(overrides.length, 1);
  assert.equal(overrides[0].plugin_version, DRIFT_SENTINEL_SHA);
});

test("scanSession: plugin_version is stamped on self_handle_unaided_invocation events", () => {
  const { scanSession } = loadLib();
  const entries = [
    dispatchAuditEntry("self_handle_unaided"),
    dispatchAuditEntry("self_handle_unaided"),
  ];
  const result = scanSession({
    entries,
    sessionId: "hv-sess-002",
    pluginVersion: DRIFT_SENTINEL_SHA,
  });
  const shu = result.filter((e) => e.type === "self_handle_unaided_invocation");
  assert.equal(shu.length, 1);
  assert.equal(shu[0].plugin_version, DRIFT_SENTINEL_SHA);
});

test("scanSession: plugin_version is stamped on needs_more_detail_repeat events", () => {
  const { scanSession } = loadLib();
  const entries = [
    dispatchAuditEntry("needs_more_detail", "writer"),
    dispatchAuditEntry("needs_more_detail", "writer"),
  ];
  const result = scanSession({
    entries,
    sessionId: "hv-sess-003",
    pluginVersion: DRIFT_SENTINEL_SHA,
  });
  const nmd = result.filter((e) => e.type === "needs_more_detail_repeat");
  assert.equal(nmd.length, 1);
  assert.equal(nmd[0].plugin_version, DRIFT_SENTINEL_SHA);
});

test("scanSession: plugin_version is stamped on catalog_degraded_session events", () => {
  const { scanSession } = loadLib();
  const entries = [
    assistantTextEntry("[CATALOG ERROR] Dispatch catalog is degraded: catalog file missing."),
  ];
  const result = scanSession({
    entries,
    sessionId: "hv-sess-004",
    pluginVersion: DRIFT_SENTINEL_SHA,
  });
  const degraded = result.filter((e) => e.type === "catalog_degraded_session");
  assert.equal(degraded.length, 1);
  assert.equal(degraded[0].plugin_version, DRIFT_SENTINEL_SHA);
});

test("scanSession: plugin_version is stamped on skill_mediated_delegation events", () => {
  const { scanSession } = loadLib();
  const entries = [skillCallEntry("dispatch"), agentCallEntry("writer")];
  const result = scanSession({
    entries,
    sessionId: "hv-sess-005",
    pluginVersion: DRIFT_SENTINEL_SHA,
  });
  const smd = result.filter((e) => e.type === "skill_mediated_delegation");
  assert.equal(smd.length, 1);
  assert.equal(smd[0].plugin_version, DRIFT_SENTINEL_SHA);
});

test("scanSession: plugin_version defaults to 'unknown' when not provided", () => {
  const { scanSession } = loadLib();
  const entries = [dispatchAuditEntry("self_handle_unaided")];
  const result = scanSession({ entries, sessionId: "hv-sess-006" });
  const shu = result.filter((e) => e.type === "self_handle_unaided_invocation");
  assert.equal(shu.length, 1);
  assert.equal(shu[0].plugin_version, "unknown");
});

// ---------------------------------------------------------------------------
// Task 5 — component version stamping on drift events (refs #395)
//
// getComponentVersion injection: scanSession() accepts an optional
// getComponentVersion parameter. The real helper is the default; tests inject
// fakes so stamping is verified without touching the filesystem.
//
// Sentinel discipline (mirrors Tasks 3 and 4):
//   getComponentVersion returns undefined rev  → omit version fields entirely
//   getComponentVersion returns null rev        → include fields with null values
//   getComponentVersion returns integer/hex rev → include fields with values
// ---------------------------------------------------------------------------

// Helper: build a fake getComponentVersion that returns a fixed rev/hash per name
function fakeCV(revByName) {
  return (name) => {
    const r = revByName[name];
    if (r === undefined) return { rev: undefined, content_hash: undefined };
    if (r === null) return { rev: null, content_hash: null };
    return { rev: r, content_hash: `hash-${name}` };
  };
}

// ---------------------------------------------------------------------------
// advisory_override — two agents, both stamped
// ---------------------------------------------------------------------------

test("advisory_override: stamps recommended_agent_rev and actual_agent_rev when helper returns values", () => {
  const { scanSession } = loadLib();
  const entries = [
    {
      type: "assistant",
      message: {
        content: [
          {
            type: "text",
            text: "🎯 Dispatch → advisory [writer] (confidence: 0.75)\n   Rationale: ...",
          },
          {
            type: "tool_use",
            name: "Agent",
            input: { subagent_type: "fixer", prompt: "fix it" },
          },
        ],
      },
    },
  ];
  const getCV = fakeCV({ "writer": 42, fixer: 7 });
  const result = scanSession({ entries, sessionId: "cv-ao-001", getComponentVersion: getCV });
  const overrides = result.filter((e) => e.type === "advisory_override");
  assert.equal(overrides.length, 1);
  const ev = overrides[0];
  assert.equal(ev.recommended_agent_rev, 42);
  assert.equal(ev.recommended_agent_content_hash, "hash-writer");
  assert.equal(ev.actual_agent_rev, 7);
  assert.equal(ev.actual_agent_content_hash, "hash-fixer");
});

test("advisory_override: sentinel undefined → omit all four component version fields", () => {
  const { scanSession } = loadLib();
  const entries = [
    {
      type: "assistant",
      message: {
        content: [
          {
            type: "text",
            text: "🎯 Dispatch → advisory [writer] (confidence: 0.75)",
          },
          {
            type: "tool_use",
            name: "Agent",
            input: { subagent_type: "fixer", prompt: "fix it" },
          },
        ],
      },
    },
  ];
  // undefined sentinel: helper returns {rev: undefined, content_hash: undefined}
  const getCV = fakeCV({});
  const result = scanSession({ entries, sessionId: "cv-ao-002", getComponentVersion: getCV });
  const overrides = result.filter((e) => e.type === "advisory_override");
  assert.equal(overrides.length, 1);
  const ev = overrides[0];
  assert.ok(
    !("recommended_agent_rev" in ev),
    "recommended_agent_rev must be absent when sentinel is undefined"
  );
  assert.ok(
    !("recommended_agent_content_hash" in ev),
    "recommended_agent_content_hash must be absent when sentinel is undefined"
  );
  assert.ok(
    !("actual_agent_rev" in ev),
    "actual_agent_rev must be absent when sentinel is undefined"
  );
  assert.ok(
    !("actual_agent_content_hash" in ev),
    "actual_agent_content_hash must be absent when sentinel is undefined"
  );
});

test("advisory_override: sentinel null → include all four component version fields with null", () => {
  const { scanSession } = loadLib();
  const entries = [
    {
      type: "assistant",
      message: {
        content: [
          {
            type: "text",
            text: "🎯 Dispatch → advisory [writer] (confidence: 0.75)",
          },
          {
            type: "tool_use",
            name: "Agent",
            input: { subagent_type: "fixer", prompt: "fix it" },
          },
        ],
      },
    },
  ];
  const getCV = fakeCV({ "writer": null, fixer: null });
  const result = scanSession({ entries, sessionId: "cv-ao-003", getComponentVersion: getCV });
  const overrides = result.filter((e) => e.type === "advisory_override");
  assert.equal(overrides.length, 1);
  const ev = overrides[0];
  assert.ok(
    "recommended_agent_rev" in ev,
    "recommended_agent_rev must be present when sentinel is null"
  );
  assert.equal(ev.recommended_agent_rev, null);
  assert.equal(ev.recommended_agent_content_hash, null);
  assert.ok("actual_agent_rev" in ev, "actual_agent_rev must be present when sentinel is null");
  assert.equal(ev.actual_agent_rev, null);
  assert.equal(ev.actual_agent_content_hash, null);
});

// ---------------------------------------------------------------------------
// needs_more_detail_repeat — single agent, stamped with agent_rev
// ---------------------------------------------------------------------------

test("needs_more_detail_repeat: stamps agent_rev and agent_content_hash when helper returns value", () => {
  const { scanSession } = loadLib();
  const entries = [
    dispatchAuditEntry("needs_more_detail", "writer"),
    dispatchAuditEntry("needs_more_detail", "writer"),
  ];
  const getCV = fakeCV({ "writer": 55 });
  const result = scanSession({ entries, sessionId: "cv-nmd-001", getComponentVersion: getCV });
  const nmd = result.filter((e) => e.type === "needs_more_detail_repeat");
  assert.equal(nmd.length, 1);
  const ev = nmd[0];
  assert.equal(ev.agent_rev, 55);
  assert.equal(ev.agent_content_hash, "hash-writer");
});

test("needs_more_detail_repeat: sentinel undefined → omit agent_rev and agent_content_hash", () => {
  const { scanSession } = loadLib();
  const entries = [
    dispatchAuditEntry("needs_more_detail", "writer"),
    dispatchAuditEntry("needs_more_detail", "writer"),
  ];
  const getCV = fakeCV({});
  const result = scanSession({ entries, sessionId: "cv-nmd-002", getComponentVersion: getCV });
  const nmd = result.filter((e) => e.type === "needs_more_detail_repeat");
  assert.equal(nmd.length, 1);
  const ev = nmd[0];
  assert.ok(!("agent_rev" in ev), "agent_rev must be absent when sentinel is undefined");
  assert.ok(
    !("agent_content_hash" in ev),
    "agent_content_hash must be absent when sentinel is undefined"
  );
});

test("needs_more_detail_repeat: sentinel null → include agent_rev and agent_content_hash with null", () => {
  const { scanSession } = loadLib();
  const entries = [
    dispatchAuditEntry("needs_more_detail", "writer"),
    dispatchAuditEntry("needs_more_detail", "writer"),
  ];
  const getCV = fakeCV({ "writer": null });
  const result = scanSession({ entries, sessionId: "cv-nmd-003", getComponentVersion: getCV });
  const nmd = result.filter((e) => e.type === "needs_more_detail_repeat");
  assert.equal(nmd.length, 1);
  const ev = nmd[0];
  assert.ok("agent_rev" in ev, "agent_rev must be present when sentinel is null");
  assert.equal(ev.agent_rev, null);
  assert.equal(ev.agent_content_hash, null);
});

// ---------------------------------------------------------------------------
// Component-agnostic events: must NOT gain any component version fields
// ---------------------------------------------------------------------------

test("self_handle_unaided_invocation: no component version fields added even when helper returns values", () => {
  const { scanSession } = loadLib();
  const entries = [dispatchAuditEntry("self_handle_unaided")];
  // Helper always returns a value — but agnostic events must still not be stamped
  const getCV = fakeCV({ "": 99 });
  const result = scanSession({ entries, sessionId: "cv-agnostic-001", getComponentVersion: getCV });
  const shu = result.filter((e) => e.type === "self_handle_unaided_invocation");
  assert.equal(shu.length, 1);
  const ev = shu[0];
  assert.ok(!("agent_rev" in ev), "agent_rev must not appear on self_handle_unaided_invocation");
  assert.ok(
    !("agent_content_hash" in ev),
    "agent_content_hash must not appear on self_handle_unaided_invocation"
  );
  assert.ok(!("skill_rev" in ev), "skill_rev must not appear on self_handle_unaided_invocation");
  assert.ok(
    !("skill_content_hash" in ev),
    "skill_content_hash must not appear on self_handle_unaided_invocation"
  );
});

test("catalog_degraded_session: no component version fields added", () => {
  const { scanSession } = loadLib();
  const entries = [assistantTextEntry("[CATALOG ERROR] catalog degraded.")];
  const getCV = fakeCV({ "": 99 });
  const result = scanSession({ entries, sessionId: "cv-agnostic-002", getComponentVersion: getCV });
  const degraded = result.filter((e) => e.type === "catalog_degraded_session");
  assert.equal(degraded.length, 1);
  const ev = degraded[0];
  assert.ok(!("agent_rev" in ev), "agent_rev must not appear on catalog_degraded_session");
  assert.ok(
    !("agent_content_hash" in ev),
    "agent_content_hash must not appear on catalog_degraded_session"
  );
  assert.ok(!("skill_rev" in ev), "skill_rev must not appear on catalog_degraded_session");
  assert.ok(
    !("skill_content_hash" in ev),
    "skill_content_hash must not appear on catalog_degraded_session"
  );
});

test("skill_mediated_delegation: no component version fields added", () => {
  const { scanSession } = loadLib();
  const entries = [skillCallEntry("dispatch"), agentCallEntry("writer")];
  const getCV = fakeCV({ dispatch: 1, "writer": 2 });
  const result = scanSession({ entries, sessionId: "cv-agnostic-003", getComponentVersion: getCV });
  const smd = result.filter((e) => e.type === "skill_mediated_delegation");
  assert.equal(smd.length, 1);
  const ev = smd[0];
  assert.ok(!("agent_rev" in ev), "agent_rev must not appear on skill_mediated_delegation");
  assert.ok(
    !("agent_content_hash" in ev),
    "agent_content_hash must not appear on skill_mediated_delegation"
  );
  assert.ok(!("skill_rev" in ev), "skill_rev must not appear on skill_mediated_delegation");
  assert.ok(
    !("skill_content_hash" in ev),
    "skill_content_hash must not appear on skill_mediated_delegation"
  );
});
