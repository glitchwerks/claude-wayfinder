"""Catalog I/O helpers for the dispatch matcher.

Handles catalog path resolution, SHA hashing, dispatch log writing,
and loading the compiled catalog JSON into typed ``CatalogEntry``
objects.  Also owns the catalog-degraded error banner (``_emit_catalog_error``)
so callers never need to reach back into ``__init__`` for it.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NoReturn

from claude_wayfinder.match._parse import _parse_triggers
from claude_wayfinder.match._types import CatalogEntry

# Catalog error banner prefix (v5 §3.1.6).
_CATALOG_ERROR_PREFIX = "[CATALOG ERROR]"


def _emit_catalog_error(details: str) -> NoReturn:
    """Write the catalog-degraded banner to stderr and exit 2.

    Args:
        details: Human-readable description of the degradation.
    """
    banner = (
        f"{_CATALOG_ERROR_PREFIX} Dispatch catalog is degraded: {details}. "
        "Until restored, routing falls back to LLM judgment per the "
        "legacy prose-policy."
    )
    print(banner, file=sys.stderr)
    sys.exit(2)


def _resolve_catalog_path(
    explicit_path: str | Path | None = None,
) -> Path:
    """Return the catalog file path from the explicit arg or env var.

    Resolution order (first match wins):

    1. ``explicit_path`` argument — supplied via ``--catalog-path`` CLI
       flag.
    2. ``DISPATCH_CATALOG_PATH`` env var.
    3. **Fail loud** — emits a ``[CATALOG ERROR]`` banner on stderr and
       exits with code 2.

    The previous three-step lookup (env var, home-env middle step,
    platform default) has been reduced to two steps — env var or explicit
    arg, else fail loud (Issue #10).  Callers that previously relied on
    the default must now supply an explicit path or set
    ``DISPATCH_CATALOG_PATH``.

    Args:
        explicit_path: Path supplied by the caller (e.g. ``--catalog-path``
            CLI flag).  ``None`` falls through to the env var.

    Returns:
        Resolved ``Path`` to the catalog file.  The file may not exist;
        callers are responsible for checking.

    Raises:
        SystemExit: With code 2 when no path source is available.
    """
    if explicit_path is not None:
        return Path(explicit_path)
    env_val = os.environ.get("DISPATCH_CATALOG_PATH")
    if env_val:
        return Path(env_val)
    _emit_catalog_error(
        "no catalog path specified — pass --catalog-path <path> "
        "or set DISPATCH_CATALOG_PATH"
    )


def _resolve_overrides_path() -> Path | None:
    """Return the overrides file path from env, or None when disabled.

    Resolution: ``$DISPATCH_OVERRIDES_PATH`` env var only.  No
    auto-discovery.  When the env var is absent overrides are silently
    disabled and the matcher proceeds with scored matching.

    Returns:
        ``Path`` to the overrides file, or ``None`` when the env var is
        not set.
    """
    val = os.environ.get("DISPATCH_OVERRIDES_PATH")
    return Path(val).expanduser() if val else None


def _resolve_log_path() -> Path | None:
    """Return the dispatch log file path from env, or None to disable logging.

    Resolution order:

    1. ``DISPATCH_LOG_PATH`` env var — absolute override.
    2. ``None`` — logging is silently disabled (no ``~/.claude/`` fallback).

    The previous ``~/.claude/state/dispatch-log.jsonl`` platform default
    has been removed (Issue #10).  When ``DISPATCH_LOG_PATH`` is absent,
    log writing is skipped without error.

    Returns:
        ``Path`` to the log file, or ``None`` when logging is disabled.
    """
    explicit = os.environ.get("DISPATCH_LOG_PATH")
    if explicit:
        return Path(explicit).expanduser()
    return None


def _compute_catalog_hash(catalog_data: dict[str, Any] | str | bytes) -> str:
    """Return a stable SHA-256 digest of the catalog content.

    Normalises the catalog before hashing so that whitespace and
    key-order variations produce the same digest.  If ``catalog_data``
    is already a ``str`` or ``bytes`` it is re-parsed as JSON first to
    guarantee normalisation.

    Args:
        catalog_data: The catalog content as a parsed dict, a JSON
            string, or UTF-8-encoded JSON bytes.

    Returns:
        Hash string in the form ``"sha256:<64-hex-digits>"``.
    """
    if isinstance(catalog_data, (str, bytes)):
        if isinstance(catalog_data, bytes):
            catalog_data = catalog_data.decode("utf-8")
        catalog_data = json.loads(catalog_data)
    normalised = json.dumps(
        catalog_data, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    hexdigest = hashlib.sha256(normalised).hexdigest()
    return f"sha256:{hexdigest}"


def _get_matcher_version() -> str:
    """Return a stable identifier for the current matcher revision.

    Attempts to read the short git SHA from the repository that contains
    this file.  Falls back to the string ``"unknown"`` on any failure
    (missing git binary, not a git repo, subprocess timeout, etc.).

    Returns:
        Short git SHA string, or ``"unknown"`` if unavailable.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).parent,
            capture_output=True,
            text=True,
            timeout=1,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:  # any failure is acceptable here
        pass
    return "unknown"


def _write_log_entry(
    input_dict: dict[str, Any],
    output_dict: dict[str, Any],
    catalog_hash: str,
    log_path: Path | None,
    override_id: str | None = None,
) -> None:
    """Append one decision record to the dispatch log file.

    The entry is written as newline-delimited JSON (NDJSON).  If the
    parent directory does not exist it is created.  All I/O errors are
    caught and emitted to stderr; this function never raises.

    When ``log_path`` is ``None`` (logging disabled — no
    ``DISPATCH_LOG_PATH`` env var was set), the function returns
    immediately without writing or emitting any message.

    Args:
        input_dict: The parsed dispatch context (stdin JSON).
        output_dict: The matcher decision (stdout JSON).
        catalog_hash: SHA-256 digest of the catalog used, from
            ``_compute_catalog_hash``.
        log_path: Path to the ``.jsonl`` log file, or ``None`` to
            silently skip log writing.
        override_id: The matched override rule's ``id`` when the
            decision was produced by an override, or ``None`` for
            scored decisions.
    """
    if log_path is None:
        return
    entry: dict[str, Any] = {
        "type": "matcher_decision",
        "ts": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z",
        "session_id": os.environ.get("CLAUDE_SESSION_ID", ""),
        "input": input_dict,
        "output": output_dict,
        "catalog_hash": catalog_hash,
        "matcher_version": _get_matcher_version(),
        "override_id": override_id,
    }
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except (OSError, ValueError) as err:
        print(f"[match.py] log write failed: {err}", file=sys.stderr)


def load_catalog(path: Path) -> list[CatalogEntry]:
    """Load and parse the dispatch catalog JSON file.

    Args:
        path: Resolved path to ``dispatch-catalog.json``.

    Returns:
        List of ``CatalogEntry`` objects.  An empty list is returned for
        catalogs whose ``entries`` array is present but empty.

    Raises:
        FileNotFoundError: If the catalog file does not exist.
        json.JSONDecodeError: If the file contains malformed JSON.
    """
    raw_text = path.read_text(encoding="utf-8")
    catalog = json.loads(raw_text)
    raw_entries: list[dict[str, Any]] = catalog.get("entries", [])
    # Empty entries list is a valid degraded state (#506 catalog-error
    # path, fresh-checkout pre-build). Callers like audit-catalog need
    # to operate on it without crashing. Return empty rather than raise.
    if not raw_entries:
        return []

    entries: list[CatalogEntry] = []
    for raw in raw_entries:
        triggers_raw = raw.get("triggers", {})
        triggers = _parse_triggers(
            triggers_raw if isinstance(triggers_raw, dict) else {}
        )
        entries.append(
            CatalogEntry(
                name=str(raw.get("name", "")),
                kind=str(raw.get("kind", "")),
                triggers=triggers,
                applicable_agents=tuple(raw.get("applicable_agents", [])),
                applicable_skills=tuple(raw.get("applicable_skills", [])),
                source=str(raw.get("source", "owned")),
                routable=bool(raw.get("routable", True)),
                applicable_agents_intentional=str(
                    raw.get("applicable_agents_intentional", "")
                ),
            )
        )
    return entries
