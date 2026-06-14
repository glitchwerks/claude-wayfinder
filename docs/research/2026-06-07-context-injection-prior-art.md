# Prior Art: Scenario-Scoped / Conditional Context Injection in AI Coding Assistants

**Research date:** 2026-06-07
**Authored for:** claude-wayfinder Issue #315 — "Explore context injection — a lighter-weight guidance unit below skills"
**Wayfinder source read:** `glitchwerks/claude-wayfinder` @ main (sha `56698cd` / schema.md sha `95c22b7`)

---

## Idea

Add a new first-class catalog entry kind to claude-wayfinder — lighter than a skill, heavier than a memory — that the deterministic matcher injects as a paragraph of situational context when a scenario (path pattern, keyword, tool mention) fires, without loading a full SKILL.md procedure.

---

## Requirements (what a candidate must do to be "worth drawing from")

1. **Conditional/scoped triggering** — injection is gated by a signal (glob, keyword, tool), not always-on.
2. **Deterministic trigger evaluation** — the trigger fires or does not fire based on data, not a model inference pass at routing time.
3. **Granularity distinction** — the system distinguishes "small situational note" from "full procedure/skill" in how it is authored and how it loads.
4. **Over-injection avoidance** — the system either caps the number of injected units, budgets by tokens, or provides author-facing guidance that prevents noise.
5. **Authoring & precedence rules** — clear conflict/ordering semantics when multiple units match simultaneously.
6. **Payload delivery to the consuming agent** — the matched context reaches the agent somehow (return payload field, hook injection, prompt template slot).

---

## Search axes used

- **Direct synonyms:** "cursor rules globs", "copilot-instructions applyTo", "conditional context injection", "scenario-scoped guidance", "path-scoped rules"
- **Problem-shape synonyms:** "context window budget", "token tax", "over-injection avoidance", "rule triggering mechanism", "situational context"
- **Adjacent domains:** RAG retrieve-rerank-truncate pipelines; progressive disclosure pattern (SKILL.md / AGENTS.md); system-prompt budget management
- **Vendor-specific phrasing:** Cursor `.mdc` / "Auto Attached" / "Agent Requested"; GitHub Copilot `applyTo`; Continue.dev context providers / rules; Windsurf `.windsurfrules`; AGENTS.md spec; Zed `.rules`; aider CONVENTIONS.md
- **Negative axes:** always-on global instructions (CLAUDE.md, `.windsurfrules`, Zed `.rules`); model-mediated selection (Cursor "Agent Requested"); repository-level repo-wide instructions with no conditional loading

---

## Shortlist (ranked by expected value to wayfinder's design questions)

---

### 1. Cursor Project Rules (`.cursor/rules/*.mdc`) — multi-tier conditional rule injection with explicit glob and description-based triggers

- **URL:** https://docs.cursor.com/en/context/rules (fetched 2026-06-07; canonical docs redirect to cursor.com/docs — content sourced from https://www.morphllm.com/cursor-rules-best-practices and https://techsy.io/en/blog/cursor-rules-guide, fetched 2026-06-07)
- **Reference implementation:** https://github.com/sanjeed5/awesome-cursor-rules-mdc/blob/main/cursor-rules-reference.md (fetched 2026-06-07)
- **Relevance:** addresses requirements 1, 2 (glob mode), 3, 4, 5
- **Maturity:** Cursor Inc., production product, 2024–2026 active development; `.mdc` format stable since v2.0, superseded by folder-based `.cursor/rules/` in v2.2
- **License:** proprietary product; the pattern/schema is documented publicly

#### Evaluation table

| Dimension | Detail |
|---|---|
| **Triggering mechanism** | Four discrete tiers: (a) `alwaysApply: true` — global, always loaded; (b) `globs: [...]` — Auto Attached, deterministic fnmatch against open files; (c) `description:` with no globs — Agent Requested, model reads description and decides; (d) `@rule-name` — Manual, explicit user mention. **Only (b) is fully deterministic.** |
| **Granularity tier** | Rules are intended to be either a "situational note" (a few dozen lines) or a "procedure" (up to 500 lines). No formal schema field distinguishes them — it is a size convention, not a type distinction. A small rule and a large rule are authored identically. |
| **Selection / scoring** | For glob-matched rules: all matching rules inject simultaneously, no ranking or cap. No scoring. For Agent Requested: model makes a probabilistic inclusion decision per rule description. No cap documented. For Always Apply: all load unconditionally. Conflict resolution for overlapping rules is undefined ("behavior isn't strictly defined; rules loaded later tend to take precedence"); numbered filenames (`001-base.mdc`) are a workaround. |
| **Size discipline** | Community consensus (not enforced by the tool): individual rule files ≤ 500 lines; all `alwaysApply` rules combined ≤ 2,000 tokens (~50 lines × 4 tokens/line × N files). The "token tax" framing — "five always-apply rules at 50 lines each = 1,000–2,000 tokens overhead per request" — is the primary design pressure pushing authors toward glob-scoped rules. |
| **Authoring & precedence** | Frontmatter schema: `description`, `globs`, `alwaysApply`. Precedence hierarchy: Team Rules > Project Rules (`.cursor/rules/`) > User Rules (Cursor Settings). When multiple rules at the same level match, all are included — no suppression. |

**Worth borrowing:** The **glob-triggered Auto Attached pattern** (req 1, 2) is the closest structural match to what wayfinder wants: a rule file with a `globs:` block that fires deterministically when file paths in the dispatch context match, without model mediation. The **token-tax framing** (req 4) — "every always-apply rule costs tokens before you type a word" — validates wayfinder's stated motivation for scope-gating context. The **500-line / 2,000-token combined-budget recommendation** is a concrete benchmark for size discipline authoring guidance.

**What to avoid:** The "Agent Requested" tier is a model-mediated probabilistic decision — antithetical to wayfinder's deterministic-first principle. The undefined conflict behavior when multiple glob-matched rules overlap is a known footgun; wayfinder should specify explicit ordering semantics. The absence of a formal schema field distinguishing "note" from "procedure" means authors have no guardrail — wayfinder can improve on this with a typed `kind` field and a enforced content-length NIT.

**Lift effort:** Adapt the pattern; the glob-match + concurrent injection model is directly mappable onto wayfinder's existing `triggers.path_globs` machinery.

---

### 2. GitHub Copilot Path-Scoped Instructions (`.github/instructions/*.instructions.md` with `applyTo`) — additive, priority-layered, glob-gated instruction injection

- **URL:** https://docs.github.com/copilot/customizing-copilot/adding-custom-instructions-for-github-copilot (fetched 2026-06-07)
- **VS Code docs:** https://code.visualstudio.com/docs/agent-customization/custom-instructions (fetched 2026-06-07)
- **Changelog reference:** https://github.blog/changelog/2025-09-03-copilot-code-review-path-scoped-custom-instruction-file-support/ (fetched 2026-06-07)
- **Relevance:** addresses requirements 1, 2, 4, 5, 6
- **Maturity:** GitHub/Microsoft, GA in VS Code since mid-2025, code review support added September 2025, active maintenance

#### Evaluation table

| Dimension | Detail |
|---|---|
| **Triggering mechanism** | `applyTo: "glob/pattern"` in YAML frontmatter of `*.instructions.md` files under `.github/instructions/`. Deterministic glob matching against the file being edited. Multiple comma-separated patterns allowed. Omitting `applyTo` makes the file inert. The repo-wide `copilot-instructions.md` is always loaded (no `applyTo` required). |
| **Granularity tier** | Two named tiers: (a) repo-wide `copilot-instructions.md` — always-loaded, unconditional; (b) path-scoped `*.instructions.md` — conditionally loaded via `applyTo`. The distinction is structural (different file locations and naming convention), not a type field. Also: agent-specific exclusion via `excludeAgent: "code-review"` frontmatter (added November 2025). |
| **Selection / scoring** | All matching `applyTo` patterns inject simultaneously — additive union, no ranking within a tier. Priority ordering across tiers (Personal > Path-scoped > Repo-wide > AGENTS.md > Org-level) determines which instruction is preferred when conflicts occur, but all layers reach the model's context window simultaneously. There is no cap on the number of matching instruction files injected. |
| **Size discipline** | Hard limit: the first 4,000 characters of any instruction file are processed; content beyond that is silently discarded. No per-session aggregate cap is documented. The "no longer than 2 pages" authoring guidance is informal. |
| **Authoring & precedence** | Frontmatter: `applyTo` (glob string), `excludeAgent` (agent name string). Priority chain is well-defined for conflict resolution. No limit on the number of instruction files per repo. |

**Worth borrowing:** The `applyTo` glob-gating pattern (req 1, 2) and the **hard size cap per file** (req 4) are directly applicable. The 4,000-character hard cap (enforced by Copilot code review) is the most concrete size-discipline number in the entire ecosystem — it provides a benchmark for wayfinder's budget NIT. The **additive-union semantics** (all matching files inject simultaneously, priority only resolves conflicts) is worth borrowing as the base behavior, with wayfinder adding a top-N cap for over-injection avoidance that Copilot lacks. The structural separation of always-on (`copilot-instructions.md`) from conditional (`*.instructions.md`) mirrors the skill vs. context-unit distinction wayfinder is designing.

**What to avoid:** The silent truncation at 4,000 characters is a footgun — authors don't know their content is being dropped. Wayfinder should prefer an explicit size NIT at catalog build time rather than silent runtime truncation. The absence of any injection cap (all matches load) means a poorly-maintained repo can stack arbitrarily many instruction files onto one turn.

**Lift effort:** Adapt the pattern; the `applyTo` + additive-union + file-level size cap pattern maps cleanly onto wayfinder's existing trigger/catalog machinery with one extension: an explicit top-N cap.

---

### 3. AGENTS.md Subdirectory Scoping — directory-hierarchy-based context scoping with progressive disclosure

- **URL:** https://www.morphllm.com/agents-md-guide (fetched 2026-06-07)
- **Windsurf/Devin docs:** https://docs.devin.ai/desktop/cascade/agents-md (fetched 2026-06-07, via redirect from docs.windsurf.com)
- **Codex discussion:** https://vibecoding.app/blog/agents-md-guide (fetched 2026-06-07)
- **Standard steward:** Agentic AI Foundation / Linux Foundation (2025); reported adoption in 60,000+ repos
- **Relevance:** addresses requirements 1, 3, 4, 6
- **Maturity:** emerging open standard (2025), active ecosystem adoption across Codex CLI, Copilot, Cursor, Windsurf, Gemini CLI

#### Evaluation table

| Dimension | Detail |
|---|---|
| **Triggering mechanism** | **Location-based directory scoping**: root `AGENTS.md` is always-on (loaded every session). Subdirectory `AGENTS.md` files are scoped to `<directory>/**` — they activate automatically when the agent is working within that directory subtree. No frontmatter required; discovery is by filesystem path. This is **deterministic** (no model inference) but coarser than glob-pattern matching — the trigger is the directory boundary, not a file-extension or keyword. |
| **Granularity tier** | The AGENTS.md vs. SKILL.md distinction maps directly: AGENTS.md = always-on or directory-scoped context; SKILL.md = on-demand skill invoked when a task matches. AGENTS.md is explicitly designed for "project-wide context" (always loaded), while SKILL.md is explicitly for "single reusable task/capability" (on-demand). This is a formal granularity split. |
| **Selection / scoring** | Nearest-file wins for conflicts: the closest `AGENTS.md` to the file being edited takes precedence for conflict resolution. All files in the ancestor chain are loaded and merged (inherited from parent directories). No scoring; no injection cap. |
| **Size discipline** | Enforced cap: **32 KiB default** (Codex), content beyond silently truncated. Research consensus: 20–30 lines outperforms longer files for instruction-following. |
| **Authoring & precedence** | Plain markdown, no required fields. Hierarchy: nearest-file wins on conflicts; parent files contribute via inheritance. No formal frontmatter schema. |

**Worth borrowing:** The **formal two-tier granularity distinction** (AGENTS.md = contextual note, SKILL.md = procedure) is the clearest model in the ecosystem for what wayfinder is building. The distinction is structural (different file types, different loading semantics) rather than just a size convention, which validates wayfinder's decision to make context units a separate catalog `kind`. The **progressive disclosure analogy** — SKILL.md loads only names/descriptions initially, then full content on task match — is a design pattern wayfinder should apply to context units: catalog carries a `description` summary; the full content body is fetched only when the trigger fires.

**What to avoid:** Directory-boundary scoping is too coarse for wayfinder's use cases. The motivating examples in Issue #315 (API layer path pattern, specific module paths, keyword-based gotchas) require file-glob precision, not directory-level always-on-within-subtree behavior. AGENTS.md's lack of keyword or tool-mention triggers also means it cannot fire on wayfinder's non-path trigger axes.

**Lift effort:** Study-and-design-from-scratch on the triggering side; the granularity model and progressive disclosure framing are worth borrowing directly.

---

### 4. Cursor "Agent Requested" / Description-Matching — model-mediated rule relevance selection

- **URL:** https://techsy.io/en/blog/cursor-rules-guide (fetched 2026-06-07)
- **Forum reference:** https://forum.cursor.com/t/correct-way-to-specify-rule-type/100672 (fetched 2026-06-07)
- **Relevance:** addresses requirements 1, 3 — but via a mechanism wayfinder must explicitly reject
- **Maturity:** Cursor Inc., production; no separate maturity rating from #1 Cursor entry

#### Evaluation table

| Dimension | Detail |
|---|---|
| **Triggering mechanism** | The Cursor agent reads each rule's `description:` field and decides whether the rule is relevant to the current task. Decision is model-inferred, probabilistic, and non-reproducible. No scoring; model either includes or excludes based on its interpretation of the description. |
| **Granularity tier** | Same as glob-triggered rules — no schema distinction. |
| **Selection / scoring** | Model-side probabilistic inclusion. No cap, no ranking, no deterministic output. |
| **Size discipline** | Same as glob rules. |
| **Authoring & precedence** | Description must be actionable ("Stripe payment flows" vs. "payment stuff"). Good description authoring is the only lever; no mechanical enforcement. |

**Worth borrowing:** The **description field as a human-readable signal** for tool-side triage is worth adopting in the catalog entry's authoring surface — even if wayfinder doesn't use the description as a trigger mechanism, it serves as the audit label that explains why a context unit fires. The warning that "descriptions must be written as if explaining to a human when to use the rule" is valid authoring guidance.

**What to avoid:** The model-mediated selection mechanism itself is incompatible with wayfinder's deterministic-first design principle (documented in `docs/design.md`: "The same request routes differently across turns. Self-handle drift, skill-pass failures, and advisor-consultation failures are overwhelmingly mechanical — the model scans prose and makes a probabilistic call where a lookup would be exact"). This is the explicitly named failure mode wayfinder was built to eliminate. Any description-matching trigger for context units must be stem-matched (like existing keyword scoring), not model-inferred.

**Lift effort:** Study-only for the triggering mechanism; the description authoring guidance is port-one-module.

---

### 5. AGENTS.md/SKILL.md Progressive Disclosure Pattern — on-demand full-content loading after name-level triage

- **URL:** https://www.newsletter.swirlai.com/p/agent-skills-progressive-disclosure (fetched 2026-06-07)
- **Firecrawl explainer:** https://www.firecrawl.dev/blog/agent-skills (fetched 2026-06-07)
- **Relevance:** addresses requirements 3, 4, 6
- **Maturity:** documented pattern in SKILL.md ecosystem (2025); implemented by Claude Code skill-loader

#### Evaluation table

| Dimension | Detail |
|---|---|
| **Triggering mechanism** | Three-tier: (1) discovery — load only name + description (~50–100 tokens) at startup for all skills; (2) activation — load full SKILL.md body when a task matches the skill's domain; (3) execution — pull supporting materials (scripts, templates) only when an execution step needs them. |
| **Granularity tier** | Formally named: metadata (always cheap), instructions (on-demand), supporting materials (as-needed). This is the clearest granularity model in the ecosystem. |
| **Selection / scoring** | Matching logic is external to the progressive disclosure pattern; the pattern only describes the loading lifecycle, not the matching mechanism. |
| **Size discipline** | The first tier (metadata) is strictly bounded (~50–100 tokens). The second tier (instructions) is bounded by the SKILL.md file itself. The third tier is uncapped but only loads when execution reaches that step. |
| **Authoring & precedence** | Authors write three distinct sections in the same file (or separate files); the framework only loads what the tier demands. |

**Worth borrowing:** The **three-tier loading lifecycle** directly solves wayfinder's stated token-efficiency requirement — context units in the catalog should carry only a `description` + `triggers` summary at catalog-load time; full body text is loaded only when the trigger fires. This is also the conceptual basis for why context units need to be a separate `kind`: they have a different loading lifecycle than skills (which require the router to invoke them via the Skill tool) and agents (which require delegation).

**What to avoid:** This is a pure pattern — no external library to integrate. The risk is over-engineering the three-tier model for what is, in wayfinder's case, effectively a two-tier need (catalog entry summary + full injection text).

**Lift effort:** Adapt the pattern; the two-tier model (summary in catalog, full body injected on match) is straightforward.

---

### 6. Continue.dev Rules — always-on concatenated system message augmentation (no conditional loading)

- **URL:** https://docs.continue.dev/reference (fetched 2026-06-07)
- **Context providers:** https://docs.continue.dev/blocks/context-providers (fetched 2026-06-07; page not found)
- **Relevance:** addresses none of requirements 1 or 2; negative reference only
- **Maturity:** Continue Inc., open-source (Apache 2.0), active development 2024–2026

#### Evaluation table

| Dimension | Detail |
|---|---|
| **Triggering mechanism** | **None** — rules are concatenated into the system message for every Agent, Chat, and Edit request. No conditional loading, no glob patterns, no path scoping. The `rules:` section in `config.yaml` is always-on. |
| **Granularity tier** | None formally — rules and context providers are separate concepts but neither is a "lightweight note" tier. Context providers are user-@-mention activated, not automatically injected. |
| **Selection / scoring** | No scoring. All rules always inject. Context providers require explicit user invocation (`@provider`). |
| **Size discipline** | None specified or enforced. |
| **Authoring & precedence** | Rules are a plain YAML list. No frontmatter, no conflict resolution, no ordering semantics. |

**Worth borrowing:** The **context providers = explicit invocation, rules = automatic** split is a useful framing for what wayfinder is _not_ building. Context units are closer to Continue's "rules" (automatic injection) than its "context providers" (user-initiated). The distinction confirms that trigger-gated automatic injection is the right frame.

**What to avoid:** The absence of any conditional loading mechanism makes Continue.dev unsuitable as a model. Its rules system is the "always-on CLAUDE.md" equivalent that Issue #315 explicitly identifies as the problem to solve.

**Lift effort:** Study-only; useful as a negative reference.

---

### 7. Zed AI Rules (`.rules`) — single-file, monolithic, always-on, no conditional support

- **URL:** https://22.frenchintelligence.org/2025/11/30/complete-guide-how-to-set-ai-coding-rules-for-zed/ (fetched 2026-06-07)
- **Relevance:** requirements 4 (size guidance only); negative reference for requirements 1–3
- **Maturity:** Zed Industries, production IDE, 2024–2026

#### Evaluation table

| Dimension | Detail |
|---|---|
| **Triggering mechanism** | None — the first matching file in the priority list (`.rules` > `.cursorrules` > etc.) is loaded once per session as a monolithic system prompt. No conditional loading, no path scoping, no frontmatter. |
| **Granularity tier** | None — all content injected identically regardless of length or intent. |
| **Selection / scoring** | First-file-wins among the priority list; no multiple-file merging. No scoring. |
| **Size discipline** | Community guidance: ≤ 500 lines (~2,500 tokens). No enforced cap. |
| **Authoring & precedence** | Pure markdown. YAML headers ignored. "Only the first matching file is loaded" means all conditional logic must be written inline within one file. |

**Worth borrowing:** The **≤ 500 lines / ~2,500 token** community benchmark is consistent with the Cursor community's guidance; it cross-validates the rough "context unit should be much smaller than this" framing.

**What to avoid:** The single-file, always-on model with no conditional loading is the architectural anti-pattern wayfinder is solving. Zed's approach is the "problem case" that motivates context injection.

**Lift effort:** Study-only; useful as a negative reference confirming the problem.

---

### 8. Windsurf `.windsurfrules` — always-on, project-wide, root-only

- **URL:** https://skillsplayground.com/guides/windsurf-rules/ (fetched 2026-06-07)
- **Relevance:** negative reference only
- **Maturity:** Windsurf (Codeium), production IDE, 2024–2026

#### Evaluation table

| Dimension | Detail |
|---|---|
| **Triggering mechanism** | Always-on, loaded from project root on project open. No conditional loading, no glob support. |
| **Granularity tier** | None — single markdown file, monolithic injection. Subdirectory conventions must be embedded as sections within one file. |
| **Selection / scoring** | N/A — always loads. |
| **Size discipline** | None documented. |
| **Authoring & precedence** | No schema. No conflict resolution (single file). |

**Worth borrowing:** Nothing structural. The scoping sections _within_ a single Windsurf rules file (e.g. "### Frontend (`src/frontend/`)") is the inline-conditional workaround that motivated the `applyTo` / glob-gating approach in Cursor and Copilot. Wayfinder's catalog-based trigger model solves this problem correctly.

**Lift effort:** Study-only; negative reference.

---

### 9. Aider CONVENTIONS.md — always-on read-only convention injection

- **URL:** https://aider.chat/docs/usage/conventions.html (fetched 2026-06-07)
- **Relevance:** negative reference; useful only for size guidance
- **Maturity:** Aider-AI, open-source (Apache 2.0), active 2024–2026

#### Evaluation table

| Dimension | Detail |
|---|---|
| **Triggering mechanism** | Manual (`/read CONVENTIONS.md`) or configured-always-on in `.aider.conf.yml`. No path scoping, no conditional loading. |
| **Granularity tier** | None — a single markdown file loaded as read-only context. |
| **Selection / scoring** | No scoring. Either loaded or not. |
| **Size discipline** | Community guidance: ≤ 150–200 lines. Beyond this, "Aider may deprioritize rules at the bottom of the file as the conversation grows." No enforced cap. |
| **Authoring & precedence** | Multiple files supported as a list in `.aider.conf.yml`; interaction between files not specified. |

**Worth borrowing:** The **"beyond 150–200 lines the model deprioritizes rules at the bottom"** observation is a practical token-budget benchmark from a different axis: not cost but attention. Wayfinder's size NIT for context units should target a body significantly below 200 lines. The community number from Cursor (500-line cap per rule, 2,000-token combined) and from Aider (150-200 lines before degradation) bracket the useful range: **a context unit body above ~150 lines is likely to degrade model behavior even if it is within token budget.**

**Lift effort:** Study-only.

---

### 10. Sourcegraph Cody Context Filters — repository-include/exclude for LLM context, not instruction injection

- **URL:** https://sourcegraph.com/docs/cody/capabilities/ignore-context (fetched 2026-06-07)
- **Relevance:** does not address the design space; included for completeness
- **Maturity:** Sourcegraph Enterprise, production, 2024–2026

Cody's "context filters" are repository-level allow/deny lists for which repo content reaches the LLM — not a mechanism for injecting authored guidance paragraphs. This is a data-access filter, not an instruction-injection system. Drop from further analysis.

---

## Synthesis: mapping prior art onto wayfinder's design questions

### Triggering mechanism choice

**Finding:** the ecosystem has converged on two viable deterministic trigger mechanisms:
1. **File-glob matching** (Cursor Auto Attached, Copilot `applyTo`, AGENTS.md directory scoping) — fires when file paths in the request match a pattern.
2. **Slash-command / explicit mention** (Cursor Manual, Copilot `excludeAgent`, wayfinder's existing `command_prefixes` / `agent_mentions`) — fires on an explicit signal.

A third mechanism — **model-mediated description matching** (Cursor Agent Requested) — is the one mechanism the ecosystem has tried and wayfinder must explicitly reject. The design rationale in `docs/design.md` names this failure mode by name ("the model scans prose and makes a probabilistic call where a lookup would be exact"). This is the Cursor "Agent Requested" mechanism, and wayfinder's answer is stem-based keyword scoring — which it already has.

**Recommendation for wayfinder:** context units should fire on the same trigger vocabulary already in `docs/schema.md §1` — `path_globs`, `keywords` (stem-scored), `tool_mentions`, `command_prefixes`, `agent_mentions`. No new triggering mechanism is needed. The `keyword_groups` AND-conjunction mechanism is also directly applicable for "fires only when both path AND keyword match."

### Scoring threshold

**Finding:** no prior system scores context units against a threshold — they either match or don't, and all matching units inject. Wayfinder is unique in having a scoring ladder ({0.25, 0.5, 1.0}) and a threshold-based activation model.

**Recommendation for wayfinder:** apply a **lower activation threshold** for context units than for skills. Skills activate at ≥ 0.5 (schema §4). Context units are lighter, so they may warrant a lower floor — but a floor still matters. Issue #315 Open Question #2 ("same {0.25, 0.5, 1.0} ladder, or its own band?") should lean toward: same ladder, lower threshold (e.g. ≥ 0.25 for `path_globs`-only matches), to reflect the lighter payload cost. The existing `matched_glob_count × 0.4` formula already provides a natural signal: a single glob match (score = 0.4) would cross a 0.25 threshold, meaning even a weak path signal fires the context unit. This is appropriate given that context units are cheap to inject relative to skills.

### Over-injection avoidance

**Finding:** the ecosystem has **no injection cap** in production systems (Cursor, Copilot, AGENTS.md all inject all matching units simultaneously). The only avoidance mechanisms are:
- **Authoring discipline** — "keep always-apply rules under 2,000 tokens combined" (Cursor community)
- **Hard per-file size cap** — 4,000 characters (Copilot code review)
- **Silent truncation** (AGENTS.md 32 KiB, Copilot 4K char) — a footgun

**Recommendation for wayfinder:** implement a **top-N cap** (Issue #315 Open Question #5 asks exactly this). Prior art provides no N; the recommendation is: cap at **3 context units per dispatch turn** (matching the `skills` field cap of `[:3]` in the existing decision schema). This provides consistency and prevents runaway injection in over-triggered catalogs. Emit a `[DISPATCH WARNING]` when the cap fires (consistent with existing stale-mtime warning behavior).

**Size discipline:** enforce via catalog-build NIT, not silent runtime truncation. Recommended bounds: context unit body ≥ 1 sentence (trivial content is noise) and ≤ 150 lines (above this threshold, Aider evidence shows model attention degrades; Cursor community cap is 500 lines but that is for a full procedure). A 150-line / ~600-token cap per context unit, with a 2,000-token combined cap per turn across all injected units, mirrors the Cursor 2,000-token always-apply budget.

### Injection mechanism choice (Issue #315 Open Question #1)

**Finding:** prior systems use three injection mechanisms:
1. **Return payload field** — Copilot embeds instruction text in the model's context window directly; wayfinder's equivalent is adding an `injected_context` field to the decision JSON.
2. **Hook injection** — Cursor injects matched rule text before the model sees the prompt; wayfinder's equivalent is the deferred v0.3 `UserPromptSubmit` hook.
3. **Explicit user-tool invocation** — Continue.dev context providers require `@mention`; Aider requires `/read`; not applicable to automatic context injection.

Mechanism (a) — returning `injected_context` in the dispatch decision payload — is the only option compatible with wayfinder's post-cognitive, auditable design. The router agent receives the matched context text in the decision JSON and embeds it into sub-agent briefs (Option b from Issue #315 discussion) or its own reasoning (Option a). This keeps injection visible in the decision artifact, consistent with the "every dispatch decision is a structured artifact" design principle.

### Granularity: formal type vs. size convention

**Finding:** no system in the ecosystem enforces a schema-level distinction between a "note" and a "procedure." Cursor, Copilot, and AGENTS.md all use size conventions and authoring guidelines rather than type fields. Only the AGENTS.md vs. SKILL.md ecosystem split provides a formal structural distinction — and that split maps exactly onto wayfinder's proposed `kind: "context"` vs. `kind: "skill"`.

**Recommendation for wayfinder:** the decision to make context units a **separate `kind`** (not a sub-type or size-band of skills) is validated by this survey. The only ecosystem that got the distinction right did it the same way: different file format, different loading lifecycle, different schema. A size-based threshold on skills would reproduce the ecosystem's known footgun (no guardrail on drift between "meant to be a note" and "accidentally became a procedure").

---

## No prior art found

- **Injection cap (top-N across a single turn):** no system in the ecosystem implements a hard cap on the number of conditionally injected context units per turn. This is original design work for wayfinder. The Cursor 2,000-token combined-budget guidance and the Copilot 4,000-char per-file cap are the closest analogues but are not injection counts.
- **Scored context units with a threshold:** no system scores conditional context units against a numeric threshold. All systems use binary match/no-match. Wayfinder's weight-ladder scoring applied to context units has no prior art to borrow from; the threshold design is original.
- **Conflict/precedence semantics for same-turn multi-unit injection:** Cursor's conflict behavior is "undefined in practice" and relies on numbered filenames as a workaround. Copilot's priority ordering resolves conflicts but all layers still inject. No system has a published conflict-resolution algorithm for same-turn multiple context units. This is original design work.
- **Deduplication across overlapping trigger sets:** no system deduplicates context units that share overlapping trigger conditions. If a path matches both a "Python style" context unit and an "API layer" context unit, both inject — there is no dedup. AGENTS.md's "nearest-file wins" is the only approximation, and it is directory-boundary only. Wayfinder should specify dedup semantics (by entry name) in the schema.

---

## Recommended handoff

- **`project-planner`** — top recommendation: Cursor's Auto Attached glob model + Copilot's additive-union injection + AGENTS.md's formal AGENTS.md/SKILL.md type split, synthesized with wayfinder's existing trigger vocabulary and a new top-N cap. The planner should design the `kind: "context"` schema extension, the `injected_context` return payload field, and the catalog-build NIT for size enforcement.
- **`doc-writer`** — the `docs/schema.md` update needs (new `kind` value, new decision payload field `injected_context`, new size NIT) and `docs/dispatch-authoring-guide.md` update (context unit authoring section, size guidance, trigger selection vs. skill authoring vs. CLAUDE.md) are the primary documentation deliverables.
- **`user`** — Issue #315 Open Questions #1 (injection mechanism: dispatch payload vs. sub-agent brief vs. hook), #3 (interaction with decision ladder — orthogonal or specific to `self_handle`/`self_handle_unaided`?), and the top-N cap value (this report recommends 3, matching the `skills` cap; user should confirm this is the right number before implementation).

---

## Open questions

- **Copilot character limit evolution:** a February 2026 GitHub community thread (https://github.com/orgs/community/discussions/151848) questioned whether the 4,000-character limit has been removed. The limit may have been relaxed; the report cites 4,000 characters as the Copilot code review limit per file but this should be verified against current Copilot documentation before using it as a hard benchmark.
- **Cursor "Agent Requested" internals:** how the model receives and evaluates rule descriptions is not publicly documented. The design document claims it is a model inference step, but Cursor has not published whether descriptions are embedded, scored by a classifier, or evaluated in a separate pass. This matters only as a negative reference (wayfinder is not adopting this mechanism), so the gap is low-risk.
- **AGENTS.md merge semantics across ancestor chain:** the Windsurf/Cascade documentation notes that subdirectory AGENTS.md files "inherit from parent directories" but does not detail the merge algorithm. The nearest-file-wins-on-conflict claim has not been verified against the implementation. Again low-risk since wayfinder is not adopting directory-boundary scoping.
