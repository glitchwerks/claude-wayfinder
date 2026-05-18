---
touches:
  - src/claude_wayfinder/match.py
  - src/claude_wayfinder/build_catalog.py
  - src/claude_wayfinder/_dispatch.py
  - tests/test_match.py
  - tests/test_build_catalog.py
  - docs/design/trigger-schema.md
skills_relevant:
  - project-review
tracking: glitchwerks/claude-wayfinder#135
related: glitchwerks/claude-wayfinder#138
status: approved
---

# AND-group conjunctive triggers — design

Tracking issue: [#135](https://github.com/glitchwerks/claude-wayfinder/issues/135)
Related: [#138](https://github.com/glitchwerks/claude-wayfinder/issues/138) (mixed-content disambiguation — follow-up, orthogonal)
Author: brainstorming session 2026-05-18 (cbeaulieu-gt + Claude)
Status: **design approved — input to writing-plans**
Reviewed: project-reviewer 2026-05-18 — APPROVE_WITH_CHANGES, findings addressed in plan revisions

---

## § 1. Why this design exists

The dispatch matcher's keyword scoring (`src/claude_wayfinder/match.py`, function `score()`) treats triggered terms as a `frozenset[str]` and contributes `0.5 × keyword.weight` per independent match. This shape **cannot express conjunction** — there is no way to say *"term A and term B together carry signal that neither carries alone."*

The original motivation for this work cited a "165-case `code-writer ↔ doc-writer` ambiguous-tie fix." That figure did not survive direct analysis of `~/.claude/state/dispatch-log.jsonl` (9,936 `matcher_decision` records):

| Bucket | Count | % |
| --- | --- | --- |
| `code-writer` ↔ `doc-writer` ambiguous ties (actual) | 65 | — |
| Short verb-noun prompts (the pattern this spec targets) | **0** | 0% |
| Mixed-content prompts (≥ 3 file paths) | 44 | 68% |
| Other | 21 | 32% |

The dominant ambiguity driver in the live logs is **mixed-content prompts** where both agents reach `min(s, 1.0) = 1.00` via path-glob hits alone (tracked separately in [#138](https://github.com/glitchwerks/claude-wayfinder/issues/138)). AND-groups cannot fix that — the ambiguity is glob-driven, not keyword-driven.

This spec ships AND-groups for **honest reasons that survive the data**:

1. **Schema expressiveness.** Conjunction is a real modeling primitive that the trigger language lacks today. Closing the gap is good schema design regardless of current call volume.
2. **Likely higher impact on skill attachment than agent routing.** The `self_handle` decision path attaches skills using the same scoring. Skills with conjunctive trigger phrases (`"review the PR"`, `"create issue"`, `"plan file"`) cannot express their match condition cleanly today; once authors can, the skill side of the matcher likely sees wins the agent side does not.
3. **Catches future ambiguity that DOES match the pattern.** Our sample is one user. Less-mature catalogs in other deployments likely surface verb-noun ambiguity more often. Shipping the primitive before it becomes load-bearing is cheaper than retrofitting later.
4. **Forward-compatible with #138.** AND-groups change keyword scoring; #138 will change path-glob scoring. They compose without conflict.

## § 2. Design decisions (locked from brainstorm)

| # | Decision | Rationale |
| - | -------- | --------- |
| D1 | **Add `keyword_groups:` as a sibling field** to `keywords:` on each trigger block. | Matches the existing dataclass split (`Keyword` vs new `KeywordGroup`) and avoids the `term`/`terms` discriminator footgun a unified-list shape would introduce. |
| D2 | **Grammar is strictly two layers, flat.** Group = AND-of-slots; slot = OR-of-terms. No nested subgroups. | Two-layer AND-of-ORs has a single canonical normal form (DNF). Nesting destroys canonicalization and pushes the matcher toward a boolean-DSL evaluator that the data does not justify. Forward-compatible escape hatch: a future `subgroup:` field on slots can be added without breaking v1 catalogs if real cases ever emerge. |
| D3 | **Satisfaction = strict all-of across slots.** No K-of-N, no required/optional flags, no partial credit. | Author's mental model: *"a group is an expression, not fragments of one."* Strict AND is self-disciplining — adding slots makes a group LESS likely to fire, so the schema naturally pushes authors to keep groups tight. K-of-N inverts that pressure. |
| D4 | **Score contribution = `_GROUP_MULTIPLIER × group.weight`** with `_GROUP_MULTIPLIER = 1.0` (vs singleton multiplier of 0.5). | Conjunction is genuinely stronger signal than a single token. A `weight: 1.0` group contributes 1.00 → solo-delegates; a `weight: 0.5` group contributes 0.50 → attachment-only. Asymmetric multipliers give authors a clean two-tier dial without bloating the weight scale beyond the documented `{0.25, 0.5, 1.0}` clamp. |
| D5 | **Replacement rule:** when a group is satisfied, every singleton whose term appears in any slot of that group is **suppressed** for that entry. Singletons NOT covered by any satisfied group contribute normally. | Lets authors express *"this noun is a weak hint on its own, but `(verb) + (noun)` is a strong hint"* — `noun@0.25` singleton plus a `weight: 1.0` group containing `noun`. Without replacement, authors who list a term in both places would double-count. |
| D6 | **Multiple satisfied groups on one entry sum independently.** Existing `min(s, 1.0)` clamp absorbs any overflow. | Consistent with how singletons already compose. Two distinct expressions firing is more signal than one, even when the clamp eats some of it. |
| D7 | **Positional slots with optional `name:` for documentation.** Matcher ignores the name; it appears in sidecar source and matcher debug output only. | Positional is general (handles `(github) + (issue\|PR)` where the role taxonomy is fuzzy). Optional names give authors the self-documenting feel of named slots when the linguistic shape is clean (`verbs:`, `nouns:`). Single surface; no two-syntax precedence rule to teach validators. |
| D8 | **Slot count N ∈ [2, 8].** Validator errors at N < 2 or N > 8; warns at N ≥ 4. | 2-slot is the workhorse in the data. N ≥ 3 is rare but should not be forbidden. Hard cap at 8 prevents pathological catalogs. The N ≥ 4 warning surfaces likely over-modeling. |
| D9 | **Intra-group slot overlap is a validator error.** A term cannot appear in two slots of the same group. Cross-group overlap (same term in different groups on the same entry) is allowed. | A prompt token cannot fill two roles in one expression. Bipartite-matching at runtime is too much algorithm for a case that shouldn't exist. |
| D10 | **Per-slot term count ≥ 2 recommended, ≥ 1 required.** Validator warns at single-term slot. | A single-term slot reduces to "the literal token must appear" — equivalent to inlining into an adjacent slot or using `keywords:`. Allowed for unambiguous-anchor cases (`(github) + (issue\|PR\|workflow)`) where a single-token role is meaningful. |

## § 3. Grammar

```
group       := AND-of-slots                      (≥ 2 slots required)
slot        := OR-of-terms with optional `name:` (≥ 1 term required, ≥ 2 recommended)
term        := literal lowercase token (alphanumeric + hyphen, no whitespace)
group.weight ∈ {0.25, 0.5, 1.0}                  (same clamp as singleton keyword weights)
```

No nesting. No K-of-N. No required/optional per-slot flags.

## § 4. Schema shape

### Sidecar / frontmatter

```yaml
triggers:
  keywords:
    - {term: docs,   weight: 0.5}     # weak singleton signal — unchanged behavior
    - {term: readme, weight: 0.5}
  keyword_groups:
    - slots:
        - {name: verbs, terms: [update, edit, modify, change]}
        - {name: nouns, terms: [docs, readme, spec]}
      weight: 1.0
    # Slots may also be written as bare lists when names would be noise:
    - slots:
        - [github]
        - [issue, pr, workflow]
      weight: 1.0
```

### Catalog JSON shape

The built `dispatch-catalog.json` gains an optional `keyword_groups` array on each entry's `triggers` block:

```json
{
  "triggers": {
    "keywords": [...],
    "path_globs": [...],
    "keyword_groups": [
      {
        "slots": [
          {"name": "verbs", "terms": ["update", "edit", "modify", "change"]},
          {"name": "nouns", "terms": ["docs", "readme", "spec"]}
        ],
        "weight": 1.0
      }
    ]
  }
}
```

Catalogs without the field load and score identically to current behavior — `keyword_groups` defaults to an empty tuple in the parsed `Triggers` dataclass.

## § 5. Scoring rules

The `score()` function in `match.py` gains a group-evaluation step inserted after singleton contributions are computed but before final clamping.

### Constants

```python
_KEYWORD_MULTIPLIER = 0.5   # unchanged
_GROUP_MULTIPLIER   = 1.0   # new; named distinctly to make the asymmetry explicit
```

### Algorithm (informal pseudocode)

```
def score(entry, features):
    # 1. Short-circuit cases unchanged: command_prefix, agent_mention, excludes.

    s = 0.0
    s += 0.4 * matched_glob_count(entry, features)
    s += 0.5 * count_matching(entry.triggers.tool_mentions, features.tool_mentions)

    # 2. Evaluate groups; collect suppressed terms.
    suppressed = set()
    for group in entry.triggers.keyword_groups:
        if all(any(t in features.keywords for t in slot.terms) for slot in group.slots):
            s += _GROUP_MULTIPLIER * group.weight
            for slot in group.slots:
                suppressed.update(slot.terms)

    # 3. Singletons — skip those covered by a satisfied group.
    for kw in entry.triggers.keywords:
        if kw.term in features.keywords and kw.term not in suppressed:
            s += _KEYWORD_MULTIPLIER * kw.weight

    return min(s, 1.0)
```

### Edge-case rules

| Case | Behavior |
| --- | --- |
| Two groups on one entry both satisfy | SUM both contributions; `min(s, 1.0)` clamps at end |
| Term in group A's slot AND group B's slot, both satisfied | Singleton for that term is suppressed once (set semantics — suppression is idempotent) |
| Term in group A's slot (satisfied) AND group B's slot (unsatisfied) | Singleton for that term is suppressed because at least one containing group fired |
| Group satisfied but entry has no singleton listing the suppressed terms | No-op suppression; group contributes its own weight |
| Group has zero singleton coverage on the entry (terms exist only in slots) | Same as the previous row — pure conjunction signal, no suppression to do |

## § 6. Validator rules

Enforced at catalog build time (`build_catalog.py`) and re-checked by `match_filters.py` load-time validation where applicable.

| Condition | Severity | Message |
| --- | --- | --- |
| `slots:` missing or has 0–1 entries | **Error** | `Group needs ≥ 2 slots; use 'keywords:' for single-term triggers.` |
| `slots:` has more than 8 entries | **Error** | `Group has {N} slots; max is 8.` |
| `slots:` has 4–8 entries | **Warning** | `Group has {N} slots — real prompts rarely contain {N} distinct role tokens. Verify against real user phrasing.` |
| Slot has 0 terms or missing `terms:` | **Error** | `Slot requires 'terms:' with ≥ 1 entry.` |
| Slot has exactly 1 term | **Warning** | `Single-term slot '{term}' — consider merging into adjacent slot or using 'keywords:' if the term is a standalone signal.` |
| Same term appears in ≥ 2 slots of the SAME group | **Error** | `Term '{x}' cannot fill two roles in one expression. Place it in exactly one slot.` |
| `group.weight` outside `{0.25, 0.5, 1.0}` | **Error** | preserves existing weight-clamp invariant |
| `name:` on slot contains whitespace or non-identifier chars | **Warning** | `Slot name '{name}' should be a short identifier (alphanumeric + underscore).` |

Cross-group term overlap on the same entry is **allowed** and produces no diagnostic.

## § 7. Worked examples

### 7.1 Verb-noun expression — the case the schema unlocks

Doc-writer with new schema (incremental addition; production singletons preserved):

```yaml
# Existing production keywords (unchanged — shown for context):
keywords:
  - {term: docs,     weight: 1.0}
  - {term: readme,   weight: 1.0}
  - {term: spec,     weight: 1.0}
  - {term: update,   weight: 0.25}
  - {term: edit,     weight: 0.25}
  # ...other doc-writer keywords unchanged

# New keyword group added by this spec:
keyword_groups:
  - slots:
      - [update, edit, modify, change]
      - [docs, readme, spec]
    weight: 1.0
```

Code-writer keeps its existing `update@0.5`, `edit@0.5` singletons and `**/*.py` glob list — no change.

| Prompt | code-writer | doc-writer | Decision |
| --- | --- | --- | --- |
| *"update the docs"* | singleton `update@0.5` → 0.25 | group fires → 1.00; singletons `update@0.25`, `docs@1.0` suppressed | **delegate doc-writer** (gap 0.75, leader 1.00) |
| *"edit the readme"* | singleton `edit@0.5` → 0.25 | group fires → 1.00; singletons `edit@0.25`, `readme@1.0` suppressed | **delegate doc-writer** |
| *"the docs are great"* | 0 | group does NOT fire (no verb); singleton `docs@1.0` only → 0.50 | self_handle (attachment-level, no verb — correct) |
| *"update auth.py"* (with `.py` path) | singleton `update@0.5` (0.25) + glob `**/*.py` (0.4) → 0.65 | singleton `update@0.25` → 0.125 (group does not fire — no noun match) | advisory code-writer; combined with other code-writer signals would push to delegate at ≥ 0.85 |

Singleton-only prompts behave identically to today. Code-writing prompts unaffected.

### 7.2 Mixed-content prompt — what the spec does NOT solve

Real log case: *"Execute find-and-replace across mixed code and docs in glitchwerks/siege-web…"* — 19 paths spanning `.tsx`, `.md`, `.ps1`, `.html`, README, CHANGELOG.

Even with the verb-noun group on doc-writer, this prompt:

- Does NOT contain any verb in `{update, edit, modify, change}` — the actual verb is "replace" / "find-and-replace"
- Group does NOT fire
- Both agents remain pinned at `1.00` via path-glob hits alone (code-writer: 6 distinct code globs × 0.4; doc-writer: 5 distinct doc globs × 0.4 + `docs` singleton)

→ still ambiguous. This is **issue #138's** territory, not this spec's. Documenting the case here so future readers understand what AND-groups are and aren't.

### 7.3 Multi-group sum — skill attachment

Skill `gh-pr-review-address` with two groups:

```yaml
keyword_groups:
  - slots:
      - [address, fix, handle]
      - [review, comments, feedback]
    weight: 1.0
  - slots:
      - [check, what is, anything]
      - [blocking, merge]
    weight: 0.5
```

Prompt: *"address my review comments"* → group 1 fires (1.00), group 2 does not → skill score 1.00.

Prompt: *"address my review comments — anything blocking merge"* → both fire → 1.00 + 0.50 = 1.50 → clamped to 1.00. No regression vs single-group case, but the multi-trigger prompt is now scored at the ceiling rather than partway up.

## § 8. Backward compatibility

| Surface | Change |
| --- | --- |
| Existing catalog entries without `keyword_groups:` | **No behavior change.** Field defaults to empty tuple in parsed `Triggers`. |
| Existing `matcher_decision` log records | Unchanged shape. The `output.rationale` field SHOULD list which groups fired when applicable (new substring `groups_fired: [...]` in the rationale string). Older readers ignore unknown text. |
| Existing test fixtures | Pass unchanged. New tests added for groups. |
| `dispatch-catalog.json` format | Adds optional `keyword_groups` array per entry. Verified-ignored by older readers via the existing JSON parser's permissive shape. |
| `_KEYWORD_MULTIPLIER` constant | Unchanged at 0.5. Singleton scoring identical to v0.4.x. |

No catalog rebuild required for entries that don't author groups. Entries adopting groups can do so incrementally; partial rollout is safe.

## § 9. Acceptance criteria

1. **Matcher** honors `keyword_groups` per § 5 deterministically. Order-independent across catalog loads (verified by a replay test).
2. **Catalog builder** picks up `keyword_groups:` from skill sidecars and agent frontmatter. Missing field → empty tuple, no error.
3. **Validator** emits the errors/warnings in § 6. Build fails on errors; warnings appear in `catalog-generation.log`.
4. **Regression locked via mathematical equivalence** (deliberate punt; project-reviewer C5): since no current production catalog entry declares `keyword_groups`, the scoring formula reduces to the v0.4.2 formula for every existing record. The full existing test suite passing in Task 9 step 9.1 — combined with the new "no groups = unchanged behavior" unit test in Task 3 — is the substantive regression lock; no separate replay-against-`~/.claude/state/dispatch-log.jsonl` task is required. A full live-log replay can be added later if/when production catalogs begin authoring `keyword_groups` and behavior drift becomes a real risk.
5. **Forward test**: a regression fixture with the 5 sample prompts in § 7.1 plus 3–5 short verb-noun prompts that would resolve correctly with the doc-writer group from § 7.1. (These prompts are synthetic — the real logs do not contain them — and are kept under `src/claude_wayfinder/fixtures/and_groups/` for replay.)
6. **`docs/design/trigger-schema.md`** updated with §§ 2.x (schema reference for `keyword_groups`), § 4.x (matching rule), § 6.x (validation rule), § 9.x (authoring example).
7. **`matcher_decision.output.rationale`** lists groups that fired (e.g. `"matched group [verbs+nouns] on doc-writer; singleton docs suppressed"`).
8. **No new dependencies.** The matcher remains stdlib-only.

## § 10. Out of scope / forward pointers

- **Ordered n-grams / phrase adjacency** — deferred. The data does not currently justify the additional feature dimension; revisit post-rollout if logs surface order-sensitive cases.
- **IDF / discriminative reweighting** — not justified by current data (the common words are common because the work is common).
- **Proximity boosting** — subsumed by future n-gram work if it happens.
- **Phrase-excludes** — no high-frequency false-positive pattern in the logs.
- **Nested subgroups** — forward-compatible escape hatch via an optional `subgroup:` field on slots in a v2, if and only if logs justify the boolean-DSL surface.
- **Mixed-content disambiguation** — tracked separately in [#138](https://github.com/glitchwerks/claude-wayfinder/issues/138). Orthogonal: affects path-glob scoring, not keyword scoring. Either feature can land first; together they address different layers of the same discrimination problem.
- **`router-drift.jsonl` schema enrichment** — separate issue. The drift log currently records `category` but not `matcher_agent → actual_agent` transitions, which would be the highest-signal data for future calibration.
