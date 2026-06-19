"""Corpus JSONL reader + optional gold-label join.

Reads the corpus format produced by phase A (see
docs/research/2026-06-12-corpus-manifest.json format_spec):

    JSONL; one JSON object per line; fields: original log entry fields +
    corpus_id (int, 1-based) + stratum (dict: decision_band str,
    td_length_band str, file_paths_present bool).

Gold labels are an OPTIONAL separate JSONL join file:
    corpus_id → {domain, posture, gold_agent, is_any}

When no labels path is supplied (``load_labels(None)`` or no file),
metrics that require gold are skipped during evaluation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CorpusEntry:
    """One record from the phase-A corpus JSONL.

    Attributes:
        corpus_id: 1-based line number in the source dispatch log.
            Unique within the corpus; not necessarily sequential.
        task_description: Free-text task description from the dispatch.
        file_paths: File/directory paths named in the dispatch context.
        agent_mentions: Explicit agent names referenced in the dispatch.
        tool_mentions: Explicit tool names mentioned.
        command_prefix: Slash-command or CLI prefix, or None when absent.
        stratum: Stratification dict with keys
            ``decision_band``, ``td_length_band``, ``file_paths_present``.
        raw: The full original record dict (all original log fields).
    """

    corpus_id: int
    task_description: str
    file_paths: list[str]
    agent_mentions: list[str]
    tool_mentions: list[str]
    command_prefix: str | None
    stratum: dict[str, Any]
    raw: dict[str, Any]


@dataclass(frozen=True)
class GoldLabel:
    """Gold routing label for one corpus entry.

    Attributes:
        corpus_id: Join key matching ``CorpusEntry.corpus_id``.
        domain: Coarse domain label (5-way: ``code``, ``infra_deploy``,
            ``data``, ``docs_prose``, ``project_meta``).
        posture: 8-way posture label (``build``, ``diagnose``, ``assess``,
            ``critique``, ``verify``, ``plan``, ``research``, ``operate``,
            ``idea-critique``).
        gold_agent: Expected routing target agent name.
        is_any: True when the prompt is domain-any (should route on
            posture; domain encoder is expected to show high entropy).
        area_span: Number of distinct layers/areas the task spans (issue
            #396). ``1`` = single-layer (default); ``2+`` = multi-layer,
            which routes (diagnose, span≥2) to ``investigator``.
    """

    corpus_id: int
    domain: str
    posture: str
    gold_agent: str
    is_any: bool
    area_span: int = 1


# ---------------------------------------------------------------------------
# load_corpus
# ---------------------------------------------------------------------------


def load_corpus(path: Path) -> list[CorpusEntry]:
    """Load a corpus JSONL file and return a list of CorpusEntry objects.

    Blank lines are silently skipped.  Encoding is UTF-8.

    Args:
        path: Path to the corpus JSONL file.

    Returns:
        List of CorpusEntry, one per non-blank line.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        json.JSONDecodeError: If a line is not valid JSON.
        KeyError: If a required field (``corpus_id``, ``task_description``,
            ``stratum``) is missing from a record.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"Corpus file not found: {path}"
        )

    entries: list[CorpusEntry] = []
    with open(path, encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line:
                continue
            record: dict[str, Any] = json.loads(line)
            entries.append(_parse_corpus_record(record))
    return entries


def _parse_corpus_record(record: dict[str, Any]) -> CorpusEntry:
    """Parse one corpus JSON record into a CorpusEntry.

    The phase-A corpus format (produced by ``scripts/corpus/builder.py``)
    nests dispatch-context fields inside the original log entry's ``input``
    dict, following the actual log-entry shape:

    .. code-block:: json

        {
          "type": "matcher_decision",
          "session_id": "...",
          "input": {
            "task_description": "...",
            "file_paths": [...],
            "agent_mentions": [...],
            "tool_mentions": [...],
            "command_prefix": null
          },
          "output": {...},
          "corpus_id": 5,
          "stratum": {...}
        }

    ``corpus_id`` and ``stratum`` remain at the top level (added by the
    builder).  If the ``input`` key is absent or ``None``, all context
    fields default to empty values.

    Args:
        record: Raw JSON dict from one corpus JSONL line.

    Returns:
        CorpusEntry with typed fields.

    Raises:
        KeyError: If required top-level fields (``corpus_id``, ``stratum``)
            are missing from a record.
    """
    # Dispatch-context fields live inside the nested 'input' dict.
    # Tolerate absent/None input gracefully — yield empty defaults.
    inp: dict[str, Any] = record.get("input") or {}

    return CorpusEntry(
        corpus_id=int(record["corpus_id"]),
        task_description=str(inp.get("task_description", "")),
        file_paths=list(inp.get("file_paths") or []),
        agent_mentions=list(inp.get("agent_mentions") or []),
        tool_mentions=list(inp.get("tool_mentions") or []),
        command_prefix=inp.get("command_prefix") or None,
        stratum=dict(record.get("stratum") or {}),
        raw=record,
    )


# ---------------------------------------------------------------------------
# load_labels
# ---------------------------------------------------------------------------


def load_labels(
    path: Path | None,
) -> dict[int, GoldLabel]:
    """Load a gold-labels JSONL file and return a corpus_id → GoldLabel map.

    When ``path`` is ``None`` or the file does not exist, returns an
    empty dict so callers can treat labels as optional without branching.

    Args:
        path: Path to the gold-labels JSONL file, or ``None``.

    Returns:
        Dict mapping ``corpus_id`` (int) → ``GoldLabel``.

    Raises:
        FileNotFoundError: If ``path`` is not None but does not exist.
        json.JSONDecodeError: If a line is not valid JSON.
    """
    if path is None:
        return {}

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"Gold-labels file not found: {path}"
        )

    labels: dict[int, GoldLabel] = {}
    with open(path, encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line:
                continue
            record: dict[str, Any] = json.loads(line)
            label = _parse_label_record(record)
            labels[label.corpus_id] = label
    return labels


def _parse_label_record(record: dict[str, Any]) -> GoldLabel:
    """Parse one gold-label JSON record into a GoldLabel.

    Args:
        record: Raw JSON dict from one labels JSONL line.

    Returns:
        GoldLabel with typed fields.
    """
    return GoldLabel(
        corpus_id=int(record["corpus_id"]),
        domain=str(record.get("domain", "")),
        posture=str(record.get("posture", "")),
        gold_agent=str(record.get("gold_agent", "")),
        is_any=bool(record.get("is_any", False)),
        area_span=int(record.get("area_span", 1)),
    )
