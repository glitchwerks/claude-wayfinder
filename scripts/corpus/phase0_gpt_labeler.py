"""Phase 0 independent GPT labeler harness for spike #382.

Batches 168 corpus entries into ~25-entry groups, submits each batch to
``codex exec --sandbox read-only`` (OpenAI GPT family — non-Claude), parses
the returned JSON lines, validates completeness and allowed-value membership,
and re-calls for any missing or invalid entries.  Writes a complete 168-row
JSONL on success.

Usage::

    python -m scripts.corpus.phase0_gpt_labeler \\
        --corpus <corpus.jsonl> \\
        --output <out.jsonl> \\
        [--batch-size 25] \\
        [--shuffle]          # shuffle entry order (use for run2)

Requirements: ``codex`` CLI must be on PATH and authenticated.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Allowed-value sets (fixed by the pre-registered spec)
# ---------------------------------------------------------------------------

ALLOWED_DOMAINS: frozenset[str] = frozenset(
    {"code", "infra_deploy", "data", "docs_prose", "project_meta", "is_any"}
)

ALLOWED_POSTURES: frozenset[str] = frozenset(
    {
        "build",
        "diagnose",
        "assess",
        "critique",
        "verify",
        "plan",
        "research",
        "operate",
    }
)

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

LABELER_PROMPT_TEMPLATE = """\
You are a dispatch-routing labeler. Classify each task into domain and posture
using the rubric below. Output ONLY one JSON object per task, one per line,
nothing else.

ALLOWED VALUES (use only these exact strings):
  domain: code | infra_deploy | data | docs_prose | project_meta | is_any
  posture: build | diagnose | assess | critique | verify | plan | research | operate

DOMAIN RULES:
- code: .py/.ts/.go/.js/.rs etc; src/**, tests/**, explicit code references
- infra_deploy: terraform/, bicep/, .github/workflows/, deployment commands
  (az, kubectl, docker, terraform), topology/provider questions
- data: database schemas, migrations, data pipeline files, query languages
- docs_prose: docs/**, *.md, *.rst, README files, prose artifact targets.
  ACTION decides domain: EDITING the prose of an existing plan/spec/design
  doc (docs/**, docs/superpowers/plans|specs/*.md target, posture build) ->
  docs_prose. READING a plan/spec doc to SCOPE new work (posture plan,
  deliverable = a new plan) -> project_meta.
- project_meta: issue/PR scope, project planning, spec/plan file paths,
  VCS metadata, GitHub/VCS state operations (issue queries, PR queries and
  writes, repo metadata, CI status checks) -- even with no file paths
- is_any: conversational tasks, simple lookups or questions, explanations with
  NO domain-bearing file paths or artifacts (e.g. "what does X do",
  "explain Y", "how does Z work", "summarize this"). Use when no concrete
  domain evidence is present.

POSTURE RULES (apply in order; first match wins):
1. operate: non-null command_prefix, OR VCS-command shape (git/gh commands),
   OR natural-language GitHub/VCS state operations -- listing, reading,
   querying, or checking GitHub issues, PRs, CI status, commits, repo
   metadata, merge state, or milestone/label state -- even with no command
   shape and no file paths.
   CRITICAL BOUNDARY: "operate" requires NO review/critique intent.
   A bare PR status/CI check = operate. "Review PR #N diff" or
   "critique the approach" = assess or critique, NOT operate.
   EXEMPTION: a GitHub-issue/PR read that is a MEANS to a subsequent
   codebase investigation (read issue to understand scope, THEN
   explore/grep/read code to assess feasibility or behaviour) is NOT
   operate -- dominant intent is investigation -> diagnose (branch b).
   operate fires only when the GitHub/VCS state-read IS the deliverable.
2. diagnose: EITHER (a) machine-emitted failure output in prompt (stacktrace,
   test-runner summary like "FAILED tests/...", compiler diagnostic, panic:)
   AND cause not stated (no causal connective like "after/because/due to/
   caused by/since/introduced by" in the same clause as the failure);
   OR (b) read-only investigation of how existing/external code or a system
   BEHAVES -- comprehending an unfamiliar codebase, external-repo mechanics,
   or platform behaviour -- with NO failure output pasted AND NO prior-art
   markers.
   EXCEPTION (branch a only): if cause IS stated in same clause as failure
   -> posture = build
3. assess: explicit review/critique intent on a PR diff, code review of a PR,
   or reading PR change-request feedback to evaluate it. Signals: tool_mentions
   includes get_pull_request* AND task asks to evaluate/review the content;
   OR task explicitly asks to "review", "assess", "evaluate" a PR's diff or
   change-request feedback. A bare PR state check (is it open? CI status?
   unresolved comments count?) is operate, NOT assess.
4. verify: two or more distinct artifact references PLUS relational conformance
   marker ("consistent with", "matches", "conforms to", "drifted from", etc.)
5. critique: challenge-frame markers (adversarial/harsh review) AND either
   code/architecture artifact present -> inquisitor path, OR no artifact present
6. plan: no artifact-bearing evidence AND scope-frame markers ("roadmap",
   "phases", "milestones", "scope", "requirements")
7. research: prior-art markers ("prior art", "what exists", "alternatives",
   "has anyone"). Prior-art / alternatives DISCOVERY only -- surveying what
   already exists out there. SUBJECT-vs-REFERENCE test for artifacts: research
   MAY apply WITH artifacts when the artifact is a REFERENCE, BASELINE, or
   CONSTRAINT that seeds an open exploration (deliverable = discovered options).
   research does NOT apply when the artifact is the SUBJECT being understood
   or worked (-> diagnose or build). The artifact's ROLE decides, not its
   presence. Reading a SPECIFIC existing codebase's behaviour is diagnose
   (branch b), not research.
8. build: DEFAULT -- use when no other posture fires; or when target behavior
   is known and no failure evidence is present

SPECIAL RULES:
- GitHub/VCS state operations -> domain: project_meta (even without file paths)
- Conversational/no-evidence tasks with no domain signal -> domain: is_any
- agent_mentions non-empty with directive intent: output domain/posture from
  prompt evidence; ignore agent_mentions for the domain/posture classification
- Harness config files (agents/**/*.md, CLAUDE.md): label domain/posture from
  content as normal
- If multiple postures are plausible, pick the strongest evidence signal

OUTPUT FORMAT (strict):
One JSON object per input entry, on its own line, NOTHING else:
{{"corpus_id": <int>, "domain": "<one of the 6 values>", "posture": "<one of the 8 values>"}}

ENTRIES TO LABEL:
{entries_json}
"""

# Pattern to extract valid JSON output lines
_JSON_LINE_RE = re.compile(r'^\{"corpus_id"')


def _build_entry_json(entry: dict[str, Any]) -> str:
    """Serialize one corpus entry into the format sent to the labeler.

    Args:
        entry: Raw corpus JSONL record (with nested ``input`` dict).

    Returns:
        A compact JSON string containing corpus_id plus the input fields.
    """
    inp = entry.get("input", {})
    payload = {
        "corpus_id": entry["corpus_id"],
        "task_description": inp.get("task_description", ""),
        "file_paths": inp.get("file_paths") or [],
        "agent_mentions": inp.get("agent_mentions") or [],
        "tool_mentions": inp.get("tool_mentions") or [],
        "command_prefix": inp.get("command_prefix"),
    }
    return json.dumps(payload, ensure_ascii=False)


def _call_codex(prompt: str) -> str:
    """Invoke ``codex exec --sandbox read-only`` with *prompt* on stdin.

    Args:
        prompt: Full prompt text to pipe into codex.

    Returns:
        Combined stdout+stderr output from codex.

    Raises:
        RuntimeError: If the subprocess exits with a non-zero return code
            and produced no JSON lines (indicating a hard failure, not just
            noise output).
    """
    # On Windows, codex is a .cmd file installed by npm; shell=True resolves it
    # via PATH just as the interactive shell does.
    result = subprocess.run(
        "codex exec --sandbox read-only -",
        input=prompt.encode("utf-8"),
        capture_output=True,
        timeout=300,
        shell=True,
    )
    combined = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    return combined


def _extract_json_lines(raw_output: str) -> list[dict[str, Any]]:
    """Extract and parse JSON label lines from noisy codex output.

    Keeps only lines matching ``^{"corpus_id"``.

    Args:
        raw_output: Full stdout+stderr from a codex call.

    Returns:
        List of parsed label dicts.
    """
    results: list[dict[str, Any]] = []
    for line in raw_output.splitlines():
        line = line.strip()
        if _JSON_LINE_RE.match(line):
            try:
                obj = json.loads(line)
                results.append(obj)
            except json.JSONDecodeError:
                print(f"  [warn] Could not parse line: {line[:80]!r}")
    return results


def _validate_labels(
    labels: list[dict[str, Any]],
    expected_ids: set[int],
) -> tuple[list[dict[str, Any]], list[int]]:
    """Validate a batch of labels against expected ids and allowed values.

    Args:
        labels: Parsed label dicts from codex.
        expected_ids: Set of corpus_id values we expect to receive.

    Returns:
        A tuple of (valid_labels, missing_or_invalid_ids) where
        missing_or_invalid_ids is a list of corpus_ids that need re-calling.
    """
    valid: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    invalid_ids: list[int] = []

    for lbl in labels:
        raw_cid = lbl.get("corpus_id")
        try:
            cid: int = int(raw_cid)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            print(
                f"  [warn] corpus_id {raw_cid!r} is not a valid integer,"
                " routing to re-call."
            )
            # Cannot match to expected_ids without a valid int; skip so
            # the entry falls into 'missing' and is re-requested by the
            # caller.
            continue
        domain = lbl.get("domain", "")
        posture = lbl.get("posture", "")

        if cid not in expected_ids:
            print(f"  [warn] Unexpected corpus_id {cid} in output, skipping.")
            continue
        if domain not in ALLOWED_DOMAINS:
            print(
                f"  [warn] corpus_id={cid}: invalid domain {domain!r},"
                " will re-call."
            )
            invalid_ids.append(cid)
            continue
        if posture not in ALLOWED_POSTURES:
            print(
                f"  [warn] corpus_id={cid}: invalid posture {posture!r},"
                " will re-call."
            )
            invalid_ids.append(cid)
            continue
        if cid in seen_ids:
            print(f"  [warn] corpus_id={cid} duplicated, keeping first.")
            continue

        seen_ids.add(cid)
        valid.append(
            {
                "corpus_id": cid,
                "domain": domain,
                "posture": posture,
            }
        )

    missing = sorted(expected_ids - seen_ids - set(invalid_ids))
    return valid, sorted(set(invalid_ids) | set(missing))


def _label_batch(
    entries: list[dict[str, Any]],
    batch_label: str = "",
) -> list[dict[str, Any]]:
    """Label one batch of entries, with re-calls for any missing/invalid rows.

    Args:
        entries: List of raw corpus records to label in this batch.
        batch_label: Human-readable label for log output (e.g. "batch 1/7").

    Returns:
        A list of validated label dicts (one per entry, in any order).
    """
    expected_ids = {e["corpus_id"] for e in entries}
    id_to_entry = {e["corpus_id"]: e for e in entries}

    accum: list[dict[str, Any]] = []
    remaining_ids = set(expected_ids)
    attempt = 0

    while remaining_ids:
        attempt += 1
        batch_entries = [id_to_entry[cid] for cid in sorted(remaining_ids)]
        entries_json = "\n".join(_build_entry_json(e) for e in batch_entries)
        prompt = LABELER_PROMPT_TEMPLATE.format(entries_json=entries_json)

        n = len(batch_entries)
        suffix = f"(attempt {attempt})" if attempt > 1 else ""
        print(
            f"  {batch_label} calling codex for {n} entries {suffix}..."
        )

        raw = _call_codex(prompt)
        new_labels = _extract_json_lines(raw)
        valid, bad_ids = _validate_labels(new_labels, remaining_ids)

        accum.extend(valid)
        # Remove successfully labeled ids
        for lbl in valid:
            remaining_ids.discard(lbl["corpus_id"])

        if bad_ids:
            print(
                f"  {batch_label} {len(bad_ids)} ids need re-call: {bad_ids}"
            )
        if remaining_ids and attempt >= 5:
            print(
                f"  [error] {batch_label} giving up after 5 attempts;"
                f" {len(remaining_ids)} ids unresolved: {sorted(remaining_ids)}"
            )
            break

    return accum


def label_corpus(
    corpus_path: Path,
    output_path: Path,
    batch_size: int = 25,
    shuffle: bool = False,
) -> list[dict[str, Any]]:
    """Label all 168 corpus entries via codex, write JSONL output.

    Args:
        corpus_path: Path to the corpus JSONL file.
        output_path: Path to write the resulting label JSONL.
        batch_size: Number of entries per codex call.
        shuffle: If True, shuffle entry order before batching (for run2
            to surface order effects).

    Returns:
        List of all 168 label dicts (sorted by corpus_id).

    Raises:
        SystemExit: If fewer than 168 valid labels are produced after
            all retry attempts.
    """
    with open(corpus_path, encoding="utf-8") as f:
        entries = [json.loads(line) for line in f]

    print(f"Loaded {len(entries)} corpus entries.")

    if shuffle:
        import random
        random.seed(42)
        random.shuffle(entries)
        print("Entry order shuffled (run2 mode).")

    batches = [
        entries[i : i + batch_size]
        for i in range(0, len(entries), batch_size)
    ]
    n_batches = len(batches)
    print(f"Batching into {n_batches} batches of ~{batch_size}.")

    all_labels: list[dict[str, Any]] = []
    for i, batch in enumerate(batches, 1):
        print(f"\n--- Batch {i}/{n_batches} ({len(batch)} entries) ---")
        batch_labels = _label_batch(batch, batch_label=f"[batch {i}/{n_batches}]")
        all_labels.extend(batch_labels)
        print(
            f"  Batch {i} done. Cumulative: {len(all_labels)} labels collected."
        )

    # Sort by corpus_id for deterministic output
    all_labels.sort(key=lambda x: x["corpus_id"])

    # Final completeness check
    all_ids = {e["corpus_id"] for e in entries}
    labeled_ids = {lbl["corpus_id"] for lbl in all_labels}
    missing = sorted(all_ids - labeled_ids)
    if missing:
        print(
            f"\n[FATAL] Missing labels for {len(missing)} entries: {missing}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Write output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8", newline="\n") as f:
        for lbl in all_labels:
            f.write(json.dumps(lbl, ensure_ascii=False) + "\n")

    print(
        f"\nWrote {len(all_labels)} labels to {output_path}"
    )
    return all_labels


def main(argv: list[str] | None = None) -> None:
    """Entry point for the Phase 0 GPT labeler harness.

    Args:
        argv: Command-line argument list; defaults to sys.argv[1:].
    """
    parser = argparse.ArgumentParser(
        description=(
            "Phase 0 independent GPT labeler — batches corpus entries to "
            "codex exec and produces a domain/posture JSONL for eval."
        )
    )
    parser.add_argument(
        "--corpus",
        required=True,
        type=Path,
        metavar="PATH",
        help="Corpus JSONL (phase-A format).",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        metavar="PATH",
        help="Output JSONL path for GPT labels.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=25,
        metavar="N",
        help="Entries per codex call (default: 25).",
    )
    parser.add_argument(
        "--shuffle",
        action="store_true",
        help="Shuffle entry order before batching (use for run2).",
    )
    args = parser.parse_args(argv)
    label_corpus(
        corpus_path=args.corpus,
        output_path=args.output,
        batch_size=args.batch_size,
        shuffle=args.shuffle,
    )


if __name__ == "__main__":
    main()
