---
name: dispatch-authoring
description: >
  Matcher-aware authoring and troubleshooting knowledge for the full
  dispatch authoring surface — trigger frontmatter, applicable_agents,
  applicable_skills, routable, and description: phrase-matching.
  Loaded by any agent (router, code-writer, doc-writer,
  project-planner, etc.) when the user wants to write, improve,
  troubleshoot, or understand dispatch configuration. Trigger this
  skill whenever the user types /dispatch-authoring, asks "how do I
  write triggers", "how do I make frontmatter for my agent", "what's
  a good keyword weight", "set up triggers", or says "my agent isn't
  being dispatched", "this skill never matches", "my frontmatter
  isn't working", "dispatch isn't picking up", or similar authoring
  or troubleshooting requests around dispatch configuration. Covers
  the matcher's seven-decision ladder, scoring math, weight ladder
  {0.25, 0.5, 1.0}, symmetric Porter2 stemming and the no_stem
  opt-out, fnmatch path-glob footguns, conflict-pair detection, and
  the audit-catalog CLI pointer.
---

# Frontmatter Authoring Guide

This skill covers everything you need to write, improve, and troubleshoot
trigger frontmatter for claude-wayfinder agents and skills. It explains
what fields the matcher reads, how scoring works, the seven routing
decisions the matcher can produce, and the most common mistakes that cause
an entry to match poorly or never match at all.

---

## 1. What the Matcher Consumes

### Source precedence

The catalog generator accepts trigger configuration from two places: inline
frontmatter inside the agent or skill file itself, and a **sidecar file**
placed next to the main file. The sidecar always wins when both are present.

The current sidecar format is **v6**. Under v6:

- A **skill** stores its trigger configuration in a file named `triggers.yml`
  placed in the same directory as `SKILL.md`:

  ```
  skills/python/SKILL.md
  skills/python/triggers.yml   ← authoritative trigger config
  ```

  Per issue #150, a colocated `<name>.triggers.yml` next to `SKILL.md`
  (owned and project agent sidecars) overrides inline frontmatter. The
  `SKILL.md` file itself should not contain `triggers:`,
  `applicable_agents:`, or `applicable_skills:` keys; the generator emits
  a warning and ignores them if they appear there.

- A **plugin-shipped agent** stores its trigger override at
  `triggers/<plugin>/agents/<name>.yml` (per issue #142). This file
  activates a dormant plugin agent that would otherwise score zero.

For the canonical field reference see `docs/schema.md`. For the design
discussion behind the sidecar approach see `docs/design/trigger-schema.md`.

### Schema fields

| Field | Where it lives | Purpose |
|---|---|---|
| `command_prefixes` | `triggers:` block | Slash commands that immediately short-circuit to score `1.0`. |
| `agent_mentions` | `triggers:` block | Agent names whose explicit mention in the prompt immediately short-circuits to score `1.0`. |
| `path_globs` | `triggers:` block | `fnmatch`-style globs matched against the `file_paths` dimension of the input. Each matched glob adds `0.4` to the score. |
| `keywords` | `triggers:` block | List of `{term, weight}` mappings. Each term found in the input keywords adds `0.5 × weight` to the score. Terms are Porter2-stemmed unless `no_stem: true` is set (see § 1 "Stemming"). |
| `no_stem` | per-keyword flag | Optional `no_stem: true` on a `{term, weight}` mapping exempts that term from stemming — it matches the raw (unstemmed) input token verbatim. Use for acronyms, product names, and CLI flags (`aws`, `gh`, `ps1`). |
| `keyword_groups` | `triggers:` block | **AND-group conjunctive triggers** (added v0.6.0, #135). List of `{slots: [{name, terms: [...]}, ...], weight}` groups. A group fires only when **all** of its slots match — each slot must have ≥ 1 of its terms present in the input keywords. On match, the group adds `1.0 × weight` (via `_GROUP_MULTIPLIER`, distinct from the `0.5` `_KEYWORD_MULTIPLIER` so a satisfied weight-1.0 group can solo-decide `delegate`). Use when a routing decision should require co-occurrence of two or more terms (e.g. verb + noun) rather than either alone. |
| `tool_mentions` | `triggers:` block | Tool names matched against the input `tool_mentions` dimension. Each match adds `0.5`. |
| `excludes` | `triggers:` block | Terms that hard-zero the entry's score when found in the input keywords. |
| `applicable_agents` | skill sidecar top-level | Hard allowlist of agent names that may receive this skill. `["*"]` means any agent. `[]` means no agent — the skill is dormant. |
| `applicable_skills` | agent sidecar top-level | Hard allowlist of skill names to attach when routing to this agent. `["*"]` means any. `[]` means no skills. |
| `routable` | agent frontmatter top-level | When `false`, the agent is excluded from the scored-agent pool. Set to `false` on the router itself so it is never selected as a delegation target. Absent on skill entries. |

### How each field type feeds the matcher

`command_prefixes` and `agent_mentions` are checked before any additive
scoring begins — a match on either field returns `1.0` immediately without
examining any other field. `excludes` is also a pre-scoring check: a match
zeroes the entry and stops further evaluation.

`path_globs`, `keywords`, and `tool_mentions` contribute additively to a
running score that is capped at `1.0` before being returned. An entry
with no triggers in any of these three fields will score `0.0` on any
prompt that does not happen to name the entry directly by command prefix
or agent mention.

### Stemming (symmetric Porter2, issue #304)

The matcher applies **symmetric Porter2 (Snowball English) stemming** to
keyword matching. Catalog terms are stemmed at catalog-build time; input
tokens are stemmed at dispatch time; matching is stem-vs-stem. The practical
consequence for authors: **list the base form only — you do not need to
enumerate inflections.** `implement` matches `implementing` and
`implemented`; `refactor` matches `refactored`; `lint` matches `linting`.

This applies to flat `keywords` terms and to `keyword_groups` slot terms
alike. Two distinct catalog terms that collapse to the same stem are
deduped at parse time so a single input token cannot double-count.

**`no_stem` opt-out.** Add `no_stem: true` to a `{term, weight}` mapping to
exempt that term from stemming. A `no_stem` term is matched verbatim against
the raw (unstemmed) input token. Use it for tokens that must not collapse —
acronyms, product names, and CLI flags:

```yaml
triggers:
  keywords:
    - { term: "deploy", weight: 1.0 }                 # stemmed: matches "deploying", "deployed"
    - { term: "aws", weight: 1.0, no_stem: true }     # exact: matches "aws" only
    - { term: "gh", weight: 0.5, no_stem: true }      # exact: matches "gh" only
```

Stemming changes nothing about the scoring formula or thresholds — it only
changes which input tokens count as a match. See `docs/dispatch-authoring-guide.md`
for the canonical reference, and § 9 for the `--check-stems` collision
pre-flight.

---

## 2. The Seven-Decision Ladder

The matcher evaluates the catalog against the input features and emits
exactly one of seven decisions. The ladder is evaluated in order; the
first branch whose conditions are satisfied wins.

1. **`needs_more_detail`** — the input's extracted features populate fewer
   than two distinct dimensions (paths, keywords, tools, command prefixes,
   agent mentions); the matcher did not attempt to score the catalog at all.

2. **`delegate`** — one routable agent scored ≥ 0.85 and its gap above
   the second-place agent is ≥ 0.2; high-confidence single winner,
   delegation is appropriate.

3. **`self_handle`** — no dominant agent, but at least one skill scored
   ≥ 0.5; the router handles the task itself with the matched skills
   attached.

4. **`mixed_content`** — the gap between the top two agents is < 0.2 and
   ≥ 2 agents are each clamped at 1.0 on path-disjoint lanes (every
   qualifying agent has at least one path-glob match, and no input path
   is claimed by more than one agent). The decision carries a `lanes[]`
   array (one entry per specialist, each with `agent`, `score`,
   `matched_paths`, and `skills`) and an `unassigned_paths[]` list for
   input paths not claimed by any lane. Fires after `self_handle` and
   before `advisory`.

5. **`advisory`** — the best agent scored ≥ 0.5 but the `delegate`
   threshold was not met (gap < 0.2 or score < 0.85) and `mixed_content`
   conditions were not satisfied; delegation is suggested but not certain.

6. **`ask_user`** — reserved in v0.1 and v0.2; the current matcher never
   produces this decision. Include a handler for forward compatibility.

7. **`self_handle_unaided`** — no agent and no skill scored above
   threshold; the router proceeds without delegation or skill attachment.

### Input-side density floor vs. entry-side weak scoring

These are two separate concerns. Conflating them leads to incorrect
diagnosis when an entry fails to match.

**Input-side density floor (the `needs_more_detail` branch above).**
The matcher emits `needs_more_detail` when the *user prompt's* extracted
`Features` populate fewer than two dimensions — paths, keywords, tools,
command prefixes, or agent mentions. This is a property of how thin the
*input* is, not of how thin any catalog entry's triggers are. A two-word
prompt with no file paths and no recognised keywords triggers this branch
regardless of how rich the catalog is. The fix is to provide a richer
prompt, not to change the entry's triggers.

**Entry-side weak scoring (a calibration footgun — see Section 5).**
An entry whose triggers populate only one dimension — for example
keywords-only with no `path_globs`, `tool_mentions`, or
`command_prefixes` — will score weakly on most inputs, because it can
only accumulate score when the input happens to mention one of its
specific terms. This is not an unreachability theorem; the entry *can*
score if the input fills its one dimension. But it is a calibration
smell: the score ceiling on matching inputs is limited (`+0.5 × weight`
per keyword hit, clamped at `1.0`), and any prompt that doesn't mention
one of the entry's specific terms scores it at zero. Section 5 elaborates
the practical guidance.

---

## 3. Scoring Math

The matcher computes a per-entry score using the following rules, applied
in order. Short-circuits fire before any additive contribution is
calculated.

**Short-circuit rules (evaluated first, in this order):**

- `command_prefixes` match → score = `1.0` immediately.
- `agent_mentions` match → score = `1.0` immediately.
- `excludes` match in `features.keywords` → score = `0.0` immediately.

**Additive scoring (when no short-circuit fired):**

- Per `path_globs` match: `+0.4`
- Per `keywords` match: `+0.5 × weight` (verified at
  `src/claude_wayfinder/match.py:84` — `_KEYWORD_MULTIPLIER = 0.5`;
  raised from 0.3 to fix single-keyword skills never attaching)
- Per `keyword_groups` match: `+1.0 × weight` per group that fires
  (a group fires only when **all** of its slots are satisfied;
  `_GROUP_MULTIPLIER = 1.0` is deliberately distinct from
  `_KEYWORD_MULTIPLIER = 0.5` so a satisfied weight-1.0 group can
  solo-decide `delegate`; #135 / v0.6.0)
- Per `tool_mentions` match: `+0.5`

The final additive score is **clamped to `1.0`** before being returned.

### AND-group worked example

An entry has a `keyword_groups` block requiring both a "verb" and a "noun"
to co-occur:

```yaml
keyword_groups:
  - slots:
      - {name: verbs, terms: [create, open, file]}
      - {name: nouns, terms: [issue, ticket, bug]}
    weight: 1.0
```

| Prompt                              | verbs slot | nouns slot | Group fires? | Score from this group |
| ----------------------------------- | :---------: | :---------: | :-----------: | --------------------: |
| "open the issue tracker"            |     ✓       |     ✓       |     yes       |  `+1.0 × 1.0 = +1.0`  |
| "create the issue body"             |     ✓       |     ✓       |     yes       |  `+1.0`               |
| "open the file"                     |     ✓       |     ✗       |     no        |  `+0.0`               |
| "tell me about issues"              |     ✗       |     ✓       |     no        |  `+0.0`               |

A satisfied weight-`1.0` group contributes `1.0` to the entry — enough to
clamp at the ceiling on its own and solo-decide `delegate` if the gap to
the runner-up is ≥ `0.2`. That is the design intent of
`_GROUP_MULTIPLIER = 1.0`: a group is a stronger signal than any single
flat keyword (which maxes at `+0.5`) because the group already encodes
co-occurrence — the matcher has more evidence that the prompt intends
this entry, so the score reflects that.

The flat-`keywords` equivalent of the same term lists would score on
either side firing alone — scoring `+0.5 × weight` on "open the file" and
"tell me about issues" too, since each individual term contributes
independently. The AND-group structurally requires co-occurrence, which
is the right shape when the trigger only makes sense for the combined
intent.

### Worked example

An entry has one `path_globs` entry (`**/*.py`) and one `keywords` entry
(`python`, weight `1.0`). For a prompt that mentions a `.py` file and
the word "python":

```
path_glob match:   +0.4
keyword match:     +0.5 × 1.0 = +0.5
total:              0.9   (below 1.0, no clamping needed)
```

A score of `0.9` with a gap ≥ 0.2 above the second-place entry would
yield a `delegate` decision.

### Clamping footgun

Because the final score is hard-clamped at `1.0`, stacking additional
high-weight keywords past the ceiling adds nothing. Consider an entry
with one path-glob hit (`+0.4`) and two weight-`1.0` keyword hits
(`+0.5` + `+0.5`): the additive total is `1.4`, which clamps to `1.0` —
exactly the same as one path-glob plus one weight-`1.0` keyword. The
second high-weight keyword is dead weight on any input that already
crosses the ceiling.

The practical guidance: once an entry can plausibly reach `≥ 1.0` on its
highest-signal inputs, prefer broadening *coverage* — add more distinct
terms at `0.25` or `0.5` weight — over stacking duplicate `1.0` weights.
Broadening raises the score on a wider range of inputs; stacking only
inflates the sum on the inputs where the ceiling would already be hit,
and the clamp throws that extra score away.

---

## 4. Trigger Field Rules

These are the validation rules the catalog generator enforces. Violating
them produces a warning at build time and may cause the entry to score
unexpectedly.

- **Weight ladder is exactly `{0.25, 0.5, 1.0}`.** Any other numeric value
  is clamped to the nearest ladder step with a validator warning (see
  `_clamp_weight` in `build_catalog.py`). There is no weight of `0.75`,
  `0.3`, or `2.0`.

- **`keywords` is a list of `{term, weight}` mappings.** Bare strings are
  rejected by the generator. Every keyword entry must be an object with
  exactly two keys: `term` (a string) and `weight` (one of `0.25`,
  `0.5`, `1.0`).

- **Keyword terms are Porter2-stemmed unless `no_stem: true`.** List base
  forms; stemming matches inflections automatically (see § 1 "Stemming").
  Set `no_stem: true` on terms that must match verbatim — acronyms, product
  names, CLI flags. A `no_stem` term is compared against the raw input token,
  not its stem. Authoring inflected variants of a stemmed term (`deploy`,
  `deploying`, `deployed`) is redundant — they all collapse to one stem and
  are deduped.

- **`path_globs` uses Python `fnmatch` semantics, not gitignore semantics.**
  The matcher calls `fnmatch.fnmatch(path, glob)`. This has important
  consequences — see the footguns section for the most common mistake.

- **`tool_mentions` is case-sensitive.** The matcher compares tool names as
  literal strings. `Bash` and `bash` are different values; the correct
  casing matches what the Claude Code harness uses. Wrong case silently
  fails to match.

- **`excludes` matches against `features.keywords` only.** The `excludes`
  list is not checked against `file_paths`, `tool_mentions`, or
  `agent_mentions`. An exclude term that appears only in a file path will
  not zero the score.

- **`command_prefixes` should start with `/`.** A prefix like `dispatch`
  without a leading slash will not match a user-typed `/dispatch` command,
  because the dispatcher passes the slash as part of the string.

- **`keyword_groups` requires ≥ 2 slots per group.** A single-slot group is
  semantically equivalent to a flat `keywords` entry — the catalog
  generator warns and drops single-slot groups. Use plain `keywords`
  instead when you only want one set of alternatives. Each slot needs a
  non-empty `terms` list; empty `terms` makes the slot unsatisfiable and
  drops the group.

- **`keyword_groups` weights follow the same `{0.25, 0.5, 1.0}` ladder as
  flat keywords.** Off-ladder weights on a group are clamped with a
  validator warning, same as on flat keyword entries.

---

## 5. Footguns

These are the most common authoring mistakes, in roughly descending order
of frequency.

**`fnmatch *.py` does not match nested files.** Python's `fnmatch` matches
only within a single path component when the glob contains no path
separator. `*.py` matches `foo.py` but not `src/foo.py`. Use `**/*.py` if
you mean "any `.py` file anywhere under the tree." This is the most common
path-glob mistake and the one most likely to cause silent non-matching on
real inputs.

**Tool names are case-sensitive.** The harness passes tool names with the
casing it uses internally: `Bash`, `Read`, `Edit`, `WebFetch`, `Glob`.
Lowercase variants like `bash` or `webfetch` will silently fail to match.
When in doubt, check the tool name in the harness output rather than
guessing the casing.

**`applicable_skills: []` mutes the agent's skill attachment entirely.**
Setting this to an empty list means the agent will never have any skill
attached, regardless of how well those skills score against the input.
Only set `[]` when you genuinely want no skills auto-attached to this
agent. The most common unintended form of this is inheriting a default
empty list in a new agent sidecar template and forgetting to change it.

**One-dimensional triggers are a calibration footgun (entry-side).**
A routable agent or skill with only `keywords` and no `path_globs`,
`tool_mentions`, or `command_prefixes` will score zero on any prompt that
does not mention at least one of its specific keyword terms. Even on
matching prompts, the score is bounded by `+0.5 × weight` per hit,
clamped at `1.0`. This is not the same as the input-side `needs_more_detail`
floor described in Section 2, which fires when the *user's prompt* is too
thin. This is about the *entry* being weakly reachable across the distribution
of prompts the matcher actually sees. The fix is to pair keywords with at
least one `path_globs`, `tool_mentions`, or `command_prefixes` entry, giving
the matcher a second scoring dimension to work with.

**Flat `keywords` over-fire when terms only make sense together.** If your
entry should match `"open issue"` but not `"open file"` or `"recent issues"`
alone, flat keywords score each term independently and accumulate on
either side firing alone. Use `keyword_groups` to require co-occurrence:
each `slot` carries one set of alternatives, and the group fires only
when **all** slots are satisfied. See the §3 worked example.

**Conflict pairs produce `advisory` decisions.** Two entries whose
`keywords` lists share three or more overlapping case-insensitive terms,
with no discriminating `path_globs`, `tool_mentions`, or
`command_prefixes` to break the tie, will both score similarly on inputs
that mention those shared terms. The gap falls below 0.2, so neither
reaches the `delegate` floor, and the matcher emits `advisory` — the top
agent is suggested but not confirmed. (Prior to v0.9.0 this scenario
produced an `ambiguous` decision; that outcome was removed. The runtime
now emits `advisory` for all gap-tied and below-threshold cases.) Heavy
keyword overlap is a design smell. The remedy is to introduce a
discriminator: a `path_globs` entry that is unique to one of the two
entries, a `tool_mentions` entry that only one of them legitimately fires
on, or a `command_prefixes` entry that explicitly routes one of them. If
the overlap is fundamental — the two entries genuinely do the same thing
in the same context — consider whether they should be merged into one.

**Stemming can make two distinct terms collide silently.** Because matching
is stem-vs-stem, two terms you intended to be different can collapse to the
same Porter2 stem and fire as one — `universe` and `university` both stem to
`univers`, `operating` and `operate` both stem to `oper`. When that collision
spans two competing catalog entries, it manufactures a conflict pair you did
not author by hand. The fix is either to accept the collision (often it is
harmless) or to add `no_stem: true` to one of the terms so it matches
verbatim. Run `python -m claude_wayfinder catalog build --check-stems` (see
§ 9) to surface cross-entry stem collisions as `STEM_COLLISION` warnings
before they reach the live catalog. Acronyms and product names are the most
common over-stem casualties — prefer `no_stem: true` on those by default.

---

## 6. Authoring Workflow

Use this workflow when writing trigger frontmatter for a new agent or skill
from scratch.

**Step 1 — Read the body in full.**
Open the agent or skill file and read it completely before writing a single
trigger. Triggers that are drafted from a one-line summary routinely miss
the recurring terminology the body actually uses.

**Step 2 — Identify prominent terms and assign weights.**
As you read, note terms by how central they are to the entry's purpose:

- The skill or agent **name**, or the core verb it acts on (e.g. `refactor`
  for the refactoring agent, `dispatch` for the routing skill) → weight
  `1.0`. At most one or two terms should sit here; if many terms seem equally
  central, that is a sign the scope is too broad.
- Recurring concept terms — vocabulary the body uses repeatedly and that
  distinguishes this entry from neighboring ones → weight `0.5`.
- Supporting or contextual terms — words that appear in the body and hint at
  the use-case, but are not discriminating on their own → weight `0.25`.

A well-calibrated entry typically has one or two `1.0` terms, three to five
`0.5` terms, and a handful of `0.25` terms. If the `1.0` bucket is full of
generic words (`code`, `file`, `run`) the entry will conflict with half the
catalog.

List **base forms only** — terms are Porter2-stemmed, so `implement` already
covers `implementing` and `implemented` (see § 1 "Stemming"). Mark acronyms,
product names, and CLI flags with `no_stem: true` so they are not over-stemmed
into a collision with another entry's term.

**Step 3 — Add `path_globs` for any file patterns the body implies.**
If the body directs attention to specific file types or directory trees, add
a `path_globs` entry for each. Always use `**/*.ext` for extension-based
patterns (not `*.ext` — see the fnmatch footgun in Section 5). A
`path_globs` entry adds `+0.4` per match and provides a second scoring
dimension that significantly improves disambiguation against other entries
with similar keyword sets.

**Step 4 — Add `tool_mentions` for any tools the body explicitly names.**
If the body tells the user to reach for a specific tool — `Bash`, `Edit`,
`WebFetch`, etc. — list those tools in `tool_mentions`. Use the exact
casing the harness uses (capitalize the first letter; see the case-sensitive
footgun in Section 5).

**Step 5 — Decide `applicable_skills` (for agents).**
Read the body for skill-task language: verbs like "plan", "test", "review",
"debug". List the skill names whose purpose aligns with those verbs in
`applicable_skills`. Use `["*"]` only if the agent is genuinely purpose-
agnostic. Do not leave the field blank or set it to `[]` unless you
intentionally want no skills attached.

**Step 6 — Prefer the v6 sidecar location over inline frontmatter.**
Place the resulting YAML in the sidecar rather than embedding it in the
agent or skill file's frontmatter block. Sidecars isolate trigger config
from body copy, which makes diffs cleaner and code review faster. The
correct paths are:

- Owned skills: `skills/<name>/triggers.yml`
- Plugin agents: `triggers/<plugin>/agents/<name>.yml`

Inline frontmatter is still read by the generator, but a sidecar at
the location above always overrides it.

---

## 7. Tuning Workflow

Use this workflow when improving trigger frontmatter that already exists
but is producing poor match results.

**Step 1 — Read the body and the current triggers side-by-side.**
Open both the sidecar (or inline frontmatter) and the agent or skill body
at the same time. This side-by-side read is the only reliable way to spot
divergence between what the entry does and what the triggers say it does.

**Step 2 — Find stale keywords.**
Look for terms in `triggers.keywords` that no longer appear in the body.
Bodies change over time; triggers often do not. A stale keyword raises
the entry's score on inputs that no longer reflect its actual purpose,
creating misleading matches. Remove or replace stale terms.

**Step 3 — Find missing keywords.**
Scan the body for recurring terms that are absent from `triggers.keywords`.
If a concept appears in every paragraph but is not listed as a keyword,
the entry will miss prompts that use that concept. Add the term at the
weight level appropriate to how central it is.

**Step 4 — Check weight alignment.**
For each existing keyword, ask whether its weight still reflects its
centrality to the body. The most common drift pattern is a term that was
elevated to `1.0` during an early iteration and was never revisited as
the scope of the entry narrowed. A `1.0` weight that should be `0.5`
inflates the score on prompts that mention that term even tangentially
and widens conflict-pair risk.

**Step 5 — Check conflict-pair risk.**
Eyeball the catalog for other entries that share several of the same
keywords. If two or more entries have three or more overlapping terms at
`0.5` or `1.0` weight and no differentiating `path_globs` or
`tool_mentions`, they will produce `advisory` decisions on the prompts
where those terms overlap (the gap falls below 0.2, preventing `delegate`).
Introduce a discriminator (see Section 5) or run `audit-catalog`
(see Section 9) to surface all conflict pairs at once.

**Step 6 — Check for structural violations.**
Before committing, verify that:

- Every `keywords` entry is a `{term, weight}` mapping, not a bare string.
- Every weight is exactly one of `0.25`, `0.5`, or `1.0`.
- No term contains leading or trailing whitespace (the generator does not
  strip these; `" python"` and `"python"` are different terms).
- `command_prefixes` entries each start with `/`.
- Acronyms, product names, and CLI flags carry `no_stem: true` so they are
  not over-stemmed; run `python -m claude_wayfinder catalog build --check-stems`
  to confirm no unintended cross-entry stem collisions were introduced.

---

## 8. Troubleshooting Workflow

When an agent is not being dispatched or a skill is not attaching as
expected, work through the symptom table below to identify the cause.

| Symptom | Likely cause |
|---|---|
| Routable agent scores 0 on prompts that should match | Unreachable routable: `triggers` is empty or every keyword weight is `0`. |
| Score never crosses the delegation floor (`0.85`) | One-dimensional triggers — the entry has only `keywords` and no `path_globs` or `tool_mentions`; max reachable score is limited. |
| Agent matches everything indiscriminately | Keyword set too generic (`code`, `file`, `run`); conflict-pair risk against many other entries. |
| Skill never attaches to the expected agent | `applicable_agents` on the skill sidecar excludes that agent name; or `applicable_skills: []` on the agent sidecar mutes all skill attachment. |
| Weight you set in the sidecar is not what the matcher uses | Non-ladder weight (e.g. `0.75`) was silently clamped to the nearest step; check `catalog-generation.log`. |
| A specific term never contributes to the score | The term appears in the entry's own `excludes` list, zeroing the entry whenever it is present in the input. |
| A `tool_mentions` entry never matches | Case mismatch — the harness passes `Bash`, not `bash`; `WebFetch`, not `webfetch`. Use the exact harness casing. |
| A term matches inputs that share only a word stem (`university` firing on `universe`) | Porter2 over-stemming collapsed two distinct terms to one stem. Add `no_stem: true` to the term, or run `catalog build --check-stems` to find the collision (§ 1, § 5, § 9). |
| An acronym / product name matches unexpected inflections | The token was stemmed when it should be literal. Add `no_stem: true` to match it verbatim. |

**Diagnostic sequence when none of the above is obvious:**

1. Run `audit-catalog` (see Section 9) and check the output for
   structural warnings on the entry.
2. Compare the entry's trigger YAML against the schema in `docs/schema.md`
   field by field.
3. Check whether a sidecar overrides the inline frontmatter you edited —
   both exist, the sidecar wins, and the edit may have gone to the wrong
   file.
4. Confirm the entry is marked `routable: true` (or that the key is absent,
   which defaults to `true`). An explicit `routable: false` removes the
   entry from the scored-agent pool entirely.

---

## 9. When to Run the CLI

> The matcher-aware checks the LLM cannot do consistently across all ~70
> catalog entries — conflict-pair detection, unreachable-routable scans,
> structural validation across the whole catalog — live in
> `python -m claude_wayfinder audit-catalog`. Run it whenever you add or
> substantially edit a routable agent, before opening a PR that ships new
> frontmatter, or as a periodic catalog sanity check. See
> `docs/dispatch-authoring-guide.md` for the rule reference and exit-code
> contract.

The CLI is the authoritative source for catalog-wide problems. It is not a
substitute for the field-by-field checks in Sections 6 and 7, but it
catches conflict pairs and unreachable entries that would require reading
the full catalog by hand to detect otherwise.

**Stem-collision pre-flight.** Run
`python -m claude_wayfinder catalog build --check-stems` whenever you add or
edit keyword terms. It detects pairs of distinct keyword terms — across
different catalog entries — that share the same Porter2 stem, printing each
to stderr as a `STEM_COLLISION` line. Review each: accept the collision if
it is harmless, or add `no_stem: true` to one of the terms to disambiguate
(see the § 5 stemming footgun). This check is distinct from `audit-catalog`'s
conflict-pair detection — a stem collision can manufacture a conflict pair
that the hand-authored keyword lists do not reveal on inspection.

---

## 10. References

- `docs/schema.md` — canonical trigger field reference; start here when
  looking up the exact name, type, or default for any trigger field.
- `docs/design/trigger-schema.md` — design rationale for the schema;
  explains why certain field shapes were chosen and what alternatives were
  considered.
- `docs/dispatch-authoring-guide.md` — extended worked-examples companion
  to this skill; covers edge cases and advanced calibration patterns not
  addressed above.
- `agent-authoring` skill (in `~/.claude/skills/agent-authoring/`) —
  broader harness authoring discipline covering agent structure, routing
  configuration, and the full lifecycle of a new agent; this
  dispatch-authoring skill is its matcher-specific counterpart.
