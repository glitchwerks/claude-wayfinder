# Consumer-Leakage Audit — 2026-05-24

**Issue:** #234
**Scope:** 10 active reference docs (`README.md` + curated set under `docs/`). Historical artifacts (`docs/superpowers/`, `docs/reviews/`, `docs/postmortems/`, `docs/refactor/`, `docs/exploration/`) explicitly out of scope per #234.
**Mode:** Per-instance categorization as **Hard leakage** / **Example leakage** / **Legitimate**.

This report is the deliverable for #234. It enumerates findings; fixes ship as separate follow-up issues per the AC.

## Summary

| Track | Files reviewed | Hard | Example | Legitimate (verified) | Net actionable |
|---|---:|---:|---:|---:|---:|
| Active reference docs | 10 | 5 | 4 | 5 | **9** |

The 94 agent-name occurrences flagged by the initial grep in #234's scope sweep mostly resolved to **labeled examples** (worked example sections clearly marked as such) rather than leakage. The per-instance categorization caught the genuinely-problematic uses without over-firing on legitimate illustrative ones.

**Concentration of Hard findings**: `docs/dispatch-discipline.md` accounts for **all 5 Hard hits**. The four rules in that doc were written with "source harness implements this as `<filename>.js`" framing that reads as prescriptive rather than illustrative.

---

## Findings

### `docs/dispatch-discipline.md` — 5 Hard, 0 Example, 2 Legitimate (verified)

| Line | Cat | Current | Recommended action |
|---|---|---|---|
| L9 | Hard | "…each one emerged from a concrete class of observed routing error in the **source harness**." | Replace with "…from patterns observed in reference router implementations." |
| L21 | Hard | "**The source harness implements this as** `check-no-self-dispatch.js`." | Soften to "Reference-implementation pattern: a PreToolUse hook on the Agent tool reading `subagent_type`. One example is `check-no-self-dispatch.js` in the `glitchwerks/claude-configs` repo." |
| L29 | Hard | "In the source harness, the allowed callers are `project-planner` and `inquisitor` (both `model: opus`). The allowed targets are `Explore` and `ops`…" — **agent names baked into rule body** | Genericize rule body to fictional names: "For example, a router might allow `strategic-planner` and `adversarial-reviewer` (Opus-tier) to dispatch read-only `explorer` and `ops-query` agents directly. The allowlist is consumer-defined; substitute your topology's names." |
| L33 | Hard | "**The source harness implements this as** `check-opus-native-allowlist.js`." | Same pattern as L21 — pointer not prescription. |
| L45 | Hard | "The **source harness** also ships `inject-subagent-preamble.js` as a PostToolUse hook…" | Same — neutral framing. |
| L55 | Legitimate | "The source harness implements this as part of `check-agent-dispatch-pairing.js` (a Tier 1 hook **that already ships with wayfinder**…)." | **Keep as-is.** The hook ships with the plugin; naming it is correct. |
| L75 | Legitimate | "A consumer whose router is named `router` and whose Opus-tier planner is named `strategic-planner`…" | **Keep as-is.** Obviously-fictional consumer names — good example of the desired pattern. |
| L77 | Legitimate-with-fix | "The source harness implementations (`check-no-self-dispatch.js`, `check-opus-native-allowlist.js`, `inject-subagent-preamble.js`) are **reference implementations** — they encode the pattern without being the canonical deliverable for every consumer." | **Keep as-is.** Already explicitly labeled as reference implementations, not canonical. |

### `README.md` — 1 Example

| Line | Cat | Current | Action |
|---|---|---|---|
| L71–73, L125, L128, L192 | Example | Demo output blocks use `code-writer` as the example agent without "example output; your agent names will differ" framing. | Add a one-line comment to each demo block: `# Example output; your agents will have different names`. |

### `docs/api.md` — 1 Example

| Line | Cat | Current | Action |
|---|---|---|---|
| L46, L106, L111, L115 | Example | Python code examples use `code-writer`, `general-purpose`, `debugger` as concrete agent names without "substitute your agent names" framing. | Add a leading `# Substitute your own agent names` comment to the first example block; or genericize to `agent-a`, `agent-b` if the example doesn't depend on the names having semantic meaning. |

### `docs/dispatch-authoring-guide.md` — 1 Example

| Line | Cat | Current | Action |
|---|---|---|---|
| L21 | Example | "Useful for entries that should win when the user says 'ask the **code-writer agent**'…" | Reframe with "for example" qualifier: "For example, if your router has a `code-writer` agent and a user says 'ask the code-writer agent…'". |

### `docs/schema.md` — 0 Hard, 0 Example, 1 Legitimate (verified)

| Line | Cat | Notes |
|---|---|---|
| L33, L361–466 | Legitimate | Minimal-example catalog (§7) uses `general-purpose`, `code-writer`, `debugger`. The block is explicitly labeled "Minimal example catalog" and is structurally separate from normative schema text — readers understand these as examples. **Keep as-is.** |

### `docs/design/trigger-schema.md` — 0 Hard, 0 Example, 2 Legitimate (verified)

| Line | Cat | Notes |
|---|---|---|
| §9 worked examples | Legitimate | Agent-name uses are within clearly-marked worked examples. |
| `routable: false` mechanism | Legitimate | Describing the router-exclusion mechanism unavoidably requires naming the router agent; this is structurally required, not leakage. |

### `docs/design.md` — clean

### `docs/integration.md` — clean

`docs/integration.md`'s "Bundled hooks" table (L357–361) names hooks that genuinely ship with the plugin. SessionStart hook reference on L19 (`check-catalog-health.js`) is also a bundled wayfinder hook. **Both legitimate.**

### `docs/release-process.md` — clean

Memory-file path references (L129, L139, L187) point to `~/.claude/agent-memory/general-purpose/…`. These describe the **author's** memory layout, not bundled fixtures — but they appear in a section about the author's release-process workflow rather than as prescriptive guidance for consumers. Borderline; not flagged as Hard because the doc's framing already positions itself as the author's process rather than a consumer requirement. If a future audit re-evaluates this, the right action would be to either rewrite as consumer-applicable guidance or move the author-specific content out of `docs/`.

### `docs/design/methodology-lessons.md` — clean

(Just-created by PR #233; lessons cite #143 / PR #152 as origin, which is legitimate provenance.)

---

## Follow-up issues

Per the issue #234 AC, fixes ship as **separate follow-up issues**.

| Follow-up | Scope | Effort |
|---|---|---|
| `fix(docs): rewrite dispatch-discipline.md hard-leakage — neutral framing for reference implementations` | 5 Hard hits in `docs/dispatch-discipline.md` (L9, L21, L29, L33, L45). Rule-2 body rewrite is the largest change. | small-medium |
| `docs: label demo-output and code-example agent names as "your agents will differ"` | 3 Example hits across `README.md`, `docs/api.md`, `docs/dispatch-authoring-guide.md`. Bundled because the pattern is consistent (add one-line example-framing label). | trivial |

**Optional follow-up to consider** (not in this issue's AC, but surfaced by the audit):

| Optional | Scope | Reasoning |
|---|---|---|
| `docs: review release-process.md author-process content for consumer applicability` | Memory-file path references and other author-specific workflow detail in `docs/release-process.md`. | Borderline; not Hard leakage but also not consumer-applicable in places. Worth a focused pass at maintainer convenience. |

---

## Methodology + caveats

- **Single `code-reviewer` batch** (smaller scope than #216; no historical-artifact track needed). Findings re-classified by the router into the deliverable's three-category structure.
- **Bundled-fixture verification:** the audit checked whether claimed agent names appear under `claude_wayfinder/demo/` before classifying as Legitimate. Confirmed: none of `general-purpose` / `inquisitor` / `project-planner` / `code-writer` / `code-reviewer` / `doc-writer` / `debugger` / `devops` / `ops` / `Explore` / `Plan` are bundled — all appearances in plugin docs must therefore be either explicitly fictional, explicitly the consumer's choice, or labeled illustrative examples.
- **Methodology improvement** carried over from #230: the audit ran workspace-wide pattern greps **before** the per-file pass, which surfaced the `dispatch-discipline.md` concentration upfront and bounded the categorization work.
- The audit report itself (`docs/reviews/2026-05-24-consumer-leakage-audit.md`) is durable; do not delete until follow-up issues close.

🤖 _Generated by Claude Code on behalf of @cbeaulieu-gt_
