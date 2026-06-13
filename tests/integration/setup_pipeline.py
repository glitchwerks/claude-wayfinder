"""Executable mirror of the setup-wayfinder skill body.

The skill body at skills/setup-wayfinder/SKILL.md describes 8 numbered steps
that the LLM follows when /setup-wayfinder is invoked. This module exposes
each step as an importable function so CI can run the full pipeline end-to-end
on a real Python interpreter.

The skill body and this module must stay in sync — see
tests/test_skill_pipeline_sync.py for the drift check.

Spec § 4.1 (skill body) and § 3 (architecture) are the source of truth.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shlex
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path


class SetupError(Exception):
    """Raised when a step in the setup pipeline cannot complete."""


def compute_plugin_data_dir(
    plugin_id: str = "claude-wayfinder@glitchwerks",
) -> Path:
    """Step 1: Resolve ${CLAUDE_PLUGIN_DATA} deterministically.

    Honors $CLAUDE_PLUGIN_DATA when its basename matches the same-plugin
    prefix ``<plugin_name>-`` (e.g. ``claude-wayfinder-inline``,
    ``claude-wayfinder-glitchwerks``) **and** the remainder after the
    prefix contains no ``-`` (exactly one marketplace segment). Rejects
    two leak classes: unrelated plugins whose basename starts with a
    *different* plugin's name (e.g. ``codex-inline``), and prefix-
    colliding sibling plugins whose name begins with the same prefix but
    has an extra segment (e.g. ``claude-wayfinder-helper-inline`` has two
    segments after ``claude-wayfinder``). Falls back to the computed slug
    ``~/.claude/plugins/data/claude-wayfinder-glitchwerks`` in all
    rejected cases.

    This guards against the harness keying plugin data dirs as
    ``<plugin>-<marketplace>`` and a desktop/ccd session injecting
    ``$CLAUDE_PLUGIN_DATA`` pointing at a different plugin's dir (the
    infinite "requires setup" banner bug, issue #342).

    **Mirror asymmetry — no harness here:** The SKILL.md Step 1 logic
    has a three-tier fallback: (1) env var when same-plugin prefix
    matches, (2) harness-rendered ``${CLAUDE_PLUGIN_DATA}`` literal,
    (3) computed ``claude-wayfinder-glitchwerks`` slug. This Python
    mirror has no harness, so tier 2 does not exist. On a rejected or
    unset env var this function falls directly to the computed slug —
    tier 3. Do not "fix" this mirror to add a tier-2 path; without a
    harness there is no harness-rendered value to use.

    Args:
        plugin_id: Plugin identifier used to compute the data directory
            slug. Defaults to the canonical wayfinder plugin ID. The
            plugin name (part before ``@``) is also used to derive the
            same-plugin prefix guard.

    Returns:
        Absolute path to the plugin data directory. The directory is not
        created by this function — callers that need it to exist should
        call ``mkdir(parents=True, exist_ok=True)`` on the result.
    """
    # Derive plugin name: the part of the ID before "@" (e.g.
    # "claude-wayfinder" from "claude-wayfinder@glitchwerks").
    plugin_name = plugin_id.split("@")[0]
    same_plugin_prefix = f"{plugin_name}-"

    env_override = os.environ.get("CLAUDE_PLUGIN_DATA")
    if env_override:
        basename = os.path.basename(os.path.normpath(env_override))
        segment = basename[len(same_plugin_prefix):]
        if basename.startswith(same_plugin_prefix) and "-" not in segment:
            return Path(env_override)
        # Cross-plugin leak, prefix-collision, or non-matching key —
        # fall through to slug.

    slug = re.sub(r"[^a-zA-Z0-9_\-]", "-", plugin_id)
    return Path.home() / ".claude" / "plugins" / "data" / slug


def discover_python(prior_interpreter: str | None = None) -> str:
    """Step 2: Find a Python interpreter ≥3.11.

    Try, in order: prior_interpreter (from previous run's flag),
    $CLAUDE_WAYFINDER_BOOTSTRAP_PYTHON, ``py -3`` on Windows,
    ``python3``, ``python``. Probe each with
    ``-c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"``.

    Args:
        prior_interpreter: Command string from a previous setup run's
            flag file (``setup-state.json`` ``interpreter`` field). When
            provided it is tried first, allowing re-setup to reuse the
            same interpreter without user input.

    Returns:
        The first candidate command string that passes the version probe.
        This is the raw command string (e.g. ``"python3"`` or
        ``"py -3"``), not an absolute path.

    Raises:
        SetupError: When all candidates fail the version probe. The
            skill body asks the user for a path; in CI this should never
            be reached because $CLAUDE_WAYFINDER_BOOTSTRAP_PYTHON is set.
    """
    candidates: list[str] = []
    if prior_interpreter:
        candidates.append(prior_interpreter)
    env_override = os.environ.get("CLAUDE_WAYFINDER_BOOTSTRAP_PYTHON")
    if env_override:
        candidates.append(env_override)
    if platform.system() == "Windows":
        candidates.append("py -3")
    candidates.extend(["python3", "python"])

    probe = "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
    for candidate in candidates:
        try:
            args = candidate.split() + ["-c", probe]
            result = subprocess.run(args, capture_output=True, check=False, timeout=10)
            if result.returncode == 0:
                return candidate
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    raise SetupError(
        f"No Python ≥3.11 found. Tried: {candidates}. "
        "Set CLAUDE_WAYFINDER_BOOTSTRAP_PYTHON to an absolute path."
    )


def wipe_venv(venv_dir: Path) -> None:
    """Step 3: Delete the venv directory if it exists.

    Always-wipe per spec § 2 D4. No idempotency — the directory is
    removed unconditionally if present so the subsequent ``create_venv``
    call always starts from a clean slate.

    Args:
        venv_dir: Absolute path to the venv directory to remove. A
            missing directory is silently ignored (no error raised).
    """
    if venv_dir.exists():
        shutil.rmtree(venv_dir)


def create_venv(python_cmd: str, venv_dir: Path) -> None:
    """Step 4: Create a fresh virtual environment.

    Runs ``<python_cmd> -m venv <venv_dir>``. The command string is
    split on whitespace so multi-word forms such as ``"py -3"`` work
    correctly.

    Args:
        python_cmd: Interpreter command string as returned by
            :func:`discover_python` (e.g. ``"python3"`` or ``"py -3"``).
        venv_dir: Destination path for the new venv. Must not already
            exist; callers should call :func:`wipe_venv` first.

    Raises:
        SetupError: If the ``python -m venv`` subprocess exits nonzero.
            The error message includes the captured stdout and stderr so
            callers can surface them to the user verbatim.
    """
    args = python_cmd.split() + ["-m", "venv", str(venv_dir)]
    try:
        result = subprocess.run(args, capture_output=True, text=True, check=False, timeout=60)
    except subprocess.TimeoutExpired as e:
        shutil.rmtree(venv_dir, ignore_errors=True)
        raise SetupError(f"python -m venv timed out after {e.timeout}s") from e
    if result.returncode != 0:
        raise SetupError(
            f"python -m venv failed (exit {result.returncode}):\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )


def get_venv_python(venv_dir: Path) -> Path:
    """Return the path to the venv's Python binary.

    Resolves the platform-specific location: ``Scripts/python.exe`` on
    Windows and ``bin/python`` on POSIX systems.

    Args:
        venv_dir: Root path of the virtual environment as passed to
            :func:`create_venv`.

    Returns:
        Absolute path to the venv's Python interpreter. The caller is
        responsible for verifying the path exists before use.
    """
    if platform.system() == "Windows":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def pip_install(venv_dir: Path, version: str) -> None:
    """Step 5: Install claude-wayfinder==<version> from PyPI.

    Uses the venv's own Python so the package lands inside the venv
    rather than the system site-packages. On failure the partial venv is
    wiped (always-wipe invariant, spec § 6 F3) before raising.

    Honors ``$CLAUDE_WAYFINDER_PIP_SPEC`` as a test-only override of the
    package spec — when set, replaces the default
    ``claude-wayfinder==<version>`` with the literal env-var value. This
    exists so pre-v0.4.0 PyPI-publication CI can install from the local
    plugin root (set to the repo path or ``-e <repo>``). Not part of the
    production skill body — production always uses the default PyPI
    install. Phase 7 publishes v0.4.0 and removes the need for this
    override in CI.

    Args:
        venv_dir: Root path of the virtual environment created by
            :func:`create_venv`.
        version: Exact package version string to pin (e.g. ``"0.3.6"``).
            Installed as ``claude-wayfinder==<version>`` unless
            ``$CLAUDE_WAYFINDER_PIP_SPEC`` is set.

    Raises:
        SetupError: If pip exits nonzero. The partial venv is removed
            before the error is raised so the caller never sees a
            half-built installation.
    """
    venv_python = get_venv_python(venv_dir)
    pip_spec = os.environ.get(
        "CLAUDE_WAYFINDER_PIP_SPEC",
        f"claude-wayfinder=={version}",
    )
    # shlex.split defaults to POSIX mode, which treats backslash as an escape
    # character — so a Windows path like D:\a\repo gets mangled to D:arepo
    # before pip sees it. posix=False preserves backslashes on Windows.
    # The branch is on os.name (not sys.platform) per Python convention.
    args = [
        str(venv_python),
        "-m",
        "pip",
        "install",
        *shlex.split(pip_spec, posix=(os.name != "nt")),
    ]
    try:
        result = subprocess.run(args, capture_output=True, text=True, check=False, timeout=180)
    except subprocess.TimeoutExpired as e:
        shutil.rmtree(venv_dir, ignore_errors=True)
        raise SetupError(f"pip install timed out after {e.timeout}s — check network") from e
    if result.returncode != 0:
        # Wipe partial state per spec § 6 F3
        shutil.rmtree(venv_dir, ignore_errors=True)
        raise SetupError(
            f"pip install {pip_spec!r} failed (exit {result.returncode}):\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )


def verify_import(venv_dir: Path) -> None:
    """Step 6: Confirm ``import claude_wayfinder`` works.

    Runs ``<venv_python> -c "import claude_wayfinder"`` as a post-install
    smoke check. On failure the venv is wiped before raising, consistent
    with the always-wipe invariant (spec § 6 F3).

    Args:
        venv_dir: Root path of the virtual environment populated by
            :func:`pip_install`.

    Raises:
        SetupError: If the import check exits nonzero, indicating a
            corrupt or incomplete wheel. The venv is removed before the
            error is raised.
    """
    venv_python = get_venv_python(venv_dir)
    args = [str(venv_python), "-c", "import claude_wayfinder"]
    try:
        result = subprocess.run(args, capture_output=True, text=True, check=False, timeout=15)
    except subprocess.TimeoutExpired as e:
        shutil.rmtree(venv_dir, ignore_errors=True)
        raise SetupError(f"import check timed out after {e.timeout}s") from e
    if result.returncode != 0:
        shutil.rmtree(venv_dir, ignore_errors=True)
        raise SetupError(
            f"import claude_wayfinder failed after install:\nstderr: {result.stderr}"
        )


def write_flag(
    plugin_data_dir: Path,
    version: str,
    venv_dir: Path,
    interpreter: str,
) -> Path:
    """Step 7: Write the setup-state.json flag file.

    Unconditionally overwrites ``$PLUGIN_DATA/setup-state.json`` with
    the canonical shape required by hooks. The always-wipe contract
    (spec § 2 D4) means the prior file's contents are irrelevant — this
    mirrors the shell heredoc in the skill body, which uses ``cat >``
    (Bash) or ``Set-Content`` (PowerShell) to overwrite without reading.
    The parent directory is created if it does not exist.

    Args:
        plugin_data_dir: Plugin data directory as returned by
            :func:`compute_plugin_data_dir`.
        version: Installed package version string (e.g. ``"0.3.6"``).
        venv_dir: Absolute path to the created venv (stored verbatim so
            hooks can locate ``Scripts/python.exe`` or ``bin/python``).
        interpreter: Command string from :func:`discover_python` (stored
            so re-setup runs can skip the discovery probe).

    Returns:
        Absolute path to the written flag file
        (``plugin_data_dir / "setup-state.json"``).
    """
    flag = {
        "version": version,
        "venv_path": str(venv_dir),
        "interpreter": interpreter,
        "installed_at": datetime.now(timezone.utc).isoformat(),
    }
    flag_path = plugin_data_dir / "setup-state.json"
    flag_path.parent.mkdir(parents=True, exist_ok=True)
    flag_path.write_text(json.dumps(flag, indent=2), encoding="utf-8")
    return flag_path


def run_full_pipeline(
    version: str,
    prior_interpreter: str | None = None,
) -> Path:
    """Run all 8 steps in order.

    Orchestrates the full setup sequence: resolve the plugin data
    directory, discover Python, wipe any existing venv, create a fresh
    venv, install the package, verify the import, and write the flag.
    Step 8 (tell the user) is intentionally excluded — that is the
    caller's responsibility so this function remains testable without
    side-channel output.

    Args:
        version: Package version to install (e.g. ``"0.3.6"``).
        prior_interpreter: Optional interpreter command from a previous
            run's flag. Forwarded to :func:`discover_python` so re-setup
            skips the discovery probe when the prior interpreter is still
            valid.

    Returns:
        Absolute path to the written ``setup-state.json`` flag file.

    Raises:
        SetupError: Propagated from any step that fails. Partial state
            (venv) is wiped inside the individual step functions before
            the error propagates.
    """
    plugin_data_dir = compute_plugin_data_dir()
    interpreter = discover_python(prior_interpreter=prior_interpreter)
    venv_dir = plugin_data_dir / "venv"
    wipe_venv(venv_dir)
    create_venv(interpreter, venv_dir)
    pip_install(venv_dir, version)
    verify_import(venv_dir)
    return write_flag(plugin_data_dir, version, venv_dir, interpreter)
