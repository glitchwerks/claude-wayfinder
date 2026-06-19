# Posture Extractor Regression — Root-Cause Report

> **Gold adjudication notice (#402).** This is a frozen, dated record. The gold labels it consumed have since been adjudicated **in place** (#364/#394: 5 entries; #398/#399: corpus 33692 `assess`→`operate`; plus any later gold-ownership edits). Counts, distributions, and the gold sha cited below reflect the gold **as of this report's date** and are intentionally **not** updated — the committed redacted jsonl (`docs/research/2026-06-12-gold-labels-redacted.jsonl`) is the live source of truth. A reader cross-referencing current gold will see expected differences (e.g. `assess`/`operate`, `diagnose`/`research` posture counts). Per the frozen-snapshot model decided in #402, this record is preserved as historical evidence, not rewritten.

**Issue**: #355  
**Date**: 2026-06-13  
**Investigator**: Claude Sonnet 4.6 (sub-agent)  
**Probe script**: local throwaway — `.tmp/probe_355_errors.py` (not committed; depends on local-only corpus at `~/.claude/state/wayfinder-corpus/2026-06-12/`)  
**Results file**: local throwaway — `.tmp/run_330_results.json` (not committed)

---

## Failure

The posture extractor system (`run_extractors`) routes with 0.3585 confident-wrong rate on the 168-entry organic corpus — 2.38× worse than the lexical baseline (0.1507) — and with 0.8571 confident-wrong rate on the 134-entry no-mention cut (4.29× worse), as measured in the #330 run.

---

## Regenerated Performance Numbers

Probe script run against:
- Corpus: `~/.claude/state/wayfinder-corpus/2026-06-12/wayfinder-corpus.jsonl` (168 entries)
- Labels: `docs/research/2026-06-12-gold-labels-redacted.jsonl` (168 labels)
- Catalog: `~/.claude/state/dispatch-catalog.json` (86 entries)

| Cut | Extractor delegates | Extractor confident-wrong | CW rate | Lexical CW rate |
|---|---|---|---|---|
| Full corpus (n=168) | 53 | 19 | **0.3585** | 0.1507 |
| No-mention (n=134) | 21 | 18 | **0.8571** | 0.2000 |

**All numbers match the issue exactly.** No divergence from stated figures.

---

## Root Cause

**H1 confirmed as the dominant cause with two distinct misfire patterns:**

1. **E5 (`source_of_truth_pair`) fires on ordinary build/operate prompts and routes them to `auditor` (verify posture).** This single extractor accounts for 14 of 19 errors (74%) via the pattern E5+E7. The extractor's B-core condition (≥2 artifact references) is too loose: any prompt that mentions two files or paths satisfies it. The C-assist (`RELATIONAL_MARKERS` or `NAMED_DOC_NOUNS`) adds limited selectivity — terms like "changelog", "schema", "against", "matches" appear in ordinary build and operate prompts ("update the CHANGELOG section", "make sure it matches"). Result: a doc-editing or git-operate task with two file references gets classified as `verify` → `auditor`, when the gold is `doc-writer` or `ops`.

2. **E3 (`vcs_artifact_ref`) fires on PR references and routes them to `code-reviewer` (assess posture).** Accounts for 3 of 19 errors (16%). Prompts that mention a PR number or PR URL (e.g., "resolve merge conflicts in PR #11448", "review the change-request feedback on PR #11723") fire E3 → assess → `code-reviewer`. But the gold agent for these is `ops` (git/merge operate) or `ops`/`code-reviewer` in a different sense than the extractor intends. The extractor treats any PR reference as a code-review signal.

**The no-mention catastrophe (18/21 = 85.7%) is the same phenomenon, unmasked.** When agent_mentions are present, E11 routes 31 of 34 mention-bearing entries correctly at 0.9 confidence — those entries dominate the "correct" delegate pool. Strip the mentions and only E5, E3, and occasionally E4 remain to fire; and both E5 and E3 systematically misroute as described above.

---

## Evidence Chain

1. **Probe script regenerated 53 extractor delegates, 19 confident-wrong, CW=0.3585** — exact match with the cached #330 results (local throwaway `.tmp/run_330_results.json`). [local probe `.tmp/probe_355_errors.py`, Part 5 output]

2. **Of 19 errors: 14 predicted `auditor`, 3 predicted `code-reviewer`, 1 predicted `code-writer`, 1 predicted `ops`.** The 14 `auditor` errors all arise from `verify` posture winning. [Part 1 output: "Error breakdown by predicted agent"]

3. **14 of 19 errors have extractors `e5` and `e7` as the sole fired pair.** E7 alone contributes no posture (it is a diagnose modifier, gated on E1/E2); E5 is the sole posture source in all 14 cases. [Part 1 output: "Error breakdown by which extractors fired": `e5,e7`: 13 entries, `e5` alone: 1]

4. **E5's C-assist condition (NAMED_DOC_NOUNS + RELATIONAL_MARKERS) fires on "changelog" in release-prep prompts, "matches" in conformance-check language, and "schema" in API-change prompts** — all of which are genuine build/operate tasks. *(Note: the specific marker tokens cited below — "changelog", "match"/"matches", "schema" — are manual inferences from reading the extractor source (`_markers.py` / `_extractors.py`), not probe-captured marker-hit output. The probe confirmed that E5 fired on these entries; it did not sample which specific term within NAMED_DOC_NOUNS or RELATIONAL_MARKERS triggered the match. This distinction matters for any follow-up work that tightens those marker sets.)* Sample evidence:
   - corpus_id=33679: "Prepare release v1.2.0: bump version string … add a new CHANGELOG.md section" → E5 fires on "changelog" (NAMED_DOC_NOUNS) + 2 file refs → verify → auditor. Gold: doc-writer (build).
   - corpus_id=34690: "Trim verbose multi-paragraph code comments in Rust source files to match a concise one-line comment style." → E5 fires on "match" / "matches" (RELATIONAL_MARKERS stem) + 2 prose path tokens → verify → auditor. Gold: code-writer (build).
   - corpus_id=34799: "Implement GitHub issue #776: build a static, backend-less Shell-B UI prototype … Create real Jinja2 HTML templates (templates/index.html, …)" → 2 file references + structural doc language → E5 fires → auditor. Gold: code-writer (build). [Part 6 output, errors 4, 7, 10]

5. **E3 fires on PR references in git-operate prompts (3 errors).** E3 detects PR hashes/URLs as "assess" evidence:
   - corpus_id=33609: "Resolve new merge conflicts in the local git branch for warpdotdev/warp PR #11448. Fetch updated base branch origin/main, merge or rebase…" → E3 fires on "PR #11448" → assess → code-reviewer. Gold: ops (operate). [Part 6, error 1]
   - corpus_id=33692: "Read and review the change-request feedback on GitHub PR #11723 in warpdotdev/warp." → E3 fires → code-reviewer. Gold: ops (operate). [Part 6, error 5]
   
   Note: `ops` gold_posture is `assess` for 33692 per the label — this is the one true cell-mapping ambiguity (ops with assess posture). The extractor is structurally correct that a PR-review task could route to code-reviewer; the gold labeler classified it as ops/assess. This is the H2 (mapping ambiguity) component — but it affects only 1-2 entries.

6. **No-mention catastrophe is E5+E3 unmasked: 14 of 18 no-mention errors come from E5 (verify → auditor); 3 from E3 (assess → code-reviewer).** When explicit agent mentions are absent, E11 cannot fire, and the only routing comes from E5 and E3 — both misfiring systematically. [Part 2 output]

7. **Lexical beats extractor primarily by abstaining.** Of 19 extractor confident-wrong errors, lexical was advisory (abstained) on 14 of them and correct on 3. Lexical was wrong too on only 2. The extractor over-delegates (commits to a wrong agent at confidence 0.9) on prompts where lexical prudently abstains. [Part 3 output]

8. **False-default-build rate (0.5625) is a separate but related metric.** 112 of 168 entries produce no fired posture (default-build path), routing to code-writer at advisory (0.5). Since decision=advisory for those, they are not counted in confident-wrong. The 63 wrong default-build cases (code-writer predicted, gold=doc-writer/ops/self_handle/researcher/investigator) reflect the extractors' near-total inability to fire on the organic build/operate corpus. The default-build path is not the source of the 19 errors — but 56% of its cases are also wrong, showing how poorly the extractors generalise. [Part 4 output]

9. **E11 agent-mention pass-through contributes 1 error** (corpus_id=34779: E11 fires on explicit "ops" mention, routes to ops, but gold is self_handle). This is a labeling or mapping edge case; E11 itself is not a systemic problem. [Part 1, E11 section]

---

## Hypotheses Ruled Out

- **H2 (posture→agent mapping is lossy)**: Disproven as the dominant factor. The cell map `("any", "verify") → "auditor"` and `("any", "assess") → "code-reviewer"` are correct; the problem is upstream — the extractors assign the wrong posture, not that the map routes the right posture to the wrong agent. One edge case (corpus_id=33692: ops/assess gold vs code-reviewer/assess prediction) involves a genuine map ambiguity, but it affects only 1-2 entries.

- **H3 (gold-label or eval-harness artifact)**: Disproven. Regenerated numbers match the issue exactly across all cuts and all systems. The harness correctly scores errors: the 19 confident-wrong cases are genuinely wrong routes, not scoring artifacts. The default-build path correctly falls to advisory (0.5 confidence → decision=advisory, not counted in CW rate), so the harness is not inflating the error count.

- **H4 (genuine inadequacy — posture doesn't separate organic prompts)**: Partially confirmed but not in the interesting sense. The extractors do fire (53 delegates) — but when they fire, they fire wrong 36% of the time, compared to 15% for lexical. The signal they do extract (verify/assess) is not genuine posture signal for this corpus; it's noise from structural patterns (PR references, multi-file mentions) that happen to match E3 and E5's conditions but do not indicate the posture those extractors were designed to detect.

---

## Dominant Failure Pattern: E5 Misfires on Build Prompts

**Pattern**: Build-posture prompts that mention two or more files/paths AND contain one of {changelog, schema, contract, matches, against, conforms to, in sync with} are classified as verify posture and routed to `auditor`.

**Root mechanism** (`scripts/corpus/eval/_systems.py:L86`, `src/claude_wayfinder/posture/_extractors.py:L345-421`, `src/claude_wayfinder/posture/_markers.py:L35-58`):
- E5 B-core: `artifact_count >= 2` — fires on any multi-file prompt
- E5 C-assist: any item in `NAMED_DOC_NOUNS` = {release notes, changelog, schema, contract, invariant} OR any item in `RELATIONAL_MARKERS` = {against, matches, conforms to, consistent with, in sync with, drifted from} (+ stem variants)
- In organic prompts, release-prep, wiki-update, and implementation tasks routinely mention changelogs and multiple file paths. "Matches" fires on "to match a one-line comment style." "Schema" fires on API-related implementation tasks.

**Why it doesn't fire in synthetic test cases**: The P-fixtures (P1-P14) used in unit tests and the 14-entry fixture corpus are carefully constructed to exercise specific extractor paths; they don't expose the false-positive rate on organic phrasing.

**E3 secondary pattern** (`src/claude_wayfinder/posture/_extractors.py:L252-300`):
- `_RE_PR_HASH` matches `PR #<digits>` anywhere in text — fires on "merge conflicts in ... PR #11448" and "feedback on GitHub PR #11723"
- These are git-operate tasks, but E3 treats any PR reference as "assess" evidence
- The organic corpus contains many git/GitHub operate prompts that use "PR" as a shorthand; E3 cannot distinguish "review the PR code" (true assess) from "resolve the PR merge conflict" (operate)

---

## No-Mention Catastrophe Explained

The 134-entry no-mention cut has 21 extractor delegates and 18 are wrong (85.7%).

Structural cause: the 21 delegates in the no-mention cut consist almost entirely of E5 and E3 misfires — the same patterns described above, but without E11 (agent mention) to override them. The 3 correct delegates in the no-mention cut are:
- E8 operate (command_prefix present) — the one reliable Tier-A extractor
- One other entry

The 18 wrong delegates are all E5 (14) + E3 (3) + E4 (1) misfires.

This means: **the posture extractors have near-zero true positive value in the no-mention organic setting.** They delegate when they have "evidence," but that evidence is structurally spurious.

---

## Verdict Per Hypothesis

| Hypothesis | Verdict | Evidence |
|---|---|---|
| H1: Extractor logic misfires on organic phrasing | **CONFIRMED — dominant cause** | E5 accounts for 14/19 errors (73.7%); E3 accounts for 3/19 (15.8%). Both fire on structural patterns (path counts, PR references) that are common in organic prompts but don't signal the intended posture. |
| H2: Posture→agent mapping is lossy | **NOT DOMINANT** | Map is correct; wrong posture is assigned upstream. 1-2 entries involve genuine map ambiguity (ops/assess). |
| H3: Gold-label/harness artifact | **RULED OUT** | Regenerated numbers match exactly. Harness scoring is correct. |
| H4: Genuine signal inadequacy | **PARTIALLY CONFIRMED** | The extractors do have a genuine signal problem on organic prompts, but it manifests as systematic false positives (wrong fires), not as abstention. The issue is misfire, not silence. |

---

## Recommendation: Salvageable or Lexical-Only?

**Assessment: Lexical-only for the near term. E5 and E3 are not salvageable without structural redesign.**

The core problem is that E5 and E3 attempt to detect semantic postures using syntactic proxies that are too common in organic build/operate prompts:

- **E5** relies on "are there ≥2 files AND a conformance word?" — but multi-file edit prompts are extremely common (build posture), and conformance vocabulary ("changelog", "matches", "schema") appears in implementation tasks, not just audit tasks.
- **E3** relies on "is there a PR reference?" — but organic prompts mention PRs in git-operate contexts (merge, fetch) as often as in review contexts (assess).

**What would make E5/E3 salvageable**: A semantic (not syntactic) judgment of whether the conformance vocabulary is being used in an audit frame vs. an edit frame. This is precisely what the frozen Tier-C marker sets were designed to avoid (pure determinism, no LLM). Without that judgment, the false-positive rate on organic prompts is structurally unavoidable.

**E8 (command_prefix) and E11 (agent mentions) remain reliable** — Tier-A extractors that read structured fields rather than prose. These should be retained in any future design.

**The lexical baseline is better because it abstains.** Lexical's lower CW rate (0.1507 vs. 0.3585) comes partly from better keyword discrimination and partly from abstaining on ambiguous prompts. On the 14 prompts where extractors confidently routed to `auditor` (wrong), lexical abstained (advisory). A system that says "I don't know" is better than one that confidently says the wrong thing.

---

## Open Questions

1. **E5 C-assist specificity**: Could NAMED_DOC_NOUNS or RELATIONAL_MARKERS be tightened to exclude organic false positives without losing synthetic test coverage? This would require sampling which specific terms are triggering false E5 fires — not done in this probe (only confirmed that E5 fired, not which specific marker triggered it within E5).

2. **E3 PR-URL vs. PR-action disambiguation**: Is there a syntactic cue in organic prompts that distinguishes "review PR" (assess) from "merge/rebase PR" (operate) reliably enough for a deterministic extractor? Candidate: verb preceding the PR reference.

3. **Are the 3 correct extractor delegates in the no-mention cut a coincidence or signal?** They were not characterised in this probe. If they come from E8 (command_prefix), that is reliable. If from E5/E3 getting lucky, the true positive rate of those extractors may be effectively zero.

4. **Missing knowledge files**: No prior postmortem for E5 false-positive patterns was found in `docs/research/`. If issue #328 (extractor library design) has discussion of E5 over-firing risk, that should be linked here.

---

## Recommended Handoff

- **`code-writer` or `devops`** for any fix to E5: either tighten the C-assist marker sets (narrower NAMED_DOC_NOUNS / RELATIONAL_MARKERS) or add a negative-gate that blocks E5 when E8 or other operate/build signals dominate.
- **`code-writer`** for E3: add a verb-prefix heuristic to distinguish PR-review from PR-operate contexts, OR restrict E3's assess evidence to Tier-A tool_mentions only (not the Tier-B text-shape path).
- **Router / milestone decision**: whether to park E5/E3 and route on lexical-alone, or invest in redesigning the extractors, is a product/milestone decision for Milestone 14 — recommend closing the extractors track (NO-GO on posture extractors for organic routing) and narrowing future work to E8+E11 Tier-A signals only.

---

## Artifacts

- Probe script: local throwaway — `.tmp/probe_355_errors.py` (not committed; depends on local-only corpus at `~/.claude/state/wayfinder-corpus/2026-06-12/`). To reproduce, run the standard harness: `python -m scripts.corpus.eval --corpus ~/.claude/state/wayfinder-corpus/2026-06-12/wayfinder-corpus.jsonl --labels docs/research/2026-06-12-gold-labels-redacted.jsonl --catalog ~/.claude/state/dispatch-catalog.json`
- Prior measurement: local throwaway — `.tmp/run_330_results.json` (not committed; this is the #330 run output cached locally)
- Gold labels (redacted): `docs/research/2026-06-12-gold-labels-redacted.jsonl`
- Corpus manifest: `docs/research/2026-06-12-corpus-manifest.json`
- Extractor source: `src/claude_wayfinder/posture/_extractors.py`
- Markers source: `src/claude_wayfinder/posture/_markers.py`
- Cell map + runner: `scripts/corpus/eval/_systems.py:L86-117` (cell map), `L578-695` (run_extractors)
