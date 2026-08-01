---
title: Matcher v3 hard-routing cutover — serve compose_route behind hard_routing_domains
touches:
  - src/claude_wayfinder/match/_main.py
  - src/claude_wayfinder/match/_compose.py
  - src/claude_wayfinder/match/_cells.py            # READ-NOT-MODIFY (cell-map content owned elsewhere)
  - src/claude_wayfinder/match/_decide.py           # READ-NOT-MODIFY (parity source; see §10 G4 — editing it destroys the corpus)
  - src/claude_wayfinder/match/_dispatch.py         # READ-NOT-MODIFY (batch path stays un-hard-routed — §11)
  - src/claude_wayfinder/cli.py                     # READ-NOT-MODIFY (disposition_source consumer; already tolerates "posture_routed")
  - scripts/corpus/eval/_kc.py                      # READ-NOT-MODIFY (arm-prefix contract this plan preserves)
  - scripts/shadow-kc-report.py                     # READ-NOT-MODIFY (same)
  - scripts/shadow-summary.py                       # MODIFIED — add served_arm distribution (§5.4)
  - tests/test_match/test_hard_routing.py           # NEW
  - tests/test_match/test_compose_parity.py         # NEW
  - tests/test_match/test_shadow_mode.py            # one deliberate assertion re-point
  - tests/test_corpus_eval/test_kc_schema_contract.py  # additive-keys confirmation
  - skills/dispatch/SKILL.md                        # doc-only: served-vs-shadow semantics
  - docs/maintenance/release-process.md             # READ-NOT-MODIFY
  - CHANGELOG.md
  - pyproject.toml
  - .claude-plugin/plugin.json
skills_relevant:
  - python
  - claude-code-plugin-authoring
  - claude-github-tools:github-actions
---

# Matcher v3 hard-routing cutover — serve `compose_route` behind `hard_routing_domains`

**Status:** PLAN / REVISED 2026-07-31 after a `project-reviewer` pass (0 BLOCKING, 5 CONCERN, 2 NIT — all
folded in) and a router-side GitHub verification pass. Not authorization to implement.

**Read §0 first.** Two external prerequisites came back **negative** and gate the flip from outside this
repo: the dispatch-caller never shipped `confidence` emission (`claude-configs#978` closed unmerged), and
the marketplace pin is 108 commits behind at `v1.2.0`. **Implementation (§0 X1) and rehearsal (X2) are
unblocked and are the right next work; the flip (X3) is not.** Ten DECISIONS REQUIRED (§9) and four
verification gates (§10) remain.

**Settled context (do not re-litigate).** The user has decided to proceed with cutting the posture-routing
decision from shadow to live despite the formal KC framework reading NO-GO. The rationale — that the
whole-sample KC-1 floor-miss is a dilution artifact of near-zero-data long-tail posture cells, while
`operate` and `build` each clear the floor on their own — is accepted as the premise of this plan. This
document scopes **how to do it safely**, not whether.

**Relationship to the prior plan.** This plan **supersedes and replaces §2 Phase 4 (Flip)** and **§8
(Rollback)** of `docs/superpowers/plans/2026-06-19-matcher-v3-ship-live.md`. That plan's P0–P3 are
historical and remain the authoritative record of how the design was validated; its P5 (two-repo release)
is unchanged and still applies downstream of this cutover. Everything below is net-new detail that the
older plan named but did not specify.

---

## 0. PREREQUISITES — blocking; the cutover is inert for real users until both clear

Router-side GitHub verification (2026-07-31) resolved the two gaps this plan originally carried as
unverified. Both came back negative. **Neither is a caveat; each independently makes the flip a no-op for
production traffic.**

### P-1. The caller does not emit `confidence` — M15-8b was never merged

Tracking issue `glitchwerks/claude-configs#977` is **closed**, but its implementing PR
`glitchwerks/claude-configs#978` ("emit two-axis Matcher v3 labels in dispatch context") was **closed
without merging** (`mergedAt: null`). A closed issue with an unmerged PR is precisely the failure mode
`CLAUDE.md § Pull Requests` and the prior plan's P4 entry criterion warn about
(`docs/superpowers/plans/2026-06-19-matcher-v3-ship-live.md:364`–`:366`) — and it is why "is the issue
closed?" was never the right question.

**Consequence.** Compose's §D.1 fail-safe treats absent confidence as `low` and never posture-routes
(`src/claude_wayfinder/match/_compose.py:104`–`:117`). With no caller emitting `confidence`, setting
`hard_routing_domains` today changes **nothing** for production traffic on the posture-route path.

**But it does not make the flip fully inert — §1 still applies.** The gated-fallback surface does not
depend on `confidence` at all. A caller emitting only `domain` (no posture, no confidence) still gets a
changed candidate set. So the correct statement is: *the posture routes stay dark, while the gating
blast radius goes live.* That is the worst possible combination — all of the risk, none of the benefit —
and it is the single strongest reason not to flip a gated domain before P-1 clears.

### P-2. Deployed installs are 108 commits behind, at v1.2.0 — pre-Matcher-v3 entirely

The `claude-wayfinder` marketplace pin is still `v1.2.0`, **108 commits behind `main`** (repo at `1.4.0`,
HEAD `35bd0a2`). v1.2.0 predates Matcher v3, so deployed consumers are not running the shadow logic, let
alone this plan's cutover code. This is the same deployed-vs-repo split flagged in §10 G1, now quantified
and worse than the 47-commit figure carried from the June plan.

### Scope decision — RECOMMENDATION: both are hard prerequisites, NOT in this plan's scope

**Recommend this plan does NOT absorb either.** Reasons:

1. **P-1 lives in a different repo** (`glitchwerks/claude-configs`). Re-doing #978 is caller-side skill
   authoring against a different review surface. Folding a cross-repo skill change into a matcher cutover
   makes both harder to review and neither independently revertible.
2. **P-2 is the existing release runbook**, already specified end-to-end in
   `docs/maintenance/release-process.md` and in the prior plan's P5. It needs executing, not re-planning.
3. Both must be **verified merged/live before this plan's flip phase starts** — but they gate it from
   outside. Treating them as prerequisites keeps this plan's scope exactly "make hard routing correct and
   safe," which is what it is good at.

**Therefore, restructure the phasing:**

| Phase | Content | Gate to exit |
|---|---|---|
| **X0 — Prerequisites (external)** | Re-open/redo `claude-configs#978`; ship the marketplace pin bump per `release-process.md` | #978's successor PR `mergedAt` non-null; pin live-verified at ≥ the version containing the cutover |
| **X1 — Implementation (this plan, safe to start NOW)** | §2 flag, §3 parity, §5 schema + §5.4 summary, §6 tests. All land with `hard_routing_domains` **empty** ⇒ zero behavior change | All §6 tests green; §10 G4 exit check clean |
| **X2 — Rehearsal** | §7 R1–R6 | R4 byte-identical revert proven; R3 gated-fallback diff recorded |
| **X3 — Flip** | §8 first flip; D-SOAK1 soak | **Blocked on X0.** Without it the flip is dark-but-risky per P-1 |

**X1 and X2 are not blocked** and are the right next work: they are behavior-neutral while the flag is
empty, and they are what makes X3 safe once X0 clears.

**Note on D-SOAK1 (§9).** A "200-dispatch production soak" is not measurable until P-1 clears — production
emits no labels to soak. Until then the only label-bearing traffic is the maintainer's own dev
environment (see §8.1), so the soak must either wait for X0 or be explicitly scoped as a dev-traffic
rehearsal. State which.

---

## 1. The correction that reframes the whole cutover

> **The blast radius of setting `hard_routing_domains = {X}` is every request labeled `domain=X`, not
> just the posture-routed ones.**

This contradicts the framing in the dispatching brief ("the fallback path returns `decide()`'s output
verbatim — fully compatible") and it is the single most consequential finding in this plan.

`compose_route` computes `gated = gate_agents(scored_agents, labels.domain)` **before** the
`if labels.posture:` block (`src/claude_wayfinder/match/_compose.py:406`, `:432`). Its fallback branch then
returns `decide(gated, scored_skills, features, catalog)` — `decide()` over the **gated** agent list
(`_compose.py:563`), not over the full `scored_agents` list that live `main()` uses today
(`src/claude_wayfinder/match/_main.py:281`).

`gate_agents` filters to `DOMAIN_AGENT_MAP[domain]` when the domain is a known key
(`src/claude_wayfinder/match/_cells.py:155`–`:158`). For `domain="code"`, that is a 5-agent set plus
`ANY_DOMAIN_AGENTS` (`_cells.py:53`–`:69`); every other routable agent is removed from the candidate list
before `decide()` runs. `decide()`'s delegate branch keys off `scored_agents[0]` and the gap to
`scored_agents[1]` (`src/claude_wayfinder/match/_decide.py:201`–`:222`) — both of which move when entries
are removed. So the served `decision`, `agent`, `confidence`, `rationale`, and `alternatives` can all
change on a **gated-fallback** row.

Critically, this happens on rows where **none** of the designed fail-safes apply:

| Row shape | Posture route fires? | Candidate set changes? |
|---|---|---|
| `domain=code`, no `posture` at all | No (`_compose.py:432` guard) | **Yes** |
| `domain=code`, `confidence=low` | No (§D.1 fail-safe) | **Yes** |
| `domain=code`, plausibility veto fires | No (§B.1 veto) | **Yes** |
| `domain=code`, all gates pass | Yes | Yes |

The "absent/non-high confidence never fires posture routing" fail-safe protects the **posture route**. It
does **not** protect the **gate**. An implementer who reasons only about the fail-safe will
under-estimate the flip's surface by a wide margin.

Two escape hatches limit this, and they drive the §8 rollout recommendation:

1. `gate_agents` returns the list **unchanged** for `domain` that is `None`, `"is_any"`, or any string not
   a key in `DOMAIN_AGENT_MAP` (`_cells.py:155`–`:156`). `DOMAIN_AGENT_MAP` has no `"is_any"` key
   (`_cells.py:52`–`:78`).
2. If gating would empty the list, `gate_agents` falls back to ungated (`_cells.py:140`–`:141`, docstring).

**Consequence for the rollback rehearsal (§7):** a rehearsal that exercises only a posture-routed row
proves nothing about the larger surface. It must also diff a gated-fallback row.

---

## 2. `hard_routing_domains` — resolving D-FLAG1

The flag is specified in `docs/superpowers/plans/2026-06-19-matcher-v3-ship-live.md:352` and
`docs/superpowers/specs/2026-06-14-two-axis-labeling-design.md` (§G.1 BLOCKING-2) but has **zero code
references** in `src/` or `tests/` — verified by repo-wide grep for `hard_routing_domains|HARD_ROUTING`,
which matches only the four `docs/` files. This is 100% net-new implementation.

### 2.1 Shape — RECOMMENDATION: env var `DISPATCH_HARD_ROUTING_DOMAINS`

Comma-separated domain tokens. Rationale: it matches the two existing operator-facing runtime controls,
`DISPATCH_CATALOG_PATH` (`_main.py:122`) and `DISPATCH_SHADOW` (`_main.py:56`), so a rollback is an
operator action with no code change, no release, and no marketplace pin bump — which is the entire reason
the flag was designed domain-scoped rather than boolean
(`docs/superpowers/plans/2026-06-19-matcher-v3-ship-live.md:605`–`:610`). A CLI flag would require every
caller to be updated; a config file would add a new file-resolution surface and a new failure mode.

```
DISPATCH_HARD_ROUTING_DOMAINS=is_any            # first flip
DISPATCH_HARD_ROUTING_DOMAINS=is_any,code       # widened
DISPATCH_HARD_ROUTING_DOMAINS=                  # OFF (empty ⇒ full shadow)
# unset                                          # OFF
```

### 2.2 Default safety — a set flag is NOT a bool flag

This is the question the task brief flags, and it deserves a precise answer, because `_main.py` already
contains a flag whose default points the **other** way.

`DISPATCH_SHADOW` is deliberately **fail-open**: absent, truthy, or malformed all resolve to ON
(`_main.py:44`–`:59`, `_SHADOW_FALSEY_VALUES` at `:41`). That is correct for shadow, because the failure
mode of "shadow accidentally on" is a wasted compute and an extra log key.

`hard_routing_domains` must be **fail-closed**, because its failure mode is "serve an unvalidated routing
decision." But "fail-closed" for a set has three distinct cases a bool does not:

| Input | Resolves to | Rationale |
|---|---|---|
| Unset / empty string / whitespace | `frozenset()` ⇒ OFF | Absence is never consent. |
| `"is_any,code"` | `{"is_any", "code"}` | Normal case. Tokens stripped, lowercased, empties dropped. |
| `"is_any,cod"` (typo) | `{"is_any"}` + stderr warning | **Unknown tokens are dropped, not fatal.** A typo must not crash live dispatch, and must not silently widen the set. Dropping is fail-closed *for the typo'd domain*. |
| `"CODE , code"` | `{"code"}` | Normalize + dedupe. |
| Any parse exception | `frozenset()` ⇒ OFF + stderr warning | Never raise. |

The recognized-token set is `set(DOMAIN_AGENT_MAP) - {None}` ∪ `{"is_any"}` (`_cells.py:52`–`:78`). Note
`"is_any"` must be added explicitly — it is not a `DOMAIN_AGENT_MAP` key.

Compare the shapes explicitly, because this is where a reviewer's bool intuition misleads: for a bool,
"malformed ⇒ safe default" is one decision. For a set, a malformed *element* can either poison the whole
set (over-strict; one typo disables a validated rollout) or be dropped (recommended; the rollout proceeds
minus the typo'd domain, and stderr says so).

### 2.3 The two-flag interaction — RECOMMENDATION: compose stays inside the shadow gate

Today `compose_route` is called only inside `if _shadow_enabled():` (`_main.py:288`–`:303`). After
cutover the served decision depends on compose's output, so the relationship must be chosen deliberately:

- **(a) Keep compose inside the shadow gate (RECOMMENDED).** `DISPATCH_SHADOW=0` then also disables hard
  routing — an undocumented second kill switch, which this plan makes a **documented** one. The cost is
  that the two flags' defaults point in opposite safety directions (shadow absent ⇒ on;
  hard-routing absent ⇒ off), which must be stated in `skills/dispatch/SKILL.md` and the module docstring.
- **(b) Hoist compose out of the gate when the set is non-empty.** Then `DISPATCH_SHADOW=0` means "serve
  compose but log nothing" — a served, unlogged, unauditable routing decision. This is the exact
  auditing blindness §5 exists to prevent.

Recommend **(a)**, with `DISPATCH_SHADOW=0` documented as the coarse emergency kill switch (reverts
stdout to `decide()` unconditionally, across all domains) and the domain set as the surgical one. Pin
this with a test (§6, T-9).

### 2.4 How `main()` branches

Insert **after** the shadow record is built (`_main.py:304`) and **before** `_write_log_entry`
(`_main.py:313`), so that the log always reflects what was actually served:

```
lexical_result = decide(...)                  # unchanged, line 281 — ALWAYS computed
served = lexical_result                       # NEW — default is always lexical
shadow_record = None
hard_domains = _parse_hard_routing_domains()  # NEW — parsed ONCE, here
if _shadow_enabled():
    try:
        ... compose_route(...) -> shadow      # unchanged, lines 288-303
        if labels.domain is not None and labels.domain in hard_domains:
            served = shadow                   # NEW — the cutover
        shadow_record = _build_shadow_record( # MUST stay inside the try
            labels, lexical_result, shadow, diag,
            served=served,
            hard_routing_domains=hard_domains,  # passed in, NOT re-parsed
        )
    except Exception:                         # unchanged fail-open, lines 305-310
        served = lexical_result               # NEW — explicit revert
        shadow_record = None
_write_log_entry(context, served, ..., shadow_data=shadow_record)
print(json.dumps(served, sort_keys=True), flush=True)
```

> **Indentation is load-bearing here — do not hoist the `_build_shadow_record` call.** `shadow` and `diag`
> are bound only inside the `try`. At the outer indent, that call raises `NameError` whenever
> `DISPATCH_SHADOW=0` or `compose_route` raised — and it would also clobber the `shadow_record = None`
> just set by the handler. Today's code already places this call inside the `try`
> (`src/claude_wayfinder/match/_main.py:304`) for exactly that reason. Getting this wrong breaks T-9 and
> T-10 (§6), the two tests that pin the fail-safes invariants 2 and 3 below claim.

Note also the explicit `served = lexical_result` in the `except` handler. It is redundant with the
initializer on today's control flow, but it is cheap insurance against a future edit that assigns `served`
before the throwing call.

Three invariants this shape enforces, each of which is load-bearing:

1. `decide(scored_agents, ...)` is still computed unconditionally on every scored dispatch. This is what
   keeps the KC baseline arm alive (§5).
2. A compose exception leaves `served = lexical_result`. The existing never-break-live-dispatch contract
   (`_main.py:305`) becomes a routing fail-safe for free.
3. `_write_log_entry` receives `served`, not `lexical_result`, so the top-level log entry keeps meaning
   "what the matcher emitted."

**On `hard_routing_domains` being passed, not re-read.** `_build_shadow_record` must receive the resolved
set as a parameter rather than calling `_parse_hard_routing_domains()` itself. Parsing once in `main()`
guarantees the value that drove the `served = shadow` branch is the identical value recorded in the log —
a re-parse could observe a mutated environment and silently record a set that never applied. It also keeps
`_build_shadow_record` a pure function of its arguments, which is what makes it unit-testable. Serialize
it as a sorted list for stable JSON.

See D-FLAG3 (§9) for the `domain is not None` guard and the `domain=None` question.

---

## 3. Return-shape parity — `compose_route`'s posture-routed branches (own sub-task)

`compose_route`'s posture-routed return (`_compose.py:588`–`:593`) carries exactly four keys:
`decision`, `agent`, `confidence`, `disposition_source`. `decide()`'s delegate branch
(`_decide.py:214`–`:222`) carries seven: those four plus `skills`, `rationale`, `alternatives`.

**Serving the current shape is a real regression, not a cosmetic one.** A posture-routed delegate would
silently drop skill attachment — `code-writer` routed via `("code","build")` would arrive with no
`python` skill. Nothing crashes (`cli.py:98`, `:103`, `:106` all use `.get()` with defaults, so
`rationale` degrades to an empty line and `skills`/`alternatives` to omitted lines), which is precisely
what makes it dangerous: it degrades silently.

Note this affects **only** the posture-routed branches. The fallback branch already returns a full
`decide()` dict (`_compose.py:563`) and needs no parity work.

**Scope this as its own issue, merged and green before the flag is ever set non-empty.** Parity work
is behavior-preserving-by-construction while `hard_routing_domains` is empty (the enriched dict is
computed but only ever logged under `shadow_*`), so it can land independently and be validated in shadow.

Per branch:

| Branch | `skills` | `alternatives` | `rationale` |
|---|---|---|---|
| 1 — `investigator` (`_compose.py:447`) | `_skills_for_agent(entry, scored_skills, features)` | `_top_alternatives([se for se in gated if se.entry.name != agent], n=3)` | `"posture route: diagnose × area_span>=2 → investigator"` |
| 2 — sentinel `self_handle` (`:467`) | `[se.entry.name for se in scored_skills if se.score >= _SKILL_MIN][:_MAX_SKILLS]` — mirrors `_decide.py:202`, `:228` | `[]` (mirrors `_decide.py:234`) | `"posture route: project_meta × build → router self-handles"` |
| 3 — generic / testfirst / ops-veto (`:524`, `:538`, `:501`) | `_skills_for_agent(entry, ...)` for delegates; ops-veto rows mirror Branch 2 | same as Branch 1 | include the branch name and, for vetoes, `posture_veto_reason` |

Two design points to settle (D-PARITY1/2, §9):

- **`alternatives` over `gated` or ungated?** Recommend **`gated`**, excluding the routed agent — it is
  consistent with the gate the route came from, and an "alternative" the domain gate excludes is not a
  real alternative. This differs from `decide()`'s ungated behavior on non-gated domains only.
- **Where does the `CatalogEntry` come from?** `_skills_for_agent` needs the entry, not the name. Take it
  from the matching `ScoredEntry` in `gated`/`scored_agents`. Branch 1 has a hazard: `investigator` is
  routed because it is in `catalog_agent_names` (`_compose.py:444`), which does **not** guarantee it
  appears in `gated`. Specify the lookup as catalog-wide with a `None`-safe fallback to `skills=[]`.
  **This fallback must be pinned by a synthetic test (§6 T-1b) before the `code` widening**, because the
  `is_any` first flip cannot exercise it — `gate_agents` never filters for `is_any`, so `gated ==
  scored_agents` and the miss branch is unreachable during the entire first soak.

Parity is exercised by a **table-driven test asserting the key sets match `decide()`'s for the same
decision type** (§6, T-1) — not by hand-enumerated per-branch assertions, so a future eighth key added to
`decide()` fails the test rather than silently diverging.

---

## 4. Domain-vs-posture granularity mismatch

The evidence is **posture**-sliced. The only designed mechanism is **domain**-scoped. These do not
compose: `operate` spans `is_any`, `code`, `infra_deploy`, `docs_prose`, and `project_meta`, so no domain
set can express "trust `operate` everywhere."

**Decision: accept domain-level gating for this cutover; posture-scoped gating is explicitly OUT OF
SCOPE and named as a successor issue.** Reasons:

1. Adding a second orthogonal flag dimension is new design surface (interaction semantics, precedence,
   rollback matrix) on the critical path of a cutover the user wants to be *narrow and reversible*.
2. §8's recommended first flip (`is_any` only) is the one domain where the mismatch **costs nothing** —
   see below. The mismatch is a real constraint on *widening*, not on *starting*.
3. The risk characterization must therefore be **per-domain**, not per-posture. §8 does that.

**Successor issue (do not fold into this cutover):** "Posture-scoped hard-routing gate
(`hard_routing_postures`)" — needed before widening to `code` or `infra_deploy`, because those domains'
long-tail postures (`assess`/`plan`/`verify`/`research`/`critique`, n=1–6 each) are exactly the cells
with no evidence, and a domain-scoped flag cannot exclude them.

---

## 5. Auditing continuity — the `agreement` landmine and its fix

### 5.1 The precise mechanism

`scripts/corpus/eval/_kc.py:85`–`:110` (`_system_results`) reads `shadow[f"{arm}_decision"]`,
`shadow[f"{arm}_agent"]`, `shadow[f"{arm}_confidence"]` for `arm ∈ {"shadow", "live"}`. **The KC tooling
names arms by algorithm, not by what was served**: `live_*` means "the lexical `decide()` arm" and
`shadow_*` means "the `compose_route` arm." KC-1 compares them directly (`_kc.py:126`–`:135`).

Today the two meanings coincide, because `decide()`'s output is both the lexical arm and the served
decision (`_main.py:90`–`:93`, `:281`). **After cutover they diverge**, and the field names become
ambiguous. This is where the landmine lives.

### 5.2 The fix: do NOT re-point `live_*`

The naive cutover — "we now serve compose, so `live_*` should hold what we serve" — is what breaks
everything. Under it, on every posture-routed row `live_agent` and `shadow_agent` both come from compose,
so `agreement` (`_main.py:107`) becomes trivially `True`, KC-1's `lexical_rc` silently becomes a second
copy of `shadow_rc`, and the regression guard reads PASS by construction at the exact moment it matters
most.

**Fix — additive, zero KC-tooling churn:**

1. **Keep `live_*` sourced from `decide(scored_agents, ...)`, forever.** It is already computed
   unconditionally (`_main.py:281`); the §2.4 wiring keeps it that way. `live_*` continues to mean "the
   lexical baseline arm."
2. **Keep `shadow_*` sourced from `compose_route`.** Unchanged.
3. **Keep `agreement = live_agent == shadow_agent`.** It continues to mean "the two algorithms agree" —
   *not* trivially true, because the two arms are still computed independently.
4. **ADD** `served_arm: "lexical" | "compose"`, plus `served_decision` / `served_agent` /
   `served_confidence` / `served_disposition_source` mirroring what actually went to stdout.
5. **ADD** `shadow_schema_version: 2` so a joined corpus can distinguish pre- and post-cutover rows. Rows
   without the key are v1 by definition.
6. **ADD** `hard_routing_domains` (the resolved set, sorted list) so a row is self-describing about the
   flag state that produced it — essential for the post-flip forensics this whole section exists to
   enable.

The field names `live_*` / `shadow_*` become historical misnomers. That is the deliberate trade: renaming
to `lexical_*` / `compose_*` would be clearer but would break every joined corpus and require dual-read
in `_kc.py`, `scripts/shadow-kc-report.py`, and `scripts/shadow-summary.py:72`. Document the misnomer in
`_build_shadow_record`'s docstring rather than paying that cost mid-cutover. (Rename is a candidate for
the same successor milestone as §4.)

### 5.3 Why this does not break the existing schema contract test

`tests/test_corpus_eval/test_kc_schema_contract.py` asserts the **absence** of required keys is tolerated,
and `tests/test_match/test_shadow_mode.py:334` computes `_SHADOW_REQUIRED_KEYS - set(shadow.keys())` — a
**subset** check. Both are satisfied by purely additive keys. Stated here so a reviewer does not re-derive
it. The one test that does break is handled in §6, T-6.

### 5.4 Operator visibility during the soak — `shadow-summary.py` ships in this milestone

Writing `served_arm` into every record is necessary but not sufficient: during the D-SOAK1 soak an
operator needs to see, without writing ad-hoc JSONL queries, **what fraction of dispatches compose
actually served**. Otherwise "no regressions observed" is unfalsifiable — indistinguishable from "the flag
never fired," which after §0 P-1 is the *expected* state for a while.

`scripts/shadow-summary.py` already walks every log entry's `shadow` block and counts `agreement`
(`:72`–`:73`, `:81`), so this is a small additive change at an existing loop:

- Count `served_arm` values → emit `served: compose=N lexical=M` alongside the existing
  `agreement=N/S` line (`:151`–`:155`).
- Records lacking `served_arm` (schema v1, pre-cutover) count as `lexical` — correct by construction,
  since before the cutover lexical was always what was served.
- Surface the distinct `hard_routing_domains` values seen, so a summary makes the flag state that
  produced the sample self-evident.

**This ships in the same milestone as the cutover, not as a follow-on.** It is the instrument for the
soak; an instrument delivered after the experiment is worthless. Accordingly `scripts/shadow-summary.py`
is **not** READ-NOT-MODIFY in this plan's `touches:` — unlike `_kc.py` and `shadow-kc-report.py`, it is
not part of the provenance-guarded dependency set (§10 G4), so editing it is safe for the corpus.

---

## 6. Test plan

New file `tests/test_match/test_hard_routing.py` mirrors the structure of
`tests/test_match/test_shadow_flag.py` — including its worktree-shadowing guard
(`test_shadow_flag.py:67`–`:75`), which must be copied verbatim into any new test module in this package.

New file `tests/test_match/test_compose_parity.py` for §3.

| # | Test | Asserts | Why it exists |
|---|---|---|---|
| T-1 | Return-shape parity (table-driven) | For each posture-routed branch, the returned key set equals `decide()`'s key set for the same `decision` value | §3 — catches silent skill-drop, and catches a *future* key added to `decide()` |
| T-1b | **Branch-1 agent absent from `gated`** | Synthetic fixture where `investigator` is in `catalog_agent_names` but **not** in `gated` (e.g. `domain=docs_prose`, whose gate is `{"doc-writer"} ∪ ANY_DOMAIN_AGENTS`, with a scored list where investigator is filtered out). Assert the D-PARITY2 fallback yields `skills=[]` and does not raise | Without this, the fallback ships **unexercised**: the `is_any` first flip never filters `gated`, so this path first executes during the later `code` widening — untested, in production |
| T-2 | Flag absent ⇒ stdout is the pure-lexical decision | With the flag unset, stdout equals `decide(scored_agents, scored_skills, features, entries)` computed **in-process from the same fixture** — not "vs. a pre-change build," which pytest cannot run | Fail-closed default (§2.2) |
| T-3 | Flag parsing matrix | Empty / whitespace / typo'd token / mixed case / duplicate → resolved set per §2.2 table; typo emits stderr; nothing raises | Set-flag default safety, all three cases |
| T-4 | **Posture-routed row is served** (end-to-end via `main()`) | With the flag set and `domain`/`posture`/`confidence=high` supplied, **stdout** carries `disposition_source == "posture_routed"` and the cell-preferred agent | The gap named in the brief: no end-to-end test today exercises compose output as served stdout |
| T-5 | **Gated-fallback row is served** (end-to-end) | Fixture: `domain=code`, **no posture**, and a catalog whose **top-scoring agent is outside `DOMAIN_AGENT_MAP["code"]`** (`_cells.py:53`–`:69`) — e.g. `doc-writer` wins lexically. Assert the *specific* agent: unflagged ⇒ `doc-writer`; flagged ⇒ the top surviving `code`-gated agent. **Not** "may differ" — that is unfalsifiable | §1 — the surface everyone under-estimates. **Highest-value test in this plan.** |
| T-6 | `test_shadow_mode.py::test_stdout_decision_matches_served_fields_in_log` **re-pointed** | Assert `shadow["served_*"] == stdout[...]`, not `shadow["live_*"] == stdout[...]` | Deliberate update — see below |
| T-7 | Paired canary (new, alongside T-6) | `shadow["live_*"]` equals pure-lexical `decide(scored_agents, ...)` and **may differ from stdout** on a posture-routed row | Preserves what T-6 used to protect: that `live_*` is the untouched baseline arm |
| T-8 | `agreement` is not trivially true | Construct a posture-routed row where compose and lexical pick different agents; assert `agreement is False` while it is served by compose | Directly pins the §5 landmine |
| T-9 | `DISPATCH_SHADOW=0` disables hard routing | Flag set non-empty + `DISPATCH_SHADOW=0` ⇒ stdout reverts to `decide()` and log has no `shadow` key | Pins the §2.3(a) documented kill switch |
| T-10 | Compose raises ⇒ lexical served | Monkeypatch `compose_route` to raise with the flag set; stdout is the lexical decision, exit 0 | Extends `test_shadow_mode.py`'s B3 to the served path |
| T-11 | Unflagged domain unaffected | Flag `{"is_any"}`, request `domain=code` ⇒ stdout byte-identical to unflagged | Domain scoping actually scopes |

**On T-6 — the canary, handled consciously.** `test_shadow_mode.py:252` asserts
`shadow["live_decision"] == stdout_decision["decision"]`. That assertion encodes an invariant that is
*true today and deliberately false after cutover*. Treat a T-6 failure during implementation as a
checkpoint, not a chore: **if it fails and `served_*` does not yet exist, the §5 schema fix was skipped.**
The re-point must be paired with T-7 in the same commit, and the test docstring must state the reason. A
lone re-point with no T-7 loses the canary permanently.

**Existing tests expected to stay green unchanged:** all of `test_shadow_flag.py` (the shadow gate is
untouched), `test_shadow_mode.py` B2/B3/B4, and all of `test_kc_schema_contract.py` (§5.3).

---

## 7. Rollback rehearsal — scripted, not a design property

`docs/superpowers/plans/2026-06-19-matcher-v3-ship-live.md:628`–`:632` names a pre-flip rehearsal as a P4
exit criterion. It has not been performed. Below is the executable form. **Run it on `main` with the flag
still unset in the environment; each step is a single-shot `main()` invocation against a fixed catalog and
a fixed stdin fixture.**

Two fixtures are required, per §1:

- **Fixture A (posture-routed):** `{"domain": "is_any", "posture": "operate", "confidence": "high", ...}`
- **Fixture B (gated-fallback):** `{"domain": "code", ...}` with **no `posture` key** — the row shape that
  changes candidate sets with no posture route involved.

| Step | Action | Expected |
|---|---|---|
| R1 | Flag unset. Run A and B. Save stdout as `baseline_A.json`, `baseline_B.json`. | Both equal today's lexical output. |
| R2 | Set flag to the first-flip set. Run A. | `disposition_source == "posture_routed"`; agent is the cell-preferred one; differs from `baseline_A`. |
| R3 | Set flag to `{"code"}`. Run B. | Stdout **may differ** from `baseline_B`. **Record whether it did.** A no-diff result is not a pass — it means the fixture failed to exercise the gate; pick a fixture whose top-scoring agent is outside `DOMAIN_AGENT_MAP["code"]` and repeat. |
| R4 | Unset the flag (or set it empty). Re-run A and B. | Stdout **byte-identical** to `baseline_A` / `baseline_B`. This is the Layer-1 rollback proof. |
| R5 | Flag set non-empty **and** `DISPATCH_SHADOW=0`. Run A. | Stdout equals `baseline_A`; log entry has no `shadow` key. Confirms the §2.3 kill switch. |
| R6 | Inspect the R2 log line. | `served_arm == "compose"`, `served_agent` == stdout agent, `live_agent` == `baseline_A`'s agent, `agreement` reflects a genuine comparison. |

R3 and R6 are the two steps a naive rehearsal omits, and they are the two that would have caught the §1
and §5 problems respectively. R4 is the exit criterion the prior plan named; R3/R6 are new here.

---

## 8. Rollout recommendation — first flip targets `{"is_any"}` only

This is a judgment call and it is stated explicitly rather than buried.

**Recommendation: the first flip sets `DISPATCH_HARD_ROUTING_DOMAINS=is_any` and nothing else.**

Three independent mechanical properties, each verified on disk, make `is_any` the uniquely low-risk
starting point:

1. **Zero gating blast radius.** `gate_agents` returns `scored_agents` unchanged for `"is_any"`
   (`_cells.py:155`–`:156`; `DOMAIN_AGENT_MAP` has no `"is_any"` key, `:52`–`:78`). Therefore
   `decide(gated, ...) == decide(scored_agents, ...)` and the §1 hazard **does not apply**. The only
   behavior change is a genuine posture route. `is_any` is the *only* domain with this property.
2. **Cells resolve through the `any` row.** `_compose.py:409`–`:413` normalizes `is_any → "any"` before
   `cell_map_lookup`, so the fired cells are `("any", posture)` — including `("any","operate") → "ops"`
   and `("any","build") → "code-writer"` (`_cells.py:101`, `:86`), the two postures the user's evidence
   supports.
3. **`("any","operate") → "ops"` carries an extra backstop already.** The #448 tool-shape guard vetoes an
   `ops` route to `self_handle` unless a GitHub read-signal is present (`_compose.py:491`–`:513`). The
   highest-volume cell in the first flip is the one with the most guarding.

It also aligns with the evidence: `is_any × operate` is reported at ~3% provenance-drift exclusion — the
least-diluted cell in the corpus — versus 58–75% for `docs_prose × build`/`verify`. *(Router-supplied
from this session's slice investigation; not independently recomputed here — see §10 G3.)*

**Counter-consideration, stated honestly.** `is_any` is also the domain where the D-KC-GUARD1
`genuine_gated_names` guard is a **no-op**: `DOMAIN_AGENT_MAP.get("is_any")` returns `None`, so
`genuine_gated_names = gated_names` = every scored agent (`_compose.py:485`–`:490`). For `is_any`, the
only Branch-3 backstops are the `confidence=high` gate and the §B.1 plausibility veto. That is a thinner
guard stack than `code` would have — but it is the *documented, measured* configuration: the shadow
corpus's `is_any` rows were produced under exactly these conditions, so the measured numbers already
price it in. Widening to `code` trades this for the §1 gating blast radius, which is **not** priced in for
the fallback rows.

**Do NOT include in the first flip:**

- `project_meta` — its Branch 2 (`("project_meta","build") → SELF_HANDLE_SENTINEL`) is gated by
  **neither** `confidence_is_high` **nor** the plausibility veto (`_compose.py:466`–`:471`). Flipping
  `project_meta` changes behavior even when the caller emits no confidence at all. It is the one domain
  where the fail-safe genuinely does not apply, so it must be flipped on its own with its own evidence.
- `infra_deploy` — KC-5 is `INSUFFICIENT_DATA` at `slice_n: 7`, thirteen rows short of the
  `_KC5_MIN_SLICE_N: 20` floor (`docs/research/2026-07-27-shadow-kc-report.md:154`, `:218`;
  `scripts/corpus/eval/_kc.py:32`). No evidence either way.
- `docs_prose` — highest provenance-drift exclusion rate (58–75% on `build`/`verify`), so its surviving
  rows are the least representative. *(Router-supplied; see §10 G3.)*
- `code` — defensible **second**, but only after §1's gated-fallback surface has been observed in
  production on `is_any`… which it cannot be, since `is_any` has no gating. So `code`'s first flip needs
  its own gated-fallback rehearsal and its own soak. It also carries the §4 long-tail-posture exposure.

### 8.1 Supporting evidence — and a correction to an earlier draft of this section

> **CORRECTION (2026-07-31).** An earlier revision of this plan cited the gated-eligible cut
> (`shadow_rc = 0.8182`, `eligible_n: 65`, ~28% of rows) as evidence that the **`is_any` first flip** is
> well-supported and non-inert. **That citation was wrong and is withdrawn.** `compute_kc3`'s eligibility
> predicate is `is_gated and cell_exists and confidence == "high"`, where
> `is_gated = domain not in (None, "is_any")` (`scripts/corpus/eval/_kc.py:200`, `:205`). **KC-3
> eligibility explicitly EXCLUDES `is_any` rows.** Both the `0.8182` rate and the 65-row count therefore
> describe `code` / `docs_prose` / `project_meta` / `infra_deploy` traffic and say **nothing** about
> `is_any`. Recording the error rather than quietly restating it, per
> `CLAUDE.md § Cite Sources in Planning Artifacts`.

What the corrected reading actually establishes:

1. **`shadow_rc = 0.8182` on the gated-eligible cut** (n=65, vs whole-sample `0.6707`,
   `docs/research/2026-07-27-shadow-kc-report.md:196`–`:197`) clears the KC-1 absolute floor of `0.6891`
   (`scripts/corpus/eval/_kc.py:26`) by 12.9pp. This is genuine evidence — **for the gated domains,
   i.e. for the later `code` widening, not for the `is_any` first flip.** Usefully, it is measured on rows
   that already ran through `gate_agents`, so the §1 gated-fallback effect is *included* in that number
   rather than being an unmeasured risk.
2. **`confidence: "high"` is genuinely being emitted** — `eligible_n: 65` is unreachable otherwise
   (`_kc.py:205`). Reconciling with §0 P-1 (production callers emit nothing): this traffic comes from the
   **maintainer's own dev environment** running this repo's HEAD `skills/dispatch/SKILL.md`, not from
   marketplace-pinned installs. Both facts are true at once, and the distinction is exactly what §0 P-2
   is about.
3. **Nothing on disk quantifies `is_any` posture-route volume or accuracy.** The user's posture slices
   (`operate` n=59 rc 0.780, `build` n=27 rc 0.704) presumably include `is_any` rows — the brief's
   `is_any × operate` 3%-exclusion figure implies they do — but those are router-supplied and unverified
   here (§10 G3).

**Does the `is_any` recommendation survive?** Yes, but on **safety** grounds only, not on measured-quality
grounds. Its case is the three mechanical properties above (zero gating blast radius, `("any", posture)`
cell resolution, the `ops` tool-shape backstop) — all verified on disk and none of which depend on the
withdrawn statistics. The honest framing: *`is_any` is the flip whose failure mode is smallest and whose
rollback is cleanest, not the flip with the strongest accuracy evidence.*

**New gate, folded into §10 G3.** Because no committed artifact quantifies `is_any` routing quality,
G3's re-derivation must now **explicitly report an `is_any`-only slice** (n, posture mix, shadow_rc vs
lexical_rc) before the first flip. Without it, the first flip proceeds on mechanical-safety reasoning
alone — defensible, but it should be a stated choice rather than an unnoticed gap.

**Standing caveat on both cut rates.** The report warns they are computed over a gold-labeled subset that
has not grown since the 2026-07-19 run (the 120-row gold set is unchanged), so they are "not
independently reconfirmed by the larger corpus"
(`docs/research/2026-07-27-shadow-kc-report.md:199`–`:205`). Corroborating, not conclusive.

---

## 9. DECISIONS REQUIRED

| ID | Question | Recommendation |
|---|---|---|
| **D-FLAG1** | Flag shape: env var / CLI / config? | **Env var `DISPATCH_HARD_ROUTING_DOMAINS`**, comma-separated (§2.1) |
| **D-FLAG2** | Malformed element: drop-with-warning, or poison the whole set? | **Drop the unknown token, warn on stderr, never raise** (§2.2) |
| **D-FLAG3** | Does `domain: null` / absent ever hard-route? Semantically `null ≡ is_any` (`skills/dispatch/SKILL.md:121`, `:134`), so a literal reading says yes. | **No — require an explicit `is_any`.** Strictly narrower: an older caller emitting no domain cannot be swept into hard routing by a flag it never saw. Costs some coverage; buys a clean "only callers that opted into labeling are affected." **User's call — this one is a genuine trade.** |
| **D-FLAG4** | Two-flag interaction (§2.3) | **(a)** — compose stays inside the shadow gate; `DISPATCH_SHADOW=0` is the documented coarse kill switch |
| **D-PARITY1** | `alternatives` computed over `gated` or ungated? | **`gated`**, excluding the routed agent (§3) |
| **D-PARITY2** | Branch-1 `investigator` not necessarily in `gated` — skills lookup source? | Catalog-wide lookup, `None`-safe fallback to `skills=[]` (§3) |
| **D-SCOPE1** | Posture-scoped gating in this cutover? | **No — out of scope, successor issue** (§4) |
| **D-ROLL1** | First-flip domain set | **`{"is_any"}`** (§8) |
| **D-SOAK1** | How long / how many dispatches between the first flip and any widening? **And on whose traffic?** | Needs a number *and* a population. The "~28% eligible" basis from an earlier draft is **withdrawn** (§8.1 — KC-3 excludes `is_any`), and per §0 P-1 production emits no labels at all, so a production soak is unmeasurable until X0 clears. Two coherent options: **(a)** wait for X0 and soak on real traffic; **(b)** scope the first soak explicitly as a *dev-environment* rehearsal on maintainer traffic, with a stated smaller N. Recommend **(b)** as an interim, since it is available now and X1/X2 are unblocked — but it must be labeled as such, not reported as production validation. |
| **D-SEMVER1** | Reconcile the stale SemVer recommendation | See below |

**D-SEMVER1 detail.** The prior plan recommended `1.3.0` to close M15 with the flag default-off, deferring
`2.0.0` to a later default-on flip (`docs/superpowers/plans/2026-06-19-matcher-v3-ship-live.md:545`–`:558`).
The repo is already at `v1.4.0`, so that specific recommendation is stale by version, but its *reasoning*
survives intact and applies here: **this cutover ships the flag defaulting to OFF, so default behavior on
the new version is byte-identical to the current one (T-2 pins exactly that). That is a minor bump —
`1.5.0` — not a major one.** `2.0.0` remains correctly deferred to whenever `hard_routing_domains` gains a
non-empty *default*, which this plan does not propose. The current version is **verified `1.4.0`** in both
files (`pyproject.toml:7`, `.claude-plugin/plugin.json:3`), so `1.5.0` is the next minor.
**Confirm `1.5.0`.**

---

## 10. Verification gates — MUST clear before the flag is set non-empty

Three items this plan could not verify. **Do not treat any as satisfied.**

**G1 — Dispatch-caller `confidence` emission — RESOLVED NEGATIVE. See §0 P-1.**
`glitchwerks/claude-configs#978` was closed **without merging**, so production callers emit no labels.
An earlier revision of this plan read `skills/dispatch/SKILL.md` (`:65`–`:68`, `:121`–`:161`, which does
document the full rubric) plus KC-3's `eligible_n: 65` as evidence the dependency was met. That inference
was wrong in scope, not in fact: the skill file *is* correct at repo HEAD, and the 65 rows *are* real —
but both describe the **maintainer's dev environment**, not deployed installs (§8.1 item 2). The rubric
existing in this repo never implied the *caller* repo shipped it. **Gate is OPEN and blocks X3.**

**G2 — Marketplace pin — RESOLVED NEGATIVE. See §0 P-2.** Pin is `v1.2.0`, **108 commits behind** `main`
(worse than the 47 carried from `docs/superpowers/plans/2026-06-19-matcher-v3-ship-live.md:386`) and
predates Matcher v3 entirely. **Gate is OPEN and blocks X3.**

**G2b — Remaining GitHub issue state: still `unverified:`.** This sub-agent has no `Bash` or
`mcp__github__*` tools; the router verified P-1/P-2 but the following were not re-checked and remain
carried from the dispatching brief:

- `#424` "M15-7: hard_routing_domains flag" — believed open. **This plan's §2/§3/§6 should become its
  scope**, not a new issue. Verify, then either re-scope #424 or supersede it explicitly.
- `#426` "M15-11: shipping/integration wiring spec" — believed open. This plan plausibly **satisfies and
  closes** it; confirm rather than writing a second document.
- `#415` "Ship Matcher v3 live" — believed open; the umbrella. Should track §7's rehearsal and §0/§10's
  gates, and now also the two external X0 prerequisites.

Recommend the parity work (§3) be its own new issue regardless, since it is a distinct, independently
mergeable deliverable that #424's title does not describe. Recommend a **new tracking issue for §0 X0**
that references `claude-configs#978`'s successor and the pin bump, so the external blockers are visible
from this repo's backlog rather than living only in this plan file.

**G3 — Slice metrics: router-supplied, not recomputed; plus a new `is_any` requirement.** The `operate`
(n=59, rc 0.780) / `build` (n=27, rc 0.704) slices and the 3%/58–75% exclusion-rate spread are
**router-supplied from this session's investigation** and were not independently recomputed here (they
require running `scripts/corpus/eval/_metrics.py` against a corpus at
`~/.claude/state/wayfinder-corpus/`, outside this agent's reach). They are load-bearing for §8's ranking.
**Re-derive and record them as a committed artifact before the flip** — this plan must not be the only
place they exist, since a plan file is deleted at completion per `CLAUDE.md § Document Files`.

**Added by the §8.1 correction:** that re-derivation must include an **`is_any`-only slice** (n, posture
mix, `shadow_rc` vs `lexical_rc`). No committed artifact currently quantifies `is_any` routing quality —
KC-3 and the gated-eligible cut both structurally exclude it (`scripts/corpus/eval/_kc.py:200`) — so
without this the first flip rests on mechanical-safety reasoning alone.

**G4 — Does the §3 parity fix invalidate the provenance partition? — RESOLVED: No, but with one hard
constraint and one exit check.**

The concern is real in shape: the provenance guard already excludes 74 rows for `_cells.py` dependency
drift, and the drift fraction is **0.2665 against a 0.25 gate** — i.e. *already over*
(`docs/research/2026-07-27-shadow-kc-report.md:135`, `:139`–`:142`). Blinding the baseline corpus at the
moment of the flip would defeat §5 by a different mechanism. Resolved by reading the guard:

1. **`_compose.py` is NOT a blanket-exclusion trigger.** `_dependency_drift_reason` loops only
   `_TRANSITIVE_DEPENDENCY_MODULES` (`scripts/shadow-kc-report.py:486`), and `_COMPOSE_MODULE_PATH` is
   deliberately *not* a member of that tuple (`:65`–`:74`). A `_compose.py` edit instead routes the row
   through the import rig, which loads `compose_route` at both revisions and compares **outputs**.
2. **The output comparison is restricted to three fields.** `_compose_decision` returns exactly
   `{"agent", "decision", "posture_routed"}` (`:696`–`:700`). The §3 parity fix is **purely additive** —
   it adds `skills`, `alternatives`, and `rationale` and changes none of those three. **Therefore it
   produces zero new exclusions.**

**Hard constraint that follows:** `_decide.py` **IS** in `_TRANSITIVE_DEPENDENCY_MODULES` (`:69`), as are
`_cells.py`, `_types.py`, `_match.py`, `_stem.py`, and `match_filters.py` (`:68`–`:73`). Editing any of
them blanket-excludes **every** row stamped before that commit, regardless of whether behavior changed.
This is why §3 specifies that parity logic lives entirely in `_compose.py` and reuses `_decide.py`'s
helpers read-only, and why the frontmatter marks `_decide.py` and `_cells.py` READ-NOT-MODIFY. Treat any
proposal to "just add a helper to `_decide.py`" as a corpus-destroying change and reject it.

**Exit check (cheap, do it anyway):** after the parity commit lands and before the flip, re-run
`scripts/shadow-kc-report.py` and confirm the excluded-row count has **not grown**. Precedent that this
check has teeth: two rows (`corpus_id` 57925, 59238) are already excluded for a genuine baseline-vs-HEAD
`agent` disagreement (`docs/research/2026-07-27-shadow-kc-report.md:135`), so output-level drift does get
caught here. `_main.py` is not in the guarded set at all, so §2/§5's wiring changes are invisible to it.

---

## 11. Known blind spots (acknowledged, not addressed)

**Override path.** Override-matched requests short-circuit at `_main.py:243`–`:272`, before scoring,
compose, and the shadow block. They therefore produce no comparison data — today or after cutover — and
hard routing never applies to them. Zero occurrences in the current 319-row corpus sample, so this is
dormant rather than active. No action; recorded so a future reader does not mistake the absence of
override rows for coverage.

**Batch dispatch — deliberately NOT hard-routed; keep it that way.**
`_dispatch.py::run_batch_dispatch()` runs its own inline `score_entries → decide()` loop
(`src/claude_wayfinder/match/_dispatch.py:606`–`:611`) with no `parse_labels`, no `compose_route`, no
shadow record, and no `hard_routing_domains` check. This is **not** a live-serving gap: `--batch` is
offline corpus-eval tooling only (zero `--batch` invocations anywhere in `skills/`). The divergence is
intentional and must be preserved. **A future implementer who reads it as an inconsistency and "fixes" it
by routing batch through `main()` would contaminate the provenance partition that §10 G4 protects** — the
batch harness is what regenerates corpus rows, so making it emit compose-served decisions would destroy
the independent baseline the whole KC framework compares against. `_dispatch.py` is marked
READ-NOT-MODIFY in `touches:` for this reason.

---

## 12. Definition of done

- `hard_routing_domains` implemented per §2, with the §2.2 parse matrix pinned by T-3.
- `compose_route` posture-routed branches at full parity with `decide()` (§3), pinned by T-1.
- `_build_shadow_record` extended per §5 with `served_*`, `served_arm`, `shadow_schema_version`,
  `hard_routing_domains`; `live_*` still sourced from `decide(scored_agents, ...)`; `agreement`
  demonstrably non-trivial (T-8).
- `scripts/shadow-summary.py` reports the `served_arm` distribution (§5.4) — shipped **with** the cutover,
  not after it.
- All twelve tests in §6 green (T-1, T-1b, T-2…T-11); `test_shadow_mode.py` T-6 re-point landed **with**
  T-7 in the same commit; T-1b pins the D-PARITY2 fallback before any `code` widening.
- §7 rehearsal executed and its R1–R6 results recorded on the tracking issue — **including R3's
  gated-fallback diff**.
- **§0 X0 prerequisites cleared:** `claude-configs#978`'s successor PR merged (`mergedAt` non-null) and
  the marketplace pin live-verified at ≥ the cutover version. Without both, X3 does not start.
- **§10 G4 exit check performed:** `scripts/shadow-kc-report.py` re-run after the parity commit and
  **before** the flip, with the excluded-row count confirmed **not grown** vs the 2026-07-27 baseline of
  76 excluded / 0.2665 drift.
- §10 G1/G2/G2b/G3 all cleared and recorded — G3 including the new `is_any`-only slice.
- Flag flipped to `{"is_any"}` only; soak per D-SOAK1; KC report re-run before any widening.
- Release per `docs/maintenance/release-process.md` at the D-SEMVER1 version (recommended `1.5.0`),
  including the marketplace pin bump — which G1 shows is not optional here.

---

## 13. Citations

Decision-driving claims cite one of: live matcher source
(`src/claude_wayfinder/match/_main.py:Lx`, `_compose.py:Lx`, `_cells.py:Lx`, `_decide.py:Lx`,
`src/claude_wayfinder/cli.py:Lx`), the KC kernel (`scripts/corpus/eval/_kc.py:Lx`), the current test
suite (`tests/test_match/test_shadow_mode.py:Lx`, `test_shadow_flag.py:Lx`,
`tests/test_corpus_eval/test_kc_schema_contract.py:Lx`), the latest KC report
(`docs/research/2026-07-27-shadow-kc-report.md:Lx`), the caller contract
(`skills/dispatch/SKILL.md:Lx`), or the superseded plan
(`docs/superpowers/plans/2026-06-19-matcher-v3-ship-live.md:Lx`) — inline at point of use.

**`unverified:` flags (remaining after the 2026-07-31 router verification pass):**

1. **Issue state for #415 / #424 / #426** — still unchecked; this agent has no GitHub or Bash tool.
   §10 G2b.
2. **Posture-slice metrics and provenance-exclusion rates** (`operate` n=59 rc 0.780, `build` n=27
   rc 0.704, 3%/58–75% exclusion spread) — router-supplied, not recomputed. §10 G3.

**Resolved by the router's 2026-07-31 verification pass (no longer unverified):**

- `claude-configs#977` closed but its implementing PR `#978` **closed unmerged** (`mergedAt: null`) —
  §0 P-1, §10 G1.
- Marketplace pin at **`v1.2.0`, 108 commits behind** HEAD `35bd0a2` — §0 P-2, §10 G2. Supersedes the
  stale 47-commit figure carried from the June plan.

**Withdrawn (was cited in an earlier revision, now known wrong):**

- The gated-eligible cut (`shadow_rc 0.8182`, `eligible_n: 65`, ~28%) as support for the **`is_any`**
  first flip. `compute_kc3`'s eligibility requires `domain not in (None, "is_any")`
  (`scripts/corpus/eval/_kc.py:200`, `:205`), so those figures structurally exclude `is_any`. They remain
  valid evidence for the **gated domains**. Full correction in §8.1.

**Verified this pass (no longer unverified):** the repo version is `1.4.0` in both
`pyproject.toml:7` and `.claude-plugin/plugin.json:3`; the provenance guard's comparison basis and
guarded-module set (§10 G4) were read directly from `scripts/shadow-kc-report.py:65`–`:74`, `:486`,
`:696`–`:700`.
