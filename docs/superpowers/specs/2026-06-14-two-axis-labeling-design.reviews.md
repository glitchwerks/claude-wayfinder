# Review record — Matcher v3 two-axis labeling design (#362)

Companion to `2026-06-14-two-axis-labeling-design.md`. Captures the two architectural-review passes run on the spec on 2026-06-14 so the findings survive any spec revision. Source: `project-reviewer` (constructive, Sonnet) then `inquisitor` (adversarial, user-opted-in escalation).

**Router verification of the spec's two `unverified:` flags (Charge 10):**
- **#364** — confirmed **OPEN / unmerged** (2026-06-14). The §D.3 coordination contract holds: #362 ships against the current cell-map (incl. the `infra_deploy` 0.000-RC mis-cell); #364 owns the content fix and #362 absorbs it at runtime.
- **#366** — confirmed **NOT merged** (PR #373 OPEN, CI pending). The spec's §B.1 "just-merged" note is inaccurate, but harmless: §B.1 relies on the empty-after-gate fallback in `_cells.py:132–134`, which is original code; #366's fix is the `_systems.py` posture-pick guard, not that fallback.

---

## Pass 1 — project-reviewer (constructive): 2 BLOCKING, 5 CONCERN, 4 NIT

**BLOCKING**
1. **Shadow-log injection gap** (§F.1/G.1) — `_write_log_entry` (`_catalog.py:322–376`) is a closed dict literal; "extra shadow keys on the row" needs an explicit signature change (recommend `shadow_data: dict|None=None`, merged before `json.dumps`). impl-C testability + impl-D analysis script depend on the agreed log shape.
2. **`infra_deploy` carve-out needs a domain-scoped flag** (KC-5/§G.1) — §G.1 specs only a binary shadow→live toggle; the "carve out infra_deploy until #364 lands" option requires a domain-set exclude (`hard_routing_domains: set[str]`), not a bool. Either drop the carve-out (KC-5 fail = block entirely) or add the domain flag.

**CONCERN** — (a) `is_any` lookup normalization: `labels.domain or "any"` doesn't handle truthy `"is_any"`; use explicit `domain if domain not in (None,"is_any") else "any"`. (b) D-CONF1 fail-open sequencing: hard-routing could enable before the caller emits `label_confidence` — make caller deploy a named dependency of impl-E. (c) BLOCKING-1 prep PR needs a golden-equivalence test over the 168-corpus, not just "tests pass." (d) KC-3 decisiveness denominator undefined (must specify the eligible set). (e) `_write_log_entry` signature must be pinned in §G.1 change-sites.

**NIT** — `_catalog.py` load_catalog refs accurate; Option-A static-map recommendation sound; sequencing A→B→C→D→E sound; #366 unverified flag noted.

Reviewer confirmed: BLOCKING-1's two scoring paths are currently logic-identical → dedup is zero-risk extraction; `touches:` frontmatter complete for the recommended Option-A path.

---

## Pass 2 — inquisitor (adversarial, user-opted-in)

**VERDICT: Do not proceed to implementation on the current evidence basis.** The wiring design is competent; the evidence underneath is misrepresented in ways the spec's own cited sources contradict. That is a premise flaw, not a fixable detail.

**CHARGE 1 — Domain axis FAILED its pre-stated reliability target.** §A.2 cites single-rater accuracy (domain 69.7%, posture 92.7%) as label-quality evidence, but the inter-rater reliability (`gold-labeling-report.md:124–128`, vs targets set before measurement) was **domain 31/40 = 0.775, below the 0.85 target**; exact-cell 30/40 = 0.750 at threshold. Only posture (39/40) passed. The "69.7%" gold was itself adjudicated + rubric-patched (`rubric:133–142`, `:394`) so the 16 systematic `is_any→project_meta` errors land routing-neutral. A production caller reproduces the *un-adjudicated* 0.775-agreement noise, not post-patch gold.

**CHARGE 2 — The "conservative floor" is optimistic.** `run_real_label_compose` (`score_labeling.py:319–390`) reads **one** labeler dict from **one** uncommitted file (`.tmp/labeler-output.jsonl`). No second labeler, no variance estimate, no inter-rater check on the real labels. Labeler = same model family as the gold producer, scoring against gold that family produced under a rubric tuned on this same 168-entry corpus. Same-family error correlation + rubric overfit + n=1 (no CI) all point to optimism; "no conversation context" is one handicap against three optimism factors. 0.7431 is a single optimistic point estimate, not a floor.

**CHARGE 3 — Circular: no held-out distribution.** Corpus = 168 Phase-A dispatch-log entries from this project's own usage (35.1% repeated smoke probes). Gold agents, domain map, cell map, rubric all derived from / frozen against this one distribution. KC-1 (≥100 in-situ) draws from the same project's continued usage → tests "reproduces on data like what we fit to," not generalization. `infra_deploy` n=5 → 0.000 is the canary for distribution-shift exposure.

**CHARGE 4 — Confidence-gating (§D.1) is theater.** The load-bearing CW safeguard is entirely caller-supplied and unverifiable from inside the deterministic matcher; an overconfident "high" on a wrong label fires a confident-wrong 0.9 delegate and the safeguard never fires. No matcher-side calibration/agreement check. Default (D-CONF1) is **fail-open** (absent = high) on the explicitly CW-sensitive path. Direct evidence the population is overconfident: the gold corpus self-rated "low" on only **7/168** while domain accuracy was 69.7% (wrong far more often than unconfident). The spec also invents a new `label_confidence` field while ignoring the existing gold `confidence` field.

**CHARGE 5 — Auditability (wayfinder's defining property) is quietly spent.** The decisive judgment (domain+posture) moves to an opaque LLM call outside the matcher. A `posture_routed@0.9` delegate's rationale is "caller said domain=code, posture=build" — the matcher can't see/log/reproduce *why*, and re-labeling the same task isn't reproducible. The shadow-log captures *what* labels arrived, not *why*. Trades a fully-auditable lexical decision for one unfalsifiable from inside the matcher — the exact property #357's regex extractors were killed for lacking accuracy to justify. Spec never confronts the trade.

**CHARGE 6 — The 41pp gain is largely a tie-breaking artifact.** The research doc states the domain gain "works by unblocking the lexical signal that was already correct" (`358:104`,`:135`); 100% of the +16.5pp domain delta is tie-set artifacts (`:192`). The doc names the cheaper, dependency-free, auditability-preserving alternative **twice** — differentiate code-writer vs doc-writer in the lexical scorer (`:150`), or calibrate `_DELEGATE_GAP=0.2` (`:275`). The spec never quantifies the two-axis machine's marginal value over "calibrate the gap threshold + retain E8/E11 structured signals."

**CHARGE 7 — Kill criteria anchored to pass, not falsify.** KC-1 RC≥0.65 vs measured 0.7431 = 10pp rubber-stamp buffer; KC-3 ≥0.55 vs measured 0.98 = 43pp buffer. Only KC-2 (CW≤0.26) is an honest hard-block, and its enforcement depends entirely on the §D.1 safeguard Charge 4 shows is defeatable. Given Charges 2–3, real in-situ RC could sit near 0.65 and KC-1 would call it a pass.

**CHARGE 8 — BLOCKING-1 confirmed, and stronger than stated.** `_main.py:215–222` (inline) and `_match.py:469–502` (`score_entries`, never called by `main()`) are byte-identical logic, but the validated floor runs through `score_entries` — so the production matcher and the measured floor execute *different copies* today. The dedup's golden-equivalence test must assert against current `_main.py` stdout (live behavior), not merely against `score_entries`.

**CHARGE 9 — Invented `posture_routed` disposition rides a consumed serialization contract.** `disposition_source` is consumed (`cli.py:136–138` prints it; existing producers emit only `scored`/`override`). The spec asserts "consumers ignore unknown *keys*" but adds a new *value* on an existing key; the §F.2 shadow-analysis join (which computes the kill criteria) is exactly a consumer that switches on it. A schema bug in the discriminator field would corrupt KC measurement undetectably.

**CHARGE 10 — #364/#366 states load-bearing & unverified.** [Router-resolved above: #364 OPEN, #366 not merged — contract holds.]

**Required before code (inquisitor):** re-run the floor with a non-same-family labeler + a variance band; quantify the gap-threshold-calibration + E8/E11 alternative; add a matcher-side plausibility check that doesn't blindly trust caller confidence; re-anchor KC-1 to falsify rather than pass; (router has now confirmed #364/#366).
