# Oracle-Domain Ceiling Experiment — #358

> **Gold adjudication notice (#402).** This is a frozen, dated record. The gold labels it consumed have since been adjudicated **in place** (#364/#394: 5 entries; #398/#399: corpus 33692 `assess`→`operate`; plus any later gold-ownership edits). Counts, distributions, and the gold sha cited below reflect the gold **as of this report's date** and are intentionally **not** updated — the committed redacted jsonl (`docs/research/2026-06-12-gold-labels-redacted.jsonl`) is the live source of truth. A reader cross-referencing current gold will see expected differences (e.g. `assess`/`operate`, `diagnose`/`research` posture counts). Per the frozen-snapshot model decided in #402, this record is preserved as historical evidence, not rewritten.

**Date:** 2026-06-13
**Branch:** feat/330-measurement-run
**Question:** Can a perfect (oracle) domain label beat the lexical baseline? If not, no real LLM labeler will.

---

## 1. Setup

### Interpreter
`.venv/Scripts/python.exe` (project venv, `I:/ai/claude/claude-wayfinder/.claude/worktrees/vigilant-shamir-97d682/.venv`)

### Files
| Resource | Path |
|---|---|
| Corpus | `~/.claude/state/wayfinder-corpus/2026-06-12/wayfinder-corpus.jsonl` (168 entries) |
| Gold labels | `~/.claude/state/wayfinder-corpus/2026-06-12/gold-labels.jsonl` (168 entries) |
| Catalog | `~/.claude/state/dispatch-catalog.json` (22 agents, 65 skills) |
| Probe script | `.tmp/oracle_domain_probe.py` |

---

## 2. Domain → Agent Map (load-bearing assumption)

Agents declared in the §9.1 `_CELL_MAP` with `domain=="any"` appear in every domain set (gating them would be wrong — they are domain-agnostic).

### Any-domain agents (never gated)
`investigator`, `approach-critic`, `auditor`, `researcher`, `ops`, `project-planner`

### Domain-exclusive agents
| Domain | Domain-exclusive agents |
|---|---|
| `code` | `code-writer`, `debugger`, `code-reviewer`, `inquisitor` |
| `docs_prose` | `doc-writer` |
| `project_meta` | `project-reviewer` |
| `infra_deploy` | `devops` |

**Full domain sets** = domain-exclusive ∪ any-domain agents.

**Gating rule for is_any / domain=None:** pass-through (no gating). Implemented as: `DOMAIN_AGENT_MAP.get(domain)` returns `None` for the string `"None"` (how `load_labels` encodes `null` JSON), triggering the no-gate branch.

---

## 3. Corpus Cuts

| Cut | n_entries | n_labeled | Description |
|---|---|---|---|
| full | 168 | 168 | All entries |
| no_smoke | 109 | 109 | −59 repeated harness probes ("implement the new module" ×29, "update the docs" ×30) |
| no_mention | 134 | 134 | Entries with empty `agent_mentions` |

**Domain distribution (all 168 labels):** code=74, docs_prose=43, project_meta=30, is_any/None=16, infra_deploy=5

**Gold agent distribution (no-smoke, 109 entries):**
code-writer=32, ops=31, self_handle=13, doc-writer=13, investigator=6, researcher=6, project-planner=5, auditor=2, code-reviewer=1

---

## 4. Systems Evaluated

1. **Lexical baseline** — unmodified `run_lexical()` from `_systems.py`: `build_features → score_entries → decide()`
2. **Oracle hard-gate** — same as lexical, but in-domain agents only are passed to `decide()`. Out-of-domain agents are dropped from `scored_agents` entirely. For is_any entries: pass-through.
3. **Oracle soft-boost +0.3** — all in-domain agents' scores boosted by +0.3 (cap 1.0), then re-sorted before `decide()`. For is_any entries: pass-through.
4. **Oracle soft-boost +0.5** — same with +0.5 boost.

### Routing-correctness definition
Fraction of ALL labeled entries where `result.agent == gold_agent`. Abstain/advisory/self_handle counted as NOT correct unless the result agent specifically matches gold (e.g. `self_handle` gold entries are correct only if result is also `self_handle`).

---

## 5. Results

### 5.1 Full table

| System | full CWR | full RC | no_smoke CWR | no_smoke RC | no_mention CWR | no_mention RC |
|---|---|---|---|---|---|---|
| Lexical | 0.1507 | 0.3929 | 0.2558 | 0.3303 | 0.2000 | 0.3582 |
| Oracle hard-gate | 0.0482 | 0.5000 | 0.0755 | **0.4954** | 0.0667 | 0.4552 |
| Oracle soft+0.3 | 0.0923 | 0.3810 | 0.1714 | 0.3119 | 0.1200 | 0.3507 |
| Oracle soft+0.5 | 0.0952 | 0.3929 | 0.1818 | 0.3303 | 0.1250 | 0.3657 |

CWR = confident-wrong rate (lower is better). RC = routing-correctness (higher is better).

### 5.2 Headroom deltas vs lexical (no-smoke cut)

| System | RC delta | CWR delta |
|---|---|---|
| Oracle hard-gate | **+0.1651** | **−0.1803** |
| Oracle soft+0.3 | −0.0184 | −0.0844 |
| Oracle soft+0.5 | +0.0000 | −0.0740 |

---

## 6. Mechanism Analysis

### Why hard-gate wins (+16.5 pp routing-correctness on no-smoke)
The 19 hard-gate gains vs lexical are **all tie-breaking artifacts**, not domain discrimination per se. In each case:
- Lexical scores multiple agents at 1.0 (e.g. code-writer=1.0 and doc-writer=1.0 tied)
- `decide()` sees gap < 0.2, falls into `self_handle` or `advisory` branch
- Hard-gate drops the out-of-domain competitor (e.g. drops doc-writer from the `code` domain set)
- This creates gap ≥ 0.2 to the next agent → `decide()` emits `delegate` with the correct agent

This means oracle domain gating works by **unblocking the lexical signal that was already correct**, not by carrying independent domain evidence. The lexical scores already ranked the gold agent first; the problem was score ties preventing delegation.

**Hard-gate loss (1 case):** id=34760, gold=code-writer, domain=infra_deploy. The `code-writer` agent is NOT in the `infra_deploy` domain set (only `devops` + any-domain agents). The correct gold label is arguably mislabeled in domain — implementing a GitHub Release workflow is code (scripting), not infra deployment. This is a domain-map edge case.

### Why soft-boost fails (flat or worse than lexical)
Soft-boost lifts ALL in-domain agents, including the any-domain agents (`ops`, `investigator`, etc.) that are already present in every domain set. This causes:
- In-domain non-gold agents (e.g. `ops` at 0.5) are boosted to 0.8
- Gap between gold agent (1.0) and boosted competitors (0.8) = 0.2, exactly at the `decide()` delegate threshold boundary
- Some previously-delegating entries fall into `self_handle` because a skill is now ≥ 0.5 or the gap is no longer sufficient

Two verified losses (id=34698, id=34926): lexical delegated correctly to code-writer; soft-boost promoted ops from 0.5 to 0.8, eliminating the gap and triggering self_handle.

### Structural ceiling
The no-smoke cut has 13/109 entries with `gold_agent="self_handle"` — the only gold target that is NOT in any domain set. No domain gating can help these entries. This creates a structural ceiling of ~88% of entries where domain could theoretically help, but the mechanism only helps when: (a) domain is known, (b) the gold agent is in the domain set, and (c) the lexical baseline produced a tie involving an out-of-domain agent.

**Decision distribution shift (no-smoke):**
- Lexical: delegate=43, self_handle=35, advisory=27, needs_more_detail=1, self_handle_unaided=3
- Hard-gate: delegate=53 (+10), self_handle=22 (−13), advisory=27 (=), needs_more_detail=1, self_handle_unaided=6 (+3)

The hard-gate converts self_handle→delegate for the tie-broken entries (net +10 delegate decisions), and some previously-self_handle entries fall to self_handle_unaided when gating removes too many agents.

---

## 7. Verdict: GO (with important caveats)

**Oracle hard-gate beats lexical meaningfully:**
- No-smoke routing-correctness: +16.5 pp (0.3303 → 0.4954)
- No-smoke confident-wrong rate: −18.0 pp (0.2558 → 0.0755)
- Hard-gate is the winning strategy; soft-boost is not viable

**Caveats:**
1. **The gain is tie-breaking, not discrimination.** Oracle domain succeeds by collapsing multi-agent ties in the lexical scorer, not by providing an independent routing signal. A real LLM domain labeler would need to produce a domain label that matches the gold domain for entries where ties occur — which is exactly the hard cases (ambiguous tasks that score equally for code-writer and doc-writer).

2. **Soft-boost is structurally broken** for this catalog. Any-domain agents (ops, investigator, etc.) are legitimately in every domain set, so boosting them creates new ties. The only viable strategy is hard-gate.

3. **One domain-map boundary case** (infra_deploy ∩ code-writer): the map as specified loses one entry because code-writer is excluded from infra_deploy. This suggests the map should include code-writer in infra_deploy (implementation work inside infra contexts), or a softer "primary domain" concept.

4. **Smoke probes inflate the hard-gate RC advantage** somewhat. On the full cut (168 entries), hard-gate RC = 0.5000 vs lexical 0.3929 (+10.7 pp), vs +16.5 pp on no-smoke. The full-cut advantage is smaller because smoke probes tie at 1.0 for code-writer/doc-writer and the hard-gate helps both (all smoke probes are either code or docs_prose domain, so the domain is always available).

---

## 8. Recommended next steps (for design team)

If pursuing domain gating:
- **Use hard-gate only.** Soft-boost is not viable with the current any-domain agent structure.
- **The primary problem to solve is lexical ties**, not a standalone domain signal. The issue is that `decide()` requires a gap ≥ 0.2 to delegate, and multiple agents frequently score 1.0. A domain gate fixes this by shrinking the candidate pool.
- **Consider an alternative: modify the lexical scorer** to produce more differentiated scores for semantically-close agents (code-writer vs doc-writer). This avoids the LLM classifier dependency entirely.
- **The infra_deploy domain set** should include code-writer (many infra tasks involve writing scripts/automation code). Alternatively, treat infra_deploy as a posture-level signal (devops for planning, code-writer for implementation), not a hard domain gate.
- **A real LLM labeler** will not be oracle. The 16.5 pp ceiling means the practical gain with an ~80% accurate LLM labeler would be closer to 10–13 pp — still meaningful if the implementation cost is low, but not a silver bullet.

---

## 9. Raw probe output

```
Total corpus entries: 168 / Total gold labels: 168
Smoke probe IDs: 59 / Entries with agent_mentions: 34

--- Cut: no_smoke | entries=109, labeled=109 ---
  Lexical:           cwr=0.2558  rc=0.3303  decisions={'delegate': 43, 'self_handle': 35, 'advisory': 27, 'needs_more_detail': 1, 'self_handle_unaided': 3}
  Oracle hard-gate:  cwr=0.0755  rc=0.4954  decisions={'delegate': 53, 'self_handle': 22, 'advisory': 27, 'needs_more_detail': 1, 'self_handle_unaided': 6}
  Oracle soft+0.3:   cwr=0.1714  rc=0.3119  decisions={'delegate': 35, 'self_handle': 38, 'advisory': 34, 'needs_more_detail': 1, 'self_handle_unaided': 1}
  Oracle soft+0.5:   cwr=0.1818  rc=0.3303  decisions={'advisory': 36, 'self_handle': 39, 'delegate': 33, 'needs_more_detail': 1}
```

---

## 11. Tie-set characterization

**Date:** 2026-06-13 (follow-up probe)
**Probe:** `.tmp/tieset_probe.py`
**Interpreter:** `.venv/Scripts/python.exe` (project venv)

### 11.1 Definition

The **tie set** = entries where oracle hard-gate flips the outcome from
wrong/abstain (lexical) to correct (oracle), due to a multi-agent tie at/near
score ≥ 0.95. Specifically: `lex_agent != gold_agent` AND `hg_agent == gold_agent`.

### 11.2 Tie-set size per cut

| Cut | n_total | tie_set | tie_set / n_total | RC delta from tie-set |
|---|---|---|---|---|
| full | 168 | 19 | 11.3% | +0.1131 |
| no_smoke | 109 | 19 | 17.4% | +0.1743 |
| no_mention | 134 | 14 | 10.5% | +0.1045 |

**Verification (no_smoke):** lex_correct=36/109=0.3303; hg_correct=54/109=0.4954; delta=+0.1651.
Net tie-set accounting: 19 gains − 1 loss (id=34760) = 18 net / 109 = 0.1651. **The tie-set accounts for exactly 100% of the +16.5 pp delta.**

### 11.3 Difficulty classification

Classification criteria:
- **easy** — task text contains unambiguous domain-specific vocabulary (e.g. "implement", "write code", "module", "fix bug", "function" for code; "document", "readme", "changelog" for docs_prose; "review PR", "pull request" for project_meta; "deploy", "terraform", "workflow" for infra_deploy) with no competing cross-domain signals.
- **ambiguous** — task uses generic or multi-domain framing where a competent reader could plausibly assign the wrong domain (e.g. "prepare release" could be code or docs; "plan the feature" could be code or project_meta; a GitHub read-only query that looks like a code-review task).

| Category | Count | % of tie-set | RC headroom (pp, no_smoke n=109) |
|---|---|---|---|
| easy | 12 | 63.2% | 11.0 pp |
| ambiguous | 7 | 36.8% | 6.4 pp |
| **Total** | **19** | **100%** | **17.4 pp (gross)** |

### 11.4 Per-entry table (no_smoke tie set)

**EASY entries — domain obvious from text**

| id | task (truncated) | gold_agent | gold_domain | cross-domain agent dropped |
|---|---|---|---|---|
| 33614 | Implement issue #908: modify src/claude_configs/deploy.py build_plan Phase 2… | code-writer | code | doc-writer, test-implementer |
| 34680 | Implement issue #1: scaffold the pilot harness repo directory structure… | code-writer | code | doc-writer |
| 34724 | Implement issue #2 in Python: fill in after_create.py and before_run.py hook stubs… | code-writer | code | test-implementer |
| 34729 | Implement issue #3 in Python: fill in the after_run.py outcome-router stub… | code-writer | code | test-implementer |
| 34799 | Implement GitHub issue #776: build a static backend-less Shell-B UI prototype… | code-writer | code | doc-writer |
| 34840 | Add target-repo label preflight to bin/run.sh and harden after_run label reconciliation… | code-writer | code | test-implementer |
| 34905 | Implement Phase 0 of drift-aggregation per spec: add a timestamp field to variance records… | code-writer | code | doc-writer, test-implementer |
| 34921 | Port two accumulated snapshot UI changes from prototype-feed.html into the real Jinja source… | code-writer | code | test-implementer |
| 35237 | Implement Phase 1 drift-report CLI per spec: new Python module src/claude_prospector/cli/drift_report.py… | code-writer | code | test-implementer |
| 35335 | Read GitHub pull request nexu-io/open-design#3833 and report what the author is trying to do… | ops | project_meta | code-reviewer, inquisitor |
| 35369 | Port accumulated Roles-page design changes from snapshot prototype-roles.html into real Jinja source… | code-writer | code | test-implementer |
| 35416 | Implement a new Python module _auth.py exposing validate_github_token()… | code-writer | code | test-implementer |

Pattern for easy entries: 10 of 12 are "Implement issue #N" or "Port …" tasks where the word "Implement" plus a file path or Python module name makes `code` domain unambiguous. The remaining 2 (id=35335, 35416) have strong code vs. non-code domain signals without competing prose signals.

**AMBIGUOUS entries — domain genuinely unclear**

| id | task (paraphrase) | gold_agent | gold_domain | why ambiguous |
|---|---|---|---|---|
| 33641 | "Plan the drift-aggregation feature for claude-prospector (issue #63 remaining ACs): a new deterministic CLI command…" | project-planner | project_meta | "Plan" signals project_meta but body is full of implementation detail ("CLI command", Python), which lexically hits code-writer at 1.0 |
| 33679 | "Prepare release v1.2.0: bump version string in pyproject.toml and .claude-plugin/plugin.json, and add a changelog entry…" | doc-writer | docs_prose | Release prep straddles code edits (pyproject.toml) and prose (changelog); code-writer and doc-writer both legitimately apply |
| 34612 | "GitHub read-only query for /whats-next recap: for repo warpdotdev/warp fetch PR #11723 status…" | ops | project_meta | Read-only GitHub fetch; "PR status" and "code-reviewer" lexically fire; ops is right (operate posture) but a real LLM might see "PR" and label code |
| 34715 | "Update design documentation markdown files to record the harness implementation-language decision (shell to Python)…" | doc-writer | docs_prose | "Update… markdown files" is docs, but "shell to Python" and "implementation-language decision" pull toward code; code-writer and doc-writer tie |
| 34753 | "Implement baton-harness issue #5 with a YAGNI scope change: remove per-project config scaffolding and flatten…" | doc-writer | docs_prose | Title says "Implement" (code signal) but gold is doc-writer/docs_prose — this is the labeling challenge: the task is to update docs to reflect a scope change, but framing is implementation-first |
| 35378 | "Find and read the context finder GitHub issue in the current repo, then scope it out for implementation. Read-only GitHub query…" | project-planner | project_meta | "scope it out for implementation" triggers code-writer at 1.0; but gold is project-planner (planning task). A real LLM could plausibly go either way |
| 35384 | "Produce a design doc / ADR that resolves the open design questions for a new context-injection feature…" | project-planner | project_meta | "Produce a design doc" fires doc-writer at 1.0; gold is project-planner. The planning function is buried under the docs framing |

### 11.5 Bankable vs. at-risk headroom

| | pp (no_smoke, n=109) | fraction of +16.5 pp gain |
|---|---|---|
| **Bankable** (easy ties, real LLM would get right) | **11.0 pp** | **66.7%** |
| **At-risk** (ambiguous ties, real LLM may mislabel) | **6.4 pp** | **38.8%** |
| Net oracle gain | 16.5 pp | 100% |

Note: the percentages overlap because gross tie-set gain (17.4 pp) > net gain (16.5 pp) due to the 1 hard-gate loss. Bankable + at-risk = 17.4 pp gross; subtracting the 0.9 pp loss gives 16.5 pp net.

**Practical estimate:** an LLM domain labeler that gets easy cases right (p≈0.95) and ambiguous cases right at chance (p≈0.5) would realize approximately:
- 12 × 0.95 + 7 × 0.5 ≈ 11.4 + 3.5 = 14.9 gains, minus 1 hard-gate loss = net ~14/109 ≈ **12.8 pp**
- vs. the oracle ceiling of 16.5 pp.

A more optimistic LLM (easy=0.95, ambiguous=0.7) gives: 12×0.95 + 7×0.7 ≈ 11.4 + 4.9 = 16.3 − 1 = **14.0 pp realized**.

### 11.6 GO verdict assessment

The tie-set analysis **strengthens the GO with one nuance:**

1. **The 11.0 pp bankable headroom is solid.** Ten of the twelve easy entries are "Implement issue #N" tasks where the word "Implement" + a Python file path unambiguously signals `code` domain. Any competent LLM labeler will get these right.

2. **The 6.4 pp at-risk headroom is real but not fatal.** The 7 ambiguous entries divide into two sub-patterns:
   - *Release/docs tasks framed with implementation language* (ids 33679, 34715, 34753): these have gold_domain=docs_prose but contain code-framed wording. A domain classifier trained on these pairs would help here, but without fine-tuning it's genuinely uncertain.
   - *Planning tasks with "implement" in the title* (ids 33641, 35378, 35384): the task title says "implement" or "scope for implementation" but the gold posture is planning. A posture-aware LLM would catch this; a keyword-only LLM would not.

3. **The GO holds but the realistic gain is ~12–14 pp, not the full 16.5 pp oracle ceiling.** This is still meaningful — a ~13 pp improvement in no-smoke routing-correctness (from 0.33 to 0.46) justifies a lightweight LLM domain labeler if the implementation cost is low.

4. **If the ambiguous fraction is unsatisfactory:** the 7 ambiguous entries are themselves addressable by a domain classifier fine-tuned on the gold labels in the corpus. The misclassification pattern is consistent (code vs. project_meta for planning tasks; code vs. docs_prose for mixed implementation/documentation tasks) and would appear in training data.

---

## 10. Open questions

1. **Domain map for infra_deploy + code-writer:** should code-writer be included? The one hard-gate loss (id=34760) is a borderline case. Needs a ruling from the labeling team on whether "automate GitHub Release in .github/workflows" is code domain or infra domain.

2. **Gap threshold sensitivity:** `decide()` uses `_DELEGATE_GAP = 0.2` as a hard cutoff. Soft-boost's failure is sensitive to this threshold. If the gap threshold were 0.15, soft-boost would recover some gains. Worth checking if calibrating the gap threshold (independently of domain gating) would close the lexical tie problem more cleanly.

3. **Interaction with E11 (agent_mentions):** the no-mention cut (removing entries with explicit agent mentions) shows RC=0.4552 for hard-gate vs 0.3582 lexical (+9.7 pp), smaller than no-smoke (+16.5 pp). The no-mention cut still contains smoke probes. A no-smoke + no-mention combined cut was not measured; it would isolate the organic non-explicit-mention signal more cleanly.

---

## 12. Two-axis oracle: domain + posture

**Date:** 2026-06-13 (follow-up probe)
**Probe:** `.tmp/oracle_two_axis_probe.py`
**Interpreter:** `.venv/Scripts/python.exe` (project venv)
**Question:** Does a second LLM-supplied axis (posture) add routing headroom beyond domain alone?

### 12.1 Systems evaluated

Five systems run head-to-head on the same three cuts:

| # | System | Description |
|---|---|---|
| 1 | **Lexical** | Unmodified lexical baseline (re-run for clean side-by-side) |
| 2 | **Domain-only** | Oracle domain hard-gate (prior probe — re-verified) |
| 3 | **Posture-only** | Gold posture → agent via `_CELL_MAP` with `domain="any"` (no domain signal) |
| 4a | **CellMap(d+p)** | Gold (domain, posture) → agent via `_CELL_MAP` directly; always delegates |
| 4b | **Compose(d+p)** | Domain hard-gate first, then posture selects within surviving gated candidates; falls back to lexical `decide()` on gated list |

### 12.2 Cell-map coverage

All 16 distinct gold (domain, posture) combinations in the 168-entry corpus map to a cell-map agent — 100% coverage. The most populous cells:

| (domain, posture) | n | Cell-map agent |
|---|---|---|
| (code, build) | 58 | code-writer |
| (docs_prose, build) | 42 | doc-writer |
| (project_meta, operate) | 16 | ops |
| (None/any, operate) | 15 | ops |
| (project_meta, build) | 8 | code-writer |
| (code, operate) | 5 | ops |
| (project_meta, plan) | 5 | project-planner |

Note: `(project_meta, build)` and `(infra_deploy, build)` both map to `code-writer` via the `(any, build)` fallback — the cell map does not declare these domain-specific. This matters for infra_deploy entries with build posture (see §12.6 loss analysis).

### 12.3 Results table

| System | full CWR | full RC | no_smoke CWR | no_smoke RC | no_mention CWR | no_mention RC |
|---|---|---|---|---|---|---|
| Lexical | 0.1507 | 0.3929 | 0.2558 | 0.3303 | 0.2000 | 0.3582 |
| Domain-only | 0.0482 | 0.5000 | 0.0755 | **0.4954** | 0.0667 | 0.4552 |
| Posture-only | 0.3512 | 0.6488 | 0.2661 | 0.7339 | 0.4179 | 0.5821 |
| CellMap(d+p) | 0.1250 | 0.8750 | 0.1927 | **0.8073** | 0.1269 | 0.8731 |
| Compose(d+p) | 0.0886 | 0.8571 | 0.1414 | **0.7798** | 0.0806 | 0.8507 |

CWR = confident-wrong rate (lower is better). RC = routing-correctness (higher is better).

### 12.4 Headroom deltas (no-smoke cut — the primary cut)

**vs. lexical baseline (RC=0.3303, CWR=0.2558):**

| System | RC | delta-RC | CWR | delta-CWR |
|---|---|---|---|---|
| Domain-only | 0.4954 | +0.1651 | 0.0755 | −0.1803 |
| Posture-only | 0.7339 | +0.4036 | 0.2661 | +0.0103 |
| CellMap(d+p) | 0.8073 | +0.4770 | 0.1927 | −0.0631 |
| Compose(d+p) | 0.7798 | +0.4495 | 0.1414 | −0.1144 |

**vs. domain-only (RC=0.4954, CWR=0.0755) — the decisive question:**

| System | RC | delta-RC | CWR | delta-CWR |
|---|---|---|---|---|
| Posture-only | 0.7339 | **+0.2385** | 0.2661 | **+0.1906 (WORSE)** |
| CellMap(d+p) | 0.8073 | **+0.3119** | 0.1927 | **+0.1172 (WORSE)** |
| Compose(d+p) | 0.7798 | **+0.2844** | 0.1414 | **+0.0659 (WORSE)** |

### 12.5 Domain breakdown (no-smoke, n=109)

| Domain | n | Lexical RC | Domain-only RC | Posture-only RC | CellMap RC | Compose RC |
|---|---|---|---|---|---|---|
| code | 45 | 0.2000 | 0.4444 | **1.0000** | **0.9111** | **0.9111** |
| docs_prose | 13 | 0.5385 | 0.7692 | 0.0769 | **1.0000** | **1.0000** |
| project_meta | 30 | 0.2333 | 0.4000 | 0.5667 | 0.5667 | 0.5667 |
| None (is_any) | 16 | 0.6250 | 0.6250 | **0.8750** | **0.8750** | **0.8750** |
| infra_deploy | 5 | 0.6000 | 0.4000 | 0.6000 | 0.6000 | **0.0000** |

**Key observations by domain:**

- **code (n=45):** Posture-only achieves perfect RC (1.0000) — posture completely resolves the within-`code` multi-agent ambiguity (debugger vs code-reviewer vs researcher vs code-writer vs ops). CellMap and Compose both reach 0.9111, meaning adding the domain axis costs 4/45 code entries (the cell-map mis-routes them) relative to posture-only — the same `infra_deploy` mis-cell pattern discussed in §12.6 applies here. Posture alone is the main driver of code-domain routing accuracy; domain is additive in every other domain but is slightly costly on this slice. This is the main driver of posture's added value.

- **docs_prose (n=13):** Domain+posture is essential. Posture-only collapses to 0.0769 because `(any, build) → code-writer`, but gold is `doc-writer` — posture "build" with `domain=any` always picks code-writer, never doc-writer. Domain disambiguates build→doc-writer. CellMap and Compose both reach 1.0000.

- **project_meta (n=30):** Posture helps partially (+0.1667 vs domain-only) but hits a ceiling at 0.5667 across all two-axis variants. The remaining ceiling is the `self_handle` entries (gold_agent=`self_handle`) that no domain+posture combination can resolve — there is no cell-map entry for `self_handle`.

- **None/is_any (n=16):** Posture-only boosts RC from 0.6250 to 0.8750. These entries have no domain signal; posture alone provides significant uplift. All two-axis systems agree here (cell-map with domain="any" is equivalent).

- **infra_deploy (n=5):** Compose hurts — RC drops to 0.0000. Analysis below (§12.6).

### 12.6 Loss analysis: where posture hurts

**CellMap losses vs domain-only (2 entries):**
Both losses are `infra_deploy` entries with `posture=research` and `gold_agent=investigator`:
- id=34909: gold=investigator, domain-only→investigator, cellmap→researcher [domain=infra_deploy, posture=research]
- id=34912: gold=investigator, domain-only→investigator, cellmap→researcher [domain=infra_deploy, posture=research]

The cell-map routes `(infra_deploy, research) → researcher` but the gold agent is `investigator`. These entries are failure-investigation tasks in an infra context that the labeler tagged with `research` posture — but the correct agent is `investigator` (the infra-diagnose agent), not `researcher` (the information-gathering agent). The domain-only system gets these right because `investigator` is in the `infra_deploy` allowed set and scores highest lexically.

**Compose losses vs domain-only (same 2 entries):** Same cause. Plus, the infra_deploy domain is the only one where Compose RC (0.0000) is worse than domain-only RC (0.4000) — a 5-entry domain where both losses concentrate means a catastrophic collapse on this domain slice.

**Posture-only's high CWR (+0.1906 worse than domain-only):** Posture-only always delegates with confidence 0.9. Domain-only uses the lexical `decide()` which can abstain (advisory/self_handle). Entries where posture-only routes confidently to the wrong agent contribute to the CWR spike. The net effect: posture alone is high-recall/high-error vs domain-alone's more conservative (abstain-heavy) profile.

**CellMap's elevated CWR (+0.1172 worse than domain-only):** Same cause — CellMap always delegates at confidence=0.9. The 21 wrong delegates (109 × 0.1927 ≈ 21) are the entries where the cell-map misses, mainly: (a) `self_handle` gold entries (no cell-map target), (b) the 2 infra_deploy/research→investigator mismatches.

### 12.7 Compose gains: what posture resolves beyond domain-only (no-smoke, 37 gains)

Compose gains 37 entries over domain-only. Pattern breakdown:
<!-- Total reconciled from row breakdown (16+12+5+1+1+2=37); prior text stated 33. -->

| Pattern | Count | Description |
|---|---|---|
| build→correct specific agent (code-writer or doc-writer) | 16 | Domain hard-gate + posture=build selects the right build agent; domain-only abstained (None) |
| operate→ops | 12 | Within-domain entries where posture=operate breaks a non-ops tie |
| research→researcher | 5 | Posture=research selects researcher over code/docs-focused agents |
| verify→auditor | 1 | Posture=verify selects auditor |
| assess→code-reviewer | 1 | Posture=assess selects code-reviewer over code-writer |
| plan→project-planner | 2 | Posture=plan selects project-planner; domain-only abstained |

Most gains are entries where domain-only abstained (agent=None, decision=advisory/self_handle_unaided) because the gated candidate pool still contained ties or no dominant score. Posture breaks those ties by selecting the specific in-domain agent.

### 12.8 Verdict: one-axis (domain) or two-axis (domain + posture)?

**Two-axis oracle adds +28–31 pp routing-correctness beyond domain-only on the no-smoke cut** (CellMap: +31.2 pp; Compose: +28.4 pp). This is the ceiling assuming perfect posture labels.

**However, the two-axis oracle pays a significant CWR cost:**
- Domain-only CWR: 0.0755
- CellMap(d+p) CWR: 0.1927 (+12 pp worse — 2.6× higher)
- Compose(d+p) CWR: 0.1414 (+6.6 pp worse — 1.9× higher)

**The tradeoff:**
- CellMap: always delegates — no abstaining. High RC but high CWR (more confident-wrong). Good for recall-maximizing contexts.
- Compose: hybrid — posture selects within domain-gated candidates when available, falls back to lexical `decide()` (which can abstain) otherwise. Better CWR than CellMap (0.1414 vs 0.1927) but worse than domain-only (0.0755). More conservative than CellMap.
- Domain-only: lowest CWR (0.0755) — the most precision-maximizing system. Sacrifices recall for accuracy on delegated decisions.

**The decisive number is: does posture earn its complexity given real (non-oracle) labels?**

Oracle posture gives +28–31 pp on top of oracle domain. But real posture labeling will degrade this. The two critical failure modes:
1. **Posture mislabels that cause losses** (e.g. `infra_deploy+research→researcher` when gold is `investigator`). These cannot be distinguished without domain context + posture correction.
2. **`self_handle` entries** (13/109 no-smoke entries) have no posture cell — they are structural misses that no cell-map design can fix. The ceiling for any cell-map system on no-smoke is ≤88% (≈0.88), and in practice 0.8073 (CellMap) shows the actual ceiling accounting for cell-map misses.

**Practical estimation:** if posture labeling accuracy is ~75–80% (reasonable for a lightweight classifier), the realized two-axis gain above domain-only would be approximately:
- CellMap: 31.2 × 0.78 ≈ 24 pp improvement in RC, but CWR remains high (CellMap always delegates).
- Compose: 28.4 × 0.78 ≈ 22 pp RC improvement, with CWR ~10–12 pp above domain-only.

**Verdict: TWO-AXIS IS WORTH IT, with Compose as the recommended variant.**

The +28–31 pp oracle RC headroom is decisive. Even at 75% posture accuracy, the practical gain (~22–24 pp RC) dwarfs the domain-only gain of +16.5 pp. Compose is preferred over CellMap because:
1. Compose's CWR (0.1414) is 5 pp lower than CellMap's (0.1927).
2. Compose gracefully degrades: when posture doesn't select a candidate (or is wrong), it falls back to lexical `decide()` on the domain-gated list, which is exactly the domain-only system.
3. CellMap's "always delegate" profile is riskier in production — every cell-map miss becomes a confident-wrong dispatch.

**The architecture to pursue is:** LLM domain label → hard-gate → LLM posture label → within-domain posture selection → fallback to lexical `decide()` on gated list.

### 12.9 Known risks / open questions added by two-axis probe

1. **infra_deploy + research = loss:** The cell-map routes `(infra_deploy, research) → researcher` but gold is `investigator`. The labeling convention for infra_deploy investigation tasks that involve researching background context needs clarification: is the posture `research` (information gathering) or `diagnose` (failure investigation)? Two entries fail here; the fix is either updating the cell-map entry `("infra_deploy", "research") → "investigator"` or clarifying the labeling protocol.

2. **project_meta ceiling at 0.5667:** Within `project_meta`, 30 no-smoke entries reach only 0.5667 RC even with oracle (domain, posture). This is driven by `self_handle` gold entries (ops or self_handle golds where the lexical system abstains) that domain+posture cannot resolve. A third axis (e.g. explicit "read-only" vs "write" action type) might help here but is not in the current label schema.

3. **Posture-only CWR penalty is severe (+19 pp):** Posture alone is not viable as a primary routing axis — the docs_prose disaster (RC=0.0769 because all build→code-writer) shows it requires domain to function correctly. Posture is an additive second axis only.

---

## 13. Blind LLM-labeling accuracy (the floor experiment)

**Date:** 2026-06-13 (follow-up probe)
**Probe:** `.tmp/score_labeling.py` over 2026-06-12 gold corpus; blind labels `.tmp/labeler-output.jsonl`
**Interpreter:** `.venv/Scripts/python.exe` (project venv)
**Question:** What does a real LLM labeler achieve vs the oracle ceiling measured in §§5–12?

The labeler — a Claude instance simulating the dispatch caller — classified all 109 no-smoke entries into domain (5-way) + posture (8-way) using only the task text plus a taxonomy rubric. No gold labels and no conversation context were provided, making this a conservative floor: the production dispatch caller has more context available.

Probe and label artifacts (`.tmp/score_labeling.py`, `.tmp/labeler-output.jsonl`) are local throwaway — they depend on the local-only corpus at `~/.claude/state/wayfinder-corpus/2026-06-12/` and are not committed. The numbers in this section are the durable record.

### 13.1 Labeling accuracy (no-smoke, n=109, 100% coverage)

| Axis | Labels | Accuracy |
|---|---|---|
| domain (5-way) | 109/109 | **0.697 (69.7%)** |
| posture (8-way) | 109/109 | **0.927 (92.7%)** |

The inversion is notable: posture (8 classes, expected to be the harder axis) was labeled more accurately than domain (5 classes). The explanation is structural — domain errors concentrate in one systematic pattern (§13.2), whereas posture vocabulary is largely unambiguous in the task texts.

### 13.2 Domain errors — systematic and fixable

**Dominant error: `is_any → project_meta`, 16 of 16 cases (100% of is_any entries mislabeled)**

The labeler never reached for `is_any`. The rubric ambiguity: GitHub/VCS tasks that involve no file paths read as `project_meta` to the labeler (repo-level activity framing). This is a known rubric gap, not a capability failure.

Critically, these 16 errors cost approximately zero in routing: `is_any` is not domain-gated, so the Compose system treats both the real label and the oracle label identically — no routing decision changes.

**Other notable domain mislabels:**

| Error pattern | Count |
|---|---|
| `project_meta → docs_prose` | 6 |
| `code → project_meta` | 3 |
| `is_any → project_meta` (systematic) | 16 |

### 13.3 Posture errors — routing-costly class

The only routing-costly posture error class is `operate → build` (3 entries): ops tasks mislabeled as build posture, routed to `code-writer` instead of `ops`. Posture was otherwise clean:

| Posture | Correct | Total | Accuracy |
|---|---|---|---|
| build | 51 | 52 | 0.981 |
| operate | 33 | 36 | 0.917 |
| diagnose | 4 | 4 | 1.000 |

### 13.4 Four-system routing comparison (no-smoke, n=109)

| System | RC | ΔRC vs lexical | CW |
|---|---|---|---|
| Lexical | 0.3303 | +0.0000 | 0.2558 |
| Oracle domain-only | 0.4954 | +0.1651 | 0.0755 |
| Oracle two-axis Compose | 0.7798 | +0.4495 | 0.1414 |
| Real-label two-axis Compose | 0.7431 | +0.4128 | 0.2430 |

### 13.5 Headroom recovery (the headline)

Real-label Compose recovers **91.8% of the oracle RC headroom** above lexical:

```
(0.7431 − 0.3303) / (0.7798 − 0.3303) = 0.4128 / 0.4495 = 0.918
```

Oracle−real gap: **+0.0367 RC = 4 entries / 109**. The floor is nearly the ceiling.

### 13.6 Per-domain RC (real-label Compose vs oracle Compose)

| Domain | n | Lexical | OracleDom | OracleCompose | RealCompose |
|---|---|---|---|---|---|
| code | 45 | 0.200 | 0.444 | 0.911 | 0.844 |
| docs_prose | 13 | 0.538 | 0.769 | 1.000 | 0.923 |
| infra_deploy | 5 | 0.600 | 0.400 | 0.000 | 0.000 |
| is_any | 16 | 0.625 | 0.625 | 0.875 | 0.875 |
| project_meta | 30 | 0.233 | 0.400 | 0.567 | 0.567 |

`infra_deploy` (n=5) reaches 0.000 RC in both oracle and real Compose — this is the `(infra_deploy, research) → researcher` mis-cell identified in §12.6, not a labeling failure. `is_any` and `project_meta` are unaffected by the domain mislabeling (routing-neutral errors, §13.2).

### 13.7 CW caveat (design-shaping)

Real-label Compose's CW (0.243) ≈ lexical (0.256). It delegates 107/109 entries, so a wrong label becomes a confident-wrong dispatch rather than an abstention. The RC gain is real, but the CW figure signals a design requirement:

**A production design must preserve an abstention/advisory path.** A low-confidence label should degrade to advisory rather than firing a confident-wrong dispatch. The oracle/real CW gap (0.141 vs 0.243) is precisely the label-noise cost — it lands in CW, not RC. Structurally: RC is robust to label noise at the 92.7%/69.7% accuracy levels observed; CW is not.

### 13.8 Verdict (updated)

**GO — two-axis (domain + posture), Compose variant, LLM-supplied labels.**

Three converging probes validate the architecture:

| Probe | RC gain above lexical | Evidence type |
|---|---|---|
| Domain-only oracle (§5) | +16.5 pp | Ceiling, domain axis |
| Two-axis oracle (§12) | +44.9 pp | Ceiling, both axes |
| Two-axis real-label (§13) | +41.3 pp, 91.8% headroom recovery | Floor, production-realistic |

The framing ambiguity that killed the regex posture extractors (#357) is resolved by the LLM at 92.7% posture accuracy — the judgment moved to the component that can do it.

**Open design items before production:**

1. **Preserve an abstention path.** CW rises from 0.141 (oracle) to 0.243 (real labels) — label noise must degrade to advisory, not fire confidently wrong.
2. **Sharpen the domain rubric: `is_any` vs `project_meta`.** The 16 systematic mislabels are zero-routing-cost now but will matter if `is_any` gating is ever introduced.
3. **Resolve the `infra_deploy` cell-map mis-cell** (`research↔diagnose`, n=5). Both oracle and real Compose fail here; the fix is in the cell-map, not the labeler.
4. **Shadow-mode rollout.** Measure in-situ labeling accuracy against live dispatch decisions before enabling hard routing.
