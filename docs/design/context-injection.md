# Design / ADR: Context Injection — a lighter-weight guidance unit below skills

**Status:** Proposed (design locked pending user sign-off on the items in § 9)
**Issue:** [#315](https://github.com/glitchwerks/claude-wayfinder/issues/315) — "Explore context injection — a lighter-weight guidance unit below skills"
**Milestone:** #13 "Context injection"
**Date:** 2026-06-07
**Inputs:** Issue #315 (full body), the Explore internals map for #315, and the prior-art research report `docs/research/2026-06-07-context-injection-prior-art.md`.

> Citation discipline: per the repo's "Cite Sources in Planning Artifacts" rule, every decision-driving claim below cites a verifiable source — a source file + symbol, `docs/schema.md` / `docs/design.md` by section, the research report by line range, or #315. Claims I could not verify against a source carry an `unverified:` prefix.

---

## 1. Context & motivation

Wayfinder's catalog today has exactly two entry kinds — **agents** (routable delegation targets) and **skills** (scored, activated at ≥ 0.5, propagated to sub-agents). This is confirmed in the `CatalogEntry` dataclass, whose `kind` field is documented as `"agent" or "skill"` (`src/claude_wayfinder/match/_types.py:L184`, and `docs/schema.md:L34`).

Issue #315 identifies a growing middle ground between "no guidance" and "a full SKILL.md procedure":

- The procedure is overkill, but a paragraph of situational context would measurably improve output.
- The context is valid only in some scenarios (a path pattern, a keyword, a tool mention) — always-loading it (as CLAUDE.md does) pays tokens on every unrelated turn.
- The agent currently has to **rediscover** this context every turn (grep the repo, re-read a doc, reconstruct a convention) — the silent-drift / repeated-work failure wayfinder exists to eliminate (#315 § Context; the drift failure mode is named in `docs/design.md:L11`).

Motivating examples from #315: a repo convention ("error responses always use the `Problem` envelope") too small to be a skill but relevant to any agent editing the API layer; a gotcha ("this module's tests need the real DB, never mock it") relevant only when touching a path; a pointer ("the canonical event shape lives in `schema/events.json` — read it before adding a field") fired by a keyword/path scenario (#315 § Motivating examples).

**Context injection** extends wayfinder's deterministic, auditable, post-cognitive matching from "route and activate" to also "inject scenario-scoped context." This is consistent with the deterministic-first rationale in `docs/design.md:L7-L19`: a lookup against authored triggers replaces the model's probabilistic re-discovery of the same context.

This document is an up-front **design** deliverable. It resolves #315 Open Questions 1–6, specifies the schema delta and the change sites, and proposes — but does not file — follow-up implementation issues. No matcher/catalog/hook code is written here (out of scope per #315).

---

## 2. Design Direction (already decided in #315 — restated)

A context unit is a **new first-class catalog entry kind** — a third `kind` value, `"context"`, alongside `"agent"` and `"skill"` (#315 § Design Direction). It has:

- Its own authored files / sidecars, separate from `SKILL.md`.
- Its own `triggers` block, reusing the existing trigger vocabulary (`keywords`, `path_globs`, `agent_mentions`, `tool_mentions`, `command_prefixes`, `keyword_groups`, `excludes`) — confirmed kind-agnostic in `docs/schema.md:L59-L67`.
- An independent score against the dispatch context, so it can match (and inject) regardless of whether any agent or skill also matched.

**Rationale for first-class over alternatives** (a field on existing skills/agents, or a low-score band on skills): independent authoring, independent triggering, and a clean audit story (#315 § Design Direction). The research report independently validates this: the only ecosystem that drew a clean structural line between "note" and "procedure" did it the same way — the AGENTS.md vs SKILL.md split is a separate file type / loading lifecycle / schema, not a size band (`docs/research/2026-06-07-context-injection-prior-art.md:L310-L314`). A size-threshold-on-skills approach reproduces the ecosystem's known footgun: no guardrail against a "meant to be a note" entry drifting into a full procedure (`docs/research/2026-06-07-context-injection-prior-art.md:L60,L314`).

This direction is **decided**; § 3 resolves the open mechanism and supporting questions around it.

---

## 3. Resolved decisions — Open Questions 1–6

### OQ1 — Injection mechanism / lifecycle point

**Decision:** Build **option (a) — dispatch return payload** first. The matcher adds an optional `injected_context` field to the decision JSON; the router embeds the matched context into its own reasoning and/or into sub-agent briefs. Defer option (c) — the hook-injected `UserPromptSubmit` mechanism — to a later iteration tracked alongside the existing deferred v0.3 pre-cognitive hook. Option (b) (sub-agent briefs only) is **subsumed by (a)**: returning the context in the payload lets the router decide whether to use it in its own turn, a sub-agent brief, or both — (b) is a router-side usage policy, not a separate matcher mechanism.

**Rationale:**
- Option (a) is the only mechanism compatible with wayfinder's **post-cognitive, auditable** design. The matcher fires after the router has composed a dispatch context (`docs/design.md:L23-L35`); returning `injected_context` in the decision JSON keeps the injection visible in the same structured decision artifact every other dispatch produces (`docs/design.md:L15`, "every dispatch decision is a structured artifact"). The research report reaches the same conclusion: payload-field injection is "the only option compatible with wayfinder's post-cognitive, auditable design" (`docs/research/2026-06-07-context-injection-prior-art.md:L302-L308`).
- Option (c) (hook injection) is "more automatic, less auditable" (#315 OQ1) and lands closer to the deferred v0.3 pre-cognitive `UserPromptSubmit` hook, which `docs/design.md:L123` explicitly defers ("the power-user audience composes context themselves"). Building (c) first would couple context injection to a deferred mechanism and weaken the audit story. It remains a legitimate later option.
- The decision payload already carries the full `output_dict` into the drift log verbatim (`src/claude_wayfinder/match/_catalog.py:L364-L365`), so a new payload field is automatically captured in telemetry with no separate log change.

**Rejected:** (c)-first (auditability regression, couples to a deferred mechanism); (b) as a distinct mechanism (it is a router-side usage of (a), not a separate matcher change).

### OQ2 — Scoring & threshold

**Decision:** Reuse the existing **`{0.25, 0.5, 1.0}` weight ladder and the existing scoring formula unchanged** (`score()` is kind-agnostic — `docs/schema.md:L253-L267`, `src/claude_wayfinder/match/_match.py` `score()`), but apply a **dedicated, lower activation threshold for context units**: `_CONTEXT_MIN = 0.25` (vs `_SKILL_MIN = 0.5`, verified at `src/claude_wayfinder/match/_match.py:L65`). Over-injection is guarded by the top-N cap and size discipline in OQ5, not by a high score floor.

**Rationale:**
- A single `path_globs` match contributes `0.4` (`docs/schema.md:L263`), which clears a `0.25` floor but not a `0.5` floor. Setting the floor at `0.25` means a context unit fires on a single weak path signal — appropriate because a context unit is **cheap to inject relative to a skill** (a paragraph vs a full SKILL.md body) (`docs/research/2026-06-07-context-injection-prior-art.md:L286-L288`).
- A floor still matters — binary match/no-match (what every surveyed prior-art system uses) produces noise. Wayfinder's scored-threshold model for context units has no prior art and is original design work (`docs/research/2026-06-07-context-injection-prior-art.md:L321`). Starting at `0.25` is a defensible default; it is a tunable constant, and over-injection is bounded by OQ5's cap regardless.
- Keeping the weight ladder and `score()` untouched is required by #315's "Out of Scope" ("no changes to existing scoring beyond a parallel context-scoring path") and is feasible because `score()` already ignores `kind`.

**Over-injection guard:** the top-N cap (OQ5) plus the catalog-build size NIT (OQ5). A low floor without these would be noisy; with them it is recall-friendly and bounded.

**Rejected:** a separate weight band (violates #315 Out-of-Scope and the legibility goal of three discrete weights, `docs/design.md:L73-L85`); a `0.5` floor equal to skills (defeats the "lighter unit, lower bar" intent and would make single-path-glob matches inert).

### OQ3 — Interaction with the decision ladder + telemetry

**Decision:** Context injection is **orthogonal to the seven decisions — it rides along with any decision** rather than producing its own decision or being special-cased to `self_handle` / `self_handle_unaided`. The matcher scores a third `context_entries` pool in parallel, selects the top-N (OQ5), and attaches the result as an `injected_context` field to **whatever decision `decide()` returns** — `delegate`, `self_handle`, `advisory`, `mixed_content`, `self_handle_unaided`, or even `needs_more_detail`. The seven-branch ladder in `decide()` (`src/claude_wayfinder/match/_decide.py:L188-L287`) is **not** modified to branch on context; context is composed after the decision and merged into the returned dict.

**Rationale:**
- The ladder is agent+skill-centric and the internals map flags that context "should AUGMENT a decision, not produce its own." The Explore relationship-model options A (decision-modifier, separate pool, injected post-decision) and C (top-level output field on all decisions) are complementary; this decision adopts **A+C together**: a separate scored pool (A) feeding a top-level optional field present on any decision (C).
- Keeping `decide()`'s branch conditions untouched preserves the stable decision contract (`docs/schema.md:L17-L23`) — `injected_context` is a **new optional field**, which is non-breaking by the schema's own definition (`docs/schema.md:L23`).
- Attaching to `self_handle_unaided` is the highest-value case (the agent has no skill and no specialist, so injected context is the only deterministic guidance it gets), but **restricting** injection to that decision would discard the API-convention example, which should reach a delegated `code-writer` too (#315 § Motivating examples). So: available on all decisions, valuable on all, special-cased to none.

**Telemetry signal:** No new event type is required. Because `_write_log_entry` serializes the full decision `output_dict` into the `matcher_decision` NDJSON row (`src/claude_wayfinder/match/_catalog.py:L360-L368`), the presence and contents of `injected_context` are captured automatically. **Recommended additive enrichment:** include an `injected_context_count` integer in the decision payload (0 when nothing matched) so health/telemetry queries can compute an injection rate without parsing the nested field. A future `_health.py` metric ("context injection rate") can be added later; it is not required for v1 and is flagged as a follow-up.

**Rejected:** a new `context_injection` decision type (would break the 7-decision contract and the reserved-`ask_user` forward-compat story, `docs/design.md:L64-L69`); special-casing to `self_handle`/`self_handle_unaided` (drops the delegate-path examples).

### OQ4 — Relationship to adjacent mechanisms (delineation table)

**Decision:** Context units occupy a distinct cell across five axes. The table below is the normative delineation; it is reproduced in the authoring guide (OQ6).

| Axis | **context** (new) | **skill** | **dispatch override** | **memory / feedback** | **CLAUDE.md** |
|---|---|---|---|---|---|
| **Trigger** | Deterministic score ≥ `0.25` over the shared trigger vocab (path_globs / keywords / tool_mentions / command_prefixes / agent_mentions / keyword_groups). `docs/schema.md:L59-L67` | Deterministic score ≥ `0.5`. `src/.../_match.py:L65` | Hard predicate match (command_prefix / path_globs / tool_mentions), all non-empty predicates must hold. `src/.../_types.py:L281-L315` | Author-provenance heuristic; loaded always or per-session by the agent's memory system, not score-gated. (CLAUDE.md "Persistent Agent Memory") | Path-based file load; unconditional once the file is in scope. |
| **Lifetime / scope** | Single dispatch turn; scenario-scoped (injected only when the trigger fires). | Single turn; activated when scored, then invoked via Skill tool. | Single turn; short-circuits the scored pipeline (`disposition_source="override"`, `docs/schema.md:L115`). | Cross-conversation (persistent files); always/session loaded. | Whole session; always loaded. |
| **Weight / size** | Paragraph. Build-time NIT cap (~150 lines / ~600 tokens per unit; see OQ5). | Full `SKILL.md` body (procedure). | No body — emits a verbatim decision (agent/skills/confidence/rationale). | Short memory files; index in `MEMORY.md`. | Whole-file, can be large; unconditional cost. |
| **Authoring provenance** | First-party context files / sidecars; `source` tag (`owned`/`project`/`plugin`/...) as for other entries (`docs/schema.md:L43-L53`). | SKILL.md + `triggers.yml` sidecar. | `triggers/.../overrides` rule file; hand-authored routing. | Agent-authored over time from conversation. | Human-authored project/global config. |
| **Audit surface** | New audit rules (empty-applicable-targets, unused-trigger, conflict-pair) — OQ6. | Existing skill audit rules (`empty-applicable-agents` NIT, etc.). | Override audit rules (`@register_override`, `src/.../audit_catalog.py:L119-L138`). | Not audited by `audit_catalog`. | Not audited by `audit_catalog`. |

**The one-line decision rule for authors:** *Always-true and unconditional → CLAUDE.md. Scenario-scoped guidance paragraph → context. A full reusable procedure → skill. A hard routing rule → override. A learned cross-conversation fact → memory.*

**Rationale:** This mirrors the ecosystem's own converged split (always-on rules vs glob-scoped instructions vs full procedures — `docs/research/2026-06-07-context-injection-prior-art.md:L84,L186-L188,L312`) and keeps each wayfinder mechanism single-purpose, which is the catalog-design value in `docs/design.md:L39-L51`.

### OQ5 — Budget / size discipline + multi-match policy

**Decision:**
1. **Top-N cap:** inject at most **3 context units per dispatch turn**, selected by score descending (ties broken by name, matching the existing sort key `(-score, name)` at `src/claude_wayfinder/match/_main.py:L217`). This mirrors the existing `skills[:3]` cap (`_MAX_SKILLS = 3`, `src/claude_wayfinder/match/_match.py:L68`; `docs/schema.md:L124`).
2. **Dedup:** deduplicate by entry `name` before applying the cap (an entry that matches on multiple trigger axes is one unit, counted once).
3. **Per-unit size NIT:** a **catalog-build-time** audit NIT, not silent runtime truncation. Recommended bounds: body ≥ 1 sentence (trivial content is noise) and ≤ ~150 lines / ~600 tokens.
4. **Combined-turn budget guidance:** ~2,000 tokens combined across the (≤3) injected units — authoring guidance, surfaced as a NIT if the catalog can compute it, not a hard runtime cap.
5. **Over-cap warning:** when more than N units match and the cap drops some, emit a `[DISPATCH WARNING]` to stderr (consistent with the existing stale-mtime warning behavior in `docs/design.md:L49,L111`).

**Rationale:**
- No surveyed system caps the **number** of injected units — Cursor, Copilot, and AGENTS.md all inject every match (`docs/research/2026-06-07-context-injection-prior-art.md:L292-L297`). The top-N cap is **original design work** for wayfinder (`docs/research/2026-06-07-context-injection-prior-art.md:L320`); N=3 is chosen for consistency with the `skills` cap, not borrowed.
- Size bounds are bracketed by external benchmarks: Cursor community 2,000-token combined / 500-line-per-rule, Copilot hard 4,000-char per file, Aider's "model deprioritizes rules past ~150–200 lines" attention finding (`docs/research/2026-06-07-context-injection-prior-art.md:L55,L81,L253-L256,L299`). A context unit is a *note*, not a procedure, so the per-unit cap sits well below the procedure-sized 500-line number — ~150 lines is the attention-degradation threshold, which is the binding constraint for a note.
- **Build-time NIT over runtime truncation:** Copilot's and AGENTS.md's silent truncation (4,000 char / 32 KiB) is explicitly called a footgun — authors don't know content was dropped (`docs/research/2026-06-07-context-injection-prior-art.md:L86,L295`). Wayfinder surfaces the cap at build time instead.

**Rejected:** inject-all (noise, no prior-art cap to lean on); runtime truncation (silent-drop footgun).

### OQ6 — Authoring & audit surface

**Decision:**
1. **New audit-catalog rules** for `kind="context"` entries, registered via the existing `@register` decorator (`src/claude_wayfinder/audit_catalog.py:L93-L108`):
   - **empty-applicable-targets** — a context entry whose attach-target field is empty and has no intentional-rationale escape hatch (parallel to the skill `empty-applicable-agents` NIT, `docs/schema.md:L39`). Applies only if contexts carry an `applicable_*` field (see § 9 sign-off item).
   - **unused-trigger** — a context entry with zero populated trigger sub-fields can never fire; flag it.
   - **over-broad-trigger / conflict-pair** — two context entries with identical or subsuming trigger sets that would always co-inject; flag as a conflict pair (parallel to existing conflict-pair audit for skills).
   - **size NIT** (OQ5) — body outside the ≥1-sentence / ≤~150-line bounds.
2. **New `docs/dispatch-authoring-guide.md` section** — "Authoring context units": when to choose context vs skill vs override vs memory vs CLAUDE.md (the OQ4 table + the one-line rule), trigger-selection guidance (reuse the existing trigger vocab; prefer `path_globs` / `keyword_groups` for precise scoping), the size discipline (OQ5), and the `injected_context` payload shape (§ 4).

**Rationale:** The audit registries are already extensible by decorator and are the established place for catalog-quality rules (`src/claude_wayfinder/audit_catalog.py:L86-L138`). The research report calls out that no ecosystem system specifies conflict/precedence semantics for same-turn multi-unit injection — Cursor's is "undefined in practice" (`docs/research/2026-06-07-context-injection-prior-art.md:L322`) — so the conflict-pair and dedup rules are original and worth encoding. Authoring guidance is the only over-injection lever the ecosystem actually uses besides hard caps (`docs/research/2026-06-07-context-injection-prior-art.md:L293-L294`).

---

## 4. Schema delta

### 4a. Catalog entry — `kind = "context"`

A context entry reuses the `CatalogEntry` dataclass (`src/claude_wayfinder/match/_types.py:L178-L206`) with `kind="context"`. Field disposition:

| Field | Context entry | Notes |
|---|---|---|
| `name` | required, unique | As for all kinds. |
| `kind` | `"context"` | New third value. `kind` is a free string today, not enum-validated at load (`_types.py:L200`) — so loading is forward-compatible, but `_validate.py` should learn the new kind (change site § 5). |
| `description` | required (may be empty) | Human-readable audit label explaining *when* the unit fires (the research report's one borrow from Cursor "Agent Requested": description as a human triage signal, not a trigger — `docs/research/2026-06-07-context-injection-prior-art.md:L136`). |
| `source` | required, default `"owned"` | Same provenance table as agents/skills (`docs/schema.md:L43-L53`). |
| `triggers` | required | Reuses the existing `Triggers` object unchanged (`_types.py:L170-L175`; `docs/schema.md:L59-L67`). |
| `applicable_agents` / `applicable_skills` | **see § 9 sign-off** | Whether contexts attach to a winning agent (like skills) or ride free on the decision is the open sign-off item. Recommended default: **ride free** (no `applicable_*` filter) — the injection is decision-level, not agent-scoped. |
| `routable` | absent | Contexts are never delegation targets. |
| **`body` / file convention** | the injected text | The full paragraph(s). **File convention (recommended):** a `contexts/<name>/CONTEXT.md` body + a `triggers.yml` sidecar, mirroring the `skills/<name>/SKILL.md` + `triggers.yml` layout the catalog builder already scans (internals map; `docs/schema.md:L15`). The catalog stores a `description` summary; the full `body` is the injected payload. This is the two-tier progressive-disclosure model the research report recommends (`docs/research/2026-06-07-context-injection-prior-art.md:L161,L299`). |

> `unverified:` the exact in-catalog storage of the context body (inline `body` field on the entry vs a resolved file path the router reads) is an implementation choice not yet settled; the recommended default is an inline `body` string on the catalog entry so the decision payload is self-contained and auditable without a second file read. Confirm during implementation.

### 4b. Decision payload — `injected_context` field

A new **optional** common field, present on any decision when one or more context units fired (non-breaking per `docs/schema.md:L23`):

```jsonc
{
  "decision": "delegate",
  "agent": "code-writer",
  "skills": ["python"],
  "confidence": 0.92,
  "rationale": "matched keywords: implement.",
  "alternatives": [],
  "disposition_source": "scored",
  "injected_context": [
    {
      "name": "api-problem-envelope",
      "score": 0.4,
      "body": "Error responses in this layer always use the `Problem` envelope ...",
      "matched_via": ["path_globs"]
    }
  ],
  "injected_context_count": 1
}
```

- `injected_context`: `array[object]`, omitted or `[]` when nothing matched. Each element: `name` (string), `score` (number), `body` (string — the injected paragraph), `matched_via` (array of trigger-axis names, for audit). Ordered by score desc, ties by name; length ≤ 3 (OQ5).
- `injected_context_count`: `integer` (OQ3 telemetry convenience), `0` when none.

Both are additive optional fields → schema-compatible without a `schema_version` bump (`docs/schema.md:L23`). They are captured in the drift log automatically (`src/claude_wayfinder/match/_catalog.py:L364-L365`).

---

## 5. Change-site checklist (for splitting into follow-up issues)

Grouped by subsystem; each references the verified change site.

**Catalog builder** (`build_catalog/`)
- `_discover.py` — scan a new `contexts/*/CONTEXT.md` + `triggers.yml` source path (parallel to the skill scan); apply the same `source` tagging and plugin/builtin cascade.
- `_process.py` — `_resolve_applicable_references()` (~L103-L115) is a two-way `kind` branch; extend for `kind="context"` (or no-op if contexts ride free per § 9).
- `_main.py` — entry sort by `(kind, name)` (~L167-L200) already handles a new kind value generically; verify ordering.
- `_validate.py` — teach per-kind validation about `kind="context"` (today `kind` is unvalidated at load — `_types.py:L200`).

**Matcher** (`match/`)
- `_main.py` (~L206-L222) **and** `_match.py` `score_entries()` (L485-L499) — add a third `context_entries` / `scored_contexts` pool alongside the agent/skill pools. `score()` itself is unchanged (kind-agnostic — `docs/schema.md:L253-L267`).
- `_match.py` — add a `_contexts_for_decision()` helper (parallel to `_skills_for_agent()`, L433-L461): filter `scored_contexts` by `_CONTEXT_MIN = 0.25`, dedup by name, cap at top-3.
- `_match.py` — add the `_CONTEXT_MIN` constant near `_SKILL_MIN` (L65).

**Decision ladder** (`match/_decide.py`)
- `decide()` (L154-L287) — **do not modify the branch conditions.** After the chosen branch builds its result dict, merge in `injected_context` / `injected_context_count` from the context pool. Cleanest implementation: compute the context list once before the ladder and attach it to whichever dict is returned (a small post-processing wrapper, or pass it into each return).

**Filters** (`match/match_filters.py`)
- Decide whether contexts need a `is_context_injectable()` gate analogous to `is_agent_routable()` (~L24-L91). Recommended: contexts are always injectable (no routable-like exclusion), so no new filter unless plugin-context dormancy is wanted (mirror plugin-agent exclusion if so — § 9).

**Audit** (`audit_catalog.py`)
- Register the OQ6 rules via `@register` (L93-L108): empty-applicable-targets (if `applicable_*` adopted), unused-trigger, conflict-pair / over-broad-trigger, size NIT.

**Telemetry** (`match/_catalog.py` + `_health.py`)
- No change required for the drift log — `_write_log_entry` serializes the full `output_dict` (L360-L368). Optional follow-up: a `_health.py` "context injection rate" metric reading `injected_context_count`.

**Overrides** (`match/_overrides.py`, `_types.py` `OverrideRule`)
- Optional: add an `injected_context` field to `OverrideRule` (L281-L315) if an override should be able to set context verbatim. Lower priority; predicate eval is already kind-agnostic.

**Docs** — see § 7.

---

## 6. Follow-up issue breakdown (propose only — do NOT file here)

Proposed implementation issues under Milestone #13. Titles + one-line scope:

1. **Catalog builder: discover and emit `kind="context"` entries** — scan `contexts/*/` source path, tag `source`, store `description` + `body`; teach `_validate.py` the new kind.
2. **Matcher: add the `scored_contexts` pool and `_CONTEXT_MIN` threshold** — third scored pool in `_main.py` / `score_entries()`; `_contexts_for_decision()` helper with dedup + top-3 cap.
3. **Decision payload: attach `injected_context` / `injected_context_count`** — merge context list into every `decide()` return without changing branch conditions; update `docs/schema.md`.
4. **Audit rules for context entries** — unused-trigger, conflict-pair / over-broad-trigger, empty-applicable-targets (if adopted), size NIT; register via `@register`.
5. **Authoring surface: `contexts/` file convention + dispatch-authoring-guide section** — document the file layout, the OQ4 delineation table, trigger selection, and size discipline.
6. **(Optional / later) Health metric: context injection rate** — `_health.py` reads `injected_context_count` from the drift log.
7. **(Deferred) Hook-injected context (OQ1 option c)** — `UserPromptSubmit` injection path; tracked with the deferred v0.3 pre-cognitive hook, not built now.

Recommended ordering: 1 → 2 → 3 are the critical path (builder → pool → payload); 4 and 5 follow; 6 and 7 are optional/deferred.

---

## 7. Doc-update needs

- **`docs/schema.md`** — § 1 `kind` field: add `"context"` as a valid value (currently "`agent` or `skill`", L34). § 3 common fields: add `injected_context` (array) and `injected_context_count` (integer) as new optional fields, noting they are additive/non-breaking (L107-L116, L23). Possibly a short § 1 note on the context-entry shape (body / file convention).
- **`docs/design.md`** — add a "Why a third kind (context)" rationale subsection (parallel to "Why 7 decisions" / "Why discrete weights"), recording the first-class-over-size-band decision and the AGENTS.md/SKILL.md precedent.
- **`docs/dispatch-authoring-guide.md`** — new "Authoring context units" section (OQ6): the delineation table, the one-line decision rule, trigger-selection guidance, size discipline, the `injected_context` payload shape.
- **`docs/design/trigger-schema.md`** — confirm the trigger vocab section is described as kind-agnostic (it is — `docs/schema.md:L59-L67`); add a note that contexts reuse it, and whether a new `applicable_*` field applies (§ 9).
- **`README.md`** — if `/dispatch` output examples or the "what is a dispatch context" section change, update; add a one-line mention of context units in the feature overview.

---

## 8. Quality / scope check

- Open Questions 1–6 each have a recorded decision (§ 3). ✓
- Schema delta defines both the catalog entry and the decision payload (§ 4). ✓
- Delineation table vs skills / overrides / memory / CLAUDE.md present (OQ4). ✓
- Change sites cite verified file:symbol locations (§ 5). ✓
- Follow-up issues proposed, not filed (§ 6). ✓
- Doc-update needs identified (§ 7). ✓
- In scope: design only — no matcher/catalog/hook code written. Out of scope (per #315): implementation, the v0.3 pre-cognitive hook itself, scoring changes beyond a parallel context pool. ✓

---

## 9. Open decisions for user sign-off

These are defaults I chose where the decision is genuinely the user's; each has a recommended default + rationale, and none blocks the design.

1. **Injection mechanism for v1 = payload field (OQ1 option a).** Recommended; defer the hook (option c). *Confirm you want the auditable payload-field mechanism first, not the more-automatic hook.*
2. **Top-N cap = 3 (OQ5).** Chosen for consistency with the `skills[:3]` cap; the research report recommends the same and flags it as original (no prior-art number). *Confirm 3, or pick another N.*
3. **Per-unit size cap ≈ 150 lines / ~600 tokens; combined-turn ≈ 2,000 tokens (OQ5).** Bracketed by Cursor/Copilot/Aider benchmarks, enforced as a build-time NIT not runtime truncation. *Confirm the numbers and the NIT-not-truncation stance.*
4. **Context units ride free vs attach to a winning agent (OQ3 / § 4a).** Recommended default: **ride free** — injection is decision-level, attached to any decision, with no `applicable_agents`-style filter. The alternative (attach to the winning agent like skills via `applicable_agents`) would scope context to a delegation target and reuse the skill-propagation machinery, but loses the "inject regardless of whether any agent matched" property #315 § Design Direction asks for. *Confirm ride-free, or opt into agent-scoped attachment.*
5. **Activation threshold `_CONTEXT_MIN = 0.25` (OQ2).** Lower than skills' `0.5` so a single path-glob match (0.4) fires. *Confirm the lower floor, or keep parity with skills at 0.5.*
6. **Plugin-context dormancy (§ 5 filters).** Should plugin-provided context units land dormant like plugin agents/skills (inert unless activated by an override sidecar — `docs/schema.md:L50`), or be injectable on install? Recommended default: **dormant**, for parity with plugin agents/skills. *Confirm.*
