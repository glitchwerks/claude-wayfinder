# claude-wayfinder

> A typed, auditable dispatch matcher for Claude Code — post-cognitive routing with a deterministic-first scoring kernel.

## What this is — and why it matters

A conventional LLM router enforces routing policy through prose instructions read at every decision point. In practice, this means the routing decision is made by the same model that's about to do the work — using the same signal that drove the original request. It drifts silently, makes the same decision differently across turns, and leaves no structured artifact you can inspect or replay.

`claude-wayfinder` replaces that loop for the mechanical cases — the ones that don't need judgment. It scores agents and skills against a structured task description composed by the router agent, not the raw user prompt, and returns one of seven typed decisions with confidence, rationale, and alternatives.

**Three properties that matter:**

- **Auditable.** Every dispatch decision is a structured artifact. Given the same context and catalog, the matcher returns the same answer. You can replay any past decision.
- **Post-cognitive.** The matcher fires after the router agent has read the conversation and extracted intent, file paths, and tools. Raw prompts are signal-poor; the router's interpretation is richer — same model, more signal.
- **Auto-generated catalog.** Built from skill sidecars and agent frontmatter at session start. No hand-curated rule config to drift out of sync.

The decision contract is a seven-member typed enum: `delegate` / `self_handle` / `self_handle_unaided` / `advisory` / `ambiguous` / `ask_user` / `needs_more_detail`.

For the design rationale, see [`docs/design.md`](docs/design.md). For the algorithm specification, see [`docs/schema.md`](docs/schema.md).

## Install (Claude Code users)

**Requires Python >= 3.11 on `$PATH`.**

Inside Claude Code, run these two commands:

```
/plugin marketplace add glitchwerks/claude-wayfinder
/plugin install claude-wayfinder@claude-wayfinder
```

## How to use it

### In Claude Code: `/dispatch`

v0.1 is a **try-before-integrate** primitive. The `/dispatch` skill runs the matcher against bundled demo fixtures so you can see all seven decision branches in action — it does not intercept your session traffic or route your tasks automatically. After running it, you decide whether to wire the matcher into your own router agent. Automatic routing is deferred to v0.2 ([#6](https://github.com/glitchwerks/claude-wayfinder/issues/6)).

When invoked, the skill runs the matcher against the bundled demo catalog and returns all seven decision branches with inputs, decisions, confidence scores, and rationale. A single decision block looks like this:

```
[1/7] Branch: delegate
  input       : 'implement the authentication module'
  file_paths  : ['src/auth.py']
  decision    : delegate
  confidence  : 0.9000
  agent       : code-writer
  rationale   : code-writer matched on keyword 'implement' (weight 1.0) and
                path glob '**/*.py' (weight 0.5). Gap to next agent: 0.45.
  skills      : ['python']
```

**How you know it's working:** the decision field contains one of the seven typed strings; confidence is a float between 0 and 1; rationale names the specific triggers that fired. An `ask_user` result is valid in the contract but reserved in v0.1 — the matcher will not produce it against real input.

### Difference between `/dispatch` and `python -m claude_wayfinder demo`

| | `/dispatch` (in Claude Code) | `python -m claude_wayfinder demo` (CLI) |
|---|---|---|
| Catalog | Bundled demo fixtures | Bundled demo fixtures |
| Invocation | Skill triggered by router agent | Direct CLI invocation |
| Use case | Evaluate the matcher inside your Claude Code session | Evaluate the matcher without installing Claude Code |
| Output | Same seven-decision output | Same seven-decision output |

Both run the same matcher against the same bundled fixtures. The CLI path is the faster evaluation route if you are deciding whether to install the plugin.

### What's next

If you want to use the matcher for real routing in your own Claude Code setup, there are two paths:

- **Integrate now via the contributor path.** Clone the repo, build your own catalog from your agent and skill frontmatter, and call the library API from your router agent. The [Contributing](#contributing) section covers the mechanics, and `python -m claude_wayfinder --help` documents the CLI surface.
- **Wait for the bundled runtime.** Issue [#6](https://github.com/glitchwerks/claude-wayfinder/issues/6) tracks the zero-friction-install spike — a bundled router agent and catalog generator that would make daily-driver routing available without manual integration. That work is scoped to v0.2.

## Quick start

Minimum path from zero to a working real-catalog `/dispatch`:

**1. Install the plugin** — inside Claude Code:

```
/plugin marketplace add glitchwerks/claude-wayfinder
```

**2. Build a catalog** — run this console script once (and again whenever your skill or agent frontmatter changes):

```bash
claude-wayfinder catalog build \
  --skills-dir ~/.claude/skills \
  --agents-dir ~/.claude/agents \
  --out ~/.claude/dispatch-catalog.json \
  --log ~/.claude/dispatch-catalog-build.log
```

**3. Set `$DISPATCH_CATALOG_PATH`** — in your shell profile:

```bash
export DISPATCH_CATALOG_PATH=~/.claude/dispatch-catalog.json
```

Without this env var, `/dispatch` runs in demo mode against bundled fixtures and does not route your tasks.

**4. Add `Skill` to your router agent's `tools:` frontmatter** — `/dispatch` is invoked as a skill, so the router must have `Skill` in its tool list. See [`docs/integration.md`](docs/integration.md) for the full router-agent prompt snippet and catalog refresh patterns.

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
