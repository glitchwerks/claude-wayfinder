---
name: setup-wayfinder
description: |
  Materialize the claude-wayfinder Python venv at ${CLAUDE_PLUGIN_DATA}/venv/
  and write the setup-state flag. Use when:
  - User types /setup-wayfinder
  - User says "set up claude-wayfinder", "install wayfinder dependencies"
  - User says "wayfinder isn't working", "fix wayfinder", "repair wayfinder"
  - SessionStart banner indicates setup is required and the user wants to proceed
  - Plugin version bumped and re-setup needed
  Do NOT trigger on casual mentions of "wayfinder" without setup/install/fix intent.
---

# Setup claude-wayfinder

Materialize the Python venv at `${CLAUDE_PLUGIN_DATA}/venv/` so plugin hooks can spawn a Python that has `claude_wayfinder` importable. Write the setup-state flag so hooks know setup completed.

The behavior described below is mirrored by `tests/integration/setup_pipeline.py`. If you change anything here, update that file too (CI's `test_skill_pipeline_sync.py` enforces this).

## Step 1: Resolve `${CLAUDE_PLUGIN_DATA}`

The plugin data directory path is keyed `<plugin>-<marketplace>` by the harness, and the **same plugin resolves under different marketplace keys across surfaces** — `claude-wayfinder-glitchwerks` on a CLI surface, `claude-wayfinder-inline` on a desktop/ccd surface. Two sources are available, in priority order:

- **Runtime env var** `$CLAUDE_PLUGIN_DATA` (no braces) — the test seam, but the harness sets it to whichever plugin invoked the current surface, so **another plugin's data dir can leak in** (e.g. `codex-inline`). Honor it **only when its basename is a same-plugin key**: `claude-wayfinder-<marketplace>` with exactly **one** marketplace segment after the plugin name (`-glitchwerks`, `-inline`, …). This admits every marketplace variant while rejecting two leak classes — unrelated plugins (`codex-inline`) **and prefix-colliding sibling plugins** whose own name starts with `claude-wayfinder-` (e.g. `claude-wayfinder-helper` → key `claude-wayfinder-helper-inline`, which has a second `-` segment). Do **not** require an exact `-glitchwerks` match — that is the bug this guard replaces (it rejected the legitimate `-inline` desktop key and silently misinstalled into the CLI-keyed dir, leaving every desktop session stuck on the "requires setup" banner). Do **not** loosen it to a bare `claude-wayfinder-*` prefix either — that re-admits the sibling-plugin collision.
- **Harness-rendered literal** `${CLAUDE_PLUGIN_DATA}` (braces) — the harness substitutes this token in the skill body at load time with **this session's** correct data dir. Prefer it over a hardcoded `-glitchwerks` slug whenever the runtime env var is unusable (cross-plugin leak, or unset).
- **Computed slug** (`claude-wayfinder-glitchwerks`) — last resort only, for raw execution outside the harness where neither source above is available.

Use the Bash tool:

```bash
PLUGIN_NAME="claude-wayfinder"
EXPECTED_SLUG="claude-wayfinder-glitchwerks"

# Harness-rendered literal (braces): substituted at skill-load with THIS session's
# data dir (…/claude-wayfinder-inline on desktop, …/claude-wayfinder-glitchwerks on
# CLI) — correct per-session. Keep the braces; do NOT rewrite to $CLAUDE_PLUGIN_DATA.
HARNESS_DATA="${CLAUDE_PLUGIN_DATA}"

if [ -n "$CLAUDE_PLUGIN_DATA" ]; then
  ACTUAL_SLUG=$(basename "$CLAUDE_PLUGIN_DATA")
  case "$ACTUAL_SLUG" in
    ${PLUGIN_NAME}-*-*)
      # Prefix-colliding SIBLING plugin: a different plugin whose name starts with
      # "claude-wayfinder-" (e.g. claude-wayfinder-helper → key
      # claude-wayfinder-helper-inline) leaves TWO segments after the plugin name.
      # Our own keys are exactly claude-wayfinder-<marketplace> (one segment, e.g.
      # -inline | -glitchwerks). Reject the collision — use the harness literal.
      echo "Warning: \$CLAUDE_PLUGIN_DATA basename '$ACTUAL_SLUG' has extra segments after '${PLUGIN_NAME}-' (prefix-colliding sibling plugin?). Using harness-rendered data dir." >&2
      PLUGIN_DATA="$HARNESS_DATA" ;;
    ${PLUGIN_NAME}-*)
      # Same-plugin key, single marketplace segment (e.g. claude-wayfinder-inline |
      # -glitchwerks) — honor it.
      PLUGIN_DATA="$CLAUDE_PLUGIN_DATA" ;;
    *)
      # Cross-plugin leak (e.g. codex-inline) — fall back to the harness literal.
      echo "Warning: \$CLAUDE_PLUGIN_DATA basename '$ACTUAL_SLUG' is not a ${PLUGIN_NAME}-* dir (cross-plugin leak?). Using harness-rendered data dir." >&2
      PLUGIN_DATA="$HARNESS_DATA" ;;
  esac
else
  PLUGIN_DATA="$HARNESS_DATA"
fi

# Safety net: if the harness literal was never interpolated (raw execution outside
# the harness leaves it empty), compute the canonical slug as a last resort.
if [ -z "$PLUGIN_DATA" ]; then
  PLUGIN_DATA="$HOME/.claude/plugins/data/$EXPECTED_SLUG"
fi

# Normalize Git-Bash POSIX paths (/c/Users/...) to native Windows form (C:/Users/...)
# so the venv_path stored in setup-state.json (Step 7) is readable by Node's
# fs.existsSync on Windows. Git Bash expands $HOME to /c/Users/... rather than
# C:/Users/..., which Node does not recognise as a valid Windows path (#186).
# On non-Windows or paths that don't match the /X/... pattern, this is a no-op.
case "$PLUGIN_DATA" in
  /[a-zA-Z]/*)
    drive=$(printf '%s' "$PLUGIN_DATA" | cut -c2 | tr '[:lower:]' '[:upper:]')
    rest=$(printf '%s' "$PLUGIN_DATA" | cut -c3-)
    PLUGIN_DATA="${drive}:${rest}"
    ;;
esac

mkdir -p "$PLUGIN_DATA"
echo "$PLUGIN_DATA"
```

The test seam is preserved: tests that want to override the path can still set `$CLAUDE_PLUGIN_DATA` to any directory whose basename is a single-segment same-plugin key `claude-wayfinder-<seg>` (e.g., a tmpdir created as `…/claude-wayfinder-XXXX/`, where `XXXX` has no further `-`). Cross-plugin leaks (`codex-inline`) and prefix-colliding sibling plugins (`claude-wayfinder-helper-inline`) are rejected and no longer cause silent misinstall into another plugin's data dir.

## Step 2: Discover Python ≥3.11

Try these candidates in order, stopping at the first that probes successfully:

1. **Prior interpreter** from any existing setup-state.json's `interpreter` field (if a flag is currently being re-setup).
2. `$CLAUDE_WAYFINDER_BOOTSTRAP_PYTHON` if set.
3. `py -3` on Windows.
4. `python3` then `python` on PATH.

Probe each candidate with:

```bash
<candidate> -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
```

Exit 0 = success, save the candidate as `PYTHON`.

If all candidates fail, ask the user:

> No Python ≥3.11 found. I tried: $CLAUDE_WAYFINDER_BOOTSTRAP_PYTHON, py -3, python3, python. Please provide an absolute path to a Python ≥3.11 interpreter (e.g., `C:\Python313\python.exe` or `/usr/local/bin/python3.12`). If you don't have one installed, you'll need to install Python first.

Probe the user-provided path the same way. If it works, save it (will be persisted in the flag's `interpreter` field for future re-setup runs).

## Step 3: Wipe the existing venv

Per spec § 2 D4, always wipe + recreate. No idempotency.

```bash
rm -rf "$PLUGIN_DATA/venv"
```

(On Windows PowerShell: `Remove-Item -Recurse -Force -ErrorAction SilentlyContinue "$env:PLUGIN_DATA\venv"`)

## Step 4: Create the venv

```bash
"$PYTHON" -m venv "$PLUGIN_DATA/venv"
```

If this fails:
- Surface the stderr verbatim to the user.
- Offer the common-cause hint: on Debian/Ubuntu, `sudo apt install python3-venv` is often needed.
- Wipe any partial state. Do not write the flag. Stop.

## Step 5: Install claude-wayfinder from PyPI

The version to install is the plugin's current version, read from `${CLAUDE_PLUGIN_ROOT}/pyproject.toml` (preferred) or `${CLAUDE_PLUGIN_ROOT}/.claude-plugin/plugin.json`. Pin exactly:

```bash
VENV_PYTHON="$PLUGIN_DATA/venv/bin/python"  # or Scripts/python.exe on Windows
"$VENV_PYTHON" -m pip install "claude-wayfinder==$PLUGIN_VERSION"
```

If pip fails:
- Surface stderr verbatim.
- **Wipe the half-built venv** (always-wipe invariant).
- Do not write the flag.
- Tell the user to check network/PyPI and retry.

## Step 6: Verify import

```bash
"$VENV_PYTHON" -c "import claude_wayfinder"
```

If this fails after a successful `pip install`, the wheel is corrupt:
- Surface the import error.
- Suggest `pip cache purge` then retry.
- Wipe the venv. Do not write the flag.

## Step 7: Write the setup-state flag

Always-wipe contract: the Write tool's read-before-overwrite invariant is hostile on re-setup, where the prior file exists but its contents are about to be discarded — use a shell heredoc instead.

**Bash (Git Bash on Windows or POSIX):**

```bash
TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
cat > "$PLUGIN_DATA/setup-state.json" <<EOF
{
  "version": "$PLUGIN_VERSION",
  "venv_path": "$PLUGIN_DATA/venv",
  "interpreter": "$PYTHON",
  "installed_at": "$TS"
}
EOF
```

**PowerShell:**

```powershell
$ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
@"
{
  "version": "$PluginVersion",
  "venv_path": "$PluginData/venv",
  "interpreter": "$Python",
  "installed_at": "$ts"
}
"@ | Set-Content -Path "$PluginData/setup-state.json" -NoNewline
```

If the write fails (disk full, permission), wipe the venv (otherwise orphaned), surface the write error.

## Step 8: Tell the user

On success, tell the user:

> ✓ claude-wayfinder setup complete.
>
> - venv: `<path>`
> - interpreter: `<which Python was used>`
> - version: `<plugin version>`
>
> Open a new session for the dispatch matcher and catalog refresh to activate. The next `check-catalog-health.js` SessionStart hook will see the flag and proceed normally.
