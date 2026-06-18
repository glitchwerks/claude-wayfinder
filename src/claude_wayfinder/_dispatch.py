"""Mode-switching logic for the ``dispatch`` CLI subcommand.

Implements the four-outcome mode-detection contract (Issue #284):

- **Demo mode** — ``--demo`` flag is passed.  Runs the bundled demo fixtures
  with a "no catalog configured" banner.  Ignores env vars and catalog files.
- **Real-catalog mode** — ``$DISPATCH_CATALOG_PATH`` is set and resolves to
  a readable, valid JSON catalog.  Passes the context JSON to
  ``claude_wayfinder.match.main()`` in-process and returns the matcher's
  decision JSON verbatim.
- **Hard-error mode** — ``$DISPATCH_CATALOG_PATH`` is set but the path is
  missing, unreadable, or contains invalid/schema-invalid JSON.  Propagates
  the ``[CATALOG ERROR]`` banner from ``match.py`` and exits non-zero.
  **Never falls back to demo mode silently.**
- **Canonical-default mode** — neither ``--demo`` nor
  ``$DISPATCH_CATALOG_PATH`` is set.  Resolves the canonical default path
  (``$CLAUDE_HOME/state/dispatch-catalog.json`` or
  ``~/.claude/state/dispatch-catalog.json``).  If the canonical file exists
  → real-catalog mode; if absent → ``[CATALOG ERROR]`` and non-zero exit.
  Demo mode is **never** the implicit default.

Stale-mtime behavior (design § 2.1 last paragraph):
  When ``$DISPATCH_SKILLS_DIR`` and/or ``$DISPATCH_AGENTS_DIR`` are set and
  any source file within them has a mtime newer than the catalog file, a
  warning is emitted to stderr.  Execution proceeds — staleness is a
  degraded-quality signal, not an error.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
from pathlib import Path
from typing import Any

from claude_wayfinder.match import (
    build_features,
    decide,
    load_catalog,
    score_entries,
)
from claude_wayfinder.match._catalog import (
    _compute_catalog_hash,
    _get_matcher_version,
    _resolve_log_path,
    _resolve_overrides_path,
    _write_log_entry,
)
from claude_wayfinder.match._overrides import (
    OverridesError,
    load_overrides,
    resolve_override,
)

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
# Mode-detection helpers
# ---------------------------------------------------------------------------


def _canonical_catalog_path() -> Path:
    """Return the canonical default dispatch-catalog path.

    Resolution order:

    1. ``$CLAUDE_HOME/state/dispatch-catalog.json`` when ``$CLAUDE_HOME`` is
       set.
    2. ``~/.claude/state/dispatch-catalog.json`` otherwise.

    This mirrors the logic in ``audit_catalog._resolve_catalog_path`` and
    ``build_catalog._discover._resolve_catalog_build_defaults``.  The
    canonical path is publicly documented and stable as of Issue #284.

    Returns:
        The canonical ``Path`` (may or may not exist on disk).
    """
    claude_home_env = os.environ.get("CLAUDE_HOME")
    if claude_home_env:
        base = Path(claude_home_env)
    else:
        base = Path.home() / ".claude"
    return base / "state" / "dispatch-catalog.json"


def _resolve_mode(demo: bool) -> tuple[str, Path | None]:
    """Determine the dispatch mode and catalog path for one invocation.

    Decision tree (Issue #284):

    1. If *demo* is ``True`` → ``("demo", None)``.
    2. Else if ``$DISPATCH_CATALOG_PATH`` is set → ``("real", path)``.
    3. Else resolve the canonical default.  If it exists on disk →
       ``("real", canonical_path)``; if absent →
       ``("error", canonical_path)`` so the caller can emit a helpful
       error naming the missing path.

    Args:
        demo: ``True`` when the ``--demo`` flag was passed by the caller.

    Returns:
        A 2-tuple ``(mode, path)`` where *mode* is one of
        ``"demo"``, ``"real"``, or ``"error"``; and *path* is the resolved
        catalog ``Path`` (or ``None`` when *mode* is ``"demo"``).
    """
    if demo:
        return "demo", None

    catalog_env = os.environ.get("DISPATCH_CATALOG_PATH")
    if catalog_env:
        return "real", Path(catalog_env)

    # Neither --demo nor the env var — fall back to the canonical default.
    canonical = _canonical_catalog_path()
    if canonical.exists():
        return "real", canonical
    return "error", canonical


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


def check_overrides_staleness(
    catalog_path: Path,
    overrides_path: Path | None,
) -> None:
    """Emit a stderr warning when the overrides file is older than the catalog.

    Fires only when *overrides_path* is set, both files exist, and the
    overrides mtime is strictly less than the catalog mtime.  All other
    cases (either file missing, overrides newer or equal, env var absent)
    are silent.

    Args:
        catalog_path: Resolved path to the dispatch catalog file.
        overrides_path: Resolved path to the overrides JSON file, or
            ``None`` when ``$DISPATCH_OVERRIDES_PATH`` is unset.
    """
    if overrides_path is None:
        return
    try:
        overrides_mtime = overrides_path.stat().st_mtime
        catalog_mtime = catalog_path.stat().st_mtime
    except OSError:
        return
    if overrides_mtime < catalog_mtime:
        print(
            "[DISPATCH WARNING] overrides file is older than catalog"
            " — rules may reference stale agent/skill names",
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


def _call_match_in_process(
    stdin_data: str,
    catalog_path: Path,
) -> tuple[str, str, int]:
    """Call ``claude_wayfinder.match.main()`` in-process with redirected I/O.

    Replaces the former ``subprocess.run([sys.executable, "-m",
    "claude_wayfinder.match", ...])`` invocation to avoid the
    ``RuntimeWarning: 'claude_wayfinder.match' found in sys.modules``
    emitted by ``runpy`` when the module is already imported by the
    parent process (#134).

    Streams are temporarily swapped for the duration of the call:

    - ``sys.stdin``  → ``io.StringIO(stdin_data)``
    - ``sys.stdout`` → captured ``io.StringIO`` (returned as first element)
    - ``sys.stderr`` → captured ``io.StringIO`` (returned as second element)

    ``SystemExit`` is caught so that ``argparse`` or ``sys.exit()`` calls
    inside ``match.main()`` do not terminate the parent process.

    Args:
        stdin_data: JSON string with the dispatch context to feed as stdin.
        catalog_path: Resolved path to the dispatch catalog file; passed
            to ``match.main()`` via the ``--catalog-path`` argv argument.

    Returns:
        A 3-tuple ``(stdout_text, stderr_text, exit_code)``.
    """
    from claude_wayfinder.match import main as match_main

    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    original_stdin = sys.stdin
    sys.stdin = io.StringIO(stdin_data)
    try:
        with (
            contextlib.redirect_stdout(stdout_buf),
            contextlib.redirect_stderr(stderr_buf),
        ):
            try:
                rc = match_main(
                    argv=["--catalog-path", str(catalog_path)]
                )
            except SystemExit as exc:
                if isinstance(exc.code, int):
                    rc = exc.code
                elif exc.code is None:
                    rc = 0
                else:
                    rc = 1
    finally:
        sys.stdin = original_stdin

    return stdout_buf.getvalue(), stderr_buf.getvalue(), rc or 0


def dispatch(
    *,
    catalog_path: Path,
    context: dict[str, Any],
) -> dict[str, Any]:
    """In-process dispatch wrapper for tests and library callers.

    Serialises *context* to JSON, invokes the matcher with *catalog_path*
    (bypassing env-var resolution), and returns the parsed decision dict.

    Args:
        catalog_path: Path to the dispatch catalog JSON file.
        context: Dispatch context dict matching the runtime contract:
            ``{task_description, file_paths?, agent_mentions?,
            tool_mentions?, command_prefix?, session_id?}``.
            ``session_id`` (fix #294) is written verbatim into the
            ``matcher_decision`` log entry when supplied.

    Returns:
        Parsed decision dict with keys: ``decision``, ``confidence``,
        ``rationale``, and optionally ``agent``, ``skills``,
        ``alternatives``.

    Raises:
        ValueError: If the matcher exits non-zero or returns no output.
        json.JSONDecodeError: If the matcher output is not valid JSON.
    """
    stdin_data = json.dumps(context)
    stdout_text, _stderr_text, rc = _call_match_in_process(
        stdin_data=stdin_data,
        catalog_path=catalog_path,
    )
    if rc != 0 or not stdout_text.strip():
        raise ValueError(
            f"dispatch() matcher exited with rc={rc}; "
            f"stderr={_stderr_text!r}"
        )
    return json.loads(stdout_text)


def run_batch_dispatch(
    stdin_data: str | None = None,
    out: Any = None,
    demo: bool = False,
) -> int:
    """Run the dispatch subcommand in batch (NDJSON) mode.

    Reads one dispatch context JSON object per line from *stdin_data* (or
    ``sys.stdin`` when ``None``).  Blank lines are silently skipped.
    Malformed JSON lines produce an error record on stdout without aborting
    the batch.  Decisions are written as NDJSON to *out*, one per input
    line, in input order.

    Each output line is the standard single-mode decision JSON with one
    extra leading field:

    - ``input_index`` (int, 0-based) — the position of the input line
      among non-blank lines.  Present on both decision records and error
      records.

    Error record shape for malformed input lines::

        {
            "input_index": <int>,
            "error": "<description>",
            "input_line": "<raw text of the bad line>"
        }

    Mode detection (Issue #284):

    - ``demo=True`` → demo mode (banner + demo run).
    - ``$DISPATCH_CATALOG_PATH`` set and valid → real-catalog mode.
    - ``$DISPATCH_CATALOG_PATH`` set but invalid → hard error, non-zero exit.
    - Neither set, no ``demo`` → resolve canonical default; real-catalog if
      present, ``[CATALOG ERROR]`` and non-zero exit if absent.

    The catalog is loaded **once** per invocation, regardless of how many
    input lines are present.

    Args:
        stdin_data: NDJSON string (one JSON object per line) to process.
            Read from ``sys.stdin`` when ``None``.
        out: File-like object for stdout.  Defaults to ``sys.stdout``.
        demo: When ``True`` activate demo mode unconditionally, ignoring
            env vars and catalog files.

    Returns:
        Exit code: 0 when all lines produced decisions or error records
        (partial batch success counts as success).  Non-zero on hard
        errors (no catalog, invalid catalog).
    """
    if out is None:
        out = sys.stdout

    mode, catalog_path = _resolve_mode(demo)

    # ------------------------------------------------------------------
    # Demo mode — --demo flag passed
    # ------------------------------------------------------------------
    if mode == "demo":
        from claude_wayfinder.cli import run_demo  # noqa: PLC0415

        print(_DEMO_BANNER, file=out)
        print("", file=out)
        return run_demo(out=out)

    # ------------------------------------------------------------------
    # Error mode — no env var and canonical catalog absent
    # ------------------------------------------------------------------
    if mode == "error":
        banner = (
            f"{_CATALOG_ERROR_PREFIX} No catalog configured. "
            f"Expected at {catalog_path} "
            "(canonical default: $CLAUDE_HOME/state/dispatch-catalog.json "
            "or ~/.claude/state/dispatch-catalog.json). "
            "Run `claude-wayfinder catalog build` or set "
            "$DISPATCH_CATALOG_PATH to point to a valid catalog, "
            "or pass --demo to run bundled fixtures."
        )
        print(banner, file=sys.stderr)
        return 2

    # ------------------------------------------------------------------
    # Real-catalog mode — catalog_path is resolved
    # ------------------------------------------------------------------
    assert catalog_path is not None  # mode == "real" guarantees this

    error_detail = _validate_catalog_json(catalog_path)
    if error_detail is not None:
        banner = (
            f"{_CATALOG_ERROR_PREFIX} Dispatch catalog is degraded: "
            f"{error_detail}. Until restored, routing falls back to LLM "
            "judgment per the legacy prose-policy."
        )
        print(banner, file=sys.stderr)
        return 2

    # Stale-mtime check (warn-only).
    skills_dir_env = os.environ.get("DISPATCH_SKILLS_DIR")
    agents_dir_env = os.environ.get("DISPATCH_AGENTS_DIR")
    check_catalog_staleness(
        catalog_path=catalog_path,
        skills_dir=Path(skills_dir_env) if skills_dir_env else None,
        agents_dir=Path(agents_dir_env) if agents_dir_env else None,
    )

    # Overrides-mtime check (warn-only).
    overrides_env = os.environ.get("DISPATCH_OVERRIDES_PATH")
    check_overrides_staleness(
        catalog_path=catalog_path,
        overrides_path=Path(overrides_env).expanduser() if overrides_env else None,
    )

    # Load catalog ONCE for the entire batch — this is the hot path that the
    # test monkeypatches to verify catalog-once semantics.
    try:
        entries = load_catalog(catalog_path)
    except Exception:
        # load_catalog logs its own errors; the validate step above catches
        # structural issues.  If we still fail here, propagate as hard error.
        print(
            f"{_CATALOG_ERROR_PREFIX} Failed to load catalog at "
            f"{catalog_path}.",
            file=sys.stderr,
        )
        return 2

    if not entries:
        print(
            f"{_CATALOG_ERROR_PREFIX} Catalog at {catalog_path} contains "
            "zero entries.",
            file=sys.stderr,
        )
        return 2

    # Compute catalog hash once for logging.
    try:
        catalog_raw_text = catalog_path.read_text(encoding="utf-8")
        catalog_hash = _compute_catalog_hash(catalog_raw_text)
    except OSError:
        catalog_hash = ""

    # Load overrides once (same as single-mode).
    overrides_path = _resolve_overrides_path()
    override_rules: list[Any] = []
    if overrides_path is not None:
        try:
            override_rules = load_overrides(overrides_path)
        except OverridesError as exc:
            print(
                f"[OVERRIDES ERROR] {exc}; proceeding with scored matching.",
                file=sys.stderr,
            )
        print(
            f"[dispatch] overrides: {len(override_rules)} rules loaded"
            f" from {overrides_path}",
            file=sys.stderr,
        )

    log_path = _resolve_log_path()

    # Read all NDJSON input.
    if stdin_data is None:
        stdin_data = sys.stdin.read()

    input_index = 0
    for raw_line in stdin_data.splitlines():
        # Skip blank lines silently.
        if not raw_line.strip():
            continue

        # Parse the line — emit an error record on malformed JSON.
        try:
            context: dict[str, Any] = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            error_record: dict[str, Any] = {
                "input_index": input_index,
                "error": f"malformed JSON: {exc}",
                "input_line": raw_line,
            }
            print(
                json.dumps(error_record, sort_keys=True),
                file=out,
                flush=True,
            )
            input_index += 1
            continue

        # Extract features and run the scoring/decision pipeline.
        features = build_features(context)

        # Override short-circuit (same logic as single-mode).
        override_match = resolve_override(override_rules, features)
        if override_match is not None:
            rule = override_match.rule
            decision_dict: dict[str, Any] = {
                "decision": rule.decision,
                "confidence": rule.confidence,
                "rationale": rule.rationale,
                "alternatives": [],
                "disposition_source": "override",
                "override_id": rule.id,
            }
            if rule.agent is not None:
                decision_dict["agent"] = rule.agent
            if rule.skills:
                decision_dict["skills"] = list(rule.skills)
            _write_log_entry(
                context,
                decision_dict,
                catalog_hash,
                log_path,
                override_id=rule.id,
            )
            # Include catalog_hash and matcher_version in output for
            # consistency with single-mode (issue #311).
            output: dict[str, Any] = {
                "input_index": input_index,
                **decision_dict,
                "catalog_hash": catalog_hash,
                "matcher_version": _get_matcher_version(),
            }
            print(json.dumps(output, sort_keys=True), file=out, flush=True)
            input_index += 1
            continue

        # Score all entries.
        scored_agents, scored_skills = score_entries(entries, features)

        decision_dict = decide(scored_agents, scored_skills, features, entries)
        _write_log_entry(
            context, decision_dict, catalog_hash, log_path, override_id=None
        )

        # Include catalog_hash and matcher_version in output for
        # consistency with single-mode (issue #311).
        output = {
            "input_index": input_index,
            **decision_dict,
            "catalog_hash": catalog_hash,
            "matcher_version": _get_matcher_version(),
        }
        print(json.dumps(output, sort_keys=True), file=out, flush=True)
        input_index += 1

    return 0


def run_dispatch(
    stdin_data: str | None = None,
    out: Any = None,
    demo: bool = False,
) -> int:
    """Run the dispatch subcommand with mode-detection.

    Mode is determined by ``_resolve_mode`` (Issue #284):

    - ``demo=True`` → demo mode: print banner + run bundled demo fixtures.
    - ``$DISPATCH_CATALOG_PATH`` set, valid → real-catalog mode.
    - ``$DISPATCH_CATALOG_PATH`` set, invalid → hard error, non-zero exit.
    - Neither set, no ``demo`` → resolve canonical default; real-catalog if
      present, ``[CATALOG ERROR]`` and non-zero exit if absent.

    Args:
        stdin_data: JSON string with dispatch context (5-field shape from
            design § 2.2).  Read from ``sys.stdin`` when ``None``.
        out: File-like object for stdout.  Defaults to ``sys.stdout``.
        demo: When ``True`` activate demo mode unconditionally, ignoring
            env vars and catalog files.

    Returns:
        Exit code: 0 on success, non-zero on error.
    """
    if out is None:
        out = sys.stdout

    mode, catalog_path = _resolve_mode(demo)

    # ------------------------------------------------------------------
    # Demo mode — --demo flag passed
    # ------------------------------------------------------------------
    if mode == "demo":
        # Late import to avoid a circular dependency (cli → _dispatch → cli).
        from claude_wayfinder.cli import run_demo  # noqa: PLC0415

        print(_DEMO_BANNER, file=out)
        print("", file=out)
        return run_demo(out=out)

    # ------------------------------------------------------------------
    # Error mode — no env var and canonical catalog absent
    # ------------------------------------------------------------------
    if mode == "error":
        banner = (
            f"{_CATALOG_ERROR_PREFIX} No catalog configured. "
            f"Expected at {catalog_path} "
            "(canonical default: $CLAUDE_HOME/state/dispatch-catalog.json "
            "or ~/.claude/state/dispatch-catalog.json). "
            "Run `claude-wayfinder catalog build` or set "
            "$DISPATCH_CATALOG_PATH to point to a valid catalog, "
            "or pass --demo to run bundled fixtures."
        )
        print(banner, file=sys.stderr)
        return 2

    # ------------------------------------------------------------------
    # Real-catalog mode — catalog_path is resolved
    # ------------------------------------------------------------------
    assert catalog_path is not None  # mode == "real" guarantees this

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

    # Overrides-mtime check (warn-only — must not block execution).
    overrides_env = os.environ.get("DISPATCH_OVERRIDES_PATH")
    check_overrides_staleness(
        catalog_path=catalog_path,
        overrides_path=Path(overrides_env).expanduser() if overrides_env else None,
    )

    # Read dispatch context from stdin if not supplied directly.
    if stdin_data is None:
        stdin_data = sys.stdin.read()

    # Delegate to match.py — it owns the full validation + scoring pipeline.
    # Called in-process to avoid a RuntimeWarning from runpy: when the parent
    # process has already imported claude_wayfinder (which re-exports from
    # claude_wayfinder.match via __init__.py), spawning
    # ``python -m claude_wayfinder.match`` causes runpy to find the module
    # already in sys.modules before executing it as __main__ (#134).
    match_stdout, match_stderr, rc = _call_match_in_process(
        stdin_data=stdin_data,
        catalog_path=catalog_path,
    )

    # Propagate stdout (decision JSON) and stderr ([CATALOG ERROR] or logs).
    if match_stdout:
        print(match_stdout, end="", file=out)
    if match_stderr:
        print(match_stderr, end="", file=sys.stderr)

    return rc
