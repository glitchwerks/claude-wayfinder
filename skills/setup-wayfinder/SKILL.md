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

The plugin data directory path is deterministic per Anthropic's plugin docs:

- If `$CLAUDE_PLUGIN_DATA` is set in the environment, **validate its basename matches the expected slug** before honoring it — see the bash recipe below. The env var is the test seam, but the harness sets it to whichever plugin invoked the current surface, so another plugin's data dir can leak in.
- Otherwise, compute `~/.claude/plugins/data/{slug}/` where `{slug}` is `claude-wayfinder@glitchwerks` with every non-`[a-zA-Z0-9_-]` character replaced by `-`. For our plugin (`claude-wayfinder` distributed from the `glitchwerks` marketplace), the slug is `claude-wayfinder-glitchwerks`.

Use the Bash tool:

```bash
EXPECTED_SLUG="claude-wayfinder-glitchwerks"
if [ -n "$CLAUDE_PLUGIN_DATA" ]; then
  ACTUAL_SLUG=$(basename "$CLAUDE_PLUGIN_DATA")
  if [ "$ACTUAL_SLUG" != "$EXPECTED_SLUG" ]; then
    echo "Warning: \$CLAUDE_PLUGIN_DATA points at '$ACTUAL_SLUG', expected '$EXPECTED_SLUG'. Falling back to computed path." >&2
    PLUGIN_DATA="$HOME/.claude/plugins/data/$EXPECTED_SLUG"
  else
    PLUGIN_DATA="$CLAUDE_PLUGIN_DATA"
  fi
else
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

The test seam is preserved: tests that want to override the path can still set `$CLAUDE_PLUGIN_DATA` to a directory whose basename matches `claude-wayfinder-glitchwerks` (e.g., a tmpdir created as `…/claude-wayfinder-glitchwerks/`). Cross-plugin leaks no longer cause silent misinstall into another plugin's data dir.

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

Create `$PLUGIN_DATA/setup-state.json` with exact shape:

```json
{
  "version": "<PLUGIN_VERSION>",
  "venv_path": "<absolute path to $PLUGIN_DATA/venv>",
  "interpreter": "<the candidate from Step 2 that worked>",
  "installed_at": "<ISO-8601 UTC timestamp>"
}
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
