# Next-Stage Matcher Trigger Algorithms: Evaluation

> Status: research exploration · Issue: [#288](https://github.com/glitchwerks/claude-wayfinder/issues/288)  
> Hard constraint: matcher must remain deterministic, pure-Python, sub-millisecond per dispatch call. No LLM in the hot path.

---

## 1. Context: what the current matcher does and where it hits ceilings

The v5 matcher (`src/claude_wayfinder/match.py`) is a pure token-set intersection engine. Feature extraction produces a `frozenset[str]` of lowercase single tokens from the task description. Scoring accumulates additive contributions: `0.5 × weight` per matched keyword, `0.4` per matched path glob, `0.5` per matched tool mention, plus short-circuit `1.0` for command prefix or explicit agent mention.

The `keyword_groups` extension (Issue #135) added conjunctive AND-triggers: a group fires only when every slot has at least one matching token. It is still a token-set intersection — the conjunctive constraint just gates the score on multiple tokens co-occurring.

**What this cannot do** (confirmed by dispatching real prompts from the dispatch log):

| Prompt in dispatch log | Expected | Actual | Gap |
|---|---|---|---|
| `"run ruff linter on the codebase"` | python or linter skill | `self_handle_unaided` | `ruff` not in keyword list; `linter` matched nothing |
| `"implement the function"` (file: `src/main.py`) | code-writer | `self_handle_unaided` | `function` not a trigger; `implement` alone scored below threshold without glob match |
| `"implement the feature"` (file: `main.py` — no `src/`) | code-writer | `self_handle_unaided` | path glob `**/*.py` requires `src/` prefix via fnmatch |
| `"explain how python decorators work"` (file: `src/utils.py`) | python skill | `self_handle` python | Matched (only because file matched glob) — correct but fragile |

The pattern: **morphological variants of trigger terms** (`implementing`, `refactored`, `deploying`) and **synonymic near-misses** (`defect` for `bug`, `ship` for `deploy`, `ruff` for `linter`) produce zero score where a human would immediately route. This is the gap the techniques below address.

---

## 2. Catalog sample (as of catalog hash `57c984...`, fetched 2026-05-28)

Real trigger terms observed in the catalog (selected representative entries):

- `code-writer` agent: `implement (1.0)`, `feature (0.5)`, `function (0.5)` — plus `**/*.py`, `**/*.ts` globs
- `python` skill: `python (1.0)`, `pytest (0.5)`, `venv (0.5)`, `pip (0.5)`, `pep8 (0.25)`
- `ops` agent: `gh (tool_mention)`, `git (0.5)`, `deploy (0.5)`, `merge (0.5)`
- `claude-api` skill: `claude (1.0)`, `anthropic (1.0)`, excludes: `openai`, `gpt`

The catalog has ~100 entries (agents + skills). At ~10 trigger terms each, a full scoring pass requires ~1,000 token comparisons — all `frozenset.__contains__` lookups, which are O(1). The budget for any additive pre-processing is therefore tight: anything that adds per-token computation must run on the task description tokens, not per-catalog-entry.

---

## 3. Technique evaluations

### 3.1 Fuzzy matching — Levenshtein / Jaro-Winkler / RapidFuzz

**What it catches that the current matcher misses:**

Edit-distance fuzzy matching would catch:
- Typos: `implemnet` → `implement`, `debgu` → `debug`
- Inflections that are not pure stem variants: `scripting` vs `script` (distance 3 is a miss for Levenshtein at threshold ≤ 2; Jaro-Winkler at 0.85 would catch `scripting`/`script` because they share a common prefix)
- Compound fragmentation: `git-rebase` vs `gitrebase` (distance 1 after hyphen removal)

**What it mis-catches (false-positive shapes):**

Short tokens are the primary hazard. Jaro-Winkler at 0.85:
- `"bug"` (3 chars) vs `"bun"` → Jaro = 0.89 — **false positive**
- `"aws"` vs `"gws"` → Jaro ≈ 0.78 — avoided at 0.85
- `"port"` vs `"sort"` → Jaro ≈ 0.83 — *borderline* — `port`/`sort` are semantically unrelated

Levenshtein at distance 1:
- `"lint"` → would match `"hint"`, `"line"`, `"list"`, `"link"` — all false positives in a routing context
- `"plan"` → `"clan"`, `"play"`, `"span"` — all distance 1

The token length problem is severe: the catalog trigger vocabulary skews toward short tokens (3–8 chars). At edit distance 1, token collision rates spike for tokens under 5 characters.

**Concrete catalog interactions:**
- `python (1.0)` would fuzzy-match `pytho`, `pyhton` (typos) — useful but the catalog author is also the one writing the prompt; typos in structured dispatch context are rare
- `implement (1.0)` would fuzzy-match `implements`, `implemented` — distance 2, useful
- `lint (1.0)` would fuzzy-match `line`, `hint`, `list` — distance 1, dangerous

**Authoring burden:** None. Fuzzy matching is transparent to catalog authors. The match logic changes in `match.py`; sidecar files do not need any additions.

**Runtime cost:**
- Pure Python Levenshtein on two tokens of length k: O(k²). For k=8, that is ~64 operations per pair.
- With 1,000 catalog-term comparisons at k=8 average: ~64,000 operations per dispatch call. This is compute-safe (sub-millisecond in CPython for simple arithmetic loops).
- RapidFuzz ([github.com/rapidfuzz/RapidFuzz](https://github.com/rapidfuzz/RapidFuzz), fetched 2026-05-28) is mostly written in C++ with SIMD optimization and is 40%+ faster than pure Python FuzzyWuzzy equivalents. However, it is a C extension, which adds an optional dependency.
- **The real cost is false positives,** not CPU time.

**Auditability:** Poor for short tokens. The rationale line `"matched keywords: lint"` is clear; `"fuzzy-matched 'linting' → 'lint' (distance 1)"` is understandable but requires a more complex audit trail. Short-token false positives (`"hint" → "lint"`) would be invisible in the rationale and would confuse skill authors diagnosing unexpected dispatch.

**Integration shape:** Pre-processing pass. Fuzzy matching expands the extracted feature token set before scoring: `features.keywords` is augmented with near-matches from the catalog term vocabulary before the standard `k.term in features.keywords` check runs. This is a **normalization-before-scoring** slot.

**Recommendation:** Levenshtein fuzzy matching is **viable only with a token-length guard** (minimum 6 characters, maximum distance 1) and a **catalog-term allowlist** (only terms the skill author marks as fuzzy-eligible). Without gating, false-positive rates on short tokens are unacceptable. The Jaro-Winkler variant has the same problem at any threshold that would usefully catch morphological variants of short tokens.

---

### 3.2 Stemming — Porter / Snowball

**What it catches that the current matcher misses:**

Stemming collapses inflected and derived forms to a common root:
- `refactor`, `refactoring`, `refactored`, `refactors` → stem `refactor`
- `deploy`, `deploying`, `deployed`, `deployment` → stem `deploy`
- `lint`, `linting`, `linted` → stem `lint`
- `implement`, `implementing`, `implemented` → stem `implement`

This directly addresses the morphological drift problem named in the issue. A catalog trigger `implement (1.0)` would match `implementing` in the prompt without the author enumerating every form.

**What it mis-catches (false-positive shapes):**

Known Snowball/Porter collision cases (all deterministic — these will always happen):
- `port` → stem `port`; `portrait` → stem `portrayal` (Porter2) — this pair is safe  
- `organization` → stem `organ` (Porter1 known over-stem); Snowball (Porter2) does better but not perfectly
- `linger` → stem `linger`; `link` → stem `link` — safe  
- `universe` → stem `univers`; `university` → stem `univers` — **same stem**; routing to a `university` context from a `universe` prompt is a false positive (edge case, unlikely in practice)
- `analysis` → stem `analysi`; `analyst` → stem `analyst` — different stems, safe

In practice for the claude-wayfinder domain vocabulary (technical/coding terms), stem collisions are rare. Domain-specific terms like `powershell`, `pytest`, `bicep`, `anthropic` do not have stem collisions with other catalog terms.

**Critical footgun — stem the catalog, not just the prompt:**

Both the catalog trigger terms AND the extracted features must be stemmed against the same stemmer. If the catalog has `implement (1.0)` stored as-is, and the prompt tokens are stemmed, `"implementing" → "implement"` matches. But if the catalog term `implement` is also stored as-is and features are stored as stems, `implement` (stem) matches `implement` (term) directly. The normalization must be symmetric.

Implementation approach: stem all catalog keyword terms at catalog-load time (once, not per-dispatch), cache the stemmed terms. Stem feature tokens at extract time. The `frozenset` comparison then operates on stems throughout.

**Authoring burden:** Medium. Catalog authors do not need to enumerate morphological variants, but they must be aware that their trigger terms will be stemmed. A term like `aws` would stem to `aw` under Porter — potentially colliding with other stems. Authors need a stem-preview tool or at minimum documentation of the stemmer's behavior on their chosen terms.

Generator-level validation could catch high-collision stems (stems that would match ≥ N other catalog terms) and emit a warning.

**Runtime cost:**

- `snowballstemmer` (pure Python, PyPI: `snowballstemmer`) is the lightest option. Per [PyPI](https://pypi.org/project/snowballstemmer/), fetched 2026-05-28: generated Python code, no C dependency.
- `PyStemmer` ([github.com/snowballstem/pystemmer](https://github.com/snowballstem/pystemmer), fetched 2026-05-28) wraps the C Snowball library and is ~30× faster than the pure Python version.
- Dispatch call cost: stem the prompt tokens (typically 5–15 tokens per description). At ~1µs per token for `PyStemmer`, this is ~15µs — negligible against the 50–200ms dispatch budget. The `snowballstemmer` pure Python version is ~30× slower, ~450µs for 15 tokens, still sub-millisecond.
- Catalog-load-time stemming: ~1,000 terms × 1µs = ~1ms per catalog load. This is a one-time cost amortized across many dispatches.

**Auditability:** Good. The rationale line becomes `"matched stem 'implement' (from 'implementing')"` — a skill author reading the audit line understands immediately why the match fired. The stem is a human-readable root form.

**Integration shape:** Normalization-before-scoring, applied to both features (at extraction) and catalog terms (at catalog-load time). Slots into the current pipeline without changing the scoring formula: the `k.term in features.keywords` check operates on stems instead of raw tokens.

**Recommendation: HIGH PRIORITY PILOT.** Stemming is the technique with the best cost/benefit ratio for this catalog. It is deterministic, cheap, comprehensible to authors, and directly addresses the morphological drift failure mode that accounts for multiple `self_handle_unaided` misfires in the dispatch log (`"run ruff linter"`, `"implementing the feature"`). The primary risk — stem collisions — is mitigatable with a stem-collision checker in the catalog generator.

---

### 3.3 Morphological lemmatization (spaCy / NLTK)

**What it catches that the current matcher misses:**

Lemmatization is more linguistically precise than stemming: `"ran"` → `"run"`, `"better"` → `"good"`, `"geese"` → `"goose"`. For the technical vocabulary in this catalog, lemmatization and stemming produce nearly identical results because technical terms are regular: `"implementing"` → `"implement"`, `"deployed"` → `"deploy"`.

The delta between lemmatization and stemming is most visible on English irregular verbs and morphologically complex words — the technical domain this catalog covers has almost none of these.

**What it mis-catches:** Same risk profile as stemming, lower mis-catch rate. `spaCy`'s English lemmatizer resolves POS context (the same string may have different lemmas as verb vs noun), which reduces false positives.

**Authoring burden:** Higher than stemming. Lemmatization accuracy requires POS context, which requires a loaded language model (even `spaCy`'s `en_core_web_sm` is a 12 MB download). This is an external dependency that the current matcher has zero of.

**Runtime cost:**

- `spaCy` with `en_core_web_sm`: model load ~200ms on first call (cold start), ~0.1–0.5ms per document on subsequent calls. The cold start is non-trivial.
- `nltk.stem.WordNetLemmatizer`: requires `wordnet` corpus download (~10 MB). Per-token cost is higher than Snowball because it does a dictionary lookup. Without POS context it defaults to noun lemmatization and misses verb forms (`"running"` → `"running"` instead of `"run"`).
- Neither is appropriate for a sub-millisecond matcher without a persistent in-process model.

**Integration shape:** Would need a persistent daemon or pre-warmed process to avoid the cold-start penalty. This fundamentally changes the deployment model.

**Recommendation: DROP for hot path.** The accuracy delta over Snowball stemming does not justify the dependency footprint, download requirement, and cold-start latency for the technical vocabulary this catalog uses. Lemmatization is appropriate for general English NLP; the catalog vocabulary is almost entirely technical tokens (programming terms, tool names, domain verbs) where Snowball stemming is already accurate.

**Exception for offline use:** spaCy or NLTK lemmatization could be used at catalog **build time** — the `build_catalog.py` generator is not latency-constrained. A generator that uses spaCy to produce a canonicalized stem table (stored in the catalog JSON alongside the raw terms) would give hot-path access to accurate lemma forms at zero runtime cost.

---

### 3.4 Lexical synonym expansion (WordNet / hand-curated)

**What it catches that the current matcher misses:**

The issue names: `bug` vs `defect` vs `regression`, `deploy` vs `release` vs `ship`. These are not morphological variants — they are semantically equivalent terms with no shared stem. Stemming cannot help. The only deterministic solution is an explicit synonym table.

Two sources:
1. **WordNet** ([NLTK WordNet](https://www.nltk.org/howto/stem.html), fetched 2026-05-28): Princeton's lexical database. `wordnet.synsets("deploy")` returns synsets including `"deploy"`, `"use"`, `"employ"` — overly broad for routing purposes. Technical computing terms are often absent or poorly covered (WordNet was built for general English).
2. **Hand-curated catalog-level synonym groups**: the catalog author explicitly lists synonyms for a trigger term. Expressible today via multiple keyword entries (`deploy (1.0)`, `release (1.0)`, `ship (0.5)`), but requires the author to anticipate every synonym.

**What it mis-catches:**

WordNet synonymy is context-free. `"ship"` in WordNet resolves to the vessel as well as the deployment verb — a query about "ship maintenance" would score against `deploy` triggers. Technical synonyms are especially unreliable: `"port"` (TCP port), `"port"` (porting code), `"port"` (beverage) all land in the same bucket. WordNet cannot disambiguate.

**Authoring burden for WordNet expansion:** High. Authors must audit which WordNet synsets are safe for their skill, which requires NLP knowledge. The failure mode is silent: unexpected routing from semantically tangential terms.

**Authoring burden for hand-curated synonyms:** Low — this is exactly what the current multi-keyword approach supports. The `keyword_groups` mechanism already provides a structured way to express synonym clusters as OR-slots within AND-groups.

**Runtime cost:** WordNet lookup at dispatch time: requires NLTK corpus (~10 MB) and a dictionary lookup per token. Not appropriate for the hot path. At catalog-build time: fine.

**Integration shape:** Two viable forms:

1. **Pre-processing expansion (build time):** The catalog generator expands each trigger term with its WordNet synonyms (filtered to the technical domain vocabulary) and stores expanded keyword lists in the catalog JSON. The hot path remains unchanged — it still does token-set intersection, but the catalog terms now include synonyms. This is a **catalog-content change, not a matcher change.**

2. **New sidecar field (authoring time):** A `synonyms:` block in `triggers.yml` lets authors explicitly declare synonym clusters. These expand to additional keywords at catalog-build time. This is lower risk than WordNet automation because the expansion is author-controlled.

**Recommendation:** For **WordNet automation — DROP** from hot path; acceptable at build time only, with mandatory author review before committing expanded catalog. For **hand-curated synonyms via a new `synonyms:` sidecar field — MEDIUM PRIORITY PILOT.** It is a natural extension of the existing authoring model, gives authors explicit control, and has zero hot-path cost. The `keyword_groups` OR-slot mechanism partially covers this already; a dedicated `synonyms:` field would make the intent clearer and reduce authoring friction.

---

### 3.5 Adjacency / proximity scoring

**What it catches that the current matcher misses:**

Proximity scoring boosts the match weight when two trigger keywords appear within N tokens of each other in the prompt. This addresses word-order ambiguity: "review my PR" and "PR review" both contain the tokens `review` and `pr`; proximity scoring doesn't care about order, only distance.

The `keyword_groups` (AND-conjunctive) mechanism already handles "both tokens present anywhere in the description." Proximity scoring is a tighter version: "both tokens present AND within N tokens of each other."

**What it mis-catches:**

Proximity is a weak signal in short prompts. A task description of 10 tokens means any two tokens are within distance 10 of each other — the proximity gate is trivially satisfied for all prompts under ~10 words. The average dispatch description appears to be 5–15 tokens (observed from log samples). For these lengths, proximity degrades to the same behavior as `keyword_groups` co-occurrence.

Proximity is most useful for long, multi-topic descriptions where two skill keywords may coincidentally appear in different clauses with unrelated intent. In a 5-word description, this never happens.

**Authoring burden:** New frontmatter concept (proximity window `N`) that authors must reason about. How large is the window? Too small: misses valid formulations. Too large: same as co-occurrence. There is no natural default.

**Runtime cost:** Requires the token list (with positions) rather than a token set. `extract_keywords` currently produces a `frozenset` — positions are discarded. To support proximity, feature extraction must produce a token sequence with positions, increasing memory per dispatch. The matching loop becomes an O(K × T) scan (catalog terms × task tokens) rather than an O(K) frozenset lookup. For K=1,000, T=15: 15,000 iterations instead of 1,000 lookups — a 15× increase in the keyword scoring inner loop, though still well under milliseconds.

**Integration shape:** Alongside existing scoring. A `proximity_groups` field (analogous to `keyword_groups`) carries a list of `{terms, window, weight}` objects. The matcher scans token positions rather than sets for these entries. Other scoring dimensions (path globs, tool mentions, single keywords) are unaffected.

**Recommendation:** LOW PRIORITY. The benefit is marginal for the short descriptions that dominate real dispatch traffic. `keyword_groups` already covers the "both tokens present" case that is 95% of the use case. The 5% case (long descriptions with unrelated coincidental term proximity) is not a documented failure mode in the dispatch log. Defer until there is a specific corpus of failures that only proximity can fix.

---

### 3.6 Phrase matching (literal multi-word triggers)

**What it catches that the current matcher misses:**

A multi-word phrase trigger like `"pull request"` or `"claude code"` matches as a unit. This is distinct from AND-group co-occurrence (`keyword_groups`) in two ways:
1. Order matters: `"pull request"` is different from `"request pull"`.
2. Adjacency is required: `"pull"` and `"request"` must be adjacent in the description.

Currently, an author wanting `"pull request"` as a high-signal trigger must either:
- Use two separate keywords `pull (0.5)` and `request (0.5)` — each scores independently, resulting in a combined score of 0.5 (0.5 × 0.5 + 0.5 × 0.5) even when they appear adjacent, but also scoring from either term alone
- Use a `keyword_group` with both as slots — requires both present anywhere, not necessarily adjacent, and doesn't require adjacency

The phrase `"claude code"` (a product name) is the canonical motivating example from the issue: both words are common technical tokens that would score spuriously in many contexts if treated as independent keywords.

**What it mis-catches:**

Any prompt containing the phrase in an unrelated context: "I was reading about PR code review and noticed the claude code integration is broken" — the phrase `"claude code"` appears as an artifact of sentence construction, not intent. For multi-word brand names and technical terms, this is a real hazard.

Phrase ordering sensitivity may actually be desirable precision: "code review" vs "review code" might legitimately map to the same skill, but the author can list both phrases explicitly or use `keyword_groups` for the order-insensitive version.

**Authoring burden:** Low — the concept is intuitive. A `phrase_triggers:` block in `triggers.yml` is easy to explain: "this fires only when these words appear adjacent in this order." The only author footgun: forgetting that tokenization strips punctuation, so `"pull-request"` in the prompt becomes `["pull", "request"]` after hyphen-stripping (unless hyphen-preserving tokenization is applied, which the current tokenizer does for intra-word hyphens but not for standalone hyphen-connected phrases).

**Runtime cost:** Requires the token sequence (positions) as with proximity. For phrase matching, the implementation is a sliding-window scan over the token sequence: for each phrase of length L in the catalog, scan the token sequence for a consecutive match. O(T × P) where T is task token count and P is phrase count. For T=15, P=20 phrases across all catalog entries: 300 comparisons — trivially fast.

**Integration shape:** Alongside existing scoring. A new `phrase_triggers: [{phrase: "pull request", weight: 1.0}]` sidecar field contributes to the entry score when the phrase appears in the token sequence. The score contribution formula mirrors keywords: `0.5 × weight` per matched phrase. The phrase match fires only when tokens are consecutive — no window parameter needed.

**This requires changing the feature representation:** `extract_keywords` must return both a frozenset (for existing keyword matching) and a sequence (for phrase and proximity matching). The sequence is a list of strings preserving original token order.

**Recommendation: MEDIUM PRIORITY PILOT.** Phrase matching fills a gap that `keyword_groups` cannot fill (ordering and adjacency). The implementation is simple, the authoring model is intuitive, and the false-positive risk is controlled by requiring adjacency. The main prerequisite is exposing the token sequence from feature extraction alongside the existing frozenset. This change is confined to `extract_keywords` and the scoring loop for phrase-tagged entries.

---

### 3.7 Regex triggers

**What it catches that the current matcher misses:**

Regex triggers enable patterns that no other technique can express:
- Numeric identifiers: `#\d+` (issue numbers), `v\d+\.\d+` (version strings)
- File-extension constellations from the task description (not just file paths): `\.ya?ml`
- Compound token patterns: `\baws\s+lambda\b`, `\bdocker\s+compose\b`
- Anchor patterns for command-style prompts: `^deploy\s+to\s+`

**What it mis-catches:**

Catastrophic backtracking in Python's `re` module is the core safety hazard. Python's `re` uses a backtracking NFA engine, meaning a malformed or adversarial pattern can take O(2ⁿ) time on a crafted input. For catalog authors writing their own patterns, this is an authoring-time footgun: a pattern like `(a+)+b` against a long input will hang the process.

Even well-intentioned patterns can be unexpectedly slow on degenerate inputs. And the match is no longer just set-intersection — the auditability of `"matched keyword: lint"` disappears. The rationale becomes `"matched regex: ^(?:deploy|release|ship)\s+to\s+(?:prod|staging)"` — technically auditable but much harder for a non-expert author to reason about.

**Mitigations for backtracking:**

1. **`google-re2` Python binding** ([github.com/google/re2](https://github.com/google/re2), fetched 2026-05-28): RE2 provides guaranteed linear-time matching via a DFA construction. PyPI package: `google-re2`. No lookaheads, no backreferences — a strict subset of PCRE. This eliminates catastrophic backtracking at the cost of a C extension dependency and a reduced regex feature set.

2. **Regex validation at catalog-build time:** The catalog generator pre-compiles all regex triggers and runs them against a suite of adversarial inputs (long repeating strings, worst-case inputs for common pathological patterns). Any pattern that exceeds a time limit is rejected as `fatal`.

3. **Timeout per dispatch call:** Wrap the regex match in a signal timeout (POSIX-only) or subprocess. This is not portable to Windows and adds overhead.

**Authoring burden:** High. Regex authoring requires familiarity with regex syntax, anchoring semantics, and the specific flavor (Python `re` vs RE2 subset). The failure mode for a bad regex is either silent non-match (wrong pattern) or process hang (catastrophic backtracking). The authoring guide would need to substantially expand.

**Runtime cost:** Per-pattern regex match on a 100-character input is O(n) with RE2, O(n²) worst case with Python `re`. For a catalog of 100 entries with 2 regex patterns each: 200 regex matches per dispatch. With RE2 at O(n): ~20µs total. With Python `re` at best case: ~50µs. Acceptable in both cases assuming patterns are not pathological.

**Integration shape:** Alongside existing scoring. A `regex_triggers: [{pattern: "...", weight: 1.0}]` sidecar field. At catalog load time, pre-compile all regex patterns (with RE2 if available, falling back to `re` with a timeout guard). At dispatch time, match the task description string against each compiled pattern. Score contribution: same formula as keywords.

**Recommendation: LOW PRIORITY in current form. HIGH PRIORITY if a compile-time validator ships with it.** The backtracking hazard is real and not theoretical — a catalog author adding `(a+)+` to a production catalog would be catastrophic. The RE2 binding eliminates the hazard but adds a C dependency. If the decision is to support regex at all, the path is: (1) require `google-re2` as an optional dependency, (2) implement a catalog-generator validator that pre-compiles and stress-tests all regex patterns, (3) restrict to RE2-compatible syntax. Without the validator, regex triggers are a footgun that would eventually hang a production dispatch call.

---

### 3.8 BM25 / TF-IDF relevance scoring

**What it catches that the current matcher misses:**

BM25 treats each catalog entry's description and trigger terms as a "document" and scores the task description as a "query" against that document. It naturally captures term frequency and document-length normalization — a catalog entry that mentions `"deploy"` three times in its description scores higher against a deploy-heavy prompt than one that mentions it once.

This would help with:
- Under-specified prompts where multiple keywords weakly signal the same skill
- Long, multi-topic descriptions where term distribution should down-weight coincidental matches

**What it mis-catches:**

BM25 scores are relative across a corpus. For a 100-entry catalog (which is tiny compared to the 2M-document benchmarks BM25S targets), IDF (inverse document frequency) weights become unstable: if `"implement"` appears in 50 of 100 catalog entries, its IDF is very low and it contributes almost nothing to BM25 scores. The score distribution for small corpora is flat and noisy, not peaked.

BM25 fundamentally changes the mental model: the current matcher audit trail says `"matched keyword 'implement' with weight 1.0"` — a catalog author knows exactly why. A BM25 rationale would say `"BM25 score: 3.47"` — meaningless without knowing the IDF table, TF within the document, and the query length normalization. This directly contradicts the auditability requirement.

**BM25 implementation options:**

- `bm25s` ([github.com/xhluca/bm25s](https://github.com/xhluca/bm25s), fetched 2026-05-28): NumPy/SciPy sparse matrices, eager pre-computation. Designed for large corpora — the design philosophy of pre-computing all scores is wasteful for a 100-document corpus where the entire score matrix fits in memory anyway.
- `rank-bm25` (pure Python, no dependencies): simpler implementation, slower but no C required. For 100 documents, query time is sub-millisecond.
- Neither is appropriate as a drop-in for the current scoring model.

**Integration shape:** BM25 cannot slot in as a "alongside existing scoring" dimension without a design decision about how BM25 scores combine with the existing {0.25, 0.5, 1.0} weight ladder. The ladder is a budget-based system; BM25 scores are real-valued with corpus-dependent scale. A naive additive combination would be numerically incoherent.

The viable integration is **re-ranking after the existing ladder:** run the current keyword/group/glob scoring first; if the result is `ambiguous` (two agents within the 0.2 gap), use BM25 as a tiebreaker. This limits BM25's footprint and preserves the audit narrative for clear decisions.

**Authoring burden:** High in a fundamentally different way. BM25 requires corpus content (description text). The current catalog entries carry descriptions, but these were written for human readers, not BM25 scoring. BM25 quality depends heavily on description quality. Authors would need to think about term density in their descriptions, IDF implications of common terms, etc. This is a hidden authoring burden that is difficult to expose as a warning.

**Recommendation: DROP from hot path. CONSIDER as offline corpus-calibration tool.** BM25 is useful for generating a labelled prompt corpus at catalog-build time: run BM25 over a collected set of real prompts to identify which catalog entries are semantically similar (high overlap in their document representations) — this informs which entries need stronger disambiguation terms in their keywords. It is an authoring-support tool, not a runtime component.

---

## 4. Integration with the weight ladder and seven-decision structure

All techniques must map their signal onto the existing score space `[0.0, 1.0]` and the five decision thresholds (`0.5` for `self_handle`/`advisory`, `0.85` for `delegate`, `0.2` gap for `delegate` vs `ambiguous`).

| Technique | Integration slot | Score contribution | Ladder compatibility |
|---|---|---|---|
| Stemming | Pre-processing (normalization) | No change — existing formula runs on stem tokens | Perfect: transparent to the ladder |
| Phrase matching | Alongside, new `phrase_triggers` field | `0.5 × weight` per matched phrase | Additive like keywords; fits the ladder |
| Hand-curated synonyms | Build-time catalog expansion | No runtime change | Perfect: transparent to the ladder |
| Fuzzy matching (gated) | Pre-processing (feature expansion) | Adds soft tokens to feature set with fractional weight | Requires score-dampening (0.5× of keyword weight) to avoid inflating scores |
| Proximity scoring | Alongside, new `proximity_groups` field | Bonus over existing group score | Additive; fits the ladder |
| Regex triggers | Alongside, new `regex_triggers` field | `0.5 × weight` per matched pattern | Additive; fits the ladder |
| BM25 tiebreaker | Post-scoring re-rank | Replaces score for ambiguous pairs | Does not change ladder thresholds; changes gap calculation |
| Lemmatization | Pre-processing (normalization) | No change | Same as stemming |

The weight ladder itself does not need to evolve for any of these techniques except fuzzy matching (which needs a dampening factor to prevent false-positive inflation from soft near-matches). Stemming and phrase matching slot in without changing the arithmetic.

---

## 5. Shortlist: recommended pilots, ranked

### Rank 1: Stemming (Snowball/Porter2) — **Pilot immediately**

- Addresses the single highest-frequency failure class: morphological variants (`implementing`, `refactored`, `deploying`, `linting`, `scripting`)
- Zero new authoring concepts — transparent to skill/agent authors
- Deterministic and fully auditable
- Pure Python option available (`snowballstemmer` package, no C dependency)
- Sub-millisecond overhead for 15-token descriptions
- Prerequisite: stem-collision checker in the catalog generator (emits a warning when two different catalog terms stem to the same form, e.g. `lint` and `line` both stem to `lint` under some stemmers — must be verified and documented)
- Schema changes: none to `triggers.yml`; catalog JSON gains a `stemmed_terms` field per entry (optional for backward compat)

**Implementation note:** the stemmer should be symmetric — catalog terms are stemmed at catalog-load time and cached; feature tokens are stemmed at extraction time. The scoring formula `k.term in features.keywords` becomes `stem(k.term) in features.stemmed_keywords`. No other change to `match.py` logic.

### Rank 2: Phrase matching — **Pilot after stemming**

- Fills the gap that `keyword_groups` cannot fill: ordered adjacency
- Addresses the `"claude code"`, `"pull request"`, `"type hints"` pattern named in the issue
- Low authoring burden — intuitive concept
- Requires feature extraction to retain token sequence (minor change to `extract_keywords`)
- Scoring contribution slots into existing formula without disrupting thresholds
- Schema addition: `phrase_triggers: [{phrase: str, weight: float}]` in `triggers.yml`

**Prerequisite:** the token sequence (ordered list) must be exposed alongside the existing frozenset from `extract_keywords`. This is a non-breaking addition — the frozenset path continues to work unchanged; phrase scoring reads from the new sequence.

### Rank 3: Hand-curated synonym expansion via `synonyms:` sidecar field — **Pilot after phrase matching**

- Addresses the lexical synonymy failure mode (`bug`/`defect`/`regression`, `deploy`/`ship`/`release`)
- Fully author-controlled — no WordNet automation risk
- Build-time expansion: the catalog generator expands `synonyms:` into additional keyword entries at catalog-build time. Hot path unchanged.
- Schema addition: `synonyms: [{term: str, expands_to: [str], weight: float}]` — or more simply, allow a `synonyms:` block that lists alternative terms for an existing keyword, inheriting its weight
- Authoring guidance: synonyms should be semantically equivalent in the deployment context, not just lexically similar. `deploy` → `[ship, release, push]` is valid; `deploy` → `[run, execute]` is too broad.

---

## 6. Dropped from shortlist with reasons

| Technique | Reason dropped |
|---|---|
| Jaro-Winkler fuzzy matching | Short-token false-positive rate is unacceptable without per-token length gating. The gated version is marginally better than stemming for typo correction but adds complexity and an optional C dependency for a failure class not documented in the dispatch log. |
| Levenshtein fuzzy matching | Same as Jaro-Winkler. Token length gating required. The only use case not covered by stemming is genuine typos, which are rare in author-composed dispatch contexts. |
| Morphological lemmatization | Dependency footprint (spaCy model or NLTK corpus) is not acceptable for a hot-path component. At build time, acceptable but delta over Snowball stemming is negligible for technical vocabulary. |
| WordNet synonym expansion (automated) | Too broad for technical domain; context-free synsets produce unacceptable false positives. Author-controlled synonym expansion (Rank 3 above) is the right model. |
| Proximity scoring | Marginal benefit for 5–15 token descriptions where proximity degrades to co-occurrence. `keyword_groups` already handles the common case. |
| BM25 / TF-IDF | IDF instability at 100-document corpus size; auditability violation (BM25 score not human-interpretable without corpus context); mental model mismatch. |
| Regex triggers (without validator) | Catastrophic backtracking hazard in Python's `re` module. Viable only with `google-re2` binding and a catalog-generator stress-test validator. If both are implemented, regex moves to medium priority. |

---

## 7. Telemetry and fixtures needed

### For any pilot to be measurable (prerequisite for all three ranked pilots)

1. **Labelled prompt corpus (v1 deliverable for any implementation issue):** A JSONL file of `{prompt, file_paths, expected_decision, expected_agent, expected_skills}` records. Minimum 50 labelled examples covering: morphological variants of top-10 trigger terms, synonym pairs from the issue body, phrase-containing prompts, and negative examples (prompts that should NOT match a given entry). Without this corpus, "did the pilot improve things?" cannot be answered — only "did the pilot change things?"

2. **Pre/post comparison harness:** A test that runs the labelled corpus through the current matcher and records precision/recall per decision type. The pilot is an improvement only if it increases recall (correct matches that were previously missed) without degrading precision (false positives).

3. **Stem collision report:** Before shipping stemming, run all catalog trigger terms through the chosen stemmer and produce a collision table. Any two catalog terms that stem to the same form must be reviewed — if they belong to competing skills (e.g. if `"lint"` and `"line"` both stem to `"lint"` and both appear in different skills' catalogs), one must be removed or the entry must declare the collision as intentional via a `no_stem:` flag.

### For stemming specifically

- A dispatch fixture file with at least 10 prompt pairs: `{base_form, inflected_form}` — e.g., `("implement a feature", "implementing a feature")` should produce identical dispatch decisions. The fixture fails if they diverge.

### For phrase matching specifically

- A fixture set with 10 phrase-containing prompts vs their word-shuffled equivalents: `("pull request review")` should match the PR-review entry; `("review pull request")` should also match (different token order, same tokens — phrase matching is order-sensitive, so this tests that both orderings are listed as phrases or that `keyword_groups` handles the unordered case).

### For synonym expansion specifically

- A synonym coverage report: for each `synonyms:` entry, how many real dispatch-log prompts contain the synonym but not the base term? If no real prompts contain the synonym, the entry is adding noise with no benefit.

---

## 8. Prior art: how other deterministic routers handle this

### fzf / fzy (command-line fuzzy finders)
- **fzf** ([github.com/junegunn/fzf](https://github.com/junegunn/fzf), fetched 2026-05-28): Modified Smith-Waterman scoring (V2). Bonuses for word boundaries (+8–10 pts), consecutive matches (+4 pts), first-character multiplier (×2). **Lesson:** the consecutive-match bonus is structurally similar to phrase matching — rewarding adjacency over scattered occurrence.
- **fzy** ([github.com/jhawthorn/fzy](https://github.com/jhawthorn/fzy), fetched 2026-05-28): Dynamic programming with affine gap penalties. **Lesson:** optimal match via DP is possible for sub-second operation even at O(nm), but the model is character-level, not token-level — translating this to the token-level dispatch use case is non-trivial.
- **Key difference from wayfinder:** fzf/fzy match a single query against candidates; wayfinder scores a single query against a catalog to identify the *best* candidate, not to rank all candidates. The top-N candidate selection problem is similar; the scoring units are different (characters vs tokens).

### VS Code command palette
- Uses a bonus-based scoring system that rewards: word-boundary matches, sequential (consecutive) character runs, first-character prefix matches ([issue #27317 analysis](https://github.com/Microsoft/vscode/issues/27317), fetched 2026-05-28). Does not use Levenshtein — explicitly designed *not* to treat distance as the primary signal.
- **Lesson:** VS Code deliberately avoids edit-distance fuzzy matching in the command palette because false-positive rates are unacceptable for short command names. It uses positional bonuses instead. This corroborates the short-token false-positive risk identified in §3.1.

### Elasticsearch / Lucene analyzer chain
- Lucene's standard analysis pipeline: tokenize → lowercase → stopword removal → stemming (optional, Porter). Stemming is a standard pre-processing stage, not an experimental one. BM25 is the default Elasticsearch scoring function since version 5.0.
- **Lesson:** the Lucene pipeline validates the "stemming is straightforward, BM25 is for larger corpora" separation. At ~100 documents, Lucene itself recommends BM25 with a large `k1` parameter (to flatten term frequency effects) — but even so, the minimum viable BM25 corpus is 100s of documents with realistic term variety.

### RapidFuzz
- ([github.com/rapidfuzz/RapidFuzz](https://github.com/rapidfuzz/RapidFuzz), fetched 2026-05-28) C++ backed, implements Levenshtein, Jaro-Winkler, token sort/set ratio, partial ratio. Faster than FuzzyWuzzy by a large margin. Has a pure Python fallback for environments where C++ compilation fails.
- **Lesson:** for batch operations (`cdist` — compute all pairwise distances in a matrix), RapidFuzz is efficient. For the dispatch use case (one query against ~1,000 catalog terms), the C++ advantage is less critical — even pure Python Levenshtein at 1,000 pairs is fast enough. The false-positive problem is algorithmic, not a performance concern.

### Sublime Text fuzzy match
- Bonus-based, rewards prefix matches and sequential runs. Like VS Code, does not use edit distance for the core ranking.
- **Lesson:** same as VS Code — prefix-biased scoring over character-level sequences, not edit distance, is the production choice for short-name matching. The wayfinder use case is more semantic (token-level, not character-level), so this lesson applies primarily to the decision to avoid character-level fuzzy matching.

---

## 9. Open questions for follow-up issues

1. **Stemmer choice:** Porter vs Snowball (Porter2) vs domain-specific. Porter2 (Snowball English) is generally recommended over Porter1 for fewer over-stems. Should be tested against the actual catalog vocabulary before committing. Can a stem-collision report be auto-generated by the catalog builder as part of a `--check-stems` flag?

2. **Symmetric stemming in the catalog JSON:** Should stemmed forms be stored in the catalog JSON (pre-computed at build time) or computed at catalog-load time in `match.py`? Storing them in JSON makes the catalog self-documenting and allows the generator to validate collisions; computing at load time avoids catalog schema changes. Which is the right trade-off?

3. **`no_stem:` escape hatch:** Some trigger terms should not be stemmed (e.g., `"aws"` should not stem to `"aw"`; `"ps1"` should not stem to anything). How is this expressed in `triggers.yml`? A per-term flag `{term: "aws", weight: 1.0, no_stem: true}` or a sidecar-level list `no_stem_terms: [aws, ps1, gh]`?

4. **Phrase tokenization consistency:** The current tokenizer strips hyphens between words (`pull-request` → `["pull", "request"]`). Does phrase matching treat `"pull request"` and `"pull-request"` as the same phrase? The tokenizer must be consistent between prompt normalization and phrase storage in the catalog.

5. **Labelled corpus creation:** Who creates the v1 labelled corpus? Is this a manual curation task (skill authors label their expected prompt patterns) or a scripted extraction from the dispatch log (prompts that manually routed correctly become positive examples, prompts that landed on `self_handle_unaided` become candidates for labelling)?

6. **Regex pilot scope:** If regex triggers are added with `google-re2`, what is the scope of the catalog-generator stress-test suite? Which pathological patterns are tested? Is there a pattern review process before catalog-build (analogous to PR review for code) to catch dangerous patterns before they reach production?

---

## 10. Sources

- `src/claude_wayfinder/match.py` — dispatch matcher source, read 2026-05-28 (this repo)
- `docs/design/trigger-schema.md` — trigger schema spec, read 2026-05-28 (this repo)
- `docs/design.md` — design rationale, read 2026-05-28 (this repo)
- `~/.claude/state/dispatch-catalog.json` — production catalog, read 2026-05-28
- `~/.claude/state/dispatch-log.jsonl` — dispatch telemetry, lines 845–1814 sampled, read 2026-05-28
- [github.com/rapidfuzz/RapidFuzz](https://github.com/rapidfuzz/RapidFuzz) — fetched 2026-05-28
- [github.com/junegunn/fzf/blob/master/src/algo/algo.go](https://github.com/junegunn/fzf/blob/master/src/algo/algo.go) — fzf scoring algorithm, fetched 2026-05-28
- [github.com/jhawthorn/fzy/blob/master/ALGORITHM.md](https://github.com/jhawthorn/fzy/blob/master/ALGORITHM.md) — fzy algorithm doc, fetched 2026-05-28
- [github.com/xhluca/bm25s](https://github.com/xhluca/bm25s) — BM25S library, fetched 2026-05-28
- [github.com/snowballstem/pystemmer](https://github.com/snowballstem/pystemmer) — PyStemmer (C-backed Snowball), fetched 2026-05-28
- [pypi.org/project/snowballstemmer](https://pypi.org/project/snowballstemmer/) — pure Python Snowball stemmer, fetched 2026-05-28
- [github.com/google/re2](https://github.com/google/re2) — RE2 engine with Python binding (`google-re2`), fetched 2026-05-28
- [arxiv.org/abs/2407.03618](https://arxiv.org/abs/2407.03618) — BM25S paper, fetched 2026-05-28
- [github.com/Microsoft/vscode/issues/27317](https://github.com/Microsoft/vscode/issues/27317) — VS Code command palette ranking discussion, fetched 2026-05-28
- `unverified:` JetBrains "Search Everywhere" uses ML-based ranking (post-2022) in addition to fuzzy substring scoring — sourced from JetBrains documentation page but exact algorithm not publicly documented.
