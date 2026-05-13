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
