# claude-wayfinder — Design Rationale

This document explains the WHY behind the core design choices in `claude-wayfinder`. It does not reproduce the algorithm (see [`docs/schema.md §4`](schema.md#4-scoring-and-decision-algorithm) for the normative spec) or the field reference (see [`docs/design/trigger-schema.md`](design/trigger-schema.md)). It records the rejected alternatives and the reasons each choice was made.

---

## Why deterministic-first

The alternative is LLM-judgment routing: the router agent reads prose `description:` fields from every skill and agent, then decides where to delegate using the same model that is about to do the work.

Empirical analysis of production transcripts identified a consistent failure pattern: the routing decision drifts silently. The same request routes differently across turns. Self-handle drift, skill-pass failures, and advisor-consultation failures are overwhelmingly mechanical — the model scans prose and makes a probabilistic call where a lookup would be exact.

Deterministic-first trades router-LLM flexibility for three properties:

1. **Auditability.** Every dispatch decision is a structured artifact. Given the same context and catalog, the matcher returns the same answer. Past decisions are replayable.
2. **Reproducibility.** The result does not depend on the model's probabilistic scan of prose. The catalog and context are the full input; the algorithm is the full computation.
3. **Token efficiency.** The matcher is a Python subprocess, not a model call. It does not consume context tokens for the routing step.

The design accepts a latency cost (~50–200ms per dispatch call) and a catalog-maintenance burden in exchange for these properties.

---

## Why post-cognitive

The alternative (Approach C in brainstorm issue #37) is a `UserPromptSubmit` hook that intercepts raw user prompts before the router agent processes them and attempts to route from that raw signal.

`claude-wayfinder` fires after the router agent has constructed a dispatch context — after the router has read the conversation, identified intent, extracted file paths and tools, and composed a structured description. That context is richer than the raw prompt:

- File paths mentioned or implied by the conversation are resolved and listed explicitly.
- Tool names are pulled from the user's stated intent, not guessed from raw text.
- Command prefix (slash command) is available as a clean discrete signal.

A raw-prompt hook routes against signal-poor input. The post-cognitive design routes against signal that has already been processed by the model. This is the same model — the win is not architectural cleverness, it is that the router's interpretation is more useful to the matcher than the user's raw words.

The deferred alternative (structured-signal `UserPromptSubmit` hook — regex-matched paths plus slash prefix, no NLP enrichment) is a legitimate future option tracked as a v0.3 candidate. It was deferred because the power-user audience that is v0.2's target constructs context themselves and derives no benefit from an automatic hook.

---

## Why catalog-based

The alternative is a hardcoded agent list: either a constant in `match.py` or a fixed set defined in the router agent's frontmatter.

Catalog-based means:

- Consumers register their own agents and skills without forking the matcher. The matcher is provider-agnostic; it works identically whether it is routing for the project author or a third-party consumer.
- The data-driven `routable: bool` exclusion gate (introduced in PR #20, tracked as issue #19) replaced the former hardcoded name check (`name == "general-purpose"`). Any router agent with any name declares itself non-routable via a single frontmatter field. This is the proof point that the catalog model works: the boundary between "router" and "routeable agent" is data, not code.
- Plugin agents land dormant in the catalog and are inert at dispatch time unless activated by a plugin-override sidecar. This lets the matcher include plugin skills in the catalog without requiring them to drive routing decisions they were not authored for.

The catalog-maintenance burden is real: consumers must keep the catalog fresh as their skills and agents evolve. The stale-mtime warning (added in the v0.2 dispatch skill, issue #40, PR #44) mitigates the worst case — a stale catalog that routes silently against outdated data — by surfacing a `[DISPATCH WARNING]` when any source file is newer than the catalog.

---

## Why 7 decisions

A coarser set — for example, just `delegate` and `self_handle` — would lose distinctions that are load-bearing for the router:

| Decision              | What it means                                                                                       |
| --------------------- | --------------------------------------------------------------------------------------------------- |
| `delegate`            | One agent scored ≥ 0.85 with a gap ≥ 0.2. High-confidence single winner. Compose Agent call.       |
| `self_handle`         | No dominant agent; at least one skill scored ≥ 0.5. Activate skills; proceed without delegating.   |
| `self_handle_unaided` | Sufficient context, but no specialist or skill applies. Proceed without delegation or activation.   |
| `advisory`            | An agent scored ≥ 0.5 but below the `delegate` threshold. Delegation is suggested, not certain.    |
| `mixed_content`       | Two or more agents clamp at 1.0 on path-disjoint lanes. Structural multi-agent task; split and delegate each lane. (Added v0.10.0 / #210. Supersedes `ambiguous`, which was merged into `advisory` per #209.) |
| `ask_user`            | Reserved. Does not fire in v0.1 or v0.2. Exists in `VALID_DECISIONS` for forward compatibility.    |
| `needs_more_detail`   | Feature density below threshold (< 2 populated input dimensions). Recompose context and retry.     |

Collapsing `advisory` into `delegate` would remove the uncertainty signal — the router would treat a 0.61-confidence suggestion the same as a 0.92-confidence match. Collapsing `self_handle` and `self_handle_unaided` would lose the distinction between "activate these skills" and "proceed without activation."

`ask_user` is reserved: it exists in the contract so consumers can write a forward-compatible handler today. The design space it occupies — explicitly requesting human input before proceeding, distinct from `mixed_content` (structural multi-lane task) and `needs_more_detail` (too little context) — is real but not implemented in the current decision ladder. Dropping it from the contract would require a breaking change to add it later.

---

## Why discrete weights {0.25, 0.5, 1.0}

The alternative is continuous weights in `[0, 1]`. Continuous weights invite over-tuning: authors adjust weights to three decimal places to match a specific observed corpus, and the resulting catalog entries are unreadable to anyone who wasn't present for the tuning session.

Three discrete tiers force a commitment:

- `1.0` — the skill's defining concept. Absence means the skill should not match.
- `0.5` — strong supporting term. Frequently co-occurs with the topic.
- `0.25` — weak hint. Included for recall, not precision.

With three values, the catalog is legible: a reader can look at an entry and predict its score against a given context without running the matcher.

**Footgun:** weights outside the set `{0.25, 0.5, 1.0}` are silently clamped to the nearest valid value at catalog build time. `0.75` becomes `1.0`. `0.4` becomes `0.5`. The generator emits a warning, but the entry is kept. Do not rely on intermediate values — they will not survive a catalog rebuild.

---

## Why fnmatch path globs need both `**/X` and bare `X`

The catalog generator matches path globs using Python's `fnmatch.fnmatch`. The critical consequence: `**` does not match across directory separators in `fnmatch`. The pattern `"**/*.py"` does not match the bare filename `"src/foo.py"` in a naive `fnmatch` call.

The generator addresses this by testing each glob against both the full path and the basename of the path. This means that to match a file that could appear at the repo root OR nested in a subdirectory, both forms are required:

```yaml
path_globs:
  - "**/*.toml"   # matches nested paths via full-path test
  - "*.toml"      # matches root-level files via basename test
```

The trade-off being accepted: ship with Python's standard-library `fnmatch` rather than a custom glob engine that handles both cases implicitly. `fnmatch` is the ecosystem default, avoids a dependency, and is well-understood. The two-form pattern is the explicit cost of that choice. `matched_glob_count` deduplicates, so both forms matching the same path does not double-count.

The field-level documentation for this footgun is in [`docs/design/trigger-schema.md §4`](design/trigger-schema.md#4-matching-rules).

---

## Trade-offs the design accepts

**(a) Latency cost.** Running the matcher adds approximately 50–200ms per dispatch call (Python subprocess start plus catalog parse). This is acceptable for the power-user audience, which values auditability over sub-millisecond dispatch. A future compiled binary would reduce this; deferred as issue #6.

**(b) Catalog-maintenance burden.** The catalog is a compiled artifact derived from skill sidecars and agent frontmatter. Consumers must rebuild it when skills or agents change. The file-level mtime check at every user prompt (introduced in v5 to fix the v4 directory-level-mtime gap) catches in-place edits, but rebuild is still the consumer's responsibility. Stale-mtime warnings mitigate silent drift; they do not eliminate the maintenance obligation.

**(c) Catalog-vs-source staleness window.** Between a source file change and the next catalog rebuild, routing decisions are based on stale data. The stale-mtime warning in the v0.2 dispatch skill (issue #40, PR #44) surfaces this as a `[DISPATCH WARNING]` on stderr. The window is bounded by the rebuild cadence the consumer configures (pre-commit hook, CI job, or manual).

**(d) Drift telemetry requires router relay.** None of the observability surfaces — session banner, `match.py` stderr, catalog generation log — reach the operator without either router relay or operator-initiated polling. The design increases detection probability via redundancy (multiple surfaces) but does not guarantee router-independent surfacing. The health checker (`src/claude_wayfinder/_health.py`) is the one operator-controlled path that does not require router relay.

---

## Non-goals

- **Bundled standalone binary** — deferred to issue #6.
- **Shipped router agent** — wayfinder is a matcher and telemetry plugin, not a routing agent. Consumers bring their own router agent definition.
- **`UserPromptSubmit` hook for automatic dispatch-context extraction** — deferred to v0.3; the power-user audience composes context themselves.
- **No bundled dispatch-shape enforcement.** Wayfinder ships the matcher and drift telemetry (issue #53). It does not ship hooks that enforce which agents are allowed to dispatch which other agents — that policy lives in the consumer's router agent definition. See [`docs/dispatch-discipline.md`](dispatch-discipline.md) for the four routing-shape rules the matcher implicitly assumes and consumer-side implementation pointers.
