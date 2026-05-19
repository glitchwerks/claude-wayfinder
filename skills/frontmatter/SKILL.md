---
name: frontmatter
description: >
  Matcher-aware authoring and troubleshooting knowledge for agent and
  skill trigger frontmatter. Loaded by any agent (router, code-writer,
  doc-writer, project-planner, etc.) when the user wants to write,
  improve, troubleshoot, or understand dispatch frontmatter. Trigger
  this skill whenever the user types /frontmatter, asks "how do I
  write triggers", "how do I make frontmatter for my agent", "what's
  a good keyword weight", "set up triggers", or says "my agent isn't
  being dispatched", "this skill never matches", "my frontmatter
  isn't working", "dispatch isn't picking up", or similar authoring
  or troubleshooting requests around dispatch frontmatter. Covers
  the matcher's seven-decision ladder, scoring math, weight ladder
  {0.25, 0.5, 1.0}, fnmatch path-glob footguns, conflict-pair
  detection, and the audit-catalog CLI pointer.
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
| `keywords` | `triggers:` block | List of `{term, weight}` mappings. Each term found in the input keywords adds `0.5 × weight` to the score. |
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

3. **`ambiguous`** — the top routable agent scored ≥ 0.5 but the gap
   between it and the second-place agent is < 0.2; two or more agents
   are statistically tied and a tiebreak is needed.

4. **`self_handle`** — no dominant agent, but at least one skill scored
   ≥ 0.5; the router handles the task itself with the matched skills
   attached.

5. **`advisory`** — the best agent scored ≥ 0.5 with a gap ≥ 0.2 (so not
   `ambiguous`) but below the `delegate` floor of 0.85; delegation is
   suggested but not certain.

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
- Per `tool_mentions` match: `+0.5`

The final additive score is **clamped to `1.0`** before being returned.

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

**Conflict pairs produce `ambiguous` decisions.** Two entries whose
`keywords` lists share three or more overlapping case-insensitive terms,
with no discriminating `path_globs`, `tool_mentions`, or
`command_prefixes` to break the tie, will both score similarly on inputs
that mention those shared terms. The result is an `ambiguous` decision
that forces the router to ask the user to choose. Heavy keyword overlap is
a design smell. The remedy is to introduce a discriminator: a `path_globs`
entry that is unique to one of the two entries, a `tool_mentions` entry
that only one of them legitimately fires on, or a `command_prefixes` entry
that explicitly routes one of them. If the overlap is fundamental — the two
entries genuinely do the same thing in the same context — consider whether
they should be merged into one.
