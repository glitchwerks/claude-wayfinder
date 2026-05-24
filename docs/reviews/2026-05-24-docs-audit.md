# Docs Audit — 2026-05-24

**Issue:** #216
**Scope:** `README.md` + all `.md` files under `docs/`.
**Tracks:**
- **Active reference docs** — Accuracy / Section placement / Examples
- **Historical artifacts** (`docs/superpowers/`, `docs/reviews/`, `docs/refactor/`, `docs/exploration/`) — Relevance disposition (`Delete` / `Extract then delete` / `Keep`)

This report is the deliverable for #216. It lists findings + dispositions. **Fixes and extractions are filed as separate follow-up issues** (linked in the rightmost columns).

## Summary

| Track | Files reviewed | Total findings / dispositions |
|---|---:|---|
| Active reference docs | 10 | 6 findings (5 Accuracy, 1 Section, 0 Examples) |
| Historical specs + plans | 12 | 4 Keep, 2 Extract-then-delete, 6 Delete |
| Historical postmortems + reviews + refactor + exploration | 8 | 4 Keep, 1 Extract-then-delete, 3 Delete |
| **All files** | **30** | **6 reference findings · 8 Keep · 3 Extract · 9 Delete** |

**Two critical accuracy issues** in the active reference surface — both stale-since-shipped:

1. **Decision enum drift** (`README.md`, `docs/api.md`) — docs list `ambiguous` (removed in v0.10.0 #209) and omit `mixed_content` (added in v0.10.0 #211). Source of truth: `src/claude_wayfinder/match/_types.py:L20-30` — 7 members: `delegate`, `self_handle`, `self_handle_unaided`, `advisory`, `ask_user`, `needs_more_detail`, `mixed_content`.
2. **Wrong keyword scoring constant** (`docs/schema.md` §4.1) — doc says keyword contribution is `0.3 × weight`. Source (`src/claude_wayfinder/match/_match.py:L38`) is `_KEYWORD_MULTIPLIER = 0.5`. Also missing: `_GROUP_MULTIPLIER = 1.0` for satisfied keyword groups (`_match.py:L46`).

---

## Track 1 — Active reference docs

### Findings by file

| File | Accuracy | Section | Examples | Notes |
|---|---:|---:|---:|---|
| `README.md` | 1 | 0 | 0 | Stale enum at L17 |
| `docs/api.md` | 2 | 0 | 0 | Stale enum list + stale `VALID_DECISIONS` example |
| `docs/schema.md` | 1 | 0 | 0 | Wrong keyword coefficient |
| `docs/design.md` | 1 | 0 | 0 | `ambiguous` referenced as current (may be intentional historical context) |
| `docs/integration.md` | 0 | 0 | 0 | Clean |
| `docs/dispatch-discipline.md` | 0 | 0 | 0 | Clean |
| `docs/dispatch-authoring-guide.md` | 0 | 1 | 0 | Minor: sidecar/inline YAML presentation clarity |
| `docs/release-process.md` | 0 | 0 | 0 | Clean |
| `docs/design/trigger-schema.md` | 0 | 0 | 0 | Clean |
| `docs/design/2026-05-14-v0.2-integration-design.md` | 0 | 0 | 0 | Clean (historical design doc, kept as-is) |
| **Totals** | **5** | **1** | **0** | |

### Detail

**`README.md`**
- **[Accuracy]** L17: decision-contract list includes `ambiguous` and omits `mixed_content`. **Action:** replace with the current 7-member set. **Follow-up:** _(to be filed — see Follow-up Issues below)_

**`docs/api.md`**
- **[Accuracy]** L98–104: prose list of decision enum is stale (same as README L17). **Action:** sync to current 7 members.
- **[Accuracy]** L130–139: `VALID_DECISIONS` code example shows the old set including `ambiguous`. **Action:** sync to `_types.py:L20-30`.

**`docs/schema.md`**
- **[Accuracy]** §4.1 (L244, L251): keyword scoring coefficient documented as `0.3 × weight`; source uses `0.5 × weight` (`_match.py:L38`). The doc's coefficient summary on L251 (`glob = 0.4`, `tool = 0.5`) matches source for those two terms — only the keyword constant is wrong. Doc also omits `_GROUP_MULTIPLIER = 1.0` for satisfied AND-groups (`_match.py:L46`). **Action:** correct the keyword coefficient and add a one-line note about group contribution.

**`docs/design.md`**
- **[Accuracy]** L209: references `ambiguous` decision. Cross-check needed — if framing as "the decision type", update to `mixed_content` (functional successor for the dominant case) or `advisory` (where `ambiguous` was structurally merged per #209). If framing as historical design rationale, retain but annotate that the name was superseded. **Action:** read in context; pick rewrite or annotate.

**`docs/dispatch-authoring-guide.md`**
- **[Section]** L52–77 (refresh-catalog worked example): YAML shown without explicit label that it is the content of `skills/<name>/triggers.yml`, not the SKILL.md body. Current phrasing reads "The triggers.yml that results" which works, but the example block could be prefaced with the filename. **Action:** add a one-line filename header above the code block.

### Cross-reference sweep — active reference docs

All relative-path links in scope resolve under `git ls-tree HEAD`. No broken links.

---

## Track 2 — Historical artifacts

Per `CLAUDE.md § Document Files` ("delete plan files when done; extract durable info before deletion"), each file gets one of: **Delete** (parent closed, no durable content), **Extract then delete** (durable content; extract first), **Keep** (still load-bearing as-is, justified per file).

### Specs + plans (`docs/superpowers/specs/`, `docs/superpowers/plans/`)

| File | Parent | State | Disposition | Notes |
|---|---|---|---|---|
| `specs/2026-05-17-setup-skill-architecture-design.md` | #99 | closed | **Keep** | Live design for v0.4 setup-skill architecture; referenced by `docs/integration.md` |
| `specs/2026-05-18-and-groups-design.md` | #135 | closed | **Keep** | Locked spec for AND-groups; referenced by tests + `docs/design/trigger-schema.md` |
| `specs/2026-05-18-owned-project-agent-sidecars.md` | #148 | closed | **Extract then delete** | D1–D6 sidecar/source-tag conventions → `docs/schema.md` + `docs/design/trigger-schema.md` |
| `specs/2026-05-18-plugin-agent-sidecar-overrides.md` | #140 | closed | **Extract then delete** | D1–D7 plugin-agent override conventions → `docs/schema.md` + `docs/design/trigger-schema.md` |
| `specs/2026-05-19-telemetry-bypass-taxonomy-design.md` | #143 | closed | **Keep** | Live schema reference for bypass-cause enum; cited by `bypass-taxonomy.js` + `analyze-drift-causes.py` |
| `docs/dispatch-overrides.md` | #213 | closed | **Keep** | Live design for v0.11.0 overrides feature; referenced by CHANGELOG + README (relocated from `specs/` by #246) |
| `plans/2026-05-16-v0.4-bundled-venv.md` | #81 | closed/not_planned | **Delete** | Superseded by #99 setup-skill design; carries deferred banner |
| `plans/2026-05-16-v0.4-bundled-venv.inquisitor-pass-1.md` | #81 | closed/not_planned | **Delete** | Critique of deferred plan; dissolved by architecture pivot |
| `plans/2026-05-17-v0.4-bundled-venv-revision.md` | #81 | closed/not_planned | **Delete** | Revision pass; carries superseded banner |
| `plans/2026-05-17-v0.4-bundled-venv.inquisitor-pass-2.md` | #81 | closed/not_planned | **Delete** | Pass-2 critique; verdict REVISE-AGAIN before pivot |
| `plans/2026-05-19-telemetry-bypass-taxonomy.md` | #143 | closed | **Delete** | Implementation scaffolding for shipped #188; spec is the canonical reference |
| `plans/2026-05-24-issue-213-dispatch-overrides.md` | #213 | closed | **Delete** | Implementation scaffolding for shipped #214; spec is the canonical reference |

**Counts:** 4 Keep · 2 Extract-then-delete · 6 Delete (12 files total).

#### Extraction targets

**From `specs/2026-05-18-owned-project-agent-sidecars.md`:**
- L29–L37 → `docs/schema.md`: D1 (sidecar location conventions), D4 (source-tag reuse), D6 (no new source values rationale)
- L32–L38 → `docs/design/trigger-schema.md`: D2 (sidecar wins over inline), D3 (strict orphan handling — Mode 2a), D5 (no forced migration)

**From `specs/2026-05-18-plugin-agent-sidecar-overrides.md`:**
- L29–L39 → `docs/schema.md`: D1 (sidecar location), D2 (source tag: reuse `plugin-override` + disambiguate by `kind`), D3 (strict override Mode 2a), D4 (no `min_claude_version`), D7 (precedence order)
- L30–L36 → `docs/design/trigger-schema.md`: D3 (strict orphan handling rationale), D5 (unmatched sidecars emit warnings), D6 (watcher coverage claim — verify at implementation)

### Postmortems + reviews + refactor + exploration

| File | Parent | State | Disposition | Notes |
|---|---|---|---|---|
| `superpowers/postmortems/2026-05-18-telemetry-enrichment-pivot/POSTMORTEM.md` | #143 | closed | **Extract then delete** | Lessons L44–L111 → new `docs/design/methodology-lessons.md` or memory |
| `superpowers/postmortems/2026-05-18-telemetry-enrichment-pivot/spec-final.md` | #143 | closed | **Delete** | Superseded draft from abandoned PR #152; lessons captured in POSTMORTEM |
| `superpowers/postmortems/2026-05-18-telemetry-enrichment-pivot/project-reviewer.md` | #143 | closed | **Delete** | Intermediate review artifact; generalized into POSTMORTEM |
| `superpowers/postmortems/2026-05-18-telemetry-enrichment-pivot/inquisitor.md` | #143 | closed | **Delete** | Intermediate review artifact; generalized into POSTMORTEM |
| `reviews/2026-05-13-v0.1-plan-inquisitor-pass-1.md` | #4 | closed | **Keep** | Exemplar of the adversarial-review-driven planning discipline; paired with pass-2 |
| `reviews/2026-05-13-v0.1-plan-inquisitor-pass-2.md` | #4 | closed | **Keep** | Terminal review before v0.1 cut; documents post-revision failure modes |
| `refactor/2026-05-21-python-module-audit.md` | #193 | closed | **Keep** | Audit report; baseline spec for follow-on refactor issues (e.g. #196) |
| `exploration/2026-05-13-plugin-distribution.md` | #5 | closed | **Keep** | Grounding source for Python-kernel decision; cited by v0.1 reviews |

**Counts:** 4 Keep · 1 Extract-then-delete · 3 Delete (8 files total).

#### Extraction targets

**From `superpowers/postmortems/2026-05-18-telemetry-enrichment-pivot/POSTMORTEM.md`:**
- L44–L111 → new `docs/design/methodology-lessons.md` (or split into `~/.claude/agent-memory/general-purpose/` entries per scope discipline): the six lessons on (1) read the data first, (2) `PreToolUse raw_input` source matters, (3) cross-process contracts spanning trust boundaries, (4) matcher architecture asymmetry, (5) three-field model framing as scaffolding, (6) reviewer ≠ adversary.

### Cross-reference flags (deletions)

Before any **Delete** action, the following inbound references must be redirected (issue + PR + commit message stand in for the deleted plan):

| Deletable file | Inbound references | Required action |
|---|---|---|
| `plans/2026-05-16-v0.4-bundled-venv.md` and 3 sibling v0.4 docs | Internal cross-cites only within the `plans/` set (which all delete together) | None — internal references go away with the set |
| `plans/2026-05-19-telemetry-bypass-taxonomy.md` | None outside the file itself | None |
| `plans/2026-05-24-issue-213-dispatch-overrides.md` | None outside the file itself | None |
| Postmortem subfolder deletables | Internal sibling cites only | None — POSTMORTEM.md gets extracted then deleted, so the whole subfolder collapses |

No file outside the to-be-deleted set references any of these by path, so deletion is safe once extractions land.

---

## Follow-up issues

Per the issue #216 AC, fixes and extractions ship as **separate follow-up issues**. Suggested follow-ups:

| Follow-up | Scope | Effort |
|---|---|---|
| `fix(docs): sync decision enum to current 7-member set` | `README.md` L17, `docs/api.md` L98–104 + L130–139, `docs/design.md` L209 | small |
| `fix(docs): correct keyword scoring coefficient in schema.md` | `docs/schema.md` §4.1 (L244, L251) + add `_GROUP_MULTIPLIER` note | small |
| `docs: label triggers.yml example filename in dispatch-authoring-guide` | `docs/dispatch-authoring-guide.md` L52–77 | trivial |
| `docs: extract sidecar/source-tag conventions from owned-project + plugin-override specs` | Two `Extract then delete` files under `docs/superpowers/specs/` → `docs/schema.md` + `docs/design/trigger-schema.md`, then delete sources | medium |
| `docs: extract telemetry-pivot methodology lessons + delete postmortem subfolder` | POSTMORTEM.md L44–L111 → new methodology doc or memory; delete 4-file subfolder after extraction | medium |
| `chore(docs): delete superseded v0.4 bundled-venv plan set` | 4 files under `docs/superpowers/plans/` (parent #81 closed/not_planned) | trivial |
| `chore(docs): delete implementation-scaffold plans for shipped #143 and #213` | `plans/2026-05-19-telemetry-bypass-taxonomy.md`, `plans/2026-05-24-issue-213-dispatch-overrides.md` | trivial |

---

## Methodology + caveats

- **Three parallel `code-reviewer` batches** (active reference / historical specs+plans / historical postmortems+reviews+refactor+exploration). Each batch returned findings; this consolidated report is assembled by the router.
- **Critical accuracy claims** in Track 1 (decision enum, keyword coefficient) were re-verified against source by the router before publishing — not relayed unchecked.
- **Parent-issue state checks** for historical artifacts used `gh issue view <N>` against `glitchwerks/claude-wayfinder`. All closures confirmed.
- The audit report itself (`docs/reviews/2026-05-24-docs-audit.md`) is durable and referenced by the follow-up issues above — it should NOT be deleted until those follow-ups close.

🤖 _Generated by Claude Code on behalf of @cbeaulieu-gt_
