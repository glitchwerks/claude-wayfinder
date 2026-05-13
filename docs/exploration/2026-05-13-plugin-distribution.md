# Plugin distribution research — v0.1 kernel language decision

> **Status:** Research spike for issue #5. Read-only exploration; no decision committed.
> **Author:** `claude-code-guide` sub-agent dispatch (2026-05-13), written to disk by router.
> **Router note (read first):** Section "Decision-driving comparison" claims Python and plain-JS TypeScript both reach a **zero** friction floor. The TS-zero claim is grounded only on "Claude Code's Node.js is built-in" — but the bundled Node serves *hooks* (which load as modules into Claude Code's own runtime). Whether a skill that subprocess-calls `node script.js` actually leverages the bundled Node, or requires the user to have a separate Node on `$PATH`, is **not** demonstrated in the cited sources. See § Unknowns item 6 below.

## TL;DR

Claude Code's native installer ships a self-contained binary with no Node.js or Python pre-requisite for the end user. Plugins on disk are plain text (Markdown skills, JSON manifests, optional shell/JS scripts), with no bundled npm dependencies or pip requirements documented in the manifest schema. Hooks (which run Node.js code) execute in Claude Code's own Node.js runtime, not a subprocess. Per the agent's reading, Python and TypeScript plugins both face identical runtime friction: zero user-side dependencies required — but see the router note above about whether skill-subprocess-called JS truly inherits Claude's Node.

## Plugin packaging + manifest format

The plugin manifest (`.claude-plugin/plugin.json`) is minimal JSON with optional fields per [Create plugins - Claude Code Docs](https://code.claude.com/docs/en/plugins) (fetched 2026-05-13):

- **Required:** `name` (string, becomes skill namespace)
- **Optional:** `description`, `version`, `author`, `homepage`, `repository`, `license`
- **No dependencies field documented** — plugins may declare plugin-to-plugin dependencies (per [GitHub issue anthropics/claude-code#48864](https://github.com/anthropics/claude-code/issues/48864) — "Plugin docs missing `plugin.json` dependency declarations"), but the schema is undocumented and only applies to other Claude Code plugins, not npm/Python packages.

**Directory structure on disk** (verified by inspecting `C:\Users\chris\.claude\plugins\cache\claude-plugins-official\`):

```
plugin-name/
├── .claude-plugin/
│   └── plugin.json
├── skills/
│   ├── skill-one/
│   │   └── SKILL.md
│   └── skill-two/
│       └── SKILL.md
├── commands/              # Deprecated; skills/ preferred
├── agents/
├── hooks/
│   └── hooks.json
├── .mcp.json              # MCP server config
├── .lsp.json              # Language server config
├── bin/                   # Executables added to PATH during plugin use
├── monitors/
├── settings.json          # Default settings when plugin is enabled
└── README.md
```

Actual installed plugins show no `node_modules/`, no `requirements.txt`, no `package.json` dependencies — they're pure text and configuration. Example from `superpowers` v5.1.0 (`C:\Users\chris\.claude\plugins\cache\claude-plugins-official\superpowers\5.1.0\`): `package.json` exists but contains only `{"name": "superpowers", "version": "5.1.0", "type": "module", "main": ".opencode/plugins/superpowers.js"}` — metadata for the plugin ecosystem, not npm package dependencies.

## The marketplace install path

**User experience:**

1. User runs `/plugin install <name>@marketplace-name` or browses `/plugin` → Discover tab
2. Claude Code downloads the plugin archive and extracts it to `~/.claude/plugins/data/{plugin-id}/`
3. User runs `/reload-plugins` to activate
4. Done — no postinstall scripts, no subprocess automation, no package manager invoked

**What runs locally during install** per [Discover and install prebuilt plugins](https://code.claude.com/docs/en/discover-plugins) (fetched 2026-05-13):

- Pure file download + extraction from marketplace (GitHub repo, npm registry, or local path)
- **No `npm install` subprocess** — the docs show plugin dependencies auto-installing alongside the parent plugin, but this is *plugin-to-plugin*, not npm packages
- **No `pip install`** — Python plugins ship their code; if a hook or tool subprocess needs Python, the user must have it in `$PATH` already

## Language/runtime constraints — the load-bearing question

**Critical finding:** Claude Code v2.1+ ships as a **native binary** (per [How to Install Claude Code: Complete Setup Guide (2026)](https://www.nxcode.io/resources/news/install-claude-code-setup-guide-2026/), fetched 2026-05-13) with **zero dependencies for the user**:

- Native installer: `curl -fsSL https://claude.ai/install.sh | bash` (macOS/Linux) or `irm https://claude.ai/install.ps1 | iex` (Windows)
- Downloads a prebuilt binary for the OS/arch
- Requires **no Node.js, no Python, no npm, no pip**
- Auto-updates in the background
- [Advanced setup - Claude Code Docs](https://code.claude.com/docs/en/setup) confirms "ripgrep is usually included; Git for Windows recommended on native Windows"

**Node.js in plugins:**

- Hooks (`.js` files under `hooks/`) run in Claude Code's *own Node.js runtime*, not a subprocess
- The `bin/` directory (optional per manifest) can contain shell scripts or executables — Claude Code adds `bin/` to `$PATH` while the plugin is active, but these are invoked by name, not bootstrapped via npm
- No precedent found for a plugin declaring `node_modules/` or invoking `npm install` on first use

**Python in plugins:**

- Code intelligence plugins (LSP servers like `pyright-lsp`) require the language server binary to be pre-installed by the user (e.g., `pip install pyright-langserver`); [Discover and install prebuilt plugins](https://code.claude.com/docs/en/discover-plugins) documents this as "user must install the binary from the table"
- Plugin Python code (skills written in `.py` files that a shell hook calls) would require Python in `$PATH` — this is not handled by Claude Code
- No bundled venv, no `uv pip install`, no auto-provisioning documented

**The decisive symmetry (agent's claim):**

| Language | User pre-installs | How code runs | Documentation status |
|----------|-------------------|---------------|----------------------|
| Python | Python 3.x + deps | Shell subprocess (hook calls `python -m module`) | Code intelligence plugins explicitly document "user must install binary" — no auto-provisioning |
| Node.js | Node.js 18+ | Built into Claude Code runtime (hooks run in-process) | Not explicitly documented; hooks use Claude's Node, not subprocess |
| Bundled (Go/Rust binary) | Nothing | Direct subprocess | Possible but no precedent found; would need OS/arch distribution |

## Installed-plugin survey (3 examples)

### Example 1: `superpowers` v5.1.0

**Path:** `C:\Users\chris\.claude\plugins\cache\claude-plugins-official\superpowers\5.1.0\`
**Size:** ~15 MB (mostly docs/specs; source is smaller)
**Languages used:** Markdown, JavaScript/Node.js, Shell. No Python visible.
**Runtime entry points:**

- Skills defined in `SKILL.md` files — Claude invokes them
- Brainstorming skill has a background server (`server.cjs`) started by shell script
- Writing-skills has `render-graphs.js` — presumably called by hook

**Apparent runtime deps:** Node.js 18+ (for `server.cjs` and `render-graphs.js`), Bash (for shell scripts)
**Preinstall required:** Node.js, Bash
**No `package.json` with npm deps, no `requirements.txt`, no postinstall.**

### Example 2: `commit-commands` bbfcbdd86c26

**Path:** `C:\Users\chris\.claude\plugins\cache\claude-plugins-official\commit-commands\bbfcbdd86c26\`
**Size:** 50 KB
**Languages used:** Markdown only (3 command files: `clean_gone.md`, `commit-push-pr.md`, `commit.md`)
**Runtime entry points:** CLI commands exposed as slash commands; Claude invokes them, which trigger the harness hooks
**Apparent runtime deps:** None in the plugin itself; depends on `git` being in `$PATH`
**No Node.js, no Python required by the plugin directly.**

### Example 3: `frontend-design` bbfcbdd86c26

**Path:** `C:\Users\chris\.claude\plugins\cache\claude-plugins-official\frontend-design\bbfcbdd86c26\`
**Size:** 5 KB
**Languages used:** Markdown (one SKILL.md)
**Runtime entry points:** Skill file; Claude invokes it
**Apparent runtime deps:** None
**No Node.js, no Python, no external dependencies.**

**Summary:** Smallest plugins are pure Markdown; mid-size plugins may have shell/Node.js scripts; largest plugin (superpowers) has background servers. None ship `node_modules/` or `requirements.txt`. User responsibility for having Node.js/Python in `$PATH` if the plugin calls them. No auto-provisioning, no postinstall.

## Decision-driving comparison (the table)

> **Router caveat:** The "Zero" entries below for both Python (no bundling) and TS (plain JS, no deps) assume the *user already has the relevant interpreter on `$PATH`*. The TS-zero claim additionally rests on the assumption that subprocess-called `.js` files can use Claude Code's bundled Node — which the agent's research did not directly verify. Treat both zero entries with caution until § Unknowns item 6 is resolved.

| Approach | Marketplace listing possible? | Install action | First-use action | User must pre-install... | Friction floor |
| --- | --- | --- | --- | --- | --- |
| **Python kernel, no bundling** | Yes (documented) | Download + extract to `~/.claude/plugins/data/{id}/` | None; import and call functions | Python 3.x if any code hook calls Python; else nothing | **Zero** if pure Python library; **medium** if hooks call Python subprocess |
| **Python kernel, PyInstaller binary** | Yes (untested precedent) | Download + extract + run postinstall to build binary | None; binary is in `bin/` | None (binary included); or C runtime if PyInstaller has GLIBC dependency | **Zero** if single-file .exe; **low** if multi-file binary archive |
| **Python kernel, bundled venv** | Unlikely (not documented) | Download + extract (venv is ~50-200 MB) | None | None | **Low-medium** (large download, slower unzip) |
| **TypeScript kernel, plain JS, no deps** | Yes (example: superpowers hook code) | Download + extract | None | **Disputed**: agent claims none (Claude's Node bundled); router unverified for skill-subprocess case | **Zero (disputed)** |
| **TypeScript kernel, bundled `node_modules/`** | Unlikely (no precedent) | Download + extract (node_modules is 100+ MB) | None | None | **Low-medium** (large download, not standard plugin practice) |
| **TypeScript kernel, npm install on first run** | Possible (not documented, no precedent) | Download + extract | SessionStart hook runs `npm install` in plugin data dir | Node.js 18+, npm; or `npm` must be in `$PATH` | **Medium-high** (subprocess invocation, user npm setup, install time) |
| **Single binary (Go/Rust)** | Yes (theoretical; not found in official marketplace) | Download + extract | None (if single .exe) or postinstall to build | None | **Zero** if single prebuilt binary per OS/arch; **medium** if needs to be compiled |

**Agent's interpretation:** The friction floor is **identical for Python (no bundling) and TypeScript (plain JS, no deps)**: zero, assuming the user doesn't invoke subprocess calls to tools they don't have. The moment you add bundling or subprocess calls, friction rises equally for both languages.

## Recommendation (agent's)

**Ship the Python port (PR #3) for v0.1.0.**

**Rationale:**

1. **Friction floor is tied.** Python and TypeScript both run with zero user-side install friction when the plugin is pure-library code. The native installer ensures Claude Code is available; plugins are just text + Python/JS. No language has a structural advantage. _(Router note: this is the disputed claim — see § Unknowns item 6.)_
2. **Sunk cost + test coverage.** The Python port is at ~10.7k LOC with 187 passing tests and an existing CLI demo spec. Shipping it now unblocks users to evaluate the matcher and gives the maintainer a real external consumer (you) to iterate with. Rewriting the kernel in TypeScript adds 2-4 weeks of dev time and re-testing, with no friction benefit.
3. **Maintenance vector.** The matcher is algorithmic — pure business logic. Python's readability and type-annotation clarity (PEP 484) is a long-term win for a load-bearing module. TypeScript has the same type-safety story, but Python's standard library and terseness reduce cognitive load on the core decision logic.
4. **Hook + plugin ceremony.** If `claude-wayfinder` needs to export a hook entry point (e.g., a `UserPromptSubmit` hook that runs the router internally), Python is slightly less ergonomic because the hook would need to shell out to `python -m subprocess` or use a Node.js wrapper. TypeScript hooks are first-class in Claude Code. **But** the plugin doesn't ship hooks — it's a library. This concern is hypothetical for v0.1.
5. **Deferred decision path.** If, after v0.1 lands and external users ask for TypeScript SDK bindings or a Node.js-native port, you have the Python version as a reference implementation and can rewrite with confidence. Early rewrite is premature.

## Unknowns

1. **SessionStart hook auto-install pattern (Python-only concern).** The WebSearch found a reference to "postinstall spinner for heavy lifting (Bun + uv install, bun install inside the plugin cache)" but no documented mechanism or example. If v0.1 wants to auto-provision a venv on first run, that design doesn't exist in public docs yet. Treat as deferred to v0.2 if needed.
2. **Bundled venv or PyInstaller precedent.** No installed plugin on this machine uses either pattern. The absence of precedent doesn't mean it's impossible, but there's no example to copy.
3. **Single-binary Go/Rust plugin precedent.** Theoretical, not found in official marketplace. Implementable but would require CI to cross-compile for OS/arch matrix.
4. **Native installer's exact Node.js version.** Docs say "Node.js 18+" without exact version. Doesn't affect the decision; worth confirming if a plugin ever needs a specific Node version.
5. **MCP server plugins (like `microsoft-docs` 0.3.1).** The manifest shows `microsoft-docs` has both `.claude-plugin/plugin.json` and `.codex-plugin/plugin.json` and `.github/plugin/plugin.json`, suggesting multi-platform support. The actual MCP runtime requirements for plugins aren't detailed in the research.
6. **🚩 (Router-added) Skill-subprocess JS runtime source.** The agent's "zero friction" verdict for plain-JS TS relies on Claude Code's bundled Node being available to scripts a *skill* subprocess-calls. Hooks are confirmed to load into Claude's runtime (in-process). But if a skill's markdown says "run `node ./matcher.js`", does that `node` come from Claude Code's bundled binary (via `bin/` PATH injection or similar) or does it require the user to have Node separately on PATH? The agent did not find a primary source either way. **Resolution would change the recommendation.** If skill-subprocess JS *does* inherit Claude's Node → TS pivot has a real friction advantage. If it doesn't → Python and TS are truly tied on friction, and "ship Python" stands.

## Sources

- [Create plugins - Claude Code Docs](https://code.claude.com/docs/en/plugins) (fetched 2026-05-13)
- [Discover and install prebuilt plugins through marketplaces - Claude Code Docs](https://code.claude.com/docs/en/discover-plugins) (fetched 2026-05-13)
- [Advanced setup - Claude Code Docs](https://code.claude.com/docs/en/setup) (fetched 2026-05-13)
- [DOCS] Plugin docs missing `plugin.json` dependency declarations and auto-install behavior · Issue #48864 · anthropics/claude-code](https://github.com/anthropics/claude-code/issues/48864) (fetched 2026-05-13)
- [Plugins reference - Claude Code Docs](https://code.claude.com/docs/en/plugins-reference) (fetched 2026-05-13)
- [How to Install Claude Code: Complete Setup Guide (2026) | NxCode](https://www.nxcode.io/resources/news/install-claude-code-setup-guide-2026/) (fetched 2026-05-13)
- [Claude Code Native Installer: Skip Node.js Entirely | claudefa.st](https://claudefa.st/blog/guide/native-installer) (fetched 2026-05-13)
- Local inspection of installed plugins: `C:\Users\chris\.claude\plugins\cache\claude-plugins-official\`
- Local plugin manifest analysis: `superpowers` v5.1.0, `commit-commands` bbfcbdd86c26, `frontend-design` bbfcbdd86c26

---

## Follow-up research (2026-05-13, pass 2)

> **Goal:** Resolve `Unknowns` item #6 from pass 1 — does subprocess-called `node` from a skill require the user to have Node on `$PATH`?
> **Method:** `claude-code-guide` sub-agent dispatch, escalating C→B→D. Stage C (authoritative Anthropic source) resolved it; verified by router via `ops` agent reading the cited issue.

### Stage 1 result: Authoritative answer found

**The primary source:** [`anthropics/claude-code#30465` — "[FEATURE] Expose embedded Bun runtime for skill scripts"](https://github.com/anthropics/claude-code/issues/30465) — opened 2026-03-03 by `kamilchm`, closed `not_planned` 2026-03-31. Verified verbatim by router (issue body and state confirmed via `mcp__github__get_issue` on 2026-05-13).

**Load-bearing quote from the issue body** (the feature requester's stated premise, uncontested by maintainers in the closure path):

> When a skill bundles a TypeScript or JavaScript file and the SKILL.md instructs Claude to run it, Claude invokes `bun run script.ts` or `node script.ts` via Bash. But:
>
> - **Native install users** (the majority, and the recommended install path) have Bun embedded inside the `claude` binary **but no `bun` or `node` on their PATH**.
> - **npm install users** have Node.js by definition, but this install method is being deprecated.
> - **Non-technical users** — the growing audience Anthropic is targeting — have no idea how to install a language runtime, and shouldn't have to.

**Caveat:** the closure was performed by `github-actions[bot]` rather than a maintainer comment. The 2 issue comments were not fetched for verbatim review. The bot closure + `not_planned` state_reason + a feature request that wouldn't exist if the runtime were already exposed = sufficient evidence to act on. Re-open this if a comment in #30465 turns out to contradict the body.

### Stage 2 result: Skipped (Stage 1 resolved the question)

### Stage 3 result: Skipped (Stage 1 resolved the question)

### Final answer to the load-bearing question

**Subprocess-called `node` (or `bun`, or `python`) in a skill REQUIRES the user to have the relevant interpreter on `$PATH` independently of Claude Code.** Claude Code's native installer ships Bun embedded inside the `claude` binary but does NOT expose it on PATH. Anthropic was asked to expose it and chose not to. This applies equally to Python — there is no documented mechanism for skills to inherit any runtime from the Claude Code installer.

### Implication for kernel-language choice — recommendation REVISED

The pass 1 "tied at zero friction" claim was **wrong** for the skill-subprocess invocation pattern, which is the pattern `claude-wayfinder` uses. The corrected picture:

| Approach | Native-install user must pre-install | npm-install user must pre-install | Friction floor (corrected) |
| --- | --- | --- | --- |
| Python kernel, no bundling | Python 3.x | Python 3.x | **Medium** — same as TS, regardless of how user installed Claude Code |
| TypeScript kernel, plain JS, no deps | Node.js | Nothing (npm install path provides Node) | **Medium** — fails the same way Python does on native installs (the recommended path) |
| Python + PyInstaller binary | Nothing (binary in plugin) | Nothing | **Zero**, with caveat: PyInstaller on Windows has signing/AV issues |
| TS bundled via `bun build --compile` | Nothing (single binary) | Nothing | **Zero**, cleaner than PyInstaller |
| Single binary (Go/Rust, cross-compiled) | Nothing | Nothing | **Zero**, but burns the most dev time |

**Key correction:** Python and plain-JS TS are **tied on friction** for the skill-subprocess pattern, but tied at *medium* friction (interpreter prereq), not zero. The earlier "TS gives zero friction" claim was based on conflating hook execution (in-process, Claude's bundled runtime) with skill-subprocess execution (user's PATH). Issue #30465 dissolves that conflation.

**Revised recommendation: ship the Python port (PR #3) for v0.1.0.**

The logic chain:

1. The user's bar is "marketplace install → works." Strictly, no skill-subprocess kernel meets it in any language under the current Claude Code model.
2. To approach that bar, you need bundled-runtime distribution: PyInstaller, `bun build --compile`, or a single-binary rewrite in Go/Rust. All three are feasible; all three are post-v0.1 work.
3. Python and TS are symmetric on friction without bundling. Python has the work already done; TS would burn 1-2 weeks of rewrite for zero friction-floor improvement.
4. Therefore: ship Python now with a documented interpreter prereq, and treat "zero-friction distribution via bundling" as an explicit v0.2 (or v0.3) initiative — which can then evaluate Python+PyInstaller vs TS+Bun-compile vs Go/Rust on the merits of bundling, not language choice in isolation.

**Anti-recommendation: do NOT pivot to TS now.** The pivot's premise — that TS reduces install friction — does not survive the verified evidence. Pivoting would throw away the Python port to land on the same friction floor.

### Updated `# Unknowns`

- **Comments on `anthropics/claude-code#30465` not verbatim-verified.** If a maintainer comment in there contradicts the issue body, the verdict reopens. (Mitigation: low likelihood, since the request wouldn't exist if the runtime were exposed; if it matters, `gh issue view 30465 --repo anthropics/claude-code --comments` will return them.)
- **`bin/` PATH-injection behavior** for plugins. Skills *cannot* assume runtimes — but `bin/` directories added to PATH while a plugin is active might. If `bin/` PATH-injection respects bundled binaries shipped in the plugin tree, a plugin could ship its own runtime that way. This wasn't explored in pass 2 and is the cheapest path to "bundled runtime in a plugin" if it works. Worth a follow-up if the v0.2 zero-friction question becomes live.
- **PyInstaller on Windows signing / AV behavior.** Real concern for distribution if Python+bundle is the v0.2 path.
- **Bun's `--compile` story on Windows.** Bun has historically had less-mature Windows support than macOS/Linux.

---

## Community pattern survey (2026-05-13, pass 3)

> **Goal:** Find existing plugins shipping non-trivial executable code and document how they handle the install-friction problem. Decide what shape v0.2 bundling work should take, based on what real plugins do — not theoretical options.
> **Method:** `claude-code-guide` sub-agent dispatch — local plugin enumeration + GitHub search + web search.
> **Router caveat (read first):** The agent's "10 plugins" set is dominated by plugins we already surveyed locally in pass 1, plus a handful of language-server plugins that don't fit the analog (LSP plugins assume the user has installed the language server binary themselves — that's a different friction model). Third-party GitHub search for plugins shipping runtime kernels did not surface meaningful results. That null result is itself a finding — it suggests either the marketplace is heavily Anthropic-dominated, the search queries didn't hit (search-engine flakiness), or third-party plugins shipping non-trivial runtime simply don't exist at scale yet. Treat the survey's depth with some caution; treat the headline conclusion ("no precedent for runtime bundling") as well-supported because it aligns with pass 1's local survey and the absence of any documented bundling pattern in pass 2.

### Surveyed plugins

| # | Plugin | Languages used | Runtime distribution | User must pre-install | Size |
| --- | --- | --- | --- | --- | --- |
| 1 | `ralph-loop` (official) | Bash | Hooks call bash scripts | `bash`, `git` | ~200 KB |
| 2 | `explanatory-output-style` (official) | Bash | Single bash script | `bash` | ~15 KB |
| 3 | `microsoft-docs` (official) | Node/TS | Skill-subprocess calls Node CLI | Node 22+ | ~50 MB |
| 4 | `superpowers` (official) | Node + Bash | Hooks + skill-subprocess; `server.cjs`, `render-graphs.js` | Node 18+, bash | ~15 MB |
| 5 | `commit-commands` (official) | Markdown only | Skills shell out to `git` | `git` | ~50 KB |
| 6 | `frontend-design` (official) | Markdown only | No subprocess | Nothing | ~5 KB |
| 7 | `atomic-agents` (official) | Markdown only | No subprocess | Nothing | ~1 MB |
| 8 | `pyright-lsp` (official) | None (LSP-only) | LSP server runs externally | User installs `pyright-langserver` separately | small |
| 9 | `typescript-lsp` (official) | None (LSP-only) | LSP server runs externally | User installs TS language server | small |
| 10 | `csharp-lsp` (official) | None (LSP-only) | LSP server runs externally | User installs .NET/C# tools | small |

The four pass-1-surveyed plugins reappear; rows 8-10 are LSP plugins that fit a *different* friction model (user-installed daemon, not subprocess from skill). Rows 3 and 4 are the closest analogs to a Node-implemented kernel called from a skill.

### Pattern synthesis

1. **Two prevailing patterns in the official marketplace:**
   - **Pattern A — "Requires interpreter on PATH"** (most plugins with executable code, including `ralph-loop`, `microsoft-docs`, `superpowers`). Plugin ships source files; user must have the interpreter; plugin's README/install instructions document the prereq.
   - **Pattern B — "Pure Markdown"** (`frontend-design`, `atomic-agents`, `commit-commands`). No runtime needed beyond what Claude Code itself uses.
2. **Plugins shipping a Node-implemented kernel called via `node` subprocess from a skill:** `microsoft-docs` (Node 22+ CLI) and `superpowers` (Node 18+ hooks + skill scripts) are the two closest analogs. **Both follow Pattern A — they require the user to have Node on PATH**. Neither bundles Node, neither uses `bin/` to ship a runtime. This is the strongest evidence that the TS-as-kernel path lands at the same friction floor as Python: the existing TS plugins don't escape the friction.
3. **Plugins shipping a Python-implemented kernel called via skill subprocess:** None found in the official marketplace. (LSP plugins like `pyright-lsp` require Python but don't follow the skill-subprocess pattern — they delegate to an LSP daemon.) The Python-kernel-via-skill pattern would be a new entrant; no existing precedent to follow, but also no existing precedent for *not* following.
4. **`bin/` directory used to ship executables:** **Zero precedent.** The mechanism is documented (per pass 1: "Executables added to PATH while the plugin is enabled"), but no surveyed plugin uses it for bundling its own runtime. This is potentially the cleanest path for future bundling work, but it would be untested ground.
5. **Recommendation for v0.2 bundling — confirmed:**
   - The community has not solved zero-friction distribution. Pattern A (require interpreter on PATH) is the prevailing convention.
   - Bundling via `bin/` with PyInstaller / `bun build --compile` / Go-Rust single binary is *theoretically clean* but has no implementation precedent. Pioneering it would be real engineering work — could become a notable contribution to the ecosystem, but carries unknown-unknown risk (does Claude Code's `bin/` PATH injection actually work for binaries inside the plugin tree? does it work cross-platform? does it survive plugin upgrades?).
   - **v0.1 path: follow Pattern A** — ship Python, document the interpreter prereq, fit the marketplace convention. No engineering risk; meets the user where they are; consistent with how `superpowers` and `microsoft-docs` operate.
   - **v0.2 path (if zero-friction becomes a real adoption blocker): scope a `bin/`-based bundling experiment** as its own initiative. Likely value but unknown effort until prototyped.

### Pattern-survey implications for the v0.1 plan

- The Python port stays. Friction parity confirmed across two distinct lines of evidence (skill-subprocess invocations require user-side interpreter per #30465; existing Node-kernel plugins still require user-side Node).
- The v0.1 install story is "user has Python; clone or install package; works" — same shape as `microsoft-docs` documents for Node.
- A v0.2 issue should be filed to *scope* (not implement) the `bin/`-bundling experiment, while context is fresh. Likely entry points: PyInstaller per-platform binaries shipped under `bin/`, validated against Claude Code's PATH-injection on Windows / macOS / Linux.

### Updated `# Unknowns`

- **Third-party plugin ecosystem depth.** Pass 3's GitHub search did not surface third-party plugins shipping non-trivial runtime. Either the marketplace is heavily centralized on Anthropic's official set, or third-party plugins simply haven't grown that pattern yet. Worth re-checking in 3-6 months — the ecosystem is young and changing fast.
- **`bin/` PATH-injection behavior for plugin-tree-bundled binaries.** Documented but uncharacterized. The cheapest path to v0.2 zero-friction; needs a prototype to validate.
- **The two unread comments on `anthropics/claude-code#30465`** still unread. Listed in pass 2; mentioning here for completeness. Mitigation hasn't changed.
