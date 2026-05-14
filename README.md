# claude-wayfinder

> A typed, auditable dispatch matcher for Claude Code — post-cognitive routing with a deterministic-first scoring kernel.

## What this is

`claude-wayfinder` is a routing primitive for Claude Code, extracted from a working production harness. It scores agents and skills against a structured task description composed by the router agent — not the raw user prompt — and returns one of seven typed decisions with confidence and rationale.

**Key design points:**

- **Post-cognitive matching.** Operates after the router agent has read the conversation and composed a structured task description. Raw user prompts are signal-poor; richer signal comes from interpretation.
- **Typed decision contract.** Seven-decision enum (`delegate` / `self_handle` / `self_handle_unaided` / `advisory` / `ambiguous` / `ask_user` / `needs_more_detail`) with structured rationale, confidence, and alternatives.
- **Auto-generated catalog.** Built from skill sidecars and agent frontmatter — no hand-curated rule config to drift out of sync.

For the algorithm specification, see [`docs/design/2026-04-30-deterministic-first-router-design-v5.md`](docs/design/2026-04-30-deterministic-first-router-design-v5.md).

## Install (Claude Code users)

**Requires Python >= 3.11 on `$PATH`.**

Inside Claude Code, run these two commands:

```
/plugin marketplace add glitchwerks/claude-wayfinder
/plugin install claude-wayfinder@claude-wayfinder
```

Once installed, the `dispatch` skill is available. Run `/dispatch` inside Claude Code to exercise the matcher against bundled demo fixtures and see all seven decision branches in action. See [`skills/dispatch/SKILL.md`](skills/dispatch/SKILL.md) for what the skill does and what output to expect.

## Try it (no Claude Code required)

The CLI demo evaluates the matcher against bundled fixtures without requiring a Claude Code install. It covers all seven decision branches.

```bash
python -m claude_wayfinder demo
```

Expected output (truncated — seven decision blocks):

```
[1/7] Branch: delegate
  input       : 'implement the authentication module'
  file_paths  : ['src/auth.py']
  decision    : delegate
  confidence  : 0.9000
  agent       : code-writer

[2/7] Branch: self_handle
  ...

[6/7] Branch: ask_user
  decision    : ask_user
  rationale   : Reserved — not produced by the v0.1 matcher. ask_user is
                part of the 7-decision contract and reserved for future
                clarification flows.

[7/7] Branch: needs_more_detail
  ...
```

`ask_user` is a valid member of `VALID_DECISIONS` but is reserved in v0.1 — the matcher never produces it.

## Library API

The public API is documented in [`docs/api.md`](docs/api.md). A minimal integration looks like:

```python
from pathlib import Path
from claude_wayfinder import load_catalog, build_features, score, decide, ScoredEntry

catalog = load_catalog(Path("/path/to/dispatch-catalog.json"))
features = build_features({
    "task_description": "implement the login page",
    "file_paths": ["src/auth/login.py"],
})

agents = [ScoredEntry(e, score(e, features)) for e in catalog if e.kind == "agent" and e.routable]
skills = [ScoredEntry(e, score(e, features)) for e in catalog if e.kind == "skill"]

result = decide(agents, skills, features, catalog)
# result["decision"] is one of the seven decision strings
```

The `__all__`-guarded surface (`load_catalog`, `build_features`, `score`, `decide`, `VALID_DECISIONS`, and the supporting dataclasses) is stable for the v0.1 series: patch releases will not rename, remove, or alter any public signature.

## Distribution model

**v0.1 ships as a sideloadable Claude Code plugin.** There is no PyPI wheel. The "release" is a git tag and a GitHub Release page carrying the changelog; plugin users sideload from the git ref, contributors clone the repo. PyPI is deferred until a consumer with a real need surfaces.

**v0.1 does not meet the original "marketplace install → works without secondary install" bar.** The plugin sideload path requires Python >= 3.11 on `$PATH` before the demonstration skill can call the matcher. This gap is named, not finessed: Anthropic does not expose Claude Code's bundled runtime to skill subprocesses (see [`anthropics/claude-code#30465`](https://github.com/anthropics/claude-code/issues/30465)), and no marketplace plugin bundles its own runtime today. Zero-friction install is scoped for v0.2 via spike #6.

**No observability surface in v0.1.** The health-reporting module (`_health.py`) is internal and carries no stability promise. Routing-decision observability is deferred until a concrete consumer need is identified.

## Prior art

- [`wwadley-lucas/claude-dispatch`](https://github.com/wwadley-lucas/claude-dispatch) — pioneered hook-based pre-cognitive matching with zero-LLM-in-default-path principles. Operates at a different lifecycle point (raw user prompt, not router-composed task description).
- [`darco81/skills-radar`](https://github.com/darco81/skills-radar) — lazy skill loading via embedding retrieval (BM25 + dense). Adjacent problem space, different mechanism.
- [Anthropic Tool Search Tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool) — upstream pattern for the MCP-tools case.

## Contributing

**Requirements:** Python >= 3.11, [`uv`](https://github.com/astral-sh/uv).

```bash
git clone https://github.com/glitchwerks/claude-wayfinder.git
cd claude-wayfinder
uv venv .venv
uv pip install -e ".[dev]"
```

Run the test suite:

```bash
python -m pytest
```

Run the linter:

```bash
python -m ruff check src/ tests/
```

Run the demo (confirms the matcher works end-to-end against bundled fixtures):

```bash
python -m claude_wayfinder demo
```

**Filing issues:** Use [GitHub Issues](https://github.com/glitchwerks/claude-wayfinder/issues). Before opening a new issue, check that one does not already exist for the same problem.

**Workflow:** Create a branch per issue, open a PR that references the issue number in its body (`Closes #N`). For non-trivial work, set up a git worktree per branch (see the CLAUDE.md contributor notes for the worktree convention used in this repo).

## License

MIT — see [`LICENSE`](./LICENSE).
