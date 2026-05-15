"""Mode-switching logic for the ``dispatch`` CLI subcommand.

Implements the three-outcome mode-detection contract from
docs/design/2026-05-14-v0.2-integration-design.md § 2.1:

- **Demo mode** — ``$DISPATCH_CATALOG_PATH`` is absent.  Runs the bundled
  demo fixtures with a "no catalog configured" banner.
- **Real-catalog mode** — ``$DISPATCH_CATALOG_PATH`` is set and resolves to
  a readable, valid JSON catalog.  Passes the context JSON to
  ``python -m claude_wayfinder.match`` via subprocess and returns the
  matcher's decision JSON verbatim.
- **Hard-error mode** — ``$DISPATCH_CATALOG_PATH`` is set but the path is
  missing, unreadable, or contains invalid/schema-invalid JSON.  Propagates
  the ``[CATALOG ERROR]`` banner from ``match.py`` and exits non-zero.
  **Never falls back to demo mode silently.**

Stale-mtime behavior (design § 2.1 last paragraph):
  When ``$DISPATCH_SKILLS_DIR`` and/or ``$DISPATCH_AGENTS_DIR`` are set and
  any source file within them has a mtime newer than the catalog file, a
  warning is emitted to stderr.  Execution proceeds — staleness is a
  degraded-quality signal, not an error.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CATALOG_ERROR_PREFIX = "[CATALOG ERROR]"

#: Banner printed to stdout when demo mode activates (no catalog configured).
_DEMO_BANNER = (
    "=============================================================\n"
    "  claude-wayfinder dispatch — demo mode\n"
    "  no catalog configured — running in demo mode\n"
    "  Set $DISPATCH_CATALOG_PATH to activate real-catalog mode.\n"
    "============================================================="
)

#: Stale-mtime warning template emitted to stderr.
_STALE_WARNING = (
    "[DISPATCH WARNING] Catalog mtime is older than source files: {paths}. "
    "Consider running `claude-wayfinder catalog build` to refresh. "
    "Proceeding with stale catalog."
)


# ---------------------------------------------------------------------------
# Stale-mtime detection
# ---------------------------------------------------------------------------


def _collect_source_files(
    skills_dir: Path | None,
    agents_dir: Path | None,
) -> list[Path]:
    """Return a list of skill/agent source files from the given directories.

    Recurses into *skills_dir* looking for ``SKILL.md`` files and scans
    *agents_dir* for ``*.md`` files at the top level.

    Args:
        skills_dir: Root of the skills tree (or ``None`` to skip).
        agents_dir: Root of the agents tree (or ``None`` to skip).

    Returns:
        Flat list of ``Path`` objects for every enumerated source file.
    """
    files: list[Path] = []
    if skills_dir is not None and skills_dir.is_dir():
        files.extend(skills_dir.rglob("SKILL.md"))
    if agents_dir is not None and agents_dir.is_dir():
        files.extend(agents_dir.glob("*.md"))
    return files


def check_catalog_staleness(
    catalog_path: Path,
    skills_dir: Path | None,
    agents_dir: Path | None,
) -> None:
    """Emit a stderr warning when the catalog is older than any source file.

    If either *skills_dir* or *agents_dir* is ``None`` (not enumerable),
    no warning is emitted — partial information is insufficient to judge
    staleness.  This keeps the warning opt-in for consumers who set the
    source-directory env vars.

    Args:
        catalog_path: Resolved path to the dispatch catalog file.
        skills_dir: Skills source directory (from ``$DISPATCH_SKILLS_DIR``).
        agents_dir: Agents source directory (from ``$DISPATCH_AGENTS_DIR``).
    """
    if skills_dir is None and agents_dir is None:
        return
    try:
        catalog_mtime = catalog_path.stat().st_mtime
    except OSError:
        # Catalog unreadable — hard-error path handles this separately.
        return

    source_files = _collect_source_files(skills_dir, agents_dir)
    stale_sources: list[str] = []
    for src in source_files:
        try:
            if src.stat().st_mtime > catalog_mtime:
                stale_sources.append(str(src))
        except OSError:
            continue

    if stale_sources:
        print(
            _STALE_WARNING.format(paths=", ".join(stale_sources)),
            file=sys.stderr,
        )


# ---------------------------------------------------------------------------
# Catalog validation
# ---------------------------------------------------------------------------


def _validate_catalog_json(catalog_path: Path) -> str | None:
    """Check that *catalog_path* exists, is readable, and contains valid JSON.

    Performs only the structural checks that can be done without invoking
    the full ``match.py`` loader (which handles schema validation and exits
    on error itself).

    Args:
        catalog_path: Path to the catalog file to validate.

    Returns:
        An error description string when validation fails, ``None`` on
        success.
    """
    if not catalog_path.exists():
        return f"file not found at {catalog_path}"
    try:
        text = catalog_path.read_text(encoding="utf-8")
    except OSError as exc:
        return f"could not read catalog: {exc}"
    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        return f"malformed JSON ({exc})"
    return None


# ---------------------------------------------------------------------------
# Mode dispatch
# ---------------------------------------------------------------------------


def run_dispatch(
    stdin_data: str | None = None,
    out: Any = None,
) -> int:
    """Run the dispatch subcommand with mode-detection.

    Mode is determined by the presence/absence of ``$DISPATCH_CATALOG_PATH``:

    - **Absent** → demo mode: print banner + run bundled demo fixtures.
    - **Present, valid** → real-catalog mode: pipe *stdin_data* to
      ``match.py`` via subprocess and return the decision JSON verbatim.
    - **Present, invalid** → hard error: propagate ``[CATALOG ERROR]``
      and return non-zero exit code.

    Args:
        stdin_data: JSON string with dispatch context (5-field shape from
            design § 2.2).  Read from ``sys.stdin`` when ``None``.
        out: File-like object for stdout.  Defaults to ``sys.stdout``.

    Returns:
        Exit code: 0 on success, non-zero on error.
    """
    if out is None:
        out = sys.stdout

    catalog_env = os.environ.get("DISPATCH_CATALOG_PATH")

    # ------------------------------------------------------------------
    # Demo mode — env var absent
    # ------------------------------------------------------------------
    if not catalog_env:
        # Late import to avoid a circular dependency (cli → _dispatch → cli).
        from claude_wayfinder.cli import run_demo  # noqa: PLC0415

        print(_DEMO_BANNER, file=out)
        print("", file=out)
        return run_demo(out=out)

    # ------------------------------------------------------------------
    # Real-catalog mode — env var present
    # ------------------------------------------------------------------
    catalog_path = Path(catalog_env)

    # Pre-validate so we can emit a meaningful error before spawning a
    # subprocess.  match.py does its own validation too, but returns exit
    # code 2 with the [CATALOG ERROR] banner on stderr, which is exactly
    # what we want to propagate.
    error_detail = _validate_catalog_json(catalog_path)
    if error_detail is not None:
        # Emit the catalog-error banner directly (match.py would do the
        # same but we short-circuit here to avoid passing a known-bad path
        # through the subprocess layer unnecessarily).
        banner = (
            f"{_CATALOG_ERROR_PREFIX} Dispatch catalog is degraded: "
            f"{error_detail}. Until restored, routing falls back to LLM "
            "judgment per the legacy prose-policy."
        )
        print(banner, file=sys.stderr)
        return 2

    # Stale-mtime check (warn-only — must not block execution).
    skills_dir_env = os.environ.get("DISPATCH_SKILLS_DIR")
    agents_dir_env = os.environ.get("DISPATCH_AGENTS_DIR")
    check_catalog_staleness(
        catalog_path=catalog_path,
        skills_dir=Path(skills_dir_env) if skills_dir_env else None,
        agents_dir=Path(agents_dir_env) if agents_dir_env else None,
    )

    # Read dispatch context from stdin if not supplied directly.
    if stdin_data is None:
        stdin_data = sys.stdin.read()

    # Delegate to match.py — it owns the full validation + scoring pipeline.
    # Pass --catalog-path explicitly so match.py does not re-read the env var
    # (harmless but cleaner to be explicit).
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "claude_wayfinder.match",
            "--catalog-path",
            str(catalog_path),
        ],
        input=stdin_data,
        capture_output=True,
        text=True,
    )

    # Propagate stdout (decision JSON) and stderr ([CATALOG ERROR] or logs).
    if result.stdout:
        print(result.stdout, end="", file=out)
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)

    return result.returncode
