# Deterministic-First Router Design — v5

**Status:** Final design  
**Date:** 2026-04-30  
**Implemented in:** `src/claude_wayfinder/match.py`, `src/claude_wayfinder/build_catalog.py`, `src/claude_wayfinder/health.py`

---

## 1. Problem statement

A conventional LLM router enforces routing policy through prose instructions read at every decision point. Empirical analysis of production transcripts shows this approach failing measurably:

- **Self-handle drift** — the router edits code directly instead of delegating.
- **Skill-pass failures** — the router rarely passes the correct skills to the delegate agent.
- **Advisor-consultation drift** — the router bypasses advisory invocations at very high rates.
- **High-ambiguity recognitions** with multiple dropout points per request.

The drift hotspots are overwhelmingly **mechanical** — small edits to known file types, not ambiguous multi-intent requests. The prose-policy mechanism is not the right tool for these cases.

The design problem this document addresses: **how to replace prose-policy routing with a deterministic mechanism whose behavior can be observed for compliance with its specification.**

---

## 2. Design philosophy

Three principles drive the design.

### Principle 1 — Guide delegations to the right agent and skills

When the router delegates to a sub-agent, replace probabilistic LLM-scan-of-prose-descriptions with deterministic match-over-frontmatter-triggers. The matcher computes the agent and skills from structured data per a documented algorithm.

### Principle 2 — Guide self-handles to the right skills

When the router self-handles substantive work, use the same deterministic matcher to recommend skills. The router activates the recommended skills before proceeding. Whether the router then consults the activated skill content is outside the design's reach (see §8 residuals).

### Principle 3 — Deterministic quality observed; outcome judgment user-owned

Two distinct claims that this design separates explicitly:

**Empirical evidence claim (testable pre-ship):**

> The system behaves deterministically as specified. Given the same dispatch context and catalog, the matcher returns the same decision. Frontmatter triggers fire as documented. Drift events emit when their producer conditions are met. Catalog generation succeeds or visibly fails.

This is falsifiable — specific tests can fail (catalog stability replay, trigger smoke tests, bypass detection on synthetic inputs). When the design ships, this claim has been tested.

**Iteration loop signal (post-ship, user-judgment):**

> Drift counts going up or down after trigger/weight updates is signal the operator reads to judge whether their iteration is moving the system in a desired direction.

The design does NOT make a measurement-based claim that drift-going-down means "better routing outcomes." Drift trends are signal the operator interprets qualitatively. Concluding "this trigger update reduced spurious bypasses" is an outcome-quality judgment by the operator, not a measurement-based claim by the design.

The design ships a tool that enables outcome-quality user judgments by inspecting drift signal. It does not itself claim to measure outcome quality. Drift trends are observation, not evidence. The operator owns the interpretation.

### What this philosophy is NOT

- Not enforcement.
- Not auto-correction.
- Not silent.
- Not a measurement-based claim of improvement over baseline.

---

## 3. Architecture — three layers

### Layer 1 — Dispatch skill (primary, deliberate, deterministic match)

The dispatch skill instructs the router to invoke it before any substantive task.

- **`src/claude_wayfinder/match.py`** — pure-Python matcher. Reads catalog, takes context as JSON stdin, returns `{decision, agent?, skills?, confidence, rationale, alternatives}` as JSON stdout.
- **Catalog** at a configurable path (default: `dispatch-catalog.json`), regenerated at session start and when any skill or agent file's mtime is newer than the catalog (§3.4).

#### 3.1.1 Trigger schema

```yaml
triggers:
  command_prefixes: []
  agent_mentions: []
  path_globs: []
  keywords:
    - { term: "...", weight: 0.25 | 0.5 | 1.0 }
  tool_mentions: []
  excludes: []
applicable_agents: []   # for skill entries
applicable_skills: []   # for agent entries
```

Full field reference: `docs/design/trigger-schema.md`.

#### 3.1.2 Per-entry scoring

```python
def score(entry, features):
    if features.command_prefix in entry.triggers.command_prefixes:
        return 1.0
    if any(m in features.agent_mentions for m in entry.triggers.agent_mentions):
        return 1.0
    if any(x in features.keywords for x in entry.triggers.excludes):
        return 0.0

    s = 0.0
    s += 0.4 * matched_glob_count(entry, features)
    s += sum(0.3 * k.weight for k in entry.triggers.keywords if k.term in features.keywords)
    s += 0.5 * len([t for t in entry.triggers.tool_mentions if t in features.tool_mentions])
    return min(s, 1.0)
```

#### 3.1.3 Decision composition

```python
def decide(scored_agents, scored_skills, features):
    if feature_count(features) < 2:
        return {"decision": "needs_more_detail", ...}

    best_agent = scored_agents[0] if scored_agents else None
    best_skills = [s for s in scored_skills if s.score >= 0.5][:3]

    if best_agent and best_agent.score >= 0.85 and gap(scored_agents) >= 0.2:
        return {"decision": "delegate", "agent": best_agent.name,
                "skills": skills_for_agent(best_agent, features), ...}

    if best_agent and best_agent.score >= 0.5 and gap(scored_agents) < 0.2:
        return {"decision": "ambiguous", "candidates": top_3_agents, ...}

    if best_skills:
        return {"decision": "self_handle", "skills": [s.name for s in best_skills], ...}

    if best_agent and best_agent.score >= 0.5:
        return {"decision": "advisory", "agent": best_agent.name,
                "skills": skills_for_agent(best_agent, features), ...}

    return {"decision": "self_handle_unaided", ...}
```

The router agent itself is excluded from the scored agents pool.

#### 3.1.4 Decision ladder

| Decision              | When                                               | Router action                                   |
| --------------------- | -------------------------------------------------- | ----------------------------------------------- |
| `delegate`            | Best non-router agent ≥ 0.85, gap ≥ 0.2            | Compose Agent call with returned agent + skills |
| `self_handle`         | No agent dominates; ≥1 skill ≥ 0.5                 | Activate skills via Skill tool; proceed         |
| `self_handle_unaided` | Sufficient context, no specialist or skill applies | Proceed without delegation or skill activation  |
| `advisory`            | Best agent ≥ 0.5, gap ≥ 0.2; no strong skill path  | Use, note uncertainty; override is drift        |
| `ambiguous`           | Two or more agents tie above 0.5                   | Ask user, choose from candidates                |
| `ask_user`            | (Reserved)                                         | Ask user; do not guess                          |
| `needs_more_detail`   | Feature density < 2                                | Recompose with explicit signals; retry          |

#### 3.1.5 `excludes` schema and cross-skill validation

`excludes` is a hard zero-out per entry. Catalog-generation-time dead-zone detection emits warnings (not CI failures) when an exclude term never affects a decision against the captured corpus. The operator reviews and decides.

#### 3.1.6 Catalog integrity — fail-loud-where-possible

When the catalog is degraded (empty, missing, or more than 25% of entries excluded due to validation failures), the system surfaces this through multiple channels:

- **At session start:** the catalog generator validates schema. Missing or malformed entries are excluded with per-entry warnings in the catalog log.
- **Catalog degraded banner:** when the catalog is degraded, a `[CATALOG ERROR]` notice is emitted with details. Until restored, routing falls back to LLM judgment.
- **At dispatch invocation time:** if the catalog is degraded, `match.py` exits non-zero with a banner on stderr.

**Honest visibility analysis:**

| Surface               | What it requires                                                                                                        |
| --------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| Session banner        | LLM consumes and relays; relay is router compliance (same class as the policy this design replaces)                     |
| `match.py` stderr     | Read by the dispatch skill body; relay to the user is router compliance                                                 |
| Catalog generation log | User-initiated polling; never pushed                                                                                    |

None of these surfaces reaches the operator without router relay or operator polling. This is honest residual (§8). The design provides multiple surfaces to increase the probability of detection, but does not claim the catalog-degraded state is unmissable.

What v5 commits to: **the operator can detect catalog degradation by running the health checker, which directly reads the catalog file and reports.** That is one router-independent path the operator controls.

#### 3.1.7 Where the matcher fires

The matcher fires on **the dispatch context the router constructs**, not on the user's entry prompt.

#### 3.1.8 Router workflow

1. Router identifies a substantive task.
2. Router invokes the `dispatch` skill.
3. Skill body composes JSON context and runs `match.py`.
4. Matcher returns one of seven decisions OR exits with banner if catalog degraded.
5. Router acts on the decision.

### Layer 2 — Floor hook (PreToolUse on Agent, observation-only)

#### 3.2.1 Positional pairing

The hook scans conversation history backwards from the current Agent tool call:

1. Find the most recent dispatch skill invocation.
2. Count Agent tool invocations between that dispatch and now.
3. Count all non-Skill tool invocations between (no "trivial Read" exclusion).
4. Decision:
   - `count_Agent ≥ 1` → `bypass` drift logged.
   - `count_Agent = 0 AND count_other ≤ STALENESS_BOUND` → no drift.
   - `count_Agent = 0 AND count_other > STALENESS_BOUND` → `stale_dispatch` drift logged.
   - No dispatch invocation in this session at all → `bypass` drift logged.

**STALENESS_BOUND is provisional at 15.** Calibration plan: during the first 4–8 weeks post-launch, accumulate the distribution of `count_other` values for legitimate covered Agent calls and tune to the 90th percentile. Until calibrated, `stale_dispatch` events are advisory-only — surfaced in the health report but the action threshold treats them as informational rather than actionable.

#### 3.2.2 Hook actions

- If positional pairing fails: log drift with `{type, agent, prompt_excerpt, count_Agent, count_other}`.
- If positional pairing passes: no-op.
- Never blocks. Never augments.

### Layer 3 — Drift telemetry (Stop hook + drift log)

#### 3.3.1 Drift event types and producers

| Event type                       | Producer          | Detection method                                                                                                               |
| -------------------------------- | ----------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `bypass`                         | Floor hook        | Positional pairing fail with count_other ≤ STALENESS_BOUND                                                                    |
| `stale_dispatch`                 | Floor hook        | count_Agent = 0 but count_other > STALENESS_BOUND                                                                             |
| `advisory_override`              | Stop hook scanner | Dispatch returned `advisory`; next Agent call's target mismatches returned agent                                               |
| `self_handle_unaided_invocation` | Stop hook scanner | Count `self_handle_unaided` decisions per session                                                                             |
| `needs_more_detail_repeat`       | Stop hook scanner | Dispatch returned `needs_more_detail`; next dispatch has same agent target with no changes                                     |
| `catalog_degraded_session`       | Stop hook scanner | Count sessions where catalog-degraded banner appeared in context                                                               |

#### 3.3.2 Stop hook scanner

A script (~120 lines) scans the just-completed turn's events and emits drift events per the §3.3.1 table. Output to the drift log (`router-drift.jsonl`).

#### 3.3.3 Drift action thresholds, owner, cadence

- **Owner:** the operator.
- **Cadence:** drift summary appears in the session recap. The health checker (`src/claude_wayfinder/health.py`) provides an on-demand full report.
- **Trigger:** operator reads report and decides.

| Drift type                       | Action threshold                                                                     | Operator decides whether to                                                  |
| -------------------------------- | ------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------- |
| `bypass`                         | ≥ 5 events with same agent type in 7 days                                            | Investigate skipped dispatch for this agent class                            |
| `stale_dispatch`                 | ≥ 3 events in 7 days **(advisory-only until STALENESS_BOUND calibrated, see §3.2.1)** | Tune STALENESS_BOUND or improve dispatch cadence                             |
| `advisory_override`              | ≥ 3 events with same router-vs-catalog choice in 7 days                              | Adjust matcher weights or trigger schema                                     |
| `self_handle_unaided_invocation` | ≥ 10 events in 7 days                                                                | Investigate skill catalog coverage gaps; check for steering patterns         |
| `needs_more_detail_repeat`       | ≥ 3 events in 7 days                                                                 | Tune feature-density threshold or improve router prompt-composition guidance |
| `catalog_degraded_session`       | ≥ 1 ever                                                                             | Immediate action — fix catalog                                               |

#### 3.3.4 Compliance and integrity — pre-ship invariants vs runtime telemetry

Two metric classes, separated explicitly:

**Pre-ship CI invariants** (computed at catalog generation time and on PRs; not from runtime logs):

| Invariant                  | Tests claim                                                             | Pass condition                                                                   |
| -------------------------- | ----------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| **Catalog stability**      | "Catalog generation is deterministic"                                   | Run generator twice on unchanged source; compare output byte-for-byte; identical |
| **Trigger-rule firing accuracy** | "Frontmatter triggers fire on inputs that match their declared signals" | Per-skill smoke test inputs produce expected matches                         |
| **Schema validation**      | "Every cataloged entry has parseable triggers"                          | Generator exits 0 with no per-entry warnings on PRs touching skill/agent frontmatter |

The catalog stability test is meaningfully different from a matcher determinism replay: catalog generation is NOT a pure function — it traverses the filesystem, parses YAML, applies inverse-index logic, and possibly performs dependency ordering. Real failure modes include hash-based ordering producing different results across runs, file-iteration order differing on different filesystems, and YAML parser quirks. This is a real falsifier.

**Runtime telemetry** (computed by `src/claude_wayfinder/health.py` from drift log + dispatch log):

| Metric                   | Tests claim                                      | Healthy range (starting hypothesis) |
| ------------------------ | ------------------------------------------------ | ----------------------------------- |
| Dispatch invocation rate | Router invokes dispatch before substantive tasks | ≥ 80%                               |
| Bypass rate              | When dispatch invoked, Agent calls follow        | ≤ 10%                               |
| Advisory override rate   | Router either accepts advisory or escalates      | ≤ 30%                               |
| Catalog availability     | Catalog loads and validates                      | ≥ 99%                               |

The split makes clear what is verified before shipping (CI invariants — must pass for release) versus what is observed at runtime (telemetry — informs iteration).

### 3.4 — Catalog lifecycle

v4 of this design specified a directory-level mtime check. Directory mtime updates on add/remove/rename only — NOT on in-place file edits. Editing existing skill frontmatter (the most common authoring case) would not trigger a refresh. v5 fixes this by going file-level.

The catalog regenerates at:

1. **Session start** (full generation with schema validation).
2. **On each new user prompt:**
   - Find the newest mtime among all skill and agent files (not directories).
   - If newest file mtime > catalog file mtime, regenerate before processing the prompt.
   - Cost: ~30–50 stat() calls per prompt. Total overhead: <5ms typically.
3. **On-demand:** the refresh command — reports skill/agent counts, mtime delta, and any exclusion warnings from the catalog log.

**Coverage:**

- **In-place edits to existing skill files** — caught by file-level mtime-check (missed by the directory-level approach).
- **In-session skill creation** — caught.
- **Plugin install** — caught (new files, new mtimes).
- **External `git pull`** — caught.
- **IDE saves** — caught (saves update file mtimes).

---

## 4. Slim router prompt — instruction load

~6 router-prompt recognitions + ~5 skill-body recognitions per substantive task. The win is dropout-rate reduction (recognitions are mostly binary or deterministic-to-action), not raw count reduction.

---

## 5. Router prompt content

```
=== Routing ===

For any substantive task — whether you will delegate to a sub-agent
or handle it yourself — invoke the dispatch skill before acting.

  1. Decide whether the task is substantive.
  2. Compose a concrete task description: include file paths, extensions,
     intent keywords, and tool mentions wherever they apply.
  3. Invoke the dispatch skill with that description.
  4. Act on the matcher's 7-way decision.

=== Division of labor ===

Skill selection is NOT your job. The dispatch skill computes it.
You select the specialist target (or recognize self-handle is correct)
and compose a concrete prompt; the matcher resolves skills from the catalog.

You should NOT scan skill descriptions trying to find matches.
That work has moved out of your prompt.

=== Drift feedback ===

The system observes you. Bypassing dispatch, overriding advisory
decisions, and operating in self_handle_unaided mode at high rates
are all logged. The drift log is reviewed by the operator.

=== Catalog failures ===

If the catalog is degraded, you will see a [CATALOG ERROR] banner in
your context. The dispatch skill will return error text on stderr.
Surface the banner to the user verbatim. The operator can also
verify catalog state directly via the health checker.
```

---

## 6. Decision rules for the router

| Decision              | Router action                                                                                                              |
| --------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| `delegate`            | Compose Agent call with returned `agent`. If `skills` is non-empty, propagate them into the sub-agent's prompt (see §6.1). |
| `self_handle`         | Activate returned skills via Skill tool; proceed.                                                                          |
| `self_handle_unaided` | Proceed without delegation or skill activation.                                                                            |
| `advisory`            | Same as `delegate`, but note uncertainty in the audit line. Override is drift.                                             |
| `ambiguous`           | Ask user, choose from candidates.                                                                                          |
| `ask_user`            | (Reserved.)                                                                                                                |
| `needs_more_detail`   | Recompose with explicit signals; retry.                                                                                    |

### 6.1 Skill propagation on delegate and advisory paths

When the matcher returns `delegate` or `advisory` with a non-empty `skills` array, the router must propagate those skills into the sub-agent's prompt body. The Agent tool has no native `skills` parameter — the only operative mechanism is an instruction block in the prompt text.

The router appends the following block to the sub-agent's prompt whenever `skills` is non-empty:

> Apply these skills as you work: \<skill-1\>, \<skill-2\>, ... Invoke each via the Skill tool at the relevant phase. The matcher resolved these from the dispatch catalog; they are part of the routing decision, not optional flavor.

**No-op rule:** if `skills` is `[]`, the block is omitted entirely.

**Sub-agent applicability:** the matcher filters candidate skills by each agent's `applicable_skills` frontmatter before returning them, so any skill on a `delegate` result is already cleared for the target agent. The router does not need to re-check.

---

## 7. Drift handling and rule iteration

The loop:

1. **Capture:** Drift events emit per §3.3.1 to the drift log.
2. **Surface:** Session recap shows recent drift summary; the health checker provides a full report on demand.
3. **Threshold:** Per §3.3.3, when a drift type's count exceeds its threshold, the operator reads the report and decides.
4. **Action:** Operator updates triggers, weights, or thresholds.
5. **Verify:** Subsequent drift counts are signal the operator reads to judge whether iteration is moving in a desired direction. **The design does not claim this is an outcome-quality measurement.** Drift counts going down after an update is signal of "fewer of this drift type now" — whether that means "better routing outcomes" is the operator's judgment, not the design's claim.

Compared to prose-policy iteration, the wins (none of which are outcome-quality claims):

- Centralized data (catalog) instead of distributed prose.
- Deterministic matching instead of LLM scanning (per Principle 3 empirical claim).
- Audit trail per dispatch.
- Pre-ship CI invariants verifying deterministic correctness.
- Defined drift action thresholds.

---

## 8. What this design does not solve

Honest residuals:

- **Direct edits by the router.** No mechanism prevents a router that decides to edit code without delegating. Visible only via manual transcript review.
- **Multi-task chains without re-dispatching.** Layer 2 flags drift; operator reviews.
- **Skill consultation by the router after activation.** Whether the router consults activated skill content is interpretation by the same LLM. Not measured. Not claimed.
- **Outcome quality.** Not measured. Not claimed. Drift trends are operator-interpreted signal, not measurement.
- **State-based skills.** Excluded from catalog (e.g. session-recap skills, brainstorming, context-switch detection).
- **Trivial-vs-substantive judgment by the router.** LLM judgment.
- **Catalog-degradation visibility independent of router relay.** §3.1.6 has multiple surfaces but all require either router relay or operator-initiated polling. The design increases the probability of detection via redundancy but does not guarantee router-independent surfacing.
- **Whether the router actually surfaced the `[CATALOG ERROR]` banner to the user.** Detection is not attempted. The health checker is the operator's independent verification path.

The design's empirical claim is narrow: the system behaves deterministically as specified. Everything not in that claim is residual.

---

## 9. Comparison with adjacent ecosystem projects

`claude-wayfinder` occupies a specific niche: deterministic routing for structured agent catalogs, with explicit drift telemetry. Adjacent work:

- **RAG-MCP** (arXiv 2505.03275): retrieval-augmented tool/agent selection over unstructured descriptions. Complementary; RAG-MCP targets discovery; this design targets routing once agents are known.
- **Tool-to-Agent Retrieval** (arXiv 2511.01854): embedding-based retrieval of agents. Probabilistic; this design is deterministic by design.
- **Patronus AI agent routing taxonomy**: classifies routing by decision type. `claude-wayfinder` maps to their "structured rule-based" class.
- Open-source routers (metaswarm, wshobson, superpowers, ruflo, and others): mostly prose-description-scan or embedding-based. None provide explicit drift telemetry.

---

## 10. Open questions

### 10.1 Tuning decisions (can ship without resolving)

- Initial scoring weights, confidence thresholds, action thresholds, healthy ranges. Starting values; tuned post-launch.
- STALENESS_BOUND = 15 — provisional pending Phase 1 calibration per §3.2.1.

### 10.2 Design unknowns — confirmed

| Item                                      | Resolution                                                                             |
| ----------------------------------------- | -------------------------------------------------------------------------------------- |
| Catalog failure modes                     | Fail-loud session banner + match.py stderr + health checker operator-controlled path   |
| Floor hook bypass definition              | Positional pairing with provisional STALENESS_BOUND = 15                               |
| Catalog version drift                     | File-level mtime-check on every user prompt                                            |
| Multi-skill ties above 0.85              | `ambiguous`                                                                            |
| `self_handle_unaided` for routine work    | Confirmed                                                                              |
| Drift action ownership                    | Operator reviews health checker                                                        |
| Empirical claim vs iteration loop tension | Separated explicitly per §2 Principle 3 / §7 step 5                                   |

### 10.3 Future work

- Recalibrate excludes against post-launch corpus after 4–8 weeks.
- `trivial_underjudged` detection (content classification).
- Schema extension for state-based skills.
- System-notification mechanism outside LLM context for catalog degradation (would close §8 residual).

---

## 11. Verification plan

### Happy path

1. Dispatch — high-confidence delegate.
2. Dispatch — feature-density check returns `needs_more_detail`.
3. Dispatch — ambiguous returns candidates.
4. Floor hook — covered Agent.
5. Floor hook — bypass.
6. Drift telemetry — events present.

### Interaction tests

7. Catalog generation failure — generator warns + excludes.
8. Catalog degraded at session start — banner emitted; match.py stderr; router instructed to surface.
9. Multi-intent ambiguity.
10. Sparse retry — `needs_more_detail` → recompose → match.
11. Excludes hard-zero — entry returns 0; warning emitted; CI passes.
12. Positional pairing covered Agent.
13. Positional pairing multi-Agent without re-dispatch.
14. Positional pairing pure bypass.
15. Self_handle happy — `.bicepparam` edit; skills activated.

### Residual + observability tests

16. Stale dispatch — count_other > 15 triggers `stale_dispatch`.
17. Self_handle_unaided routing — sufficient context, no specialist or skill applies.
18. Excludes dead-zone detection — generator warning emitted on synthetic input set.
19. Compliance metrics report — health checker against synthetic drift log; runtime metrics computed correctly.
20. **Catalog stability.** Run catalog generator twice on identical source files; compare byte-for-byte. Identical = pass. Detects nondeterministic generation (sort order, hash collisions, file iteration order).
21. **File-level mtime-check refresh.** Edit existing skill's frontmatter; submit user prompt; verify catalog regenerated and new triggers active.
22. **Pre-ship CI invariant suite.** Catalog stability + trigger-firing accuracy + schema validation all run in CI on PRs touching skill/agent frontmatter.

---

## 12. Net story in one sentence

> The redesign replaces probabilistic LLM-scan-of-prose-descriptions with deterministic match-over-frontmatter-triggers as the mechanism for selecting agents and skills, observes its actual behavior via positional-pairing bypass detection, advisory-override scanning, fail-loud catalog-degradation banners, and runtime compliance telemetry — verified pre-ship by catalog stability and trigger-firing CI invariants — and surfaces drift signal that the operator reads to judge iteration progress, without claiming that the design itself measures outcome quality.

---

## 13. Addendum: Plugin-Namespaced Skill References in `applicable_skills`

### Problem

The catalog generator's cross-reference pass compared every `applicable_skills` entry against the set of owned skill names discovered from the skill tree. Plugin-provided skills (e.g. `my-plugin:my-skill`) follow a `<plugin>:<skill>` naming convention and are NOT in the owned-skill scan. The resolver silently dropped them with a warning, so they never appeared in the output catalog.

### Decision: pass plugin-namespaced references through as external pointers

Any name matching `<plugin>:<skill>` (exactly one colon, non-empty segments on both sides) is treated as an external reference to a runtime-installed plugin skill. It cannot be verified at catalog-build time — the owned-skill scan does not walk plugin directories. The resolver keeps the name in the entry's `applicable_skills` list and emits an `info` log noting the bypass.

**Why not a hard error at build time?** A hard error would require all plugin skills referenced by an agent to have a corresponding trigger override file. That file is only needed when you want to override a plugin skill's trigger weights — it is NOT required for the skill to be available at runtime. Making it mandatory for every reference would create unnecessary authoring friction.

**Why is this safe?** The `applicable_skills` field is advisory — it tells the matcher which skills to attach when delegating to this agent. If a plugin skill is listed but not installed at runtime, the Skill tool simply won't find it. That is a runtime failure, not a build-time data-corruption issue, and the `info` log provides enough signal for the agent author to notice.

**Implementation:** `_is_plugin_namespaced()` helper and updated `_resolve_applicable_references()` in `src/claude_wayfinder/build_catalog.py`. Covered by tests in `tests/test_build_catalog.py`.
