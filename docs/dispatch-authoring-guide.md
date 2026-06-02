# Dispatch Authoring Guide — claude-wayfinder

## Purpose

> This guide is the long-form companion to the `claude-wayfinder:dispatch-authoring` skill. The skill is what the agent loads at runtime; this doc is what the human reads when they want examples.

The `dispatch-authoring` skill covers the rules and scoring math. This companion document turns those rules into concrete walkthroughs: a skill authored from scratch, a skill tuned to fix a stale entry, and an agent that mysteriously never gets dispatched. It also serves as the rule reference for `audit-catalog`, the catalog-wide static analysis CLI.

Read the bundled `dispatch-authoring` skill body first ([`skills/dispatch-authoring/SKILL.md`](../skills/dispatch-authoring/SKILL.md)) for the field definitions and scoring formula. Come here when you need worked examples, the complete audit-catalog rule table, or the exit-code contract.

---

## Schema reference

The trigger configuration for a skill or agent lives in a sidecar file (`triggers.yml`) placed next to the main file. Each field in the `triggers:` block contributes a different type of signal to the matcher's per-entry score.

For canonical field definitions — types, defaults, and schema stability guarantees — see [`docs/schema.md`](schema.md). For the design rationale behind why these fields exist and why they have the shapes they do, see [`docs/design/trigger-schema.md`](design/trigger-schema.md). The descriptions below are deliberately brief orientation notes, not duplicates of those references.

**`command_prefixes`** — Slash commands that hard-short-circuit the matcher to a score of `1.0` for this entry, bypassing all additive scoring. Use for entries that should win unconditionally when the user types a specific command. A prefix should start with `/`; a string without a leading slash will not match a user-typed `/foo` command.

**`agent_mentions`** — Agent names whose explicit appearance in the prompt hard-short-circuits to `1.0`, the same way `command_prefixes` does. For example, if your router has a `code-writer` agent and a user says "ask the code-writer agent", listing `code-writer` here ensures that agent wins unconditionally. Use for any agent that should win when the user names it directly.

**`path_globs`** — `fnmatch`-style globs matched against the `file_paths` dimension of the dispatch input. Each matched glob contributes `+0.4` to the running score. The most common footgun: `*.py` matches only top-level files under `fnmatch` semantics; use `**/*.py` for nested paths. See the `path-glob-footgun` rule in the audit-catalog section for the auto-detection version of this check.

**`keywords`** — A list of `{term, weight}` mappings. Valid weights are exactly `0.25`, `0.5`, and `1.0`. Each term found in the input's keyword set contributes `+0.5 × weight` to the score, capped at `1.0`. Terms are case-insensitive. Terms may not contain whitespace — the matcher tokenizes on whitespace, so a multi-word term will never match.

&nbsp;&nbsp;&nbsp;&nbsp;**Stemming (issue #304):** The matcher applies symmetric Porter2 (Snowball English) stemming. Catalog terms are stemmed at catalog-build time; input tokens are stemmed at dispatch time. Matching is stem-vs-stem, so `implementing` routes the same as `implement`, `refactored` the same as `refactor`, and `linting` the same as `lint`. You do **not** need to enumerate inflected forms — list the base form and stemming handles variants automatically.

&nbsp;&nbsp;&nbsp;&nbsp;**`no_stem` opt-out:** Add `no_stem: true` to any keyword entry to exempt that term from stemming. Use this for acronyms, product names, and CLI flags that must not collapse (`aws`, `gh`, `ps1`). A `no_stem` term is matched verbatim against the raw (unstemmed) input token. Example:

```yaml
triggers:
  keywords:
    - { term: "deploy", weight: 1.0 }        # stemmed: matches "deploying", "deployed"
    - { term: "aws", weight: 1.0, no_stem: true }   # exact: matches "aws" only
    - { term: "gh", weight: 0.5, no_stem: true }    # exact: matches "gh" only
```

&nbsp;&nbsp;&nbsp;&nbsp;**Stem-collision checker:** Run `python -m claude_wayfinder catalog build --check-stems` to detect pairs of distinct keyword terms from different catalog entries that share the same Porter2 stem. Competing-skill collisions are printed to stderr as `STEM_COLLISION` lines. Review each collision and either accept it or add `no_stem: true` to one of the terms to disambiguate.

**`keyword_groups`** — AND-group conjunctive triggers (added v0.6.0, #135). A list of `{slots: [{name, terms: [...]}, ...], weight}` objects. Unlike flat `keywords`, which score independently on each matching term, a group fires **only when all of its slots are satisfied** — each slot must have at least one of its `terms` present in the input keywords. On a full match, the group contributes `+1.0 × weight` to the score (the `_GROUP_MULTIPLIER` of `1.0` is deliberately distinct from the per-keyword `0.5` multiplier, so a satisfied weight-`1.0` group can solo-reach the `delegate` threshold). Requires ≥ 2 slots per group; single-slot groups are dropped with a warning. Weight values follow the same `{0.25, 0.5, 1.0}` ladder as flat keywords. Use `keyword_groups` when a routing decision should only fire for the co-occurrence of two or more terms — for example, a verb slot (`create`, `open`) and a noun slot (`issue`, `ticket`) — rather than on either term appearing alone. See `docs/design/trigger-schema.md §9.10` for a worked example.

**`tool_mentions`** — Tool names matched against the `tool_mentions` dimension of the dispatch input. Each match contributes `+0.5`. Tool names are case-sensitive; the harness passes `Bash`, `Read`, `WebFetch`, not lowercase variants.

**`excludes`** — Terms that hard-zero this entry's score when found in the input keywords. Applied before additive scoring. Matched against `features.keywords` only — not against `file_paths`, `tool_mentions`, or `agent_mentions`.

**`path_globs_excluded`** — Path globs that drop this entry from the scored pool when any glob matches any candidate file path. Exclusion wins over `path_globs` inclusion — an entry matching both is dropped. Use for "applies everywhere except X" patterns (e.g. an agent with `**/*.md` that must not win on `agents/**/*.md`). Same `fnmatch` semantics as `path_globs`; include both `agents/**/*.md` and `agents/*.md` forms (the `**` footgun applies here too). Example:

**File:** `triggers.yml` (any skill or agent sidecar)

```yaml
triggers:
  path_globs:
    - "**/*.md"
  path_globs_excluded:
    - "agents/**/*.md"
    - "agents/*.md"
```

**`applicable_agents`** — Top-level field in a skill sidecar (not under `triggers:`). Hard allowlist of agent names that may receive this skill when the matcher routes a task. `["*"]` means any agent. `[]` means no agent — the skill is dormant and will never attach.

**`applicable_skills`** — Top-level field in an agent sidecar. Hard allowlist of skill names to attach when routing to this agent. `["*"]` means any applicable skill. `[]` means no skills — the agent will never have a skill automatically attached.

**`routable`** — Boolean in agent frontmatter (not in the `triggers:` block). When `false`, the agent is removed from the scored-agent pool entirely and can never be selected as a delegation target. The router agent itself should always carry `routable: false`.

---

## Worked example: authoring from scratch

The `claude-wayfinder:refresh-catalog` skill is a compact real-world example. Its body is a six-step operational procedure for forcing a catalog rebuild; its sidecar distills that procedure into five trigger entries.

### Reading the body

The `refresh-catalog` skill body (`skills/refresh-catalog/SKILL.md`) opens:

> Force a fresh regeneration of the dispatch catalog and report the results.
>
> The bundled `refresh-catalog-on-stale.js` hook automatically rebuilds the catalog when a source file is newer than the catalog itself. This skill exists for cases the mtime heuristic misses or when you want to force a rebuild for debugging.

The rest of the body uses `catalog`, `regenerate`, `rebuild`, `dispatch`, and `refresh` as recurring operational vocabulary. `catalog` is the load-bearing noun — the skill exists to rebuild it. `regenerate` and `rebuild` are the primary verbs the user would type. `dispatch` and `refresh` appear in supporting context.

### The triggers.yml that results

**File:** `skills/refresh-catalog/triggers.yml`

```yaml
triggers:
  command_prefixes:
    - "/refresh-catalog"
  keywords:
    - { term: "catalog", weight: 1.0 }
    - { term: "regenerate", weight: 0.5 }
    - { term: "rebuild", weight: 0.5 }
    - { term: "dispatch", weight: 0.25 }
    - { term: "refresh", weight: 0.25 }
applicable_agents: []  # router-only; catalog regeneration is a maintenance operation
```

### Why each keyword was chosen at its weight

- **`catalog` at `1.0`** — the skill's entire purpose is catalog management. Any prompt mentioning "catalog" in the context of wayfinder operations is a very strong signal. One `1.0`-weight keyword contributes `+0.5 × 1.0 = +0.5` when matched.
- **`regenerate` and `rebuild` at `0.5`** — these are the specific verbs the skill performs. They are more discriminating than generic vocabulary like "run" or "update," but not unique enough to the skill to carry full `1.0` weight on their own.
- **`dispatch` and `refresh` at `0.25`** — these words appear in the body in supporting context ("dispatch catalog," "catalog refresh") and hint at the use-case, but they overlap with many other catalog-related operations. Giving them `0.25` weight means they contribute to the score without dominating it.
- **`command_prefixes: ["/refresh-catalog"]`** — the skill owns the `/refresh-catalog` slash command. When a user types that exact command, the skill scores `1.0` immediately, bypassing keyword scoring entirely.
- **`applicable_agents: []`** — catalog regeneration is a router-level maintenance operation. No sub-agent should be wired to execute it automatically; the router invokes it directly.

The scoring for a prompt "please regenerate the dispatch catalog" (two keywords matched: `regenerate` at `0.5`, `catalog` at `1.0`) would be `0.5 × 0.5 + 0.5 × 1.0 = 0.25 + 0.5 = 0.75`. With a strong-enough gap above the next entry, that would reach the `advisory` or `delegate` branch depending on the catalog.

---

## Worked example: tuning an existing entry

This is a hypothetical before-and-after tune on `claude-wayfinder:dispatch` (`skills/dispatch/SKILL.md`). No actual changes are being proposed — this example exists to illustrate the tuning workflow.

The dispatch skill's body describes two operating modes (demo mode and real-catalog mode), the 7-decision ladder, and the dispatch context JSON shape. It uses `dispatch`, `catalog`, `mode`, `demo`, `router`, `decision`, and `context` as recurring terms.

**Hypothetical scenario 1 — stale keyword removal.**

Suppose an earlier revision of the skill body included a section on a `--verbose` flag for low-level debugging. The sidecar was given a `verbose` keyword at weight `0.5`. That section was later removed ("No `--verbose` flag exists in the current CLI" — per `docs/integration.md`). The `verbose` keyword is now stale: it raises the skill's score on prompts about debugging output that have nothing to do with dispatch routing.

The fix is straightforward: remove the `verbose` entry from `triggers.keywords`. A keyword whose term no longer appears in the body is by definition a misleading signal. Stale keywords are the most common source of unexpected `advisory` decisions on gap-tied inputs — the entry scores well on prompts that no longer reflect its purpose.

**Hypothetical scenario 2 — weight demotion.**

Suppose the sidecar was written when `mode` was the central concept in the body (demo mode vs. real-catalog mode). It was given weight `1.0`. Over subsequent revisions, the body was reorganized and `mode` became a supporting term used to label two subsections, while `decision` became the more central concept. The `mode` entry should now be weight `0.5` or `0.25`.

An overweighted term inflates the score on any prompt that mentions it, even tangentially. A user asking "what's the difference between the two modes of the tool?" should probably not score the dispatch skill at full strength — the question is informational, not operational. Demoting `mode` to `0.5` brings the score on informational prompts down while leaving operational prompts (which will also trigger `dispatch`, `catalog`, `decision`) close to the same level.

The general principle: after any significant revision to a skill or agent body, re-read the sidecar against the body and ask for each `1.0` keyword — "is this term still the most central concept in the entry, or has the body moved on?" The answer is often "demote it."

---

## Worked example: troubleshooting an unreachable agent

This walkthrough uses a fictional agent to illustrate the `needs_more_detail` and `one-dimensional-triggers` failure modes. The symptom table in Section 8 of the `dispatch-authoring` skill lists the root causes; this section makes one of them concrete.

### The scenario

A developer authors a new agent called `schema-migrator` that handles database schema migration tasks. They write a sidecar with keywords only:

```yaml
# agents/schema-migrator.triggers.yml
triggers:
  keywords:
    - { term: "schema", weight: 1.0 }
    - { term: "migration", weight: 1.0 }
    - { term: "migrate", weight: 0.5 }
routable: true
applicable_skills: ["python"]
```

They run the catalog build and register the agent. Then they open a session and prompt:

> "Run the migration for the users table in the schema upgrade PR"

The matcher returns `needs_more_detail` instead of `delegate`. The developer is confused — the prompt clearly mentions "migration" and "schema."

### The diagnosis

`needs_more_detail` does not mean the catalog entry is empty. It means the **input's extracted features** populate fewer than two distinct dimensions. The prompt "Run the migration for the users table in the schema upgrade PR" produces one populated dimension: `features.keywords` contains `migration`, `schema`, `users`, `table`, `upgrade`. There are no `file_paths`, no `tool_mentions`, no `command_prefix`, no `agent_mentions` in the prompt. Feature count = 1. The matcher did not score the catalog at all.

The fix at the **input side** is to provide a richer prompt: "Run the migration for the users table in `db/migrations/001_users.sql`" — now `file_paths` is populated, giving the matcher two dimensions to work with.

But there is also an **entry-side** problem the `audit-catalog` CLI would surface: the `schema-migrator` entry has only one populated trigger dimension (`keywords`). Even on inputs that do pass the feature-density floor, the entry can only accumulate score through keyword hits. It has no `path_globs` or `tool_mentions` to provide a second scoring dimension, which limits how confidently the matcher can distinguish it from other agents with overlapping keyword sets.

### The fix

Add a `path_globs` entry that reflects the file types the agent works with:

**File:** `agents/schema-migrator/triggers.yml`

```yaml
triggers:
  keywords:
    - { term: "schema", weight: 1.0 }
    - { term: "migration", weight: 1.0 }
    - { term: "migrate", weight: 0.5 }
  path_globs:
    - "**/*.sql"
    - "**/migrations/**"
routable: true
applicable_skills: ["python"]
```

Now a prompt that mentions a `.sql` file path alongside "migration" will score: `+0.4` (path glob hit) + `0.5 × 1.0` (`migration` keyword) = `0.9`. With a gap ≥ 0.2 above the second-place agent, that produces a `delegate` decision. The `audit-catalog --severity concern` run would no longer flag this entry under `one-dimensional-triggers`.

---

## The audit-catalog CLI

`python -m claude_wayfinder audit-catalog` runs 12 static analysis rules against the dispatch catalog and groups findings by severity. Run it whenever you add or substantially edit a routable agent, before opening a PR that ships new frontmatter, or as a periodic catalog sanity check.

```bash
# Show all findings
python -m claude_wayfinder audit-catalog

# CI gate — fail only on BLOCKING
python -m claude_wayfinder audit-catalog --severity blocking

# Machine-readable output
python -m claude_wayfinder audit-catalog --json

# Scope to one entry
python -m claude_wayfinder audit-catalog --target schema-migrator
```

The implementation source is `src/claude_wayfinder/audit_catalog.py`.

---

### weight-not-in-ladder

**Severity:** BLOCKING

**What it checks:** Every keyword entry's `weight` field is exactly one of `{0.25, 0.5, 1.0}`.

**Why it matters:** The scoring formula is `+0.5 × weight` per keyword hit. A weight of `0.75` or `0.3` is not on the ladder the matcher is calibrated for. The catalog generator clamps non-ladder weights with a warning at build time, so a weight that looked fine in the YAML may silently become a different value in the catalog. This rule flags the pre-clamp source value so you can correct it in the sidecar.

**Fix:** Change the weight to the nearest ladder value (`0.25`, `0.5`, or `1.0`) in the sidecar YAML.

---

### whitespace-in-term

**Severity:** BLOCKING

**What it checks:** No keyword `term` field contains whitespace characters.

**Why it matters:** The matcher tokenizes the input on whitespace. A term like `"schema migration"` (two words) will never match a tokenized keyword list that contains `"schema"` and `"migration"` as separate tokens. It is a silent no-op that inflates the apparent keyword count without contributing any score.

**Fix:** Split multi-word terms into separate `{term, weight}` entries, one token per entry.

---

### duplicate-keyword-term

**Severity:** BLOCKING

**What it checks:** No keyword term appears more than once within the same catalog entry.

**Why it matters:** A duplicate term does not double the contribution — the matcher checks membership, not count. The second entry is dead weight that obscures the true keyword count, makes the sidecar harder to read, and is almost always a copy-paste artifact.

**Fix:** Remove all but one instance of the duplicated term.

---

### path-glob-footgun

**Severity:** CONCERN

**What it checks:** For every `path_globs` entry matching the pattern `*.<ext>` (a bare extension glob with no directory component), the entry's glob list also contains `**/*.<ext>` as a sibling.

**Why it matters:** Python's `fnmatch.fnmatch` does not traverse path separators when the pattern contains none. `*.py` matches `auth.py` but not `src/auth.py`. In practice, most file paths in a dispatch context include at least one directory component, so a bare extension glob silently fails to match the overwhelming majority of real inputs.

**Fix:** Add `**/*.ext` alongside the bare `*.ext` glob, or replace the bare glob with the `**/*` form entirely if you always want recursive matching.

---

### tool-name-case-error

**Severity:** CONCERN

**What it checks:** Every `tool_mentions` entry that resembles a known Claude Code tool name matches the canonical casing used by the harness.

**Why it matters:** Tool name matching is case-sensitive. `bash` never matches the harness-supplied `Bash`; `webfetch` never matches `WebFetch`. A wrongly-cased tool mention is a silent zero-contribution entry.

**Fix:** Use the canonical casing: `Agent`, `Bash`, `Edit`, `Glob`, `Grep`, `Monitor`, `NotebookEdit`, `Read`, `Skill`, `TaskCreate`, `ToolSearch`, `WebFetch`, `WebSearch`, `Write`.

---

### one-dimensional-triggers

**Severity:** CONCERN

**What it checks:** Every routable agent entry populates at least two of the five positive trigger dimensions (`command_prefixes`, `agent_mentions`, `path_globs`, `keywords`, `tool_mentions`).

**Why it matters:** The matcher's feature-density floor requires at least two populated input dimensions before it attempts scoring. An agent whose triggers populate only one dimension — keywords only, for example — will score well only on inputs where that single dimension matches. On keyword-only entries, the maximum reachable score is `+0.5 × weight` per hit, capped at `1.0`, with no path-glob or tool-mention contributions to push the score toward the `delegate` threshold of `0.85`. The agent may technically be reachable on high-signal inputs, but it is weakly calibrated across the distribution.

**Fix:** Add at least one `path_globs` entry for file types the agent works with, or a `tool_mentions` entry for tools the agent explicitly uses, or a `command_prefixes` entry if the agent has a canonical slash command.

---

### unreachable-routable

**Severity:** CONCERN

**What it checks:** Every routable agent entry has at least one positive trigger — i.e., at least one non-empty field among `command_prefixes`, `agent_mentions`, `path_globs`, `keywords`, and `tool_mentions`.

**Why it matters:** An agent with zero positive triggers will score `0.0` on every input that does not short-circuit via `command_prefixes` or `agent_mentions` (which are also empty). The matcher will never produce a `delegate` or `advisory` decision for it. This is distinct from `one-dimensional-triggers` — here the entry has no triggers at all.

**Fix:** Add trigger content. If the agent should not be reachable by the matcher at all, set `routable: false` in its frontmatter.

---

### conflict-pair

**Severity:** CONCERN

**What it checks:** For every pair of routable agents that share three or more overlapping keyword terms (case-insensitive), the rule checks whether a single-sided-asymmetric discriminator exists. If no such discriminator exists, the pair is flagged as a conflict.

**Why it matters:** Two agents with heavy keyword overlap will both score similarly on inputs that mention those shared terms. If neither agent has a discriminating `path_globs`, `tool_mentions`, or `command_prefixes` entry that the other lacks, the matcher will produce an `advisory` (gap-tied) decision on the prompts where those shared terms fire. This forces the router into an uncertain suggestion rather than a deterministic delegation, defeating the point of confident routing.

**The single-sided-asymmetric discriminator rule:** A discriminator field (one of `path_globs`, `tool_mentions`, `command_prefixes`) breaks the tie only when exactly one of the two agents has a non-empty value for that field and the other has an empty value. That asymmetry means: on inputs that activate the non-empty field, the scoped agent wins; on inputs that do not activate it, both agents score identically on keywords, and the gap-tied condition persists. The critical point is that the "scored-not-tied" subspace strictly favours one agent. Two non-empty disjoint sets — for example, agent A has `**/*.py` and agent B has `**/*.ts` — do not qualify: on prompts with no file paths (a common case), neither path-glob fires, both agents score identically on keywords, and the matcher still emits a gap-tied `advisory`. The rule (implemented in `_has_breaking_discriminator` in `audit_catalog.py`) only accepts the asymmetric case.

**Fix:** Introduce a single-sided-asymmetric discriminator: give one of the two agents a `path_globs`, `tool_mentions`, or `command_prefixes` entry that the other lacks entirely (not just a different value — one must be empty). If the two agents genuinely do the same thing in the same context, consider merging them.

---

### excludes-overlap-own-keywords

**Severity:** CONCERN

**What it checks:** No entry's `excludes` list contains a term that also appears in the same entry's `keywords` terms.

**Why it matters:** When a term appears in both `keywords` and `excludes`, the `excludes` short-circuit fires first (on any input that mentions the term) and zeroes the entry's score entirely — including on inputs that should be the entry's strongest matches. The entry effectively self-destructs on the very inputs it should win.

**Fix:** Remove the overlapping term from either `excludes` or `keywords`. Almost always the correct fix is to remove it from `excludes`.

---

### source-routable-mismatch

**Severity:** CONCERN

**What it checks:** No `source="plugin"` agent entry is marked `routable: true`.

**Why it matters:** Plugin-sourced agents are advisory by default. They represent capability that the plugin ships but that the operator has not explicitly activated. Marking a plugin agent as `routable: true` in the catalog causes it to participate in scored-agent pool scoring, which can produce unexpected routing decisions for capability the operator may not have intentionally enabled.

**Fix:** To route to a plugin agent, use a `plugin-override` sidecar (`triggers/<plugin>/agents/<name>.yml`) rather than editing the agent's `routable` flag directly. The `plugin-override` source tag is treated as routable by design.

---

### empty-applicable-agents

**Severity:** NIT

**What it checks:** No skill entry has an empty `applicable_agents` list.

**Why it matters:** `applicable_agents: []` is valid — it makes the skill dormant (no agent can receive it). But it is usually unintentional, the result of copying a template and forgetting to fill in the field. A dormant skill consumes a catalog entry without contributing anything to routing.

**Fix:** Set `applicable_agents: ["*"]` if the skill should be available to any agent, or list the specific agent names that should receive it.

**Opting out for intentional empty lists:** Some skills are router-only interactive skills (called by the router via the `Skill` tool, never delegated to sub-agents). For these, `applicable_agents: []` is the _correct_ value — setting `["*"]` would be wrong. Suppress the NIT by adding `applicable_agents_intentional` with a rationale string to the skill's `triggers.yml` sidecar (for owned skills) or the plugin-override sidecar at `~/.claude/triggers/<plugin>/<skill>.yml`:

```yaml
# triggers.yml
applicable_agents: []
applicable_agents_intentional: "router-only interactive skill — never delegated to sub-agents"
triggers:
  command_prefixes:
    - /my-skill
```

The field value must be a non-empty string; an empty string does not suppress the NIT. The string serves as documentation for why the empty list is deliberate.

---

### duplicate-trigger-set

**Severity:** NIT

**What it checks:** No two agent entries share an identical trigger set while differing in their `applicable_skills` lists.

**Why it matters:** Two agents with identical trigger fingerprints (same keywords, same path globs, same tool mentions, same command prefixes, same agent mentions, same excludes) will always score identically on every input. If they also have different `applicable_skills`, the result is unpredictable: depending on which entry is evaluated first, a different skill set will be attached to what is effectively the same routing decision. This is almost always a copy-paste where the author forgot to differentiate the triggers.

**Fix:** Differentiate the trigger sets (add a discriminating entry to one of the agents), or merge the two agents into one entry.

---

## Exit code contract

The CLI exit code reflects the highest-severity finding in the (filtered) output:

| Exit | Meaning | Typical use case |
| ---- | ------- | --------------- |
| 0 | No findings (after severity/target filtering) | Clean catalog confirmation; safe to merge. |
| 1 | NIT findings only | Development-loop noise gate; acceptable before shipping. |
| 2 | CONCERN findings present (no BLOCKING) | Development-loop gate; review before shipping, but not a hard block. |
| 3 | BLOCKING findings present | CI hard gate; do not merge until resolved. |

The exit code reflects the worst severity in the **filtered** output, not the full catalog. Running with `--severity blocking` will exit 0 unless at least one BLOCKING finding is present, even if the full catalog has CONCERNs. This makes it safe to use `--severity blocking` as a CI gate without requiring a perfectly clean catalog.

---

## Cross-references

- `skills/dispatch-authoring/SKILL.md` — the runtime-loaded skill; contains the scoring math, footgun list, authoring workflow, tuning workflow, and troubleshooting symptom table.
- `docs/schema.md` — canonical trigger field reference; start here for type definitions, defaults, and stability guarantees.
- `docs/design/trigger-schema.md` — design rationale for why the schema has the shape it does.
- `agent-authoring` skill (in `~/.claude/skills/agent-authoring/`) — broader harness authoring discipline covering agent structure, routing configuration, and the full lifecycle of a new agent; the dispatch-authoring skill is its matcher-specific counterpart.
- `src/claude_wayfinder/audit_catalog.py` — implementation source for all 12 CLI rules; the docstrings on each rule function are the normative descriptions.
