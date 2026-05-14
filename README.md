# claude-wayfinder

> **A typed, auditable dispatch matcher for Claude Code — post-cognitive routing with a deterministic-first scoring kernel.**

> 🚧 **Status: pre-release scaffold.** This repo is being assembled from a private working harness. The matcher, catalog generator, and CLI demo are not yet present. Subscribe to releases for v0.1.0.

## What this is

`claude-wayfinder` is a routing primitive for Claude Code, extracted from a working production harness. It scores agents and skills against a structured task description composed by the router agent — not the raw user prompt — and returns one of seven typed decisions with confidence and rationale.

**Key design points:**

- **Post-cognitive matching.** Operates after the router agent has read the conversation and composed a structured task description. Raw user prompts are signal-poor; richer signal comes from interpretation.
- **Typed decision contract.** Seven-decision enum (`delegate` / `self_handle` / `self_handle_unaided` / `advisory` / `ambiguous` / `ask_user` / `needs_more_detail`) with structured rationale, confidence, and alternatives.
- **Auto-generated catalog.** Built from skill sidecars and agent frontmatter — no hand-curated rule config to drift out of sync.
- **Drift telemetry.** Routing-decision quality is observable over time.

> _Note on "deterministic":_ The matching kernel is deterministic — same input, same decision. The pipeline around it is not — the router agent (LLM) composes the input, and consumers (LLM) act on the output. `claude-wayfinder` does not claim end-to-end determinism.

## Quickstart

> The CLI demo is not yet shipped. Once published, the quickstart will be:
>
> ```bash
> git clone https://github.com/glitchwerks/claude-wayfinder
> cd claude-wayfinder
> python -m claude_wayfinder demo
> ```
>
> No Claude Code install needed to evaluate the matcher.

## API

The public API — `load_catalog`, `build_features`, `score`, `decide`, and the supporting dataclasses — is documented in [`docs/api.md`](docs/api.md). That document covers the full `__all__` surface, the stability promise for v0.1, and which submodule imports are not covered.

## Why not just a hook?

> Full answer to be expanded in a later sub-task. Short version: a `UserPromptSubmit` hook matches on raw prompt text, which is signal-poor. `claude-wayfinder` matches on a router-composed task description after the agent has interpreted user intent — strictly more signal at a small token cost.
>
> See prior art [`wwadley-lucas/claude-dispatch`](https://github.com/wwadley-lucas/claude-dispatch) for the hook-based pre-cognitive design point.

## Prior art

- [`wwadley-lucas/claude-dispatch`](https://github.com/wwadley-lucas/claude-dispatch) — pioneered hook-based pre-cognitive matching with zero-LLM-in-default-path principles. Operates at a different lifecycle point.
- [`darco81/skills-radar`](https://github.com/darco81/skills-radar) — lazy skill loading via embedding retrieval (BM25 + dense). Adjacent problem space, different mechanism.
- [Anthropic Tool Search Tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool) — upstream pattern for the MCP-tools case.

## License

MIT — see [`LICENSE`](./LICENSE).
