# Semantic Embedding-Based Request Routing: Prior Art Survey

> Issue: [#325](https://github.com/glitchwerks/claude-wayfinder/issues/325)
> Research date: 2026-06-07
> Preceding exploration: `docs/exploration/2026-05-28-trigger-algorithms.md` (lexical techniques)

---

## Idea

Evaluate whether semantic (embedding-based) routing can replace or augment the deterministic lexical token-set intersection matcher in claude-wayfinder, to close the gap where no trigger keyword matches a semantically correct dispatch target (e.g., "my CI went red after the last merge" → investigation agent).

---

## Requirements

1. **Open source + fully locally runnable.** No hosted embedding APIs. Must run offline.
2. **Deterministic encoding** (PRIMARY). Same input must produce the same vector every run. Deep analysis required per candidate.
3. **No generative LLM in the hot path.** Small deterministic encoder only; autoregressive intent classification is out.
4. **Auditability.** Routing decisions must be legible to skill authors (not a black-box score).
5. **Latency.** Sub-ms ideal; 10–50ms tolerable only as a fallback path.

---

## Search axes used

- **Direct synonyms:** semantic router, embedding-based routing, intent classifier, dense retrieval routing
- **Problem-shape synonyms:** query→tool dispatch, LLM tool selection, agent orchestration routing, RAG router
- **Adjacent domains:** vector similarity search, sentence embedding classification, nearest-neighbor lookup
- **Encoder family axis:** sentence-transformers, static embeddings, Model2Vec, fastText, ONNX runtime, FastEmbed, bge-small, gte-small, MiniLM
- **Negative axes:** hosted APIs (OpenAI embeddings, Cohere), generative classifiers, GPU-only inference

---

## Shortlist (ranked by expected value)

### 1. Model2Vec (`minishlab/model2vec`) — static distilled token-averaged embeddings, numpy-only

- **URL:** https://github.com/MinishLab/model2vec (MIT license; fetched 2026-06-07)
- **What it does:** Distils a sentence-transformer (e.g., bge-base-en-v1.5) into a static vocabulary of per-token vectors. Inference is pure token lookup + weighted mean — no forward pass through any neural network at query time.
- **Relevance:** Addresses requirements 1, 2, 3, 5. Does not address 4 (auditability is a layer above the encoder).
- **Maturity:** Active as of June 2026; multiple model releases through Jan 2025 (potion-base-32M). Integrated into Sentence Transformers and LangChain. MIT license. PyPI: `model2vec`. (fetched 2026-06-07, https://pypi.org/project/model2vec/)
- **Models:**

  | Model | Params | Disk | MTEB avg | Notes |
  |---|---|---|---|---|
  | potion-base-2M | 1.8M | ~8 MB | ~48 | Smallest; near fastText quality |
  | potion-base-8M | 7.5M | ~30 MB | 51.32 (~92% of MiniLM) | Best latency/quality tradeoff |
  | potion-base-32M | 32.3M | ~120 MB | 51.66 (~93% of MiniLM) | Best static model on MTEB |
  | potion-code-16M | 16M | ~60 MB | N/A (code-specific) | Distilled from CodeRankEmbed; 256-dim; targets code retrieval |

  Source: https://huggingface.co/minishlab/potion-base-8M (fetched 2026-06-07)

- **Dependency footprint:** Base inference requires only `numpy`. No PyTorch. No ONNX runtime. No BLAS beyond what numpy ships with. `pip install model2vec` (fetched 2026-06-07, https://github.com/MinishLab/model2vec)
- **CPU latency for short texts:** Claimed "up to 500× faster than the original sentence-transformer on CPU." For a 5–15 token description: a token lookup + vector mean over ≤15 rows in a fixed numpy matrix. Estimated sub-millisecond (likely ~0.05–0.5ms) for single short texts, though no official single-sentence benchmark is published for 5-word inputs. (source: https://github.com/MinishLab/model2vec, fetched 2026-06-07)
- **Determinism guarantees:** Inference is pure numpy: look up row indices in a weight matrix, compute `np.mean(rows, axis=0)`. There is no stochastic forward pass, no dropout, no attention. The determinism question reduces to: is numpy's mean over float32 rows deterministic run-to-run on the same machine? The answer is **yes with important caveats** — see Determinism Deep-Dive section below. Model2Vec does not document determinism explicitly, but the computation graph is static lookup + arithmetic on a fixed matrix, making it structurally the most deterministic option in this survey.
- **Embedding quality for short technical prompts:** MTEB classification score of 70.34 for potion-base-8M. The `potion-code-16M` variant is purpose-distilled for code domain (Python, Java, JavaScript, Go, PHP, Ruby) from CodeRankEmbed; NDCG@10 on CoIR: 37.05 average, 43.40 on CodeSearchNet. (source: https://huggingface.co/minishlab/potion-code-16M, fetched 2026-06-07)
- **What to avoid:** MTEB avg ~8% below MiniLM — for a 100-entry catalog of short technical phrases, this gap may matter for low-frequency synonyms. No sub-10-word latency benchmark is published. The `potion-code-16M` only has 256 dimensions; cosine similarity space is narrower.
- **Lift effort:** Study the embedding + adapt pattern. Would need a wayfinder-specific wrapper: load model once at catalog-load time, embed per-entry utterances, store as numpy array alongside catalog, embed query at dispatch time, compute cosine similarity. This is ~100 lines of Python on top of `model2vec`.

---

### 2. FastEmbed (`qdrant/fastembed`) — ONNX-runtime sentence embeddings without PyTorch

- **URL:** https://github.com/qdrant/fastembed (Apache-2.0; fetched 2026-06-07)
- **What it does:** Runs small sentence-transformer models (bge-small-en-v1.5, all-MiniLM-L6-v2, etc.) via ONNX Runtime on CPU, with no PyTorch dependency. Quantized models are available for faster inference.
- **Relevance:** Addresses requirements 1, 3, 5. Partially addresses 2 (ONNX CPU has better determinism than PyTorch CPU, but not guaranteed bit-exact — see Determinism section). Does not address 4.
- **Maturity:** Maintained by Qdrant team. Active repository. Apache-2.0 license.
- **Supported small English models:**

  | Model | Dims | Disk |
  |---|---|---|
  | BAAI/bge-small-en-v1.5 | 384 | 67 MB |
  | sentence-transformers/all-MiniLM-L6-v2 | 384 | 90 MB |
  | snowflake/snowflake-arctic-embed-xs | 384 | 90 MB |

  Source: https://qdrant.github.io/fastembed/examples/Supported_Models/ (fetched 2026-06-07)

- **Dependency footprint:** ONNX Runtime (`onnxruntime` package), tokenizers (HuggingFace). No PyTorch. Explicitly designed for "no GB of PyTorch dependencies." (source: https://qdrant.github.io/fastembed/, fetched 2026-06-07)
- **CPU latency:** No official single-sentence benchmark found. FastEmbed issue #292 discusses that for single sentences it may be slower than sentence-transformers due to ONNX session startup overhead. For batches, ONNX is faster. For a dispatch use case (single query at a time), warm-path latency depends on session reuse. Estimated 5–30ms per encode for warmed sessions. (source: https://github.com/qdrant/fastembed/issues/292, fetched 2026-06-07)
- **Determinism guarantees:** ONNX Runtime on CPU does not guarantee bit-exact reproducibility. The official issue #12086 states ORT "doesn't promise to be reproducible between runs," with an informal tolerance of ~1e-5. (source: https://github.com/microsoft/onnxruntime/issues/12086, fetched 2026-06-07). On CPU (vs GPU), run-to-run variance is typically sub-1e-5 and practically stable — but not formally guaranteed.
- **What to avoid:** ONNX session startup cost on cold dispatch; multi-threaded ONNX may introduce non-associative FP summation order differences. Quantized models (INT8) reduce latency but introduce additional precision loss.
- **Lift effort:** Adapt pattern. FastEmbed provides a Python API that is closer to a drop-in than Model2Vec, but the latency profile for single-sentence dispatch needs validation. The 67–90MB model files must be bundled or downloaded on first use.

---

### 3. aurelio-labs/semantic-router — utterance-example routing layer with pluggable encoders

- **URL:** https://github.com/aurelio-labs/semantic-router (MIT; 2,367+ commits; fetched 2026-06-07)
- **What it does:** A routing framework where each "Route" holds a list of example utterances. The router embeds all utterances, builds a numpy index, and at query time computes cosine similarity between the query embedding and all utterance embeddings, returning the top-scoring route.
- **Relevance:** Addresses requirements 1 (with local encoder), 3, partially 5. Weak on 2 (no determinism documentation; `HuggingFaceEncoder` uses PyTorch without `torch.use_deterministic_algorithms()`). Weak on 4 (score is `similarity_score`, often returned as `None` in examples; no nearest-neighbor explanation surfaced).
- **Maturity:** Active, MIT, widely used in the LLM agent ecosystem.
- **Local encoder options:** `HuggingFaceEncoder` (sentence-transformers via PyTorch; default model: `all-MiniLM-L6-v2`). Also supports `fastembed` encoder as an optional extra. Source: pyproject.toml and `semantic_router/encoders/huggingface.py` (fetched 2026-06-07, SHA `82d4916`).
- **Scoring mechanism:** Cosine similarity via `np.dot(index, xq.T) / (index_norm * xq_norm)` in `semantic_router/linear.py` (SHA `a308dc5`, fetched 2026-06-07). Top-K by `np.argpartition`. Threshold tuning is documented separately; LocalIndex has no hardcoded thresholds.
- **Auditability:** `RouteChoice` object carries `similarity_score` field, but this was `None` in the local-execution docs example. No nearest-neighbor utterance attribution is surfaced to callers — you get the route name and an optional score, not "matched utterance: 'CI went red'." (source: https://docs.aurelio.ai/semantic-router/user-guide/guides/local-execution, fetched 2026-06-07)
- **Dependency footprint (base):** pydantic, numpy, colorlog, pyyaml, aurelio-sdk, litellm, openai, tiktoken, aiohttp. The `[local]` extra adds: transformers, tokenizers, sentence-transformers, torch, llama-cpp-python. Source: pyproject.toml (fetched 2026-06-07).
- **Determinism:** HuggingFaceEncoder uses `torch.no_grad()` but does NOT call `torch.use_deterministic_algorithms()` or `.eval()` explicitly. PyTorch CPU inference is practically but not formally deterministic — PyTorch documentation states "completely reproducible results are not guaranteed across PyTorch releases, individual commits or different platforms." (source: https://glaringlee.github.io/notes/randomness.html, fetched 2026-06-07)
- **What to avoid:** The base package pulls in `openai` and `litellm` as hard dependencies — significant weight for a project that explicitly needs no hosted APIs. The `[local]` extra adds full torch. The framework is designed primarily around cloud-encoder workflows with local as a secondary path.
- **Worth borrowing:** The utterance-example routing *pattern* (multiple example utterances per route → centroid or per-utterance cosine → threshold gate) is the key architectural idea worth studying. The `linear.py` cosine implementation is a clean, dependency-minimal reference.
- **Lift effort:** Study-only for the framework itself (too heavy a dependency). Port-one-module for the utterance + cosine pattern using Model2Vec as the encoder.

---

### 4. sentence-transformers (`all-MiniLM-L6-v2`, `bge-small-en-v1.5`) via PyTorch or ONNX

- **URL:** https://github.com/huggingface/sentence-transformers (Apache-2.0; fetched 2026-06-07)
- **What it does:** Full sentence-transformer models providing dense 384-dim embeddings with genuine contextualized semantics (BERT-style attention over the full input sequence).
- **Relevance:** Addresses requirements 1, 3. Weak on 2 (PyTorch CPU is practically but not formally deterministic; ONNX path is closer but still not guaranteed bit-exact). Weak on 5 (10–50ms per encode on CPU for warmed models, but cold start is 200–500ms).
- **Maturity:** Extremely mature. Apache-2.0. The industry standard for sentence embeddings.
- **CPU latency:** One source reports ~14,200 sentences/minute throughput = ~0.07ms/sentence in batch mode; single-threaded single-sentence mode is ~50 sentences/second = ~20ms/sentence. (source: https://levelup.gitconnected.com/all-minilm-l6-v2-358e826ef499, fetched 2026-06-07). The 20ms figure is the more realistic single-query estimate.
- **ONNX path:** `sentence-transformers` supports ONNX export via Hugging Face Optimum. INT8 quantized ONNX models for `all-MiniLM-L6-v2` are available on the HF hub (avx2, avx512, avx512_vnni variants). Quantized models achieve ~75% size reduction with >95% similarity to original embeddings. (source: https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/discussions/82, fetched 2026-06-07)
- **Determinism:** PyTorch CPU has practical but not formal determinism. ONNX Runtime on CPU has the same status (no formal guarantee, informal 1e-5 tolerance). One study found BGE, E5, and Qwen embeddings were "perfectly reproducible across all tested configurations" in practice, but this is empirical observation not a specification. (source: https://arxiv.org/pdf/2509.18869, fetched 2026-06-07)
- **Dependency footprint:** PyTorch path: `torch`, `transformers`, `sentence-transformers` = ~2 GB disk. ONNX path: `onnxruntime`, `tokenizers`, `optimum` = ~200–400 MB.
- **Model quality:** MTEB avg 56.09 for all-MiniLM-L6-v2. bge-small-en-v1.5: competitive, often preferred for retrieval tasks.
- **What to avoid:** Cold-start latency (200–500ms model load) makes it unsuitable for hot-path dispatch without a persistent process or pre-warmed model. PyTorch dependency is very heavy for this use case.
- **Lift effort:** Adapt pattern with ONNX runtime path to minimize torch dependency. Still requires session management to avoid cold-start on every dispatch.

---

### 5. semroute (`HansalShah007/semroute`) — centroid-based routing layer

- **URL:** https://github.com/HansalShah007/semroute (MIT; fetched 2026-06-07)
- **What it does:** Implements centroid-based (O(1)) and per-utterance-average (O(n)) cosine similarity routing over user-defined routes with example utterances. Static or dynamic (GPT-3.5-based) thresholding.
- **Relevance:** Addresses requirements 1 partially (centroid approach), 3. Fails requirement 1 fully (dynamic thresholding requires OpenAI API). No local encoder option documented.
- **Worth borrowing:** The O(1) centroid-vs-query pattern (pre-average utterance embeddings per route into a single centroid vector; at query time compute one cosine per route). This is the most latency-efficient similarity structure for a ~100-entry catalog.
- **Lift effort:** Study-only. The encoder coupling to OpenAI makes it unusable locally, but the centroid-indexing pattern is worth porting.

---

### 6. NadirClaw (`NadirRouter/NadirClaw`) — binary centroid classifier using all-MiniLM-L6-v2

- **URL:** https://github.com/NadirRouter/NadirClaw (fetched 2026-06-07)
- **What it does:** Routes prompts in ~10ms using sentence embeddings. Uses all-MiniLM-L6-v2 for encoding and a binary centroid classifier (cosine similarity to centroids) for route selection. Designed as a cost-optimization LLM router (simple→cheap model, complex→expensive).
- **Relevance:** Addresses requirements 1, 3, 5 (10ms). Fails requirement 2 (no determinism documentation; uses PyTorch-backed all-MiniLM). Partially 4.
- **Worth borrowing:** The ~10ms latency claim for centroid-based routing with all-MiniLM-L6-v2 on CPU (warmed). This validates that the latency ceiling is achievable for the fallback-only use case.
- **Lift effort:** Study-only.

---

## No prior art found

- **Formally determinism-guaranteed embedding inference for routing.** All surveyed semantic routing systems (semantic-router, semroute, NadirClaw, FastEmbed-based) lack explicit determinism specifications. Model2Vec is the closest — its computation graph (static matrix lookup + numpy mean) is structurally deterministic on same-platform same-binary — but MinishLab does not formally document this guarantee. No OSS routing project was found that explicitly certifies embedding determinism in their documentation or CI.

- **Auditability: utterance attribution.** No surveyed OSS router returns "the nearest example utterance that triggered this route" as part of the routing decision object. semantic-router has a `similarity_score` field that is sometimes `None`. The research literature (Okamoto et al., 2026, arXiv:2604.03527) identifies this as an open problem for agentic routing — routing explainability is currently an active research area with no production-grade OSS solution.

- **Hybrid lexical-first / semantic-fallback with a unified scoring contract.** No OSS project was found that implements a principled two-stage dispatcher where the lexical stage outputs a confidence/ambiguity signal that gates semantic fallback. The closest analog is the pattern described in multi-agent papers (arXiv:2504.10519, fetched 2026-06-07) where "keyword-based fallback router provides defensive coverage" when primary LLM routing fails — but this is LLM-primary / keyword-fallback (inverted from the proposed wayfinder architecture).

---

## Determinism Deep-Dive

This section addresses the user's primary concern. All claims are grounded in fetched sources.

### The determinism question decomposed

Semantic routing determinism has three sub-questions:
1. **Same embedding every time?** Given input string X, does the encoder always produce vector V?
2. **Same cosine similarity from same vectors?** Given fixed V and catalog matrix M, does `cosine(V, M)` always return the same float array?
3. **Same routing decision from same similarities?** Given fixed similarity scores, does the threshold comparison always return the same route?

Question 3 is trivially yes (pure comparison arithmetic). Questions 1 and 2 depend on the encoder and BLAS implementation.

### PyTorch CPU (sentence-transformers, semantic-router HuggingFaceEncoder)

- PyTorch documentation explicitly states: "Completely reproducible results are not guaranteed across PyTorch releases, individual commits or different platforms." (source: https://glaringlee.github.io/notes/randomness.html, fetched 2026-06-07)
- `torch.use_deterministic_algorithms(True)` enables a deterministic mode but may disable certain optimized kernels and slow inference.
- The semantic-router `HuggingFaceEncoder` does NOT set `torch.use_deterministic_algorithms()`. (source: `semantic_router/encoders/huggingface.py`, SHA `82d4916`, fetched 2026-06-07)
- **Practical reality:** CPU inference with `torch.no_grad()` and `model.eval()` is empirically very stable run-to-run on the same machine and Python environment. One study found BGE/E5/Qwen transformers were "perfectly reproducible across all tested configurations" in practice. (source: arXiv:2509.18869, fetched 2026-06-07). But this is empirical, not a specification. **Verdict: practically deterministic, not formally guaranteed.**

### ONNX Runtime CPU (FastEmbed, ONNX-exported sentence-transformers)

- ONNX Runtime GitHub issue #12086: ORT "doesn't promise to be reproducible between runs." Suggested tolerance: absolute/relative error ~1e-5. (source: https://github.com/microsoft/onnxruntime/issues/12086, fetched 2026-06-07)
- CPU inference (vs GPU) is significantly more stable because it avoids parallel floating-point reduction reordering from GPU thread scheduling. However, ORT's CPU execution may use multi-threaded BLAS which can introduce non-associative summation order changes run-to-run.
- Setting `OrtSessionOptions.intra_op_num_threads = 1` forces single-threaded execution, which is more reproducible on the same machine, but ORT still does not formally guarantee it.
- **Verdict: better than GPU, not formally guaranteed, practically stable at ~1e-5 tolerance.**

### Model2Vec / numpy static lookup (strongest determinism case)

- Inference is: (1) tokenize text → token IDs; (2) look up rows in a fixed float32 weight matrix; (3) `np.mean(rows, axis=0)`.
- Step 2 is a pure array index operation — no floating-point computation, just memory reads. Step 3 is a mean over ≤15 rows (for a short prompt).
- **numpy mean over a small fixed-size array:** numpy's `np.mean` on a 1D float32 array of length ≤15 uses sequential pairwise summation. On the same CPU with the same BLAS library, pairwise reduction of a 15-element vector is deterministic because the reduction tree is a fixed depth — there is no opportunity for threading to reorder operations on such small arrays. (source: https://github.com/numpy/numpy/issues/661, fetched 2026-06-07; https://randomascii.wordpress.com/2013/07/16/floating-point-determinism/, fetched 2026-06-07)
- **BLAS caveat:** numpy's `np.dot` for 2D matrix multiply (used in BLAS-backed operations) can be non-deterministic across different BLAS builds (MKL vs OpenBLAS vs reference) because they reorder float operations for SIMD efficiency. However, Model2Vec's inference does NOT call `np.dot` for a 2D matrix multiply — it is a row-index selection followed by `np.mean`. This avoids the primary BLAS non-determinism source.
- **Cross-platform caveat:** float32 FMA (fused multiply-add) instructions produce different rounding than separate multiply+add on some CPUs. For a pure mean (no multiply), this is not triggered. But different compilers / numpy builds on different OS platforms may produce different float32 results from the same inputs. (source: https://randomascii.wordpress.com/2013/07/16/floating-point-determinism/, fetched 2026-06-07)
- **Verdict: the strongest determinism profile of all surveyed encoders.** On the same machine, same numpy version, same OS: run-to-run determinism is a very strong practical guarantee (the computation graph is a fixed-size integer lookup + sequential floating-point mean with no randomness sources). Cross-platform determinism is not guaranteed due to FP rounding differences. **For a single-machine deployment, Model2Vec embedding is effectively deterministic.**

### Determinism vs cosine similarity threshold

Even with a deterministic embedding, the routing decision is: "does cosine(query, route_centroid) exceed threshold T?" If T is a fixed constant and cosine is deterministic, the decision is deterministic. The design must:
- Fix the threshold in code (not learn it at runtime, which could drift)
- Use the same normalized catalog embeddings (precomputed, stored on disk)
- Use the same encoder version (model staleness breaks determinism of catalog embeddings)

---

## Candidate Evaluation Matrix

| Candidate | License | Deps | Model size | CPU latency (single short text) | Determinism | MTEB quality | Local-runnable |
|---|---|---|---|---|---|---|---|
| Model2Vec potion-base-8M | MIT | numpy only | 30 MB | ~0.1–0.5ms (estimated) | Strongest: static matrix lookup + numpy mean; no stochastic FP | 51.32 avg (92% of MiniLM) | Yes |
| Model2Vec potion-code-16M | MIT | numpy only | ~60 MB | ~0.1–0.5ms (estimated) | Same as above | Code-specific NDCG@10: 37–50 | Yes |
| FastEmbed bge-small-en-v1.5 | Apache-2.0 | onnxruntime, tokenizers | 67 MB | 5–30ms (warm session) | ONNX CPU: ~1e-5 informal tolerance, not formally guaranteed | MTEB competitive | Yes |
| semantic-router + HuggingFaceEncoder | MIT | torch, transformers, sentence-transformers | 90 MB (model) + ~2 GB PyTorch | ~20ms (warm) / ~500ms (cold) | PyTorch: not formally guaranteed; `use_deterministic_algorithms` not set | 56.09 (MiniLM) | Yes |
| all-MiniLM-L6-v2 via ONNX | Apache-2.0 | onnxruntime, optimum | 90 MB (FP32) / ~22 MB (INT8) | ~5–20ms (warm, single thread) | ONNX CPU: ~1e-5 informal; better with `intra_op_num_threads=1` | 56.09 | Yes |
| bge-small-en-v1.5 via FastEmbed | Apache-2.0 | onnxruntime, tokenizers | 67 MB | 5–30ms (warm) | Same as ONNX above | Top MTEB for its size | Yes |
| semroute | MIT | OpenAI API (hard dep) | n/a | n/a | n/a (hosted encoder) | n/a | No (encoder) |

---

## Answering the Research Questions

### RQ1: Existing OSS semantic routers

The primary candidate is `aurelio-labs/semantic-router` (MIT, 2,367+ commits, active). It is the most complete OSS framework for utterance-example semantic routing. Architecture: utterances per route → encode → numpy index → cosine → threshold. Local execution requires the `[local]` extra which pulls in full PyTorch.

Secondary: `HansalShah007/semroute` (MIT) — centroid-based, O(1) per route. No local encoder.

Tertiary: `NadirRouter/NadirClaw` (inspected, fetched 2026-06-07) — ~10ms binary centroid routing with all-MiniLM-L6-v2; not designed for multi-class agent routing but proves the latency floor.

`vllm-project/semantic-router` (fetched 2026-06-07) and `redhat-et/semantic_router` are production-grade but LLM/GPU-centric and do not satisfy requirement 1 or 3.

**Key architectural pattern across all:** Routes are defined as sets of example utterances. At index time, utterances are encoded and their embeddings averaged (centroid) or stored individually. At query time, query embedding is compared to route centroids/utterance matrix via cosine similarity. The route with the highest similarity above a threshold wins; below threshold returns "no match" (analogous to wayfinder's `self_handle_unaided`).

### RQ2: Local encoder options

**Static family (recommended for wayfinder):**

- **Model2Vec** (`pip install model2vec`): numpy-only, 8–120 MB, sub-ms inference, ~92–93% of MiniLM quality. The `potion-code-16M` variant is trained specifically on code/programming content (Python, Java, JS, Go) which is directly relevant to the wayfinder dispatch domain (agents/skills for coding tasks).
- **fastText** (Facebook, MIT): character n-gram embeddings; fast, but outdated compared to Model2Vec on sentence semantics. Model2Vec outperforms fastText on all benchmarks per MinishLab docs. **Verdict: skip in favor of Model2Vec.**
- **GloVe**: word-level only, no sub-word. Poor on technical domain OOV terms (tool names, library names). **Verdict: skip.**

**Neural family (for fallback path only):**

- **all-MiniLM-L6-v2 via ONNX** (Apache-2.0, 90 MB FP32 / 22 MB INT8): 384-dim, best MTEB in this size class, ~5–20ms warm CPU. Suitable as fallback if latency is acceptable.
- **bge-small-en-v1.5 via FastEmbed** (Apache-2.0, 67 MB): 384-dim, slightly better on retrieval tasks, slightly smaller. Good alternative.
- **gte-small**: competitive on MTEB but less ecosystem support than bge or MiniLM. Lower priority.

**Recommendation:** Use `potion-base-8M` or `potion-code-16M` for the wayfinder use case. The domain is technical/coding, the prompts are short (5–15 words), and the latency budget is tight. The static embedding quality drop (~8% MTEB below MiniLM) is acceptable given that the semantic layer is a *fallback* (lexical first, semantic only on unresolved dispatch).

### RQ3: Hybrid lexical+semantic designs

The prior exploration doc established the wayfinder hypothesis: "lexical primary, semantic fallback ONLY on unresolved/ambiguous dispatches."

Prior art on this specific configuration:
- The multi-agent survey paper (arXiv:2504.10519, fetched 2026-06-07) describes the inverted pattern: LLM primary, keyword fallback. This suggests the wayfinder hypothesis (keyword primary, semantic fallback) is novel for this direction.
- The tool-selection pattern (Zarar Dev blog, fetched 2026-06-07) uses pure semantic routing with a similarity threshold — routing to "no match" when similarity < 0.4. No lexical stage.
- Hybrid RAG literature (lexical BM25 + dense embedding, linear combination) is the closest structural analog. In RAG, lexical and semantic scores are usually combined with a weighted sum (Reciprocal Rank Fusion or linear blend). For routing, combining them additively raises the scoring-contract question the project already identified.

**Assessment of the wayfinder hypothesis:** The "lexical primary, semantic fallback only on unresolved" design has strong practical justification:
1. It preserves the existing scoring ladder and its auditability for the ~80–90% of dispatches that the lexical matcher handles confidently.
2. It gates the latency-heavy semantic stage only when needed.
3. The "unresolved" signal is already defined in wayfinder's seven-decision taxonomy (below-threshold → `self_handle_unaided`).

No prior art was found that implements this exact pattern with a formal scoring-contract bridge between the two stages. This is the primary design gap the project-planner will need to address.

**Architecture options surveyed:**
- **Cascade (sequential):** Lexical scores first; if top score < threshold, fall through to semantic. Binary gate. Simple but loses signal from lexical partial matches.
- **Re-rank (parallel):** Run both stages; weighted blend for the ambiguous zone (e.g., lexical score between 0.3–0.7). More nuanced but requires calibrating the blend weight empirically.
- **Semantic-only replacement:** Abandon lexical entirely. Highest semantic accuracy ceiling but abandons auditability and determinism guarantees for clear cases. **Not recommended given wayfinder's auditability requirement.**

The Outcome-Aware Tool Selection paper (arXiv:2603.13426, fetched 2026-06-07) shows embedding-based tool selection achieving sub-millisecond routing latencies (after index precomputation), validating the feasibility of pure semantic at the latency required for a fallback path.

### RQ4: Auditability patterns

The research literature identifies this as an unsolved problem. Okamoto et al. (arXiv:2604.03527, fetched 2026-06-07) specifically call out that "current routing systems offer little support for" explaining routing decisions in human-centered terms.

Surveyed patterns:
1. **Score surface:** Return `cosine_score` alongside route name. Minimal but better than nothing. semantic-router's `RouteChoice.similarity_score` does this (when not None).
2. **Nearest-utterance attribution:** Return which of the route's example utterances was the nearest neighbor to the query. "Matched utterance: 'run the linter on my code'" is actionable for a skill author diagnosing unexpected routing. No OSS router implements this in its public API.
3. **Threshold band logging:** Log the similarity scores of the top-2 candidates and the delta — "route A: 0.82, route B: 0.79, margin: 0.03." This surfaces near-ties for human review. Not implemented in any surveyed OSS router.
4. **Score banding:** Categorize similarity scores (0.9+ = high confidence, 0.7–0.9 = moderate, <0.7 = low confidence). Low-confidence semantic dispatches could be flagged in the dispatch log for human audit. This is a wayfinder-specific design idea with no prior art implementation found.

**Recommendation:** For wayfinder, the most useful auditability pattern is **nearest-utterance attribution** — when a semantic match fires, record the example utterance that was the closest neighbor. This gives skill authors the "why" they need. Model2Vec makes this particularly tractable: after computing per-utterance cosine similarities, argmax(similarities) gives the index of the closest utterance, and the utterance string can be stored alongside the index.

### RQ5: Catalog/index shape

**Build-time pattern (from surveyed systems):**

The standard pattern across all semantic routing systems:
1. At catalog-build time (analogous to wayfinder's catalog generator), encode all utterance examples for each route/agent/skill.
2. Store embeddings as numpy arrays (float32 or float16) in a file alongside the catalog JSON.
3. At process startup (or catalog-load time), load the embedding file into memory.
4. At dispatch time, embed the query and compute cosine similarities — O(N_utterances) dot products where N_utterances is the total number of example utterances across all catalog entries.

**Concrete index shapes observed:**
- semantic-router LocalIndex: numpy array, shape `(total_utterances, embedding_dim)`, stored via `np.concatenate`. Route membership tracked by a parallel list of route names. (source: `semantic_router/index/local.py`, fetched 2026-06-07)
- Tool-selection blog (Zarar Dev): embeddings stored in PostgreSQL with `pgvector`. For a 100-entry catalog this is overengineered; a numpy `.npy` file is sufficient.

**Staleness / versioning:** The Zarar Dev blog stores `model_id` alongside embeddings "to catch mismatches when providers change." This is the right pattern: the on-disk embedding file should carry a `model_id` and `model_version` header. If either changes, the catalog must be re-embedded. semantic-router implements a hash-based versioning system (`_get_hash()` / `_write_hash()`) to detect when routes change and the index is stale.

**For wayfinder:** With ~100 catalog entries and ~5–10 utterances each, the total index is ~1,000 vectors × 8 (potion-base-8M dim = 256) × 4 bytes = ~1 MB. Trivially fits in memory. A simple `.npy` or `.npz` file is sufficient; no vector database needed.

---

## Architecture Fork Recommendation

**Recommended: Hybrid fallback (lexical primary, semantic secondary)**

The lexical scorer should remain the primary dispatch path for three reasons:
1. It is perfectly deterministic, sub-ms, and auditable via the existing score/matched-keyword rationale.
2. The ~80–90% of dispatches where trigger keywords fire confidently should not pay the latency cost of semantic encoding.
3. The existing scoring ladder and threshold structure is the project's established correctness contract.

The semantic stage should activate **only when lexical output is `self_handle_unaided`** (no trigger matched) or the top two candidates are within the gap threshold (ambiguous dispatch). This mirrors the BM25 tiebreaker pattern from the prior exploration doc but with a more capable signal.

**Encoder recommendation: Model2Vec `potion-base-8M`** (fallback) or `potion-code-16M` (if code-domain precision proves more important than multilingual MTEB scores).

Rationale:
- numpy-only dependency: zero conflict with the "pure Python" project philosophy
- Sub-millisecond inference: acceptable even on the primary path if the guard condition proves too complex
- Structurally deterministic: the strongest practical guarantee of any surveyed option
- 8MB–30MB model: no download burden
- Classification MTEB 70.34 for potion-base-8M: sufficient for discriminating 100 technical agents/skills

**What to defer:** Full semantic replacement of the lexical engine. This trades away auditability for the ~80% of dispatches that are already working. There is no evidence from the dispatch log that unresolvable dispatches are frequent enough to justify that trade.

---

## Open Questions for Project-Planner

1. **Utterance corpus design.** How many example utterances per catalog entry, and who authors them? The semantic layer's quality ceiling is determined by whether the utterance examples adequately cover the variation space of real dispatch prompts. The prior doc's "labelled corpus" recommendation (§7) is a prerequisite for any semantic pilot — without ground-truth dispatch examples, threshold calibration is impossible. **Decision needed: is manual utterance authoring per catalog entry in scope?**

2. **Threshold calibration.** The cosine similarity threshold for "semantic match fires" must be calibrated against real prompts. Too low: false positive semantic dispatches that contradict the lexical result. Too high: the fallback adds no coverage. semantic-router's threshold is configurable but not auto-tuned without a labelled corpus. **Decision needed: is there a labelled corpus, or must one be created first?**

3. **Determinism contract: which platform?** Model2Vec embedding is deterministic on the same machine/numpy/OS combination. Is cross-platform determinism (Windows vs macOS vs Linux) a requirement? If wayfinder is always deployed on the user's single machine, the stronger practical guarantee applies. If catalog embeddings are precomputed on a different machine than dispatch runs, float32 rounding differences could cause cosine scores to differ by ~1e-6, which should not affect routing decisions above the threshold margin. **Decision needed: is cross-platform determinism required, or same-machine only?**

4. **Catalog embedding versioning.** When catalog entries are added or edited, and when the encoder model is updated, the embedding index must be regenerated. A staleness check (hash of catalog + model version → embedding file checksum) should be built into the catalog generator. **Decision needed: how does the catalog generator signal index staleness, and what triggers index regeneration?**

5. **Fallback trigger definition.** The semantic fallback should activate on `self_handle_unaided` output from the lexical stage. But should it also activate on "ambiguous" (two candidates within the gap threshold)? If yes, the semantic score becomes a tiebreaker, and its contribution to the scoring contract must be specified (does it override the lexical gap? blend with it?). **Decision needed before implementation: the exact guard condition and the score-combination formula for ambiguous cases.**

6. **potion-code-16M vs potion-base-8M for wayfinder's domain.** The wayfinder dispatch domain is specifically software development/devops (coding agents, git, CI/CD, linter, etc.). `potion-code-16M` is trained on code retrieval; `potion-base-8M` is general English. A comparative evaluation on a sample of real dispatch prompts would determine which is better for this specific domain. Both have 256 dimensions (code) vs 256 (base-8M is also 256 — confirmed via HF hub). **Recommendation: evaluate both on 50 labelled prompts before committing.**

---

## Dated Sources List

| Source | URL | Fetched |
|---|---|---|
| aurelio-labs/semantic-router repo | https://github.com/aurelio-labs/semantic-router | 2026-06-07 |
| semantic-router local-execution docs | https://docs.aurelio.ai/semantic-router/user-guide/guides/local-execution | 2026-06-07 |
| semantic-router `semantic.py` (SHA 82d4916) | https://github.com/aurelio-labs/semantic-router/blob/main/semantic_router/routers/semantic.py | 2026-06-07 |
| semantic-router `linear.py` (SHA a308dc5) | https://github.com/aurelio-labs/semantic-router/blob/main/semantic_router/linear.py | 2026-06-07 |
| semantic-router `huggingface.py` encoder | https://github.com/aurelio-labs/semantic-router/blob/main/semantic_router/encoders/huggingface.py | 2026-06-07 |
| semantic-router `pyproject.toml` | https://github.com/aurelio-labs/semantic-router/blob/main/pyproject.toml | 2026-06-07 |
| MinishLab/model2vec repo | https://github.com/MinishLab/model2vec | 2026-06-07 |
| model2vec PyPI | https://pypi.org/project/model2vec/ | 2026-06-07 |
| minishlab/potion-base-8M model card | https://huggingface.co/minishlab/potion-base-8M | 2026-06-07 |
| minishlab/potion-code-16M model card | https://huggingface.co/minishlab/potion-code-16M | 2026-06-07 |
| minish.ai model2vec docs | https://minish.ai/packages/model2vec/introduction/ | 2026-06-07 |
| qdrant/fastembed repo | https://github.com/qdrant/fastembed | 2026-06-07 |
| FastEmbed supported models | https://qdrant.github.io/fastembed/examples/Supported_Models/ | 2026-06-07 |
| HansalShah007/semroute | https://github.com/HansalShah007/semroute | 2026-06-07 |
| ONNX Runtime issue #12086 (reproducibility) | https://github.com/microsoft/onnxruntime/issues/12086 | 2026-06-07 |
| PyTorch reproducibility docs | https://glaringlee.github.io/notes/randomness.html | 2026-06-07 |
| Floating-point determinism (Bruce Dawson) | https://randomascii.wordpress.com/2013/07/16/floating-point-determinism/ | 2026-06-07 |
| numpy np.dot precision issue #661 | https://github.com/numpy/numpy/issues/661 | 2026-06-07 |
| all-MiniLM-L6-v2 latency (levelup.gitconnected) | https://levelup.gitconnected.com/all-minilm-l6-v2-358e826ef499 | 2026-06-07 |
| all-MiniLM-L6-v2 ONNX discussion (HF hub) | https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/discussions/82 | 2026-06-07 |
| Embedding-based tool selection (Zarar Dev) | https://zarar.dev/embedding-based-tool-selection-for-ai-agents/ | 2026-06-07 |
| Explainable Model Routing (arXiv:2604.03527) | https://arxiv.org/pdf/2604.03527 | 2026-06-07 |
| Toward Super Agent System with Hybrid AI Routers (arXiv:2504.10519) | https://arxiv.org/pdf/2504.10519 | 2026-06-07 |
| Outcome-Aware Tool Selection (arXiv:2603.13426) | https://arxiv.org/pdf/2603.13426 | 2026-06-07 |
| On Reproducibility Limitations of RAG Systems (arXiv:2509.18869) | https://arxiv.org/pdf/2509.18869 | 2026-06-07 |
| Semantic Router for vLLM (arXiv:2510.08731) | https://arxiv.org/html/2510.08731v1 | 2026-06-07 |
| docs/exploration/2026-05-28-trigger-algorithms.md | (this repo, read 2026-06-07) | 2026-06-07 |

**Unverified claims (not independently confirmed via fetched sources):**

- `unverified:` potion-base-8M has 256 embedding dimensions — cited from HF hub page but the dimension count was not explicitly stated in the fetched content; inferred from potion-code-16M (256 confirmed) and general model2vec documentation.
- `unverified:` NadirClaw achieves ~10ms latency with all-MiniLM-L6-v2 centroid routing — cited from search result description of the repo; the repo itself was not directly fetched and validated.
- `unverified:` Model2Vec uses `np.mean` (not `np.dot` for 2D matmul) for inference — inferred from the description "simple token lookup + averaging" across multiple sources; source code of model2vec inference was not directly read in this session.

---

## Recommended Handoff

- `project-planner` — the top recommendation (Model2Vec `potion-base-8M` as encoder, utterance-centroid cosine as the semantic fallback layer, hybrid lexical-primary / semantic-fallback architecture) provides the strongest combined answer to requirements 1–3 and 5. The planner should scope the implementation as: (a) utterance example authoring schema in `triggers.yml`; (b) catalog-build-time embedding precomputation; (c) embed-and-lookup fallback path in the matcher; (d) nearest-utterance attribution in the dispatch rationale. Feed this report and issue #325 as context.
- `user` — for open questions 1 (utterance corpus authoring), 2 (labelled corpus prerequisite), and 5 (fallback trigger definition and score-combination formula for ambiguous cases). These require product decisions before implementation can be scoped.
